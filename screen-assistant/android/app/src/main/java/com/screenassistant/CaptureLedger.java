package com.screenassistant;

import org.json.JSONObject;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;

/** 最近若干采集轮的标识及发送结果，仅内存保存，不保留识别文字。 */
final class CaptureLedger {
    static final class Entry {
        final String batchId, eventId, kind;
        final int lineIndex;
        final long observedAt;
        String outcome="queued";
        Entry(String batchId,String eventId,int lineIndex,String kind,long observedAt) {
            this.batchId=batchId; this.eventId=eventId; this.lineIndex=lineIndex;
            this.kind=kind; this.observedAt=observedAt;
        }
    }
    private final int capacity;
    private final ArrayDeque<List<Entry>> batches=new ArrayDeque<>();
    CaptureLedger(int capacity) { this.capacity=capacity; }
    synchronized void register(CollectedRows<JSONObject> rows) {
        List<Entry> batch=new ArrayList<>();
        for (CollectedRows.Row<JSONObject> row:rows.rows)
            batch.add(new Entry(rows.batchId,row.value.optString("event_id"),row.index,
                    row.value.optString("kind"),row.value.optLong("observed_at_ms")));
        if (batches.size() == capacity) batches.removeFirst();
        batches.addLast(batch);
    }
    synchronized void outcome(String eventId,String value) {
        for (List<Entry> batch:batches) for (Entry entry:batch)
            if (entry.eventId.equals(eventId)) entry.outcome=value;
    }
    synchronized List<Entry> snapshot() {
        List<Entry> result=new ArrayList<>();
        for (List<Entry> batch:batches) result.addAll(batch);
        return result;
    }
    synchronized void clear() { batches.clear(); }
}
