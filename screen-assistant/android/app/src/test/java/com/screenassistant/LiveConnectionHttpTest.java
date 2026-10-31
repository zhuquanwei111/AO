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
    static LiveConnection.Remote remote(String base, String description, String account) {
        return LiveClient.remote(base, description, account, System::nanoTime);
    }
    static class Fixture implements AutoCloseable {
        final ServerSocket server = new ServerSocket(0, 10, InetAddress.getByName("127.0.0.1"));
        final ExecutorService executor = Executors.newCachedThreadPool();
        final AtomicInteger creates = new AtomicInteger(), polls = new AtomicInteger(), stops = new AtomicInteger();
        final List<String> requests = Collections.synchronizedList(new ArrayList<>());
        final List<String> observationBodies = Collections.synchronizedList(new ArrayList<>());
        final CountDownLatch polling = new CountDownLatch(1), releasePoll = new CountDownLatch(1);
        volatile boolean loseCreate, offline, delayPoll;
        volatile int createHttp = 201, createDelayMs;
        volatile String summaryStatus="failed";
        volatile int summaryHttp=200;
        volatile int stopHttp=200;
        volatile boolean summaryStopped;
        volatile String clockFields = ",\"server_time_ms\":1000,\"server_processing_ms\":0";
        volatile String expiryJson = "16000";
        volatile String promptsJson;
        volatile String createCapabilities = "";
        volatile int pollBodyDelayMs;
        volatile Runnable beforePollResponse;
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
                if (createDelayMs > 0) {
                    try { Thread.sleep(createDelayMs); }
                    catch (InterruptedException error) { Thread.currentThread().interrupt(); return; }
                }
                respond(e, createHttp, createHttp == 201
                        ? "{\"session_id\":\"one\",\"session_token\":\"private-token\",\"started_at_ms\":10" + createCapabilities + "}" : "{}");
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
                if (beforePollResponse != null) beforePollResponse.run();
                String prompts = promptsJson != null ? promptsJson
                        : "[{\"prompt_id\":\"fixture\",\"priority\":25,\"expires_at_ms\":" + expiryJson + ",\"display_text\":\"真实接口夹具建议\"}]";
                respond(e, 200, "{\"session_id\":\"one\"" + clockFields + ",\"prompts\":" + prompts + ","
                        + "\"next_cursor\":\"cursor-one\",\"stopped\":false}");
            } else if (path.endsWith("/observations")) {
                observationBodies.add(body);
                respond(e, 200, "{}");
            } else if (path.endsWith("/stop")) {
                stops.incrementAndGet();
                respond(e, stopHttp, "{\"session_id\":\"one\",\"stopped_at_ms\":20}");
            } else respond(e, 404, "{}");
        }
        void respond(Socket e, int status, String value) throws IOException {
            byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
            e.getOutputStream().write(("HTTP/1.1 " + status + " Fixture\r\nCache-Control: no-store\r\nContent-Type: application/json\r\nContent-Length: "
                    + bytes.length + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
            if (pollBodyDelayMs > 0 && value.contains("\"prompts\"")) {
                try { Thread.sleep(pollBodyDelayMs); }
                catch (InterruptedException error) { Thread.currentThread().interrupt(); throw new IOException(error); }
            }
            e.getOutputStream().write(bytes);
        }
        public void close() throws IOException { releasePoll.countDown(); server.close(); executor.shutdownNow(); }
    }

    @Test public void createCapabilityControlsRealObservationBodies() throws Exception {
        try (Fixture fixture = new Fixture()) {
            fixture.createCapabilities = ",\"capabilities\":{\"observation_batch_metadata\":true}";
            LiveClient.Session session = LiveClient.create(fixture.base(), "", "");
            assertTrue(session.batchMetadata);
            CollectedRows<org.json.JSONObject> rows = new CollectedRows<>(3);
            LiveFrameAnalyzer.collectComment(rows, 2, "甲", 100, new org.json.JSONObject(), .9);
            LiveFrameAnalyzer.collectComment(rows, 7, "乙", 100, new org.json.JSONObject(), .2);
            rows.add(new org.json.JSONObject().put("kind", "online_count"));
            LiveFrameAnalyzer.freezeObservationMetadata(rows, session.batchMetadata);
            BatchDispatch.submit(Runnable::run, rows, () -> true, () -> true,
                    row -> LiveClient.call(session, "observations", row), ignored -> {}, false);
            assertEquals(3, fixture.observationBodies.size());
            org.json.JSONObject first = new org.json.JSONObject(fixture.observationBodies.get(0));
            org.json.JSONObject second = new org.json.JSONObject(fixture.observationBodies.get(1));
            assertEquals(2, first.getInt("capture_comment_count"));
            assertEquals(2, second.getInt("capture_comment_count"));
            assertEquals(2, first.getInt("line_index")); assertEquals(7, second.getInt("line_index"));
            assertEquals(first.getString("capture_batch_id"), second.getString("capture_batch_id"));
            LiveClient.call(session, "observations", rows.rows.get(0).value);
            assertEquals(fixture.observationBodies.get(0), fixture.observationBodies.get(3));

            fixture.createCapabilities = "";
            LiveClient.Session old = LiveClient.create(fixture.base(), "", "");
            assertFalse(old.batchMetadata);
            CollectedRows<org.json.JSONObject> legacy = new CollectedRows<>(1);
            LiveFrameAnalyzer.collectComment(legacy, 5, "旧行", 100, new org.json.JSONObject(), .9);
            LiveFrameAnalyzer.freezeObservationMetadata(legacy, old.batchMetadata);
            LiveClient.call(old, "observations", legacy.rows.get(0).value);
            org.json.JSONObject oldBody = new org.json.JSONObject(fixture.observationBodies.get(4));
            assertFalse(oldBody.has("capture_batch_id"));
            assertFalse(oldBody.has("line_index"));
            assertFalse(oldBody.has("capture_comment_count"));
        }
    }

    @Test public void actualConnectionRefusedKeepsDiagnosticAndHandle() throws Exception {
        int port;
        try (ServerSocket reserved = new ServerSocket(0)) { port = reserved.getLocalPort(); }
        LiveConnectionTest.Memory store = new LiveConnectionTest.Memory();
        LiveConnectionTest.Display display = new LiveConnectionTest.Display();
        LiveConnection connection = new LiveConnection(store, remote("http://127.0.0.1:" + port, "", ""), display);
        assertFalse(connection.connect());
        assertFalse(store.creating); assertNull(store.session);
        assertTrue(display.overlay.shouldDisplay(true, false));
        assertEquals(LiveConnection.OFFLINE, display.diagnostic);
        assertTrue(display.messages.isEmpty());
    }

    @Test public void realHttpResponseMustMapTtlToContinuousClockBeforeListener() throws Exception {
        try (Fixture fixture = new Fixture()) {
            LiveConnectionTest.Memory store = new LiveConnectionTest.Memory();
            OverlayMessageState state = new OverlayMessageState(); state.start();
            LiveConnectionTest.Display display = new LiveConnectionTest.Display() {
                @Override public void prompt(org.json.JSONObject prompt) {
                    super.prompt(prompt);
                    long now = System.nanoTime() / 1_000_000L;
                    long deadline = prompt.optLong("_client_expires_elapsed_ms");
                    if (deadline > now) state.update(prompt.optString("prompt_id"), prompt.optString("display_text"),
                            25, deadline, now, 12000);
                }
            };
            LiveConnection connection = new LiveConnection(store, remote(fixture.base(), "", ""), display);
            assertTrue(connection.connect()); assertTrue(connection.poll());
            assertTrue("服务端1970年时钟不能令当前本机提示失效", state.isMessageActive(System.nanoTime()/1_000_000L));
            assertEquals("真实接口夹具建议", state.textAt(System.nanoTime()/1_000_000L));
        }
    }

    @Test public void realHttpPromptsUseEightSecondProtectionWithoutRenewingSourceDeadline() throws Exception {
        try (Fixture fixture = new Fixture()) {
            java.util.concurrent.atomic.AtomicLong nanos = new java.util.concurrent.atomic.AtomicLong(1_000_000_000L);
            OverlayMessageState state = new OverlayMessageState(); state.start();
            LiveConnectionTest.Display display = new LiveConnectionTest.Display() {
                @Override public void prompt(org.json.JSONObject prompt) {
                    super.prompt(prompt);
                    long now = nanos.get() / 1_000_000L;
                    state.update(prompt.optString("prompt_id"), prompt.optString("display_text"),
                            prompt.optInt("priority"), prompt.optLong(PromptTiming.DEADLINE_MS), now, 12000);
                }
            };
            LiveConnection connection = new LiveConnection(new LiveConnectionTest.Memory(),
                    LiveClient.remote(fixture.base(), "", "", nanos::get), display);
            assertTrue(connection.connect());
            fixture.promptsJson = "[" + item("A", "31000") + "]";
            assertTrue(connection.poll());
            nanos.set(1_887_000_000L);
            fixture.promptsJson = "[" + item("C", "31000") + "]";
            assertTrue(connection.poll());
            assertEquals("建议A", state.textAt(5999));
            assertEquals("建议A", state.textAt(8999));
            assertEquals("建议C", state.textAt(9000));
            assertEquals(2, display.messages.size());
        }
    }

    @Test public void realHttpLongPollUsesFrozenProcessingContractAtActualBoundaries() throws Exception {
        try (Fixture fixture = new Fixture()) {
            java.util.concurrent.atomic.AtomicLong nanos = new java.util.concurrent.atomic.AtomicLong(1_000_000_000L);
            List<Long> deadlines = new ArrayList<>();
            LiveConnectionTest.Display display = new LiveConnectionTest.Display() {
                @Override public void prompt(org.json.JSONObject p) { super.prompt(p); deadlines.add(p.optLong(PromptTiming.DEADLINE_MS)); }
            };
            LiveConnection connection = new LiveConnection(new LiveConnectionTest.Memory(),
                    LiveClient.remote(fixture.base(), "", "", nanos::get), display);
            assertTrue(connection.connect()); assertTrue(connection.poll());
            fixture.clockFields = ",\"server_time_ms\":16000,\"server_processing_ms\":15000";
            fixture.expiryJson = "31000";
            fixture.beforePollResponse = () -> nanos.set(16_200_000_000L);
            assertTrue(connection.poll());
            assertEquals(30999L, (long) deadlines.get(1));
            assertTrue(fixture.requests.stream().anyMatch(r -> r.contains("wait_ms=15000")));
            fixture.clockFields = ",\"server_time_ms\":31000,\"server_processing_ms\":15000";
            fixture.expiryJson = "46000";
            fixture.beforePollResponse = () -> nanos.set(47_200_000_000L);
            assertTrue(connection.poll());
            assertEquals("慢传输已耗尽寿命，不投递第三条", 2, deadlines.size());
            assertFalse(display.diagnostic.contains(LiveConnection.TIME_UNKNOWN));
        }
    }

    @Test public void realHttpMissingOrMalformedTimeOnlyRejectsHintsAndStopStillWorks() throws Exception {
        try (Fixture fixture = new Fixture()) {
            LiveConnectionTest.Memory store = new LiveConnectionTest.Memory();
            LiveConnectionTest.Display display = new LiveConnectionTest.Display();
            LiveConnection connection = new LiveConnection(store,
                    LiveClient.remote(fixture.base(), "", "", () -> 1_000_000_000L), display);
            assertTrue(connection.connect());
            for (String fields : new String[]{"", ",\"server_time_ms\":1000.0,\"server_processing_ms\":0",
                    ",\"server_time_ms\":1000,\"server_processing_ms\":true",
                    ",\"server_time_ms\":1000,\"server_processing_ms\":1"}) {
                fixture.clockFields = fields;
                assertTrue(connection.poll());
                assertTrue(display.messages.isEmpty());
                assertTrue(display.diagnostic.contains(LiveConnection.TIME_UNKNOWN));
            }
            fixture.clockFields = ",\"server_time_ms\":1000,\"server_processing_ms\":0";
            fixture.expiryJson = "16000.0";
            assertTrue(connection.poll()); assertTrue(display.messages.isEmpty());
            fixture.expiryJson = "16000";
            assertTrue(connection.poll()); assertEquals(1, display.messages.size());
            assertFalse(display.diagnostic.contains(LiveConnection.TIME_UNKNOWN));
            assertEquals(1, fixture.creates.get()); assertEquals(1, display.bindings);
            connection.close(true); connection.finishStop();
            assertEquals(1, fixture.stops.get()); assertTrue(store.session.stopped);
        }
    }

    static class ReceivedDisplay extends LiveConnectionTest.Display {
        final List<org.json.JSONObject> received = new ArrayList<>();
        @Override public void prompt(org.json.JSONObject prompt) {
            super.prompt(prompt);
            received.add(prompt);
        }
        void clear() { received.clear(); messages.clear(); }
    }

    @Test public void typedPromptReachesDisplayWithServerAssociationAndLegacyNeutral() throws Exception {
        try (Fixture fixture = new Fixture()) {
            fixture.promptsJson = "[{\"prompt_id\":\"a\",\"priority\":25,\"expires_at_ms\":16000,\"prompt_type\":\"comment_reply\","
                    + "\"display_text\":\"欢迎回来\",\"reply_to\":{\"comment_id\":\"c1\",\"nickname\":\"小明\",\"text\":\"来了\"}},"
                    + "{\"prompt_id\":\"b\",\"priority\":25,\"expires_at_ms\":16000,\"source\":\"comment\",\"display_text\":\"谢谢\"}]";
            ReceivedDisplay display = new ReceivedDisplay();
            LiveConnection connection = new LiveConnection(new LiveConnectionTest.Memory(),
                    remote(fixture.base(), "", ""), display);
            assertTrue(connection.connect()); assertTrue(connection.poll());
            assertEquals(2, display.received.size());
            assertEquals("评论回复", DisplayText.promptLabel(display.received.get(0)));
            assertEquals("欢迎回来", display.received.get(0).getString("display_text"));
            assertEquals("c1", display.received.get(0).getJSONObject("reply_to").getString("comment_id"));
            assertEquals("提示", DisplayText.promptLabel(display.received.get(1)));
        }
    }

    @Test public void malformedPriorityIsIsolatedAndSourcesDoNotDropValidPrompts() throws Exception {
        for (String bad : new String[]{"true", "100.0", "\"100\"", "null", "2147483648", "MISSING"}) {
            try (Fixture fixture = new Fixture()) {
                String priority = "MISSING".equals(bad) ? "" : ",\"priority\":" + bad;
                fixture.promptsJson = "[{\"prompt_id\":\"A\",\"priority\":25,\"expires_at_ms\":16000,\"display_text\":\"甲\",\"sources\":42},"
                        + "{\"prompt_id\":\"B\"" + priority + ",\"expires_at_ms\":16000,\"display_text\":\"坏项\"},"
                        + "{\"prompt_id\":\"C\",\"priority\":100,\"expires_at_ms\":16000,\"display_text\":\"紧急\",\"sources\":[null,{}]}]";
                ReceivedDisplay display = new ReceivedDisplay();
                LiveConnection connection = new LiveConnection(new LiveConnectionTest.Memory(), remote(fixture.base(), "", ""), display);
                assertTrue(connection.connect()); assertTrue(connection.poll());
                assertEquals(2, display.received.size());
                assertEquals("A", display.received.get(0).getString("prompt_id"));
                assertEquals("C", display.received.get(1).getString("prompt_id"));
                assertEquals(25, display.received.get(0).getInt("priority"));
                assertEquals(100, display.received.get(1).getInt("priority"));
                assertTrue(display.received.get(0).getLong(PromptTiming.DEADLINE_MS) > 0);
                assertTrue(display.received.get(1).getLong(PromptTiming.DEADLINE_MS) > 0);
                assertTrue(display.diagnostic.contains("优先级"));
                fixture.promptsJson = "[]"; assertTrue(connection.poll());
                assertTrue(fixture.requests.stream().anyMatch(r -> r.contains("after=cursor-one")));
            }
        }
    }

    @Test public void validUrgentPriorityStillPreemptsAfterRealHttpDecode() throws Exception {
        try (Fixture fixture = new Fixture()) {
            fixture.promptsJson = "[{\"prompt_id\":\"A\",\"priority\":0,\"expires_at_ms\":16000,\"display_text\":\"普通\"},"
                    + "{\"prompt_id\":\"C\",\"priority\":100,\"expires_at_ms\":16000,\"display_text\":\"紧急\"}]";
            java.util.concurrent.atomic.AtomicLong nanos = new java.util.concurrent.atomic.AtomicLong(1_000_000_000L);
            OverlayMessageState windowState = new OverlayMessageState(); windowState.start();
            LiveConnectionTest.Display display = new LiveConnectionTest.Display() {
                @Override public void prompt(org.json.JSONObject prompt) {
                    super.prompt(prompt);
                    windowState.update(prompt.optString("prompt_id"), prompt.optString("display_text"),
                            LiveConnection.strictPriority(prompt), prompt.optLong(PromptTiming.DEADLINE_MS), 1000, 12000);
                }
            };
            LiveConnection connection = new LiveConnection(new LiveConnectionTest.Memory(),
                    LiveClient.remote(fixture.base(), "", "", nanos::get), display);
            assertTrue(connection.connect()); assertTrue(connection.poll());
            assertEquals("C", windowState.currentId()); assertEquals("紧急", windowState.textAt(1000));
        }
    }

    @Test public void expiredHttpPromptReportsIdWithoutDisplay() throws Exception {
        try (Fixture fixture = new Fixture()) {
            fixture.expiryJson = "999";
            java.util.List<String> drops = new java.util.ArrayList<>();
            LiveConnectionTest.Display display = new LiveConnectionTest.Display() {
                @Override public void promptDropped(String id, String reason) { drops.add(id + ":" + reason); }
            };
            LiveConnection connection = new LiveConnection(new LiveConnectionTest.Memory(),
                    remote(fixture.base(), "", ""), display);
            assertTrue(connection.connect()); assertTrue(connection.poll());
            assertTrue(display.messages.isEmpty());
            assertEquals(java.util.Collections.singletonList("fixture:conservative_deadline_exhausted"), drops);
        }
    }

    static String item(String id, String expiry) {
        return "{\"prompt_id\":\"" + id + "\",\"display_text\":\"建议" + id + "\",\"priority\":"
                + ("C".equals(id) ? 38 : 25) + (expiry == null ? "" : ",\"expires_at_ms\":" + expiry) + "}";
    }

    static void assertValidPair(ReceivedDisplay display) {
        assertEquals(2, display.received.size());
        assertEquals("A", display.received.get(0).optString("prompt_id"));
        assertEquals("C", display.received.get(1).optString("prompt_id"));
        assertEquals("建议A", display.messages.get(0));
        assertEquals("建议C", display.messages.get(1));
        assertEquals(25, display.received.get(0).optInt("priority"));
        assertEquals(38, display.received.get(1).optInt("priority"));
        for (org.json.JSONObject prompt : display.received) {
            assertEquals(16000, prompt.optLong("expires_at_ms"));
            assertEquals(24999, prompt.optLong(PromptTiming.DEADLINE_MS));
        }
    }

    @Test public void realHttpInvalidExpiryPreservesOtherItemsAndCursorThroughFailure() throws Exception {
        try (Fixture fixture = new Fixture()) {
            ReceivedDisplay display = new ReceivedDisplay();
            LiveConnectionTest.Memory store = new LiveConnectionTest.Memory();
            LiveConnection connection = new LiveConnection(store,
                    LiveClient.remote(fixture.base(), "", "", () -> 10_000_000_000L), display);
            assertTrue(connection.connect());
            fixture.promptsJson = "[" + item("A", "16000") + "," + item("B", null)
                    + "," + item("bad", "true") + "," + item("C", "16000") + "]";
            assertTrue(connection.poll());
            assertValidPair(display);
            assertTrue(display.diagnostic.contains(LiveConnection.TIME_UNKNOWN));
            fixture.offline = true;
            assertFalse(connection.poll());
            assertEquals(2, display.received.size());
            assertEquals(LiveConnection.OFFLINE, display.diagnostic);
            fixture.offline = false;
            fixture.promptsJson = "[" + item("D", "16000") + "]";
            assertTrue(connection.poll());
            assertEquals(3, display.received.size());
            assertEquals("D", display.received.get(2).optString("prompt_id"));
            assertFalse(display.diagnostic.contains(LiveConnection.TIME_UNKNOWN));
            assertEquals(2, fixture.requests.stream().filter(r ->
                    r.contains("/prompts?after=cursor-one&wait_ms=15000 private-token")).count());
            assertEquals(1, fixture.creates.get());
            assertEquals(1, display.bindings);
            connection.close(true); connection.finishStop();
            assertEquals(1, fixture.stops.get()); assertTrue(store.session.stopped);
        }
    }

    @Test public void realHttpInvalidExpiryTypesAtEveryPositionKeepValidOrder() throws Exception {
        try (Fixture fixture = new Fixture()) {
            ReceivedDisplay display = new ReceivedDisplay();
            LiveConnection connection = new LiveConnection(new LiveConnectionTest.Memory(),
                    LiveClient.remote(fixture.base(), "", "", () -> 10_000_000_000L), display);
            assertTrue(connection.connect());
            String a = item("A", "16000"), c = item("C", "16000");
            for (String expiry : new String[]{null, "null", "true", "false", "16000.0",
                    "\"16000\"", "-1", "0", "9223372036854775808", "9223372036854775807"}) {
                String bad = item("B", expiry);
                for (String page : new String[]{bad + "," + a + "," + c,
                        a + "," + bad + "," + c, a + "," + c + "," + bad}) {
                    display.clear();
                    fixture.promptsJson = "[" + page + "]";
                    assertTrue(connection.poll());
                    assertValidPair(display);
                    assertTrue(display.diagnostic.contains(LiveConnection.TIME_UNKNOWN));
                }
            }
        }
    }

    @Test public void realHttpBadPageAnchorStillRejectsAllButExpiredItemOnlySkipsItself() throws Exception {
        try (Fixture fixture = new Fixture()) {
            ReceivedDisplay display = new ReceivedDisplay();
            LiveConnection connection = new LiveConnection(new LiveConnectionTest.Memory(),
                    LiveClient.remote(fixture.base(), "", "", () -> 10_000_000_000L), display);
            assertTrue(connection.connect());
            fixture.promptsJson = "[" + item("A", "16000") + "," + item("expired", "1000")
                    + "," + item("C", "16000") + "]";
            for (String fields : new String[]{"", ",\"server_time_ms\":1000.0,\"server_processing_ms\":0",
                    ",\"server_time_ms\":1000,\"server_processing_ms\":1"}) {
                fixture.clockFields = fields;
                assertTrue(connection.poll());
                assertTrue(display.received.isEmpty());
                assertTrue(display.diagnostic.contains(LiveConnection.TIME_UNKNOWN));
            }
            fixture.clockFields = ",\"server_time_ms\":1000,\"server_processing_ms\":0";
            assertTrue(connection.poll());
            assertValidPair(display);
            assertFalse(display.diagnostic.contains(LiveConnection.TIME_UNKNOWN));
            display.clear();
            fixture.promptsJson = "[" + item("A", "16000") + "," + item("invalid", "-1")
                    + "," + item("C", "16000") + "]";
            assertTrue(connection.poll());
            assertValidPair(display);
            assertTrue(display.diagnostic.contains(LiveConnection.TIME_UNKNOWN));
        }
    }

    @Test public void realHttpBodyTransferTimeIsChargedBeforeHintDelivery() throws Exception {
        try (Fixture fixture = new Fixture()) {
            fixture.expiryJson = "1100"; fixture.pollBodyDelayMs = 400;
            LiveConnectionTest.Display display = new LiveConnectionTest.Display();
            LiveConnection connection = new LiveConnection(new LiveConnectionTest.Memory(), remote(fixture.base(), "", ""), display);
            assertTrue(connection.connect());
            long started = System.nanoTime(); assertTrue(connection.poll());
            assertTrue(System.nanoTime()-started >= 300_000_000L);
            assertTrue(display.messages.isEmpty());
            assertFalse(display.diagnostic.contains(LiveConnection.TIME_UNKNOWN));
        }
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
            LiveConnection connection = new LiveConnection(store, remote(fixture.base(), "", "account"), display);
            assertTrue(connection.connect()); assertTrue(connection.poll());
            assertEquals(1, display.bindings); assertEquals(1, display.messages.size());
            assertTrue(display.diagnostic.contains("复盘登记未确认"));
            assertTrue(fixture.requests.stream().anyMatch(r -> r.contains("wait_ms=0 private-token")));
            fixture.offline = true; assertFalse(connection.poll());
            assertEquals(1, display.messages.size());
            assertEquals(LiveConnection.OFFLINE, display.diagnostic);
            fixture.offline = false; assertTrue(connection.poll());
            assertEquals(1, fixture.creates.get());
            assertTrue(fixture.requests.stream().anyMatch(r -> r.contains("after=cursor-one&wait_ms=15000 private-token")));
            connection.close(true); connection.finishStop();
            assertEquals(1, fixture.stops.get()); assertTrue(store.session.stopped); assertFalse(store.stopping);
        }
    }

    @Test public void stopRequiresHttp200AndRetriesOnlyOriginalSession() throws Exception {
        for (int status : new int[]{202, 204}) {
            try (Fixture fixture = new Fixture()) {
                LiveConnectionTest.Memory store = new LiveConnectionTest.Memory();
                LiveConnection connection = new LiveConnection(store, remote(fixture.base(), "", ""),
                        new LiveConnectionTest.Display());
                assertTrue(connection.connect());
                LiveClient.Session original = store.session;
                connection.close(true);
                fixture.stopHttp = status;
                try { connection.finishStop(); fail("非200不能确认结束"); }
                catch (LiveClient.HttpError expected) { assertEquals(status, expected.status); }
                assertSame(original, store.session);
                assertFalse(store.session.stopped); assertTrue(store.stopping);
                fixture.stopHttp = 200;
                connection.finishStop();
                assertTrue(store.session.stopped); assertFalse(store.stopping);
                assertEquals(original.id, store.session.id);
                assertEquals(original.token, store.session.token);
                assertEquals(1, fixture.creates.get()); assertEquals(2, fixture.stops.get());
                assertEquals(2, fixture.requests.stream().filter(
                        r -> r.equals("POST /v1/live/sessions/one/stop private-token")).count());
            }
        }
    }

    @Test public void droppedCreateResponseCannotRepeatNonIdempotentPost() throws Exception {
        try (Fixture fixture = new Fixture()) {
            fixture.loseCreate = true;
            LiveConnectionTest.Memory store = new LiveConnectionTest.Memory();
            LiveConnectionTest.Display display = new LiveConnectionTest.Display();
            assertFalse(new LiveConnection(store, remote(fixture.base(), "", ""), display).connect());
            assertTrue(store.creating); assertNull(store.session);
            fixture.loseCreate = false;
            assertFalse(new LiveConnection(store, remote(fixture.base(), "", ""), display).connect());
            assertEquals(1, fixture.creates.get());
            assertTrue(display.diagnostic.contains("创建未确认"));
        }
    }

    @Test public void failedCreateRequiresExplicitLocalRecoveryBeforeNewHttpCreate() throws Exception {
        for (boolean timeout : new boolean[]{false, true}) {
            try (Fixture fixture = new Fixture()) {
                fixture.createHttp = timeout ? 201 : 503;
                fixture.createDelayMs = timeout ? 16000 : 0;
                LiveConnectionTest.Memory store = new LiveConnectionTest.Memory();
                LiveConnectionTest.Display display = new LiveConnectionTest.Display();
                assertFalse(new LiveConnection(store, remote(fixture.base(), "", ""), display).connect());
                assertTrue(store.creating); assertNull(store.session);
                fixture.createHttp = 201; fixture.createDelayMs = 0;
                assertFalse(new LiveConnection(store, remote(fixture.base(), "", ""), display).connect());
                assertEquals(1, fixture.creates.get());
                assertFalse(LiveConnection.releaseUnknownCreate(store, false, () -> true));
                assertEquals(1, fixture.creates.get());
                assertTrue(LiveConnection.releaseUnknownCreate(store, true, () -> true));
                assertEquals(1, fixture.creates.get()); // 解除本机限制本身不发请求。
                assertTrue(new LiveConnection(store, remote(fixture.base(), "", ""), display).connect());
                assertEquals(2, fixture.creates.get());
            }
        }
    }

    @Test public void lateHttpPollAfterStopCannotBindOrDeliverToNextRun() throws Exception {
        try (Fixture fixture = new Fixture()) {
            fixture.delayPoll = true;
            LiveConnectionTest.Memory store = new LiveConnectionTest.Memory();
            LiveConnectionTest.Display display = new LiveConnectionTest.Display();
            LiveConnection connection = new LiveConnection(store, remote(fixture.base(), "", ""), display);
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
