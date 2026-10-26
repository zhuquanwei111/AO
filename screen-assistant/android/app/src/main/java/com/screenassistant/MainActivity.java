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
import android.provider.Settings;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

/**
 * 主界面：服务地址/间隔配置、区域框选入口、启动（采集授权）、暂停/继续、停止。
 * 纯框架控件，简洁中文界面。
 */
public class MainActivity extends Activity {

    private static final int REQ_MEDIA_PROJECTION = 1001;
    private static final int REQ_OVERLAY = 1002;
    private static final int REQ_POST_NOTIFICATIONS = 1003;
    private static final int REQ_RECORD_AUDIO = 1004;

    private MediaProjectionManager mpm;
    private EditText etUrl, etInterval;
    private TextView tvStatus, tvRegion;
    private Button btnRegion, btnStart, btnPauseResume, btnStop;
    private LivePanel livePanel;
    private RegionPanel regionPanel;
    private AudioPanel audioPanel;

    private final BroadcastReceiver stateReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            refreshUi();
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        mpm = (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
        etUrl = findViewById(R.id.et_url);
        etInterval = findViewById(R.id.et_interval);
        tvStatus = findViewById(R.id.tv_status);
        tvRegion = findViewById(R.id.tv_region);
        btnRegion = findViewById(R.id.btn_region);
        btnStart = findViewById(R.id.btn_start);
        btnPauseResume = findViewById(R.id.btn_pause_resume);
        btnStop = findViewById(R.id.btn_stop);

        etUrl.setText(Prefs.getServerUrl(this));
        etInterval.setText(String.valueOf(Prefs.getIntervalSec(this)));
        android.widget.LinearLayout column = findViewById(R.id.main_column);
        regionPanel = new RegionPanel(this, column);
        audioPanel = new AudioPanel(this, column);
        livePanel = new LivePanel(this, column, this::onStartClicked);
        btnRegion.setText("框选所选区域（下方选择用途）");
        btnStart.setText("启动/重试采集（无场次为旧问答）");
        btnStop.setText("停止采集");

        btnRegion.setOnClickListener(v -> startRegionSelector());
        btnStart.setOnClickListener(v -> onStartClicked());
        btnPauseResume.setOnClickListener(v -> onPauseResumeClicked());
        btnStop.setOnClickListener(v -> CaptureService.requestStop(this));
    }

    @Override
    protected void onStart() {
        super.onStart();
        IntentFilter f = new IntentFilter(Prefs.ACTION_STATE);
        androidx.core.content.ContextCompat.registerReceiver(this, stateReceiver, f,
                androidx.core.content.ContextCompat.RECEIVER_NOT_EXPORTED);
        CaptureService.uiVisible = true;
    }

    @Override
    protected void onStop() {
        CaptureService.uiVisible = false;
        try {
            unregisterReceiver(stateReceiver);
        } catch (IllegalArgumentException ignored) {
        }
        super.onStop();
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshUi();
    }

    private void refreshUi() {
        if (livePanel != null) livePanel.refresh();
        if (regionPanel != null) regionPanel.refresh();
        CaptureService.Phase phase = CaptureService.phase;
        boolean busy = phase != CaptureService.Phase.IDLE;
        if (audioPanel != null) audioPanel.refresh(!busy);
        String msg = CaptureService.lastMessage;
        switch (phase) {
            case RUNNING:
                tvStatus.setText("状态：采集中 · 间隔 " + Prefs.getIntervalSec(this) + " 秒"
                        + (msg.isEmpty() ? "" : "\n" + msg));
                break;
            case PAUSED:
                tvStatus.setText("状态：已暂停（保持授权，可继续）"
                        + (msg.isEmpty() ? "" : "\n" + msg));
                break;
            case STOPPING:
                tvStatus.setText("状态：正在停止采集并收尾音频" + (msg.isEmpty() ? "" : "\n" + msg));
                break;
            default:
                tvStatus.setText("状态：未运行" + (msg.isEmpty() ? "" : "\n" + msg));
        }
        btnStart.setEnabled(!busy);
        btnPauseResume.setEnabled(phase == CaptureService.Phase.RUNNING || phase == CaptureService.Phase.PAUSED);
        btnPauseResume.setText(phase == CaptureService.Phase.PAUSED ? "继续" : "暂停");
        btnStop.setEnabled(busy);
        float[] r = Prefs.getRegion(this);
        if (r == null) {
            tvRegion.setText("识别区域：未框选（默认屏幕中央）");
        } else {
            tvRegion.setText(String.format("识别区域：宽%.0f%% 高%.0f%%（左%.0f%% 上%.0f%%）",
                    (r[2] - r[0]) * 100f, (r[3] - r[1]) * 100f, r[0] * 100f, r[1] * 100f));
        }
    }

    private void startRegionSelector() {
        if (!Settings.canDrawOverlays(this)) {
            Toast.makeText(this, "框选需要悬浮窗权限，请授权后重试", Toast.LENGTH_LONG).show();
            startActivityForResult(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + getPackageName())), REQ_OVERLAY);
            return;
        }
        if (CaptureService.phase != CaptureService.Phase.IDLE) {
            Toast.makeText(this, "请先停止采集再编辑区域，避免把编辑器作为输入", Toast.LENGTH_LONG).show(); return;
        }
        RegionSelectorService.start(this, regionPanel.selected());
        moveTaskToBack(true);
    }

    private void onStartClicked() {
        String url = etUrl.getText().toString().trim();
        int interval;
        try {
            interval = Integer.parseInt(etInterval.getText().toString().trim());
        } catch (NumberFormatException e) {
            interval = -1;
        }
        if (!isValidServerUrl(url)) {
            Toast.makeText(this, "服务地址无效，示例：http://192.168.1.10:8000", Toast.LENGTH_LONG).show();
            return;
        }
        if (interval < 1 || interval > 3600) {
            Toast.makeText(this, "间隔需为 1~3600 秒", Toast.LENGTH_SHORT).show();
            return;
        }
        Prefs.setServerUrl(this, url);
        Prefs.setIntervalSec(this, interval);
        audioPanel.save(this);
        if (!Settings.canDrawOverlays(this)) {
            Toast.makeText(this, "回复悬浮条需要悬浮窗权限，请授权后重试", Toast.LENGTH_LONG).show();
            startActivityForResult(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + getPackageName())), REQ_OVERLAY);
            return;
        }
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQ_POST_NOTIFICATIONS);
            return; // 授权回调后继续
        }
        requestAudioThenProjection();
    }

    private void requestAudioThenProjection() {
        LiveClient.Session session = Prefs.getSession(this);
        if (session != null && !session.stopped && Prefs.getAudioMode(this) != 0
                && checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_RECORD_AUDIO);
        } else requestMediaProjection();
    }

    private static boolean isValidServerUrl(String value) {
        return LiveClient.validBaseUrl(value);
    }

    private void requestMediaProjection() {
        // Android 14+：每次采集会话必须重新取得用户授权（复用 Intent 会抛 SecurityException）
        Intent consent = Build.VERSION.SDK_INT >= 34 ? mpm.createScreenCaptureIntent(
                android.media.projection.MediaProjectionConfig.createConfigForDefaultDisplay()) : mpm.createScreenCaptureIntent();
        startActivityForResult(consent, REQ_MEDIA_PROJECTION);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_POST_NOTIFICATIONS) {
            // 通知权限被拒也可运行（系统仍显示采集标识），不阻塞主链路
            requestAudioThenProjection();
        } else if (requestCode == REQ_RECORD_AUDIO) {
            if (grantResults.length == 0 || grantResults[0] != PackageManager.PERMISSION_GRANTED)
                Toast.makeText(this, "音频权限未授予，继续屏幕取数", Toast.LENGTH_LONG).show();
            requestMediaProjection();
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQ_MEDIA_PROJECTION) {
            if (resultCode == RESULT_OK && data != null) {
                stopService(new Intent(this, RegionSelectorService.class));
                CaptureService.requestStart(this, resultCode, data);
                moveTaskToBack(true); // 回到目标应用，避免把自己画面识别进去
            } else {
                Toast.makeText(this, "未授权屏幕采集，无法启动", Toast.LENGTH_LONG).show();
            }
        } else if (requestCode == REQ_OVERLAY) {
            refreshUi();
            Toast.makeText(this, Settings.canDrawOverlays(this)
                    ? "悬浮窗权限已授权" : "未授予悬浮窗权限，功能受限", Toast.LENGTH_SHORT).show();
        }
    }

    private void onPauseResumeClicked() {
        if (CaptureService.phase == CaptureService.Phase.PAUSED) {
            CaptureService.requestResume(this);
        } else if (CaptureService.phase == CaptureService.Phase.RUNNING) {
            CaptureService.requestPause(this);
        }
    }

    @Override protected void onDestroy() {
        if (livePanel != null) livePanel.close();
        super.onDestroy();
    }
}
