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
    @Test public void notConnectedRemainsPendingAndDoesNotShowInventedZero() throws Exception {
        JSONObject data=new JSONObject().put("stopped_at_ms",123).put("backstage_recap",
                new JSONObject().put("status","not_connected").put("metrics",JSONObject.NULL));
        assertTrue(RecapText.pending(data)); assertFalse(RecapText.terminal(data));
        assertTrue(RecapText.backstage(data.getJSONObject("backstage_recap")).contains("新增粉丝：未取得"));
        assertFalse(RecapText.backstage(data.getJSONObject("backstage_recap")).contains("新增粉丝：0"));
    }
}
