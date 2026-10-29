package com.screenassistant;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.IOException;
import java.net.ConnectException;
import java.net.UnknownHostException;
import java.util.function.LongSupplier;

/** 现有场次 HTTP 契约的薄适配。连接结果不决定本地投影/窗口的生死。 */
final class LiveConnection {
    static final String CONNECTING = "正在连接服务\n屏幕采集已启动，暂无运营提示";
    static final String OFFLINE = "服务未连接\n本地屏幕采集继续，暂无新运营提示";
    static final String CREATE_UNKNOWN = "场次创建未确认\n请回到应用核对，未重复创建";

    interface Store {
        LiveClient.Session get();
        void save(LiveClient.Session session) throws IOException;
        boolean creating();
        void creating(boolean value) throws IOException;
        boolean stopping();
        void stopping(boolean value) throws IOException;
    }
    interface Remote {
        LiveClient.Session create() throws Exception;
        JSONObject summary(LiveClient.Session session) throws Exception;
        JSONObject start(LiveClient.Session session) throws Exception;
        JSONObject poll(LiveClient.Session session, String cursor) throws Exception;
        void stop(LiveClient.Session session) throws Exception;
    }
    interface Listener {
        void status(String text, boolean discardMessage);
        void session(LiveClient.Session session);
        void prompt(JSONObject prompt);
        void ended();
    }

    private final Store store;
    private final Remote remote;
    private final Listener listener;
    private final LongSupplier uptime;
    private volatile boolean closed;
    private boolean endRequested, announced, blocked;
    private LiveClient.Session session, stopTarget;
    private String cursor = "", recap = "复盘状态待核对";
    private boolean recapStartUnconfirmed;
    private long refreshRecapAt;

    LiveConnection(Store store, Remote remote, Listener listener) {
        this(store, remote, listener, () -> System.nanoTime() / 1_000_000L);
    }

    LiveConnection(Store store, Remote remote, Listener listener, LongSupplier uptime) {
        this.store = store; this.remote = remote; this.listener = listener;
        this.uptime = uptime;
    }

    boolean connect() {
        if (closed || blocked) return false;
        try {
            if (store.stopping()) {
                status("上场结束未确认\n正在核对，暂不创建新场次", true);
                finishStop();
            }
            if (closed) return false;
            session = store.get();
            if (session == null || session.stopped) {
                if (store.creating()) { status(CREATE_UNKNOWN, true); return false; }
                // 先同步落盘：崩溃或创建响应丢失时禁止盲目重复POST。
                synchronized (this) {
                    if (closed) return false;
                    store.creating(true);
                }
                LiveClient.Session created;
                try { created = remote.create(); }
                catch (Exception e) {
                    // 仅明确的建连/DNS失败或确定的客户端拒绝可认为没有创建成功。
                    if (e instanceof ConnectException || e instanceof UnknownHostException
                            || e instanceof LiveClient.HttpError && ((LiveClient.HttpError)e).status >= 400
                            && ((LiveClient.HttpError)e).status < 500) store.creating(false);
                    if (e instanceof LiveClient.HttpError && !store.creating()) {
                        blocked = true;
                        status("场次请求未通过\n请回到应用核对连接与账号设置", true);
                    } else status(store.creating() ? CREATE_UNKNOWN : OFFLINE, true);
                    return false;
                }
                synchronized (this) {
                    session = created;
                    // 即使已停止也先保住刚返回的凭证；绝不丢弃后再创建。
                    store.save(created);
                    if (endRequested) { stopTarget = created; store.stopping(true); }
                    if (closed) return false;
                }
            }
            JSONObject summary = remote.summary(session);
            if (closed) return false;
            if (!summary.has("stopped_at_ms")) throw new IOException("场次状态缺失");
            if (!summary.isNull("stopped_at_ms")) { serverStopped(); return false; }
            JSONObject backstage = summary.optJSONObject("backstage_recap");
            recap = backstage == null ? "复盘状态待核对" : recapLabel(backstage.optString("status"));
            registerRecap();
            refreshRecapAt = uptime.getAsLong() + 30000;
            // 尚不宣称实时在线；由下一次真实收件成功确认。
            status("正在连接实时收件\n" + recap, true);
            return !closed;
        } catch (Exception e) {
            status(store.stopping() ? "上场结束未确认\n服务未连接，暂不创建新场次"
                    : store.creating() ? CREATE_UNKNOWN : OFFLINE, true);
            return false;
        }
    }

    boolean poll() {
        if (closed || blocked || session == null) return false;
        try {
            JSONObject page = remote.poll(session, cursor);
            if (closed) return false;
            JSONArray prompts = page.getJSONArray("prompts");
            String next = page.getString("next_cursor");
            // 先处理终止，不能先显示同页中的过期场次内容。
            if (page.optBoolean("stopped")) { serverStopped(); return false; }
            if (uptime.getAsLong() >= refreshRecapAt) {
                refreshRecapAt = uptime.getAsLong() + 30000;
                try {
                    JSONObject summary = remote.summary(session);
                    if (closed) return false;
                    if (summary.has("stopped_at_ms") && !summary.isNull("stopped_at_ms")) {
                        serverStopped(); return false;
                    }
                    JSONObject backstage = summary.optJSONObject("backstage_recap");
                    String value = backstage == null ? "" : backstage.optString("status");
                    recap = recapLabel(value);
                    if (recapStartUnconfirmed || "not_started".equals(value)) registerRecap();
                } catch (Exception e) { recap = "复盘状态未取得"; }
            }
            synchronized (this) {
                if (closed) return false;
                if (!announced) { announced = true; listener.session(session); }
                status("实时已连接，等待运营提示\n" + recap, false);
                for (int i = 0; i < prompts.length() && !closed; i++) listener.prompt(prompts.getJSONObject(i));
                cursor = next;
            }
            return true;
        } catch (Exception e) { status(OFFLINE, true); return false; }
    }

    private void registerRecap() {
        if (closed) return;
        try {
            JSONObject started = remote.start(session); // 同一id幂等；只登记复盘/来源。
            recap = recapLabel(started.optString("task_status"));
            recapStartUnconfirmed = false;
        } catch (Exception e) { recap = "复盘登记未确认"; recapStartUnconfirmed = true; }
    }

    private synchronized void serverStopped() throws IOException {
        if (closed) return;
        LiveClient.Session current = store.get();
        if (current != null && current.id.equals(session.id)) store.save(session.asStopped());
        closed = true;
        listener.ended();
    }

    synchronized void close(boolean endSession) {
        endRequested |= endSession;
        if (endSession) stopTarget = session != null && !session.stopped ? session : store.get();
        closed = true;
        LiveClient.Session current = store.get();
        if (endRequested && current != null && !current.stopped) {
            try { store.stopping(true); }
            catch (IOException ignored) { /* finishStop 仍尝试结束；不伪报成功 */ }
        }
    }

    /** 仅由连接线程/空闲场次结束操作调用；失败保留同场次待结束状态。 */
    void finishStop() throws Exception {
        LiveClient.Session target;
        synchronized (this) { target = stopTarget != null ? stopTarget : store.get(); }
        if (target == null || target.stopped) {
            LiveClient.Session current = store.get();
            if (current == null || current.stopped && (target == null || current.id.equals(target.id))) store.stopping(false);
            return;
        }
        LiveClient.Session stored = store.get();
        if (stored != null && stored.id.equals(target.id)) store.stopping(true);
        // 创建后立即停止或/start响应丢失时，先幂等登记Android来源。
        // 复盘任务冲突不阻断stop；stop自己校验场次来源。
        try { remote.start(target); } catch (Exception ignored) { }
        remote.stop(target);
        synchronized (this) {
            LiveClient.Session current = store.get();
            if (current != null && current.id.equals(target.id)) {
                store.save(target.asStopped());
                store.stopping(false);
            }
        }
    }

    boolean isClosed() { return closed; }
    synchronized boolean shouldEnd() { return endRequested; }

    private synchronized void status(String text, boolean discard) {
        if (!closed) listener.status(text, discard);
    }

    private static String recapLabel(String value) {
        switch (value) {
            case "failed": return "复盘取数失败";
            case "needs_input": return "复盘需核对账号或后台";
            case "not_configured": return "复盘未配置抖音号";
            case "waiting_for_stop": return "复盘任务已登记，取数状态待确认";
            case "capturing": return "复盘任务已领取，等待本场结果";
            case "not_connected": return "本场复盘不可取数，不自动补领";
            case "captured_locally": return "复盘五项已读取";
            case "partial": return "复盘部分数据缺失";
            default: return "复盘状态待核对";
        }
    }
}
