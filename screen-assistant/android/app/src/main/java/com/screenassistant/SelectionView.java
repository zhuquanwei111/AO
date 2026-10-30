package com.screenassistant;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.util.AttributeSet;
import android.view.MotionEvent;
import android.view.View;

/**
 * 全屏区域框选视图：空白处拖动=新建区域；区域内拖动=移动；拖动四角=缩放。
 * 坐标为视图本地坐标，由调用方换算到整屏坐标系（含状态栏偏移）。
 */
public class SelectionView extends View {

    private static final int MODE_NONE = 0;
    private static final int MODE_CREATE = 1;
    private static final int MODE_MOVE = 2;
    private static final int MODE_RESIZE_LT = 3;
    private static final int MODE_RESIZE_RT = 4;
    private static final int MODE_RESIZE_LB = 5;
    private static final int MODE_RESIZE_RB = 6;

    private static final float MIN_SIZE_PX = 60f;

    private final Paint dimPaint = new Paint();
    private final Paint strokePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint handlePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint sizePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint labelBackground = new Paint(Paint.ANTI_ALIAS_FLAG);

    private RectF rect = new RectF();
    private RectF[] selections;
    private int selected;
    private int mode = MODE_NONE;
    private float lastX, lastY;
    private float anchorX, anchorY;
    private float touchSlop = 24f;

    public SelectionView(Context c) {
        this(c, null);
    }

    public SelectionView(Context c, AttributeSet a) {
        super(c, a);
        float density = getResources().getDisplayMetrics().density;
        touchSlop = 28f * density;
        dimPaint.setColor(0x88000000);
        dimPaint.setStyle(Paint.Style.FILL);
        strokePaint.setColor(Color.WHITE);
        strokePaint.setStyle(Paint.Style.STROKE);
        strokePaint.setStrokeWidth(2.5f * density);
        handlePaint.setColor(Color.WHITE);
        handlePaint.setStyle(Paint.Style.FILL);
        sizePaint.setColor(Color.WHITE);
        sizePaint.setTextSize(13f * density);
        sizePaint.setTextAlign(Paint.Align.CENTER);
        labelBackground.setColor(0xCC111111);
    }

    @Override
    protected void onSizeChanged(int w, int h, int ow, int oh) {
        super.onSizeChanged(w, h, ow, oh);
        if (rect.isEmpty()) {
            rect.set(w * 0.25f, h * 0.40f, w * 0.75f, h * 0.60f);
        }
    }

    public RectF getSelectionRect() {
        return new RectF(rect);
    }

    void setSelections(RectF[] values) {
        selections=values; selected=0; rect=selections[0]; invalidate();
    }
    RectF[] getSelections() {
        if (selections == null) return null;
        return new RectF[]{new RectF(selections[0]),new RectF(selections[1])};
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        int w = getWidth(), h = getHeight();
        if (selections != null) {
            for (int i=0;i<selections.length;i++) {
                strokePaint.setColor(i==selected ? Color.WHITE : Color.CYAN);
                canvas.drawRect(selections[i],strokePaint);
                String label=Regions.ACTIVE_LABELS[i];
                float half=Math.min(w/2f, sizePaint.measureText(label)/2f + 6f);
                float labelX=Math.max(half,Math.min(w-half,selections[i].centerX()));
                float labelY=Math.max(selections[i].top-8f,sizePaint.getTextSize()+4f);
                canvas.drawRect(labelX-half,labelY-sizePaint.getTextSize()-3f,
                        labelX+half,labelY+4f,labelBackground);
                canvas.drawText(label,labelX,labelY,sizePaint);
            }
            strokePaint.setColor(Color.WHITE);
        }
        // 边框与四角
        canvas.drawRect(rect, strokePaint);
        float r = 7f * getResources().getDisplayMetrics().density;
        canvas.drawCircle(rect.left, rect.top, r, handlePaint);
        canvas.drawCircle(rect.right, rect.top, r, handlePaint);
        canvas.drawCircle(rect.left, rect.bottom, r, handlePaint);
        canvas.drawCircle(rect.right, rect.bottom, r, handlePaint);
        if (selections == null) {
            String info = Math.round(rect.width()) + " × " + Math.round(rect.height());
            canvas.drawText(info, rect.centerX(), Math.max(rect.top - 8f, sizePaint.getTextSize()), sizePaint);
        }
    }

    private int hitCorner(float x, float y) {
        if (dist(x, y, rect.left, rect.top) < touchSlop) return MODE_RESIZE_LT;
        if (dist(x, y, rect.right, rect.top) < touchSlop) return MODE_RESIZE_RT;
        if (dist(x, y, rect.left, rect.bottom) < touchSlop) return MODE_RESIZE_LB;
        if (dist(x, y, rect.right, rect.bottom) < touchSlop) return MODE_RESIZE_RB;
        return MODE_NONE;
    }

    private static float dist(float x1, float y1, float x2, float y2) {
        return (float) Math.hypot(x1 - x2, y1 - y2);
    }

    private void clampRect() {
        int w = getWidth(), h = getHeight();
        if (rect.width() < MIN_SIZE_PX) {
            if (mode == MODE_RESIZE_LT || mode == MODE_RESIZE_LB) rect.left = rect.right - MIN_SIZE_PX;
            else rect.right = rect.left + MIN_SIZE_PX;
        }
        if (rect.height() < MIN_SIZE_PX) {
            if (mode == MODE_RESIZE_LT || mode == MODE_RESIZE_RT) rect.top = rect.bottom - MIN_SIZE_PX;
            else rect.bottom = rect.top + MIN_SIZE_PX;
        }
        rect.left = Math.max(0, Math.min(rect.left, w));
        rect.right = Math.max(0, Math.min(rect.right, w));
        rect.top = Math.max(0, Math.min(rect.top, h));
        rect.bottom = Math.max(0, Math.min(rect.bottom, h));
    }

    @Override
    public boolean onTouchEvent(MotionEvent e) {
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                lastX = e.getX();
                lastY = e.getY();
                if (selections != null) {
                    for (int i=0;i<selections.length;i++) {
                        RectF candidate=selections[i];
                        if (candidate.contains(lastX,lastY)
                                || dist(lastX,lastY,candidate.left,candidate.top)<touchSlop
                                || dist(lastX,lastY,candidate.right,candidate.top)<touchSlop
                                || dist(lastX,lastY,candidate.left,candidate.bottom)<touchSlop
                                || dist(lastX,lastY,candidate.right,candidate.bottom)<touchSlop) {
                            selected=i; rect=candidate; break;
                        }
                    }
                }
                int corner = hitCorner(lastX, lastY);
                if (corner != MODE_NONE) {
                    mode = corner;
                } else if (rect.contains(lastX, lastY)) {
                    mode = MODE_MOVE;
                } else {
                    mode = selections == null ? MODE_CREATE : MODE_NONE;
                    anchorX = lastX;
                    anchorY = lastY;
                }
                return true;
            case MotionEvent.ACTION_MOVE:
                float x = e.getX(), y = e.getY();
                switch (mode) {
                    case MODE_CREATE:
                        rect.set(Math.min(anchorX, x), Math.min(anchorY, y),
                                Math.max(anchorX, x), Math.max(anchorY, y));
                        break;
                    case MODE_MOVE: {
                        float dx = x - lastX, dy = y - lastY;
                        int w = getWidth(), h = getHeight();
                        float nx = rect.left + dx, ny = rect.top + dy;
                        nx = Math.max(0, Math.min(nx, w - rect.width()));
                        ny = Math.max(0, Math.min(ny, h - rect.height()));
                        rect.offsetTo(nx, ny);
                        break;
                    }
                    case MODE_RESIZE_LT:
                        rect.left = x; rect.top = y; break;
                    case MODE_RESIZE_RT:
                        rect.right = x; rect.top = y; break;
                    case MODE_RESIZE_LB:
                        rect.left = x; rect.bottom = y; break;
                    case MODE_RESIZE_RB:
                        rect.right = x; rect.bottom = y; break;
                    default:
                        return true;
                }
                lastX = x;
                lastY = y;
                clampRect();
                invalidate();
                return true;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                mode = MODE_NONE;
                performClick();
                return true;
            default:
                return false;
        }
    }

    @Override
    public boolean performClick() {
        return super.performClick();
    }
}
