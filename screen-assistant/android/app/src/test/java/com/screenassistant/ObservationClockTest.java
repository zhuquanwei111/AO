package com.screenassistant;

import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class ObservationClockTest {
    private PromptTiming timing(long server, long processing, long sent, long received) throws Exception {
        return PromptTiming.from(new JSONObject().put("server_time_ms",server).put("server_processing_ms",processing)
                .put(PromptTiming.SENT_NS,sent).put(PromptTiming.RECEIVED_NS,received));
    }

    @Test public void mapsSamplingTimeWithoutDeviceWallClockAndRetainsFrozenValue() throws Exception {
        ObservationClock first=ObservationClock.from("scene",1000,timing(1100,0,1_000_000_000L,1_100_000_000L));
        assertNotNull(first);
        assertEquals(Long.valueOf(1100),first.observedAt("scene",1_100_000_001L));
        Long frozen=first.observedAt("scene",1_601_999_999L);
        assertEquals(Long.valueOf(1601),frozen);
        ObservationClock next=ObservationClock.from("scene",1000,timing(2000,0,2_000_000_000L,2_010_000_000L));
        assertEquals(Long.valueOf(1601),frozen);
        assertEquals(Long.valueOf(2010),next.observedAt("scene",2_020_000_000L));
    }

    @Test public void rejectsMissingStaleCrossScenePreAnchorAndUnsafeTransport() throws Exception {
        assertNull(ObservationClock.from("scene",1000,null));
        ObservationClock anchor=ObservationClock.from("scene",1000,timing(1000,0,1_000_000_000L,1_100_000_000L));
        assertNull(anchor.observedAt("other",1_200_000_000L));
        assertNull(anchor.observedAt("scene",1_099_999_999L));
        assertNull(anchor.observedAt("scene",31_100_000_001L));
        assertNull(ObservationClock.from("scene",1000,timing(1000,0,0,1_001_000_000L)));
        assertNull(ObservationClock.from("scene",1001,timing(1000,0,0,0)));
    }

    @Test public void refusesOverflowAndRetainsStrictTimingValidation() throws Exception {
        ObservationClock anchor=ObservationClock.from("scene",1,timing(Long.MAX_VALUE,0,0,0));
        assertNull(anchor.observedAt("scene",1_000_000L));
        assertNull(ObservationClock.from("scene",1,timing(1000,2,0,1_000_000L)));
    }

    @Test public void frozenProductionObservationIsUnchangedAfterAnchorRefresh() throws Exception {
        ObservationClock first=ObservationClock.from("scene",1000,timing(1100,0,1_000_000_000L,1_100_000_000L));
        long sampled=first.observedAt("scene",1_600_000_000L);
        CollectedRows<JSONObject> rows=new CollectedRows<>(4);
        assertTrue(LiveFrameAnalyzer.collectComment(rows,3,"测试文字",sampled,new JSONObject(),.8));
        LiveFrameAnalyzer.freezeObservationMetadata(rows,true);
        String before=rows.rows.get(0).value.toString();
        ObservationClock next=ObservationClock.from("scene",1000,timing(5000,0,2_000_000_000L,2_000_000_000L));
        assertEquals(Long.valueOf(5100),next.observedAt("scene",2_100_000_000L));
        assertEquals(before,rows.rows.get(0).value.toString());
        assertEquals(1600,rows.rows.get(0).value.getLong("observed_at_ms"));
    }
}
