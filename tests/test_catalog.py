"""Tests for the event catalog (architecture.md §9.1)."""

from __future__ import annotations

import pytest

from assistant.events.catalog import (
    EventOrigin,
    catalogs,
    is_known_type,
    origin_of,
)


def test_all_expected_server_events_are_known() -> None:
    expected_server = {
        "session.started",
        "session.ended",
        "transcript.partial",
        "transcript.final",
        "llm.token",
        "llm.error",
        "tts.audio",
        "tts.done",
        "request.started",
        "request.completed",
        "metrics",
        "replay",
    }
    for name in expected_server:
        assert is_known_type(name), f"missing known event: {name}"


def test_all_expected_client_events_are_known() -> None:
    for name in {"audio.frame", "audio.eos"}:
        assert is_known_type(name), f"missing known event: {name}"


def test_terminal_events_mostly_server_or_both() -> None:
    for origin in (origin_of("request.error"), origin_of("request.canceled")):
        assert origin in (EventOrigin.SERVER, EventOrigin.BOTH)


def test_unknown_event_is_not_known() -> None:
    assert not is_known_type("not.a.real.event")


def test_catalog_keys_match_specified_origins() -> None:
    assert origin_of("session.started") is EventOrigin.SERVER
    assert origin_of("audio.frame") is EventOrigin.CLIENT
    assert origin_of("error") is EventOrigin.BOTH


def test_catalogs_returns_snapshot_and_does_not_allow_mutation() -> None:
    snapshot = catalogs()
    with pytest.raises(TypeError):
        snapshot["nope"] = "x"  # type: ignore[index]
    assert "session.started" in snapshot
