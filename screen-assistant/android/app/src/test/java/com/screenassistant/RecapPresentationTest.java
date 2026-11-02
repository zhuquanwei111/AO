package com.screenassistant;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;
import java.util.Locale;
import java.util.TimeZone;
import java.nio.charset.StandardCharsets;
import java.io.InputStream;
import static org.junit.Assert.*;

public class RecapPresentationTest {
    private JSONObject fixture(String name) throws Exception {
        try (InputStream in = getClass().getResourceAsStream("/backstage-summary-fixtures.json")) {
            assertNotNull(in);
            JSONArray cases = new JSONArray(new String(in.readAllBytes(), StandardCharsets.UTF_8));
            for (int i = 0; i < cases.length(); i++) {
                JSONObject item = cases.getJSONObject(i);
                if (name.equals(item.getString("case"))) return item.getJSONObject("summary");
            }
        }
        throw new AssertionError(name);
    }

    @Test public void backstageUsesActualRawDisplaysAndShowsMissingFields() throws Exception {
        String partial = RecapPresentation.summary(fixture("partial"));
        assertTrue(partial.contains("累计观众：约1.2万"));
        assertTrue(partial.contains("获得音浪：3.2万"));
        assertTrue(partial.contains("评论人数：暂无数据"));
        assertFalse(partial.contains("约 约"));
        assertFalse(partial.contains("页面未读到"));
        String complete = RecapPresentation.summary(fixture("captured_locally"));
        assertTrue(complete.contains("新增粉丝：2"));
        assertFalse(complete.contains("部分数据暂不可用"));
        String missing = RecapPresentation.summary(fixture("needs_input"));
        for (String name : new String[]{"累计观众", "新增粉丝", "评论人数", "送礼人数", "获得音浪"})
            assertTrue(missing.contains(name + "：暂无数据"));
        assertTrue(missing.contains("部分数据暂不可用"));
        String absent = RecapPresentation.summary(new JSONObject());
        assertTrue(absent.contains("累计观众：暂无数据"));
        assertTrue(absent.contains("获得音浪：暂无数据"));
        assertTrue(absent.contains("平台数据暂不可用"));
    }

    @Test public void backstageLegacyZeroAndInvalidRawUseNumericFallback() throws Exception {
        JSONObject data = fixture("partial"), backstage = data.getJSONObject("backstage_recap");
        backstage.getJSONObject("metrics").put("新增粉丝", 0);
        backstage.remove("raw_displays");
        String legacy = RecapPresentation.summary(data);
        assertTrue(legacy.contains("新增粉丝：0"));
        assertTrue(legacy.contains("累计观众：约 12000"));
        backstage.put("raw_displays", new JSONObject().put("累计观众", " ")
                .put("新增粉丝", new JSONObject().put("invalid", true))
                .put("送礼人数", "**").put("获得音浪", "3..2万"));
        String invalid = RecapPresentation.summary(data);
        assertTrue(invalid.contains("累计观众：约 12000"));
        assertTrue(invalid.contains("新增粉丝：0"));
        assertTrue(invalid.contains("送礼人数：4"));
        assertTrue(invalid.contains("获得音浪：约 32000"));
        assertFalse(invalid.contains("invalid"));
    }
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
