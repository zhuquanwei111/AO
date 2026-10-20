package com.screenassistant;

import android.content.Context;
import android.graphics.Rect;
import android.os.Build;
import android.util.DisplayMetrics;
import android.view.Display;
import android.view.WindowManager;

/** 获取整屏真实尺寸（含系统栏，MediaProjection 采集坐标系）与 DPI。 */
public final class ScreenMetrics {
    private ScreenMetrics() {}

    /** 返回 int[]{width, height, densityDpi}（整屏真实尺寸）。 */
    public static int[] realSizeDpi(Context ctx) {
        Display display = ((android.hardware.display.DisplayManager)
                ctx.getSystemService(Context.DISPLAY_SERVICE))
                .getDisplay(Display.DEFAULT_DISPLAY);
        DisplayMetrics dm = new DisplayMetrics();
        // getRealMetrics 自 API31 起标记废弃但仍可用，是 API29 取整屏尺寸的官方途径
        display.getRealMetrics(dm);
        int w = dm.widthPixels;
        int h = dm.heightPixels;
        if (Build.VERSION.SDK_INT >= 30) {
            Rect b = ctx.getSystemService(WindowManager.class)
                    .getMaximumWindowMetrics().getBounds();
            if (b.width() > 0 && b.height() > 0) {
                w = Math.max(w, b.width());
                h = Math.max(h, b.height());
            }
        }
        return new int[]{w, h, dm.densityDpi};
    }

    /** 当前显示旋转角（用于检测会话中途旋转）。 */
    public static int rotation(Context ctx) {
        Display display = ((android.hardware.display.DisplayManager)
                ctx.getSystemService(Context.DISPLAY_SERVICE))
                .getDisplay(Display.DEFAULT_DISPLAY);
        return display.getRotation();
    }
}
