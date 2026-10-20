import Foundation

// ============================================================================
// SAReplyChannel —— 屏识AI助手 扩展 → 主App 无文件 reply 传输通道
//
// 职责边界（主控 2026-10-20 定向）：
//   - 只承载 reply 字符串；metadata 走内存/控制配置；不写句子/回复文件；
//     不做去重/推理；不承载画面帧。
//   - 载荷只经过内核 socket 缓冲中转，不落盘。socket inode 是端点元数据，
//     存放在 App Group 容器内（36 报告 §6.5 口径允许：元数据可存 AppGroup，
//     正文不可文件化）。
//
// 通道选择依据（2026-10-20 一手核实）：
//   1) Apple《App Groups entitlement》官方页明文：组内 App 可用 IPC 机制
//      包含 "UNIX domain sockets"，无 OS 限定（iOS 3.0+）；socket 路径须在
//      App Group 容器内，socket 名受 SOCK_MAXADDRLEN 限制。
//      https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.application-groups
//   2) Darwin 侧 sockaddr_un.sun_path 为 104 字节（含 NUL），比
//      SOCK_MAXADDRLEN(255) 更紧，本模块按 103 字节（路径 UTF-8 长度）硬校验。
//   3) 实现层用 raw AF_UNIX（Darwin/BSD 公开系统 API），不用 Network.framework：
//      NWEndpoint.unix(path:) 官方存在（iOS 12+），但监听端路径绑定、陈旧端点
//      清理与部分读写的控制语义不透明；raw AF_UNIX 完全可控、便于审计。
//      127.0.0.1 loopback TCP 保留为后备（36 §6.7：证据薄，仅 UDS 真机失败再评）。
//   4) 无可直接依赖的成熟库：kuttz/UnixDomainSocketSwift 无 LICENSE 文件
//      （36 已核实）→ 不可复制代码，仅作设计参考；Medium 实操文仅参考思路。
//      本文件为按官方 API 自写的最薄实现。
//
// 协议：4 字节大端长度前缀 + UTF-8 载荷（上限 maxReplyBytes 字节）。
// 语义：单最新值不堆积 —— 接收端单槽覆盖（takeLatestReply 取走即清空），
//       发送端单槽 pending（新值覆盖旧值），有限重试后丢弃并置失败状态。
//
// 使用约束：
//   - 每进程只创建一个实例。主 App 实例调用 startReceiving()/stop()/
//     takeLatestReply()；广播扩展实例只调用 send()。
//   - 不要在 receiverQueue 上调用 stop()/startReceiving()（有断言保护）。
// ============================================================================

/// 发送失败原因（最短运行状态接口）。
public enum SAReplySendFailureReason: Equatable {
    /// 空 reply（上游“空OCR跳过”约定外的防御）。
    case invalidPayload
    /// 超过单帧上限。
    case payloadTooLarge(limit: Int, actual: Int)
    /// 连续重试耗尽仍未连上主 App（对齐 36 §6.7“未连接即丢弃”口径）。
    case listenerUnreachable(attempts: Int)
    /// 已连上但写入失败/写超时。
    case connectionLost(detail: String)
}

/// 最近一次发送失败（含时间），send 成功后清空。
public struct SAReplySendFailure: Equatable {
    public let reason: SAReplySendFailureReason
    public let at: Date
}

/// startReceiving() 失败原因。
public enum SAReplyChannelError: Error, Equatable {
    /// App Group 容器不可用（entitlement 未配置/标识不符）。
    case appGroupContainerUnavailable(appGroupIdentifier: String)
    /// socket 路径超 sun_path 上限（模拟器组容器路径常超限，见 README）。
    case socketPathTooLong(limit: Int, actual: Int)
    /// 本实例已在监听。
    case alreadyStarted
    /// 组容器内已有活跃监听（拒绝覆盖绑定，避免双监听竞态）。
    case anotherListenerActive(path: String)
    case bindFailed(detail: String)
    case listenFailed(detail: String)
}

public final class SAReplyChannel {

    // MARK: - 常量（内存控制配置）

    /// 单帧载荷上限（短回复足够；超限即拒发/拒收）。
    public static let maxReplyBytes = 16_384
    /// Darwin sockaddr_un.sun_path = 104 字节含 NUL → 路径最长 103 字节。
    static let sunPathLimit = 103
    /// socket 端点文件名（短名，为组容器路径留余量）。
    static let defaultSocketFileName = "sa.sock"
    /// 固定间隔重试（36 §6.7 推荐口径）。
    static let sendRetryDelay: TimeInterval = 2.0
    /// 每 pending 载荷的有限重试次数，耗尽即丢弃并置失败状态（有界）。
    static let maxSendAttempts = 3
    static let connectTimeoutMs: Int32 = 500
    static let writeTimeout: TimeInterval = 2.0
    /// 已接受连接的空闲超时（对端异常不关连接时的 fd 保护）。
    static let connIdleTimeout: TimeInterval = 10.0
    static let listenBacklog = 16

    // MARK: - 状态

    private let stateLock = NSLock()
    private let receiverQueue = DispatchQueue(label: "com.screenassistant.ios.SAReplyChannel.receiver", qos: .userInitiated)
    private let senderQueue = DispatchQueue(label: "com.screenassistant.ios.SAReplyChannel.sender", qos: .utility)
    /// 记录所有由本实例创建的 fd 的关闭完成情况；stop/重启用它保证 close 先于重绑。
    private let teardownGroup = DispatchGroup()

    private let appGroupIdentifier: String
    private let socketFileName: String
    /// 仅宿主/脚本测试用：显式目录替代 App Group 容器。
    private let testDirectory: URL?

    /// socket 端点完整路径（锁定保护；懒解析）。
    private var socketPath: String?
    /// 端点已建立（bind 成功）且未 teardown。
    private var endpointActive = false
    /// 主App侧监听运行状态。
    public private(set) var isListening = false
    /// 接收槽：单最新值，取走即清空（锁定保护）。
    private var latestReply: String?

    private var listenerSource: DispatchSourceRead?
    /// 全部事件源（监听 + 已接受连接），stop 时统一 cancel（锁定保护）。
    private var allSources: [DispatchSourceRead] = []
    /// 连接状态表（仅 receiverQueue 访问）。
    private var connStates: [Int32: ConnState] = [:]
    private var nextConnGeneration = 0

    // 发送侧（扩展进程；pending 单槽，锁定保护）
    private var pendingPayload: [UInt8]?
    private var pendingGeneration = 0
    private var pendingAttempts = 0
    private var sendInFlight = false
    /// 最近一次发送失败；send 成功后清空。
    public private(set) var lastSendFailure: SAReplySendFailure?

    private final class ConnState {
        var buffer: [UInt8] = []
        let generation: Int
        var source: DispatchSourceRead?
        init(generation: Int) { self.generation = generation }
    }

    // MARK: - 初始化

    /// 约定接口：`SAReplyChannel(appGroupIdentifier:)`。
    /// socketFileName 仅为宿主测试/极端路径长度场景保留，默认即约定行为。
    public init(appGroupIdentifier: String, socketFileName: String = SAReplyChannel.defaultSocketFileName) {
        self.appGroupIdentifier = appGroupIdentifier
        self.socketFileName = socketFileName
        self.testDirectory = nil
    }

    /// 内部：显式目录（等价 App Group 容器），供 macOS 可复现检查脚本使用。
    internal init(directory: URL, socketFileName: String) {
        self.appGroupIdentifier = ""
        self.socketFileName = socketFileName
        self.testDirectory = directory
    }

    deinit {
        // 实例由持有方在常规线程释放；禁止在 receiverQueue 上释放（见 stop 断言）。
        stop()
    }

    // MARK: - 主App侧：监听

    /// 主App启动后台监听（bind/listen 组容器内端点；接受连接 → 单槽存最新 reply）。
    public func startReceiving() throws {
        dispatchPrecondition(condition: .notOnQueue(receiverQueue))
        // 等待上一次 teardown 的 close 全部完成，避免 fd 号复用竞态。
        _ = teardownGroup.wait(timeout: .now() + 2.0)

        let active = locked { endpointActive }
        if active { throw SAReplyChannelError.alreadyStarted }

        // 1) 解析端点路径（App Group 容器内）
        let dirURL: URL
        if let d = testDirectory {
            dirURL = d
        } else if let c = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroupIdentifier) {
            dirURL = c
        } else {
            throw SAReplyChannelError.appGroupContainerUnavailable(appGroupIdentifier: appGroupIdentifier)
        }
        let path = dirURL.appendingPathComponent(socketFileName).path
        let pathBytes = Array(path.utf8)
        guard pathBytes.count <= Self.sunPathLimit else {
            throw SAReplyChannelError.socketPathTooLong(limit: Self.sunPathLimit, actual: pathBytes.count)
        }

        // 2) 建立监听 socket
        let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else {
            throw SAReplyChannelError.bindFailed(detail: "socket: \(Self.errStr())")
        }
        var owned = true
        defer { if owned { Darwin.close(fd) } }
        Self.configureSocket(fd: fd, nonBlocking: true)

        // 3) 端点文件已存在：先探测是否有活跃监听，避免覆盖绑定竞态；否则清残留
        if FileManager.default.fileExists(atPath: path) {
            if Self.probeListener(pathBytes: pathBytes) {
                throw SAReplyChannelError.anotherListenerActive(path: path)
            }
            try? FileManager.default.removeItem(atPath: path)
        }

        // 4) bind + listen
        let bindResult = Self.bindUnix(fd: fd, pathBytes: pathBytes)
        guard bindResult == 0 else {
            throw SAReplyChannelError.bindFailed(detail: "bind: \(Self.errStr())")
        }
        guard Darwin.listen(fd, Self.listenBacklog) == 0 else {
            throw SAReplyChannelError.listenFailed(detail: "listen: \(Self.errStr())")
        }

        // 5) 注册读事件源（挂起态注册完再 resume，消除 stop 竞态窗口）
        teardownGroup.enter()
        let group = teardownGroup
        let src = DispatchSource.makeReadSource(fileDescriptor: fd, queue: receiverQueue)
        src.setEventHandler { [weak self] in
            self?.acceptReadyConnections(on: fd)
        }
        src.setCancelHandler {
            Darwin.close(fd)
            group.leave()
        }
        locked {
            endpointActive = true
            socketPath = path
            isListening = true
            listenerSource = src
            allSources.append(src)
        }
        owned = false // fd 生命周期交给 cancel handler
        src.resume()
    }

    /// 停止监听并清理端点与内存（取消事件源、关闭 fd、unlink 端点文件、清空接收槽）。
    public func stop() {
        dispatchPrecondition(condition: .notOnQueue(receiverQueue))
        let (sources, path, wasActive) = locked { (allSources, socketPath, endpointActive) }
        locked {
            endpointActive = false
            isListening = false
            listenerSource = nil
            allSources = []
        }
        guard wasActive else { return }

        sources.forEach { $0.cancel() } // cancel handler 在 receiverQueue 上关闭 fd
        _ = teardownGroup.wait(timeout: .now() + 2.0)
        receiverQueue.sync {
            // 兜底：取消快照之后仍残留的连接源
            for st in connStates.values { st.source?.cancel() }
            connStates.removeAll()
            if let p = path {
                try? FileManager.default.removeItem(atPath: p)
            }
        }
        locked { latestReply = nil } // stop 即清内存，重启不回填旧值
    }

    /// 主App侧非阻塞取最新 reply 并清空；无新值返回 nil。
    @discardableResult
    public func takeLatestReply() -> String? {
        locked {
            let v = latestReply
            latestReply = nil
            return v
        }
    }

    // MARK: - 扩展侧：发送

    /// 扩展侧有界异步发送（立即返回，不阻塞采集线程）。
    /// 单槽 pending：新 reply 覆盖未发出的旧 reply；有限重试后丢弃并置失败状态。
    public func send(reply: String) {
        let bytes = Array(reply.utf8)
        guard !bytes.isEmpty else {
            recordSendFailure(.invalidPayload)
            return
        }
        guard bytes.count <= Self.maxReplyBytes else {
            recordSendFailure(.payloadTooLarge(limit: Self.maxReplyBytes, actual: bytes.count))
            return
        }
        let shouldStart = locked {
            pendingPayload = bytes
            pendingGeneration &+= 1
            pendingAttempts = 0
            lastSendFailure = nil
            let start = !sendInFlight
            sendInFlight = true
            return start
        }
        if shouldStart {
            senderQueue.async { self.flushPending() }
        }
    }

    private func recordSendFailure(_ reason: SAReplySendFailureReason) {
        locked { lastSendFailure = SAReplySendFailure(reason: reason, at: Date()) }
    }

    private func flushPending() {
        let (payload, gen, attempt): ([UInt8]?, Int, Int) = locked {
            guard let p = pendingPayload else {
                sendInFlight = false
                return (nil, 0, 0)
            }
            pendingAttempts += 1
            return (p, pendingGeneration, pendingAttempts)
        }
        guard let payload = payload else { return }

        let outcome: TransmitOutcome
        if let path = resolveSocketPath() {
            outcome = Self.transmit(payload: payload, socketPath: path)
        } else {
            outcome = .connectFailed
        }

        locked {
            guard pendingGeneration == gen else {
                // 发送期间有新 reply 进槽：转去发最新值（不堆积）
                senderQueue.async { self.flushPending() }
                return
            }
            switch outcome {
            case .success:
                pendingPayload = nil
                lastSendFailure = nil
                sendInFlight = false
            case .connectFailed:
                if attempt >= Self.maxSendAttempts {
                    pendingPayload = nil
                    lastSendFailure = SAReplySendFailure(reason: .listenerUnreachable(attempts: attempt), at: Date())
                    sendInFlight = false
                } else {
                    senderQueue.asyncAfter(.now() + Self.sendRetryDelay) { self.flushPending() }
                }
            case .writeFailed(let detail):
                if attempt >= Self.maxSendAttempts {
                    pendingPayload = nil
                    lastSendFailure = SAReplySendFailure(reason: .connectionLost(detail: detail), at: Date())
                    sendInFlight = false
                } else {
                    senderQueue.asyncAfter(.now() + Self.sendRetryDelay) { self.flushPending() }
                }
            }
        }
    }

    /// 懒解析端点路径（发送侧）。
    private func resolveSocketPath() -> String? {
        if let p = locked({ socketPath }) { return p }
        let dirURL: URL
        if let d = testDirectory {
            dirURL = d
        } else if let c = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroupIdentifier) {
            dirURL = c
        } else {
            return nil
        }
        let p = dirURL.appendingPathComponent(socketFileName).path
        locked {
            if socketPath == nil { socketPath = p }
        }
        return p
    }

    // MARK: - 接收端内部（仅 receiverQueue）

    private func acceptReadyConnections(on listenFd: Int32) {
        while true {
            let cfd = Darwin.accept(listenFd, nil, nil)
            if cfd < 0 {
                if errno == EINTR { continue }
                return // EWOULDBLOCK：本轮无更多连接
            }
            Self.configureSocket(fd: cfd, nonBlocking: true)
            let generation = nextConnGeneration
            nextConnGeneration += 1
            let state = ConnState(generation: generation)

            teardownGroup.enter()
            let group = teardownGroup
            let src = DispatchSource.makeReadSource(fileDescriptor: cfd, queue: receiverQueue)
            src.setEventHandler { [weak self] in
                self?.drainConnection(cfd, state: state)
            }
            src.setCancelHandler {
                Darwin.close(cfd)
                group.leave()
            }
            connStates[cfd] = state
            state.source = src
            locked { allSources.append(src) }
            src.resume()

            // 空闲超时保护：对端异常不关连接时回收 fd
            receiverQueue.asyncAfter(deadline: .now() + Self.connIdleTimeout) { [weak self] in
                self?.closeIfIdle(cfd, generation: generation)
            }
        }
    }

    private func drainConnection(_ fd: Int32, state: ConnState) {
        guard connStates[fd]?.generation == state.generation else { return }
        var chunk = [UInt8](repeating: 0, count: 16_384)
        while true {
            let n = chunk.withUnsafeMutableBytes { raw -> Int in
                Darwin.read(fd, raw.baseAddress, raw.count)
            }
            if n > 0 {
                state.buffer.append(contentsOf: chunk[0..<n])
                if !ingestFrames(connFd: fd, state: state) { return } // 协议错误已关连接
                if n < chunk.count { return } // 本轮可读数据大概率取尽
            } else if n == 0 {
                finishConnection(fd, generation: state.generation) // 对端关闭（EOF）
                return
            } else {
                if errno == EINTR { continue }
                if errno != EWOULDBLOCK {
                    finishConnection(fd, generation: state.generation)
                }
                return
            }
        }
    }

    /// 增量帧解析。返回 false 表示协议错误（已关闭连接）。
    private func ingestFrames(connFd: Int32, state: ConnState) -> Bool {
        while true {
            let buf = state.buffer
            if buf.count < 4 { return true }
            let len = (Int(buf[0]) << 24) | (Int(buf[1]) << 16) | (Int(buf[2]) << 8) | Int(buf[3])
            guard len <= Self.maxReplyBytes else {
                finishConnection(connFd, generation: state.generation)
                return false
            }
            guard buf.count >= 4 + len else { return true } // 等待更多数据（部分读）
            let payload = Array(buf[4..<(4 + len)])
            state.buffer.removeFirst(4 + len)
            guard len > 0 else { continue } // 0 长帧忽略（心跳位，不携带内容）
            // UTF-8 严格解码；坏帧丢弃不做推理
            if let text = String(data: Data(payload), encoding: .utf8) {
                locked { latestReply = text } // 单槽覆盖：单最新值不堆积
            }
        }
    }

    private func finishConnection(_ fd: Int32, generation: Int) {
        guard let st = connStates[fd], st.generation == generation else { return }
        connStates[fd] = nil
        let src = st.source
        st.source = nil
        src?.cancel() // cancel handler 关闭 fd
        locked { allSources.removeAll { $0 === src } }
    }

    private func closeIfIdle(_ fd: Int32, generation: Int) {
        if connStates[fd]?.generation == generation {
            finishConnection(fd, generation: generation)
        }
    }

    // MARK: - POSIX 辅助

    private func locked<T>(_ body: () throws -> T) rethrows -> T {
        stateLock.lock()
        defer { stateLock.unlock() }
        return try body()
    }

    private static func errStr() -> String {
        String(cString: strerror(errno))
    }

    private static func configureSocket(fd: Int32, nonBlocking: Bool) {
        var one: Int32 = 1
        _ = setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout<Int32>.size))
        if nonBlocking {
            let flags = fcntl(fd, F_GETFL, 0)
            if flags >= 0 {
                _ = fcntl(fd, F_SETFL, flags | O_NONBLOCK)
            }
        }
    }

    private enum TransmitOutcome {
        case success
        case connectFailed
        case writeFailed(String)
    }

    /// 阻塞式 connect + 写一帧（非阻塞 fd + poll 限时；循环处理部分写）。
    /// 仅在 senderQueue 上调用。载荷只进内核 socket 缓冲，不落盘。
    private static func transmit(payload: [UInt8], socketPath: String) -> TransmitOutcome {
        var frame: [UInt8] = [
            UInt8((payload.count >> 24) & 0xFF),
            UInt8((payload.count >> 16) & 0xFF),
            UInt8((payload.count >> 8) & 0xFF),
            UInt8(payload.count & 0xFF),
        ]
        frame.append(contentsOf: payload)
        let pathBytes = Array(socketPath.utf8)
        guard pathBytes.count <= sunPathLimit else { return .connectFailed }

        let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return .connectFailed }
        defer { Darwin.close(fd) }
        configureSocket(fd: fd, nonBlocking: true)

        guard connectUnix(fd: fd, pathBytes: pathBytes, timeoutMs: connectTimeoutMs) else {
            return .connectFailed
        }

        var sent = 0
        let deadline = Date().addingTimeInterval(writeTimeout)
        while sent < frame.count {
            let remainMs = Int32(max(0, deadline.timeIntervalSinceNow * 1000))
            if remainMs <= 0 { return .writeFailed("write timeout") }
            var pfd = pollfd(fd: fd, events: Int16(POLLOUT), revents: 0)
            let r = Darwin.poll(&pfd, 1, remainMs)
            if r <= 0 { return .writeFailed("poll: \(errStr())") }
            let n = frame.withUnsafeBytes { raw -> Int in
                Darwin.write(fd, raw.baseAddress!.advanced(by: sent), frame.count - sent)
            }
            if n > 0 {
                sent += n
            } else if n < 0 {
                if errno == EINTR { continue }
                return .writeFailed(errStr())
            }
        }
        return .success
    }

    /// 非阻塞 connect（AF_UNIX）+ poll 限时确认。
    private static func connectUnix(fd: Int32, pathBytes: [UInt8], timeoutMs: Int32) -> Bool {
        var connected = false
        pathBytes.withUnsafeBufferPointer { bytes in
            var addr = sockaddr_un()
            addr.sun_family = sa_family_t(AF_UNIX)
            addr.sun_len = UInt8(2 + bytes.count)
            withUnsafeMutableBytes(of: &addr.sun_path) { $0.copyBytes(from: bytes) }
            let cr = withUnsafePointer(to: &addr) { p in
                p.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                    Darwin.connect(fd, sa, socklen_t(2 + bytes.count))
                }
            }
            if cr == 0 {
                connected = true
                return
            }
            guard errno == EINPROGRESS else { return }
            var pfd = pollfd(fd: fd, events: Int16(POLLOUT), revents: 0)
            if Darwin.poll(&pfd, 1, timeoutMs) > 0 {
                var err: Int32 = 0
                var l = socklen_t(MemoryLayout<Int32>.size)
                getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &l)
                connected = (err == 0)
            }
        }
        return connected
    }

    /// bind 到 AF_UNIX 路径；成功返回 0。
    private static func bindUnix(fd: Int32, pathBytes: [UInt8]) -> Int32 {
        pathBytes.withUnsafeBufferPointer { bytes -> Int32 in
            var addr = sockaddr_un()
            addr.sun_family = sa_family_t(AF_UNIX)
            addr.sun_len = UInt8(2 + bytes.count)
            withUnsafeMutableBytes(of: &addr.sun_path) { $0.copyBytes(from: bytes) }
            return withUnsafePointer(to: &addr) { p in
                p.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                    Darwin.bind(fd, sa, socklen_t(2 + bytes.count))
                }
            }
        }
    }

    /// 探测路径上是否已有活跃监听（快速 connect 探测）。
    private static func probeListener(pathBytes: [UInt8]) -> Bool {
        guard pathBytes.count <= sunPathLimit else { return false }
        let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        defer { Darwin.close(fd) }
        configureSocket(fd: fd, nonBlocking: true)
        return connectUnix(fd: fd, pathBytes: pathBytes, timeoutMs: 300)
    }
}
