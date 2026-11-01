package com.screenassistant;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Rect;
import android.media.Image;
import android.os.SystemClock;
import com.paddle.ocr.model.OCRBox;
import com.paddle.ocr.model.OCRResult;
import com.paddle.ocr.model.OCRRunResult;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

/** 仅SDK及现有HTTP协议的薄适配；同帧时间、单在途推理、最多一个待发批次。 */
final class LiveFrameAnalyzer implements AutoCloseable {
    static final class Frame implements AutoCloseable {
        final Map<String,Bitmap> images=new LinkedHashMap<>();
        final Map<String,Rect> rects=new LinkedHashMap<>();
        final long captureNs=SystemClock.elapsedRealtimeNanos(), uptime=captureNs/1_000_000L;
        final Long wall;
        final int width,height;
        Frame(int width,int height, ObservationClock clock, String sessionId) {
            this.width=width; this.height=height;
            wall=clock==null ? null : clock.observedAt(sessionId,captureNs);
        }
        @Override public void close() { for (Bitmap b:images.values()) b.recycle(); }
    }
    static Frame snapshot(Context context, Image image, boolean ocr,
                          ObservationClock clock, String sessionId) {
        Frame f=new Frame(image.getWidth(),image.getHeight(),clock,sessionId);
        try {
            for (String key:Regions.ACTIVE_KEYS) {
                if (!ocr) continue;
                float[] region=Prefs.getRegion(context,key); if (region==null) continue;
                int[] r=Regions.crop(region,f.width,f.height); Rect rect=new Rect(r[0],r[1],r[2],r[3]);
                Bitmap crop=CaptureService.cropImage(image,rect);
                f.images.put(key,crop); f.rects.put(key,rect);
            }
            return f;
        } catch (RuntimeException e) { f.close(); throw e; }
    }

    private final PaddleOcrBridge text;
    private final LiveClient.Session session;
    private final BooleanSupplier active;
    private final Consumer<String> status;
    private final Context context;
    private final ThreadPoolExecutor upload=BatchDispatch.executor();
    private final CaptureLedger ledger=new CaptureLedger(10);
    private volatile long earliestUptime;

    void invalidateBefore(long uptime) { earliestUptime=uptime; }

    LiveFrameAnalyzer(Context context, PaddleOcrBridge text, LiveClient.Session session,
                      BooleanSupplier active, Consumer<String> status) {
        this.context=context; this.text=text; this.session=session; this.active=active; this.status=status;
    }

    private static JSONObject observation(String kind,long wall,JSONObject content,double confidence) throws Exception {
        return new JSONObject().put("kind",kind).put("event_id",UUID.randomUUID().toString())
                .put("source","android_media_projection_ocr").put("observed_at_ms",wall)
                .put("availability",content==null?"missing":"available").put("coverage","ocr_visible_sample")
                .put("reliability",confidence>=.5?"known":"unknown")
                .put("confidence",confidence<0?JSONObject.NULL:confidence).put("content",content==null?JSONObject.NULL:content);
    }

    static boolean collectComment(CollectedRows<JSONObject> rows, int lineIndex, String value, long wall,
                                  JSONObject position, double confidence) throws Exception {
        if (blankComment(value)) return false;
        if (value.codePointCount(0,value.length())>500) return false;
        rows.addAt(lineIndex,observation("comment",wall,new JSONObject().put("text",value)
                .put("text_position",position),confidence));
        return true;
    }

    private static boolean blankComment(String value) {
        if (value == null || value.isEmpty()) return true;
        for (int offset=0; offset<value.length();) {
            int cp=value.codePointAt(offset);
            boolean whitespace=(Character.isWhitespace(cp) && (cp<0x1c || cp>0x1f))
                    || Character.isSpaceChar(cp) || cp==0x85;
            if (!whitespace) return false;
            offset+=Character.charCount(cp);
        }
        return true;
    }

    static void freezeObservationMetadata(CollectedRows<JSONObject> rows, boolean enabled) throws Exception {
        if (!enabled) return;
        int count = 0;
        for (CollectedRows.Row<JSONObject> row : rows.rows)
            if ("comment".equals(row.value.optString("kind"))) count++;
        for (CollectedRows.Row<JSONObject> row : rows.rows) {
            String kind = row.value.optString("kind");
            if ("comment".equals(kind)) row.value.put("capture_batch_id", rows.batchId)
                    .put("line_index", row.index).put("capture_comment_count", count);
            else if ("online_count".equals(kind)) row.value.put("capture_batch_id", rows.batchId);
        }
    }

    void analyze(Frame frame) throws Exception {
        if (frame.uptime<earliestUptime) return;
        if (frame.wall==null) { status.accept("采样时间未核实，本轮未上传"); return; }
        long frameStartMs=SystemClock.elapsedRealtime();
        long coldLoadMs=text.initializeBlocking();
        CollectedRows<JSONObject> outgoing=new CollectedRows<>(128);
        int invalidComments=0, skippedOnlineLines=0;
        long inferenceWallMs=0;
        for (Map.Entry<String,Bitmap> entry:frame.images.entrySet()) {
            if (!active.getAsBoolean()) return;
            String kind=entry.getKey();
            OCRRunResult result=text.recognizeBlocking(entry.getValue());
            inferenceWallMs+=result.getTotalTimeMs();
            JSONArray lines=new JSONArray(); double confidence=1; StringBuilder raw=new StringBuilder();
            int lineIndex=0;
            for (OCRResult line:result.getResults()) {
                int sourceIndex=lineIndex++;
                if (!kind.equals("comment") && lines.length()>=20) { skippedOnlineLines++; continue; }
                Rect roi=frame.rects.get(kind);
                float[] quad=new float[8];
                for (int point=0;point<4;point++) {
                    quad[point*2]=line.getBox().getPoints().get(point).x;
                    quad[point*2+1]=line.getBox().getPoints().get(point).y;
                }
                int[] bounds=PaddleOcrGeometry.absoluteBounds(quad,roi.left,roi.top,roi.right,roi.bottom);
                if (bounds==null) { if (kind.equals("comment")) invalidComments++; continue; }
                int left=bounds[0],top=bounds[1],right=bounds[2],bottom=bounds[3];
                String value=line.getText();
                if (value==null || (kind.equals("comment") ? blankComment(value) : value.trim().isEmpty())) continue;
                double score=line.getConfidence(); if (!Double.isFinite(score)||score<0||score>1) score=0;
                JSONObject position=Regions.position(left,top,right,bottom,frame.width,frame.height);
                confidence=Math.min(confidence,score);
                if (kind.equals("comment")) {
                    if (session.batchMetadata && sourceIndex > 65535) { invalidComments++; continue; }
                    if (!collectComment(outgoing,sourceIndex,value,frame.wall,position,score)) invalidComments++;
                    continue;
                }
                if (value.codePointCount(0,value.length())>200) { skippedOnlineLines++; continue; }
                lines.put(new JSONObject().put("text",value).put("bbox",position).put("confidence",score));
                if (raw.length()>0) raw.append(' '); raw.append(value);
            }
            String value=raw.toString();
            if (kind.equals("comment")) continue;
            if (value.codePointCount(0,value.length())>120) { skippedOnlineLines += lines.length(); continue; }
            Rect roi=frame.rects.get(kind);
            JSONObject content=null;
            if (!value.isEmpty()) {
                content=new JSONObject().put("ocr_lines",lines).put("text_position",Regions.position(
                        roi.left,roi.top,roi.right,roi.bottom,frame.width,frame.height));
                content.put("raw_text",limit(value,120))
                        .put("field_semantics_confirmed",Prefs.onlineConfirmed(context));
            }
            outgoing.add(observation(kind,frame.wall,content,lines.length()==0?-1:confidence));
        }
        android.util.Log.d("LiveFrameAnalyzer", "Paddle OCR coldLoadMs="+coldLoadMs+
                " inferenceMs="+inferenceWallMs+" frameMs="+(SystemClock.elapsedRealtime()-frameStartMs)+
                " uploadRows="+outgoing.rows.size()+" uploadQueue="+upload.getQueue().size());
        boolean hadLoss=outgoing.dropped + invalidComments + skippedOnlineLines > 0;
        if (hadLoss)
            status.accept("本轮有 " + (outgoing.dropped + invalidComments + skippedOnlineLines) + " 行未上传，请检查识别区域");
        freezeObservationMetadata(outgoing, session.batchMetadata);
        ledger.register(outgoing);
        BatchDispatch.submit(upload,outgoing,active,() -> fresh(frame),
                row -> {
                    long sampleToSendMs=SystemClock.elapsedRealtime()-frame.uptime;
                    try { LiveClient.call(session,"observations",row); }
                    catch (LiveClient.HttpError error) {
                        if ("HTTP 422 observations time_outside_session".equals(error.getMessage()))
                            android.util.Log.i("CaptureService", "event=observation_time_422 route=observations status=422 "
                                    + LiveClient.observationTimes(session,row,sampleToSendMs));
                        throw error;
                    }
                },status,hadLoss,
                (row,outcome) -> ledger.outcome(row.optString("event_id"),outcome));
    }

    private boolean fresh(Frame f) {
        return FrameFreshness.valid(active.getAsBoolean(),f.uptime,earliestUptime,SystemClock.elapsedRealtime());
    }
    private static String limit(String s,int n) { return s.substring(0,Math.min(n,s.length())); }
    @Override public void close() { upload.shutdownNow(); ledger.clear(); }
}
