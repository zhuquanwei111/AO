package com.screenassistant;

import org.junit.Test;
import static org.junit.Assert.*;

public class OverlayWindowGeometryTest {
    @Test public void overlappingWindowsStayWithinSystemOpacityLimit() {
        for (float maximum : new float[]{0f, .2f, .8f, 1f}) {
            float handle = OverlayWindowGeometry.handleAlpha(maximum);
            float body = OverlayWindowGeometry.bodyAlpha(maximum);
            assertTrue(handle >= 0 && body >= 0);
            assertTrue(1d - (1d - handle) * (1d - body) <= maximum + .000001d);
        }
    }
    @Test public void bodyFollowsHandleWithoutOverlapAndFitsScreen() {
        int[] layout = OverlayWindowGeometry.clamp(500, 900, 432, 960, 48, 240, 90);
        assertArrayEquals(new int[]{144, 870, 192}, layout);
        assertEquals(layout[0] + 48, layout[2]);
        assertArrayEquals(new int[]{0, 0, 48}, OverlayWindowGeometry.clamp(-5, -1, 432, 960, 48, 0, 48));
    }
}
