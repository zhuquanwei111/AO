package com.screenassistant;

import org.json.JSONArray;
import org.json.JSONObject;
import java.text.DateFormat;
import java.util.Calendar;
import java.util.Date;
import java.util.Locale;

/** 仅整理复盘的展示内容，不改变服务端记录。 */
final class RecapPresentation {
    private RecapPresentation() { }

    static String summary(JSONObject data) {
        long start = data.optLong("started_at_ms", 0), end = data.optLong("stopped_at_ms", 0);
        boolean dates = start > 0 && end > 0 && !sameDay(start, end);
        StringBuilder out = new StringBuilder("本场概况\n开始：").append(time(start, dates))
                .append("\n结束：").append(time(end, dates));
        if (start > 0 && end >= start) out.append("\n时长：").append(duration(end - start));
        JSONObject count = data.optJSONObject("online_count");
        boolean hasCount = appendCount(out, count);
        if (!hasCount && count != null && "multiple_sources".equals(count.optString("status"))) {
            JSONArray sources = data.optJSONArray("online_count_by_source");
            if (sources != null) for (int i = 0; i < sources.length(); i++)
                hasCount |= appendCount(out, sources.optJSONObject(i));
        }
        if (hasCount) out.append("\n仅供参考，可能不完整");
        else out.append("\n暂无可用的在线人数数据");
        JSONObject backstage = data.optJSONObject("backstage_recap");
        if (backstage != null && backstage.optJSONObject("metrics") != null) {
            JSONObject metrics = backstage.optJSONObject("metrics");
            String[] names = {"累计观众", "新增粉丝", "评论人数", "送礼人数", "获得音浪"};
            boolean any = false;
            for (String name : names) if (metrics.has(name) && !metrics.isNull(name)) {
                if (!any) { out.append("\n\n平台数据"); any = true; }
                out.append("\n").append(name).append("：");
                JSONArray approximate = backstage.optJSONArray("approximate_metrics");
                if (approximate != null) for (int i = 0; i < approximate.length(); i++)
                    if (name.equals(approximate.optString(i))) { out.append("约 "); break; }
                out.append(metrics.opt(name));
            }
            if (any && !"captured_locally".equals(backstage.optString("status"))) out.append("\n部分数据暂不可用");
        }
        return out.toString();
    }

    private static boolean appendCount(StringBuilder out, JSONObject count) {
        if (count == null) return false;
        String status = count.optString("status");
        JSONObject peak = count.optJSONObject("peak");
        if (!("available".equals(status) || "partial".equals(status)) || peak == null
                || !peak.has("nominal_value") || peak.isNull("nominal_value")) return false;
        out.append("\n参考人数峰值：");
        if (peak.optBoolean("approximate")) out.append("约 ");
        out.append(peak.opt("nominal_value"));
        return true;
    }

    static String events(JSONArray rows) {
        StringBuilder out = new StringBuilder("建议记录（").append(rows.length()).append("）");
        if (rows.length() == 0) return out.append("\n本场暂无建议记录").toString();
        for (int i = 0; i < rows.length(); i++) {
            JSONObject row = rows.optJSONObject(i);
            if (row == null) continue;
            String type = row.optString("prompt_type");
            String label = "comment_reply".equals(type) ? "评论回复"
                    : "operation_notice".equals(type) ? "运营建议" : "建议";
            out.append("\n\n").append(label);
            if (row.has("start_offset_ms") && !row.isNull("start_offset_ms") && row.optLong("start_offset_ms", -1) >= 0)
                out.append(" · 开场后 ").append(duration(row.optLong("start_offset_ms")));
            String body = DisplayText.plain(row.optString("suggestion", ""));
            if (body.isEmpty()) body = DisplayText.plain(row.optString("display_text", ""));
            out.append("\n").append(body.isEmpty() ? "暂无建议内容" : body);
        }
        return out.toString();
    }

    private static boolean sameDay(long a, long b) {
        Calendar left = Calendar.getInstance(), right = Calendar.getInstance();
        left.setTimeInMillis(a); right.setTimeInMillis(b);
        return left.get(Calendar.ERA) == right.get(Calendar.ERA)
                && left.get(Calendar.YEAR) == right.get(Calendar.YEAR)
                && left.get(Calendar.DAY_OF_YEAR) == right.get(Calendar.DAY_OF_YEAR);
    }
    private static String time(long value, boolean date) {
        if (value <= 0) return "暂未确认";
        Locale locale = Locale.getDefault();
        DateFormat format = date ? DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT, locale)
                : DateFormat.getTimeInstance(DateFormat.SHORT, locale);
        return format.format(new Date(value));
    }
    private static String duration(long millis) {
        long seconds = Math.max(0, millis / 1000);
        return seconds >= 60 ? (seconds / 60) + "分" + (seconds % 60) + "秒" : seconds + "秒";
    }
}
