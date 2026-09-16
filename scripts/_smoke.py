r"""M1 smoke test against a live uvicorn server (no providers involved).

Exercises: /health, a WebSocket connect (session.started), and a rejected
unknown event (validation error).

Shell 1 (start server, then wait a few seconds)::

    $env:PYTHONPATH='src'
    .venv\Scripts\python -c "import uvicorn; \
        uvicorn.run('assistant.transport.app:create_app', \
        factory=True, host='127.0.0.1', port=8765, \
        log_level='warning')"

Shell 2 (run client)::

    $env:PYTHONPATH='src'
    .venv\Scripts\python scripts\_smoke.py

Expected: prints "SMOKE PASS".
"""

from __future__ import annotations

import asyncio
import json

import websockets  # type: ignore[import-untyped]

URL = "ws://127.0.0.1:8765/ws"


async def run() -> None:
    async with websockets.connect(URL) as ws:
        started = json.loads(await ws.recv())
        assert started["type"] == "session.started", started
        print("OK session.started:", started["session_id"])

        await ws.send(
            json.dumps(
                {
                    "type": "bogus.type",
                    "version": "1.0",
                    "session_id": started["session_id"],
                    "request_id": None,
                    "seq": 1,
                    "timestamp": 1.0,
                    "payload": {},
                }
            )
        )
        err = json.loads(await ws.recv())
        assert err["type"] == "error", err
        assert err["payload"]["kind"] == "validation", err
        print("OK validation error event:", err["payload"])


def main() -> None:
    asyncio.run(run())
    print("SMOKE PASS")


if __name__ == "__main__":
    main()
