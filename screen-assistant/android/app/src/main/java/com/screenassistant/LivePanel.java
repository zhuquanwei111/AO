package com.screenassistant;

import android.app.Activity;
import android.content.Intent;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;

/** 折叠设置中的场次参数和内部场次操作；首页只通过开始/结束辅助调用。 */
final class LivePanel {
    private final Activity activity;
    private final TextView state;
    private final EditText description;
    private final EditText account;
    private final Button endIdleSession;
    private final Runnable stateChanged;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private boolean working;
    private String lastError = "";

    LivePanel(Activity activity, LinearLayout parent, Runnable stateChanged) {
        this.activity = activity;
        this.stateChanged = stateChanged;
        state = new TextView(activity);
        parent.addView(state);
        account = new EditText(activity);
        account.setSingleLine(true);
        account.setHint("抖音号（用于本场复盘）");
        account.setText(Prefs.getDouyinAccount(activity));
        parent.addView(account);
        description = new EditText(activity);
        description.setHint("本场说明（选填，最多500字）");
        description.setFilters(new android.text.InputFilter[]{new android.text.InputFilter.LengthFilter(500)});
        parent.addView(description);
        endIdleSession = new Button(activity);
        endIdleSession.setText("结束当前辅助场次并查看复盘");
        parent.addView(endIdleSession);
        endIdleSession.setOnClickListener(v -> stopOperationSession(() -> openRecap(), this::showError));
        refresh();
    }

    void beginOrResume(Runnable capture, Consumer<String> onError) {
        if (working) return;
        String base = Prefs.getServerUrl(activity);
        if (!LiveClient.validBaseUrl(base)) {
            onError.accept("连接设置中的服务地址无效。请检查地址后重试。");
            return;
        }
        Prefs.setDouyinAccount(activity, account.getText().toString().trim());
        String descriptionSnapshot = description.getText().toString().trim();
        LiveClient.Session existing = Prefs.getSession(activity);
        boolean needsCreate = existing == null || existing.stopped;
        LiveClient.Session selected = existing;
        working = true;
        lastError = "";
        refreshOnUi();
        stateChanged.run();
        io.execute(() -> {
            try {
                LiveClient.Session session = selected;
                if (needsCreate) {
                    session = LiveClient.create(base, descriptionSnapshot, Prefs.getDouyinAccount(activity));
                    if (!Prefs.saveSession(activity, session)) {
                        LiveClient.stop(session);
                        throw new java.io.IOException("场次凭证保存失败；已请求结束新场次");
                    }
                }
                LiveClient.start(session);
                activity.runOnUiThread(() -> {
                    working = false;
                    refresh();
                    stateChanged.run();
                    capture.run();
                });
            } catch (Exception e) {
                String message = (needsCreate ? "场次创建或启动未确认：" : "辅助场次启动未确认：")
                        + e.getMessage() + "。请检查连接设置后重试；不要重复创建场次。";
                activity.runOnUiThread(() -> {
                    working = false;
                    lastError = message;
                    refresh();
                    stateChanged.run();
                    onError.accept(message);
                });
            }
        });
    }

    void stopOperationSession(Runnable onComplete, Consumer<String> onError) {
        LiveClient.Session session = Prefs.getSession(activity);
        if (session == null || session.stopped) {
            activity.runOnUiThread(onComplete);
            return;
        }
        if (working) return;
        working = true;
        lastError = "";
        refreshOnUi();
        stateChanged.run();
        io.execute(() -> {
            try {
                try { CaptureService.audioCompletion.get(20, java.util.concurrent.TimeUnit.SECONDS); }
                catch (Exception audioError) { /* stop still closes the already sent audio stream */ }
                LiveClient.stop(session);
                if (!Prefs.saveSession(activity, session.asStopped()))
                    throw new java.io.IOException("服务端已结束，但本地状态保存失败；请重新打开后核对场次状态");
                activity.runOnUiThread(() -> {
                    working = false;
                    refresh();
                    stateChanged.run();
                    onComplete.run();
                });
            } catch (Exception e) {
                String message = "本机采集已停止，但场次结束未确认：" + e.getMessage()
                        + "。请在设置中重试结束场次；复盘保留服务端实际状态。";
                activity.runOnUiThread(() -> {
                    working = false;
                    lastError = message;
                    refresh();
                    stateChanged.run();
                    onError.accept(message);
                });
            }
        });
    }

    boolean hasActiveSession() {
        LiveClient.Session session = Prefs.getSession(activity);
        return session != null && !session.stopped;
    }

    boolean isWorking() { return working; }

    String getSummary() {
        if (working) return "正在准备辅助场次…";
        if (!lastError.isEmpty()) return lastError;
        LiveClient.Session session = Prefs.getSession(activity);
        if (session == null) return "尚未开始";
        if (session.stopped) return "上一场已结束，可查看复盘或开始新辅助";
        return CaptureService.phase == CaptureService.Phase.IDLE
                ? "辅助场次已就绪，屏幕采集未运行。可继续辅助或结束本场。"
                : "辅助场次进行中";
    }

    private void showError(String message) {
        lastError = message;
        refreshOnUi();
        stateChanged.run();
    }

    private void refreshOnUi() {
        activity.runOnUiThread(this::refresh);
    }

    void refresh() {
        state.setText(getSummary());
        boolean active = hasActiveSession();
        endIdleSession.setVisibility(active && CaptureService.phase == CaptureService.Phase.IDLE
                ? android.view.View.VISIBLE : android.view.View.GONE);
        endIdleSession.setEnabled(!working);
    }

    void openRecap() {
        if (!activity.isFinishing() && !activity.isDestroyed())
            activity.startActivity(new Intent(activity, RecapActivity.class));
    }

    void clearError() {
        lastError = "";
        refresh();
        stateChanged.run();
    }

    void close() { io.shutdown(); }
}
