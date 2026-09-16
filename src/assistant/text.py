"""Deterministic text segmentation for streaming TTS input (M4, Step 8).

Rule (documented, deliberately simple): a *segment* is the accumulated LLM
token text up to and including the first sentence-terminating character — one
of ``.``, ``!``, ``?`` or a newline. Any remainder left when generation
completes is flushed as a final segment. No sentence processing beyond this
single split point; empty segments are never produced.

This feeds TTS meaningful units instead of arbitrary token fragments while
keeping the pipeline streaming.
"""

from __future__ import annotations

_TERMINATORS: frozenset[str] = frozenset(".!?\n")


class TextSegmenter:
    """Accumulates streaming tokens and yields sentence-boundary segments."""

    def __init__(self) -> None:
        self._buffer: list[str] = []

    def feed(self, token: str) -> list[str]:
        """Consume one token; return any completed segments in order."""
        self._buffer.append(token)
        return self._drain()

    def flush(self) -> str:
        """Return and clear any trailing remainder (may be empty)."""
        remainder = "".join(self._buffer)
        self._buffer.clear()
        return remainder

    def _drain(self) -> list[str]:
        segments: list[str] = []
        start = 0
        buffer = "".join(self._buffer)
        for index, char in enumerate(buffer):
            if char in _TERMINATORS:
                segments.append(buffer[start : index + 1])
                start = index + 1
        self._buffer = [buffer[start:]] if start < len(buffer) else []
        return segments
