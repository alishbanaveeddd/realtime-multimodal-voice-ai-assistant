from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from websockets.protocol import State

from assistant.asr.deepgram import (
    DeepgramASRProvider,
    DeepgramASRStream,
)
from assistant.asr.interface import (
    ASRConfigurationError,
    ASRResult,
    ASRStreamError,
    TranscriptKind,
)


class StubConnection:
    def __init__(self, messages: list[str] | None = None) -> None:
        self.messages = list(messages or [])
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
    """
    Stub connection used by the finish() test.

    Unlike the normal finite StubConnection, this connection stays alive
    while waiting for finish() to send CloseStream. Once CloseStream is
    received, it makes a final Deepgram Results message available.
    """

    def __init__(self) -> None:
        super().__init__()
        self._message_available = asyncio.Event()

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


class Collector:
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

    payload = {
        "type": "Results",
        "is_final": top_is_final,
        "channel": channel,
    }

    return json.dumps(payload)


async def _drain(stream: DeepgramASRStream) -> None:
    assert stream._reader_task is not None
    await stream._reader_task


@pytest.mark.asyncio
async def test_provider_requires_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)

    with pytest.raises(ASRConfigurationError):
        DeepgramASRProvider.from_env()


@pytest.mark.asyncio
async def test_provider_reads_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")

    provider = DeepgramASRProvider.from_env()

    assert provider.api_key == "test-key"


@pytest.mark.asyncio
async def test_interim_result_is_partial() -> None:
    collector = Collector()

    message = _results_message(
        "hello",
        top_is_final=False,
    )

    stream = DeepgramASRStream(
        StubConnection([message]),
        collector,
    )

    await _drain(stream)

    assert len(collector.results) == 1
    assert collector.results[0].kind is TranscriptKind.PARTIAL
    assert collector.results[0].transcript == "hello"
    assert collector.results[0].sequence == 1

    await stream.close()


@pytest.mark.asyncio
async def test_top_level_final_result_is_final() -> None:
    collector = Collector()

    message = _results_message(
        "hello world",
        top_is_final=True,
    )

    stream = DeepgramASRStream(
        StubConnection([message]),
        collector,
    )

    await _drain(stream)

    assert len(collector.results) == 1
    assert collector.results[0].kind is TranscriptKind.FINAL
    assert collector.results[0].transcript == "hello world"
    assert collector.results[0].is_final is True

    await stream.close()


@pytest.mark.asyncio
async def test_channel_level_final_result_is_final() -> None:
    collector = Collector()

    message = _results_message(
        "hello world",
        top_is_final=False,
        channel_is_final=True,
    )

    stream = DeepgramASRStream(
        StubConnection([message]),
        collector,
    )

    await _drain(stream)

    assert len(collector.results) == 1
    assert collector.results[0].kind is TranscriptKind.FINAL
    assert collector.results[0].transcript == "hello world"

    await stream.close()


@pytest.mark.asyncio
async def test_sequences_are_monotonic() -> None:
    collector = Collector()

    messages = [
        _results_message("hello", top_is_final=False),
        _results_message("hello world", top_is_final=True),
    ]

    stream = DeepgramASRStream(
        StubConnection(messages),
        collector,
    )

    await _drain(stream)

    assert [result.sequence for result in collector.results] == [1, 2]

    await stream.close()


@pytest.mark.asyncio
async def test_send_audio_forwards_bytes() -> None:
    collector = Collector()
    connection = StubConnection()

    stream = DeepgramASRStream(
        connection,
        collector,
    )

    audio = b"\x01\x02\x03\x04"

    await stream.send_audio(audio)

    assert connection.sent == [audio]

    await stream.close()


@pytest.mark.asyncio
async def test_malformed_json_is_ignored() -> None:
    collector = Collector()

    stream = DeepgramASRStream(
        StubConnection(["not valid json"]),
        collector,
    )

    await _drain(stream)

    assert collector.results == []

    await stream.close()


@pytest.mark.asyncio
async def test_empty_alternatives_are_ignored() -> None:
    collector = Collector()

    message = json.dumps(
        {
            "type": "Results",
            "is_final": True,
            "channel": {
                "alternatives": [],
            },
        }
    )

    stream = DeepgramASRStream(
        StubConnection([message]),
        collector,
    )

    await _drain(stream)

    assert collector.results == []

    await stream.close()


@pytest.mark.asyncio
async def test_unknown_message_type_is_ignored() -> None:
    collector = Collector()

    message = json.dumps(
        {
            "type": "Metadata",
            "request_id": "test-request",
        }
    )

    stream = DeepgramASRStream(
        StubConnection([message]),
        collector,
    )

    await _drain(stream)

    assert collector.results == []

    await stream.close()


@pytest.mark.asyncio
async def test_interim_without_text_is_ignored() -> None:
    collector = Collector()

    message = json.dumps(
        {
            "type": "Results",
            "is_final": False,
            "channel": {
                "alternatives": [
                    {},
                ],
            },
        }
    )

    stream = DeepgramASRStream(
        StubConnection([message]),
        collector,
    )

    await _drain(stream)

    assert collector.results == []

    await stream.close()


@pytest.mark.asyncio
async def test_error_is_stored_and_surfaces_on_next_send() -> None:
    collector = Collector()

    message = json.dumps(
        {
            "type": "Error",
            "description": "Deepgram error",
        }
    )

    stream = DeepgramASRStream(
        StubConnection([message]),
        collector,
    )

    await _drain(stream)

    with pytest.raises(ASRStreamError, match="Deepgram error"):
        await stream.send_audio(b"\x00\x00")

    await stream.close()


@pytest.mark.asyncio
async def test_finish_surfaces_deepgram_error() -> None:
    collector = Collector()

    message = json.dumps(
        {
            "type": "Error",
            "description": "Deepgram error",
        }
    )

    stream = DeepgramASRStream(
        StubConnection([message]),
        collector,
    )

    await _drain(stream)

    with pytest.raises(ASRStreamError, match="Deepgram error"):
        await stream.finish()

    await stream.close()


@pytest.mark.asyncio
async def test_finish_sends_closestream_and_waits_for_final() -> None:
    collector = Collector()
    connection = FinishAwareStubConnection()

    stream = DeepgramASRStream(
        connection,
        collector,
    )

    await stream.finish()

    assert connection.sent == [
        json.dumps({"type": "CloseStream"})
    ]

    assert len(collector.results) == 1
    assert collector.results[0].kind is TranscriptKind.FINAL
    assert collector.results[0].transcript == "hello world"

    await stream.close()


@pytest.mark.asyncio
async def test_cancel_closes_connection() -> None:
    collector = Collector()
    connection = StubConnection()

    stream = DeepgramASRStream(
        connection,
        collector,
    )

    await stream.cancel()

    assert connection.closed is True


@pytest.mark.asyncio
async def test_close_closes_connection() -> None:
    collector = Collector()
    connection = StubConnection()

    stream = DeepgramASRStream(
        connection,
        collector,
    )

    await stream.close()

    assert connection.closed is True
