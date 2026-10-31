package com.screenassistant;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/** 一帧的有界行集合；批次及行序仅在本机使用。 */
final class CollectedRows<T> {
    static final class Row<T> {
        final int index;
        final T value;
        Row(int index, T value) { this.index = index; this.value = value; }
    }
    final String batchId = UUID.randomUUID().toString();
    final List<Row<T>> rows = new ArrayList<>();
    private final int capacity;
    int dropped;
    private int nextIndex;
    CollectedRows(int capacity) { this.capacity = capacity; }
    void add(T value) {
        addAt(nextIndex, value);
    }
    void addAt(int index, T value) {
        nextIndex = Math.max(nextIndex, index + 1);
        if (rows.size() >= capacity) { dropped++; return; }
        rows.add(new Row<>(index, value));
    }
}
