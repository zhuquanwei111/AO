import Foundation
import CoreGraphics
import Darwin

enum LiveCommentFilter {
    static func isWorthwhileQuestion(_ text: String) -> Bool {
        let compact = text.filter { !$0.isWhitespace }
        let knownQuestion = ["哪里", "哪儿", "来自", "老家在", "几点开播", "什么时候开播"]
        if knownQuestion.contains(where: { compact.contains($0) }) { return true }
        guard compact.contains(where: { "?？".contains($0) }) else { return false }
        return ["什么", "怎么", "哪里", "哪儿", "几点", "多少", "为何", "吗", "呢", "么"]
            .contains(where: { compact.contains($0) })
    }

    /// 将 Vision 裁剪图内、左下原点的框映射回全屏左上原点归一化坐标。
    static func screenPosition(_ box: CGRect, within roi: CGRect) -> [String: Double] {
        [
            "x": Double(roi.minX + box.minX * roi.width),
            "y": Double(roi.minY + (1 - box.maxY) * roi.height),
            "width": Double(box.width * roi.width),
            "height": Double(box.height * roi.height)
        ]
    }
}

struct LiveCapabilities: Codable {
    let observationBatchMetadata: Bool?
    let typedPrompts: Bool?
    var supportsObservationBatch: Bool {
        observationBatchMetadata == true
    }
    enum CodingKeys: String, CodingKey {
        case observationBatchMetadata = "observation_batch_metadata", typedPrompts = "typed_prompts"
    }
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        observationBatchMetadata = try? values.decode(Bool.self, forKey: .observationBatchMetadata)
        typedPrompts = try? values.decode(Bool.self, forKey: .typedPrompts)
    }
}

struct LiveSessionCredentials: Codable {
    let sessionID: String
    let token: String
    let startedAtMS: Int64
    let douyinAccount: String?
    var backstageTaskID: String?
    var backstageStatus: String?
    /// 仅本地保存的状态标记，不发送给服务端。
    var backstageStartPending: Bool?
    var liveConnectionPending: Bool?
    var stopPending: Bool? = nil
    var capabilities: LiveCapabilities? = nil
    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id", token = "session_token", startedAtMS = "started_at_ms"
        case douyinAccount = "douyin_account"
        case backstageTaskID = "backstage_task_id", backstageStatus = "backstage_status"
        case backstageStartPending = "backstage_start_pending"
        case liveConnectionPending = "live_connection_pending"
        case stopPending = "stop_pending"
        case capabilities
    }
    init(sessionID: String, token: String, startedAtMS: Int64, douyinAccount: String?,
         backstageTaskID: String?, backstageStatus: String?, backstageStartPending: Bool?,
         liveConnectionPending: Bool?, stopPending: Bool? = nil, capabilities: LiveCapabilities? = nil) {
        self.sessionID = sessionID; self.token = token; self.startedAtMS = startedAtMS
        self.douyinAccount = douyinAccount; self.backstageTaskID = backstageTaskID
        self.backstageStatus = backstageStatus; self.backstageStartPending = backstageStartPending
        self.liveConnectionPending = liveConnectionPending; self.stopPending = stopPending
        self.capabilities = capabilities
    }
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        sessionID = try values.decode(String.self, forKey: .sessionID)
        token = try values.decode(String.self, forKey: .token)
        startedAtMS = try values.decode(Int64.self, forKey: .startedAtMS)
        douyinAccount = try values.decodeIfPresent(String.self, forKey: .douyinAccount)
        backstageTaskID = try values.decodeIfPresent(String.self, forKey: .backstageTaskID)
        backstageStatus = try values.decodeIfPresent(String.self, forKey: .backstageStatus)
        backstageStartPending = try values.decodeIfPresent(Bool.self, forKey: .backstageStartPending)
        liveConnectionPending = try values.decodeIfPresent(Bool.self, forKey: .liveConnectionPending)
        stopPending = try values.decodeIfPresent(Bool.self, forKey: .stopPending)
        capabilities = try? values.decode(LiveCapabilities.self, forKey: .capabilities)
    }
}

private struct LiveBackstageStart: Decodable {
    let status: String
    let sessionID: String
    let taskID: String?
    let douyinAccount: String?
    let taskStatus: String
    enum CodingKeys: String, CodingKey {
        case status, sessionID = "session_id", taskID = "task_id"
        case douyinAccount = "douyin_account", taskStatus = "task_status"
    }
}

struct LiveOperationsHTTPError: Error {
    let statusCode: Int
    let detail: String?

    var isBackstageTaskBusy: Bool {
        statusCode == 409 && detail == "a backstage recap task is already active"
    }
}

private struct LiveOperationsErrorPayload: Decodable {
    let detail: String?
}

struct LivePrompt: Codable, Identifiable {
    let promptID: String
    let inputMode: String
    let startAtMS: Int64
    let endAtMS: Int64
    let phenomenon: String
    let suggestion: String
    let displayText: String
    let observationCount: Int
    var expiresAtMS: Int64?
    var priority: Int?
    let sources: [LivePromptSource]?
    var promptType: String?
    var replyTo: LivePromptReplyTo?
    var priorityInvalid: Bool
    let sourcesInvalid: Bool
    var id: String { promptID }
    mutating func applyCapabilities(_ capabilities: LiveCapabilities?) {
        if capabilities?.typedPrompts != true {
            promptType = nil
            replyTo = nil
        }
    }
    enum CodingKeys: String, CodingKey {
        case promptID = "prompt_id", inputMode = "input_mode"
        case startAtMS = "start_at_ms", endAtMS = "end_at_ms"
        case phenomenon, suggestion, displayText = "display_text"
        case observationCount = "observation_count"
        case expiresAtMS = "expires_at_ms", priority, sources
        case promptType = "prompt_type", replyTo = "reply_to"
    }
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        promptID = try values.decode(String.self, forKey: .promptID)
        inputMode = try values.decode(String.self, forKey: .inputMode)
        startAtMS = try values.decode(Int64.self, forKey: .startAtMS)
        endAtMS = try values.decode(Int64.self, forKey: .endAtMS)
        phenomenon = try values.decode(String.self, forKey: .phenomenon)
        suggestion = try values.decode(String.self, forKey: .suggestion)
        displayText = try values.decode(String.self, forKey: .displayText)
        observationCount = try values.decode(Int.self, forKey: .observationCount)
        expiresAtMS = try? values.decode(Int64.self, forKey: .expiresAtMS)
        priority = try? values.decode(Int.self, forKey: .priority)
        priorityInvalid = priority == nil
        if !values.contains(.sources) || (try? values.decodeNil(forKey: .sources)) == true {
            sources = nil
            sourcesInvalid = false
        } else if var entries = try? values.nestedUnkeyedContainer(forKey: .sources) {
            var valid: [LivePromptSource] = []
            var invalid = false
            while !entries.isAtEnd {
                let decoder = try entries.superDecoder()
                if let source = try? LivePromptSource(from: decoder) { valid.append(source) }
                else { invalid = true }
            }
            sources = valid
            sourcesInvalid = invalid
        } else {
            sources = nil
            sourcesInvalid = true
        }
        promptType = try? values.decode(String.self, forKey: .promptType)
        replyTo = try? values.decode(LivePromptReplyTo.self, forKey: .replyTo)
    }
}

struct LivePromptReplyTo: Codable {
    let commentID: String
    let nickname: String?
    let text: String
    enum CodingKeys: String, CodingKey {
        case commentID = "comment_id", nickname, text
    }
}

struct LivePromptSource: Codable {
    let kind: String?
}

/// 只隔离无法解码的单条提示，保留有效项在原始页面中的位置。
private struct LivePromptItems: Decodable {
    let items: [LivePrompt]
    let indices: [Int]
    let invalidCount: Int
    init(from decoder: Decoder) throws {
        var entries = try decoder.unkeyedContainer()
        var valid: [LivePrompt] = []
        var positions: [Int] = []
        var invalid = 0
        while !entries.isAtEnd {
            let index = entries.currentIndex
            let itemDecoder = try entries.superDecoder()
            if let item = try? LivePrompt(from: itemDecoder) {
                valid.append(item)
                positions.append(index)
            } else { invalid += 1 }
        }
        items = valid
        indices = positions
        invalidCount = invalid
    }
}

struct LivePromptPage: Decodable {
    let sessionID: String
    var prompts: [LivePrompt]
    let rawPromptIndices: [Int]
    let invalidEntryCount: Int
    let stopped: Bool
    let nextCursor: String
    var serverTimeMS: Int64?
    var serverProcessingMS: Int64?
    var requestStartNS: UInt64?
    var responseEndNS: UInt64?
    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id", prompts, stopped, nextCursor = "next_cursor"
        case serverTimeMS = "server_time_ms", serverProcessingMS = "server_processing_ms"
    }
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        sessionID = try values.decode(String.self, forKey: .sessionID)
        let decoded = try values.decode(LivePromptItems.self, forKey: .prompts)
        prompts = decoded.items
        rawPromptIndices = decoded.indices
        invalidEntryCount = decoded.invalidCount
        stopped = try values.decode(Bool.self, forKey: .stopped)
        nextCursor = try values.decode(String.self, forKey: .nextCursor)
        serverTimeMS = try? values.decode(Int64.self, forKey: .serverTimeMS)
        serverProcessingMS = try? values.decode(Int64.self, forKey: .serverProcessingMS)
    }
}

struct LiveEventPage: Decodable {
    let sessionID: String
    let startedAtMS: Int64
    let stoppedAtMS: Int64?
    var events: [LivePrompt]
    let nextCursor: String
    let hasMore: Bool
    let rawEventIndices: [Int]
    var invalidEntryCount: Int
    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id", startedAtMS = "started_at_ms"
        case stoppedAtMS = "stopped_at_ms", events, nextCursor = "next_cursor", hasMore = "has_more"
    }
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        sessionID = try values.decode(String.self, forKey: .sessionID)
        startedAtMS = try values.decode(Int64.self, forKey: .startedAtMS)
        stoppedAtMS = try values.decodeIfPresent(Int64.self, forKey: .stoppedAtMS)
        let decoded = try values.decode(LivePromptItems.self, forKey: .events)
        events = decoded.items
        rawEventIndices = decoded.indices
        invalidEntryCount = decoded.invalidCount
        nextCursor = try values.decode(String.self, forKey: .nextCursor)
        hasMore = try values.decode(Bool.self, forKey: .hasMore)
    }
}

struct LiveCountPeak: Decodable {
    let nominalValue: Double
    let rawText: String
    let approximate: Bool
    let displayUnit: String
    let textPosition: [String: Double]?
    let observedAtMS: Int64
    let confidence: Double?
    enum CodingKeys: String, CodingKey {
        case nominalValue = "nominal_value", rawText = "raw_text", approximate
        case displayUnit = "display_unit", textPosition = "text_position"
        case observedAtMS = "observed_at_ms", confidence
    }
}

struct LiveCountAverage: Decodable {
    let nominalValue: Double
    let approximate: Bool
    let method: String
    enum CodingKeys: String, CodingKey {
        case nominalValue = "nominal_value", approximate, method
    }
}

struct LiveCountCoverage: Decodable {
    let recordedSamples: Int?
    let validValueSamples: Int?
    let coveredDurationMS: Int64?
    let uncoveredBetweenSamplesMS: Int64?
    let uncoveredIntervals: Int?
    let longestUncoveredIntervalMS: Int64?
    let uncoveredBeforeFirstValidMS: Int64?
    let uncoveredAfterLastValidMS: Int64?
    let sampleIssues: [String: Int]?
    enum CodingKeys: String, CodingKey {
        case recordedSamples = "recorded_samples", validValueSamples = "valid_value_samples"
        case coveredDurationMS = "covered_duration_ms"
        case uncoveredBetweenSamplesMS = "uncovered_between_samples_ms"
        case uncoveredIntervals = "uncovered_intervals"
        case longestUncoveredIntervalMS = "longest_uncovered_interval_ms"
        case uncoveredBeforeFirstValidMS = "uncovered_before_first_valid_ms"
        case uncoveredAfterLastValidMS = "uncovered_after_last_valid_ms"
        case sampleIssues = "sample_issues"
    }
}

struct LiveOnlineCountSummary: Decodable {
    let status: String
    let peak: LiveCountPeak?
    let timeWeightedAverage: LiveCountAverage?
    let coverage: LiveCountCoverage
    enum CodingKeys: String, CodingKey {
        case status, peak, timeWeightedAverage = "time_weighted_average", coverage
    }
}

struct LiveBackstageMetrics: Decodable {
    let cumulativeAudience: Int?
    let newFollowers: Int?
    let commentCount: Int?
    let giftGivers: Int?
    let soundEnergy: Int?
    enum CodingKeys: String, CodingKey {
        case cumulativeAudience = "累计观众"
        case newFollowers = "新增粉丝"
        case commentCount = "评论人数"
        case giftGivers = "送礼人数"
        case soundEnergy = "获得音浪"
    }
}

struct LiveBackstageRecap: Decodable {
    let taskID: String
    let status: String
    let douyinAccount: String
    let roomID: String?
    let stopSignalReceived: Bool
    let startedAtMS: Int64
    let stoppedAtMS: Int64?
    let claimedAtMS: Int64?
    let receivedAtMS: Int64?
    let metrics: LiveBackstageMetrics?
    let rawDisplays: [String: String]?
    let approximateMetrics: [String]
    let metricsCapturedAt: String?
    let captureMethod: String?
    let valueSemantics: String?
    let missingFields: [String: String]
    let startSignalSource: String
    let stopSignalSource: String?
    enum CodingKeys: String, CodingKey {
        case taskID = "task_id", status, douyinAccount = "douyin_account"
        case roomID = "room_id", stopSignalReceived = "stop_signal_received"
        case startedAtMS = "started_at_ms", stoppedAtMS = "stopped_at_ms"
        case claimedAtMS = "claimed_at_ms", receivedAtMS = "received_at_ms"
        case metrics, rawDisplays = "raw_displays", approximateMetrics = "approximate_metrics"
        case metricsCapturedAt = "metrics_captured_at", captureMethod = "capture_method"
        case valueSemantics = "value_semantics", missingFields = "missing_fields"
        case startSignalSource = "start_signal_source", stopSignalSource = "stop_signal_source"
    }
}

struct LiveSessionSummary: Decodable {
    let sessionID: String
    let startedAtMS: Int64
    let stoppedAtMS: Int64?
    let onlineCount: LiveOnlineCountSummary
    let backstageRecap: LiveBackstageRecap?
    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id", startedAtMS = "started_at_ms"
        case stoppedAtMS = "stopped_at_ms", onlineCount = "online_count"
        case backstageRecap = "backstage_recap"
    }
}

/// 薄适配 M2 的场次接口。所有路径都相对用户配置的服务基地址，保留其路径前缀。
struct LiveOperationsClient {
    private let baseURL: URL
    private let session: URLSession

    static func continuousNowNS() -> UInt64 { clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW) }

    init?(baseURL: URL, session: URLSession? = nil) {
        guard baseURL.scheme == "http" || baseURL.scheme == "https" else { return nil }
        self.baseURL = baseURL
        let config = URLSessionConfiguration.ephemeral
        config.waitsForConnectivity = false
        config.timeoutIntervalForRequest = 35
        self.session = session ?? URLSession(configuration: config)
    }

    func create(description: String, douyinAccount: String,
                completion: @escaping (Result<LiveSessionCredentials, Error>) -> Void) {
        var request = URLRequest(url: endpoint("v1", "live", "sessions"), timeoutInterval: 10)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONEncoder().encode([
            "description": description,
            "douyin_account": douyinAccount
        ])
        session.dataTask(with: request) { data, response, error in
            completion(Self.decode(LiveSessionCredentials.self, data: data, response: response, error: error,
                                   acceptedStatusCodes: [200, 201]))
        }.resume()
    }

    func start(_ credentials: LiveSessionCredentials,
               completion: @escaping (Result<LiveSessionCredentials, Error>) -> Void) {
        var request = authorized(endpoint("v1", "live", "sessions", credentials.sessionID, "start"),
                                 token: credentials.token)
        request.httpMethod = "POST"
        session.dataTask(with: request) { data, response, error in
            switch Self.decode(LiveBackstageStart.self, data: data, response: response, error: error) {
            case .failure(let error): completion(.failure(error))
            case .success(let started):
                guard started.sessionID == credentials.sessionID,
                      started.douyinAccount == credentials.douyinAccount,
                      (started.status == "started" || started.status == "duplicate"),
                      let taskID = started.taskID, !taskID.isEmpty else {
                    completion(.failure(NSError(domain: "LiveOperations", code: -2)))
                    return
                }
                var active = credentials
                active.backstageTaskID = taskID
                active.backstageStatus = started.taskStatus
                active.backstageStartPending = false
                active.liveConnectionPending = false
                completion(.success(active))
            }
        }.resume()
    }

    func stop(_ credentials: LiveSessionCredentials, recordResponse: Bool = true,
              completion: @escaping (Bool, URLResponse?, Error?) -> Void) {
        var request = authorized(endpoint("v1", "live", "sessions", credentials.sessionID, "stop"), token: credentials.token)
        request.httpMethod = "POST"
        session.dataTask(with: request) { _, response, error in
            if recordResponse { ControlState.recordServiceResponse(error: error, response: response) }
            completion(error == nil && (response as? HTTPURLResponse)?.statusCode == 200, response, error)
        }.resume()
    }

    func poll(_ credentials: LiveSessionCredentials, after: String,
              completion: @escaping (Result<LivePromptPage, Error>, URLResponse?, Error?) -> Void) -> URLSessionDataTask {
        var components = URLComponents(url: endpoint("v1", "live", "sessions", credentials.sessionID, "prompts"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "after", value: after), URLQueryItem(name: "wait_ms", value: "15000")]
        let request = authorized(components.url!, token: credentials.token)
        let requestStartNS = Self.continuousNowNS()
        let task = session.dataTask(with: request) { data, response, error in
            let responseEndNS = Self.continuousNowNS()
            switch Self.decode(LivePromptPage.self, data: data, response: response, error: error,
                               recordResponse: false) {
            case .failure(let failure): completion(.failure(failure), response, error)
            case .success(var page):
                guard page.sessionID == credentials.sessionID else {
                    completion(.failure(NSError(domain: "LiveOperations", code: -3)), response, error)
                    return
                }
                for index in page.prompts.indices {
                    page.prompts[index].applyCapabilities(credentials.capabilities)
                }
                if let timing = PromptTimingJSON.inspect(data),
                   page.rawPromptIndices.allSatisfy({ $0 < timing.expiries.count && $0 < timing.priorities.count }) {
                    page.serverTimeMS = timing.serverTimeMS
                    page.serverProcessingMS = timing.processingMS
                    for index in page.prompts.indices {
                        page.prompts[index].expiresAtMS = timing.expiries[page.rawPromptIndices[index]]
                    }
                    Self.applyStrictPriorities(&page.prompts, rawIndices: page.rawPromptIndices,
                                               priorities: timing.priorities)
                } else {
                    page.serverTimeMS = nil
                    page.serverProcessingMS = nil
                    for index in page.prompts.indices {
                        page.prompts[index].priority = nil
                        page.prompts[index].priorityInvalid = true
                    }
                }
                page.requestStartNS = requestStartNS
                page.responseEndNS = responseEndNS
                completion(.success(page), response, error)
            }
        }
        task.resume()
        return task
    }

    func events(_ credentials: LiveSessionCredentials,
                onResponse: @escaping (URLResponse?, Error?) -> Void,
                completion: @escaping (Result<LiveEventPage, Error>) -> Void) {
        loadEvents(credentials, after: "", accumulated: [], invalidCount: 0,
                   onResponse: onResponse, completion: completion)
    }

    func summary(_ credentials: LiveSessionCredentials,
                 onResponse: @escaping (URLResponse?, Error?) -> Void,
                 completion: @escaping (Result<LiveSessionSummary, Error>) -> Void) {
        let request = authorized(endpoint("v1", "live", "sessions", credentials.sessionID, "summary"),
                                 token: credentials.token)
        session.dataTask(with: request) { data, response, error in
            onResponse(response, error)
            completion(Self.decode(LiveSessionSummary.self, data: data, response: response, error: error,
                                   recordResponse: false))
        }.resume()
    }

    private func loadEvents(_ credentials: LiveSessionCredentials, after: String,
                            accumulated: [LivePrompt], invalidCount: Int,
                            onResponse: @escaping (URLResponse?, Error?) -> Void,
                            completion: @escaping (Result<LiveEventPage, Error>) -> Void) {
        var components = URLComponents(url: endpoint("v1", "live", "sessions", credentials.sessionID, "events"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "after", value: after), URLQueryItem(name: "limit", value: "100")]
        let request = authorized(components.url!, token: credentials.token)
        session.dataTask(with: request) { data, response, error in
            onResponse(response, error)
            switch Self.decode(LiveEventPage.self, data: data, response: response, error: error,
                               recordResponse: false) {
            case .failure(let error): completion(.failure(error))
            case .success(var page):
                for index in page.events.indices {
                    page.events[index].applyCapabilities(credentials.capabilities)
                }
                let timing = PromptTimingJSON.inspect(data, collectionKey: "events")
                Self.applyStrictPriorities(&page.events, rawIndices: page.rawEventIndices,
                                           priorities: timing?.priorities ?? [])
                let all = accumulated + page.events
                if page.hasMore {
                    self.loadEvents(credentials, after: page.nextCursor, accumulated: all,
                                    invalidCount: invalidCount + page.invalidEntryCount,
                                    onResponse: onResponse, completion: completion)
                } else {
                    var completePage = page
                    completePage.events = all
                    completePage.invalidEntryCount += invalidCount
                    completion(.success(completePage))
                }
            }
        }.resume()
    }

    private static func applyStrictPriorities(_ prompts: inout [LivePrompt], rawIndices: [Int],
                                              priorities: [Int64?]) {
        for index in prompts.indices {
            guard index < rawIndices.count, rawIndices[index] < priorities.count,
                  let raw = priorities[rawIndices[index]], let priority = Int(exactly: raw) else {
                prompts[index].priority = nil
                prompts[index].priorityInvalid = true
                continue
            }
            prompts[index].priority = priority
            prompts[index].priorityInvalid = false
        }
    }

    func submitObservation(_ text: String, textPosition: [String: Double], observedAtMS: Int64,
                           completion: @escaping () -> Void) -> URLSessionDataTask? {
        guard let credentials = LiveSessionState.credentials() else { return nil }
        var request = authorized(endpoint("v1", "live", "sessions", credentials.sessionID, "comments"), token: credentials.token)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "event_id": UUID().uuidString.lowercased(),
            "input_mode": "live_observation",
            "source": "ios_replaykit_ocr",
            "coverage": "ocr_visible_sample",
            "reliability": "known",
            "text": String(text.prefix(500)),
            "text_position": textPosition,
            "observed_at_ms": observedAtMS
        ]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        let task = session.dataTask(with: request) { _, response, error in
            ControlState.recordServiceResponse(error: error, response: response)
            completion()
        }
        task.resume()
        return task
    }

    /// 按序发送一个采样周期内的typed数值/提醒观察，始终只有一个HTTP收件请求在途。
    func submitObservations(_ payloads: [[String: Any]],
                            shouldContinue: @escaping () -> Bool,
                            onTask: @escaping (URLSessionDataTask?) -> Void,
                            completion: @escaping () -> Void) {
        func submit(_ index: Int) {
            guard index < payloads.count, shouldContinue(),
                  let credentials = LiveSessionState.credentials() else {
                onTask(nil)
                completion()
                return
            }
            var request = authorized(endpoint("v1", "live", "sessions", credentials.sessionID,
                                               "observations"), token: credentials.token)
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try? JSONSerialization.data(withJSONObject: payloads[index])
            let task = session.dataTask(with: request) { _, response, error in
                ControlState.recordServiceResponse(error: error, response: response)
                guard shouldContinue() else {
                    onTask(nil)
                    completion()
                    return
                }
                submit(index + 1)
            }
            onTask(task)
            task.resume()
        }
        submit(0)
    }

    func submitPersonSignal(startedAtMS: Int64, observedAtMS: Int64, confidence: Double,
                            sampleCount: Int, completion: @escaping () -> Void) -> URLSessionDataTask? {
        guard let credentials = LiveSessionState.credentials() else { return nil }
        var request = authorized(endpoint("v1", "live", "sessions", credentials.sessionID, "signals"), token: credentials.token)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "event_id": UUID().uuidString.lowercased(),
            "source": "ios_replaykit_vision",
            "signal": "head_down",
            "confidence": confidence,
            "sample_count": sampleCount,
            "started_at_ms": startedAtMS,
            "observed_at_ms": observedAtMS
        ]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        let task = session.dataTask(with: request) { _, response, error in
            ControlState.recordServiceResponse(error: error, response: response)
            completion()
        }
        task.resume()
        return task
    }

    private func authorized(_ url: URL, token: String) -> URLRequest {
        var request = URLRequest(url: url, timeoutInterval: 20)
        request.setValue(token, forHTTPHeaderField: "X-Live-Session-Token")
        return request
    }

    private func endpoint(_ components: String...) -> URL {
        components.reduce(baseURL) { $0.appendingPathComponent($1, isDirectory: false) }
    }

    private static func decode<T: Decodable>(_ type: T.Type, data: Data?, response: URLResponse?, error: Error?,
                                             acceptedStatusCodes: Set<Int> = [200],
                                             recordResponse: Bool = true) -> Result<T, Error> {
        if let error {
            if recordResponse { ControlState.recordServiceResponse(error: error, response: response) }
            return .failure(error)
        }
        let statusCode = (response as? HTTPURLResponse)?.statusCode ?? -1
        guard acceptedStatusCodes.contains(statusCode), let data else {
            if recordResponse { ControlState.recordServiceResponse(error: nil, response: response) }
            let detail = data.flatMap { try? JSONDecoder().decode(LiveOperationsErrorPayload.self, from: $0).detail }
            return .failure(LiveOperationsHTTPError(statusCode: statusCode, detail: detail))
        }
        if recordResponse { ControlState.recordServiceResponse(error: nil, response: response) }
        do { return .success(try JSONDecoder().decode(type, from: data)) }
        catch { return .failure(error) }
    }
}

/// App Group 中只保存当前场次能力令牌；停止采集后为晚到summary暂留，任务终态后清除。
enum LiveSessionState {
    private static let enabledKey = "live.enabled"
    private static var fileURL: URL? {
        AppGroup.containerURL?.appendingPathComponent("live-session.json", isDirectory: false)
    }

    static func recoverActive(_ value: LiveSessionCredentials?, captureEnabled: Bool) -> Bool {
        value != nil && (captureEnabled || value?.stopPending == true)
    }

    static func holdCaptureOnRecovery(_ value: LiveSessionCredentials?) -> Bool {
        value?.stopPending == true || value?.liveConnectionPending == true
    }

    static func save(_ value: LiveSessionCredentials, enableCapture: Bool = true) -> Bool {
        guard let fileURL, let defaults = AppGroup.defaults else { return false }
        return save(value, fileURL: fileURL, defaults: defaults, enableCapture: enableCapture)
    }

    static func save(_ value: LiveSessionCredentials, fileURL: URL, defaults: UserDefaults,
                     enableCapture: Bool = true) -> Bool {
        guard let data = try? JSONEncoder().encode(value) else { return false }
        do {
            try data.write(to: fileURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            defaults.set(enableCapture, forKey: enabledKey)
            return true
        } catch {
            return false
        }
    }

    static func credentials() -> LiveSessionCredentials? {
        guard let fileURL else { return nil }
        return credentials(fileURL: fileURL)
    }
    static func credentials(fileURL: URL) -> LiveSessionCredentials? {
        guard let data = try? Data(contentsOf: fileURL) else { return nil }
        return try? JSONDecoder().decode(LiveSessionCredentials.self, from: data)
    }
    static func confirmStopped(_ value: LiveSessionCredentials) -> Bool {
        guard let fileURL, let defaults = AppGroup.defaults else { return false }
        return confirmStopped(value, fileURL: fileURL, defaults: defaults)
    }
    static func confirmStopped(_ value: LiveSessionCredentials, fileURL: URL, defaults: UserDefaults) -> Bool {
        var confirmed = value
        confirmed.stopPending = false
        return save(confirmed, fileURL: fileURL, defaults: defaults, enableCapture: false)
    }
    static func isEnabled() -> Bool { AppGroup.defaults?.bool(forKey: enabledKey) ?? false }
    static func disableCapturePreservingCredentials() {
        AppGroup.defaults?.set(false, forKey: enabledKey)
    }
    static func clear() {
        if let fileURL { try? FileManager.default.removeItem(at: fileURL) }
        AppGroup.defaults?.set(false, forKey: enabledKey)
    }
}
