"""Deterministic fake TTS provider for offline tests (architecture §15).

The fake requires no API key, network, or external calls. It is scripted with
a :class:`FakeTTSScript` so tests can deterministically produce streamed audio
chunks per segment, a provider error at stream open, a mid-stream error at
``send_text``, and an error at ``wait``. It records segments and lifecycle
calls so tests can assert on streaming and cleanup.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from assistant.tts.interface import (
    AudioChunkCallback,
    TTSProvider,
    TTSStream,
    TTSStreamError,
)


@dataclass(frozen=True, slots=True)
class FakeTTSScript:
    """Scripted behavior for the fake TTS provider/stream."""

    #: Audio chunks emitted per fed segment (all of them, in order).
    chunks: tuple[bytes, ...] = ()
    #: If set, ``open_stream`` raises ``TTSStreamError`` (provider failure).
    error_at_open: str | None = None
    #: If set, ``send_text`` raises ``TTSStreamError`` (mid-stream failure).
    error_at_send: str | None = None
    #: Number of successful ``send_text`` calls before ``error_at_send``
    #: fires (0 = fail on the first call). Lets tests stream audio first.
    error_at_send_after: int = 0
    #: If set, ``wait`` raises ``TTSStreamError`` (mid-stream failure).
    error_at_wait: str | None = None


@dataclass(slots=True)
class _State:
    segments: list[str] = field(default_factory=list)
    chunks_sent: list[bytes] = field(default_factory=list)


class FakeTTSStream(TTSStream):
    """Scripted TTS stream that collects introspection state for tests."""

    def __init__(
        self, on_audio: AudioChunkCallback, script: FakeTTSScript
    ) -> None:
        self._on_audio = on_audio
        self._script = script
        self.state = _State()
        self.finished = False
        self.waited = False
        self.canceled = False
        self.closed = False

    async def send_text(self, text: str) -> None:
        if (
            self._script.error_at_send is not None
            and len(self.state.segments) >= self._script.error_at_send_after
        ):
            raise TTSStreamError(self._script.error_at_send)
        self.state.segments.append(text)
        for chunk in self._script.chunks:
            self.state.chunks_sent.append(chunk)
            await self._on_audio(chunk)

    async def finish(self) -> None:
        self.finished = True

    async def wait(self) -> None:
        self.waited = True
        if self._script.error_at_wait is not None:
            raise TTSStreamError(self._script.error_at_wait)

    async def cancel(self) -> None:
        self.canceled = True

    async def close(self) -> None:
        self.closed = True


class FakeTTSProvider(TTSProvider):
    """Provider factory producing :class:`FakeTTSStream` instances."""

    def __init__(self, script: FakeTTSScript | None = None) -> None:
        self._script = script or FakeTTSScript()
        self.streams: list[FakeTTSStream] = []

    async def open_stream(
        self, *, on_audio: AudioChunkCallback
    ) -> FakeTTSStream:
        if self._script.error_at_open is not None:
            raise TTSStreamError(self._script.error_at_open)
        stream = FakeTTSStream(on_audio, self._script)
        self.streams.append(stream)
        return stream
