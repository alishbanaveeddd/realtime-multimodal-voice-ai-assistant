"""TTS provider abstraction (architecture.md §6, `.clinerules` §2/§4).

The orchestration/transport layer depends only on these interfaces, never on a
concrete TTS vendor. A :class:`TTSProvider` opens a :class:`TTSStream`; text
is fed progressively via :meth:`TTSStream.send_text` and synthesized audio
chunks (PCM16/16 kHz/mono per the approved contract) are pushed to a
caller-supplied async callback as they become available.

Audio flows through the async ``on_audio`` callback (matching the streaming
nature of TTS). Provider/stream failures are signalled by typed exceptions so
the transport layer can translate them into the structured ``request.error``
events defined by the architecture.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

#: Async callback invoked by a stream to deliver each synthesized audio chunk.
AudioChunkCallback = Callable[[bytes], Awaitable[None]]


class TTSProviderError(RuntimeError):
    """Base class for all TTS provider failures."""


class TTSConfigurationError(TTSProviderError):
    """Raised when the provider is misconfigured (e.g. missing credentials)."""


class TTSStreamError(TTSProviderError):
    """Raised when a live TTS stream fails after being opened."""


class TTSProvider(ABC):
    """Factory for opening streaming synthesis sessions."""

    @abstractmethod
    async def open_stream(
        self, *, on_audio: AudioChunkCallback
    ) -> TTSStream:
        """Open a new streaming synthesis session.

        The returned stream pushes PCM16/16 kHz/mono audio chunks through
        ``on_audio`` as text is fed in. Raises :class:`TTSProviderError`
        subclasses on failure.
        """


class TTSStream(ABC):
    """A single streaming synthesis session.

    Lifecycle: feed text with :meth:`send_text` (audio chunks arrive via the
    ``on_audio`` callback), signal end-of-input with :meth:`finish`, await
    completion with :meth:`wait` (raising on failure), abort with
    :meth:`cancel`, and always release resources with :meth:`close` (safe to
    call more than once).
    """

    @abstractmethod
    async def send_text(self, text: str) -> None:
        """Feed one segment of text to the synthesizer."""

    @abstractmethod
    async def finish(self) -> None:
        """Signal end-of-input so the provider can flush remaining audio."""

    @abstractmethod
    async def wait(self) -> None:
        """Block until synthesis finishes; raise on provider/stream failure."""

    @abstractmethod
    async def cancel(self) -> None:
        """Abort synthesis and release upstream work."""

    @abstractmethod
    async def close(self) -> None:
        """Guaranteed cleanup; safe to call more than once."""
