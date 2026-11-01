package com.screenassistant;

import org.junit.Test;
import static org.junit.Assert.*;

public class RegionPlacementTest {
    @Test public void commentDefaultUsesFullWidthAndEighteenPercentAboveInput() {
        assertArrayEquals(new float[]{0f,.768f,1f,.948f}, Regions.commentDefault(.948f), .0001f);
        assertTrue(Regions.valid(Regions.onlineDefault()));
    }

    @Test public void overlayAvoidsBothTextRegionsAndPreservesRequestedPositionWhenSafe() {
        int[][] text = {{0,768,432,948},{324,40,410,100}};
        assertArrayEquals(new int[]{20,200}, Regions.placeOverlay(432,960,250,70,20,200,text));
        int[] moved=Regions.placeOverlay(432,960,250,70,100,800,text);
        assertNotNull(moved);
        assertFalse(Regions.overlaps(moved[0],moved[1],250,70,text[0]));
        assertFalse(Regions.overlaps(moved[0],moved[1],250,70,text[1]));
    }

    @Test public void noRoomIsExplicitRatherThanCroppingText() {
        assertNull(Regions.placeOverlay(100,100,80,40,0,0,new int[][]{{0,0,100,100}}));
    }
}
