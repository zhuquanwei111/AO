import Foundation
import CoreGraphics

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

struct LiveSessionCredentials: Codable {
    let sessionID: String
    let token: String
    let startedAtMS: Int64
    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id", token = "session_token", startedAtMS = "started_at_ms"
    }
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
    var id: String { promptID }
    enum CodingKeys: String, CodingKey {
        case promptID = "prompt_id", inputMode = "input_mode"
        case startAtMS = "start_at_ms", endAtMS = "end_at_ms"
        case phenomenon, suggestion, displayText = "display_text"
        case observationCount = "observation_count"
    }
}

struct LivePromptPage: Decodable {
    let sessionID: String
    let prompts: [LivePrompt]
    let stopped: Bool
    let nextCursor: String
    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id", prompts, stopped, nextCursor = "next_cursor"
    }
}

struct LiveEventPage: Decodable {
    let sessionID: String
    let startedAtMS: Int64
    let stoppedAtMS: Int64?
    var events: [LivePrompt]
    let nextCursor: String
    let hasMore: Bool
    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id", startedAtMS = "started_at_ms"
        case stoppedAtMS = "stopped_at_ms", events, nextCursor = "next_cursor", hasMore = "has_more"
    }
}

/// 薄适配 M2 的场次接口。所有路径都相对用户配置的服务基地址，保留其路径前缀。
struct LiveOperationsClient {
    private let baseURL: URL
    private let session: URLSession

    init?(baseURL: URL) {
        guard baseURL.scheme == "http" || baseURL.scheme == "https" else { return nil }
        self.baseURL = baseURL
        let config = URLSessionConfiguration.ephemeral
        config.waitsForConnectivity = false
        config.timeoutIntervalForRequest = 35
        self.session = URLSession(configuration: config)
    }

    func create(description: String, completion: @escaping (Result<LiveSessionCredentials, Error>) -> Void) {
        var request = URLRequest(url: endpoint("v1", "live", "sessions"), timeoutInterval: 10)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONEncoder().encode(["description": description])
        session.dataTask(with: request) { data, response, error in
            completion(Self.decode(LiveSessionCredentials.self, data: data, response: response, error: error))
        }.resume()
    }

    func stop(_ credentials: LiveSessionCredentials, completion: @escaping (Bool) -> Void) {
        var request = authorized(endpoint("v1", "live", "sessions", credentials.sessionID, "stop"), token: credentials.token)
        request.httpMethod = "POST"
        session.dataTask(with: request) { _, response, error in
            completion(error == nil && (response as? HTTPURLResponse)?.statusCode == 200)
        }.resume()
    }

    func poll(_ credentials: LiveSessionCredentials, after: String,
              completion: @escaping (Result<LivePromptPage, Error>) -> Void) -> URLSessionDataTask {
        var components = URLComponents(url: endpoint("v1", "live", "sessions", credentials.sessionID, "prompts"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "after", value: after), URLQueryItem(name: "wait_ms", value: "15000")]
        let request = authorized(components.url!, token: credentials.token)
        let task = session.dataTask(with: request) { data, response, error in
            completion(Self.decode(LivePromptPage.self, data: data, response: response, error: error))
        }
        task.resume()
        return task
    }

    func events(_ credentials: LiveSessionCredentials, completion: @escaping (Result<LiveEventPage, Error>) -> Void) {
        loadEvents(credentials, after: "", accumulated: [], completion: completion)
    }

    private func loadEvents(_ credentials: LiveSessionCredentials, after: String,
                            accumulated: [LivePrompt],
                            completion: @escaping (Result<LiveEventPage, Error>) -> Void) {
        var components = URLComponents(url: endpoint("v1", "live", "sessions", credentials.sessionID, "events"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "after", value: after), URLQueryItem(name: "limit", value: "100")]
        let request = authorized(components.url!, token: credentials.token)
        session.dataTask(with: request) { data, response, error in
            switch Self.decode(LiveEventPage.self, data: data, response: response, error: error) {
            case .failure(let error): completion(.failure(error))
            case .success(let page):
                let all = accumulated + page.events
                if page.hasMore {
                    self.loadEvents(credentials, after: page.nextCursor, accumulated: all, completion: completion)
                } else {
                    var completePage = page
                    completePage.events = all
                    completion(.success(completePage))
                }
            }
        }.resume()
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
        let task = session.dataTask(with: request) { _, _, _ in completion() }
        task.resume()
        return task
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
        let task = session.dataTask(with: request) { _, _, _ in completion() }
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

    private static func decode<T: Decodable>(_ type: T.Type, data: Data?, response: URLResponse?, error: Error?) -> Result<T, Error> {
        if let error { return .failure(error) }
        guard (response as? HTTPURLResponse)?.statusCode == 200, let data else {
            return .failure(NSError(domain: "LiveOperations", code: (response as? HTTPURLResponse)?.statusCode ?? -1))
        }
        do { return .success(try JSONDecoder().decode(type, from: data)) }
        catch { return .failure(error) }
    }
}

/// App Group 中仅保存当前场次能力令牌；结束场次时立即清空，不写模型密钥或观测正文。
enum LiveSessionState {
    private static let enabledKey = "live.enabled"
    private static var fileURL: URL? {
        AppGroup.containerURL?.appendingPathComponent("live-session.json", isDirectory: false)
    }

    static func save(_ value: LiveSessionCredentials) -> Bool {
        guard let fileURL, let defaults = AppGroup.defaults,
              let data = try? JSONEncoder().encode(value) else { return false }
        do {
            try data.write(to: fileURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            defaults.set(true, forKey: enabledKey)
            return true
        } catch {
            return false
        }
    }

    static func credentials() -> LiveSessionCredentials? {
        guard let fileURL, let data = try? Data(contentsOf: fileURL) else { return nil }
        return try? JSONDecoder().decode(LiveSessionCredentials.self, from: data)
    }
    static func isEnabled() -> Bool { AppGroup.defaults?.bool(forKey: enabledKey) ?? false }
    static func clear() {
        if let fileURL { try? FileManager.default.removeItem(at: fileURL) }
        AppGroup.defaults?.set(false, forKey: enabledKey)
    }
}
