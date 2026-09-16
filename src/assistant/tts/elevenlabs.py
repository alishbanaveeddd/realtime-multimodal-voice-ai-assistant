"""ElevenLabs streaming TTS adapter (approved stack decision).

This is the only module that knows about ElevenLabs' protocol. The transport /
session layer depends solely on :class:`assistant.tts.interface.TTSProvider`.

Protocol (documented streaming ``stream-input`` WebSocket API):

* ``wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input``
  with ``output_format=pcm_16000`` — which **is** the approved PCM16/16 kHz/
  mono contract, so no transcoding is required;
* an initial BOS message carries voice settings;
* each text segment is sent as ``{"text": ..., "try_trigger_generation": false}``;
* end-of-input is signalled with an empty-text EOS message;
* audio arrives as JSON messages with base64 PCM payloads (``{"audio": ...}``);
  ``{"isFinal": true}`` marks completion; ``{"detail": ...}`` signals errors.

Configuration comes from the environment (``ELEVENLABS_API_KEY`` and
``ELEVENLABS_VOICE_ID``); credentials are never hardcoded or logged. The
officially-installed :mod:`websockets` client is used directly — no
ElevenLabs SDK dependency is added.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection, connect

from assistant.tts.interface import (
    AudioChunkCallback,
    TTSConfigurationError,
    TTSProvider,
    TTSStream,
    TTSStreamError,
)

ELEVENLABS_BASE_URL: str = "wss://api.elevenlabs.io/v1/text-to-speech"
#: Env vars holding credentials/config (names only, never values).
ELEVENLABS_API_KEY_ENV: str = "ELEVENLABS_API_KEY"
ELEVENLABS_VOICE_ID_ENV: str = "ELEVENLABS_VOICE_ID"
#: Approved audio contract: PCM16, 16 kHz, mono.
OUTPUT_FORMAT: str = "pcm_16000"


class ElevenLabsTTSProvider(TTSProvider):
    """Provider opening ElevenLabs streaming synthesis sessions."""

    def __init__(
        self,
        *,
        api_key: str,
        voice_id: str,
        base_url: str = ELEVENLABS_BASE_URL,
    ) -> None:
        if not api_key or not api_key.strip():
            raise TTSConfigurationError(
                "ElevenLabs API key is missing or blank; set ELEVENLABS_API_KEY"
            )
        if not voice_id or not voice_id.strip():
            raise TTSConfigurationError(
                "ElevenLabs voice id is missing or blank; "
                "set ELEVENLABS_VOICE_ID"
            )
        self._api_key = api_key
        self._voice_id = voice_id
        self._base_url = base_url.rstrip("/")

    @classmethod
    def from_env(cls) -> ElevenLabsTTSProvider:
        """Build a provider from environment variables (raises if missing)."""
        return cls(
            api_key=os.environ.get(ELEVENLABS_API_KEY_ENV, ""),
            voice_id=os.environ.get(ELEVENLABS_VOICE_ID_ENV, ""),
        )

    async def open_stream(
        self, *, on_audio: AudioChunkCallback
    ) -> ElevenLabsTTSStream:
        url = (
            f"{self._base_url}/{self._voice_id}/stream-input"
            f"?output_format={OUTPUT_FORMAT}"
        )
        headers = {"xi-api-key": self._api_key}
        connection = await connect(url, additional_headers=headers)
        stream = ElevenLabsTTSStream(connection, on_audio)
        await stream._start()
        return stream

class ElevenLabsTTSStream(TTSStream):
    """Streaming synthesis session over one ElevenLabs stream-input socket."""

    def __init__(
        self, connection: ClientConnection, on_audio: AudioChunkCallback
    ) -> None:
        self._connection = connection
        self._on_audio = on_audio
        self._reader_task: asyncio.Task[None] | None = None
        self._error: TTSStreamError | None = None
        self._closed = False

    async def _start(self) -> None:
        """Send the BOS message and start the background audio reader."""
        bos = {
            "text": " ",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        try:
            await self._connection.send(json.dumps(bos))
        except websockets.ConnectionClosed as exc:
            raise TTSStreamError(f"ElevenLabs connection closed: {exc}") from exc
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for message in self._connection:
                if not isinstance(message, str):
                    continue
                try:
                    data: dict[str, Any] = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if "audio" in data:
                    try:
                        chunk = base64.b64decode(data["audio"])
                    except (binascii.Error, ValueError, TypeError):
                        continue
                    if chunk:
                        await self._on_audio(chunk)
                elif data.get("isFinal"):
                    return
                elif "detail" in data:
                    self._error = TTSStreamError(
                        str(data.get("detail", "ElevenLabs error"))
                    )
                    return
        except websockets.ConnectionClosed:
            pass

    def _check_error(self) -> None:
        if self._error is not None:
            raise self._error

    async def send_text(self, text: str) -> None:
        self._check_error()
        payload = json.dumps({"text": text, "try_trigger_generation": False})
        try:
            await self._connection.send(payload)
        except websockets.ConnectionClosed as exc:
            raise TTSStreamError(f"ElevenLabs connection closed: {exc}") from exc

    async def finish(self) -> None:
        self._check_error()
        try:
            await self._connection.send(json.dumps({"text": ""}))
        except websockets.ConnectionClosed as exc:
            raise TTSStreamError(f"ElevenLabs connection closed: {exc}") from exc

    async def wait(self) -> None:
        if self._reader_task is not None:
            await self._reader_task
        self._check_error()

    async def cancel(self) -> None:
        if not self._closed:
            await self._connection.close()

    async def close(self) -> None:
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
        if not self._closed:
            await self._connection.close()
            self._closed = True

