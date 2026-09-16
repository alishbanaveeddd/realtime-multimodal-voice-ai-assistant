"""Tests for session/request lifecycle state machines (architecture.md §9.2/§10)."""

from __future__ import annotations

import pytest

from assistant.events.state import (
    IllegalTransitionError,
    RequestState,
    SessionState,
    is_request_terminal,
    validate_request_transition,
    validate_session_transition,
)


# --- session transitions ----------------------------------------------------
def test_session_active_to_ended_is_legal() -> None:
    validate_session_transition(SessionState.ACTIVE, SessionState.ENDED)


def test_session_ended_transition_is_illegal() -> None:
    with pytest.raises(IllegalTransitionError):
        validate_session_transition(SessionState.ENDED, SessionState.ENDED)


# --- request transitions ----------------------------------------------------
def test_request_created_to_running_is_legal() -> None:
    validate_request_transition(RequestState.CREATED, RequestState.RUNNING)


def test_request_created_can_cancel() -> None:
    validate_request_transition(RequestState.CREATED, RequestState.CANCELED)


def test_request_running_to_completed_is_legal() -> None:
    validate_request_transition(RequestState.RUNNING, RequestState.COMPLETED)


def test_request_created_to_completed_is_illegal() -> None:
    with pytest.raises(IllegalTransitionError):
        validate_request_transition(RequestState.CREATED, RequestState.COMPLETED)


def test_request_terminal_states_accept_no_more() -> None:
    for terminal in (
        RequestState.COMPLETED,
        RequestState.CANCELED,
        RequestState.ERROR,
    ):
        for target in RequestState:
            with pytest.raises(IllegalTransitionError):
                validate_request_transition(terminal, target)


def test_is_request_terminal() -> None:
    assert is_request_terminal(RequestState.COMPLETED)
    assert is_request_terminal(RequestState.CANCELED)
    assert is_request_terminal(RequestState.ERROR)
    assert not is_request_terminal(RequestState.RUNNING)
    assert not is_request_terminal(RequestState.CREATED)
