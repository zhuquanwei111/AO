package com.screenassistant;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import org.json.JSONArray;
import org.json.JSONObject;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** 可见期间有限刷新；离页不继续轮询，重开从同一场次重新读取。 */
public class RecapActivity extends Activity {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private TextView summary, events, status;
    private LiveClient.Session session;
    private boolean visible, busy;
    private int rounds;
    private JSONArray loadedEvents;
    private final Runnable refresh = () -> load(false);

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        session = Prefs.getSession(this);
        LinearLayout column = new LinearLayout(this); column.setOrientation(LinearLayout.VERTICAL);
        int p = Math.round(16 * getResources().getDisplayMetrics().density); column.setPadding(p, p, p, p);
        ScrollView scroll = new ScrollView(this); scroll.addView(column); setContentView(scroll);
        TextView title = text(column); title.setText("本场复盘"); title.setTextSize(22);
        Button reload = new Button(this); reload.setText("刷新本场"); column.addView(reload);
        reload.setOnClickListener(v -> {
            if (session == null) session = Prefs.getSession(this);
            rounds = 0; load(true);
        });
        status = text(column); summary = text(column); events = text(column);
    }

    private TextView text(LinearLayout parent) {
        TextView t = new TextView(this); t.setTextSize(16); t.setPadding(0, 12, 0, 12);
        t.setTextIsSelectable(true); parent.addView(t); return t;
    }
    @Override protected void onStart() { super.onStart(); CaptureService.setAssistantUiVisible(true); visible = true; rounds = 0; load(true); }
    @Override protected void onStop() { CaptureService.setAssistantUiVisible(false); visible = false; main.removeCallbacks(refresh); super.onStop(); }
    @Override protected void onDestroy() { io.shutdown(); super.onDestroy(); }

    private void load(boolean includeEvents) {
        if (!visible || busy) return;
        if (session == null) {
            JSONObject cache=Prefs.getCachedRecap(this);
            if (cache==null) status.setText(Prefs.createPending(this)
                    ? "场次创建未确认，复盘不可用；没有重复创建。"
                    : "尚无已确认场次；服务未连接时不会生成复盘或模型建议。");
            else {
                status.setText("最近场次已完成；显示已保存复盘（已清理场次令牌）。");
                JSONObject saved=cache.optJSONObject("summary"); JSONArray savedEvents=cache.optJSONArray("events");
                if (saved!=null) summary.setText(renderSummary(saved));
                if (savedEvents!=null) events.setText(renderEvents(savedEvents));
            }
            return;
        }
        busy = true; rounds++; main.removeCallbacks(refresh);
        status.setText("读取本场…");
        JSONArray previousEvents=loadedEvents;
        io.execute(() -> {
            JSONObject result = null; JSONArray rows = null; String error = "";
            try { result = LiveClient.call(session, "summary", null); }
            catch (Exception e) { error = "摘要读取失败：" + e.getMessage(); }
            if (includeEvents) {
                try { rows = LiveClient.events(session); }
                catch (Exception e) { error += " 事件读取失败：" + e.getMessage(); }
            }
            JSONArray completeEvents=rows==null?previousEvents:rows;
            boolean cached=result!=null && completeEvents!=null && RecapText.terminal(result)
                    && Prefs.cacheTerminalRecap(this,session,result,completeEvents);
            final JSONObject data = result; final JSONArray eventRows = rows; final String message = error;
            main.post(() -> {
                busy = false;
                if (!visible) return;
                status.setText(message.isEmpty() ? "已读取本场；在线统计是屏幕采样，后台五项另列。"
                        : "复盘读取不可用；本地辅助保持原状态。\n" + message);
                if (data != null) summary.setText(renderSummary(data));
                if (eventRows != null) { loadedEvents=eventRows; events.setText(renderEvents(eventRows)); }
                if (cached) session=null;
                if (rounds < 24 && (data == null || pending(data))) main.postDelayed(refresh, 5000);
                else if (data!=null && pending(data)) status.append("\n本轮自动刷新已暂停，点刷新或重开可继续。");
            });
        });
    }

    static boolean pending(JSONObject data) {
        return RecapText.pending(data);
    }

    static String renderSummary(JSONObject data) {
        StringBuilder s = new StringBuilder("开始：").append(time(data.optLong("started_at_ms")))
                .append("\n结束：").append(time(data.optLong("stopped_at_ms"))).append("\n\n在线人数（OCR可见样本）\n");
        JSONObject count = data.optJSONObject("online_count");
        if (count!=null && "multiple_sources".equals(count.optString("status"))) {
            s.append("多来源样本分别统计，不合并峰值/均值。\n");
            JSONArray sources=data.optJSONArray("online_count_by_source");
            if (sources!=null) for (int i=0;i<sources.length();i++) {
                JSONObject source=sources.optJSONObject(i);
                if (source!=null) { s.append("\n来源 ").append(i+1).append("\n"); appendCount(s,source); }
            }
        } else if (count!=null) appendCount(s,count);
        else s.append("未取得在线样本统计");
        s.append("\n\n").append(RecapText.backstage(data.optJSONObject("backstage_recap")));
        return s.toString();
    }

    private static void appendCount(StringBuilder s,JSONObject count) {
        s.append("状态：").append(count.optString("status")).append("\n峰值：").append(number(count.optJSONObject("peak")))
                .append("\n时间加权均值：").append(number(count.optJSONObject("time_weighted_average")));
        JSONObject coverage=count.optJSONObject("coverage");
        if (coverage!=null) {
            s.append("\n记录/有效样本：").append(coverage.has("recorded_samples")?coverage.opt("recorded_samples"):"未知")
                    .append(" / ").append(coverage.has("valid_value_samples")?coverage.opt("valid_value_samples"):"未知");
            String[] keys={"covered_duration_ms","uncovered_between_samples_ms","uncovered_before_first_valid_ms","uncovered_after_last_valid_ms"};
            String[] labels={"覆盖秒数","样本间未覆盖秒数","首个有效样本前未覆盖秒数","末个有效样本后未覆盖秒数"};
            for (int i=0;i<keys.length;i++) if (coverage.has(keys[i])) s.append("\n").append(labels[i]).append("：")
                    .append(coverage.isNull(keys[i])?"未知":coverage.optLong(keys[i])/1000.0);
            JSONObject issues=coverage.optJSONObject("sample_issues");
            if (issues!=null && issues.length()>0) s.append("\n样本问题：").append(issues);
        }
    }

    private static String number(JSONObject o) {
        return o == null || o.isNull("nominal_value") ? "未知" : (o.optBoolean("approximate") ? "约 " : "") + o.opt("nominal_value");
    }
    static String renderEvents(JSONArray rows) {
        StringBuilder s = new StringBuilder("事件（").append(rows.length()).append("）\n");
        for (int i = 0; i < rows.length(); i++) {
            JSONObject e = rows.optJSONObject(i); if (e == null) continue;
            s.append("\n").append(time(e.optLong("start_at_ms"))).append(" ～ ").append(time(e.optLong("end_at_ms")))
                    .append("\n开场+").append(e.optLong("start_offset_ms") / 1000.0).append("秒 ～ +")
                    .append(e.optLong("end_offset_ms") / 1000.0).append("秒")
                    .append("\n现象：").append(DisplayText.plain(e.optString("phenomenon")))
                    .append("\n建议：").append(DisplayText.plain(e.optString("suggestion")))
                    .append("\n观察次数：").append(e.optInt("observation_count"))
                    .append("\n时间依据：").append("observed".equals(e.optString("time_basis"))?"客户端观察时间（未独立认证）":"采集/接收时间的近似映射")
                    .append("\n送达：").append("returned_to_client".equals(e.optString("delivery_state"))?"接口已返回到客户端（不代表已阅读）":"已生成，未确认返回");
            JSONArray sources=e.optJSONArray("sources");
            if (sources!=null) for (int j=0;j<sources.length();j++) {
                JSONObject row=sources.optJSONObject(j); if (row==null) continue;
                String kind=row.optString("kind"),label;
                switch (kind) {
                    case "comment": label="公屏OCR样本"; break;
                    case "online_count": label="在线人数OCR样本"; break;
                    case "person_state": label="人物数值候选"; break;
                    case "transcript": label="音轨转写"; break;
                    case "platform_notice": label="用户指定通知区域"; break;
                    case "quality": label="采集诊断"; break;
                    default: label="其他观察样本";
                }
                s.append("\n依据：").append(label);
                if (row.has("evidence_text")) s.append(" · ").append(DisplayText.plain(row.optString("evidence_text")));
            }
            s.append("\n");
        }
        return s.toString();
    }
    private static String time(long ms) { return ms <= 0 ? "未提供" : new SimpleDateFormat("MM-dd HH:mm:ss", Locale.CHINA).format(new Date(ms)); }
}
