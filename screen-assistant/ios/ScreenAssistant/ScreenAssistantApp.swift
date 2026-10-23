import SwiftUI
import Combine

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
    @Published var onlineCountROI: CGRect? {
        didSet { SharedConfig.setOnlineCountROI(onlineCountROI) }
    }
    @Published var platformNoticeROI: CGRect? {
        didSet { SharedConfig.setPlatformNoticeROI(platformNoticeROI) }
    }
    @Published var captureWanted: Bool {
        didSet { ControlState.setUserWantsCapture(captureWanted) }
    }
    @Published var roundCounter: Int = 0
    @Published var lastUpdated: Date?
    @Published var liveDescription = ""
    @Published var liveStatus = "未开始"
    @Published var liveEvents: [LivePrompt] = []
    @Published var liveSummary: LiveSessionSummary?
    @Published var liveActive = false

    private var timer: AnyCancellable?
    private var liveCredentials: LiveSessionCredentials?
    private var liveClient: LiveOperationsClient?
    private var livePollTask: URLSessionDataTask?
    private var liveCursor = ""

    init() {
        serverURLString = SharedConfig.serverBaseURLRaw() ?? ""
        intervalSeconds = SharedConfig.intervalSeconds()
        requestTimeout = SharedConfig.requestTimeoutSeconds()
        roi = SharedConfig.roi()
        onlineCountROI = SharedConfig.onlineCountROI()
        platformNoticeROI = SharedConfig.platformNoticeROI()
        captureWanted = ControlState.userWantsCapture()
        liveActive = LiveSessionState.credentials() != nil
        roundCounter = ControlState.roundCounter()

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

        if liveActive, let credentials = Self.savedCredentials() {
            liveCredentials = credentials
            liveClient = SharedConfig.serverBaseURL().flatMap { LiveOperationsClient(baseURL: $0) }
            liveStatus = "恢复当前场次收件"
            if liveClient != nil { pollLivePrompts() }
        } else if LiveSessionState.isEnabled() {
            LiveSessionState.clear()
        }
    }

    var serverURLIsValid: Bool {
        SharedConfig.serverBaseURL() != nil
    }

    func startLiveSession() {
        guard !liveActive, let base = SharedConfig.serverBaseURL(),
              let client = LiveOperationsClient(baseURL: base) else {
            liveStatus = "请先配置有效服务地址"
            return
        }
        liveStatus = "正在创建场次…"
        client.create(description: liveDescription) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                switch result {
                case .success(let credentials):
                    guard LiveSessionState.save(credentials) else {
                        client.stop(credentials) { _ in }
                        self.liveStatus = "无法安全保存本场授权，已请求取消场次"
                        return
                    }
                    self.liveCredentials = credentials
                    self.liveClient = client
                    self.liveActive = true
                    self.liveStatus = "直播辅助已开始；请在前台启动 PiP 与 ReplayKit 广播"
                    self.liveEvents = []
                    self.liveSummary = nil
                    self.liveCursor = ""
                    self.captureWanted = true
                    self.pollLivePrompts()
                case .failure:
                    self.liveStatus = "场次创建失败，请检查服务连接"
                }
            }
        }
    }

    func stopLiveSession() {
        guard let credentials = liveCredentials ?? Self.savedCredentials(),
              let client = liveClient ?? SharedConfig.serverBaseURL().flatMap({ LiveOperationsClient(baseURL: $0) }) else {
            liveStatus = "没有可停止的场次"
            return
        }
        livePollTask?.cancel()
        livePollTask = nil
        captureWanted = false
        liveStatus = "正在结束场次…"
        client.stop(credentials) { [weak self] ok in
            DispatchQueue.main.async {
                guard let self else { return }
                guard ok else {
                    self.liveStatus = "停止请求未确认；可重试结束"
                    return
                }
                LiveSessionState.clear()
                self.liveActive = false
                self.liveCredentials = nil
                self.liveStatus = "已结束，正在读取本场记录"
                self.loadStoppedOverview(credentials, client: client)
            }
        }
    }

    private func pollLivePrompts() {
        guard liveActive, let credentials = liveCredentials, let client = liveClient else { return }
        livePollTask = client.poll(credentials, after: liveCursor) { [weak self] result in
            DispatchQueue.main.async {
                guard let self, self.liveActive else { return }
                switch result {
                case .success(let page):
                    self.liveCursor = page.nextCursor
                    let known = Set(self.liveEvents.map(\.promptID))
                    let fresh = page.prompts.filter { !known.contains($0.promptID) }
                    self.liveEvents.insert(contentsOf: fresh, at: 0)
                    self.liveEvents = Array(self.liveEvents.prefix(100))
                    if let prompt = fresh.last {
                        PiPDisplayController.shared.showLivePrompt(prompt.displayText)
                    }
                    if page.stopped {
                        self.liveStatus = "服务端场次已停止"
                        self.liveActive = false
                        LiveSessionState.clear()
                        self.loadStoppedOverview(credentials, client: client)
                    } else {
                        self.liveStatus = "直播辅助运行中"
                        self.pollLivePrompts()
                    }
                case .failure:
                    self.liveStatus = "运营收件暂不可用，正在重试"
                    DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
                        self?.pollLivePrompts()
                    }
                }
            }
        }
    }

    private func loadStoppedOverview(_ credentials: LiveSessionCredentials, client: LiveOperationsClient) {
        let group = DispatchGroup()
        var fetchedEvents: LiveEventPage?
        var fetchedSummary: LiveSessionSummary?
        group.enter()
        client.events(credentials) { result in
            DispatchQueue.main.async {
                if case .success(let page) = result { fetchedEvents = page }
                group.leave()
            }
        }
        group.enter()
        client.summary(credentials) { result in
            DispatchQueue.main.async {
                if case .success(let summary) = result { fetchedSummary = summary }
                group.leave()
            }
        }
        group.notify(queue: .main) { [weak self] in
            guard let self else { return }
            self.liveEvents = fetchedEvents?.events ?? []
            self.liveSummary = fetchedSummary
            switch (fetchedEvents, fetchedSummary) {
            case let (.some(page), .some(_)):
                self.liveStatus = "本场结束，已读取 \(page.events.count) 条记录"
            case (.some, .none):
                self.liveStatus = "本场结束；人数概览暂不可读，事件记录已读取"
            case (.none, .some):
                self.liveStatus = "本场结束；人数概览已读取，事件记录暂不可读"
            case (.none, .none):
                self.liveStatus = "本场已结束；概览和记录暂不可读取"
            }
        }
    }

    private static func savedCredentials() -> LiveSessionCredentials? {
        LiveSessionState.credentials()
    }
}
