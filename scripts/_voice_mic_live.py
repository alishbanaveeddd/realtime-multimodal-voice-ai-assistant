r"""M9 live microphone voice assistant (NOT part of pytest).

Client-side microphone mode for the existing real pipeline:

    microphone -> PCM16/16 kHz/mono binary frames -> WebSocket
    -> real Deepgram ASR -> real Groq LLM -> real ElevenLabs TTS
    -> binary audio frames -> speaker playback

It only runs when ALL of these are set in the environment:

    DEEPGRAM_API_KEY
    GROQ_API_KEY
    ELEVENLABS_API_KEY
    ELEVENLABS_VOICE_ID

If any is missing it prints "LIVE SMOKE: BLOCKED - missing credentials" and
exits without touching the network. Requires the ``sounddevice`` package
(PortAudio; installed with the project dependencies). No raw audio bytes are
ever printed. Run (PowerShell)::

    .venv\Scripts\python scripts\_voice_mic_live.py

Speak your question, then press Enter to stop recording and run the pipeline.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
from pathlib import Path
from typing import Any

from assistant.transport.app import create_app

#: Audio wire contract (must match the gateway/ASR contract exactly).
MIC_SAMPLE_RATE_HZ: int = 16000
MIC_CHANNELS: int = 1
MIC_DTYPE: str = "int16"  # signed 16-bit little-endian
FRAME_SIZE_BYTES: int = 320  # 320 bytes = 160 samples = 10 ms


def _load_smoke_module() -> Any:
    """Import the PCM smoke script to reuse playback helpers."""
    spec = importlib.util.spec_from_file_location(
        "_smoke_e2e_live", Path(__file__).resolve().parent / "_smoke_e2e_live.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_smoke = _load_smoke_module()


class MicPcmFramer:
    """Hardware-independent PCM framer for the microphone stream.

    Accumulates raw PCM16/16 kHz/mono chunks and emits fixed 320-byte wire
    frames (10 ms each). The final partial frame is zero-padded to a whole
    PCM16 sample boundary so every frame satisfies the audio contract.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.frames: list[bytes] = []

    def add_chunk(self, data: bytes) -> None:
        """Buffer one raw capture chunk and emit complete 320-byte frames."""
        self._buf.extend(data)
        while len(self._buf) >= FRAME_SIZE_BYTES:
            self.frames.append(bytes(self._buf[:FRAME_SIZE_BYTES]))
            del self._buf[:FRAME_SIZE_BYTES]

    def finish(self) -> None:
        """Flush the remainder as one final (sample-aligned) frame."""
        if not self._buf:
            return
        if len(self._buf) % 2:
            self._buf.append(0)  # keep whole 16-bit samples
        self.frames.append(bytes(self._buf))
        self._buf.clear()


def _audio_callback(framer: MicPcmFramer):  # noqa: ANN201 - sd callback
    """Return a sounddevice callback that feeds captured bytes to ``framer``."""

    def callback(
        indata, _frames, _time, status  # type: ignore[no-untyped-def]
    ) -> None:
        if status:
            pass  # occasional overflow is non-fatal; keep the stream running
        framer.add_chunk(bytes(indata))

    return callback


async def _send_audio_and_eos(
    ws: Any, frames: list[bytes], session_id: str, seq: int = 1
) -> None:
    """Send capture frames as real-time-paced binary messages, then eos.

    ``seq`` must increase monotonically across audio.eos envelopes on the
    same connection (the gateway rejects non-monotonic sequences).
    """
    import time as _time

    sent = 0
    pace = _time.monotonic()
    while sent < len(frames):
        await ws.send(frames[sent])
        sent += 1
        pace += 0.01  # one 10 ms frame per 10 ms of capture
        delay = pace - _time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
    await ws.send(json.dumps({
        "type": "audio.eos", "version": "1.0", "session_id": session_id,
        "request_id": None, "seq": seq, "timestamp": 0.0, "payload": {},
    }))


async def _ensure_session_id(ws: Any, session_id: str | None) -> str:
    """Return the session id, reading ``session.started`` only on turn one.

    The gateway sends ``session.started`` exactly once per connection, so a
    follow-up turn must reuse the id captured on the first turn instead of
    waiting for another ``session.started``.
    """
    if session_id is not None:
        return session_id
    started = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
    assert started["type"] == "session.started"
    return str(started["session_id"])


async def _consume_pipeline(ws: Any) -> None:
    """Consume pipeline events; print stages; play the response audio."""
    audio_chunks: list[bytes] = []
    saw_done = False
    printed_asr_llm = False
    while True:
        msg = await asyncio.wait_for(ws.recv(), timeout=90.0)
        if isinstance(msg, bytes):
            audio_chunks.append(msg)
            continue
        event = json.loads(msg)
        etype = event["type"]
        if etype == "transcript.final" and not printed_asr_llm:
            printed_asr_llm = True
            print("ASR: receiving...")
            print("LLM: receiving...")
        elif etype == "tts.done":
            saw_done = True
        elif etype == "request.completed":
            break
        elif etype == "request.error":
            raise AssertionError(f"request.error: {event}")
        elif etype == "llm.error":
            raise AssertionError(f"llm.error: {event}")
    if not audio_chunks:
        raise AssertionError("no TTS audio received")
    if not saw_done:
        raise AssertionError("no tts.done")
    if audio_chunks:
        print("TTS: receiving...")
    print("Playing assistant response...")
    _smoke.play_response(b"".join(audio_chunks))
    print("Playback complete.")


async def _record_and_respond(
    ws: Any, session_id: str | None = None, seq: int = 1
) -> str:
    """One microphone turn: record, stream, eos, then consume the pipeline."""
    import sounddevice as sd

    framer = MicPcmFramer()
    stop = threading.Event()
    stream = sd.InputStream(
        samplerate=MIC_SAMPLE_RATE_HZ,
        channels=MIC_CHANNELS,
        dtype=MIC_DTYPE,
        blocksize=160,  # 10 ms blocks
        callback=_audio_callback(framer),
    )
    with stream:
        print("Listening... speak now.")
        print("[recording]  (press Enter to stop)")
        waiter = threading.Thread(
            target=lambda: (input(), stop.set()), daemon=True
        )
        waiter.start()
        await asyncio.to_thread(stop.wait)
    print("Stopped listening.")
    framer.finish()
    session_id = await _ensure_session_id(ws, session_id)
    await _send_audio_and_eos(ws, framer.frames, session_id, seq=seq)
    await _consume_pipeline(ws)
    return session_id


def _wait_healthy(health_url: str, timeout: float = 15.0) -> None:
    import time as _time
    import urllib.request as _ur

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        try:
            with _ur.urlopen(health_url, timeout=1) as resp:
                if resp.status == 200:
                    return
        except OSError:
            _time.sleep(0.2)
    raise RuntimeError("server did not become healthy")


def main() -> None:
    missing = _smoke._credentials_ready()
    if missing is not None:
        print(f"LIVE SMOKE: BLOCKED - missing credentials: {missing}")
        return
    try:
        import sounddevice  # noqa: F401
    except ImportError:
        print("LIVE SMOKE: BLOCKED - sounddevice is not installed")
        return

    import uvicorn
    import websockets

    print("Starting voice assistant...")
    host, port = "127.0.0.1", 8772
    url = f"ws://{host}:{port}/ws"
    app = create_app()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_healthy(f"http://{host}:{port}/health")

    async def session() -> None:
        # One connection for the whole conversation: the gateway keeps the
        # minimal follow-up state per connection.
        async with websockets.connect(url) as ws:
            session_id: str | None = None
            turn = 0
            while True:
                session_id = await _record_and_respond(ws, session_id, turn + 1)
                turn += 1
                again = input("Ask another question? [y/N]: ").strip().lower()
                if again not in ("y", "yes"):
                    break

    try:
        asyncio.run(session())
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


if __name__ == "__main__":
    sys.exit(main())

