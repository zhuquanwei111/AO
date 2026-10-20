import SwiftUI
import Combine
import UserNotifications

@main
struct ScreenAssistantApp: App {
    @StateObject private var appModel = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(appModel)
        }
    }
}

/// 全局可观察状态：配置读写（App Group）+ 运行状态。
final class AppModel: ObservableObject {
    @Published var serverURLString: String {
        didSet { SharedConfig.setServerBaseURL(serverURLString) }
    }
    @Published var intervalSeconds: Double {
        didSet { SharedConfig.setIntervalSeconds(intervalSeconds) }
    }
    @Published var requestTimeout: Double {
        didSet { SharedConfig.setRequestTimeoutSeconds(requestTimeout) }
    }
    @Published var roi: CGRect {
        didSet { SharedConfig.setROI(roi) }
    }
    @Published var notificationShowsContent: Bool {
        didSet { SharedConfig.setNotificationShowsContent(notificationShowsContent) }
    }

    @Published var captureWanted: Bool {
        didSet { ControlState.setUserWantsCapture(captureWanted) }
    }
    @Published var roundCounter: Int = 0
    @Published var lastUpdated: Date?

    private var timer: AnyCancellable?

    init() {
        serverURLString = SharedConfig.serverBaseURLRaw() ?? ""
        intervalSeconds = SharedConfig.intervalSeconds()
        requestTimeout = SharedConfig.requestTimeoutSeconds()
        roi = SharedConfig.roi()
        notificationShowsContent = SharedConfig.notificationShowsContent()
        captureWanted = ControlState.userWantsCapture()
        roundCounter = ControlState.roundCounter()

        // 通知权限：主 App 首次启动时请求（展示链路的授权入口；
        // 未授权则扩展静默降级，不影响采集/OCR/网络）。
        UNUserNotificationCenter.current()
            .requestAuthorization(options: [.alert]) { _, _ in }

        // 轮询共享状态（扩展每完成一轮会 bump 计数器）。
        // Darwin 通知需要 CFRunLoop 集成，轮询实现更简单且已足够（主 App 仅展示用途）。
        timer = Timer.publish(every: 1, on: .main, in: .default)
            .autoconnect()
            .sink { [weak self] _ in
                guard let self else { return }
                let latest = ControlState.roundCounter()
                if latest != self.roundCounter {
                    self.roundCounter = latest
                    self.lastUpdated = Date()
                }
                let want = ControlState.userWantsCapture()
                if want != self.captureWanted {
                    self.captureWanted = want
                }
            }
    }

    var serverURLIsValid: Bool {
        SharedConfig.serverBaseURL() != nil
    }
}
