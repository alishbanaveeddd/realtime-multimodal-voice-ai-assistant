"""Lifecycle state machines (architecture.md §9.2 / §10).

Session and request objects expose explicit state transitions. Only the
transitions declared below are legal; any other transition raises
``IllegalTransitionError``. Request terminal states (``COMPLETED``,
``CANCELED``, ``ERROR``) accept no further transitions.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class IllegalTransitionError(RuntimeError):
    """Raised when an object attempts an illegal lifecycle transition."""


class SessionState(StrEnum):
    ACTIVE = "active"
    ENDED = "ended"


class RequestState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELED = "canceled"
    ERROR = "error"


#: Legal session transitions: active -> ended only.
_SESSION_TRANSITIONS: Final[dict[SessionState, frozenset[SessionState]]] = {
    SessionState.ACTIVE: frozenset({SessionState.ENDED}),
    SessionState.ENDED: frozenset(),
}

#: Legal request transitions (terminal states admit no successors).
_REQUEST_TRANSITIONS: Final[dict[RequestState, frozenset[RequestState]]] = {
    RequestState.CREATED: frozenset(
        {RequestState.RUNNING, RequestState.CANCELED, RequestState.ERROR}
    ),
    RequestState.RUNNING: frozenset(
        {RequestState.COMPLETED, RequestState.CANCELED, RequestState.ERROR}
    ),
    RequestState.COMPLETED: frozenset(),
    RequestState.CANCELED: frozenset(),
    RequestState.ERROR: frozenset(),
}

_REQUEST_TERMINAL: Final[frozenset[RequestState]] = frozenset(
    {
        RequestState.COMPLETED,
        RequestState.CANCELED,
        RequestState.ERROR,
    }
)


def is_request_terminal(state: RequestState) -> bool:
    """Return whether ``state`` is a terminal request state."""
    return state in _REQUEST_TERMINAL


def validate_session_transition(
    current: SessionState, target: SessionState
) -> None:
    """Raise ``IllegalTransitionError`` if the session transition is illegal."""
    if target not in _SESSION_TRANSITIONS[current]:
        raise IllegalTransitionError(
            f"illegal session transition {current.value} -> {target.value}"
        )


def validate_request_transition(
    current: RequestState, target: RequestState
) -> None:
    """Raise ``IllegalTransitionError`` if the request transition is illegal."""
    if target not in _REQUEST_TRANSITIONS[current]:
        raise IllegalTransitionError(
            f"illegal request transition {current.value} -> {target.value}"
        )
