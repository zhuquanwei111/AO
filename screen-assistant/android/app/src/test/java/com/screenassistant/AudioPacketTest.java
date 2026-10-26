package com.screenassistant;

import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class AudioPacketTest {
    @Test public void pcmUsesExistingM6ShapeAndRealFrameClock() throws Exception {
        JSONObject p=AudioPackets.packet("capture-id","audioMic",3,19200,new byte[12800],20000,123456,false,true);
        assertEquals(16000,p.getInt("sample_rate"));
        assertFalse(p.getBoolean("host_confirmed"));
        assertEquals(6400,p.getJSONArray("spans").getJSONObject(0).getInt("frames"));
        assertEquals(19200,p.getJSONArray("spans").getJSONObject(0).getJSONObject("pts").getLong("value"));
        assertEquals(20000,p.getJSONObject("clock_anchor").getJSONObject("pts").getLong("value"));
        assertEquals("capture_gap",p.getJSONArray("gap_before").getString(0));
    }
    @Test public void finalHasNoInventedFramesAndMissingClockStaysUnknown() throws Exception {
        JSONObject p=AudioPackets.packet("capture-id","audioApp",4,25600,new byte[0],-1,0,false,false);
        assertTrue(p.getBoolean("final")); assertEquals(0,p.getJSONArray("spans").length());
        assertFalse(p.has("clock_anchor"));
    }
    @Test public void rejectsOversizedAndMisalignedPcm() throws Exception {
        for (int size:new int[]{19202,1}) {
            try { AudioPackets.packet("id","audioMic",0,0,new byte[size],-1,0,false,false); fail(); }
            catch (IllegalArgumentException expected) { }
        }
    }
}
