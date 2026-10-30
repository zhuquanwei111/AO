package com.screenassistant;

import org.junit.Test;
import java.io.IOException;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;
import java.net.InetAddress;
import java.net.Socket;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.Assert.*;

/** 只绑定随机loopback端口，不启动业务后端、不调用模型/后台取数。 */
public class LiveConnectionHttpTest {
    static class Fixture implements AutoCloseable {
        final ServerSocket server = new ServerSocket(0, 10, InetAddress.getByName("127.0.0.1"));
        final ExecutorService executor = Executors.newCachedThreadPool();
        final AtomicInteger creates = new AtomicInteger(), polls = new AtomicInteger(), stops = new AtomicInteger();
        final List<String> requests = Collections.synchronizedList(new ArrayList<>());
        final CountDownLatch polling = new CountDownLatch(1), releasePoll = new CountDownLatch(1);
        volatile boolean loseCreate, offline, delayPoll;
        volatile String summaryStatus="failed";
        volatile int summaryHttp=200;
        volatile boolean summaryStopped;
        Fixture() throws IOException {
            executor.execute(() -> {
                while (!server.isClosed()) {
                    try {
                        Socket socket = server.accept();
                        socket.setSoTimeout(5000);
                        executor.execute(() -> {
                            try (Socket client = socket) { handle(client); }
                            catch (IOException ignored) { /* 客户端断开时由请求侧断言结果 */ }
                        });
                    } catch (IOException stopped) { return; }
                }
            });
        }
        String base() { return "http://127.0.0.1:" + server.getLocalPort(); }
        static String line(InputStream in) throws IOException {
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            int value;
            while ((value = in.read()) >= 0 && value != '\n') if (value != '\r') bytes.write(value);
            return bytes.toString("US-ASCII");
        }
        void handle(Socket e) throws IOException {
            InputStream in = e.getInputStream();
            String[] request = line(in).split(" ");
            if (request.length < 2) return;
            String path = request[1], header, token = "null";
            int length = 0;
            while (!(header = line(in)).isEmpty()) {
                String lower = header.toLowerCase(java.util.Locale.ROOT);
                if (lower.startsWith("content-length:")) length = Integer.parseInt(header.split(":", 2)[1].trim());
                if (lower.startsWith("x-live-session-token:")) token = header.split(":", 2)[1].trim();
            }
            requests.add(request[0] + " " + path + " " + token);
            byte[] payload = new byte[length];
            int offset = 0, read;
            while (offset < length && (read = in.read(payload, offset, length - offset)) > 0) offset += read;
            String body = new String(payload, StandardCharsets.UTF_8);
            if (path.equals("/v1/live/sessions")) {
                creates.incrementAndGet();
                if (loseCreate) return; // 模拟服务器已创建、响应却丢失。
                respond(e, 201, "{\"session_id\":\"one\",\"session_token\":\"private-token\",\"started_at_ms\":10}");
            } else if (path.endsWith("/summary")) {
                respond(e, summaryHttp, "{\"session_id\":\"one\",\"stopped_at_ms\":" + (summaryStopped ? "123" : "null")
                        + ",\"backstage_recap\":{\"status\":\"" + summaryStatus + "\"}}");
            } else if (path.endsWith("/start")) {
                if (!body.contains("android_app")) throw new IOException("source missing");
                respond(e, 409, "{}");
            } else if (path.contains("/prompts?")) {
                polls.incrementAndGet();
                if (delayPoll) {
                    polling.countDown();
                    try { if (!releasePoll.await(5, TimeUnit.SECONDS)) throw new IOException("fixture timeout"); }
                    catch (InterruptedException error) { Thread.currentThread().interrupt(); throw new IOException(error); }
                }
                if (offline) { respond(e, 503, "{}"); return; }
                respond(e, 200, "{\"session_id\":\"one\",\"prompts\":[{\"display_text\":\"真实接口夹具建议\"}],\"next_cursor\":\"cursor-one\",\"stopped\":false}");
            } else if (path.endsWith("/stop")) {
                stops.incrementAndGet();
                respond(e, 200, "{\"session_id\":\"one\",\"stopped_at_ms\":20}");
            } else respond(e, 404, "{}");
        }
        void respond(Socket e, int status, String value) throws IOException {
            byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
            e.getOutputStream().write(("HTTP/1.1 " + status + " Fixture\r\nContent-Type: application/json\r\nContent-Length: "
                    + bytes.length + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
            e.getOutputStream().write(bytes);
        }
        public void close() throws IOException { releasePoll.countDown(); server.close(); executor.shutdownNow(); }
    }

    @Test public void actualConnectionRefusedStillHasOverlayRequest() throws Exception {
        int port;
        try (ServerSocket reserved = new ServerSocket(0)) { port = reserved.getLocalPort(); }
        LiveConnectionTest.Memory store = new LiveConnectionTest.Memory();
        LiveConnectionTest.Display display = new LiveConnectionTest.Display();
        LiveConnection connection = new LiveConnection(store, LiveClient.remote("http://127.0.0.1:" + port, "", ""), display);
        assertFalse(connection.connect());
        assertFalse(store.creating); assertNull(store.session);
        assertTrue(display.overlay.shouldDisplay(true, false));
        assertEquals(LiveConnection.OFFLINE, display.overlay.textAt(1000));
        assertTrue(display.messages.isEmpty());
    }

    @Test public void manualOldSessionRefreshReadsLateResultAndReportsForbidden() throws Exception {
        try (Fixture fixture = new Fixture()) {
            fixture.summaryStopped=true; fixture.summaryStatus="not_connected";
            LiveClient.Session session=new LiveClient.Session(fixture.base(),"one","private-token",10,true);
            org.json.JSONObject first=LiveClient.call(session,"summary",null);
            assertFalse(RecapText.pending(first)); assertFalse(RecapText.terminal(first));
            fixture.summaryStatus="captured_locally";
            org.json.JSONObject refreshed=LiveClient.call(session,"summary",null);
            assertTrue(RecapText.terminal(refreshed));
            fixture.summaryHttp=403;
            try { LiveClient.call(session,"summary",null); fail("应返回权限错误"); }
            catch (LiveClient.HttpError error) {
                assertEquals(403,error.status); assertTrue(RecapRequest.failure(error).contains("读取未确认"));
            }
            assertEquals(0,fixture.creates.get()); assertEquals(0,fixture.stops.get());
            assertEquals(3,fixture.requests.size());
            for (String request:fixture.requests) assertEquals("GET /v1/live/sessions/one/summary private-token",request);
        }
    }

    @Test public void realHttpRecapConflictAndRecoveryUseOneSceneAndOriginalCursor() throws Exception {
        try (Fixture fixture = new Fixture()) {
            LiveConnectionTest.Memory store = new LiveConnectionTest.Memory();
            LiveConnectionTest.Display display = new LiveConnectionTest.Display();
            LiveConnection connection = new LiveConnection(store, LiveClient.remote(fixture.base(), "", "account"), display);
            assertTrue(connection.connect()); assertTrue(connection.poll());
            assertEquals(1, display.bindings); assertEquals(1, display.messages.size());
            assertTrue(display.overlay.textAt(1000).contains("复盘登记未确认"));
            assertTrue(fixture.requests.stream().anyMatch(r -> r.contains("wait_ms=0 private-token")));
            fixture.offline = true; assertFalse(connection.poll());
            assertEquals(1, display.messages.size());
            assertEquals(LiveConnection.OFFLINE, display.overlay.textAt(1001));
            fixture.offline = false; assertTrue(connection.poll());
            assertEquals(1, fixture.creates.get());
            assertTrue(fixture.requests.stream().anyMatch(r -> r.contains("after=cursor-one&wait_ms=15000 private-token")));
            connection.close(true); connection.finishStop();
            assertEquals(1, fixture.stops.get()); assertTrue(store.session.stopped); assertFalse(store.stopping);
        }
    }

    @Test public void droppedCreateResponseCannotRepeatNonIdempotentPost() throws Exception {
        try (Fixture fixture = new Fixture()) {
            fixture.loseCreate = true;
            LiveConnectionTest.Memory store = new LiveConnectionTest.Memory();
            LiveConnectionTest.Display display = new LiveConnectionTest.Display();
            assertFalse(new LiveConnection(store, LiveClient.remote(fixture.base(), "", ""), display).connect());
            assertTrue(store.creating); assertNull(store.session);
            fixture.loseCreate = false;
            assertFalse(new LiveConnection(store, LiveClient.remote(fixture.base(), "", ""), display).connect());
            assertEquals(1, fixture.creates.get());
            assertTrue(display.overlay.textAt(1000).contains("创建未确认"));
        }
    }

    @Test public void lateHttpPollAfterStopCannotBindOrDeliverToNextRun() throws Exception {
        try (Fixture fixture = new Fixture()) {
            fixture.delayPoll = true;
            LiveConnectionTest.Memory store = new LiveConnectionTest.Memory();
            LiveConnectionTest.Display display = new LiveConnectionTest.Display();
            LiveConnection connection = new LiveConnection(store, LiveClient.remote(fixture.base(), "", ""), display);
            assertTrue(connection.connect());
            ExecutorService thread = Executors.newSingleThreadExecutor();
            try {
                java.util.concurrent.Future<Boolean> pending = thread.submit(connection::poll);
                assertTrue(fixture.polling.await(3, TimeUnit.SECONDS));
                connection.close(false); fixture.releasePoll.countDown();
                assertFalse(pending.get(3, TimeUnit.SECONDS));
                assertEquals(0, display.bindings); assertTrue(display.messages.isEmpty());
                assertFalse(store.session.stopped);
            } finally { thread.shutdownNow(); }
        }
    }
}
