package com.screenassistant;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/** Android 薄客户端：所有场次请求绑定创建时的服务地址；令牌不写日志。 */
public final class LiveClient {
    public static final class Session {
        public final String baseUrl, id, token;
        public final long startedAt;
        public final boolean stopped;

        public Session(String baseUrl, String id, String token, long startedAt, boolean stopped) {
            this.baseUrl = baseUrl; this.id = id; this.token = token;
            this.startedAt = startedAt; this.stopped = stopped;
        }

        public JSONObject json() throws JSONException {
            return new JSONObject().put("base_url", baseUrl).put("session_id", id)
                    .put("session_token", token).put("started_at_ms", startedAt).put("stopped", stopped);
        }

        public static Session from(JSONObject o) throws JSONException {
            return new Session(o.getString("base_url"), o.getString("session_id"),
                    o.getString("session_token"), o.getLong("started_at_ms"), o.optBoolean("stopped"));
        }

        public Session asStopped() { return new Session(baseUrl, id, token, startedAt, true); }
    }

    public static final class HttpError extends IOException {
        public final int status;
        HttpError(int status) { super("HTTP " + status); this.status = status; }
    }

    public static boolean validBaseUrl(String value) {
        try {
            URI uri = new URI(value);
            return ("http".equalsIgnoreCase(uri.getScheme()) || "https".equalsIgnoreCase(uri.getScheme()))
                    && uri.getHost() != null && uri.getUserInfo() == null
                    && uri.getRawQuery() == null && uri.getRawFragment() == null
                    && (uri.getPort() == -1 || uri.getPort() > 0 && uri.getPort() <= 65535);
        } catch (Exception e) { return false; }
    }

    public static Session create(String baseUrl, String description, String account) throws IOException, JSONException {
        JSONObject payload = new JSONObject().put("description", description);
        if (!account.isEmpty()) payload.put("douyin_account", account);
        JSONObject result = request(baseUrl, "/v1/live/sessions", "POST", null, payload, 15000);
        return new Session(baseUrl, result.getString("session_id"), result.getString("session_token"),
                result.getLong("started_at_ms"), false);
    }

    public static JSONObject call(Session s, String path, JSONObject body) throws IOException, JSONException {
        JSONObject result = request(s.baseUrl, "/v1/live/sessions/" + encode(s.id) + "/" + path,
                body == null ? "GET" : "POST", s.token, body, path.startsWith("prompts") ? 22000 : 15000);
        if (result.has("session_id") && !s.id.equals(result.getString("session_id"))) throw new IOException("场次响应不匹配");
        return result;
    }

    public static JSONObject start(Session s) throws IOException, JSONException {
        return call(s,"start",new JSONObject().put("source","android_app"));
    }
    public static JSONObject stop(Session s) throws IOException, JSONException {
        return call(s,"stop",new JSONObject().put("source","android_app"));
    }

    public static JSONObject poll(Session s, String cursor) throws IOException, JSONException {
        return call(s, "prompts?after=" + encode(cursor) + "&wait_ms=15000", null);
    }

    public static JSONArray events(Session s) throws IOException, JSONException {
        JSONArray all = new JSONArray();
        String cursor = "";
        for (int page = 0; page < 1000; page++) {
            JSONObject data = call(s, "events?limit=100&after=" + encode(cursor), null);
            JSONArray rows = data.getJSONArray("events");
            for (int i = 0; i < rows.length(); i++) all.put(rows.getJSONObject(i));
            if (!data.optBoolean("has_more")) return all;
            String next = data.getString("next_cursor");
            if (next.equals(cursor) || rows.length() == 0) throw new IOException("事件分页未前进");
            cursor = next;
        }
        throw new IOException("事件分页超限");
    }

    static String encode(String v) {
        try { return URLEncoder.encode(v, "UTF-8"); }
        catch (java.io.UnsupportedEncodingException e) { throw new AssertionError(e); }
    }

    static JSONObject request(String base, String path, String method, String token,
                              JSONObject body, int timeout) throws IOException, JSONException {
        if (!validBaseUrl(base)) throw new IOException("服务地址无效");
        HttpURLConnection c = (HttpURLConnection) URI.create(base.replaceAll("/+$", "") + path).toURL().openConnection();
        try {
            c.setRequestMethod(method);
            c.setInstanceFollowRedirects(false);
            c.setConnectTimeout(5000); c.setReadTimeout(timeout);
            c.setRequestProperty("Accept", "application/json");
            if (token != null) c.setRequestProperty("X-Live-Session-Token", token);
            if (body != null) {
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
                c.setFixedLengthStreamingMode(bytes.length);
                try (java.io.OutputStream out = c.getOutputStream()) { out.write(bytes); }
            }
            int status = c.getResponseCode();
            if (status < 200 || status >= 300) throw new HttpError(status);
            if (status == 204) return new JSONObject();
            try (InputStream in = c.getInputStream(); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
                byte[] buffer = new byte[4096]; int n;
                while ((n = in.read(buffer)) != -1) {
                    if (out.size() + n > 2 * 1024 * 1024) throw new IOException("响应体超限");
                    out.write(buffer, 0, n);
                }
                return new JSONObject(out.toString(StandardCharsets.UTF_8.name()));
            }
        } finally { c.disconnect(); }
    }
}
