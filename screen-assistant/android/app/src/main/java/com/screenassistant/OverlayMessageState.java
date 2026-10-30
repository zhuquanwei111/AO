package com.screenassistant;

import java.util.ArrayDeque;
import java.util.Iterator;
import java.util.LinkedHashMap;

/** 主线程内管理本场提示；只保留有界内存，不持久化提示正文。 */
final class OverlayMessageState {
    static final String WAITING_TEXT = "辅助中，等待内容";
    static final long MIN_READ_MS = 5000;
    static final int CAPACITY = 16;
    private static final int SEEN_CAPACITY = 128;

    private static final class Message {
        final String id, text;
        final int priority;
        final long sourceExpiry, maxVisible;
        Message(String id, String text, int priority, long sourceExpiry, long maxVisible) {
            this.id = id; this.text = text; this.priority = priority;
            this.sourceExpiry = sourceExpiry; this.maxVisible = maxVisible;
        }
    }

    private boolean running;
    private final ArrayDeque<Message> pending = new ArrayDeque<>();
    private final LinkedHashMap<String, Long> seen = new LinkedHashMap<>();
    private Message current;
    private long expiresAtMs, protectedUntilMs;
    private int expiredCount, overflowCount;

    void start() { start(WAITING_TEXT); }
    void start(String initialStatus) { clear(); running = true; }
    void stop() { running = false; clear(); }

    private void clear() {
        current = null; pending.clear(); seen.clear();
        expiresAtMs = 0; protectedUntilMs = 0;
        expiredCount = 0; overflowCount = 0;
    }

    boolean update(String value, long sourceExpiryMs, long nowMs, long maxVisibleMs) {
        return update("", value, 0, sourceExpiryMs, nowMs, maxVisibleMs);
    }

    boolean update(String id, String value, int priority, long sourceExpiryMs, long nowMs, long maxVisibleMs) {
        if (!running || value == null || value.isEmpty() || maxVisibleMs <= 0) return false;
        advance(nowMs);
        if (sourceExpiryMs > 0 && sourceExpiryMs <= nowMs) { expiredCount++; return false; }
        // 实时提示以ID幂等；兼容无ID的本地问答仅对当前/待显示文字去重。
        String key = id == null ? "" : id;
        seen.entrySet().removeIf(entry -> entry.getValue() <= nowMs);
        if (!key.isEmpty() && (seen.containsKey(key) || containsId(key))) return false;
        if (key.isEmpty() && ((current != null && value.equals(current.text))
                || pending.stream().anyMatch(item -> value.equals(item.text)))) return false;
        long sourceExpiry = sourceExpiryMs > 0 ? sourceExpiryMs : nowMs + maxVisibleMs;
        if (!key.isEmpty()) {
            if (seen.size() >= SEEN_CAPACITY) seen.remove(seen.keySet().iterator().next());
            seen.put(key, sourceExpiry);
        }
        Message next = new Message(key, value, priority, sourceExpiry, maxVisibleMs);
        if (pending.size() >= CAPACITY) {
            overflowCount++;
            if (priority < 100) return false;
            // 与现有iOS有界容器一致：紧急项满载时可淘汰最后一个普通等待项。
            Iterator<Message> reverse = pending.descendingIterator();
            boolean removed = false;
            while (reverse.hasNext()) {
                if (reverse.next().priority < 100) { reverse.remove(); removed = true; break; }
            }
            if (!removed) return false;
        }
        pending.addLast(next);
        advance(nowMs);
        return true;
    }

    private boolean containsId(String id) {
        return (current != null && id.equals(current.id)) || pending.stream().anyMatch(item -> id.equals(item.id));
    }

    private void advance(long nowMs) {
        if (!running) return;
        Iterator<Message> iterator = pending.iterator();
        while (iterator.hasNext()) {
            if (iterator.next().sourceExpiry <= nowMs) { iterator.remove(); expiredCount++; }
        }
        if (current != null && nowMs >= expiresAtMs) current = null;
        Message urgent = null;
        for (Message item : pending) if (item.priority >= 100) { urgent = item; break; }
        Message next = null;
        if (urgent != null && (current == null || current.priority < 100)) {
            pending.remove(urgent); next = urgent;
        } else if (current == null || (current.priority < 100 && nowMs >= protectedUntilMs)) {
            next = pending.pollFirst();
        }
        if (next != null) {
            current = next;
            // 排队不改源TTL；开始显示时仍受既有6秒显示上限约束。
            expiresAtMs = Math.min(next.sourceExpiry, nowMs + next.maxVisible);
            protectedUntilMs = Math.min(expiresAtMs, nowMs + MIN_READ_MS);
        } else if (current == null) { expiresAtMs = 0; protectedUntilMs = 0; }
    }

    String textAt(long nowMs) { advance(nowMs); return current == null ? "" : current.text; }

    void setStatus(String value, boolean discardMessage) {
        // 诊断由App/通知保存；连接、上传、复盘状态不能作为悬浮正文。
        if (discardMessage) { current = null; pending.clear(); expiresAtMs = 0; protectedUntilMs = 0; }
    }

    boolean shouldDisplay(boolean overlayAllowed, boolean assistantVisible) {
        return running && overlayAllowed && !assistantVisible;
    }

    long expiresAtMs() { return expiresAtMs; }
    long protectedUntilMs() { return protectedUntilMs; }
    long sourceExpiryMs() { return current == null ? 0 : current.sourceExpiry; }
    String currentId() { return current == null ? "" : current.id; }
    int pendingCount() { return pending.size(); }
    int expiredCount() { return expiredCount; }
    int overflowCount() { return overflowCount; }
    boolean isMessageActive(long nowMs) { advance(nowMs); return current != null; }

    long nextChangeAtMs(long nowMs) {
        advance(nowMs);
        if (current == null) return 0;
        long next = expiresAtMs;
        if (!pending.isEmpty() && current.priority < 100) next = Math.min(next, protectedUntilMs);
        for (Message item : pending) next = Math.min(next, item.sourceExpiry);
        return next;
    }
}
