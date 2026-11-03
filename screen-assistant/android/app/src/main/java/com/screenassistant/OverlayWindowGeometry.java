package com.screenassistant;

/** 仅计算同一悬浮提示的两窗位置及保守遮挡透明度。 */
final class OverlayWindowGeometry {
    private OverlayWindowGeometry() { }

    private static float limit(float maximum) {
        return Float.isFinite(maximum) ? Math.max(0f, Math.min(.8f, maximum)) : 0f;
    }
    static float handleAlpha(float maximum) { return Math.min(.4f, limit(maximum) / 2f); }
    static float bodyAlpha(float maximum) {
        float max = limit(maximum), handle = handleAlpha(maximum);
        if (max == 0f) return 0f;
        return Math.max(0f, Math.nextDown((max - handle) / (1f - handle)));
    }
    static int[] clamp(int x, int y, int screenWidth, int screenHeight,
                       int handleWidth, int bodyWidth, int totalHeight) {
        int left = Math.max(0, Math.min(x, Math.max(0, screenWidth - handleWidth - bodyWidth)));
        int top = Math.max(0, Math.min(y, Math.max(0, screenHeight - totalHeight)));
        return new int[]{left, top, left + handleWidth};
    }
}
