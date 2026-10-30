package com.screenassistant;

import android.content.Intent;

/** 只连接实际产品提示窗，不申请投影、不发送请求。 */
public final class DebugOverlayService extends CaptureService {
    static boolean running;
    @Override public int onStartCommand(Intent intent,int flags,int startId) {
        if (intent!=null && ACTION_STOP.equals(intent.getAction())) return super.onStartCommand(intent,flags,startId);
        if (showLocalOverlayForDebug()) running=true; else stopSelf();
        return START_NOT_STICKY;
    }
    @Override public void onDestroy() { running=false; super.onDestroy(); }
}
