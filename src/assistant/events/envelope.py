"""The common event envelope (architecture.md §8.2).

Every structured event uses one envelope: type, schema version, session/request
identifiers, a per-request sequence number, a monotonic timestamp (ms), and a
free-form payload. The envelope enforces the structural contract with Pydantic;
type membership and origin rules are enforced by the validator.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Schema version for every event in this milestone.
EVENT_VERSION: str = "1.0"


class EventEnvelope(BaseModel):
    """A validated structured event envelope."""

    model_config = ConfigDict(extra="forbid")

    type: str
    version: str = EVENT_VERSION
    session_id: str
    request_id: str | None = None
    seq: int = Field(ge=0, description="Strictly increasing per request")
    timestamp: float = Field(
        ge=0.0, description="Monotonic timestamp in milliseconds"
    )
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _type_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("type must not be blank")
        return stripped

    def to_wire(self) -> dict[str, Any]:
        """Return the envelope as a JSON-serializable mapping."""
        return self.model_dump(mode="json")


def envelope_from_mapping(data: dict[str, Any]) -> EventEnvelope:
    """Build and validate an envelope from a mapping.

    Raises ``pydantic.ValidationError`` on structural failure.
    """
    return EventEnvelope.model_validate(data)
