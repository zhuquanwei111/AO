import Foundation

/// 仅描述服务端已返回的状态；任务登记、领取与结果是不同事实。
enum BackstageStatusPresentation {
    static func acceptsStartCallback(generation: Int, currentGeneration: Int,
                                     starting: Bool, stopping: Bool) -> Bool {
        starting && !stopping && generation == currentGeneration
    }

    static func summary(status: String?, claimedAtMS: Int64?, receivedMetricCount: Int? = nil) -> String {
        switch status {
        case "waiting_for_stop":
            return claimedAtMS == nil ? "后台任务已登记，取数状态待确认" : "取数任务已领取，等待本场结果"
        case "capturing":
            return "取数任务已领取，等待本场结果"
        case "not_connected":
            return "本场后台取数不可用，未取得本场复盘；不自动补领"
        case "captured_locally":
            return receivedMetricCount == 5 ? "后台五项汇总已收到" : "后台结果状态已返回，五项读数待确认"
        case "partial":
            return receivedMetricCount == nil ? "后台结果状态为部分，读数尚未取得" : "后台汇总部分缺失，请核对缺项"
        case "needs_input":
            return "后台任务需要补充信息"
        case "failed":
            return "后台汇总失败，请检查任务状态"
        case "not_configured":
            return "未配置后台取数任务"
        default:
            return "后台取数状态待确认"
        }
    }

    static func active(status: String?) -> String {
        "实时场次已创建；\(summary(status: status, claimedAtMS: nil))；请按下方步骤启动本机提示与屏幕广播"
    }

    static func recovered(status: String?) -> String {
        "运营收件已恢复；\(summary(status: status, claimedAtMS: nil))"
    }
}
