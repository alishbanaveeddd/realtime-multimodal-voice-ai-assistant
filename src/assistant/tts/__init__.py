"""TTS stage package (Milestone M4): provider abstraction, fake, ElevenLabs."""

from assistant.tts.factory import build_tts_provider
from assistant.tts.fake import FakeTTSProvider, FakeTTSScript
from assistant.tts.interface import (
    AudioChunkCallback,
    TTSConfigurationError,
    TTSProvider,
    TTSProviderError,
    TTSStream,
    TTSStreamError,
)

__all__ = [
    "AudioChunkCallback",
    "FakeTTSProvider",
    "FakeTTSScript",
    "TTSConfigurationError",
    "TTSProvider",
    "TTSProviderError",
    "TTSStream",
    "TTSStreamError",
    "build_tts_provider",
]
