# 多 Harness 参考项目调研

调研日期：2026-10-12。Stars 数为本次访问 GitHub 页面时显示值，会变化；Stars 只作关注度线索，不等同于安全性、稳定性或生产验证。

## 结论

在公开项目中仍没有证据表明某个成熟项目原生支持并验证 Codex、DeepSeek Harness、Kimi Code、ZCode 四者的完整协作。最新决策采用 Windows 版 Orchestrator.inc 作为唯一首期协调底座：它有 Windows 桌面发行版、本地 daemon、项目 orchestrator、独立 worker/worktree 和多种内置 Harness adapters；本机已完成 Codex→Claude Code 委派。此前 WSL2 OpenCode 合成任务只保留为历史证据，不再继续 Linux/桥接验证。

优先复用 AO Windows 的会话、角色、worktree、Harness 启动、review 与状态观测，不再部署第二个调度平台。官方 adapter 能力表列出 Codex、Kimi、DeepSeek Harness，但不同 harness 的 Chat/worker/reviewer 能力各异，必须逐项实测。ACP 作为 DSH/Kimi 的接入协议直接复用，不自研协议客户端。CCTeam 与 Omnigent只作设计参考；CCTeam 的 MCP 派发与 fail-closed pre-agent 策略 hook 有价值，但其公开文档未说明 per-worker worktree 隔离，且 Windows 使用路径要求 WSL。ZCode 未出现在 AO 当前 adapter 能力表，需要验证 Windows CLI/headless 适配；MCP 工具权限不能自动限制 Harness 原生文件和终端工具。

## 项目对比

| 项目 | 本次 Stars | 已证实的做法 | 对本项目的价值 | 缺口 |
| --- | ---: | --- | --- | --- |
| [Orchestrator.inc](https://github.com/OrchestratorInc/agent-orchestrator) | 13.1k | Windows 桌面发行版与本地 daemon；项目 orchestrator 拆任务并派发；Git worker 各有独立 branch/worktree；官方 adapter 表含 Codex、Kimi、DeepSeek Harness 等。Windows 本机已完成 Codex→Claude 子任务；WSL OpenCode 是历史试验。 | 直接作为首期 Windows 协调、worker/session 生命周期和隔离工作区；避免再造第二个调度器。 | 目标四 Harness 尚未全验收；ZCode 不在 adapter 表；Chat MCP 按 Harness/Provider 支持，AO 不是统一原生工具权限代理。需要按角色做工具、认证和取消测试。 |
| [CCTeam](https://github.com/firstintent/ccteam) | 低星项目（此前核验约 648 stars） | 有跨 Harness MCP 派发与 Flow 编排实现。 | **排除可信基座与代码复用**：按用户原则，低星项目不作为可靠来源；本轮未克隆、未运行、未采用。 | 仅留作非可信样本，不参与架构依赖或实现。 |
| [Omnigent](https://github.com/omnigent-ai/omnigent) / [Polly](https://omnigent.ai/docs/use/builtin-agents/polly) | 10.7k | Meta-harness 启动不同 Harness 的 worker；worker 各有独立 Git worktree；不同厂商可交叉评审；worker 可各自创建 PR，Polly 不合并。Codex 全支持，Kimi 为社区支持；另有自定义 ACP Agent。 | 最接近目标的运行层和委派方式；支持 Codex、Kimi、OpenCode，便于继续验证跨 Harness 会话、隔离和评审。 | Polly 内置 roster 没有 DSH、ZCode。DSH 需验证 ACP 接入；ZCode 的 app-server 是自有协议，不是 ACP，需要协议桥或受限 headless 适配。Polly 文档中的无头 worker 采用 bypass 配置；不得照抄，必须先验证外层权限、OS 沙箱与审计。 |
| [AWS CLI Agent Orchestrator (CAO)](https://github.com/awslabs/cli-agent-orchestrator) / [多 Provider 文档](https://github.com/awslabs/cli-agent-orchestrator/blob/main/docusaurus/docs/features/multi-provider.md) | 1.4k | 本地 cao-server 管理真实 CLI 与隔离 tmux session；supervisor 可以按 worker profile 指定其他 provider；有 provider 专用状态检测、MCP 和 Web UI。官方列表包括 Codex、Kimi CLI、OpenCode。 | 可参考显式 provider/role 配置、worker 生命周期、进程状态监控与跨 Provider 派发。Apache-2.0。 | 官方支持列表未列 DSH、ZCode；需要继续开发 provider 适配。与本项目已实现的 SQLite 状态服务、MCP 网关功能重叠，首期整体替换会扩大迁移面。 |
| [Vibe Kanban](https://github.com/BloopAI/vibe-kanban) | 28.3k | 以任务卡和 agent attempt 管理工作；为任务提供隔离工作区/worktree；文档列出 Codex、OpenCode 等多个 CLI。 | 借鉴任务隔离、尝试记录和人工查看差异的交互方式。 | 文档呈现的是按任务启动不同 Coding Agent；没有证明其会让 Codex、Kimi、DSH、ZCode 在同一任务内互相派发或交叉评审。支持列表也未覆盖目标四者。 |
| [Morning Star / mstar-harness](https://github.com/btspoony/mstar-harness) | 63 | 一套流程插件/角色技能可安装在 Codex、DSH、Kimi Code、ZCode、OpenCode 等不同 Host；把产物、计划、评审记录保存在仓库。 | 目标 Harness 名称覆盖最全，可研究角色职责与跨 Host 的工作流文件结构。 | Stars 较少，不能按高关注度项目作为底座。已验证的信息证明的是“工作流在多个 Host 可安装”，没有证明一个 Host 能在运行时启动其余 Host 并完成跨 Harness 调度。只作设计阅读，不直接依赖。 |
| [dsh-alpha](https://github.com/songofhawk/dsh-alpha) | 3 | DSH 主控通过 Worker/网关派发到 Codex、Kimi ACP、DSH、ZCode headless 等运行时；可登记逻辑仓库、返回任务事件、审批和取消。 | 与四 Harness 目标覆盖最接近，可研究跨机器 worker、workspace 目录映射、ZCode 适配和持久任务 ID。 | 只有 3 stars，按实验性项目处理。其 DSH headless worker 目前仅回传最终文本，不支持逐轮模型覆盖或转发 DSH 内部审批；不可直接作为可靠首期底座。 |
| [DSH Kernel Mesh](https://github.com/oppnc/dsh-kernel-mesh) | 0 | 把 Kimi、Codex 等封装为 DSH 插件/路由，让 DSH 内部调用。 | 可了解“把其他 Harness 封装成 DSH 插件”的实现方向。 | 关注度为 0，且未覆盖 ZCode；这是 DSH 内部扩展，不是 Harness 中立的总控层，不作为 MVP 依赖。 |

## 关键实现模式

1. **Harness 适配层与上下文交接**：由协调层创建目标 Harness 的独立会话，把任务卡、验收条件、必要文件路径/产物引用和权限范围传给 worker；worker 仍使用自己的原生运行时。不要把主会话全部历史拼成超长 prompt。项目 `AGENTS.md`、角色说明和代码由 Harness 按自身机制读取；结果通过结构化交付物、diff 和测试证据回传。
2. **任务隔离**：写入任务使用独立 worktree；reviewer 读取固定 diff 与测试证据。集成后必须对合并结果重新验证。
3. **交叉评审**：实现者与 reviewer 使用不同 Harness/供应商时提高独立性，但“不同 Harness”不必然等于“不同模型或供应商”；需记录真实认证和返回模型标识。
4. **治理与发布分开**：自动创建 Draft PR 可作受授权的最后一步；合并、部署不由 Agent 决定。工具 bypass 不能取代外部状态门禁、权限策略和 OS 沙箱。
5. **流程按任务复杂度选用**：Vibe Kanban 与 Polly 都未证明所有任务必须机械执行固定六阶段。保留 spec → plan → build → test → review → ship 作为复杂任务的检查项；简单调查或小改动使用精简路径，并记录省略项理由。

6. **共享工作区不等于安全并行**：CCTeam 的 project 是注册的代码目录，可有多个独立会话；其 Flow 文档明确说明并行编辑共享同一个 project working tree，没有 per-hire 隔离。若两个写者共享同一目录，文件写入和 Git 状态会互相影响。MVP 写任务仍应每个任务一个 worktree；只读评审可读取固定基线与 diff。

## 对 MVP 的具体调整

- 首期执行底座改为 Windows Orchestrator；停止后续 WSL/Linux 运行，不做跨 OS worktree 映射。历史 WSL 源树和数据保留，不重置或清理。
- 首先复用 AO 内置项目 orchestrator、worker/reviewer、worktree、角色/项目规则和已支持 Harness adapter。仅当合成项目证明 AO 无法提供所需硬门禁时，才考虑 Windows 原生 state gate/MCP gateway；旧 Linux 代码本轮不启动、不移植。
- 用户目标仍是 Codex 规划/只读评审、DSH 编码、Kimi 只读调查/复审、ZCode 前端实现。逐个 Harness 完成真实 CLI、认证、模型映射、工具权限和取消验证后才加入角色池。
- Windows 已测 DSH ACP 请求仍报鉴权失败；Kimi CLI/ACP 与 AO 接入待验证；ZCode 的 app-server 是自有协议，不应假设它等于 ACP。认证/模型验证全部在 Windows 主机的合成项目完成。
- 下一步先核验 AO 对 Codex、DSH、Kimi 的每 Harness 权限和 MCP roster，再验证 ZCode CLI 的受限 headless 路径；不修改全局凭据或真实项目。
- 只有 M0 权限与恢复门禁通过后，才在真实项目独立副本跑任务；失败保留 worktree，人工确认处置，不做自动回滚或自动发布。

## 2026-10-12 Windows AO 复用审查（最新，覆盖下文 WSL 试验计划）

| 方案/项目 | 可复用部分 | 结论 |
| --- | --- | --- |
| AO Windows（13.1k stars） | 原生 Windows daemon、Harness adapters、项目 orchestrator、worker/reviewer、独立 worktree、会话与结果看板 | 作为唯一 MVP 编排底座；已覆盖大部分重复造轮子的工作。AO 官方能力表显示 Codex 原生 app-server、DSH/Kimi ACP；ZCode 未列出。 |
| ACP 官方协议 | Harness 初始化、session、prompt、工具事件、权限请求、取消等通用协议边界 | 复用 AO 内置 ACP client/adapter。Kimi 官方 `kimi acp` 支持 session/new、cancel、permission request 和 MCP server 转发；DSH 也有 ACP adapter。不要另写 ACP runner。 |
| CCTeam（648 stars） | MCP dispatch、每次派发前的 fail-closed 策略 hook、会话创建/结果回收、Flow 任务账本模式 | 只参考派发、拒绝策略和回传设计；公开文档未说明 per-worker worktree 隔离，Windows 使用路径要求 WSL，不采用为首期执行层。 |
| Omnigent/Polly（10.7k stars） | 跨 Harness worker、独立 worktree、交叉评审 | 能力与 AO 大幅重叠，且当前首期由用户选定 Windows AO；不再安装第二个平台。 |
| Vibe Kanban（28.3k stars） | 任务卡、隔离 workspace、人工查看差异 | 可参考看板/任务可见性；公开资料未证明同一任务内跨 Harness 派发，AO 已具备对应工作区基础。 |
| Harnss（384 stars） | 多 engine：Claude SDK、Codex app-server、通用 ACP；MCP 按项目配置 | 只作结构参考；仓库明确标记早期开发并计划大改写，不部署到首期。 |

执行边界：AO Chat 的 MCP 工具依赖 provider 支持；MCP 允许列表只约束经过 MCP 的工具，不等于约束 Harness 自身文件、终端和 Git 工具。每个角色需要独立核验原生权限与 Windows 沙箱。若 AO 无法隔离评审只读/编码写入，再评估 Windows 本地策略网关；不启动 Linux 服务，也不把 WSL 当成权限边界。

## 2026-10-12 Orchestrator WSL 试验

- 官方 Orchestrator Linux v0.13.7 deb 下载并校验 SHA-256 后解压到用户目录；未安装系统包。CLI 版本字段显示 dev，daemon 状态 ready。
- WSL OpenCode 1.19.0 在 Orchestrator agent catalog 中为 authorized。合成 Git 仓库 mh-pilot-wsl 注册后，worker mh-pilot-wsl-1 在分支 ao/mh-pilot-wsl-1/root 创建唯一文件 opencode-result.txt，内容符合任务要求。session idle，未 commit/push；源仓库保持干净。
- WSL 当前仅 OpenCode 通过 Harness 认证探测；Codex/DSH 解析为 Windows npm shim，Kimi/ZCode 未在 PATH 中发现。Linux AO doctor 对这些 Harness 的警告已记入 M0 状态。
- 本次未触碰真实原型项目；不据此宣称权限网关、状态机或四 Harness 已验收。

## 本机 M0 预检结果（Orchestrator 试验前记录）

- Omnigent 0.17.0 的 CLI 列出 Codex、Kimi、Polly 等入口；没有 DSH 内置 Harness。
- DSH CLI 的 `--profile acp --help` 成功，确认它提供 ACP stdio profile；Kimi CLI 的 `kimi acp --help` 成功，确认其也提供 ACP server。两者尚未完成 ACP 握手、真实模型请求或权限拒绝验证。
- Codex CLI `login status` 返回 `Not logged in`。Windows GUI 的登录态不会自动证明 WSL CLI 已认证；Codex 的真实模型连通性待用户登录 WSL CLI 后验证。
- ZCode 0.16.9 CLI 提供 `--prompt`、`--json`、`--disallowed-tools`、`--mode guarded` 等 headless 入口；`app-server` 明确是 ZCode Protocol，不是 ACP。尚未验证 model login、文件修改隔离、取消和结构化事件回传。
- 此预检记录的是 Orchestrator WSL 试验前状态；后续 OpenCode worker 已完成一次合成仓库隔离任务，目标四 Harness 仍未闭环。

DeepSeek Harness 官方仓库本身关注度很高，但其 [README](https://github.com/deepseek-ai/deepseek-harness) 明确标记为 developer preview，并提示存在兼容性破坏变更；因此不能把高 Stars 等同于 API、插件和配置稳定，MVP 应固定版本并保留适配测试。

## 官方/一手资料

- Orchestrator.inc GitHub：https://github.com/OrchestratorInc/agent-orchestrator
- Orchestrator Linux 安装：https://docs.orchestrator.inc/installation/
- Orchestrator OpenCode ACP 适配：https://docs.orchestrator.inc/plugins/agents/opencode/
- Orchestrator per-role agent 派发：https://docs.orchestrator.inc/guides/per-role-agents/
- Orchestrator Harness 适配矩阵：https://docs.orchestrator.inc/plugins/agents/
- Omnigent GitHub 与 Stars：https://github.com/omnigent-ai/omnigent
- Omnigent Polly 工作方式、worktree、交叉评审与审批配置：https://omnigent.ai/docs/use/builtin-agents/polly
- Omnigent Harness 支持矩阵：https://omnigent.ai/docs/build/harnesses/supported
- Omnigent 社区插件与 ACP 边界：https://omnigent.ai/docs/build/harnesses/community
- Omnigent 自定义 ACP 配置与执行方式：https://omnigent.ai/docs/build/harnesses/configuration
- AWS CAO GitHub：https://github.com/awslabs/cli-agent-orchestrator
- AWS CAO 多 Provider 文档：https://github.com/awslabs/cli-agent-orchestrator/blob/main/docusaurus/docs/features/multi-provider.md
- Vibe Kanban GitHub：https://github.com/BloopAI/vibe-kanban
- Vibe Kanban Agent 支持文档：https://github.com/BloopAI/vibe-kanban/blob/main/docs/supported-coding-agents.mdx
- Morning Star：https://github.com/btspoony/mstar-harness
- CCTeam：https://github.com/firstintent/ccteam
- dsh-alpha：https://github.com/songofhawk/dsh-alpha
- DSH Kernel Mesh：https://github.com/oppnc/dsh-kernel-mesh
- DeepSeek Harness 官方仓库与 Developer Preview 声明：https://github.com/deepseek-ai/deepseek-harness
- Orchestrator Windows 桌面、worktree 与 Harness 支持：https://github.com/OrchestratorInc/agent-orchestrator
- Orchestrator 当前 Harness/Chat 适配矩阵：https://docs.orchestrator.inc/plugins/agents/
- Agent Client Protocol 官方仓库：https://github.com/agentclientprotocol/agent-client-protocol
- Kimi Code ACP 及 MCP 转发能力：https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-acp
- Harnss（多 engine/ACP/MCP；早期开发状态）：https://github.com/OpenSource03/harnss
- Kimi Code 官方仓库（ACP、第三方兼容 Provider 与 Hooks）：https://github.com/MoonshotAI/kimi-code
- ZCode 官方仓库：https://github.com/zai-org/ZCode

## 2026-10-12 评审独立性与中文配置补充

### 参考项目的评审隔离做法

- **Orchestrator**：reviewer 是独立于 worker 的 reviewer run，检查 worker PR 当前 head，不拥有 worker 分支；worker 权限不会自动传给 reviewer。项目文档描述的是角色/会话与 PR 目标分离，没有要求必须有通用 `read-only` 权限，也没有把 reviewer 提示词说成工具沙箱。MVP 应复用这个边界：评审固定 PR head、单独 reviewer 会话、只提交结构化发现；如果 reviewer 对工作区产生改动，应判定该轮评审受影响并人工处理。
- **OpenAI Codex Cookbook**：评审阶段只把 artifact 内容送入模型并要求结构化 findings；修复阶段针对副本写入。它把“发现问题”和“改动文件”分成两个阶段。示例提示要求评审不执行、不编辑，但不能据此推断提示本身是强制权限边界。
- **OpenAI Agents sandbox 文档**：建议将 harness 控制面与执行沙箱分离，只挂载任务需要的输入，并在把产物移出沙箱前检查。适合作为后续加强隔离的设计参考，不代表 AO 默认 reviewer 已具备此类 OS 级限制。

### 本机中文规则配置

- 已通过 AO 官方 `project set-config` 为合成项目 `mh-pilot` 写入简体中文 `agentRules` 与 `orchestratorRules`；保留原 worker、orchestrator、模型、权限及环境项。该设置用于新建会话。
- AO 文档目前只说明 `agentRules` 用于 worker、`orchestratorRules` 用于监督会话；未发现单独的 `reviewerRules` 项。因此不能保证 reviewer 默认 prompt 或 AO 固定界面文案也会变成中文。历史消息不会被改写；模型名、命令、路径与应用自带菜单仍可能显示英文。
- 后续应在合成项目真实触发一次 reviewer，核实 reviewer 的实际语言、PR head 固定性及评审前后工作区变化，再决定是否需要项目内中文评审规则文件或薄的评审提示适配。

### 一手资料

- AO 项目与 reviewer 配置：https://docs.orchestrator.inc/configuration/projects/
- AO reviewer loop：https://docs.orchestrator.inc/guides/review-loop/
- OpenAI Codex 评审/修复分离示例：https://developers.openai.com/cookbook/examples/codex/build_iterative_repair_loops_with_codex
- OpenAI sandbox 与控制面/执行面边界：https://developers.openai.com/api/docs/guides/agents/sandboxes

## 2026-10-12 按用户低星排除原则复核与源码复用

- 筛选规则：低星项目直接排除；高星只说明值得检查，仍核对维护状态、发布/提交、CI/测试、Windows 支持、许可证和代码是否真正覆盖目标能力。
- 已拉取 AO 与 Omnigent 官方源码到 `C:\Users\admin\Documents\ChatGPT\多Harness参考源码_20261012\`。只读源码，没有运行其安装、构建或启动脚本。CCTeam 未拉取或使用。
- **AO 直接作为运行底座复用**：本机已安装 AO，源码含 DSH 专用适配器 `backend/internal/adapters/agent/deepseekharness/deepseekharness.go`、ACP Chat driver 与 Windows worktree/runtime 适配。适配器明确分开 `dsh --profile acp` Chat 与 `dsh --profile headless` Terminal UI；无需新写跨 Harness dispatcher。
- **Omnigent 只取可移植设计/局部代码参考**：其 Apache-2.0 源码将 ACP CLI 声明集中在 `omnigent/acp_cli_harnesses.py`，共用 ACP 执行器；`omnigent/host/git_worktree.py` 管理隔离 worktree；`omnigent/tools/builtins/spawn.py` 实现子会话创建与查询。它使用 Python runtime，原生 TUI wrapper 依赖 Windows 不提供的 tmux/PTY；与 AO 功能重叠且不能直接移植成 AO Go 适配器，因此本期不另起 Omnigent 运行时。
- 本地验证发现 AO→DSH TUI 首次失败的配置差异：ACP profile 已配置 Tianyi，而 AO Terminal UI 固定读取 `headless` profile，原 headless patch 为空。已在该 profile 配置同一 `tianyi` route 与 `deepseek-v4-pro-0813` 默认模型，并保存原模板副本。
- 直接 DSH headless 冒烟成功；AO 直接 worker 也曾在隔离 worktree 创建标记文件。Codex Chat orchestrator 这轮确实派发了 DSH TUI 子会话，但其 worktree 未出现目标文件，AO 没有保存可核查的终端输出。当前用户级 Key 在 AO daemon 子进程中的可见性仍未闭环，不能把跨 Harness 端到端验证记为通过。
- DSH 官方凭据文档说明进程环境在启动时取快照，且本机 DSH 凭据 store 尚无 `TIANYI_API_KEY` 引用；脚本向凭据 store 写入该 Key 的操作被运行策略拒绝，未改变凭据文件、未输出 Key。继续验证前需通过 DSH 官方模型设置保存该引用，或在不打断现有会话的前提下解决 AO daemon 环境继承。
### 跨 Harness 实际代码路径核验

- Codex Chat 编排者实际创建 DSH Chat worker；AO worktree 中出现唯一目标文件，AO/DSH 记录显示任务结束，最终回复有中文。直接 DSH TUI 控制也成功，但 Codex 派发的 TUI 子任务失败且没有回执。当前可复用方案明确为 AO orchestrator + AO DSH ACP Chat adapter；不需要自研跨 Harness dispatcher。
- 这个验证说明，判断“项目能不能协同”要看控制面是否能创建子会话、隔离工作区、传入任务、回收状态与结果；框架星数本身不能替代端到端核验。
- 已把本轮经验写入合成项目 `AGENTS.md`：跨 Harness 默认 AO Chat/ACP；只有 Harness 不支持 Chat 或用户要求原生终端时使用 TUI；必须核对子会话终态、产物与 Git diff 后再报告完成。该规则位于用户选定的项目级第一层，不加中文输出校验等第三层。
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
