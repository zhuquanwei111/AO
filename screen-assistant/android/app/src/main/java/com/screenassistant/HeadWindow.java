package com.screenassistant;

/** 直接移植72c94d2 PersonObservationEvidence.swift的现有持续低头窗口。
 * 这是既有产品启发式，非ML Kit姿态分类或真人准确率保证。 */
final class HeadWindow {
    static final class Candidate {
        final long started, observed;
        final int samples;
        final double confidence;
        Candidate(long started, long observed, int samples, double confidence) {
            this.started=started; this.observed=observed; this.samples=samples; this.confidence=confidence;
        }
    }
    private final double[] reference = new double[3];
    private int references, count;
    private long previousUptime=-1, previousWall, startUptime, startWall, lastPrompt=-1;
    private double minimumConfidence=1;

    private void clear() { references=0; count=0; minimumConfidence=1; }
    Candidate consume(long uptime, long wall, double ratio, double confidence, boolean valid) {
        if (previousUptime >= 0) {
            long gap=uptime-previousUptime;
            if (gap<=0 || wall<=previousWall) return null;
            if (gap>6500 || Math.abs((wall-previousWall)-gap)>2000) clear();
        }
        previousUptime=uptime; previousWall=wall;
        if (!valid || !Double.isFinite(ratio) || ratio < -5 || ratio > 5
                || !Double.isFinite(confidence) || confidence<.5 || confidence>1) { clear(); return null; }
        if (references<3) { reference[references++]=ratio; return null; }
        double high=Math.max(reference[0],Math.max(reference[1],reference[2]));
        double low=Math.min(reference[0],Math.min(reference[1],reference[2]));
        if (high-low>.12) { reference[0]=ratio; references=1; return null; }
        double baseline=(reference[0]+reference[1]+reference[2])/3;
        if (baseline-ratio<.22) { count=0; minimumConfidence=1; return null; }
        if (count==0) { startUptime=uptime; startWall=wall; }
        count=Math.min(100,count+1); minimumConfidence=Math.min(minimumConfidence,confidence);
        if (count<6 || uptime-startUptime<15000 || lastPrompt>=0 && uptime-lastPrompt<90000) return null;
        lastPrompt=uptime;
        return new Candidate(startWall,wall,count,minimumConfidence);
    }
}
