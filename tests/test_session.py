"""Tests for the session model and session manager (architecture.md §10)."""

from __future__ import annotations

import pytest

from assistant.core.session import (
    SessionManager,
    UnknownSessionError,
    generate_session_id,
)
from assistant.events.state import SessionState


def test_generate_session_id_is_prefixed_and_unique() -> None:
    a, b = generate_session_id(), generate_session_id()
    assert a.startswith("ses_")
    assert a != b


def test_create_registers_active_session(sessions: SessionManager) -> None:
    session = sessions.create()
    assert session.state is SessionState.ACTIVE
    assert session.session_id in sessions
    assert sessions.get(session.session_id) is session


def test_get_unknown_session_raises(sessions: SessionManager) -> None:
    with pytest.raises(UnknownSessionError):
        sessions.get("ses_missing")


def test_end_transitions_to_ended(sessions: SessionManager) -> None:
    session = sessions.create()
    returned = sessions.end(session.session_id)
    assert returned.state is SessionState.ENDED
    assert session.state is SessionState.ENDED


def test_end_is_idempotent(sessions: SessionManager) -> None:
    session = sessions.create()
    sessions.end(session.session_id)
    # Second end must be a no-op, not an error.
    sessions.end(session.session_id)
    assert session.state is SessionState.ENDED


def test_session_created_at_uses_injected_clock() -> None:
    manager = SessionManager(clock=lambda: 500.0)
    session = manager.create()
    assert session.created_at_ms == 500.0 * 1000.0


def test_active_snapshot_contains_created(sessions: SessionManager) -> None:
    session = sessions.create()
    assert sessions.active()[session.session_id] is session
