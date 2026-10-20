import Foundation

/// OCR 句子的基础格式化（客户端只做格式化，不做去重、不做推理）。
enum SentenceFormatter {
    /// 基础格式化：按行合并空白、去首尾空白，再按近似阅读顺序拼接成单句。
    /// 返回 nil 表示空白（调用方按“空 OCR 跳过”处理）。
    static func sentence(fromOCRText raw: String) -> String? {
        let normalized = raw
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")

        // 1. 每行：合并内部空白，去首尾空白，丢弃空行
        let cleanedLines: [String] = normalized
            .split(separator: "\n", omittingEmptySubsequences: true)
            .compactMap { line in
                let collapsed = String(line)
                    .components(separatedBy: .whitespacesAndNewlines)
                    .joined()
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                return collapsed.isEmpty ? nil : collapsed
            }
        guard !cleanedLines.isEmpty else { return nil }

        // 2. 行拼接：中文/中文标点/全角之间不加空格；ASCII 字母数字之间补一个空格。
        var result = cleanedLines[0]
        for line in cleanedLines.dropFirst() {
            if joinsWithoutSpace(result.last, line.first) {
                result += line
            } else {
                result += " " + line
            }
        }

        // 3. 整句去首尾空白；空白 → nil
        let trimmed = result.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    /// 两侧都是非 ASCII 文本形态（CJK/中文标点/全角）时直接拼接。
    private static func joinsWithoutSpace(_ left: Character?, _ right: Character?) -> Bool {
        guard let l = left, let r = right else { return true }
        if isCJKLike(l) || isCJKLike(r) { return true }
        // 标点紧贴前后词，不加空格
        if isPunctuation(l) || isPunctuation(r) { return true }
        return false
    }

    private static func isCJKLike(_ ch: Character) -> Bool {
        guard let scalar = ch.unicodeScalars.first else { return false }
        let v = scalar.value
        return (0x4E00...0x9FFF).contains(v)    // CJK 统一表意
            || (0x3400...0x4DBF).contains(v)    // 扩展 A
            || (0xF900...0xFAFF).contains(v)    // 兼容表意
            || (0x3000...0x303F).contains(v)    // CJK 标点
            || (0xFF00...0xFFEF).contains(v)    // 全角形式
    }

    private static func isPunctuation(_ ch: Character) -> Bool {
        guard let scalar = ch.unicodeScalars.first else { return false }
        switch scalar.properties.generalCategory {
        case .connectorPunctuation, .dashPunctuation, .openPunctuation,
             .closePunctuation, .initialPunctuation, .finalPunctuation,
             .otherPunctuation:
            return true
        default:
            return false
        }
    }
}
