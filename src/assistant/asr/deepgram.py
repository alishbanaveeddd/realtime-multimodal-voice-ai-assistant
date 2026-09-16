from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection, connect

from assistant.asr.interface import (
    ASRConfigurationError,
    ASRProvider,
    ASRProviderError,
    ASRResult,
    ASRStream,
    ASRStreamError,
    ResultCallback,
    TranscriptKind,
)

DEEPGRAM_BASE_URL = "wss://api.deepgram.com/v1/listen"

ENCODING = "linear16"
SAMPLE_RATE_HZ = 16_000
CHANNELS = 1

FINISH_TIMEOUT_SECONDS = 10.0


class DeepgramASRProvider(ASRProvider):
    """Deepgram streaming ASR provider."""

    def __init__(self, api_key: str) -> None:
        if not api_key.strip():
            raise ASRConfigurationError("DEEPGRAM_API_KEY is empty")

        self._api_key = api_key

    @property
    def api_key(self) -> str:
        """Read-only accessor for the configured credential (never logged)."""
        return self._api_key

    @classmethod
    def from_env(cls) -> DeepgramASRProvider:
        api_key = os.getenv("DEEPGRAM_API_KEY", "").strip()

        if not api_key:
            raise ASRConfigurationError(
                "DEEPGRAM_API_KEY is not configured"
            )

        return cls(api_key)

    async def open_stream(
        self,
        *,
        on_result: ResultCallback,
    ) -> ASRStream:
        params = {
            "encoding": ENCODING,
            "sample_rate": str(SAMPLE_RATE_HZ),
            "channels": str(CHANNELS),
            "interim_results": "true",
        }

        url = DEEPGRAM_BASE_URL + "?" + "&".join(
            f"{key}={value}" for key, value in params.items()
        )

        headers = {
            "Authorization": f"Token {self._api_key}",
        }

        try:
            connection = await connect(
                url,
                additional_headers=headers,
            )
        except Exception as exc:
            raise ASRStreamError(
                f"Failed to connect to Deepgram: {exc}"
            ) from exc

        return DeepgramASRStream(
            connection,
            on_result,
        )


class DeepgramASRStream(ASRStream):
    """One streaming Deepgram transcription session."""

    def __init__(
        self,
        connection: ClientConnection,
        on_result: ResultCallback,
    ) -> None:
        self._connection = connection
        self._on_result = on_result

        self._reader_task: asyncio.Task[None] | None = None
        self._sequence = 0

        self._error: ASRProviderError | None = None
        self._final_received = asyncio.Event()

        self._start_reader()

    def _start_reader(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(
                self._read_loop()
            )

    async def _read_loop(self) -> None:
        try:
            async for message in self._connection:
                await self._handle_message(message)

        except websockets.ConnectionClosed:
            # A normal connection close is not itself a transcript.
            # Do not mark _final_received here.
            return

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            self._error = ASRStreamError(
                f"Deepgram reader failed: {exc}"
            )

    async def _handle_message(self, message: str | bytes) -> None:
        if isinstance(message, bytes):
            try:
                message = message.decode("utf-8")
            except UnicodeDecodeError:
                return

        try:
            payload: dict[str, Any] = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            return

        message_type = payload.get("type")

        if message_type == "Error":
            description = (
                payload.get("description")
                or payload.get("message")
                or "Deepgram returned an error"
            )

            self._error = ASRStreamError(str(description))
            return

        if message_type != "Results":
            return

        channel = payload.get("channel")

        if not isinstance(channel, dict):
            return

        alternatives = channel.get("alternatives")

        if not isinstance(alternatives, list) or not alternatives:
            return

        first_alternative = alternatives[0]

        if not isinstance(first_alternative, dict):
            return

        transcript = first_alternative.get("transcript")

        if not isinstance(transcript, str):
            return

        transcript = transcript.strip()

        if not transcript:
            return

        # Deepgram sets ``is_final`` at the message level, but some message
        # shapes only carry it on the channel. Treat an explicit ``true`` at
        # either level as final; an interim (false/absent) at the top level
        # must not mask a channel-level final.
        top_level_final = payload.get("is_final")
        channel_level_final = channel.get("is_final")

        is_final = top_level_final is True or channel_level_final is True

        self._sequence += 1

        result = ASRResult(
            kind=(
                TranscriptKind.FINAL
                if is_final
                else TranscriptKind.PARTIAL
            ),
            transcript=transcript,
            sequence=self._sequence,
        )

        await self._on_result(result)

        if is_final:
            self._final_received.set()

    def _check_error(self) -> None:
        if self._error is not None:
            error = self._error
            self._error = None
            raise error

    async def send_audio(self, data: bytes) -> None:
        self._check_error()

        if not data:
            return

        try:
            await self._connection.send(data)
        except Exception as exc:
            raise ASRStreamError(
                f"Failed to send audio to Deepgram: {exc}"
            ) from exc

        self._check_error()

    async def finish(self) -> None:
        self._check_error()

        payload = json.dumps({"type": "CloseStream"})

        try:
            await self._connection.send(payload)
        except Exception as exc:
            raise ASRStreamError(
                f"Failed to send CloseStream to Deepgram: {exc}"
            ) from exc

        try:
            await asyncio.wait_for(
                self._final_received.wait(),
                timeout=FINISH_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            self._check_error()

            raise ASRStreamError(
                "Deepgram did not return a final transcript "
                "before finish timeout"
            ) from exc

        self._check_error()

    async def cancel(self) -> None:
        if self._connection.state is not websockets.State.CLOSED:
            try:
                await self._connection.close()
            except Exception:
                pass

        if self._reader_task is not None:
            if not self._reader_task.done():
                self._reader_task.cancel()

            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

    async def close(self) -> None:
        if self._reader_task is not None:
            if not self._reader_task.done():
                self._reader_task.cancel()

            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

            self._reader_task = None

        if self._connection.state is not websockets.State.CLOSED:
            try:
                await self._connection.close()
            except Exception:
                pass
