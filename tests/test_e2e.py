"""End-to-end lifecycle tests (Milestone M6): the full ASR→LLM→TTS pipeline.

These use the deterministic fake providers with a stepping clock, so they are
fully offline and deterministic — no API keys, no network. They validate the
entire request lifecycle and the streaming overlap that M6 must preserve.
"""

from __future__ import annotations

import json

from assistant.asr.fake import FakeASRProvider, FakeASRScript
from assistant.core.request import RequestManager
from assistant.core.session import SessionManager
from assistant.events.envelope import EVENT_VERSION
from assistant.llm.fake import FakeLLMProvider, FakeLLMScript
from assistant.transport.gateway import InboundKind, SessionConnection
from assistant.tts.fake import FakeTTSProvider, FakeTTSScript


class Sender:
    """Records text/binaries in order and exposes a combined event log."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.binaries: list[bytes] = []
        self.log: list[tuple[str, object]] = []

    async def send_text(self, payload: str) -> None:
        self.texts.append(payload)
        self.log.append(("text", payload))

    async def send_binary(self, payload: bytes) -> None:
        self.binaries.append(payload)
        self.log.append(("binary", payload))


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


def _connect(
    sessions: SessionManager,
    requests: RequestManager,
    asr: FakeASRProvider,
    llm: FakeLLMProvider,
    tts: FakeTTSProvider,
    clock: Clock,
) -> tuple[SessionConnection, Sender]:
    sender = Sender()
    connection = SessionConnection(
        sessions, requests, sender.send_text,
        asr, llm, tts, send_binary=sender.send_binary, clock=clock,
    )
    return connection, sender


def _envelope(sid: str, type_: str, seq: int) -> str:
    return json.dumps({
        "type": type_, "version": EVENT_VERSION, "session_id": sid,
        "request_id": None, "seq": seq, "timestamp": 1000.0, "payload": {},
    })


async def test_full_lifecycle_ids_remain_correlated() -> None:
    sessions = SessionManager(clock=lambda: 0.0)
    requests = RequestManager(clock=lambda: 0.0)
    asr = FakeASRProvider(FakeASRScript(
        partials=("hel", "hello"), final="hello world"
    ))
    llm = FakeLLMProvider(FakeLLMScript(tokens=("Hi", " there", ".")))
    tts = FakeTTSProvider(FakeTTSScript(chunks=(b"AA", b"BB")))
    connection, sender = _connect(
        sessions, requests, asr, llm, tts, Clock()
    )
    await connection.on_connect()
    await connection.on_message(b"\x00\x01" * 80)
    req_id = connection.request_id
    assert req_id is not None
    await connection.on_message(_envelope(
        connection.session_id or "", "audio.eos", 1
    ))

    events = [json.loads(t) for t in sender.texts]
    types = [e["type"] for e in events]
    order = [
        "session.started",
        "request.started",
        "transcript.partial",
        "transcript.partial",
        "transcript.final",
        "llm.token",
        "llm.token",
        "llm.token",
        "tts.done",
        "metrics",
        "request.completed",
    ]
    assert types == order, types

    # Request ID is identical across every request-scoped event.
    for e in events[1:]:  # session.started has no request yet
        assert e["request_id"] == req_id, (e, req_id)
    # Session ID identical on all events.
    for e in events:
        assert e["session_id"] == connection.session_id, e

    # Two audio chunks streamed, not one blob.
    assert sender.binaries == [b"AA", b"BB"]

    # metrics payload has full latency set, correlated.
    metrics = [e for e in events if e["type"] == "metrics"][0]
    assert metrics["request_id"] == req_id
    for field in (
        "asr_first_transcript_ms", "asr_final_transcript_ms",
        "llm_ttft_ms", "tts_ttfb_ms", "request_total_ms",
    ):
        assert field in metrics["payload"], field

    # LLM got the final transcript; segments reached TTS; all closed.
    assert llm.streams[0].text == "hello world"
    assert tts.streams[0].state.segments == ["Hi there."]
    assert asr.streams[0].closed is True
    assert llm.streams[0].closed is True
    assert tts.streams[0].closed is True


async def test_streaming_overlap_audio_before_second_llm_token() -> None:
    sessions = SessionManager(clock=lambda: 0.0)
    requests = RequestManager(clock=lambda: 0.0)
    asr = FakeASRProvider(FakeASRScript(final="say hi. ok"))
    llm = FakeLLMProvider(FakeLLMScript(tokens=("Hi.", " Next")))
    tts = FakeTTSProvider(FakeTTSScript(chunks=(b"CH",)))
    connection, sender = _connect(
        sessions, requests, asr, llm, tts, Clock()
    )
    await connection.on_connect()
    await connection.on_message(b"\x00\x01" * 80)
    await connection.on_message(_envelope(
        connection.session_id or "", "audio.eos", 1
    ))

    log_types = [
        t if t == "binary" else json.loads(p)["type"]
        for t, p in sender.log
    ]
    first_binary = log_types.index("binary")
    second_llm = [
        i for i, x in enumerate(log_types) if x == "llm.token"
    ][1]
    # First audio arrives before the second llm.token (overlap preserved).
    assert first_binary < second_llm
    assert tts.streams[0].state.segments == ["Hi.", " Next"]


async def test_asr_failure_terminates_with_request_error() -> None:
    sessions = SessionManager(clock=lambda: 0.0)
    requests = RequestManager(clock=lambda: 0.0)
    asr = FakeASRProvider(FakeASRScript(error_at_send="asr broke"))
    connection, sender = _connect(
        sessions, requests, asr, FakeLLMProvider(), FakeTTSProvider(), Clock()
    )
    await connection.on_connect()
    result = await connection.on_message(b"\x00\x01" * 80)

    assert result.kind is InboundKind.REQUEST_ERROR
    events = [json.loads(t) for t in sender.texts]
    types = [e["type"] for e in events]
    error = [e for e in events if e["type"] == "request.error"][0]
    assert error["payload"]["kind"] == "asr"
    assert "request.completed" not in types
    assert "metrics" not in types


async def test_llm_failure_terminates_with_request_error() -> None:
    sessions = SessionManager(clock=lambda: 0.0)
    requests = RequestManager(clock=lambda: 0.0)
    asr = FakeASRProvider(FakeASRScript(final="hello"))
    llm = FakeLLMProvider(FakeLLMScript(error_at_open="llm down"))
    tts = FakeTTSProvider(FakeTTSScript(chunks=(b"AA",)))
    connection, sender = _connect(
        sessions, requests, asr, llm, tts, Clock()
    )
    await connection.on_connect()
    await connection.on_message(b"\x00\x01" * 80)
    result = await connection.on_message(_envelope(
        connection.session_id or "", "audio.eos", 1
    ))

    assert result.kind is InboundKind.REQUEST_ERROR
    events = [json.loads(t) for t in sender.texts]
    types = [e["type"] for e in events]
    assert "llm.error" in types
    error = [e for e in events if e["type"] == "request.error"][0]
    assert error["payload"]["kind"] == "llm"
    assert "request.completed" not in types
    assert "metrics" not in types
    assert tts.streams == []  # no TTS opened


async def test_tts_mid_stream_failure_keeps_streamed_audio() -> None:
    sessions = SessionManager(clock=lambda: 0.0)
    requests = RequestManager(clock=lambda: 0.0)
    asr = FakeASRProvider(FakeASRScript(final="hello"))
    llm = FakeLLMProvider(FakeLLMScript(tokens=("Hi.", " More")))
    tts = FakeTTSProvider(FakeTTSScript(
        chunks=(b"OK",), error_at_send="synth broke", error_at_send_after=1
    ))
    connection, sender = _connect(
        sessions, requests, asr, llm, tts, Clock()
    )
    await connection.on_connect()
    await connection.on_message(b"\x00\x01" * 80)
    result = await connection.on_message(_envelope(
        connection.session_id or "", "audio.eos", 1
    ))

    assert result.kind is InboundKind.REQUEST_ERROR
    events = [json.loads(t) for t in sender.texts]
    types = [e["type"] for e in events]
    assert sender.binaries == [b"OK"]  # valid audio retained
    error = [e for e in events if e["type"] == "request.error"][0]
    assert error["payload"]["kind"] == "tts"
    assert "request.completed" not in types
    assert "metrics" not in types
    assert tts.streams[0].closed is True


async def test_disconnect_cancels_pipeline_no_spurious_completion() -> None:
    sessions = SessionManager(clock=lambda: 0.0)
    requests = RequestManager(clock=lambda: 0.0)
    asr = FakeASRProvider(FakeASRScript(
        partials=("partial",), final="done"
    ))
    connection, sender = _connect(
        sessions, requests, asr, FakeLLMProvider(), FakeTTSProvider(), Clock()
    )
    await connection.on_connect()
    await connection.on_message(b"\x00\x01" * 80)
    req_id = connection.request_id
    await connection.on_close()

    events = [json.loads(t) for t in sender.texts]
    types = [e["type"] for e in events]
    canceled = [e for e in events if e["type"] == "request.canceled"]
    assert len(canceled) == 1
    assert canceled[0]["request_id"] == req_id
    assert "request.completed" not in types
    assert "metrics" not in types
    assert asr.streams[0].canceled is True
