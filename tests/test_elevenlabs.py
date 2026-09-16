"""Offline tests for the ElevenLabs TTS adapter (deterministic, no network).

The real service is never contacted: the WebSocket connection is replaced by
an in-memory stub implementing the small surface the adapter uses (async
iteration, ``send``, ``close``, ``state``). No API key is required, per
`.clinerules` §3 / architecture §15.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from websockets.protocol import State

from assistant.tts import elevenlabs as el
from assistant.tts.elevenlabs import (
    ElevenLabsTTSProvider,
    ElevenLabsTTSStream,
)
from assistant.tts.interface import (
    TTSConfigurationError,
    TTSStreamError,
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


class AudioCollector:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    async def __call__(self, chunk: bytes) -> None:
        self.chunks.append(chunk)


def _audio_msg(chunk: bytes) -> str:
    return json.dumps({"audio": base64.b64encode(chunk).decode()})


def _patch_connect(
    monkeypatch: Any, connection: StubConnection, captured: dict[str, Any]
) -> None:
    async def fake_connect(url: str, **kwargs: Any) -> StubConnection:
        captured["url"] = url
        captured["headers"] = kwargs.get("additional_headers")
        return connection

    monkeypatch.setattr(el, "connect", fake_connect)


async def _drain(stream: ElevenLabsTTSStream) -> None:
    assert stream._reader_task is not None
    await stream._reader_task


# == configuration ===========================================================


def test_blank_api_key_raises_configuration_error() -> None:
    with pytest.raises(TTSConfigurationError):
        ElevenLabsTTSProvider(api_key="  ", voice_id="voice-1")


def test_blank_voice_id_raises_configuration_error() -> None:
    with pytest.raises(TTSConfigurationError):
        ElevenLabsTTSProvider(api_key="k", voice_id="")


def test_from_env_missing_vars_raises(monkeypatch: Any) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    with pytest.raises(TTSConfigurationError):
        ElevenLabsTTSProvider.from_env()


# == connection contract =====================================================


async def test_open_stream_uses_approved_contract_and_key(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}
    connection = StubConnection()
    _patch_connect(monkeypatch, connection, captured)
    provider = ElevenLabsTTSProvider(
        api_key="test-key", voice_id="voice-1", base_url="wss://api.test"
    )
    stream = await provider.open_stream(on_audio=AudioCollector())
    await stream.close()

    assert captured["url"] == (
        "wss://api.test/voice-1/stream-input?output_format=pcm_16000"
    )
    assert captured["headers"] == {"xi-api-key": "test-key"}
    # The BOS message was sent on open.
    assert len(connection.sent) == 1
    bos = json.loads(str(connection.sent[0]))
    assert bos["text"] == " "
    assert "voice_settings" in bos


# == streaming behavior ======================================================


async def test_send_text_forwards_protocol_message(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    connection = StubConnection()
    _patch_connect(monkeypatch, connection, captured)
    provider = ElevenLabsTTSProvider(
        api_key="k", voice_id="v", base_url="wss://api.test"
    )
    stream = await provider.open_stream(on_audio=AudioCollector())

    await stream.send_text("Hi.")
    await stream.close()

    sent = json.loads(str(connection.sent[1]))
    assert sent == {"text": "Hi.", "try_trigger_generation": False}


async def test_finish_sends_eos_empty_text(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    connection = StubConnection()
    _patch_connect(monkeypatch, connection, captured)
    provider = ElevenLabsTTSProvider(
        api_key="k", voice_id="v", base_url="wss://api.test"
    )
    stream = await provider.open_stream(on_audio=AudioCollector())

    await stream.finish()
    await stream.close()

    assert json.loads(str(connection.sent[1])) == {"text": ""}


async def test_audio_messages_are_decoded_and_streamed(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}
    chunk_a, chunk_b = b"\x01\x02", b"\x03\x04"
    connection = StubConnection(
        [_audio_msg(chunk_a), _audio_msg(chunk_b), '{"isFinal": true}']
    )
    _patch_connect(monkeypatch, connection, captured)
    provider = ElevenLabsTTSProvider(
        api_key="k", voice_id="v", base_url="wss://api.test"
    )
    collector = AudioCollector()
    stream = await provider.open_stream(on_audio=collector)
    await _drain(stream)
    await stream.close()

    assert collector.chunks == [chunk_a, chunk_b]


async def test_malformed_audio_and_json_are_skipped(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    connection = StubConnection(
        [
            json.dumps({"audio": "!!!not-base64!!!"}),
            "not-json",
            _audio_msg(b"\x09"),
            '{"isFinal": true}',
        ]
    )
    _patch_connect(monkeypatch, connection, captured)
    provider = ElevenLabsTTSProvider(
        api_key="k", voice_id="v", base_url="wss://api.test"
    )
    collector = AudioCollector()
    stream = await provider.open_stream(on_audio=collector)
    await _drain(stream)
    await stream.close()

    assert collector.chunks == [b"\x09"]


async def test_detail_message_raises_on_wait(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    connection = StubConnection(['{"detail": {"message": "quota exceeded"}}'])
    _patch_connect(monkeypatch, connection, captured)
    provider = ElevenLabsTTSProvider(
        api_key="k", voice_id="v", base_url="wss://api.test"
    )
    stream = await provider.open_stream(on_audio=AudioCollector())

    with pytest.raises(TTSStreamError, match="quota exceeded"):
        await stream.wait()
    await stream.close()


async def test_error_detail_does_not_leak_api_key(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    connection = StubConnection(['{"detail": {"status": "invalid_key"}}'])
    _patch_connect(monkeypatch, connection, captured)
    provider = ElevenLabsTTSProvider(
        api_key="super-secret-key", voice_id="v", base_url="wss://api.test"
    )
    stream = await provider.open_stream(on_audio=AudioCollector())

    with pytest.raises(TTSStreamError) as excinfo:
        await stream.wait()
    assert "super-secret-key" not in str(excinfo.value)


# == lifecycle ===============================================================


async def test_cancel_closes_connection(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    connection = StubConnection()
    _patch_connect(monkeypatch, connection, captured)
    provider = ElevenLabsTTSProvider(
        api_key="k", voice_id="v", base_url="wss://api.test"
    )
    stream = await provider.open_stream(on_audio=AudioCollector())

    await stream.cancel()

    assert connection.closed is True
    await stream.close()


async def test_close_is_repeatable(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    connection = StubConnection()
    _patch_connect(monkeypatch, connection, captured)
    provider = ElevenLabsTTSProvider(
        api_key="k", voice_id="v", base_url="wss://api.test"
    )
    stream = await provider.open_stream(on_audio=AudioCollector())

    await stream.close()
    await stream.close()

    assert connection.closed is True


async def test_empty_audio_chunks_are_not_forwarded(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    connection = StubConnection(
        [
            json.dumps({"audio": ""}),
            _audio_msg(b"\x07"),
            '{"isFinal": true}',
        ]
    )
    _patch_connect(monkeypatch, connection, captured)
    provider = ElevenLabsTTSProvider(
        api_key="k", voice_id="v", base_url="wss://api.test"
    )
    collector = AudioCollector()
    stream = await provider.open_stream(on_audio=collector)
    await _drain(stream)
    await stream.close()

    assert collector.chunks == [b"\x07"]
