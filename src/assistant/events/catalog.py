"""The agreed event vocabulary (architecture.md §9.1).

The catalog is the single source of truth for which event types the system
understands, who may originate them, and their meaning. The validator and the
transport gateway both consult this registry, keeping the set of supported
events explicit and testable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class EventOrigin(StrEnum):
    """Which side is allowed to originate an event.

    ``CLIENT``/``SERVER`` are one-directional; ``BOTH`` may originate from
    either endpoint.
    """

    CLIENT = "client"
    SERVER = "server"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class EventSpec:
    """Immutable description of one event type."""

    name: str
    origin: EventOrigin
    description: str


def _spec(name: str, origin: EventOrigin, description: str) -> EventSpec:
    return EventSpec(name=name, origin=origin, description=description)


# --- Server-originated (streaming / lifecycle) events -----------------------
_SERVER_EVENTS: Final[tuple[EventSpec, ...]] = (
    _spec("session.started", EventOrigin.SERVER, "WS accepted; session_id issued"),
    _spec(
        "session.ended",
        EventOrigin.SERVER,
        "Session closed/expired; resources released",
    ),
    _spec("transcript.partial", EventOrigin.SERVER, "Interim ASR hypothesis"),
    _spec(
        "transcript.final", EventOrigin.SERVER, "Finalized ASR result for the request"
    ),
    _spec("llm.token", EventOrigin.SERVER, "A streamed text token (delta)"),
    _spec("llm.error", EventOrigin.SERVER, "Provider/stream failure in generation"),
    _spec("tts.audio", EventOrigin.SERVER, "Synthesized audio chunk (binary)"),
    _spec("tts.done", EventOrigin.SERVER, "TTS finished for the request"),
    _spec("request.started", EventOrigin.SERVER, "New request_id opened"),
    _spec("request.completed", EventOrigin.SERVER, "Request finished normally"),
    _spec("metrics", EventOrigin.SERVER, "Latency/telemetry snapshot for a request"),
    _spec("replay", EventOrigin.SERVER, "Replay control event"),
)

# --- Client-originated events ----------------------------------------------
_CLIENT_EVENTS: Final[tuple[EventSpec, ...]] = (
    _spec("audio.frame", EventOrigin.CLIENT, "Binary audio chunk"),
    _spec(
        "audio.eos", EventOrigin.CLIENT, "Client signals end-of-stream for utterance"
    ),
)

# --- Either-side terminal events -------------------------------------------
_TERMINAL_EVENTS: Final[tuple[EventSpec, ...]] = (
    _spec("request.canceled", EventOrigin.BOTH, "Request interrupted"),
    _spec("request.error", EventOrigin.SERVER, "Terminal error for the request"),
    _spec("error", EventOrigin.BOTH, "Non-request-scoped error (transport/validation)"),
)

_EVENT_CATALOG: Final[Mapping[str, EventSpec]] = {
    spec.name: spec
    for spec in (*_SERVER_EVENTS, *_CLIENT_EVENTS, *_TERMINAL_EVENTS)
}

#: Human-readable cache of type -> origin for wiring tables (maintains order).
CATALOG: Final[tuple[EventSpec, ...]] = tuple(_EVENT_CATALOG.values())


def is_known_type(event_type: str) -> bool:
    """Return whether ``event_type`` exists in the catalog."""
    return event_type in _EVENT_CATALOG


def origin_of(event_type: str) -> EventOrigin:
    """Return the origin of a known event type; raise ``KeyError`` if unknown."""
    return _EVENT_CATALOG[event_type].origin


def catalogs() -> Mapping[str, EventSpec]:
    """Return an read-only view of the catalog keyed by event type name."""
    return MappingProxyType(_EVENT_CATALOG)
