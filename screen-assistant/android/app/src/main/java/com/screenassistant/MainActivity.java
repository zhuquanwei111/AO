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

    private MediaProjectionManager mpm;
    private EditText etUrl, etInterval;
    private TextView tvStatus, tvRegion;
    private Button btnRegion, btnStart, btnPauseResume, btnStop;
    private LivePanel livePanel;

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
        livePanel = new LivePanel(this, findViewById(R.id.main_column), this::onStartClicked);
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
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(stateReceiver, f, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(stateReceiver, f);
        }
    }

    @Override
    protected void onStop() {
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
        CaptureService.Phase phase = CaptureService.phase;
        boolean busy = phase != CaptureService.Phase.IDLE;
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
            default:
                tvStatus.setText("状态：未运行" + (msg.isEmpty() ? "" : "\n" + msg));
        }
        btnStart.setEnabled(!busy);
        btnPauseResume.setEnabled(busy);
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
        RegionSelectorService.start(this);
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
        requestMediaProjection();
    }

    private static boolean isValidServerUrl(String value) {
        return LiveClient.validBaseUrl(value);
    }

    private void requestMediaProjection() {
        // Android 14+：每次采集会话必须重新取得用户授权（复用 Intent 会抛 SecurityException）
        startActivityForResult(mpm.createScreenCaptureIntent(), REQ_MEDIA_PROJECTION);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_POST_NOTIFICATIONS) {
            // 通知权限被拒也可运行（系统仍显示采集标识），不阻塞主链路
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
