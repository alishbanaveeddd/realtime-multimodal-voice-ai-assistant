"""Mysa web entrypoint: serve the EXISTING backend for the browser frontend.

This is a new, thin launch script only — it does not modify any backend code.
It runs the same ``create_app()`` used by every other entrypoint on port 8000,
which the Vite dev server proxies (``/ws`` → here).

Run (PowerShell)::

    .venv\\Scripts\\python scripts\\run_server.py
"""

from __future__ import annotations

import uvicorn

from assistant.transport.app import create_app


def main() -> None:
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
