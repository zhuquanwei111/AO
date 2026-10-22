package com.screenassistant;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * POST /v1/answer 的极简客户端（HttpURLConnection + org.json，零第三方依赖）。
 * 契约：
 *  - 请求体 JSON 仅含 sentence 字段；
 *  - HTTP200 {status=ok, reply=非空} -> SUCCESS；
 *  - HTTP204 -> DUPLICATE（客户端保留当前展示，不更新）；
 *  - HTTP422/503/504 及网络/解析异常 -> DROP（本轮丢弃，不弹窗，等待下轮）。
 */
public final class AnswerClient {

    public enum Outcome { SUCCESS, DUPLICATE, DROP }

    public static final class Result {
        public final Outcome outcome;
        public final String reply;   // 仅 SUCCESS 时非空
        public final String error;   // 仅 DROP 时可能含诊断信息（只写日志/通知，不展示给用户弹窗）
        Result(Outcome o, String reply, String error) {
            this.outcome = o; this.reply = reply; this.error = error;
        }
    }

    private AnswerClient() {}

    /** 阻塞调用，需在后台线程执行。 */
    public static Result post(String baseUrl, String sentence, int connectTimeoutMs, int readTimeoutMs) {
        HttpURLConnection conn = null;
        try {
            String url = baseUrl.trim();
            if (url.endsWith("/")) url = url.substring(0, url.length() - 1);
            url = url + "/v1/answer";
            conn = (HttpURLConnection) new URL(url).openConnection();
            conn.setRequestMethod("POST");
            conn.setConnectTimeout(connectTimeoutMs);
            conn.setReadTimeout(readTimeoutMs);
            conn.setDoOutput(true);
            conn.setInstanceFollowRedirects(false);
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            conn.setRequestProperty("Accept", "application/json");
            byte[] body = ("{\"sentence\":" + JSONObject.quote(sentence) + "}")
                    .getBytes(StandardCharsets.UTF_8);
            conn.setFixedLengthStreamingMode(body.length);
            try (OutputStream os = conn.getOutputStream()) {
                os.write(body);
                os.flush();
            }
            int code = conn.getResponseCode();
            if (code == 204) return new Result(Outcome.DUPLICATE, null, null);
            if (code == 200) {
                String respBody = readAll(conn.getInputStream(), 64 * 1024);
                JSONObject jo = new JSONObject(respBody);
                if ("ok".equals(jo.optString("status")) && !jo.optString("reply").isEmpty()) {
                    return new Result(Outcome.SUCCESS, jo.optString("reply"), null);
                }
                return new Result(Outcome.DROP, null, "200响应格式无效");
            }
            // 422 / 5xx / 其他：本轮丢弃
            return new Result(Outcome.DROP, null, "HTTP " + code);
        } catch (IOException | org.json.JSONException | RuntimeException e) {
            return new Result(Outcome.DROP, null, e.getClass().getSimpleName() + ": " + e.getMessage());
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private static String readAll(InputStream in, int cap) throws IOException {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = in.read(buf)) > 0) {
            if (bos.size() + n > cap) throw new IOException("响应体超限");
            bos.write(buf, 0, n);
        }
        return new String(bos.toByteArray(), StandardCharsets.UTF_8);
    }
}
