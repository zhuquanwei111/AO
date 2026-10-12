# 多智能体协同开发系统搭建方案

Orchestrator Windows 主环境试点版 3.2　｜　2026 年 10 月 12 日

## 决策摘要

**首期只使用 Windows 版 Orchestrator；停止后续 WSL/Linux 执行与桥接验证。** AO 的 Windows 版本已原生管理本地 daemon、Harness 会话、角色委派和 Git worktree；MVP 不再并行维护第二套 Linux AO。此前 WSL OpenCode 任务只保留为历史试验。Windows AO v0.13.7 已验证 Codex→Claude 委派；DSH Chat 的 ACP 请求仍鉴权失败。已有外部 SQLite 状态机与 MCP 权限网关代码不在本轮运行或修改；若这些强制门禁仍需保留，后续只评估 Windows 原生实现，不跨 WSL 桥接。本轮未触碰真实原型项目。

这些试验只证明部分会话启动、跨 Harness 派发和 worktree 隔离能力，**没有证明目标四 Harness、权限网关或状态机已经端到端接通**。Claude 和 OpenCode 是验证用 Harness，不替代用户指定的 Codex、DeepSeek Harness、Kimi Code、ZCode。AO 目录将 DeepSeek 标记为“已安装/已授权”，但实际 Windows ACP 请求返回 API Key 无效；该目录状态不能替代真实请求验证。Kimi 尚未安装到 Windows AO，ZCode 当前没有 AO 内置适配器。

| 决策项 | MVP 决定 |
| --- | --- |
| 执行底座 | 仅 Windows Orchestrator。停止新建 WSL AO、Linux worker 和跨 OS worktree 映射；历史 WSL 环境保留，不清理、不重置。 |
| 任务流程 | AO 原生 orchestrator 负责拆解、派发、跟踪与回收；复杂任务可采用 spec/plan/build/test/review/ship 检查清单，不强制每个任务走固定六次审批。 |
| 强制门禁 | 先复用 AO 项目规则、角色权限、MCP 能力和人工审批。若必须做到外部状态机硬拦截阶段跳转，M0 再验证 Windows 本地 gate 能否拦截所有派发入口；验证前不声称已具备强制门禁。 |
| 派发 | 优先由 AO 原生 orchestrator 创建隔离 worker；不再另装 CCTeam/Omnigent 作为第二个任务平台。若外部 gate 被确认必需，AO 派发要先经过 Windows 侧 gate，否则只开放人工派发。 |
| 目标 Harness | Codex 规划/只读评审，DeepSeek Harness 编码，Kimi Code 只读分析/复审，ZCode 前端实现；逐一完成 CLI、认证和工具权限验收后才纳入角色池。 |
| 模型 | GPT-6.1 Sol 使用官方 ChatGPT 订阅登录；DeepSeek 请求模型 ID 固定为用户已选的 V4 Pro API ID，接受供应商路由并记录返回标识；不自动切换模型。Kimi、ZCode 模型 ID 在 M0 登记。 |
| 成本 | 不作为 MVP 目标；质量、可控性和完成率优先。保留 token 用量供诊断，不设降本比例。 |
| 发布 | MVP 由人确认交付；不自动 push、合并或部署。 |

## 架构与任务流程

用户提出任务 → Windows AO orchestrator 规划与派发 → 按任务选择 Codex、DSH、Kimi 或其他已验证 Harness → AO 为 Git 任务建立独立 worktree → 使用 Harness 原生权限及已验证的角色 MCP → 测试/交叉评审 → 人工确认交付。复杂任务可用阶段清单；小任务可按风险裁剪。

spec → plan → build → test → review → ship 作为可裁剪的质量检查清单，不是固定工作流。任务记录目标、验收证据、参与 Harness 和人工决策即可。是否保留独立状态机硬门禁，须由 M0 证明 AO 原生角色/权限/人工审批无法满足之后再实现；不能把提示词约定写成安全边界。

Orchestrator 的 Windows daemon、项目 orchestrator、worker/reviewer、Harness 适配、Git worktree 和会话 UI 是首期直接复用的协调层。AO 提供的 Chat MCP 能力取决于 Harness/Provider；MCP 网关不是 AO 的统一全局权限代理。每个 Harness 必须分别核验可见工具、原生文件/终端权限、取消和审计。AO daemon 保持本机默认访问边界，不另开公网入口。

## Harness 与模型职责

| Harness / 角色 | 职责 | MVP 状态与限制 |
| --- | --- | --- |
| Codex + GPT-6.1 Sol | 需求分析、架构、计划、只读终审 | Windows Codex CLI 已完成历史隔离任务试跑；当前 AO doctor 报告 Codex 启动参数兼容警告，需按目标模型重新验证。WSL Codex 登录不再属于首期范围。 |
| DeepSeek Harness | 有明确验收条件的编码任务 | Windows 已安装 DSH 0.1.2-alpha.5；AO ACP worker 两次请求均返回鉴权失败，没有执行写入。需修正 DSH 自身凭据后再测。请求模型 ID 固定，接受供应商公共 API 路由并记录返回标识；不推断底层权重。 |
| Kimi Code | 只读代码调查、计划挑刺和独立复审 | Windows AO 目录显示未安装；CLI、供应商配置、适配器握手和只读权限待验证。 |
| ZCode | 前端与交互实现 | Windows AO 当前无内置 ZCode 适配器；需验证官方 CLI/app-server 能否安全桥接，不能直接假设可按 ACP 接入。 |
| OpenCode | 不纳入首期 Harness 角色 | WSL 中历史完成的一次隔离任务仅作为参考，不再继续 Linux 验证。 |

Harness 名称不等于模型身份。API 调用固定请求模型 ID；实际返回标识单独记录。供应商改路由时继续当前响应，不自动换模型。角色分工只是初始假设，须用相同任务集比较质量后才能扩权。

## 复用、自研与后续扩展

| 分类 | 组件 | 落地边界 |
| --- | --- | --- |
| 复用开源 | Windows Orchestrator、内置 Harness adapters、Git worktree、ACP、Playwright | AO 承担协调、任务 worker、review、会话生命周期和 worktree；DSH/Kimi 使用 AO 已提供的 ACP adapters；不自研 Harness 循环。 |
| 参考但不部署 | CCTeam、Omnigent/Polly、Vibe Kanban、Harnss | 参考跨 Harness 会话/结果回收、任务看板、ACP/MCP 配置和派发前策略 hook。它们与 AO 的协调/隔离能力重叠；CCTeam 公开文档未说明 per-worker worktree 隔离，且 Windows 使用路径要求 WSL，Harnss 公开标注早期开发且约 384 stars，不作为生产底座。 |
| 保留的现有代码 | WSL 工作树中的 SQLite controller、MCP gateway、模型代理原型 | 本轮不启动、不读写、不移植、不删除。后续如需复用，先在隔离副本盘点可移植部分；不做 Windows↔WSL 桥接。 |
| 需自研/补齐 | ZCode 适配；必要时 Windows 原生状态门禁/MCP 代理；各 Harness 角色权限与审计验证 | 只补 AO 和 Harness 原生能力实际缺口。若 native tools 可绕过 MCP 权限，不给该角色写权限；不把自研状态机预先定为首期阻断项。 |
| 二期扩展 | CAO 对照、更多 Harness、自动角色选择、自动 Draft PR、并行多写者 | 只有 M0/M1 权限和恢复验收通过后再评估；当前不启用自动发布或自动修复。 |

Orchestrator.inc 官方 Windows 发行版与本机实测最贴合当前目标；GitHub 仓库约 13.1k stars。其内置 adapter 矩阵包含 Codex、Kimi、DeepSeek Harness 等，但 Worker/Chat/Review 支持各异，按官方能力矩阵逐项验收。CCTeam（648 stars）证明 MCP 派发和策略 hook 可以驱动多 Harness；其公开文档未说明 per-worker worktree 隔离，且 Windows 使用路径要求 WSL，因此只借鉴派发/拒绝策略，不在首期部署。Omnigent（约 10.7k stars）与 AO 的多 Harness/worktree 能力重叠；Vibe Kanban（约 28.3k stars）偏任务/worktree 管理，未证明同一任务内跨 Harness 相互派发；Harnss（384 stars）虽有 ACP + per-project MCP 结构，但仓库明示早期开发/大改写，暂不部署。调研细节见《多Harness参考项目调研》。

## 权限、凭据与失败规则

- 规划和评审角色只读；编码角色只能写任务批准路径，禁止 git push、数据库写和读取其他任务凭据。
- 每个 Harness/角色单独配置 API Key 或登录身份，不把密钥写入任务卡、提示或审计。首期按用户决定允许代码发往第三方云 API；审计仍不保存代码原文、完整提示或密钥。
- MCP 网关负责其代理的文件读写、终端、Git、Playwright 工具。Harness 原生工具还须通过 Harness 权限配置与 Windows 进程/文件系统隔离；未证明能限制任务外读写的 Harness 不开放写权限。
- 每次 API 调用记录请求模型 ID、供应商返回模型标识、token 用量、耗时、状态码、角色、Harness 版本、task_id 和错误类别；拿不到的字段标记为不可得。
- 超时、限流和服务错误统一分类；单阶段最多额外重试 2 次。鉴权失败不重试；耗尽预算即失败阻断，不自动切模型、不自动修代码。
- 每个写任务用独立 worktree。失败保留现场；删除全量 checkout/reset/clean 回滚方案。清理或回滚仅由人工确认范围后执行。
- 浏览器使用任务独立临时用户目录；验收检查最终 URL、关键 DOM、控制台/页面错误、网络失败和 HTTP 错误，截图只作补充证据。about:blank、意外重定向或残留 Profile 锁均阻断。
- 工具调用全量留审计事件；不得把提示全文、密钥、源代码原文写入审计日志。AO daemon 固定回环监听，禁止直接暴露远程端口。

## M0 与后续里程碑

### 已完成的底座试验

| 项目 | 结果 |
| --- | --- |
| Windows Orchestrator | v0.13.7 daemon ready；Codex 与 Claude Code 已完成隔离单文件试跑及 Codex→Claude 委派。最新 doctor 报 Codex 启动参数警告，需复验。 |
| Windows DSH worker | AO agent catalog 显示 installed/authorized；合成仓库 worker 的两次 ACP turn 均返回鉴权失败，worktree 无任务文件，未 commit/push。真实模型调用未通过。 |
| WSL2 OpenCode worker | 历史试验在 Linux AO 的独立合成 Git 仓库完成单文件任务。新决策后不再作为运行或验证环境；保留历史记录，不继续操作。 |
| 目标 Harness 状态 | Codex 需要复验目标模型与 AO 启动参数；DSH 凭据无效；Kimi 未安装；ZCode 无 AO 内置适配器。目标四 Harness 尚未闭环。 |
| 真实项目隔离 | 本轮只使用合成仓库；未向 Orchestrator 注册或修改真实原型项目。 |

### M0 下一步

1. 以 Windows AO 建立目标 Harness × 平台 × 身份 × 模型 × 协议 × 工具权限矩阵；把“AO 目录标记授权”和“真实模型请求成功”分开记录。
2. 由用户在 DSH 自身凭据界面修正无效 API Key；不读取或输出 Key。之后在合成仓库重跑单文件 ACP 任务，验证模型返回、取消、权限拒绝和审计字段。
3. 复验 Windows Codex CLI 与 AO 0.13.7 的启动参数兼容性，并核对目标 GPT-6.1 Sol 是否可用及规划/评审只读限制；WSL Codex 登录不再属于首期范围。
4. 安装并验证 Windows Kimi Code；单独评估 ZCode 官方 CLI/app-server 的最薄适配路径。任何工具权限无法拦截时，只保留只读或不接入。
5. 不做 WSL 桥接。先在 Windows AO 合成项目核验项目 orchestrator、worker/reviewer、每 Harness 的 MCP roster、原生工具权限、worktree 和人工审批。只有证明 AO 原生能力无法实现所需硬门禁后，才实现最薄的 Windows 本地状态服务/网关，并在合成仓库证明越权拒绝、审计脱敏和失败现场保留。

M0 权限与恢复验收通过前不进入真实原型开发任务。现有 Linux 状态机/网关工作树保持原样，不启动、不读写、不清理、不重置；如确认需要复用，只能在另行创建的隔离副本评估。

### M1 与 M2

- **M1：** 在 Windows AO 合成仓库完成一条按任务需要裁剪的协作链：Codex 只读计划 → 人工或已验证的 Windows 门禁批准 → 指定 Harness worker 实现 → 确定性测试 → 独立只读评审 → 人工确认交付。失败保留现场并验证人工恢复。
- **M2：** 对目标四 Harness 逐一做真实角色任务和交叉评审，再在真实项目的只读快照/独立副本完成试点。未经用户授权不写回原始工作树、不 push、不合并、不部署。

## MVP 验收

| 指标 | 标准 |
| --- | --- |
| 目标 Harness 接入 | Codex、DeepSeek Harness、Kimi Code、ZCode 均以真实 CLI/API 完成各自角色任务；认证、模型标识、权限拒绝、取消和审计字段有证据。任一未通过，不能宣称多 Harness MVP 闭环。 |
| 端到端交付成功率 | 预登记 10 个小中型任务，至少 9 个通过全部适用验收；人工补写代码单独统计，不算 Agent 自主交付成功。 |
| 人工干预 | 统计每任务计划内审批、计划外介入、人工补写三项；成功任务的计划外介入平均不高于 2 次。 |
| 缺陷逃逸 | 交付后 7 日内高严重度缺陷为 0；确认缺陷逃逸率不高于 10%，无确认缺陷时标记 N/A。 |
| 安全门禁 | 评审写入、Worker push、任务外写入、无授权数据库写均为 0；角色越权拒绝测试全部通过。 |
| 任务隔离与恢复 | 并行任务 worktree 不串写；失败现场可检查；重启/取消后不自动重放写操作。 |
| 浏览器 | 每任务 Profile 隔离；URL、DOM、控制台/页面错误和网络检查有证据。 |
| 成本 | 不设降本目标，也不设与 GPT 基线的成本门槛；token 只用于诊断。 |

稳定使用前累计观察至少 30 个任务。MVP 发布须附任务清单、版本与配置、角色权限矩阵、审计样例和未解决问题。

## 主要风险与依据

- **权限旁路（高）：** AO 原生界面、Harness CLI 或 MCP 子进程可能绕过外部门禁。受控派发只是第一道边界；逐 Harness 验证原生工具与 OS 隔离，否则该 Harness 保持只读或不接入。
- **身份/模型误认（高）：** CLI 登录成功不证明请求模型可用；返回模型标识不证明供应商底层权重。模型映射和返回字段都应留证。
- **适配变更（中高）：** DeepSeek Harness 仍需版本与协议回归；ZCode 可能需要专有协议适配。升级前在合成仓库复测。
- **Windows Harness 兼容（中高）：** AO 自检发现 Codex CLI 启动参数被拒；DSH catalog 的授权状态与真实 API 鉴权结果不一致。修复并回归前，Windows 主环境只算已选定，不算目标 Harness 已就绪。
- **原生工具绕过 MCP（高）：** AO 的 Chat MCP 能力依 Harness/Provider 而异，MCP gateway 不自动约束 Harness 原生文件、终端、Git 权限。逐 Harness 验证并隔离；不能证明边界时不开放写入。
- **ZCode 适配缺口（中高）：** 当前 AO 支持矩阵未列出 ZCode；需检验官方 CLI 的 JSON/headless 与 guarded 权限，必要时开发 Windows adapter。不能因 ACP 普及而假设专有 app-server 是 ACP。
- **Linux 原型可移植性（中）：** 原 WSL 状态机/网关原型依赖 Linux 系统调用和沙箱。首期不桥接、不操作；只有必要性成立后才在 Windows 隔离副本评估移植。
- **产品覆盖不足（高）：** 目标 DSH 请求失败、Kimi 未安装、ZCode 无内置适配器；不得把底座能选择 adapter 等同于真实接入。

官方资料：

[1] Orchestrator.inc 项目与 worktree 说明：https://github.com/OrchestratorInc/agent-orchestrator

[2] Orchestrator Windows 安装与本地 Harness 认证：https://docs.orchestrator.inc/installation/

[3] Orchestrator OpenCode 原生 ACP 适配：https://docs.orchestrator.inc/plugins/agents/opencode/

[4] Orchestrator 角色与 worker 委派：https://docs.orchestrator.inc/guides/per-role-agents/

[5] Orchestrator Harness 适配矩阵：https://docs.orchestrator.inc/plugins/agents/

[6] CAO 跨 Provider profile 参考：https://awslabs.github.io/cli-agent-orchestrator/docs/features/multi-provider/

[7] Orchestrator 支持的平台：https://docs.orchestrator.inc/platforms/

[8] Orchestrator DeepSeek Harness 适配说明：https://github.com/OrchestratorInc/agent-orchestrator/blob/main/docs/harnesses/deepseek-harness.md

[9] Orchestrator Chat/MCP 与当前 Harness 适配矩阵：https://docs.orchestrator.inc/plugins/agents/

[10] Agent Client Protocol 官方规范：https://github.com/agentclientprotocol/agent-client-protocol

[11] Kimi Code 官方 ACP 能力与 MCP 转发说明：https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-acp

[12] Harnss 仓库（早期开发状态、ACP engines、per-project MCP）：https://github.com/OpenSource03/harnss
