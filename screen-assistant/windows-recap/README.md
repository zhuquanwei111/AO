# Windows单场取数组件

该组件复用10号`store-finalization-20261027-r1`源码中的业务流程、固定步骤、Windows-MCP桌面客户端和RapidOCR；由现有`backstage_bridge.py`按单一App场次调用，不依赖AO会话或聊天窗口。当前路径不调用模型、不发送或保存图片。

## 运行布局与解释器

保留以下目录为同级目录：

- `glm-recap-prototype/`：CLI入口和兼容旧调用的已禁用图片视觉入口。
- `fixed-recap-prototype/`：已有结果解析、Store/Runner、页面步骤、MCP调用和OCR。
- `llm-recap-prototype/`：既有页面定位及原`fill_missing`结果补缺函数。没有复制旧Codex视觉调用。
- `windows-mcp/`：现有官方MCP客户端与9工具白名单适配。

后端/桥沿服务部署选择的Python运行；Windows取数CLI使用独立Python 3.13环境。准备三个互相独立的Python环境：

1. CLI/OCR环境使用Python 3.13，并安装`fixed-recap-prototype/requirements-ocr.lock.txt`。
2. MCP客户端环境安装`requirements-mcp-client.txt`中的`mcp==1.30.0`。
3. Windows-MCP环境安装`requirements-windows-mcp.txt`中的`windows-mcp==0.8.8`。

后两份requirements只锁直接依赖；其传递依赖尚无完整锁，本交付不宣称全新机器安装已复现。OCR权重不入仓，由已存在的`provision_ocr.py`从锁定RapidOCR发行包提取并校验哈希后写到外部模型目录。旧视觉Gate及Agents SDK不再是当前路径前置。

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

桥沿现有参数传外部路径；CLI会拒绝将记录、模型或scratch写入源码安装目录。桥与MCP子进程只继承所需系统和路径变量，不继承worker令牌、模型凭据或`AO_*`。旧`--gate-a`参数保留兼容，但当前路径不使用它。

## 调用与来源

生产路径由桥按同一后端`session_id`、抖音号快照和App来源调用`start`。正常绑定且有有效身份/绑定证据时，桥等待真实同来源App `stop`，再调用CLI `stop`与只读`result`。如果`start`返回同场同源、无绑定的`needs_input`或终态`failed`记录，桥仍等真实App `stop`，之后调用一次CLI `stop`以保存停止来源并完成CLI本地失败收口；桥保留该CLI记录的原状态和原因，不再调用CLI `result`。若`needs_input`已有绑定证据但结果不确定，桥不再调用`stop`或重抓取；等真实App `stop`后回写未知/空值。无record或身份/source不匹配时不调用CLI `stop`/`result`，只在真实同场App `stop`后按后端任务身份回写空值。App来源保持`ios_app`或`android_app`；`test_simulated`只作明确受控测试。CLI的`start`/`stop`在`needs_input`或`failed`时返回exit 2并携带record；`status`/`result`是只读操作，应返回exit 0。source/身份不匹配不改标。

常驻桥和单次 `run_once` 使用同一主机级锁：Windows 使用系统命名互斥对象，进程终止时由系统释放；无法取得锁则不领取任务。该锁不覆盖其他机器，部署时仍需确认跨主机唯一执行器。受保护的worker凭据由桥运行环境提供，现有桥不包含从外置文件加载凭据的管理入口。

真实调用需Windows交互桌面、用户现有Edge登录态、上述三个隔离环境、外部OCR权重目录及桥进程的受控worker凭据。当前PR只交源码/配置模板，不能将离线fixture测试当作抖音实播验证。

截图经现有 Windows-MCP 返回后只通过进程管道驻留内存，RapidOCR 直接读取字节；原图与裁区均不落盘或外发。导航只使用当前窗口的本地 OCR/UIA 和既有受保护动作；识别到登录/验证码、账号或场次歧义时停止。图片视觉入口及图片证据回退禁用；仅按已确认的同场账号读取五项文本，并要求至少三项高置信标签属于同一动态汇总行，其他行不用于补缺。该保守约束可能拒绝真实布局或漏读字段，不等于真实平台可用；真实页面识别、Windows-MCP接线和端到端链路仍须独立验收。

## 许可与来源

保留源清单提供的RapidOCR及模型许可材料。RapidOCR代码为Apache-2.0，随附模型许可另见`fixed-recap-prototype/rapidocr-MODEL_LICENSES.md`及`ocr-model-manifest.json`。锁定的Agents SDK与Python MCP SDK为MIT，Windows-MCP 0.8.8为MIT，分别见[OpenAI Agents SDK许可](https://github.com/openai/openai-agents-python/blob/main/LICENSE)、[Python MCP SDK许可](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE)和[Windows-MCP 0.8.8发行说明](https://pypi.org/project/windows-mcp/0.8.8/)。本目录不内嵌这些依赖包；两份直接依赖列表没有完整锁定全部传递依赖，安装方仍须随实际分发检查其许可。本目录仅包含客户端适配源码，不包含Windows-MCP二进制包、模型权重、API密钥、桌面截图或本地结果。
