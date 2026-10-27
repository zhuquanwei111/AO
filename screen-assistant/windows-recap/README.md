# Windows单场取数组件

该组件复用10号`store-finalization-20261027-r1`源码中的业务流程、固定步骤、Windows-MCP桌面客户端、天翼Agents SDK和RapidOCR；本目录已适配到仓库相对布局和外置运行目录。它由现有`backstage_bridge.py`按单一App场次调用，不依赖AO会话或聊天窗口。

## 运行布局与解释器

保留以下目录为同级目录：

- `glm-recap-prototype/`：CLI入口、GLM视觉适配和受限Agent SDK工作进程。
- `fixed-recap-prototype/`：已有结果解析、Store/Runner、页面步骤、MCP调用和OCR。
- `llm-recap-prototype/`：既有页面定位及原`fill_missing`结果补缺函数。没有复制旧Codex视觉调用。
- `windows-mcp/`：现有官方MCP客户端与9工具白名单适配。

后端/桥沿服务部署选择的Python运行；当前11号部署配置选择Python 3.12系列。Windows取数CLI使用独立Python 3.13环境。按10号已核依赖，再准备四个互相独立的Python环境：

1. CLI/OCR环境使用Python 3.13，并安装`fixed-recap-prototype/requirements-ocr.lock.txt`。
2. Agents SDK环境使用Python 3.13，并安装`glm-recap-prototype/requirements-sdk.lock.txt`。锁定版本包括`openai-agents==0.24.0`、`mcp==2.3.0`与`openai==3.37.0`。
3. MCP客户端环境安装`requirements-mcp-client.txt`中的`mcp==1.30.0`。
4. Windows-MCP环境安装`requirements-windows-mcp.txt`中的`windows-mcp==0.8.8`。

SDK内MCP（2.3.0）和连接Windows-MCP的客户端MCP（1.30.0）职责不同，不能合并为同一环境。后两份requirements只锁直接依赖；其传递依赖尚无完整锁，本交付不宣称全新机器安装已复现。OCR权重不入仓，由已存在的`provision_ocr.py`从锁定RapidOCR发行包提取并校验哈希后写到外部模型目录。

## 服务进程配置

将下列路径配置到运行普通后端/桥的Windows进程环境中。路径均由部署者按实际安装位置填写；不要把值写入本仓库或模型上下文。

| 环境变量 | 用途 |
|---|---|
| `BACKSTAGE_CLI_PYTHON` | CLI/OCR Python 3.13解释器 |
| `BACKSTAGE_CLI_ENTRY` | 本目录下`glm-recap-prototype/recap_glm.py` |
| `BACKSTAGE_CLI_DATA` | 外部本场记录目录 |
| `BACKSTAGE_CLI_MODELS` | 外部RapidOCR模型目录 |
| `BACKSTAGE_CLI_SCRATCH` | 外部scratch目录；MCP截图/模型任务临时文件均写在其下 |
| `BACKSTAGE_CLI_GATE_A` | 经单独授权生成的Gate A文件路径；此仓库不含该文件 |
| `BACKSTAGE_SDK_PYTHON` | Agents SDK独立Python 3.13解释器 |
| `BACKSTAGE_MCP_CLIENT_PYTHON` | `mcp==1.30.0`客户端解释器 |
| `BACKSTAGE_WINDOWS_MCP_PYTHON` | 安装`windows-mcp==0.8.8`的解释器 |
| `TIANYI_API_KEY` | 仅由受控进程环境提供天翼业务凭据，不存入文件、参数或任务上下文 |

桥沿现有`BACKSTAGE_CLI_DATA`、`BACKSTAGE_CLI_MODELS`、`BACKSTAGE_CLI_SCRATCH`和`BACKSTAGE_CLI_GATE_A`参数传路径；SDK/MCP解释器变量由CLI子进程读取。CLI会拒绝将记录、模型、Gate或scratch写入源码安装目录。MCP子进程不继承`TIANYI_API_KEY`、worker令牌或`AO_*`环境变量；仅工具客户端使用独立解释器及外部截图目录。

Gate A需对应现有模型名`glm-5.3-flash`和天翼业务入口`https://ai.ctaigw.cn/v1`。本交付没有运行Gate生成或模型请求。服务端worker令牌由桥进程读取，桥在启动CLI时会移除该令牌；CLI只在受控运行环境中取得业务模型凭据。

## 调用与来源

生产路径由桥按同一后端`session_id`、抖音号快照和App来源调用`start`。正常绑定且有有效身份/绑定证据时，桥等待真实同来源App `stop`，再调用CLI `stop`与只读`result`。如果`start`返回同场同源、无绑定的`needs_input`或终态`failed`记录，桥仍等真实App `stop`，之后调用一次CLI `stop`以保存停止来源并完成CLI本地失败收口；桥保留该CLI记录的原状态和原因，不再调用CLI `result`。若`needs_input`已有绑定证据但结果不确定，桥不再调用`stop`或重抓取；等真实App `stop`后回写未知/空值。无record或身份/source不匹配时不调用CLI `stop`/`result`，只在真实同场App `stop`后按后端任务身份回写空值。App来源保持`ios_app`或`android_app`；`test_simulated`只作明确受控测试。CLI的`start`/`stop`在`needs_input`或`failed`时返回exit 2并携带record；`status`/`result`是只读操作，应返回exit 0。source/身份不匹配不改标。

真实调用需Windows交互桌面、用户现有Edge登录态、安装好的四个隔离环境、外部OCR权重目录、有效Gate A及进程凭据。当前PR只交源码/配置模板，不启动桌面、模型或后台任务；不能将离线fixture测试当作抖音实播验证。

## 许可与来源

保留源清单提供的RapidOCR及模型许可材料。RapidOCR代码为Apache-2.0，随附模型许可另见`fixed-recap-prototype/rapidocr-MODEL_LICENSES.md`及`ocr-model-manifest.json`。锁定的Agents SDK与Python MCP SDK为MIT，Windows-MCP 0.8.8为MIT，分别见[OpenAI Agents SDK许可](https://github.com/openai/openai-agents-python/blob/main/LICENSE)、[Python MCP SDK许可](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE)和[Windows-MCP 0.8.8发行说明](https://pypi.org/project/windows-mcp/0.8.8/)。本目录不内嵌这些依赖包；两份直接依赖列表没有完整锁定全部传递依赖，安装方仍须随实际分发检查其许可。本目录仅包含客户端适配源码，不包含Windows-MCP二进制包、模型权重、API密钥、桌面截图或本地结果。
