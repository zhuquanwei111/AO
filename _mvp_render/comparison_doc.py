from docx import Document
from pathlib import Path
p=Path(r'C:\Users\admin\Documents\ChatGPT\多智能体搭建\多智能体协同开发系统搭建方案_MVP版.docx')
d=Document(p)
for para in d.paragraphs:
    if para.text.startswith('A｜接受 DeepSeek 公共 API 路由：'):
        para.text='A｜接受 DeepSeek 公共 API 路由。质量：按实测返回的 V4 Pro/Flash评估，官方宣传与本项目编码结果不等价；成本：若按 Flash 计费有望最低，最终看供应商账单；难度：低，优先复用现有 Provider 配置思路；风险：服务端映射、质量和计费不透明；进度：三案中最快。接受 Flash 即继续，记录响应标识，不自动切换。'
    elif para.text.startswith('B｜首期编码全部用 GPT-6.1 Sol：'):
        para.text='B｜首期编码全部用 GPT-6.1 Sol。质量：固定单一型号便于建立基线，实际编码质量仍需试点验证；成本：预计高于按 Flash 计费的 A，供应商报价为准；难度：低至中，新增隔离的 GPT 编码 Worker/凭据并经网关授予受限写权限；风险：调整“GPT 只读”原规则，且仍需验证供应商模型映射；进度：接入通过后较可预测。规划/终审 Worker 仍只读。'
    elif para.text.startswith('C｜改用 Kimi K3 编码：'):
        para.text='C｜改用 Kimi K3 编码。质量：官方定位包含长程编码/软件工程，但本仓库效果未知；成本：按第三方报价核算，不能以官方直连价代替；难度：中，须完成独立映射、凭据、工具兼容与任务验收；风险：模型映射和质量尚无本项目数据；进度：通常慢于 A/B，M0/M3 需多一轮验证。[9]'
    elif para.text.startswith('M0 前置核验（策略拍板后）'):
        para.text='M0 前置核验（策略拍板后）：对模型矩阵内每个模型做连通性、请求 ID/响应 model/供应商实际服务标识核验，并记录 token、耗时、状态码；验证供应商限流与超时行为，确认每阶段最多 2 次重试、鉴权失败不重试、失败阻断且不切模型。A 接受实测返回 V4.1 Flash；B 验证 GPT 编码 Worker 独立凭据与网关写权限；C 验证 Kimi K3 映射。截图显示 ZCode 的天翼 Provider 使用 https://ai.ctaigw.cn/v1、OpenAI Chat Completions，目录含 deepseek-v4-pro-0813、deepseek-v4-flash-vision-exp-0817、kimi-k3、glm-5.3、glm-5.3-flash；这只证明 ZCode 可选该目录，不证明 OpenCode 兼容、实际底层版本或账单，须在 OpenCode 单独核验。另确认仓库与云端模型调用已获授权。未通过则不进入 M1。'
d.save(p)
print('saved',p,'chars=',sum(len(x.text) for x in d.paragraphs)+sum(len(c.text) for t in d.tables for r in t.rows for c in r.cells))
