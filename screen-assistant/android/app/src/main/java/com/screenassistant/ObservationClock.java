package com.screenassistant;

/** 同场采样时间的保守下界；依赖服务端墙钟连续及两端单调时钟速率近似。 */
final class ObservationClock {
    private static final long MAX_AGE_NS=30_000_000_000L;
    private static final long MAX_TRANSPORT_MS=1000;
    private final String sessionId;
    private final long serverMs, receivedNs;

    private ObservationClock(String sessionId, long serverMs, long receivedNs) {
        this.sessionId=sessionId; this.serverMs=serverMs; this.receivedNs=receivedNs;
    }

    static ObservationClock from(String sessionId, long startedAt, PromptTiming timing) {
        if (sessionId==null || sessionId.isEmpty() || startedAt<=0 || timing==null
                || timing.serverTimeMs<startedAt || timing.transportMs>MAX_TRANSPORT_MS) return null;
        return new ObservationClock(sessionId,timing.serverTimeMs,timing.receivedNs);
    }

    Long observedAt(String sessionId, long frameNs) {
        if (!this.sessionId.equals(sessionId) || frameNs<receivedNs) return null;
        long delta=frameNs-receivedNs;
        if (delta<0 || delta>MAX_AGE_NS) return null;
        long elapsedMs=delta/1_000_000L;
        if (serverMs>Long.MAX_VALUE-elapsedMs) return null;
        return serverMs+elapsedMs;
    }
}
