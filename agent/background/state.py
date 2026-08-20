from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Mapping

_TASK_KIND = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")
_ALLOWED_TRANSITIONS: dict[AsyncTaskStatus, frozenset[AsyncTaskStatus]]


class AsyncTaskStatus(StrEnum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {
            AsyncTaskStatus.SUCCEEDED,
            AsyncTaskStatus.FAILED,
            AsyncTaskStatus.CANCELLED,
        }


_ALLOWED_TRANSITIONS = {
    AsyncTaskStatus.ACCEPTED: frozenset(
        {
            AsyncTaskStatus.RUNNING,
            AsyncTaskStatus.FAILED,
            AsyncTaskStatus.CANCELLED,
        }
    ),
    AsyncTaskStatus.RUNNING: frozenset(
        {
            AsyncTaskStatus.SUCCEEDED,
            AsyncTaskStatus.FAILED,
            AsyncTaskStatus.CANCELLED,
        }
    ),
    AsyncTaskStatus.SUCCEEDED: frozenset(),
    AsyncTaskStatus.FAILED: frozenset(),
    AsyncTaskStatus.CANCELLED: frozenset(),
}


class AsyncTaskTransitionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AsyncTaskState:
    task_id: str
    task_kind: str
    status: AsyncTaskStatus
    attempt: int
    accepted_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    reason_code: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        _bounded("task_id", self.task_id, maximum=128)
        if not _TASK_KIND.fullmatch(self.task_kind):
            raise ValueError("async task kind is invalid")
        if isinstance(self.attempt, bool) or self.attempt < 1:
            raise ValueError("async task attempt must be positive")
        for label, value in (
            ("accepted_at", self.accepted_at),
            ("updated_at", self.updated_at),
            ("started_at", self.started_at),
            ("finished_at", self.finished_at),
        ):
            if value is not None and value.tzinfo is None:
                raise ValueError(f"async task {label} must include a timezone")
            if value is not None:
                object.__setattr__(self, label, value.astimezone(UTC))
        if self.updated_at < self.accepted_at:
            raise ValueError("async task updated_at precedes accepted_at")
        if self.started_at is not None and self.started_at < self.accepted_at:
            raise ValueError("async task started_at precedes accepted_at")
        if self.finished_at is not None and self.finished_at < self.accepted_at:
            raise ValueError("async task finished_at precedes accepted_at")
        if self.status is AsyncTaskStatus.ACCEPTED and (
            self.started_at is not None or self.finished_at is not None
        ):
            raise ValueError("accepted async task cannot have execution timestamps")
        if self.status is AsyncTaskStatus.RUNNING and (
            self.started_at is None or self.finished_at is not None
        ):
            raise ValueError("running async task timestamps are invalid")
        if self.status.is_terminal and self.finished_at is None:
            raise ValueError("terminal async task needs finished_at")
        if self.status is AsyncTaskStatus.SUCCEEDED and self.error_code is not None:
            raise ValueError("succeeded async task cannot have an error code")
        if self.status is AsyncTaskStatus.FAILED and not self.error_code:
            raise ValueError("failed async task needs an error code")
        for label, value in (
            ("reason_code", self.reason_code),
            ("error_code", self.error_code),
        ):
            if value is not None:
                _bounded(label, value, maximum=128)

    @classmethod
    def accepted(
        cls,
        *,
        task_id: str,
        task_kind: str,
        attempt: int = 1,
        now: datetime | None = None,
    ) -> AsyncTaskState:
        timestamp = now or datetime.now(UTC)
        return cls(
            task_id=task_id,
            task_kind=task_kind,
            status=AsyncTaskStatus.ACCEPTED,
            attempt=attempt,
            accepted_at=timestamp,
            updated_at=timestamp,
        )

    def transition(
        self,
        status: AsyncTaskStatus,
        *,
        now: datetime | None = None,
        reason_code: str | None = None,
        error_code: str | None = None,
    ) -> AsyncTaskState:
        if status not in _ALLOWED_TRANSITIONS[self.status]:
            raise AsyncTaskTransitionError(
                f"invalid async task transition: {self.status.value}->{status.value}"
            )
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        if timestamp < self.updated_at:
            raise ValueError("async task transition timestamp precedes current state")
        started_at = self.started_at
        if status is AsyncTaskStatus.RUNNING:
            started_at = timestamp
        finished_at = timestamp if status.is_terminal else None
        return replace(
            self,
            status=status,
            updated_at=timestamp,
            started_at=started_at,
            finished_at=finished_at,
            reason_code=reason_code,
            error_code=error_code,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "1",
            "task_id": self.task_id,
            "task_kind": self.task_kind,
            "status": self.status.value,
            "attempt": self.attempt,
            "accepted_at": _timestamp(self.accepted_at),
            "started_at": _timestamp(self.started_at),
            "finished_at": _timestamp(self.finished_at),
            "updated_at": _timestamp(self.updated_at),
            "reason_code": self.reason_code,
            "error_code": self.error_code,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> AsyncTaskState:
        if raw.get("schema_version") != "1":
            raise ValueError("unsupported async task state schema")
        return cls(
            task_id=_required_string(raw, "task_id"),
            task_kind=_required_string(raw, "task_kind"),
            status=AsyncTaskStatus(_required_string(raw, "status")),
            attempt=_required_int(raw, "attempt"),
            accepted_at=_required_datetime(raw, "accepted_at"),
            started_at=_optional_datetime(raw, "started_at"),
            finished_at=_optional_datetime(raw, "finished_at"),
            updated_at=_required_datetime(raw, "updated_at"),
            reason_code=_optional_string(raw, "reason_code"),
            error_code=_optional_string(raw, "error_code"),
        )


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _required_datetime(raw: Mapping[str, object], key: str) -> datetime:
    return _parse_datetime(_required_string(raw, key))


def _optional_datetime(raw: Mapping[str, object], key: str) -> datetime | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"async task {key} must be a timestamp")
    return _parse_datetime(value)


def _required_string(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"async task {key} must be a string")
    return value


def _optional_string(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"async task {key} must be a string")
    return value


def _required_int(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"async task {key} must be an integer")
    return value


def _bounded(label: str, value: str, *, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(character in value for character in "\r\n\t")
    ):
        raise ValueError(f"async task {label} is invalid")


__all__ = [
    "AsyncTaskState",
    "AsyncTaskStatus",
    "AsyncTaskTransitionError",
]
