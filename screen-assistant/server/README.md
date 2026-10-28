# 屏识AI助手 — 远端单轮回复服务（screen-assistant/server）

Android 客户端把 OCR 得到的**单条中文句子**发到本服务；本服务用真实 Redis 做原子去重，
新句调用天翼 OpenAI 兼容模型（默认 `glm-5.3-flash`）做单轮短回答。不做去重之外的
agent/RAG/历史上下文/个性化；不持久化句子或回复日志。

## 固定 HTTP 契约

| 请求 | 情形 | 响应 |
|---|---|---|
| `POST /v1/answer` | 新句，模型回答成功 | `200` `{"status":"ok","reply":"<非空字符串>"}` |
| `POST /v1/answer` | 规范化后重复的句子（24h TTL 内） | `204` 无响应体（客户端保留当前展示） |
| `POST /v1/answer` | 空白 / 缺字段 / 非字符串 / 额外字段 / 超限 | `422` |
| `POST /v1/answer` | Redis 不可用 / 模型凭据缺失 / 模型失败 | `503` |
| `POST /v1/answer` | 模型请求超时 | `504` |
| `GET /healthz` | 健康状态 | `200`，含 redis/model 配置状态，**不含密钥** |

请求体只含一个字段：`{"sentence": "<OCR完整句子>"}`，`Content-Type: application/json`。

服务端行为：

- 规范化：去除首尾空白/换行后以**完整规范化句子**判重（SHA-256 摘要进 key，不截断前缀）。
- 去重：`SET key <随机token> NX EX 86400` 单命令原子完成（O(1)），并发同句恰好一个请求
  获胜，其余得 204；重复句**不调用模型、不更新回复**。
- 释放：模型失败（503/504）时，用官方 compare-and-delete Lua 脚本（值等于本请求 token 才
  `DEL`）释放本次占位，**不会误删其他请求的 key**，句子之后可重试。
- Redis 连接失败不降级为进程内去重，直接 503。
- 无有效模型凭据（`TIANYI_API_KEY` 为空）时 503，**不返回任何假回复**。
- 模型调用请求显式设置 `reasoning_effort=low`（thinking 仍开启）；日志仅记录 opaque request ID、请求总耗时、
  模型耗时、固定错误码、上游 HTTP 状态、`finish_reason`、输出/推理 token 数及未知字段标记；
  不打印句子、回复、密钥或完整请求/响应体。`/v1/answer` 的所有响应带 opaque `X-Request-ID`，
  可用于把客户端状态与服务端日志关联；无句子/回复持久化。

## 环境变量（模板见 `.env.example`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `TIANYI_API_KEY` | （必填） | 天翼 OpenAI 兼容路由凭据；缺失时 `/v1/answer` 503 |
| `TIANYI_BASE_URL` | `https://ai.ctaigw.cn/v1` | 天翼 OpenAI 兼容端点 |
| `TIANYI_MODEL` | `glm-5.3-flash` | 模型名 |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis 连接（compose 内为 `redis://redis:6379/0`） |
| `DEDUP_TTL_SECONDS` | `86400` | 去重 TTL（执行计划固定 24h） |
| `MAX_SENTENCE_CHARS` | `2000` | 规范化后句子长度上限，超出 422 |
| `MODEL_TIMEOUT_SECONDS` | `30` | 模型请求总超时（超时映射 504） |
| `MODEL_CONNECT_TIMEOUT_SECONDS` | `5` | 模型连接超时 |
| `LIVE_FUSION_CONCURRENCY` | `2` | 每个服务进程内普通融合执行上限，允许 1–16 |
| `LIVE_FUSION_QUEUE_LIMIT` | `16` | 每个服务进程内等待分析的不同场次上限，允许 1–256 |

实时观察已保存并安排分析时返回 `status=accepted`、`reason=scheduled`、`prompt_id=null`；队列满而观察已保存时 `reason=analysis_busy`，本次不安排分析。结果仍通过 `/prompts` 获取，`scheduled` 不表示已生成提示。上述上限是进程内保护值，不是性能保证；进程退出后未完成的内存调度不会自动恢复。

## 启动路径

### 非 Docker（Windows 本机已验证）

```powershell
cd screen-assistant/server
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:TIANYI_API_KEY = "<真实key>"        # 本机已有该用户级变量则可省略

# Redis（二选一；本机预装的原生 6379 实例当前不可用，见下方「重要」）
# 方式A：专用临时实例（推荐，随用随起，不落盘）
redis-server --port 6390 --save '' --appendonly no
$env:REDIS_URL = "redis://127.0.0.1:6390/0"
# 方式B：直接 docker compose up -d --build（自带 redis:8，无需本机 Redis）

.\.venv\Scripts\python.exe -m uvicorn screen_assistant_server.app:app --host 127.0.0.1 --port 8000
```

> **重要（R 调研结论修正）**：本机预装的原生 Redis 8.10.1（6379，`D:\develop\software\Redis\...`
> Windows-x64-msys2 版）当前处于 **MISCONF** 状态：`rdb_last_bgsave_status:err`、工作目录为
> msys2 路径 `/redis/...` 无法落盘、`stop-writes-on-bgsave-error=yes`，导致**所有写命令被拒**
> （读命令正常，`redis-cli ping` 仍返回 PONG）。本服务对其写入会按契约返回 503（正确行为，
> 不降级）。修复需该共享实例自身处理（如修复落盘目录或 `CONFIG SET stop-writes-on-bgsave-error no`），
> 本任务不做系统级配置改动；集成请用上方方式A/B。

### Docker（本机已验证构建与启动）

```powershell
cd screen-assistant/server
$env:TIANYI_API_KEY = "<真实key>"        # 或写入同目录 .env（已被 .gitignore 忽略）
docker compose up -d --build             # 启动 redis:8 + 服务，服务端口 8000
docker compose down -v                   # 停止收容
```

镜像也可单独构建：`docker build -t screen-assistant-server:local .`
（运行时注入 `TIANYI_API_KEY` 与 `REDIS_URL`。）

### 健康检查

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/healthz
# {"status":"ok","redis":"ok","model_key_configured":true,"model":"glm-5.3-flash","dedup_ttl_seconds":86400}
# status=degraded 时按字段排查：redis 不可达或 TIANYI_API_KEY 未配置
```

## 安卓客户端接线（A owner 参考）

- Base URL：`http://<host>:8000`；每轮 OCR 一句，`POST /v1/answer`，body 只含 `sentence`。
- 客户端**不做任何去重**（不按文本、不做本地缓存判断），去重由服务端负责。
- 每轮最多一个在途请求：上一请求未返回则本轮跳过，不排队。
- `200`：取 `reply` 更新悬浮条；`204`：保留当前展示不动；`422`/`503`/`504`：本轮丢弃，
  不弹窗，下一轮继续；网络失败同样本轮丢弃。
- 停止采集后不得把旧响应回填悬浮条（只展示最新运行会话的结果）。

## 真机（iOS）局域网接线（2026-10-21 增补）

模拟器/本机用 loopback（见上文）；真实 iPhone 需局域网地址，由**宿主侧 uvicorn 进程**提供
（Docker Desktop WSL2 后端在本机无法向 LAN IP 发布端口——显式 IP 发布实测无监听；且 19180
被 Docker 的内核级端口占位排除其他绑定，故真机端口用 **19280**）：

| 目标 | 地址 | 提供者 |
|---|---|---|
| 模拟器/本机 | `http://127.0.0.1:19180`、`http://127.0.0.1:8000` | Docker compose（loopback-only 发布） |
| 真机（同 Wi-Fi） | `http://<宿主机当前IP>:19280`（2026-10-21 实测 `http://192.168.0.4:19280`） | 宿主 uvicorn，PID 见下文启动命令 |

宿主真机实例启动（与 loopback 服务共用同一专用 Redis，去重状态一致）：

```powershell
cd screen-assistant/server
# 前置：docker compose -f docker-compose.local.yml up -d   （其中含 127.0.0.1:16390 专用Redis）
$env:TIANYI_API_KEY = "<真实key>"
$env:REDIS_URL = "redis://127.0.0.1:16390/0"
# IP 每次启动前先核对：Get-NetIPAddress -AddressFamily IPv4 | ? IPAddress -like '192.168.*'
.\.venv\Scripts\python.exe -m uvicorn screen_assistant_server.app:app --host 192.168.0.4 --port 19280
```

**已验证（host 侧）**：`netstat` 显示 `192.168.0.4:19280 LISTENING`；该地址自连
`GET /healthz` → 200 `{"status":"ok","redis":"ok","model_key_configured":true,...}`。
**未真机验证**：iPhone 实际可达性未测，不得当作已通。

真机可达条件：

1. 手机与宿主机同一 Wi-Fi 网段（当前 192.168.0.x）；路由器未开 AP/客户端隔离。
2. 防火墙：**无需改动**——监听进程映像为 `...\Python313\python.exe`，已存在
   `python.exe` Inbound Allow（Public profile，RemoteAddress=Any）规则。若实测仍不通，
   最小排查步骤（管理员 PowerShell，不关闭防火墙）：
   `New-NetFirewallRule -DisplayName "screen-assistant 19280" -Direction Inbound -Protocol TCP -LocalPort 19280 -RemoteAddress 192.168.0.0/24 -Action Allow`
3. **IP 为 DHCP 且已发生一次变更（.2→.4）**：每次真机联调前先核对宿主 IP；建议在路由器
   为本机设 DHCP 保留。
4. iOS 侧注意：ATS 默认禁明文 HTTP，Info.plist 需 `NSAppTransportSecurity →
   NSAllowsLocalNetworking`；iOS 14+ 首次连接会弹「本地网络」权限，需
   `NSLocalNetworkUsageDescription`。
5. 本服务无鉴权，仅限可信个人局域网；不做路由器公网转发、不部署公网。

## 验证证据（2026-10-20，本机）

**真实 Redis（已验证）**：本机 Redis 8.10.1（测试实例 6390 端口）。
`pytest tests` 8/8 通过：200→重复204（不重复调用模型）、422 各无效输入、模型失败释放占位
且可重试、超时 504 且释放、无凭据 503、Redis 不可用 503（两次，确认无进程内降级）、
healthz ok/degraded、10 并发同句恰好 1×200 + 9×204（真实 SET NX 原子性）。

**真实模型接线（已验证）**：服务以真实 `TIANYI_API_KEY` 启动，真实 Redis，单条极短句子：
`POST /v1/answer` → `200 {"status":"ok","reply":"1+1=2"}`（天翼 glm-5.3-flash，短中文回答）；
同句再发 → `204` 空体（未再调用模型）；空白句 → `422`；`GET /healthz` → `200 status=ok`。
共计 1 次真实付费调用（最小必要验证，未压测）。凭据有效性另经免费 `GET /models` 核对
（模型列表含 `glm-5.3-flash`）。服务日志仅含状态码，无句子/密钥。

**Docker 路径（已验证）**：`docker build` 成功（python:3.13-slim，252MB）；`docker compose
config` 通过；`compose up` 后容器内 `healthz` 200 ok（内部 redis + 真实凭据注入）；空 key
容器 `POST /v1/answer` → 503（无凭据路径，未调用模型）；验证后已 `compose down -v` 收容。

**默认 6379 路径复核（2026-10-20 补充）**：R 调研称原生 Redis 6379 就绪，实测发现其处于
MISCONF（详见上方「重要」），写命令全拒、服务按契约 503；只读取证：
`CONFIG GET dir` → `/redis/Redis-8.10.1-Windows-x64-msys2`、`rdb_last_bgsave_status:err`。
契约测试与真实模型验证均使用专用临时实例（6390，`--save ''`），该路径可用，未受影响。

**未验证 / 不在范围**：公网部署与 HTTPS、多实例部署、30 分钟长稳与 OCR 端到端延迟（属
客户端+集成验收）、Redis 主从/哨兵等高可用拓扑；模型侧速率限制与配额行为未做压测。

## 已知限制

- 模型失败且此刻 Redis 恰好不可用 → 占位无法立即释放，该句在 TTL 内会得到 204（保留旧
  展示）；TTL 过期后自动恢复可重试。占位值是随机 token，不影响其他句子。
- 去重 key 为句子 SHA-256 摘要：完整覆盖规范化句子，原文不入 key、不入日志。
- 原 `/v1/answer` 无鉴权（原执行计划范围：个人自用）；新 `/v1/live` 按场次令牌隔离。
  两者均未作公网部署验收，请勿直接暴露公网。

## M2：本地 fixture 公屏→提示→事件记录

本节为后端增量，前面的真实模型/Docker记录是旧问答链的历史验证，不能作为 M2
或抖音原生开播验收。M2 **只接显式测试观察**，没有抖音采集适配器、iOS收件器、ASR、
流量或完整合规能力；当前用确定性自然短句模板，不调用真实模型，不增加依赖。

复用 FastAPI/Pydantic、`deque/Counter`、`asyncio` 和 `sqlite3`。
`LIVE_DB_PATH` 默认 `data/live.sqlite3`（相对 server 工作目录），可设为可写的持久路径；
测试使用临时目录。数据库在线程中延迟打开，异常返回运营接口503，不阻断旧问答生命周期。
只支持单进程，不使用多 worker；容器卷挂载/部署未验证，临时容器目录不构成持久化保证。

| 接口 | 输入/查询 | 结果 |
|---|---|---|
| `POST /v1/live/sessions` | 可无请求体，或 `{"description":"本场选填说明"}` | 201：`session_id, started_at_ms, session_token` |
| `POST /v1/live/sessions/{id}/comments` | 下述观察对象 | 200：`status, counted, reason, prompt_id, received_at_ms, effective_at_ms, time_basis` |
| `GET /v1/live/sessions/{id}/prompts` | `after` 默认空；`wait_ms` 默认0、范围0—15000 | `prompts, next_cursor, stopped, session_id` |
| `POST /v1/live/sessions/{id}/stop` | 无请求体；重复调用幂等 | `session_id, stopped_at_ms` |
| `GET /v1/live/sessions/{id}/events` | `after` 默认空；`limit` 默认50、范围1—100 | `events, next_cursor, has_more, session_id, started_at_ms, stopped_at_ms` |

除创建外，均需 `X-Live-Session-Token: <创建返回的令牌>`；只存 SHA-256 摘要，
令牌仅创建时返回，调用方应妥善保存且不得写日志。不存在/不匹配均403；同幂等ID内容
冲突、停止后输入409；无效参数/时刻/cursor422；达到容量上限429；存储不可用503。
旧 `/healthz` 仍检查旧问答依赖，不代表运营数据库状态。

评论示例（**人工构造 fixture，ID 不是实际平台数据**）：

```json
{
  "event_id": "fixture-observation-1",
  "input_mode": "test_fixture",
  "source": "fixture:chat",
  "coverage": "structured_sample",
  "reliability": "known",
  "text": "主播你是哪里人？",
  "platform_message_id": "fixture-message-1",
  "author_id": "fixture-author-a"
}
```

`event_id/source/coverage/text/input_mode` 必填；`author_id/platform_message_id/observed_at_ms`
可选；`reliability` 缺省 `unknown`。coverage 只允许 `structured_sample/ocr_visible_sample/unknown`，
不是全量保证；source 只是标签，不证明授权。M2交付时只接受 `test_fixture`；M3新增的实际观察
模式见下文。fixture不得改名冒充真实接入。可靠性未知或来源证据不足时会明确不计数。

第二条换独立ID与作者、正文 `你来自哪里？`，短窗口内可得到：

```json
{"status":"accepted","counted":true,"reason":"generated","prompt_id":"<不透明ID>",
 "received_at_ms":1790000001000,"effective_at_ms":1790000001000,"time_basis":"received"}
```

展示短句为 `公屏反复问“来自哪里”，可以集中答一下。`。无作者但有两个不同消息ID也可
触发该句，不声称“多位”。同 event_id 完全重投不再次计数；相同 source+平台消息ID不重复；
只有作者时，同作者同主题在窗口内最多计一次。不要把同一实际评论同时从两个 source 喂入
并宣称跨源去重。小范围家乡/开播时间问句有显式同义归一化；其他明确问句仅完整文本重复，
不做泛化语义聚类，不删除否定词/数字。不确定则不合并，普通陈述保持安静。

时间全部为 UTC 毫秒；HTTP 入口捕获 `received_at_ms`，缺 `observed_at_ms` 时采用该时刻且
`time_basis=received`。源观察时刻须处于开场至接收之间，接收不能晚于处理时刻；
负数、未来、开场前和倒置时刻拒绝。`observed` 仍只是调用方观察时间，不是平台事件时间。
事件包含 `start_at_ms/end_at_ms`、相对开场的 `start_offset_ms/end_offset_ms`、
`first_received_at_ms/last_received_at_ms`；点事件起止相同，混合时间依据标 `mixed`。

每条事件分别保留 `phenomenon/suggestion/display_text` 与样本来源、观察条数；
`generated_at_ms/expires_at_ms/returned_at_ms/delivery_state` 区分生成和服务器返回。
`returned_to_client` 仅表示已用于服务器响应，**不是手机已收到或PiP已显示的确认**。
不持久化整场原始公屏，只存事件摘要及幂等元数据。重启保留记录/幂等/冷却，但原始窗口
不持久化，重新积累；SQLite重开后仍在有效期的提示可读取。

cursor 绑定场次且对应递增记录顺序，客户端当作不透明字符串，原样回传 `next_cursor`，
不要解析、加一、按字典序比较或跨场复用。提示和复盘各自保存续读位置；未到期提示在
相同 after 下可重读，调用方按 prompt_id 幂等展示。过期仅从提示收件过滤，复盘记录保留；
停止后提示接口立即返回 `stopped=true` 且无提示，评论拒绝，复盘仍可查询。

可解释的首版默认：窗口30秒、观察最多晚到10秒、重复阈值2条、同主题冷却60秒、
提示有效15秒；窗口最多200条、待返回提示16条、单场事件1000条/幂等元数据10000条。
冷却内的额外重复不生成新事件，已生成事件保留当时的窗口快照，不把后续全部评论记入复盘。
长轮询最多15秒，单场4/全进程16等待者，活跃场次最多8；等待不持数据库锁，取消释放名额。
这些是保守资源边界，不是多房间能力或原PRD性能承诺；达到限制需停场/新建或后续明确方案。

隔离验证（在 server 工作目录，需本机 PATH 中有 `redis-server`）：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_live.py -q
.venv/Scripts/python.exe tests/run_isolated.py
```

第一条只用临时SQLite/ASGI，不连接Redis或真实模型；第二条启动随机临时目录/端口的
loopback Redis，核对目录身份后为旧测试提供 `TEST_REDIS_URL`，结束关闭所属进程树。
它不使用旧Redis或Docker，不开启HTTP服务。旧测试的默认地址保持不变，所有断言保留。
本地验证不替代iOS接线、Apple构建、真机后台/PiP或抖音实播验证。

## M3：iOS 运营观察接线（本地实现）

M3 在 M2 fixture 契约上增加 `input_mode=live_observation`，不改变旧 `/v1/answer`。
ReplayKit OCR 保留逐行文本框：iOS 将裁剪ROI内的 Vision 框映射为全屏左上原点归一化
`text_position`，并提交 `source=ios_replaykit_ocr` 与 `observed_at_ms`。运营分支只挑
首条符合明确问题特征的单行候选；普通数字、一般提示或无关界面文字不作为评论内容，未匹配行
不发送到模型。当前 OCR API 不提供识别置信度，因此 `confidence` 保持 `null`。没有作者/平台
消息ID时，按来源、规范化问句和10秒观察时间桶做重放抑制，不推断昵称归属、多人或全量覆盖；
bbox仅表示文本位置，不能证明昵称与评论配对。

客户端把当前场次 ID、能力令牌和开场时间放在 App Group 容器的受文件保护 JSON 状态中，
仅以 UserDefaults 布尔值发布是否启用；场次结束后移除授权文件并清除启用标志。

单条符合当前明确问句规则的实时样本会进入独立运营提示路径：服务端使用已有
`TIANYI_BASE_URL`、`TIANYI_API_KEY`、`TIANYI_MODEL` 和模型超时配置，通过同一 OpenAI
兼容 `/chat/completions` 传输发独立系统提示；评论以不可信资料传入。模型失败、超时、截断、
空输出或超长输出不生成提示。fixture 继续走既有重复问题确定性模板，不调用模型。人物
`head_down` 由 iOS Vision 观察器在连续有效姿态满足窗口后触发轻模板，不调用模型。

新增 `POST /v1/live/sessions/{id}/signals` 只接受有界 `head_down` 信号及 source、confidence、
sample_count、开始/观察时间；按场次令牌授权，并设置90秒冷却。黑帧/无可读姿态只重置连续窗口，
不转换为离镜结论。iOS 独立长轮询 `/prompts`，实际收到的短句送现有 PiP renderer；stop 后读取
`/events` 分页列表。服务端 `returned_to_client` 仍不是 PiP 显示确认。

服务端测试的实时模型响应使用受控 fake；没有读取实际 `.env`、key，也没有发真实业务模型请求。
本机没有 Apple 工具链，ReplayKit 与 Vision 代码未编译/安装/真机运行。M3 的“实时观察”仅指
客户端代码已接入 ReplayKit 当前交付帧中的 OCR/Vision；它不证明抖音原生同机开播时 ReplayKit
可持续收到未遮挡评论或主播画面，也不证明平台未阻止或限制采集。主播侧在线人数与平台提醒有
明确的屏幕候选来源，但本切片没有独立区域和实屏校准：不解析人数、不按评论关键词声称官方警告，
也不把热度/累计观看当在线人数。语音转写和 ASR 未接入。后续沿现有选区编辑器增加可选在线人数
区域与平台提醒区域，须由用户在真实主播界面选择核对后才启用对应解析/提醒。
## M6 可选音频适配（默认关闭）

在现有场次内 `POST /v1/live/sessions/{id}/audio`，沿用
`X-Live-Session-Token`。入口不启动新 listener；仅接受16kHz、单声道、s16le
PCM的Base64 JSON，每包<=600ms、正文<=64KiB，完整契约见 `AudioPacket`
（`screen_assistant_server/audio.py`）。必须携带音源、采集代次、包序号、原格式及逐块
原始PTS/帧数/序号；可选近似墙钟锚点。没有锚点不伪造说话UTC时刻。
`final=true`可处理尾窗；空PCM结束包必须同时使用空`spans`，且属于当前采集代次。

现有server启动环境追加以下变量；本功能不自动安装框架、下载权重或启动ASR服务：

| 配置 | 值 |
|---|---|
| `AUDIO_ENABLED` | `1`，未设置默认关闭 |
| `FUNASR_WS_URL` | 已准备好的本机官方服务，如 `ws://127.0.0.1:10095`，只允许loopback |
| `FUNASR_MODE` | 默认`online`；`2pass`须先准备并核准其额外模型 |
| `SILERO_ONNX_PATH` | 已批准、已存在的Silero ONNX绝对路径 |

基础依赖沿用`requirements.txt`，音频分析运行时另见`requirements-audio.txt`。
Silero使用官方`OnnxWrapper/get_speech_timestamps`；FunASR使用官方JSON+binary PCM协议，
协商`binary`子协议，以`is_speaking=false,is_end=true`结束，并要求
`is_end=true,is_final=true`确认。只返回单句final、缺少全段ACK的旧服务会失败，不冒充完成。
官方参考见[客户端](https://github.com/modelscope/FunASR/blob/main/runtime/python/websocket/funasr_wss_client.py)
与[协议](https://github.com/modelscope/FunASR/blob/main/runtime/docs/websocket_protocol_zh.md)。
不要启用第三方服务的录音/离线片段落盘选项；本适配不保存原始PCM。

每轨最多6秒待分析PCM和一个分析作业；最多16轨，空闲15秒清除，过载切断连续性。
VAD按短窗处理，边界可能切词，转写段时间来自VAD区间，不是词级对齐；不会补造缺块。
仅确认主播、VAD有效、具备同源连续覆盖和近似时钟映射的最终结果进入M4节奏规则。
VAD不是音乐/说话人分类器；来源、音乐或混声不明时撤回主播确认或关闭音频。
真实ASR/VAD引擎和iOS采集仍须运行验证；测试fixture不等于识别验收。
