package com.screenassistant;

import org.json.JSONObject;
import org.junit.Test;
import java.net.ConnectException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.Assert.*;

public class PendingStopRecoveryTest {
    @Test public void recoveryHttpUsesOriginalHostTokenSceneAndAndroidSource() throws Exception {
        try (java.net.ServerSocket server = new java.net.ServerSocket(0, 2,
                java.net.InetAddress.getByName("127.0.0.1"))) {
            server.setSoTimeout(5000);
            AtomicReference<Throwable> error = new AtomicReference<>();
            Thread responder = new Thread(() -> {
                try {
                    for (String route : new String[]{"start", "stop"}) try (java.net.Socket socket = server.accept()) {
                        socket.setSoTimeout(5000);
                        java.io.BufferedReader reader = new java.io.BufferedReader(new java.io.InputStreamReader(
                                socket.getInputStream(), java.nio.charset.StandardCharsets.UTF_8));
                        assertEquals("POST /saved/v1/live/sessions/original/" + route + " HTTP/1.1", reader.readLine());
                        int size = 0; String token = "", line;
                        while ((line = reader.readLine()) != null && !line.isEmpty()) {
                            String[] header = line.split(":", 2);
                            if (header[0].equalsIgnoreCase("Content-Length")) size = Integer.parseInt(header[1].trim());
                            if (header[0].equalsIgnoreCase("X-Live-Session-Token")) token = header[1].trim();
                        }
                        assertEquals("saved-token", token); assertTrue(size > 0 && size < 1000);
                        char[] body = new char[size]; int offset = 0;
                        while (offset < size) {
                            int count = reader.read(body, offset, size - offset);
                            assertTrue(count > 0); offset += count;
                        }
                        assertEquals("android_app", new JSONObject(new String(body)).getString("source"));
                        byte[] reply = "{\"session_id\":\"original\"}".getBytes(java.nio.charset.StandardCharsets.UTF_8);
                        socket.getOutputStream().write(("HTTP/1.1 200 OK\r\nContent-Length: " + reply.length
                                + "\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
                                .getBytes(java.nio.charset.StandardCharsets.US_ASCII));
                        socket.getOutputStream().write(reply);
                    }
                } catch (Throwable failure) { error.set(failure); }
            });
            LiveConnectionTest.Memory store = pending();
            store.session = new LiveClient.Session("http://127.0.0.1:" + server.getLocalPort() + "/saved/",
                    "original", "saved-token", 10, false);
            responder.start();
            try {
                assertTrue(LiveConnection.retryPendingStop(store,
                        LiveClient.remote("http://unused.invalid", "", "", () -> 0L), () -> true));
                assertTrue(store.session.stopped); assertFalse(store.stopping);
            } finally { responder.join(6000); }
            assertFalse(responder.isAlive()); assertNull(error.get());
        }
    }

    private LiveConnectionTest.Memory pending() {
        LiveConnectionTest.Memory store = new LiveConnectionTest.Memory();
        store.session = LiveConnectionTest.SESSION; store.stopping = true;
        return store;
    }

    @Test public void reopenedEntryConsumesOnlySavedStopAndNeverCreates() throws Exception {
        LiveConnectionTest.Memory store = pending();
        LiveConnectionTest.Remote remote = new LiveConnectionTest.Remote() {
            @Override public JSONObject start(LiveClient.Session scene) throws Exception {
                assertSame(LiveConnectionTest.SESSION, scene);
                return super.start(scene);
            }
            @Override public void stop(LiveClient.Session scene) throws Exception {
                assertSame(LiveConnectionTest.SESSION, scene);
                super.stop(scene);
            }
        };
        assertTrue(LiveConnection.retryPendingStop(store, remote, () -> true));
        assertTrue(store.session.stopped); assertFalse(store.stopping);
        assertEquals(1, remote.starts); assertEquals(1, remote.stops); assertEquals(0, remote.creates);
        assertFalse(LiveConnection.retryPendingStop(store, remote, () -> true));
        assertEquals(1, remote.stops);
    }

    @Test public void noSavedIntentOrCredentialsDoesNotStopAnything() throws Exception {
        LiveConnectionTest.Memory store = pending();
        LiveConnectionTest.Remote remote = new LiveConnectionTest.Remote();
        store.stopping = false;
        assertFalse(LiveConnection.retryPendingStop(store, remote, () -> true));
        store.stopping = true; store.session = null;
        assertFalse(LiveConnection.retryPendingStop(store, remote, () -> true));
        assertTrue(store.stopping); assertEquals(0, remote.starts); assertEquals(0, remote.stops);
    }

    @Test public void activeCaptureOrWorkerKeepsOriginalStopOwner() throws Exception {
        LiveConnectionTest.Memory store = pending();
        LiveConnectionTest.Remote remote = new LiveConnectionTest.Remote();
        assertFalse(LiveConnection.retryPendingStop(store, remote, () -> false));
        LiveConnection.beginWorker();
        try { assertFalse(LiveConnection.retryPendingStop(store, remote, () -> true)); }
        finally { LiveConnection.endWorker(); }
        assertTrue(store.stopping); assertEquals(0, remote.stops);
    }

    @Test public void mainAndRecapConcurrentEntriesSendOnlyOnce() throws Exception {
        LiveConnectionTest.Memory store = pending();
        CountDownLatch entered = new CountDownLatch(1), release = new CountDownLatch(1);
        AtomicReference<Throwable> error = new AtomicReference<>();
        LiveConnectionTest.Remote remote = new LiveConnectionTest.Remote() {
            @Override public void stop(LiveClient.Session scene) throws Exception {
                super.stop(scene); entered.countDown();
                assertTrue(release.await(5, TimeUnit.SECONDS));
            }
        };
        Thread mainEntry = new Thread(() -> {
            try { assertTrue(LiveConnection.retryPendingStop(store, remote, () -> true)); }
            catch (Throwable failure) { error.set(failure); }
        });
        mainEntry.start();
        try {
            assertTrue(entered.await(5, TimeUnit.SECONDS));
            assertTrue(LiveConnection.isRecoveringStop());
            assertFalse(LiveConnection.retryPendingStop(store, remote, () -> true));
            assertEquals(1, remote.stops);
        } finally { release.countDown(); mainEntry.join(5000); }
        assertFalse(mainEntry.isAlive()); assertNull(error.get());
        assertFalse(LiveConnection.isRecoveringStop());
    }

    @Test public void networkFailureRetainsIntentForNextVisibleRetry() throws Exception {
        LiveConnectionTest.Memory store = pending();
        LiveConnectionTest.Remote remote = new LiveConnectionTest.Remote();
        remote.stopError = new ConnectException("offline");
        try { LiveConnection.retryPendingStop(store, remote, () -> true); fail(); }
        catch (ConnectException expected) { }
        assertSame(LiveConnectionTest.SESSION, store.session); assertTrue(store.stopping);
        assertFalse(LiveConnection.isRecoveringStop());
        remote.stopError = null;
        assertTrue(LiveConnection.retryPendingStop(store, remote, () -> true));
        assertEquals(2, remote.stops); assertEquals(0, remote.creates); assertFalse(store.stopping);
    }

    @Test public void sourceRegistrationFailureStillUsesNormalSameSceneStop() throws Exception {
        LiveConnectionTest.Memory store = pending();
        LiveConnectionTest.Remote remote = new LiveConnectionTest.Remote();
        remote.startError = new ConnectException("offline");
        assertTrue(LiveConnection.retryPendingStop(store, remote, () -> true));
        assertTrue(store.session.stopped); assertEquals(1, remote.stops); assertEquals(0, remote.creates);
    }

    @Test public void alreadyConfirmedStopClearsOnlyResidualIntentWithoutHttp() throws Exception {
        LiveConnectionTest.Memory store = pending(); store.session = store.session.asStopped();
        LiveConnectionTest.Remote remote = new LiveConnectionTest.Remote();
        assertTrue(LiveConnection.retryPendingStop(store, remote, () -> true));
        assertFalse(store.stopping); assertEquals(0, remote.starts); assertEquals(0, remote.stops);
    }

    @Test public void delayedManualEntryDoesNotConsumeAnotherScenesIntent() throws Exception {
        LiveConnectionTest.Memory store = pending();
        LiveConnectionTest.Remote remote = new LiveConnectionTest.Remote();
        LiveClient.Session original = store.session;
        store.session = new LiveClient.Session("http://other:8000", "two", "other", 20, false);
        assertFalse(LiveConnection.retryPendingStop(store, remote, () -> true, original));
        assertTrue(store.stopping); assertFalse(store.session.stopped); assertEquals(0, remote.starts);
        assertEquals(0, remote.stops);
    }

    @Test public void lateOldStopCannotClearNewSceneIntentOrCredentials() throws Exception {
        LiveConnectionTest.Memory store = pending();
        LiveClient.Session next = new LiveClient.Session("http://other:8000", "two", "other", 20, false);
        LiveConnectionTest.Remote remote = new LiveConnectionTest.Remote() {
            @Override public void stop(LiveClient.Session scene) throws Exception {
                super.stop(scene); store.session = next; store.creating = true; store.stopping = true;
            }
        };
        assertTrue(LiveConnection.retryPendingStop(store, remote, () -> true));
        assertSame(next, store.session); assertTrue(store.stopping); assertTrue(store.creating);
        assertEquals(LiveConnectionTest.SESSION.id, remote.stoppedId);
    }

    @Test public void sameIdOnDifferentServerOrCredentialIsStillAnotherScene() throws Exception {
        for (LiveClient.Session next : new LiveClient.Session[]{
                new LiveClient.Session("http://other:8000", "one", "secret", 20, false),
                new LiveClient.Session("http://original:8000", "one", "other", 20, false)}) {
            LiveConnectionTest.Memory store = pending();
            LiveConnectionTest.Remote remote = new LiveConnectionTest.Remote() {
                @Override public void stop(LiveClient.Session scene) throws Exception {
                    super.stop(scene); store.session = next;
                }
            };
            assertTrue(LiveConnection.retryPendingStop(store, remote, () -> true));
            assertSame(next, store.session); assertTrue(store.stopping);
        }
    }
}
