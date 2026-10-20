package com.screenassistant;

import android.content.Context;
import android.content.SharedPreferences;

/** 轻量配置存取：服务地址 / 轮询间隔 / 框选区域（归一化 0..1）。 */
public final class Prefs {
    public static final String ACTION_STATE = "com.screenassistant.ACTION_STATE";

    private static final String FILE = "screen_assistant";
    private static final String K_URL = "server_url";
    private static final String K_INTERVAL = "interval_sec";
    private static final String K_REGION = "region_ltrb_norm"; // "l,t,r,b" 归一化

    public static final String DEFAULT_URL = "http://10.0.2.2:8000";
    public static final int DEFAULT_INTERVAL_SEC = 5;

    private Prefs() {}

    private static SharedPreferences sp(Context c) {
        return c.getApplicationContext().getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    public static String getServerUrl(Context c) {
        return sp(c).getString(K_URL, DEFAULT_URL);
    }

    public static void setServerUrl(Context c, String url) {
        sp(c).edit().putString(K_URL, url).apply();
    }

    public static int getIntervalSec(Context c) {
        return sp(c).getInt(K_INTERVAL, DEFAULT_INTERVAL_SEC);
    }

    public static void setIntervalSec(Context c, int sec) {
        sp(c).edit().putInt(K_INTERVAL, sec).apply();
    }

    /** 返回归一化区域 float[]{l,t,r,b}，未设置返回 null。 */
    public static float[] getRegion(Context c) {
        String v = sp(c).getString(K_REGION, null);
        if (v == null) return null;
        try {
            String[] parts = v.split(",");
            if (parts.length != 4) return null;
            float[] r = new float[4];
            for (int i = 0; i < 4; i++) r[i] = Float.parseFloat(parts[i]);
            if (r[0] < r[2] && r[1] < r[3]) return r;
            return null;
        } catch (Exception e) {
            return null;
        }
    }

    public static void setRegion(Context c, float l, float t, float r, float b) {
        sp(c).edit().putString(K_REGION, l + "," + t + "," + r + "," + b).apply();
    }

    public static void clearRegion(Context c) {
        sp(c).edit().remove(K_REGION).apply();
    }
}
