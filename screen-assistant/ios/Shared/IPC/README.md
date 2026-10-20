# Shared/IPC — SAReplyChannel（扩展 → 主App 无文件 reply 通道）

本目录只属于 multi-agent-build-41 worker。当前仅含 `SAReplyChannel.swift`（会被双 target 自动编译，无需改 project.yml）。README 与检查脚本会被 XcodeGen 当作资源收进 bundle，属无害噪音；如需排除，可在 project.yml 对应 target 的 sources 加 `excludes`（由 39/40 决定，本 worker 不改 project.yml）。

## 模块定位与边界

- 只承载 **reply 字符串**（扩展 OCR 后的短回答）从广播扩展传到主App。
- 载荷只经**内核 socket 缓冲**中转，**不落盘**；socket inode 是端点元数据，存于 App Group 容器（36 报告 §6.5 口径：元数据可存 AppGroup，正文不可文件化）。
- 单最新值不堆积：接收端单槽覆盖（`takeLatestReply()` 取走即清空）；发送端单槽 pending（新值覆盖旧值），有限重试后丢弃并置失败状态。
- 不写句子/回复文件；不做去重/推理；不承载画面帧；不做真实远端调用。

## 固定接口（供 39 接线）

```swift
final class SAReplyChannel {
    init(appGroupIdentifier: String)          // 约定签名（另有默认参数 socketFileName，测试用，可忽略）
    func startReceiving() throws              // 主App：启动后台监听
    func stop()                               // 停监听 + unlink端点 + 清内存
    func send(reply: String)                  // 扩展：有界异步发送，立即返回，不阻塞采集
    func takeLatestReply() -> String?         // 主App：非阻塞取最新并清空
    // 最短补充状态接口：
    var isListening: Bool { get }             // 监听运行状态
    var lastSendFailure: SAReplySendFailure? { get }  // 最近发送失败（含原因与时间）
    static var maxReplyBytes: Int { get }     // 单帧上限 16_384
}
```

错误与失败类型：`SAReplyChannelError`（startReceiving 抛出：容器不可用 / 路径超 sun_path 上限 / 已启动 / 已有活跃监听 / bind / listen 失败）；`SAReplySendFailureReason`（invalidPayload / payloadTooLarge / listenerUnreachable(attempts:) / connectionLost(detail:)）。

## 接线要点（每进程单实例）

- **主App**：创建一个实例（建议挂 App 生命周期单例），`try channel.startReceiving()`；PiP 更新处轮询 `takeLatestReply()`；退出/停采集时 `stop()`。可与现有 `DarwinSignal.newReplyAvailable` 组合（收到信号后 takeLatestReply）。
- **扩展**：创建自己的实例，OCR 得到 reply 后直接 `channel.send(reply: text)`。内部串行队列执行 connect+write（限时 0.5s/2s），固定 2s 间隔最多重试 3 次后丢弃并置 `lastSendFailure`；对齐 36 §6.7“App 不在运行即丢弃并在状态行显示未连接”。
- `Shared/ReplyTransport.swift` 的适配由 39 在其文件内完成（如 `ReplyTransportFactory.make()` 换成包一层 SAReplyChannel 的 adapter：`publish(reply:)` → `send(reply:)`，`consumeLatest()` → `takeLatestReply()`，App 侧负责 startReceiving/stop）。本 worker 不改该文件。
- 禁止在同一进程创建两个实例（第二个 `startReceiving()` 会报 `anotherListenerActive`）。

## 协议与实现要点

- 帧格式：4 字节大端长度 + UTF-8 载荷；0 长帧忽略。超限帧按协议错误断开。
- 端点：`<AppGroup容器>/sa.sock`（短文件名，为 sun_path 留余量）。路径 UTF-8 长度硬校验 ≤103（Darwin `sun_path`=104 含 NUL；Apple 页面另有 SOCK_MAXADDRLEN=255 上限）。
- 部分读写：非阻塞 fd + poll，读写均循环补齐；连接 10s 空闲超时回收 fd；`SO_NOSIGPIPE` 防 SIGPIPE。
- 启动清理：端点文件已存在时先 300ms 探测 connect——有活跃监听则拒绝启动（防覆盖绑定竞态），否则按崩溃残留清理后重绑。
- stop：cancel 全部事件源（cancel handler 关 fd）→ 等 teardown 完成（≤2s）→ queue 同步块 unlink 端点 + 清连接表 → 清接收槽。重启可直接 `startReceiving()`。
- 挂起语义：主App短暂挂起时，扩展的帧会停在内核缓冲/监听队列（进程存活即可送达，恢复后读出）；主App进程被杀则 connect 失败 → 有限重试 → 丢弃+失败状态。

## 复用决定（2026-10-20 核实）

1. **无成熟可直接依赖库**：kuttz/UnixDomainSocketSwift（设计最接近，iOS14+、长度前缀帧、重连策略）**无 LICENSE 文件 → 不可复制代码**，仅作设计参考；Medium 2023-08 实操文无明确授权，仅参考思路。故按 Apple/BSD 公开 API 自写最薄实现（本文件 ~560 行）。
2. **通道 = AppGroup 内 UDS**（一手核实 Apple《App Groups entitlement》现行页：组内 IPC 官方机制明列 UNIX domain sockets，无 OS 限定，iOS 3.0+；socket 须在组容器内、名字受 SOCK_MAXADDRLEN 限制）。
3. **实现层 = raw AF_UNIX**（Darwin/BSD 公开系统 API）而非 Network.framework：`NWEndpoint.unix(path:)` 官方存在（iOS 12+，已核实），但监听端路径绑定、陈旧端点清理、部分读写控制在 NW 下不透明；raw AF_UNIX 语义完全可控、便于审计“载荷不落盘”。
4. **未采用**：mmap 文件邮箱（落盘，违反 PRD）；POSIX shm（本任务只传短文本无需求，且 36 §6.7 指出其真机证据缺口，不研究）；loopback TCP（第三备选，仅 UDS 真机失败再评）。

## 验证状态（如实标注）

- **本模块未编译、未真机验证**（Windows 无 Xcode/Swift）。交付的是可接入源码 + 本地 git 提交，不声称通道已跑通。
- **Mac 可复现检查**：`bash check_sa_reply_channel.sh`（需 Xcode CLT）。脚本做静态审计（无文件写入调用）+ 8 组行为检查：基本往返、单槽覆盖、UTF-8/emoji、超限/空串拒发、逐字节部分写、stop 清理与重启、有限重试失败状态、残留端点自动清理。macOS 与 iOS 的 AF_UNIX 行为一致度高，但**不能替代 iOS 真机冒烟**。
- **iOS 15 真机小切片步骤**（待有 Mac 时）：① 两个 target 打进真机，主App启 `startReceiving()`；② 扩展侧 `send(reply:)` 一次；③ 主App轮询 `takeLatestReply()` 断言收到；④ 观察 `lastSendFailure`/`isListening` 状态行；⑤ 反复 start/stop/杀进程验证端点清理与重连。
- **已知限制**：iOS 模拟器组容器路径常超 sun_path 103 字节，全链路模拟器验证可能报 `socketPathTooLong`（真机路径约 90 字节可用）；真机冒烟失败时按 36 §6.7 评估 loopback TCP 后备（接口不变）。
