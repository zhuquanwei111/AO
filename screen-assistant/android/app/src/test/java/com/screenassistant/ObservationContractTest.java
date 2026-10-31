package com.screenassistant;

import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class ObservationContractTest {
    @Test public void capabilityRequiresExplicitBooleanAndRestoredSessionFallsBack() throws Exception {
        JSONObject base = new JSONObject().put("session_id", "s").put("session_token", "t").put("started_at_ms", 1);
        assertFalse(LiveClient.sessionFromCreate("http://local", base).batchMetadata);
        JSONObject capabilities = new JSONObject().put("observation_batch_metadata", true);
        base.put("capabilities", capabilities);
        assertFalse(LiveClient.sessionFromCreate("http://local", base).batchMetadata);
        capabilities.remove("observation_batch_metadata"); capabilities.put("typed_prompts", true);
        assertFalse(LiveClient.sessionFromCreate("http://local", base).batchMetadata);
        for (Object invalid : new Object[]{JSONObject.NULL, "true", 1, false}) {
            capabilities.put("observation_batch_metadata", invalid).put("typed_prompts", true);
            assertFalse(LiveClient.sessionFromCreate("http://local", base).batchMetadata);
            capabilities.put("observation_batch_metadata", true).put("typed_prompts", invalid);
            assertFalse(LiveClient.sessionFromCreate("http://local", base).batchMetadata);
        }
        capabilities.put("observation_batch_metadata", true).put("typed_prompts", true);
        LiveClient.Session s = LiveClient.sessionFromCreate("http://local", base);
        assertTrue(s.batchMetadata);
        assertFalse(LiveClient.Session.from(s.json()).batchMetadata);
        assertTrue(s.asStopped().batchMetadata);
    }

    @Test public void frozenCountCoversOnlyAcceptedCommentsAndKeepsOriginalLineHoles() throws Exception {
        CollectedRows<JSONObject> rows = new CollectedRows<>(3);
        JSONObject position = new JSONObject();
        LiveFrameAnalyzer.collectComment(rows, 2, "甲", 100, position, .9);
        LiveFrameAnalyzer.collectComment(rows, 7, "乙", 100, position, .3);
        rows.add(new JSONObject().put("kind", "online_count"));
        LiveFrameAnalyzer.collectComment(rows, 12, "丢弃", 100, position, .9);
        LiveFrameAnalyzer.freezeObservationMetadata(rows, true);
        assertEquals(2, rows.rows.get(0).value.getInt("capture_comment_count"));
        assertEquals(2, rows.rows.get(1).value.getInt("capture_comment_count"));
        assertEquals(2, rows.rows.get(0).value.getInt("line_index"));
        assertEquals(7, rows.rows.get(1).value.getInt("line_index"));
        for (CollectedRows.Row<JSONObject> row : rows.rows) assertEquals(rows.batchId, row.value.getString("capture_batch_id"));
        assertFalse(rows.rows.get(2).value.has("capture_comment_count"));
        assertFalse(rows.rows.get(2).value.has("line_index"));
    }

    @Test public void emptyCommentBatchDoesNotInventCountOrEvent() throws Exception {
        CollectedRows<JSONObject> rows = new CollectedRows<>(2);
        rows.add(new JSONObject().put("kind", "online_count"));
        LiveFrameAnalyzer.freezeObservationMetadata(rows, true);
        assertEquals(1, rows.rows.size());
        assertEquals(rows.batchId, rows.rows.get(0).value.getString("capture_batch_id"));
        assertFalse(rows.rows.get(0).value.has("capture_comment_count"));
    }

    @Test public void oldCapabilityDoesNotMutateOldDto() throws Exception {
        CollectedRows<JSONObject> rows = new CollectedRows<>(2);
        LiveFrameAnalyzer.collectComment(rows, 4, "甲", 100, new JSONObject(), .9);
        String original = rows.rows.get(0).value.toString();
        LiveFrameAnalyzer.freezeObservationMetadata(rows, false);
        assertEquals(original, rows.rows.get(0).value.toString());
        assertEquals("评论回复", DisplayText.promptLabel(new JSONObject().put("prompt_type", "comment_reply")));
        assertEquals("运营提醒", DisplayText.promptLabel(new JSONObject().put("prompt_type", "operation_notice")));
        assertEquals("提示", DisplayText.promptLabel(new JSONObject().put("source", "comment")));
        assertEquals("提示", DisplayText.promptLabel(new JSONObject().put("prompt_type", "future")));
    }
}
