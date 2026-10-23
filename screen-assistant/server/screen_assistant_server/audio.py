"""M6 场次内有界PCM接入。只保留短窗，绝不将原始PCM写入SQLite。"""
from __future__ import annotations

import asyncio
import base64
import binascii
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .asr import AudioEngineUnavailable, FunASRSDK, SileroVAD

router = APIRouter(prefix="/v1/live/sessions", tags=["audio"])
RATE, WINDOW_FRAMES, MAX_TRACKS = 16000, 96000, 16


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MediaTime(StrictModel):
    value: int = Field(ge=0, le=2**63-1)
    timescale: int = Field(gt=0, le=1_000_000_000)
    epoch: int = Field(ge=0, le=2**63-1)

    def seconds(self):
        return Fraction(self.value, self.timescale)


class OriginalFormat(StrictModel):
    sample_rate: int = Field(ge=8000, le=96000)
    channels: int = Field(ge=1, le=2)
    sample_format: Literal["s16le", "f32le"]
    interleaved: bool


class AudioSpan(StrictModel):
    sequence: int = Field(ge=0, le=2**53-1)
    pts: MediaTime
    frames: int = Field(gt=0, le=57600)


class ClockAnchor(StrictModel):
    pts: MediaTime
    observed_at_ms: int = Field(ge=0, le=2**63-1)


class AudioPacket(StrictModel):
    stream_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    source: Literal["audioMic", "audioApp"]
    sequence: int = Field(ge=0, le=2**53-1)
    sample_rate: Literal[16000]
    channels: Literal[1]
    sample_format: Literal["s16le"]
    pcm_base64: str = Field(max_length=25600)
    original_format: OriginalFormat
    spans: list[AudioSpan] = Field(max_length=128)
    clock_anchor: ClockAnchor | None = None
    gap_before: list[str] = Field(default_factory=list, max_length=8)
    host_confirmed: bool = False
    final: bool = False

    def pcm(self):
        try:
            data = base64.b64decode(self.pcm_base64, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("invalid PCM base64") from None
        if (not data and not (self.final and not self.spans)) or len(data) > 19200 or len(data) % 2:
            raise ValueError("PCM must contain at most 600ms of aligned s16le")
        return data

    @model_validator(mode="after")
    def valid_audio(self):
        data = self.pcm()
        if data and not self.spans:
            raise ValueError("PCM requires original spans")
        duration = sum(span.frames for span in self.spans) / self.original_format.sample_rate
        if duration > 0.6 or abs(duration - len(data)/32000) > 0.025:
            raise ValueError("original span duration differs from converted PCM")
        if any(len(reason) > 64 for reason in self.gap_before):
            raise ValueError("invalid gap reason")
        return self


@dataclass
class Track:
    stream_id: str
    vad: object
    sequence: int = -1
    original_sequence: int = -1
    expected_pts: Fraction | None = None
    epoch: int = 0
    original_format: dict | None = None
    anchor: dict | None = None
    host_confirmed: bool = False
    continuity_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    pending: bytearray = field(default_factory=bytearray)
    window_start: Fraction | None = None
    first_span: dict | None = None
    last_span: dict | None = None
    coverage_start_ms: int | None = None
    busy: bool = False
    touched: float = field(default_factory=time.monotonic)
    retired: deque = field(default_factory=lambda: deque(maxlen=32))
    job: asyncio.Task | None = None
    ingest_task: asyncio.Task | None = None
    job_error: bool = False
    transcripts: deque = field(default_factory=lambda: deque(maxlen=48))

    def gap(self):
        self.pending.clear()
        self.window_start = None
        self.coverage_start_ms = None
        self.continuity_id = uuid.uuid4().hex


class AudioService:
    def __init__(self, settings, live):
        self.settings, self.live = settings, live
        self.tracks: dict[tuple[str, str], Track] = {}
        self.closing: set[str] = set()
        self.body_readers: dict[str, int] = {}
        self.vad_factory = lambda: SileroVAD(settings.silero_model_path)
        self.asr = FunASRSDK(settings.funasr_model_path, self.offload)
        self.reaper = asyncio.create_task(self.reap()) if settings.audio_enabled else None

    async def expire(self):
        for key, track in list(self.tracks.items()):
            if not track.busy and time.monotonic()-track.touched > 15:
                track.busy = True
                track.gap()
                if track.job and not track.job.done():
                    track.job.cancel()
                    await asyncio.gather(track.job, return_exceptions=True)
                self.tracks.pop(key, None)

    async def reap(self):
        while True:
            await asyncio.sleep(1)
            await self.expire()

    def configure_for_test(self, vad_factory, asr):
        self.vad_factory, self.asr = vad_factory, asr

    async def access(self, sid, token):
        return await self.live.run("audio_access", sid, token)

    async def offload(self, operation, *args):
        # 取消HTTP不能让仍在运行的VAD线程与下一请求并发访问同一模型。
        task = asyncio.create_task(asyncio.to_thread(operation, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    def configured(self):
        if not self.settings.audio_enabled:
            raise HTTPException(503, "audio adapter disabled")
        if not self.settings.funasr_model_path:
            raise HTTPException(503, "local FunASR model directory required")

    async def ingest(self, sid, token, packet: AudioPacket):
        session = await self.access(sid, token)
        self.configured()
        if sid in self.closing:
            raise HTTPException(409, "audio session closing")
        key = sid, packet.source
        now = time.monotonic()
        for stale_key, item in list(self.tracks.items()):
            if not item.busy and (not item.job or item.job.done()) and now-item.touched > 15:
                del self.tracks[stale_key]
        track = self.tracks.get(key)
        if not packet.pcm() and (not track or track.stream_id != packet.stream_id):
            raise HTTPException(409, "audio end requires the current capture")
        if track and track.busy:
            raise HTTPException(429, "audio track busy; mark next input gap")
        if track and packet.stream_id in track.retired:
            raise HTTPException(409, "retired audio capture")
        if not track:
            if len(self.tracks) >= MAX_TRACKS:
                raise HTTPException(429, "audio track limit reached")
            # 同步预留名额和busy，再加载模型，避免并发创建无界模型。
            track = Track(packet.stream_id, None, busy=True)
            self.tracks[key] = track
        else:
            track.busy = True
        transcripts, gaps = [], []
        track.ingest_task = asyncio.current_task()
        try:
            if track.vad is None:
                track.vad = await self.offload(self.vad_factory)
                if isinstance(self.asr, FunASRSDK):
                    await self.asr.prepare()
            if track.stream_id != packet.stream_id:
                if packet.sequence != 0:
                    raise HTTPException(409, "new audio capture must start at sequence zero")
                track.retired.append(track.stream_id)
                track.stream_id, track.sequence = packet.stream_id, -1
                track.expected_pts = None
                gaps.append("capture_changed")
            if packet.sequence <= track.sequence:
                raise HTTPException(409, "audio sequence already consumed")
            if packet.sequence != track.sequence+1:
                gaps.append("sequence_gap")
            if not packet.pcm():
                gaps.extend(packet.gap_before)
                track.sequence = packet.sequence
                if gaps:
                    if track.job and not track.job.done():
                        track.job.cancel()
                        await asyncio.gather(track.job, return_exceptions=True)
                        track.job = None
                    track.gap()
                if track.job:
                    await track.job
                if track.job_error:
                    raise HTTPException(503, "audio analysis failed before end")
                await self.analyze(sid, token, packet.source, track, transcripts)
                transcripts.extend(track.transcripts)
                track.transcripts.clear()
                return {"status": "accepted", "gaps": gaps, "continuity_id": track.continuity_id,
                        "buffered_ms": 0, "transcripts": transcripts}
            spans, fmt = packet.spans, packet.original_format.model_dump()
            if track.original_format is not None and track.original_format != fmt:
                gaps.append("format_changed")
            if track.sequence >= 0 and track.host_confirmed != packet.host_confirmed:
                gaps.append("host_confirmation_changed")
            anchor = packet.clock_anchor.model_dump() if packet.clock_anchor else None
            if track.sequence >= 0 and track.anchor != anchor:
                gaps.append("clock_anchor_changed")
            expected, previous_seq, epoch = track.expected_pts, track.original_sequence, track.epoch
            if track.original_format != fmt or "capture_changed" in gaps:
                expected, previous_seq = None, -1
            for span in spans:
                if previous_seq >= 0 and span.sequence != previous_seq+1:
                    gaps.append("original_sequence_gap")
                if expected is not None and (span.pts.epoch != epoch or
                        abs(span.pts.seconds()-expected) > Fraction(2, fmt["sample_rate"])):
                    gaps.append("pts_gap")
                expected = span.pts.seconds() + Fraction(span.frames, fmt["sample_rate"])
                previous_seq, epoch = span.sequence, span.pts.epoch
            gaps = list(dict.fromkeys([*gaps, *packet.gap_before]))
            # 包内不连续不能把它拼为一个连续窗；完整丢弃这一包，下一包重新开始。
            internal_gap = any(
                right.sequence != left.sequence+1 or right.pts.epoch != left.pts.epoch or
                abs(right.pts.seconds()-left.pts.seconds()-Fraction(left.frames, fmt["sample_rate"]))
                > Fraction(2, fmt["sample_rate"])
                for left, right in zip(spans, spans[1:])
            )
            if gaps:
                if track.job and not track.job.done():
                    track.job.cancel()
                    await asyncio.gather(track.job, return_exceptions=True)
                    track.job = None
                track.gap()
            if track.job_error:
                gaps.append("analysis_failed")
                track.gap()
                track.job_error = False
            track.original_format, track.anchor = fmt, anchor
            track.sequence, track.original_sequence = packet.sequence, previous_seq
            track.expected_pts, track.epoch = expected, epoch
            track.host_confirmed = packet.host_confirmed
            if anchor:
                start_ms = self.mapped_ms(track, spans[0].pts.seconds())
                end_ms = self.mapped_ms(track, expected)
                if (anchor["pts"]["epoch"] != epoch or start_ms < session["started_at_ms"]
                        or end_ms > self.live.clock()+1500):
                    raise HTTPException(422, "audio observation clock outside session")
            if not internal_gap:
                # 输入包<=600ms，达到6秒前先清窗，内存上限严格为6秒。
                pcm = packet.pcm()
                if len(track.pending)+len(pcm) > WINDOW_FRAMES*2:
                    if not await self.schedule(sid, token, packet.source, track):
                        gaps.append("analysis_backpressure")
                if not track.pending:
                    track.window_start = spans[0].pts.seconds()
                    track.first_span = spans[0].model_dump()
                track.pending.extend(pcm)
                track.last_span = spans[-1].model_dump()
                if packet.final or len(track.pending) >= WINDOW_FRAMES*2:
                    if not await self.schedule(sid, token, packet.source, track):
                        gaps.append("analysis_backpressure")
                if packet.final and track.job:
                    await track.job
                    if track.job_error:
                        raise HTTPException(503, "audio analysis failed before final")
            transcripts.extend(track.transcripts)
            track.transcripts.clear()
            return {"status": "accepted", "gaps": gaps, "continuity_id": track.continuity_id,
                    "buffered_ms": len(track.pending)//32, "transcripts": transcripts}
        except HTTPException as error:
            if error.status_code == 422:
                track.gap()
            raise
        except asyncio.CancelledError:
            track.gap()
            raise
        except Exception as error:
            track.gap()
            # 不泄漏音频/转写/引擎异常原文；未完成的中间结果不能进入确定规则。
            raise HTTPException(503, "audio engine unavailable or finalization failed") from error
        finally:
            track.busy, track.touched = False, time.monotonic()
            track.ingest_task = None
            if track.vad is None:
                self.tracks.pop(key, None)

    @staticmethod
    def mapped_ms(track, media):
        if track.anchor is None:
            return None
        anchor = track.anchor
        return anchor["observed_at_ms"] + round(
            (media-Fraction(anchor["pts"]["value"], anchor["pts"]["timescale"])) * 1000)

    async def schedule(self, sid, token, source, track):
        if not track.pending:
            return True
        if track.job and not track.job.done():
            # 一窗正在识别、一窗已收满；不再排第二个分析作业。
            track.gap()
            track.job.cancel()
            await asyncio.gather(track.job, return_exceptions=True)
            track.job = None
            return False
        snapshot = replace(track, pending=track.pending, transcripts=deque(maxlen=48))
        track.pending = bytearray()
        if track.coverage_start_ms is None:
            track.coverage_start_ms = self.mapped_ms(track, snapshot.window_start)
            snapshot.coverage_start_ms = track.coverage_start_ms
        async def work():
            try:
                async with asyncio.timeout(15):
                    await self.analyze(sid, token, source, snapshot, track.transcripts, owner=track)
                if hasattr(self, "deliver"):
                    await self.deliver(sid, token)
            except asyncio.CancelledError:
                raise
            except Exception:
                track.job_error = True
                track.gap()
        track.job = asyncio.create_task(work())
        return True

    async def analyze(self, sid, token, source, track, output, owner=None):
        if not track.pending:
            return
        pcm = bytes(track.pending)
        track.pending.clear()
        start = track.window_start
        end = start + Fraction(len(pcm), 32000)
        window_start_ms, window_end_ms = self.mapped_ms(track, start), self.mapped_ms(track, end)
        if track.coverage_start_ms is None:
            track.coverage_start_ms = window_start_ms
        regions = await self.offload(track.vad.speech, pcm)
        # Silero结果是sample offsets；尾窗不丢，短于512由官方函数内部补齐。
        if len(regions) > 24:
            raise AudioEngineUnavailable("too many VAD regions")
        for left, right in regions:
            if not 0 <= left < right <= len(pcm)//2:
                raise AudioEngineUnavailable("invalid VAD interval")
            segment_id = uuid.uuid4().hex
            revision, previous_id = 0, None
            segment_start, segment_end = start+Fraction(left, RATE), start+Fraction(right, RATE)
            start_ms, end_ms = self.mapped_ms(track, segment_start), self.mapped_ms(track, segment_end)
            async def emit(text, final, mode):
                nonlocal revision, previous_id
                if owner and owner.continuity_id != track.continuity_id:
                    raise AudioEngineUnavailable("audio continuity changed during recognition")
                if revision > 99:
                    raise AudioEngineUnavailable("ASR revision limit")
                event_id = f"asr-{segment_id}-{revision}"
                body = {
                    "kind": "transcript", "event_id": event_id,
                    "source": "ios_replaykit_"+source+"_asr",
                    "observed_at_ms": end_ms, "started_at_ms": start_ms, "ended_at_ms": end_ms,
                    "availability": "available", "coverage": "continuous",
                    "reliability": "known" if track.host_confirmed and end_ms is not None else "unknown",
                    "confidence": None,
                    "content": {"text": text, "segment_started_at_ms": start_ms,
                        "segment_ended_at_ms": end_ms, "is_final": final,
                        "speaker_is_host": track.host_confirmed, "revision": revision,
                        "supersedes_event_id": previous_id,
                        "audio_evidence": {
                            "segment_id": segment_id, "continuity_id": track.continuity_id,
                            "source": source, "stream_id": track.stream_id,
                            "time_basis": "pts_arrival_approx" if track.anchor else "media_pts_only",
                            "vad": "silero", "speech_validated": True,
                            "coverage_started_at_ms": track.coverage_start_ms,
                            "coverage_ended_at_ms": window_end_ms,
                            "media_started_ms": round(segment_start*1000),
                            "media_ended_ms": round(segment_end*1000),
                            "original_format": track.original_format,
                            "first_span": track.first_span, "last_span": track.last_span,
                            "recognizer_mode": mode,
                        }},
                }
                # 复用M4观察去重/时间窗/提示；原始音频不进入此路径。
                await self.live.run("add_observation", sid, token, body, self.live.clock())
                output.append(body)
                previous_id, revision = event_id, revision+1
            await self.asr.transcribe(pcm[left*2:right*2], segment_id, emit)

    async def finish_session(self, sid, token):
        await self.access(sid, token)
        if sid in self.closing:
            raise HTTPException(409, "audio session already closing")
        self.closing.add(sid)
        output, incomplete = [], False
        async def finish_track(key, track):
            nonlocal incomplete
            try:
                # 两轨并行收尾，总等待12秒；不排队积压媒体。
                async with asyncio.timeout(12):
                    while track.busy:
                        await asyncio.sleep(0.02)
                    track.busy = True
                    if track.job:
                        await asyncio.shield(track.job)
                    await self.analyze(sid, token, key[1], track, output)
                    incomplete = incomplete or track.job_error
            except (Exception, asyncio.CancelledError):
                incomplete = True
                track.gap()
                tasks = [task for task in (track.job, track.ingest_task)
                         if task and not task.done()]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                track.busy = False
                self.tracks.pop(key, None)
        try:
            await asyncio.gather(*(finish_track(key, track) for key, track in list(self.tracks.items())
                                   if key[0] == sid))
        finally:
            self.closing.discard(sid)
        return {"transcript_count": len(output), "incomplete": incomplete}

    async def close(self):
        if self.reaper:
            self.reaper.cancel()
            await asyncio.gather(self.reaper, return_exceptions=True)
        jobs = [track.job for track in self.tracks.values() if track.job]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        self.tracks.clear()
        if isinstance(self.asr, FunASRSDK):
            await self.asr.aclose()


@router.post("/{session_id}/audio")
async def audio_packet(session_id: str, request: Request,
                       token: str | None = Header(None, alias="X-Live-Session-Token")):
    service = request.app.state.audio
    await service.access(session_id, token)
    service.configured()
    if (sum(service.body_readers.values()) >= 16 or
            service.body_readers.get(session_id, 0) >= 2):
        raise HTTPException(429, "audio body reader limit reached")
    service.body_readers[session_id] = service.body_readers.get(session_id, 0)+1
    raw = bytearray()
    try:
        async with asyncio.timeout(2):
            async for chunk in request.stream():
                if len(raw)+len(chunk) > 65536:
                    raise HTTPException(413, "audio packet too large")
                raw.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, "audio body timeout") from None
    finally:
        service.body_readers[session_id] -= 1
        if not service.body_readers[session_id]:
            del service.body_readers[session_id]
    try:
        packet = AudioPacket.model_validate_json(raw)
    except (ValidationError, ValueError):
        raise HTTPException(422, "invalid bounded PCM packet") from None
    result = await service.ingest(session_id, token, packet)
    if result["transcripts"]:
        await request.app.state.live.deliver_fusion(session_id, token, request)
    return result
