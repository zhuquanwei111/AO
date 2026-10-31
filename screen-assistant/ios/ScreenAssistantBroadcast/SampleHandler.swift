import ReplayKit
import CoreImage
import CoreGraphics
import CoreVideo
import CoreMedia
import Darwin
import Foundation
import os

/// 广播上传扩展主类：跨应用采集 → 方向转正 → ROI 内存裁剪 → 中文 OCR →
/// 基础格式化 → POST /v1/answer → UDS 短回复发送。
///
/// 隐私与生命周期约束：
///   - 原始 sample/pixel buffer 不出采集回调；OCR仅保留自有ROI图像。
///     输入测试可保留一张自有小图做单在途检测；所有图像仅内存处理，不落盘。
///   - 不持久化句子/回复；运营观察只在有限的内存发送缓冲中暂留。
///   - 运营一轮 = 帧→OCR→入队；旧问答仍等待响应。忙时到达的帧直接跳过
///     （PRD：“下一轮忙则跳过以免堆积”）。
///   - 运营上传拒入及失败只记录计数与原因；下一轮继续。
///   - 204：不刷新当前 PiP 展示。
final class SampleHandler: RPBroadcastSampleHandler {

    /// 所有轮次状态只在此串行队列上读写。
    /// （ReplayKit 回调本身串行，但 OCR/网络完成闭包在后台队列触发。）
    private let stateQueue = DispatchQueue(label: "com.screenassistant.broadcast.state")

    /// 下一轮可开始的最早时刻（CFAbsoluteTime）。仅在 stateQueue 内读写。
    /// 忙时跳过新帧，不保留 sampleBuffer 队列；扩展峰值内存尚未真机测量。
    private var nextRoundEligibleTime: CFAbsoluteTime = 0
    /// 一轮是否正在进行（运营到入队为止；旧问答到响应为止）。
    private var roundActive = false
    /// 暂停、继续、结束广播时使旧 OCR 与 HTTP 回调失效。
    private var roundGeneration = 0
    private var isBroadcastPaused = false
    private var activeCaptureGeneration = 0
    private var activeRequest: URLSessionDataTask?
    private var activeOCR: OCRService.CancellationToken?
    private var observationBuffer = ObservationUploadBuffer()
    private var observationTask: URLSessionDataTask?
    private let observationSession: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.waitsForConnectivity = false
        config.timeoutIntervalForRequest = 35
        return URLSession(configuration: config)
    }()
    private let personObservationQueue = DispatchQueue(label: "com.screenassistant.person-observation-http")
    private let personObservationSession: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 5
        configuration.timeoutIntervalForResource = 7
        return URLSession(configuration: configuration)
    }()
    private var personObservationRequest: URLSessionDataTask?
    private var personObservationRequestID: UUID?

    private let ciContext = CIContext(options: [.useSoftwareRenderer: false])
    private lazy var inputDiagnostics = InputDiagnosticsSampler(context: ciContext)
    private lazy var livePersonObserver = LivePersonObserver(onObservation: { [weak self] observation, sessionID, token in
        self?.submitPersonObservation(observation, sessionID: sessionID, token: token)
    })
    private let continuousAudio = ContinuousAudioCapture()
    private let memoryLog = Logger(subsystem: "com.screenassistant.broadcast", category: "ocr-memory")
    // 40MB 为待验证工作预算、50MB 为历史风险参考；均非系统硬保证。

    // MARK: - 生命周期

    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
        ControlState.setBroadcastPhase(.waitingForVideo, resetVideoSample: true)
        resetPersonObservationTransport()
        continuousAudio.transition(running: true)
        inputDiagnostics.transition(to: .running)
        stateQueue.sync {
            nextRoundEligibleTime = 0
            roundActive = false
            isBroadcastPaused = false
            roundGeneration &+= 1
            cancelActiveWork()
        }
    }

    override func broadcastPaused() {
        ControlState.setBroadcastPhase(.paused)
        resetPersonObservationTransport()
        continuousAudio.transition(running: false)
        inputDiagnostics.transition(to: .paused)
        stateQueue.sync {
            isBroadcastPaused = true
            roundGeneration &+= 1
            roundActive = false
            cancelActiveWork()
        }
    }

    override func broadcastResumed() {
        ControlState.setBroadcastPhase(.waitingForVideo, resetVideoSample: true)
        resetPersonObservationTransport()
        continuousAudio.transition(running: true)
        inputDiagnostics.transition(to: .running)
        stateQueue.sync {
            isBroadcastPaused = false
            roundGeneration &+= 1
            roundActive = false
            nextRoundEligibleTime = 0
            cancelActiveWork()
        }
    }

    override func broadcastFinished() {
        ControlState.setBroadcastPhase(.finished)
        resetPersonObservationTransport()
        continuousAudio.transition(running: false)
        inputDiagnostics.transition(to: .finished)
        stateQueue.sync {
            roundGeneration &+= 1
            roundActive = false
            isBroadcastPaused = true
            cancelActiveWork()
        }
        ControlState.setUserWantsCapture(false)
    }

    // MARK: - 帧处理

    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer,
                                      with sampleBufferType: RPSampleBufferType) {
        // 原始输入诊断独立于视频筛选、OCR开关、轮次忙闲和远端响应。
        inputDiagnostics.observe(sampleBuffer, type: sampleBufferType)
        continuousAudio.observe(sampleBuffer, type: sampleBufferType)
        guard sampleBufferType == .video else { return }
        if CMSampleBufferDataIsReady(sampleBuffer), CMSampleBufferGetImageBuffer(sampleBuffer) != nil {
            ControlState.noteVideoSample()
        }
        livePersonObserver.observe(sampleBuffer)

        // 闸门：正在处理、暂停或间隔未到时丢弃当前帧；控制开关每帧读取，
        // 用 generation 及时使旧 OCR/HTTP 结果失效。
        let now = CFAbsoluteTimeGetCurrent()
        var startRound = false
        var generation = 0
        var captureGeneration = 0
        stateQueue.sync {
            let wantsCapture = ControlState.userWantsCapture()
            let configuredGeneration = ControlState.captureGeneration()
            if (roundActive || observationTask != nil || !observationBuffer.pending.isEmpty) &&
                (!wantsCapture || activeCaptureGeneration != configuredGeneration) {
                roundGeneration &+= 1
                roundActive = false
                cancelActiveWork()
            }
            if !isBroadcastPaused, !roundActive, now >= nextRoundEligibleTime {
                if wantsCapture {
                    roundActive = true
                    generation = roundGeneration
                    captureGeneration = configuredGeneration
                    activeCaptureGeneration = configuredGeneration
                    startRound = true
                } else {
                    nextRoundEligibleTime = now + 1
                }
            }
        }
        guard startRound else { return }

        logMemory(stage: "frame-start")
        // 每类数据只裁剪用户指定的小区域；图像只进入一个串行OCR作业，不落盘或上传。
        let observationROI = SharedConfig.roi()
        var regionROIs: [String: CGRect] = ["comment": observationROI]
        var regions: [OCRService.RegionInput] = [
            OCRService.RegionInput(identifier: "comment",
                                   image: autoreleasepool(invoking: { makeOCRImage(from: sampleBuffer, roi: observationROI) }))
        ]
        if LiveSessionState.isEnabled() {
            if let roi = SharedConfig.onlineCountROI() {
                regionROIs["online_count"] = roi
                regions.append(OCRService.RegionInput(identifier: "online_count",
                                                       image: autoreleasepool(invoking: { makeOCRImage(from: sampleBuffer, roi: roi) })))
            }
        }
        guard regions.contains(where: { $0.image != nil }) else { endRound(generation: generation); return }
        logMemory(stage: "roi-rendered")
        let sampleObservedAtMS = Int64(Date().timeIntervalSince1970 * 1000)
        let countSemanticsConfirmed = SharedConfig.onlineCountSemanticsConfirmed()
        let sampledCredentials = LiveSessionState.isEnabled() ? LiveSessionState.credentials() : nil

        // 同一个有界OCR任务串行识别评论和可选人数裁剪图。
        guard let ocr = OCRService.recognize(regions: regions, completion: { [weak self] results in
            guard let self else { return }
            self.logMemory(stage: "ocr-finished")
            guard self.isCurrentRound(generation, captureGeneration: captureGeneration) else {
                self.endRound(generation: generation)
                return
            }

            // 运营会话使用独立收件契约；旧 OCR 问答不得抢先进入 PiP。
            if LiveSessionState.isEnabled() {
                guard let credentials = sampledCredentials,
                      let baseURL = SharedConfig.serverBaseURL(),
                      baseURL.scheme == "http" || baseURL.scheme == "https" else {
                    self.endRound(generation: generation)
                    return
                }
                let commentLines = results.first(where: { $0.identifier == "comment" })?.lines ?? []
                var countPayload: [String: Any]?
                if let countROI = regionROIs["online_count"] {
                    let lines = results.first(where: { $0.identifier == "online_count" })?.lines ?? []
                    countPayload = Self.onlineCountPayload(lines: lines, roi: countROI,
                                                           observedAtMS: sampleObservedAtMS,
                                                           semanticsConfirmed: countSemanticsConfirmed)
                }
                self.enqueueSample(commentLines: commentLines, commentROI: observationROI,
                                   countPayload: countPayload, observedAtMS: sampleObservedAtMS,
                                   credentials: credentials, baseURL: baseURL,
                                   generation: generation, captureGeneration: captureGeneration)
                self.endRound(generation: generation)
                return
            }

            // 旧问答继续使用 ROI 整段文本；运营分支只处理上方筛选出的单行候选。
            let recognizedLines = results.first(where: { $0.identifier == "comment" })?.lines ?? []
            let recognizedText = recognizedLines.map(\.text).joined(separator: "\n")
            guard let sentence = SentenceFormatter.sentence(fromOCRText: recognizedText),
                  sentence.count <= SharedConfig.maxSentenceLength else {
                self.endRound(generation: generation)
                return
            }

            // 7) 服务未配置 → 本轮丢弃（主 App 设置页有明确提示）
            guard let endpoint = AnswerEndpoint(timeout: SharedConfig.requestTimeoutSeconds()) else {
                ControlState.setServiceConnection(.configurationInvalid)
                self.endRound(generation: generation)
                return
            }
            guard self.isCurrentRound(generation, captureGeneration: captureGeneration) else {
                self.endRound(generation: generation)
                return
            }
            ControlState.bumpRoundCounter()

            // 8) 请求；响应回来后本轮结束。
            let request = endpoint.request(sentence: sentence) { [weak self] outcome in
                guard let self else { return }
                guard self.isCurrentRound(generation, captureGeneration: captureGeneration) else {
                    self.endRound(generation: generation)
                    return
                }
                defer { self.endRound(generation: generation) }
                switch outcome {
                case .ok(let reply):
                    // 只发送短回复；PiP 未监听时 UDS 有界重试后丢弃载荷。
                    ReplyTransportFactory.make().send(reply: reply)
                case .duplicate:
                    // 204：保留当前展示 → 不发新通知
                    break
                case .discarded:
                    // 网络/422/503/504/超时：本轮丢弃，静默，下轮继续
                    break
                }
            }
            self.stateQueue.sync {
                if self.roundActive && !self.isBroadcastPaused &&
                    self.roundGeneration == generation &&
                    self.activeCaptureGeneration == captureGeneration &&
                    ControlState.userWantsCapture() &&
                    ControlState.captureGeneration() == captureGeneration {
                    self.activeRequest = request
                } else {
                    request?.cancel()
                }
            }
        }) else {
            endRound(generation: generation)
            return
        }
        stateQueue.sync {
            if roundActive && !isBroadcastPaused && roundGeneration == generation &&
                activeCaptureGeneration == captureGeneration {
                activeOCR = ocr
            } else {
                ocr.cancel()
            }
        }
        logMemory(stage: "ocr-submitted")
    }

    /// M7数值出口使用取样时的场次凭据；只保留一个HTTP请求在途，不等待服务端响应。
    private func submitPersonObservation(_ observation: LivePersonObservation,
                                         sessionID: String, token: String) {
        guard LiveSessionState.isEnabled(), ControlState.userWantsCapture(),
              let credentials = LiveSessionState.credentials(),
              credentials.sessionID == sessionID, credentials.token == token,
              let baseURL = SharedConfig.serverBaseURL(),
              let body = try? JSONEncoder().encode(observation) else { return }
        let url = ["v1", "live", "sessions", sessionID, "person-observations"].reduce(baseURL) {
            $0.appendingPathComponent($1, isDirectory: false)
        }
        var request = URLRequest(url: url, timeoutInterval: 5)
        request.httpMethod = "POST"
        request.setValue(token, forHTTPHeaderField: "X-Live-Session-Token")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        let requestID = UUID()
        personObservationQueue.async { [weak self] in
            guard let self, self.personObservationRequest == nil,
                  let current = LiveSessionState.credentials(),
                  current.sessionID == sessionID, current.token == token,
                  LiveSessionState.isEnabled(), ControlState.userWantsCapture() else { return }
            let task = self.personObservationSession.dataTask(with: request) { [weak self] _, _, _ in
                guard let self else { return }
                self.personObservationQueue.async {
                    guard self.personObservationRequestID == requestID else { return }
                    self.personObservationRequest = nil
                    self.personObservationRequestID = nil
                }
            }
            self.personObservationRequestID = requestID
            self.personObservationRequest = task
            task.resume()
        }
    }

    private func resetPersonObservationTransport() {
        livePersonObserver.reset()
        personObservationQueue.sync {
            personObservationRequest?.cancel()
            personObservationRequest = nil
            personObservationRequestID = nil
        }
    }

    private static func onlineCountPayload(lines: [OCRService.TextLine], roi: CGRect,
                                           observedAtMS: Int64, semanticsConfirmed: Bool) -> [String: Any] {
        let rawText = String(lines.map(\.text).joined(separator: " ").prefix(120))
        let available = !rawText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let content: Any
        if available {
            content = [
                "raw_text": rawText, "field_semantics_confirmed": semanticsConfirmed,
                "text_position": mergedPosition(lines: lines, roi: roi),
                "ocr_lines": Array(lines.prefix(20)).map { ocrLinePayload($0, roi: roi) }
            ] as [String: Any]
        } else { content = NSNull() }
        let confidence: Any
        if let value = lines.map(\.confidence).max() { confidence = Double(value) }
        else { confidence = NSNull() }
        return [
            "event_id": UUID().uuidString.lowercased(),
            "kind": "online_count", "source": "ios_replaykit_online_count_roi",
            "observed_at_ms": observedAtMS, "availability": available ? "available" : "missing",
            "coverage": "user_selected_count_roi", "reliability": "known",
            "confidence": confidence, "content": content
        ]
    }

    private static func platformNoticePayload(lines: [OCRService.TextLine], roi: CGRect,
                                              observedAtMS: Int64) -> [String: Any] {
        let text = String(lines.map(\.text).joined(separator: "\n").prefix(500))
        let available = !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let content: Any
        if available {
            content = [
                "text": text, "region_confirmed": true, "explicit_warning_confirmed": false,
                "text_position": mergedPosition(lines: lines, roi: roi),
                "ocr_lines": Array(lines.prefix(20)).map { ocrLinePayload($0, roi: roi) }
            ] as [String: Any]
        } else { content = NSNull() }
        let confidence: Any
        if let value = lines.map(\.confidence).max() { confidence = Double(value) }
        else { confidence = NSNull() }
        return [
            "event_id": UUID().uuidString.lowercased(),
            "kind": "platform_notice", "source": "ios_replaykit_platform_notice_roi",
            "observed_at_ms": observedAtMS, "availability": available ? "available" : "missing",
            "coverage": "user_selected_notice_roi", "reliability": "known",
            "confidence": confidence, "content": content
        ]
    }

    private static func ocrLinePayload(_ line: OCRService.TextLine, roi: CGRect) -> [String: Any] {
        ["text": String(line.text.prefix(200)),
         "bbox": LiveCommentFilter.screenPosition(line.boundingBox, within: roi),
         "confidence": Double(line.confidence)]
    }

    private static func mergedPosition(lines: [OCRService.TextLine], roi: CGRect) -> [String: Double] {
        let bounds = lines.map(\.boundingBox).reduce(CGRect.null) { $0.union($1) }
        return LiveCommentFilter.screenPosition(bounds, within: roi)
    }

    /// 采样轮内保持人数后评论的原有顺序；只保留可发送的有界前缀。
    private func enqueueSample(commentLines: [OCRService.TextLine], commentROI: CGRect,
                               countPayload: [String: Any]?, observedAtMS: Int64,
                               credentials: LiveSessionCredentials, baseURL: URL,
                               generation: Int, captureGeneration: Int) {
        var builder = ObservationUploadBuffer.BatchBuilder()
        var rejected: [String: Int] = [:]
        func note(_ reason: String) { rejected[reason, default: 0] += 1 }
        func append(_ event: ObservationUploadBuffer.Event) {
            let isComment = event.isComment
            switch builder.append(event) {
            case .accepted: break
            case .commentLimit: note(isComment ? "limit_comment" : "limit_count")
            case .countLimit: note("invalid_count")
            case .byteLimit: note(isComment ? "bytes_comment" : "bytes_count")
            case .itemOversize: note(isComment ? "oversize_comment" : "oversize_count")
            }
        }
        if let countPayload {
            if let data = try? JSONSerialization.data(withJSONObject: countPayload) {
                append(.init(id: UUID(), body: data, isComment: false, observedAtMS: observedAtMS))
            } else { note("invalid_count") }
        }
        for (index, line) in commentLines.enumerated() {
            guard index < ObservationUploadBuffer.BatchBuilder.maxComments else {
                note("limit_comment")
                continue
            }
            let position = LiveCommentFilter.screenPosition(line.boundingBox, within: commentROI)
            var validPosition: [String: Double]?
            if let x = position["x"], let y = position["y"], let w = position["width"],
               let h = position["height"], x.isFinite, y.isFinite, w.isFinite, h.isFinite,
               x >= 0, y >= 0, x <= 1, y <= 1, w > 0, h > 0, w <= 1, h <= 1 {
                validPosition = position
            }
            let confidence = line.confidence.isFinite && (0...1).contains(line.confidence)
                ? Double(line.confidence) : nil
            guard let event = ObservationUploadBuffer.comment(text: line.text, position: validPosition,
                    confidence: confidence, observedAtMS: observedAtMS) else {
                note("invalid_comment")
                continue
            }
            append(event)
        }
        stateQueue.sync {
            guard observationContextValid(generation: generation, captureGeneration: captureGeneration,
                                          sessionID: credentials.sessionID, token: credentials.token) else { return }
            for (reason, count) in rejected { recordObservationIssue(reason, count: count) }
            let nowMS = Int64(Date().timeIntervalSince1970 * 1000)
            let fresh = builder.events.filter { !ObservationUploadBuffer.isExpired($0, nowMS: nowMS) }
            recordObservationIssue("expired", count: builder.events.count - fresh.count)
            let batch = ObservationUploadBuffer.Batch(id: UUID(), events: fresh,
                observedAtMS: observedAtMS, generation: captureGeneration,
                sessionID: credentials.sessionID, token: credentials.token, baseURL: baseURL)
            guard !batch.events.isEmpty else { return }
            if let active = observationBuffer.active,
               (active.sessionID != credentials.sessionID || active.token != credentials.token ||
                active.generation != captureGeneration) { cancelObservationTransport() }
            switch observationBuffer.append(batch) {
            case .accepted: startNextObservation()
            case .overflow:
                recordObservationIssue("overflow_batch")
                recordObservationIssue("overflow_comment", count: batch.events.filter(\.isComment).count)
                recordObservationIssue("overflow_count", count: batch.events.filter { !$0.isComment }.count)
            case .invalid:
                recordObservationIssue("invalid_comment", count: batch.events.filter(\.isComment).count)
                recordObservationIssue("invalid_count", count: batch.events.filter { !$0.isComment }.count)
            }
            updateObservationQueueStatus()
        }
    }

    private func observationContextValid(generation: Int, captureGeneration: Int,
                                         sessionID: String, token: String) -> Bool {
        guard !isBroadcastPaused, roundGeneration == generation,
              ControlState.userWantsCapture(), ControlState.captureGeneration() == captureGeneration,
              LiveSessionState.isEnabled(), let current = LiveSessionState.credentials() else { return false }
        return current.sessionID == sessionID && current.token == token
    }

    /// 必须在 stateQueue；仅一批在途，批内逐条发送，不自动重试。
    private func startNextObservation() {
        guard observationTask == nil else { return }
        let generation = roundGeneration
        while let candidate = observationBuffer.begin(), let event = candidate.current {
            guard observationContextValid(generation: generation, captureGeneration: candidate.generation,
                                          sessionID: candidate.sessionID, token: candidate.token) else {
                cancelObservationTransport()
                return
            }
            guard ObservationUploadBuffer.isExpired(event, nowMS: Int64(Date().timeIntervalSince1970 * 1000)) else { break }
            _ = observationBuffer.complete(batchID: candidate.id, eventID: event.id)
            recordObservationIssue("expired")
        }
        guard let batch = observationBuffer.begin(), let item = batch.current else {
            updateObservationQueueStatus()
            return
        }
        if Int64(Date().timeIntervalSince1970 * 1000) < item.observedAtMS {
            recordObservationIssue("clock_backward")
        }
        guard observationContextValid(generation: generation, captureGeneration: batch.generation,
                                      sessionID: batch.sessionID, token: batch.token) else {
            cancelObservationTransport()
            return
        }
        let endpoint = ["v1", "live", "sessions", batch.sessionID,
                        item.isComment ? "comments" : "observations"].reduce(batch.baseURL) {
            $0.appendingPathComponent($1, isDirectory: false)
        }
        var request = URLRequest(url: endpoint, timeoutInterval: 20)
        request.httpMethod = "POST"
        request.setValue(batch.token, forHTTPHeaderField: "X-Live-Session-Token")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = item.body
        let task = observationSession.dataTask(with: request) { [weak self] data, response, error in
            guard let self else { return }
            self.stateQueue.async {
                guard self.observationBuffer.active?.id == batch.id,
                      self.observationBuffer.active?.current?.id == item.id else { return }
                guard self.observationContextValid(generation: generation, captureGeneration: batch.generation,
                                                   sessionID: batch.sessionID, token: batch.token) else {
                    self.cancelObservationTransport()
                    return
                }
                _ = self.observationBuffer.complete(batchID: batch.id, eventID: item.id)
                self.observationTask = nil
                ControlState.recordServiceResponse(error: error, response: response)
                switch ObservationUploadBuffer.classify(data: data,
                    statusCode: (response as? HTTPURLResponse)?.statusCode, hasError: error != nil) {
                case .received:
                    self.recordObservationIssue("received")
                case .duplicate:
                    self.recordObservationIssue("duplicate")
                case .merged:
                    self.recordObservationIssue("merged")
                case .analysisBusy:
                    self.recordObservationIssue("received")
                    self.recordObservationIssue("analysis_busy")
                case .rejected:
                    self.recordObservationIssue("rejected")
                case .unknown:
                    self.recordObservationIssue("unknown")
                    if error != nil || ((response as? HTTPURLResponse)?.statusCode ?? 0) >= 500 {
                        self.cancelObservationTransport()
                        return
                    }
                }
                self.startNextObservation()
            }
        }
        observationTask = task
        updateObservationQueueStatus()
        task.resume()
    }

    private func recordObservationIssue(_ reason: String, count: Int = 1) {
        guard count > 0 else { return }
        guard let defaults = AppGroup.defaults else { return }
        let key = "observation.upload.\(reason)"
        defaults.set(defaults.integer(forKey: key) + count, forKey: key)
        defaults.set(reason, forKey: "observation.upload.lastReason")
    }

    private func updateObservationQueueStatus() {
        guard let defaults = AppGroup.defaults else { return }
        defaults.set(observationBuffer.pending.count, forKey: "observation.upload.waiting_batches")
        defaults.set(observationTask != nil, forKey: "observation.upload.sending")
    }

    private func cancelObservationTransport() {
        let discarded = observationBuffer.discard(inFlight: observationTask != nil)
        recordObservationIssue("unknown", count: discarded.unknown)
        recordObservationIssue("cancel_unsent", count: discarded.unsent)
        observationTask?.cancel()
        observationTask = nil
        updateObservationQueueStatus()
    }

    /// 结束一轮：清 roundActive 并按配置间隔排定下一轮可开始时刻。
    private func isCurrentRound(_ generation: Int, captureGeneration: Int) -> Bool {
        stateQueue.sync {
            !isBroadcastPaused && roundGeneration == generation &&
                ControlState.userWantsCapture() &&
                ControlState.captureGeneration() == captureGeneration
        }
    }

    private func endRound(generation: Int) {
        stateQueue.sync {
            guard roundGeneration == generation else { return }
            roundActive = false
            activeOCR = nil
            activeRequest = nil
            nextRoundEligibleTime = CFAbsoluteTimeGetCurrent()
                + SharedConfig.intervalSeconds()
        }
    }

    /// Core Image 只在采集回调中读取当前帧；ROI 先裁剪再限制最长边，坐标统一限制在 extent。
    private func makeOCRImage(from sampleBuffer: CMSampleBuffer, roi: CGRect) -> CGImage? {
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return nil }
        var orientation = CGImagePropertyOrientation.up
        if let raw = CMGetAttachment(sampleBuffer,
                                     key: RPVideoSampleOrientationKey as CFString,
                                     attachmentModeOut: nil) as? NSNumber {
            orientation = CGImagePropertyOrientation(rawValue: raw.uint32Value) ?? .up
        }

        var ciImage = CIImage(cvPixelBuffer: pixelBuffer).oriented(orientation)
        let extent = ciImage.extent
        guard extent.width > 0, extent.height > 0 else { return nil }
        let normalized = CGRect(
            x: min(max(roi.minX, 0), 1),
            y: min(max(1 - roi.maxY, 0), 1),
            width: min(max(roi.width, 0), 1),
            height: min(max(roi.height, 0), 1)
        )
        let cropRect = CGRect(
            x: extent.minX + normalized.minX * extent.width,
            y: extent.minY + normalized.minY * extent.height,
            width: normalized.width * extent.width,
            height: normalized.height * extent.height
        ).intersection(extent)
        guard !cropRect.isNull, cropRect.width > 0, cropRect.height > 0 else { return nil }
        ciImage = ciImage.cropped(to: cropRect)

        let longSide = max(ciImage.extent.width, ciImage.extent.height)
        let maxDimension = SharedConfig.maxCropPixelDimension
        if longSide > maxDimension {
            let scale = maxDimension / longSide
            ciImage = ciImage.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        }
        return ciContext.createCGImage(ciImage, from: ciImage.extent)
    }

    /// 在 stateQueue 内调用；只取消句柄与网络任务，不保存图像或文字诊断内容。
    private func cancelActiveWork() {
        activeOCR?.cancel()
        activeOCR = nil
        activeRequest?.cancel()
        activeRequest = nil
        cancelObservationTransport()
        logMemory(stage: "work-cancelled")
    }

    /// 仅写固定阶段名与当前进程常驻内存数值，供 Instruments/设备日志配合采样。
    private func logMemory(stage: String) {
        var info = mach_task_basic_info()
        var count = mach_msg_type_number_t(MemoryLayout<mach_task_basic_info>.size) / 4
        let result = withUnsafeMutablePointer(to: &info) { pointer in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(MACH_TASK_BASIC_INFO), $0, &count)
            }
        }
        guard result == KERN_SUCCESS else { return }
        let rssMB = Double(info.resident_size) / 1_048_576
        memoryLog.info("mem stage=\(stage, privacy: .public) rss_mb=\(rssMB, format: .fixed(precision: 1), privacy: .public)")
    }

}
