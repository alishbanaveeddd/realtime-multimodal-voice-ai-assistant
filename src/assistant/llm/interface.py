"""LLM provider abstraction (architecture.md §6, `.clinerules` §2/§4).

The orchestration/transport layer depends only on these interfaces, never on a
concrete LLM vendor. An :class:`LLMProvider` opens an :class:`LLMStream` for a
completed utterance transcript; the stream pushes token deltas to a
caller-supplied async callback as generation progresses.

Tokens flow through the async ``on_token`` callback (matching the event-driven
nature of streaming generation). Provider/stream failures are signalled by
typed exceptions so the transport layer can translate them into the structured
``llm.error``/``request.error`` events defined by the architecture.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

#: Async callback invoked by a stream to deliver each generated token delta.
TokenCallback = Callable[[str], Awaitable[None]]


class LLMProviderError(RuntimeError):
    """Base class for all LLM provider failures."""


class LLMConfigurationError(LLMProviderError):
    """Raised when the provider is misconfigured (e.g. missing credentials)."""


class LLMStreamError(LLMProviderError):
    """Raised when a live LLM stream fails after being opened."""


class LLMProvider(ABC):
    """Factory for opening streaming LLM generation sessions."""

    @abstractmethod
    async def open_stream(
        self, text: str, *, on_token: TokenCallback
    ) -> LLMStream:
        """Start streaming generation for the final utterance ``text``.

        The returned stream pushes token deltas through ``on_token`` as they
        are generated. Raises :class:`LLMProviderError` subclasses on failure.
        """


class LLMStream(ABC):
    """A single streaming generation session.

    Lifecycle: tokens arrive via the ``on_token`` callback after
    :meth:`LLMProvider.open_stream`; :meth:`wait` blocks until generation has
    finished (raising :class:`LLMProviderError` on failure); :meth:`cancel`
    aborts generation (barge-in/disconnect); :meth:`close` always releases
    resources and is safe to call more than once.
    """

    @abstractmethod
    async def wait(self) -> None:
        """Block until generation finishes; raise on provider/stream failure."""

    @abstractmethod
    async def cancel(self) -> None:
        """Abort generation and release upstream work."""

    @abstractmethod
    async def close(self) -> None:
        """Guaranteed cleanup; safe to call more than once."""
