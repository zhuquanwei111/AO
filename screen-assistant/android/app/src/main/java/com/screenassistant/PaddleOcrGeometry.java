package com.screenassistant;

/** Paddle 四点框转现有屏幕绝对坐标，不对识别行做语义重排或合并。 */
final class PaddleOcrGeometry {
    private PaddleOcrGeometry() { }

    static int[] absoluteBounds(float[] quad, int roiLeft, int roiTop, int roiRight, int roiBottom) {
        if (quad == null || quad.length != 8) return null;
        float left=Float.POSITIVE_INFINITY, top=Float.POSITIVE_INFINITY;
        float right=Float.NEGATIVE_INFINITY, bottom=Float.NEGATIVE_INFINITY;
        for (int i=0; i<quad.length; i+=2) {
            float x=quad[i], y=quad[i+1];
            if (!Float.isFinite(x) || !Float.isFinite(y)) return null;
            left=Math.min(left,x); top=Math.min(top,y);
            right=Math.max(right,x); bottom=Math.max(bottom,y);
        }
        int l=Math.max(roiLeft,roiLeft+(int)Math.floor(left));
        int t=Math.max(roiTop,roiTop+(int)Math.floor(top));
        int r=Math.min(roiRight,roiLeft+(int)Math.ceil(right));
        int b=Math.min(roiBottom,roiTop+(int)Math.ceil(bottom));
        return l>=r || t>=b ? null : new int[]{l,t,r,b};
    }
}
