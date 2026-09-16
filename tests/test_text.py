"""Tests for the deterministic text segmenter (M4 Step 8)."""

from __future__ import annotations

from assistant.text import TextSegmenter


def test_no_terminator_means_no_segments() -> None:
    segmenter = TextSegmenter()
    assert segmenter.feed("Hello") == []
    assert segmenter.feed(" there") == []
    assert segmenter.flush() == "Hello there"


def test_terminator_flushes_segment() -> None:
    segmenter = TextSegmenter()
    assert segmenter.feed("Hi") == []
    assert segmenter.feed(".") == ["Hi."]
    assert segmenter.flush() == ""


def test_multiple_segments_per_token() -> None:
    segmenter = TextSegmenter()
    segments = segmenter.feed("One. Two!")
    # Leading whitespace is preserved (desirable for TTS prosody).
    assert segments == ["One.", " Two!"]
    assert segmenter.flush() == ""


def test_newline_terminates_segment() -> None:
    segmenter = TextSegmenter()
    assert segmenter.feed("line\n") == ["line\n"]


def test_flush_returns_remainder() -> None:
    segmenter = TextSegmenter()
    segmenter.feed("trailing text")
    assert segmenter.flush() == "trailing text"
    assert segmenter.flush() == ""  # cleared after flush


def test_segmenter_is_reusable_after_flush() -> None:
    segmenter = TextSegmenter()
    segmenter.feed("first. ")
    segmenter.flush()
    assert segmenter.feed("second.") == ["second."]
