# M0/M1 环境推进记录

更新时间：2026-10-12

## 当前决策与状态（2026-10-12）

- 用户已采纳：Windows Orchestrator 作为首期唯一主环境；并要求停止 Linux/WSL 执行与桥接。历史 WSL 工作树保留原样，不同步管理同一项目或 worktree。
- Windows AO v0.13.7 daemon 当前 `ready`、健康检查通过；主试点 `mh-pilot` 指向 `C:\Users\admin\AppData\Local\Temp\mh-pilot-orchestrator` 合成仓库，未注册真实原型项目。
- Codex GPT-6.1 Sol 的 AO Chat 模型调用已通过；但 `mh-pilot-20` 证明 AO `default` 权限允许写文件，Codex 规划/评审的只读边界验证失败。探测标记保留在隔离 worktree，无 commit/push。
- DSH 0.1.2-alpha.5 已接入天翼 ACP；直连 API、ACP 与 AO Chat 最小探测通过。`mh-pilot-21` 隔离编码 smoke 的 4 项 unittest 通过，0 commit/0 push；`mh-pilot-18` clean-session restore 后回到 idle，worktree clean。`mh-pilot-13` 证明默认工作区写模式允许任务范围外写入；`mh-pilot-14`/`mh-pilot-16` 的 `read-only` 探测拒绝写入，`mh-pilot-16` 的一次性升级仍为未批准 `pending`。
- 合成项目测试期间曾临时设 `DSH_PERMISSION_MODE=read-only`；拒写/审批探测完成后已恢复 `workspace-write`，DSH worker 仍显式为 `permissions=default`。AO 当前不能按 Harness 角色单独设置该环境变量。
- Kimi Code 2.1.1 已安装；天翼 HTTP API、CLI 和原生 ACP 通过。截图中的旧 CLI 探针曾有 `marker_match=False`，已改为分别捕获 stdout/stderr 的隐藏输出脚本并复测成功。AO Chat 仍被 `CHAT_AUTH_REQUIRED` 阻断；AO TUI 会话仍在运行，尚无可核验完成结果。
- 天翼模型共用同一 `TIANYI_API_KEY`；AO daemon 子 Harness 可能继承该变量，不能声称按 Harness 隔离。AO 的 SQLite 会话活动记录会持久化 MCP 工具调用的输入字段（包括路径和写入内容），当前不能宣称已实现脱敏审计。
- ZCode 没有 AO 内置 Harness 适配器，按用户决定暂不纳入首期。M0 仍只使用 `mh-pilot` 合成仓库，真实原型项目未注册、未触碰。

| Harness | Windows 本机/AO 状态 | 已有证据 | 下一门禁 |
| --- | --- | --- | --- |
| Codex | CLI 0.161.0；目标 GPT-6.1 Sol AO Chat 调用通过 | `mh-pilot-20` 在 `default` 下写入 `CODEX_READONLY_PROBE.txt`；只读边界失败；AO CLI 没有 `read-only` 权限选项 | 找到可由 AO 强制的只读 sandbox 传递方式或受限适配器；验证前不分配硬只读角色 |
| DeepSeek Harness | DSH 0.1.2-alpha.5；天翼 ACP/AO Chat 最小探测通过 | `mh-pilot-21` 编码 smoke 4/4 unittest；read-only 拒写、取消、dirty worktree 保留、clean session restore 已验证；`mh-pilot-16` 升级仍 pending；AO 活动保留原始工具输入 | 保持 `permissions=default`；验证审计脱敏和任务文件级写权限 |
| Kimi Code | CLI 2.1.1；天翼 API、CLI、原生 ACP 通过 | AO Chat `CHAT_AUTH_REQUIRED`；TUI 会话未核验完成 | 解决 AO Chat readiness，核验或停止 TUI 探索 |
| ZCode | 无 AO 内置适配器 | 用户决定暂不纳入首期，未发起请求 | 不进入 M0；后续需要时再评估 |

以下条目保留此前的按时间顺序记录；与本节或后续最新试验章节冲突时，以最新记录为准。

## 2026-10-12 Codex 只读失败与 Kimi CLI 复测

- AO `mh-pilot-20` 用 Codex + GPT-6.1 Sol、`permissions=default` 执行单文件写入探测；Codex 成功创建 `CODEX_READONLY_PROBE.txt`。这是 AO 默认权限没有提供只读隔离的实证，不代表其他 AO 权限模式。
- 探测文件位于 `C:\Users\admin\.ao\data\worktrees\mh-pilot\mh-pilot-20\CODEX_READONLY_PROBE.txt`，仍未跟踪；0 commit、0 push。worktree 保留，不清理或回滚。
- `ao project set-config --help` 权限枚举为 `default`、`accept-edits`、`auto`、`bypass-permissions`；spawn 没有每会话 sandbox 参数。AO 官方文档声明实验性 reviewer 提示不提供工具/网络沙箱。因此 Codex 当前不满足只读规划/终审角色的 M0 门禁。
- 官方 Codex app-server 协议本身支持 `read-only` sandbox，但没有证据 AO v0.13.7 把它传入当前 Chat 会话。未改全局 Codex 配置；后续只在合成项目评估 AO 官方支持的覆盖方式或独立受限适配器。
- 同日重新运行隐藏输出的 Kimi CLI 探针：`tianyi/kimi-k3` 标记匹配、退出码 0；不输出密钥或提示正文。该证据不替代 AO Chat/TUI 接入验收。

## 2026-10-12 DSH clean-session restore

- 对已终止且 worktree 干净的合成会话 `mh-pilot-18` 执行手动 `ao session restore`。会话从 `terminated` 回到 `idle`，session ID 和分支保持不变。
- restore 后 worktree 存在、`git status --short` 为空、0 commit、0 push；未重跑产生标记的任务。该结果只覆盖 DSH clean-session 恢复，不代表 dirty session 或失败中断恢复已验收。

## 2026-10-12 DSH bounded coding smoke

- `mh-pilot-21` 在 `ao/mh-pilot-21/root` 完成小型 Python 任务，只创建 `m0_smoke.py` 和 `test_m0_smoke.py`，标准库 `unittest` 4/4 通过；随后由本机独立复跑，结果仍 4/4。
- Git 状态保留两个源文件与 `__pycache__/`，0 commit、0 push。该证据只覆盖小型编码/测试任务，不证明大型任务质量、任务文件级隔离或脱敏审计。

## 已核验

- 模型策略 A 已拍板：build 请求固定使用天翼 API 模型 ID `deepseek-v4-pro-0813`，接受供应商服务端路由；客户端不自动切模。API 探测 HTTP 200，返回 `response.model=deepseek-v4-pro-0813`、77 tokens、约 2.4 秒。该字段不能证明供应商底层实际服务版本。
- GPT 规划/评审使用 OpenCode 的官方 ChatGPT OAuth（非 OpenAI API Key）。OpenCode CLI 对 `openai/gpt-6.1-sol` 的无代码短探针成功，返回 `M0_GPT_READY`。
- WSL2 Ubuntu 24.04、OpenCode 1.19.0、Git 2.43.0、Node 24.21.0 LTS、Python 3.12.3、Playwright MCP 0.0.83 与 Chromium 已就绪。
- Playwright 使用任务临时用户目录验证成功；检查了最终 URL、DOM 和控制台。外网探测经显式传入 WSL 的 `HTTPS_PROXY` 后成功。
- OpenCode 登录记录仅确认 provider/认证类型；未输出或写入报告任何 API Key、OAuth token 或代理凭据。

## 尚未完成

- 当前工作区 Git 仓库没有提交、远端或实际产品代码；尚无用户指定的目标代码仓库迁入 WSL2，也未建立任务级 worktree 生命周期。
- 外部状态机、MCP 权限网关、按角色隔离的 Worker 凭据和全量工具审计仍需自研，尚不能安全开放文件、终端、Git 或浏览器 MCP 给 Agent。DeepSeek 凭据目前只是 OpenCode 的 `tianyi-build` provider 凭据，尚未隔离到网关 Worker。
- OpenCode 的 GPT 调用事件没有暴露原始 HTTP 状态码和供应商 `response.model`；此类字段在接入统一审计网关前应记录为不可得，不得推测。
- 已核对天翼文档对 429 限流/限额、504 网关超时的说明；没有主动触发真实限流或超时。重试最多 2 次、401/403 不重试、失败阻断且不切模型，待 M2 网关完成后用受控模拟核验。
- Playwright MCP 已安装并单独验证，但未注册到 OpenCode Agent，避免绕过尚未完成的权限网关。

## 下一步顺序

1. 确定实际代码仓库后放入 WSL2 Linux 文件系统，创建干净基线并核验任务 worktree 隔离。
2. 实现外部 SQLite 状态机与 MCP 权限网关；先验证越权拒绝、审计脱敏、失败阻断与人工审批，再接通 Agent 工具。
3. 通过网关做可控的限流/超时故障注入；只在通过后进入六阶段试点任务。

质量验收优先。此记录不设成本对比或成本优化目标；token 用量仅按既定审计字段保留。

## 2026-10-12 多 Harness 调研更新

以下信息覆盖上面 2026-10-11 的部分旧状态。WSL 中已经导入用户指定的“国庆后的原型”快照并创建任务 worktree；旧状态文案所说“尚无目标代码仓库”已过期。OpenCode + WSL2 的 SQLite 状态机、MCP 网关和模型代理原型保留在 `/home/mvpdev/worktrees/multi-agent-workflow`，工作树存在未提交改动，不得清理或重置。

- Omnigent 0.17.0 已安装到独立试验目录，版本校验及 WSL2 `bwrap` 预检通过；这只证明运行时安装和沙箱基础能力，不代表四 Harness 端到端接入完成。
- Codex CLI、Kimi Code、DeepSeek Harness 均已在隔离目录完成 CLI 启动/帮助检查；ZCode 源码在独立目录构建成功，CLI 版本为 0.16.9。除原 OpenCode 的历史登录之外，新隔离 Harness 尚未完成认证，也未用第三方密钥发起模型请求。
- 当前 WSL Codex CLI 报未登录；Windows Codex/OpenCode 登录不会自动变成 WSL CLI 凭据。Kimi、DSH、ZCode 也需分别在其隔离身份目录登录后才能做真实模型连通性验证。
- 本机命令核验确认 `dsh --profile acp --help` 与 `kimi acp --help` 均存在 ACP stdio 模式；Omnigent 自带 Kimi harness，但没有 DSH 内置项。ACP 握手、模型请求、权限拒绝、取消和审计仍待测。
- ZCode 0.16.9 提供 `--prompt`/`--json`/`--disallowed-tools`/`--mode guarded` headless 参数；`app-server` 标明使用 ZCode Protocol，不是 ACP。文件隔离、取消和结构化结果回传待验证。
- Omnigent 原生支持 Codex，Kimi 为社区支持；Polly 将各 Worker 放入独立 worktree，并支持跨 Harness 评审。其默认 Worker 清单未包含 DSH 与 ZCode。DSH 的 ACP profile 可作为配置接入候选；ZCode `app-server` 是自有协议，需独立验证桥接/中断/审计。
- 新核对的 CCTeam（约 648 stars）是真正跨 Harness 派发层：daemon + MCP `agent`/`agent_read` + JS Flow，可驱动 Codex、DSH、Kimi、OpenCode 等独立会话；未列 ZCode。其文档明确写明并行文件编辑共享 project working tree、没有 per-hire 隔离。它传递任务消息与文件/技能路径，各 Harness 保持独立会话并按自身机制读取项目指令；不需要复制整个主对话长上下文。
- dsh-alpha（约 3 stars）列出 Codex、DSH、Kimi、ZCode 等 worker，能参考其网关、workspace 映射和任务事件；但成熟度较低，DSH worker 仅回传最终文本且不转发内部审批，不作为首期底座。Morning Star（约 63 stars）仍是可安装到多个 Host 的工作流插件，并非跨 Harness 调度器。
- DeepSeek 官方仓库标注其 Harness 仍处于 developer preview，并警示兼容性破坏变更；固定版本和适配回归是 M0 必需项，即使其 GitHub 关注度很高也不降低该风险。
- 因此 M0 候选调整为对比 CCTeam（派发与会话生命周期）和 Omnigent（worker/worktree 隔离与评审）。SQLite 服务继续作为唯一流程状态源；任何项目的原生 spawn/Flow 都必须接入状态机与权限网关后才能开放，不能直接复制其默认无头执行配置。ZCode 仍是适配缺口。

### 新的 M0 门禁

1. 为 Codex、Kimi、DSH、ZCode 建立 Harness × 角色 × 模型 × 认证 × ACP/原生工具 × 中断 × 审计兼容矩阵；未完成真实 CLI/工具事件验证的标为“待验证”，不算接入成功。
2. 先验证 Omnigent 自定义 ACP 对 DSH 与 Kimi 的启动、工具请求/响应、权限拒绝、取消和结果回传。Kimi 原生 Harness 与 ACP 方式选实测更可审计的一种。
3. ZCode 先检查 `app-server` 事件/工具协议；评估使用官方 CLI 的受限 headless 模式或写最薄的协议桥，不重写 Agent 循环。若无法阻止未经授权的本机工具，不开放写任务。
4. 禁用所有绕过 SQLite 的 worker spawn 路径；仅由受控派发器检查阶段、任务角色、worktree 与凭据后启动子会话。
5. 对真实 API 连通性、模型标识、429/超时/鉴权失败分别记录证据；当前无密钥请求测试仍未完成，不在报告中写成通过。

项目调研细节见 `多Harness参考项目调研.md`；当前方案见《多智能体协同开发系统搭建方案 Orchestrator 与 WSL2 试点版 3.0》。

## 2026-10-12 Orchestrator.inc 最新试验

此节覆盖此前“优先比较 CCTeam 与 Omnigent”的底座候选建议；目标四 Harness 和 SQLite/MCP 门禁仍未完成验收。

- Windows Orchestrator v0.13.7：Codex、Claude Code 各完成一个独立 worktree 单文件 smoke；Codex orchestrator 创建 Claude Code 子会话 mh-pilot-6，Claude 在 ao/mh-pilot-6/root 创建指定文件，未 commit/push。该试验验证 AO 跨 Harness 委派与隔离，不验证外部状态机/权限网关。
- WSL2 Linux Orchestrator：官方 v0.13.7 deb 下载并校验 SHA-256 后，解压到 /home/mvpdev/.local/opt/agent-orchestrator-pilot；未安装系统包。使用 AO_DATA_DIR=/home/mvpdev/.ao-pilot/data 和独立 run file；daemon status 为 ready、health 为 ok。ao version 返回 dev 构建标识。
- WSL2 OpenCode 1.19.0 路径 /home/mvpdev/.opencode/bin/opencode，在 AO agent catalog 中为 authorized。合成仓库 /home/mvpdev/mh-pilot-wsl 注册为 mh-pilot-wsl，AO 创建 worker mh-pilot-wsl-1 和 worktree /home/mvpdev/.ao-pilot/data/worktrees/mh-pilot-wsl/mh-pilot-wsl-1。只新增 opencode-result.txt，内容为 “OpenCode worker completed in WSL.”；未 commit/push，session 已 idle，源仓库 main 保持 clean。
- WSL Harness 发现：codex 解析为 /mnt/c/Users/admin/AppData/Roaming/npm/codex，且未通过 AO 探测；dsh 解析为 Windows npm shim；kimi 和 zcode 未在 PATH 中发现；OpenCode 已授权。Windows DSH smoke 曾返回鉴权类错误。未读取或输出任何 API Key。
- 本轮只使用合成仓库；没有注册或修改真实原型项目。现有真实项目/状态机工作树保留原样。
- 当时的下一步建议已被后续 Windows 主环境决策覆盖；WSL Codex 登录仍按用户选择暂缓，不重启设备登录。
- CAO Linux Codex 对照仍暂缓；不把安装成功或 OpenCode smoke 记为 CAO/目标 Harness 接入通过。

## 2026-10-12 Windows 主环境决策与 DSH 复验

- 用户采纳 Windows Orchestrator 作为首期主环境；WSL2 降为 OpenCode 接入验证环境。两边的 AO 数据目录和 synthetic project 分离；真实项目不注册到两边。
- Windows AO v0.13.7 daemon `ready`、`healthz ok`；项目列表中仅有合成项目 `mh-pilot`，路径为 `C:\Users\admin\AppData\Local\Temp\mh-pilot-orchestrator`。真实原型项目未触碰。
- `ao agent ls --refresh` 报 DeepSeek Harness installed/authorized，且本机 `dsh --version` 为 0.1.2-alpha.5；这仅代表本地 adapter/凭据目录检测结果。
- Windows AO 创建隔离 worker `mh-pilot-7` 后，首次 prompt 与后续显式 steer 均在 ACP turn 返回 Authentication Fails；对应 worktree `C:\Users\admin\.ao\data\worktrees\mh-pilot\mh-pilot-7` 只含基线 README，没有新增文件，未 commit/push。无需再次重试，当前阻塞是 DSH 凭据鉴权。
- `ao doctor`：daemon、Git、ConPTY、Claude Code、Codex CLI 基础检查通过；另报告 `codex-launch-flags` 与 Codex 0.161.0 不兼容，AO 注入的配置参数被拒，可能缺少活动 hooks。旧 Codex smoke 不足以证明当前版本 hooks 正常。
- 下一步执行顺序：先由用户在 DSH 自身凭据设置修正无效 API Key，再于合成项目复跑；同时复验 Codex 目标模型与启动配置。随后安装/验证 Windows Kimi、评估 ZCode 官方 CLI/app-server 桥接。之后需决定控制面走 Windows 移植还是 WSL 桥接，再接入 SQLite 状态机和 MCP 权限网关。密钥不读取、不回显或写入审计。

## 2026-10-12 Windows AO 复用决策（最新）

- 用户要求停止 Linux 部分以免干扰。此后不再执行 WSL/Linux 命令、不新建 Linux AO/worker、不做 Windows↔WSL 路径或权限桥接；本轮没有执行 `wsl --shutdown`，避免关闭用户可能正在使用的 Ubuntu 会话。
- WSL 控制面工作树与历史 OpenCode 合成试验保持原样。没有清理、重置、移植或改写其中的文件；历史通过结果不代表当前 Windows 目标 Harness 已通过。
- 官方 Orchestrator 文档确认 Windows 是正式桌面发行平台；AO 原生管理 worker、project orchestrator、Git worktree、review 与会话状态。最新支持矩阵列有 Codex、DeepSeek Harness、Kimi 等，Chat/worker/reviewer 能力各自不同，需逐 harness 验收。
- 项目复用结论：首期仅用 Windows AO 做协调与执行；不另装 CCTeam 或 Omnigent。ACP 直接复用 AO adapter，DSH/Kimi 不另写协议客户端。CCTeam（648 stars）的 MCP 派发、每次派发前 fail-closed 策略 hook 值得借鉴；公开文档未说明 per-worker worktree 隔离，且 Windows 路径要求 WSL。Harnss（384 stars）有 ACP/per-project MCP 参考价值，但公开标注为早期开发并计划大改写；不作为运行底座。
- 权限边界仍需实测：AO 的 MCP 能力依赖 Harness/Provider，MCP allowlist 不会自动约束 Harness 原生文件、终端、Git 工具。先在 `mh-pilot` 合成项目验证每角色工具列表和写权限；无法证明只读边界的角色不授写权限。
- 新方案文件：`多智能体协同开发系统搭建方案_Orchestrator试点版_v3.2.md` 与 `.docx` 已更新；研究结论追加到 `多Harness参考项目调研.md`。最新决定是首期复用 AO 原生编排与人工确认，不开发外部状态机；工具权限继续按 Harness 核验，具体缺口先报告再由用户决定是否补组件。

### Windows-only 下一步（较早快照，已由下方接入复测更新）

1. 在合成 AO 项目上复验 Codex 0.161.0 的启动参数兼容性、模型与只读工具权限。
2. DSH 天翼 ACP 路由及 AO Chat 最小派发已通过；继续验证权限拒绝、取消和审计。
3. Kimi Code 的天翼 API、CLI 与原生 ACP 已通过；处理 AO Chat `CHAT_AUTH_REQUIRED` 并核验 AO 终端路径。
4. ZCode 当前没有 AO 内置适配器，按用户要求首期不使用、不做桥接。
5. 在 Windows 合成项目验证 AO 原生 orchestrator/worker/reviewer 和人工确认是否满足实际任务；首期不另建状态机，不回到 Linux/WSL 桥接路线。


## 2026-10-12 用户确认的首期协作方式

- 用户确认 AO 原生 orchestrator/worker/reviewer 编排满足首期协作需求；阶段按任务灵活裁剪，重要交付点由人确认。首期不开发独立外部状态机，也不把 AO 会话状态描述为自定义阶段的硬门禁。
- Kimi Code CLI 2.1.1 已按官方 Windows PowerShell 安装器安装到 `C:\Users\admin\.kimi-code\bin\kimi.exe`。该早期快照中的独立 Key 配置待办已被后续用户确认的“天翼模型共用 Key”前提覆盖；当前使用 `TIANYI_API_KEY`。
- ZCode 当前没有 AO 内置适配器，按用户要求先不使用、不做首期桥接。

## 2026-10-12 天翼 API 模型来源确认（早期快照，已由后续共享 Key 决策覆盖）

- 用户确认：GPT-6 Sol 通过官方订阅账号使用；DeepSeek、Kimi、GLM 等第三方模型均由天翼供应商 API 提供。M0 当前角色只分配 DeepSeek 和 Kimi；天翼目录中的 GLM 暂不启用。
- 当时 Kimi `config.toml` 的模型和天翼 OpenAI-compatible 地址已配置，但 Key 来源与 AO 识别尚未验证；后续改为引用共享 `TIANYI_API_KEY`，API、CLI 与原生 ACP 均已通过。
- 当时 AO `authStatus` 为 unknown；之后 AO 已识别 Kimi 为 installed，但认证状态仍为 unknown，AO Chat 返回 `CHAT_AUTH_REQUIRED`。
- 现有 DeepSeek 策略保持：请求 `deepseek-v4-pro-0813`，接受天翼服务端路由并记录可见返回模型标识；客户端不自动切换。第三方 API 审计不记录完整提示、密钥或代码原文。

## 2026-10-12 天翼共享 Key 与 Kimi CLI 复测（最新）

- 用户确认：DeepSeek、Kimi、GLM 等天翼模型共用同一个供应商 API Key；GPT 仍走 Codex 官方订阅。Kimi 与其他天翼模型统一使用用户级环境变量 `TIANYI_API_KEY`，不记录 Key 值。
- Kimi `config.toml` 已指向天翼 OpenAI-compatible provider，默认模型为 `tianyi/kimi-k3`，Key 来源为 `TIANYI_API_KEY`。
- HTTP API 探测：状态 200、返回模型 `kimi-k3`、110 tokens、约 3158 ms。Kimi Code CLI 2.1.1 的安全探测也已成功（退出码 0，标记匹配）；未输出密钥或提示内容。
- 截图中的错误发生在 PowerShell 将 CLI 的 stderr 按原生命令错误处理，不代表 API 鉴权失败。已改用共享 Key 的无回显探测脚本，通过独立进程捕获 stdout/stderr 后确认 CLI 成功。
- AO `agent ls --refresh --json` 现将 Kimi 列为 installed，但 `authStatus=unknown`；AO Chat 的认证预检仍未通过，原生 ACP 探测已通过。
- 天翼供应商不给各 Harness 独立 Key，因此不能宣称凭据按角色隔离；只在需要的进程中注入环境变量，保持工具权限隔离并避免写入提示、审计和日志。

## 2026-10-12 DSH ACP 与 AO 派发复测

- 用户前提继续适用：GPT-6 Sol 走 Codex 官方订阅；DeepSeek、Kimi、GLM 走天翼第三方 API，共用用户级 `TIANYI_API_KEY`，没有自托管或专属节点。
- 修正 DSH ACP 模型路由：`~/.dsh/profiles/acp/cordis.patch.yml` 为 `llm-pi-ai` 配置 Tianyi OpenAI-compatible endpoint 和 `apiKeyEnv: TIANYI_API_KEY`；同时将 `agent-default-model` 与 ACP 专属 `acp` 插件的默认路由设为 `tianyi/deepseek-v4-pro-0813`。此前只改通用默认模型，ACP 仍用 `deepseek-official`，所以认证失败。
- 直连天翼 DeepSeek 探测：HTTP 200，响应模型 `deepseek-v4-pro-0813`，返回标记匹配。DSH 原生 ACP 探测成功；AO `mh-pilot-10` Chat 经 DSH ACP 返回验证标记，消息已在 AO 会话记录中确认。该 AO worktree 为 0 commit、未 push；只执行只读短提示，没有改动文件。
- DSH profile 已列出天翼模型：`deepseek-v4-pro-0813`、`deepseek-v4-flash-vision-exp-0817`、`kimi-k3`、`glm-5.3`、`glm-5.3-flash`。MVP 默认只将 DeepSeek Harness 用作编码角色，Kimi AO 适配未闭环、GLM 暂不纳入角色池。
- 修复截图中的 Kimi 探测脚本：原脚本仍询问 Kimi 专属 Key，并把 NativeCommand stderr 当作终止错误。改用共用 `TIANYI_API_KEY`，隐藏 CLI 输出后重测，Kimi Code CLI 2.1.1 退出码 0、标记匹配；Kimi API 探测此前也为 HTTP 200，返回模型 `kimi-k3`。Kimi 原生 ACP 探测已通过。
- Kimi AO Chat readiness 仍显示 `unknown`；此前 AO Chat 返回 `CHAT_AUTH_REQUIRED`，说明 AO 对自定义 Tianyi Provider 的认证预检与 Kimi 当前 provider 配置不匹配。不能把 Kimi CLI/API 通过写成 AO Chat 已接通。另起的 AO Terminal UI 探索会话当前 idle，结果标记未独立确认，暂不计入通过。
- 共享 Key 暴露范围：AO daemon 的本次启动环境含 `TIANYI_API_KEY`，其子 Harness 进程可能继承该变量，包括 Codex。AO per-Harness 环境隔离尚未验证；当前可证明“共用同一供应商 Key”，不能证明“该 Key 只对 Tianyi Harness 可见”。密钥值未显示、未写入项目仓库或审计记录。
- 当前下一步：核验 Codex AO 启动参数与 GPT-6 Sol 账号/模型；验证 DSH 的拒权、取消与审计字段；继续修复或确认 Kimi AO Chat 认证预检，必要时再独立验证 AO TUI 路径。权限/恢复验收前仍不触碰真实原型项目。

## 2026-10-12 Codex 角色模型与只读边界复核

- 截图旧脚本的 HTTP 探测返回 200，但 `marker_match=False`，该次 CLI 探测不能算通过；红色 `NativeCommandError` 是 PowerShell 把原生命令 stderr 作为错误流处理。更新后的隐藏输出探测脚本使用已配置的共享 `TIANYI_API_KEY`，本轮复跑 Kimi Code CLI 2.1.1 成功；没有显示密钥或提示正文。
- AO 桌面进程与 daemon 仍在运行，daemon health/ready 正常。当前 PowerShell 的 PATH 没有 `ao` 命令；通过桌面安装目录内的 `resources\daemon\ao.exe` 访问同一 daemon，确认 `mh-pilot` 的 orchestrator 为 Codex、worker 为 DeepSeek Harness。
- 将合成项目 `mh-pilot` 的 orchestrator 默认模型保存为 `gpt-6.1-sol`，保留原 worker 和其余可见配置。现有 `mh-pilot-11` AO Chat 已用该模型返回验证标记，worktree 无文件改动、0 commit、未 push；刚才尝试新建 orchestrator smoke 时命令返回了历史 session `mh-pilot-5`，daemon 列表确认没有新会话，因此“项目角色配置生效”仍待新会话验证。
- `ao project set-config --help` 显示角色权限选项为 `default`、`accept-edits`、`auto`、`bypass-permissions`，没有显式 `read-only`。本机 Codex CLI 支持 `--sandbox read-only`，但没有证据证明 AO Chat 会从项目级 Codex sandbox 配置读取并强制该限制；当前不把 Codex 只读权限记为通过，也未修改全局 Codex 配置。
- Kimi AO session `standalone-1` 当前状态为 `working`、Harness 为 Kimi、模型为 `tianyi/kimi-k3`；未读取到可核验的最终回复。它保持运行，不将其标记为通过。Kimi AO Chat 仍有 `CHAT_AUTH_REQUIRED` 问题。
- DeepSeek/Kimi/GLM 继续共用天翼供应商 Key；AO daemon 环境变量可能被子 Harness 继承，尚无按 Harness 隔离的证据。没有接触真实原型项目。
- 下一步：在不改全局 Codex 设置的前提下，验证合成 AO worktree 内的 Codex 项目级只读 sandbox 是否被 Chat/app-server 强制；单独确认 Kimi TUI 返回结果或其阻塞原因；随后完成 DSH 拒权、取消与审计字段检查。

## 2026-10-12 DSH 隔离写入 smoke

- AO `mh-pilot-12` 使用 DSH Harness 在独立 worktree 执行了一个用户已明确授权的写入提示，生成 `permission-probe.txt`，内容为 `DSH_WRITE_PROBE`。worktree 只有该未跟踪文件，0 commit、未 push；合成仓库基线未改变。文件保留用于审查，没有自动回滚。
- AO 会话记录有 2 条已完成的 `mcp_tool` 活动，没有审批活动类别；安全筛选后的记录未能确认工具名称。因此这证明任务内写入可通过 AO MCP 活动完成及 worktree 隔离有效，不证明越权写入会被拒，也不证明存在覆盖 Harness 原生工具的独立权限网关。
- 下一步在合成 worktree 执行明确超出任务授权范围的拒绝探测，并核验取消与工具名/调用结果等审计字段。用户授权前不测试真实项目路径、数据库或远程 push。

## 2026-10-12 Kimi ACP API-Key 认证阻断复核

- 截图中的旧探针 HTTP 200 但 `marker_match=False`，该次不算 CLI 模型回答成功。更新后的隐藏输出 CLI 提示探针对 `tianyi/kimi-k3` 返回标记、退出码 0；Key 与提示正文未显示。
- AO Kimi Chat 实际 spawn 失败：`CHAT_AUTH_REQUIRED`。`ao agent ls --refresh --json` 显示 Kimi installed、`authStatus=unknown`；能力目录登记和已安装状态不代表认证/会话创建通过。
- 官方 Kimi Code ACP 认证门只检查 Kimi OAuth token；上游 PR [#2185](https://github.com/MoonshotAI/kimi-cli/pull/2185) 记录 API-Key 配置仍被强制 OAuth 的问题。该已知限制与当前 AO 失败相符；此处不是天翼 API 连通性失败。
- `standalone-1` Kimi TUI smoke 仍为 `working`，最终回复未核验，不终止也不清理。新建的 `mh-pilot-17` 当前为 `working/active`，AO CLI 未提供可核验的终端回复；合成 worktree 只有基线文件，不计通过。
- 下一步需从 AO TUI 会话界面核验输出，或等 Kimi ACP API-Key auth gate 上游修复后重测 Chat；通过前不把 Kimi 算入闭环，真实项目保持未触碰。

## 2026-10-12 DSH 取消与现场保留复测

- `mh-pilot-18`：45 秒无副作用 sleep 子进程被 `ao session kill` 终止；因 worktree 干净，AO 自动回收目录。无任务改动丢失。
- `mh-pilot-19`：写入单一标记 `.ao-cancel-retain-probe.txt` 后运行 sleep；kill 后 AO session 终止、子进程归零，worktree 与未跟踪标记仍保留，0 commit、0 push。标记留作审查证据，不清理。
- 结论：已验证 kill 会停止 DSH 子进程，且 dirty worktree 保留；干净目录会安全回收。`session restore`、复杂失败恢复、任务文件级写权限和完整脱敏审计仍待核验。

## 2026-10-12 AO 评审隔离与中文规则配置

- AO 官方 review loop：reviewer 是 worker 之外的独立 reviewer run，针对 worker PR 的当前 head 评审，不拥有 worker 分支；worker permissions 不会传递给 reviewer。它描述的是独立会话/评审对象边界，不要求存在通用 `read-only` 权限。提示词本身不构成工具或网络隔离。
- OpenAI Codex Cookbook 的参考模式：review 从固定 artifact 内容产生结构化 findings；repair 只修改 artifact 副本。该模式将发现和修改拆开，不把“提示不编辑”当成强制权限隔离。OpenAI sandbox 文档进一步建议限制输入挂载，并在产物离开沙箱前审查。
- 已用 AO 项目配置为合成项目 `mh-pilot` 设置简体中文 `agentRules` / `orchestratorRules`，通过 `ao project get mh-pilot --json` 验证写入成功；worker 仍是 DeepSeek Harness，orchestrator 仍为 Codex + `gpt-6.1-sol`，保留原权限及 `DSH_PERMISSION_MODE`。新建会话读取这些项目规则，已有会话与历史英文消息不会追溯变更。
- AO 当前文档有 worker 与 orchestrator 规则字段，未见独立 reviewer 规则字段；UI 固定菜单、按钮、模型名、命令与路径也不会被项目规则本地化。评审输出中文仍需在合成项目触发 reviewer 后实测。真实原型项目没有注册或修改。
- 下一步：在合成项目验证 AO reviewer 对固定 PR head 的评审行为、实际回复语言与评审前后工作区差异；检查 reviewer 规则注入办法前，不宣称评审角色语言或只读权限已闭环。

## 2026-10-12 AO reviewer 实际触发预检

- 在合成 worker `mh-pilot-21` 上以 `ao review trigger --no-inject` 尝试启动 AO 原生 reviewer。AO 返回 `REVIEW_INVALID`：AO 尚未跟踪该 worker 的 PR；没有 reviewer run，也没有开启 review auto-inject。
- `mh-pilot` 的 `origin` 是本地 bare Git 路径，不是 GitHub/GitLab。AO 官方 reviewer 入口要求关联 PR 并按 provider 当前 PR head 评审。因此当前不能核实 reviewer 实际读取的 head、回复语言或 reviewer 工作区的前后差异。
- worker 仍为 idle、0 commits；源仓库 `main` 干净。既有 worker worktree 保留 `m0_smoke.py`、`test_m0_smoke.py` 与 `__pycache__`。由于 AO 未启动 reviewer，本次没有 reviewer 工作区可比较；真实项目未触碰，也没有创建远程仓库或 PR。
- 完成端到端验证需要一个 AO 可识别的 GitHub/GitLab 合成 PR。可使用用户提供的临时测试仓库，或在用户明确同意后创建隔离的私有测试仓库；不伪造 AO 数据库中的 PR/review 记录来冒充真实链路。
- AO review loop 当前文档还明确说明：reviewer harness 的权限可与 worker 不同，实验性 reviewer 并不会自动获得沙箱隔离；所以即使后续 PR 触发成功，也必须用合成 PR 实测实际工具权限与文件状态，不能从“独立 reviewer”推断“只读”。

## 2026-10-12 GitHub 账号只读核验

- 用户提供 GitHub 个人主页 `https://github.com/zhuquanwei111`。公开主页显示 2 个公开仓库：`ai-agents-for-beginners` 与 `bignodes`，均为 fork；未发现合适的合成 PR 测试目标。
- 本机未安装 GitHub CLI（`gh`），因此无法通过 CLI 核实该账号的私有仓库、认证状态或创建 PR。没有进行任何远程写操作。
- 继续 AO 原生 reviewer 端到端验证需要一个已认证、由 GitHub/GitLab 识别的合成仓库与 PR；可由用户提供，或在用户明确授权并完成账号认证后创建私有测试资源。

## 2026-10-12 仓库链接更正与真实项目保护

- 用户指出 `https://github.com/zhuquanwei111/mingjing-develop`。只读核对确认本机 `D:\mingjing-develop\国庆后的原型` 位于 Git 根目录 `D:\mingjing-develop` 内，`origin` 为 `github.com/zhuquanwei111/mingjing-develop`。未登录网页返回 404 不能说明仓库不存在；可能是私有仓库。
- 本地该真实仓库当前在 `frontend` 分支，存在大量既有修改和未跟踪内容；本轮只读取 Git 元数据/状态，没有改动、暂存、提交或推送。
- 该仓库与本轮指定的合成 AO reviewer 测试目标不同。继续按“真实项目不触碰”的既有边界，不在它上面创建测试提交或 PR。端到端 AO reviewer 验证仍需单独的空白合成仓库/PR，或用户明确调整测试范围。

## 2026-10-12 用户授权将真实分支用于 AO Reviewer 验证

- 用户明确要求：用 `mingjing-develop` 仓库的 `frontend` 分支验证 AO Reviewer；这覆盖此前“真实项目不用于本次验证”的范围限制，但不等于授权提交、推送或合并任何代码。
- 只读检查：本地 `frontend` HEAD 为 `1c1786f`，比 `origin/frontend` 多 81 个提交；本地工作区另有 275 项修改/未跟踪内容。`origin/frontend` 为 `a9e594a`，仅比 `origin/main` 多 3 个提交；本地 HEAD 对 `origin/main` 多 84 个提交。
- PR diff 规模差异很大：远端 `origin/frontend` 相对 `origin/main` 为 684 个文件、约 98,173 行新增；本地 `frontend` HEAD 相对 `origin/main` 为 2,087 个文件、约 474,515 行新增。没有提交、推送、创建 PR 或合并。
- 后续必须先确定 AO 应评审的确切快照（远端 `origin/frontend`，或本地 HEAD `1c1786f`）。若选择本地 HEAD，需先由用户确认是否允许将 81 个本地提交推送并为其创建 PR；当前 275 项未提交工作区内容默认不包含在 PR 中。

## 2026-10-12 `mingjing-develop` 隔离 AO Reviewer 验证

- 用户选择隔离验证 PR。未改写真实 `frontend`，而是从 `origin/frontend` (`a9e594a`) 建临时分支 `ao-review-smoke-20261012-7f3c`，只加两个烟雾文件并提交 `9ff9ffd`；创建私有 PR [#1](https://github.com/zhuquanwei111/mingjing-develop/pull/1)，目标分支为 `frontend`。PR 明确标记不要合并，未合并。
- 为避免影响现有 AO 会话，使用临时 AO daemon（端口 3137、独立数据目录）注册临时项目，并以隔离 owner session `mingjing-review-smoke-1` 关联该 PR。owner 使用 Claude Code TUI 仅作 PR 归属；Reviewer 选择 Codex + `gpt-6.1-sol`，调用时带 `--no-inject`。
- AO 实际完成两次评审，head 均为 `9ff9ffd63b9781246349a25df22decf25f5e8273`；第一次 run `f5b7659e-0c5d-4ecd-ab26-6feb97723420`，第二次在 PR 描述加入简体中文要求后 rerun，run `4e63557c-32ea-43ed-a0ec-52209f4794bf`。两次均给出 `changes_requested`，并正确指出 `can_start_job` 的 `<=` 边界错误，说明 AO 能把实际 PR head 和变更 diff 交给 Codex Reviewer。
- 两次评审输出仍为英文。PR 标题、描述为中文也没有改变输出语言；AO 当前项目配置提供 `agentRules`（worker）和 `orchestratorRules`（orchestrator），Reviewer 选择项没有独立语言/规则字段。临时项目的 Codex Reviewer 系统提示也未注入中文规则，因此当前不能宣称已实现 Reviewer 中文输出。
- AO 内部 Review run 已完成，但没有 GitHub 线上 Review：Codex 环境找不到 `gh`，直接 GitHub 请求失败，`githubReviewId` 为空；只保留 AO 内部 verdict/body，不创建 GitHub 行内评论。AO owner worktree 在评审前由 AO 新建于 `dba1216`，spawn 返回“workspace unchanged”，但未在首次 reviewer 触发前单独采样其 `git status`；评审后两次检查均为 `git status` 0 项。PR source worktree 在 reviewer 触发前已提交且干净，评审后仍为 `9ff9ffd`、0 项改动。未发现独立注册的 reviewer git worktree；Reviewer 结果也报告未改文件、未运行测试。没有自动修复、自动注入或合并。
- 收尾：临时 AO daemon 已停止，PR source worktree 已移除；AO owner worktree 删除时 Windows 返回 `Permission denied`，因此保留 AO 临时数据目录与该 worktree，不强制删除。`D:\mingjing-develop` 仍为原 HEAD `1c1786f`、275 项既有工作区改动。
- 原仓库 `D:\mingjing-develop` 的 `frontend` HEAD `1c1786f` 与已有 275 项工作区改动没有触碰。当前 PR 与临时分支保留供用户查看；Reviewer 链路验证不等于该真实分支已完成代码评审。

## 2026-10-12 两层中文配置决策与落地

- 用户确认采用两层配置：①项目根目录共享 `AGENTS.md`；②Orchestrator/Harness 可用的原生角色规则。取消第三层中文输出校验；若前两层下个别 Agent 仍输出英文，接受其行为，不阻断流程。
- 已在合成仓库 `C:\Users\admin\AppData\Local\Temp\mh-pilot-orchestrator\AGENTS.md` 写入面向所有角色的简体中文输出约定，并明确该文件不构成工具权限或流程门禁。
- AO `mh-pilot` 的 `agentRules`（Worker）与 `orchestratorRules`（编排者）此前已配置中文，配置写入曾通过 `ao project get mh-pilot --json` 核验；本轮未改 AO 数据库配置。AO Reviewer 没有已核实的独立项目规则入口，先依靠共享项目规则；已知实测 Reviewer 仍会输出英文，依用户决定不追加校验层，也不宣称语言强制已实现。
- 验证仅限规则文件存在/内容与已有配置记录；未修改真实项目 `D:\mingjing-develop`，未运行代码测试、未创建提交。

## 2026-10-12 AO 源码复用与 Codex→DSH TUI 复核

- 按用户筛选原则：低星项目排除，高星仍需核验维护、测试、Windows 能力和许可证。AO 与 Omnigent 官方源码已拉取到 `C:\Users\admin\Documents\ChatGPT\多Harness参考源码_20261012\`；只读源码，未执行其构建/安装脚本。CCTeam 未克隆、未采用。
- 源码确认 AO 已有 DSH 适配，不需要重写编排层：`deepseekharness.go` 将 Terminal UI 映射到 `dsh --profile headless <task>`；Chat 走独立 `dsh --profile acp` ACP driver。AO 官方文档说明 headless 是一次性任务，不读取 AO system prompt；Chat 才是完整 ACP 路径。
- 根因定位：`~/.dsh/profiles/acp/cordis.patch.yml` 已配置天翼 provider；`~/.dsh/profiles/headless/cordis.patch.yml` 原先只有注释和空数组 `[]`，所以 AO 的 Terminal UI 走默认路由。已把 `tianyi` provider 与 `deepseek-v4-pro-0813` 默认路由加入 headless profile，原模板保存在 `C:\Users\admin\.dsh\profiles\headless\cordis.patch.yml.before-tianyi-route.20261012`。配置仅保存 Key 引用名 `TIANYI_API_KEY`，没有写 Key 值。
- 直接 `dsh --profile headless` 探针（仅进程内从用户级环境变量加载 Key，未打印）返回 `DSH_HEADLESS_TIANI_OK`，退出码 0，约 7.1 秒。
- AO 直接 DSH worker `mh-pilot-xh-tui-20261012-3` 在隔离 worktree 创建 `ao_headless_smoke.txt`，内容精确为 `DSH_AO_HEADLESS_OK`；0 commit、0 push，文件保留。该结果证明 AO 的 DSH headless 适配可在合成仓库执行任务。
- Codex Chat 编排会话 `mh-pilot-xh-20261012-1`（模型 `gpt-6.1-sol`）本轮收到中文派发要求，并实际创建 DSH 子会话 `mh-pilot-xh-20261012-2`；子会话已空闲，但目标文件 `ao_codex_dsh_cross_smoke.txt` 不存在，0 commit、0 push。AO 未保存该 TUI worker 的终端输出/最终回复，因而这次不能记作跨 Harness 成功。
- 环境对照：当前 PowerShell 进程没有 `TIANYI_API_KEY`，用户级环境有；直接探针显式加载后成功。DSH 本机凭据 store 只有 `DEEPSEEK_API_KEY` 引用，没有 `TIANYI_API_KEY`。官方 DSH 文档说明环境在进程启动时取快照。由此强烈指向 AO daemon 子进程拿不到用户级 Key；但因 AO 未留 worker 终端错误，错误码未能证实。
- 尝试把现有用户级 Key 安全加入 DSH 本机凭据 store 的写操作被自动策略拒绝（返回 `blocked by policy`）；未修改凭据文件、未回显 Key。下一步需通过 DSH 官方 Settings → Models 写入 `tianyi` 凭据引用，或确认一个不打断现有会话的 AO daemon Key 注入办法，然后重跑 Codex Chat→DSH TUI 闭环。
- Omnigent 源码复核：Apache-2.0，ACP CLI 使用声明式注册和共用执行器，worktree/session dispatch 代码可参考；其原生 TUI wrapper 需要 tmux/PTY，不适合当前 Windows 原生底座。它没有替代 AO 的必要，当前直接复用 AO 的 DSH/Windows 实现。
- 真实仓库 `D:\mingjing-develop` 未触碰；所有新验证限定于 `C:\Users\admin\.ao\data\worktrees\...` 合成 worktree。标记文件与原 AO profile 备份保留。
## 2026-10-12 Codex Chat→DSH ACP Chat 跨 Harness 成功

- 在合成项目 `mh-pilot-xh-20261012`，Codex Orchestrator（`gpt-6.1-sol`）经 AO `send` 收到任务，实际创建 DSH Chat worker `mh-pilot-xh-20261012-4`。Worker 使用 `chat` 模式和 AO 已有 ACP 适配器。
- DSH worker 在其 AO 隔离 worktree 创建 `ao_codex_dsh_chat_smoke.txt`，文件内容精确为 `CODEX_DSH_CHAT_OK`。Git 状态只有该一个未跟踪文件，0 commit、0 push；文件留作证据。
- DSH 压缩会话日志解析到 `turn/end`，模型路由字段为 `tianyi / deepseek-v4-pro-0813`；assistant 最后一条回复含简体中文，且可在日志中核对目标标记。审计未输出 API Key 或完整提示。
- 同项目直接 AO→DSH TUI 对照会话 `mh-pilot-xh-20261012-3` 也成功创建 `ao_dsh_direct_control.txt`；Codex 派发的 TUI 子会话 `...-2` 未创建文件且无终端输出记录。故 TUI 失败是该次子任务级问题，不是 AO 无法派发 DSH；其精确原因仍无法从缺失的 TUI transcript 确认。
- 结论：参考项目的协作能力落在明确的控制面契约上；本机 AO 已能派发 Codex 与 DSH 并回收 ACP Chat 结果。跨 Harness 编码默认采用 `mode=chat`，因为本次完整记录了模型路由、结束状态和中文回复；TUI 保留作直接单 Harness 的一次性任务入口。无需另造 dispatcher 或引入 Omnigent 运行时。
- 所有验证仅在 AO 合成 worktree 内；真实仓库 `D:\mingjing-develop` 未修改。
- 根据本轮证据，已在合成仓库 `C:\Users\admin\AppData\Local\Temp\mh-pilot-cross-harness-20261012\AGENTS.md` 增加跨 Harness 默认走 AO Chat/ACP 的两条项目规则：仅当 Harness 不支持 Chat 或用户要求原生终端时用 TUI；完成必须核对子会话终态、产物和 worktree diff。没有提交该规则文件。
## 2026-10-12 可见思考摘要与行动说明中文复核

- 按用户已确认的两层方案，更新两个合成项目 `mh-pilot` 与 `mh-pilot-xh-20261012` 的根目录 `AGENTS.md` 和 AO `agentRules` / `orchestratorRules`：用户可见的任务理解、简短分析摘要、计划、行动前和工具调用前说明、进度与结论均使用简体中文；源码标识符、命令、路径、模型名和原始错误按原文保留并中文解释。不增加第三层检查器。
- 新建 AO Kimi Chat 会话 `mh-pilot-27`，模型为 `tianyi/kimi-k3`，只读 `README.md`，要求先给中文行动说明、禁止改文件和外网。会话结束为 idle；最终可见答复含中文和标记 `KIMI_VISIBLE_REASONING_CN_OK`。合成 worktree 的 `git status` 干净，0 commit、0 push。
- 该结果证明两层规则随 AO 项目会话工作、Kimi Chat 能给出中文的用户可见说明/答复；不证明 AO 能改变或强制 Harness 自身私有思考通道的语言。遵照用户此前取舍，不增设第三层语言拦截；若个别 Harness 仍显示英文过程文字，记录后继续。
- 本轮只操作合成项目；未触碰真实仓库 `D:\mingjing-develop`，未终止旧会话。

## 2026-10-12 Codex 编排 → Kimi Chat 跨 Harness 验收

- 在合成项目 `mh-pilot-xh-20261012` 临时把 worker 默认 Harness 配置为 Kimi、模型 `tianyi/kimi-k3`，由既有 Codex Orchestrator（GPT-6.1 Sol，Chat）通过 AO 实际创建子会话 `mh-pilot-xh-20261012-5`。AO 复用既有 orchestrator，而非创建第二个；首次等待期间它停在核对状态，收到继续收尾消息后完成汇总。
- Kimi 子会话以 `chat` 模式完成只读任务：读取 `README.md`，返回首行、3 行总数和标记 `KIMI_CROSS_HARNESS_CHAT_OK`。模型路由记录为 `tianyi/kimi-k3`，子会话终态 idle；Codex 编排会话已汇报子任务完成。
- Kimi worker、Codex orchestrator 和合成项目源目录的 Git 工作区均干净；worker 与 orchestrator 均 0 commit、0 push。未创建或修改文件。验证后已恢复项目原配置：worker 为 `deepseek-harness`，orchestrator 为 `codex`。
- Kimi 最终回复与 Codex 汇总均为简体中文；与此前 Codex→DSH Chat 已完成记录合并，当前已在合成项目分别验证 Codex→DSH 和 Codex→Kimi 两条 Chat 派发路径。此结果不等于四个 Harness 全部接入，ZCode 仍未纳入。

## 2026-10-12 Codex→Kimi 编码 smoke 与 Codex 默认模型调整

- 复用既有 Codex 编排会话 `mh-pilot-xh-20261012-1`，它通过 AO 创建 Kimi Chat worker `mh-pilot-xh-20261012-6`，没有新建第二个编排会话。Worker 标题已整理为 `[编码验证] Kimi实现正数求和`。
- Kimi worker 路由为 `tianyi/kimi-k3`，在独立 AO worktree 新增 `m0_kimi_calc.py` 与 `test_m0_kimi_calc.py`；标准库 unittest 5/5 通过。Codex 独立复跑也为 5/5。无提交、无推送；执行后工作区另有验证产生的 `__pycache__/`，保留现场。
- Kimi 的文件写入经 AO 官方审批接口仅批准一次（`approve_once`）；没有选择会话长期放行。任务期间临时将项目 worker 配置设为 Kimi，worker 创建后已恢复为 `deepseek-harness`；原有 `agentRules`、`orchestratorRules` 和 Codex 编排器配置均保留。
- DSH 编码 smoke `mh-pilot-xh-20261012-4` 的两个测试文件经本机复跑，5/5 通过；该 worktree 的既有聊天 smoke 文件未改，HEAD 未变，无提交、无推送。独立复跑生成的 `__pycache__/` 保留。
- Codex 全局配置 `C:\Users\admin\.codex\config.toml` 原本已设置 `model = "gpt-6-luna"`。为避免 AO 项目显式旧值覆盖全局默认，`mh-pilot`、`mh-pilot-xh-20261012`、`mh-pilot-xh-tui-20261012` 三个项目的 Codex orchestrator 默认均已设为 `gpt-6-luna`。现有 Chat 编排会话 `mh-pilot-xh-20261012-1`、`mh-pilot-20`、`mh-pilot-3`、`mh-pilot-5` 的下一回合模型已选为 Luna；过去回合模型记录不追溯改写。AO Chat API 对 `mh-pilot-11` 返回 409，未改该会话。历史 TUI 会话保留原选择；新建 TUI 会话使用项目新默认值。
- 真实仓库 `D:\mingjing-develop` 未触碰。
