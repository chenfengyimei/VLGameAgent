"""The MuMu/仙遇 (mumu-xianyu) rule inventory (D12).

Every entry is one planner fast path declared as data: the page precondition
it requires, the action it may emit, its risk class and its effect.  The
inventory SCOPES the rule code in the planner — the rules themselves live in
the planner and session state, and every action they propose still passes the
unified action safety gate (D03) exactly like a model reply.
"""

from __future__ import annotations

from uga.agent.strategies import StrategyRegistry, StrategyRule

GAME_ID = "mumu-xianyu"

_RULES: tuple[StrategyRule, ...] = (
    StrategyRule(
        name="mumu_close_dialog_cancel",
        source="ocr_mumu_dialog_cancel_fast",
        game_id=GAME_ID,
        summary=(
            "MuMu 确定要关闭 原生确认框：只点 取消（确定会关掉模拟器）；"
            "前置 = 确定要关闭/不再提示 标记在标题条以下"
        ),
        allowed_action="click(取消)",
        risk="deterministic-dismissal",
        effect="the MuMu close confirmation is dismissed",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="dialogue_advance",
        source="ocr_dialogue_click_fast",
        game_id=GAME_ID,
        summary=(
            "剧情对话 倒计时区（秒后自动继续）：连点推进区推进对话；"
            "前置 = 秒后自动继续 文本"
        ),
        allowed_action="click(倒计时区)",
        risk="low",
        effect="the dialogue advances to the next line",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="popup_close_glyph",
        source="ocr_close_glyph_fast",
        game_id=GAME_ID,
        summary=(
            "OCR 可见的弹窗关闭 ×（右上、短标签；排除 MuMu 标题条与"
            "元宝×500 式数量标签）；有可操作按钮的页面跳过"
        ),
        allowed_action="click(close glyph)",
        risk="low",
        effect="the popup closes",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="task_panel_tracker_click",
        source="ocr_task_panel_fast",
        game_id=GAME_ID,
        summary=(
            "左上角主线任务追踪文字点击 = 自动寻路；25s 冷却按任务文本，"
            "让位给游戏自身的高亮引导"
        ),
        allowed_action="click(任务追踪行)",
        risk="low",
        effect="the game auto-navigates to the quest target",
        cooldown_s=25.0,
    ),
    StrategyRule(
        name="progress_control_click",
        source="ocr_progress_control_fast",
        game_id=GAME_ID,
        summary="推进可见的引导/进度控件（对话、强制进入倒计时等）",
        allowed_action="click(进度控件)",
        risk="low",
        effect="advance the visible dialogue or guided flow",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="quest_satisfied_back_exit",
        source="ocr_quest_satisfied_back_fast",
        game_id=GAME_ID,
        summary="页面等级已达任务目标（达到N级）：点左上返回花纹退出功能页",
        allowed_action="click(ui_back hotspot)",
        risk="navigation",
        effect="the feature page is left for the world view",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="completed_panel_back_exit",
        source="ocr_completed_panel_back_fast",
        game_id=GAME_ID,
        summary="面板显示完成态（进化上限/编队占位）：点返回退出",
        allowed_action="click(ui_back hotspot)",
        risk="navigation",
        effect="the completed panel is left",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="irrelevant_page_back_exit",
        source="ocr_quest_irrelevant_back_fast",
        game_id=GAME_ID,
        summary=(
            "页面 OCR 与任务关键词无关（如任务=灵宠而页面=功法升级）："
            "点返回退出；20s 冷却 + back/close 交替"
        ),
        allowed_action="click(ui_back/ui_close hotspot)",
        risk="navigation",
        effect="the irrelevant feature page is left",
        cooldown_s=20.0,
    ),
    StrategyRule(
        name="xiuxian_path_jump",
        source="ocr_xiuxian_path_jump_fast",
        game_id=GAME_ID,
        summary="点 修仙之路 任务行自动跳转界面（行须带 目标/进度）；10s 冷却",
        allowed_action="click(修仙之路任务行)",
        risk="navigation",
        effect="the 修仙之路 interface opens",
        cooldown_s=10.0,
    ),
    StrategyRule(
        name="xiuxian_objective_goto",
        source="ocr_xiuxian_objective_goto_fast",
        game_id=GAME_ID,
        summary="修仙之路 界面内点最上目标的 前往 按钮；10s 冷却（与 jump 共享）",
        allowed_action="click(前往)",
        risk="navigation",
        effect="the objective's target screen opens",
        cooldown_s=10.0,
    ),
    StrategyRule(
        name="stall_item_cell_click",
        source="ocr_stall_item_fast",
        game_id=GAME_ID,
        summary=(
            "摆摊出售页点首个物品格（N级 标签锚点，点击瞄向上方图标）；"
            "8s 冷却；前置 = 我要出售/我的物品 标记"
        ),
        allowed_action="click(物品格)",
        risk="low",
        effect="the item cell is selected for listing",
        cooldown_s=8.0,
    ),
    StrategyRule(
        name="realm_promotion",
        source="ocr_realm_promote_fast",
        game_id=GAME_ID,
        summary=(
            "境界 页目标全部 已完成 时点 晋升 图章（OCR 不可读的图形控件，"
            "由规则层决策）；前置 = 境界 标题 + ≥2 已完成"
        ),
        allowed_action="click(ui_promote hotspot)",
        risk="progression",
        effect="the realm promotion runs",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="market_entry_click",
        source="ocr_market_entry_fast",
        game_id=GAME_ID,
        summary="出售/摆摊类任务：点 市场 入口（排除任务面板区域与 MuMu 标题条）",
        allowed_action="click(市场)",
        risk="low",
        effect="the market interface opens",
        cooldown_s=0.0,
    ),
)

REGISTRY = StrategyRegistry(game_id=GAME_ID, rules=_RULES)

assert len({rule.source for rule in _RULES}) == len(_RULES)
