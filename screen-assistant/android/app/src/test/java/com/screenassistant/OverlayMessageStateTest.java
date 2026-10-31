package com.screenassistant;

import org.junit.Test;
import static org.junit.Assert.*;

public class OverlayMessageStateTest {
    @Test public void reportsQueuedExpiryAndOverflowWithAffectedIds() {
        java.util.List<String> events = new java.util.ArrayList<>();
        OverlayMessageState state = new OverlayMessageState((id, reason) -> events.add(id + ":" + reason));
        state.start();
        assertTrue(state.update("A", "甲", 0, 30000, 1000, 12000));
        assertTrue(state.update("B", "乙", 0, 3000, 1100, 12000));
        assertTrue(events.contains("B:queued"));
        state.textAt(3000);
        assertTrue(events.contains("B:source_expired_queued"));
        for (int i = 0; i < 16; i++) assertTrue(state.update("Q" + i, "等" + i, 0, 30000, 3100, 12000));
        assertFalse(state.update("overflow", "满", 0, 30000, 3200, 12000));
        assertTrue(events.contains("overflow:queue_full"));
        assertTrue(state.update("urgent", "紧急", 100, 30000, 3300, 12000));
        assertTrue(events.contains("Q15:queue_evicted"));
    }
    private OverlayMessageState started() {
        OverlayMessageState state = new OverlayMessageState(); state.start(); return state;
    }
    private boolean offer(OverlayMessageState state, String id, int priority, long expiry, long now) {
        return state.update(id, "运营：" + id, priority, expiry, now, 12000);
    }

    @Test public void ordinaryArrivalAt887msMustKeepCurrentForEightSeconds() {
        OverlayMessageState state = started();
        state.update("运营：当前建议", 16000, 1000, 12000);
        state.update("运营：普通后到", 16887, 1887, 12000);
        assertEquals("运营：当前建议", state.textAt(1887));
        assertEquals("运营：当前建议", state.textAt(8999));
        assertEquals("运营：普通后到", state.textAt(9000));
    }

    @Test public void ordinaryWaitsEightSecondsAndIdleCollapsesAtTwelve() {
        OverlayMessageState state = started();
        assertTrue(state.update("A", "普通A", 25, 31000, 1000, 12000));
        assertTrue(state.update("B", "普通B", 38, 31000, 1887, 12000));
        assertEquals("普通A", state.textAt(5999));
        assertEquals("普通A", state.textAt(8999));
        assertEquals("普通B", state.textAt(9000));

        OverlayMessageState idle = started();
        assertTrue(idle.update("A", "普通A", 25, 31000, 1000, 12000));
        assertEquals("普通A", idle.textAt(12999));
        assertEquals("", idle.textAt(13000));
    }

    @Test public void longerProtectionCanExpireWaitingPromptWithoutRenewingIt() {
        OverlayMessageState state = started();
        assertTrue(state.update("A", "普通A", 25, 31000, 1000, 12000));
        assertTrue(state.update("B", "普通B", 38, 8000, 1887, 12000));
        assertEquals("普通A", state.textAt(6000));
        assertEquals("普通A", state.textAt(8000));
        assertEquals(1, state.expiredCount());
        assertEquals(0, state.pendingCount());
        assertEquals("", state.textAt(13000));
    }

    @Test public void idleAndExpiredMessagesMustNotShowConnectionOrUploadStatus() {
        OverlayMessageState state = started();
        assertEquals("", state.textAt(1000));
        state.update("运营：有效建议", 2000, 1000, 6000);
        state.setStatus("已上传，复盘等待中", false);
        assertEquals("运营：有效建议", state.textAt(1999));
        assertEquals("", state.textAt(2000));
        state.setStatus("实时已连接", false);
        assertEquals("", state.textAt(2001));
        assertTrue(state.shouldDisplay(true, false));
    }

    @Test public void twelveSecondLocalLimitStillAppliesWithoutPending() {
        OverlayMessageState state = started();
        offer(state, "A", 25, 16000, 1000);
        assertEquals("运营：A", state.textAt(12999));
        assertEquals("", state.textAt(13000));
        assertFalse(state.isMessageActive(13000));
    }

    @Test public void sourceExpiryWinsEvenDuringProtectionAndDuplicateDoesNotExtend() {
        OverlayMessageState state = started();
        assertTrue(offer(state, "A", 25, 2200, 1000));
        assertFalse(offer(state, "A", 100, 99999, 1100));
        offer(state, "B", 38, 16000, 1887);
        assertEquals("运营：A", state.textAt(2199));
        assertEquals("运营：B", state.textAt(2200));
        assertEquals(14200, state.expiresAtMs());
    }

    @Test public void expiredQueuedItemNeverAppearsAndDoesNotExtendCurrent() {
        OverlayMessageState state = started();
        offer(state, "A", 25, 16000, 1000);
        offer(state, "B", 38, 4000, 1887);
        assertEquals(4000, state.nextChangeAtMs(1887));
        assertEquals("运营：A", state.textAt(4000));
        assertEquals(1, state.expiredCount());
        assertEquals(0, state.pendingCount());
        assertEquals(13000, state.nextChangeAtMs(4000));
        assertEquals("", state.textAt(13000));
    }

    @Test public void queuedShortLifeUsesRemainingSourceTimeWithoutEightSecondExtension() {
        OverlayMessageState state = started();
        offer(state, "A", 25, 16000, 1000);
        offer(state, "B", 38, 9500, 1887);
        assertEquals("运营：B", state.textAt(9000));
        assertEquals(9500, state.sourceExpiryMs());
        assertEquals(9500, state.expiresAtMs());
        assertEquals(9500, state.protectedUntilMs());
        assertEquals("", state.textAt(9500));
    }

    @Test public void realUrgentPreemptsButPriority38AndTextDoNot() {
        OverlayMessageState state = started();
        offer(state, "A", 25, 16000, 1000);
        state.update("B", "紧急！规则模板消费等级100", 38, 16000, 1887, 12000);
        assertEquals("运营：A", state.textAt(1887));
        assertTrue(offer(state, "U", 100, 18000, 1900));
        assertEquals("运营：U", state.textAt(1900));
        offer(state, "C", 80, 18000, 2000);
        assertEquals("运营：U", state.textAt(7000));
        assertEquals("紧急！规则模板消费等级100", state.textAt(13900));
    }

    @Test public void idempotencyIncludesQueuedShownAndDiscardedIds() {
        OverlayMessageState state = started();
        offer(state, "A", 25, 30000, 1000);
        offer(state, "B", 38, 30000, 1887);
        assertFalse(offer(state, "B", 100, 40000, 2000));
        assertEquals(1, state.pendingCount());
        assertEquals(9000, state.nextChangeAtMs(2000));
        state.textAt(9000);
        assertFalse(offer(state, "A", 25, 30000, 9100));
        state.setStatus("断线", true);
        assertEquals("", state.textAt(9200));
        assertFalse(offer(state, "B", 38, 30000, 9200));
    }

    @Test public void boundedQueueReportsOverflowAndPreservesUrgentAdmission() {
        OverlayMessageState state = started();
        offer(state, "A", 25, 99999, 1000);
        for (int i=0; i<OverlayMessageState.CAPACITY; i++) assertTrue(offer(state, "Q"+i, 38, 99999, 1100));
        assertFalse(offer(state, "overflow", 38, 99999, 1200));
        assertEquals(OverlayMessageState.CAPACITY, state.pendingCount());
        assertEquals(1, state.overflowCount());
        assertTrue(offer(state, "U", 100, 99999, 1300));
        assertEquals("运营：U", state.textAt(1300));
        assertEquals(OverlayMessageState.CAPACITY-1, state.pendingCount());
        assertEquals(2, state.overflowCount());
    }

    @Test public void allUrgentQueueIsBoundedAndOrdinaryCannotEvictIt() {
        OverlayMessageState state = started();
        offer(state, "U", 100, 99999, 1000);
        for (int i=0; i<OverlayMessageState.CAPACITY; i++) offer(state, "U"+i, 100, 99999, 1100);
        assertFalse(offer(state, "extra", 100, 99999, 1200));
        assertEquals(OverlayMessageState.CAPACITY, state.pendingCount());
        assertEquals("运营：U", state.textAt(6000));
    }

    @Test public void stoppedAndNewSessionClearQueueAndIdempotency() {
        OverlayMessageState state = started();
        offer(state, "A", 25, 30000, 1000);
        offer(state, "B", 38, 30000, 1887);
        state.stop();
        assertEquals("", state.textAt(2000));
        assertFalse(offer(state, "late", 100, 30000, 2000));
        assertFalse(state.shouldDisplay(true, false));
        state.start("重新连接");
        assertEquals("", state.textAt(2100));
        assertEquals(0, state.pendingCount());
        assertTrue(offer(state, "A", 25, 30000, 2100));
        assertEquals("运营：A", state.textAt(2100));
    }

    @Test public void expiredSourceCannotReplaceCurrentAndPermissionsStillGateWindow() {
        OverlayMessageState state = started();
        offer(state, "A", 25, 16000, 1000);
        assertFalse(offer(state, "expired", 100, 900, 1100));
        assertEquals("运营：A", state.textAt(1100));
        assertFalse(state.shouldDisplay(false, false));
        assertFalse(state.shouldDisplay(true, true));
    }

    @Test public void normalizedLocalDuplicateDoesNotRefreshOrBypassProtection() {
        OverlayMessageState state = started();
        state.update(DisplayText.labeled("运营", "**新建议**"), 0, 1000, 12000);
        assertFalse(state.update(DisplayText.labeled("运营", "# 新建议"), 0, 1200, 12000));
        assertEquals(13000, state.expiresAtMs());
        assertEquals("", state.textAt(13000));
    }
}
