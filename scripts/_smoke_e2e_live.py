r"""M6 OPTIONAL real-provider end-to-end smoke test (NOT part of pytest).

Exercises the complete real pipeline against Deepgram + OpenAI + ElevenLabs:

    audio.frame(binary) -> ASR -> transcript.final -> LLM -> llm.token
        -> TTS -> binary audio -> tts.done -> metrics -> request.completed

It only runs when ALL of these are set in the environment:

    DEEPGRAM_API_KEY
    GROQ_API_KEY
    ELEVENLABS_API_KEY
    ELEVENLABS_VOICE_ID

If any is missing it prints:

    LIVE SMOKE: BLOCKED - missing credentials

and exits without touching the network. Real audio must be provided via the
WAV_FILE arg (PCM16 / 16 kHz / mono). Run (PowerShell)::

    $env:PYTHONPATH='src'
    .venv\Scripts\python scripts\_smoke_e2e_live.py path\to\clip.pcm [voice_id]

Expected on success: "LIVE SMOKE: PASS <request_id> <metrics>".
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path

import uvicorn
import websockets  # type: ignore[import-untyped]

from assistant.transport.app import create_app

HOST = "127.0.0.1"
PORT = 8771
URL = f"ws://{HOST}:{PORT}/ws"
HEALTH = f"http://{HOST}:{PORT}/health"

_REQUIRED = ("DEEPGRAM_API_KEY", "GROQ_API_KEY",
             "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")


def _credentials_ready() -> str | None:
    missing = [name for name in _REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        return ", ".join(missing)
    return None


def _start_server() -> tuple[uvicorn.Server, threading.Thread]:
    """Boot the real server; providers selected from env by the factories."""
    app = create_app()  # env-driven: real Deepgram/OpenAI/ElevenLabs
    config = uvicorn.Config(app, host=HOST, port=PORT,
                            log_level="info")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, thread


def _wait_healthy(timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(HEALTH, timeout=1) as resp:
                if resp.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("server did not become healthy")



# == client-side audio output (M7) ============================================
# The gateway forwards TTS audio as raw PCM16 / 16 kHz / mono binary frames
# (ElevenLabs output_format=pcm_16000). This script buffers the complete
# response, wraps it in a WAV header (stdlib `wave`), and plays it through
# the Windows speakers via the built-in System.Media.SoundPlayer (PowerShell
# interop) — no new Python dependency. FFmpeg's ffplay is used only as a
# fallback if the Windows player is unavailable.

SAMPLE_RATE_HZ: int = 16000
NUM_CHANNELS: int = 1
SAMPLE_WIDTH_BYTES: int = 2


def _pcm_to_wav(pcm: bytes) -> Path:
    """Wrap raw PCM16/16 kHz/mono bytes in a temporary WAV file."""
    wav_path = Path(tempfile.gettempdir()) / "voice_assistant_response.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(NUM_CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav.setframerate(SAMPLE_RATE_HZ)
        wav.writeframes(pcm)
    return wav_path


def _play_wav_windows(path: Path) -> None:
    """Play a WAV file synchronously via Windows SoundPlayer (PowerShell)."""
    command = (
        "Add-Type -AssemblyName System.Media; "
        f"(New-Object System.Media.SoundPlayer '{path}').PlaySync()"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = result.stderr
        message = (
            stderr.decode(errors="replace") if isinstance(stderr, bytes)
            else str(stderr)
        ).strip()
        raise RuntimeError(message)


def _play_wav_ffmpeg(path: Path) -> None:
    """Fallback playback via FFmpeg's ffplay."""
    subprocess.run(
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
        check=True,
    )


def play_response(pcm: bytes) -> None:
    """Play the buffered assistant speech through the speakers."""
    if not pcm:
        return
    wav_path = _pcm_to_wav(pcm)
    try:
        _play_wav_windows(wav_path)
    except (OSError, RuntimeError):
        if shutil.which("ffplay") is None:
            raise
        _play_wav_ffmpeg(wav_path)


async def _recv(ws: websockets.WebSocketClientProtocol,
                timeout: float = 60.0) -> object:
    return await asyncio.wait_for(ws.recv(), timeout=timeout)


async def run(audio: bytes) -> None:
    async with websockets.connect(URL) as ws:
        started = json.loads(await _recv(ws))
        assert started["type"] == "session.started"
        sid = started["session_id"]

        # 1) Stream real audio in 320-byte frames (10 ms of 16 kHz mono).
        frame_size = 320
        for i in range(0, len(audio), frame_size):
            await ws.send(audio[i:i + frame_size])
            await asyncio.sleep(0.01)

        # 2) Signal end-of-utterance.
        await ws.send(json.dumps({
            "type": "audio.eos", "version": "1.0", "session_id": sid,
            "request_id": None, "seq": 1, "timestamp": 0.0, "payload": {},
        }))

        # 3) Consume events until request completes/errors.
        #    Binary frames are the TTS audio: collect, never print.
        request_id = None
        audio_chunks: list[bytes] = []
        stages = {"transcript.partial": False, "transcript.final": False,
                  "llm.token": False, "tts.audio": False}
        saw_done = False
        metrics: dict[str, object] = {}
        while True:
            msg = await _recv(ws)
            if isinstance(msg, bytes):
                if not stages["tts.audio"]:
                    stages["tts.audio"] = True
                    print("TTS: receiving...")
                audio_chunks.append(msg)
                continue
            event = json.loads(msg)
            etype = event["type"]
            if etype == "request.started":
                request_id = event["request_id"]
                print("ASR: receiving...")
            elif etype == "transcript.partial":
                stages["transcript.partial"] = True
            elif etype == "transcript.final":
                stages["transcript.final"] = True
                print("LLM: receiving...")
            elif etype == "llm.token":
                stages["llm.token"] = True
            elif etype == "tts.done":
                saw_done = True
            elif etype == "metrics":
                metrics = event["payload"]
            elif etype == "request.completed":
                break
            elif etype == "request.error":
                raise AssertionError(f"request.error: {event}")
            elif etype == "llm.error":
                raise AssertionError(f"llm.error: {event}")

        assert request_id is not None, "no request.id received"
        assert stages["transcript.final"], "no transcript.final received"
        assert stages["llm.token"], "no LLM token received"
        assert audio_chunks, "no TTS audio received"
        assert saw_done, "no tts.done"
        for field in ("asr_first_transcript_ms", "asr_final_transcript_ms",
                      "llm_ttft_ms", "tts_ttfb_ms", "request_total_ms"):
            assert field in metrics, f"metrics missing {field}"

        # 4) Play the generated assistant speech through the speakers.
        print("Playing assistant response...")
        play_response(b"".join(audio_chunks))
        print("Playback complete.")
        print(f"LIVE SMOKE: PASS {request_id} metrics={json.dumps(metrics)}")


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python _smoke_e2e_live.py <audio.pcm> [voice_id]")
        sys.exit(2)
    audio_path = Path(sys.argv[1])
    if not audio_path.exists():
        print(f"audio file not found: {audio_path}")
        sys.exit(2)
    audio = audio_path.read_bytes()

    missing = _credentials_ready()
    if missing is not None:
        print(f"LIVE SMOKE: BLOCKED - missing credentials: {missing}")
        return

    print("Starting live voice pipeline...")
    server, thread = _start_server()
    try:
        _wait_healthy()
        asyncio.run(run(audio))
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


if __name__ == "__main__":
    main()
