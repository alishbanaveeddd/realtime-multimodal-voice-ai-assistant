"""Request model and in-memory request manager (architecture.md §10).

A request is the unit of one user utterance -> response cycle. IDs are
server-generated and immutable (``req_<hex>``). Each request starts in
``CREATED``, may run, and terminates in exactly one of ``COMPLETED``,
``CANCELED``, or ``ERROR`` (no further transitions once terminal).
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from assistant.events.state import (
    RequestState,
    is_request_terminal,
    validate_request_transition,
)


class UnknownRequestError(KeyError):
    """Raised when looking up a request id that is not registered."""


@dataclass(slots=True)
class Request:
    """One request cycle; state transitions are validated."""

    request_id: str
    session_id: str
    created_at_ms: float
    state: RequestState = RequestState.CREATED

    def run(self) -> None:
        """Transition from ``CREATED`` to ``RUNNING``."""
        validate_request_transition(self.state, RequestState.RUNNING)
        self.state = RequestState.RUNNING

    def complete(self) -> None:
        """Transition to the terminal ``COMPLETED`` state."""
        validate_request_transition(self.state, RequestState.COMPLETED)
        self.state = RequestState.COMPLETED

    def cancel(self) -> None:
        """Transition to the terminal ``CANCELED`` state (idempotent)."""
        if self.state is RequestState.CANCELED:
            return
        validate_request_transition(self.state, RequestState.CANCELED)
        self.state = RequestState.CANCELED

    def fail(self) -> None:
        """Transition to the terminal ``ERROR`` state (idempotent)."""
        if self.state is RequestState.ERROR:
            return
        validate_request_transition(self.state, RequestState.ERROR)
        self.state = RequestState.ERROR

    @property
    def is_terminal(self) -> bool:
        return is_request_terminal(self.state)


def generate_request_id() -> str:
    """Return a fresh, opaque, server-generated request id."""
    return f"req_{secrets.token_hex(16)}"


class RequestManager:
    """Registers and looks up requests in memory."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock: Callable[[], float] = clock
        self._requests: dict[str, Request] = {}

    def create(self, session_id: str) -> Request:
        """Create and register a new request bound to ``session_id``."""
        request_id = generate_request_id()
        request = Request(
            request_id=request_id,
            session_id=session_id,
            created_at_ms=self._clock() * 1000.0,
        )
        self._requests[request_id] = request
        return request

    def get(self, request_id: str) -> Request:
        """Return a registered request or raise ``UnknownRequestError``."""
        try:
            return self._requests[request_id]
        except KeyError as exc:
            raise UnknownRequestError(request_id) from exc

    def snapshot(self) -> Mapping[str, Request]:
        """Return a read-only snapshot of all registered requests."""
        return dict(self._requests)

    def __contains__(self, request_id: str) -> bool:
        return request_id in self._requests
