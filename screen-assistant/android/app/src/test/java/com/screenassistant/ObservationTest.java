package com.screenassistant;

import org.junit.Test;
import static org.junit.Assert.*;

public class ObservationTest {
    @Test public void normalizedRegionRejectsNaNAndMapsSameFrameCoordinates() throws Exception {
        assertFalse(Regions.valid(new float[]{Float.NaN,0,1,1}));
        assertFalse(Regions.valid(new float[]{-.1f,0,1,1}));
        assertArrayEquals(new int[]{80,10,90,20}, Regions.crop(new float[]{.8f,.05f,.9f,.1f},100,200));
        assertEquals(.8, Regions.position(80,10,90,20,100,200).getDouble("x"),.00001);
        assertEquals(.05, Regions.position(80,10,90,20,100,200).getDouble("y"),.00001);
    }

    @Test public void activeCaptureRegionsOnlyIncludeCommentAndOnlineCount() {
        assertArrayEquals(new String[]{"comment", "online_count"}, Regions.ACTIVE_KEYS);
        assertEquals(Regions.ACTIVE_KEYS.length, Regions.ACTIVE_LABELS.length);
    }
}
