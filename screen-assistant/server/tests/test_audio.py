"""M6：真实PCM字节；VAD/ASR测试替身不代表识别效果。"""
import asyncio
import base64
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from screen_assistant_server.app import create_app
from screen_assistant_server.audio import AudioPacket, AudioService
from screen_assistant_server.asr import FunASRConnection
from test_live import Clock, create, settings, wait_for_fusion


def packet(seq=0, *, source="audioMic", start=0, frames=9600, final=False,
           stream="capture-1", **changes):
    value = dict(stream_id=stream, source=source, sequence=seq, final=final,
                 gap_before=[], pcm_base64=base64.b64encode(b"\x01\x00" * frames).decode(),
                 sample_rate=16000, channels=1, sample_format="s16le",
                 original_format=dict(sample_rate=48000, channels=2,
                                      sample_format="f32le", interleaved=False),
                 spans=[dict(sequence=seq, pts=dict(value=start*48, timescale=48000, epoch=0),
                             frames=frames*3)],
                 clock_anchor=dict(pts=dict(value=0, timescale=48000, epoch=0),
                                   observed_at_ms=1_790_000_000_000),
                 host_confirmed=True)
    value.update(changes)
    return value


class VadFixture:
    def __init__(self, regions=None):
        self.regions = regions

    def speech(self, pcm):
        return self.regions if self.regions is not None else [(0, len(pcm)//2)]


class ASRFixture:
    async def transcribe(self, pcm, segment_id, emit):
        # 中间结果→末段修订；输入必须来自实际解码的PCM。
        assert pcm and len(pcm) % 2 == 0
        await emit("欢迎大家", False, "online")
        await emit("欢迎大家一起聊聊今天的生活故事和自己的想法" * 3, True, "online")


def enabled_settings(path):
    return replace(settings(path), audio_enabled=True, funasr_url="ws://127.0.0.1:10095",
                   silero_model_path="fixture-only", funasr_model_path="fixture-only")


def fixture_app(tmp_path, clock, regions=None):
    app = create_app(enabled_settings(tmp_path / "live.sqlite3"), live_clock=clock)
    return app, lambda: app.state.audio.configure_for_test(
        lambda: VadFixture(regions), ASRFixture())


def test_pcm_contract_rejects_duration_format_and_corrupt_payload():
    assert len(AudioPacket.model_validate(packet()).pcm()) == 19200
    for change in [dict(sample_rate=48000), dict(channels=2), dict(sample_format="f32le"),
                   dict(pcm_base64="bad!"), dict(pcm_base64=base64.b64encode(b"odd").decode()),
                   dict(spans=[]), dict(frames=9601)]:
        with pytest.raises(ValueError):
            AudioPacket.model_validate(packet(**change))


def test_audio_disabled_auth_size_gap_and_stop(tmp_path):
    clock = Clock()
    app = create_app(settings(tmp_path / "disabled.sqlite3"), live_clock=clock)
    with TestClient(app) as client:
        session, headers = create(client)
        url = f"/v1/live/sessions/{session['session_id']}/audio"
        assert client.post(url, json=packet()).status_code == 403
        assert client.post(url, headers=headers, json=packet()).status_code == 503
    app, configure = fixture_app(tmp_path, clock)
    with TestClient(app) as client:
        configure()
        session, headers = create(client)
        url = f"/v1/live/sessions/{session['session_id']}"
        assert client.post(url + "/audio", headers=headers, content=b"x"*65537).status_code == 413
        clock.now += 10000
        first = client.post(url + "/audio", headers=headers, json=packet()).json()
        assert first["buffered_ms"] == 600
        gap = client.post(url + "/audio", headers=headers,
                          json=packet(2, start=1200)).json()
        assert "sequence_gap" in gap["gaps"]
        assert gap["continuity_id"] != first["continuity_id"]
        assert gap["buffered_ms"] == 600
        assert client.post(url + "/audio", headers=headers, json=packet(1, start=600)).status_code == 409
        other = client.post(url + "/audio", headers=headers,
                            json=packet(source="audioApp", host_confirmed=False)).json()
        assert other["continuity_id"] != gap["continuity_id"]
        client.post(url + "/stop", headers=headers)
        assert client.post(url + "/audio", headers=headers, json=packet(3, start=1800)).status_code == 409
        assert not app.state.audio.tracks


def test_internal_gap_resets_pending_window_before_next_packet(tmp_path):
    clock = Clock()
    app, configure = fixture_app(tmp_path, clock)
    with TestClient(app) as client:
        configure()
        session, headers = create(client)
        clock.now += 10000
        url = f"/v1/live/sessions/{session['session_id']}/audio"

        first = client.post(url, headers=headers, json=packet(0, start=0)).json()
        assert first["buffered_ms"] == 600

        internal_gap = packet(1, start=600, spans=[
            dict(sequence=1, pts=dict(value=600 * 48, timescale=48000, epoch=0),
                 frames=14400),
            dict(sequence=3, pts=dict(value=1200 * 48, timescale=48000, epoch=0),
                 frames=14400),
        ])
        gap_response = client.post(url, headers=headers, json=internal_gap)
        assert gap_response.status_code == 200, gap_response.text
        gap = gap_response.json()
        assert "original_sequence_gap" in gap["gaps"]
        assert "pts_gap" in gap["gaps"]
        assert gap["continuity_id"] != first["continuity_id"]
        assert gap["buffered_ms"] == 0

        next_packet = packet(2, start=1500, spans=[
            dict(sequence=4, pts=dict(value=1500 * 48, timescale=48000, epoch=0),
                 frames=28800),
        ])
        next_result = client.post(url, headers=headers, json=next_packet).json()
        assert next_result["continuity_id"] == gap["continuity_id"]
        assert next_result["buffered_ms"] == 600


def test_final_revisions_enter_m4_once_then_short_prompt_and_record(tmp_path):
    clock = Clock()
    app, configure = fixture_app(tmp_path, clock)
    with TestClient(app) as client:
        configure()
        session, headers = create(client)
        url = f"/v1/live/sessions/{session['session_id']}"
        clock.now += 10000
        results = []
        for seq in range(10):
            response = client.post(url + "/audio", headers=headers,
                                   json=packet(seq, start=seq*600, final=seq in (2, 5, 9)))
            assert response.status_code == 200, response.text
            results.extend(response.json()["transcripts"])
        assert len(results) == 6
        assert [row["content"]["is_final"] for row in results] == [False, True]*3
        assert results[1]["content"]["supersedes_event_id"] == results[0]["event_id"]
        assert results[1]["content"]["revision"] == 1
        windows = app.state.live.store.fusion_windows[session['session_id']]
        finals = [row for row in windows if row['kind'] == 'transcript' and row['content']['is_final']]
        assert len(finals) == 3
        wait_for_fusion(client)
        events = client.get(url + "/events", headers=headers).json()["events"]
        assert len(events) == 1
        assert "稍快" in events[0]["suggestion"] and len(events[0]["suggestion"]) <= 80
        assert events[0]["time_basis"] == "pts_arrival_approx"
        assert events[0]["sources"][0]["audio_evidence"]["source"] == "audioMic"
        assert len(client.get(url + "/prompts", headers=headers).json()["prompts"]) == 1
        # PCM与完整转写不进SQLite，事件仅存必要原话证据。
        assert "pcm_base64" not in "\n".join(app.state.live.store.db.iterdump())


def test_android_app_pcm_track_uses_android_asr_source(tmp_path):
    clock = Clock()
    app, configure = fixture_app(tmp_path, clock)
    with TestClient(app) as client:
        configure()
        session, headers = create(client, douyin_account="android-audio-account")
        url = f"/v1/live/sessions/{session['session_id']}"
        started = client.post(url + "/start", headers=headers,
                              json={"source": "android_app"})
        assert started.status_code == 200, started.text
        clock.now += 10000
        response = client.post(url + "/audio", headers=headers,
                               json=packet(source="audioMic", final=True))
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "accepted"
        assert [row["source"] for row in response.json()["transcripts"]] == [
            "android_audioMic_asr", "android_audioMic_asr"
        ]


@pytest.mark.parametrize("regions,changes", [([], {}), (None, {"host_confirmed": False}),
    (None, {"clock_anchor": None})])
def test_no_valid_host_or_clock_or_vad_is_quiet(tmp_path, regions, changes):
    clock = Clock()
    app, configure = fixture_app(tmp_path, clock, regions)
    with TestClient(app) as client:
        configure()
        session, headers = create(client)
        url = f"/v1/live/sessions/{session['session_id']}"
        clock.now += 10000
        for seq in range(10):
            response = client.post(url + "/audio", headers=headers,
                                   json=packet(seq, start=seq*600, final=seq in (2,5,9), **changes))
            assert response.status_code == 200, response.text
        assert client.get(url + "/events", headers=headers).json()["events"] == []


def test_funasr_wire_partial_final_and_end_ack():
    class Socket:
        def __init__(self):
            self.sent = []
            self.messages = asyncio.Queue()
        async def send(self, value):
            self.sent.append(value)
            if isinstance(value, bytes) and self.messages.empty():
                self.messages.put_nowait('{"mode":"online","text":"你好","is_final":false}')
            if isinstance(value, str) and json.loads(value).get('is_speaking') is False:
                self.messages.put_nowait('{"mode":"online","text":"世界","is_final":true}')
                self.messages.put_nowait('{"mode":"online","is_end":true,"is_final":true}')
        async def recv(self):
            return await self.messages.get()
        async def close(self):
            pass
    async def run():
        socket = Socket()
        async def connect(*args, **kwargs):
            return socket
        output = []
        async def emit(*args):
            output.append(args)
        engine = FunASRConnection("ws://127.0.0.1:10095", connect=connect, pace=False)
        await engine.transcribe(b"\x01\x00"*512, "seg", emit)
        assert output[-1] == ("你好世界", True, "online")
        assert any(isinstance(item, bytes) for item in socket.sent)
        assert '"is_speaking": false' in socket.sent[-1]
    asyncio.run(run())


def test_background_window_is_bounded_and_marks_backpressure(tmp_path):
    async def run():
        from screen_assistant_server.live import LiveService
        clock = Clock()
        live = LiveService(str(tmp_path / 'bounded.sqlite3'), clock)
        session = await live.run('create', '')
        clock.now += 30000
        audio = AudioService(enabled_settings(tmp_path / 'unused.sqlite3'), live)
        audio.configure_for_test(lambda: VadFixture(), ASRFixture())
        entered, release = asyncio.Event(), asyncio.Event()
        class SlowASR:
            async def transcribe(self, pcm, segment_id, emit):
                entered.set()
                await release.wait()
        audio.asr = SlowASR()
        try:
            for seq in range(10):
                result = await audio.ingest(session['session_id'], session['session_token'],
                                            AudioPacket.model_validate(packet(seq, start=seq*600)))
            await asyncio.wait_for(entered.wait(), 2)
            first = result['continuity_id']
            for seq in range(10,20):
                result = await audio.ingest(session['session_id'], session['session_token'],
                                            AudioPacket.model_validate(packet(seq, start=seq*600)))
            assert 'analysis_backpressure' in result['gaps']
            assert result['continuity_id'] != first
            assert sum(len(t.pending) for t in audio.tracks.values()) <= 192000
            assert len(audio.tracks) == 1
        finally:
            await audio.close()
            await live.run('close')
    asyncio.run(run())


def test_stop_flushes_tail_and_missing_engine_is_honest(tmp_path):
    clock = Clock()
    app, configure = fixture_app(tmp_path, clock)
    with TestClient(app) as client:
        configure()
        session, headers = create(client)
        clock.now += 10000
        url = f"/v1/live/sessions/{session['session_id']}"
        client.post(url+'/audio', headers=headers, json=packet())
        result = client.post(url+'/stop', headers=headers)
        assert result.status_code == 200
        assert result.json()['audio']['transcript_count'] == 2
        assert not app.state.audio.tracks
    app = create_app(enabled_settings(tmp_path / 'missing.sqlite3'), live_clock=clock)
    with TestClient(app) as client:
        session, headers = create(client)
        clock.now += 1000
        url = f"/v1/live/sessions/{session['session_id']}/audio"
        response = client.post(url, headers=headers, json=packet(clock_anchor=None))
        assert response.status_code == 503
        assert not app.state.audio.tracks


@pytest.mark.parametrize("pending_tail,before_finish,replace_job", [
    (False, False, False), (True, False, False), (False, True, False), (False, True, True),
])
def test_stop_fuses_transcripts_from_inflight_job_without_flush_output(tmp_path, pending_tail, before_finish, replace_job):
    import httpx

    async def run():
        clock = Clock()
        app, _ = fixture_app(tmp_path, clock)
        entered, release, stop_gate = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class WindowVAD:
            def speech(self, pcm):
                if len(pcm) == 192000:
                    return [(0, 16000), (16000, 32000), (32000, 96000)]
                return [(0, len(pcm) // 2)]

        class DelayedASR:
            async def transcribe(self, pcm, segment_id, emit):
                entered.set()
                await release.wait()
                await emit("欢迎大家一起聊聊今天的生活故事和自己的想法" * 3, True, "online")

        async with app.router.lifespan_context(app):
            app.state.audio.configure_for_test(WindowVAD, DelayedASR())
            if before_finish:
                original_stop_fusion = app.state.live.stop_fusion
                async def wait_before_finish(sid):
                    await original_stop_fusion(sid)
                    await stop_gate.wait()
                app.state.live.stop_fusion = wait_before_finish
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
                session = (await client.post("/v1/live/sessions", json={})).json()
                sid = session["session_id"]
                headers = {"X-Live-Session-Token": session["session_token"]}
                url = f"/v1/live/sessions/{sid}"
                clock.now += 10_000
                for seq in range(10):
                    response = await client.post(url + "/audio", headers=headers,
                                                 json=packet(seq, start=seq * 600))
                    assert response.status_code == 200, response.text
                await asyncio.wait_for(entered.wait(), 2)
                if pending_tail:
                    tail = await client.post(url + "/audio", headers=headers, json=packet(10, start=6000))
                    assert tail.status_code == 200, tail.text
                stopping = asyncio.create_task(client.post(url + "/stop", headers=headers))
                try:
                    async with asyncio.timeout(2):
                        while sid not in app.state.live.fusion_stopping:
                            await asyncio.sleep(0)
                    assert (await client.post(url + "/stop", headers=headers)).status_code == 409
                    release.set()
                    if before_finish:
                        track = app.state.audio.tracks[(sid, "audioMic")]
                        await asyncio.wait_for(track.job, 2)
                        if replace_job:
                            clock.now += 6_000
                            for seq in range(10, 20):
                                next_packet = await client.post(url + "/audio", headers=headers,
                                    json=packet(seq, start=seq * 600))
                                assert next_packet.status_code == 200, next_packet.text
                            await asyncio.wait_for(track.job, 2)
                        stop_gate.set()
                    response = await asyncio.wait_for(stopping, 3)
                    assert response.status_code == 200, response.text
                    if pending_tail:
                        assert response.json()["audio"]["transcript_count"] == 1
                    else:
                        assert "audio" not in response.json()
                    assert app.state.live.store.db.execute(
                        "SELECT COUNT(*) FROM live_observations WHERE session_id=?",
                        (sid,),
                    ).fetchone()[0] == (6 if replace_job else 4 if pending_tail else 3)
                    events = (await client.get(url + "/events", headers=headers)).json()["events"]
                    assert len(events) == 1
                    assert events[0]["phenomenon"] == "近期连续主播转写的文字率偏快"
                    repeated = await client.post(url + "/stop", headers=headers)
                    assert repeated.status_code == 200
                    assert repeated.json()["stopped_at_ms"] == response.json()["stopped_at_ms"]
                finally:
                    release.set()
                    stop_gate.set()
                    if not stopping.done():
                        stopping.cancel()
                    await asyncio.gather(stopping, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["direct_ingest", "failed", "partial_failure", "history", "cancel_after_store", "pre_gate"])
def test_stop_only_fuses_new_inflight_audio(tmp_path, mode):
    import httpx

    async def run():
        clock = Clock()
        app, _ = fixture_app(tmp_path, clock)
        entered, release, stored, finish_write = asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()

        class ControlledASR:
            async def transcribe(self, pcm, segment_id, emit):
                entered.set()
                if mode == "pre_gate":
                    await emit("欢迎大家一起聊聊今天的生活故事", True, "online")
                    stored.set()
                    await release.wait()
                    return
                if mode not in ("history", "cancel_after_store"):
                    await release.wait()
                if mode == "failed":
                    raise RuntimeError("controlled ASR failure")
                await emit("欢迎大家一起聊聊今天的生活故事", True, "online")
                if mode == "partial_failure":
                    raise RuntimeError("controlled failure after persistence")

        async with app.router.lifespan_context(app):
            app.state.audio.configure_for_test(VadFixture, ControlledASR())
            app.state.live.schedule_fusion = lambda *args: False
            if mode == "cancel_after_store":
                original_run = app.state.live.run
                async def controlled_run(method, *args):
                    result = await original_run(method, *args)
                    if method == "add_observation" and args[2]["kind"] == "transcript":
                        stored.set()
                        await finish_write.wait()
                    return result
                app.state.live.run = controlled_run
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
                session = (await client.post("/v1/live/sessions", json={})).json()
                sid = session["session_id"]
                headers = {"X-Live-Session-Token": session["session_token"]}
                url = f"/v1/live/sessions/{sid}"
                clock.now += 10_000
                assert (await client.post(url + "/audio", headers=headers, json=packet())).status_code == 200
                end = asyncio.create_task(client.post(url + "/audio", headers=headers,
                    json=packet(1, pcm_base64="", spans=[], final=True)))
                await asyncio.wait_for(entered.wait(), 2)
                if mode in ("cancel_after_store", "pre_gate"):
                    await asyncio.wait_for(stored.wait(), 2)
                if mode == "history":
                    assert (await asyncio.wait_for(end, 2)).status_code == 200
                for index in range(2):
                    comment = {"event_id": f"question-{index}", "input_mode": "live_observation",
                               "source": "ios_replaykit_ocr", "coverage": "ocr_visible_sample",
                               "reliability": "known", "text": "主播你是哪里人？"}
                    assert (await client.post(url + "/comments", headers=headers, json=comment)).status_code == 200
                stopping = asyncio.create_task(client.post(url + "/stop", headers=headers))
                try:
                    if mode != "history":
                        async with asyncio.timeout(2):
                            while sid not in app.state.live.fusion_stopping:
                                await asyncio.sleep(0)
                    if mode == "cancel_after_store":
                        end.cancel()
                        finish_write.set()
                    release.set()
                    result = await asyncio.wait_for(stopping, 3)
                    assert result.status_code == 200
                    await asyncio.gather(end, return_exceptions=True)
                    events = (await client.get(url + "/events", headers=headers)).json()["events"]
                    if mode in ("direct_ingest", "partial_failure", "cancel_after_store", "pre_gate"):
                        assert len(events) == 1
                        assert "重复" in events[0]["phenomenon"]
                    else:
                        assert events == []
                finally:
                    release.set()
                    finish_write.set()
                    for task in (end, stopping):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(end, stopping, return_exceptions=True)
    asyncio.run(run())


def test_real_websocket_transport_with_protocol_fixture():
    """真实loopback WebSocket传输；服务端是协议fixture，绝非真实ASR。"""
    from websockets.asyncio.server import serve
    async def run():
        received = bytearray()
        async def handler(socket):
            config = json.loads(await socket.recv())
            assert config['audio_fs'] == 16000 and config['wav_format'] == 'pcm'
            async for message in socket:
                if isinstance(message, bytes):
                    received.extend(message)
                    await socket.send(json.dumps(dict(mode='2pass-online', text='我在', is_final=True)))
                elif json.loads(message).get('is_speaking') is False:
                    await socket.send(json.dumps(dict(mode='2pass-offline', text='我来自上海', is_final=True)))
                    await socket.send(json.dumps(dict(mode='2pass', is_end=True, is_final=True)))
                    break
        async with serve(handler, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            engine = FunASRConnection(f'ws://127.0.0.1:{port}', mode='2pass', pace=False)
            output = []
            async def emit(*args):
                output.append(args)
            pcm = b'\x02\x00'*512
            await engine.transcribe(pcm, 'fixture-segment', emit)
            assert bytes(received) == pcm
            assert output == [('我在', False, '2pass-online'), ('我来自上海', False, '2pass-offline'),
                              ('我来自上海', True, '2pass-offline')]
    asyncio.run(run())


def test_disconnect_never_promotes_partial_to_final():
    async def run():
        class Socket:
            async def send(self, value):
                pass
            async def recv(self):
                raise ConnectionError('fixture disconnection')
            async def close(self):
                pass
        async def connect(*args, **kwargs):
            return Socket()
        output = []
        async def emit(*args):
            output.append(args)
        with pytest.raises(ConnectionError):
            await FunASRConnection('ws://127.0.0.1:1', connect=connect, pace=False).transcribe(
                b'\x01\x00'*512, 'fixture', emit)
        assert not any(final for _, final, _ in output)
    asyncio.run(run())


def test_empty_end_packet_flushes_existing_pcm_and_retired_capture_stays_out(tmp_path):
    clock = Clock()
    app, configure = fixture_app(tmp_path, clock)
    with TestClient(app) as client:
        configure()
        session, headers = create(client)
        clock.now += 20000
        url = f"/v1/live/sessions/{session['session_id']}/audio"
        client.post(url, headers=headers, json=packet())
        end = packet(1, pcm_base64='', spans=[], final=True)
        response = client.post(url, headers=headers, json=end)
        assert response.status_code == 200, response.text
        assert len(response.json()['transcripts']) == 2
        assert response.json()['buffered_ms'] == 0
        newer = packet(stream='capture-2', start=600)
        assert client.post(url, headers=headers, json=newer).status_code == 200
        assert client.post(url, headers=headers, json=end).status_code == 409


@pytest.mark.parametrize("scheduled, expected", [(True, "scheduled"), (False, "analysis_busy")])
def test_audio_reports_transcript_fusion_schedule(tmp_path, scheduled, expected):
    clock = Clock()
    app, configure = fixture_app(tmp_path, clock)
    with TestClient(app) as client:
        configure()
        app.state.live.schedule_fusion = lambda sid, token, request: scheduled
        session, headers = create(client)
        clock.now += 20_000
        url = f"/v1/live/sessions/{session['session_id']}/audio"
        assert client.post(url, headers=headers, json=packet()).status_code == 200
        response = client.post(url, headers=headers,
                               json=packet(1, pcm_base64='', spans=[], final=True))
        assert response.status_code == 200
        result = response.json()
        assert result['status'] == 'accepted' and len(result['transcripts']) == 2
        assert result['fusion_schedule'] == expected
        assert app.state.live.store.db.execute(
            'SELECT COUNT(*) FROM live_observations WHERE session_id=?', (session['session_id'],)
        ).fetchone()[0] == 2


def test_background_audio_busy_is_durable_and_session_scoped(tmp_path):
    clock = Clock()
    app, configure = fixture_app(tmp_path, clock)
    with TestClient(app) as client:
        configure()
        app.state.live.schedule_fusion = lambda sid, token, request: False
        session, headers = create(client)
        other, other_headers = create(client)
        url = f"/v1/live/sessions/{session['session_id']}"
        clock.now += 10_000
        for seq in range(10):
            response = client.post(url + "/audio", headers=headers, json=packet(seq, start=seq * 600))
            assert response.status_code == 200
        async def wait_job():
            track = app.state.audio.tracks[(session['session_id'], 'audioMic')]
            await asyncio.wait_for(track.job, 2)
        client.portal.call(wait_job)
        summary = client.get(url + "/summary", headers=headers).json()
        assert summary['audio_fusion']['background_analysis_busy_count'] == 1
        other_url = f"/v1/live/sessions/{other['session_id']}"
        assert client.get(other_url + "/summary", headers=other_headers).json()['audio_fusion']['background_analysis_busy_count'] == 0
        assert client.get(url + "/summary", headers=other_headers).status_code == 403
        assert client.post(url + "/stop", headers=headers).status_code == 200
    reopened = create_app(settings(tmp_path / "live.sqlite3"), live_clock=clock)
    with TestClient(reopened) as client:
        assert client.get(url + "/summary", headers=headers).json()['audio_fusion']['background_analysis_busy_count'] == 1


def test_background_busy_record_survives_cancellation(tmp_path):
    import httpx
    async def run():
        clock = Clock()
        app = create_app(settings(tmp_path / "cancel.sqlite3"), live_clock=clock)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
                session = (await client.post("/v1/live/sessions", json={})).json()
                sid, token = session['session_id'], session['session_token']
                app.state.live.schedule_fusion = lambda *args: False
                await app.state.live.lock.acquire()
                try:
                    task = asyncio.create_task(app.state.audio.deliver(sid, token, True))
                    await asyncio.sleep(0)
                    task.cancel()
                finally:
                    app.state.live.lock.release()
                result = await asyncio.gather(task, return_exceptions=True)
                assert isinstance(result[0], asyncio.CancelledError)
                summary = await client.get(f"/v1/live/sessions/{sid}/summary",
                                           headers={"X-Live-Session-Token": token})
                assert summary.json()['audio_fusion']['background_analysis_busy_count'] == 1
    asyncio.run(run())


def test_abandoned_capture_pcm_expires_without_a_new_request(tmp_path):
    async def run():
        from screen_assistant_server.live import LiveService
        clock = Clock()
        live = LiveService(str(tmp_path/'expire.sqlite3'), clock)
        session = await live.run('create', '')
        clock.now += 1000
        audio = AudioService(enabled_settings(tmp_path/'unused'), live)
        audio.configure_for_test(lambda: VadFixture(), ASRFixture())
        try:
            await audio.ingest(session['session_id'], session['session_token'],
                               AudioPacket.model_validate(packet()))
            abandoned = next(iter(audio.tracks.values()))
            assert abandoned.pending
            abandoned.touched -= 16
            await audio.expire()
            assert not audio.tracks and not abandoned.pending
        finally:
            await audio.close()
            await live.run('close')
    asyncio.run(run())


def test_explicit_gap_end_cancels_old_analysis_without_using_it(tmp_path):
    async def run():
        from screen_assistant_server.live import LiveService
        clock = Clock()
        live = LiveService(str(tmp_path/'gap-end.sqlite3'), clock)
        session = await live.run('create', '')
        clock.now += 10000
        audio = AudioService(enabled_settings(tmp_path/'unused'), live)
        entered = asyncio.Event()
        class SlowASR:
            async def transcribe(self, pcm, segment_id, emit):
                entered.set()
                await asyncio.Event().wait()
        audio.configure_for_test(lambda: VadFixture(), SlowASR())
        try:
            for seq in range(10):
                await audio.ingest(session['session_id'], session['session_token'],
                                   AudioPacket.model_validate(packet(seq, start=seq*600)))
            await asyncio.wait_for(entered.wait(), 2)
            result = await audio.ingest(session['session_id'], session['session_token'],
                AudioPacket.model_validate(packet(10, pcm_base64='', spans=[], final=True,
                                                  gap_before=['capture_stopped_with_unsent_audio'])))
            assert result['transcripts'] == [] and result['buffered_ms'] == 0
            assert result['gaps'] == ['capture_stopped_with_unsent_audio']
        finally:
            await audio.close()
            await live.run('close')
    asyncio.run(run())


def test_failed_asr_final_is_503_and_never_becomes_a_prompt(tmp_path):
    clock = Clock()
    app, configure = fixture_app(tmp_path, clock)
    class FailingASR:
        async def transcribe(self, pcm, segment_id, emit):
            await emit('中间结果', False, 'online')
            raise ConnectionError('fixture failed')
    with TestClient(app) as client:
        configure()
        app.state.audio.asr = FailingASR()
        session, headers = create(client)
        clock.now += 2000
        url = f"/v1/live/sessions/{session['session_id']}"
        response = client.post(url+'/audio', headers=headers, json=packet(final=True))
        assert response.status_code == 503
        assert client.get(url+'/events', headers=headers).json()['events'] == []
