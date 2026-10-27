package com.screenassistant;

import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.graphics.PixelFormat;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.view.Gravity;
import android.view.LayoutInflater;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.TextView;
import android.widget.Toast;

/**
 * 跨应用区域框选：
 * 1) 先显示一个小悬浮泡“框选区域”（可拖动；点击=开始框选，长按=退出）；
 * 2) 用户切到目标应用后点击悬浮泡，弹出全屏框选覆盖层（拖动新建/移动/四角缩放）；
 * 3) 完成后把区域按整屏真实尺寸归一化存入 Prefs，并通知 MainActivity 刷新。
 */
public class RegionSelectorService extends Service {

    private WindowManager wm;
    private View bubble;
    private View overlay;
    private String regionKind = "legacy";
    private final Handler main = new Handler(Looper.getMainLooper());

    private final Runnable longPressExit = () -> {
        Toast.makeText(this, "已退出框选模式", Toast.LENGTH_SHORT).show();
        stopSelf();
    };

    public static void start(Context ctx) {
        ctx.startService(new Intent(ctx, RegionSelectorService.class));
    }

    public static void start(Context ctx, String kind) {
        ctx.startService(new Intent(ctx, RegionSelectorService.class).putExtra("region_kind", kind));
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        String kind = intent == null ? null : intent.getStringExtra("region_kind");
        regionKind = java.util.Arrays.asList(Regions.KEYS).contains(kind) ? kind : "legacy";
        return START_NOT_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        wm = getSystemService(WindowManager.class);
        // 稍等片刻让任务切换动画结束，再显示悬浮泡
        main.postDelayed(() -> {
            if (bubble == null) showBubble();
        }, 350);
    }

    @Override
    public void onDestroy() {
        main.removeCallbacksAndMessages(null);
        removeBubble();
        removeOverlay();
        super.onDestroy();
    }

    private void showBubble() {
        TextView tv = new TextView(this);
        tv.setText("框选" + Regions.LABELS[java.util.Arrays.asList(Regions.KEYS).indexOf(regionKind)]);
        tv.setTextColor(0xFFFFFFFF);
        tv.setBackgroundResource(R.drawable.bg_bubble);
        int pad = (int) (12 * getResources().getDisplayMetrics().density);
        tv.setPadding(pad * 2, pad, pad * 2, pad);
        bubble = tv;

        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                PixelFormat.TRANSLUCENT);
        lp.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL;
        lp.y = (int) (72 * getResources().getDisplayMetrics().density);

        tv.setOnTouchListener((v, e) -> handleBubbleTouch(e));
        try {
            wm.addView(bubble, lp);
        } catch (Exception ex) {
            Toast.makeText(this, "悬浮窗显示失败：" + ex.getMessage(), Toast.LENGTH_LONG).show();
            stopSelf();
        }
    }

    private boolean handleBubbleTouch(MotionEvent e) {
        WindowManager.LayoutParams lp = (WindowManager.LayoutParams) bubble.getLayoutParams();
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                bubble.setTag(new float[]{e.getRawX(), e.getRawY(), lp.x, lp.y});
                main.postDelayed(longPressExit, 650);
                return true;
            case MotionEvent.ACTION_MOVE: {
                float[] st = (float[]) bubble.getTag();
                float dx = e.getRawX() - st[0];
                float dy = e.getRawY() - st[1];
                if (Math.abs(dx) > 8 || Math.abs(dy) > 8) main.removeCallbacks(longPressExit);
                lp.x = (int) (st[2] + dx);
                lp.y = (int) (st[3] + dy);
                wm.updateViewLayout(bubble, lp);
                return true;
            }
            case MotionEvent.ACTION_UP: {
                main.removeCallbacks(longPressExit);
                float[] st = (float[]) bubble.getTag();
                float dx = e.getRawX() - st[0];
                float dy = e.getRawY() - st[1];
                if (Math.abs(dx) < 8 && Math.abs(dy) < 8) showSelectionOverlay();
                return true;
            }
            case MotionEvent.ACTION_CANCEL:
                main.removeCallbacks(longPressExit);
                return true;
            default:
                return false;
        }
    }

    private void showSelectionOverlay() {
        removeBubble();
        overlay = LayoutInflater.from(this).inflate(R.layout.overlay_region, null);
        SelectionView sv = overlay.findViewById(R.id.selection_view);
        Button confirm = overlay.findViewById(R.id.btn_confirm);
        Button cancel = overlay.findViewById(R.id.btn_cancel);

        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                PixelFormat.TRANSLUCENT);
        try {
            wm.addView(overlay, lp);
        } catch (Exception ex) {
            Toast.makeText(this, "框选覆盖层显示失败：" + ex.getMessage(), Toast.LENGTH_LONG).show();
            stopSelf();
            return;
        }

        confirm.setOnClickListener(v -> {
            android.graphics.RectF vr = sv.getSelectionRect();
            int[] loc = new int[2];
            sv.getLocationOnScreen(loc);
            // 视图坐标 -> 整屏坐标（TYPE_APPLICATION_OVERLAY 不覆盖状态栏，需加偏移）
            android.graphics.RectF screenRect = new android.graphics.RectF(
                    vr.left + loc[0], vr.top + loc[1], vr.right + loc[0], vr.bottom + loc[1]);
            int[] size = ScreenMetrics.realSizeDpi(this);
            if (size[0] <= 0 || size[1] <= 0) {
                Toast.makeText(this, "无法获取屏幕尺寸", Toast.LENGTH_SHORT).show();
                stopSelf();
                return;
            }
            float l = clamp01(screenRect.left / size[0]);
            float t = clamp01(screenRect.top / size[1]);
            float r = clamp01(screenRect.right / size[0]);
            float b = clamp01(screenRect.bottom / size[1]);
            if (r - l < 0.03f || b - t < 0.02f) {
                Toast.makeText(this, "区域太小，请拖动放大", Toast.LENGTH_SHORT).show();
                return;
            }
            Prefs.setRegion(this, regionKind, l, t, r, b);
            Toast.makeText(this, "识别区域已保存", Toast.LENGTH_SHORT).show();
            broadcastState();
            stopSelf();
        });
        cancel.setOnClickListener(v -> {
            Toast.makeText(this, "已取消框选", Toast.LENGTH_SHORT).show();
            stopSelf();
        });
    }

    private static float clamp01(float v) {
        return Math.max(0f, Math.min(1f, v));
    }

    private void broadcastState() {
        Intent i = new Intent(Prefs.ACTION_STATE);
        i.setPackage(getPackageName());
        sendBroadcast(i);
    }

    private void removeBubble() {
        if (bubble != null) {
            try {
                wm.removeView(bubble);
            } catch (Exception ignored) {
            }
            bubble = null;
        }
    }

    private void removeOverlay() {
        if (overlay != null) {
            try {
                wm.removeView(overlay);
            } catch (Exception ignored) {
            }
            overlay = null;
        }
    }
}
