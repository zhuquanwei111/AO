package com.screenassistant;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;
import java.net.ConnectException;
import java.net.SocketTimeoutException;
import java.util.ArrayList;
import java.util.List;
import static org.junit.Assert.*;

public class LiveConnectionTest {
    static final LiveClient.Session SESSION = new LiveClient.Session("http://original:8000", "one", "secret", 10, false);
    static class Memory implements LiveConnection.Store {
        LiveClient.Session session;
        boolean creating, stopping;
        public LiveClient.Session get() { return session; }
        public void save(LiveClient.Session s) { session = s; creating = false; }
        public boolean creating() { return creating; }
        public void creating(boolean value) { creating = value; }
        public boolean stopping() { return stopping; }
        public void stopping(boolean value) { stopping = value; }
    }
    static class Remote implements LiveConnection.Remote {
        int creates, starts, stops, polls;
        Exception createError, startError, pollError, stopError, summaryError;
        String startStatus = "failed", summaryStatus = "failed";
        Runnable duringCreate, duringPoll;
        String lastCursor;
        String stoppedId;
        JSONObject page = object("{\"prompts\":[],\"next_cursor\":\"c1\",\"stopped\":false}");
        public LiveClient.Session create() throws Exception {
            creates++;
            if (duringCreate != null) duringCreate.run();
            if (createError != null) throw createError;
            return SESSION;
        }
        public JSONObject summary(LiveClient.Session s) throws Exception {
            if (summaryError != null) throw summaryError;
            assertEquals(SESSION.id, s.id);
            assertEquals(SESSION.baseUrl, s.baseUrl);
            return new JSONObject().put("session_id", s.id).put("stopped_at_ms", JSONObject.NULL)
                    .put("backstage_recap", new JSONObject().put("status", summaryStatus));
        }
        public JSONObject start(LiveClient.Session s) throws Exception {
            starts++;
            if (startError != null) throw startError;
            return new JSONObject().put("task_status", startStatus);
        }
        public JSONObject poll(LiveClient.Session s, String cursor) throws Exception {
            polls++; lastCursor = cursor;
            if (duringPoll != null) duringPoll.run();
            if (pollError != null) throw pollError;
            return page;
        }
        public void stop(LiveClient.Session s) throws Exception { stops++; stoppedId = s.id; if (stopError != null) throw stopError; }
    }
    static class Display implements LiveConnection.Listener {
        final OverlayMessageState overlay = new OverlayMessageState();
        final List<String> messages = new ArrayList<>();
        int bindings, ended;
        Display() { overlay.start(LiveConnection.CONNECTING); }
        public void status(String text, boolean discard) { overlay.setStatus(text, discard); }
        public void session(LiveClient.Session s) { bindings++; }
        public void prompt(JSONObject p) { messages.add(p.optString("display_text")); }
        public void ended() { ended++; }
    }

    static JSONObject object(String value) {
        try { return new JSONObject(value); }
        catch (Exception e) { throw new AssertionError(e); }
    }

    @Test public void backendUnreachableStillRequestsStatusOverlayWithoutFakePrompts() throws Exception {
        Memory store = new Memory(); Remote remote = new Remote(); Display display = new Display();
        remote.createError = new ConnectException("refused");
        LiveConnection connection = new LiveConnection(store, remote, display);
        assertFalse(connection.connect());
        assertTrue(display.overlay.shouldDisplay(true, false));
        assertTrue(display.overlay.textAt(1000).contains("服务未连接"));
        assertFalse(display.overlay.shouldDisplay(false, false));
        assertNull(store.session); assertFalse(store.creating);
        assertEquals(0, display.bindings); assertTrue(display.messages.isEmpty());
        remote.createError = null;
        assertTrue(connection.connect()); assertTrue(connection.poll());
        assertEquals(1, display.bindings);
        assertTrue(display.overlay.textAt(1001).contains("实时已连接"));
    }

    @Test public void recapFailureDoesNotStopKnownRealtimeSession() throws Exception {
        Memory store = new Memory(); store.session = SESSION;
        Remote remote = new Remote(); Display display = new Display();
        remote.startError = new LiveClient.HttpError(409);
        remote.page.getJSONArray("prompts").put(new JSONObject().put("display_text", "实际运营建议"));
        LiveConnection connection = new LiveConnection(store, remote, display);
        assertTrue(connection.connect()); assertTrue(connection.poll());
        assertEquals(0, remote.creates); assertEquals(0, remote.stops);
        assertEquals(1, display.bindings); assertEquals("实际运营建议", display.messages.get(0));
        assertTrue(display.overlay.textAt(1000).contains("复盘登记未确认"));
    }

    @Test public void busyRecapIsUnavailableButRealPromptStillArrivesWithoutRequeue() throws Exception {
        Memory store = new Memory(); store.session = SESSION;
        Remote remote = new Remote(); Display display = new Display();
        java.util.concurrent.atomic.AtomicLong clock = new java.util.concurrent.atomic.AtomicLong();
        remote.startStatus = "not_connected"; remote.summaryStatus = "not_connected";
        remote.page.getJSONArray("prompts").put(new JSONObject().put("display_text", "实际运营建议"));
        LiveConnection connection = new LiveConnection(store, remote, display, clock::get);
        assertTrue(connection.connect()); assertTrue(connection.poll());
        assertEquals(1, display.bindings); assertEquals("实际运营建议", display.messages.get(0));
        assertTrue(display.overlay.textAt(1000).contains("本场复盘不可取数"));
        assertTrue(display.overlay.textAt(1000).contains("不自动补领"));
        assertEquals(0, remote.stops); assertEquals(0, display.ended);
        clock.set(30001); assertTrue(connection.poll());
        assertEquals(1, remote.starts); assertEquals(0, remote.creates);
        assertEquals(0, remote.stops); assertEquals(0, display.ended);
    }

    @Test public void pollingRecoversSameCredentialsAndCursorWithoutCreatingAgain() throws Exception {
        Memory store = new Memory(); store.session = SESSION;
        Remote remote = new Remote(); Display display = new Display();
        LiveConnection connection = new LiveConnection(store, remote, display);
        assertTrue(connection.connect()); assertTrue(connection.poll());
        remote.pollError = new ConnectException(); assertFalse(connection.poll());
        assertTrue(display.overlay.textAt(1000).contains("服务未连接"));
        remote.pollError = null; assertTrue(connection.poll());
        assertEquals("c1", remote.lastCursor); assertEquals(0, remote.creates);
        assertEquals(1, display.bindings); assertEquals(SESSION, store.session);
    }

    @Test public void ambiguousCreateIsDurableAndNeverAutomaticallyRetried() throws Exception {
        Memory store = new Memory(); Remote remote = new Remote(); Display display = new Display();
        remote.createError = new SocketTimeoutException("response lost");
        assertFalse(new LiveConnection(store, remote, display).connect());
        assertTrue(store.creating);
        remote.createError = null;
        assertFalse(new LiveConnection(store, remote, display).connect());
        assertEquals(1, remote.creates); assertEquals(0, display.bindings);
        assertTrue(display.overlay.textAt(1000).contains("创建未确认"));
    }

    @Test public void stopDuringCreatePreservesCredentialsAndClosesOnlyOldSession() throws Exception {
        Memory store = new Memory(); Remote remote = new Remote(); Display display = new Display();
        LiveConnection connection = new LiveConnection(store, remote, display);
        remote.duringCreate = () -> connection.close(true);
        assertFalse(connection.connect());
        assertEquals(SESSION, store.session); assertTrue(store.stopping);
        assertEquals(0, display.bindings);
        connection.finishStop();
        assertTrue(store.session.stopped); assertFalse(store.stopping); assertEquals(1, remote.stops);
    }

    @Test public void stoppedPageAndLatePollNeverDeliverSuggestions() throws Exception {
        Memory store = new Memory(); store.session = SESSION;
        Remote remote = new Remote(); Display display = new Display();
        LiveConnection connection = new LiveConnection(store, remote, display);
        assertTrue(connection.connect());
        remote.page.put("stopped", true).getJSONArray("prompts").put(new JSONObject().put("display_text", "旧建议"));
        assertFalse(connection.poll());
        assertEquals(1, display.ended); assertTrue(store.session.stopped); assertTrue(display.messages.isEmpty());

        store.session = SESSION; display = new Display();
        connection = new LiveConnection(store, remote, display);
        assertTrue(connection.connect());
        LiveConnection old = connection;
        remote.duringPoll = () -> old.close(false);
        assertFalse(connection.poll());
        assertEquals(0, display.ended); assertEquals(0, display.bindings);
        assertFalse(store.session.stopped); assertTrue(display.messages.isEmpty());
    }

    @Test public void unconfirmedStopIsReconciledBeforeNewCreate() throws Exception {
        Memory store = new Memory(); store.session = SESSION; store.stopping = true;
        Remote remote = new Remote(); Display display = new Display();
        remote.stopError = new ConnectException();
        LiveConnection connection = new LiveConnection(store, remote, display);
        assertFalse(connection.connect()); assertEquals(0, remote.creates); assertTrue(store.stopping);
        remote.stopError = null;
        assertTrue(connection.connect());
        assertEquals(2, remote.stops); assertEquals(1, remote.creates); assertFalse(store.stopping);
    }

    @Test public void oldConnectionCleanupCannotStopOrOverwriteNextScene() throws Exception {
        Memory store = new Memory(); store.session = SESSION;
        Remote remote = new Remote(); Display display = new Display();
        LiveConnection old = new LiveConnection(store, remote, display);
        assertTrue(old.connect()); old.close(true);
        LiveClient.Session next = new LiveClient.Session("http://next:8000", "two", "next-token", 20, false);
        // 另一个生命周期已幂等确认旧场结束并保存新场次；旧HTTP此时才返回。
        store.session = next; store.stopping = false;
        old.finishStop();
        assertEquals(SESSION.id, remote.stoppedId);
        assertEquals(next, store.session); assertFalse(store.stopping);
    }

    @Test public void recapRecoveryIsThrottledAndDoesNotInterruptRealtime() throws Exception {
        Memory store = new Memory(); store.session = SESSION;
        Remote remote = new Remote(); Display display = new Display();
        java.util.concurrent.atomic.AtomicLong clock = new java.util.concurrent.atomic.AtomicLong();
        remote.startError = new ConnectException();
        LiveConnection connection = new LiveConnection(store, remote, display, clock::get);
        assertTrue(connection.connect()); assertTrue(connection.poll());
        assertEquals(1, remote.starts);
        clock.set(30001); remote.startError = null;
        assertTrue(connection.poll()); assertEquals(2, remote.starts);
        assertTrue(display.overlay.textAt(1000).contains("复盘取数失败"));
        clock.set(60002); remote.summaryError = new LiveClient.HttpError(503);
        assertTrue(connection.poll());
        assertTrue(display.overlay.textAt(1001).contains("实时已连接"));
        assertTrue(display.overlay.textAt(1001).contains("复盘状态未取得"));
        assertEquals(0, remote.creates); assertEquals(0, remote.stops); assertEquals(1, display.bindings);
    }
}
