package com.screenassistant;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;

/** 仅供 adb 启动本地窗口测试，系统权限限制其他应用调用。 */
public final class DebugOverlayActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        Intent service=new Intent(this,DebugOverlayService.class);
        boolean stop=getIntent().getBooleanExtra("stop",false);
        if (stop && !DebugOverlayService.running) { finish(); return; }
        if (!stop && (CaptureService.phase != CaptureService.Phase.IDLE || CaptureService.uiVisible)) {
            finish(); return;
        }
        if (stop) service.setAction(CaptureService.ACTION_STOP);
        startService(service);
        finish();
    }
}
