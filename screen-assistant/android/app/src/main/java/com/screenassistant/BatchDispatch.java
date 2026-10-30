package com.screenassistant;

import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;
import java.util.function.BiConsumer;

/** 逐条发送有界采样；停止、过期、队列满和请求失败分别处理。 */
final class BatchDispatch {
    static ThreadPoolExecutor executor() {
        return new ThreadPoolExecutor(1,1,0,TimeUnit.SECONDS,
                new ArrayBlockingQueue<>(8),new ThreadPoolExecutor.AbortPolicy());
    }
    interface Send<T> { void send(T value) throws Exception; }
    static <T> void submit(Executor executor, CollectedRows<T> batch, BooleanSupplier active,
                           BooleanSupplier fresh, Send<T> send, Consumer<String> status, boolean hadLoss) {
        submit(executor,batch,active,fresh,send,status,hadLoss,(value,outcome)->{});
    }
    static <T> void submit(Executor executor, CollectedRows<T> batch, BooleanSupplier active,
                           BooleanSupplier fresh, Send<T> send, Consumer<String> status, boolean hadLoss,
                           BiConsumer<T,String> outcome) {
        if (batch.rows.isEmpty()) return;
        try {
            executor.execute(() -> {
                int sent=0;
                try {
                    for (CollectedRows.Row<T> row:batch.rows) {
                        if (!active.getAsBoolean()) {
                            remaining(batch,sent,outcome,"not_sent");
                            status.accept("采集暂停或场次已变化，" + (batch.rows.size()-sent) + " 条未上传"); return;
                        }
                        if (!fresh.getAsBoolean()) {
                            remaining(batch,sent,outcome,"expired");
                            status.accept("采样已过期，" + (batch.rows.size()-sent) + " 条未上传"); return;
                        }
                        send.send(row.value); outcome.accept(row.value,"http_success"); sent++;
                    }
                    if (!hadLoss && active.getAsBoolean()) status.accept("采集中，采样已上传；等待运营提示");
                } catch (Exception e) {
                    if (sent < batch.rows.size()) outcome.accept(batch.rows.get(sent).value,"unconfirmed");
                    remaining(batch,sent+1,outcome,"not_sent");
                    String cause=e instanceof LiveClient.HttpError ? "HTTP " + ((LiveClient.HttpError)e).status
                            : e.getClass().getSimpleName();
                    if (active.getAsBoolean()) status.accept("采样上传未确认，剩余 " + (batch.rows.size()-sent)
                            + " 条未确认：" + cause);
                }
            });
        } catch (RejectedExecutionException e) {
            remaining(batch,0,outcome,"queue_full");
            if (active.getAsBoolean()) status.accept("上传队列已满，本轮 " + batch.rows.size() + " 条未上传");
        }
    }
    private static <T> void remaining(CollectedRows<T> batch,int from,BiConsumer<T,String> outcome,String value) {
        for (int i=from;i<batch.rows.size();i++) outcome.accept(batch.rows.get(i).value,value);
    }
}
