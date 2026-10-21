import UIKit
import Darwin
import Foundation

/// 真机通道冒烟验证切片 #1：shm_open POSIX 共享内存（App Group 命名）。
///
/// 依据（R/36 三轮口径更正）：
/// - 官方 App Groups entitlement 页明列 POSIX shared memory（shm_open，
///   标识格式 `<group id>/<name>`）为认可 IPC；
/// - Apple iPhoneOS shm_open man 页明文 iOS 实现 "no visible entry in the
///   file system" = 真无文件后备（与 mmap 文件环本质不同）。
/// 待真机验证点（R/36）：SO 11589105 称真机可用，但 2014 年有 ENOENT 前科；
/// mode 参数必填；名字须以 group id 开头。
///
/// 本视图在本 App 进程内执行：写→读→校验→unlink，并逐项显示结果。
/// 真正的跨进程验证（扩展↔App）需运行 SmokeChannelsBroadcastApp；本切片
/// 先验证 shm_open 在目标机的进程内可用性与命名合法性。
final class ShmSmokeTestView: UIView {

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
            // Apple 规范：标识以 group id 开头（POSIX 名去前导斜杠后接 group 名）
            let name = "/\(AppGroup.identifier)/smoke"
            let bytes = 64

            // 1) shm_open（O_CREAT|O_RDWR，mode 必填 0600）
            let fd = shm_open(name, O_CREAT | O_RDWR, mode_t(0o600))
            if fd < 0 {
                self.append("FAIL shm_open errno=\(errno) (\(String(cString: strerror(errno))))")
                self.append("=> shm 通道在本机不可用；备选 UDS（见 UDS 冒烟切片）")
                return
            }
            self.append("PASS shm_open fd=\(fd)")

            // 2) ftruncate + mmap MAP_SHARED
            var ok = ftruncate(fd, off_t(bytes)) == 0
            self.append((ok ? "PASS" : "FAIL") + " ftruncate \(bytes)B")
            guard ok else { close(fd); return }

            guard let map = mmap(nil, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0),
                  map != MAP_FAILED else {
                self.append("FAIL mmap errno=\(errno)")
                close(fd); shm_unlink(name)
                return
            }
            self.append("PASS mmap MAP_SHARED")

            // 3) 写入测试数据（进程内往返）
            let payload = Data("SMOKE-SHM-OK".utf8)
            payload.withUnsafeBytes { raw in
                memcpy(map, raw.baseAddress, raw.count)
            }

            let readBack = Data(bytes: map, count: payload.count)
            ok = readBack == payload
            self.append((ok ? "PASS" : "FAIL") + " 写入→读回校验 \(String(decoding: readBack, as: UTF8.self))")

            // 4) 清理
            munmap(map, bytes)
            shm_unlink(name)
            close(fd)
            self.append("PASS munmap/shm_unlink/close")
            self.append("=> 进程内 shm_open 冒烟通过；跨进程（扩展↔App）请运行广播冒烟切片")
        }
    }
}
