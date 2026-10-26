package com.screenassistant;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import java.util.Base64;

/** 既有M6 AudioPacket的16k mono s16le适配，无端侧VAD/ASR或重采样。 */
final class AudioPackets {
    private static JSONObject pts(long frame) throws JSONException {
        return new JSONObject().put("value",frame).put("timescale",16000).put("epoch",0);
    }
    static JSONObject packet(String stream,String source,long sequence,long frame,byte[] pcm,
                             long anchorFrame,long anchorWall,boolean host,boolean gap) throws JSONException {
        if (pcm.length>19200 || pcm.length%2!=0 || sequence<0 || frame<0) throw new IllegalArgumentException("PCM包无效");
        JSONArray spans=new JSONArray();
        if (pcm.length>0) spans.put(new JSONObject().put("sequence",sequence).put("pts",pts(frame)).put("frames",pcm.length/2));
        JSONObject p=new JSONObject().put("stream_id",stream).put("source",source).put("sequence",sequence)
                .put("sample_rate",16000).put("channels",1).put("sample_format","s16le")
                .put("pcm_base64",Base64.getEncoder().encodeToString(pcm))
                .put("original_format",new JSONObject().put("sample_rate",16000).put("channels",1)
                        .put("sample_format","s16le").put("interleaved",true))
                .put("spans",spans).put("host_confirmed",host).put("final",pcm.length==0)
                .put("gap_before",gap?new JSONArray().put("capture_gap"):new JSONArray());
        if (anchorFrame>=0) p.put("clock_anchor",new JSONObject().put("pts",pts(anchorFrame)).put("observed_at_ms",anchorWall));
        return p;
    }
}
