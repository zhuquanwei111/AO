package com.screenassistant;

import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.media.projection.MediaProjectionManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.SystemClock;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Single entry point for the assistant; implementation details stay in expandable settings. */
public class MainActivity extends Activity {
    private static final int REQ_MEDIA_PROJECTION = 1001;
    private static final int REQ_OVERLAY = 1002;
    private static final int REQ_POST_NOTIFICATIONS = 1003;
    private static final int REQ_RECORD_AUDIO = 1004;

    private MediaProjectionManager mpm;
    private EditText etUrl, etInterval;
    private TextView tvStatus, tvPermissions;
    private Button btnMain, btnSettings, btnAdvanced, btnRecap, btnPause;
    private View settingsPanel, advancedPanel;
    private LivePanel livePanel;
    private RegionPanel regionPanel;
    private AudioPanel audioPanel;
    private boolean settingsExpanded, advancedExpanded, pendingStart, pendingLegacyStart;
    private final StartPermissions startPermissions = new StartPermissions();
    private String transientStatus = "";

    private final BroadcastReceiver stateReceiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) { refreshUi(); }
    };

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        mpm = (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
        etUrl = findViewById(R.id.et_url);
        etInterval = findViewById(R.id.et_interval);
        tvStatus = findViewById(R.id.tv_status);
        tvPermissions = findViewById(R.id.tv_permissions);
        btnMain = findViewById(R.id.btn_main_action);
        btnSettings = findViewById(R.id.btn_settings_toggle);
        btnAdvanced = findViewById(R.id.btn_advanced_toggle);
        btnRecap = findViewById(R.id.btn_recap);
        btnPause = findViewById(R.id.btn_pause_resume);
        settingsPanel = findViewById(R.id.settings_panel);
        advancedPanel = findViewById(R.id.advanced_panel);

        etUrl.setText(Prefs.getServerUrl(this));
        etInterval.setText(String.valueOf(Prefs.getIntervalSec(this)));
        LinearLayout regionContainer = findViewById(R.id.region_panel_container);
        LinearLayout audioContainer = findViewById(R.id.audio_panel_container);
        LinearLayout sessionContainer = findViewById(R.id.live_panel_container);
        regionPanel = new RegionPanel(this, regionContainer);
        audioPanel = new AudioPanel(this, audioContainer);
        livePanel = new LivePanel(this, sessionContainer, this::refreshUi);

        btnMain.setOnClickListener(v -> onMainAction());
        btnSettings.setOnClickListener(v -> {
            settingsExpanded = !settingsExpanded;
            settingsPanel.setVisibility(settingsExpanded ? View.VISIBLE : View.GONE);
            btnSettings.setText(settingsExpanded ? "收起设置" : "连接与识别设置");
        });
        btnAdvanced.setOnClickListener(v -> {
            advancedExpanded = !advancedExpanded;
            advancedPanel.setVisibility(advancedExpanded ? View.VISIBLE : View.GONE);
            btnAdvanced.setText(advancedExpanded ? "收起高级功能" : "高级功能");
        });
        btnRecap.setOnClickListener(v -> livePanel.openRecap());
        btnPause.setOnClickListener(v -> onPauseResumeClicked());
        findViewById(R.id.btn_region).setOnClickListener(v -> startRegionSelector());
        refreshUi();
    }

    @Override protected void onStart() {
        super.onStart();
        IntentFilter f = new IntentFilter(Prefs.ACTION_STATE);
        androidx.core.content.ContextCompat.registerReceiver(this, stateReceiver, f,
                androidx.core.content.ContextCompat.RECEIVER_NOT_EXPORTED);
        CaptureService.setAssistantUiVisible(true);
    }

    @Override protected void onStop() {
        CaptureService.setAssistantUiVisible(false);
        try { unregisterReceiver(stateReceiver); } catch (IllegalArgumentException ignored) { }
        super.onStop();
    }

    @Override protected void onResume() { super.onResume(); refreshUi(); }

    private void refreshUi() {
        if (livePanel != null) livePanel.refresh();
        if (regionPanel != null) regionPanel.refresh();
        CaptureService.Phase phase = CaptureService.phase;
        boolean capturing = phase != CaptureService.Phase.IDLE;
        if (audioPanel != null) audioPanel.refresh(!capturing);

        String status;
        switch (phase) {
            case RUNNING:
                status = "辅助中。切换到目标应用后，提示会显示在悬浮条中。";
                if (!CaptureService.lastMessage.isEmpty()) status += "\n" + CaptureService.lastMessage;
                break;
            case PAUSED:
                status = "辅助已暂停。屏幕授权仍保留，可从高级功能继续或结束。";
                break;
            case STOPPING:
                status = CaptureService.stopInProgress ? "本机采集已停止，正在确认场次结束。" : "正在结束屏幕辅助…";
                break;
            case STARTING:
                status = "正在启动本地屏幕采集…";
                break;
            default:
                status = livePanel != null ? livePanel.getSummary() : "尚未开始";
                LiveClient.Session current = Prefs.getSession(this);
                if (!transientStatus.isEmpty()) status = transientStatus;
                else if (!Prefs.stopPending(this) && !CaptureService.stopInProgress
                        && (current == null || !current.stopped)
                        && (current != null || !Prefs.hasCachedRecap(this))
                        && !CaptureService.lastMessage.isEmpty()
                        && !"已结束辅助".equals(CaptureService.lastMessage)) status = CaptureService.lastMessage;
                break;
        }
        tvStatus.setText(status);
        tvPermissions.setText("悬浮窗权限：" + (Settings.canDrawOverlays(this) ? "已允许" : "需要允许")
                + "\n屏幕采集：开始时由 Android 再次确认");

        boolean busy = livePanel != null && livePanel.isWorking();
        if (phase == CaptureService.Phase.RUNNING || phase == CaptureService.Phase.PAUSED) {
            btnMain.setText("结束辅助");
            btnMain.setEnabled(!busy);
        } else if (phase == CaptureService.Phase.STOPPING) {
            btnMain.setText("正在结束…");
            btnMain.setEnabled(false);
        } else if (phase == CaptureService.Phase.STARTING) {
            btnMain.setText("正在启动…");
            btnMain.setEnabled(false);
        } else {
            btnMain.setText(livePanel != null && livePanel.hasActiveSession() ? "继续辅助" : "开始辅助");
            btnMain.setEnabled(!busy && !pendingStart && !CaptureService.stopInProgress);
        }
        btnPause.setEnabled(phase == CaptureService.Phase.RUNNING || phase == CaptureService.Phase.PAUSED);
        btnPause.setText(phase == CaptureService.Phase.PAUSED ? "继续屏幕辅助" : "暂停屏幕辅助");
        btnRecap.setEnabled(true);
    }

    private void onMainAction() {
        if (CaptureService.phase == CaptureService.Phase.RUNNING || CaptureService.phase == CaptureService.Phase.PAUSED) {
            endAssistance();
        } else if (CaptureService.phase == CaptureService.Phase.IDLE) {
            startCapture(false);
        }
    }

    private void startCapture(boolean legacyAnswer) {
        transientStatus = "";
        logFlow("assist_start_clicked", "entry=" + (legacyAnswer ? "advanced_legacy" : "primary"));
        if (pendingStart || CaptureService.phase != CaptureService.Phase.IDLE || livePanel.isWorking()) return;
        String url = etUrl.getText().toString().trim();
        int interval;
        try { interval = Integer.parseInt(etInterval.getText().toString().trim()); }
        catch (NumberFormatException e) { interval = -1; }
        if (!LiveClient.validBaseUrl(url)) { showStartError("连接设置阶段：服务地址无效。请在连接与识别设置中检查地址后重试。"); return; }
        if (interval < 1 || interval > 3600) { showStartError("连接设置阶段：识别间隔须为 1 至 3600 秒。"); return; }
        if (legacyAnswer && livePanel.hasActiveSession()) {
            showStartError("旧问答兼容入口不可用于正在进行的辅助场次。请先结束该场次。"); return;
        }
        Prefs.setServerUrl(this, url);
        Prefs.setIntervalSec(this, interval);
        audioPanel.save(this);
        pendingStart = true;
        pendingLegacyStart = legacyAnswer;
        startPermissions.reset();
        refreshUi();
        continuePermissionFlow();
    }

    private void continuePermissionFlow() {
        if (!pendingStart) return;
        boolean needsNotifications = Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED;
        boolean needsMicrophone = !pendingLegacyStart && Prefs.getAudioMode(this) == 2
                && checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED;
        StartPermissions.Step step = startPermissions.next(Settings.canDrawOverlays(this), needsNotifications, needsMicrophone);
        if (step == StartPermissions.Step.OVERLAY) {
            logFlow("overlay_permission_required", "stage=window_permission");
            startActivityForResult(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + getPackageName())), REQ_OVERLAY);
            return;
        }
        if (step == StartPermissions.Step.NOTIFICATIONS && Build.VERSION.SDK_INT >= 33) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQ_POST_NOTIFICATIONS);
            return;
        }
        if (step == StartPermissions.Step.MICROPHONE) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_RECORD_AUDIO);
            return;
        }
        requestMediaProjection();
    }

    private void startRegionSelector() {
        if (!Settings.canDrawOverlays(this)) {
            showStartError("区域选择阶段：需要悬浮窗权限。允许后再点选择区域。");
            startActivityForResult(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + getPackageName())), REQ_OVERLAY);
            return;
        }
        if (CaptureService.phase != CaptureService.Phase.IDLE) {
            showStartError("区域选择阶段：请先结束屏幕辅助，再编辑识别区域。");
            return;
        }
        RegionSelectorService.start(this);
        moveTaskToBack(true);
    }

    private void requestMediaProjection() {
        if (!pendingStart) return;
        logFlow("projection_consent_requested", "api=" + Build.VERSION.SDK_INT);
        try {
            Intent consent = Build.VERSION.SDK_INT >= 34 ? mpm.createScreenCaptureIntent(
                    android.media.projection.MediaProjectionConfig.createConfigForDefaultDisplay()) : mpm.createScreenCaptureIntent();
            startActivityForResult(consent, REQ_MEDIA_PROJECTION);
        } catch (RuntimeException e) {
            pendingStart = false;
            logFlow("projection_consent_launch_failed", "error=" + e.getClass().getSimpleName());
            showStartError("屏幕授权界面阶段失败（" + e.getClass().getSimpleName()
                    + "）。请回到应用检查系统屏幕采集功能后重试。");
        }
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_POST_NOTIFICATIONS) {
            boolean granted = grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED;
            logFlow("notification_permission_result", "granted=" + granted);
            continuePermissionFlow(); // denied notifications do not block Android's foreground-service indicator
        } else if (requestCode == REQ_RECORD_AUDIO) {
            boolean granted = grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED;
            logFlow("microphone_permission_result", "granted=" + granted);
            if (!granted) showStartError("麦克风权限未授予；本次继续屏幕辅助，不采集音频。" );
            requestMediaProjection();
        }
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQ_MEDIA_PROJECTION) {
            boolean accepted = resultCode == RESULT_OK && data != null;
            logFlow("projection_consent_result", "accepted=" + accepted);
            if (!accepted) {
                pendingStart = false;
                showStartError("屏幕授权阶段：未允许屏幕采集，因此没有启动辅助。可重新点击开始并允许授权。");
                return;
            }
            if (!pendingStart) return; // Activity 已取消/重建时不使用迟到授权结果。
            stopService(new Intent(this, RegionSelectorService.class));
            pendingStart = false;
            try {
                livePanel.saveInputs();
                CaptureService.requestStart(this, resultCode, data, pendingLegacyStart);
                refreshUi();
                moveTaskToBack(true);
            } catch (RuntimeException e) {
                showStartError("前台服务启动阶段失败（" + e.getClass().getSimpleName()
                        + "）。请检查权限后重新开始；尚未确认屏幕采集运行。");
                logFlow("foreground_service_start_failed", "error=" + e.getClass().getSimpleName());
            }
        } else if (requestCode == REQ_OVERLAY) {
            boolean granted = Settings.canDrawOverlays(this);
            logFlow("overlay_permission_result", "granted=" + granted);
            if (granted && pendingStart) continuePermissionFlow();
            else if (!granted) {
                pendingStart = false;
                showStartError("悬浮窗权限阶段：未允许显示悬浮提示。请到系统“显示在其他应用上层”允许本应用，再点开始辅助。");
            }
            refreshUi();
        }
    }

    private void endAssistance() {
        logFlow("assist_end_clicked", "phase=" + CaptureService.phase);
        CaptureService.requestStop(this);
        livePanel.openRecap();
        refreshUi();
    }

    private void onPauseResumeClicked() {
        if (CaptureService.phase == CaptureService.Phase.PAUSED) CaptureService.requestResume(this);
        else if (CaptureService.phase == CaptureService.Phase.RUNNING) CaptureService.requestPause(this);
    }

    private void showStartError(String message) {
        transientStatus = message;
        tvStatus.setText(message);
        refreshControlsOnly();
    }

    private void refreshControlsOnly() {
        CaptureService.Phase phase = CaptureService.phase;
        if (phase == CaptureService.Phase.IDLE) {
            btnMain.setText(livePanel != null && livePanel.hasActiveSession() ? "继续辅助" : "开始辅助");
            btnMain.setEnabled(!pendingStart && (livePanel == null || !livePanel.isWorking()));
        }
    }

    private static void logFlow(String event, String fields) {
        android.util.Log.i("AssistFlow", "t=" + SystemClock.elapsedRealtime() + " event=" + event + " " + fields);
    }

    @Override protected void onDestroy() {
        if (livePanel != null) livePanel.close();
        super.onDestroy();
    }
}
