"""Deterministic tests for the microphone client (scripts/_voice_mic_live.py).

No microphone, no sounddevice import at test time: the framer and the frame
sender are pure Python and driven with canned bytes and a fake WebSocket.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

from assistant.asr.audio import validate_audio_frame

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "_voice_mic_live.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("_voice_mic_live", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeWS:
    """Records sends in order; binary vs text is distinguishable."""

    def __init__(self) -> None:
        self.sent: list[bytes | str] = []

    async def send(self, payload: bytes | str) -> None:
        self.sent.append(payload)


class RecvWS(FakeWS):
    """Fake WebSocket scripted with inbound messages for ``recv``."""

    def __init__(self, inbox: list[str]) -> None:
        super().__init__()
        self._inbox = list(inbox)
        self.recv_calls = 0

    async def recv(self) -> str:
        self.recv_calls += 1
        if not self._inbox:
            raise AssertionError("recv() called but no session.started pending")
        return self._inbox.pop(0)


def test_capture_contract_is_pcm16_16k_mono() -> None:
    """The microphone stream must request the exact approved audio contract."""
    module = _load_module()
    assert module.MIC_SAMPLE_RATE_HZ == 16000
    assert module.MIC_CHANNELS == 1
    assert module.MIC_DTYPE == "int16"  # PCM16 signed little-endian


def test_framer_emits_320_byte_frames_preserving_samples() -> None:
    """Chunks of arbitrary size are re-framed into 320-byte wire frames."""
    module = _load_module()
    pcm = bytes(range(256)) * 5  # 1280 bytes, arbitrary chunking
    framer = module.MicPcmFramer()
    framer.add_chunk(pcm[:333])
    framer.add_chunk(pcm[333:777])
    framer.add_chunk(pcm[777:])
    framer.finish()
    assert all(len(f) == 320 for f in framer.frames)
    assert b"".join(framer.frames) == pcm
    for frame in framer.frames:
        validate_audio_frame(frame)  # must satisfy the wire contract


def test_framer_finish_pads_final_partial_sample_aligned() -> None:
    """A trailing odd-length remainder is zero-padded to a whole sample."""
    module = _load_module()
    framer = module.MicPcmFramer()
    framer.add_chunk(b"\x01\x00" * 50 + b"\x02")  # 101 bytes (odd)
    framer.finish()
    assert len(framer.frames) == 1
    assert len(framer.frames[0]) == 102  # padded to a whole PCM16 sample
    assert framer.frames[0][:100] == b"\x01\x00" * 50
    assert framer.frames[0][100] == 2
    validate_audio_frame(framer.frames[0])


def test_empty_recording_produces_no_frames() -> None:
    module = _load_module()
    framer = module.MicPcmFramer()
    framer.finish()
    assert framer.frames == []


async def test_frames_sent_as_binary_then_audio_eos() -> None:
    """All frames go out as binary WS messages; audio.eos is sent last."""
    module = _load_module()
    frames = [b"\x00\x01" * 160, b"\x02\x03" * 160, b"\x04\x05"]
    ws = FakeWS()
    await module._send_audio_and_eos(ws, frames, "ses_test")
    assert len(ws.sent) == 4
    assert ws.sent[0] == frames[0]
    assert ws.sent[1] == frames[1]
    assert ws.sent[2] == frames[2]
    eos = json.loads(ws.sent[3])  # type: ignore[arg-type]
    assert eos["type"] == "audio.eos"
    assert eos["session_id"] == "ses_test"


async def test_no_frames_still_sends_audio_eos() -> None:
    """Even silence/no capture must signal end-of-utterance."""
    module = _load_module()
    ws = FakeWS()
    await module._send_audio_and_eos(ws, [], "ses_x")
    assert len(ws.sent) == 1
    assert json.loads(ws.sent[0])["type"] == "audio.eos"  # type: ignore[arg-type]


async def test_realtime_pacing_does_not_burst() -> None:
    """Sending 5 frames is paced (>= ~40 ms) rather than instantaneous."""
    module = _load_module()
    frames = [b"\x00\x00" * 160] * 5
    ws = FakeWS()
    loop = asyncio.get_running_loop()
    start = loop.time()
    await module._send_audio_and_eos(ws, frames, "ses_x")
    elapsed = loop.time() - start
    assert elapsed >= 0.04  # 4 inter-frame gaps of ~10 ms


async def test_audio_eos_seq_is_monotonic_across_turns() -> None:
    """Later turns must send a strictly greater seq (gateway §8.3)."""
    module = _load_module()
    ws = FakeWS()
    await module._send_audio_and_eos(ws, [], "ses_x", seq=1)
    await module._send_audio_and_eos(ws, [], "ses_x", seq=2)
    seqs = [json.loads(m)["seq"] for m in ws.sent]  # type: ignore[arg-type]
    assert seqs == [1, 2]


async def test_session_id_is_reused_after_first_turn() -> None:
    """session.started arrives once per connection; turns 2+ reuse the id."""
    module = _load_module()

    class HandshakeWS(FakeWS):
        def __init__(self) -> None:
            super().__init__()
            self.recv_calls = 0

        async def recv(self) -> str:
            self.recv_calls += 1
            return json.dumps(
                {
                    "type": "session.started",
                    "version": "1.0",
                    "session_id": "ses_first",
                    "seq": 1,
                    "timestamp": 0.0,
                    "payload": {},
                }
            )

    ws = HandshakeWS()
    first = await module._ensure_session_id(ws, None)
    second = await module._ensure_session_id(ws, first)
    assert first == "ses_first"
    assert second == "ses_first"
    assert ws.recv_calls == 1  # no second handshake read


async def test_audio_eos_sequence_is_monotonic_across_turns() -> None:
    """The gateway rejects non-monotonic seq, so each turn must increase it."""
    module = _load_module()
    ws = FakeWS()
    await module._send_audio_and_eos(ws, [b"\x00\x01" * 160], "ses_x", seq=1)
    await module._send_audio_and_eos(ws, [b"\x00\x01" * 160], "ses_x", seq=2)
    eos = [json.loads(m) for m in ws.sent if isinstance(m, str)]
    assert [e["seq"] for e in eos] == [1, 2]


async def test_first_turn_reads_session_started_from_the_wire() -> None:
    """Turn one has no cached session id and must read session.started."""
    module = _load_module()
    ws = RecvWS(
        [json.dumps({"type": "session.started", "session_id": "ses_abc"})]
    )
    session_id = await module._ensure_session_id(ws, None)
    assert session_id == "ses_abc"
    assert ws.recv_calls == 1


async def test_followup_turn_reuses_session_id_without_waiting() -> None:
    """Follow-up turns must NOT wait for a second session.started."""
    module = _load_module()
    ws = RecvWS([])  # no inbound events pending
    session_id = await module._ensure_session_id(ws, "ses_abc")
    assert session_id == "ses_abc"
    assert ws.recv_calls == 0  # would have raised / blocked otherwise
