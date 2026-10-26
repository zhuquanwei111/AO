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
            if (Regions.valid(r)) return r;
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

    public static LiveClient.Session getSession(Context c) {
        try {
            String raw = sp(c).getString("live_session", null);
            return raw == null ? null : LiveClient.Session.from(new org.json.JSONObject(raw));
        } catch (org.json.JSONException e) { return null; }
    }

    /** 私有SharedPreferences；同步落盘后才报告创建成功，避免进程退出丢凭证。 */
    public static boolean saveSession(Context c, LiveClient.Session session) {
        try { return sp(c).edit().putString("live_session", session.json().toString()).commit(); }
        catch (org.json.JSONException e) { return false; }
    }

    public static String getDouyinAccount(Context c) { return sp(c).getString("douyin_account", ""); }
    public static void setDouyinAccount(Context c, String value) { sp(c).edit().putString("douyin_account", value).apply(); }

    public static float[] getRegion(Context c, String kind) {
        if ("legacy".equals(kind)) return getRegion(c);
        String raw = sp(c).getString("roi_" + kind, null);
        if (raw == null) return null;
        try {
            String[] parts = raw.split(",");
            float[] r = new float[parts.length];
            for (int i = 0; i < parts.length; i++) r[i] = Float.parseFloat(parts[i]);
            return Regions.valid(r) ? r : null;
        } catch (RuntimeException e) { return null; }
    }
    public static void setRegion(Context c, String kind, float l, float t, float r, float b) {
        if (!Regions.valid(new float[]{l,t,r,b})) return;
        if ("legacy".equals(kind)) { setRegion(c,l,t,r,b); return; }
        sp(c).edit().putString("roi_" + kind, l + "," + t + "," + r + "," + b).apply();
    }
    public static void clearRegion(Context c, String kind) {
        if ("legacy".equals(kind)) clearRegion(c);
        else sp(c).edit().remove("roi_" + kind).apply();
    }
    public static boolean onlineConfirmed(Context c) { return sp(c).getBoolean("online_semantics", false); }
    public static void setOnlineConfirmed(Context c, boolean value) { sp(c).edit().putBoolean("online_semantics", value).apply(); }
    public static int getAudioMode(Context c) { return Math.max(0,Math.min(2,sp(c).getInt("audio_mode",0))); }
    public static void setAudioMode(Context c,int mode) { sp(c).edit().putInt("audio_mode",mode).apply(); }
    public static boolean hostConfirmed(Context c) { return sp(c).getBoolean("audio_host_confirmed",false); }
    public static void setHostConfirmed(Context c,boolean value) { sp(c).edit().putBoolean("audio_host_confirmed",value).apply(); }

    public static org.json.JSONObject getCachedRecap(Context c) {
        try {
            String raw=sp(c).getString("last_recap",null);
            return raw==null?null:new org.json.JSONObject(raw);
        } catch (org.json.JSONException e) { return null; }
    }
    public static boolean hasCachedRecap(Context c) { return sp(c).contains("last_recap"); }
    public static boolean cacheTerminalRecap(Context c,LiveClient.Session session,
                                            org.json.JSONObject summary,org.json.JSONArray events) {
        LiveClient.Session current=getSession(c);
        if (current==null || !current.id.equals(session.id) || !RecapText.terminal(summary)) return false;
        try {
            String raw=new org.json.JSONObject().put("summary",summary).put("events",events).toString();
            return sp(c).edit().putString("last_recap",raw).remove("live_session").commit();
        } catch (org.json.JSONException e) { return false; }
    }
}
