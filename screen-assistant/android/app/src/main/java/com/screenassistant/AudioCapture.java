package com.screenassistant;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioPlaybackCaptureConfiguration;
import android.media.AudioRecord;
import android.media.AudioRecordingConfiguration;
import android.media.AudioTimestamp;
import android.media.MediaRecorder;
import android.media.projection.MediaProjection;
import android.os.SystemClock;
import org.json.JSONObject;
import java.util.Arrays;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/** 官方AudioRecord采集与M6上传薄接线；播放与麦克风不自动互相回退。 */
final class AudioCapture {
    private static final int RATE=16000, PACKET_BYTES=12800;
    private static final class Chunk {
        final byte[] pcm; final long sequence,frame,anchorFrame,anchorWall,created;
        Chunk(byte[] pcm,long sequence,long frame,long anchorFrame,long anchorWall) {
            this.pcm=pcm; this.sequence=sequence; this.frame=frame;
            this.anchorFrame=anchorFrame; this.anchorWall=anchorWall; created=SystemClock.elapsedRealtime();
        }
    }
    private final AudioRecord recorder;
    private final LiveClient.Session session;
    private final Consumer<String> status;
    private final String source,stream=UUID.randomUUID().toString();
    private final boolean host;
    private final ArrayBlockingQueue<Chunk> queue=new ArrayBlockingQueue<>(2);
    private volatile boolean running=true,captureDone;
    private volatile long nextSequence,frameCursor;
    private volatile boolean gap;
    final CompletableFuture<Void> completion=new CompletableFuture<>();

    AudioCapture(Context context,MediaProjection projection,LiveClient.Session session,int mode,
                 boolean host,Consumer<String> status) throws Exception {
        this.session=session; this.host=host; this.status=status; source=mode==2?"audioMic":"audioApp";
        if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO)!=PackageManager.PERMISSION_GRANTED)
            throw new SecurityException("未授予音频权限");
        int minimum=AudioRecord.getMinBufferSize(RATE,AudioFormat.CHANNEL_IN_MONO,AudioFormat.ENCODING_PCM_16BIT);
        if (minimum<=0) throw new IllegalStateException("设备不支持16k单声道PCM采集");
        AudioRecord.Builder builder=new AudioRecord.Builder().setAudioFormat(new AudioFormat.Builder()
                .setSampleRate(RATE).setChannelMask(AudioFormat.CHANNEL_IN_MONO).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                .setBufferSizeInBytes(Math.max(minimum*2,PACKET_BYTES*2));
        if (mode==2) builder.setAudioSource(MediaRecorder.AudioSource.MIC);
        else {
            int uid=context.getPackageManager().getApplicationInfo("com.ss.android.ugc.aweme",0).uid;
            builder.setAudioPlaybackCaptureConfig(new AudioPlaybackCaptureConfiguration.Builder(projection)
                    .addMatchingUid(uid).addMatchingUsage(AudioAttributes.USAGE_MEDIA)
                    .addMatchingUsage(AudioAttributes.USAGE_GAME).addMatchingUsage(AudioAttributes.USAGE_UNKNOWN).build());
        }
        recorder=builder.build();
        if (recorder.getState()!=AudioRecord.STATE_INITIALIZED) { recorder.release(); throw new IllegalStateException("音轨初始化失败"); }
        try { recorder.startRecording(); }
        catch (RuntimeException e) { recorder.release(); throw e; }
        if (recorder.getRecordingState()!=AudioRecord.RECORDSTATE_RECORDING) { recorder.release(); throw new IllegalStateException("音轨未进入录制状态"); }
        new Thread(this::capture,"live-audio-record").start();
        new Thread(this::upload,"live-audio-upload").start();
        status.accept("音频：已请求"+(mode==2?"麦克风（同机并发可能被静音）":"抖音播放捕获（不等于主播上行声）")+"，等待有效数据");
    }

    private void capture() {
        byte[] packet=new byte[PACKET_BYTES]; int filled=0; long silentSince=0;
        try {
            while (running) {
                int n=recorder.read(packet,filled,packet.length-filled,AudioRecord.READ_BLOCKING);
                if (n<=0 || n%2!=0) { if (running) status.accept("音频读取中断，继续其他取数"); gap=true; break; }
                filled+=n;
                if (filled<packet.length) continue;
                boolean nonzero=false;
                for (byte value:packet) if (value!=0) { nonzero=true; break; }
                AudioRecordingConfiguration config=recorder.getActiveRecordingConfiguration();
                boolean silenced=config!=null && config.isClientSilenced();
                long sequence=nextSequence++,frame=frameCursor; frameCursor+=filled/2;
                if (silenced || !nonzero) {
                    gap=true;
                    if (silentSince==0) silentSince=SystemClock.elapsedRealtime();
                    if (silenced || SystemClock.elapsedRealtime()-silentSince>2000)
                        status.accept(silenced?"音频：系统已屏蔽本App音轨；未上传静音冒充人声"
                                :"音频：持续未取得非零样本；可能静音/不允许捕获，不能证明主播声可用");
                } else {
                    silentSince=0;
                    AudioTimestamp timestamp=new AudioTimestamp(); long anchorFrame=-1,anchorWall=0;
                    if (recorder.getTimestamp(timestamp,AudioTimestamp.TIMEBASE_MONOTONIC)==AudioRecord.SUCCESS) {
                        anchorFrame=timestamp.framePosition;
                        anchorWall=System.currentTimeMillis()-(System.nanoTime()-timestamp.nanoTime)/1000000;
                    }
                    Chunk chunk=new Chunk(Arrays.copyOf(packet,filled),sequence,frame,anchorFrame,anchorWall);
                    if (!queue.offer(chunk)) { queue.poll(); gap=true; queue.offer(chunk); }
                }
                filled=0;
            }
        } catch (RuntimeException e) { status.accept("音频：采集不可用（"+e.getClass().getSimpleName()+"），屏幕链路继续"); gap=true; }
        finally {
            // 未满400ms尾段不伪造补零；标记断档，服务端stop收尾已收到数据。
            if (filled>0) gap=true;
            running=false;
            try { recorder.stop(); } catch (RuntimeException ignored) { }
            recorder.release(); captureDone=true;
        }
    }

    private void upload() {
        long previous=-1; boolean accepted=false;
        try {
            while (!captureDone || !queue.isEmpty()) {
                Chunk c=queue.poll(200,TimeUnit.MILLISECONDS); if (c==null) continue;
                if (SystemClock.elapsedRealtime()-c.created>1500) { gap=true; continue; }
                boolean discontinuity=gap || c.sequence!=previous+1; gap=false;
                try {
                    JSONObject reply=LiveClient.call(session,"audio",AudioPackets.packet(stream,source,c.sequence,c.frame,c.pcm,
                            c.anchorFrame,c.anchorWall,host,discontinuity));
                    accepted=true; previous=c.sequence;
                    status.accept("音频：PCM已送达；ASR/主播来源与抖音同机收音仍待实际核对");
                } catch (Exception e) {
                    gap=true; status.accept("音频上传未确认："+e.getMessage()+"；其他取数继续");
                    if (e instanceof LiveClient.HttpError) {
                        int code=((LiveClient.HttpError)e).status;
                        if (code==403 || code==409 || code==422 || code==503) { stop(); queue.clear(); break; }
                    }
                }
            }
            if (accepted) {
                LiveClient.call(session,"audio",AudioPackets.packet(stream,source,nextSequence,frameCursor,new byte[0],-1,0,host,gap));
            }
            completion.complete(null);
        } catch (Exception e) {
            status.accept("音频收尾未确认："+e.getMessage()); completion.completeExceptionally(e);
        }
    }

    void stop() {
        running=false;
        try { recorder.stop(); } catch (RuntimeException ignored) { }
    }
}
