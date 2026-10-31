package com.screenassistant;

import org.json.JSONObject;

/** 将同源服务时效映射到接收端连续单调时钟；本机字段不发回服务器。 */
final class PromptTiming {
    static final String SENT_NS = "_client_sent_elapsed_ns";
    static final String RECEIVED_NS = "_client_received_elapsed_ns";
    static final String DEADLINE_MS = "_client_expires_elapsed_ms";
    final long serverTimeMs, processingMs, rttMs, receivedMs, transportMs;

    private PromptTiming(long serverTime, long processing, long rtt, long received) {
        serverTimeMs = serverTime; processingMs = processing; rttMs = rtt;
        receivedMs = received; transportMs = rtt - processing;
    }

    static PromptTiming from(JSONObject page) {
        Long server = integer(page, "server_time_ms"), processing = integer(page, "server_processing_ms");
        Long sent = integer(page, SENT_NS), received = integer(page, RECEIVED_NS);
        if (server == null || server <= 0 || processing == null || processing < 0
                || sent == null || sent < 0 || received == null || received < sent) return null;
        long duration = received - sent;
        long rtt = duration / 1_000_000L + (duration % 1_000_000L == 0 ? 0 : 1);
        if (processing > rtt) return null;
        return new PromptTiming(server, processing, rtt, received / 1_000_000L);
    }

    /** 负数表示非法时效，0表示已过期；不按接收时刻重建源TTL。 */
    long deadline(JSONObject prompt) {
        Long expires = integer(prompt, "expires_at_ms");
        if (expires == null || expires <= 0) return -1;
        if (expires <= serverTimeMs) return 0;
        long remaining = expires - serverTimeMs;
        // 冻结契约：RTT向上取整，服务处理向下取整，再扣1ms取整余量。
        if (remaining <= transportMs || remaining - transportMs <= 1) return 0;
        remaining -= transportMs + 1;
        if (remaining > Long.MAX_VALUE - receivedMs) return -1;
        return receivedMs + remaining;
    }

    private static Long integer(JSONObject value, String key) {
        Object raw = value.opt(key);
        // getLong会接受浮点及数字字符串，这里只接受JSON整数的实际解析类型。
        return raw instanceof Integer || raw instanceof Long ? ((Number) raw).longValue() : null;
    }
}
