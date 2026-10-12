from docx import Document
from pathlib import Path

path = Path("多智能体协同开发系统搭建方案_Orchestrator试点版_v3.2.docx")
doc = Document(path)
paragraphs = list(doc.paragraphs)
for table in doc.tables:
    for row in table.rows:
        for cell in row.cells:
            paragraphs.extend(cell.paragraphs)

replacements = [
    ("DSH read-only 拒写、AO default 升级审批、kill 停止子进程和 dirty worktree 保留均有合成证据；mh-pilot-19 标记文件保留作现场，不清理。继续核验 session restore、审计字段和任务文件级写权限。", "DSH read-only 拒写、AO default 升级审批、kill 停止子进程、dirty worktree 保留和 mh-pilot-18 clean session restore 均有合成证据；restore 后会话 idle、worktree clean。mh-pilot-19 标记文件保留作现场，不清理。继续核验审计字段和任务文件级写权限。"),
    ("Codex 只读权限与 Kimi TUI 结果仍未验收。", "Codex AO Chat default 权限已实测允许写入，只读边界失败；Kimi TUI 结果仍未验收。"),
    ("DSH Chat 的 ACP 请求仍鉴权失败。", "DSH 的天翼 ACP 直连与 AO 派发均已通过最小模型探测。"),
    ("AO readiness 仍待核验", "HTTP API、CLI 与原生 ACP 已通过，但 AO Chat 仍被 CHAT_AUTH_REQUIRED 阻断"),
    ("Windows 已安装 DSH 0.1.2-alpha.5；AO ACP worker 两次请求均返回鉴权失败，没有执行写入。需修正 DSH 自身凭据后再测。请求模型 ID 固定，接受供应商公共 API 路由并记录返回标识；不推断底层权重。", "Windows 已安装 DSH 0.1.2-alpha.5；ACP profile 已改为天翼 OpenAI-compatible Provider，并设为 deepseek-v4-pro-0813。直连 ACP 与 AO Chat 均返回验证标记；权限、取消、审计字段仍待验收。"),
    ("CLI 2.1.1 已安装；默认模型配置为天翼 kimi-k3（https://ai.ctaigw.cn/v1），通过 TIANYI_API_KEY 读取天翼共享 Key；HTTP API 和 Kimi CLI 探测已通过。AO 当前列出 kimi 为 supported、未列入 installed；需处理 AO 进程 PATH 后复查 readiness 和只读权限。", "CLI 2.1.1 已安装；默认模型为天翼 kimi-k3（https://ai.ctaigw.cn/v1），通过 TIANYI_API_KEY 读取共享 Key。HTTP API、Kimi CLI 与原生 ACP 探测已通过；AO readiness 为 unknown，AO Chat 返回 CHAT_AUTH_REQUIRED，尚未证明 AO Chat 可派发自定义 Provider。"),
    ("合成仓库 worker 的两次 ACP turn 均返回鉴权失败，worktree 无任务文件，未 commit/push。真实模型调用未通过。", "ACP profile 已指向天翼 deepseek-v4-pro-0813；直接 ACP 与 AO Chat smoke 均成功返回标记，AO worktree 0 commit、未 push。权限、取消和审计验收未完成。"),
    ("DSH ACP 曾鉴权失败，需核对其是否使用共享天翼 Key；Kimi CLI 已安装；天翼专属 Key 与 AO adapter 识别待验证；ZCode 按用户决定暂不纳入。三个首期 Harness 尚未闭环。", "DSH ACP 与 AO Chat 最小模型派发已通过，仍需验收权限/取消/审计；Kimi API、CLI、原生 ACP 已通过但 AO Chat 被 CHAT_AUTH_REQUIRED 阻断；ZCode 按用户决定暂不纳入。整体仍未完成三 Harness 闭环。"),
    ("现有 standalone-1 Kimi TUI smoke 仍为 working，未取得可核验结果；不终止、不清理。下一步仅在 mh-pilot 合成 worktree 验证 AO TUI 使用 Tianyi API Key；未通过前不把 Kimi 计入闭环。", "standalone-1 Kimi TUI smoke 仍为 working，最终回复未核验，不终止、不清理。mh-pilot-17 当前为 working/active，AO CLI 无可核验终端回复，worktree 只有基线文件；不计通过。需从 AO TUI 界面核验或等待上游修复 ACP API-Key auth gate。"),
    ("standalone-1 Kimi TUI smoke 仍为 working，最终回复未核验，不终止也不清理。mh-pilot-17 TUI smoke 很快变为 idle，worktree 只有基线文件，但 AO CLI 未提供可核验回复，不计通过；需从 AO TUI 界面核验或等待上游修复 ACP API-Key auth gate。", "standalone-1 Kimi TUI smoke 仍为 working，最终回复未核验，不终止、不清理。mh-pilot-17 当前为 working/active，AO CLI 无可核验终端回复，worktree 只有基线文件；不计通过。需从 AO TUI 界面核验或等待上游修复 ACP API-Key auth gate。"),
    ("由用户在 DSH 自身凭据界面修正无效 API Key；不读取或输出 Key。之后在 Windows 合成仓库重跑单文件 ACP 任务，验证模型返回、取消、权限拒绝和审计字段。", "DSH ACP 的天翼路由已在合成仓库通过；继续验证 permission request 拒绝、取消和审计字段，不再要求另设 DSH Key。"),
    ("重启/刷新 AO 后检查 kimi adapter readiness，再验证真实连通、返回模型、工具调用、只读权限与 MCP 能力。", "处理 AO readiness unknown / CHAT_AUTH_REQUIRED，并确认 AO Chat 是否识别自定义 Tianyi Provider；若未解除，再单独验证 AO Terminal UI 路径。"),
    ("并通过 TIANYI_API_KEY 供需要的 Harness 进程读取。天翼供应商不支持按角色独立撤销该 Key，因此不得声称凭据已实现角色隔离；按最小范围注入进程，不写入任务卡、提示或审计。", "并通过 TIANYI_API_KEY 供 Harness 读取。天翼供应商不支持按角色撤销该 Key；当前 AO daemon 环境含该变量，子 Harness 进程可能继承（包括 Codex），且 per-Harness 环境隔离尚未验证。不得声称凭据已按角色隔离；不写入任务卡、提示或审计。"),
    ("DSH catalog 的授权状态与真实 API 鉴权结果不一致。", "Kimi 的 AO Chat 认证预检与自定义 Tianyi Provider 不兼容。"),
    ("DSH ACP 曾鉴权失败，需确认它读取共享天翼 Key；Kimi 的 HTTP API 与 CLI 已通过，但 AO 当前未识别 Kimi CLI。", "DSH ACP 与 AO Chat 最小派发已通过；Kimi 的 HTTP API、CLI、原生 ACP 已通过但 AO Chat 认证预检仍失败。"),
    ("但实际 Windows ACP 请求返回鉴权失败；该目录状态不能替代真实请求验证。", "此前 ACP 仍指向 DeepSeek 官方 Provider；修正为天翼 ACP 专用模型路由后，直连与 AO Chat 最小请求均已通过。"),
    ("并通过 `TIANYI_API_KEY` 供需要的 Harness 进程读取。天翼供应商不支持按角色独立撤销该 Key，因此不得声称凭据已实现角色隔离；按最小范围注入进程，不写入任务卡、提示或审计。", "并通过 `TIANYI_API_KEY` 供 Harness 读取。天翼供应商不支持按角色撤销该 Key；当前 AO daemon 环境含该变量，子 Harness 进程可能继承（包括 Codex），per-Harness 环境隔离尚未验证。不得声称凭据已按角色隔离；不写入任务卡、提示或审计。"),
    ("重启/刷新 AO 并检查 kimi adapter readiness，再验证真实连通、返回模型、工具调用、只读权限与 MCP 能力。", "处理 AO readiness unknown / CHAT_AUTH_REQUIRED，并确认 AO Chat 是否识别自定义 Tianyi Provider；若未解除，再单独验证 AO Terminal UI 路径。"),
    ("DSH 请求鉴权失败；Kimi 的天翼 API 与 Kimi CLI 探测已通过，但 AO 当前未识别本机 Kimi CLI。", "DSH ACP 与 AO Chat 最小派发已通过；Kimi 的天翼 API、CLI、原生 ACP 已通过但 AO Chat 认证预检仍失败。"),
]

def rewrite(paragraph, old, new):
    text = paragraph.text
    if old not in text:
        return 0
    paragraph.text = text.replace(old, new)
    return 1

counts = []
for old, new in replacements:
    count = sum(rewrite(paragraph, old, new) for paragraph in paragraphs)
    counts.append(count)

if not any(p.text == "2026-10-12 接入状态更新" for p in doc.paragraphs):
    doc.add_heading("2026-10-12 接入状态更新", level=1)
    for item in [
    "GPT-6 Sol 使用 Codex 官方订阅；DeepSeek、Kimi、GLM 使用天翼 API 并共用 TIANYI_API_KEY。密钥值没有写入文档或审计。",
    "DSH ACP profile 通过 apiKeyEnv 引用共享 Key；必须同时设置通用 agent-default-model 与 ACP 专属 acp route。直接 ACP 和 AO Chat 最小探测均通过，AO worktree 无改动、未提交。",
    "Kimi 天翼 HTTP API、CLI 2.1.1、原生 ACP 均通过；AO readiness 为 unknown，AO Chat 仍返回 CHAT_AUTH_REQUIRED。",
    "本次 AO daemon 环境含 TIANYI_API_KEY，子 Harness 可能继承该变量，包括 Codex；per-Harness 环境隔离尚未验证，是当前高风险项。",
    "M0 仍需完成 Codex 模型/启动核验、DSH 权限/取消/审计验证，以及 Kimi AO 接入路径验证；真实原型项目尚未触碰。",
    ]:
        doc.add_paragraph(item, style="List Bullet")

latest_heading = "2026-10-12 DSH 沙箱与 AO 审批复测"
if not any(p.text == latest_heading for p in doc.paragraphs):
    doc.add_heading(latest_heading, level=1)
    for item in [
        "mh-pilot-13 在默认 workspace-write 下通过 DSH write 工具创建任务外标记 OUT_OF_SCOPE_PROBE.txt；worktree 仅有该未跟踪文件，0 commit、0 push。提示词不能把工作区写权限缩小到任务文件范围。",
        "拒写探测期间，合成项目 mh-pilot 临时设 DSH_PERMISSION_MODE=read-only、worker permissions=default。mh-pilot-14 与 mh-pilot-16 的直接写入均被拒；mh-pilot-16 的一次性 workspace-write 升级进入 AO pending 审批，未批准，标记文件不存在，0 commit、0 push。探测后已恢复 workspace-write，worker permissions 仍为 default。",
        "AO SQLite conversation_activities.detail_json 持久化 MCP 工具输入，包括 file_path 与 content；当前不能宣称 AO 原生会话记录满足不保存代码原文的脱敏审计要求。",
        "截图中的旧 Kimi CLI 探针因 marker_match=False 未通过；更新后的隐藏输出脚本已复测 CLI 成功。AO Chat 仍 CHAT_AUTH_REQUIRED，TUI 会话仍无可核验完成结果。真实原型项目未触碰。",
    ]:
        doc.add_paragraph(item, style="List Bullet")

for table in doc.tables:
    for row in table.rows:
        label = row.cells[0].text.strip()
        if label == "目标 Harness" and len(row.cells) >= 2:
            row.cells[1].text = (
                "Codex 负责规划、架构和终审，目标为只读；DeepSeek Harness 承担编码，Kimi Code 负责只读调查与复审。"
                "mh-pilot-20 已证明 AO Codex Chat default 可写，故当前只读边界失败；未找到 AO 强制传递 read-only sandbox 的配置。"
                "未解决前不把 Codex 作为硬只读角色。ZCode 暂不使用。"
            )
        elif label == "Codex + GPT-6.1 Sol" and len(row.cells) >= 3:
            row.cells[2].text = (
                "mh-pilot-11 模型调用通过；mh-pilot-20 在 AO default 权限下成功写入只读探测标记，"
                "故 AO Chat 只读边界失败。AO CLI 当前没有 read-only 权限选项。"
            )
        elif label == "DeepSeek Harness" and len(row.cells) >= 3:
            row.cells[2].text = (
                "DSH 0.1.2-alpha.5；mh-pilot-21 编码 smoke 4/4 unittest；workspace-write 可越出任务文件范围，read-only 拒写/升级审批已验；"
                "kill 能停子进程并保留 dirty worktree；mh-pilot-18 clean session restore 后 idle、worktree clean。审计/任务文件权限仍待验。"
            )
        elif label == "Kimi Code" and len(row.cells) >= 3:
            row.cells[2].text = (
            "CLI 2.1.1；天翼 HTTP API 与 CLI 提示探针通过。AO Chat spawn 返回 CHAT_AUTH_REQUIRED；"
                "Kimi ACP 认证门只认 Kimi OAuth，未接受自定义 API Key；现有 TUI 会话结果未确认。"
            )
        elif label == "Windows DSH worker" and len(row.cells) >= 2:
            row.cells[1].text = (
                "天翼 ACP/AO Chat 模型探测通过；mh-pilot-21 隔离编码 smoke 的 unittest 4/4 通过，0 commit/0 push。"
                "mh-pilot-13 证明 workspace-write 可越出任务文件范围；"
                "read-only 写入拒绝、AO 升级审批、kill 停止子进程与 dirty worktree 保留已验证；"
                "mh-pilot-18 clean session restore 回到 idle，worktree clean；任务文件级权限与脱敏审计仍待测。"
            )
        elif label == "Windows Orchestrator" and len(row.cells) >= 2:
            row.cells[1].text = (
                "daemon health/ready 通过；ao CLI 位于桌面安装包 resources\\daemon\\ao.exe。"
                "Codex Chat 与 DSH Chat 模型调用有通过证据；mh-pilot-20 证明 Codex default 可写，只读边界失败。"
            )
        elif label == "首期 Harness 状态" and len(row.cells) >= 2:
            row.cells[1].text = (
                "Codex GPT-6.1 Sol AO Chat 通过，但 mh-pilot-20 证明 default 可写、只读边界失败；DSH 编码 smoke 4/4 通过，read-only 拒写、升级审批、取消、dirty worktree 保留与 clean session restore 已验，审计待验；"
                "Kimi 天翼 API/CLI 探针通过，但 AO Chat 被 Kimi ACP OAuth-only 检查阻断，TUI 未确认；ZCode 暂不纳入。"
            )

for paragraph in doc.paragraphs:
    text = paragraph.text
    if text.startswith("规划和评审角色必须只读。"):
        paragraph.text = (
            "规划和评审角色必须只读。AO 没有通用只读权限；mh-pilot-20 已证明 Codex AO Chat 在 default 权限下可写，故只读边界失败。"
            "AO 官方文档也说明实验性 reviewer 提示不隔离工具或网络；不把提示词视为权限控制。"
            "DSH 在 DSH_PERMISSION_MODE=read-only 下直接写入被拒，升级请求进入 AO pending 审批；"
            "worker permissions=default 已验证。workspace-write 仅限制 worktree，不限制任务文件范围。"
        )
    elif text.startswith("每次 API 调用记录"):
        paragraph.text = (
            "每次 API 调用预期记录请求模型 ID、返回模型标识、token 用量、耗时、状态码、角色、Harness 版本、"
            "task_id 和错误类别。AO SQLite 会话活动详情实测保留 MCP 工具输入（路径和写入内容）；"
            "AO 原生记录不等于脱敏审计，当前尚未满足不保存代码原文的要求。"
        )
    elif text.startswith("工具调用全量留审计事件"):
        paragraph.text = (
            "AO 会话历史会保存提示和部分工具输入；不得向会话输入密钥。要满足脱敏审计，需先核实 AO 保留策略，"
            "或评估单独的脱敏审计层。AO daemon 固定回环监听，禁止直接暴露远程端口。"
        )
    elif text.startswith("以 Windows AO 建立目标 Harness"):
        paragraph.text = (
            "保持真实原型项目未注册；继续使用 mh-pilot 核验目标 Harness、模型、权限与协议，"
            "并把 AO 目录授权和真实模型请求结果分别记录。"
        )
    elif text.startswith("DSH read-only 拒写、AO default 升级审批、kill 停止子进程"):
        paragraph.text = (
            "DSH mh-pilot-21 编码 smoke 4/4 unittest 通过；read-only 拒写、AO default 升级审批、kill、dirty worktree 保留和 mh-pilot-18 clean session restore 均有证据。"
            "restore 后会话 idle、worktree clean。mh-pilot-19 标记和 mh-pilot-21 文件保留作现场；继续核验审计字段和任务文件级写权限。"
        )
    elif text.startswith("GPT-6.1 Sol AO Chat 已通过"):
        paragraph.text = (
            "GPT-6.1 Sol AO Chat 已通过，但 mh-pilot-20 在 default 权限下写入只读探测标记，AO CLI 也没有 read-only 权限选项。"
            "当前不把 Codex 用于硬只读规划/终审；先确认 AO 支持的 sandbox 传递方式或评估受限适配器，不改全局 Codex 配置。"
        )
    elif text.startswith("Kimi Code 的天翼 HTTP API"):
        paragraph.text = (
            "Kimi 天翼 API/CLI 提示探针已通过；AO Chat spawn 因 Kimi ACP 只检查 OAuth 而返回 CHAT_AUTH_REQUIRED。"
            "继续在合成 worktree 验证 AO TUI 的 API-Key 路径，保留现有未完成会话。ZCode 暂不接入。"
        )
    elif text.startswith("Kimi Code 已预配天翼 OpenAI-compatible provider"):
        paragraph.text = (
            "Kimi 天翼 API/CLI 提示探针已通过；AO Chat spawn 的 CHAT_AUTH_REQUIRED 与 Kimi ACP OAuth-only 认证门相符。"
            "mh-pilot-17 AO TUI 仍 working/active，需从 Orchestrator 终端面板核验回复；保留 standalone-1。"
            "未核验前不把 Kimi 计入闭环，ZCode 暂不接入。"
        )
    elif text.startswith("M0 仍需验证 Codex 只读边界"):
        paragraph.text = (
            "M0 已确认 Codex AO Chat default 可写；需解决强制只读 sandbox 传递，再验 DSH session restore/审计/任务文件级权限和 Kimi AO 路径；"
            "真实原型项目尚未触碰。"
        )

latest_kimi_heading = "2026-10-12 Kimi ACP API-Key 认证阻断"
if not any(p.text == latest_kimi_heading for p in doc.paragraphs):
    doc.add_heading(latest_kimi_heading, level=1)
    for item in [
        "截图里的旧探针 HTTP 200 但 marker_match=False，不能算模型回答通过；更新后的隐藏输出 CLI 提示探针已返回标记并退出 0。没有显示 Key 或提示正文。",
        "Windows AO 对 Kimi Chat 的真实 spawn 返回 CHAT_AUTH_REQUIRED。AO agent catalog 显示 installed、authStatus=unknown；接口能力登记不等于会话认证成功。",
        "Kimi Code CLI 官方 ACP 代码的认证门检查 Kimi OAuth token；官方 PR #2185 记录了 API-Key 用户在 ACP 被强制 OAuth 登录的问题。该限制与 Tianyi API/CLI 探针成功、AO Chat 被拒绝相符。",
        "现有 standalone-1 Kimi TUI smoke 仍为 working，未取得可核验结果；不终止、不清理。新建 mh-pilot-17 当前为 working/active，AO CLI 无可核验终端回复，worktree 未改动，不计通过。",
        "下一步需从 AO TUI 会话界面核验输出，或等待上游修复 API-Key ACP auth gate 后重测 Chat；未取得可核验成功前不把 Kimi 计入闭环。",
    ]:
        doc.add_paragraph(item, style="List Bullet")

latest_cancel_heading = "2026-10-12 DSH 取消与 worktree 保留复测"
if not any(p.text == latest_cancel_heading for p in doc.paragraphs):
    doc.add_heading(latest_cancel_heading, level=1)
    for item in [
        "mh-pilot-18：45 秒无副作用的 Python sleep 子进程被 ao session kill 终止；因 worktree 干净，AO 自动回收目录，无任务改动丢失。",
        "mh-pilot-19：先创建单一未跟踪标记，再启动 sleep。kill 后 AO session 为 terminated，子进程归零；worktree 和标记仍存在，0 commit、0 push。标记留作验收证据，不清理。",
        "已验证 kill 停止 DSH 子进程，并保留有未提交改动的 worktree；干净 worktree 会安全回收。session restore、复杂失败恢复和任务文件级写权限仍未验收。规则见 https://docs.orchestrator.inc/plugins/workspaces/worktree/。",
    ]:
        doc.add_paragraph(item, style="List Bullet")

cancel_section_index = next(i for i, p in enumerate(doc.paragraphs) if p.text == latest_cancel_heading)
cancel_section_paragraphs = []
for paragraph in doc.paragraphs[cancel_section_index + 1:]:
    if paragraph.style.name.startswith("Heading"):
        break
    cancel_section_paragraphs.append(paragraph)
if cancel_section_paragraphs:
    cancel_section_paragraphs[0].text = (
        "mh-pilot-18：kill 终止 45 秒无副作用 sleep，clean worktree 回收；手动 restore 后同一 session 回到 idle，worktree clean、0 commit。"
        "mh-pilot-19：kill 停止子进程并保留 dirty worktree 与标记，0 commit/0 push；现场未清理。"
    )
    for paragraph in cancel_section_paragraphs[1:]:
        paragraph._element.getparent().remove(paragraph._element)

latest_codex_heading = "2026-10-12 Codex AO Chat 只读边界复测"
if not any(p.text == latest_codex_heading for p in doc.paragraphs):
    doc.add_heading(latest_codex_heading, level=1)
    for item in [
        "mh-pilot-20 用 Codex + GPT-6.1 Sol、permissions=default 执行限定写入探测，创建 CODEX_READONLY_PROBE.txt；Git 状态为一个未跟踪文件，0 commit、0 push。标记与 worktree 保留。",
        "ao project set-config --help 当前只列出 default、accept-edits、auto、bypass-permissions；ao spawn 没有会话级 sandbox 参数。AO 官方文档说明实验性 reviewer 模式/提示不隔离工具或网络。",
        "结论：AO Chat default 不是只读；Codex CLI 支持 read-only 不代表 AO 已传递该设置，故暂不分配硬只读角色。本轮 Kimi CLI 探针再次成功（tianyi/kimi-k3、退出 0）。mh-pilot-18 手动 restore 后 idle、worktree clean；密钥/提示未输出，真实项目未触碰，现场与 pending 审批均保留。",
    ]:
        doc.add_paragraph(item, style="List Bullet")

codex_section_index = next(i for i, p in enumerate(doc.paragraphs) if p.text == latest_codex_heading)
codex_section_paragraphs = []
for paragraph in doc.paragraphs[codex_section_index + 1:]:
    if paragraph.style.name.startswith("Heading"):
        break
    codex_section_paragraphs.append(paragraph)
codex_section_items = [
    "mh-pilot-20 用 Codex + GPT-6.1 Sol、permissions=default 执行限定写入探测，创建 CODEX_READONLY_PROBE.txt；Git 状态为一个未跟踪文件，0 commit、0 push。标记与 worktree 保留。",
    "ao project set-config --help 只列出 default、accept-edits、auto、bypass-permissions；ao spawn 没有会话级 sandbox 参数。AO 官方文档说明实验性 reviewer 提示不隔离工具或网络。",
    "结论：AO Chat default 不是只读；Codex CLI 支持 read-only 不代表 AO 已传递该设置，故暂不分配硬只读角色。本轮 Kimi CLI 探针再次成功（tianyi/kimi-k3、退出 0），密钥/提示未输出；真实项目未触碰，现场与历史审批均保留。",
]
for paragraph, item in zip(codex_section_paragraphs, codex_section_items):
    paragraph.text = item
for paragraph in codex_section_paragraphs[len(codex_section_items):]:
    paragraph._element.getparent().remove(paragraph._element)

doc.save(path)
print("docx_updated=true replacement_counts=" + ",".join(str(n) for n in counts))
