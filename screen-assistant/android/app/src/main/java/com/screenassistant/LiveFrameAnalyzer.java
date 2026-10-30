package com.screenassistant;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Rect;
import android.media.Image;
import android.os.SystemClock;
import com.google.android.gms.tasks.Tasks;
import com.google.mlkit.vision.common.InputImage;
import com.google.mlkit.vision.text.Text;
import com.google.mlkit.vision.text.TextRecognizer;
import com.google.mlkit.vision.pose.Pose;
import com.google.mlkit.vision.pose.PoseDetection;
import com.google.mlkit.vision.pose.PoseDetector;
import com.google.mlkit.vision.pose.PoseLandmark;
import com.google.mlkit.vision.pose.defaults.PoseDetectorOptions;
import com.google.mlkit.vision.face.FaceDetection;
import com.google.mlkit.vision.face.FaceDetector;
import com.google.mlkit.vision.face.FaceDetectorOptions;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

/** 仅SDK及现有HTTP协议的薄适配；同帧时间、单在途推理、最多一个待发批次。 */
final class LiveFrameAnalyzer implements AutoCloseable {
    static final String PERSON_SOURCE="android_vision_person";
    static final class Frame implements AutoCloseable {
        final Map<String,Bitmap> images=new LinkedHashMap<>();
        final Map<String,Rect> rects=new LinkedHashMap<>();
        final long wall=System.currentTimeMillis(), uptime=SystemClock.elapsedRealtime();
        final int width,height;
        Frame(int width,int height) { this.width=width; this.height=height; }
        @Override public void close() { for (Bitmap b:images.values()) b.recycle(); }
    }
    static Frame snapshot(Context context, Image image, boolean ocr, boolean person) {
        Frame f=new Frame(image.getWidth(),image.getHeight());
        try {
            for (String key:Regions.ACTIVE_KEYS) {
                if (!ocr) continue;
                float[] region=Prefs.getRegion(context,key); if (region==null) continue;
                int[] r=Regions.crop(region,f.width,f.height); Rect rect=new Rect(r[0],r[1],r[2],r[3]);
                Bitmap crop=CaptureService.cropImage(image,rect);
                if (key.equals("person") && Math.max(crop.getWidth(),crop.getHeight())>512) {
                    float scale=512f/Math.max(crop.getWidth(),crop.getHeight());
                    Bitmap small=Bitmap.createScaledBitmap(crop,Math.max(1,Math.round(crop.getWidth()*scale)),
                            Math.max(1,Math.round(crop.getHeight()*scale)),true); crop.recycle(); crop=small;
                }
                f.images.put(key,crop); f.rects.put(key,rect);
            }
            return f;
        } catch (RuntimeException e) { f.close(); throw e; }
    }

    private final TextRecognizer text;
    private PoseDetector poses;
    private FaceDetector faces;
    private final LiveClient.Session session;
    private final BooleanSupplier active;
    private final Consumer<String> status;
    private final Context context;
    private final ThreadPoolExecutor upload=BatchDispatch.executor();
    private final CaptureLedger ledger=new CaptureLedger(10);
    private String previousNotice="";
    private String segment=UUID.randomUUID().toString();
    private HeadWindow head=new HeadWindow();
    private long sequence;
    private double[] previousGray;
    private String personRegion="";
    private volatile long earliestUptime;
    private volatile boolean reset;

    void invalidateBefore(long uptime) { earliestUptime=uptime; reset=true; }

    LiveFrameAnalyzer(Context context, TextRecognizer text, LiveClient.Session session,
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
        if (value.codePointCount(0,value.length())>500) return false;
        rows.addAt(lineIndex,observation("comment",wall,new JSONObject().put("text",value)
                .put("text_position",position),confidence));
        return true;
    }

    void analyze(Frame frame) throws Exception {
        if (frame.uptime<earliestUptime) return;
        if (reset) {
            reset=false; segment=UUID.randomUUID().toString(); sequence=0; head=new HeadWindow(); previousGray=null;
            previousNotice="";
        }
        CollectedRows<JSONObject> outgoing=new CollectedRows<>(128);
        int invalidComments=0, skippedOnlineLines=0;
        for (Map.Entry<String,Bitmap> entry:frame.images.entrySet()) {
            if (!active.getAsBoolean()) return;
            String kind=entry.getKey(); if (kind.equals("person")) continue;
            Text result=Tasks.await(text.process(InputImage.fromBitmap(entry.getValue(),0)));
            JSONArray lines=new JSONArray(); double confidence=1; StringBuilder raw=new StringBuilder();
            int lineIndex=0;
            for (Text.TextBlock block:result.getTextBlocks()) for (Text.Line line:block.getLines()) {
                int sourceIndex=lineIndex++;
                if (!kind.equals("comment") && lines.length()>=20) { skippedOnlineLines++; continue; }
                Rect box=line.getBoundingBox(); if (box==null || box.isEmpty()) { if (kind.equals("comment")) invalidComments++; continue; }
                Rect roi=frame.rects.get(kind);
                int left=Math.max(roi.left,roi.left+box.left),top=Math.max(roi.top,roi.top+box.top);
                int right=Math.min(roi.right,roi.left+box.right),bottom=Math.min(roi.bottom,roi.top+box.bottom);
                if (left>=right || top>=bottom) { if (kind.equals("comment")) invalidComments++; continue; }
                String value=line.getText(); if (value==null || value.trim().isEmpty()) continue;
                double score=line.getConfidence(); if (!Double.isFinite(score)||score<0||score>1) score=0;
                JSONObject position=Regions.position(left,top,right,bottom,frame.width,frame.height);
                confidence=Math.min(confidence,score);
                if (kind.equals("comment")) {
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
            if (kind.equals("platform_notice") && value.equals(previousNotice)) continue;
            Rect roi=frame.rects.get(kind);
            JSONObject content=null;
            if (!value.isEmpty()) {
                content=new JSONObject().put("ocr_lines",lines).put("text_position",Regions.position(
                        roi.left,roi.top,roi.right,roi.bottom,frame.width,frame.height));
                if (kind.equals("online_count")) content.put("raw_text",limit(value,120))
                        .put("field_semantics_confirmed",Prefs.onlineConfirmed(context));
                else content.put("text",limit(value,500)).put("region_confirmed",true).put("explicit_warning_confirmed",false);
            }
            outgoing.add(observation(kind,frame.wall,content,lines.length()==0?-1:confidence));
            if (kind.equals("platform_notice")) previousNotice=value;
        }
        boolean hadLoss=outgoing.dropped + invalidComments + skippedOnlineLines > 0;
        if (hadLoss)
            status.accept("本轮有 " + (outgoing.dropped + invalidComments + skippedOnlineLines) + " 行未上传，请检查识别区域");
        ledger.register(outgoing);
        BatchDispatch.submit(upload,outgoing,active,() -> fresh(frame),
                row -> LiveClient.call(session,"observations",row),status,hadLoss,
                (row,outcome) -> ledger.outcome(row.optString("event_id"),outcome));
    }

    private boolean fresh(Frame f) {
        return FrameFreshness.valid(active.getAsBoolean(),f.uptime,earliestUptime,SystemClock.elapsedRealtime());
    }
    private JSONObject inspectPerson(Bitmap image,Frame f) throws Exception {
        JSONObject p=new JSONObject().put("source",PERSON_SOURCE).put("coverage","sampled_person_roi")
                .put("segment_id",segment).put("sequence",sequence++).put("observed_at_ms",f.wall)
                .put("uptime_ms",f.uptime).put("quality","unknown");
        // 复用iOS的16x16灰度门禁及阈值，不从静止/黑屏推断人物不存在。
        double[] gray=new double[256]; double sum=0,change=0,edge=0; int dark=0;
        for (int i=0;i<256;i++) {
            int pixel=image.getPixel(Math.min(image.getWidth()-1,(i%16)*image.getWidth()/16),
                    Math.min(image.getHeight()-1,(i/16)*image.getHeight()/16));
            gray[i]=(.299*((pixel>>16)&255)+.587*((pixel>>8)&255)+.114*(pixel&255))/255;
            sum+=gray[i]; if (gray[i]<.04) dark++;
            if (previousGray!=null) change+=Math.abs(gray[i]-previousGray[i]);
            if (i>0) edge+=Math.abs(gray[i]-gray[i-1]);
        }
        boolean first=previousGray==null; previousGray=gray;
        if (image.getWidth()<96 || image.getHeight()<96) return p.put("quality","small_roi");
        double variance=0;
        for (double value:gray) variance+=(value-sum/256)*(value-sum/256);
        if (sum/256<.04 && dark/256.0>.98) return p.put("quality","near_black");
        if (Math.sqrt(variance/256)<.035 || edge/255<.008) return p.put("quality","low_detail");
        if (first) return p;
        if (change/256<.0008) return p.put("quality","frozen_or_still");
        if (change/256>.25) return p.put("quality","scene_change");
        InputImage input=InputImage.fromBitmap(image,0);
        if (poses==null) poses=PoseDetection.getClient(new PoseDetectorOptions.Builder()
                .setDetectorMode(PoseDetectorOptions.SINGLE_IMAGE_MODE).build());
        if (faces==null) faces=FaceDetection.getClient(new FaceDetectorOptions.Builder()
                .setPerformanceMode(FaceDetectorOptions.PERFORMANCE_MODE_FAST).build());
        Pose pose=Tasks.await(poses.process(input));
        int faceCount=Tasks.await(faces.process(input)).size();
        if (faceCount>1) return p.put("quality","multiple_people").put("person_count",Math.min(10,faceCount));
        PoseLandmark nose=pose.getPoseLandmark(PoseLandmark.NOSE),left=pose.getPoseLandmark(PoseLandmark.LEFT_SHOULDER),
                right=pose.getPoseLandmark(PoseLandmark.RIGHT_SHOULDER);
        if (nose==null || left==null || right==null) return p.put("quality","not_detected_unconfirmed");
        double confidence=Math.min(nose.getInFrameLikelihood(),Math.min(left.getInFrameLikelihood(),right.getInFrameLikelihood()));
        if (confidence<.65 || faceCount!=1) return p.put("quality","low_confidence");
        double[] points={nose.getPosition().x,nose.getPosition().y,left.getPosition().x,left.getPosition().y,
                right.getPosition().x,right.getPosition().y};
        double[] body=PersonGeometry.body(points,image.getWidth(),image.getHeight());
        if (body==null) return p.put("quality","invalid_or_small_pose");
        JSONArray vector=new JSONArray();
        for (double value:body) vector.put(value);
        status.accept("人物：采样中可见一个可靠姿态；不代表身份认证/情绪判断");
        return p.put("quality","valid").put("person_count",1).put("confidence",confidence)
                .put("body_vector",vector).put("head_ratio",vector.getDouble(1)).put("head_confidence",confidence);
    }
    private static String limit(String s,int n) { return s.substring(0,Math.min(n,s.length())); }
    @Override public void close() { upload.shutdownNow(); ledger.clear(); if (poses!=null) poses.close(); if (faces!=null) faces.close(); }
}
