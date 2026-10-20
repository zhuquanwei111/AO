import UIKit
import Darwin
import Foundation

/// 真机通道冒烟验证切片 #2：Unix Domain Socket 流式通道（BSD socket 实现）。
///
/// 依据（R/36 三轮口径更正）：官方 App Groups entitlement 页认可 UDS；
/// socket 文件须在 App Group 容器内，sun_path 受 ~104 字节限制（路径要短）；
/// 内核中转不落盘。无成熟 MIT 参考实现（kuttz/UnixDomainSocketSwift 无
/// LICENSE），按 36 建议自写。
///
/// 本视图在本 App 进程内：bind（容器内短路径）→ listen → connect →
/// send → recv 校验 → 清理，逐项显示结果。跨进程（扩展→App）验证在
/// shm 探针切片覆盖同一容器能力后按需补充。
final class UDSSmokeTestView: UIView {

    private let label = UILabel()
    private var log: [String] = []

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .systemBackground
        label.numberOfLines = 0
        label.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        label.translatesAutoresizingMaskIntoConstraints = false
        addSubview(label)
        NSLayoutConstraint.activate([
            label.topAnchor.constraint(equalTo: safeAreaLayoutGuide.topAnchor, constant: 12),
            label.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 12),
            label.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12)
        ])
        runTest()
    }

    required init?(coder: NSCoder) { fatalError() }

    private func append(_ line: String) {
        log.append(line)
        DispatchQueue.main.async { self.label.text = self.log.joined(separator: "\n") }
    }

    private func runTest() {
        DispatchQueue.global(qos: .userInitiated).async {
            guard let container = FileManager.default
                .containerURL(forSecurityApplicationGroupIdentifier: AppGroup.identifier) else {
                self.append("FAIL App Group 容器不可用（entitlement 未生效？）")
                return
            }
            // 路径要短：sun_path ~104 字节限制（R/36）
            let path = container.appendingPathComponent("s.s").path
            try? FileManager.default.removeItem(atPath: path)

            // 1) 服务端 socket + bind（容器内路径）
            let serverFd = socket(AF_UNIX, SOCK_STREAM, 0)
            guard serverFd >= 0 else {
                self.append("FAIL socket errno=\(errno)"); return
            }
            var addr = sockaddr_un()
            addr.sun_family = sa_family_t(AF_UNIX)
            let pathBytes = path.data(using: .utf8)!
            pathBytes.withUnsafeBytes { raw in
                withUnsafeMutableBytes(of: &addr.sun_path) { dest in
                    dest.copyBytes(from: raw.prefix(dest.count - 1))
                }
            }
            let bindRes = withUnsafePointer(to: &addr) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    bind(serverFd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            guard bindRes == 0 else {
                self.append("FAIL bind errno=\(errno) (\(String(cString: strerror(errno))))")
                close(serverFd); return
            }
            self.append("PASS bind（App Group 容器内 unix socket）")

            guard listen(serverFd, 1) == 0 else {
                self.append("FAIL listen errno=\(errno)"); close(serverFd); return
            }
            self.append("PASS listen")

            // 2) 客户端连接（同进程模拟发送侧；内核中转，不落盘）
            let clientFd = socket(AF_UNIX, SOCK_STREAM, 0)
            guard clientFd >= 0 else {
                self.append("FAIL client socket"); close(serverFd); return
            }
            let connectRes = withUnsafePointer(to: &addr) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    connect(clientFd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            guard connectRes == 0 else {
                self.append("FAIL connect errno=\(errno) (\(String(cString: strerror(errno))))")
                close(clientFd); close(serverFd); return
            }
            self.append("PASS connect（UDS 内核中转）")

            // 3) accept + send + recv 校验
            var acceptAddr = sockaddr_un()
            var acceptLen = socklen_t(MemoryLayout<sockaddr_un>.size)
            let acceptFd = withUnsafeMutablePointer(to: &acceptAddr) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    accept(serverFd, $0, &acceptLen)
                }
            }
            guard acceptFd >= 0 else {
                self.append("FAIL accept errno=\(errno)"); close(clientFd); close(serverFd); return
            }

            let payload = Data("SMOKE-UDS-OK".utf8)
            let sent = payload.withUnsafeBytes { raw in
                send(clientFd, raw.baseAddress, raw.count, 0)
            }
            self.append(sent == payload.count ? "PASS send \(sent)B" : "FAIL send")

            var buf = [UInt8](repeating: 0, count: 64)
            let n = recv(acceptFd, &buf, buf.count, 0)
            let received = Data(buf.prefix(max(n, 0)))
            let ok = received == payload
            self.append((ok ? "PASS" : "FAIL")
                + " 接收校验: \(String(decoding: received, as: UTF8.self))")

            close(acceptFd); close(clientFd); close(serverFd)
            try? FileManager.default.removeItem(atPath: path)
            if ok {
                self.append("=> UDS 流式通道冒烟通过；帧通道可行性以本结果+shm 探针为准")
            }
        }
    }
}
