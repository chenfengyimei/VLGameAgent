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
        name="auto_navigation_wait",
        source="ocr_auto_navigation_wait",
        game_id=GAME_ID,
        summary="画面显示 自动寻路中 时不重复点击任务栏，等待到达目的地或战斗状态。",
        allowed_action="wait",
        risk="low",
        effect="the game completes its in-progress auto-navigation",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="invasion_task_navigate",
        source="ocr_invasion_task_navigate_fast",
        game_id=GAME_ID,
        summary="主线 入侵袭击/击败这些不速之客 且未见黑衣人时，点左侧任务行自动寻路。",
        allowed_action="click(入侵袭击 task line)",
        risk="navigation",
        effect="the game auto-navigates to the black-clad enemy encounter",
        cooldown_s=25.0,
    ),
    StrategyRule(
        name="invasion_group_attack",
        source="ocr_invasion_group_attack_fast",
        game_id=GAME_ID,
        summary=(
            "入侵袭击/击败这些不速任务与黑衣人/恶灵同时可见时，直接点用户校准的"
            "右下群攻热点；不要求 OCR 读到技能小字。"
        ),
        allowed_action="click(ui_group_attack hotspot)",
        risk="combat",
        effect="the black-clad enemies take damage and quest progress advances",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="demonized_spirit_group_attack",
        source="ocr_demonized_spirit_group_attack_fast",
        game_id=GAME_ID,
        summary="魔化精怪任务和魔化妖灵/魔化猪猪同时可见时，点击右侧蓝色灵宠群攻。",
        allowed_action="click(ui_pet_group_attack hotspot)",
        risk="combat",
        effect="the demonized spirits take damage and quest progress advances",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="peach_tree_spirit_group_attack",
        source="ocr_peach_tree_spirit_group_attack_fast",
        game_id=GAME_ID,
        summary=(
            "暴虐精怪任务和桃木精同时可见时，依次轮换蓝色灵宠群攻、"
            "上方紫色群攻和下方紫色群攻。"
        ),
        allowed_action="click(recorded group-attack hotspot rotation)",
        risk="combat",
        effect="the peach-tree spirits take damage and quest progress advances",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="raging_tree_spirit_group_attack",
        source="ocr_raging_tree_spirit_group_attack_fast",
        game_id=GAME_ID,
        summary=(
            "狂暴树精任务与千年桃木精同时可见时，轮换上、下两个紫色群攻技能。"
        ),
        allowed_action="click(ui_secondary_group_attack/ui_group_attack rotation)",
        risk="combat",
        effect="the raging thousand-year tree spirit takes damage",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="red_dust_auto_once",
        source="ocr_red_dust_auto_once_fast",
        game_id=GAME_ID,
        summary=(
            "红尘入世第一段 与师姐一起 任务出现时点击底部 自动 一次；"
            "仅在全部物理点击回执成功后持久标记，后续不再点击。"
        ),
        allowed_action="click(ui_auto_combat hotspot once per character)",
        risk="progression",
        effect="automatic combat is enabled for the remaining onboarding flow",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="onboarding_joystick_forward",
        source="ocr_onboarding_joystick_forward_fast",
        game_id=GAME_ID,
        summary=(
            "首次世界移动教学同时识别 滑动摇杆可以移动：从左下摇杆中心向上短拖，"
            "绝不在普通世界画面盲目移动。"
        ),
        allowed_action="drag(left joystick upward)",
        risk="navigation",
        effect="the character moves forward and the joystick tutorial advances",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="character_creation_customize",
        source="ocr_character_creation_customize_fast",
        game_id=GAME_ID,
        summary="创角页同时识别 创角 标题与右下 定制细节，进入预设选择。",
        allowed_action="click(定制细节)",
        risk="progression",
        effect="the character preset-selection page opens",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="character_preset_start",
        source="ocr_character_preset_start_fast",
        game_id=GAME_ID,
        summary="选择预设页同时识别 选择预设 标题与右下 开启仙途，继续创角。",
        allowed_action="click(开启仙途)",
        risk="progression",
        effect="the character-name prompt opens",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="character_name_confirm",
        source="ocr_character_name_confirm_fast",
        game_id=GAME_ID,
        summary=(
            "请输入名字 弹窗仅在字符计数为非零 N/7 时点 确定；空名称绝不自动确认。"
        ),
        allowed_action="click(确定 after a nonempty name is visible)",
        risk="progression",
        effect="the entered character name is confirmed",
        cooldown_s=0.0,
    ),
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
            "剧情对话：左侧 回顾剧情 仅作识别锚点，点击已校准右下推进区；"
            "倒计时 秒后自动继续 仍可直接推进，绝不点击 回顾剧情 本身"
        ),
        allowed_action="click(右下对话推进区 or 倒计时区)",
        risk="low",
        effect="the dialogue advances to the next line",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="cutscene_skip",
        source="ocr_cutscene_skip_fast",
        game_id=GAME_ID,
        summary=(
            "仅当 跳过 位于游戏内容右上、MuMu 标题条以下时点击；"
            "用于跳过主线过场，普通 跳过 文本不匹配"
        ),
        allowed_action="click(top-right 跳过)",
        risk="low",
        effect="the cutscene ends and the game world becomes visible",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="rescue_little_dragon_choice",
        source="ocr_rescue_little_dragon_choice_fast",
        game_id=GAME_ID,
        summary="小青龙对话中只点击右下的 拯救小龙 选项，进入疗伤互动。",
        allowed_action="click(拯救小龙)",
        risk="progression",
        effect="the little-dragon healing interaction opens",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="little_dragon_heal",
        source="ocr_little_dragon_heal_fast",
        game_id=GAME_ID,
        summary=(
            "拯救重伤的小青龙 和 传功疗伤 同时可见时，点校准后的小青龙头部；"
            "任一锚点缺失则不触发。"
        ),
        allowed_action="click(little dragon head hotspot)",
        risk="progression",
        effect="the injured little dragon receives healing and the quest advances",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="narrative_continue",
        source="ocr_narrative_continue_fast",
        game_id=GAME_ID,
        summary="结契/剧情完成页下方出现 点击任意处继续 时，点该继续控件。",
        allowed_action="click(点击任意处继续)",
        risk="low",
        effect="the story-completion page closes and the next quest becomes visible",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="pet_training_entry",
        source="ocr_pet_training_entry_fast",
        game_id=GAME_ID,
        summary="小龙升级/灵宠达到2级任务可见时，点击右侧灵宠入口打开培养界面。",
        allowed_action="click(right-side 灵宠 entry)",
        risk="navigation",
        effect="the pet formation interface opens",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="pet_information_tab",
        source="ocr_pet_information_tab_fast",
        game_id=GAME_ID,
        summary="灵宠布阵页同时显示布阵目标与右侧信息标签时，点击信息进入培养页。",
        allowed_action="click(信息 tab)",
        risk="navigation",
        effect="the selected pet information and training page opens",
        cooldown_s=0.0,
    ),
    StrategyRule(
        name="pet_upgrade_once",
        source="ocr_pet_upgrade_once_fast",
        game_id=GAME_ID,
        summary=(
            "灵宠信息页当前等级低于任务目标时点击升N级一次；达到目标后让位给"
            "ocr_quest_satisfied_back_fast 退出，禁止继续消耗材料。"
        ),
        allowed_action="click(升N级 once while current level is below target)",
        risk="progression",
        effect="the pet reaches the tracked quest target level",
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
