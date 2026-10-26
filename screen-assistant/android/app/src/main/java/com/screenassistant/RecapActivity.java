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
    private final Runnable refresh = () -> load(false);

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        session = Prefs.getSession(this);
        LinearLayout column = new LinearLayout(this); column.setOrientation(LinearLayout.VERTICAL);
        int p = Math.round(16 * getResources().getDisplayMetrics().density); column.setPadding(p, p, p, p);
        ScrollView scroll = new ScrollView(this); scroll.addView(column); setContentView(scroll);
        TextView title = text(column); title.setText("本场复盘"); title.setTextSize(22);
        Button reload = new Button(this); reload.setText("刷新本场"); column.addView(reload);
        reload.setOnClickListener(v -> { rounds = 0; load(true); });
        status = text(column); summary = text(column); events = text(column);
    }

    private TextView text(LinearLayout parent) {
        TextView t = new TextView(this); t.setTextSize(16); t.setPadding(0, 12, 0, 12);
        t.setTextIsSelectable(true); parent.addView(t); return t;
    }
    @Override protected void onStart() { super.onStart(); CaptureService.uiVisible = true; visible = true; rounds = 0; load(true); }
    @Override protected void onStop() { CaptureService.uiVisible = false; visible = false; main.removeCallbacks(refresh); super.onStop(); }
    @Override protected void onDestroy() { io.shutdown(); super.onDestroy(); }

    private void load(boolean includeEvents) {
        if (!visible || busy) return;
        if (session == null) { status.setText("尚无场次"); return; }
        busy = true; rounds++; main.removeCallbacks(refresh);
        status.setText("读取本场…");
        io.execute(() -> {
            JSONObject result = null; JSONArray rows = null; String error = "";
            try { result = LiveClient.call(session, "summary", null); }
            catch (Exception e) { error = "摘要读取失败：" + e.getMessage(); }
            if (includeEvents) {
                try { rows = LiveClient.events(session); }
                catch (Exception e) { error += " 事件读取失败：" + e.getMessage(); }
            }
            final JSONObject data = result; final JSONArray eventRows = rows; final String message = error;
            main.post(() -> {
                busy = false;
                if (!visible) return;
                status.setText(message.isEmpty() ? "已读取本场；在线统计是屏幕采样，后台五项另列。" : message);
                if (data != null) summary.setText(renderSummary(data));
                if (eventRows != null) events.setText(renderEvents(eventRows));
                if (rounds < 24 && (data == null || pending(data))) main.postDelayed(refresh, 5000);
            });
        });
    }

    static boolean pending(JSONObject data) {
        JSONObject recap = data.optJSONObject("backstage_recap");
        if (recap == null) return false;
        String state = recap.optString("status");
        return state.equals("queued") || state.equals("binding") || state.equals("waiting_for_stop") || state.equals("capturing");
    }

    static String renderSummary(JSONObject data) {
        StringBuilder s = new StringBuilder("开始：").append(time(data.optLong("started_at_ms")))
                .append("\n结束：").append(time(data.optLong("stopped_at_ms"))).append("\n\n在线人数（OCR可见样本）\n");
        JSONObject count = data.optJSONObject("online_count");
        if (count != null) {
            s.append("状态：").append(count.optString("status")).append("\n峰值：").append(number(count.optJSONObject("peak")))
                    .append("\n时间加权均值：").append(number(count.optJSONObject("time_weighted_average")));
            JSONObject coverage = count.optJSONObject("coverage");
            if (coverage != null) s.append("\n记录/有效样本：").append(coverage.optInt("recorded_samples"))
                    .append(" / ").append(coverage.optInt("valid_value_samples"))
                    .append("\n覆盖秒数：").append(coverage.optLong("covered_duration_ms") / 1000.0)
                    .append("\n样本问题：").append(coverage.optJSONObject("sample_issues"));
        }
        s.append("\n\n后台五项：");
        if (data.optJSONObject("backstage_recap") == null) s.append("服务尚未提供，未取得数据");
        else s.append("等待冻结契约接入");
        return s.toString();
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
                    .append("\n来源：").append(e.optString("input_mode")).append(" / ").append(e.optString("time_basis"))
                    .append("\n送达：").append(e.optString("delivery_state")).append("\n");
        }
        return s.toString();
    }
    private static String time(long ms) { return ms <= 0 ? "未提供" : new SimpleDateFormat("MM-dd HH:mm:ss", Locale.CHINA).format(new Date(ms)); }
}
