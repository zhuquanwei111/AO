import Foundation
import CoreGraphics

/// 运行配置：主 App 写入，广播扩展每轮开始前**重新读取**（保证即时生效、不留过期快照）。
///
/// 隐私约束（PRD）：App Group 仅存配置项、ControlState 控制标志及 InputDiagnostics 数值摘要；
/// 严禁写入识别句子、回复文本、画面数据或任何历史记录。
struct SharedConfig {
    // MARK: - 键（全部限定在受控清单内）

    private enum Keys {
        static let serverBaseURL = "config.serverBaseURL"
        static let douyinAccount = "config.douyinAccount"
        static let intervalSeconds = "config.intervalSeconds"
        static let requestTimeoutSeconds = "config.requestTimeoutSeconds"
        static let roiX = "config.roiX"
        static let roiY = "config.roiY"
        static let roiWidth = "config.roiWidth"
        static let roiHeight = "config.roiHeight"
        static let onlineCountROIEnabled = "config.onlineCountROI.enabled"
        static let onlineCountROIX = "config.onlineCountROI.x"
        static let onlineCountROIY = "config.onlineCountROI.y"
        static let onlineCountROIWidth = "config.onlineCountROI.width"
        static let onlineCountROIHeight = "config.onlineCountROI.height"
        static let platformNoticeROIEnabled = "config.platformNoticeROI.enabled"
        static let platformNoticeROIX = "config.platformNoticeROI.x"
        static let platformNoticeROIY = "config.platformNoticeROI.y"
        static let platformNoticeROIWidth = "config.platformNoticeROI.width"
        static let platformNoticeROIHeight = "config.platformNoticeROI.height"
        static let personROIEnabled = "config.personROI.enabled"
        static let personROIX = "config.personROI.x"
        static let personROIY = "config.personROI.y"
        static let personROIWidth = "config.personROI.width"
        static let personROIHeight = "config.personROI.height"
    }

    // MARK: - 边界常量

    static let intervalRange: ClosedRange<Double> = 1...60
    static let timeoutRange: ClosedRange<Double> = 2...30
    /// 发送前的单句长度上限（超出视为无效轮次，跳过，与契约中 422 的客户端侧一致处理）。
    static let maxSentenceLength = 2000
    /// ROI 裁剪后送 OCR 的最长边（像素）。兼顾扩展内存上限与识别精度。
    static let maxCropPixelDimension: CGFloat = 640

    // MARK: - 读写

    /// 服务基地址，如 `http://192.168.1.10:8000`。nil/空/无法解析 = 未配置。
    static func serverBaseURL() -> URL? {
        guard let raw = serverBaseURLRaw(), !raw.isEmpty,
              let url = URL(string: raw),
              let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https"
        else { return nil }
        return url
    }

    /// 原始字符串（未校验），供设置界面回显。
    static func serverBaseURLRaw() -> String? {
        AppGroup.defaults?.string(forKey: Keys.serverBaseURL)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func setServerBaseURL(_ raw: String) {
        AppGroup.defaults?.set(raw, forKey: Keys.serverBaseURL)
    }

    static func douyinAccount() -> String {
        AppGroup.defaults?.string(forKey: Keys.douyinAccount)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    static func setDouyinAccount(_ raw: String) {
        AppGroup.defaults?.set(raw.trimmingCharacters(in: .whitespacesAndNewlines),
                               forKey: Keys.douyinAccount)
    }

    /// 轮次间隔（秒）。越界时回退默认值。
    static func intervalSeconds() -> Double {
        let v = AppGroup.defaults?.double(forKey: Keys.intervalSeconds) ?? 0
        return intervalRange.contains(v) ? v : 3
    }

    static func setIntervalSeconds(_ v: Double) {
        let clamped = intervalRange.contains(v) ? v : 3
        AppGroup.defaults?.set(clamped, forKey: Keys.intervalSeconds)
    }

    /// 请求超时（秒）。
    static func requestTimeoutSeconds() -> Double {
        let v = AppGroup.defaults?.double(forKey: Keys.requestTimeoutSeconds) ?? 0
        return timeoutRange.contains(v) ? v : 8
    }

    static func setRequestTimeoutSeconds(_ v: Double) {
        let clamped = timeoutRange.contains(v) ? v : 8
        AppGroup.defaults?.set(clamped, forKey: Keys.requestTimeoutSeconds)
    }

    /// 识别区域：归一化矩形（0..1，**左上原点**，相对竖屏满屏）。
    /// iOS 无跨应用任意拖选能力，区域由主 App 内的预设/编辑器设置（PRD 允许的降级）。
    static func roi() -> CGRect {
        let d = AppGroup.defaults
        let r = CGRect(
            x: d?.double(forKey: Keys.roiX) ?? 0.05,
            y: d?.double(forKey: Keys.roiY) ?? 0.30,
            width: d?.double(forKey: Keys.roiWidth) ?? 0.90,
            height: d?.double(forKey: Keys.roiHeight) ?? 0.25
        )
        // 防御：确保在合法范围内且 w/h > 0
        let x = min(max(r.minX, 0), 1)
        let y = min(max(r.minY, 0), 1)
        let w = min(max(r.width, 0.01), 1 - x)
        let h = min(max(r.height, 0.01), 1 - y)
        return CGRect(x: x, y: y, width: w, height: h)
    }

    static func setROI(_ r: CGRect) {
        let x = min(max(r.minX, 0), 1)
        let y = min(max(r.minY, 0), 1)
        let w = min(max(r.width, 0.01), 1 - x)
        let h = min(max(r.height, 0.01), 1 - y)
        let d = AppGroup.defaults
        d?.set(x, forKey: Keys.roiX)
        d?.set(y, forKey: Keys.roiY)
        d?.set(w, forKey: Keys.roiWidth)
        d?.set(h, forKey: Keys.roiHeight)
    }

    static func onlineCountROI() -> CGRect? {
        configuredROI(enabledKey: Keys.onlineCountROIEnabled, xKey: Keys.onlineCountROIX,
                      yKey: Keys.onlineCountROIY, widthKey: Keys.onlineCountROIWidth,
                      heightKey: Keys.onlineCountROIHeight)
    }

    static func setOnlineCountROI(_ roi: CGRect?) {
        setConfiguredROI(roi, enabledKey: Keys.onlineCountROIEnabled, xKey: Keys.onlineCountROIX,
                         yKey: Keys.onlineCountROIY, widthKey: Keys.onlineCountROIWidth,
                         heightKey: Keys.onlineCountROIHeight)
    }

    static func platformNoticeROI() -> CGRect? {
        configuredROI(enabledKey: Keys.platformNoticeROIEnabled, xKey: Keys.platformNoticeROIX,
                      yKey: Keys.platformNoticeROIY, widthKey: Keys.platformNoticeROIWidth,
                      heightKey: Keys.platformNoticeROIHeight)
    }

    static func setPlatformNoticeROI(_ roi: CGRect?) {
        setConfiguredROI(roi, enabledKey: Keys.platformNoticeROIEnabled, xKey: Keys.platformNoticeROIX,
                         yKey: Keys.platformNoticeROIY, widthKey: Keys.platformNoticeROIWidth,
                         heightKey: Keys.platformNoticeROIHeight)
    }

    /// 直播人物采样区域；未设置时人物观察器继续沿用既有输入诊断区域。
    static func personROI() -> CGRect? {
        configuredROI(enabledKey: Keys.personROIEnabled, xKey: Keys.personROIX,
                      yKey: Keys.personROIY, widthKey: Keys.personROIWidth,
                      heightKey: Keys.personROIHeight)
    }

    static func setPersonROI(_ roi: CGRect?) {
        setConfiguredROI(roi, enabledKey: Keys.personROIEnabled, xKey: Keys.personROIX,
                         yKey: Keys.personROIY, widthKey: Keys.personROIWidth,
                         heightKey: Keys.personROIHeight)
    }

    private static func configuredROI(enabledKey: String, xKey: String, yKey: String,
                                      widthKey: String, heightKey: String) -> CGRect? {
        guard let defaults = AppGroup.defaults, defaults.bool(forKey: enabledKey) else { return nil }
        let x = defaults.double(forKey: xKey)
        let y = defaults.double(forKey: yKey)
        let width = defaults.double(forKey: widthKey)
        let height = defaults.double(forKey: heightKey)
        guard x.isFinite, y.isFinite, width.isFinite, height.isFinite,
              x >= 0, y >= 0, width > 0, height > 0, x + width <= 1, y + height <= 1 else { return nil }
        return CGRect(x: x, y: y, width: width, height: height)
    }

    private static func setConfiguredROI(_ roi: CGRect?, enabledKey: String, xKey: String, yKey: String,
                                         widthKey: String, heightKey: String) {
        guard let defaults = AppGroup.defaults else { return }
        guard let roi else {
            defaults.set(false, forKey: enabledKey)
            defaults.removeObject(forKey: xKey)
            defaults.removeObject(forKey: yKey)
            defaults.removeObject(forKey: widthKey)
            defaults.removeObject(forKey: heightKey)
            return
        }
        let x = min(max(roi.minX, 0), 1)
        let y = min(max(roi.minY, 0), 1)
        let width = min(max(roi.width, 0.01), 1 - x)
        let height = min(max(roi.height, 0.01), 1 - y)
        defaults.set(x, forKey: xKey)
        defaults.set(y, forKey: yKey)
        defaults.set(width, forKey: widthKey)
        defaults.set(height, forKey: heightKey)
        defaults.set(true, forKey: enabledKey)
    }

}

/// 用户确认后可一次应用；归一化坐标使用竖屏满屏左上原点。
enum LiveAreaPresets {
    static let comments = CGRect(x: 0.03, y: 0.69, width: 0.82, height: 0.25)
    static let onlineCount = CGRect(x: 0.80, y: 0.055, width: 0.10, height: 0.045)
    static let person = CGRect(x: 0.00, y: 0.14, width: 1.00, height: 0.80)
}
