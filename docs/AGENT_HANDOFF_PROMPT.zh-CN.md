# 可直接转发给其他人的 Agent 提示词

将下面整段原样发送给另一个具备终端和文件访问能力的 Agent。把尖括号内容替换为实际信息；不知道的字段可以保留，让 Agent 先向你确认。

```text
请使用仓库内的 $universal-game-agent-setup Skill，帮我完整安装、配置、验证并启动 Universal Game Agent。

仓库地址：https://github.com/chenfengyimei/VLGameAgent.git
目标目录：<例如 D:\Projects\VLGameAgent；若目录不存在则创建，若已存在先检查 git status，绝对不要覆盖未提交修改>
目标游戏进程：<Game.exe>
目标窗口标题：<窗口标题；请生成尽量精确且带 ^ 和 $ 的正则>
视觉模型 Base URL：<本地或云端 OpenAI-compatible 地址>
视觉模型名称：<模型服务实际暴露的模型 ID>
API Key 环境变量：<默认 UGA_VLM_API_KEY；不得在回复、日志或 Git 中输出密钥>
当前目标：<一个具体、可见、可验证的游戏目标>
完成证据：<画面上可由 OCR 识别的完成文字；可有多条>

请严格按以下要求执行：
1. 如果本机还没有 Skill，先从仓库的 skills/universal-game-agent-setup/SKILL.md 读取完整说明，再按其中 references 的路由读取必要文档；不要凭空猜启动方式。
2. 获取或更新仓库前先检查目标目录和 Git 状态；保留现有修改，不得 reset --hard、checkout 覆盖或删除用户文件。
3. 在 Windows 上运行 install.cmd 和 check.cmd；若失败，定位第一个真实错误并修复，不要无上限重试。
4. 从 configs/games/generic-visual-game.example.yaml 复制新档案，替换全部 CHANGE_ME。只能写入已经确认的进程、窗口、按键和坐标；不要虚构返回、关闭或技能热点。
5. 云端密钥只从指定环境变量读取，不得写入配置、脚本、聊天、日志或提交。
6. 真实操作前确认目标窗口唯一、当前不在登录/支付/实名/购买/删除/交易/账号设置页面，并明确告诉我紧急停止键是 Ctrl+Shift+F12。
7. 第一次只运行 180 到 300 秒，使用 model-first、开启本机看板与 Episode 记录；打开 http://127.0.0.1:8787 检查画面、OCR、动作来源、效果验证和安全拦截。
8. 若出现重复点击、错误窗口、无关页面不退出或敏感动作，立即停止并根据事件证据修复，不得仅靠修改提示词掩盖问题。
9. 只有短测通过后，才向我说明如何用 -DurationSeconds 0 -Continuous 长期运行；未经我确认不要自行开启无限时运行。
10. 完成后给出：安装结果、创建的档案路径、验证命令及结果、实际启动命令、看板地址、停止方法、已知限制和下一步建议。
```

## Skill 安装方式

如果对方使用 Codex，克隆仓库后可以把 Skill 复制到个人技能目录：

```powershell
Copy-Item -Recurse -Force `
  .\skills\universal-game-agent-setup `
  "$env:USERPROFILE\.codex\skills\universal-game-agent-setup"
```

也可以不安装，直接让 Agent 读取仓库内 `skills/universal-game-agent-setup/SKILL.md`。
