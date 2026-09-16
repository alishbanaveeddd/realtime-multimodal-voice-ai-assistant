"""Tests for the event envelope and boundary validator (architecture.md §8)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from assistant.events.catalog import EventOrigin
from assistant.events.envelope import EVENT_VERSION, EventEnvelope
from assistant.events.validator import (
    validate_event,
    validation_error_payload,
)


def _envelope(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "type": "audio.eos",
        "version": EVENT_VERSION,
        "session_id": "ses_abc",
        "request_id": "req_123",
        "seq": 1,
        "timestamp": 1000.0,
        "payload": {},
    }
    base.update(overrides)
    return base


def test_valid_client_event_passes() -> None:
    result = validate_event(_envelope(), sender=EventOrigin.CLIENT)
    assert result.is_valid
    assert result.envelope is not None
    assert result.envelope.type == "audio.eos"


def test_missing_required_field_is_invalid() -> None:
    data = _envelope()
    del data["seq"]
    result = validate_event(data, sender=EventOrigin.CLIENT)
    assert not result.is_valid
    assert any(i.field == "seq" for i in result.issues)


def test_unknown_event_type_is_invalid() -> None:
    result = validate_event(_envelope(type="bogus.type"), sender=EventOrigin.CLIENT)
    assert not result.is_valid
    assert result.issues[0].field == "type"
    assert "unknown" in result.issues[0].message


def test_wrong_schema_version_is_invalid() -> None:
    result = validate_event(
        _envelope(version="9.9"), sender=EventOrigin.CLIENT
    )
    assert not result.is_valid
    assert any(i.field == "version" for i in result.issues)


def test_server_only_event_rejected_from_client() -> None:
    # 'session.started' is server-only; a client may not send it.
    result = validate_event(
        _envelope(type="session.started"), sender=EventOrigin.CLIENT
    )
    assert not result.is_valid
    assert any(i.field == "type" for i in result.issues)


def test_client_event_rejected_from_server_sender() -> None:
    # 'audio.frame' is client-only; a server sender must not claim it.
    result = validate_event(
        _envelope(type="audio.frame"), sender=EventOrigin.SERVER
    )
    assert not result.is_valid
    assert any(i.field == "type" for i in result.issues)


def test_both_origin_accepted_from_either_sender() -> None:
    for sender in (EventOrigin.CLIENT, EventOrigin.SERVER):
        result = validate_event(_envelope(type="error"), sender=sender)
        assert result.is_valid, f"failed for sender {sender}"


def test_envelope_to_wire_round_trips() -> None:
    wire = validate_event(_envelope(), sender=EventOrigin.CLIENT)
    assert wire.envelope is not None
    dumped = wire.envelope.to_wire()
    rebuilt = EventEnvelope.model_validate(dumped)
    assert rebuilt == wire.envelope


def test_extra_fields_are_rejected() -> None:
    result = validate_event(
        _envelope(extra_field="boom"), sender=EventOrigin.CLIENT
    )
    assert not result.is_valid
    assert any("extra_field" in i.field for i in result.issues)


def test_validation_error_payload_shape() -> None:
    data = _envelope(seq=-1)
    result = validate_event(data, sender=EventOrigin.CLIENT)
    payload = validation_error_payload(result.issues)
    assert payload["kind"] == "validation"
    assert isinstance(payload["issues"], list)
    assert all("field" in issue and "message" in issue for issue in payload["issues"])


def test_envelope_blank_type_rejected() -> None:
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(_envelope(type="   "))
