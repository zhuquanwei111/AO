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
        didSet {
            onlineCountSemanticsConfirmed = false
            SharedConfig.setOnlineCountROI(onlineCountROI)
        }
    }
    @Published var onlineCountSemanticsConfirmed: Bool {
        didSet { SharedConfig.setOnlineCountSemanticsConfirmed(onlineCountSemanticsConfirmed) }
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
    @Published var liveTimingWarning = false
    @Published private(set) var liveTimingAnchorFailures = 0
    @Published private(set) var liveTimingInvalidItems = 0
    @Published private(set) var livePriorityInvalidItems = 0
    @Published private(set) var liveSourcesInvalidItems = 0
    @Published private(set) var liveEntryInvalidItems = 0
    @Published var liveSummary: LiveSessionSummary?
    @Published var liveActive = false
    @Published var liveStopped = false
    @Published var liveStarting = false
    @Published var liveStopping = false
    @Published var liveConnectionPending = false
    @Published private(set) var liveVerificationNeedsRetry = false
    @Published private(set) var isolatedStopAvailable = false
    @Published private(set) var isolatedStopInFlight = false
    @Published private(set) var isolatedStopStatus = ""
    @Published var broadcastStatusText = "系统屏幕广播状态尚未确认"
    @Published var serviceStatusText = "服务连接尚未通过请求核验"
    @Published var observationUploadStatus = "采样上传暂无诊断"
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
    private var isolatedStopGeneration = 0
    private var liveCursor = ""
    private var liveDelivery = LivePromptDelivery()
    private var liveDisplayedUntilMS: Int64?
    private var liveDisplayedItems: [LivePromptDelivery.Item] = []
    private var summaryRefreshInFlight = false
    private var overviewRequest = OverviewRequestTracker()
    private var lastServiceConnection = ControlState.serviceConnection()

    init() {
        serverURLString = SharedConfig.serverBaseURLRaw() ?? ""
        douyinAccount = SharedConfig.douyinAccount()
        intervalSeconds = SharedConfig.intervalSeconds()
        requestTimeout = SharedConfig.requestTimeoutSeconds()
        roi = SharedConfig.roi()
        let initialOnlineCountROI = SharedConfig.onlineCountROI()
        onlineCountROI = initialOnlineCountROI
        onlineCountSemanticsConfirmed = SharedConfig.onlineCountSemanticsConfirmed() && initialOnlineCountROI != nil
        platformNoticeROI = SharedConfig.platformNoticeROI()
        let diagnosticRegion = InputDiagnosticsStore.configuration().region.clamped
        personROI = SharedConfig.personROI() ?? CGRect(x: CGFloat(diagnosticRegion.x),
                                                       y: CGFloat(diagnosticRegion.y),
                                                       width: CGFloat(diagnosticRegion.width),
                                                       height: CGFloat(diagnosticRegion.height))
        captureWanted = ControlState.userWantsCapture()
        audioSettings = AudioCaptureSettings.load()
        broadcastStatusText = Self.broadcastStatusDescription(
            activity: ControlState.broadcastActivity(),
            captureWanted: captureWanted,
            now: Date()
        )
        serviceStatusText = Self.serviceStatusDescription(ControlState.serviceConnection(), now: Date())
        if LiveSessionState.credentials() == nil && !LiveSessionState.isolatedStopRecordExists() {
            audioSettings.appHostConfirmed = false
            audioSettings.micHostConfirmed = false
            AudioCaptureSettings.save(audioSettings)
        }
        let savedLiveCredentials = LiveSessionState.credentials()
        let pendingIsolatedStop = LiveSessionState.isolatedStopIntent()
        let unknownStopRecord = pendingIsolatedStop == nil && LiveSessionState.isolatedStopRecordExists()
        if pendingIsolatedStop != nil {
            isolatedStopAvailable = true
            isolatedStopStatus = "有原场结束尚未确认，可手动重试"
        } else if unknownStopRecord {
            isolatedStopAvailable = true
            isolatedStopStatus = "原场结束尚未确认，原服务信息不足或记录不可读取"
        }
        let pendingOwnsSaved = pendingIsolatedStop.map {
            $0.credentials.sessionID == savedLiveCredentials?.sessionID &&
            $0.credentials.token == savedLiveCredentials?.token &&
            $0.serviceURL.absoluteString == savedLiveCredentials?.boundServiceURL?.absoluteString
        } ?? false
        let savedConnectionPending = savedLiveCredentials?.liveConnectionPending == true
        liveActive = !pendingOwnsSaved && !unknownStopRecord && LiveSessionState.recoverActive(savedLiveCredentials,
                                                   captureEnabled: LiveSessionState.isEnabled())
        liveConnectionPending = liveActive && savedConnectionPending
        let restoredService = savedLiveCredentials?.captureServiceURL(configured: SharedConfig.serverBaseURL())
        if pendingOwnsSaved || (liveActive && (LiveSessionState.holdCaptureOnRecovery(savedLiveCredentials) || restoredService == nil)) {
            captureWanted = false
            ControlState.setUserWantsCapture(false)
        }
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
                self.observationUploadStatus = Self.observationUploadDescription()
                self.deliverLivePrompts()
            }

        if pendingOwnsSaved {
            liveStatus = "原场结束尚未确认，采集未恢复"
        } else if unknownStopRecord {
            liveStatus = "原场结束记录无法确认，未自动恢复场次"
        } else if liveActive, let credentials = Self.savedCredentials() {
            PiPDisplayController.shared.setLiveSessionActive(true)
            liveCredentials = credentials
            liveClient = credentials.boundServiceURL.flatMap {
                IsolatedStopIntent.safeServiceURL($0) ? LiveOperationsClient(baseURL: $0) : nil
            }
            if credentials.stopPending == true {
                liveStatus = "本机采集已停止；服务未确认结束场次，可重试结束"
            } else if restoredService == nil {
                liveStatus = "本场原服务信息不可确认或配置已变化，采集未开启"
            } else if liveConnectionPending {
                liveStatus = "后台启动结果未确认；正在核验同一场实时连接"
            } else if credentials.backstageStartPending == true {
                liveStatus = "恢复同场实时收件；后台复盘任务状态仍未知"
            } else {
                liveStatus = "恢复当前场次收件"
            }
            if liveClient != nil && restoredService != nil && credentials.stopPending != true { pollLivePrompts() }
        } else if LiveSessionState.isEnabled() && !LiveSessionState.isolatedStopRecordExists() {
            captureWanted = false
            ControlState.setUserWantsCapture(false)
            liveStatus = "本场授权无法读取，采集未开启；结束状态尚未确认"
        } else if let credentials = savedLiveCredentials {
            liveStopped = true
            liveClient = credentials.boundServiceURL.flatMap {
                IsolatedStopIntent.safeServiceURL($0) ? LiveOperationsClient(baseURL: $0) : nil
            }
            if let client = liveClient {
                liveStatus = "本场已结束，正在检查后台汇总"
                loadStoppedOverview(credentials, client: client)
            } else {
                liveStatus = "本场原服务地址未确认，暂不能读取后台汇总"
            }
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
        guard !isolatedStopInFlight else { return }
        guard !liveStarting, !liveStopping else { return }
        guard !LiveSessionState.isolatedStopRecordUnreadable() else {
            liveStatus = "原场结束记录无法读取，未创建新场；可重试读取"
            return
        }
        guard !LiveSessionState.isEnabled() || Self.savedCredentials() != nil else {
            liveStatus = "已有场次授权无法读取，不能创建新场"
            return
        }
        let account = douyinAccount.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !account.isEmpty else {
            liveStatus = "请先填写本场抖音号"
            return
        }
        guard !liveActive else {
            liveStatus = "本场辅助已运行"
            return
        }
        guard let base = SharedConfig.serverBaseURL(), IsolatedStopIntent.safeServiceURL(base),
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
                    pendingCredentials.boundServiceURL = client.baseURL
                    pendingCredentials.backstageStartPending = true
                    pendingCredentials.liveConnectionPending = true
                    self.captureWanted = false
                    guard LiveSessionState.save(pendingCredentials, enableCapture: true) else {
                        client.stop(credentials) { _, _, _ in }
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
        liveVerificationNeedsRetry = false
        liveEvents = []
        liveTimingWarning = false
        liveTimingAnchorFailures = 0
        liveTimingInvalidItems = 0
        livePriorityInvalidItems = 0
        liveSourcesInvalidItems = 0
        liveEntryInvalidItems = 0
        overviewLoading = false
        summaryRefreshInFlight = false
        overviewRequest.clear()
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
        guard !liveStopping, !isolatedStopInFlight else { return }
        guard let credentials = liveCredentials ?? Self.savedCredentials(),
              let client = liveClient ?? credentials.boundServiceURL.flatMap({
                  IsolatedStopIntent.safeServiceURL($0) ? LiveOperationsClient(baseURL: $0) : nil
              }) else {
            liveStatus = "原场服务信息不足，结束尚未确认"
            return
        }
        let saved = Self.savedCredentials()
        if saved?.sessionID != credentials.sessionID || saved?.token != credentials.token ||
            saved?.boundServiceURL?.absoluteString != client.baseURL.absoluteString ||
            (liveClient != nil && liveClient?.baseURL.absoluteString != SharedConfig.serverBaseURL()?.absoluteString) {
            if saved?.sessionID == credentials.sessionID && saved?.token == credentials.token &&
                saved?.boundServiceURL?.absoluteString == client.baseURL.absoluteString {
                captureWanted = false
            }
            guard let originalClient = liveClient else {
                isolatedStopStatus = "原场服务信息不足，未发送结束请求"
                return
            }
            let intent = IsolatedStopIntent(credentials: credentials, serviceURL: originalClient.baseURL)
            beginIsolatedStop(intent, client: originalClient)
            return
        }
        liveStartGeneration &+= 1
        let stopGeneration = liveStartGeneration
        liveVerificationNeedsRetry = false
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
        stoppingCredentials.stopPending = true
        guard LiveSessionState.save(stoppingCredentials) else {
            liveStatus = "本机采集已停止；结束状态无法保存，未发送结束请求，可重试"
            return
        }
        liveCredentials = stoppingCredentials
        liveStopping = true
        liveStatus = "本机采集已停止；正在确认服务端结束…"
        client.stop(stoppingCredentials, recordResponse: false) { [weak self] ok, response, error in
            DispatchQueue.main.async {
                guard let self, self.liveClient?.baseURL.absoluteString == client.baseURL.absoluteString,
                      SharedConfig.serverBaseURL()?.absoluteString == client.baseURL.absoluteString,
                      let saved = Self.savedCredentials(),
                      stoppingCredentials.hasSameBoundIdentity(as: saved), PollCallbackGate.commitIfCurrent(
                    sessionID: stoppingCredentials.sessionID, generation: stopGeneration,
                    currentSessionID: saved.sessionID,
                    currentGeneration: self.liveStartGeneration, active: self.liveStopping,
                    stopping: false, commit: {
                        ControlState.recordServiceResponse(error: error, response: response)
                    }) else { return }
                self.liveStopping = false
                guard ok else {
                    self.liveStatus = "本机采集已停止；服务未确认结束场次，检查网络后可重试"
                    return
                }
                self.audioSettings.appHostConfirmed = false
                self.audioSettings.micHostConfirmed = false
                self.liveActive = false
                PiPDisplayController.shared.setLiveSessionActive(false)
                self.liveStopped = true
                self.liveCredentials = nil
                if !LiveSessionState.confirmStopped(stoppingCredentials) {
                    LiveSessionState.disableCapturePreservingCredentials()
                }
                self.liveStatus = "已结束，正在读取本场记录"
                self.loadStoppedOverview(credentials, client: client)
            }
        }
    }

    private func pollLivePrompts() {
        guard !LiveSessionState.isolatedStopRecordUnreadable(), liveActive,
              let credentials = liveCredentials, let bound = credentials.boundServiceURL,
              IsolatedStopIntent.safeServiceURL(bound), let client = liveClient,
              client.baseURL.absoluteString == bound.absoluteString else { return }
        livePollGeneration &+= 1
        let generation = livePollGeneration
        let waitMS = liveConnectionPending ? 0 : 15_000
        livePollTask = client.poll(credentials, after: liveCursor, waitMS: waitMS) { [weak self] result, response, transportError in
            DispatchQueue.main.async {
                guard let self else { return }
                let saved = Self.savedCredentials()
                guard !LiveSessionState.isolatedStopRecordUnreadable(),
                      self.liveCredentials?.boundServiceURL?.absoluteString == bound.absoluteString else { return }
                let disposition = PollCallbackGate.disposition(
                    sessionID: credentials.sessionID, token: credentials.token, generation: generation,
                    currentSessionID: self.liveCredentials?.sessionID, currentToken: self.liveCredentials?.token,
                    currentGeneration: self.livePollGeneration,
                    savedSessionID: saved?.sessionID, savedToken: saved?.token,
                    active: self.liveActive, stopping: self.liveStopping)
                guard disposition != .stale else { return }
                if disposition == .savedCredentialsMismatch || saved?.boundServiceURL?.absoluteString != bound.absoluteString {
                    if self.liveConnectionPending {
                        self.livePollTask = nil
                        self.liveVerificationNeedsRetry = true
                        self.liveStatus = "本场尚未核验，采集未开启；请检查本场授权后重试"
                    }
                    return
                }
                self.liveVerificationNeedsRetry = false
                guard PollCallbackGate.commitIfCurrent(
                    sessionID: credentials.sessionID, generation: generation,
                    currentSessionID: self.liveCredentials?.sessionID,
                    currentGeneration: self.livePollGeneration,
                    active: self.liveActive, stopping: self.liveStopping,
                    commit: { ControlState.recordServiceResponse(error: transportError, response: response) }
                ) else { return }
                switch result {
                case .success(let page):
                    self.liveEntryInvalidItems += page.invalidEntryCount
                    self.liveCursor = page.nextCursor
                    let now = Int64(LiveOperationsClient.continuousNowNS() / 1_000_000)
                    let deadlines = page.prompts.map { prompt in
                        LivePromptDelivery.displayExpiry(prompt.expiresAtMS, serverTimeMS: page.serverTimeMS,
                            processingMS: page.serverProcessingMS, startNS: page.requestStartNS, endNS: page.responseEndNS)
                    }
                    let anchorValid = LivePromptDelivery.displayExpiry(page.serverTimeMS, serverTimeMS: page.serverTimeMS,
                        processingMS: page.serverProcessingMS, startNS: page.requestStartNS, endNS: page.responseEndNS) != nil
                    let items = zip(page.prompts, deadlines).map { prompt, deadline in
                        let label = prompt.sources?.contains(where: { $0.kind == "comment" }) == true ? "问答" : "运营"
                        return LivePromptDelivery.Item(id: prompt.promptID,
                            text: "【\(label)】" + prompt.displayText,
                            expiresAtMS: deadline ?? 0,
                            priority: prompt.priority, timingInvalid: deadline == nil,
                            priorityInvalid: prompt.priorityInvalid)
                    }
                    let beforeOverflow = self.liveDelivery.overflowCount
                    let beforeExpired = self.liveDelivery.expiredCount
                    let freshItems = self.liveDelivery.receive(items, nowMS: now)
                    if !freshItems.isEmpty {
                        if !anchorValid { self.liveTimingAnchorFailures += 1 }
                        else { self.liveTimingInvalidItems += freshItems.filter(\.timingInvalid).count }
                        if !anchorValid || freshItems.contains(where: \.timingInvalid) { self.liveTimingWarning = true }
                    }
                    var acceptedIDs = Set(freshItems.map(\.id))
                    let fresh = page.prompts.filter { acceptedIDs.remove($0.promptID) != nil }
                    self.livePriorityInvalidItems += fresh.filter(\.priorityInvalid).count
                    self.liveSourcesInvalidItems += fresh.filter(\.sourcesInvalid).count
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

    func retryLiveVerification() {
        guard liveVerificationNeedsRetry, liveConnectionPending, liveActive, !liveStarting, !liveStopping,
              !isolatedStopInFlight,
              let credentials = liveCredentials else { return }
        if let pending = LiveSessionState.isolatedStopIntent(),
           pending.credentials.sessionID == credentials.sessionID,
           pending.credentials.token == credentials.token,
           pending.serviceURL.absoluteString == liveClient?.baseURL.absoluteString { return }
        let saved = Self.savedCredentials()
        guard credentials.hasSameBoundIdentity(as: saved),
            PollCallbackGate.matchesCredentials(sessionID: credentials.sessionID, token: credentials.token,
            currentSessionID: liveCredentials?.sessionID, currentToken: liveCredentials?.token,
            savedSessionID: saved?.sessionID, savedToken: saved?.token) else {
            liveStatus = "本场尚未核验，采集未开启；请检查本场授权后重试"
            return
        }
        liveVerificationNeedsRetry = false
        liveStatus = "正在重新核验本场实时连接"
        pollLivePrompts()
    }

    func retryIsolatedStop() {
        guard !isolatedStopInFlight, let intent = LiveSessionState.isolatedStopIntent(),
              let client = LiveOperationsClient(baseURL: intent.serviceURL) else {
            isolatedStopStatus = "原场结束记录或服务地址不可用，未发送请求，可重试"
            return
        }
        beginIsolatedStop(intent, client: client)
    }

    private func beginIsolatedStop(_ intent: IsolatedStopIntent, client: LiveOperationsClient) {
        guard !isolatedStopInFlight else { return }
        guard client.baseURL.absoluteString == intent.serviceURL.absoluteString,
              LiveSessionState.saveIsolatedStop(intent) else {
            isolatedStopStatus = "原场结束意图未能安全保存，未发送请求，可重试"
            return
        }
        let credentials = intent.credentials
        isolatedStopAvailable = true
        isolatedStopInFlight = true
        isolatedStopGeneration &+= 1
        let generation = isolatedStopGeneration
        let startGeneration = liveStartGeneration
        if liveCredentials?.sessionID == credentials.sessionID && liveCredentials?.token == credentials.token &&
            liveClient?.baseURL.absoluteString == intent.serviceURL.absoluteString {
            livePollGeneration &+= 1
            livePollTask?.cancel()
            livePollTask = nil
            liveVerificationNeedsRetry = false
        }
        isolatedStopStatus = "正在确认原场结束"
        client.stop(credentials, recordResponse: false) { [weak self] ok, _, _ in
            DispatchQueue.main.async {
                guard let self, self.isolatedStopGeneration == generation,
                      self.liveStartGeneration == startGeneration,
                      let pending = LiveSessionState.isolatedStopIntent(), pending.matches(intent) else { return }
                self.isolatedStopInFlight = false
                guard ok else {
                    self.isolatedStopStatus = "原场结束尚未确认，可重试"
                    return
                }
                guard LiveSessionState.clearIsolatedStop(intent) else {
                    self.isolatedStopStatus = "原场结束已确认，但待结束记录未清除，可重试核销"
                    return
                }
                self.isolatedStopAvailable = false
                self.isolatedStopStatus = "原场结束已确认"
                if self.liveCredentials?.sessionID == credentials.sessionID,
                   self.liveCredentials?.token == credentials.token,
                   self.liveClient?.baseURL.absoluteString == intent.serviceURL.absoluteString {
                    if let saved = Self.savedCredentials(), saved.sessionID == credentials.sessionID,
                       saved.token == credentials.token,
                       saved.boundServiceURL?.absoluteString == intent.serviceURL.absoluteString {
                        LiveSessionState.disableCapturePreservingCredentials()
                    }
                    self.liveActive = false
                    self.liveStopped = true
                    self.liveConnectionPending = false
                    self.liveVerificationNeedsRetry = false
                    self.liveCredentials = nil
                    self.liveStatus = "原场结束已确认"
                }
            }
        }
    }

    private func deliverLivePrompts() {
        let now = Int64(LiveOperationsClient.continuousNowNS() / 1_000_000)
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
        if !liveDisplayedItems.isEmpty, !PiPDisplayController.shared.hasLiveContent {
            _ = PiPDisplayController.shared.showLiveFrame(liveDisplayedItems)
        }
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

    private static func observationUploadDescription() -> String {
        guard let defaults = AppGroup.defaults else { return "采样上传诊断不可用" }
        func value(_ key: String) -> Int { defaults.integer(forKey: "observation.upload.\(key)") }
        let overLimit = ["limit_comment", "limit_count", "bytes_comment", "bytes_count",
                         "oversize_comment", "oversize_count"]
            .reduce(0) { $0 + value($1) }
        let invalid = value("invalid_comment") + value("invalid_count")
        let sending = defaults.bool(forKey: "observation.upload.sending") ? "发送中" : "未发送中"
        return "采样上传（本机累计）：已接收 \(value("received"))，幂等重复 \(value("duplicate"))，服务端合并 \(value("merged"))；结果未知 \(value("unknown"))，服务端拒绝 \(value("rejected"))。未发送：队列满 \(value("overflow_batch")) 批（评论 \(value("overflow_comment"))、人数 \(value("overflow_count"))），超限 \(overLimit)，无效 \(invalid)，过期 \(value("expired"))，停止或断连余项 \(value("cancel_unsent"))；本机时钟回拨 \(value("clock_backward"))。最近报告：\(sending)，待发批次 \(value("waiting_batches"))。"
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

    private func overviewResponseHandler(_ credentials: LiveSessionCredentials, generation: Int)
        -> (URLResponse?, Error?) -> Void {
        { [weak self] response, error in
            DispatchQueue.main.async {
                guard let self, !LiveSessionState.isolatedStopRecordUnreadable(),
                      credentials.hasSameBoundIdentity(as: Self.savedCredentials()) else { return }
                _ = PollCallbackGate.commitIfCurrent(
                    sessionID: credentials.sessionID, generation: generation,
                    currentSessionID: Self.savedCredentials()?.sessionID,
                    currentGeneration: self.liveStartGeneration,
                    active: !self.liveActive && !self.liveStarting, stopping: self.liveStopping,
                    commit: { ControlState.recordServiceResponse(error: error, response: response) }
                )
            }
        }
    }

    private func loadStoppedOverview(_ credentials: LiveSessionCredentials, client: LiveOperationsClient) {
        guard !LiveSessionState.isolatedStopRecordUnreadable(),
              let bound = credentials.boundServiceURL, IsolatedStopIntent.safeServiceURL(bound),
              client.baseURL.absoluteString == bound.absoluteString else { return }
        let generation = liveStartGeneration
        let onResponse = overviewResponseHandler(credentials, generation: generation)
        let group = DispatchGroup()
        var fetchedEvents: LiveEventPage?
        var fetchedSummary: LiveSessionSummary?
        let requestID = overviewRequest.begin()
        overviewLoading = true
        summaryRefreshInFlight = true
        group.enter()
        client.events(credentials, onResponse: onResponse) { result in
            DispatchQueue.main.async {
                if case .success(let page) = result { fetchedEvents = page }
                group.leave()
            }
        }
        group.enter()
        client.summary(credentials, onResponse: onResponse) { result in
            DispatchQueue.main.async {
                if case .success(let summary) = result { fetchedSummary = summary }
                group.leave()
            }
        }
        group.notify(queue: .main) { [weak self] in
            guard let self, self.overviewRequest.finish(requestID) else { return }
            self.overviewLoading = false
            self.summaryRefreshInFlight = false
            guard !self.liveActive, !self.liveStarting, generation == self.liveStartGeneration,
                  !LiveSessionState.isolatedStopRecordUnreadable(),
                  credentials.hasSameBoundIdentity(as: Self.savedCredentials()) else { return }
            if let page = fetchedEvents {
                let knownIDs = Set(self.liveEvents.map(\.promptID))
                let newlyRead = page.events.filter { !knownIDs.contains($0.promptID) }
                self.livePriorityInvalidItems += newlyRead.filter(\.priorityInvalid).count
                self.liveSourcesInvalidItems += newlyRead.filter(\.sourcesInvalid).count
                self.liveEntryInvalidItems += page.invalidEntryCount
                self.liveEvents = page.events
            }
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
        guard !LiveSessionState.isolatedStopRecordUnreadable(),
              !liveActive, !liveStarting, !summaryRefreshInFlight,
              let credentials = Self.savedCredentials(),
              let bound = credentials.boundServiceURL, IsolatedStopIntent.safeServiceURL(bound),
              let client = liveClient ?? LiveOperationsClient(baseURL: bound),
              client.baseURL.absoluteString == bound.absoluteString else {
            return
        }
        summaryRefreshInFlight = true
        overviewLoading = true
        let requestID = overviewRequest.begin()
        let generation = liveStartGeneration
        client.summary(credentials, onResponse: overviewResponseHandler(credentials, generation: generation)) { [weak self] result in
            DispatchQueue.main.async {
                guard let self, self.overviewRequest.finish(requestID) else { return }
                self.summaryRefreshInFlight = false
                self.overviewLoading = false
                guard !self.liveActive, !self.liveStarting, generation == self.liveStartGeneration,
                      !LiveSessionState.isolatedStopRecordUnreadable(),
                      credentials.hasSameBoundIdentity(as: Self.savedCredentials()) else { return }
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
