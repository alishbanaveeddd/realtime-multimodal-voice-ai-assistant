"""Session model and in-memory session manager (architecture.md §10).

Sessions own client connectivity and resource lifetimes. IDs are
server-generated, opaque, and immutable (``ses_<hex>``). A session is created
``ACTIVE`` and may only transition to ``ENDED`` (window close, expiry, or
error). One session can own many requests.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from assistant.events.state import SessionState, validate_session_transition


class UnknownSessionError(KeyError):
    """Raised when looking up a session id that is not registered."""


@dataclass(slots=True)
class Session:
    """Immutable-by-convention session; state transitions are validated."""

    session_id: str
    created_at_ms: float
    state: SessionState = SessionState.ACTIVE

    def end(self) -> None:
        """Transition the session to ``ENDED`` (idempotent)."""
        if self.state is SessionState.ENDED:
            return
        validate_session_transition(self.state, SessionState.ENDED)
        self.state = SessionState.ENDED


def generate_session_id() -> str:
    """Return a fresh, opaque, server-generated session id."""
    return f"ses_{secrets.token_hex(16)}"


class SessionManager:
    """Registers and looks up sessions in memory."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock: Callable[[], float] = clock
        self._sessions: dict[str, Session] = {}

    def create(self) -> Session:
        """Create and register a new active session."""
        session_id = generate_session_id()
        session = Session(
            session_id=session_id, created_at_ms=self._clock() * 1000.0
        )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Session:
        """Return a registered session or raise ``UnknownSessionError``."""
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise UnknownSessionError(session_id) from exc

    def end(self, session_id: str) -> Session:
        """End a session (no-op if already ended). Returns the session."""
        session = self.get(session_id)
        session.end()
        return session

    def active(self) -> Mapping[str, Session]:
        """Return a read-only snapshot of currently registered sessions."""
        return dict(self._sessions)

    def __contains__(self, session_id: str) -> bool:
        return session_id in self._sessions
