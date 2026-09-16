"""Shared fixtures for the test suite."""

from __future__ import annotations

import pytest

from assistant.asr.fake import FakeASRProvider, FakeASRScript
from assistant.core.request import RequestManager
from assistant.core.session import SessionManager
from assistant.llm.fake import FakeLLMProvider
from assistant.tts.fake import FakeTTSProvider, FakeTTSScript


@pytest.fixture
def sessions() -> SessionManager:
    """A fresh in-memory session manager with a deterministic clock."""
    return SessionManager(clock=lambda: 1000.0)


@pytest.fixture
def requests_mgr() -> RequestManager:
    """A fresh in-memory request manager with a deterministic clock."""
    return RequestManager(clock=lambda: 1000.0)


@pytest.fixture
def asr() -> FakeASRProvider:
    """A deterministic fake ASR provider for offline tests."""
    return FakeASRProvider(FakeASRScript())


@pytest.fixture
def llm() -> FakeLLMProvider:
    """A deterministic fake LLM provider for offline tests (no tokens)."""
    return FakeLLMProvider()


@pytest.fixture
def tts() -> FakeTTSProvider:
    """A deterministic fake TTS provider (one audio chunk per segment)."""
    return FakeTTSProvider(FakeTTSScript(chunks=(b"\x00\x01" * 160,)))
