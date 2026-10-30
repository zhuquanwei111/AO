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
    @Published var liveConnectionPending = false
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
    private var livePollGeneration = 0
    private var liveStartGeneration = 0
    private var liveCursor = ""
    private var liveDelivery = LivePromptDelivery()
    private var liveDisplayedUntilMS: Int64?
    private var liveDisplayedItems: [LivePromptDelivery.Item] = []
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
        let savedConnectionPending = savedLiveCredentials?.liveConnectionPending == true
        liveActive = LiveSessionState.isEnabled() && savedLiveCredentials != nil
        liveConnectionPending = liveActive && savedConnectionPending
        if liveConnectionPending { captureWanted = false }
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
                self.deliverLivePrompts()
            }

        if liveActive, let credentials = Self.savedCredentials() {
            PiPDisplayController.shared.setLiveSessionActive(true)
            liveCredentials = credentials
            liveClient = SharedConfig.serverBaseURL().flatMap { LiveOperationsClient(baseURL: $0) }
            if liveConnectionPending {
                liveStatus = "后台启动结果未确认；正在核验同一场实时连接"
            } else if credentials.backstageStartPending == true {
                liveStatus = "恢复同场实时收件；后台复盘任务状态仍未知"
            } else {
                liveStatus = "恢复当前场次收件"
            }
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
        liveStartGeneration &+= 1
        let generation = liveStartGeneration
        liveStatus = "正在创建场次…"
        client.create(description: liveDescription, douyinAccount: account) { [weak self] result in
            DispatchQueue.main.async {
                guard let self, BackstageStatusPresentation.acceptsStartCallback(
                    generation: generation, currentGeneration: self.liveStartGeneration,
                    starting: self.liveStarting, stopping: self.liveStopping) else { return }
                switch result {
                case .success(let credentials):
                    var pendingCredentials = credentials
                    pendingCredentials.backstageStartPending = true
                    pendingCredentials.liveConnectionPending = true
                    self.captureWanted = false
                    guard LiveSessionState.save(pendingCredentials, enableCapture: true) else {
                        client.stop(credentials) { _ in }
                        self.liveStarting = false
                        self.liveStatus = "无法安全保存本场授权，已请求取消场次"
                        return
                    }
                    client.start(pendingCredentials) { [weak self] startResult in
                        DispatchQueue.main.async {
                            guard let self, BackstageStatusPresentation.acceptsStartCallback(
                                    generation: generation, currentGeneration: self.liveStartGeneration,
                                    starting: self.liveStarting, stopping: self.liveStopping),
                                  Self.savedCredentials()?.sessionID == pendingCredentials.sessionID else { return }
                            self.liveStarting = false
                            switch startResult {
                            case .success(let activeCredentials):
                                guard activeCredentials.backstageTaskID != nil else {
                                    var unconfirmed = activeCredentials
                                    unconfirmed.backstageStartPending = true
                                    unconfirmed.liveConnectionPending = true
                                    self.activateLivePromptSession(
                                        unconfirmed,
                                        client: client,
                                        status: "后台任务响应缺少任务编号；正在核验同一场实时连接",
                                        captureImmediately: false
                                    )
                                    return
                                }
                                self.activateLivePromptSession(
                                    activeCredentials,
                                    client: client,
                                    status: BackstageStatusPresentation.active(status: activeCredentials.backstageStatus)
                                )
                            case .failure(let error):
                                self.activateLivePromptSession(
                                    pendingCredentials,
                                    client: client,
                                    status: Self.startOutcomePendingMessage(error),
                                    captureImmediately: false
                                )
                            }
                        }
                    }
                case .failure(let error):
                    self.liveStarting = false
                    self.liveStatus = Self.createFailureMessage(error)
                }
            }
        }
    }

    private func activateLivePromptSession(_ credentials: LiveSessionCredentials,
                                           client: LiveOperationsClient,
                                           status: String,
                                           captureImmediately: Bool = true) {
        PiPDisplayController.shared.setLiveSessionActive(true)
        var activeCredentials = credentials
        if !captureImmediately { activeCredentials.liveConnectionPending = true }
        livePollGeneration &+= 1
        livePollTask?.cancel()
        livePollTask = nil
        liveEvents = []
        overviewLoading = false
        summaryRefreshInFlight = false
        liveDelivery.clear()
        liveDisplayedUntilMS = nil
        liveDisplayedItems = []
        PiPDisplayController.shared.endLiveContent()
        liveSummary = nil
        liveCursor = ""
        guard LiveSessionState.save(activeCredentials) else {
            activeCredentials.backstageStartPending = true
            activeCredentials.liveConnectionPending = true
            liveCredentials = activeCredentials
            liveClient = client
            liveActive = true
            liveStopped = false
            liveConnectionPending = true
            liveStatus = "本场授权仍保留；启动状态未能安全保存，未启用采集"
            captureWanted = false
            pollLivePrompts()
            return
        }
        liveCredentials = activeCredentials
        liveClient = client
        liveActive = true
        liveStopped = false
        liveStatus = status
        liveConnectionPending = activeCredentials.liveConnectionPending == true
        captureWanted = captureImmediately && !liveConnectionPending
        pollLivePrompts()
    }

    func stopLiveSession() {
        guard !liveStopping else { return }
        guard let credentials = liveCredentials ?? Self.savedCredentials(),
              let client = liveClient ?? SharedConfig.serverBaseURL().flatMap({ LiveOperationsClient(baseURL: $0) }) else {
            liveStatus = "没有可停止的场次"
            return
        }
        liveStartGeneration &+= 1
        liveStarting = false
        livePollGeneration &+= 1
        livePollTask?.cancel()
        livePollTask = nil
        liveDelivery.clear()
        liveDisplayedUntilMS = nil
        liveDisplayedItems = []
        PiPDisplayController.shared.endLiveContent()
        captureWanted = false
        liveConnectionPending = false
        var stoppingCredentials = credentials
        stoppingCredentials.liveConnectionPending = false
        _ = LiveSessionState.save(stoppingCredentials)
        liveCredentials = stoppingCredentials
        liveStopping = true
        liveStatus = "正在结束场次…"
        client.stop(stoppingCredentials) { [weak self] ok in
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
                PiPDisplayController.shared.setLiveSessionActive(false)
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
        livePollGeneration &+= 1
        let generation = livePollGeneration
        livePollTask = client.poll(credentials, after: liveCursor) { [weak self] result, response, transportError in
            DispatchQueue.main.async {
                guard let self else { return }
                guard PollCallbackGate.commitIfCurrent(
                    sessionID: credentials.sessionID, generation: generation,
                    currentSessionID: self.liveCredentials?.sessionID,
                    currentGeneration: self.livePollGeneration,
                    active: self.liveActive, stopping: self.liveStopping,
                    commit: { ControlState.recordServiceResponse(error: transportError, response: response) }
                ) else { return }
                switch result {
                case .success(let page):
                    self.liveCursor = page.nextCursor
                    let now = Int64(Date().timeIntervalSince1970 * 1000)
                    let items = page.prompts.map { prompt in
                        let label = prompt.sources?.contains(where: { $0.kind == "comment" }) == true ? "问答" : "运营"
                        return LivePromptDelivery.Item(id: prompt.promptID,
                            text: "【\(label)】" + prompt.displayText,
                            expiresAtMS: prompt.expiresAtMS ?? 0,
                            priority: prompt.priority ?? 0)
                    }
                    let beforeOverflow = self.liveDelivery.overflowCount
                    let beforeExpired = self.liveDelivery.expiredCount
                    var acceptedIDs = Set(self.liveDelivery.receive(items, nowMS: now).map(\.id))
                    let fresh = page.prompts.filter { acceptedIDs.remove($0.promptID) != nil }
                    self.liveEvents.insert(contentsOf: fresh, at: 0)
                    self.liveEvents = Array(self.liveEvents.prefix(100))
                    if !fresh.isEmpty { NSLog("Live prompt delivery: received=%d", fresh.count) }
                    if self.liveDelivery.overflowCount != beforeOverflow || self.liveDelivery.expiredCount != beforeExpired {
                        NSLog("Live prompt delivery: overflow=%d expired=%d", self.liveDelivery.overflowCount, self.liveDelivery.expiredCount)
                    }
                    if !page.stopped { self.deliverLivePrompts() }
                    if page.stopped {
                        self.liveDelivery.clear()
                        self.liveDisplayedUntilMS = nil
                        self.liveDisplayedItems = []
                        PiPDisplayController.shared.endLiveContent()
                        self.liveStatus = "服务端场次已停止"
                        self.liveActive = false
                        PiPDisplayController.shared.setLiveSessionActive(false)
                        self.liveStopped = true
                        self.liveConnectionPending = false
                        LiveSessionState.disableCapturePreservingCredentials()
                        self.audioSettings.appHostConfirmed = false
                        self.audioSettings.micHostConfirmed = false
                        self.loadStoppedOverview(credentials, client: client)
                    } else {
                        if self.liveConnectionPending {
                            var confirmedCredentials = credentials
                            confirmedCredentials.liveConnectionPending = false
                            guard LiveSessionState.save(confirmedCredentials) else {
                                self.liveStatus = "同场实时收件已响应，但授权状态无法保存；暂未启用采集"
                                self.scheduleLivePromptRetry(sessionID: credentials.sessionID, generation: generation)
                                return
                            }
                            self.liveCredentials = confirmedCredentials
                            self.liveConnectionPending = false
                            self.captureWanted = true
                            self.liveStatus = confirmedCredentials.backstageStartPending == true
                                ? "同场实时收件已响应；后台复盘任务状态未知，未重复启动"
                                : "同场实时收件已响应；已恢复本机采集"
                        } else if self.liveStatus == "运营收件暂不可用，正在重试" {
                            self.liveStatus = BackstageStatusPresentation.recovered(status: credentials.backstageStatus)
                        }
                        self.pollLivePrompts()
                    }
                case .failure(let error):
                    self.liveStatus = self.liveConnectionPending
                        ? Self.liveVerificationFailureMessage(error)
                        : "运营收件暂不可用，正在重试"
                    self.scheduleLivePromptRetry(sessionID: credentials.sessionID, generation: generation)
                }
            }
        }
    }

    private func deliverLivePrompts() {
        let now = Int64(Date().timeIntervalSince1970 * 1000)
        if let expiry = liveDisplayedUntilMS, expiry <= now {
            liveDisplayedItems = LivePromptDelivery.unexpired(liveDisplayedItems, nowMS: now)
            liveDisplayedUntilMS = liveDisplayedItems.map(\.expiresAtMS).min()
            if liveDisplayedItems.isEmpty {
                PiPDisplayController.shared.endLiveContent()
            } else {
                _ = PiPDisplayController.shared.showLiveFrame(liveDisplayedItems)
            }
        }
        guard liveActive, !liveStopping, PiPDisplayController.shared.canShowLiveFrame else { return }
        let beforeExpired = liveDelivery.expiredCount
        let frame = liveDelivery.take(nowMS: now) { PiPDisplayController.shared.liveFrameFits($0) }
        if liveDelivery.expiredCount != beforeExpired {
            NSLog("Live prompt delivery: expired=%d", liveDelivery.expiredCount)
        }
        if !frame.isEmpty, PiPDisplayController.shared.showLiveFrame(frame) {
            liveDisplayedItems = frame
            liveDisplayedUntilMS = frame.map(\.expiresAtMS).min()
            NSLog("Live prompt delivery: selected=%d", frame.count)
        }
    }

    private func scheduleLivePromptRetry(sessionID: String, generation: Int) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
            guard let self, self.liveActive, !self.liveStopping,
                  self.liveCredentials?.sessionID == sessionID,
                  self.livePollGeneration == generation else { return }
            self.pollLivePrompts()
        }
    }

    private static func createFailureMessage(_ error: Error) -> String {
        if let httpError = error as? LiveOperationsHTTPError,
           (400...499).contains(httpError.statusCode) {
            return "场次创建收到 HTTP \(httpError.statusCode) 拒绝；未自动重试"
        }
        let status = (error as? LiveOperationsHTTPError)?.statusCode
        let reason = status.map { "HTTP \($0)" } ?? "连接或响应异常"
        return "场次创建结果未确认（\(reason)）；未自动重试。当前接口没有按请求回查或幂等键，核对服务端后再开始，避免重复场次"
    }

    private static func startOutcomePendingMessage(_ error: Error) -> String {
        if let httpError = error as? LiveOperationsHTTPError,
           (500...599).contains(httpError.statusCode) {
            return "后台启动返回 HTTP \(httpError.statusCode)，任务结果未确认；正在核验同一场实时连接"
        }
        let failure = error as NSError
        if failure.domain == NSURLErrorDomain ||
            (error as? LiveOperationsHTTPError)?.statusCode == -1 {
            return "后台启动未收到完整响应，任务结果未确认；正在核验同一场实时连接"
        }
        if let httpError = error as? LiveOperationsHTTPError {
            return "后台启动返回 HTTP \(httpError.statusCode)；保留本场授权并核验实时连接"
        }
        return "后台启动响应无法确认；保留本场授权并核验实时连接，不会重建场次"
    }

    private static func liveVerificationFailureMessage(_ error: Error) -> String {
        if let httpError = error as? LiveOperationsHTTPError {
            if (500...599).contains(httpError.statusCode) {
                return "实时会话核验收到 HTTP \(httpError.statusCode)；未启用采集，保留本场授权并重试只读检查"
            }
            return "实时会话核验未通过（HTTP \(httpError.statusCode)）；未启用采集，保留本场授权并重试检查"
        }
        let failure = error as NSError
        if failure.domain == NSURLErrorDomain {
            return "实时会话尚未收到服务响应；未启用采集，保留本场授权并重试只读检查"
        }
        return "实时会话状态无法确认；未启用采集，保留本场授权并重试只读检查"
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
            guard let self, !self.liveActive,
                  Self.savedCredentials()?.sessionID == credentials.sessionID else { return }
            self.overviewLoading = false
            self.summaryRefreshInFlight = false
            if let events = fetchedEvents?.events { self.liveEvents = events }
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
                self.liveStatus = Self.backstageStatusText(recap)
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
                guard let self, !self.liveActive,
                      Self.savedCredentials()?.sessionID == credentials.sessionID else { return }
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
                    self.liveStatus = Self.backstageStatusText(recap)
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

    private static func backstageStatusText(_ recap: LiveBackstageRecap) -> String {
        let count = recap.metrics.map { metrics in
            [metrics.cumulativeAudience, metrics.newFollowers, metrics.commentCount,
             metrics.giftGivers, metrics.soundEnergy].compactMap { $0 }.count
        }
        return BackstageStatusPresentation.summary(status: recap.status, claimedAtMS: recap.claimedAtMS,
                                                   receivedMetricCount: count)
    }

    private static func savedCredentials() -> LiveSessionCredentials? {
        LiveSessionState.credentials()
    }
}
