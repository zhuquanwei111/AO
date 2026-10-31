package com.screenassistant;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.BroadcastReceiver;
import android.content.Intent;
import android.content.IntentFilter;
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
import android.os.SystemClock;
import android.os.PowerManager;
import android.view.Gravity;
import android.view.LayoutInflater;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.TextView;

import com.paddle.ocr.model.OCRRunResult;

import java.nio.ByteBuffer;
import java.lang.ref.WeakReference;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

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
 * 隐私：全部画面仅在内存处理（Image->Bitmap->Paddle OCR），不落盘、不缓存句子与回复。
 */
public class CaptureService extends Service {

    public enum Phase { IDLE, STARTING, RUNNING, PAUSED, STOPPING }

    public static final String ACTION_START = "com.screenassistant.START";
    public static final String ACTION_PAUSE = "com.screenassistant.PAUSE";
    public static final String ACTION_RESUME = "com.screenassistant.RESUME";
    public static final String ACTION_STOP = "com.screenassistant.STOP";

    /** 供 MainActivity 刷新 UI 的轻量状态（进程内静态，无持久化）。 */
    public static volatile Phase phase = Phase.IDLE;
    public static volatile String lastMessage = "";
    public static volatile boolean uiVisible;
    private static final AtomicInteger visibleAssistantPages = new AtomicInteger();
    private static volatile WeakReference<CaptureService> serviceReference = new WeakReference<>(null);

    private static final String EXTRA_RESULT_CODE = "result_code";
    private static final String EXTRA_RESULT_DATA = "result_data";
    private static final String EXTRA_LEGACY = "legacy_answer";
    private static final int NOTIF_ID = 100;
    private static final String CHANNEL_ID = "capture";

    private static final int CONNECT_TIMEOUT_MS = 5000;
    private static final int READ_TIMEOUT_MS = 10000;
    private static final long REPLY_AUTO_HIDE_MS = 12000L;
    private static final String OVERLAY_CONFLICT = "提示框遮挡识别区，请调整区域或提示框";

    private MediaProjectionManager mpm;
    private NotificationManager notifManager;
    private WindowManager wm;

    private MediaProjection projection;
    private android.hardware.display.VirtualDisplay virtualDisplay;
    private ImageReader imageReader;
    private PaddleOcrBridge paddleOcr;

    private HandlerThread captureThread;
    private Handler captureHandler;
    private final Handler main = new Handler(Looper.getMainLooper());
    private ExecutorService workExecutor;
    private ExecutorService promptExecutor;
    private volatile LiveClient.Session liveSession;
    private boolean operationMode;
    private volatile LiveConnection liveConnection;
    private volatile String connectionStatus = "";
    private volatile LiveFrameAnalyzer liveAnalyzer;
    private long lastLiveOcr;
    private long lastLivePerson, lastFrameTimestamp;
    private AudioCapture audioCapture;
    private boolean microphoneForeground;
    public static volatile java.util.concurrent.CompletableFuture<Void> audioCompletion = java.util.concurrent.CompletableFuture.completedFuture(null);
    private String audioMessage = "";

    private final AtomicInteger busyEpoch = new AtomicInteger(-1);
    private volatile boolean tornDown = true;
    private volatile int sessionEpoch = 0;
    private volatile int messageGeneration;
    private int rotationAtStart;
    private int intervalMs = 5000;
    private String serverUrl = "";
    private int[] screenSize = new int[2];
    private String setupStage = "idle";
    private volatile String teardownReason = "none";
    private final OverlayMessageState overlayState = new OverlayMessageState();

    private View replyOverlayView;
    private TextView replyTextView;
    private volatile boolean overlayBlocksCapture;
    private boolean draggingOverlay;
    private boolean overlayConflictNotified;
    private String displayedPromptId = "";
    private String drawnPromptId = "";
    private int localDebugMessages;
    private final Runnable replyHide = () -> renderOverlay(sessionEpoch);
    private boolean screenReceiverRegistered;
    private final BroadcastReceiver overlayScreenState = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            logEvent("overlay_screen_state", "action=" + intent.getAction()
                    + " interactive=" + getSystemService(PowerManager.class).isInteractive()
                    + " phase=" + phase + " ui=" + uiVisible + " torn_down=" + tornDown);
            renderOverlay(sessionEpoch);
        }
    };

    private MediaProjection.Callback projectionCallback;

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            if (phase != Phase.RUNNING || tornDown) return;
            if (uiVisible) { main.post(CaptureService.this::removeReplyOverlay); scheduleTick(intervalMs); return; }
            if (ScreenMetrics.rotation(CaptureService.this) != rotationAtStart) {
                // 回主线程执行teardown（内部有 stopForeground/stopSelf）
                int rotationEpoch = sessionEpoch;
                main.post(() -> {
                    if (!tornDown && rotationEpoch == sessionEpoch)
                        teardown("屏幕方向已变化，会话已停止，请重新框选并开始辅助", "rotation_changed");
                });
                return;
            }
            if (busyEpoch.get() == sessionEpoch) {
                scheduleTick(intervalMs);
                return;
            }
            if (operationMode) {
                if (liveAnalyzer != null) liveTick();
                else {
                    // 等待连接时丢弃旧帧；恢复时不把积压画面当成当前观察。
                    try (Image ignored = imageReader.acquireLatestImage()) { }
                    catch (RuntimeException ignored) { }
                    scheduleTick(intervalMs);
                }
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
                scheduleTick(intervalMs);
                return;
            }
            final int epoch = sessionEpoch;
            if (!busyEpoch.compareAndSet(-1, epoch)) {
                scheduleNext(epoch);
                return;
            }
            final Bitmap b = crop;
            final String requestUrl = serverUrl;
            workExecutor.execute(() -> {
                try {
                    long coldLoadMs = paddleOcr.initializeBlocking();
                    OCRRunResult ocr = paddleOcr.recognizeBlocking(b);
                    android.util.Log.d("CaptureService", "Paddle OCR coldLoadMs=" + coldLoadMs
                            + " totalMs=" + ocr.getTotalTimeMs() + " lines=" + ocr.getLineCount());
                    if (epoch != sessionEpoch) return;
                    StringBuilder rawText = new StringBuilder();
                    for (com.paddle.ocr.model.OCRResult line : ocr.getResults()) {
                        if (line.getText() == null || line.getText().trim().isEmpty()) continue;
                        if (rawText.length() > 0) rawText.append(' ');
                        rawText.append(line.getText());
                    }
                    String sentence = normalize(rawText.toString());
                    if (sentence.isEmpty() || operationMode) return;
                    AnswerClient.Result result =
                            AnswerClient.post(requestUrl, sentence, CONNECT_TIMEOUT_MS, READ_TIMEOUT_MS);
                    android.util.Log.i("CaptureService", "answer response status=" + result.httpStatus
                            + " outcome=" + result.outcome + " request_id=" + safeId(result.requestId)
                            + " reply_chars=" + (result.reply == null ? 0 : result.reply.length())
                            + " epoch=" + epoch);
                    if (phase != Phase.RUNNING || tornDown || epoch != sessionEpoch) {
                        android.util.Log.i("CaptureService", "answer dropped by lifecycle request_id="
                                + safeId(result.requestId) + " phase=" + phase + " current_epoch=" + sessionEpoch);
                        return;
                    }
                    if (result.outcome == AnswerClient.Outcome.SUCCESS) {
                        showReply(result.reply, epoch, result.requestId);
                    } else if (result.outcome == AnswerClient.Outcome.DROP) {
                        android.util.Log.w("CaptureService", "answer dropped request_id="
                                + safeId(result.requestId) + " status=" + result.httpStatus
                                + " error_type=" + errorType(result.error));
                    }
                } catch (Throwable t) {
                    android.util.Log.e("CaptureService", "Paddle OCR failed; no alternate OCR is used", t);
                    setState("Paddle OCR初始化或识别失败，本轮未识别");
                } finally {
                    b.recycle();
                    busyEpoch.compareAndSet(epoch, -1);
                    scheduleNext(epoch);
                }
            });
        }
    };

    public static void requestStart(Context ctx, int resultCode, Intent data, boolean legacyAnswer) {
        if (phase != Phase.IDLE) return;
        logEvent("service_start_requested", "consent=accepted");
        Intent i = new Intent(ctx, CaptureService.class);
        i.setAction(ACTION_START);
        i.putExtra(EXTRA_RESULT_CODE, resultCode);
        i.putExtra(EXTRA_RESULT_DATA, data);
        i.putExtra(EXTRA_LEGACY, legacyAnswer);
        phase = Phase.STARTING;
        lastMessage = "正在启动本地屏幕采集";
        try { ctx.startForegroundService(i); }
        catch (RuntimeException e) { phase = Phase.IDLE; throw e; }
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
        logEvent("service_stop_requested", "source=app_action");
        Intent i = new Intent(ctx, CaptureService.class).setAction(ACTION_STOP);
        ctx.startService(i);
    }

    public static void setAssistantUiVisible(boolean visible) {
        int count = visibleAssistantPages.updateAndGet(value -> visible ? value + 1 : Math.max(0, value - 1));
        uiVisible = count > 0;
        WeakReference<CaptureService> reference = serviceReference;
        CaptureService service = reference == null ? null : reference.get();
        if (service != null) service.main.post(() -> service.renderOverlay(service.sessionEpoch));
    }

    private static void logEvent(String event, String fields) {
        android.util.Log.i("CaptureService", "t=" + SystemClock.elapsedRealtime()
                + " event=" + event + " " + fields);
    }

    @Override
    public void onCreate() {
        super.onCreate();
        serviceReference = new WeakReference<>(this);
        logEvent("service_created", "phase=" + phase);
        mpm = (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
        notifManager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        wm = getSystemService(WindowManager.class);
        IntentFilter screenEvents = new IntentFilter(Intent.ACTION_SCREEN_ON);
        screenEvents.addAction(Intent.ACTION_SCREEN_OFF);
        androidx.core.content.ContextCompat.registerReceiver(this, overlayScreenState, screenEvents,
                androidx.core.content.ContextCompat.RECEIVER_NOT_EXPORTED);
        screenReceiverRegistered = true;
        NotificationChannel ch = new NotificationChannel(CHANNEL_ID, "屏幕识别",
                NotificationManager.IMPORTANCE_LOW);
        ch.setShowBadge(false);
        notifManager.createNotificationChannel(ch);
        captureThread = new HandlerThread("capture");
        captureThread.start();
        captureHandler = new Handler(captureThread.getLooper());
        workExecutor = Executors.newSingleThreadExecutor();
        paddleOcr = new PaddleOcrBridge(this);
        promptExecutor = Executors.newSingleThreadExecutor();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_STOP : intent.getAction();
        logEvent("service_start_command", "action=" + (action == null ? "missing" : action.substring(action.lastIndexOf('.') + 1))
                + " phase=" + phase);
        switch (action == null ? "" : action) {
            case ACTION_START: {
                if (!tornDown) return START_NOT_STICKY;
                operationMode = !intent.getBooleanExtra(EXTRA_LEGACY, false);
                // Android14：必须先成为 mediaProjection 前台服务，才允许 getMediaProjection()
                int types = ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION;
                microphoneForeground = android.os.Build.VERSION.SDK_INT >= 30 && operationMode
                        && Prefs.getAudioMode(this) == 2 && checkSelfPermission(android.Manifest.permission.RECORD_AUDIO)
                        == android.content.pm.PackageManager.PERMISSION_GRANTED;
                if (android.os.Build.VERSION.SDK_INT >= 30 && microphoneForeground) types |= ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE;
                try {
                    try {
                        startForeground(NOTIF_ID, buildNotification("正在启动…"), types);
                        setupStage = "foreground_service_started";
                        logEvent("foreground_service_started", "types=" + types);
                    } catch (SecurityException e) {
                        if (!microphoneForeground) throw e;
                        microphoneForeground = false;
                        logEvent("foreground_service_microphone_type_denied", "error=" + e.getClass().getSimpleName());
                        startForeground(NOTIF_ID, buildNotification("麦克风条件受限，继续屏幕"), ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
                        setupStage = "foreground_service_started_without_microphone";
                        logEvent("foreground_service_started", "types=" + ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
                    }
                } catch (RuntimeException e) {
                    setupStage = "foreground_service_start";
                    logEvent("foreground_service_start_failed", "stage=" + setupStage + " error=" + e.getClass().getSimpleName());
                    teardown("启动失败（前台服务阶段，" + e.getClass().getSimpleName()
                            + "）。请检查屏幕采集权限与系统设置后重新开始。", "foreground_service_start_failed");
                    break;
                }
                int code = intent.getIntExtra(EXTRA_RESULT_CODE, 0);
                Intent data = intent.getParcelableExtra(EXTRA_RESULT_DATA);
                if (data == null) {
                    teardown("启动失败：未收到屏幕授权数据。请回到应用重新开始并完成系统授权。", "start_data_missing");
                } else {
                    setup(code, data);
                }
                break;
            }
            case ACTION_PAUSE: {
                if (phase == Phase.RUNNING) {
                    messageGeneration++;
                    captureHandler.removeCallbacks(tick);
                    phase = Phase.PAUSED;
                    overlayState.stop();
                    if (liveAnalyzer != null) liveAnalyzer.invalidateBefore(android.os.SystemClock.elapsedRealtime());
                    else if (!operationMode) sessionEpoch++;
                    if (audioCapture != null) { audioCapture.stop(); audioCapture = null; }
                    main.post(this::removeReplyOverlay);
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
                    overlayState.start(operationMode ? connectionStatus : OverlayMessageState.WAITING_TEXT);
                    if (liveAnalyzer != null) liveAnalyzer.invalidateBefore(android.os.SystemClock.elapsedRealtime());
                    startAudio();
                    setState("已继续");
                    notifManager.notify(NOTIF_ID, buildNotification("采集中 · 间隔 "
                            + (intervalMs / 1000) + " 秒"));
                    renderOverlay(sessionEpoch);
                    scheduleTick(300);
                } else if (phase == Phase.IDLE) {
                    stopSelf();
                }
                break;
            }
            case ACTION_STOP:
            default:
                if (liveConnection != null) liveConnection.close(true);
                teardown("已结束辅助", "app_stop_action");
                break;
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        if (screenReceiverRegistered) { unregisterReceiver(overlayScreenState); screenReceiverRegistered = false; }
        logEvent("service_destroyed", "phase=" + phase + " teardown_reason=" + teardownReason);
        teardownInternal();
        LiveFrameAnalyzer closingAnalyzer = liveAnalyzer;
        PaddleOcrBridge closingPaddleOcr = paddleOcr;
        if (workExecutor != null) {
            workExecutor.execute(() -> {
                if (closingAnalyzer != null) closingAnalyzer.close();
                if (closingPaddleOcr != null) closingPaddleOcr.close();
            });
            workExecutor.shutdown();
        }
        // 保住迟到创建凭证并幂等结束旧场，再由连接线程自行退出。
        if (promptExecutor != null) promptExecutor.shutdown();
        if (captureThread != null) captureThread.quitSafely();
        WeakReference<CaptureService> reference = serviceReference;
        if (reference != null && reference.get() == this) serviceReference = new WeakReference<>(null);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    /** 供 debug 子类复用真实窗口；不创建投影、不启动联网或采集线程任务。 */
    final boolean showLocalOverlayForDebug() {
        if ((getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) == 0
                || phase != Phase.IDLE || !tornDown || uiVisible) return false;
        screenSize = ScreenMetrics.realSizeDpi(this);
        if (screenSize[0] <= 0 || screenSize[1] <= 0) return false;
        tornDown = false;
        operationMode = false;
        phase = Phase.RUNNING;
        localDebugMessages = 0;
        overlayState.start();
        renderOverlay(sessionEpoch);
        return true;
    }

    final void expandLocalOverlayForDebug() {
        if ((getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) == 0
                || phase != Phase.RUNNING || tornDown || operationMode) return;
        long nowNs = SystemClock.elapsedRealtimeNanos();
        String label = (++localDebugMessages % 2 == 1) ? "A" : "B";
        try {
            // 固定的过去/未来服务时钟，验证产品入队路径不依赖设备墙钟；不联网。
            long serverNow = "A".equals(label) ? 1_700_000_000_000L : 1_900_000_000_000L;
            org.json.JSONObject page = new org.json.JSONObject().put("server_time_ms", serverNow)
                    .put("server_processing_ms", 0).put(PromptTiming.SENT_NS, nowNs).put(PromptTiming.RECEIVED_NS, nowNs);
            long deadline = PromptTiming.from(page).deadline(new org.json.JSONObject().put("expires_at_ms", serverNow + 15000));
            submitOverlayMessage("本地固定测试文字" + label + "\n第一行\n第二行\n第三行\n第四行\n第五行",
                    sessionEpoch, deadline, "debug-" + nowNs, 0);
        } catch (org.json.JSONException e) { throw new IllegalStateException(e); }
    }

    /** 由debug服务的系统DUMP入口核对同一个产品TextView；不读取采集帧或联网。 */
    final void dumpLocalOverlayForDebug(java.io.PrintWriter writer) {
        if ((getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) == 0 || operationMode) return;
        TextView body = replyTextView;
        View window = replyOverlayView;
        if (body == null || window == null) { writer.println("overlay=absent"); return; }
        android.graphics.Rect bounds = new android.graphics.Rect();
        boolean visible = body.getGlobalVisibleRect(bounds);
        android.text.Layout layout = body.getLayout();
        int ellipsized = 0;
        if (layout != null) for (int i=0; i<layout.getLineCount(); i++) ellipsized += layout.getEllipsisCount(i);
        writer.println("at=" + System.currentTimeMillis() + " id=" + safeId(overlayState.currentId())
                + " elapsed=" + SystemClock.elapsedRealtime()
                + " drawn=" + safeId(drawnPromptId) + " body_shown=" + body.isShown()
                + " visible_rect=" + visible + " bounds=" + bounds.toShortString()
                + " window=" + window.getWidth() + "x" + window.getHeight() + " ellipsized=" + ellipsized
                + " source_expires=" + overlayState.sourceExpiryMs() + " visible_until=" + overlayState.expiresAtMs());
        writer.println("fixed_text=" + body.getText());
    }

    private void setup(int resultCode, Intent data) {
        setupStage = "setup_entered";
        logEvent("projection_setup_started", "phase=" + phase);
        try {
            if (!tornDown) {
                teardownReason = "setup_replaced_previous_session";
                teardownInternal();
            }
            tornDown = false;
            sessionEpoch++;
            setupStage = "display_metrics";
            serverUrl = Prefs.getServerUrl(this);
            liveSession = null; // 实际场次/收件校验后才绑定，不从本地存储推断在线。
            intervalMs = Prefs.getIntervalSec(this) * 1000;
            int[] size = ScreenMetrics.realSizeDpi(this);
            screenSize = size;
            rotationAtStart = ScreenMetrics.rotation(this);
            logEvent("display_metrics_ready", "width=" + size[0] + " height=" + size[1] + " rotation=" + rotationAtStart);

            setupStage = "image_reader";
            imageReader = ImageReader.newInstance(size[0], size[1], PixelFormat.RGBA_8888, 3);
            setupStage = "get_media_projection";
            projection = mpm.getMediaProjection(resultCode, data);
            logEvent("media_projection_acquired", "api=" + android.os.Build.VERSION.SDK_INT);
            // Android14：createVirtualDisplay 前必须注册 onStop 回调
            setupStage = "register_projection_callback";
            final int projectionEpoch = sessionEpoch;
            projectionCallback = new MediaProjection.Callback() {
                @Override public void onStop() {
                    logEvent("projection_on_stop", "prior_teardown=" + teardownReason);
                    main.post(() -> {
                        if (!tornDown && sessionEpoch == projectionEpoch)
                            teardown("屏幕采集已停止。Android 未提供具体原因；回到应用可重新开始。", "projection_on_stop");
                    });
                }
            };
            projection.registerCallback(projectionCallback, captureHandler);
            setupStage = "create_virtual_display";
            virtualDisplay = projection.createVirtualDisplay(
                    "screen-assistant", size[0], size[1], size[2],
                    android.hardware.display.DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                    imageReader.getSurface(), null, captureHandler);
            logEvent("virtual_display_created", "result=" + (virtualDisplay == null ? "null" : "created"));
            if (virtualDisplay == null) throw new IllegalStateException("VirtualDisplay unavailable");
            setupStage = "paddle_ocr";
            lastLiveOcr = 0;
            lastLivePerson = 0; lastFrameTimestamp = 0;
            connectionStatus = operationMode ? LiveConnection.CONNECTING : OverlayMessageState.WAITING_TEXT;
            overlayState.start(connectionStatus);
            phase = Phase.RUNNING;
            setupStage = "running";
            startAudio();
            if (operationMode) startLiveConnection(sessionEpoch);
            logEvent("capture_running", "session=" + (operationMode ? "operation_connecting" : "answer_compatibility"));
            setState(connectionStatus);
            notifManager.notify(NOTIF_ID, buildNotification(connectionStatus));
            renderOverlay(sessionEpoch);
            scheduleTick(800);
        } catch (Exception e) {
            logEvent("projection_setup_failed", "stage=" + setupStage + " error=" + e.getClass().getSimpleName());
            android.util.Log.e("CaptureService", "projection setup failed at stage=" + setupStage, e);
            teardown("启动失败（" + setupStage + "，" + e.getClass().getSimpleName()
                    + "）。请确认权限后重新开始。", "setup_exception");
        }
    }

    private void scheduleTick(long delayMs) {
        if (phase == Phase.RUNNING && !tornDown) {
            captureHandler.postDelayed(tick, delayMs);
        }
    }

    private void startAudio() {
        if (liveSession == null || Prefs.getAudioMode(this) == 0 || audioCapture != null) return;
        if (!audioCompletion.isDone()) {
            int pendingEpoch = sessionEpoch;
            setAudioState("上一段音频正在收尾，完成后恢复所选来源");
            audioCompletion.whenComplete((value,error) -> main.post(() -> {
                if (!tornDown && phase == Phase.RUNNING && pendingEpoch == sessionEpoch) startAudio();
            }));
            return;
        }
        int epoch = sessionEpoch;
        if (Prefs.getAudioMode(this) == 2 && android.os.Build.VERSION.SDK_INT >= 30 && !microphoneForeground) {
            setAudioState("麦克风前台条件不足，未启动音轨，其他取数继续"); return;
        }
        try {
            audioCapture = new AudioCapture(this, projection, liveSession, Prefs.getAudioMode(this), Prefs.hostConfirmed(this),
                    message -> { if (epoch == sessionEpoch) setAudioState(message); });
            audioCompletion = audioCapture.completion;
        } catch (Exception e) { setAudioState("音频未启动：" + e.getMessage() + "；其他取数继续"); }
    }

    private void setAudioState(String message) {
        if (message.equals(audioMessage)) return;
        audioMessage = message; setState("采集中");
    }

    private void scheduleNext(int epoch) {
        if (epoch == sessionEpoch) scheduleTick(liveSession == null ? intervalMs : Math.min(intervalMs, 3000));
    }

    private void liveTick() {
        int epoch = sessionEpoch;
        if (overlayBlocksCapture) { scheduleNext(epoch); return; }
        if (!busyEpoch.compareAndSet(-1, epoch)) { scheduleNext(epoch); return; }
        LiveFrameAnalyzer.Frame frame = null;
        try (Image image = imageReader.acquireLatestImage()) {
            if (image != null) {
                long now = android.os.SystemClock.elapsedRealtime();
                if (image.getTimestamp() <= lastFrameTimestamp) {
                    if (liveAnalyzer != null) liveAnalyzer.invalidateBefore(now);
                    setState("屏幕源时间戳未前进，本轮不发送旧画面");
                    busyEpoch.compareAndSet(epoch, -1); scheduleNext(epoch); return;
                }
                lastFrameTimestamp = image.getTimestamp();
                boolean ocr = now - lastLiveOcr >= intervalMs;
                boolean person = false;
                frame = LiveFrameAnalyzer.snapshot(this, image, ocr, person);
                if (ocr) lastLiveOcr = now;
                if (person) lastLivePerson = now;
            }
        } catch (Exception e) { setState("屏幕取样失败：" + e.getClass().getSimpleName()); }
        if (frame == null) { busyEpoch.compareAndSet(epoch, -1); scheduleNext(epoch); return; }
        LiveFrameAnalyzer.Frame sample = frame;
        LiveFrameAnalyzer analyzer = liveAnalyzer;
        workExecutor.execute(() -> {
            try {
                if (!tornDown && !overlayBlocksCapture && phase == Phase.RUNNING && epoch == sessionEpoch) analyzer.analyze(sample);
            } catch (Exception e) {
                if (!tornDown && epoch == sessionEpoch) setState("本轮观察未取得：" + e.getClass().getSimpleName());
            } finally { sample.close(); busyEpoch.compareAndSet(epoch, -1); scheduleNext(epoch); }
        });
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
    static Bitmap cropImage(Image image, Rect r) {
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

    private void showReply(String reply, int epoch, String requestId) {
        showMessage("问答", reply, epoch, 0);
    }

    private void startLiveConnection(int epoch) {
        LiveConnection connection = new LiveConnection(Prefs.liveStore(this),
                LiveClient.remote(serverUrl, Prefs.getLiveDescription(this), Prefs.getDouyinAccount(this)),
                new LiveConnection.Listener() {
                    @Override public void status(String text, boolean discard) {
                        main.post(() -> {
                            if (tornDown || epoch != sessionEpoch || text.equals(connectionStatus)) return;
                            connectionStatus = text;
                            overlayState.setStatus(text, discard);
                            setState(text);
                            notifManager.notify(NOTIF_ID, buildNotification(text));
                            renderOverlay(epoch);
                        });
                    }
                    @Override public void session(LiveClient.Session session) {
                        main.post(() -> {
                            if (tornDown || epoch != sessionEpoch) return;
                            try (Image ignored = imageReader.acquireLatestImage()) { }
                            catch (RuntimeException ignored) { }
                            liveSession = session;
                            liveAnalyzer = new LiveFrameAnalyzer(CaptureService.this, paddleOcr, session,
                                    () -> !tornDown && !uiVisible && phase == Phase.RUNNING && sessionEpoch == epoch,
                                    message -> main.post(() -> {
                                        if (!tornDown && epoch == sessionEpoch) {
                                            setState(message);
                                            overlayState.setStatus(connectionStatus + "\n" + message, false);
                                            renderOverlay(epoch);
                                        }
                                    }));
                            if (phase == Phase.RUNNING) startAudio();
                        });
                    }
                    @Override public void prompt(org.json.JSONObject prompt) {
                        long expires = prompt.optLong(PromptTiming.DEADLINE_MS);
                        String id = prompt.optString("prompt_id");
                        logEvent("prompt_received", "id=" + safeId(id) + " source_expires=" + prompt.optLong("expires_at_ms")
                                + " deadline_elapsed=" + expires + " rtt=" + prompt.optLong("_client_rtt_ms")
                                + " processing=" + prompt.optLong("_client_processing_ms")
                                + " transport=" + prompt.optLong("_client_transport_ms") + " epoch=" + epoch);
                        if (expires > SystemClock.elapsedRealtime() && !id.isEmpty())
                            showMessage("运营", prompt.optString("display_text"), epoch, expires, id, prompt.optInt("priority"));
                    }
                    @Override public void ended() {
                        main.post(() -> {
                            if (!tornDown && epoch == sessionEpoch) teardown("共享场次已结束", "live_session_end");
                        });
                    }
                });
        liveConnection = connection;
        LiveConnection.beginWorker();
        try { promptExecutor.execute(() -> {
            boolean prepared = false;
            try {
                while (!tornDown && epoch == sessionEpoch && !connection.isClosed()) {
                    if (!prepared) prepared = connection.connect();
                    boolean received = prepared && connection.poll();
                    if (!received && !connection.isClosed()) {
                        try { Thread.sleep(2000); }
                        catch (InterruptedException e) { Thread.currentThread().interrupt(); break; }
                    }
                }
            } finally {
                try { if (connection.shouldEnd()) {
                    try { audioCompletion.get(20, java.util.concurrent.TimeUnit.SECONDS); }
                    catch (Exception ignored) { /* 仍请求幂等结束 */ }
                    try { connection.finishStop(); }
                    catch (Exception e) { logEvent("session_stop_unconfirmed", "error=" + e.getClass().getSimpleName()); }
                } } finally {
                    LiveConnection.endWorker();
                    Intent changed = new Intent(Prefs.ACTION_STATE).setPackage(getPackageName());
                    sendBroadcast(changed);
                }
            }
        }); } catch (RuntimeException error) { LiveConnection.endWorker(); throw error; }
    }

    private void showMessage(String purpose, String reply, int epoch, long expiresAt) {
        showMessage(purpose, reply, epoch, expiresAt, "", 0);
    }

    private void showMessage(String purpose, String reply, int epoch, long expiresAt, String id, int priority) {
        submitOverlayMessage(DisplayText.labeled(purpose, reply), epoch, expiresAt, id, priority);
    }

    private void submitOverlayMessage(String fullText, int epoch, long expiresAt, String id, int priority) {
        if (phase != Phase.RUNNING || tornDown || epoch != sessionEpoch) return;
        int generation = messageGeneration;
        if (fullText.isEmpty()) return;
        main.post(() -> {
            if (phase != Phase.RUNNING || tornDown || epoch != sessionEpoch || generation != messageGeneration) return;
            long now = SystemClock.elapsedRealtime();
            if (expiresAt > 0 && expiresAt <= now) return;
            boolean accepted = overlayState.update(id, fullText, priority, expiresAt, now, REPLY_AUTO_HIDE_MS);
            logEvent("prompt_processed", "id=" + safeId(id) + " accepted=" + accepted
                    + " pending=" + overlayState.pendingCount() + " expired=" + overlayState.expiredCount()
                    + " overflow=" + overlayState.overflowCount() + " epoch=" + epoch);
            renderOverlay(epoch);
        });
    }

    private void renderOverlay(int epoch) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            main.post(() -> renderOverlay(epoch));
            return;
        }
        main.removeCallbacks(replyHide);
        if (phase != Phase.RUNNING || tornDown || epoch != sessionEpoch) {
            removeReplyOverlay();
            return;
        }
        // Handler使用uptime，休眠会推迟它；关屏移窗，亮屏先按连续时钟重算再附着。
        if (!getSystemService(PowerManager.class).isInteractive()) { removeReplyOverlay(); return; }
        if (android.os.Build.VERSION.SDK_INT >= 23 && !android.provider.Settings.canDrawOverlays(this)) {
            removeReplyOverlay();
            setState("辅助正在运行，但悬浮窗权限不可用。回到应用打开悬浮窗权限后重新开始。");
            notifManager.notify(NOTIF_ID, buildNotification("悬浮窗权限不可用，点此查看"));
            logEvent("overlay_permission_missing", "phase=" + phase);
            return;
        }
        if (!overlayState.shouldDisplay(true, uiVisible)) { removeReplyOverlay(); return; }
        try {
            if (replyOverlayView == null) {
                replyOverlayView = LayoutInflater.from(this).inflate(R.layout.overlay_reply, null);
                replyTextView = replyOverlayView.findViewById(R.id.tv_reply);
                replyOverlayView.getViewTreeObserver().addOnDrawListener(() -> {
                    String id = overlayState.currentId();
                    if (replyOverlayView != null && !id.equals(drawnPromptId)) {
                        logEvent("prompt_drawn", "id=" + safeId(id) + " previous=" + safeId(drawnPromptId)
                                + " at=" + System.currentTimeMillis() + " body_visible=" + replyTextView.isShown()
                                + " width=" + replyOverlayView.getWidth() + " height=" + replyOverlayView.getHeight()
                                + " epoch=" + sessionEpoch);
                        drawnPromptId = id;
                    }
                });
                overlayBlocksCapture = true;
                replyOverlayView.addOnLayoutChangeListener((view,left,top,right,bottom,oldLeft,oldTop,oldRight,oldBottom) -> {
                    if (!draggingOverlay && (right-left != oldRight-oldLeft || bottom-top != oldBottom-oldTop)) {
                        overlayBlocksCapture = true;
                        view.post(this::placeReplyOverlay);
                    }
                });
                replyOverlayView.addOnAttachStateChangeListener(new View.OnAttachStateChangeListener() {
                    @Override
                    public void onViewAttachedToWindow(View view) {
                        logEvent("overlay_attached", "shown=" + view.isShown());
                    }

                    @Override
                    public void onViewDetachedFromWindow(View view) {
                        logEvent("overlay_detached", "phase=" + phase);
                    }
                });
                WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                        WindowManager.LayoutParams.WRAP_CONTENT,
                        WindowManager.LayoutParams.WRAP_CONTENT,
                        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                                | WindowManager.LayoutParams.FLAG_SECURE
                                | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                        PixelFormat.TRANSLUCENT);
                lp.gravity = Gravity.TOP | Gravity.START;
                // Android12+不可信覆盖层的触摸穿透必须满足系统最大遮挡透明度。
                lp.alpha = android.os.Build.VERSION.SDK_INT >= 31
                        ? Math.min(.8f, getSystemService(android.hardware.input.InputManager.class).getMaximumObscuringOpacityForTouch()) : .8f;
                lp.y = (int) (64 * getResources().getDisplayMetrics().density);
                lp.x = (int) (16 * getResources().getDisplayMetrics().density);
                lp.width = Math.max(200, (int) (screenSize[0] * .72f));
                View handle = replyOverlayView.findViewById(R.id.drag_handle);
                handle.setOnTouchListener(new View.OnTouchListener() {
                    float downX, downY; int startX, startY;
                    @Override public boolean onTouch(View view, MotionEvent event) {
                        if (replyOverlayView == null) return false;
                        switch (event.getActionMasked()) {
                            case MotionEvent.ACTION_DOWN:
                                draggingOverlay = true; overlayBlocksCapture = true;
                                downX = event.getRawX(); downY = event.getRawY();
                                startX = lp.x; startY = lp.y; return true;
                            case MotionEvent.ACTION_MOVE:
                                lp.x = startX + Math.round(event.getRawX() - downX);
                                lp.y = startY + Math.round(event.getRawY() - downY);
                                wm.updateViewLayout(replyOverlayView, lp); return true;
                            case MotionEvent.ACTION_UP:
                                view.performClick();
                            case MotionEvent.ACTION_CANCEL:
                                draggingOverlay = false; placeReplyOverlay(); return true;
                            default: return false;
                        }
                    }
                });
                wm.addView(replyOverlayView, lp);
            }
            long now = SystemClock.elapsedRealtime();
            String text = overlayState.textAt(now);
            boolean expanded = !text.isEmpty();
            if (overlayConflictNotified && expanded) text = OVERLAY_CONFLICT + "\n" + text;
            replyTextView.setVisibility(expanded ? View.VISIBLE : View.GONE);
            WindowManager.LayoutParams layout = (WindowManager.LayoutParams) replyOverlayView.getLayoutParams();
            int width = expanded ? Math.max(200, (int) (screenSize[0] * .72f))
                    : Math.round(48 * getResources().getDisplayMetrics().density);
            if (layout.width != width) { layout.width = width; wm.updateViewLayout(replyOverlayView, layout); }
            if (!text.contentEquals(replyTextView.getText())) replyTextView.setText(text);
            replyOverlayView.setVisibility(View.VISIBLE);
            if (!draggingOverlay) {
                overlayBlocksCapture = true;
                replyOverlayView.post(this::placeReplyOverlay);
            }
            String id = overlayState.currentId();
            if (!id.equals(displayedPromptId)) {
                logEvent("prompt_display", "id=" + safeId(id) + " previous=" + safeId(displayedPromptId)
                        + " at=" + System.currentTimeMillis() + " elapsed=" + now
                        + " source_expires_elapsed=" + overlayState.sourceExpiryMs()
                        + " visible_until_elapsed=" + overlayState.expiresAtMs() + " protected_until_elapsed=" + overlayState.protectedUntilMs()
                        + " short_window=" + (expanded && overlayState.expiresAtMs() - now < OverlayMessageState.MIN_READ_MS)
                        + " epoch=" + epoch + " attached=" + replyOverlayView.isAttachedToWindow());
                displayedPromptId = id;
            }
            long nextChange = overlayState.nextChangeAtMs(now);
            if (nextChange > 0) {
                main.postDelayed(replyHide, Math.max(1, nextChange - now));
            }
        } catch (Exception e) {
            logEvent("overlay_attach_failed", "error=" + e.getClass().getSimpleName());
            android.util.Log.w("CaptureService", "overlay attach failed: " + e.getClass().getSimpleName());
            removeReplyOverlay();
            setState("辅助正在运行，但悬浮提示未显示（" + e.getClass().getSimpleName()
                    + "）。请回到应用检查悬浮窗权限后重新开始。");
            notifManager.notify(NOTIF_ID, buildNotification("悬浮提示未显示，点此检查"));
        }
    }

    private void placeReplyOverlay() {
        View view = replyOverlayView;
        if (view == null || draggingOverlay || !view.isAttachedToWindow()) return;
        int[] loc = new int[2]; view.getLocationOnScreen(loc);
        java.util.List<int[]> areas = new java.util.ArrayList<>();
        for (String key : Regions.ACTIVE_KEYS) {
            float[] region = Prefs.getRegion(this, key);
            if (Regions.valid(region)) areas.add(Regions.crop(region, screenSize[0], screenSize[1]));
        }
        int[] target = Regions.placeOverlay(screenSize[0], screenSize[1], view.getWidth(), view.getHeight(),
                loc[0], loc[1], areas.toArray(new int[0][]));
        if (target == null) {
            overlayBlocksCapture = true;
            setState("提示框遮挡识别区域，文字采样已暂停；请调整区域或提示框");
            if (!overlayConflictNotified) {
                overlayConflictNotified = true;
                replyTextView.setText(OVERLAY_CONFLICT + "\n" + overlayState.textAt(SystemClock.elapsedRealtime()));
                android.widget.Toast.makeText(this, "提示框放不下，请调整识别区域", android.widget.Toast.LENGTH_LONG).show();
            }
            return;
        }
        WindowManager.LayoutParams lp = (WindowManager.LayoutParams) view.getLayoutParams();
        if (target[0] != loc[0] || target[1] != loc[1]) {
            lp.x += target[0] - loc[0]; lp.y += target[1] - loc[1];
            wm.updateViewLayout(view, lp);
            view.post(this::verifyReplyPlacement);
        } else verifyReplyPlacement();
    }

    private void verifyReplyPlacement() {
        View view = replyOverlayView;
        if (view == null || draggingOverlay) return;
        int[] loc = new int[2]; view.getLocationOnScreen(loc);
        boolean blocked = false;
        for (String key : Regions.ACTIVE_KEYS) {
            float[] r = Prefs.getRegion(this, key);
            if (Regions.valid(r) && Regions.overlaps(loc[0], loc[1], view.getWidth(), view.getHeight(),
                    Regions.crop(r, screenSize[0], screenSize[1]))) blocked = true;
        }
        overlayBlocksCapture = blocked;
        if (blocked) {
            setState("提示框遮挡识别区域，文字采样已暂停；请调整区域或提示框");
            if (!overlayConflictNotified) {
                overlayConflictNotified=true;
                replyTextView.setText(OVERLAY_CONFLICT + "\n" + overlayState.textAt(SystemClock.elapsedRealtime()));
            }
        } else if (overlayConflictNotified) {
            overlayConflictNotified=false;
            replyTextView.setText(overlayState.textAt(SystemClock.elapsedRealtime()));
        }
    }

    private static String safeId(String id) {
        if (id == null || id.isEmpty()) return "missing";
        String safe = id.replaceAll("[^A-Za-z0-9_-]", "");
        return safe.substring(0, Math.min(safe.length(), 64));
    }

    private static String errorType(String error) {
        if (error == null || error.isEmpty()) return "unknown";
        int colon = error.indexOf(':');
        int space = error.indexOf(' ');
        int end = colon >= 0 ? colon : (space >= 0 ? space : error.length());
        return error.substring(0, Math.min(end, 48)).replaceAll("[^A-Za-z0-9_-]", "");
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
        overlayBlocksCapture = false;
        overlayConflictNotified = false;
        displayedPromptId = "";
        drawnPromptId = "";
    }

    private void setState(String message) {
        if (operationMode && (phase == Phase.RUNNING || phase == Phase.PAUSED)
                && !connectionStatus.isEmpty() && !message.equals(connectionStatus))
            message = connectionStatus + "\n" + message;
        lastMessage = message + (audioMessage.isEmpty() ? "" : "\n" + audioMessage);
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
        teardown(message, "unspecified");
    }

    private void teardown(String message, String reason) {
        teardownReason = reason;
        logEvent("teardown_requested", "reason=" + reason + " phase=" + phase + " already_torn_down=" + tornDown);
        if (tornDown) {
            if (phase == Phase.STOPPING) return;
            phase = Phase.IDLE;
            setState(message);
            stopForegroundAndSelf();
            return;
        }
        teardownInternal();
        setState(message);
        phase = Phase.STOPPING;
        Runnable finish = () -> {
            if (phase != Phase.STOPPING) return;
            phase = Phase.IDLE;
            setState(message);
            notifManager.cancel(NOTIF_ID);
            stopForegroundAndSelf();
        };
        audioCompletion.whenComplete((value, error) -> main.post(finish));
        main.postDelayed(finish, 20000);
    }

    private void teardownInternal() {
        if (liveConnection != null) liveConnection.close(false);
        tornDown = true;
        phase = Phase.IDLE;
        sessionEpoch++;
        messageGeneration++;
        overlayState.stop();
        if (audioCapture != null) { audioCapture.stop(); audioCapture = null; }
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
                logEvent("projection_stop_requested", "reason=" + teardownReason);
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
