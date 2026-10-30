package com.screenassistant;

import android.content.Intent;

/** 只连接实际产品提示窗，不申请投影、不发送请求。 */
public final class DebugOverlayService extends CaptureService {
    static boolean running;
    static final String ACTION_EXPAND="com.screenassistant.DEBUG_EXPAND";
    @Override public int onStartCommand(Intent intent,int flags,int startId) {
        if (intent!=null && ACTION_STOP.equals(intent.getAction())) return super.onStartCommand(intent,flags,startId);
        if (intent!=null && ACTION_EXPAND.equals(intent.getAction()) && running) {
            expandLocalOverlayForDebug(); return START_NOT_STICKY;
        }
        verifyClockJsonTypes();
        if (showLocalOverlayForDebug()) running=true; else stopSelf();
        return START_NOT_STICKY;
    }
    @Override public void onDestroy() { running=false; super.onDestroy(); }
    @Override protected void dump(java.io.FileDescriptor fd, java.io.PrintWriter writer, String[] args) {
        dumpLocalOverlayForDebug(writer);
    }
    private void verifyClockJsonTypes() {
        String[] values={"1000","1000.0","true","\"1000\"","null","9223372036854775808"};
        try {
            long now=android.os.SystemClock.elapsedRealtimeNanos();
            for (int i=0;i<values.length;i++) {
                org.json.JSONObject page=new org.json.JSONObject("{\"server_time_ms\":"+values[i]+",\"server_processing_ms\":0}")
                        .put(PromptTiming.SENT_NS,now).put(PromptTiming.RECEIVED_NS,now);
                boolean valid=PromptTiming.from(page)!=null;
                if (valid!=(i==0)) throw new IllegalStateException("本机JSON时效整数核验失败");
                android.util.Log.i("CaptureService","event=debug_timing_json_type case="+i
                        +" actual="+page.opt("server_time_ms").getClass().getSimpleName()+" accepted="+valid);
            }
        } catch (org.json.JSONException e) { throw new IllegalStateException(e); }
    }
}
