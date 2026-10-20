import Foundation

/// Darwin 通知观察者（CFRunLoop 集成）。
/// 在主线程注册，回调转投主队列；析构时移除观察。
final class DarwinObserver {
    private let name: String
    private let handler: () -> Void
    private var registered = false

    init(name: String, handler: @escaping () -> Void) {
        self.name = name
        self.handler = handler
        let opaque = Unmanaged.passUnretained(self).toOpaque()
        CFNotificationCenterAddObserver(
            CFNotificationCenterGetDarwinNotifyCenter(),
            opaque,
            { _, observer, _, _, _ in
                guard let observer else { return }
                let token = Unmanaged<DarwinObserver>.fromOpaque(observer).takeUnretainedValue()
                DispatchQueue.main.async { token.handler() }
            },
            name as CFString,
            nil,
            .deliverImmediately
        )
        registered = true
    }

    deinit {
        if registered {
            CFNotificationCenterRemoveObserver(
                CFNotificationCenterGetDarwinNotifyCenter(),
                Unmanaged.passUnretained(self).toOpaque(),
                nil, nil
            )
        }
    }
}
