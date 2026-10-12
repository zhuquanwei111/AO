from docx import Document
from pathlib import Path
p=Path(r'C:\Users\admin\Documents\ChatGPT\多智能体搭建\多智能体协同开发系统搭建方案_MVP版.docx')
d=Document(p)
reps={
'版本：MVP 1.2（模型策略待拍板）    日期：2026-10-11    目标：在授权代码仓库验证六阶段交付闭环':'版本：MVP 1.3（模型策略 A 已确认）    日期：2026-10-11    目标：在授权代码仓库验证六阶段交付闭环',
'首期只支持 spec → plan → build → test → review → ship。build 编码模型在 A/B/C 中拍板；当前推荐 B（GPT-6.1 Sol），待用户确认。规划/架构/终审 GPT Worker 保持只读，若选 B，另设独立凭据和受网关约束的 GPT 编码 Worker。失败保留任务现场，不自动修复、不自动全盘回滚。':'首期只支持 spec → plan → build → test → review → ship。用户已确认选 A：build 使用 DeepSeek 第三方公共 API，接受供应商服务端实际路由（包括 V4.1 Flash），记录返回标识；客户端不自动切模。GPT-6.1 Sol 负责规划、架构和只读终审。失败保留现场，不自动修复、不自动全盘回滚。',
'DeepSeek 路由不是已确认的首期阻断项：官方 9 月 10 日公告称 deepseek-v4-pro 请求会路由至 V4.1-Flash；但更新日志又称 9 月 14 日后继续提供 V4 Pro API，当前价格页列出 V4-Pro-0813。两组官方资料存在冲突，不能据其推断第三方供应商实际返回；M0 对所选供应商实测请求 ID、response.model、实际服务/计费标识。无法证明底层版本时标记“供应商未验证”，由所选策略决定是否接受，不得静默切换。[7][8]':'DeepSeek 官方资料对 V4 Pro 路由/继续提供服务存在冲突：9 月 10 日公告称 deepseek-v4-pro 请求会路由至 V4.1-Flash；更新日志及当前价格表又称继续提供 V4 Pro API、列出 V4-Pro-0813。[7][8] 用户已选 A，接受供应商服务端路由；M0 仍记录请求 ID、response.model、token、状态码、耗时和账单。若 response.model 只是请求别名回显，标记“底层版本未验证”，不将其误报为真实权重证明。',
'模型策略拍板项（推荐 B，待用户确认）':'模型策略拍板项（用户已确认 A）',
'推荐 B 作为最稳妥的流程 MVP：首期固定一个编码模型建立可比较的质量基线，减少 DeepSeek/Kimi 供应商映射和质量波动对首轮验收的影响；但 GPT 编码能力与实际返回版本仍须 M0/M3 验证，不能预设质量不降。代价是成本可能上升并需调整 GPT 编码权限。若优先控制成本且愿意接受 Flash，A 是改动最小的备选；不建议首期同时接入多模型。30% 降本仍是待验证假设。':'决策结果：首期执行 A，不同时接入 B/C。其主要优势是改动和成本预期较小；质量由 10 个试点任务验证。若 M3 未达到交付成功门槛，阶段失败阻断并提交人工决策，不自动切换 GPT 或 Kimi。30% 降本仍是待验证假设。',
'模型策略：A 接受 DeepSeek 公共 API 服务端实际路由并继续，记录请求 ID 和供应商返回标识；B build 使用 GPT-6.1 Sol；C build 使用 Kimi K3。最终仅启用拍板组合，不做客户端自动路由、自动降级或切换。记录 response.model、usage、耗时和状态码；该字段可能只是请求别名回显，标记“已验证/仅回显/未知”，不可据此宣称底层版本已证实。API 连续失败后阶段阻断。':'模型策略：A 已确认，build 请求 DeepSeek 公共 API ID，接受供应商服务端路由（含 V4.1 Flash）；记录 response.model、usage、耗时和状态码。若 response.model 仅回显请求别名，标记“底层版本未验证”。客户端关闭自动路由、降级和切模；API 连续失败后阶段阻断。',
'模型身份与路由是 M0 风险核验项。DeepSeek 官方公告与更新日志/当前价格页对 V4 Pro 是否路由、是否继续提供服务的信息不一致；第三方供应商也可能另行映射。[7][8] 请求模型 ID、response.model 与实际底层服务不必相同。若选 A，按约定接受实际返回的 Flash 并记录；若供应商无法提供实际标识，标注“仅供应商声明/未知”，由用户决定是否接受。B/C 同样核验各自映射。任何策略都不得自动换模型。':'模型身份与路由按已确认的 A 处理：供应商可将请求路由到 Flash，MVP 接受该行为并记录供应商响应与账单；API response.model 若是别名回显，标记底层版本未验证，不因此自动阻断或切模型。若 API 没有响应模型标识、token 用量、状态码等必要审计字段，M0 不通过；任何 API 故障均阶段失败阻断。',
'规划/终审 GPT-6.1 Sol 只读；build 选 A DeepSeek、B GPT-6.1 Sol、C Kimi K3（待拍板）':'规划/终审 GPT-6.1 Sol 只读；build 固定 DeepSeek 公共 API，接受服务端路由（A 已确认）',
'规划/终审 GPT 只读；build 按 A/B/C 固定模型；B 时 GPT 编码 Worker 与只读 Worker 分离；不共享 Key':'规划/终审 GPT 只读；build 固定 DeepSeek 公共 API；GPT 与 DeepSeek Worker/Key 按角色隔离',
'拍板模型 A/B/C':'DeepSeek 公共 API（A）',
'按批准计划逐切片改动，仅写任务 worktree；记录请求 ID、response.model、token、耗时、状态码。A 接受实际路由；失败阻断，不自动切模。':'按批准计划逐切片改动，仅写任务 worktree；记录请求 ID、response.model、token、耗时、状态码。接受供应商服务端路由，失败阻断，不自动切模。',
'基线：同一 30 个匹配任务全部使用 GPT-6.1 Sol；MVP 组使用拍板后的 A/B/C 组合。逐任务记录请求模型 ID、返回标识、token、耗时及第三方实际账单，比较每个验收通过任务成本并报告质量差异。':'基线：同一 30 个匹配任务全部使用 GPT-6.1 Sol；MVP 组使用 GPT 规划/终审 + DeepSeek build（A）。逐任务记录请求模型 ID、返回标识、token、耗时及第三方实际账单，比较每个验收通过任务成本并报告质量差异。'
}
for para in d.paragraphs:
    if para.text in reps: para.text=reps[para.text]
for t in d.tables:
    for row in t.rows:
        for c in row.cells:
            if c.text in reps: c.text=reps[c.text]
# Single-cell replacements in model scope, worker and build rows.
for t in d.tables:
    for row in t.rows:
        if len(row.cells)==3 and row.cells[0].text=='模型':
            row.cells[1].text='规划/终审 GPT-6.1 Sol 只读；build 固定 DeepSeek 公共 API，接受服务端路由（A 已确认）'
        if len(row.cells)==3 and row.cells[0].text=='模型 Worker':
            row.cells[2].text='规划/终审 GPT 只读；build 固定 DeepSeek 公共 API；GPT 与 DeepSeek Worker/Key 按角色隔离'
        if len(row.cells)==3 and row.cells[0].text=='build':
            row.cells[1].text='DeepSeek 第三方公共 API（A）'
            row.cells[2].text='按批准计划逐切片改动，仅写任务 worktree；记录请求 ID、response.model、token、耗时、状态码。接受供应商服务端路由，失败阻断，不自动切模。'
# Update recommendation heading if it remains in normal paragraphs or table content.
d.save(p)
print('saved',p,'chars=',sum(len(x.text) for x in d.paragraphs)+sum(len(c.text) for t in d.tables for r in t.rows for c in r.cells))
