"""Deterministic fake ASR provider for offline tests (architecture §15).

The fake requires no API key, network, or external calls. It is scripted with a
:class:`FakeASRScript` so tests can deterministically produce interim partials,
a final transcript, a provider error at stream open, and a mid-stream error at
``finish``. It also records audio frames, finish/cancel/close calls so tests can
assert on lifecycle and cleanup.
"""

from __future__ import annotations

from dataclasses import dataclass

from assistant.asr.interface import (
    ASRProvider,
    ASRResult,
    ASRStream,
    ASRStreamError,
    ResultCallback,
    TranscriptKind,
)


@dataclass(frozen=True, slots=True)
class FakeASRScript:
    """Scripted behavior for the fake ASR provider/stream."""

    #: Interim transcripts, all emitted on the first audio frame.
    partials: tuple[str, ...] = ()
    #: Final transcript, emitted on ``finish()``.
    final: str = ""
    #: If set, ``open_stream`` raises ``ASRStreamError`` (provider failure).
    error_at_open: str | None = None
    #: If set, ``finish()`` raises ``ASRStreamError`` (mid-stream failure).
    error_at_finish: str | None = None
    #: If set, ``send_audio`` raises ``ASRStreamError`` (mid-stream failure).
    error_at_send: str | None = None


class FakeASRStream(ASRStream):
    """Scripted ASR stream that collects introspection state for tests."""

    def __init__(
        self, on_result: ResultCallback, script: FakeASRScript
    ) -> None:
        self._on_result = on_result
        self._script = script
        self._partials_sent = False
        self._seq = 0
        self.frames: list[bytes] = []
        self.finished = False
        self.canceled = False
        self.closed = False

    async def _emit(self, kind: TranscriptKind, transcript: str) -> None:
        self._seq += 1
        await self._on_result(
            ASRResult(kind=kind, transcript=transcript, sequence=self._seq)
        )

    async def send_audio(self, data: bytes) -> None:
        if self._script.error_at_send is not None:
            raise ASRStreamError(self._script.error_at_send)
        self.frames.append(data)
        if not self._partials_sent:
            self._partials_sent = True
            for partial in self._script.partials:
                await self._emit(TranscriptKind.PARTIAL, partial)

    async def finish(self) -> None:
        if self._script.error_at_finish is not None:
            raise ASRStreamError(self._script.error_at_finish)
        if self._script.final:
            await self._emit(TranscriptKind.FINAL, self._script.final)
        self.finished = True

    async def cancel(self) -> None:
        self.canceled = True

    async def close(self) -> None:
        self.closed = True


class FakeASRProvider(ASRProvider):
    """Provider factory producing :class:`FakeASRStream` instances."""

    def __init__(self, script: FakeASRScript | None = None) -> None:
        self._script = script or FakeASRScript()
        self.streams: list[FakeASRStream] = []

    async def open_stream(
        self, *, on_result: ResultCallback
    ) -> FakeASRStream:
        if self._script.error_at_open is not None:
            raise ASRStreamError(self._script.error_at_open)
        stream = FakeASRStream(on_result, self._script)
        self.streams.append(stream)
        return stream
