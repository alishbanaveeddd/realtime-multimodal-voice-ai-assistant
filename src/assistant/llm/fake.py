"""Deterministic fake LLM provider for offline tests (architecture §15).

The fake requires no API key, network, or external calls. It is scripted with a
:class:`FakeLLMScript` so tests can deterministically produce token streams,
a provider error at stream open, and a mid-stream error during ``wait``. It
also records the prompt text and cancel/close/wait calls for test assertions.
"""

from __future__ import annotations

from dataclasses import dataclass

from assistant.llm.interface import (
    LLMProvider,
    LLMStream,
    LLMStreamError,
    TokenCallback,
)


@dataclass(frozen=True, slots=True)
class FakeLLMScript:
    """Scripted behavior for the fake LLM provider/stream."""

    #: Token deltas emitted in order by ``wait()``.
    tokens: tuple[str, ...] = ()
    #: If set, ``open_stream`` raises ``LLMStreamError`` (provider failure).
    error_at_open: str | None = None
    #: If set, ``wait()`` raises ``LLMStreamError`` after the first token
    #: (or immediately when no tokens are scripted).
    error_mid_stream: str | None = None


class FakeLLMStream(LLMStream):
    """Scripted LLM stream that collects introspection state for tests."""

    def __init__(
        self, on_token: TokenCallback, script: FakeLLMScript, text: str
    ) -> None:
        self._on_token = on_token
        self._script = script
        self.text = text
        self.tokens_sent: list[str] = []
        self.waited = False
        self.canceled = False
        self.closed = False

    async def wait(self) -> None:
        self.waited = True
        for token in self._script.tokens:
            if self.canceled:
                return
            if self._script.error_mid_stream is not None and self.tokens_sent:
                raise LLMStreamError(self._script.error_mid_stream)
            self.tokens_sent.append(token)
            await self._on_token(token)
        if self._script.error_mid_stream is not None:
            # Loop completed without raising: either no tokens were scripted
            # (raise immediately) or a single token was emitted (raise after
            # the first token, per the script's documented semantics).
            raise LLMStreamError(self._script.error_mid_stream)

    async def cancel(self) -> None:
        self.canceled = True

    async def close(self) -> None:
        self.closed = True


class FakeLLMProvider(LLMProvider):
    """Provider factory producing :class:`FakeLLMStream` instances."""

    def __init__(self, script: FakeLLMScript | None = None) -> None:
        self._script = script or FakeLLMScript()
        self.streams: list[FakeLLMStream] = []

    async def open_stream(
        self, text: str, *, on_token: TokenCallback
    ) -> FakeLLMStream:
        if self._script.error_at_open is not None:
            raise LLMStreamError(self._script.error_at_open)
        stream = FakeLLMStream(on_token, self._script, text)
        self.streams.append(stream)
        return stream
