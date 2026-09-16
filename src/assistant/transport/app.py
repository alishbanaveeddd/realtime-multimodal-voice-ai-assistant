"""FastAPI application factory (architecture.md §6, §8).

Exposes a ``/health`` probe and a ``/ws`` WebSocket endpoint that drives a
:class:`~assistant.transport.gateway.SessionConnection` per connection. The app
factory accepts ``SessionManager``/``RequestManager`` and an
:class:`~assistant.asr.interface.ASRProvider` so tests can inject deterministic
state/fakes; otherwise fresh in-memory managers and an environment-selected ASR
provider are created (see :func:`assistant.asr.factory.build_asr_provider`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from assistant.asr.factory import build_asr_provider
from assistant.asr.interface import ASRProvider
from assistant.core.request import RequestManager
from assistant.core.session import SessionManager
from assistant.llm.factory import build_llm_provider
from assistant.llm.interface import LLMProvider
from assistant.transport.gateway import SessionConnection
from assistant.tts.factory import build_tts_provider
from assistant.tts.interface import TTSProvider


def create_app(
    sessions: SessionManager | None = None,
    requests: RequestManager | None = None,
    asr: ASRProvider | None = None,
    llm: LLMProvider | None = None,
    tts: TTSProvider | None = None,
    *,
    clock: Callable[[], float] | None = None,
) -> FastAPI:
    """Build the FastAPI application with the health and WebSocket endpoints."""
    if sessions is None:
        sessions = SessionManager(clock=clock) if clock else SessionManager()
    if requests is None:
        requests = RequestManager(clock=clock) if clock else RequestManager()
    if asr is None:
        asr = build_asr_provider()
    if llm is None:
        llm = build_llm_provider()
    if tts is None:
        tts = build_tts_provider()

    app = FastAPI(title="Voice Assistant", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()

        async def send_text(payload: str) -> None:
            await websocket.send_text(payload)

        async def send_binary(payload: bytes) -> None:
            await websocket.send_bytes(payload)

        connection = SessionConnection(
            sessions,
            requests,
            send_text,
            asr,
            llm,
            tts,
            send_binary=send_binary,
            clock=clock,
        )
        await connection.on_connect()
        try:
            while True:
                message: Any = await websocket.receive()
                kind = message.get("type")
                if kind == "websocket.disconnect":
                    break
                if kind == "websocket.receive":
                    text = message.get("text")
                    raw = message.get("bytes")
                    if text is not None:
                        await connection.on_message(text)
                    elif raw is not None:
                        await connection.on_message(bytes(raw))
        except WebSocketDisconnect:
            pass
        finally:
            await connection.on_close()
            try:
                await websocket.close()
            except RuntimeError:
                # The peer already disconnected; nothing left to close.
                pass

    return app
