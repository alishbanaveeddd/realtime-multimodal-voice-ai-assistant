"""Tests for the audio-frame validator (architecture §7 audio contract).

The contract is PCM16 / 16 kHz / mono: each frame must be a non-empty whole
number of 16-bit (2-byte) samples.
"""

from __future__ import annotations

import pytest

from assistant.asr.audio import AudioFrameInvalidError, validate_audio_frame


def test_valid_frame_passes() -> None:
    """A frame with a whole number of 16-bit samples is valid."""
    validate_audio_frame(b"\x00\x01" * 80)  # 160 bytes = 80 samples


def test_empty_frame_is_rejected() -> None:
    """An empty frame is rejected (no samples to transcribe)."""
    with pytest.raises(AudioFrameInvalidError, match="empty"):
        validate_audio_frame(b"")


def test_odd_length_frame_is_rejected() -> None:
    """A frame with an odd byte length cannot be PCM16 (samples are 2 bytes)."""
    with pytest.raises(AudioFrameInvalidError, match="whole number"):
        validate_audio_frame(b"\x00\x01\x02")  # 3 bytes


def test_single_byte_frame_is_rejected() -> None:
    with pytest.raises(AudioFrameInvalidError):
        validate_audio_frame(b"\xff")


def test_error_message_mentions_pcm16() -> None:
    """The error message helps operators diagnose format mismatches."""
    with pytest.raises(AudioFrameInvalidError, match="PCM16"):
        validate_audio_frame(b"\x00\x01\x02")
