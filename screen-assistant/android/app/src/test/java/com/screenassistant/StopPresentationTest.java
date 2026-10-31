package com.screenassistant;

import org.junit.Test;
import static org.junit.Assert.*;

public class StopPresentationTest {
    @Test public void distinguishesConfirmationFailureAndConfirmedStop() {
        assertEquals("本机采集已停止，正在确认场次结束。", RecapText.stopStatus(true, false, true, true));
        assertEquals("本机采集已停止，场次结束尚未确认。请返回设置重试结束本场。", RecapText.stopStatus(true, false, true, false));
        assertEquals("本场已结束。", RecapText.stopStatus(true, true, false, false));
        assertEquals("", RecapText.stopStatus(true, false, false, false));
        assertEquals("", RecapText.stopStatus(false, false, false, false));
        assertEquals("场次结束尚未确认，本机未保存场次凭证，请核对服务端状态。", RecapText.stopStatus(false, false, true, false));
    }
}
