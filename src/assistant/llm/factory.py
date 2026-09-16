"""Construction and selection of the active LLM provider.

Selection is environment-driven (`.clinerules` §4): if a Groq API key is
present, use the OpenAI streaming adapter; otherwise fall back to the
deterministic fake so the application remains runnable and testable offline
(architecture §3 / §15 — tests must never depend on a real provider or
credentials).
"""

from __future__ import annotations

import os

from assistant.llm.fake import FakeLLMProvider
from assistant.llm.interface import LLMProvider
from assistant.llm.openai_adapter import GROQ_API_KEY_ENV, OpenAILLMProvider


def build_llm_provider(api_key: str | None = None) -> LLMProvider:
    """Return the LLM provider selected by configuration.

    When ``api_key`` is omitted it is read from ``GROQ_API_KEY``. A Groq key
    selects the OpenAI-compatible streaming adapter; otherwise the deterministic fake
    is used.
    """
    key = api_key if api_key is not None else os.environ.get(GROQ_API_KEY_ENV)
    key = (key or "").strip()
    if key:
        return OpenAILLMProvider(api_key=key)
    return FakeLLMProvider()
