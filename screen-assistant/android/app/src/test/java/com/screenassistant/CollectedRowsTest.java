package com.screenassistant;

import org.junit.Test;
import static org.junit.Assert.*;
import org.json.JSONObject;
import java.util.HashSet;

public class CollectedRowsTest {
    @Test public void keepsMoreThanTenAndIdenticalLines() {
        CollectedRows<String> batch = new CollectedRows<>(20);
        for (int i=0; i<15; i++) batch.add("同一句");
        assertEquals(15, batch.rows.size());
        assertEquals(14, batch.rows.get(14).index);
        assertEquals(0, batch.dropped);
    }
    @Test public void reportsCapacityLossAndDistinctBatchIdentity() {
        CollectedRows<String> batch = new CollectedRows<>(2);
        batch.add("甲"); batch.add("乙"); batch.add("丙");
        assertEquals(2, batch.rows.size()); assertEquals(1, batch.dropped);
        assertNotEquals(batch.batchId, new CollectedRows<String>(2).batchId);
    }
    @Test public void commentPathKeepsEveryLineWithDistinctEventAndOriginalMetadata() throws Exception {
        CollectedRows<JSONObject> batch=new CollectedRows<>(128);
        JSONObject position=new JSONObject().put("x",.1).put("y",.2).put("width",.3).put("height",.04);
        for (int i=0;i<25;i++) assertTrue(LiveFrameAnalyzer.collectComment(batch,"相同文字",1234,position,.8));
        HashSet<String> ids=new HashSet<>();
        for (CollectedRows.Row<JSONObject> row:batch.rows) {
            assertTrue(ids.add(row.value.getString("event_id")));
            assertEquals(1234,row.value.getLong("observed_at_ms"));
            assertEquals("相同文字",row.value.getJSONObject("content").getString("text"));
            assertEquals(.8,row.value.getDouble("confidence"),.001);
            assertEquals(.1,row.value.getJSONObject("content").getJSONObject("text_position").getDouble("x"),.001);
        }
        assertEquals(25,batch.rows.size());
    }
}
