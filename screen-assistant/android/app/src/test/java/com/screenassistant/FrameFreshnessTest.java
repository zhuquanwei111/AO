package com.screenassistant;

import org.junit.Test;
import static org.junit.Assert.*;

public class FrameFreshnessTest {
    @Test public void expiryAndPauseBoundariesRejectStaleFrames() {
        assertTrue(FrameFreshness.valid(true,1000,1000,7499));
        assertFalse(FrameFreshness.valid(true,1000,1000,7500));
        assertFalse(FrameFreshness.valid(true,1000,1001,1100));
        assertFalse(FrameFreshness.valid(false,1000,0,1100));
        assertFalse(FrameFreshness.valid(true,1000,0,999));
    }
}
