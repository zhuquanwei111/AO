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
import java.util.function.BooleanSupplier;

/** 折叠设置中的场次参数和内部场次操作；首页只通过开始/结束辅助调用。 */
final class LivePanel {
    private final Activity activity;
    private final TextView state;
    private final EditText description;
    private final EditText account;
    private final Button endIdleSession;
    private final Button releaseUnknownCreate;
    private final Runnable stateChanged;
    private final BooleanSupplier idle;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private boolean working;
    private String lastError = "";

    LivePanel(Activity activity, LinearLayout parent, Runnable stateChanged, BooleanSupplier idle) {
        this.activity = activity;
        this.stateChanged = stateChanged;
        this.idle = idle;
        state = new TextView(activity);
        parent.addView(state);
        account = new EditText(activity);
        account.setSingleLine(true);
        account.setHint("抖音号（用于本场复盘）");
        account.setText(Prefs.getDouyinAccount(activity));
        parent.addView(account);
        description = new EditText(activity);
        description.setHint("本场说明（选填，最多500字）");
        description.setText(Prefs.getLiveDescription(activity));
        description.setFilters(new android.text.InputFilter[]{new android.text.InputFilter.LengthFilter(500)});
        parent.addView(description);
        endIdleSession = new Button(activity);
        endIdleSession.setText("结束当前辅助场次并查看复盘");
        parent.addView(endIdleSession);
        endIdleSession.setOnClickListener(v -> stopOperationSession(() -> openRecap(), this::showError));
        releaseUnknownCreate = new Button(activity);
        releaseUnknownCreate.setText("解除本机开场限制");
        parent.addView(releaseUnknownCreate);
        releaseUnknownCreate.setOnClickListener(v -> new android.app.AlertDialog.Builder(activity)
                .setMessage("上次开场可能已在服务器创建。此操作只解除本机开场限制，不证明服务器没有创建，也不会结束可能遗留的旧场。确认后请自行正常点击开始。")
                .setPositiveButton("确认解除", (dialog, which) -> releaseCreateRestriction())
                .setNegativeButton("取消", null).show());
        refresh();
    }

    private void releaseCreateRestriction() {
        try {
            boolean released = LiveConnection.releaseUnknownCreate(Prefs.liveStore(activity), true,
                    () -> !working && idle.getAsBoolean());
            lastError = released ? "本机开场限制已解除；服务器旧场状态仍未确认。可正常点击开始。"
                    : "状态已变化或仍在收尾，本机开场限制未解除。请核对原场次后再试。";
        } catch (java.io.IOException error) {
            lastError = "本机状态保存失败，开场限制仍未解除。请检查存储后重试。";
        }
        refresh(); stateChanged.run();
    }

    void saveInputs() {
        Prefs.setDouyinAccount(activity, account.getText().toString().trim());
        Prefs.setLiveDescription(activity, description.getText().toString().trim());
    }

    void stopOperationSession(Runnable onComplete, Consumer<String> onError) {
        if (CaptureService.phase != CaptureService.Phase.IDLE || CaptureService.stopInProgress) return;
        LiveClient.Session session = Prefs.getSession(activity);
        if (session == null || session.stopped) {
            activity.runOnUiThread(onComplete);
            return;
        }
        if (working) return;
        LiveConnection cleanup = new LiveConnection(Prefs.liveStore(activity),
                LiveClient.remote(session.baseUrl, "", ""), null);
        cleanup.close(true); // 在点击时固定旧场次，不能等异步收尾后再读取新场次。
        working = true;
        lastError = "";
        refreshOnUi();
        stateChanged.run();
        io.execute(() -> {
            try {
                try { CaptureService.audioCompletion.get(20, java.util.concurrent.TimeUnit.SECONDS); }
                catch (Exception audioError) { /* stop still closes the already sent audio stream */ }
                cleanup.finishStop();
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
        if (working) return "本机采集已停止，正在确认场次结束。";
        LiveClient.Session session = Prefs.getSession(activity);
        String stopStatus = RecapText.stopStatus(session != null, session != null && session.stopped,
                Prefs.stopPending(activity), CaptureService.stopInProgress);
        if (!stopStatus.isEmpty()) return stopStatus;
        if (Prefs.createPending(activity)) return "场次创建未确认，未重复创建。请核对原服务端记录后再继续；本地状态提示仍可使用。";
        if (!lastError.isEmpty()) return lastError;
        if (session == null) return Prefs.hasCachedRecap(activity) ? "最近场次已结束，可查看已保存复盘。" : "尚未开始";
        if (session.stopped) return "上一场已结束，可查看复盘或开始新辅助";
        return CaptureService.phase == CaptureService.Phase.IDLE
                ? "保留了原场次凭证，当前未确认在线。可继续连接或重试结束本场。"
                : "已保留本场凭证；实时连接与采集状态见首页。";
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
        endIdleSession.setEnabled(!working && !CaptureService.stopInProgress);
        releaseUnknownCreate.setVisibility(Prefs.createPending(activity)
                ? android.view.View.VISIBLE : android.view.View.GONE);
        releaseUnknownCreate.setEnabled(!working && LiveConnection.canReleaseUnknownCreate(
                Prefs.liveStore(activity), idle));
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
