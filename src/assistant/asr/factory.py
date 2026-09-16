"""Construction and selection of the active ASR provider.

Selection is environment-driven (`.clinerules` §4): if a Deepgram API key is
present, use the Deepgram adapter; otherwise fall back to the deterministic
fake so the application remains runnable and testable offline (architecture
§3 / §15 — tests must never depend on a real provider or credentials).
"""

from __future__ import annotations

import os

from assistant.asr.deepgram import DeepgramASRProvider
from assistant.asr.fake import FakeASRProvider
from assistant.asr.interface import ASRProvider

#: Env var holding the Deepgram streaming ASR credential (name only, never value).
DEEPGRAM_API_KEY_ENV: str = "DEEPGRAM_API_KEY"


def build_asr_provider(api_key: str | None = None) -> ASRProvider:
    """Return the ASR provider selected by configuration.

    When ``api_key`` is omitted it is read from ``DEEPGRAM_API_KEY``. A Deepgram
    key selects the Deepgram adapter; otherwise the deterministic fake is used.
    """
    key = api_key if api_key is not None else os.environ.get(DEEPGRAM_API_KEY_ENV)
    key = (key or "").strip()
    if key:
        return DeepgramASRProvider(api_key=key)
    return FakeASRProvider()
