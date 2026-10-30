package com.screenassistant;

import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

/** 逐条发送有界采样；停止、过期、队列满和请求失败分别处理。 */
final class BatchDispatch {
    static ThreadPoolExecutor executor() {
        return new ThreadPoolExecutor(1,1,0,TimeUnit.SECONDS,
                new ArrayBlockingQueue<>(8),new ThreadPoolExecutor.AbortPolicy());
    }
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
                    String cause=e instanceof LiveClient.HttpError ? "HTTP " + ((LiveClient.HttpError)e).status
                            : e.getClass().getSimpleName();
                    if (active.getAsBoolean()) status.accept("采样上传未确认，剩余 " + (batch.rows.size()-sent)
                            + " 条未确认：" + cause);
                }
            });
        } catch (RejectedExecutionException e) {
            if (active.getAsBoolean()) status.accept("上传队列已满，本轮 " + batch.rows.size() + " 条未上传");
        }
    }
}
