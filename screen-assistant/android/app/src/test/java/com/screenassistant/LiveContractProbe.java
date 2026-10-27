package com.screenassistant;

import org.json.JSONArray;
import org.json.JSONObject;
import static org.junit.Assert.*;

/** 仅供专属CLI测试：目标限定独立127.0.0.1测试实例，不读取产品设置或设备。 */
public final class LiveContractProbe {
    public static void main(String[] args) throws Exception {
        if (args.length!=1 || !"127.0.0.1".equals(java.net.URI.create(args[0]).getHost()))
            throw new IllegalArgumentException("只允许独立回环测试服务");
        LiveClient.Session s=LiveClient.create(args[0],"Android受控协议测试，非设备场次","android-contract-fixture");
        try {
            assertEquals("android_app",LiveClient.start(s).getString("signal_source"));
            assertEquals("duplicate",LiveClient.start(s).getString("status"));
            long now=System.currentTimeMillis();
            JSONObject countBox=new JSONObject().put("x",.8).put("y",.05).put("width",.1).put("height",.05);
            JSONObject count=new JSONObject().put("event_id","android-probe-count").put("kind","online_count")
                    .put("source","android_media_projection_ocr").put("observed_at_ms",now)
                    .put("availability","available").put("coverage","ocr_visible_sample").put("reliability","known")
                    .put("confidence",.9).put("content",new JSONObject().put("raw_text","123")
                            .put("field_semantics_confirmed",true).put("text_position",countBox)
                            .put("ocr_lines",new JSONArray().put(new JSONObject().put("text","123")
                                    .put("bbox",countBox).put("confidence",.9))));
            LiveClient.call(s,"observations",count);
            JSONObject comment=new JSONObject().put("event_id","android-probe-comment").put("kind","comment")
                    .put("source","android_media_projection_ocr").put("observed_at_ms",System.currentTimeMillis())
                    .put("availability","available").put("coverage","ocr_visible_sample").put("reliability","known")
                    .put("confidence",.9).put("content",new JSONObject().put("text","受控普通评论"));
            LiveClient.call(s,"observations",comment);
            JSONObject observation=new JSONObject().put("event_id","android-probe-notice").put("kind","platform_notice")
                    .put("source","android_media_projection_ocr").put("observed_at_ms",now)
                    .put("availability","available").put("coverage","ocr_visible_sample").put("reliability","known")
                    .put("confidence",.9).put("content",new JSONObject().put("text","受控通知文字")
                            .put("region_confirmed",true).put("explicit_warning_confirmed",false));
            LiveClient.call(s,"observations",observation);
            JSONObject prompts=LiveClient.poll(s,"");
            assertFalse(prompts.getBoolean("stopped"));
            assertTrue(prompts.getJSONArray("prompts").length()>0);
            JSONObject person=new JSONObject().put("source",LiveFrameAnalyzer.PERSON_SOURCE).put("coverage","sampled_person_roi")
                    .put("segment_id","android-controlled").put("sequence",0).put("observed_at_ms",System.currentTimeMillis())
                    .put("uptime_ms",1000).put("quality","unknown");
            LiveClient.call(s,"person-observations",person);
            byte[] pcm=new byte[12800]; pcm[0]=1;
            try {
                LiveClient.call(s,"audio",AudioPackets.packet("android-probe","audioMic",0,0,pcm,-1,0,false,false));
                throw new AssertionError("测试实例应关闭ASR");
            } catch (LiveClient.HttpError e) { assertEquals(503,e.status); }
            JSONObject stopped=LiveClient.stop(s);
            assertEquals("android_app",stopped.getString("signal_source"));
            assertEquals(stopped.getLong("stopped_at_ms"),LiveClient.stop(s).getLong("stopped_at_ms"));
            JSONObject summary=LiveClient.call(s,"summary",null);
            assertEquals("android-contract-fixture",summary.getJSONObject("backstage_recap").getString("douyin_account"));
            assertTrue(RecapText.pending(summary));
            assertEquals(123,summary.getJSONObject("online_count").getJSONObject("peak").getInt("nominal_value"));
            assertEquals(1,summary.getJSONObject("online_count").getJSONObject("coverage").getInt("valid_value_samples"));
            assertTrue(RecapText.backstage(summary.getJSONObject("backstage_recap")).contains("新增粉丝：未取得"));
            JSONArray events=LiveClient.events(s); assertTrue(events.length()>0);
            assertTrue(events.getJSONObject(0).has("start_offset_ms"));
            try { LiveClient.call(s,"observations",observation); throw new AssertionError("stop后不应继续接收"); }
            catch (LiveClient.HttpError e) { assertEquals(409,e.status); }
            System.out.println("PASS: Android真实HTTP create/start幂等/typed通知/独立收件/人物DTO/音频503/stop幂等/events/summary；受控数据，非设备或ASR成功。");
        } finally { LiveClient.stop(s); }
    }
}
