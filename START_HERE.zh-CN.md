# Universal Game Agent：第一次运行只看这一页

Universal Game Agent 是一个通用的视觉模型游戏操作平台。它像人一样读取窗口画面，通过 OCR 与视觉语言模型理解界面，每次只执行一个可验证动作，再根据新画面决定下一步。它不依赖特定游戏接口，也不要求把规则写死在某一个标题里。

## 1. 准备环境

- Windows 10/11 x64。
- Python 3.11 或 3.12。
- 已启动的目标游戏窗口。
- 一个支持图片输入、兼容 OpenAI API 的视觉模型服务。
- 云端服务需要 API Key；本地服务通常不需要。

真实运行前，请确认你随时可以按下紧急停止键：`Ctrl+Shift+F12`。

## 2. 下载与安装

```powershell
git clone https://github.com/chenfengyimei/VLGameAgent.git
cd VLGameAgent
.\install.cmd
.\check.cmd
```

`install.cmd` 会创建 `.venv`，安装锁定版本的运行、OCR 与视觉依赖，并安装项目。`check.cmd` 只做离线自检，不会操作游戏。

## 3. 创建自己的目标档案

```powershell
Copy-Item .\configs\games\generic-visual-game.example.yaml `
  .\configs\games\my-game.yaml
```

编辑 `my-game.yaml`：

1. `game.id`：使用小写英文和连字符，例如 `my-game`。
2. `process.executable`：填写实际进程文件名。
3. `window.title_pattern`：填写尽量精确的窗口标题正则。
4. `no_click_regions`：排除系统标题栏、悬浮工具栏等不可点击区域。
5. 没有人工校准过的 `ui_back`、`ui_close` 热点不要启用。

启动脚本会拒绝包含 `CHANGE_ME` 的档案，避免用模板误操作。

## 4. 配置模型密钥

云端模型建议把密钥写入当前 PowerShell 进程，不要写入仓库：

```powershell
$env:UGA_VLM_API_KEY = "your-key"
```

也可以永久保存到当前 Windows 用户环境变量，但不要截图、提交或输出密钥。

## 5. 第一次只运行 5 分钟

```powershell
.\start.cmd `
  -Profile .\configs\games\my-game.yaml `
  -Goal "打开当前任务并安全推进一个步骤" `
  -GoalEvidence "任务完成" `
  -Model "your-vision-model" `
  -BaseUrl "https://provider.example/v1" `
  -DurationSeconds 300
```

本地模型把 `-BaseUrl` 换成本地地址即可。运行期间打开：

```text
http://127.0.0.1:8787
```

看板会显示当前画面、OCR、决策来源、动作、效果验证、被安全层拦截的原因和运行状态。

## 6. 通过短测后再长期运行

```powershell
.\start.cmd `
  -Profile .\configs\games\my-game.yaml `
  -Goal "持续推进当前可见目标；遇到敏感页面立即停止" `
  -Model "your-vision-model" `
  -BaseUrl "https://provider.example/v1" `
  -DurationSeconds 0 `
  -Continuous
```

长期运行前必须确认：

- 窗口匹配唯一且稳定。
- 点击坐标与窗口缩放、分辨率一致。
- 没有登录、支付、实名、购买、删除或账号设置页面。
- 短时运行没有重复点击、误点击和无法退出的页面。
- `Ctrl+Shift+F12` 紧急停止有效。

## 7. 常用命令

```powershell
.\install.cmd             # 安装或重建环境
.\check.cmd               # 离线自检
.\start.cmd --help        # 查看通用启动参数
python -m pytest           # 开发者测试
uga-capture-probe --help   # 窗口捕获诊断
uga-replay --help          # Episode 回放
uga-dataset --help         # 数据集处理
```

## 8. 出问题先做什么

- 一直等待：确认窗口没有被遮挡、OCR 已启用、模型接口可访问、任务文字足够明确。
- 点击无效：检查窗口焦点、DPI/缩放、标题匹配、帧是否过期。
- 重复点击：立即急停，保存看板事件；不要只修改提示词，应增加页面状态与效果验证。
- 进入无关页面：为该页面增加稳定识别锚点和确定性返回规则。
- 云端接口报错：检查 `UGA_VLM_API_KEY`、模型名、Base URL 和供应商 JSON 模式要求。

完整原理、档案字段、模型配置、安全机制和排错方法见 [docs/usage.zh-CN.md](docs/usage.zh-CN.md)。
