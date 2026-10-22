package com.screenassistant;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.Looper;
import android.view.Gravity;
import android.view.LayoutInflater;
import android.view.View;
import android.view.WindowManager;
import android.widget.TextView;

import com.google.mlkit.vision.common.InputImage;
import com.google.mlkit.vision.text.TextRecognition;
import com.google.mlkit.vision.text.TextRecognizer;
import com.google.mlkit.vision.text.chinese.ChineseTextRecognizerOptions;

import java.nio.ByteBuffer;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 采集前台服务（foregroundServiceType=mediaProjection）。
 *
 * Android 14+ 约束（官方文档核实）：
 *  - 每次采集会话必须先经 createScreenCaptureIntent() 取得新授权，授权 Intent 不得复用；
 *  - 必须先 startForeground(mediaProjection 类型) 才能调用 getMediaProjection()；
 *  - 同一 MediaProjection 实例只允许一次 createVirtualDisplay()，因此“暂停”保留
 *    VirtualDisplay 仅停轮询，“继续”复用，不重建；旋转时按官方 resize/setSurface
 *    路径过于复杂，V1 采取安全停止会话并提示重新开始；
 *  - createVirtualDisplay 前必须注册 onStop 回调，回调中回收全部资源。
 *
 * 隐私：全部画面仅在内存处理（Image->Bitmap->ML Kit），不落盘、不缓存句子与回复。
 */
public class CaptureService extends Service {

    public enum Phase { IDLE, RUNNING, PAUSED }

    public static final String ACTION_START = "com.screenassistant.START";
    public static final String ACTION_PAUSE = "com.screenassistant.PAUSE";
    public static final String ACTION_RESUME = "com.screenassistant.RESUME";
    public static final String ACTION_STOP = "com.screenassistant.STOP";

    /** 供 MainActivity 刷新 UI 的轻量状态（进程内静态，无持久化）。 */
    public static volatile Phase phase = Phase.IDLE;
    public static volatile String lastMessage = "";

    private static final String EXTRA_RESULT_CODE = "result_code";
    private static final String EXTRA_RESULT_DATA = "result_data";
    private static final int NOTIF_ID = 100;
    private static final String CHANNEL_ID = "capture";

    private static final int CONNECT_TIMEOUT_MS = 5000;
    private static final int READ_TIMEOUT_MS = 10000;
    private static final long REPLY_AUTO_HIDE_MS = 6000L;

    private MediaProjectionManager mpm;
    private NotificationManager notifManager;
    private WindowManager wm;

    private MediaProjection projection;
    private android.hardware.display.VirtualDisplay virtualDisplay;
    private ImageReader imageReader;
    private TextRecognizer recognizer;

    private HandlerThread captureThread;
    private Handler captureHandler;
    private final Handler main = new Handler(Looper.getMainLooper());
    private ExecutorService workExecutor;

    private final AtomicBoolean busy = new AtomicBoolean(false);
    private boolean tornDown = true;
    private int sessionEpoch = 0;
    private int rotationAtStart;
    private int intervalMs = 5000;
    private String serverUrl = "";
    private int[] screenSize = new int[2];

    private View replyOverlayView;
    private TextView replyTextView;
    private final Runnable replyHide = () -> removeReplyOverlay();

    private final MediaProjection.Callback projectionCallback = new MediaProjection.Callback() {
        @Override
        public void onStop() {
            main.post(() -> teardown("采集已被系统或用户停止"));
        }
    };

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            if (phase != Phase.RUNNING || tornDown) return;
            if (ScreenMetrics.rotation(CaptureService.this) != rotationAtStart) {
                // 回主线程执行teardown（内部有 stopForeground/stopSelf）
                main.post(() -> teardown("屏幕方向已变化，会话已停止，请重新框选并启动"));
                return;
            }
            if (busy.get()) {
                scheduleNext();
                return;
            }
            Image image = null;
            Bitmap crop = null;
            try {
                image = imageReader.acquireLatestImage();
                if (image != null) {
                    Rect r = computeCropRect(image.getWidth(), image.getHeight());
                    if (r != null) crop = cropImage(image, r);
                }
            } catch (Throwable t) {
                android.util.Log.w("CaptureService", "capture frame failed", t);
            } finally {
                if (image != null) image.close();
            }
            if (crop == null) {
                // 无新帧：屏幕内容未变，跳过本轮（服务端去重语义兜底）
                scheduleNext();
                return;
            }
            busy.set(true);
            final Bitmap b = crop;
            final int epoch = sessionEpoch;
            recognizer.process(InputImage.fromBitmap(b, 0))
                    .addOnSuccessListener(workExecutor, text -> {
                        b.recycle();
                        try {
                            String sentence = normalize(text.getText());
                            if (sentence.isEmpty()) {
                                busy.set(false);
                                scheduleNext();
                                return;
                            }
                            AnswerClient.Result result =
                                    AnswerClient.post(serverUrl, sentence, CONNECT_TIMEOUT_MS, READ_TIMEOUT_MS);
                            busy.set(false);
                            if (phase != Phase.RUNNING || tornDown || epoch != sessionEpoch) {
                                return; // 会话已停/换轮：不得回填
                            }
                            if (result.outcome == AnswerClient.Outcome.SUCCESS) {
                                showReply(result.reply);
                            }
                            // DUPLICATE：保留当前展示；DROP：本轮静默丢弃
                        } catch (Throwable t) {
                            android.util.Log.w("CaptureService", "round failed", t);
                            busy.set(false);
                        }
                        scheduleNext();
                    })
                    .addOnFailureListener(workExecutor, e -> {
                        b.recycle();
                        android.util.Log.w("CaptureService", "ocr failed", e);
                        busy.set(false);
                        scheduleNext();
                    });
        }
    };

    public static void requestStart(Context ctx, int resultCode, Intent data) {
        Intent i = new Intent(ctx, CaptureService.class);
        i.setAction(ACTION_START);
        i.putExtra(EXTRA_RESULT_CODE, resultCode);
        i.putExtra(EXTRA_RESULT_DATA, data);
        ctx.startForegroundService(i);
    }

    public static void requestPause(Context ctx) {
        Intent i = new Intent(ctx, CaptureService.class).setAction(ACTION_PAUSE);
        ctx.startService(i);
    }

    public static void requestResume(Context ctx) {
        Intent i = new Intent(ctx, CaptureService.class).setAction(ACTION_RESUME);
        ctx.startService(i);
    }

    public static void requestStop(Context ctx) {
        Intent i = new Intent(ctx, CaptureService.class).setAction(ACTION_STOP);
        ctx.startService(i);
    }

    @Override
    public void onCreate() {
        super.onCreate();
        mpm = (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
        notifManager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        wm = getSystemService(WindowManager.class);
        NotificationChannel ch = new NotificationChannel(CHANNEL_ID, "屏幕识别",
                NotificationManager.IMPORTANCE_LOW);
        ch.setShowBadge(false);
        notifManager.createNotificationChannel(ch);
        captureThread = new HandlerThread("capture");
        captureThread.start();
        captureHandler = new Handler(captureThread.getLooper());
        workExecutor = Executors.newSingleThreadExecutor();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_STOP : intent.getAction();
        switch (action == null ? "" : action) {
            case ACTION_START: {
                // Android14：必须先成为 mediaProjection 前台服务，才允许 getMediaProjection()
                startForeground(NOTIF_ID, buildNotification("正在启动…"),
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
                int code = intent.getIntExtra(EXTRA_RESULT_CODE, 0);
                Intent data = intent.getParcelableExtra(EXTRA_RESULT_DATA);
                if (data == null) {
                    teardown("启动参数缺失");
                } else {
                    setup(code, data);
                }
                break;
            }
            case ACTION_PAUSE: {
                if (phase == Phase.RUNNING) {
                    captureHandler.removeCallbacks(tick);
                    phase = Phase.PAUSED;
                    setState("已暂停（保留采集授权）");
                    notifManager.notify(NOTIF_ID, buildNotification("已暂停 · 点击应用继续"));
                } else if (phase == Phase.IDLE) {
                    stopSelf(); // 无会话时不滞留空服务
                }
                break;
            }
            case ACTION_RESUME: {
                if (phase == Phase.PAUSED && !tornDown) {
                    phase = Phase.RUNNING;
                    setState("已继续");
                    notifManager.notify(NOTIF_ID, buildNotification("采集中 · 间隔 "
                            + (intervalMs / 1000) + " 秒"));
                    scheduleTick(300);
                } else if (phase == Phase.IDLE) {
                    stopSelf();
                }
                break;
            }
            case ACTION_STOP:
            default:
                teardown("已停止");
                break;
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        teardownInternal();
        if (workExecutor != null) workExecutor.shutdownNow();
        if (captureThread != null) captureThread.quitSafely();
        if (recognizer != null) {
            try {
                recognizer.close();
            } catch (Exception ignored) {
            }
            recognizer = null;
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void setup(int resultCode, Intent data) {
        try {
            if (!tornDown) teardownInternal();
            tornDown = false;
            sessionEpoch++;
            serverUrl = Prefs.getServerUrl(this);
            intervalMs = Prefs.getIntervalSec(this) * 1000;
            int[] size = ScreenMetrics.realSizeDpi(this);
            screenSize = size;
            rotationAtStart = ScreenMetrics.rotation(this);
            busy.set(false);

            imageReader = ImageReader.newInstance(size[0], size[1], PixelFormat.RGBA_8888, 3);
            projection = mpm.getMediaProjection(resultCode, data);
            // Android14：createVirtualDisplay 前必须注册 onStop 回调
            projection.registerCallback(projectionCallback, captureHandler);
            virtualDisplay = projection.createVirtualDisplay(
                    "screen-assistant", size[0], size[1], size[2],
                    android.hardware.display.DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                    imageReader.getSurface(), null, captureHandler);
            if (recognizer == null) {
                recognizer = TextRecognition.getClient(
                        new ChineseTextRecognizerOptions.Builder().build());
            }
            phase = Phase.RUNNING;
            setState("已连接 " + serverUrl);
            notifManager.notify(NOTIF_ID, buildNotification("采集中 · 间隔 " + (intervalMs / 1000) + " 秒"));
            scheduleTick(800);
        } catch (Exception e) {
            android.util.Log.e("CaptureService", "setup failed", e);
            teardown("启动失败：" + e.getMessage());
        }
    }

    private void scheduleTick(long delayMs) {
        if (phase == Phase.RUNNING && !tornDown) {
            captureHandler.postDelayed(tick, delayMs);
        }
    }

    private void scheduleNext() {
        scheduleTick(intervalMs);
    }

    /** 依据归一化区域计算帧内像素裁切矩形，含越界钳制；无有效区域返回默认中央区域。 */
    private Rect computeCropRect(int imgW, int imgH) {
        float[] r = Prefs.getRegion(this);
        if (r == null) r = new float[]{0.25f, 0.40f, 0.75f, 0.60f};
        int l = Math.round(r[0] * imgW);
        int t = Math.round(r[1] * imgH);
        int rr = Math.round(r[2] * imgW);
        int b = Math.round(r[3] * imgH);
        l = clamp(l, 0, imgW - 2);
        t = clamp(t, 0, imgH - 2);
        rr = clamp(rr, l + 2, imgW);
        b = clamp(b, t + 2, imgH);
        return new Rect(l, t, rr, b);
    }

    private static int clamp(int v, int min, int max) {
        return Math.max(min, Math.min(v, max));
    }

    /**
     * 从整屏 RGBA_8888 Image 中按矩形裁切为 ARGB_8888 Bitmap。
     * 处理 rowStride/pixelStride 与 RGBA->ARGB 字节序，仅在内存完成。
     */
    private Bitmap cropImage(Image image, Rect r) {
        Image.Plane plane = image.getPlanes()[0];
        ByteBuffer buf = plane.getBuffer();
        int rowStride = plane.getRowStride();
        int pixelStride = plane.getPixelStride();
        int w = r.width(), h = r.height();
        int[] px = new int[w * h];
        for (int row = 0; row < h; row++) {
            int base = (r.top + row) * rowStride + r.left * pixelStride;
            for (int col = 0; col < w; col++) {
                int i = base + col * pixelStride;
                int v = (buf.get(i) & 0xFF)
                        | ((buf.get(i + 1) & 0xFF) << 8)
                        | ((buf.get(i + 2) & 0xFF) << 16)
                        | ((buf.get(i + 3) & 0xFF) << 24);
                // 内存字节序 R,G,B,A -> 整型 0xAABBGGRR -> 交换为 Android 的 0xAARRGGBB
                px[row * w + col] = (v & 0xFF00FF00) | ((v & 0x000000FF) << 16) | ((v >>> 16) & 0x000000FF);
            }
        }
        return Bitmap.createBitmap(px, w, h, Bitmap.Config.ARGB_8888);
    }

    /** 基础格式化：去首尾空白、内部空白折叠为单空格；空文本返回 ""（本轮跳过）。 */
    private static String normalize(String raw) {
        if (raw == null) return "";
        return raw.trim().replaceAll("\\s+", " ");
    }

    private void showReply(String reply) {
        main.post(() -> {
            if (phase != Phase.RUNNING || tornDown) return;
            try {
                if (replyOverlayView == null) {
                    replyOverlayView = LayoutInflater.from(this).inflate(R.layout.overlay_reply, null);
                    replyTextView = replyOverlayView.findViewById(R.id.tv_reply);
                    WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                            WindowManager.LayoutParams.WRAP_CONTENT,
                            WindowManager.LayoutParams.WRAP_CONTENT,
                            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                                    | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                                    | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                            PixelFormat.TRANSLUCENT);
                    lp.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL;
                    lp.y = (int) (64 * getResources().getDisplayMetrics().density);
                    lp.width = Math.max(200, screenSize[0]
                            - (int) (32 * getResources().getDisplayMetrics().density));
                    wm.addView(replyOverlayView, lp);
                }
                replyTextView.setText(reply);
                replyOverlayView.setVisibility(View.VISIBLE);
                main.removeCallbacks(replyHide);
                main.postDelayed(replyHide, REPLY_AUTO_HIDE_MS);
            } catch (Exception e) {
                android.util.Log.w("CaptureService", "show reply failed", e);
                removeReplyOverlay();
            }
        });
    }

    private void removeReplyOverlay() {
        if (replyOverlayView != null) {
            try {
                wm.removeView(replyOverlayView);
            } catch (Exception ignored) {
            }
            replyOverlayView = null;
            replyTextView = null;
        }
    }

    private void setState(String message) {
        lastMessage = message;
        Intent i = new Intent(Prefs.ACTION_STATE);
        i.setPackage(getPackageName());
        sendBroadcast(i);
    }

    private Notification buildNotification(String text) {
        PendingIntent pi = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_scan)
                .setContentTitle("屏识AI助手")
                .setContentText(text)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(pi)
                .build();
    }

    /** 完整回收（幂等）：停止轮询、释放采集链、移除悬浮条、退出前台并自毁。 */
    private void teardown(String message) {
        if (tornDown) {
            setState(message);
            stopForegroundAndSelf();
            return;
        }
        teardownInternal();
        setState(message);
        notifManager.cancel(NOTIF_ID);
        stopForegroundAndSelf();
    }

    private void teardownInternal() {
        tornDown = true;
        phase = Phase.IDLE;
        sessionEpoch++;
        busy.set(false);
        if (captureHandler != null) captureHandler.removeCallbacksAndMessages(null);
        main.removeCallbacks(replyHide);
        main.post(this::removeReplyOverlay);
        if (virtualDisplay != null) {
            try {
                virtualDisplay.release();
            } catch (Exception ignored) {
            }
            virtualDisplay = null;
        }
        if (imageReader != null) {
            try {
                imageReader.close();
            } catch (Exception ignored) {
            }
            imageReader = null;
        }
        if (projection != null) {
            try {
                projection.unregisterCallback(projectionCallback);
                projection.stop();
            } catch (Exception ignored) {
            }
            projection = null;
        }
    }

    private void stopForegroundAndSelf() {
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }
}
