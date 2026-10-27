"""显式本地模型的真实CPU验证；无路径时跳过，不以替身证明识别。"""
import base64
import hashlib
import os
import wave
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from screen_assistant_server.app import create_app
from test_live import Clock, create, settings


def test_real_silero_sdk_pcm_final_silence_stop_reopen(tmp_path):
    model = os.getenv("M6_REAL_FUNASR_MODEL_PATH")
    vad = os.getenv("M6_REAL_SILERO_ONNX_PATH")
    if not model or not vad:
        pytest.skip("requires explicitly prepared local FunASR/Silero models")
    wav = Path(model) / "example/asr_example.wav"
    assert hashlib.sha256(wav.read_bytes()).hexdigest() == (
        "a1bd32dc78493c123f9625a66deee562aed2895f53fbc39f2cca3be7e6f4f20f")
    with wave.open(str(wav), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
        pcm = audio.readframes(audio.getnframes())
    clock = Clock()
    config = replace(settings(tmp_path / "real.sqlite3"), audio_enabled=True,
                     redis_url=os.environ["TEST_REDIS_URL"],
                     funasr_model_path=model, silero_model_path=vad)
    app = create_app(config, live_clock=clock)
    observed = []

    def feed(client, session, headers, data, *, final=True, confirmed=True):
        # 既有接口的audioApp槽承载合法WAV测试，不代表手机ReplayKit来源。
        anchor = clock.now
        clock.now += round(len(data) / 32) + 1000
        result = []
        for seq, offset in enumerate(range(0, len(data), 19200)):
            chunk = data[offset:offset+19200]
            packet = dict(
                stream_id="official-wav-fixture", source="audioApp", sequence=seq,
                sample_rate=16000, channels=1, sample_format="s16le",
                original_format=dict(sample_rate=16000, channels=1,
                                     sample_format="s16le", interleaved=True),
                pcm_base64=base64.b64encode(chunk).decode(), gap_before=[],
                spans=[dict(sequence=seq, pts=dict(value=offset//2, timescale=16000, epoch=0),
                            frames=len(chunk)//2)],
                clock_anchor=dict(pts=dict(value=0, timescale=16000, epoch=0),
                                  observed_at_ms=anchor),
                host_confirmed=confirmed, final=final and offset+19200 >= len(data))
            response = client.post(f"/v1/live/sessions/{session['session_id']}/audio",
                                   headers=headers, json=packet)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["gaps"] == []
            assert body["buffered_ms"] <= 6000
            result.extend(body["transcripts"])
        return result

    def quiet(client, session, headers):
        url = f"/v1/live/sessions/{session['session_id']}"
        assert client.get(url+"/events", headers=headers).json()["events"] == []
        assert client.get(url+"/prompts", headers=headers).json()["prompts"] == []

    def stop(client, session, headers):
        url = f"/v1/live/sessions/{session['session_id']}"
        response = client.post(url+"/stop", headers=headers)
        assert response.status_code == 200, response.text
        result = response.json().get("audio", {"transcript_count": 0, "incomplete": False})
        assert result["incomplete"] is False
        assert not app.state.audio.tracks
        return result

    with TestClient(app) as client:
        original_run = app.state.live.run

        async def trace(operation, *args):
            result = await original_run(operation, *args)
            if operation == "add_observation":
                observed.append((args[0], args[2]))
            return result

        app.state.live.run = trace
        session, headers = create(client)
        rows = feed(client, session, headers, pcm)
        finals = [row for row in rows if row["content"]["is_final"]]
        assert len(finals) == 1 and len(rows) > 1
        text = finals[0]["content"]["text"]
        assert text == "欢迎大家来体验达摩院推出的语音识别模型"
        for revision, row in enumerate(rows):
            assert row["content"]["revision"] == revision
            assert row["content"]["supersedes_event_id"] == (
                rows[revision-1]["event_id"] if revision else None)
            assert row["content"]["audio_evidence"]["time_basis"] == "pts_arrival_approx"
        windows = app.state.live.store.fusion_windows[session["session_id"]]
        retained = [row for row in windows if row["kind"] == "transcript" and row["content"]["is_final"]]
        assert len(retained) == 1 and retained[0]["content"]["is_final"]
        quiet(client, session, headers)
        stop(client, session, headers)

        silence, silence_headers = create(client)
        assert feed(client, silence, silence_headers, bytes(192000)) == []
        quiet(client, silence, silence_headers)
        stop(client, silence, silence_headers)

        # 收到末窗但未显式final：stop必须真正识别已收到的尾段。
        reopened, reopened_headers = create(client)
        assert feed(client, reopened, reopened_headers, pcm, final=False, confirmed=False) == []
        tail = stop(client, reopened, reopened_headers)
        assert tail["transcript_count"] > 1
        newer = [row for sid, row in observed if sid == reopened["session_id"]
                 and row["kind"] == "transcript" and row["content"]["is_final"]]
        assert len(newer) == 1 and newer[0]["content"]["text"] == text
        assert newer[0]["reliability"] == "unknown"
        assert newer[0]["content"]["audio_evidence"]["continuity_id"] != (
            finals[0]["content"]["audio_evidence"]["continuity_id"])
        quiet(client, reopened, reopened_headers)
        assert not app.state.audio.asr.lock.locked()
        assert not app.state.audio.asr.model.kwargs.get("cache")
        print({"real_final": text, "revisions": len(rows), "silence_transcripts": 0,
               "reopened_tail_transcripts": tail["transcript_count"], "events": 0})
    assert app.state.audio.asr.model is None
