"""Tests for the TTS provider abstraction and the deterministic fake (M4)."""

from __future__ import annotations

import pytest

from assistant.tts.fake import FakeTTSProvider, FakeTTSScript
from assistant.tts.interface import TTSStreamError


class AudioCollector:
    """Async audio callback collecting chunks in order."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    async def __call__(self, chunk: bytes) -> None:
        self.chunks.append(chunk)


async def test_fake_streams_chunks_per_segment() -> None:
    collector = AudioCollector()
    provider = FakeTTSProvider(FakeTTSScript(chunks=(b"a", b"bb")))
    stream = await provider.open_stream(on_audio=collector)

    await stream.send_text("Hi.")
    await stream.send_text("Bye.")

    assert collector.chunks == [b"a", b"bb", b"a", b"bb"]
    assert stream.state.segments == ["Hi.", "Bye."]
    assert stream.state.chunks_sent == [b"a", b"bb", b"a", b"bb"]


async def test_fake_records_finish_wait_lifecycle() -> None:
    provider = FakeTTSProvider()
    stream = await provider.open_stream(on_audio=AudioCollector())

    await stream.send_text("x")
    await stream.finish()
    await stream.wait()

    assert stream.finished is True
    assert stream.waited is True
    assert provider.streams == [stream]


async def test_fake_error_at_open_raises_tts_stream_error() -> None:
    provider = FakeTTSProvider(FakeTTSScript(error_at_open="tts down"))
    with pytest.raises(TTSStreamError, match="tts down"):
        await provider.open_stream(on_audio=AudioCollector())


async def test_fake_error_at_send_raises_mid_stream() -> None:
    provider = FakeTTSProvider(FakeTTSScript(error_at_send="stream broke"))
    stream = await provider.open_stream(on_audio=AudioCollector())

    with pytest.raises(TTSStreamError, match="stream broke"):
        await stream.send_text("x")
    assert stream.state.segments == []


async def test_fake_error_at_wait_raises() -> None:
    provider = FakeTTSProvider(FakeTTSScript(error_at_wait="synthesis failed"))
    stream = await provider.open_stream(on_audio=AudioCollector())

    with pytest.raises(TTSStreamError, match="synthesis failed"):
        await stream.wait()


async def test_fake_cancel_records_flag_and_close_is_repeatable() -> None:
    collector = AudioCollector()
    provider = FakeTTSProvider(FakeTTSScript(chunks=(b"a",)))
    stream = await provider.open_stream(on_audio=collector)

    await stream.cancel()
    # The fake models cancel as a recorded flag; the gateway is responsible
    # for stopping calls after cancellation (asserted in the gateway tests).
    assert stream.canceled is True

    await stream.close()
    await stream.close()
    assert stream.closed is True
