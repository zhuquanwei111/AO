# Windows单场取数组件

该组件复用10号`store-finalization-20261027-r1`源码中的业务流程、Windows-MCP桌面客户端和RapidOCR；由现有`backstage_bridge.py`按单一App场次调用，不依赖AO会话或聊天窗口。导航可将已确认目标浏览器的当前窗口图片发送给既有天翼 `glm-5.3-flash`；直播间身份和单项指标采用限定裁区。图片通过内存传递，不写入图片文件或图片日志。

## 运行布局与解释器

保留以下目录为同级目录：

- `glm-recap-prototype/`：CLI入口、受限局部视觉裁区和模型调用。
- `fixed-recap-prototype/`：已有结果解析、Store/Runner、页面步骤、MCP调用和OCR。
- `llm-recap-prototype/`：既有页面定位及原`fill_missing`结果补缺函数。没有复制旧Codex视觉调用。
- `windows-mcp/`：现有官方MCP客户端与9工具白名单适配。

后端/桥沿服务部署选择的Python运行；Windows取数CLI使用独立Python 3.13环境。准备四个互相独立的Python环境：

1. CLI/OCR环境使用Python 3.13，并安装`fixed-recap-prototype/requirements-ocr.lock.txt`。
2. MCP客户端环境安装`requirements-mcp-client.txt`中的`mcp==1.30.0`。
3. Windows-MCP环境安装`requirements-windows-mcp.txt`中的`windows-mcp==0.8.8`。
4. 视觉 SDK 环境使用独立解释器，安装 `glm-recap-prototype/requirements-sdk.lock.txt` 中的依赖。

MCP 两份 requirements 只锁直接依赖；其传递依赖尚无完整锁，本交付不宣称全新机器安装已复现。OCR权重不入仓，由已存在的`provision_ocr.py`从锁定RapidOCR发行包提取并校验哈希后写到外部模型目录。视觉流程需要现有模型路由、SDK 和匹配的能力 Gate；旧 Gate 只证明旧链能力，不证明新路径已经实测。

## 服务进程配置

将下列路径配置到运行普通后端/桥的Windows进程环境中。路径均由部署者按实际安装位置填写；不要把值写入本仓库或模型上下文。

| 环境变量 | 用途 |
|---|---|
| `BACKSTAGE_CLI_PYTHON` | CLI/OCR Python 3.13解释器 |
| `BACKSTAGE_CLI_ENTRY` | 本目录下`glm-recap-prototype/recap_glm.py` |
| `BACKSTAGE_CLI_DATA` | 外部本场记录目录 |
| `BACKSTAGE_CLI_MODELS` | 外部RapidOCR模型目录 |
| `BACKSTAGE_CLI_SCRATCH` | 外部scratch目录，仅保存非图片运行状态 |
| `BACKSTAGE_MCP_CLIENT_PYTHON` | `mcp==1.30.0`客户端解释器 |
| `BACKSTAGE_WINDOWS_MCP_PYTHON` | 安装`windows-mcp==0.8.8`的解释器 |
| `BACKSTAGE_SDK_PYTHON` | 已安装 Agents SDK 的独立解释器 |
| `BACKSTAGE_CLI_GATE_A` | 现有模型与路由匹配的视觉能力 Gate 路径 |
| `TIANYI_API_KEY` | 由受控父进程环境提供的天翼模型凭据 |
| `BACKSTAGE_POLL_SECONDS` | 可选轮询间隔，默认2秒，最低0.5秒 |
| `BACKSTAGE_CLI_TIMEOUT_SECONDS` | 可选 CLI 超时，默认480秒，允许60至600秒 |

桥沿现有参数传外部路径；CLI会拒绝将记录、模型或scratch写入源码安装目录。模型凭据仅继承到受控桥、CLI 和视觉 SDK 子进程，不放入参数、日志或图片；worker 令牌不传给 CLI、MCP 或模型，`AO_*` 也不继承。启动桌面动作前检查模型凭据、Gate 和 SDK 路径；不能通过时以安全原因停止。

## 调用与来源

生产路径由桥按同一后端`session_id`、抖音号快照和App来源调用`start`。正常绑定且有有效身份/绑定证据时，桥等待真实同来源App `stop`，再调用CLI `stop`与只读`result`。如果`start`返回同场同源、无绑定的`needs_input`或终态`failed`记录，桥仍等真实App `stop`，之后调用一次CLI `stop`以保存停止来源并完成CLI本地失败收口；桥保留该CLI记录的原状态和原因，不再调用CLI `result`。若`needs_input`已有绑定证据但结果不确定，桥不再调用`stop`或重抓取；等真实App `stop`后回写未知/空值。无record或身份/source不匹配时不调用CLI `stop`/`result`，只在真实同场App `stop`后按后端任务身份回写空值。App来源保持`ios_app`或`android_app`；`test_simulated`只作明确受控测试。CLI的`start`/`stop`在`needs_input`或`failed`时返回exit 2并携带record；`status`/`result`是只读操作，应返回exit 0。source/身份不匹配不改标。

常驻桥和单次 `run_once` 使用同一主机级锁：Windows 使用系统命名互斥对象，进程终止时由系统释放；无法取得锁则不领取任务。该锁不覆盖其他机器，部署时仍需确认跨主机唯一执行器。受限启动器从当前用户受保护的单字段文件读取 worker 凭据，只注入桥进程。

真实调用需Windows交互桌面、用户现有Edge登录态、上述隔离环境、外部OCR权重目录、视觉 Gate 及受控凭据。当前PR只交源码/配置模板，不能将离线fixture测试当作抖音实播验证。

截图经现有 Windows-MCP 返回后只通过进程管道驻留内存，RapidOCR 直接读取字节；原图和裁区不落盘。导航使用已确认的目标浏览器窗口图片；直播间身份与指标只使用经来源、窗口和范围检查的独立裁区，未找到合法裁区不得退回整窗。识别到登录/验证码、账号或场次歧义时停止。五项读取需先确认同场账号并要求至少三项高置信标签属于同一动态汇总行；缺项才尝试独立单项裁区，其他行不用于补缺。该保守约束可能拒绝真实布局或漏读字段，不等于真实平台可用；真实页面识别、Windows-MCP接线和端到端链路仍须独立验收。

窗口遮挡检查按实际呈现区分：DWM 明确标为 cloaked 的窗口和系统明确报告整体透明度为零的分层窗口不构成图像覆盖。Windows-MCP 0.8.8 自身的控制提示及截图闪光由官方截图实现临时隐藏/撤除；仅当窗口类、分层/穿透/置顶/不激活样式、无 owner 和顶层身份均符合官方实现，且其进程属于本次仍存活 MCP 客户端的子树、创建时间顺序及复核一致时，才豁免矩形相交检查。其他进程的同名窗口、透明度未知、真实覆盖或枚举异常仍拒绝；用户接管、目标身份、窗口位置和输入落点检查继续生效。拒绝日志只记录截图/输入阶段、固定类别与必要句柄/样式，不记录窗口标题、页面或图片。

实现依据：[Windows-MCP 0.8.8 截图实现](https://github.com/CursorTouch/Windows-MCP/blob/v0.8.8/src/windows_mcp/desktop/service.py)、[控制提示](https://github.com/CursorTouch/Windows-MCP/blob/v0.8.8/src/windows_mcp/desktop/control_overlay.py)、[微软 DWM 窗口属性](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute)、[分层窗口透明度](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlayeredwindowattributes)、[进程父子元数据](https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/ns-tlhelp32-processentry32w)。这些资料与离线回归证明适配办法，不代替本次真实平台、账号或五项验收。

## 许可与来源

保留源清单提供的RapidOCR及模型许可材料。RapidOCR代码为Apache-2.0，随附模型许可另见`fixed-recap-prototype/rapidocr-MODEL_LICENSES.md`及`ocr-model-manifest.json`。锁定的Agents SDK与Python MCP SDK为MIT，Windows-MCP 0.8.8为MIT，分别见[OpenAI Agents SDK许可](https://github.com/openai/openai-agents-python/blob/main/LICENSE)、[Python MCP SDK许可](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE)和[Windows-MCP 0.8.8发行说明](https://pypi.org/project/windows-mcp/0.8.8/)。本目录不内嵌这些依赖包；两份直接依赖列表没有完整锁定全部传递依赖，安装方仍须随实际分发检查其许可。本目录仅包含客户端适配源码，不包含Windows-MCP二进制包、模型权重、API密钥、桌面截图或本地结果。
