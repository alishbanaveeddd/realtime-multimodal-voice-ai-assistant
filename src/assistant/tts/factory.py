"""Construction and selection of the active TTS provider.

Selection is environment-driven (`.clinerules` §4): if ElevenLabs credentials
are present, use the ElevenLabs adapter; otherwise fall back to the
deterministic fake so the application remains runnable and testable offline
(architecture §3 / §15 — tests must never depend on a real provider or
credentials).
"""

from __future__ import annotations

import os

from assistant.tts.elevenlabs import (
    ELEVENLABS_API_KEY_ENV,
    ELEVENLABS_VOICE_ID_ENV,
    ElevenLabsTTSProvider,
)
from assistant.tts.fake import FakeTTSProvider
from assistant.tts.interface import TTSProvider


def build_tts_provider(
    api_key: str | None = None, voice_id: str | None = None
) -> TTSProvider:
    """Return the TTS provider selected by configuration.

    When ``api_key``/``voice_id`` are omitted they are read from
    ``ELEVENLABS_API_KEY``/``ELEVENLABS_VOICE_ID``. Both present selects the
    ElevenLabs adapter; otherwise the deterministic fake is used.
    """
    key = api_key if api_key is not None else os.environ.get(ELEVENLABS_API_KEY_ENV)
    vid = voice_id if voice_id is not None else os.environ.get(ELEVENLABS_VOICE_ID_ENV)
    if (key or "").strip() and (vid or "").strip():
        return ElevenLabsTTSProvider(api_key=key or "", voice_id=vid or "")
    return FakeTTSProvider()
