package com.screenassistant;

import org.json.JSONArray;
import org.json.JSONObject;

/** 冻结v1共享summary的纯Java展示适配。缺值不补零，后台与OCR统计分开。 */
final class RecapText {
    private static final String[] METRICS={"累计观众","新增粉丝","评论人数","送礼人数","获得音浪"};
    static boolean pending(JSONObject summary) {
        JSONObject recap=summary.optJSONObject("backstage_recap");
        if (recap==null) return false;
        String status=recap.optString("status");
        return status.equals("waiting_for_stop") || status.equals("capturing");
    }
    static boolean terminal(JSONObject summary) {
        JSONObject recap=summary.optJSONObject("backstage_recap");
        if (recap==null || summary.isNull("stopped_at_ms")) return false;
        String status=recap.optString("status");
        return status.equals("captured_locally") || status.equals("partial") || status.equals("needs_input")
                || status.equals("failed") || status.equals("not_configured") || status.equals("not_connected");
    }
    static String backstage(JSONObject recap) {
        if (recap==null) return "后台五项：服务尚未提供，未取得数据";
        String status=recap.optString("status");
        StringBuilder s=new StringBuilder("后台五项\n状态：").append(statusLabel(status));
        s.append("\n抖音号：").append(value(recap,"douyin_account"));
        JSONObject metrics=recap.optJSONObject("metrics"),raw=recap.optJSONObject("raw_displays"),missing=recap.optJSONObject("missing_fields");
        JSONArray approximate=recap.optJSONArray("approximate_metrics");
        for (String key:METRICS) {
            s.append("\n").append(key).append("：");
            if (metrics==null || metrics.isNull(key) || !metrics.has(key)) {
                s.append("未取得");
                if (missing!=null && missing.has(key)) s.append("（").append(missing.optString(key)).append("）");
            } else {
                boolean approx=false;
                if (approximate!=null) for (int i=0;i<approximate.length();i++) if (key.equals(approximate.optString(i))) approx=true;
                if (approx) s.append("约 ");
                s.append(metrics.opt(key));
                if (raw!=null && raw.has(key)) s.append("（页面：").append(raw.optString(key)).append("）");
            }
        }
        s.append("\n采集时间：").append(value(recap,"metrics_captured_at"));
        if (!recap.isNull("capture_method")) s.append("\n采集方式：").append(value(recap,"capture_method"));
        if (!recap.isNull("value_semantics")) s.append("\n口径：").append(value(recap,"value_semantics"));
        s.append("\n后台五项按停止信号后的页面汇总口径；音浪不换算金额，评论人数不是弹幕条数。");
        return s.toString();
    }
    private static String value(JSONObject o,String key) { return !o.has(key)||o.isNull(key)?"未提供":o.optString(key); }
    private static String statusLabel(String s) {
        switch (s) {
            case "not_configured": return "未配置抖音号";
            case "not_started": return "后台任务尚未启动";
            case "waiting_for_stop": return "等待本场结束";
            case "not_connected": return "本场后台复盘不可取数";
            case "capturing": return "后台正在取数";
            case "captured_locally": return "已读取五项";
            case "partial": return "部分数据缺失";
            case "needs_input": return "需要核对账号/后台状态";
            case "failed": return "取数失败，未补零";
            default: return "未知（"+s+"），请刷新";
        }
    }
}
