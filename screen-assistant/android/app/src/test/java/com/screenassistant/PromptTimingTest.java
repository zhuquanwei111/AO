package com.screenassistant;

import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class PromptTimingTest {
    private JSONObject page(long server, Object processing, long sentNs, long receivedNs) throws Exception {
        return new JSONObject().put("server_time_ms", server).put("server_processing_ms", processing)
                .put(PromptTiming.SENT_NS, sentNs).put(PromptTiming.RECEIVED_NS, receivedNs);
    }
    private JSONObject expiry(Object value) throws Exception { return new JSONObject().put("expires_at_ms", value); }

    @Test public void frozenLongPollExamplesSubtractProcessingAndOneMsMargin() throws Exception {
        long server = 1_790_000_000_000L;
        PromptTiming timing = PromptTiming.from(page(server, 15000, 1_000_000_000L, 16_200_000_000L));
        assertNotNull(timing);
        assertEquals(15200, timing.rttMs);
        assertEquals(200, timing.transportMs);
        assertEquals(14799, timing.deadline(expiry(server+15000))-timing.receivedMs);
        PromptTiming slow = PromptTiming.from(page(server, 15000, 1_000_000_000L, 32_000_000_000L));
        assertEquals(0, slow.deadline(expiry(server+15000)));
    }

    @Test public void nanosecondRttRoundsUpRatherThanGrantingExtraLifetime() throws Exception {
        PromptTiming timing = PromptTiming.from(page(1000, 0, 1_000_000_000L, 1_000_000_001L));
        assertEquals(1, timing.rttMs);
        assertEquals(14998, timing.deadline(expiry(16000))-timing.receivedMs);
    }

    @Test public void deviceAndServerWallClockOffsetsDoNotAffectRelativeLifetime() throws Exception {
        for (long server : new long[]{1, 1_700_000_000_000L, 1_900_000_000_000L}) {
            PromptTiming timing = PromptTiming.from(page(server, 0, 1_000_000_000L, 1_010_000_000L));
            assertEquals(15999, timing.deadline(expiry(server+15000)));
        }
    }

    @Test public void missingClockAndDurationNeverInventTimeAnchor() throws Exception {
        JSONObject complete = page(1000, 0, 0, 0);
        for (String key : new String[]{"server_time_ms", "server_processing_ms", PromptTiming.SENT_NS, PromptTiming.RECEIVED_NS}) {
            JSONObject partial = new JSONObject(complete.toString()); partial.remove(key);
            assertNull(PromptTiming.from(partial));
        }
    }

    @Test public void rawJsonFloatBooleanStringAndOverflowAreNotIntegers() throws Exception {
        for (String value : new String[]{"1000.0", "1e3", "true", "\"1000\"", "null", "9223372036854775808"}) {
            JSONObject raw = new JSONObject("{\"server_time_ms\":"+value+",\"server_processing_ms\":0}")
                    .put(PromptTiming.SENT_NS, 0).put(PromptTiming.RECEIVED_NS, 0);
            assertNull(value, PromptTiming.from(raw));
        }
        for (Object value : new Object[]{0.0, true, "0", JSONObject.NULL}) {
            assertNull(PromptTiming.from(page(1000, value, 0, 0)));
        }
    }

    @Test public void negativeOrImpossibleDurationsAreRejected() throws Exception {
        assertNull(PromptTiming.from(page(1000, -1, 0, 1000000)));
        assertNull(PromptTiming.from(page(1000, 2, 0, 1000000)));
        assertNull(PromptTiming.from(page(0, 0, 0, 0)));
        assertNull(PromptTiming.from(page(1000, 0, -1, 0)));
        assertNull(PromptTiming.from(page(1000, 0, 1000000, 0)));
    }

    @Test public void malformedSourceExpiryIsDifferentFromAlreadyExpired() throws Exception {
        PromptTiming timing = PromptTiming.from(page(1000, 0, 0, 0));
        for (Object value : new Object[]{16000.0, true, "16000", JSONObject.NULL, -1, 0}) {
            assertEquals(-1, timing.deadline(expiry(value)));
        }
        assertEquals(-1, timing.deadline(new JSONObject()));
        assertEquals(0, timing.deadline(expiry(1000)));
        assertEquals(0, timing.deadline(expiry(999)));
    }

    @Test public void parserAndQueueDelayConsumeOriginalDeadline() throws Exception {
        PromptTiming timing = PromptTiming.from(page(1000, 0, 1_000_000_000L, 1_000_000_000L));
        long deadline = timing.deadline(expiry(4000));
        assertEquals(3999, deadline);
        OverlayMessageState state = new OverlayMessageState(); state.start();
        assertFalse(state.update("late", "迟到", 25, deadline, 4000, 6000));
        assertEquals("", state.textAt(4000));
    }

    @Test public void shortSourceLifeCannotBeExtendedToThreeOrFiveSeconds() throws Exception {
        PromptTiming timing = PromptTiming.from(page(1000, 0, 1_000_000_000L, 1_000_000_000L));
        OverlayMessageState state = new OverlayMessageState(); state.start();
        long deadline = timing.deadline(expiry(3000));
        assertTrue(state.update("short", "短源寿命", 25, deadline, 1000, 6000));
        assertEquals(2999, state.expiresAtMs());
        assertEquals(2999, state.protectedUntilMs());
        assertEquals("", state.textAt(2999));
    }

    @Test public void continuousTimeIncludingSleepExpiresCurrentAndQueuedItems() throws Exception {
        PromptTiming timing = PromptTiming.from(page(1000, 0, 1_000_000_000L, 1_000_000_000L));
        OverlayMessageState state = new OverlayMessageState(); state.start();
        state.update("A", "当前", 25, timing.deadline(expiry(16000)), 1000, 6000);
        state.update("B", "排队", 38, timing.deadline(expiry(7000)), 1887, 6000);
        // 代表休眠后连续时钟推进，而Handler的uptime尚未补足延时。
        assertEquals("", state.textAt(10000));
        assertEquals(1, state.expiredCount());
        assertEquals(0, state.pendingCount());
    }

    @Test public void conservativeDeadlineMustNotMakeDuplicateBecomeNewMessage() throws Exception {
        OverlayMessageState state = new OverlayMessageState(); state.start();
        PromptTiming first = PromptTiming.from(page(1000, 0, 0, 10_000_000_000L));
        long early = first.deadline(expiry(16000));
        assertTrue(state.update("A", "同一条", 25, early, first.receivedMs, 6000));
        assertEquals("", state.textAt(early));
        PromptTiming reread = PromptTiming.from(page(6000, 0, 15_000_000_000L, 15_000_000_000L));
        assertFalse(state.update("A", "同一条", 25, reread.deadline(expiry(16000)), 15000, 6000));
        assertTrue(state.update("B", "新的ID", 25, reread.deadline(expiry(16000)), 15000, 6000));
    }

    @Test public void localDeadlineOverflowIsRejected() throws Exception {
        PromptTiming timing = PromptTiming.from(page(1, 0, Long.MAX_VALUE, Long.MAX_VALUE));
        assertEquals(-1, timing.deadline(expiry(Long.MAX_VALUE)));
    }
}
