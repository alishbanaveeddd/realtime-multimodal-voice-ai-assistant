"""LLM stage package (Milestone M3): provider abstraction, fake, OpenAI."""

from assistant.llm.factory import build_llm_provider
from assistant.llm.fake import FakeLLMProvider, FakeLLMScript
from assistant.llm.interface import (
    LLMConfigurationError,
    LLMProvider,
    LLMProviderError,
    LLMStream,
    LLMStreamError,
)

__all__ = [
    "FakeLLMProvider",
    "FakeLLMScript",
    "LLMConfigurationError",
    "LLMProvider",
    "LLMProviderError",
    "LLMStream",
    "LLMStreamError",
    "build_llm_provider",
]
