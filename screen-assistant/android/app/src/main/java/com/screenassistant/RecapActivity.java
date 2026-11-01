package com.screenassistant;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** 可见期间有限刷新；离页不继续轮询，重开从同一场次重新读取。 */
public class RecapActivity extends Activity {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private TextView summary, events, status, stopStatus;
    private LiveClient.Session session;
    private boolean visible, busy;
    private int rounds;
    private JSONArray loadedEvents;
    private final Runnable refresh = () -> load(false);
    private final BroadcastReceiver stateReceiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) { refreshStopStatus(); }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        session = Prefs.getSession(this);
        LinearLayout column = new LinearLayout(this); column.setOrientation(LinearLayout.VERTICAL);
        int p = Math.round(16 * getResources().getDisplayMetrics().density); column.setPadding(p, p, p, p);
        ScrollView scroll = new ScrollView(this); scroll.addView(column); setContentView(scroll);
        TextView title = text(column); title.setText("本场复盘"); title.setTextSize(22);
        Button reload = new Button(this); reload.setText("刷新本场"); column.addView(reload);
        reload.setOnClickListener(v -> {
            LiveClient.Session current = Prefs.getSession(this);
            if (!RecapRequest.sameSession(session,current)) { session=current; loadedEvents=null; }
            rounds = 0; load(true);
        });
        stopStatus = text(column); status = text(column); summary = text(column); events = text(column);
    }

    private TextView text(LinearLayout parent) {
        TextView t = new TextView(this); t.setTextSize(16); t.setPadding(0, 12, 0, 12);
        t.setTextIsSelectable(true); parent.addView(t); return t;
    }
    @Override protected void onStart() {
        super.onStart();
        androidx.core.content.ContextCompat.registerReceiver(this, stateReceiver,
                new IntentFilter(Prefs.ACTION_STATE), androidx.core.content.ContextCompat.RECEIVER_NOT_EXPORTED);
        CaptureService.setAssistantUiVisible(true); visible = true; rounds = 0; refreshStopStatus(); load(true);
    }
    @Override protected void onStop() {
        CaptureService.setAssistantUiVisible(false); visible = false; main.removeCallbacks(refresh);
        unregisterReceiver(stateReceiver); super.onStop();
    }
    @Override protected void onDestroy() { io.shutdown(); super.onDestroy(); }

    private void refreshStopStatus() {
        LiveClient.Session current = Prefs.getSession(this);
        boolean cached = current == null && Prefs.hasCachedRecap(this);
        stopStatus.setText(RecapText.stopStatus(current != null || cached,
                cached || current != null && current.stopped,
                Prefs.stopPending(this), CaptureService.stopInProgress));
    }

    private void load(boolean includeEvents) {
        if (!visible || busy) return;
        if (session == null) {
            JSONObject cache=Prefs.getCachedRecap(this);
            if (cache==null) status.setText(Prefs.createPending(this)
                    ? "场次创建未确认，复盘不可用；没有重复创建。"
                    : "尚无已确认场次；服务未连接时不会生成复盘或模型建议。");
            else {
                status.setText("最近场次已结束，显示已保存的复盘。");
                JSONObject saved=cache.optJSONObject("summary"); JSONArray savedEvents=cache.optJSONArray("events");
                if (saved!=null) summary.setText(RecapPresentation.summary(saved));
                if (savedEvents!=null) events.setText(RecapPresentation.events(savedEvents));
            }
            return;
        }
        busy = true; rounds++; main.removeCallbacks(refresh);
        status.setText("读取本场…");
        LiveClient.Session requested = session;
        JSONArray previousEvents=loadedEvents;
        io.execute(() -> {
            JSONObject result = null; JSONArray rows = null; String error = "";
            try { result = LiveClient.call(requested, "summary", null); }
            catch (Exception e) { error = RecapRequest.failure(e); }
            if (includeEvents) {
                try { rows = LiveClient.events(requested); }
                catch (Exception e) { error += " 建议记录暂时无法读取，请刷新重试。"; }
            }
            JSONArray completeEvents=rows==null?previousEvents:rows;
            final JSONObject data = result; final JSONArray eventRows = rows; final String message = error;
            main.post(() -> {
                busy = false;
                if (!visible) return;
                if (!RecapRequest.sameSession(requested,Prefs.getSession(this))) {
                    session=Prefs.getSession(this); loadedEvents=null;
                    status.setText("场次已变化，请刷新当前场次。");
                    summary.setText(""); events.setText(""); return;
                }
                boolean cached=data!=null && completeEvents!=null && RecapText.terminal(data)
                        && Prefs.cacheTerminalRecap(this,requested,data,completeEvents);
                if (!RecapRequest.mayApply(requested,Prefs.getSession(this),cached)) {
                    session=Prefs.getSession(this); loadedEvents=null;
                    status.setText("场次已变化，请刷新当前场次。");
                    summary.setText(""); events.setText(""); return;
                }
                status.setText(message.isEmpty() ? "本场记录已更新。"
                        : "部分记录暂时无法读取。\n" + message);
                if (data != null) summary.setText(RecapPresentation.summary(data));
                if (eventRows != null) { loadedEvents=eventRows; events.setText(RecapPresentation.events(eventRows)); }
                if (cached) session=null;
                refreshStopStatus();
                if (rounds < 24 && data != null && pending(data)) main.postDelayed(refresh, 5000);
                else if (data!=null && pending(data)) status.append("\n本轮自动刷新已暂停，点刷新或重开可继续。");
            });
        });
    }

    static boolean pending(JSONObject data) {
        return RecapText.pending(data);
    }

}
