package com.screenassistant;

import org.junit.Test;
import static org.junit.Assert.*;

public class ObservationTest {
    @Test public void sdkPixelsMapToExistingIosNormalizedNoseShoulderGeometry() {
        double[] portrait=PersonGeometry.body(new double[]{50,60,30,100,70,100},100,200);
        double[] landscape=PersonGeometry.body(new double[]{100,30,60,50,140,50},200,100);
        assertNotNull(portrait); assertArrayEquals(portrait,landscape,.00001);
        assertEquals(.5,portrait[1],.00001);
        assertNull(PersonGeometry.body(new double[]{50,60,49,100,51,100},100,200));
        assertNull(PersonGeometry.body(new double[]{Double.NaN,60,30,100,70,100},100,200));
    }
    @Test public void normalizedRegionRejectsNaNAndMapsSameFrameCoordinates() throws Exception {
        assertFalse(Regions.valid(new float[]{Float.NaN,0,1,1}));
        assertFalse(Regions.valid(new float[]{-.1f,0,1,1}));
        assertArrayEquals(new int[]{80,10,90,20}, Regions.crop(new float[]{.8f,.05f,.9f,.1f},100,200));
        assertEquals(.8, Regions.position(80,10,90,20,100,200).getDouble("x"),.00001);
        assertEquals(.05, Regions.position(80,10,90,20,100,200).getDouble("y"),.00001);
    }

    @Test public void existingIosHeadWindowRequiresStableReferenceAndSustainedEvidence() {
        HeadWindow w = new HeadWindow();
        for (int i=0;i<3;i++) assertNull(w.consume(i*3000L,100000+i*3000L,1,.8,true));
        for (int i=3;i<8;i++) assertNull(w.consume(i*3000L,100000+i*3000L,.7,.8,true));
        HeadWindow.Candidate c=w.consume(24000,124000,.7,.8,true);
        assertNotNull(c); assertEquals(6,c.samples); assertEquals(109000,c.started);
        assertNull(w.consume(27000,127000,.7,.8,true));
    }

    @Test public void missingLowConfidenceAndLongGapNeverAccumulateHeadCandidate() {
        HeadWindow w = new HeadWindow();
        for (int i=0;i<3;i++) w.consume(i*3000L,i*3000L,1,.8,true);
        for (int i=3;i<10;i++) assertNull(w.consume(i*3000L,i*3000L,.7,.4,true));
        assertNull(w.consume(60000,60000,.7,.8,true));
        assertNull(w.consume(63000,63000,.7,.8,false));
    }
}
