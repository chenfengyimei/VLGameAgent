# 仙遇：已记录的初始创角流程

这些截图来自已授权的 MuMu 游戏会话，用作 `mumu-xianyu` 配置的首轮视觉流程基准。
运行时不依赖截图的固定像素坐标：它只会在同一张最新 OCR 快照中同时看到页面标题和
目标按钮时才执行单步点击；统一安全门仍会在物理输入前复核窗口、画面新鲜度和目标。

| 步骤 | 视觉前置条件 | 自动动作 | 成功后的预期状态 |
| --- | --- | --- | --- |
| 1 | `创角` 标题 + 下方 `定制细节` 按钮 | 点击 `定制细节` | 出现 `选择预设` 页面 |
| 2 | `选择预设` 标题 + 下方 `开启仙途` 按钮 | 点击 `开启仙途` | 出现 `请输入名字` 弹窗 |
| 3 | `请输入名字` 标题 + 非零 `N/7` 计数 + 下方 `确定` 按钮 | 点击 `确定` | 已输入的角色名被确认 |
| 4 | `滑动摇杆可以移动` 教学提示 | 从左下摇杆中心向上短拖 | 角色向前移动，教学继续 |
| 5a | 左侧主线出现 `入侵袭击` 或 `击败这些不速之客`，且尚未见敌人 | 点击任务行 | 游戏自动寻路至战斗位置 |
| 5b | `自动寻路中` 可见 | 等待 | 不重复点击任务栏，直到到达战斗位置或任务刷新 |
| 5c | `入侵袭击`、`黑衣人`或`恶灵`与右下 `群攻` 同时可见 | 点击群攻图标 | 敌人受击，任务进度推进；敌人消失后停止此规则 |
| 5d | 战斗结束，左侧主线刷新（如 `危机四伏`） | 点击新任务行 | 游戏开始下一次自动寻路 |
| 6a | 游戏内容右上（MuMu 标题条以下）出现高置信 `跳过` | 点击 `跳过` | 过场结束，回到游戏世界或任务界面 |
| 6b | 左侧主线任务为 `与师姐对话`、`与师父对话`、`药灵对话` 等 | 点击当前任务行 | 游戏自动寻路到对应角色；`自动寻路中` 时继续等待 |
| 6c | 左侧中部出现 `回顾剧情` | 点击已校准的右下对话推进区 | 对话进入下一句；`回顾剧情` 只作识别锚点，绝不点击它本身 |
| 6d | 对话结束、回到世界，左侧主线刷新 | 点击新的任务行 | 重复自动寻路、对话或战斗流程；再次出现过场时按 6a |
| 7a | `药园天劫` 主线（如 `前往药园`） | 点击当前任务行 | 游戏自动寻路并进入小青龙剧情 |
| 7b | 右下出现 `拯救小龙` 选项 | 点击 `拯救小龙` | 打开疗伤互动页面 |
| 7c | `拯救重伤的小青龙` 与 `传功疗伤` 同时可见 | 点击已校准的小青龙头部（约 `0.568, 0.588`） | 完成治疗并回到游戏世界；任一文字锚点缺失时不点击该热点 |
| 7d | 结契画面下方出现 `点击任意处继续` | 点击该继续文本 | 关闭剧情完成页，继续新的主线任务 |

名称框为空、计数不可读，或任何页面标题/按钮不完整匹配时，流程不点击并等待下一张稳定画面。
后续新界面继续按同样方式补充截图和成功状态；未知分支交给视觉模型判断，而不是把本流程
的坐标套用过去。

本流程同时固化在机器可读清单
[`configs/flows/mumu-xianyu-onboarding.yaml`](../../configs/flows/mumu-xianyu-onboarding.yaml)
中。新增截图时必须同时登记识别锚点、规则来源、动作、预期结果，并由测试检查截图存在且
规则已经注册；这样后续补充不会只停留在说明文档中。

## 基准截图

1. [创角与定制细节](../assets/mumu-xianyu/onboarding/01-character-create.png)
2. [选择预设与开启仙途](../assets/mumu-xianyu/onboarding/02-preset-selection.png)
3. [名称确认](../assets/mumu-xianyu/onboarding/03-name-confirmation.png)
4. [左摇杆前进教学](../assets/mumu-xianyu/onboarding/04-joystick-forward-tutorial.png)
5. [入侵袭击与群攻](../assets/mumu-xianyu/onboarding/05-invasion-group-attack.png)
6. [自动寻路中](../assets/mumu-xianyu/onboarding/06-auto-navigation.png)
7. [恶灵战斗与群攻](../assets/mumu-xianyu/onboarding/07-evil-spirit-group-attack.png)
8. [战斗后刷新主线](../assets/mumu-xianyu/onboarding/08-refreshed-main-quest.png)
9. [过场右上跳过](../assets/mumu-xianyu/onboarding/09-cutscene-skip.png)
10. [师姐相救任务](../assets/mumu-xianyu/onboarding/10-senior-sister-task.png)
11. [师姐对话推进](../assets/mumu-xianyu/onboarding/11-senior-sister-dialogue.png)
12. [拜见师父任务](../assets/mumu-xianyu/onboarding/12-master-task.png)
13. [师父对话推进](../assets/mumu-xianyu/onboarding/13-master-dialogue.png)
14. [药灵对话任务](../assets/mumu-xianyu/onboarding/14-spirit-task.png)
15. [药灵对话推进](../assets/mumu-xianyu/onboarding/15-spirit-dialogue.png)
16. [另一段过场右上跳过](../assets/mumu-xianyu/onboarding/16-cutscene-skip.png)
17. [药园天劫任务](../assets/mumu-xianyu/onboarding/17-little-dragon-task.png)
18. [拯救小龙选项](../assets/mumu-xianyu/onboarding/18-little-dragon-rescue-choice.png)
19. [小青龙疗伤互动](../assets/mumu-xianyu/onboarding/19-little-dragon-heal.png)
20. [疗伤后与小青龙对话](../assets/mumu-xianyu/onboarding/20-little-dragon-dialogue-task.png)
21. [小青龙对话推进](../assets/mumu-xianyu/onboarding/21-little-dragon-dialogue.png)
22. [结契完成继续](../assets/mumu-xianyu/onboarding/22-contract-continue.png)
