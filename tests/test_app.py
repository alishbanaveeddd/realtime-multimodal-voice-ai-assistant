"""End-to-end tests for the FastAPI app: /health and the /ws WebSocket."""

from __future__ import annotations

from fastapi.testclient import TestClient

from assistant.asr.fake import FakeASRProvider, FakeASRScript
from assistant.events.envelope import EVENT_VERSION
from assistant.transport.app import create_app


def test_health_returns_ok() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_ws_connect_emits_session_started() -> None:
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            started = websocket.receive_json()
            assert started["type"] == "session.started"
            assert started["session_id"].startswith("ses_")


def test_ws_accepts_valid_client_event() -> None:
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            started = websocket.receive_json()
            sid = started["session_id"]
            websocket.send_json({
                "type": "audio.eos", "version": EVENT_VERSION,
                "session_id": sid, "request_id": None, "seq": 1,
                "timestamp": 1000.0, "payload": {},
            })
            websocket.close()


def test_ws_rejects_unknown_event_with_validation_error() -> None:
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            started = websocket.receive_json()
            sid = started["session_id"]
            websocket.send_json({
                "type": "bogus.type", "version": EVENT_VERSION,
                "session_id": sid, "request_id": None, "seq": 1,
                "timestamp": 1000.0, "payload": {},
            })
            error = websocket.receive_json()
            assert error["type"] == "error"
            assert error["payload"]["kind"] == "validation"


def test_ws_audio_frame_yields_transcript_events() -> None:
    script = FakeASRScript(partials=("hi", "hi there"), final="hi there")
    app = create_app(asr=FakeASRProvider(script))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            started = websocket.receive_json()
            sid = started["session_id"]
            websocket.send_bytes(b"\x00\x01" * 160)
            # The gateway emits request.started, then transcript.partial events.
            # The fake emits 2 partials on the first frame, so we expect 3 text events.
            events = [websocket.receive_json() for _ in range(3)]
            partial_events = [e for e in events if e["type"] == "transcript.partial"]
            assert len(partial_events) == 2
            assert partial_events[0]["session_id"] == sid
            assert partial_events[0]["request_id"] is not None
            assert partial_events[0]["payload"]["text"] in ("hi", "hi there")


def test_ws_invalid_audio_frame_returns_validation_error() -> None:
    app = create_app(asr=FakeASRProvider(FakeASRScript()))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.receive_json()
            websocket.send_bytes(b"\x00\x01\x02")
            error = websocket.receive_json()
            assert error["type"] == "error"
            assert error["payload"]["kind"] == "validation"
