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
- 模型故障日志仅记录阶段、固定错误码、上游 HTTP 状态及必要的 `finish_reason`/空内容标记；
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
- 服务无鉴权（执行计划范围：个人自用、无鉴权扩展）；请勿直接暴露公网。
