"""Deterministic tests for RequestLatency and the gateway latency metrics (M5).

All timings come from an injectable fake clock, so tests assert on elapsed
durations without any real timer. We assert *presence*, arithmetic and
ordering — never exact wall values.
"""

from __future__ import annotations

from assistant.latency import RequestLatency


class Clock:
    """A stepping monotonic clock; advance with ``.step(dt)``."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def step(self, dt: float) -> None:
        self.now += dt


def _request(clock: Clock) -> RequestLatency:
    r = RequestLatency()
    r.reset(clock=clock(), request_id="req_1")
    return r


# == RequestLatency unit tests ===============================================


def test_empty_payload_omits_all() -> None:
    r = RequestLatency()
    assert r.payload() == {}


def test_partial_payload_only_has_recorded_metrics() -> None:
    clock = Clock()
    r = _request(clock)
    clock.step(1.0)
    r.asr_first_ts = clock()

    payload = r.payload()
    assert payload == {"asr_first_transcript_ms": 1000.0}
    assert "asr_final_transcript_ms" not in payload
    assert "llm_ttft_ms" not in payload
    assert "tts_ttfb_ms" not in payload
    assert "request_total_ms" not in payload


def test_full_payload_calculates_all_metrics() -> None:
    clock = Clock()
    r = _request(clock)
    clock.step(10.0)
    r.asr_first_ts = clock()
    clock.step(20.0)
    r.asr_final_ts = clock()
    clock.step(5.0)
    r.llm_start_ts = clock()
    clock.step(30.0)
    r.llm_first_token_ts = clock()
    clock.step(11.0)
    r.tts_start_ts = clock()
    clock.step(9.0)
    r.tts_first_audio_ts = clock()
    clock.step(15.0)
    r.completed_ts = clock()

    payload = r.payload()
    assert payload == {
        "asr_first_transcript_ms": 10_000.0,
        "asr_final_transcript_ms": 30_000.0,
        "llm_ttft_ms": 30_000.0,
        "tts_ttfb_ms": 9_000.0,
        "request_total_ms": 100_000.0,
    }


def test_missing_stage_is_never_fabricated() -> None:
    # No-stage / no-LLM / no-TTS request: only ASR + total present.
    clock = Clock()
    r = _request(clock)
    clock.step(2.0)
    r.asr_first_ts = clock()
    clock.step(3.0)
    r.asr_final_ts = clock()
    clock.step(5.0)
    r.completed_ts = clock()

    payload = r.payload()
    assert payload == {
        "asr_first_transcript_ms": 2000.0,
        "asr_final_transcript_ms": 5000.0,
        "request_total_ms": 10_000.0,
    }
    assert "llm_ttft_ms" not in payload
    assert "tts_ttfb_ms" not in payload


def test_reset_clears_previous_measurements() -> None:
    clock = Clock()
    r = _request(clock)
    clock.step(7.0)
    r.asr_first_ts = clock()
    r.reset(clock=clock(), request_id="req_2")

    assert r.payload() == {}
    assert r.request_id == "req_2"
    assert r.started_ts == clock()
