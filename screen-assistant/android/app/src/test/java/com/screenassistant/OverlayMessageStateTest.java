package com.screenassistant;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class OverlayMessageStateTest {
    @Test
    public void runningShowsWaitingThenWholeMessageAndReturnsToWaitingOnExpiry() {
        OverlayMessageState state = new OverlayMessageState();
        state.start();

        assertEquals("辅助中，等待内容", state.textAt(1000));
        assertTrue(state.update("运营：整条提示", 0, 1000, 6000));
        assertEquals("运营：整条提示", state.textAt(6999));
        assertEquals("辅助中，等待内容", state.textAt(7000));
        assertFalse(state.isMessageActive(7000));
    }

    @Test
    public void sourceExpiryWinsAndDuplicateDoesNotExtendMessage() {
        OverlayMessageState state = new OverlayMessageState();
        state.start();

        assertTrue(state.update("问答：结果", 1200, 1000, 6000));
        assertFalse(state.update("问答：结果", 0, 1100, 6000));
        assertEquals("辅助中，等待内容", state.textAt(1200));
    }

    @Test
    public void stoppedStateNeverShowsWaitingOrMessage() {
        OverlayMessageState state = new OverlayMessageState();
        state.start();
        state.update("问答：结果", 0, 1000, 6000);
        state.stop();

        assertEquals("", state.textAt(2000));
        assertFalse(state.update("运营：迟到", 0, 2000, 6000));
    }
}
