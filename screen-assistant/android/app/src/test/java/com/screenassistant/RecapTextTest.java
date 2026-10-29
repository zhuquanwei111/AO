package com.screenassistant;

import org.json.JSONObject;
import org.json.JSONArray;
import org.junit.Test;
import static org.junit.Assert.*;

public class RecapTextTest {
    @Test public void partialKeepsNullApproximationAndBusinessCaptureTime() throws Exception {
        JSONObject recap=new JSONObject().put("status","partial").put("douyin_account","example")
                .put("metrics",new JSONObject().put("累计观众",12000).put("新增粉丝",JSONObject.NULL)
                        .put("评论人数",JSONObject.NULL).put("送礼人数",0).put("获得音浪",32000))
                .put("raw_displays",new JSONObject().put("累计观众","约1.2万"))
                .put("approximate_metrics",new JSONArray().put("累计观众"))
                .put("missing_fields",new JSONObject().put("新增粉丝","页面未读到"))
                .put("metrics_captured_at","2026-10-27T13:00:08+08:00").put("received_at_ms",1793077210500L);
        String text=RecapText.backstage(recap);
        assertTrue(text.contains("累计观众：约 12000（页面：约1.2万）"));
        assertTrue(text.contains("新增粉丝：未取得（页面未读到）"));
        assertTrue(text.contains("送礼人数：0"));
        assertTrue(text.contains("采集时间：2026-10-27T13:00:08+08:00"));
        assertFalse(text.contains("1793077210500"));
        assertTrue(RecapText.terminal(new JSONObject().put("stopped_at_ms",123).put("backstage_recap",recap)));
    }
    @Test public void notConnectedIsUnavailableNotPendingAndDoesNotShowInventedZero() throws Exception {
        JSONObject data=new JSONObject().put("stopped_at_ms",123).put("backstage_recap",
                new JSONObject().put("status","not_connected").put("metrics",JSONObject.NULL));
        assertFalse(RecapText.pending(data)); assertTrue(RecapText.terminal(data));
        assertTrue(RecapText.backstage(data.getJSONObject("backstage_recap")).contains("本场后台复盘不可取数"));
        assertTrue(RecapText.backstage(data.getJSONObject("backstage_recap")).contains("不自动补领"));
        assertTrue(RecapText.backstage(data.getJSONObject("backstage_recap")).contains("新增粉丝：未取得"));
        assertFalse(RecapText.backstage(data.getJSONObject("backstage_recap")).contains("新增粉丝：0"));
        data.put("stopped_at_ms",JSONObject.NULL);
        assertFalse(RecapText.pending(data)); assertFalse(RecapText.terminal(data));
    }

    @Test public void activeWorkWaitsAndFailedOrCapturedRemainTerminal() throws Exception {
        for (String status : new String[]{"waiting_for_stop", "capturing"}) {
            JSONObject data=new JSONObject().put("stopped_at_ms",123)
                    .put("backstage_recap",new JSONObject().put("status",status));
            assertTrue(status,RecapText.pending(data)); assertFalse(status,RecapText.terminal(data));
        }
        for (String status : new String[]{"failed", "captured_locally", "partial"}) {
            JSONObject data=new JSONObject().put("stopped_at_ms",123)
                    .put("backstage_recap",new JSONObject().put("status",status));
            assertFalse(status,RecapText.pending(data)); assertTrue(status,RecapText.terminal(data));
        }
        assertTrue(RecapText.backstage(new JSONObject().put("status","failed")).contains("取数失败"));
        assertTrue(RecapText.backstage(new JSONObject().put("status","captured_locally")).contains("已读取五项"));
        assertTrue(RecapText.backstage(new JSONObject().put("status","waiting_for_stop"))
                .contains("任务已登记，取数状态待确认"));
        assertTrue(RecapText.backstage(new JSONObject().put("status","capturing"))
                .contains("取数任务已领取，等待本场结果"));
    }
}
