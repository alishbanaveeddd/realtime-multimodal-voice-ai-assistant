r"""M2 smoke test: live WebSocket audio -> streaming transcripts (offline).

Starts a real uvicorn server on 127.0.0.1 with the deterministic fake ASR
provider (scripted: two partials, one final), then connects a real WebSocket
client and streams PCM16 audio frames, asserting the exact event sequence:

    session.started
    request.started
    transcript.partial x2   (on first audio frame)
    transcript.final        (on audio.eos)
    llm.token               (M3: LLM stage on the final transcript)
    tts.audio (binary)      (M4: streamed synthesized audio)
    tts.done
    metrics (all latency fields)  (M5: asr/llm/tts/total latency)
    request.completed

No API key and no network beyond localhost. Run::

    $env:PYTHONPATH='src'
    .venv\Scripts\python scripts\_smoke_asr.py

Expected: prints "SMOKE PASS".
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.request
from typing import Any

import uvicorn
import websockets  # type: ignore[import-untyped]

from assistant.asr.fake import FakeASRProvider, FakeASRScript
from assistant.llm.fake import FakeLLMProvider, FakeLLMScript
from assistant.transport.app import create_app
from assistant.tts.fake import FakeTTSProvider, FakeTTSScript

HOST = "127.0.0.1"
PORT = 8767
URL = f"ws://{HOST}:{PORT}/ws"
HEALTH = f"http://{HOST}:{PORT}/health"

# 320 bytes = 160 PCM16 samples = 10 ms of 16 kHz mono audio.
FRAME: bytes = b"\x00\x01" * 160


def _start_server() -> tuple[uvicorn.Server, threading.Thread]:
    """Boot a real uvicorn server with a scripted fake ASR provider."""
    app = create_app(
        asr=FakeASRProvider(
            FakeASRScript(partials=("hel", "hello"), final="hello world")
        ),
        llm=FakeLLMProvider(FakeLLMScript(tokens=("Hello",))),
        tts=FakeTTSProvider(FakeTTSScript(chunks=(b"\x00\x01" * 160,))),
    )
    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, thread


def _wait_healthy(timeout: float = 10.0) -> None:
    """Poll /health until the server answers or the timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(HEALTH, timeout=1) as resp:
                if resp.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"server did not become healthy at {HEALTH}")


async def _recv(ws: Any) -> dict[str, Any]:
    """Receive one JSON event with a hard timeout."""
    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
    event: dict[str, Any] = json.loads(raw)
    return event


async def run() -> None:
    async with websockets.connect(URL) as ws:
        started = await _recv(ws)
        assert started["type"] == "session.started", started
        print("OK session.started:", started["session_id"])

        # One validated PCM16 frame opens the request and triggers partials.
        await ws.send(FRAME)
        req_started = await _recv(ws)
        assert req_started["type"] == "request.started", req_started
        request_id = req_started["request_id"]
        print("OK request.started:", request_id)

        partial_a = await _recv(ws)
        partial_b = await _recv(ws)
        assert partial_a["type"] == "transcript.partial", partial_a
        assert partial_a["payload"]["text"] == "hel", partial_a
        assert partial_a["request_id"] == request_id, partial_a
        assert partial_b["type"] == "transcript.partial", partial_b
        assert partial_b["payload"]["text"] == "hello", partial_b
        print("OK transcript.partial x2:", partial_a["payload"]["text"],
              partial_b["payload"]["text"])

        # audio.eos finalizes the utterance: final transcript + completion.
        await ws.send(
            json.dumps(
                {
                    "type": "audio.eos",
                    "version": "1.0",
                    "session_id": started["session_id"],
                    "request_id": None,
                    "seq": 1,
                    "timestamp": 1.0,
                    "payload": {},
                }
            )
        )
        final = await _recv(ws)
        assert final["type"] == "transcript.final", final
        assert final["payload"]["text"] == "hello world", final
        assert final["request_id"] == request_id, final
        token = await _recv(ws)
        assert token["type"] == "llm.token", token
        assert token["payload"]["text"] == "Hello", token
        assert token["request_id"] == request_id, token
        # M4: streamed audio arrives as binary frame(s) before tts.done.
        audio = await asyncio.wait_for(ws.recv(), timeout=5.0)
        assert isinstance(audio, bytes), audio
        assert len(audio) == 320, audio
        tts_done = await _recv(ws)
        assert tts_done["type"] == "tts.done", tts_done
        metrics = await _recv(ws)
        assert metrics["type"] == "metrics", metrics
        for field in (
            "asr_first_transcript_ms",
            "asr_final_transcript_ms",
            "llm_ttft_ms",
            "tts_ttfb_ms",
            "request_total_ms",
        ):
            assert field in metrics["payload"], metrics
        assert metrics["request_id"] == request_id, metrics
        completed = await _recv(ws)
        assert completed["type"] == "request.completed", completed
        assert completed["request_id"] == request_id, completed
        print("OK transcript.final:", final["payload"]["text"])
        print("OK llm.token:", token["payload"]["text"])
        print("OK tts.audio binary frame:", len(audio), "bytes")
        print("OK tts.done")
        print("OK metrics:", metrics["payload"])
        print("OK request.completed:", completed["request_id"])


def main() -> None:
    server, thread = _start_server()
    try:
        _wait_healthy()
        print("OK /health")
        asyncio.run(run())
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
    print("SMOKE PASS")


if __name__ == "__main__":
    main()
