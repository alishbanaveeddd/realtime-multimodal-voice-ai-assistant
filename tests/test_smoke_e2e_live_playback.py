"""Client-side playback helpers for the live smoke test (scripts/_smoke_e2e_live.py).

Deterministic offline tests: no audio hardware, no PowerShell, no network.
`subprocess.run` is monkeypatched so playback paths are fully mocked.
"""

from __future__ import annotations

import importlib.util
import subprocess
import wave
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "_smoke_e2e_live.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("_smoke_e2e_live", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Recorder:
    """Captures subprocess.run calls without executing anything."""

    def __init__(self, returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode

    def __call__(
        self, cmd: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, self.returncode, "", "boom")


def test_pcm_to_wav_has_contract_header(tmp_path: Path, monkeypatch: Any) -> None:
    """WAV must carry the PCM16/16 kHz/mono contract of the TTS stream."""
    module = _load_module()
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(tmp_path))
    wav_path = module._pcm_to_wav(b"\x00\x00" * 160)  # 10 ms of silence
    with wave.open(str(wav_path), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.getnframes() == 160


def test_play_response_uses_windows_soundplayer(monkeypatch: Any) -> None:
    """Primary path: powershell SoundPlayer PlaySync, no ffplay needed."""
    module = _load_module()
    recorder = _Recorder()
    monkeypatch.setattr(module.subprocess, "run", recorder)
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: "." )
    module.play_response(b"\x01\x00" * 16)
    assert len(recorder.calls) == 1
    assert recorder.calls[0][0] == "powershell"
    joined = " ".join(recorder.calls[0])
    assert "SoundPlayer" in joined and "PlaySync" in joined


def test_play_response_falls_back_to_ffplay(monkeypatch: Any) -> None:
    """If the Windows player fails and ffplay exists, ffplay is used."""
    module = _load_module()
    recorder = _Recorder(returncode=1)
    monkeypatch.setattr(module.subprocess, "run", recorder)
    monkeypatch.setattr(module.shutil, "which", lambda name: "ffplay")
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: "." )
    module.play_response(b"\x01\x00" * 16)
    assert recorder.calls[-1][0] == "ffplay"


def test_play_response_raises_without_ffplay(monkeypatch: Any) -> None:
    """No fallback available -> the error propagates (no silent success)."""
    module = _load_module()
    recorder = _Recorder(returncode=1)
    monkeypatch.setattr(module.subprocess, "run", recorder)
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: "." )
    with pytest.raises(RuntimeError):
        module.play_response(b"\x01\x00" * 16)


def test_play_response_empty_is_noop(monkeypatch: Any) -> None:
    """No audio -> nothing is played and no temp file is touched."""
    module = _load_module()
    recorder = _Recorder()
    monkeypatch.setattr(module.subprocess, "run", recorder)
    module.play_response(b"")
    assert recorder.calls == []
