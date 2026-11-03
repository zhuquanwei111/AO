package com.screenassistant;

import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class CaptureLedgerTest {
    @Test public void preservesIdentityAndResultWithinBoundWithoutStoringText() throws Exception {
        CaptureLedger ledger=new CaptureLedger(2);
        CollectedRows<JSONObject> first=new CollectedRows<>(3);
        first.addAt(2,new JSONObject().put("event_id","a").put("kind","comment")
                .put("observed_at_ms",123).put("text","不写入账本"));
        ledger.register(first); ledger.outcome("a","http_success");
        CaptureLedger.Entry entry=ledger.snapshot().get(0);
        assertEquals(first.batchId,entry.batchId); assertEquals(2,entry.lineIndex);
        assertEquals("comment",entry.kind); assertEquals(123,entry.observedAt);
        assertEquals("http_success",entry.outcome);
        CollectedRows<JSONObject> second=new CollectedRows<>(1);
        second.add(new JSONObject().put("event_id","b")); ledger.register(second);
        CollectedRows<JSONObject> third=new CollectedRows<>(1);
        third.add(new JSONObject().put("event_id","c")); ledger.register(third);
        assertEquals(2,ledger.snapshot().size()); assertEquals("b",ledger.snapshot().get(0).eventId);
        ledger.clear(); assertTrue(ledger.snapshot().isEmpty());
    }
}
