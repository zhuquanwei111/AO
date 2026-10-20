import UIKit
import Darwin

/// 通道冒烟切片 #3：跨进程 shm（广播扩展写 → 主 App 读）。
/// 随主 App 的广播扩展一同编译；真机上开启广播后本视图每 2s 轮询共享内存
/// 序列号，显示扩展写入是否到达主 App 进程。
final class ShmCrossProcessProbeView: UIView {

    private let label = UILabel()
    private var timer: Timer?
    private var lastSeq: Int = -1
    private var updates = 0

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
        label.text = "等待广播扩展写入…（请先启动广播）"
    }

    required init?(coder: NSCoder) { fatalError() }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        if window != nil {
            start()
        } else {
            stop()
        }
    }

    private func start() {
        stop()
        let t = Timer(timeInterval: 2, repeats: true) { [weak self] _ in
            self?.poll()
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    private func stop() {
        timer?.invalidate()
        timer = nil
    }

    /// 布局与 ShmSmokeTestView 一致：前 8 字节 = 序列号（Int64 little-endian）。
    private func poll() {
        DispatchQueue.global(qos: .userInitiated).async {
            let name = "/\(AppGroup.identifier)/xproc"
            let fd = shm_open(name, O_RDWR, mode_t(0o600))
            guard fd >= 0 else { return } // 扩展尚未写入
            defer { close(fd) }
            guard let map = mmap(nil, 64, PROT_READ, MAP_SHARED, fd, 0),
                  map != MAP_FAILED else { return }
            defer { munmap(map, 64) }

            let seq = map.assumingMemoryBound(to: Int64.self).pointee
            DispatchQueue.main.async {
                guard seq != self.lastSeq else { return }
                self.lastSeq = seq
                self.updates += 1
                self.label.text =
                    "扩展→App shm 跨进程通道 OK\n" +
                    "最新序列号: \(seq)\n" +
                    "更新次数: \(self.updates)\n" +
                    "（广播扩展每轮写入 +1）"
            }
        }
    }
}
