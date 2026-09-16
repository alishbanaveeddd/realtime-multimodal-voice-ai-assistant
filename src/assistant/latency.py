"""Per-request latency instrumentation (architecture.md §13, Milestone M5).

A :class:`RequestLatency` instance records monotonic marks at the pipeline's
measurement points and turns the elapsed durations into the documented
``metrics`` event payload. Only *available* measurements appear in the
payload — a stage that never ran is omitted, never fabricated (§13).

Elapsed durations are computed with the caller-supplied monotonic clock; raw
timestamps are never exposed to clients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RequestLatency:
    """Monotonic marks for one request, keyed by its ``request_id``.

    All timestamps come from the caller's monotonic clock (seconds).
    ``payload()`` converts marks to the documented millisecond metrics and
    omits any measurement whose stage did not occur.
    """

    started_ts: float | None = None
    asr_first_ts: float | None = None
    asr_final_ts: float | None = None
    llm_start_ts: float | None = None
    llm_first_token_ts: float | None = None
    tts_start_ts: float | None = None
    tts_first_audio_ts: float | None = None
    completed_ts: float | None = None
    request_id: str | None = field(default=None)

    def elapsed_ms(self, start: float | None, end: float | None) -> float | None:
        """Elapsed milliseconds between two marks, or None if unavailable."""
        if start is None or end is None:
            return None
        return (end - start) * 1000.0

    def payload(self) -> dict[str, float]:
        """The documented metrics fields for this request.

        Only stages that actually produced a measurement are included; a
        missing stage is represented by *absence*, never a fabricated value.
        """
        values: dict[str, float | None] = {
            "asr_first_transcript_ms": self.elapsed_ms(
                self.started_ts, self.asr_first_ts
            ),
            "asr_final_transcript_ms": self.elapsed_ms(
                self.started_ts, self.asr_final_ts
            ),
            "llm_ttft_ms": self.elapsed_ms(
                self.llm_start_ts, self.llm_first_token_ts
            ),
            "tts_ttfb_ms": self.elapsed_ms(
                self.tts_start_ts, self.tts_first_audio_ts
            ),
            "request_total_ms": self.elapsed_ms(
                self.started_ts, self.completed_ts
            ),
        }
        return {k: v for k, v in values.items() if v is not None}

    def reset(self, *, clock: float, request_id: str | None) -> None:
        """Start a new measurement window for a fresh request."""
        self.started_ts = clock
        self.request_id = request_id
        self.asr_first_ts = None
        self.asr_final_ts = None
        self.llm_start_ts = None
        self.llm_first_token_ts = None
        self.tts_start_ts = None
        self.tts_first_audio_ts = None
        self.completed_ts = None

    def describe(self) -> dict[str, Any]:
        """Correlated payload for structured logging (ids + available ms)."""
        data: dict[str, Any] = {"request_id": self.request_id}
        data.update(self.payload())
        return data
