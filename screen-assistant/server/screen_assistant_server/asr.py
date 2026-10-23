"""现成Silero VAD / FunASR WebSocket薄适配；不下载模型、不保存音频。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path


class AudioEngineUnavailable(Exception):
    pass


class SileroVAD:
    """只加载明确给出的本地ONNX；官方API负责语音切段，非能量阈值。"""
    def __init__(self, path: str):
        if not Path(path).is_file():
            raise AudioEngineUnavailable("local Silero ONNX model required")
        try:
            import numpy as np
            import torch
            from silero_vad.utils_vad import OnnxWrapper, get_speech_timestamps
            self.np, self.torch = np, torch
            self.model = OnnxWrapper(path, force_onnx_cpu=True)
            self.timestamps = get_speech_timestamps
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            raise AudioEngineUnavailable("Silero runtime unavailable") from error

    def speech(self, pcm: bytes) -> list[tuple[int, int]]:
        samples = self.np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768
        regions = self.timestamps(
            self.torch.from_numpy(samples), self.model, sampling_rate=16000,
            min_speech_duration_ms=250, min_silence_duration_ms=300,
            speech_pad_ms=0, return_seconds=False,
        )
        return [(int(row["start"]), int(row["end"])) for row in regions]


class FunASRConnection:
    """每个VAD语段一条有界连接；online为增量，2pass-offline替换当前句。

    文本在段内按官方客户端的增量语义累计。句尾发送结束标志，未收到结束确认
    不把中间结果升级final。2pass内部多句无时间戳时不用于精确停顿。
    """
    def __init__(self, url: str, *, mode="online", connect=None, pace=True):
        self.url, self.mode, self.connect, self.pace = url, mode, connect, pace

    async def transcribe(self, pcm: bytes, segment_id: str, emit):
        if self.connect is None:
            from websockets.asyncio.client import connect
        else:
            connect = self.connect
        socket = await connect(self.url, open_timeout=2, close_timeout=1, subprotocols=["binary"],
                               max_size=32768, max_queue=4, proxy=None)
        final_received = asyncio.Event()
        ending = False
        online, committed = "", ""
        last_emitted = None
        last_text_mode = "online" if self.mode == "online" else "2pass-online"

        async def receive():
            nonlocal online, committed, last_emitted, last_text_mode
            while True:
                message = json.loads(await socket.recv())
                if message.get("wav_name", segment_id) != segment_id:
                    raise AudioEngineUnavailable("FunASR segment identity mismatch")
                if message.get("error"):
                    raise AudioEngineUnavailable("FunASR rejected audio")
                mode = message.get("mode")
                if message.get("is_end") is True:
                    if not ending or message.get("is_final") is not True or mode != self.mode:
                        raise AudioEngineUnavailable("invalid FunASR end acknowledgement")
                    total = (committed + online).strip()
                    if total:
                        await emit(total, True, last_text_mode)
                    final_received.set()
                    return
                if mode not in {"online", "2pass-online", "2pass-offline"}:
                    raise AudioEngineUnavailable("unsupported FunASR response mode")
                text = message.get("text", "")
                if not isinstance(text, str) or len(text) > 500:
                    raise AudioEngineUnavailable("invalid FunASR transcript")
                if mode == "2pass-offline":
                    committed += text
                    online = ""
                else:
                    online += text
                total = (committed + online).strip()
                if len(total) > 500:
                    raise AudioEngineUnavailable("FunASR segment transcript too long")
                # 单句is_final可能先于末段确认；只把is_end确认后的完整文本交业务final。
                if total and (total, False) != last_emitted:
                    await emit(total, False, mode)
                    last_emitted = total, False
                    last_text_mode = mode

        receiver = None
        try:
            await socket.send(json.dumps({
                "mode": self.mode, "wav_name": segment_id, "wav_format": "pcm",
                "audio_fs": 16000, "is_speaking": True, "chunk_size": [5, 10, 5],
                "chunk_interval": 10, "itn": False,
            }))
            receiver = asyncio.create_task(receive())
            # 官方客户端60ms PCM分包；不会把整窗瞬间压入引擎。
            for offset in range(0, len(pcm), 1920):
                if receiver.done():
                    await receiver
                    raise AudioEngineUnavailable("FunASR ended before input")
                chunk = pcm[offset:offset+1920]
                await socket.send(chunk)
                await asyncio.sleep(len(chunk)/32000 if self.pace else 0)
            ending = True
            await socket.send(json.dumps({"is_speaking": False, "is_end": True}))
            await asyncio.wait_for(receiver, timeout=5)
            if not final_received.is_set():
                raise AudioEngineUnavailable("FunASR final acknowledgement missing")
        finally:
            if receiver is not None:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)
            await socket.close()
