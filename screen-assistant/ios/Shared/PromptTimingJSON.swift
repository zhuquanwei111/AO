import Foundation
import CoreFoundation

/// 校验 JSON 数字原类型；JSONDecoder 对 1.0 可接受为 Int64。
enum PromptTimingJSON {
    private static func isInteger(_ value: Any?) -> Bool {
        guard let number = value as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID() else { return false }
        let type = String(cString: number.objCType)
        return type != "f" && type != "d"
    }

    static func hasValidIntegers(_ data: Data?) -> Bool {
        guard let data,
              let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              isInteger(root["server_time_ms"]), isInteger(root["server_processing_ms"]),
              let prompts = root["prompts"] as? [[String: Any]] else { return false }
        return prompts.allSatisfy { isInteger($0["expires_at_ms"]) }
    }
}
