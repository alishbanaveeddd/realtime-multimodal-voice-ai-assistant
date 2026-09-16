"""M7 resilience and failure-handling tests.

Covers provider failure,
cancellation, disconnect, cleanup, sanitization, streaming, latency, timeouts.
"""

from __future__ import annotations

import json
import os

import pytest

from assistant.asr.fake import FakeASRProvider, FakeASRScript
from assistant.core.request import RequestManager
from assistant.core.session import SessionManager
from assistant.events.envelope import EVENT_VERSION
from assistant.llm.fake import FakeLLMProvider, FakeLLMScript
from assistant.transport.gateway import (
    InboundKind,
    SessionConnection,
    _Timeouts,
)
from assistant.tts.fake import FakeTTSProvider, FakeTTSScript


class FakeSender:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.binaries: list[bytes] = []

    async def send_text(self, payload: str) -> None:
        self.texts.append(payload)

    async def send_binary(self, payload: bytes) -> None:
        self.binaries.append(payload)


def _envelope(
    session_id: str, type_: str, seq: int, **payload: object
) -> dict[str, object]:
    return {
        "type": type_,
        "version": EVENT_VERSION,
        "session_id": session_id,
        "request_id": None,
        "seq": seq,
        "timestamp": 2000.0,
        "payload": payload,
    }


def _events(sender: FakeSender) -> list[dict[str, object]]:
    return [json.loads(t) for t in sender.texts]


def _make_connection(sessions, requests, asr, llm, tts, clock, timeouts=None):
    sender = FakeSender()
    connection = SessionConnection(
        sessions,
        requests,
        sender.send_text,
        asr,
        llm,
        tts,
        send_binary=sender.send_binary,
        clock=clock,
        timeouts=timeouts,
    )
    return connection, sender


class _SteppingClock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value


@pytest.fixture
def sessions():
    return SessionManager(clock=lambda: 1000.0)


@pytest.fixture
def requests_mgr():
    return RequestManager(clock=lambda: 1000.0)


@pytest.fixture
def clock():
    return _SteppingClock(value=1000.0)


@pytest.fixture
def good_asr():
    return FakeASRProvider(FakeASRScript(partials=("partial",), final="hello"))


@pytest.fixture
def good_llm():
    return FakeLLMProvider(FakeLLMScript(tokens=("Hi ", "there")))


@pytest.fixture
def good_tts():
    return FakeTTSProvider(FakeTTSScript(chunks=(b"\x00\x01" * 160,)))


async def _run_full_pipeline(connection, sender):
    sid = connection.session_id
    assert sid is not None
    await connection.on_message(b"\x00\x01" * 160)
    await connection.on_message(json.dumps(_envelope(sid, "audio.eos", 1)))


# === 1. Provider failure: ASR + LLM ===


@pytest.mark.asyncio
async def test_asr_open_failure(sessions, requests_mgr, clock):
    asr = FakeASRProvider(FakeASRScript(error_at_open="DG fail"))
    llm = FakeLLMProvider()
    tts = FakeTTSProvider(FakeTTSScript())
    conn, sender = _make_connection(sessions, requests_mgr, asr, llm, tts, clock)
    await conn.on_connect()
    result = await conn.on_message(b"\x00\x01" * 160)
    assert result.kind is InboundKind.REQUEST_ERROR
    events = _events(sender)
    errs = [e for e in events if e["type"] == "request.error"]
    assert len(errs) == 1 and errs[0]["payload"]["kind"] == "asr"
    assert not any(e["type"] == "request.completed" for e in events)
    assert not any(e["type"] == "metrics" for e in events)
    assert conn.request_id is None
    # open_stream failed before creating a stream, so streams list is empty
    assert asr.streams == []


@pytest.mark.asyncio
async def test_asr_finish_failure(sessions, requests_mgr, clock):
    asr = FakeASRProvider(FakeASRScript(final="hello", error_at_finish="finish fail"))
    llm = FakeLLMProvider()
    tts = FakeTTSProvider(FakeTTSScript())
    conn, sender = _make_connection(sessions, requests_mgr, asr, llm, tts, clock)
    await conn.on_connect()
    sid = conn.session_id
    await conn.on_message(b"\x00\x01" * 160)
    result = await conn.on_message(json.dumps(_envelope(sid, "audio.eos", 1)))
    assert result.kind is InboundKind.REQUEST_ERROR
    events = _events(sender)
    errs = [e for e in events if e["type"] == "request.error"]
    assert len(errs) == 1 and errs[0]["payload"]["kind"] == "asr"
    assert not any(e["type"] == "request.completed" for e in events)
    assert conn.request_id is None
    assert asr.streams[0].closed


@pytest.mark.asyncio
async def test_llm_open_failure(sessions, requests_mgr, clock, good_asr, good_tts):
    llm = FakeLLMProvider(FakeLLMScript(error_at_open="openai fail"))
    conn, sender = _make_connection(
        sessions, requests_mgr, good_asr, llm, good_tts, clock
    )
    await conn.on_connect()
    await _run_full_pipeline(conn, sender)
    events = _events(sender)
    errs = [e for e in events if e["type"] == "request.error"]
    assert len(errs) == 1 and errs[0]["payload"]["kind"] == "llm"
    assert not any(e["type"] == "request.completed" for e in events)
    assert not any(e["type"] == "metrics" for e in events)
    assert conn.request_id is None
    # LLM open failed - no LLM stream created; TTS never reached
    assert llm.streams == []
    assert good_tts.streams == []


@pytest.mark.asyncio
async def test_llm_mid_stream_failure(
    sessions, requests_mgr, clock, good_asr, good_tts
):
    llm = FakeLLMProvider(
        FakeLLMScript(tokens=("Hi ", "there "), error_mid_stream="gen fail")
    )
    conn, sender = _make_connection(
        sessions, requests_mgr, good_asr, llm, good_tts, clock
    )
    await conn.on_connect()
    await _run_full_pipeline(conn, sender)
    events = _events(sender)
    tokens = [e for e in events if e["type"] == "llm.token"]
    assert len(tokens) >= 1
    errs = [e for e in events if e["type"] == "request.error"]
    assert len(errs) == 1 and errs[0]["payload"]["kind"] == "llm"
    assert not any(e["type"] == "request.completed" for e in events)
    assert not any(e["type"] == "metrics" for e in events)
    assert conn.request_id is None
    assert llm.streams[0].closed
    # TTS stream may or may not have been created depending on segmentation
    if good_tts.streams:
        assert good_tts.streams[0].closed


# === 2. Provider failure: TTS ===


@pytest.mark.asyncio
async def test_tts_open_failure(sessions, requests_mgr, clock, good_asr, good_llm):
    tts = FakeTTSProvider(FakeTTSScript(error_at_open="el fail"))
    conn, sender = _make_connection(
        sessions, requests_mgr, good_asr, good_llm, tts, clock
    )
    await conn.on_connect()
    await _run_full_pipeline(conn, sender)
    events = _events(sender)
    errs = [e for e in events if e["type"] == "request.error"]
    assert len(errs) == 1 and errs[0]["payload"]["kind"] == "tts"
    assert not any(e["type"] == "request.completed" for e in events)
    assert not any(e["type"] == "tts.done" for e in events)
    assert not any(e["type"] == "metrics" for e in events)
    assert conn.request_id is None
    # TTS open failed - no TTS stream created
    assert tts.streams == []


@pytest.mark.asyncio
async def test_tts_mid_stream_failure(sessions, requests_mgr, clock, good_asr):
    a1 = b"\x00\x01" * 160
    a2 = b"\x02\x03" * 160
    tts = FakeTTSProvider(
        FakeTTSScript(chunks=(a1, a2), error_at_send="send fail", error_at_send_after=1)
    )
    # Use an LLM that produces two sentence segments so TTS gets two send_text calls
    llm = FakeLLMProvider(FakeLLMScript(tokens=("First. ", "Second.")))
    conn, sender = _make_connection(sessions, requests_mgr, good_asr, llm, tts, clock)
    await conn.on_connect()
    await _run_full_pipeline(conn, sender)
    events = _events(sender)
    errs = [e for e in events if e["type"] == "request.error"]
    assert len(errs) == 1 and errs[0]["payload"]["kind"] == "tts"
    assert len(sender.binaries) >= 1
    assert sender.binaries[0] == a1
    assert not any(e["type"] == "request.completed" for e in events)
    assert not any(e["type"] == "tts.done" for e in events)
    assert not any(e["type"] == "metrics" for e in events)
    assert conn.request_id is None
    assert tts.streams[0].closed


@pytest.mark.asyncio
async def test_tts_wait_failure(sessions, requests_mgr, good_asr, good_llm):
    tts = FakeTTSProvider(
        FakeTTSScript(chunks=(b"\x00\x01" * 160,), error_at_wait="wait fail")
    )
    conn, sender = _make_connection(
        sessions,
        requests_mgr,
        good_asr,
        good_llm,
        tts,
        clock=_SteppingClock(value=1000.0),
    )
    await conn.on_connect()
    await _run_full_pipeline(conn, sender)
    events = _events(sender)
    errs = [e for e in events if e["type"] == "request.error"]
    assert len(errs) == 1 and errs[0]["payload"]["kind"] == "tts"
    assert not any(e["type"] == "request.completed" for e in events)
    assert not any(e["type"] == "tts.done" for e in events)


# === 3. Cancellation ===


@pytest.mark.asyncio
async def test_cancel_during_asr(sessions, requests_mgr, clock):
    asr = FakeASRProvider(FakeASRScript(partials=("partial",), final="hello"))
    conn, sender = _make_connection(
        sessions,
        requests_mgr,
        asr,
        FakeLLMProvider(),
        FakeTTSProvider(FakeTTSScript()),
        clock,
    )
    await conn.on_connect()
    await conn.on_message(b"\x00\x01" * 160)
    assert conn.request_id is not None
    await conn._cancel_active_request()
    events = _events(sender)
    cancels = [e for e in events if e["type"] == "request.canceled"]
    assert len(cancels) == 1
    assert not any(e["type"] == "request.completed" for e in events)
    assert not any(e["type"] == "transcript.final" for e in events)
    assert conn.request_id is None
    assert asr.streams[0].canceled
    assert asr.streams[0].closed


@pytest.mark.asyncio
async def test_cancel_via_inbound_event(sessions, requests_mgr, clock):
    """Send request.canceled event while request is active -> cleanup."""
    asr = FakeASRProvider(FakeASRScript(partials=("partial",), final="hello"))
    llm = FakeLLMProvider()
    tts = FakeTTSProvider(FakeTTSScript())
    conn, sender = _make_connection(sessions, requests_mgr, asr, llm, tts, clock)
    await conn.on_connect()
    sid = conn.session_id

    # Open ASR stream, then send cancel event before eos
    await conn.on_message(b"\x00\x01" * 160)
    assert conn.request_id is not None
    req_id = conn.request_id

    # Client sends cancel
    result = await conn.on_message(json.dumps(_envelope(sid, "request.canceled", 2)))
    assert result.kind is InboundKind.ACCEPTED

    events = _events(sender)
    cancels = [e for e in events if e["type"] == "request.canceled"]
    assert len(cancels) == 1
    assert cancels[0]["request_id"] == req_id

    assert not any(e["type"] == "request.completed" for e in events)
    assert conn.request_id is None
    assert asr.streams[0].canceled and asr.streams[0].closed


@pytest.mark.asyncio
async def test_cancel_asr_stream_cleanup(sessions, requests_mgr, clock):
    """Cancel during ASR cleans up the ASR stream properly."""
    asr = FakeASRProvider(FakeASRScript(partials=("partial",), final="hello"))
    llm = FakeLLMProvider()
    tts = FakeTTSProvider(FakeTTSScript())
    conn, sender = _make_connection(sessions, requests_mgr, asr, llm, tts, clock)
    await conn.on_connect()

    await conn.on_message(b"\x00\x01" * 160)
    assert conn.request_id is not None
    assert len(asr.streams) == 1

    await conn._cancel_active_request()

    # ASR stream should be both canceled and closed
    assert asr.streams[0].canceled
    assert asr.streams[0].closed


# === 4. WebSocket disconnect ===


@pytest.mark.asyncio
async def test_disconnect_cancels_request(sessions, requests_mgr, clock):
    asr = FakeASRProvider(FakeASRScript(partials=("partial",), final="hello"))
    conn, sender = _make_connection(
        sessions,
        requests_mgr,
        asr,
        FakeLLMProvider(),
        FakeTTSProvider(FakeTTSScript()),
        clock,
    )
    await conn.on_connect()
    sid = conn.session_id
    await conn.on_message(b"\x00\x01" * 160)
    assert conn.request_id is not None
    await conn.on_close()
    events = _events(sender)
    assert any(e["type"] == "request.canceled" for e in events)
    assert any(e["type"] == "session.ended" for e in events)
    assert not any(e["type"] == "request.completed" for e in events)
    sess = sessions.get(sid)
    assert sess is not None and sess.state.name == "ENDED"


@pytest.mark.asyncio
async def test_disconnect_after_completion_is_noop(
    sessions, requests_mgr, clock, good_asr, good_llm, good_tts
):
    """Disconnect after request completed - streams already cleaned up."""
    conn, sender = _make_connection(
        sessions, requests_mgr, good_asr, good_llm, good_tts, clock
    )
    await conn.on_connect()
    await _run_full_pipeline(conn, sender)
    assert conn.request_id is None  # Already cleaned up
    # All streams should be closed from the success path
    assert good_asr.streams[0].closed
    assert good_llm.streams[0].closed
    if good_tts.streams:
        assert good_tts.streams[0].closed
    # Disconnect after completion - should not raise
    await conn.on_close()
    assert conn.request_id is None


# === 5. Error sanitization ===


@pytest.mark.asyncio
async def test_error_sanitizes_api_key(sessions, requests_mgr, clock):
    fake_key = "sk-test-1234567890abcdef"
    os.environ["OPENAI_API_KEY"] = fake_key
    try:
        asr = FakeASRProvider(FakeASRScript(error_at_open=f"Key: {fake_key} used"))
        conn, sender = _make_connection(
            sessions,
            requests_mgr,
            asr,
            FakeLLMProvider(),
            FakeTTSProvider(FakeTTSScript()),
            clock,
        )
        await conn.on_connect()
        await conn.on_message(b"\x00\x01" * 160)
        events = _events(sender)
        errs = [e for e in events if e["type"] == "request.error"]
        assert len(errs) == 1
        msg = errs[0]["payload"]["message"]
        assert fake_key not in msg
        assert "<redacted>" in msg
    finally:
        os.environ.pop("OPENAI_API_KEY", None)


@pytest.mark.asyncio
async def test_error_sanitizes_multiple_keys(sessions, requests_mgr, clock):
    dg, oa, el = "dg-key-123", "sk-key-456", "el-key-789"
    os.environ["DEEPGRAM_API_KEY"] = dg
    os.environ["OPENAI_API_KEY"] = oa
    os.environ["ELEVENLABS_API_KEY"] = el
    try:
        asr = FakeASRProvider(FakeASRScript(error_at_open=f"K: {dg} {oa} {el}"))
        conn, sender = _make_connection(
            sessions,
            requests_mgr,
            asr,
            FakeLLMProvider(),
            FakeTTSProvider(FakeTTSScript()),
            clock,
        )
        await conn.on_connect()
        await conn.on_message(b"\x00\x01" * 160)
        events = _events(sender)
        errs = [e for e in events if e["type"] == "request.error"]
        msg = errs[0]["payload"]["message"]
        for k in (dg, oa, el):
            assert k not in msg
        assert msg.count("<redacted>") == 3
    finally:
        for k in ("DEEPGRAM_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
            os.environ.pop(k, None)


@pytest.mark.asyncio
async def test_error_preserves_non_secret(sessions, requests_mgr, clock):
    asr = FakeASRProvider(FakeASRScript(error_at_open="Service unavailable"))
    conn, sender = _make_connection(
        sessions,
        requests_mgr,
        asr,
        FakeLLMProvider(),
        FakeTTSProvider(FakeTTSScript()),
        clock,
    )
    await conn.on_connect()
    await conn.on_message(b"\x00\x01" * 160)
    events = _events(sender)
    errs = [e for e in events if e["type"] == "request.error"]
    msg = errs[0]["payload"]["message"]
    assert msg == "Service unavailable"
    assert "<redacted>" not in msg


# === 6. Cleanup guarantees ===


@pytest.mark.asyncio
async def test_cleanup_after_asr_failure(sessions, requests_mgr, clock):
    asr = FakeASRProvider(FakeASRScript(error_at_open="fail"))
    conn, sender = _make_connection(
        sessions,
        requests_mgr,
        asr,
        FakeLLMProvider(),
        FakeTTSProvider(FakeTTSScript()),
        clock,
    )
    await conn.on_connect()
    await conn.on_message(b"\x00\x01" * 160)
    assert asr.streams == []
    assert conn.request_id is None


@pytest.mark.asyncio
async def test_cleanup_after_llm_failure(
    sessions, requests_mgr, clock, good_asr, good_tts
):
    llm = FakeLLMProvider(FakeLLMScript(error_at_open="fail"))
    conn, sender = _make_connection(
        sessions, requests_mgr, good_asr, llm, good_tts, clock
    )
    await conn.on_connect()
    await _run_full_pipeline(conn, sender)
    assert good_asr.streams[0].closed
    assert llm.streams == []
    assert good_tts.streams == []
    assert conn.request_id is None


@pytest.mark.asyncio
async def test_cleanup_after_tts_failure(
    sessions, requests_mgr, clock, good_asr, good_llm
):
    tts = FakeTTSProvider(FakeTTSScript(error_at_open="fail"))
    conn, sender = _make_connection(
        sessions, requests_mgr, good_asr, good_llm, tts, clock
    )
    await conn.on_connect()
    await _run_full_pipeline(conn, sender)
    assert good_asr.streams[0].closed
    assert good_llm.streams[0].closed
    assert tts.streams == []
    assert conn.request_id is None


@pytest.mark.asyncio
async def test_cleanup_after_cancel(sessions, requests_mgr, clock):
    asr = FakeASRProvider(FakeASRScript(partials=("partial",), final="hello"))
    conn, sender = _make_connection(
        sessions,
        requests_mgr,
        asr,
        FakeLLMProvider(),
        FakeTTSProvider(FakeTTSScript()),
        clock,
    )
    await conn.on_connect()
    await conn.on_message(b"\x00\x01" * 160)
    await conn._cancel_active_request()
    assert asr.streams[0].canceled and asr.streams[0].closed
    assert conn.request_id is None


# === 7. No events after terminal state ===


@pytest.mark.asyncio
async def test_no_events_after_completion(
    sessions, requests_mgr, clock, good_asr, good_llm, good_tts
):
    conn, sender = _make_connection(
        sessions, requests_mgr, good_asr, good_llm, good_tts, clock
    )
    await conn.on_connect()
    sid = conn.session_id
    await _run_full_pipeline(conn, sender)
    before = list(sender.texts)
    assert conn.request_id is None
    result = await conn.on_message(json.dumps(_envelope(sid, "audio.eos", 2)))
    # After completion, audio.eos is ignored (no active request) - ACCEPTED
    assert result.kind is InboundKind.ACCEPTED
    assert len(sender.texts) == len(before)


@pytest.mark.asyncio
async def test_no_events_after_error(sessions, requests_mgr, clock):
    asr = FakeASRProvider(FakeASRScript(error_at_open="fail"))
    conn, sender = _make_connection(
        sessions,
        requests_mgr,
        asr,
        FakeLLMProvider(),
        FakeTTSProvider(FakeTTSScript()),
        clock,
    )
    await conn.on_connect()
    sid = conn.session_id
    await conn.on_message(b"\x00\x01" * 160)
    before = list(sender.texts)
    assert conn.request_id is None
    result = await conn.on_message(json.dumps(_envelope(sid, "audio.eos", 2)))
    # After error, audio.eos is ignored (no active request) - ACCEPTED
    assert result.kind is InboundKind.ACCEPTED
    assert len(sender.texts) == len(before)


@pytest.mark.asyncio
async def test_no_events_after_cancel(sessions, requests_mgr, clock):
    asr = FakeASRProvider(FakeASRScript(partials=("partial",), final="hello"))
    conn, sender = _make_connection(
        sessions,
        requests_mgr,
        asr,
        FakeLLMProvider(),
        FakeTTSProvider(FakeTTSScript()),
        clock,
    )
    await conn.on_connect()
    sid = conn.session_id
    await conn.on_message(b"\x00\x01" * 160)
    await conn._cancel_active_request()
    before = list(sender.texts)
    assert conn.request_id is None
    result = await conn.on_message(json.dumps(_envelope(sid, "audio.eos", 2)))
    # After cancel, audio.eos is ignored (no active request) - ACCEPTED
    assert result.kind is InboundKind.ACCEPTED
    assert len(sender.texts) == len(before)


# === 8. State consistency ===


@pytest.mark.asyncio
async def test_cancel_prevents_late_tts_done(sessions, requests_mgr, clock, good_asr):
    tts = FakeTTSProvider(FakeTTSScript(chunks=(b"\x00\x01" * 160, b"\x02\x03" * 160)))
    llm = FakeLLMProvider(FakeLLMScript(tokens=("Hi ", "there")))
    conn, sender = _make_connection(sessions, requests_mgr, good_asr, llm, tts, clock)
    await conn.on_connect()
    await conn.on_message(b"\x00\x01" * 160)
    await conn._cancel_active_request()
    sid = conn.session_id
    result = await conn.on_message(json.dumps(_envelope(sid, "audio.eos", 1)))
    # After cancel, audio.eos is ignored (no active request) - ACCEPTED
    assert result.kind is InboundKind.ACCEPTED
    events = _events(sender)
    assert not any(e["type"] == "tts.done" for e in events)
    assert not any(e["type"] == "request.completed" for e in events)


@pytest.mark.asyncio
async def test_llm_failure_prevents_tts_and_completion(
    sessions, requests_mgr, clock, good_asr, good_tts
):
    llm = FakeLLMProvider(FakeLLMScript(tokens=("Hi ",), error_mid_stream="fail"))
    conn, sender = _make_connection(
        sessions, requests_mgr, good_asr, llm, good_tts, clock
    )
    await conn.on_connect()
    await _run_full_pipeline(conn, sender)
    events = _events(sender)
    assert not any(e["type"] == "tts.done" for e in events)
    assert not any(e["type"] == "request.completed" for e in events)
    assert not any(e["type"] == "metrics" for e in events)


@pytest.mark.asyncio
async def test_tts_failure_prevents_completion(sessions, requests_mgr, clock, good_asr):
    tts = FakeTTSProvider(FakeTTSScript(error_at_send="fail"))
    llm = FakeLLMProvider(FakeLLMScript(tokens=("First. ", "Second.")))
    conn, sender = _make_connection(sessions, requests_mgr, good_asr, llm, tts, clock)
    await conn.on_connect()
    await _run_full_pipeline(conn, sender)
    events = _events(sender)
    assert not any(e["type"] == "request.completed" for e in events)
    assert not any(e["type"] == "metrics" for e in events)
    assert any(e["type"] == "request.error" for e in events)


# === 9. Streaming preserved ===


@pytest.mark.asyncio
async def test_llm_tokens_streamed_before_failure(
    sessions, requests_mgr, clock, good_asr, good_tts
):
    llm = FakeLLMProvider(
        FakeLLMScript(tokens=("Hello ", "world "), error_mid_stream="fail")
    )
    conn, sender = _make_connection(
        sessions, requests_mgr, good_asr, llm, good_tts, clock
    )
    await conn.on_connect()
    await _run_full_pipeline(conn, sender)
    events = _events(sender)
    tokens = [e for e in events if e["type"] == "llm.token"]
    assert len(tokens) >= 1


@pytest.mark.asyncio
async def test_tts_audio_preserved_before_failure(
    sessions, requests_mgr, clock, good_asr
):
    a1 = b"\x00\x01" * 160
    a2 = b"\x02\x03" * 160
    tts = FakeTTSProvider(
        FakeTTSScript(chunks=(a1, a2), error_at_send="fail", error_at_send_after=1)
    )
    llm = FakeLLMProvider(FakeLLMScript(tokens=("First. ", "Second.")))
    conn, sender = _make_connection(sessions, requests_mgr, good_asr, llm, tts, clock)
    await conn.on_connect()
    await _run_full_pipeline(conn, sender)
    assert len(sender.binaries) >= 1
    assert sender.binaries[0] == a1


# === 10. Latency + timeout ===


@pytest.mark.asyncio
async def test_failed_request_no_metrics(
    sessions, requests_mgr, clock, good_asr, good_llm
):
    tts = FakeTTSProvider(FakeTTSScript(error_at_open="down"))
    conn, sender = _make_connection(
        sessions, requests_mgr, good_asr, good_llm, tts, clock
    )
    await conn.on_connect()
    await _run_full_pipeline(conn, sender)
    events = _events(sender)
    assert not any(e["type"] == "metrics" for e in events)
    assert not any(e["type"] == "request.completed" for e in events)


@pytest.mark.asyncio
async def test_success_emits_all_latency(
    sessions, requests_mgr, clock, good_asr, good_llm, good_tts
):
    conn, sender = _make_connection(
        sessions, requests_mgr, good_asr, good_llm, good_tts, clock
    )
    await conn.on_connect()
    await _run_full_pipeline(conn, sender)
    events = _events(sender)
    metrics = [e for e in events if e["type"] == "metrics"]
    assert len(metrics) == 1
    m = metrics[0]["payload"]
    for f in (
        "asr_first_transcript_ms",
        "asr_final_transcript_ms",
        "llm_ttft_ms",
        "tts_ttfb_ms",
        "request_total_ms",
    ):
        assert f in m, f"missing {f}"
        assert isinstance(m[f], (int, float)) and m[f] >= 0.0


@pytest.mark.asyncio
async def test_custom_timeouts_used(
    sessions, requests_mgr, clock, good_asr, good_llm, good_tts
):
    timeouts = _Timeouts(
        llm_wait_seconds=5.0,
        tts_send_seconds=3.0,
        tts_finish_seconds=2.0,
        tts_wait_seconds=10.0,
    )
    conn, sender = _make_connection(
        sessions, requests_mgr, good_asr, good_llm, good_tts, clock, timeouts=timeouts
    )
    await conn.on_connect()
    await _run_full_pipeline(conn, sender)
    events = _events(sender)
    assert any(e["type"] == "request.completed" for e in events)
