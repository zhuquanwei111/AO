# 多智能体协同开发系统搭建方案

Orchestrator Windows 主环境试点版 3.2　｜　2026 年 10 月 12 日

## 决策摘要

**首期只使用 Windows 版 Orchestrator；停止后续 WSL/Linux 执行与桥接验证。** AO 的 Windows 版本已原生管理本地 daemon、Harness 会话、角色委派和 Git worktree；MVP 不再并行维护第二套 Linux AO。此前 WSL OpenCode 任务只保留为历史试验。Windows AO v0.13.7 已验证 Codex→Claude 委派；本轮修正 DSH ACP 独立模型路由后，天翼 DeepSeek 请求与 AO→DSH 派发均已通过。Kimi 的天翼 API、CLI 和原生 ACP 通过，但 AO Chat 仍因自定义 Provider 的认证预检阻断。任务流程由 AO 原生 orchestrator、worker 和 reviewer 按任务需要协作，在重要交付节点由人确认；首期不开发外部状态机。本轮未触碰真实原型项目。

这些试验只证明部分会话启动、跨 Harness 派发和 worktree 隔离能力，**尚未证明目标 Harness 的工具权限、取消和审计已端到端验收**。Claude 和 OpenCode 是验证用 Harness，不替代首期目标 Harness。DSH 之前的失败来自 ACP profile 仍指向 DeepSeek 官方 Provider；现已将 ACP 专用路由改为天翼 `deepseek-v4-pro-0813`，直连 ACP 与 AO Chat 均返回验证标记。Kimi Code CLI 2.1.1 已安装；用户确认 GPT 走官方订阅，DeepSeek、Kimi、GLM 等天翼模型共用同一 API Key。Kimi 的 HTTP API、CLI 和原生 ACP 均通过；AO readiness 仍为 `unknown`，AO Chat 返回 `CHAT_AUTH_REQUIRED`，需继续验证 AO adapter 与自定义 Provider 的兼容性。ZCode 当前没有 AO 内置适配器，按用户决定暂不纳入首期。

本轮权限复测确认 DSH 在 `read-only` 下会拒绝直接写入；正确的一次性升级请求会进入 AO `pending` 人工审批。默认 `workspace-write` 仍允许工作区内的任务外写入，因此不等于按任务文件范围授权。只读探测结束后，合成项目已恢复 `workspace-write`，DSH worker 的 AO 权限保持 `default`。AO SQLite 会话活动记录保存 MCP 工具输入内容，现阶段不能称为脱敏审计。Kimi 截图中的旧探针未通过标记校验，已修复 stderr 捕获并复测 CLI 成功；AO Chat/TUI 闭环仍待核验。

| 决策项 | MVP 决定 |
| --- | --- |
| 执行底座 | 仅 Windows Orchestrator。停止新建 WSL AO、Linux worker 和跨 OS worktree 映射；历史 WSL 环境保留，不清理、不重置。 |
| 任务流程 | AO 原生 orchestrator 负责拆解、派发、跟踪与回收；复杂任务可采用 spec/plan/build/test/review/ship 检查清单，不强制每个任务走固定六次审批。 |
| 阶段管理 | 采用 AO 原生 orchestrator/worker/reviewer 编排，并在重要交付点由人确认；流程按任务复杂度裁剪，不开发独立状态机。AO 的会话状态和 PR/CI 状态用于跟踪，不宣称它们是自定义阶段的硬拦截器。 |
| 派发 | 由 AO 原生 orchestrator 创建隔离 worker 并回收结果；不另装 CCTeam/Omnigent，不在 AO 外再建派发控制面。 |
| 首期 Harness | Codex 负责规划/架构/终审，目标为只读；DeepSeek Harness 承担编码；Kimi Code 负责只读调查与复审。`mh-pilot-20` 已证明 AO Codex Chat 的 `default` 权限允许写文件，故 Codex 只读边界验证失败；当前配置下不得把它当作强制只读角色。DSH 的只读沙箱通过项目环境变量实测有效，但 AO 环境配置是项目级，不可按 Harness 角色分开；`workspace-write` 只限制在 worktree 内，不能限制任务范围内的具体文件。ZCode 暂不使用。 |
| 模型来源 | GPT-6.1 Sol 通过 Codex 官方订阅使用；DeepSeek、Kimi、GLM 通过天翼 API，共用环境变量 `TIANYI_API_KEY`。DeepSeek 请求 ID 固定为 `deepseek-v4-pro-0813`，接受并记录供应商实际返回标识，不自动切换；Kimi 使用 `kimi-k3`。天翼列表中的 GLM 暂不分配首期角色。 |
| 成本 | 不作为 MVP 目标；质量、可控性和完成率优先。保留 token 用量供诊断，不设降本比例。 |
| 发布 | MVP 由人确认交付；不自动 push、合并或部署。 |

## 架构与任务流程

用户提出任务 → Windows AO orchestrator 规划与派发 → 按任务选择 Codex、DSH、Kimi 或其他已验证 Harness → AO 为 Git 任务建立独立 worktree → 使用 Harness 原生权限及已验证的角色 MCP → 测试/交叉评审 → 人工确认交付。复杂任务可用阶段清单；小任务可按风险裁剪。

spec → plan → build → test → review → ship 作为可裁剪的质量检查清单，不是固定工作流。AO orchestrator 根据任务复杂度决定需要哪些角色和步骤，重要交接由人确认。首期不单独实现阶段状态机；AO 会话状态和项目规则用于协作跟踪，不冒称不可绕过的程序级门禁。权限仍须逐 Harness 核验。

Orchestrator 的 Windows daemon、项目 orchestrator、worker/reviewer、Harness 适配、Git worktree 和会话 UI 是首期直接复用的协调层。AO 提供的 Chat MCP 能力取决于 Harness/Provider；MCP 网关不是 AO 的统一全局权限代理。每个 Harness 必须分别核验可见工具、原生文件/终端权限、取消和审计。AO daemon 保持本机默认访问边界，不另开公网入口。

## Harness 与模型职责

| Harness / 角色 | 职责 | MVP 状态与限制 |
| --- | --- | --- |
| Codex + GPT-6.1 Sol | 需求分析、架构、计划、只读终审 | `mh-pilot-11` 的 AO Chat 以 `gpt-6.1-sol` 返回验证标记。`mh-pilot-20` 在 AO `default` 权限下成功创建只读探测标记，故此角色的只读边界失败。AO CLI 权限枚举没有 `read-only`，旧 doctor 的启动参数警告也未针对本次角色配置复验。 |
| DeepSeek Harness | 有明确验收条件的编码任务 | Windows DSH 0.1.2-alpha.5；ACP 固定天翼 `deepseek-v4-pro-0813` 并引用 `TIANYI_API_KEY`。`mh-pilot-21` 在隔离 worktree 完成有边界的小型编码任务，4 项 unittest 全通过，未 commit/push；源文件、测试和生成的 `__pycache__` 均保留。`mh-pilot-13` 证明默认工作区写模式允许任务外写入；`mh-pilot-14`/`mh-pilot-16` 的 `read-only` 写入被拒，`mh-pilot-16` 一次性升级停在未批准的 AO `pending`。取消、clean-session restore 已验；任务文件级权限和审计脱敏仍待验收。 |
| Kimi Code | 只读代码调查、计划挑刺和独立复审 | CLI 2.1.1 已安装；天翼 `kimi-k3` HTTP API、CLI 和原生 ACP 探测通过。截图中的旧脚本 `marker_match=False` 且把 stderr 当错误；隐藏输出的新探针复测成功。AO readiness 为 `unknown`，AO Chat 仍返回 `CHAT_AUTH_REQUIRED`；AO TUI session `standalone-1` 仍为 working，未拿到可核验最终结果。 |
| ZCode | 暂不纳入首期 | AO 当前无内置适配器；不为首期增加自定义桥接。以后需要时再评估官方 CLI/app-server 的权限和协议。 |
| OpenCode | 不纳入首期 Harness 角色 | WSL 中历史完成的一次隔离任务仅作为参考，不再继续 Linux 验证。 |

Harness 名称不等于模型身份。API 调用固定请求模型 ID；实际返回标识单独记录。供应商改路由时继续当前响应，不自动换模型。角色分工只是初始假设，须用相同任务集比较质量后才能扩权。

## 复用、自研与后续扩展

| 分类 | 组件 | 落地边界 |
| --- | --- | --- |
| 复用开源 | Windows Orchestrator、内置 Harness adapters、Git worktree、ACP、Playwright | AO 承担协调、任务 worker、review、会话生命周期和 worktree；DSH/Kimi 使用 AO 已提供的 ACP adapters；不自研 Harness 循环。 |
| 参考但不部署 | CCTeam、Omnigent/Polly、Vibe Kanban、Harnss | 参考跨 Harness 会话/结果回收、任务看板、ACP/MCP 配置和派发前策略 hook。它们与 AO 的协调/隔离能力重叠；CCTeam 公开文档未说明 per-worker worktree 隔离，且 Windows 使用路径要求 WSL，Harnss 公开标注早期开发且约 384 stars，不作为生产底座。 |
| 保留的现有代码 | WSL 工作树中的 SQLite controller、MCP gateway、模型代理原型 | 本轮不启动、不读写、不移植、不删除。后续如需复用，先在隔离副本盘点可移植部分；不做 Windows↔WSL 桥接。 |
| 需自研/补齐 | 各 Harness 角色权限与审计验证；仅在用户批准后评估缺失的 adapter 或 MCP 代理 | 首期先用 AO 现有编排和 Harness 权限配置。发现具体能力缺口时先报告证据和影响，再决定是否开发；外部状态机不在首期范围。 |
| 二期扩展 | CAO 对照、更多 Harness、自动角色选择、自动 Draft PR、并行多写者 | 只有 M0/M1 权限和恢复验收通过后再评估；当前不启用自动发布或自动修复。 |

Orchestrator.inc 官方 Windows 发行版与本机实测最贴合当前目标；GitHub 仓库约 13.1k stars。其内置 adapter 矩阵包含 Codex、Kimi、DeepSeek Harness 等，但 Worker/Chat/Review 支持各异，按官方能力矩阵逐项验收。CCTeam（648 stars）证明 MCP 派发和策略 hook 可以驱动多 Harness；其公开文档未说明 per-worker worktree 隔离，且 Windows 使用路径要求 WSL，因此只借鉴派发/拒绝策略，不在首期部署。Omnigent（约 10.7k stars）与 AO 的多 Harness/worktree 能力重叠；Vibe Kanban（约 28.3k stars）偏任务/worktree 管理，未证明同一任务内跨 Harness 相互派发；Harnss（384 stars）虽有 ACP + per-project MCP 结构，但仓库明示早期开发/大改写，暂不部署。调研细节见《多Harness参考项目调研》。

## 权限、凭据与失败规则

- 规划和评审角色必须只读。AO 项目权限枚举没有通用 `read-only`；`mh-pilot-20` 已实测 Codex AO Chat 在 `default` 权限下可创建文件，故只读边界失败。AO 官方文档也说明实验性 reviewer 提示不会隔离工具或网络。当前不把 Codex 配置为受信任的只读规划/评审角色；需先找到 AO 支持且实测有效的 sandbox 传递方式，或另行实现受限适配器。DSH 在合成项目 `DSH_PERMISSION_MODE=read-only` 下直接写入被拒，正确的一次性升级则产生 AO `pending` 审批。AO DSH worker 的 `permissions=default` 已实测生效；不设为 `auto`。DSH `workspace-write` 仅约束 worktree，不约束单个任务获准的文件清单。提示词不能代替权限隔离。
- Codex 使用官方订阅身份；DeepSeek、Kimi、GLM 等天翼模型共用用户提供的同一 API Key，并通过 `TIANYI_API_KEY` 供 Harness 读取。天翼供应商不支持按角色独立撤销该 Key。当前 AO daemon 的测试进程环境含该变量，AO 子 Harness 进程可能继承它（包括 Codex）；AO 尚未验证 per-Harness 环境变量隔离，因此目前不能声称密钥已按角色隔离。不要把 Key 写入任务卡、提示或审计；首期按用户决定允许代码发往第三方云 API，审计不保存代码原文或完整提示。
- MCP 网关负责其代理的文件读写、终端、Git、Playwright 工具。Harness 原生工具还须通过 Harness 权限配置与 Windows 进程/文件系统隔离；未证明能限制任务外读写的 Harness 不开放写权限。
- 每次 API 调用审计预期记录请求模型 ID、供应商返回模型标识、token 用量、耗时、状态码、角色、Harness 版本、task_id 和错误类别；拿不到的字段标记为不可得。实测 AO SQLite 会话活动详情包含 MCP 工具输入（包括文件路径和写入内容），AO 原生会话记录不是脱敏审计。M0 尚未满足“审计不记录完整提示/密钥/代码原文”；不得向会话输入密钥，继续调查 AO 保留策略或增加脱敏审计层后再宣称通过。
- 超时、限流和服务错误统一分类；单阶段最多额外重试 2 次。鉴权失败不重试；耗尽预算即失败阻断，不自动切模型、不自动修代码。
- 每个写任务用独立 worktree。失败保留现场；删除全量 checkout/reset/clean 回滚方案。清理或回滚仅由人工确认范围后执行。
- 浏览器使用任务独立临时用户目录；验收检查最终 URL、关键 DOM、控制台/页面错误、网络失败和 HTTP 错误，截图只作补充证据。about:blank、意外重定向或残留 Profile 锁均阻断。
- 工具调用全量留审计事件；不得把提示全文、密钥、源代码原文写入审计日志。AO daemon 固定回环监听，禁止直接暴露远程端口。

## M0 与后续里程碑

### 已完成的底座试验

| 项目 | 结果 |
| --- | --- |
| Windows Orchestrator | daemon health/ready 通过；AO CLI 由桌面安装包内 `resources\daemon\ao.exe` 提供，本机 `ao` 命令未在 PATH。Codex Chat 与 DSH Chat 均有模型标记通过证据；Codex 只读权限尚未验收。 |
| Windows DSH worker | ACP profile 固定天翼 `deepseek-v4-pro-0813`；direct/AO Chat smoke 通过。`mh-pilot-21` 的隔离编码任务 4/4 unittest 通过，0 commit/0 push；`mh-pilot-18/19` 证明 kill 停止子进程、dirty worktree 保留、clean worktree 回收；`mh-pilot-18` restore 后 idle、clean、0 commit。任务文件级限制、复杂失败恢复与审计仍待验。 |
| WSL2 OpenCode worker | 历史试验在 Linux AO 的独立合成 Git 仓库完成单文件任务。新决策后不再作为运行或验证环境；保留历史记录，不继续操作。 |
| 首期 Harness 状态 | Codex GPT-6.1 Sol AO Chat 模型调用通过，但 `mh-pilot-20` 证明 `default` 权限允许写文件，角色只读边界失败；DSH API 与 AO ACP 派发已通过，仍需验收权限/取消/审计；Kimi 天翼 API/CLI 提示探针通过，但 AO Chat spawn 被 Kimi ACP 的 OAuth-only 认证门以 `CHAT_AUTH_REQUIRED` 阻断；AO TUI 结果未确认；ZCode 按用户决定暂不纳入。整体仍未完成三 Harness 闭环。 |
| 真实项目隔离 | 本轮只使用合成仓库；未向 Orchestrator 注册或修改真实原型项目。 |

### M0 下一步

1. 保持真实原型项目未注册。`mh-pilot-16` 的 AO 权限升级请求留在 `pending`，不要批准或清理标记证据；它证明了 read-only 写拒绝与一次性审批门，但不证明任务文件范围限制。
2. DSH 的隔离编码 smoke（`mh-pilot-21`，4/4 unittest）、直接拒写、AO `default` 升级审批、取消、dirty-worktree 保留，以及 clean session `mh-pilot-18` 的 restore 均有合成证据；restore 后会话 idle、worktree clean、0 commit。编码文件、`__pycache__` 和 `mh-pilot-19` 的 `.ao-cancel-retain-probe.txt` 均保留，不清理。`mh-pilot` 已恢复 `workspace-write`；继续核验审计字段和任务文件级权限。
3. Codex GPT-6.1 Sol 已通过 AO Chat 模型调用，但 `mh-pilot-20` 的只读探测成功写入标记，AO CLI 权限枚举也不含 `read-only`。当前 Codex 不能承担要求硬只读的规划/终审；先研究 AO 支持的 sandbox 传递或评估受限适配器，不改全局 Codex 配置。
4. 核查 AO 会话原文保留、删除/导出和脱敏选项；工具输入内容已进入 SQLite 会话活动详情。若 AO 无法满足审计不保留代码原文的要求，形成“自研脱敏审计层”与“接受本机 AO 会话历史留存”两项可选取舍，再决定是否触碰真实项目。
5. Kimi 的天翼 API/CLI 提示探针通过；AO Chat spawn 的 `CHAT_AUTH_REQUIRED` 与 Kimi ACP OAuth-only 认证门相符。`mh-pilot-17` 的 AO TUI 仍 `working/active`，等从 Orchestrator 终端面板核验回复；`standalone-1` 也保留原样。未核验前不把 Kimi 计入闭环；ZCode 暂不接入、不做 WSL 桥接。

M0 权限与恢复验收通过前不进入真实原型开发任务。现有 Linux 状态机/网关工作树保持原样，不启动、不读写、不清理、不重置；如确认需要复用，只能在另行创建的隔离副本评估。

## 2026-10-12 DSH 沙箱与 AO 人工审批复测

- 合成项目 `mh-pilot` 在拒写探测期间配置 `env.DSH_PERMISSION_MODE=read-only`、`worker.agentConfig.permissions=default`，并保留 Codex orchestrator 的 `gpt-6.1-sol`。探测结束后已恢复 `workspace-write`；worker 权限仍为 `default`。新 DSH 会话 `mh-pilot-15`/`mh-pilot-16` 记录为 `default`，不是 AO 自动批准模式。
- `mh-pilot-13` 使用默认 `workspace-write`，DSH `write` 工具成功创建 `OUT_OF_SCOPE_PROBE.txt`，只有该未跟踪文件，0 commit、0 push；这是越权范围测试失败的证据，文件保留。
- `mh-pilot-14` 在 DSH `read-only` 下直接写入失败，文件未创建。`mh-pilot-16` 同样先收到 read-only 写拒绝；随后按 Harness 要求的“被拒后一次性升级”发起 `workspace-write` 请求，AO 活动记录出现 `approval/pending`，会话状态为 `waiting_input`。未批准，升级标记文件不存在，0 commit、0 push。
- AO 的 `conversation_activities.detail_json` 持久化 MCP 工具输入字段，包括 `file_path` 和 `content`；示例标记内容在 AO SQLite 活动详情可检出。不能把 AO 会话历史当作满足脱敏要求的独立审计日志。
- DSH 的只读沙箱与审批入口当前可用于限制 Harness 提权；`workspace-write` 不提供按任务文件清单的写授权。AO 项目环境变量是项目级配置，不可按 Harness 角色单独设置；本次探测后合成项目已切回 `workspace-write`，后续 DSH 写入仍只限于其隔离 worktree。
- 官方参考：[AO DSH 权限适配](https://github.com/OrchestratorInc/agent-orchestrator/blob/main/docs/harnesses/deepseek-harness.md)、[DSH 沙箱模式与升级规则](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/sandbox/sandbox/README.md)、[DSH 安全说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/SAFETY.md)。

### M1 与 M2

- **M1：** 在 Windows AO 合成仓库完成一条按任务需要裁剪的协作链：Codex 只读计划 → AO orchestrator 按任务派发 → 指定 Harness worker 实现 → 确定性测试 → 独立只读评审 → 人工确认交付。失败保留现场并验证人工恢复。
- **M2：** 对首期三个 Harness 逐一做真实角色任务和交叉评审，再在真实项目的只读快照/独立副本完成试点。ZCode 暂不纳入；未经用户授权不写回原始工作树、不 push、不合并、不部署。

## MVP 验收

| 指标 | 标准 |
| --- | --- |
| 目标 Harness 接入 | Codex、DeepSeek Harness、Kimi Code 均以真实 CLI/API 完成各自角色任务；认证、模型标识、权限拒绝、取消和审计字段有证据。ZCode 因 AO 暂无 adapter 而排除在首期验收范围外。任一首期 Harness 未通过，不能宣称多 Harness MVP 闭环。 |
| 端到端交付成功率 | 预登记 10 个小中型任务，至少 9 个通过全部适用验收；人工补写代码单独统计，不算 Agent 自主交付成功。 |
| 人工干预 | 统计每任务计划内审批、计划外介入、人工补写三项；成功任务的计划外介入平均不高于 2 次。 |
| 缺陷逃逸 | 交付后 7 日内高严重度缺陷为 0；确认缺陷逃逸率不高于 10%，无确认缺陷时标记 N/A。 |
| 安全门禁 | 评审写入、Worker push、任务外写入、无授权数据库写均为 0；角色越权拒绝测试全部通过。 |
| 任务隔离与恢复 | 并行任务 worktree 不串写；失败现场可检查；重启/取消后不自动重放写操作。 |
| 浏览器 | 每任务 Profile 隔离；URL、DOM、控制台/页面错误和网络检查有证据。 |
| 成本 | 不设降本目标，也不设与 GPT 基线的成本门槛；token 只用于诊断。 |

稳定使用前累计观察至少 30 个任务。MVP 发布须附任务清单、版本与配置、角色权限矩阵、审计样例和未解决问题。

## 主要风险与依据

- **工具权限边界（高）：** AO 提供的 MCP 能力依 Harness/Provider 而异，不能自动约束 Harness 原生文件、终端和 Git 工具。逐 Harness 验证权限；不能证明写入边界时，该 Harness 暂不执行写任务。
- **身份/模型误认（高）：** CLI 登录成功不证明请求模型可用；返回模型标识不证明供应商底层权重。模型映射和返回字段都应留证。
- **适配变更（中高）：** DeepSeek Harness 仍需版本与协议回归；Kimi 登录与 AO adapter 需要真实验收。升级前在合成仓库复测。
- **Windows Harness 兼容（中高）：** AO 自检发现 Codex CLI 启动参数被拒；Kimi 的 AO Chat 认证预检与自定义 Tianyi Provider 不兼容。逐 Harness 验收后再扩大写权限。
- **共享凭据边界（高）：** 天翼所有模型共用同一 API Key，供应商侧不能按 Harness/角色分别撤销；当前 AO daemon 环境变量会被子进程继承，可能覆盖到 Codex。AO 尚无已验证的 per-Harness 秘密隔离，需在 M0 安全验收中明确边界与缓解方式。
- **原生工具绕过 MCP（高）：** AO 的 Chat MCP 能力依 Harness/Provider 而异，MCP gateway 不自动约束 Harness 原生文件、终端、Git 权限。逐 Harness 验证并隔离；不能证明边界时不开放写入。
- **ZCode 当前不纳入首期：** AO 支持矩阵没有列出 ZCode adapter，因此首期不使用，也不开发桥接。若后续需要，再评估官方协议与权限边界。
- **Linux 原型可移植性（中）：** 原 WSL 状态机/网关原型依赖 Linux 系统调用和沙箱。首期不桥接、不操作，也不迁移。
- **Harness 就绪度不足（高）：** DSH ACP 与 AO Chat 已通过最小模型派发；Kimi 的天翼 API/CLI 提示探针通过，但 AO Chat spawn 被 Kimi ACP OAuth-only 认证门挡住，TUI 仍待实测。AO 支持目录中的 adapter 不等于真实接入成功。
- **Codex 只读边界（高）：** AO 项目角色配置没有显式只读模式；即使把 Codex 指定为规划/评审角色，也不能据此断言其工具被硬性限制为只读。必须用隔离的 `mh-pilot` worktree 验证 Codex 项目级沙箱，或在验证前禁用 Codex 写工具。

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

[13] DeepSeek Harness 官方自定义 Provider 与 `apiKeyEnv` 配置：https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.md

[14] DeepSeek Harness 官方 ACP 模型路由配置：https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/acp/acp/README.md

## 2026-10-12 Kimi ACP API-Key 认证阻断

- 截图里的旧探针 HTTP 200 但 `marker_match=False`，不能算模型回答通过；更新后的隐藏输出 CLI 提示探针使用 `tianyi/kimi-k3` 返回标记并以退出码 0 结束，未显示 Key 或提示正文。
- 对本机 AO 执行真实 Kimi Chat spawn，返回 `CHAT_AUTH_REQUIRED`；`ao agent ls --refresh --json` 显示 Kimi 已安装但 `authStatus=unknown`。此次失败发生在会话创建预检，不能记为 AO Chat 接入成功。
- Kimi Code CLI ACP 认证门当前只检查 Kimi OAuth token。上游公开 PR [#2185](https://github.com/MoonshotAI/kimi-cli/pull/2185) 记录了已配置 API Key 的用户仍被 ACP 强制要求 OAuth 登录；这与天翼 API/CLI 提示探针通过、AO Chat 被拒的表现一致。不同 Harness 入口的认证能力不能互相推断。
- 现有 AO `standalone-1` Kimi TUI smoke 仍显示 `working`，没有可核验的最终回复；不终止、不清理。新建的 `mh-pilot-17` 当前显示 `working/active`，AO CLI 不提供可核验的最终终端回复，worktree 仅有基线 `.git`/`README.md`，故不计通过。
- 下一步需从 AO TUI 会话界面核验输出，或等待 Kimi ACP API-Key auth gate 上游修复后重测 Chat；在取得可核验成功前不把 Kimi 计入多 Harness 闭环。真实原型未触碰。

## 2026-10-12 DSH 取消与 worktree 保留复测

- `mh-pilot-18` 用 DSH Chat 启动 45 秒无副作用的 Python sleep。`ao session kill` 成功终止 AO session 与子进程；该 worktree 干净，AO 自动回收，未丢失任务文件或提交。
- `mh-pilot-19` 在独立 worktree 创建唯一标记 `.ao-cancel-retain-probe.txt`，再启动同一 sleep。kill 后 session 为 terminated，Python 子进程数归零；标记文件仍存在、Git 状态为单个未跟踪文件、无提交或 push。文件保留作验收证据，未清理。
- 因此已验证 AO 可停止当前 DSH 子进程并保留含未提交改动的 worktree；干净 worktree 会被自动回收，符合 AO 对可安全清理目录的策略。`session restore`、更复杂失败恢复和任务文件级写权限仍未验收。AO 的规则见 [Git worktree cleanup 文档](https://docs.orchestrator.inc/plugins/workspaces/worktree/)。

## 2026-10-12 Codex AO Chat 只读边界复测（最新）

- AO `mh-pilot-20` 使用 Codex + GPT-6.1 Sol、session permissions=`default`，在合成 worktree 执行一次限定的写入探测。Codex 创建了 `CODEX_READONLY_PROBE.txt`，内容为 `CODEX_WRITE_PROBE`；`git status` 显示一个未跟踪文件，0 commit、0 push。文件与 worktree 保留作审查证据：`C:\Users\admin\.ao\data\worktrees\mh-pilot\mh-pilot-20\CODEX_READONLY_PROBE.txt`。
- 结论：当前 AO Codex Chat `default` 不是只读。`ao project set-config --help` 只列出 `default`、`accept-edits`、`auto`、`bypass-permissions`，`ao spawn --help` 没有会话级 sandbox 参数；AO 官方文档说明实验性 reviewer 模式/提示不提供工具或网络隔离。角色提示词不能作为安全边界。
- 在 AO 能把 Codex 原生 `read-only` sandbox 传入会话并通过拒写探测之前，不把 Codex 用于有硬只读要求的规划/终审角色；不修改全局 Codex 配置。Codex CLI 支持只读参数不代表 AO Chat 已传递该参数。
- 本轮再次运行隐藏输出的 Kimi 探针脚本，`tianyi/kimi-k3` 返回预期标记，退出码 0；密钥和提示正文未输出。此结果只证明 CLI 模型调用，不证明 AO Chat 接入。
- 真实项目未触碰；标记、worktree 和历史 pending 审批均保留，未执行自动回滚、清理、commit 或 push。
- 官方参考：[AO Codex adapter](https://docs.orchestrator.inc/plugins/agents/codex/)、[AO agent adapter capability notes](https://docs.orchestrator.inc/plugins/agents/)（含 reviewer 不等于沙箱隔离的说明）、[Codex SDK sandbox presets](https://github.com/openai/codex/blob/main/sdk/python/src/openai_codex/_sandbox.py)。

## 2026-10-12 DSH clean-session restore 复测

- 按 AO 官方 restore 命令语义，对已终止且 worktree 干净的 `mh-pilot-18` 执行一次手动 restore。会话由 `terminated` 回到 `idle`，仍是同一 session/branch；worktree 恢复存在且 `git status --short` 为空，0 commit、0 push，没有重新产生测试标记。
- 这只验证 DSH clean-session 的恢复路径；dirty session 恢复、失败中的多进程恢复、AO 活动原文脱敏与任务文件级权限仍待验证。没有触碰运行中的 `mh-pilot-17`、pending 的 `mh-pilot-16` 或真实项目。

## 2026-10-12 DSH bounded coding smoke

- 在合成项目新建会话 `mh-pilot-21`，分支 `ao/mh-pilot-21/root`，DSH 使用当前天翼 ACP 配置完成一个有明确范围的 Python 编码任务。仅新增 `m0_smoke.py` 与 `test_m0_smoke.py`；运行标准库 `unittest`，4 项全部通过。
- 独立复跑同一命令，结果仍为 4/4 通过；人工检查实现符合任务约束。Git 状态为两个源文件和测试生成的 `__pycache__/` 未跟踪，0 commit、0 push；全部现场保留。
- 这证明 DSH 能在 AO worktree 完成小型编码与确定性本地检查，不证明大型任务质量、按任务文件授权、模型底层版本身份或审计脱敏。

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
