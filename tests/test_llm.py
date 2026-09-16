"""Tests for the LLM provider abstraction and the deterministic fake (M3)."""

from __future__ import annotations

import pytest

from assistant.llm.fake import FakeLLMProvider, FakeLLMScript
from assistant.llm.interface import LLMStreamError


class Collector:
    """Async token callback collecting tokens in order."""

    def __init__(self) -> None:
        self.tokens: list[str] = []

    async def __call__(self, token: str) -> None:
        self.tokens.append(token)


async def test_fake_emits_scripted_tokens_in_order() -> None:
    collector = Collector()
    provider = FakeLLMProvider(FakeLLMScript(tokens=("Hello", " there")))
    stream = await provider.open_stream("hi", on_token=collector)

    assert stream.text == "hi"
    await stream.wait()

    assert collector.tokens == ["Hello", " there"]
    assert stream.tokens_sent == ["Hello", " there"]
    assert stream.waited is True


async def test_fake_with_no_tokens_completes_without_calls() -> None:
    collector = Collector()
    provider = FakeLLMProvider()
    stream = await provider.open_stream("hi", on_token=collector)

    await stream.wait()

    assert collector.tokens == []
    assert stream.tokens_sent == []


async def test_fake_error_at_open_raises_llm_stream_error() -> None:
    provider = FakeLLMProvider(FakeLLMScript(error_at_open="llm down"))
    with pytest.raises(LLMStreamError, match="llm down"):
        await provider.open_stream("hi", on_token=Collector())


async def test_fake_mid_stream_error_raises_on_wait() -> None:
    collector = Collector()
    provider = FakeLLMProvider(
        FakeLLMScript(tokens=("a", "b"), error_mid_stream="generation broke")
    )
    stream = await provider.open_stream("hi", on_token=collector)

    with pytest.raises(LLMStreamError, match="generation broke"):
        await stream.wait()
    # The first token was delivered before the failure.
    assert collector.tokens == ["a"]


async def test_fake_mid_stream_error_without_tokens_raises_immediately() -> None:
    collector = Collector()
    provider = FakeLLMProvider(FakeLLMScript(error_mid_stream="boom"))
    stream = await provider.open_stream("hi", on_token=collector)

    with pytest.raises(LLMStreamError, match="boom"):
        await stream.wait()
    assert collector.tokens == []


async def test_fake_cancel_stops_token_emission() -> None:
    collector = Collector()
    provider = FakeLLMProvider(FakeLLMScript(tokens=("a", "b", "c")))
    stream = await provider.open_stream("hi", on_token=collector)
    await stream.cancel()
    await stream.wait()

    assert collector.tokens == []
    assert stream.canceled is True


async def test_fake_close_is_repeatable_and_records_state() -> None:
    provider = FakeLLMProvider()
    stream = await provider.open_stream("hi", on_token=Collector())

    await stream.close()
    await stream.close()

    assert stream.closed is True
    assert provider.streams == [stream]
