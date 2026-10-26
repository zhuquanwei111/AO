package com.screenassistant;

import android.app.Activity;
import android.content.Intent;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONObject;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** 简单场次控制；失败保留凭证，可重试stop；不把停止采集冒充停止共享场次。 */
final class LivePanel {
    private final Activity activity;
    private final TextView state;
    private final EditText description, account;
    private final Button create, stop;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private boolean working;

    LivePanel(Activity activity, LinearLayout parent, Runnable capture) {
        this.activity = activity;
        state = new TextView(activity); parent.addView(state);
        account = new EditText(activity); account.setSingleLine(true); account.setHint("抖音号（后台五项所用账号）");
        account.setText(Prefs.getDouyinAccount(activity)); parent.addView(account);
        description = new EditText(activity); description.setHint("本场说明（选填，最多500字）");
        description.setFilters(new android.text.InputFilter[]{new android.text.InputFilter.LengthFilter(500)});
        parent.addView(description);
        create = button(parent, "开始运营场次并采集");
        stop = button(parent, "结束运营场次");
        Button recap = button(parent, "查看最近场次复盘／刷新后台汇总");
        create.setOnClickListener(v -> {
            EditText url = activity.findViewById(R.id.et_url);
            String base = url.getText().toString().trim();
            if (!LiveClient.validBaseUrl(base)) { state.setText("请填写有效的服务base URL"); return; }
            Prefs.setServerUrl(activity, base);
            String accountSnapshot = account.getText().toString().trim();
            String descriptionSnapshot = description.getText().toString().trim();
            Prefs.setDouyinAccount(activity, accountSnapshot);
            working = true; refresh();
            io.execute(() -> {
                try {
                    LiveClient.Session session = LiveClient.create(base, descriptionSnapshot, accountSnapshot);
                    if (!Prefs.saveSession(activity, session)) {
                        LiveClient.call(session, "stop", new JSONObject());
                        throw new java.io.IOException("凭证保存失败，已请求结束该场次");
                    }
                    if (!accountSnapshot.isEmpty()) LiveClient.call(session, "start", new JSONObject());
                    activity.runOnUiThread(() -> {
                        working = false; refresh();
                        if (!activity.isFinishing() && !activity.isDestroyed()) capture.run();
                    });
                } catch (Exception e) { finish("场次/后台启动未确认：" + e.getMessage() + "。已保存的场次可继续采集或结束；请勿反复创建。"); }
            });
        });
        stop.setOnClickListener(v -> {
            LiveClient.Session session = Prefs.getSession(activity);
            if (session == null || session.stopped) return;
            working = true; refresh();
            CaptureService.requestStop(activity);
            io.execute(() -> {
                try {
                    try { CaptureService.audioCompletion.get(20, java.util.concurrent.TimeUnit.SECONDS); }
                    catch (Exception audioError) { /* 后端stop仍对已收PCM收尾；界面保留音频诊断。 */ }
                    LiveClient.call(session, "stop", new JSONObject());
                    if (!Prefs.saveSession(activity, session.asStopped())) throw new java.io.IOException("停止成功但本地状态保存失败，可重试");
                    activity.runOnUiThread(() -> {
                        working = false; refresh();
                        if (!activity.isFinishing() && !activity.isDestroyed()) activity.startActivity(new Intent(activity, RecapActivity.class));
                    });
                } catch (Exception e) { finish("结束未确认：" + e.getMessage() + "，已停本地采集，请重试结束"); }
            });
        });
        recap.setOnClickListener(v -> activity.startActivity(new Intent(activity, RecapActivity.class)));
        refresh();
    }

    private Button button(LinearLayout parent, String text) {
        Button b = new Button(activity); b.setText(text); parent.addView(b); return b;
    }
    private void finish(String message) {
        activity.runOnUiThread(() -> { working = false; refresh(); state.setText(message); });
    }
    void refresh() {
        LiveClient.Session s = Prefs.getSession(activity);
        boolean active = s != null && !s.stopped;
        create.setEnabled(!working && !active && CaptureService.phase == CaptureService.Phase.IDLE);
        stop.setEnabled(!working && active);
        state.setText(working ? "场次请求中…" : active
                ? "运营场次进行中；可用下方按钮启动/重试采集，结束请点这里。"
                : "运营场次：" + (s == null ? "尚未创建" : "已结束，可查看复盘"));
    }
    void close() { io.shutdown(); }
}
