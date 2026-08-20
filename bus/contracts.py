from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, TypeVar

_PayloadT = TypeVar("_PayloadT")
_EVENT_ID = re.compile(r"^event:[0-9a-f]{32}$")
_DOTTED_NAME = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")


@dataclass(frozen=True, slots=True)
class EventEnvelope(Generic[_PayloadT]):
    schema_version: str
    event_id: str
    event_type: str
    source: str
    subject_kind: str
    subject_id: str
    occurred_at: datetime
    payload: _PayloadT
    correlation_id: str = ""
    causation_id: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != "1":
            raise ValueError("unsupported event envelope schema version")
        if not _EVENT_ID.fullmatch(self.event_id):
            raise ValueError("event envelope event_id is invalid")
        for label, value in (
            ("event_type", self.event_type),
            ("source", self.source),
            ("subject_kind", self.subject_kind),
        ):
            if not _DOTTED_NAME.fullmatch(value):
                raise ValueError(f"event envelope {label} is invalid")
        _bounded_identity("subject_id", self.subject_id)
        if self.correlation_id:
            _bounded_identity("correlation_id", self.correlation_id)
        if self.causation_id is not None:
            _bounded_identity("causation_id", self.causation_id)
        if self.occurred_at.tzinfo is None:
            raise ValueError("event envelope timestamp must include a timezone")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))

    @classmethod
    def create(
        cls,
        *,
        event_type: str,
        source: str,
        subject_kind: str,
        subject_id: str,
        payload: _PayloadT,
        correlation_id: str = "",
        causation_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> EventEnvelope[_PayloadT]:
        return cls(
            schema_version="1",
            event_id=f"event:{uuid.uuid4().hex}",
            event_type=event_type,
            source=source,
            subject_kind=subject_kind,
            subject_id=subject_id,
            occurred_at=occurred_at or datetime.now(UTC),
            payload=payload,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    def header(self) -> dict[str, str | None]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source": self.source,
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "occurred_at": self.occurred_at.isoformat().replace("+00:00", "Z"),
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
        }


def _bounded_identity(label: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 256
        or any(character in value for character in "\r\n\t")
    ):
        raise ValueError(f"event envelope {label} is invalid")


__all__ = ["EventEnvelope"]
