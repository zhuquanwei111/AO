package com.screenassistant;

import org.json.JSONException;
import org.json.JSONObject;

/** 全屏左上原点、ltrb归一化；不依据文字/人物自动移动。 */
public final class Regions {
    public static final String[] KEYS = {"legacy", "comment", "online_count", "platform_notice", "person"};
    public static final String[] LABELS = {"旧问答区域", "公屏评论", "在线人数", "平台通知", "独立人物画面"};
    private Regions() {}
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
