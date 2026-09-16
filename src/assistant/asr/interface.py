"""ASR provider abstraction (architecture.md §6, `.clinerules` §2/§4).

The orchestration/transport layer depends only on these interfaces, never on a
concrete ASR vendor. A :class:`ASRProvider` opens :class:`ASRStream`
instances; each stream ingests audio frames and pushes structured transcription
results to a caller-supplied async callback.

Results flow through the async ``on_result`` callback (matching the
event-driven nature of streaming ASR). Provider/stream failures are signalled by
typed exceptions so the transport layer can translate them into the structured
``error``/``request.error`` events defined by the architecture.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum


class TranscriptKind(StrEnum):
    """Whether a transcription result is interim or final."""

    PARTIAL = "partial"
    FINAL = "final"


@dataclass(frozen=True, slots=True)
class ASRResult:
    """One streaming transcription result emitted by an ASR provider.

    ``sequence`` is a monotonically increasing per-stream counter used by the
    transport layer to guarantee ordered delivery of transcript events.
    """

    kind: TranscriptKind
    transcript: str
    sequence: int

    @property
    def is_final(self) -> bool:
        """Whether this result is a finalized transcript."""
        return self.kind is TranscriptKind.FINAL


#: Async callback invoked by a stream to deliver each transcription result.
ResultCallback = Callable[[ASRResult], Awaitable[None]]


class ASRProviderError(RuntimeError):
    """Base class for all ASR provider failures."""


class ASRConfigurationError(ASRProviderError):
    """Raised when the provider is misconfigured (e.g. missing credentials)."""


class ASRStreamError(ASRProviderError):
    """Raised when a live ASR stream fails after being opened."""


class ASRProvider(ABC):
    """Factory for opening ASR transcription streams."""

    @abstractmethod
    async def open_stream(self, *, on_result: ResultCallback) -> ASRStream:
        """Open a new streaming transcription session.

        The returned stream pushes results through ``on_result`` as audio is
        fed in. Raises :class:`ASRProviderError` subclasses on failure.
        """


class ASRStream(ABC):
    """A single streaming transcription session.

    Lifecycle: ingest ``audio.frame`` binary chunks via :meth:`send_audio`,
    signal utterance end with :meth:`finish`, interrupt with :meth:`cancel`,
    and always free resources with :meth:`close`. Calls are idempotent-safe at
    the transport layer (see the gateway).
    """

    @abstractmethod
    async def send_audio(self, data: bytes) -> None:
        """Feed one validated PCM16 audio frame to the stream."""

    @abstractmethod
    async def finish(self) -> None:
        """Gracefully complete transcription (audio.eos) and flush the final."""

    @abstractmethod
    async def cancel(self) -> None:
        """Abort streaming (disconnect/barge-in) and release upstream work."""

    @abstractmethod
    async def close(self) -> None:
        """Guaranteed cleanup; safe to call more than once."""
