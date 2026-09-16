"""Tests for the WebSocket gateway protocol handler (architecture.md Â§6, Â§8)."""

from __future__ import annotations

import json

import pytest

from assistant.asr.fake import FakeASRProvider, FakeASRScript
from assistant.core.request import RequestManager
from assistant.core.session import SessionManager
from assistant.events.envelope import EVENT_VERSION
from assistant.llm.fake import FakeLLMProvider
from assistant.transport.gateway import InboundKind, SessionConnection
from assistant.tts.fake import FakeTTSProvider


class FakeSender:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.binaries: list[bytes] = []
        #: Combined ordered log of ("text", payload) / ("binary", payload).
        self.log: list[tuple[str, object]] = []

    async def send_text(self, payload: str) -> None:
        self.texts.append(payload)
        self.log.append(("text", payload))

    async def send_binary(self, payload: bytes) -> None:
        self.binaries.append(payload)
        self.log.append(("binary", payload))


async def _connect(
    sessions: SessionManager,
    requests: RequestManager,
    asr: FakeASRProvider,
    llm: FakeLLMProvider,
    tts: FakeTTSProvider,
) -> tuple[SessionConnection, FakeSender]:
    sender = FakeSender()
    connection = SessionConnection(
        sessions, requests, sender.send_text, asr, llm, tts,
        send_binary=sender.send_binary, clock=lambda: 1000.0,
    )
    await connection.on_connect()
    return connection, sender


def _envelope(
    session_id: str, type_: str, seq: int, **payload: object
) -> dict[str, object]:
    return {
        "type": type_, "version": EVENT_VERSION, "session_id": session_id,
        "request_id": None, "seq": seq, "timestamp": 2000.0, "payload": payload,
    }


@pytest.mark.asyncio
async def test_on_connect_emits_session_started(
    sessions: SessionManager, requests_mgr: RequestManager,
    asr: FakeASRProvider, llm: FakeLLMProvider, tts: FakeTTSProvider
) -> None:
    connection, sender = await _connect(sessions, requests_mgr, asr, llm, tts)
    assert connection.session_id is not None
    assert connection.session_id.startswith("ses_")
    assert len(sender.texts) == 1
    started = json.loads(sender.texts[0])
    assert started["type"] == "session.started"
    assert started["session_id"] == connection.session_id


@pytest.mark.asyncio
async def test_valid_client_event_is_accepted(
    sessions: SessionManager, requests_mgr: RequestManager,
    asr: FakeASRProvider, llm: FakeLLMProvider, tts: FakeTTSProvider
) -> None:
    connection, _ = await _connect(sessions, requests_mgr, asr, llm, tts)
    msg = json.dumps(_envelope(connection.session_id or "", "audio.eos", 1))
    result = await connection.on_message(msg)
    assert result.kind is InboundKind.ACCEPTED
    assert result.event_type == "audio.eos"


@pytest.mark.asyncio
async def test_non_monotonic_seq_is_validation_error(
    sessions: SessionManager, requests_mgr: RequestManager,
    asr: FakeASRProvider, llm: FakeLLMProvider, tts: FakeTTSProvider
) -> None:
    connection, sender = await _connect(sessions, requests_mgr, asr, llm, tts)
    sid = connection.session_id or ""
    await connection.on_message(json.dumps(_envelope(sid, "audio.eos", 5)))
    result = await connection.on_message(json.dumps(_envelope(sid, "audio.eos", 3)))
    assert result.kind is InboundKind.VALIDATION_ERROR
    assert "non-monotonic" in result.detail
    error_event = json.loads(sender.texts[-1])
    assert error_event["type"] == "error"
    assert error_event["payload"]["kind"] == "validation"


@pytest.mark.asyncio
async def test_unknown_event_type_is_rejected(
    sessions: SessionManager, requests_mgr: RequestManager,
    asr: FakeASRProvider, llm: FakeLLMProvider, tts: FakeTTSProvider
) -> None:
    connection, sender = await _connect(sessions, requests_mgr, asr, llm, tts)
    sid = connection.session_id or ""
    result = await connection.on_message(json.dumps(_envelope(sid, "bogus.type", 1)))
    assert result.kind is InboundKind.VALIDATION_ERROR
    assert "unknown" in result.detail
    assert json.loads(sender.texts[-1])["payload"]["kind"] == "validation"


@pytest.mark.asyncio
async def test_wrong_session_id_is_rejected(
    sessions: SessionManager, requests_mgr: RequestManager,
    asr: FakeASRProvider, llm: FakeLLMProvider, tts: FakeTTSProvider
) -> None:
    connection, _ = await _connect(sessions, requests_mgr, asr, llm, tts)
    result = await connection.on_message(
        json.dumps(_envelope("ses_OTHER", "audio.eos", 1))
    )
    assert result.kind is InboundKind.VALIDATION_ERROR
    assert "does not match" in result.detail


@pytest.mark.asyncio
async def test_malformed_json_is_rejected(
    sessions: SessionManager, requests_mgr: RequestManager,
    asr: FakeASRProvider, llm: FakeLLMProvider, tts: FakeTTSProvider
) -> None:
    connection, _ = await _connect(sessions, requests_mgr, asr, llm, tts)
    result = await connection.on_message("{not json")
    assert result.kind is InboundKind.VALIDATION_ERROR
    assert "unparseable" in result.detail


@pytest.mark.asyncio
async def test_binary_message_is_tracked_and_sent_to_asr(
    sessions: SessionManager, requests_mgr: RequestManager,
    asr: FakeASRProvider, llm: FakeLLMProvider, tts: FakeTTSProvider
) -> None:
    """In M2, binary audio is validated and fed to ASR (not echoed back)."""
    connection, sender = await _connect(sessions, requests_mgr, asr, llm, tts)
    result = await connection.on_message(b"\x00\x01" * 80)  # valid PCM16
    assert result.kind is InboundKind.BINARY_AUDIO
    assert connection.binary_frames == 1
    # The frame is forwarded to the ASR stream, not echoed back as binary.
    assert sender.binaries == []
    assert asr.streams[0].frames == [b"\x00\x01" * 80]


@pytest.mark.asyncio
async def test_close_emits_session_ended(
    sessions: SessionManager, requests_mgr: RequestManager,
    asr: FakeASRProvider, llm: FakeLLMProvider, tts: FakeTTSProvider
) -> None:
    connection, sender = await _connect(sessions, requests_mgr, asr, llm, tts)
    await connection.on_message(b"\x01")
    await connection.on_close()
    ended = [t for t in sender.texts if json.loads(t)["type"] == "session.ended"]
    assert len(ended) == 1


@pytest.mark.asyncio
async def test_close_is_safe_on_disconnected_peer(
    sessions: SessionManager, requests_mgr: RequestManager,
    asr: FakeASRProvider, llm: FakeLLMProvider, tts: FakeTTSProvider
) -> None:
    calls = 0

    async def flaky_send(payload: str) -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise ConnectionError("socket gone")

    connection = SessionConnection(
        sessions, requests_mgr, flaky_send, asr, llm, tts, clock=lambda: 1000.0,
    )
    await connection.on_connect()
    await connection.on_close()
    assert calls == 2

# --- M2 ASR integration: audio.frame -> ASR -> transcript events ---


@pytest.mark.asyncio
async def test_audio_frame_feeds_asr_and_emits_transcript(
    sessions: SessionManager, requests_mgr: RequestManager,
    llm: FakeLLMProvider, tts: FakeTTSProvider
) -> None:
    script = FakeASRScript(partials=("hel", "hello"), final="hello world")
    connection, sender = await _connect(
        sessions, requests_mgr, FakeASRProvider(script), llm, tts
    )
    frame = b"\x00\x01" * 160
    result = await connection.on_message(frame)
    assert result.kind is InboundKind.BINARY_AUDIO
    partial_events = [
        json.loads(t) for t in sender.texts
        if json.loads(t)["type"] == "transcript.partial"
    ]
    assert len(partial_events) == 2
    assert partial_events[0]["payload"]["text"] == "hel"
    assert partial_events[1]["payload"]["text"] == "hello"
    assert partial_events[0]["session_id"] == connection.session_id
    assert partial_events[0]["request_id"] == connection.request_id
    # Capture the request_id before audio.eos completes and resets the request.
    req_id = connection.request_id
    eos = json.dumps(_envelope(connection.session_id or "", "audio.eos", 1))
    await connection.on_message(eos)
    final_events = [
        json.loads(t) for t in sender.texts
        if json.loads(t)["type"] == "transcript.final"
    ]
    assert len(final_events) == 1
    assert final_events[0]["payload"]["text"] == "hello world"
    assert final_events[0]["request_id"] == req_id


@pytest.mark.asyncio
async def test_invalid_audio_frame_emits_error_not_crash(
    sessions: SessionManager, requests_mgr: RequestManager,
    asr: FakeASRProvider, llm: FakeLLMProvider, tts: FakeTTSProvider
) -> None:
    connection, sender = await _connect(sessions, requests_mgr, asr, llm, tts)
    result = await connection.on_message(b"\x00\x01\x02")
    assert result.kind is InboundKind.AUDIO_INVALID
    error_event = json.loads(sender.texts[-1])
    assert error_event["type"] == "error"
    assert error_event["payload"]["kind"] == "validation"


@pytest.mark.asyncio
async def test_asr_provider_error_emits_request_error(
    sessions: SessionManager, requests_mgr: RequestManager,
    llm: FakeLLMProvider, tts: FakeTTSProvider
) -> None:
    asr = FakeASRProvider(FakeASRScript(error_at_open="provider down"))
    connection, sender = await _connect(sessions, requests_mgr, asr, llm, tts)
    result = await connection.on_message(b"\x00\x01" * 80)
    assert result.kind is InboundKind.REQUEST_ERROR
    error_event = json.loads(sender.texts[-1])
    assert error_event["type"] == "request.error"
    assert error_event["payload"]["kind"] == "asr"
    assert "provider down" in error_event["payload"]["message"]


@pytest.mark.asyncio
async def test_asr_mid_stream_error_emits_request_error(
    sessions: SessionManager, requests_mgr: RequestManager,
    llm: FakeLLMProvider, tts: FakeTTSProvider
) -> None:
    asr = FakeASRProvider(FakeASRScript(error_at_send="stream broke"))
    connection, sender = await _connect(sessions, requests_mgr, asr, llm, tts)
    result = await connection.on_message(b"\x00\x01" * 80)
    assert result.kind is InboundKind.REQUEST_ERROR
    error_event = json.loads(sender.texts[-1])
    assert error_event["type"] == "request.error"
    assert error_event["payload"]["kind"] == "asr"


@pytest.mark.asyncio
async def test_disconnect_cancels_active_request(
    sessions: SessionManager, requests_mgr: RequestManager,
    llm: FakeLLMProvider, tts: FakeTTSProvider
) -> None:
    script = FakeASRScript(partials=("partial",), final="done")
    connection, sender = await _connect(
        sessions, requests_mgr, FakeASRProvider(script), llm, tts
    )
    await connection.on_message(b"\x00\x01" * 80)
    req_id = connection.request_id  # capture before close resets it
    await connection.on_close()
    canceled_events = [
        json.loads(t) for t in sender.texts
        if json.loads(t)["type"] == "request.canceled"
    ]
    assert len(canceled_events) == 1
    assert canceled_events[0]["request_id"] == req_id


# -- LLM stage (M3) ----------------------------------------------------------


async def test_audio_eos_runs_llm_tokens_metrics_then_completed(
    sessions: SessionManager, requests_mgr: RequestManager,
    tts: FakeTTSProvider,
) -> None:
    from assistant.llm.fake import FakeLLMProvider, FakeLLMScript

    asr = FakeASRProvider(FakeASRScript(final="hello world"))
    llm = FakeLLMProvider(FakeLLMScript(tokens=("Hello", " there")))
    connection, sender = await _connect(
        sessions, requests_mgr, asr, llm, tts
    )
    await connection.on_message(b"\x00\x01" * 80)
    req_id = connection.request_id
    await connection.on_message(
        json.dumps(_envelope(connection.session_id or "", "audio.eos", 1))
    )

    events = [json.loads(t) for t in sender.texts]
    types = [e["type"] for e in events]
    assert types.index("transcript.final") < types.index("llm.token")
    assert types.index("llm.token") < types.index("metrics")
    assert types.index("metrics") < types.index("request.completed")

    token_events = [e for e in events if e["type"] == "llm.token"]
    assert [e["payload"]["text"] for e in token_events] == ["Hello", " there"]
    assert all(e["request_id"] == req_id for e in token_events)

    metrics = [e for e in events if e["type"] == "metrics"]
    assert len(metrics) == 1
    assert metrics[0]["payload"]["llm_ttft_ms"] == 0.0  # deterministic clock
    assert metrics[0]["request_id"] == req_id

    # The LLM stream received the final transcript as its prompt.
    assert llm.streams[0].text == "hello world"
    assert llm.streams[0].closed is True


async def test_llm_error_at_open_emits_llm_error_and_request_error(
    sessions: SessionManager, requests_mgr: RequestManager,
    tts: FakeTTSProvider,
) -> None:
    from assistant.llm.fake import FakeLLMProvider, FakeLLMScript

    asr = FakeASRProvider(FakeASRScript(final="hello"))
    llm = FakeLLMProvider(FakeLLMScript(error_at_open="llm down"))
    connection, sender = await _connect(
        sessions, requests_mgr, asr, llm, tts
    )
    await connection.on_message(b"\x00\x01" * 80)
    result = await connection.on_message(
        json.dumps(_envelope(connection.session_id or "", "audio.eos", 1))
    )

    assert result.kind is InboundKind.REQUEST_ERROR
    events = [json.loads(t) for t in sender.texts]
    llm_errors = [e for e in events if e["type"] == "llm.error"]
    request_errors = [e for e in events if e["type"] == "request.error"]
    assert len(llm_errors) == 1
    assert "llm down" in llm_errors[0]["payload"]["message"]
    assert len(request_errors) == 1
    assert request_errors[0]["payload"]["kind"] == "llm"


async def test_llm_mid_stream_error_emits_llm_error_and_request_error(
    sessions: SessionManager, requests_mgr: RequestManager,
    tts: FakeTTSProvider,
) -> None:
    from assistant.llm.fake import FakeLLMProvider, FakeLLMScript

    asr = FakeASRProvider(FakeASRScript(final="hello"))
    llm = FakeLLMProvider(
        FakeLLMScript(tokens=("a",), error_mid_stream="generation broke")
    )
    connection, sender = await _connect(
        sessions, requests_mgr, asr, llm, tts
    )
    await connection.on_message(b"\x00\x01" * 80)
    result = await connection.on_message(
        json.dumps(_envelope(connection.session_id or "", "audio.eos", 1))
    )

    assert result.kind is InboundKind.REQUEST_ERROR
    events = [json.loads(t) for t in sender.texts]
    assert [e["type"] for e in events].count("llm.error") == 1
    assert [e["type"] for e in events].count("llm.token") == 1
    assert not any(e["type"] == "request.completed" for e in events)


async def test_no_final_transcript_skips_llm_stage(
    sessions: SessionManager, requests_mgr: RequestManager,
    tts: FakeTTSProvider,
) -> None:
    from assistant.llm.fake import FakeLLMProvider

    llm = FakeLLMProvider()
    connection, sender = await _connect(
        sessions, requests_mgr, FakeASRProvider(FakeASRScript()), llm, tts
    )
    await connection.on_message(b"\x00\x01" * 80)
    result = await connection.on_message(
        json.dumps(_envelope(connection.session_id or "", "audio.eos", 1))
    )

    assert result.kind is InboundKind.REQUEST_FINISHED
    events = [json.loads(t) for t in sender.texts]
    assert not any(e["type"] == "llm.token" for e in events)
    assert any(e["type"] == "request.completed" for e in events)
    # M5 contract: metrics is emitted on every successful completion. With no
    # ASR/LLM/TTS output, only total is present (absent stages omitted).
    metrics = [e for e in events if e["type"] == "metrics"]
    assert len(metrics) == 1
    assert "request_total_ms" in metrics[0]["payload"]
    assert "asr_first_transcript_ms" not in metrics[0]["payload"]
    assert "llm_ttft_ms" not in metrics[0]["payload"]
    assert "tts_ttfb_ms" not in metrics[0]["payload"]
    assert llm.streams == []  # LLM never opened


# -- TTS stage (M4) ----------------------------------------------------------


async def test_llm_output_reaches_tts_and_streams_audio(
    sessions: SessionManager, requests_mgr: RequestManager
) -> None:
    from assistant.llm.fake import FakeLLMProvider, FakeLLMScript
    from assistant.tts.fake import FakeTTSProvider, FakeTTSScript

    asr = FakeASRProvider(FakeASRScript(final="hello world"))
    llm = FakeLLMProvider(FakeLLMScript(tokens=("Hello", " there")))
    tts = FakeTTSProvider(FakeTTSScript(chunks=(b"AA", b"BB")))
    connection, sender = await _connect(
        sessions, requests_mgr, asr, llm, tts
    )
    await connection.on_message(b"\x00\x01" * 80)
    req_id = connection.request_id
    await connection.on_message(
        json.dumps(_envelope(connection.session_id or "", "audio.eos", 1))
    )

    events = [json.loads(t) for t in sender.texts]
    types = [e["type"] for e in events]
    # Audio is streamed, then tts.done, then metrics, then completion.
    assert types.index("transcript.final") < types.index("llm.token")
    assert types.index("llm.token") < types.index("tts.done")
    assert types.index("tts.done") < types.index("metrics")
    assert types.index("metrics") < types.index("request.completed")

    # Two chunks streamed (not one blob).
    assert sender.binaries == [b"AA", b"BB"]
    # One segment ("Hello there" — no sentence terminator in the tokens).
    assert tts.streams[0].state.segments == ["Hello there"]
    assert tts.streams[0].closed is True

    done = [e for e in events if e["type"] == "tts.done"]
    assert len(done) == 1
    assert done[0]["request_id"] == req_id


async def test_segmentation_streams_audio_mid_llm(
    sessions: SessionManager, requests_mgr: RequestManager
) -> None:
    from assistant.llm.fake import FakeLLMProvider, FakeLLMScript
    from assistant.tts.fake import FakeTTSProvider, FakeTTSScript

    asr = FakeASRProvider(FakeASRScript(final="say hi. ok"))
    llm = FakeLLMProvider(FakeLLMScript(tokens=("Hi.", " Next")))
    tts = FakeTTSProvider(FakeTTSScript(chunks=(b"CH",)))
    connection, sender = await _connect(
        sessions, requests_mgr, asr, llm, tts
    )
    await connection.on_message(b"\x00\x01" * 80)
    await connection.on_message(
        json.dumps(_envelope(connection.session_id or "", "audio.eos", 1))
    )

    kinds = [kind for kind, _ in sender.log]
    types = [
        json.loads(payload)["type"]
        for kind, payload in sender.log
        if kind == "text"
    ]
    text_positions = [i for i, k in enumerate(kinds) if k == "text"]
    # The "Hi." segment flushed at the first token: its audio arrives before
    # the second llm.token (streaming pipeline, not batch-at-end).
    binary_positions = [i for i, k in enumerate(kinds) if k == "binary"]
    second_token_pos = text_positions[types.index("llm.token") + 1]
    assert binary_positions[0] < second_token_pos
    assert tts.streams[0].state.segments == ["Hi.", " Next"]


async def test_tts_open_failure_terminates_request(
    sessions: SessionManager, requests_mgr: RequestManager
) -> None:
    from assistant.llm.fake import FakeLLMProvider, FakeLLMScript
    from assistant.tts.fake import FakeTTSProvider, FakeTTSScript

    asr = FakeASRProvider(FakeASRScript(final="hello"))
    llm = FakeLLMProvider(FakeLLMScript(tokens=("Hi.",)))
    tts = FakeTTSProvider(FakeTTSScript(error_at_open="tts down"))
    connection, sender = await _connect(
        sessions, requests_mgr, asr, llm, tts
    )
    await connection.on_message(b"\x00\x01" * 80)
    result = await connection.on_message(
        json.dumps(_envelope(connection.session_id or "", "audio.eos", 1))
    )

    assert result.kind is InboundKind.REQUEST_ERROR
    events = [json.loads(t) for t in sender.texts]
    types = [e["type"] for e in events]
    assert types.count("request.error") == 1
    error = events[types.index("request.error")]
    assert error["payload"]["kind"] == "tts"
    assert "tts down" in error["payload"]["message"]
    assert "tts.done" not in types
    assert "request.completed" not in types


async def test_tts_mid_stream_failure_keeps_previous_audio(
    sessions: SessionManager, requests_mgr: RequestManager
) -> None:
    from assistant.llm.fake import FakeLLMProvider, FakeLLMScript
    from assistant.tts.fake import FakeTTSProvider, FakeTTSScript

    asr = FakeASRProvider(FakeASRScript(final="hello"))
    llm = FakeLLMProvider(FakeLLMScript(tokens=("Hi.", " More")))
    tts = FakeTTSProvider(
        FakeTTSScript(chunks=(b"OK",), error_at_send="synth broke",
                      error_at_send_after=1)
    )
    connection, sender = await _connect(
        sessions, requests_mgr, asr, llm, tts
    )
    await connection.on_message(b"\x00\x01" * 80)
    result = await connection.on_message(
        json.dumps(_envelope(connection.session_id or "", "audio.eos", 1))
    )

    assert result.kind is InboundKind.REQUEST_ERROR
    # Audio already streamed before the failure remains valid.
    assert sender.binaries == [b"OK"]
    events = [json.loads(t) for t in sender.texts]
    types = [e["type"] for e in events]
    error = events[types.index("request.error")]
    assert error["payload"]["kind"] == "tts"
    assert "synth broke" in error["payload"]["message"]
    assert "tts.done" not in types
    assert "request.completed" not in types
    # The TTS stream was cleaned up after the failure.
    assert tts.streams[0].closed is True


async def test_no_llm_output_skips_tts_stage(
    sessions: SessionManager, requests_mgr: RequestManager
) -> None:
    from assistant.llm.fake import FakeLLMProvider
    from assistant.tts.fake import FakeTTSProvider, FakeTTSScript

    asr = FakeASRProvider(FakeASRScript(final="hello"))
    llm = FakeLLMProvider()
    tts = FakeTTSProvider(FakeTTSScript(chunks=(b"X",)))
    connection, sender = await _connect(
        sessions, requests_mgr, asr, llm, tts
    )
    await connection.on_message(b"\x00\x01" * 80)
    result = await connection.on_message(
        json.dumps(_envelope(connection.session_id or "", "audio.eos", 1))
    )

    assert result.kind is InboundKind.REQUEST_FINISHED
    assert sender.binaries == []
    assert tts.streams == []  # no segment ever reached TTS
    events = [json.loads(t) for t in sender.texts]
    assert any(e["type"] == "request.completed" for e in events)
    assert not any(e["type"] == "tts.done" for e in events)


# -- latency metrics (M5) ----------------------------------------------------


async def test_metrics_event_contains_full_latency_payload(
    sessions: SessionManager, requests_mgr: RequestManager
) -> None:
    from assistant.llm.fake import FakeLLMProvider, FakeLLMScript
    from assistant.tts.fake import FakeTTSProvider, FakeTTSScript

    clock = _SteppingClock()
    asr = FakeASRProvider(FakeASRScript(final="hello world"))
    llm = FakeLLMProvider(FakeLLMScript(tokens=("Hi", " there")))
    tts = FakeTTSProvider(FakeTTSScript(chunks=(b"AA",)))
    sender = FakeSender()
    connection = SessionConnection(
        sessions, requests_mgr, sender.send_text,
        asr, llm, tts, send_binary=sender.send_binary, clock=clock,
    )
    await connection.on_connect()
    await connection.on_message(b"\x00\x01" * 80)
    req_id = connection.request_id
    await connection.on_message(json.dumps(
        _envelope(connection.session_id or "", "audio.eos", 1)
    ))

    events = [json.loads(t) for t in sender.texts]
    metrics = [e for e in events if e["type"] == "metrics"]
    assert len(metrics) == 1
    m = metrics[0]
    assert m["request_id"] == req_id
    payload = m["payload"]
    for field in (
        "asr_first_transcript_ms",
        "asr_final_transcript_ms",
        "llm_ttft_ms",
        "tts_ttfb_ms",
        "request_total_ms",
    ):
        assert field in payload, field
        assert isinstance(payload[field], (int, float))
        assert payload[field] >= 0.0

    # ttfb is time from first TTS segment; total is from request start.
    assert payload["tts_ttfb_ms"] < payload["request_total_ms"]
    assert payload["asr_first_transcript_ms"] < payload["asr_final_transcript_ms"]


async def test_no_llm_or_tts_metrics_are_emitted_when_stage_absent(
    sessions: SessionManager, requests_mgr: RequestManager,
    tts: FakeTTSProvider,
) -> None:
    from assistant.llm.fake import FakeLLMProvider

    asr = FakeASRProvider(FakeASRScript(final="hello"))
    llm = FakeLLMProvider()  # no tokens
    clock = _SteppingClock()
    sender = FakeSender()
    connection = SessionConnection(
        sessions, requests_mgr, sender.send_text,
        asr, llm, tts, send_binary=sender.send_binary, clock=clock,
    )
    await connection.on_connect()
    await connection.on_message(b"\x00\x01" * 80)
    await connection.on_message(json.dumps(
        _envelope(connection.session_id or "", "audio.eos", 1)
    ))

    events = [json.loads(t) for t in sender.texts]
    metrics = [e for e in events if e["type"] == "metrics"][0]["payload"]
    assert "llm_ttft_ms" not in metrics
    assert "tts_ttfb_ms" not in metrics
    assert "asr_first_transcript_ms" in metrics
    assert "request_total_ms" in metrics


async def test_failed_request_does_not_emit_successful_completion_metrics(
    sessions: SessionManager, requests_mgr: RequestManager
) -> None:
    from assistant.llm.fake import FakeLLMProvider, FakeLLMScript
    from assistant.tts.fake import FakeTTSProvider, FakeTTSScript

    asr = FakeASRProvider(FakeASRScript(final="hello"))
    llm = FakeLLMProvider(FakeLLMScript(tokens=("Hi.",)))
    tts = FakeTTSProvider(FakeTTSScript(error_at_open="tts down"))
    clock = _SteppingClock()
    sender = FakeSender()
    connection = SessionConnection(
        sessions, requests_mgr, sender.send_text,
        asr, llm, tts, send_binary=sender.send_binary, clock=clock,
    )
    await connection.on_connect()
    await connection.on_message(b"\x00\x01" * 80)
    result = await connection.on_message(json.dumps(
        _envelope(connection.session_id or "", "audio.eos", 1)
    ))

    assert result.kind is InboundKind.REQUEST_ERROR
    events = [json.loads(t) for t in sender.texts]
    assert not any(e["type"] == "request.completed" for e in events)
    # The metrics event is only emitted on successful completion, so it must
    # not appear for a failed/cancelled request either.
    assert not any(e["type"] == "metrics" for e in events)


class _SteppingClock:
    """Deterministic stepping clock (seconds) used across a request."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now
