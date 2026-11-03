package com.screenassistant;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertNull;

import org.junit.Test;

public class PaddleOcrGeometryTest {
    @Test public void quadCoordinatesBecomeClippedAbsoluteScreenBounds() {
        assertArrayEquals(new int[]{110,220,150,262}, PaddleOcrGeometry.absoluteBounds(
                new float[]{2.2f,3.1f,42f,3f,41.5f,44.8f,2f,45f},
                108,217,160,270));
    }

    @Test public void quadOutsideRoiIsRejected() {
        assertNull(PaddleOcrGeometry.absoluteBounds(
                new float[]{-30,-30,-20,-30,-20,-20,-30,-20},
                10,10,100,100));
    }

    @Test public void invalidQuadIsRejected() {
        assertNull(PaddleOcrGeometry.absoluteBounds(new float[]{0,0,1,1},0,0,10,10));
        assertNull(PaddleOcrGeometry.absoluteBounds(
                new float[]{0,0,1,0,Float.NaN,1,0,1},0,0,10,10));
    }
}
