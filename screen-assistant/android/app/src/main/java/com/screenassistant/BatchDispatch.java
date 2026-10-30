package com.screenassistant;

import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

/** 逐条发送有界采样；停止、过期、队列满和请求失败分别处理。 */
final class BatchDispatch {
    interface Send<T> { void send(T value) throws Exception; }
    static <T> void submit(Executor executor, CollectedRows<T> batch, BooleanSupplier active,
                           BooleanSupplier fresh, Send<T> send, Consumer<String> status, boolean hadLoss) {
        if (batch.rows.isEmpty()) return;
        try {
            executor.execute(() -> {
                int sent=0;
                try {
                    for (CollectedRows.Row<T> row:batch.rows) {
                        if (!active.getAsBoolean()) {
                            status.accept("采集暂停或场次已变化，" + (batch.rows.size()-sent) + " 条未上传"); return;
                        }
                        if (!fresh.getAsBoolean()) {
                            status.accept("采样已过期，" + (batch.rows.size()-sent) + " 条未上传"); return;
                        }
                        send.send(row.value); sent++;
                    }
                    if (!hadLoss && active.getAsBoolean()) status.accept("采集中，采样已上传；等待运营提示");
                } catch (Exception e) {
                    if (active.getAsBoolean()) status.accept("采样上传未确认，剩余 " + (batch.rows.size()-sent)
                            + " 条未确认：" + e.getClass().getSimpleName());
                }
            });
        } catch (RejectedExecutionException e) {
            if (active.getAsBoolean()) status.accept("上传队列已满，本轮 " + batch.rows.size() + " 条未上传");
        }
    }
}
