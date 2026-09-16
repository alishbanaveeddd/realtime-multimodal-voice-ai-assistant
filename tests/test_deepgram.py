
"""Offline, deterministic tests for the Deepgram adapter.

The real Deepgram service is never contacted: the WebSocket connection is
replaced by an in-memory stub implementing the small surface the adapter uses
(async iteration, ``send``, ``close``, ``state``). No API key or network
is required, per `.clinerules` §3 / architecture §15.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from websockets.protocol import State

from assistant.asr import deepgram as dg
from assistant.asr.deepgram import DeepgramASRProvider, DeepgramASRStream
from assistant.asr.interface import (
    ASRConfigurationError,
    ASRResult,
    ASRStreamError,
    TranscriptKind,
)


class StubConnection:
    """Stand-in for ``websockets.asyncio.client.ClientConnection``."""

    def __init__(self, messages: list[str] | None = None) -> None:
        self.messages: list[str] = list(messages or [])
        self.sent: list[bytes | str] = []
        self.closed = False

    @property
    def state(self) -> State:
        return State.CLOSED if self.closed else State.OPEN

    def __aiter__(self) -> StubConnection:
        return self

    async def __anext__(self) -> str:
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)

    async def send(self, data: bytes | str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


class FinishAwareStubConnection(StubConnection):
    """Stub that produces a final result after CloseStream is sent."""

    def __init__(self) -> None:
        super().__init__()
        self._message_available = asyncio.Event()

    def __aiter__(self) -> FinishAwareStubConnection:
        return self

    async def __anext__(self) -> str:
        while not self.messages:
            if self.closed:
                raise StopAsyncIteration

            self._message_available.clear()
            await self._message_available.wait()

        message = self.messages.pop(0)

        if not self.messages:
            self._message_available.clear()

        return message

    async def send(self, data: bytes | str) -> None:
        self.sent.append(data)

        if data == json.dumps({"type": "CloseStream"}):
            self.messages.append(
                _results_message("hello world", top_is_final=True)
            )
            self._message_available.set()

    async def close(self) -> None:
        self.closed = True
        self._message_available.set()


class ErrorFinishAwareStubConnection(FinishAwareStubConnection):
    """Stub that produces a Deepgram Error after CloseStream."""

    def __init__(self, message: str) -> None:
        super().__init__()
        self._error_message = message

    async def send(self, data: bytes | str) -> None:
        self.sent.append(data)

        if data == json.dumps({"type": "CloseStream"}):
            self.messages.append(
                json.dumps(
                    {
                        "type": "Error",
                        "message": self._error_message,
                    }
                )
            )
            self._message_available.set()


class Collector:
    """Async result callback collecting results in order."""

    def __init__(self) -> None:
        self.results: list[ASRResult] = []

    async def __call__(self, result: ASRResult) -> None:
        self.results.append(result)


def _results_message(
    transcript: str,
    *,
    top_is_final: bool,
    channel_is_final: Any = None,
) -> str:
    channel: dict[str, Any] = {
        "alternatives": [{"transcript": transcript}]
    }

    if channel_is_final is not None:
        channel["is_final"] = channel_is_final

    payload: dict[str, Any] = {
        "type": "Results",
        "is_final": top_is_final,
        "channel": channel,
    }

    return json.dumps(payload)


async def _drain(stream: DeepgramASRStream) -> None:
    assert stream._reader_task is not None
    await stream._reader_task


def make_test_stream(
    on_result: Any,
) -> DeepgramASRStream:
    """Create a stream whose CloseStream produces a final transcript."""
    connection = FinishAwareStubConnection()

    stream = DeepgramASRStream(
        connection,
        on_result,
    )

    stream._start_reader()

    return stream


def make_test_stream_with_error(
    message: str,
) -> DeepgramASRStream:
    """Create a stream whose CloseStream produces a Deepgram error."""
    connection = ErrorFinishAwareStubConnection(message)

    stream = DeepgramASRStream(
        connection,
        Collector(),
    )

    stream._start_reader()

    return stream


def make_test_stream_without_final_result() -> DeepgramASRStream:
    """Create a stream that never produces a final transcript."""
    connection = StubConnection()

    stream = DeepgramASRStream(
        connection,
        Collector(),
    )

    stream._start_reader()

    return stream


# == configuration ===========================================================


def test_blank_api_key_raises_configuration_error() -> None:
    for key in ("", "   "):
        try:
            DeepgramASRProvider(api_key=key)
        except ASRConfigurationError:
            pass
        else:
            raise AssertionError(f"blank key {key!r} did not raise")


def test_from_env_missing_key_raises_configuration_error(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)

    try:
        DeepgramASRProvider.from_env()
    except ASRConfigurationError:
        pass
    else:
        raise AssertionError("missing env key did not raise")


# == result parsing ==========================================================


async def test_interim_result_maps_to_partial() -> None:
    collector = Collector()

    stream = DeepgramASRStream(
        StubConnection(
            [_results_message("hel", top_is_final=False)]
        ),
        collector,
    )

    stream._start_reader()
    await _drain(stream)

    assert len(collector.results) == 1
    assert collector.results[0].kind is TranscriptKind.PARTIAL
    assert collector.results[0].transcript == "hel"
    assert collector.results[0].sequence == 1


async def test_top_level_is_final_maps_to_final() -> None:
    collector = Collector()

    stream = DeepgramASRStream(
        StubConnection(
            [_results_message("hello", top_is_final=True)]
        ),
        collector,
    )

    stream._start_reader()
    await _drain(stream)

    assert len(collector.results) == 1
    assert collector.results[0].kind is TranscriptKind.FINAL
    assert collector.results[0].transcript == "hello"


async def test_channel_level_is_final_fallback_maps_to_final() -> None:
    collector = Collector()

    stream = DeepgramASRStream(
        StubConnection(
            [
                _results_message(
                    "hello",
                    top_is_final=False,
                    channel_is_final=True,
                )
            ]
        ),
        collector,
    )

    stream._start_reader()
    await _drain(stream)

    assert len(collector.results) == 1
    assert collector.results[0].kind is TranscriptKind.FINAL


async def test_results_are_numbered_monotonically() -> None:
    collector = Collector()

    stream = DeepgramASRStream(
        StubConnection(
            [
                _results_message("hel", top_is_final=False),
                _results_message("hello", top_is_final=False),
                _results_message("hello", top_is_final=True),
            ]
        ),
        collector,
    )

    stream._start_reader()
    await _drain(stream)

    assert [r.sequence for r in collector.results] == [1, 2, 3]

    assert [r.kind for r in collector.results] == [
        TranscriptKind.PARTIAL,
        TranscriptKind.PARTIAL,
        TranscriptKind.FINAL,
    ]


# == failure handling ========================================================


async def test_error_message_surfaces_on_next_send() -> None:
    collector = Collector()

    err = json.dumps(
        {
            "type": "Error",
            "message": "boom",
        }
    )

    stream = DeepgramASRStream(
        StubConnection([err]),
        collector,
    )

    stream._start_reader()
    await _drain(stream)

    with pytest.raises(ASRStreamError, match="boom"):
        await stream.send_audio(b"\x00\x00")


async def test_malformed_json_does_not_kill_reader() -> None:
    collector = Collector()

    stream = DeepgramASRStream(
        StubConnection(
            [
                "not-json",
                _results_message("hi", top_is_final=True),
            ]
        ),
        collector,
    )

    stream._start_reader()
    await _drain(stream)

    assert len(collector.results) == 1
    assert collector.results[0].kind is TranscriptKind.FINAL


async def test_empty_alternatives_and_unknown_types_are_ignored() -> None:
    collector = Collector()

    empty = json.dumps(
        {
            "type": "Results",
            "is_final": False,
            "channel": {},
        }
    )

    meta = json.dumps({"type": "Metadata"})

    stream = DeepgramASRStream(
        StubConnection([empty, meta]),
        collector,
    )

    stream._start_reader()
    await _drain(stream)

    assert collector.results == []


async def test_interim_without_text_is_ignored() -> None:
    collector = Collector()

    stream = DeepgramASRStream(
        StubConnection(
            [_results_message("", top_is_final=False)]
        ),
        collector,
    )

    stream._start_reader()
    await _drain(stream)

    assert collector.results == []


# == stream operations =======================================================


async def test_send_audio_forwards_bytes() -> None:
    connection = StubConnection()

    stream = DeepgramASRStream(
        connection,
        Collector(),
    )

    frame = b"\x00\x01" * 160

    await stream.send_audio(frame)

    assert connection.sent == [frame]


async def test_finish_sends_closestream_and_waits_for_final() -> None:
    collector = Collector()

    connection = FinishAwareStubConnection()

    stream = DeepgramASRStream(
        connection,
        collector,
    )

    stream._start_reader()

    await stream.finish()

    assert connection.sent == [
        json.dumps({"type": "CloseStream"})
    ]

    assert len(collector.results) == 1
    assert collector.results[0].kind is TranscriptKind.FINAL
    assert collector.results[0].transcript == "hello world"

    await stream.close()


async def test_finish_waits_for_final_result() -> None:
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()

    async def on_result(result: ASRResult) -> None:
        callback_started.set()
        await release_callback.wait()

    stream = make_test_stream(on_result)

    finish_task = asyncio.create_task(stream.finish())

    await callback_started.wait()

    assert not finish_task.done()

    release_callback.set()

    await finish_task
    await stream.close()


async def test_finish_does_not_return_before_final_callback_completes() -> None:
    callback_started = asyncio.Event()
    callback_finished = asyncio.Event()

    async def on_result(result: ASRResult) -> None:
        callback_started.set()
        await asyncio.sleep(0)
        callback_finished.set()

    stream = make_test_stream(on_result)

    await stream.finish()

    assert callback_started.is_set()
    assert callback_finished.is_set()

    await stream.close()


async def test_finish_raises_when_deepgram_returns_error() -> None:
    stream = make_test_stream_with_error(
        "Deepgram failure"
    )

    with pytest.raises(
        ASRStreamError,
        match="Deepgram failure",
    ):
        await stream.finish()

    await stream.close()


async def test_finish_times_out_without_final_result(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        dg,
        "FINISH_TIMEOUT_SECONDS",
        0.01,
    )

    stream = make_test_stream_without_final_result()

    with pytest.raises(
        ASRStreamError,
        match="finish timeout",
    ):
        await stream.finish()

    await stream.close()


async def test_cancel_closes_connection() -> None:
    connection = StubConnection()

    stream = DeepgramASRStream(
        connection,
        Collector(),
    )

    await stream.cancel()

    assert connection.closed is True


async def test_close_closes_connection_and_is_repeatable() -> None:
    connection = StubConnection()

    stream = DeepgramASRStream(
        connection,
        Collector(),
    )

    await stream.close()
    await stream.close()

    assert connection.closed is True

