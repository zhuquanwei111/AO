package com.screenassistant;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;
import java.util.Locale;
import java.util.TimeZone;
import static org.junit.Assert.*;

public class RecapPresentationTest {
    @Test public void absentCountDiffersFromRealZero() throws Exception {
        JSONObject data = new JSONObject().put("online_count", new JSONObject().put("status", "unknown")
                .put("peak", JSONObject.NULL).put("coverage", new JSONObject().put("sample_issues", "source_missing")));
        String missing = RecapPresentation.summary(data);
        assertTrue(missing.contains("暂无可用的在线人数数据"));
        assertFalse(missing.contains("source_missing"));
        data.put("online_count", new JSONObject().put("status", "partial")
                .put("peak", new JSONObject().put("nominal_value", 0)));
        String zero = RecapPresentation.summary(data);
        assertTrue(zero.contains("参考人数峰值：0"));
        assertFalse(zero.contains("暂无可用的在线人数数据"));
    }

    @Test public void eventsAreRecordsNotClaimsOfReading() throws Exception {
        JSONArray rows = new JSONArray().put(new JSONObject().put("prompt_type", "comment_reply")
                .put("start_offset_ms", 12641).put("suggestion", "你好").put("delivery_state", "returned_to_client"))
                .put(new JSONObject().put("prompt_type", "operation_notice").put("suggestion", "继续话题"))
                .put(new JSONObject().put("prompt_type", "future").put("suggestion", "建议内容"));
        String text = RecapPresentation.events(rows);
        assertTrue(text.contains("评论回复 · 开场后 12秒"));
        assertTrue(text.contains("运营建议"));
        assertTrue(text.contains("\n\n建议\n建议内容"));
        assertFalse(text.contains("已读")); assertFalse(text.contains("returned_to_client"));
        assertTrue(RecapPresentation.events(new JSONArray()).contains("暂无建议记录"));
    }

    @Test public void crossDayUsesLocalDateAndDuration() throws Exception {
        TimeZone original = TimeZone.getDefault(); Locale originalLocale = Locale.getDefault();
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("Asia/Shanghai")); Locale.setDefault(Locale.CHINA);
            JSONObject data = new JSONObject().put("started_at_ms", 1793548740000L)
                    .put("stopped_at_ms", 1793548860000L);
            String text = RecapPresentation.summary(data);
            assertTrue(text.contains("时长：2分0秒"));
            assertTrue(text.contains("11/1") || text.contains("11-1") || text.contains("11月1"));
            assertTrue(text.contains("11/2") || text.contains("11-2") || text.contains("11月2"));
        } finally { TimeZone.setDefault(original); Locale.setDefault(originalLocale); }
    }
}
