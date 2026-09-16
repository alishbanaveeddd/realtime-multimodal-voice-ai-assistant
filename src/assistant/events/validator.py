"""Boundary event validation (architecture.md §8.4).

Every event entering or leaving the system is validated against the catalog
and the envelope's structural contract. A failed event yields a typed issue
set rather than raising, so the transport gateway can surface a structured
``error`` event (kind = ``validation``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from pydantic import ValidationError

from assistant.events.catalog import EventOrigin, is_known_type, origin_of
from assistant.events.envelope import EVENT_VERSION, EventEnvelope

#: Stable machine-readable error kind assigned to validation failures (§11).
VALIDATION_KIND: Final[str] = "validation"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A single validation failure on one field."""

    field: str
    message: str


@dataclass(slots=True)
class ValidationResult:
    """Result of validating one event."""

    envelope: EventEnvelope | None = None
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.issues


def _structural_issues(data: dict[str, Any]) -> list[ValidationIssue]:
    try:
        EventEnvelope.model_validate(data)
    except ValidationError as exc:
        return [
            ValidationIssue(field=".".join(map(str, e["loc"])), message=e["msg"])
            for e in exc.errors()
        ]
    return []


def validate_event(
    data: dict[str, Any],
    *,
    sender: EventOrigin = EventOrigin.CLIENT,
) -> ValidationResult:
    """Validate a raw event mapping against the envelope and catalog.

    ``sender`` must be ``CLIENT`` for events originating at the client and
    ``SERVER`` for events the server itself emits. Origin rules from the
    catalog are enforced accordingly.
    """
    issues: list[ValidationIssue] = _structural_issues(data)
    if issues:
        return ValidationResult(envelope=None, issues=issues)

    envelope: EventEnvelope = EventEnvelope.model_validate(data)

    if not is_known_type(envelope.type):
        issues.append(
            ValidationIssue("type", f"unknown event type: {envelope.type!r}")
        )
        return ValidationResult(envelope=envelope, issues=issues)

    if envelope.version != EVENT_VERSION:
        issues.append(
            ValidationIssue(
                "version",
                f"unsupported schema version {envelope.version!r}; "
                f"expected {EVENT_VERSION!r}",
            )
        )

    spec_origin = origin_of(envelope.type)
    if spec_origin is not EventOrigin.BOTH and spec_origin is not sender:
        issues.append(
            ValidationIssue(
                "type",
                f"event {envelope.type!r} is not allowed to originate "
                f"from the {sender.value} endpoint",
            )
        )

    return ValidationResult(envelope=envelope, issues=issues)


def validation_error_payload(
    issues: Sequence[ValidationIssue],
) -> dict[str, Any]:
    """Build the ``payload`` for an ``error`` event describing validation issues."""
    return {
        "kind": VALIDATION_KIND,
        "issues": [
            {"field": issue.field, "message": issue.message} for issue in issues
        ],
    }
