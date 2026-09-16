"""Tests for the request model and request manager (architecture.md §10)."""

from __future__ import annotations

import pytest

from assistant.core.request import (
    RequestManager,
    UnknownRequestError,
    generate_request_id,
)
from assistant.core.session import SessionManager
from assistant.events.state import IllegalTransitionError, RequestState


def test_generate_request_id_is_prefixed_and_unique() -> None:
    a, b = generate_request_id(), generate_request_id()
    assert a.startswith("req_")
    assert a != b


def test_create_binds_request_to_session(
    sessions: SessionManager, requests_mgr: RequestManager
) -> None:
    session = sessions.create()
    request = requests_mgr.create(session.session_id)
    assert request.session_id == session.session_id
    assert request.state is RequestState.CREATED
    assert request.request_id in requests_mgr


def test_request_full_lifecycle(
    sessions: SessionManager, requests_mgr: RequestManager
) -> None:
    session = sessions.create()
    request = requests_mgr.create(session.session_id)
    request.run()
    assert request.state is RequestState.RUNNING
    request.complete()
    assert request.state is RequestState.COMPLETED
    assert request.is_terminal


def test_request_cancel_is_idempotent(
    sessions: SessionManager, requests_mgr: RequestManager
) -> None:
    session = sessions.create()
    request = requests_mgr.create(session.session_id)
    request.cancel()
    request.cancel()  # no-op on already canceled
    assert request.state is RequestState.CANCELED


def test_request_created_to_completed_is_illegal(
    sessions: SessionManager, requests_mgr: RequestManager
) -> None:
    session = sessions.create()
    request = requests_mgr.create(session.session_id)
    with pytest.raises(IllegalTransitionError):
        request.complete()  # CREATED -> COMPLETED is not legal


def test_error_transition_is_terminal(
    sessions: SessionManager, requests_mgr: RequestManager
) -> None:
    session = sessions.create()
    request = requests_mgr.create(session.session_id)
    request.fail()
    assert request.is_terminal
    assert request.state is RequestState.ERROR


def test_get_unknown_request_raises(requests_mgr: RequestManager) -> None:
    with pytest.raises(UnknownRequestError):
        requests_mgr.get("req_missing")


def test_request_uses_injected_clock(sessions: SessionManager) -> None:
    manager = RequestManager(clock=lambda: 300.0)
    session = sessions.create()
    request = manager.create(session.session_id)
    assert request.created_at_ms == 300.0 * 1000.0
