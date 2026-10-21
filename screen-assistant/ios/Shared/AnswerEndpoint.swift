import Foundation

/// 固定 HTTP 契约客户端：POST /v1/answer。
///
/// 契约（与远端 server=multi-agent-build-37 对齐）：
///   请求：JSON，只含 sentence 字符串。
///   200  → {"status":"ok","reply":"非空字符串"}
///   204  → 重复，无响应体，客户端保留当前展示（不刷新）
///   422  → 空白/无效/超限输入，本轮按失败跳过
///   503/504 → 远端不可用/模型失败，不弹窗，下轮继续
///
/// 客户端约束（PRD）：
///   - 不做本地去重，不携带历史，无鉴权
///   - 每轮最多一个在途请求；调用方保证
///   - 网络超时可配置（SharedConfig.requestTimeoutSeconds）
///   - 不把 204 之外的任何失败当作成功
struct AnswerEndpoint {
    enum Outcome {
        /// 200：获得新回复
        case ok(reply: String)
        /// 204：重复，保留当前展示
        case duplicate
        /// 422/503/504/网络错误/解码失败：本轮丢弃
        case discarded
    }

    /// 单例 URLSession：不持久 Cookie、不缓存（配置 URLSessionConfiguration 无状态）。
    private static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    private let baseURL: URL
    private let timeout: TimeInterval

    init?(baseURL: URL, timeout: TimeInterval) {
        guard baseURL.scheme == "http" || baseURL.scheme == "https" else { return nil }
        self.baseURL = baseURL
        self.timeout = timeout
    }

    init?(timeout: TimeInterval) {
        guard let base = SharedConfig.serverBaseURL() else { return nil }
        self.init(baseURL: base, timeout: timeout)
    }

    /// 发送单句请求。完成后在 completion queue 回调结果。
    /// 请求与响应体仅存在于内存，方法返回后不再持有。
    func request(sentence: String,
                 completionQueue: DispatchQueue = .global(),
                 completion: @escaping (Outcome) -> Void) -> URLSessionDataTask? {
        // 契约固定为 POST {base}/v1/answer
        let url = baseURL.appendingPathComponent("v1/answer")

        var request = URLRequest(url: url, timeoutInterval: timeout)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body: [String: String] = ["sentence": sentence]
        // 编码失败：本轮丢弃
        guard let httpBody = try? JSONSerialization.data(withJSONObject: body) else {
            completionQueue.async { completion(.discarded) }
            return nil
        }
        request.httpBody = httpBody

        let task = Self.session.dataTask(with: request) { data, response, _ in
            let outcome = Self.map(data: data, response: response)
            completionQueue.async { completion(outcome) }
        }
        task.resume()
        return task
    }

    /// 响应映射（纯函数，便于单元测试）。
    static func map(data: Data?, response: URLResponse?) -> Outcome {
        guard let http = response as? HTTPURLResponse else { return .discarded }
        switch http.statusCode {
        case 200:
            // {"status":"ok","reply":"..."}，reply 必须非空字符串
            guard let data,
                  let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  let status = obj["status"] as? String,
                  status == "ok",
                  let reply = obj["reply"] as? String,
                  !reply.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            else { return .discarded }
            return .ok(reply: reply)
        case 204:
            // 重复：无响应体，保留当前展示
            return .duplicate
        default:
            // 422 / 503 / 504 / 其他：本轮丢弃
            return .discarded
        }
    }
}
