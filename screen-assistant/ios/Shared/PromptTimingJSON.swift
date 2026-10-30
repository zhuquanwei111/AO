import Foundation
import CoreFoundation

/// 校验 JSON 数字原类型；JSONDecoder 对 1.0 可接受为 Int64。
enum PromptTimingJSON {
    private static func integer(_ value: Any?) -> Int64? {
        guard let number = value as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let type = String(cString: number.objCType)
        guard type != "f" && type != "d" else { return nil }
        return Int64(number.stringValue)
    }

    struct Inspection {
        let serverTimeMS: Int64?
        let processingMS: Int64?
        let expiries: [Int64?]
    }

    static func inspect(_ data: Data?) -> Inspection? {
        guard let data,
              let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let prompts = root["prompts"] as? [[String: Any]] else { return nil }
        return Inspection(serverTimeMS: integer(root["server_time_ms"]),
                          processingMS: integer(root["server_processing_ms"]),
                          expiries: prompts.map { integer($0["expires_at_ms"]) })
    }
}
