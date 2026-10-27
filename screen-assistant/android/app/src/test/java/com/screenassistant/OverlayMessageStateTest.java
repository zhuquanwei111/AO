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

    @Test public void expiredSourceCannotReplaceCurrentMessage() {
        OverlayMessageState state = new OverlayMessageState();
        state.start();
        state.update("运营\n当前建议", 0, 1000, 6000);
        assertFalse(state.update("运营\n过期建议", 900, 1100, 6000));
        assertEquals("运营\n当前建议", state.textAt(1100));
    }

    @Test public void offlineStatusPersistsWithoutBackendOrPermissionBypass() {
        OverlayMessageState state = new OverlayMessageState();
        state.start("正在连接服务");
        assertTrue(state.shouldDisplay(true, false));
        assertFalse(state.shouldDisplay(false, false));
        assertFalse(state.shouldDisplay(true, true));
        state.update("运营\n已收到", 0, 1000, 6000);
        state.setStatus("服务未连接，暂无新提示", true);
        assertEquals("服务未连接，暂无新提示", state.textAt(100000));
        state.setStatus("实时已连接，等待内容", false);
        assertEquals("实时已连接，等待内容", state.textAt(100001));
        state.stop();
        assertFalse(state.shouldDisplay(true, false));
    }

    @Test public void purposeSwitchReplacesWholeTextAndNormalizedDuplicateDoesNotRefresh() {
        OverlayMessageState state = new OverlayMessageState(); state.start();
        state.update(DisplayText.labeled("问答", "旧内容"), 0, 1000, 6000);
        state.update(DisplayText.labeled("运营", "**新建议**"), 0, 1100, 6000);
        assertEquals("运营\n新建议", state.textAt(1100));
        assertFalse(state.update(DisplayText.labeled("运营", "# 新建议"), 0, 1200, 6000));
        assertEquals(7100, state.expiresAtMs());
        assertEquals(OverlayMessageState.WAITING_TEXT, state.textAt(7100));
    }
}
