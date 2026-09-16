"""Streaming speech-to-text (ASR) stage.

M2 adds the first real-time ASR stage behind a provider-agnostic interface
(architecture.md §6). The transport/session layer depends only on the
interface in :mod:`assistant.asr.interface`; provider-specific logic lives in
the adapters (:mod:`assistant.asr.deepgram`) and deterministic test doubles in
:mod:`assistant.asr.fake`.
"""
