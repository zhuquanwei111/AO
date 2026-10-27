package com.screenassistant;

/** 只管理当前辅助覆盖层文字与过期时点，不持久化任何提示内容。 */
final class OverlayMessageState {
    static final String WAITING_TEXT = "辅助中，等待内容";

    private boolean running;
    private String status = WAITING_TEXT;
    private String message = "";
    private long expiresAtMs;

    void start() {
        start(WAITING_TEXT);
    }

    void start(String initialStatus) {
        running = true;
        status = initialStatus;
        message = "";
        expiresAtMs = 0;
    }

    void stop() {
        running = false;
        message = "";
        expiresAtMs = 0;
    }

    boolean update(String value, long sourceExpiryMs, long nowMs, long maxVisibleMs) {
        if (!running || value == null || value.isEmpty()) return false;
        if (sourceExpiryMs > 0 && sourceExpiryMs <= nowMs) return false;
        if (value.equals(message) && !isExpired(nowMs)) return false;
        message = value;
        long localExpiry = nowMs + maxVisibleMs;
        expiresAtMs = sourceExpiryMs > 0 ? Math.min(sourceExpiryMs, localExpiry) : localExpiry;
        return true;
    }

    String textAt(long nowMs) {
        if (!running) return "";
        if (isExpired(nowMs)) {
            message = "";
            expiresAtMs = 0;
        }
        return message.isEmpty() ? status : message;
    }

    void setStatus(String value, boolean discardMessage) {
        status = value;
        if (discardMessage) { message = ""; expiresAtMs = 0; }
    }

    boolean shouldDisplay(boolean overlayAllowed, boolean assistantVisible) {
        return running && overlayAllowed && !assistantVisible;
    }

    long expiresAtMs() {
        return expiresAtMs;
    }

    boolean isMessageActive(long nowMs) {
        return running && !message.isEmpty() && !isExpired(nowMs);
    }

    private boolean isExpired(long nowMs) {
        return !message.isEmpty() && expiresAtMs > 0 && nowMs >= expiresAtMs;
    }
}
