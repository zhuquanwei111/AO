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
    @Published var personROI: CGRect {
        didSet { SharedConfig.setPersonROI(personROI) }
    }
    @Published var captureWanted: Bool {
        didSet { ControlState.setUserWantsCapture(captureWanted) }
    }
    @Published var roundCounter: Int = 0
    @Published var lastUpdated: Date?
    @Published var douyinAccount: String {
        didSet { SharedConfig.setDouyinAccount(douyinAccount) }
    }
    @Published var liveDescription = ""
    @Published var liveStatus = "未开始"
    @Published var liveEvents: [LivePrompt] = []
    @Published var liveSummary: LiveSessionSummary?
    @Published var liveActive = false
    @Published var liveStopped = false
    @Published var liveStarting = false
    @Published var liveStopping = false
    @Published var broadcastStatusText = "系统屏幕广播状态尚未确认"
    @Published var serviceStatusText = "服务连接尚未通过请求核验"
    @Published var overviewLoading = false
    @Published var audioSettings: AudioCaptureSettings {
        didSet { AudioCaptureSettings.save(audioSettings) }
    }
    @Published var audioStatus = AudioCaptureStatus()

    private var timer: AnyCancellable?
    private var liveCredentials: LiveSessionCredentials?
    private var liveClient: LiveOperationsClient?
    private var livePollTask: URLSessionDataTask?
    private var liveCursor = ""
    private var summaryRefreshInFlight = false
    private var lastServiceConnection = ControlState.serviceConnection()

    init() {
        serverURLString = SharedConfig.serverBaseURLRaw() ?? ""
        douyinAccount = SharedConfig.douyinAccount()
        intervalSeconds = SharedConfig.intervalSeconds()
        requestTimeout = SharedConfig.requestTimeoutSeconds()
        roi = SharedConfig.roi()
        onlineCountROI = SharedConfig.onlineCountROI()
        platformNoticeROI = SharedConfig.platformNoticeROI()
        let diagnosticRegion = InputDiagnosticsStore.configuration().region.clamped
        personROI = SharedConfig.personROI() ?? CGRect(x: CGFloat(diagnosticRegion.x),
                                                       y: CGFloat(diagnosticRegion.y),
                                                       width: CGFloat(diagnosticRegion.width),
                                                       height: CGFloat(diagnosticRegion.height))
        captureWanted = ControlState.userWantsCapture()
        broadcastStatusText = Self.broadcastStatusDescription(
            activity: ControlState.broadcastActivity(),
            captureWanted: captureWanted,
            now: Date()
        )
        serviceStatusText = Self.serviceStatusDescription(ControlState.serviceConnection(), now: Date())
        audioSettings = AudioCaptureSettings.load()
        if LiveSessionState.credentials() == nil {
            audioSettings.appHostConfirmed = false
            audioSettings.micHostConfirmed = false
            AudioCaptureSettings.save(audioSettings)
        }
        let savedLiveCredentials = LiveSessionState.credentials()
        liveActive = LiveSessionState.isEnabled() && savedLiveCredentials != nil
        roundCounter = ControlState.roundCounter()

        // 轮询共享状态（扩展每完成一轮会 bump 计数器）。
        // Darwin 通知需要 CFRunLoop 集成，轮询实现更简单且已足够（主 App 仅展示用途）。
        timer = Timer.publish(every: 1, on: .main, in: .default)
            .autoconnect()
            .sink { [weak self] _ in
                guard let self else { return }
                self.audioStatus = AudioCaptureStatus.load()
                let latest = ControlState.roundCounter()
                if latest != self.roundCounter {
                    self.roundCounter = latest
                    self.lastUpdated = Date()
                }
                let want = ControlState.userWantsCapture()
                if want != self.captureWanted {
                    self.captureWanted = want
                }
                self.broadcastStatusText = Self.broadcastStatusDescription(
                    activity: ControlState.broadcastActivity(),
                    captureWanted: self.captureWanted,
                    now: Date()
                )
                self.refreshServiceConnectionStatus()
            }

        if liveActive, let credentials = Self.savedCredentials() {
            liveCredentials = credentials
            liveClient = SharedConfig.serverBaseURL().flatMap { LiveOperationsClient(baseURL: $0) }
            liveStatus = "恢复当前场次收件"
            if liveClient != nil { pollLivePrompts() }
        } else if LiveSessionState.isEnabled() {
            LiveSessionState.clear()
        } else if let credentials = savedLiveCredentials {
            liveStopped = true
            liveClient = SharedConfig.serverBaseURL().flatMap { LiveOperationsClient(baseURL: $0) }
            liveStatus = "本场已结束，正在检查后台汇总"
            if let client = liveClient { loadStoppedOverview(credentials, client: client) }
        }
    }

    var serverURLIsValid: Bool {
        SharedConfig.serverBaseURL() != nil
    }

    func applyLiveAreaPreset() {
        roi = LiveAreaPresets.comments
        onlineCountROI = LiveAreaPresets.onlineCount
        personROI = LiveAreaPresets.person
        // 平台提醒没有参考坐标，本操作不配置或清除用户现有选择。
    }

    func startLiveSession() {
        guard !liveStarting, !liveStopping else { return }
        let account = douyinAccount.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !account.isEmpty else {
            liveStatus = "请先填写本场抖音号"
            return
        }
        guard !liveActive else {
            liveStatus = "本场辅助已运行"
            return
        }
        guard let base = SharedConfig.serverBaseURL(),
              let client = LiveOperationsClient(baseURL: base) else {
            ControlState.setServiceConnection(.configurationInvalid)
            refreshServiceConnectionStatus()
            liveStatus = "请先配置有效服务地址"
            return
        }
        liveStarting = true
        liveStatus = "正在创建场次…"
        client.create(description: liveDescription, douyinAccount: account) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                switch result {
                case .success(let credentials):
                    guard LiveSessionState.save(credentials, enableCapture: false) else {
                        client.stop(credentials) { _ in }
                        self.liveStarting = false
                        self.liveStatus = "无法安全保存本场授权，已请求取消场次"
                        return
                    }
                    client.start(credentials) { [weak self] startResult in
                        DispatchQueue.main.async {
                            guard let self else { return }
                            self.liveStarting = false
                            switch startResult {
                            case .success(let activeCredentials):
                                guard activeCredentials.backstageTaskID != nil else {
                                    client.stop(activeCredentials) { _ in }
                                    LiveSessionState.clear()
                                    self.liveStatus = "后台任务未创建成功，已请求取消场次"
                                    return
                                }
                                self.activateLivePromptSession(
                                    activeCredentials,
                                    client: client,
                                    status: "后台场次已就绪；请按下方步骤启动本机提示与屏幕广播"
                                )
                            case .failure(let error):
                                if (error as? LiveOperationsHTTPError)?.isBackstageTaskBusy == true {
                                    self.activateLivePromptSession(
                                        credentials,
                                        client: client,
                                        status: "后台复盘任务繁忙；本场实时提示仍可继续，五项汇总可能缺失"
                                    )
                                } else {
                                    client.stop(credentials) { _ in }
                                    LiveSessionState.clear()
                                    self.liveStatus = Self.operationFailureMessage(error, action: "后台任务启动")
                                }
                            }
                        }
                    }
                case .failure(let error):
                    self.liveStarting = false
                    self.liveStatus = Self.operationFailureMessage(error, action: "场次创建")
                }
            }
        }
    }

    private func activateLivePromptSession(_ credentials: LiveSessionCredentials,
                                           client: LiveOperationsClient,
                                           status: String) {
        guard LiveSessionState.save(credentials) else {
            client.stop(credentials) { _ in }
            LiveSessionState.clear()
            liveStatus = "无法安全保存本场授权，已请求取消场次"
            return
        }
        liveCredentials = credentials
        liveClient = client
        liveActive = true
        liveStopped = false
        liveStatus = status
        liveEvents = []
        liveSummary = nil
        liveCursor = ""
        captureWanted = true
        pollLivePrompts()
    }

    func stopLiveSession() {
        guard !liveStopping else { return }
        guard let credentials = liveCredentials ?? Self.savedCredentials(),
              let client = liveClient ?? SharedConfig.serverBaseURL().flatMap({ LiveOperationsClient(baseURL: $0) }) else {
            liveStatus = "没有可停止的场次"
            return
        }
        livePollTask?.cancel()
        livePollTask = nil
        captureWanted = false
        liveStopping = true
        liveStatus = "正在结束场次…"
        client.stop(credentials) { [weak self] ok in
            DispatchQueue.main.async {
                guard let self else { return }
                self.liveStopping = false
                guard ok else {
                    self.liveStatus = "服务未确认结束场次；检查网络后可重试，当前场次授权仍保留"
                    return
                }
                self.audioSettings.appHostConfirmed = false
                self.audioSettings.micHostConfirmed = false
                self.liveActive = false
                self.liveStopped = true
                self.liveCredentials = nil
                LiveSessionState.disableCapturePreservingCredentials()
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
                        self.liveStopped = true
                        LiveSessionState.disableCapturePreservingCredentials()
                        self.audioSettings.appHostConfirmed = false
                        self.audioSettings.micHostConfirmed = false
                        self.loadStoppedOverview(credentials, client: client)
                    } else {
                        if self.liveStatus == "后台收件暂不可用，正在重试" {
                            self.liveStatus = "后台场次已就绪；运营收件已恢复"
                        }
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

    private static func operationFailureMessage(_ error: Error, action: String) -> String {
        if let httpError = error as? LiveOperationsHTTPError {
            if httpError.isBackstageTaskBusy {
                return "已有后台复盘任务运行；本场实时提示仍可继续，五项复盘不可用"
            }
            switch httpError.statusCode {
            case 401, 403:
                return "\(action)失败：本场授权未通过，请重新开始场次"
            case 409:
                return "\(action)失败：场次或任务状态冲突，请检查后重试"
            case 422:
                return "\(action)失败：场次信息或来源不符合要求，请检查后重试"
            case -1:
                return "\(action)失败：没有收到服务响应，请检查网络和服务地址"
            default:
                if (100...599).contains(httpError.statusCode) {
                    return "\(action)失败：服务返回 HTTP \(httpError.statusCode)，请检查场次状态后重试"
                }
                return "\(action)失败：服务响应无法读取，请检查服务版本"
            }
        }
        let failure = error as NSError
        if failure.domain == NSURLErrorDomain {
            return "\(action)失败：无法连接服务，请检查网络和服务地址"
        }
        guard failure.domain == "LiveOperations" else {
            return "\(action)失败：操作未完成，请检查服务状态后重试"
        }
        switch failure.code {
        case 401, 403:
            return "\(action)失败：本场授权未通过，请重新开始场次"
        case 409:
            return "\(action)失败：已有活动后台任务，请先结束或处理上一场"
        case 422:
            return "\(action)失败：场次信息或状态不符合要求，请检查后重试"
        case -2:
            return "\(action)失败：服务响应无法读取，请检查服务版本"
        case -1:
            return "\(action)失败：没有收到服务响应，请检查网络和服务地址"
        default:
            if (100...599).contains(failure.code) {
                return "\(action)失败：服务返回 HTTP \(failure.code)，请检查场次状态后重试"
            }
            return "\(action)失败：操作未完成，请检查服务状态后重试"
        }
    }

    private static func broadcastStatusDescription(
        activity: ControlState.BroadcastActivity,
        captureWanted: Bool,
        now: Date
    ) -> String {
        let nowMs = Int64(now.timeIntervalSince1970 * 1000)
        let stateAge = activity.stateChangedAtMs > 0
            ? max(0, nowMs - activity.stateChangedAtMs) / 1000
            : Int64.max
        let sampleAge = activity.lastVideoSampleAtMs > 0
            ? max(0, nowMs - activity.lastVideoSampleAtMs) / 1000
            : Int64.max

        switch activity.phase {
        case .unknown:
            return captureWanted
                ? "识别已启用，但尚无广播状态回报；请通过系统按钮启动屏幕广播"
                : "系统屏幕广播状态尚未确认"
        case .waitingForVideo:
            return stateAge > 10
                ? "系统广播尚未送来视频帧；请查看系统广播状态，原因未知"
                : "系统广播已启动，等待首个屏幕视频帧…"
        case .receivingVideo:
            if sampleAge > 10 {
                return "超过 10 秒未收到新视频帧；请检查系统广播状态，原因未知"
            }
            return captureWanted
                ? "已收到屏幕视频帧；不代表 OCR 一定识别到文字"
                : "最近收到屏幕视频帧，但识别开关当前已关闭"
        case .paused:
            return "系统报告屏幕广播已暂停"
        case .finished:
            return "系统报告屏幕广播已结束"
        }
    }

    private func refreshServiceConnectionStatus() {
        let current = ControlState.serviceConnection()
        serviceStatusText = Self.serviceStatusDescription(current, now: Date())
        if current != lastServiceConnection {
            let previous = lastServiceConnection
            lastServiceConnection = current
            if current.phase == .connected && Self.isServiceFailure(previous.phase) {
                PiPDisplayController.shared.showConnectionStatus("服务请求已恢复")
            }
        }
        if Self.isServiceFailure(current.phase) {
            PiPDisplayController.shared.showConnectionStatus(Self.pipServiceStatus(current))
        }
    }

    private static func isServiceFailure(_ phase: ControlState.ServicePhase) -> Bool {
        phase == .unreachable || phase == .respondedWithError || phase == .configurationInvalid
    }

    private static func serviceStatusDescription(_ status: ControlState.ServiceConnection, now: Date) -> String {
        guard status.phase != .unknown else { return "服务连接尚未通过请求核验" }
        let ageMs = Int64(now.timeIntervalSince1970 * 1000) - status.observedAtMs
        let stale = status.observedAtMs == 0 || ageMs > 30_000
        switch status.phase {
        case .unknown:
            return "服务连接尚未通过请求核验"
        case .connected:
            return stale ? "最近连接记录已过期，等待新请求确认" : "最近请求已收到服务响应"
        case .unreachable:
            return stale ? "最近请求未收到服务响应；尚未通过新请求确认恢复" : "最近请求未收到服务响应；新请求会重试"
        case .respondedWithError:
            let code = status.httpStatusCode > 0 ? "（HTTP \(status.httpStatusCode)）" : ""
            return stale ? "服务曾响应但请求失败\(code)；等待新请求确认" : "服务已响应但请求失败\(code)"
        case .configurationInvalid:
            return "服务地址无效；请到采集与服务设置检查"
        }
    }

    private static func pipServiceStatus(_ status: ControlState.ServiceConnection) -> String {
        switch status.phase {
        case .unreachable:
            return "最近一次实时请求未连接到服务；下一次请求会重试"
        case .respondedWithError:
            return "服务已响应但请求失败（HTTP \(status.httpStatusCode)）；下一次请求会重试"
        case .configurationInvalid:
            return "服务地址配置无效，请检查采集与服务设置"
        case .unknown, .connected:
            return "服务连接状态待核验"
        }
    }

    private func loadStoppedOverview(_ credentials: LiveSessionCredentials, client: LiveOperationsClient) {
        let group = DispatchGroup()
        var fetchedEvents: LiveEventPage?
        var fetchedSummary: LiveSessionSummary?
        overviewLoading = true
        summaryRefreshInFlight = true
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
            self.overviewLoading = false
            self.summaryRefreshInFlight = false
            self.liveEvents = fetchedEvents?.events ?? []
            self.liveSummary = fetchedSummary
            self.finishCredentialRetentionIfTerminal(fetchedSummary?.backstageRecap?.status)
            switch (fetchedEvents, fetchedSummary) {
            case let (.some(page), .some(_)):
                self.liveStatus = "本场结束，已读取 \(page.events.count) 条记录"
            case (.some, .none):
                self.liveStatus = "本场概览暂不可读；事件记录已读取"
            case (.none, .some):
                self.liveStatus = "本场概览已读取；事件记录暂不可读"
            case (.none, .none):
                self.liveStatus = "本场已结束；概览和记录暂不可读取"
            }
            if let recap = fetchedSummary?.backstageRecap {
                self.liveStatus = self.backstageStatusText(recap.status)
            } else if fetchedSummary != nil {
                self.liveStatus = "本场事件记录已读取；服务未返回后台五项任务记录，结果保持未知"
            }
        }
    }

    func refreshStoppedOverview() {
        guard !liveActive, !summaryRefreshInFlight,
              let credentials = Self.savedCredentials(),
              let client = liveClient ?? SharedConfig.serverBaseURL().flatMap({ LiveOperationsClient(baseURL: $0) }) else {
            return
        }
        summaryRefreshInFlight = true
        overviewLoading = true
        client.summary(credentials) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                self.summaryRefreshInFlight = false
                self.overviewLoading = false
                guard case .success(let summary) = result else {
                    if self.liveSummary == nil {
                        self.liveStatus = "本场后台汇总暂不可读，可重试；事件记录独立保留"
                    }
                    return
                }
                self.liveSummary = summary
                if let recap = summary.backstageRecap {
                    self.liveStatus = self.backstageStatusText(recap.status)
                    self.finishCredentialRetentionIfTerminal(recap.status)
                } else {
                    self.liveStatus = "服务未返回后台五项任务记录，结果保持未知"
                }
            }
        }
    }

    private func finishCredentialRetentionIfTerminal(_ status: String?) {
        guard let status, ["captured_locally", "partial", "needs_input", "failed"].contains(status) else {
            return
        }
        LiveSessionState.clear()
    }

    private func backstageStatusText(_ status: String) -> String {
        switch status {
        case "waiting_for_stop": return "后台任务已绑定，等待结束信号"
        case "capturing": return "已结束，后台汇总处理中"
        case "not_connected": return "已结束；后台执行器未连接，结果待处理"
        case "captured_locally": return "后台五项汇总已收到"
        case "partial": return "后台汇总部分缺失，请核对缺项"
        case "needs_input": return "后台任务需要处理后重试"
        case "failed": return "后台汇总失败，请检查任务状态"
        default: return "本场后台汇总状态：\(status)"
        }
    }

    private static func savedCredentials() -> LiveSessionCredentials? {
        LiveSessionState.credentials()
    }
}
