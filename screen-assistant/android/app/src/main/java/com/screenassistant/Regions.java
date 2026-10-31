package com.screenassistant;

import org.json.JSONException;
import org.json.JSONObject;

/** 全屏左上原点、ltrb归一化；不依据文字/人物自动移动。 */
public final class Regions {
    public static final String[] KEYS = {"legacy", "comment", "online_count", "platform_notice", "person"};
    public static final String[] LABELS = {"旧问答区域", "公屏评论", "在线人数", "平台通知", "独立人物画面"};
    public static final String[] ACTIVE_KEYS = {"comment", "online_count"};
    public static final String[] ACTIVE_LABELS = {"公屏评论：读取可见评论", "当前在线人数：读取实时人数"};
    private Regions() {}
    static float[] commentDefault(float inputTop) {
        float bottom=Math.max(.18f,Math.min(1f,inputTop));
        return new float[]{0f,bottom-.18f,1f,bottom};
    }
    static float[] onlineDefault() { return new float[]{.75f,.035f,.96f,.115f}; }

    static boolean overlaps(int x,int y,int width,int height,int[] r) {
        return x<r[2] && x+width>r[0] && y<r[3] && y+height>r[1];
    }

    /** 窗口整块避让：只试目标位置和各矩形边界，不裁切任何识别区域。 */
    static int[] placeOverlay(int width,int height,int ow,int oh,int x,int y,int[][] regions) {
        if (ow<=0 || oh<=0 || ow>width || oh>height) return null;
        java.util.List<Integer> xs=new java.util.ArrayList<>(),ys=new java.util.ArrayList<>();
        xs.add(Math.max(0,Math.min(width-ow,x))); xs.add(0); xs.add(width-ow);
        ys.add(Math.max(0,Math.min(height-oh,y))); ys.add(0); ys.add(height-oh);
        for (int[] r:regions) { xs.add(r[0]-ow); xs.add(r[2]); ys.add(r[1]-oh); ys.add(r[3]); }
        int[] best=null; long distance=Long.MAX_VALUE;
        for (int px:xs) for (int py:ys) {
            if (px<0 || py<0 || px+ow>width || py+oh>height) continue;
            boolean hit=false; for (int[] r:regions) if (overlaps(px,py,ow,oh,r)) { hit=true; break; }
            long dx=(long)px-x,dy=(long)py-y,d=dx*dx+dy*dy;
            if (!hit && d<distance) { best=new int[]{px,py}; distance=d; }
        }
        return best;
    }
    public static boolean valid(float[] r) {
        if (r == null || r.length != 4) return false;
        for (float x : r) if (!Float.isFinite(x) || x < 0 || x > 1) return false;
        return r[2] > r[0] && r[3] > r[1];
    }
    public static int[] crop(float[] r, int w, int h) {
        if (!valid(r) || w < 2 || h < 2) throw new IllegalArgumentException("区域无效");
        int l = Math.min(w - 2, Math.max(0, Math.round(r[0] * w)));
        int t = Math.min(h - 2, Math.max(0, Math.round(r[1] * h)));
        return new int[]{l, t, Math.min(w, Math.max(l + 2, Math.round(r[2] * w))),
                Math.min(h, Math.max(t + 2, Math.round(r[3] * h)))};
    }
    public static JSONObject position(int l, int t, int r, int b, int w, int h) throws JSONException {
        return new JSONObject().put("x", l / (double) w).put("y", t / (double) h)
                .put("width", (r - l) / (double) w).put("height", (b - t) / (double) h);
    }
}
