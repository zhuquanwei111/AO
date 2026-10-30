package com.screenassistant;

import org.junit.Test;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicBoolean;
import static org.junit.Assert.*;

public class BatchDispatchTest {
    private CollectedRows<String> batch(int count) {
        CollectedRows<String> b=new CollectedRows<>(128);
        for (int i=0;i<count;i++) b.add("行"+i);
        return b;
    }
    @Test public void moreThanTenReachTheSenderWithoutTextDeduplication() {
        CollectedRows<String> b=batch(15); b.add("行0");
        List<String> sent=new ArrayList<>(), states=new ArrayList<>();
        BatchDispatch.submit(Runnable::run,b,()->true,()->true,sent::add,states::add,false);
        assertEquals(16,sent.size()); assertEquals("行0",sent.get(15));
        assertTrue(states.get(0).contains("已上传"));
    }
    @Test public void expiredAndQueueFullAreVisibleAndNeverSend() {
        List<String> sent=new ArrayList<>(), states=new ArrayList<>();
        BatchDispatch.submit(Runnable::run,batch(3),()->true,()->false,sent::add,states::add,false);
        BatchDispatch.submit(r->{throw new RejectedExecutionException();},batch(4),()->true,()->true,sent::add,states::add,false);
        assertTrue(sent.isEmpty()); assertTrue(states.get(0).contains("3 条未上传"));
        assertTrue(states.get(1).contains("4 条未上传"));
    }
    @Test public void errorReportsRemainderAndStopPreventsLaterRows() {
        List<String> states=new ArrayList<>(), sent=new ArrayList<>();
        BatchDispatch.submit(Runnable::run,batch(3),()->true,()->true,v->{
            if (!sent.isEmpty()) throw new IOException(); sent.add(v);
        },states::add,false);
        assertEquals(1,sent.size()); assertTrue(states.get(0).contains("剩余 2 条未确认"));
        AtomicBoolean active=new AtomicBoolean(true); sent.clear(); states.clear();
        BatchDispatch.submit(Runnable::run,batch(3),active::get,()->true,v->{ sent.add(v); active.set(false); },states::add,false);
        assertEquals(1,sent.size()); assertTrue(states.get(0).contains("2 条未上传"));
    }
}
