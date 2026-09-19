"""Durable in-process session memory for one live game run.

The planner sees single frames; this module carries the cross-frame state the
handoff contract requires: the current main-quest text (fuzzily merged against
OCR jitter), the coarse screen type, and the recent *physically executed*
actions with their verified effects.  The supervisor updates it from every
perception snapshot, and the planner instruction injects a compact summary so
decisions serve the long-lived quest instead of the latest local widget.
"""

from __future__ import annotations

import os
import re
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from enum import StrEnum
from pathlib import Path

from uga.perception.builder import normalize_visible_text
from uga.perception.schema import NormalizedBox, PerceptionSnapshot, TextRegion

_QUEST_SIMILARITY_THRESHOLD = 0.72
# F15: persisted session state is a small JSON document; anything larger is
# quarantined instead of trusted.
_STATE_SCHEMA = "uga.session-state/1"
_MAX_STATE_BYTES = 64 * 1024
_HEADER_MAX_CENTER_X = 0.28
_HEADER_MIN_CENTER_Y = 0.10
_HEADER_MAX_CENTER_Y = 0.32
_QUEST_MIN_CENTER_Y = 0.20
# 任务面板展开时（分类列表布局），主线任务行会落到远低于紧凑追踪条的位置
# （实测中心 y=0.3805）：条带上限必须留出余量，否则任务采纳永远压线失败。
_QUEST_MAX_CENTER_Y = 0.42
_TASK_HEADER = "主线"
_EXCLUDED_QUEST_LABELS = frozenset(
    {
        "任务",
        "主线",
        "世界",
        "仙遇",
        "经验",
        "追踪",
        "队伍",
        "福利",
        "商城",
    }
)
_DIALOGUE_CUES = ("秒后自动继续", "回顾剧情")
_LEVEL_TARGET_RE = re.compile(r"达到\s*(\d+)\s*级")
_PAGE_LEVEL_RE = re.compile(r"等级\D{0,3}(\d+)")
_QUEST_PROGRESS_RE = re.compile(r"\d+/\d+")
_CHARACTER_NAME_LENGTH_RE = re.compile(r"([1-7])\s*[/／]\s*7")
_ONBOARDING_HOSTILE_CUES = ("黑衣人", "恶灵")
_DEMONIZED_SPIRIT_CUES = ("魔化妖灵", "魔化猪猪", "魔化精怪")
_PEACH_TREE_SPIRIT_CUES = ("桃木精",)
_LOADING_CUES = ("加载中", "正在加载", "loading")
_POPUP_CUES = ("确定", "领取", "关闭")
_TITLE_BAND_MAX_CENTER_X = 0.30
_TITLE_BAND_MAX_CENTER_Y = 0.16
_RECENT_ACTION_LIMIT = 12


def quest_level_target(quest_text: str | None) -> int | None:
    """Parse a numeric level target out of tracked quest text.

    ``拥有1只灵宠达到10级0/1`` yields ``10``; quest text without a 级 target
    yields ``None``.
    """
    if not quest_text:
        return None
    match = _LEVEL_TARGET_RE.search(quest_text)
    return int(match.group(1)) if match else None


QUEST_PAGE_KEYWORDS: tuple[str, ...] = (
    "灵宠",
    "坐骑",
    "法宝",
    "功法",
    "装备",
    "称号",
    "时装",
)


def quest_page_keyword(quest_text: str | None) -> str | None:
    """The game-system keyword a tracked quest is about, if recognisable.

    ``拥有1只灵宠达到10级`` is about 灵宠: any feature page whose OCR never
    mentions 灵宠 does not serve this quest and should be left.
    """
    if not quest_text:
        return None
    for keyword in QUEST_PAGE_KEYWORDS:
        if keyword in quest_text:
            return keyword
    return None


_MARKET_TASK_RE = re.compile(r"出售|摆摊|购买|拍卖")


def quest_is_market_task(quest_text: str | None) -> bool:
    """True when the tracked quest requires selling/buying at the market —
    these tasks progress through the market UI, not by clicking the
    quest-panel text."""
    if not quest_text:
        return False
    return _MARKET_TASK_RE.search(quest_text) is not None


def character_creation_name_prompt_active(regions: Iterable[TextRegion]) -> bool:
    """Whether the initial character-name dialog is visible.

    This is deliberately narrower than a generic ``确定`` dialog.  It keeps
    the onboarding fast path from confirming an empty character name just
    because the generic progress-control rule sees a button with that label.
    """
    return any(
        normalize_visible_text(region.text) == "请输入名字" and region.confidence >= 0.85
        for region in regions
    )


def character_creation_control(
    regions: Iterable[TextRegion],
) -> tuple[str, TextRegion] | None:
    """Return the one verified control for the recorded 仙遇 onboarding flow.

    A match requires both the page's title anchor and its lower-screen button
    in the same latest OCR snapshot.  The name-confirmation step additionally
    requires a nonzero ``N/7`` character counter, so the agent never confirms
    an empty name field.  Unknown variants fall through to the VLM instead of
    being treated as this deterministic flow.
    """
    visible = tuple(regions)

    def has_title(title: str) -> bool:
        return any(
            normalize_visible_text(region.text) == title and region.confidence >= 0.85
            for region in visible
        )

    def lower_button(label: str) -> TextRegion | None:
        matches = [
            region
            for region in visible
            if normalize_visible_text(region.text) == label
            and region.confidence >= 0.85
            and region.box.center.x >= 0.45
            and region.box.center.y >= 0.60
        ]
        if not matches:
            return None
        return max(matches, key=lambda region: region.confidence)

    if has_title("创角"):
        button = lower_button("定制细节")
        if button is not None:
            return "customize", button
    if has_title("选择预设"):
        button = lower_button("开启仙途")
        if button is not None:
            return "start", button
    if has_title("请输入名字") and any(
        (match := _CHARACTER_NAME_LENGTH_RE.fullmatch(region.text.strip()))
        and int(match.group(1)) > 0
        and region.confidence >= 0.85
        for region in visible
    ):
        button = lower_button("确定")
        if button is not None:
            return "confirm_name", button
    return None


def onboarding_joystick_tutorial_active(regions: Iterable[TextRegion]) -> bool:
    """Whether the first-world movement tutorial explicitly asks for the stick.

    The virtual stick itself is graphical, so it cannot be OCR-grounded like a
    normal button.  Its calibrated drag is permitted only while the game's
    own ``滑动摇杆可以移动`` teaching cue is visible in the latest frame.
    """
    return any(
        "滑动摇杆可以移动" in normalize_visible_text(region.text)
        and region.confidence >= 0.85
        for region in regions
    )


def invasion_task_navigation_target(regions: Iterable[TextRegion]) -> TextRegion | None:
    """The left main-quest line that starts the recorded 黑衣人 encounter.

    The task line itself is the game's auto-navigation control.  It is only
    used before enemies appear, so combat frames do not keep reopening the
    tracker instead of using the combat rule.
    """
    visible = tuple(regions)
    has_main_quest = any(
        normalize_visible_text(region.text) == "主线"
        and region.confidence >= 0.85
        and region.box.center.x <= 0.30
        and 0.10 <= region.box.center.y <= 0.35
        for region in visible
    )
    enemies_visible = any(
        any(cue in normalize_visible_text(region.text) for cue in _ONBOARDING_HOSTILE_CUES)
        and region.confidence >= 0.85
        and 0.30 <= region.box.center.x <= 0.75
        and 0.18 <= region.box.center.y <= 0.70
        for region in visible
    )
    if not has_main_quest or enemies_visible:
        return None
    candidates = [
        region
        for region in visible
        if region.confidence >= 0.85
        and region.box.center.x <= 0.35
        and 0.15 <= region.box.center.y <= 0.45
        and (
            "入侵袭击" in normalize_visible_text(region.text)
            or "击败这些不速之客" in normalize_visible_text(region.text)
        )
    ]
    return max(candidates, key=lambda region: region.confidence) if candidates else None


def invasion_group_attack_control(regions: Iterable[TextRegion]) -> TextRegion | None:
    """The 群攻 skill for the recorded 黑衣人/恶灵 main-quest encounters."""
    visible = tuple(regions)
    invasion_task_active = any(
        "入侵袭击" in normalize_visible_text(region.text)
        or "击败这些不速" in normalize_visible_text(region.text)
        for region in visible
    )
    enemy_visible = any(
        any(cue in normalize_visible_text(region.text) for cue in _ONBOARDING_HOSTILE_CUES)
        and region.confidence >= 0.85
        and 0.30 <= region.box.center.x <= 0.75
        and 0.18 <= region.box.center.y <= 0.70
        for region in visible
    )
    if not invasion_task_active or not enemy_visible:
        return None
    skills = [
        region
        for region in visible
        if normalize_visible_text(region.text) == "群攻"
        and region.confidence >= 0.85
        and 0.55 <= region.box.center.x <= 0.90
        and 0.70 <= region.box.center.y <= 0.98
    ]
    return max(skills, key=lambda region: region.confidence) if skills else None


def invasion_combat_active(regions: Iterable[TextRegion]) -> bool:
    """The recorded invasion fight, independent of OCR on the skill icon.

    Enemy names and the tracked quest are large, reliable anchors.  The tiny
    ``群攻`` label is frequently hidden by combat effects, so it must not be a
    prerequisite for using the owner-calibrated skill hotspot.
    """
    visible = tuple(regions)
    task_active = any(
        (
            "入侵袭击" in normalize_visible_text(region.text)
            or "击败这些不速" in normalize_visible_text(region.text)
        )
        and region.confidence >= 0.75
        for region in visible
    )
    enemy_visible = any(
        any(cue in normalize_visible_text(region.text) for cue in _ONBOARDING_HOSTILE_CUES)
        and region.confidence >= 0.75
        and 0.25 <= region.box.center.x <= 0.82
        and 0.14 <= region.box.center.y <= 0.75
        for region in visible
    )
    return task_active and enemy_visible


def red_dust_auto_enable_ready(regions: Iterable[TextRegion]) -> bool:
    """Whether the recorded 红尘入世 step is asking to enable ``自动`` once.

    The Auto control itself is graphical and remains visible after it is
    enabled, so the narrow first subquest (红尘入世 + 与师姐一起) is the page
    anchor. Persistent session state prevents a later subquest from clicking
    the same toggle again.
    """
    visible = tuple(regions)
    task_regions = tuple(
        region
        for region in visible
        if region.confidence >= 0.75
        and region.box.center.x <= 0.35
        and 0.14 <= region.box.center.y <= 0.45
    )
    has_red_dust = any(
        "红尘入世" in normalize_visible_text(region.text) for region in task_regions
    )
    has_senior_sister_step = any(
        "与师姐一起" in normalize_visible_text(region.text)
        or (
            "师姐" in normalize_visible_text(region.text)
            and "一起" in normalize_visible_text(region.text)
        )
        for region in task_regions
    )
    return has_red_dust and has_senior_sister_step


def demonized_spirit_combat_active(regions: Iterable[TextRegion]) -> bool:
    """Recorded 魔化精怪 fight whose first action is the pet group skill."""
    visible = tuple(regions)
    task_active = any(
        any(
            cue in normalize_visible_text(region.text)
            for cue in ("魔化精怪", "制服魔化妖灵")
        )
        and region.confidence >= 0.75
        and region.box.center.x <= 0.38
        for region in visible
    )
    enemy_visible = any(
        any(cue in normalize_visible_text(region.text) for cue in _DEMONIZED_SPIRIT_CUES)
        and region.confidence >= 0.75
        and 0.25 <= region.box.center.x <= 0.85
        and 0.14 <= region.box.center.y <= 0.75
        for region in visible
    )
    return task_active and enemy_visible


def peach_tree_spirit_combat_active(regions: Iterable[TextRegion]) -> bool:
    """Recorded 暴虐精怪 fight with 桃木精 enemies on screen."""
    visible = tuple(regions)
    task_active = any(
        any(
            cue in normalize_visible_text(region.text)
            for cue in ("暴虐精怪", "制服桃木精")
        )
        and region.confidence >= 0.75
        and region.box.center.x <= 0.38
        for region in visible
    )
    enemy_visible = any(
        any(cue in normalize_visible_text(region.text) for cue in _PEACH_TREE_SPIRIT_CUES)
        and region.confidence >= 0.75
        and 0.25 <= region.box.center.x <= 0.90
        and 0.14 <= region.box.center.y <= 0.75
        for region in visible
    )
    return task_active and enemy_visible


def raging_tree_spirit_combat_active(regions: Iterable[TextRegion]) -> bool:
    """Recorded 狂暴树精 fight against the 千年桃木精 boss."""
    visible = tuple(regions)
    task_active = any(
        any(
            cue in normalize_visible_text(region.text)
            for cue in ("狂暴树精", "制服狂暴的树精")
        )
        and region.confidence >= 0.75
        and region.box.center.x <= 0.38
        for region in visible
    )
    enemy_visible = any(
        "千年桃木精" in normalize_visible_text(region.text)
        and region.confidence >= 0.75
        and 0.25 <= region.box.center.x <= 0.85
        and 0.08 <= region.box.center.y <= 0.75
        for region in visible
    )
    return task_active and enemy_visible


def demon_sect_disciple_combat_active(regions: Iterable[TextRegion]) -> bool:
    """Recorded 孤身应战 fight against three 魔宗门徒 enemies."""
    visible = tuple(regions)
    task_active = any(
        any(
            cue in normalize_visible_text(region.text)
            for cue in ("孤身应战", "击败魔宗门徒")
        )
        and region.confidence >= 0.75
        and region.box.center.x <= 0.38
        for region in visible
    )
    enemy_visible = any(
        "魔宗门徒" in normalize_visible_text(region.text)
        and region.confidence >= 0.75
        and 0.25 <= region.box.center.x <= 0.90
        and 0.12 <= region.box.center.y <= 0.75
        for region in visible
    )
    return task_active and enemy_visible


def pet_training_entry_control(regions: Iterable[TextRegion]) -> TextRegion | None:
    """Right-side 灵宠 entry for the recorded upgrade/star-up tasks."""
    visible = tuple(regions)
    task_active = any(
        any(
            cue in normalize_visible_text(region.text)
            for cue in (
                "小龙升级",
                "拥有1只灵宠达到2级",
                "小龙合体",
                "灵宠达到4星",
                "灵宠达到四星",
            )
        )
        and region.confidence >= 0.75
        and region.box.center.x <= 0.38
        for region in visible
    )
    if not task_active:
        return None
    candidates = [
        region
        for region in visible
        if normalize_visible_text(region.text) == "灵宠"
        and region.confidence >= 0.80
        and region.box.center.x >= 0.85
        and 0.25 <= region.box.center.y <= 0.75
    ]
    return max(candidates, key=lambda region: region.confidence) if candidates else None


def pet_information_tab_control(regions: Iterable[TextRegion]) -> TextRegion | None:
    """Information tab on the pet formation page, not the already-open info page."""
    visible = tuple(regions)
    has_pet_title = any(
        normalize_visible_text(region.text) == "灵宠"
        and region.confidence >= 0.80
        and region.box.center.x <= 0.25
        and region.box.center.y <= 0.18
        for region in visible
    )
    has_formation_page = any(
        any(
            cue in normalize_visible_text(region.text)
            for cue in ("布阵目标", "布阵总修为", "推荐阵容")
        )
        and region.confidence >= 0.75
        for region in visible
    )
    if not has_pet_title or not has_formation_page:
        return None
    candidates = [
        region
        for region in visible
        if normalize_visible_text(region.text) == "信息"
        and region.confidence >= 0.80
        and region.box.center.x >= 0.85
        and 0.20 <= region.box.center.y <= 0.60
    ]
    return max(candidates, key=lambda region: region.confidence) if candidates else None


def pet_upgrade_control(
    regions: Iterable[TextRegion], quest_target_level: int | None
) -> TextRegion | None:
    """One pet-upgrade button while the page level is below the task target."""
    if quest_target_level is None:
        return None
    visible = tuple(regions)
    current_level = page_level_value(visible)
    if current_level is None or current_level >= quest_target_level:
        return None
    has_pet_title = any(
        normalize_visible_text(region.text) == "灵宠"
        and region.confidence >= 0.80
        and region.box.center.x <= 0.25
        and region.box.center.y <= 0.18
        for region in visible
    )
    has_info_page = any(
        "基础属性" in normalize_visible_text(region.text)
        and region.confidence >= 0.75
        for region in visible
    )
    if not has_pet_title or not has_info_page:
        return None
    candidates = [
        region
        for region in visible
        if re.fullmatch(r"(?:升\d+级|升级)", normalize_visible_text(region.text))
        and region.confidence >= 0.80
        and region.box.center.x >= 0.75
        and 0.12 <= region.box.center.y <= 0.48
    ]
    return max(candidates, key=lambda region: region.confidence) if candidates else None


def quest_is_pet_star_task(quest_text: str | None) -> bool:
    """Whether the durable tracked task is the recorded four-star pet task."""
    if not quest_text:
        return False
    normalized = normalize_visible_text(quest_text)
    return any(
        cue in normalized
        for cue in ("小龙合体", "灵宠达到4星", "灵宠达到四星")
    )


def pet_star_menu_control(regions: Iterable[TextRegion]) -> TextRegion | None:
    """Collapsed world-page menu for the recorded four-star pet task."""
    visible = tuple(regions)
    task_active = any(
        any(
            cue in normalize_visible_text(region.text)
            for cue in ("小龙合体", "灵宠达到4星", "灵宠达到四星")
        )
        and region.confidence >= 0.75
        and region.box.center.x <= 0.38
        for region in visible
    )
    if not task_active:
        return None
    candidates = [
        region
        for region in visible
        if normalize_visible_text(region.text) == "菜单"
        and region.confidence >= 0.80
        and region.box.center.x >= 0.85
        and 0.20 <= region.box.center.y <= 0.55
    ]
    return max(candidates, key=lambda region: region.confidence) if candidates else None


def _pet_title_visible(regions: Iterable[TextRegion]) -> bool:
    return any(
        normalize_visible_text(region.text) == "灵宠"
        and region.confidence >= 0.80
        and region.box.center.x <= 0.25
        and region.box.center.y <= 0.18
        for region in regions
    )


def _pet_star_page_visible(regions: Iterable[TextRegion]) -> bool:
    visible = tuple(regions)
    return _pet_title_visible(visible) and any(
        cue in normalize_visible_text(region.text)
        and region.confidence >= 0.75
        for region in visible
        for cue in ("技能升级", "成长率")
    )


def pet_star_tab_control(
    regions: Iterable[TextRegion], *, task_active: bool
) -> TextRegion | None:
    """Right-side 升星 tab before the star-up page itself is active."""
    if not task_active:
        return None
    visible = tuple(regions)
    if not _pet_title_visible(visible) or _pet_star_page_visible(visible):
        return None
    candidates = [
        region
        for region in visible
        if normalize_visible_text(region.text) == "升星"
        and region.confidence >= 0.80
        and region.box.center.x >= 0.90
        and 0.25 <= region.box.center.y <= 0.70
    ]
    return max(candidates, key=lambda region: region.confidence) if candidates else None


def pet_star_action_control(
    regions: Iterable[TextRegion], *, task_active: bool
) -> TextRegion | None:
    """Bottom 升星 action on the active star-up page.

    This control is deliberately separate from the same-labelled right-side
    tab.  It is valid both before material selection and after the 3/3
    selection has been confirmed.
    """
    if not task_active:
        return None
    visible = tuple(regions)
    if not _pet_star_page_visible(visible):
        return None
    candidates = [
        region
        for region in visible
        if normalize_visible_text(region.text) == "升星"
        and region.confidence >= 0.80
        and 0.65 <= region.box.center.x <= 0.90
        and region.box.center.y >= 0.70
    ]
    return max(candidates, key=lambda region: region.confidence) if candidates else None


def pet_star_material_control(
    regions: Iterable[TextRegion], *, task_active: bool
) -> tuple[str, TextRegion] | None:
    """Material-dialog action: first 一键放入, then 确定 at 3/3."""
    if not task_active:
        return None
    visible = tuple(regions)
    has_dialog_title = any(
        normalize_visible_text(region.text) == "升星"
        and region.confidence >= 0.80
        and region.box.center.x <= 0.40
        and 0.15 <= region.box.center.y <= 0.40
        for region in visible
    )
    has_material_requirement = any(
        "需要" in normalize_visible_text(region.text)
        and "灵宠" in normalize_visible_text(region.text)
        and region.confidence >= 0.75
        for region in visible
    )
    if not has_dialog_title or not has_material_requirement:
        return None
    selected_all = any(
        (
            re.search(r"3\s*[/／]\s*3", region.text)
            or "已选中33" in normalize_visible_text(region.text)
        )
        and region.confidence >= 0.75
        for region in visible
    )
    target = "确定" if selected_all else "一键放入"
    candidates = [
        region
        for region in visible
        if normalize_visible_text(region.text) == target
        and region.confidence >= 0.80
        and 0.25 <= region.box.center.x <= 0.80
        and region.box.center.y >= 0.65
    ]
    if not candidates:
        return None
    return (
        "confirm" if selected_all else "autofill",
        max(candidates, key=lambda region: region.confidence),
    )


def pet_star_success_continue_control(
    regions: Iterable[TextRegion], *, task_active: bool
) -> TextRegion | None:
    """The single dismiss click on the four-star success presentation."""
    if not task_active:
        return None
    visible = tuple(regions)
    success = any(
        "升星成功" in normalize_visible_text(region.text)
        and region.confidence >= 0.80
        for region in visible
    )
    if not success:
        return None
    candidates = [
        region
        for region in visible
        if "点击任意" in normalize_visible_text(region.text)
        and "关闭" in normalize_visible_text(region.text)
        and region.confidence >= 0.75
        and region.box.center.y >= 0.70
    ]
    return max(candidates, key=lambda region: region.confidence) if candidates else None


def quest_is_skill_learning_task(quest_text: str | None) -> bool:
    """Whether the durable task is the recorded third-skill tutorial."""
    if not quest_text:
        return False
    normalized = normalize_visible_text(quest_text)
    return "招式传授" in normalized or "学习第3个技能" in normalized


def skill_training_entry_control(regions: Iterable[TextRegion]) -> TextRegion | None:
    """Right-side 技能 entry while the third-skill tutorial is tracked."""
    visible = tuple(regions)
    task_active = any(
        any(
            cue in normalize_visible_text(region.text)
            for cue in ("招式传授", "学习第3个技能")
        )
        and region.confidence >= 0.75
        and region.box.center.x <= 0.38
        for region in visible
    )
    if not task_active:
        return None
    candidates = [
        region
        for region in visible
        if normalize_visible_text(region.text) == "技能"
        and region.confidence >= 0.80
        and region.box.center.x >= 0.85
        and 0.25 <= region.box.center.y <= 0.75
    ]
    return max(candidates, key=lambda region: region.confidence) if candidates else None


def _skill_upgrade_page_visible(regions: Iterable[TextRegion]) -> bool:
    visible = tuple(regions)
    has_title = any(
        normalize_visible_text(region.text) == "升级"
        and region.confidence >= 0.80
        and region.box.center.x <= 0.25
        and region.box.center.y <= 0.18
        for region in visible
    )
    has_skill_points = any(
        "技能点" in normalize_visible_text(region.text)
        and region.confidence >= 0.75
        and region.box.center.y <= 0.30
        for region in visible
    )
    return has_title and has_skill_points


def skill_treatment_node_control(
    regions: Iterable[TextRegion], *, task_active: bool
) -> TextRegion | None:
    """The tutorial-highlighted third 治疗 node, not another healing node."""
    if not task_active:
        return None
    visible = tuple(regions)
    if not _skill_upgrade_page_visible(visible):
        return None
    if any("花语素心" in normalize_visible_text(region.text) for region in visible):
        return None
    candidates = [
        region
        for region in visible
        if normalize_visible_text(region.text) == "治疗"
        and region.confidence >= 0.80
        and 0.25 <= region.box.center.x <= 0.42
        and 0.55 <= region.box.center.y <= 0.82
    ]
    return max(candidates, key=lambda region: region.confidence) if candidates else None


def skill_learn_control(
    regions: Iterable[TextRegion], *, task_active: bool
) -> TextRegion | None:
    """Learn 花语素心 once while its displayed level is still zero."""
    if not task_active:
        return None
    visible = tuple(regions)
    if not _skill_upgrade_page_visible(visible):
        return None
    unlearned = any(
        re.search(r"花语素心\D{0,3}0级", normalize_visible_text(region.text))
        and region.confidence >= 0.75
        for region in visible
    )
    if not unlearned:
        return None
    candidates = [
        region
        for region in visible
        if "学习" in normalize_visible_text(region.text)
        and region.confidence >= 0.80
        and region.box.center.x >= 0.65
        and region.box.center.y >= 0.70
    ]
    return max(candidates, key=lambda region: region.confidence) if candidates else None


def skill_training_complete(
    regions: Iterable[TextRegion], *, task_active: bool
) -> bool:
    """The requested 花语素心 skill visibly reached level one."""
    if not task_active:
        return False
    visible = tuple(regions)
    return _skill_upgrade_page_visible(visible) and any(
        re.search(r"花语素心\D{0,3}[1-9]\d*级", normalize_visible_text(region.text))
        and region.confidence >= 0.75
        for region in visible
    )


def auto_navigation_active(regions: Iterable[TextRegion]) -> bool:
    """Whether the game is already carrying the character to a quest target."""
    return any(
        "自动寻路中" in normalize_visible_text(region.text) and region.confidence >= 0.85
        for region in regions
    )


def cutscene_skip_control(regions: Iterable[TextRegion]) -> TextRegion | None:
    """Return the recorded top-right cutscene skip control, if it is present.

    ``跳过`` is a common word, so it is not enough to match its OCR text.  The
    recorded control is a short, high-confidence label at the *upper right of
    the game client*, beneath MuMu's protected title strip.  This deliberately
    rejects ordinary in-game text and anything in the emulator chrome.
    """
    candidates = [
        region
        for region in regions
        if normalize_visible_text(region.text) == "跳过"
        and region.confidence >= 0.85
        and region.box.center.x >= 0.85
        and 0.065 <= region.box.center.y <= 0.20
    ]
    return max(candidates, key=lambda region: region.confidence) if candidates else None


def dialogue_review_visible(regions: Iterable[TextRegion]) -> bool:
    """Recognize the dialogue scene from its left-side ``回顾剧情`` affordance.

    The review button is an anchor only: clicking it would leave the dialogue.
    A separately user-confirmed lower-right hotspot advances the dialogue.
    """
    return any(
        "回顾剧情" in normalize_visible_text(region.text)
        and region.confidence >= 0.85
        and region.box.center.x <= 0.15
        and 0.25 <= region.box.center.y <= 0.70
        for region in regions
    )


def rescue_little_dragon_choice(regions: Iterable[TextRegion]) -> TextRegion | None:
    """Return the confirmed rescue choice shown in the 小青龙 dialogue."""
    candidates = [
        region
        for region in regions
        if normalize_visible_text(region.text) == "拯救小龙"
        and region.confidence >= 0.85
        and region.box.center.x >= 0.60
        and 0.60 <= region.box.center.y <= 0.90
    ]
    return max(candidates, key=lambda region: region.confidence) if candidates else None


def little_dragon_healing_active(regions: Iterable[TextRegion]) -> bool:
    """Whether the recorded central 小青龙 healing interaction is on screen.

    The creature is graphical and therefore uses a calibrated hotspot.  Both
    independent OCR anchors are required so the same central point cannot be
    clicked on an ordinary world or dialogue screen.
    """
    visible = tuple(regions)
    return any(
        "拯救重伤的小青龙" in normalize_visible_text(region.text)
        and region.confidence >= 0.85
        for region in visible
    ) and any(
        "传功疗伤" in normalize_visible_text(region.text)
        and region.confidence >= 0.85
        for region in visible
    )


def narrative_continue_control(regions: Iterable[TextRegion]) -> TextRegion | None:
    """Return a lower-screen ``点击任意处继续`` story-completion control."""
    candidates = [
        region
        for region in regions
        if "点击任意处继续" in normalize_visible_text(region.text)
        and region.confidence >= 0.85
        and region.box.center.y >= 0.70
    ]
    return max(candidates, key=lambda region: region.confidence) if candidates else None


def peach_talisman_continue_control(regions: Iterable[TextRegion]) -> TextRegion | None:
    """Dismiss the 桃妖符印 reward presentation without waiting for timeout."""
    visible = tuple(regions)
    has_reward = any(
        "桃妖符印" in normalize_visible_text(region.text)
        and region.confidence >= 0.80
        for region in visible
    )
    if not has_reward:
        return None
    candidates = [
        region
        for region in visible
        if "点击任意" in normalize_visible_text(region.text)
        and "关闭" in normalize_visible_text(region.text)
        and region.confidence >= 0.75
        and region.box.center.y >= 0.70
    ]
    return max(candidates, key=lambda region: region.confidence) if candidates else None


def peach_talisman_barrier_active(regions: Iterable[TextRegion]) -> bool:
    """Whether the 桃天符印 drag-to-centre barrier interaction is visible.

    The gem and magic-circle centre are graphical, so the drag is calibrated
    from the owner's screenshots.  Requiring both instruction and countdown
    anchors keeps that graphical gesture confined to this one mini-game.
    """
    visible = tuple(regions)
    has_instruction = any(
        "用桃天符印开启结界" in normalize_visible_text(region.text)
        and region.confidence >= 0.80
        for region in visible
    )
    has_countdown = any(
        "秒后将自动完成" in normalize_visible_text(region.text)
        and region.confidence >= 0.75
        and region.box.center.y >= 0.75
        for region in visible
    )
    return has_instruction and has_countdown


def find_xiuxian_path_quest_line(regions: Iterable[TextRegion]) -> TextRegion | None:
    """The clickable 修仙之路 quest-tracker line (clicking it auto-navigates).

    Owner-confirmed: tapping the top-left task-panel quest row jumps straight
    to the 修仙之路 interface where the objectives are clicked.  The line must
    carry 目标/progress (``完成2个修仙之路目标0/2``) so the bare section
    header ``修仙之路`` in the expanded quest panel never matches.
    """
    best: TextRegion | None = None
    for region in regions:
        center = region.box.center
        if region.confidence < 0.9 or center.x > 0.30:
            continue
        if not 0.15 <= center.y <= 0.45:
            continue
        text = normalize_visible_text(region.text)
        if "修仙之路" not in text:
            continue
        if "目标" not in text and not _QUEST_PROGRESS_RE.search(text):
            continue
        if best is None or region.confidence > best.confidence:
            best = region
    return best


def xiuxian_path_objective_goto(regions: Iterable[TextRegion]) -> TextRegion | None:
    """The 前往 button of the topmost 修仙之路 objective row.

    The 修仙之路 interface lists day objectives (``【装备】完成3次30级装备
    秘境 (0/3)`` …) each with a 前往 button just below-right of the text; the
    objective TEXT itself is not clickable (the model's clicks there never
    verified).  The big 修仙之路 header anchors the interface.
    """
    has_title = any(
        "修仙之路" in region.text
        and region.box.center.x > 0.35
        and region.confidence >= 0.9
        for region in regions
    )
    if not has_title:
        return None
    gotos = [
        region
        for region in regions
        if normalize_visible_text(region.text) == "前往"
        and region.box.center.x > 0.70
        and region.confidence >= 0.9
    ]
    if not gotos:
        return None
    best: tuple[float, TextRegion] | None = None
    for region in regions:
        row_y = region.box.center.y
        if (
            region.confidence < 0.9
            or not 0.30 <= region.box.center.x <= 0.60
            or "完成" not in region.text
        ):
            continue
        button = min(
            gotos,
            key=lambda goto: abs(goto.box.center.y - row_y),
        )
        if abs(button.box.center.y - row_y) > 0.05:
            continue
        if best is None or row_y < best[0]:
            best = (row_y, button)
    if best is None:
        return None
    return best[1]


def find_market_entry(regions: Iterable[TextRegion]) -> TextRegion | None:
    """The 市场 (market) entry button, if OCR can see it.

    Excludes the quest-panel area (tracker text lives there) and the MuMu
    chrome row.
    """
    best: TextRegion | None = None
    for region in regions:
        center = region.box.center
        if center.y < 0.05:
            continue
        if normalize_visible_text(region.text) != "市场":
            continue
        if region.confidence < 0.8:
            continue
        if center.x <= 0.32 and center.y <= 0.40:
            continue  # quest-panel/tracker area
        if best is None or region.confidence > best.confidence:
            best = region
    return best


def stall_sell_item_cell(regions: Iterable[TextRegion]) -> TextRegion | None:
    """The level label of the first item cell on the stall sell page.

    The stall grid shows items with a ``20级``-style level label under the
    icon; OCR cannot read the icon itself, so the label anchors the click
    (the caller aims slightly above it at the icon).
    """
    has_stall_markers = any(
        "我要出售" in region.text or "我的物品" in region.text for region in regions
    )
    if not has_stall_markers:
        return None
    best: TextRegion | None = None
    for region in regions:
        text = region.text.strip()
        if not re.fullmatch(r"\d+级", text):
            continue
        center = region.box.center
        if center.x > 0.35 or not 0.30 <= center.y <= 0.85:
            continue
        if region.confidence < 0.9:
            continue
        if best is None or center.y < best.box.center.y:
            best = region
    return best


def page_level_value(regions: Iterable[TextRegion]) -> int | None:
    """The strongest 等级 N/… number visible on the current page.

    Feature pages display the pet level as ``等级 10/40``; unrelated numeric
    labels (counters, ratios) do not carry the 等级 marker and are ignored.
    """
    best: int | None = None
    for region in regions:
        if "等级" not in region.text:
            continue
        match = _PAGE_LEVEL_RE.search(region.text)
        if match is None:
            continue
        value = int(match.group(1))
        if best is None or value > best:
            best = value
    return best


_CLOSE_GLYPHS = ("×", "✕", "✖")
_CLOSE_GLYPHS_LATIN_STANDALONE = ("X", "x")
_MONETIZATION_CUES = ("充值", "首充", "礼包", "特惠", "限时抢购", "超值")
# 广告/运营诱饵横幅（首充礼包、新服冲榜、商城福利、问卷兑换…）对主线推进
# 毫无意义，但视觉模型总觉得它们醒目——规则层按坐标直接拒绝。
_DECOY_CUES = (
    "充值",
    "首充",
    "礼包",
    "特惠",
    "限时抢购",
    "超值",
    "新服",
    "冲榜",
    "开服",
    "广告",
    "福利",
    "商城",
    "问卷",
    "兑换",
    "页游",
)


def real_name_gate_active(regions: Iterable[TextRegion]) -> bool:
    """True when the game's mandatory real-name registration form is up.

    实名登记/防沉迷 is a legal account-level gate that requires the OWNER's
    personal identity data: the agent must never fill it (PII) — it stands
    by until the owner completes the form and the game moves on.
    """
    return any(
        ("实名登记" in region.text or "防沉迷" in region.text)
        and region.confidence >= 0.8
        for region in regions
    )


def decoy_click_blocked(
    regions: Iterable[TextRegion], point: tuple[float, float]
) -> bool:
    """True when the click point lands on an advertising/monetization decoy."""
    x, y = point
    for region in regions:
        if region.confidence < 0.5:
            continue
        if not any(cue in region.text for cue in _DECOY_CUES):
            continue
        box = region.box
        if (
            box.left - 0.01 <= x <= box.right + 0.01
            and box.top - 0.01 <= y <= box.bottom + 0.01
        ):
            return True
    return False


def find_close_glyph(regions: Iterable[TextRegion]) -> tuple[TextRegion, bool] | None:
    """An OCR-visible popup close glyph (×) with an aim-right flag.

    MuMu window chrome (title-bar row) and quantity labels (``元宝×500``) are
    excluded; only upper-right short labels count.
    """
    best: tuple[float, TextRegion, bool] | None = None
    for region in regions:
        center = region.box.center
        if center.y < 0.05 or center.y > 0.6 or center.x <= 0.5:
            continue
        text = region.text.strip()
        if not text or len(text) > 8:
            continue
        if (region.box.right - region.box.left) > 0.25:
            continue
        for glyph in _CLOSE_GLYPHS:
            if text == glyph:
                candidate: tuple[float, TextRegion, bool] | None = (
                    region.confidence,
                    region,
                    False,
                )
            elif text.endswith(glyph):
                tail = text[text.rfind(glyph) :]
                if any(ch.isdigit() for ch in tail):
                    continue  # 元宝×500 style quantity label
                candidate = (region.confidence, region, True)
            else:
                continue
            break
        else:
            # OCR often reads the graphical close X as the Latin letter X/x:
            # accept it only as a standalone single character (never as an
            # English-word suffix like MAX/EXP).
            if text in _CLOSE_GLYPHS_LATIN_STANDALONE:
                candidate = (region.confidence, region, False)
            else:
                continue
        if candidate is not None and (best is None or candidate[0] > best[0]):
            best = candidate
    if best is None:
        return None
    return best[1], best[2]


def close_glyph_aim(region: TextRegion) -> tuple[float, float]:
    """Click point for a close-glyph region (right-edge aware)."""
    text = region.text.strip()
    center = region.box.center
    if text.endswith("×") or text.endswith("✕") or text.endswith("✖"):
        width = region.box.right - region.box.left
        return (region.box.right - width * 0.12, center.y)
    return (center.x, center.y)


def page_mentions_monetization(regions: Iterable[TextRegion]) -> bool:
    """True when OCR shows a monetization popup (充值/首充/礼包…)."""
    return any(
        any(cue in region.text for cue in _MONETIZATION_CUES) for region in regions
    )


_ACTION_BUTTON_TERMS = (
    "上架",
    "出售",
    "确定",
    "领取",
    "挑战",
    "强化",
    "升级",
    "升星",
    "一键放入",
    "购买",
    "使用",
    "前往",
    "开始",
)


def page_has_action_button(regions: Iterable[TextRegion]) -> bool:
    """True when a short label is an actionable button (上架/确定/领取…).

    Pages with actionable buttons are workflows to operate, not popups to
    dismiss — the close-glyph path must not fire on them.  Long sentences
    (chat, quest text mentioning the words) do not count.
    """
    for region in regions:
        text = region.text.strip()
        if len(text) > 6:
            continue
        if any(term in text for term in _ACTION_BUTTON_TERMS):
            return True
    return False


_MUMU_DIALOG_CUES = ("确定要关闭", "不再提示", "MuMu安卓设备")


def mumu_close_dialog_cancel(regions: Iterable[TextRegion]) -> TextRegion | None:
    """The 取消 button of MuMu's own "确定要关闭 MuMu安卓设备-1 吗?" dialog.

    A stray click on the emulator's title-bar ✕ pops this native confirmation
    over the game.  The only safe dismissal is 取消 — 确定 closes the emulator
    and kills the run.  Marker cues are MuMu-specific; both marker and button
    must sit below the title-bar strip so the always-present tab row (which
    also renders the device name) cannot match.
    """
    marker = any(
        any(cue in region.text for cue in _MUMU_DIALOG_CUES)
        and region.box.center.y > 0.08
        for region in regions
    )
    if not marker:
        return None
    for region in regions:
        if (
            normalize_visible_text(region.text) == "取消"
            and region.box.center.y > 0.08
            and region.confidence >= 0.8
        ):
            return region
    return None


def realm_promotion_ready(regions: Iterable[TextRegion]) -> bool:
    """True on the 境界 page when its objectives are all marked 已完成.

    The 晋升 medallion on this page is a graphical control OCR cannot read,
    so the rule layer (not the vision model) decides when to press it: the
    page title 境界 in the header area plus at least two 已完成 objectives.
    """
    has_title = any(
        normalize_visible_text(region.text) == "境界"
        and region.box.center.x <= 0.30
        and region.confidence > 0.9
        for region in regions
    )
    done_count = sum(1 for region in regions if "已完成" in region.text)
    return has_title and done_count >= 2


def stable_anchor_tokens(values: Iterable[str]) -> frozenset[str]:
    """Digit-stripped tokens for effect detection.

    ``770/15`` normalizes to ``77015`` (dropped), while ``修为1814`` and
    ``修为770/15`` both collapse to ``修为`` so counter jitter cannot fake an
    action effect.  Comparing the stripped forms keeps quest and page text
    comparable while ignoring every numeric-only flicker.
    """
    tokens: set[str] = set()
    for value in values:
        normalized = normalize_visible_text(value)
        if not normalized:
            continue
        alpha = re.sub(r"\d+", "", normalized, flags=re.UNICODE)
        if len(alpha) < 2:
            continue
        tokens.add(alpha)
    return frozenset(tokens)


def page_anchor_signature(regions: Iterable[TextRegion]) -> frozenset[str]:
    """Short high-confidence labels inside the page-header band.

    The band anchors on the page title (灵宠/召唤/布阵/境界 …).  Its presence
    or absence is a stable page-transition signal that survives OCR flicker in
    the numeric and marquee areas.
    """
    band: list[str] = []
    for region in regions:
        center = region.box.center
        if center.x > _TITLE_BAND_MAX_CENTER_X or center.y > _TITLE_BAND_MAX_CENTER_Y:
            continue
        if region.confidence <= 0.75:
            continue
        band.append(region.text)
    return stable_anchor_tokens(band)


class ScreenType(StrEnum):
    WORLD = "world"
    DIALOGUE = "dialogue"
    LOADING = "loading"
    FEATURE = "feature"
    POPUP = "popup"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class MainQuestSnapshot:
    """The latest tracked main-quest text with its provenance."""

    raw_text: str
    canonical_text: str
    box: NormalizedBox
    confidence: float
    first_seen_frame_id: str
    last_seen_frame_id: str
    observed_at_ns: int
    generation: int
    # F15: a quest restored from disk is UNTRUSTED_RESTORED — it may be shown
    # and used for retrieval, but fast-path actions wait until two fresh
    # frames re-confirm the same text on the live tracker.
    restored: bool = False
    verified_frames: int = 0


@dataclass(frozen=True, slots=True)
class ActionTrace:
    """One physically supervised action from proposal to verified effect."""

    action_id: str
    source: str
    proposed_label: str
    proposed_box: tuple[float, float, float, float] | None
    final_label: str
    final_box: tuple[float, float, float, float] | None
    point: tuple[float, float] | None
    physical_point: tuple[float, float] | None = None
    primitives: tuple[str, ...] = ()
    supervisor_verdict: str = "accepted"
    submitted: bool = False
    effect: str = "pending"
    detail: str = ""
    created_at_ns: int = 0
    updated_at_ns: int = 0


@dataclass(slots=True)
class GameSessionState:
    """Cross-frame quest/page memory updated from every perception snapshot."""

    latest_main_task: MainQuestSnapshot | None = None
    screen_type: ScreenType = ScreenType.UNKNOWN
    feature_page: str | None = None
    dialogue_active: bool = False
    recent_actions: deque[ActionTrace] = field(
        default_factory=lambda: deque(maxlen=_RECENT_ACTION_LIMIT)
    )
    last_verified_progress_at_ns: int | None = None
    persistence_path: Path | None = None
    _candidate_raw: str | None = None
    _candidate_frame_id: str | None = None
    _last_observed_frame: tuple[str, int] | None = None
    # F08: the single task-generation source.  Quest identity changes bump it
    # and every request/snapshot stamped with the old generation is stale.
    task_generation: int = 1
    # F15: persistence namespace and health.
    profile_id: str | None = None
    last_persistence_error: str | None = None
    restored_from_disk: bool = False
    # The user-confirmed bottom-centre 自动 toggle is a one-shot onboarding
    # action. It is persisted only after every click primitive reaches the OS.
    auto_combat_enabled: bool = False
    # The barrier page keeps the same OCR anchors after the gem reaches the
    # centre. Persist this receipt-backed flag so that the held drag is not
    # repeated while the page's completion countdown continues.
    peach_talisman_barrier_dragged: bool = False

    @property
    def restored_task_unverified(self) -> bool:
        """True while a restored quest has not been re-confirmed on screen."""
        quest = self.latest_main_task
        return bool(
            quest is not None and quest.restored and quest.verified_frames < 2
        )

    def set_persistence(
        self, path: Path, *, profile_id: str | None = None
    ) -> None:
        """Keep the tracked quest across agent restarts: load now, save on change."""
        self.persistence_path = path
        self.profile_id = profile_id
        self._load_quest_memory(path)

    def _load_quest_memory(self, path: Path) -> None:
        import json

        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError:
            return
        if len(raw_text.encode("utf-8")) > _MAX_STATE_BYTES:
            self._isolate_corrupt_state(path, "state file exceeds the size cap")
            return
        try:
            payload = json.loads(raw_text)
            if str(payload.get("schema", "")) != _STATE_SCHEMA:
                raise ValueError("unsupported session-state schema")
            stored_profile = payload.get("profile_id")
            if (
                self.profile_id is not None
                and stored_profile is not None
                and stored_profile != self.profile_id
            ):
                # F15 namespace isolation: the file belongs to another
                # profile — ignore it instead of cross-wiring tasks.
                self.last_persistence_error = (
                    f"state file belongs to profile {stored_profile!r}; ignored"
                )
                return
            raw_value = payload.get("raw_text")
            raw = None if raw_value is None else str(raw_value)
            canonical = None if raw is None else str(payload.get("canonical_text") or raw)
            generation = int(payload.get("generation", 0))
            auto_combat_enabled = bool(payload.get("auto_combat_enabled", False))
            peach_talisman_barrier_dragged = bool(
                payload.get("peach_talisman_barrier_dragged", False)
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self._isolate_corrupt_state(path, f"corrupt state file: {exc}")
            return
        if raw is not None and canonical is not None:
            self.latest_main_task = MainQuestSnapshot(
                raw_text=raw,
                canonical_text=canonical,
                box=NormalizedBox(0.06, _QUEST_MIN_CENTER_Y, 0.22, 0.30),
                confidence=0.5,
                first_seen_frame_id="restored",
                last_seen_frame_id="restored",
                observed_at_ns=0,
                generation=generation,
                restored=True,
                verified_frames=0,
            )
        self.auto_combat_enabled = auto_combat_enabled
        self.peach_talisman_barrier_dragged = peach_talisman_barrier_dragged
        self.restored_from_disk = True

    def _isolate_corrupt_state(self, path: Path, why: str) -> None:
        """F15: quarantine a broken state file and start from empty memory."""
        try:
            diagnostic = path.with_name(f"{path.name}.corrupt-{time.time_ns()}")
            path.replace(diagnostic)
            self.last_persistence_error = f"{why}; isolated to {diagnostic.name}"
        except OSError as exc:
            self.last_persistence_error = f"{why}; quarantine failed: {exc}"

    def _save_quest_memory(self) -> None:
        quest = self.latest_main_task
        if self.persistence_path is None:
            return
        try:
            import json

            body = json.dumps(
                {
                    "schema": _STATE_SCHEMA,
                    "profile_id": self.profile_id,
                    "raw_text": None if quest is None else quest.raw_text,
                    "canonical_text": None if quest is None else quest.canonical_text,
                    "generation": 0 if quest is None else quest.generation,
                    "auto_combat_enabled": self.auto_combat_enabled,
                    "peach_talisman_barrier_dragged": (
                        self.peach_talisman_barrier_dragged
                    ),
                    "saved_at_ns": time.time_ns(),
                },
                ensure_ascii=False,
            )
            self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.persistence_path.with_name(
                f"{self.persistence_path.name}.tmp-{os.getpid()}"
            )
            with open(temp, "w", encoding="utf-8") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.persistence_path)
            self.last_persistence_error = None
        except OSError as exc:
            # F15: a failed save is observable, never silent — the next run
            # must know the memory may be stale.
            self.last_persistence_error = f"save failed: {exc}"

    def observe_snapshot(self, snapshot: PerceptionSnapshot, captured_at_ns: int) -> None:
        marker = (snapshot.frame_id, captured_at_ns)
        if self._last_observed_frame == marker:
            return
        self._last_observed_frame = marker
        # A newly opened character-creation page starts a fresh onboarding
        # run. Re-arm persisted one-shot actions even when the profile state
        # was restored from an older character.
        new_character_visible = any(
            normalize_visible_text(region.text) == "创角"
            and region.confidence >= 0.85
            and region.box.center.y <= 0.20
            for region in snapshot.visible_text
        )
        if new_character_visible and (
            self.auto_combat_enabled or self.peach_talisman_barrier_dragged
        ):
            self.auto_combat_enabled = False
            self.peach_talisman_barrier_dragged = False
            self._save_quest_memory()
        self._update_quest_memory(snapshot, captured_at_ns)
        self._classify_screen(snapshot)

    def mark_auto_combat_enabled(self) -> None:
        """Persist completion of the one-time Auto click after OS receipts."""
        if self.auto_combat_enabled:
            return
        self.auto_combat_enabled = True
        self._save_quest_memory()

    def mark_peach_talisman_barrier_dragged(self) -> None:
        """Persist the barrier drag only after every OS primitive executes."""
        if self.peach_talisman_barrier_dragged:
            return
        self.peach_talisman_barrier_dragged = True
        self._save_quest_memory()

    def record_trace(self, trace: ActionTrace) -> None:
        self.recent_actions.append(trace)
        if trace.effect == "verified":
            self.last_verified_progress_at_ns = trace.updated_at_ns

    def update_trace(
        self,
        action_id: str,
        *,
        effect: str | None = None,
        detail: str | None = None,
        physical_point: tuple[float, float] | None = None,
        primitives: tuple[str, ...] | None = None,
        updated_at_ns: int | None = None,
    ) -> None:
        for index, trace in enumerate(self.recent_actions):
            if trace.action_id != action_id:
                continue
            updated = trace
            if effect is not None:
                updated = replace(updated, effect=effect)
            if detail is not None:
                updated = replace(updated, detail=detail)
            if physical_point is not None:
                updated = replace(updated, physical_point=physical_point)
            if primitives is not None:
                updated = replace(updated, primitives=primitives)
            requested_ns = trace.updated_at_ns if updated_at_ns is None else updated_at_ns
            updated = replace(
                updated, updated_at_ns=max(requested_ns, trace.created_at_ns)
            )
            self.recent_actions[index] = updated
            if updated.effect == "verified":
                self.last_verified_progress_at_ns = updated.updated_at_ns
            return

    def latest_trace(self) -> ActionTrace | None:
        return self.recent_actions[-1] if self.recent_actions else None

    def _update_quest_memory(self, snapshot: PerceptionSnapshot, captured_at_ns: int) -> None:
        candidate = _quest_candidate(snapshot)
        if candidate is None:
            # The tracker is hidden (popup/loading/dialogue): memory persists.
            self._candidate_raw = None
            self._candidate_frame_id = None
            return
        region, frame_id = candidate
        current = self.latest_main_task
        # F08: fuzzy similarity only absorbs OCR jitter and progress-count
        # drift — when the fuzzy-same text carries a DIFFERENT level target
        # (达到10级 → 达到20级), the quest identity itself changed and must
        # advance both the quest generation and the task generation.
        identity_changed = (
            current is not None
            and _same_quest(current.canonical_text, region.text)
            and quest_level_target(current.raw_text)
            != quest_level_target(region.text)
        )
        if (
            current is not None
            and not identity_changed
            and _same_quest(current.canonical_text, region.text)
        ):
            self.latest_main_task = replace(
                current,
                raw_text=region.text,
                box=region.box,
                confidence=region.confidence,
                last_seen_frame_id=frame_id,
                observed_at_ns=captured_at_ns,
                verified_frames=min(2, current.verified_frames + 1),
            )
            self._save_quest_memory()
            self._candidate_raw = None
            self._candidate_frame_id = None
            return
        # Different quest text: require two consecutive frames before the
        # generation advances (single-frame OCR artefacts must not flip tasks).
        if self._candidate_raw == region.text and self._candidate_frame_id != frame_id:
            self.latest_main_task = MainQuestSnapshot(
                region.text,
                region.text,
                region.box,
                region.confidence,
                frame_id,
                frame_id,
                captured_at_ns,
                0 if current is None else current.generation + 1,
            )
            # F08: a new task identity invalidates every request stamped with
            # the previous task generation.
            self.task_generation += 1
            self._save_quest_memory()
            self._candidate_raw = None
            self._candidate_frame_id = None
            return
        self._candidate_raw = region.text
        self._candidate_frame_id = frame_id
        if current is None:
            # No previous task: adopt immediately (nothing to protect).
            self.latest_main_task = MainQuestSnapshot(
                region.text,
                region.text,
                region.box,
                region.confidence,
                frame_id,
                frame_id,
                captured_at_ns,
                0,
            )
            self.task_generation += 1
            self._save_quest_memory()
            self._candidate_raw = None
            self._candidate_frame_id = None

    def _classify_screen(self, snapshot: PerceptionSnapshot) -> None:
        labels = tuple(region.text for region in snapshot.visible_text)
        normalized = tuple(normalize_visible_text(value) for value in labels)
        self.dialogue_active = any(
            cue in value for value in normalized for cue in _DIALOGUE_CUES
        )
        if self.dialogue_active:
            self.screen_type = ScreenType.DIALOGUE
            self.feature_page = None
            return
        if any(cue in value for value in normalized for cue in _LOADING_CUES):
            self.screen_type = ScreenType.LOADING
            self.feature_page = None
            return
        if _has_quest_header(snapshot):
            self.screen_type = ScreenType.WORLD
            self.feature_page = None
            return
        if any(cue in value for value in normalized for cue in _POPUP_CUES):
            self.screen_type = ScreenType.POPUP
            self.feature_page = None
            return
        title = _feature_title(snapshot)
        if title is not None:
            self.screen_type = ScreenType.FEATURE
            self.feature_page = title
            return
        self.screen_type = ScreenType.UNKNOWN

    def context_summary(self) -> str | None:
        """Compact prompt context: current quest, page, and recent real actions."""
        lines: list[str] = []
        quest = self.latest_main_task
        target_level = quest_level_target(None if quest is None else quest.raw_text)
        if quest is not None:
            marker = (
                "；恢复自上次运行、未在本次运行中证实——仅参考，"
                "执行任何与任务相关的操作前先在任务追踪上重新确认"
                if self.restored_task_unverified
                else f"（第{quest.generation}次确认的同一任务）"
            )
            lines.append(f"当前主线任务：{quest.raw_text}{marker}")
        if target_level is not None:
            lines.append(
                f"任务目标：等级达到{target_level}级。若当前页面显示的等级已达到"
                f"{target_level}级，任务已完成，必须退出功能页（点击返回），"
                "禁止继续点击升级类按钮。"
            )
        page = {
            ScreenType.WORLD: "世界界面（主线追踪可见）",
            ScreenType.DIALOGUE: "对话剧情",
            ScreenType.LOADING: "加载中",
            ScreenType.FEATURE: f"功能页（{self.feature_page}）",
            ScreenType.POPUP: "弹窗",
            ScreenType.UNKNOWN: "未知页面",
        }[self.screen_type]
        lines.append(f"当前页面：{page}")
        lines.append(
            "自动战斗状态："
            + (
                "auto_combat_enabled=true"
                if self.auto_combat_enabled
                else "auto_combat_enabled=false"
            )
        )
        lines.append(
            "桃天符印结界拖拽状态："
            + (
                "peach_talisman_barrier_dragged=true"
                if self.peach_talisman_barrier_dragged
                else "peach_talisman_barrier_dragged=false"
            )
        )
        recent = [
            trace
            for trace in reversed(self.recent_actions)
            if trace.effect in {"verified", "ineffective"}
        ][:5]
        if recent:
            rendered = "；".join(
                f"{trace.final_label}→{'已生效' if trace.effect == 'verified' else '无效果'}"
                for trace in reversed(recent)
            )
            lines.append(f"最近真实动作及效果：{rendered}")
        return "\n".join(lines) if lines else None

    def to_envelope(self) -> dict[str, object]:
        quest = self.latest_main_task
        # D10: the dashboard HTTP thread calls this while the agent loop may
        # append to the deque — iterate a snapshot copy so a concurrent
        # append cannot raise "deque mutated during iteration".
        recent = list(self.recent_actions)
        return {
            "latest_main_task": None
            if quest is None
            else {
                "raw_text": quest.raw_text,
                "canonical_text": quest.canonical_text,
                "generation": quest.generation,
                "confidence": quest.confidence,
                "last_seen_frame_id": quest.last_seen_frame_id,
            },
            "screen_type": self.screen_type.value,
            "feature_page": self.feature_page,
            "dialogue_active": self.dialogue_active,
            "auto_combat_enabled": self.auto_combat_enabled,
            "peach_talisman_barrier_dragged": self.peach_talisman_barrier_dragged,
            "recent_actions": [
                {
                    "action_id": trace.action_id,
                    "final_label": trace.final_label,
                    "effect": trace.effect,
                    "physical_point": trace.physical_point,
                }
                for trace in recent
            ],
            "last_verified_progress_at_ns": self.last_verified_progress_at_ns,
        }


def _same_quest(canonical: str, candidate: str) -> bool:
    left = normalize_visible_text(canonical)
    right = normalize_visible_text(candidate)
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    if min(len(left), len(right)) < 4:
        return False
    return SequenceMatcher(None, left, right).ratio() >= _QUEST_SIMILARITY_THRESHOLD


def _has_quest_header(snapshot: PerceptionSnapshot) -> bool:
    return any(
        normalize_visible_text(region.text) == _TASK_HEADER
        and region.confidence > 0.80
        and region.box.center.x <= _HEADER_MAX_CENTER_X
        and _HEADER_MIN_CENTER_Y <= region.box.center.y <= _HEADER_MAX_CENTER_Y
        for region in snapshot.visible_text
    )


def _quest_candidate(snapshot: PerceptionSnapshot) -> tuple[TextRegion, str] | None:
    """The tracked quest text directly under a visible 主线 header."""
    if not _has_quest_header(snapshot):
        return None
    candidates = []
    for region in snapshot.visible_text:
        label = normalize_visible_text(region.text)
        center = region.box.center
        if (
            region.confidence <= 0.85
            or not label
            or label in _EXCLUDED_QUEST_LABELS
            or len(label) < 2
            or center.x > _HEADER_MAX_CENTER_X
            or not _QUEST_MIN_CENTER_Y <= center.y <= _QUEST_MAX_CENTER_Y
        ):
            continue
        candidates.append(region)
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda region: (
            # 展开的任务面板里同一竖列混着分类行/其他系统任务：带进度计数
            # （0/2）的是主线追踪行本身，优先于更长的杂项文本。
            1 if _QUEST_PROGRESS_RE.search(region.text) else 0,
            len(normalize_visible_text(region.text)),
            region.box.right - region.box.left,
            region.confidence,
        ),
    )
    return best, snapshot.frame_id


def _feature_title(snapshot: PerceptionSnapshot) -> str | None:
    titles = [
        region
        for region in snapshot.visible_text
        if region.confidence > 0.85
        and region.box.center.x <= _TITLE_BAND_MAX_CENTER_X
        and region.box.center.y <= _TITLE_BAND_MAX_CENTER_Y
        and 2 <= len(normalize_visible_text(region.text)) <= 4
        and normalize_visible_text(region.text) not in _EXCLUDED_QUEST_LABELS
        and not normalize_visible_text(region.text).isdecimal()
    ]
    if not titles:
        return None
    return max(
        titles,
        key=lambda region: (
            len(normalize_visible_text(region.text)),
            region.confidence,
        ),
    ).text
