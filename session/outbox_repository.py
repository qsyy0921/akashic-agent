from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any, Literal, cast

from session.reliability_records import (
    DeliveryAttemptRecord,
    DeliveryLane,
    OutboundIntentDraft,
    OutboxRecord,
    OutboxState,
)
from session.store import SessionStore


class OutboxError(RuntimeError):
    pass


class OutboxNotFoundError(OutboxError):
    pass


class OutboxStateError(OutboxError):
    pass


class OutboxRepository:
    """CAS repository sharing the SessionStore connection and lock."""

    def __init__(self, store: SessionStore) -> None:
        self._store = store
        self._conn = store._conn
        self._lock = store._lock

    def enqueue(self, draft: OutboundIntentDraft) -> OutboxRecord:
        return self._store.insert_outbound(draft)

    def get(self, delivery_id: str) -> OutboxRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM reliability_outbox WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
        return row_to_outbox_record(row) if row is not None else None

    def claim_next(
        self,
        worker_id: str,
        *,
        now: datetime,
        lease_expires_at: datetime,
    ) -> OutboxRecord | None:
        _require_utc("now", now)
        _require_utc("lease_expires_at", lease_expires_at)
        if lease_expires_at <= now:
            raise ValueError("lease_expires_at must be later than now")
        _require_text("worker_id", worker_id)
        with self._lock:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    """
                    SELECT delivery_id, attempt_count
                    FROM reliability_outbox
                    WHERE status = 'pending'
                    ORDER BY created_at, delivery_id
                    LIMIT 1
                    """
                ).fetchone()
                if row is None:
                    return None
                delivery_id = str(row["delivery_id"])
                attempt = int(row["attempt_count"]) + 1
                cursor = self._conn.execute(
                    """
                    UPDATE reliability_outbox
                    SET status = 'sending', attempt_count = ?, lease_owner = ?,
                        lease_expires_at = ?, last_error_code = NULL, updated_at = ?
                    WHERE delivery_id = ? AND status = 'pending'
                    """,
                    (
                        attempt,
                        worker_id,
                        lease_expires_at.isoformat(),
                        now.isoformat(),
                        delivery_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise OutboxStateError("outbound claim CAS failed")
                self._conn.execute(
                    """
                    INSERT INTO reliability_delivery_attempts (
                        delivery_id, attempt, status, started_at
                    ) VALUES (?, ?, 'started', ?)
                    """,
                    (delivery_id, attempt, now.isoformat()),
                )
                claimed = self._conn.execute(
                    "SELECT * FROM reliability_outbox WHERE delivery_id = ?",
                    (delivery_id,),
                ).fetchone()
        if claimed is None:
            raise RuntimeError("claimed outbound disappeared")
        return row_to_outbox_record(claimed)

    def mark_sent(
        self,
        delivery_id: str,
        attempt: int,
        *,
        observed_at: datetime,
        channel_message_id: str | None = None,
    ) -> OutboxRecord:
        return self._finish_attempt(
            delivery_id,
            attempt,
            target="sent",
            observed_at=observed_at,
            channel_message_id=channel_message_id,
            error_code=None,
        )

    def mark_failed(
        self,
        delivery_id: str,
        attempt: int,
        *,
        observed_at: datetime,
        error_code: str,
    ) -> OutboxRecord:
        return self._finish_attempt(
            delivery_id,
            attempt,
            target="failed",
            observed_at=observed_at,
            channel_message_id=None,
            error_code=error_code,
        )

    def mark_unknown(
        self,
        delivery_id: str,
        attempt: int,
        *,
        observed_at: datetime,
        error_code: str,
    ) -> OutboxRecord:
        return self._finish_attempt(
            delivery_id,
            attempt,
            target="unknown",
            observed_at=observed_at,
            channel_message_id=None,
            error_code=error_code,
        )

    def _finish_attempt(
        self,
        delivery_id: str,
        attempt: int,
        *,
        target: Literal["sent", "failed", "unknown"],
        observed_at: datetime,
        channel_message_id: str | None,
        error_code: str | None,
    ) -> OutboxRecord:
        _require_text("delivery_id", delivery_id)
        _require_utc("observed_at", observed_at)
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        if error_code is not None:
            _require_text("error_code", error_code)
        with self._lock:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                cursor = self._conn.execute(
                    """
                    UPDATE reliability_outbox
                    SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                        channel_message_id = ?, last_error_code = ?, updated_at = ?,
                        sent_at = CASE WHEN ? = 'sent' THEN ? ELSE sent_at END
                    WHERE delivery_id = ? AND status = 'sending'
                      AND attempt_count = ?
                    """,
                    (
                        target,
                        channel_message_id,
                        error_code,
                        observed_at.isoformat(),
                        target,
                        observed_at.isoformat(),
                        delivery_id,
                        attempt,
                    ),
                )
                if cursor.rowcount != 1:
                    self._raise_state(delivery_id, f"sending attempt {attempt}")
                attempt_cursor = self._conn.execute(
                    """
                    UPDATE reliability_delivery_attempts
                    SET status = ?, finished_at = ?, channel_message_id = ?, error_code = ?
                    WHERE delivery_id = ? AND attempt = ? AND status = 'started'
                    """,
                    (
                        target,
                        observed_at.isoformat(),
                        channel_message_id,
                        error_code,
                        delivery_id,
                        attempt,
                    ),
                )
                if attempt_cursor.rowcount != 1:
                    raise OutboxStateError("delivery attempt completion CAS failed")
                row = self._conn.execute(
                    "SELECT * FROM reliability_outbox WHERE delivery_id = ?",
                    (delivery_id,),
                ).fetchone()
        if row is None:
            raise OutboxNotFoundError(f"delivery not found: {delivery_id}")
        return row_to_outbox_record(row)

    def reconcile_abandoned_sending(self, *, observed_at: datetime) -> int:
        """Single-instance startup proves all prior sending leases are abandoned."""

        _require_utc("observed_at", observed_at)
        now_text = observed_at.isoformat()
        with self._lock:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                rows = self._conn.execute(
                    """
                    SELECT delivery_id, attempt_count
                    FROM reliability_outbox
                    WHERE status = 'sending'
                    """
                ).fetchall()
                for row in rows:
                    delivery_id = str(row["delivery_id"])
                    attempt = int(row["attempt_count"])
                    self._conn.execute(
                        """
                        UPDATE reliability_outbox
                        SET status = 'unknown', lease_owner = NULL,
                            lease_expires_at = NULL,
                            last_error_code = 'process_restarted_during_send',
                            updated_at = ?
                        WHERE delivery_id = ? AND status = 'sending'
                        """,
                        (now_text, delivery_id),
                    )
                    self._conn.execute(
                        """
                        UPDATE reliability_delivery_attempts
                        SET status = 'unknown', finished_at = ?,
                            error_code = 'process_restarted_during_send'
                        WHERE delivery_id = ? AND attempt = ? AND status = 'started'
                        """,
                        (now_text, delivery_id, attempt),
                    )
        return len(rows)

    def confirm_unknown_sent(
        self,
        delivery_id: str,
        *,
        observed_at: datetime,
        reason: str,
        channel_message_id: str | None = None,
    ) -> OutboxRecord:
        return self._resolve_unknown(
            delivery_id,
            target="sent",
            observed_at=observed_at,
            reason=reason,
            channel_message_id=channel_message_id,
        )

    def confirm_unknown_absent(
        self,
        delivery_id: str,
        *,
        observed_at: datetime,
        reason: str,
    ) -> OutboxRecord:
        return self._resolve_unknown(
            delivery_id,
            target="failed",
            observed_at=observed_at,
            reason=reason,
            channel_message_id=None,
        )

    def _resolve_unknown(
        self,
        delivery_id: str,
        *,
        target: Literal["sent", "failed"],
        observed_at: datetime,
        reason: str,
        channel_message_id: str | None,
    ) -> OutboxRecord:
        _require_utc("observed_at", observed_at)
        _require_text("reason", reason)
        marker = f"manual_{target}:{reason}"
        with self._lock:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    """
                    SELECT attempt_count FROM reliability_outbox
                    WHERE delivery_id = ? AND status = 'unknown'
                    """,
                    (delivery_id,),
                ).fetchone()
                if row is None:
                    self._raise_state(delivery_id, "unknown")
                attempt = int(row["attempt_count"])
                self._conn.execute(
                    """
                    UPDATE reliability_outbox
                    SET status = ?, channel_message_id = COALESCE(?, channel_message_id),
                        last_error_code = ?, updated_at = ?,
                        sent_at = CASE WHEN ? = 'sent' THEN ? ELSE sent_at END
                    WHERE delivery_id = ? AND status = 'unknown'
                    """,
                    (
                        target,
                        channel_message_id,
                        marker,
                        observed_at.isoformat(),
                        target,
                        observed_at.isoformat(),
                        delivery_id,
                    ),
                )
                self._conn.execute(
                    """
                    UPDATE reliability_delivery_attempts
                    SET status = ?, channel_message_id = COALESCE(?, channel_message_id),
                        error_code = ?
                    WHERE delivery_id = ? AND attempt = ? AND status = 'unknown'
                    """,
                    (target, channel_message_id, marker, delivery_id, attempt),
                )
                resolved = self._conn.execute(
                    "SELECT * FROM reliability_outbox WHERE delivery_id = ?",
                    (delivery_id,),
                ).fetchone()
        if resolved is None:
            raise OutboxNotFoundError(f"delivery not found: {delivery_id}")
        return row_to_outbox_record(resolved)

    def requeue_failed(
        self,
        delivery_id: str,
        *,
        observed_at: datetime,
        reason: str,
    ) -> OutboxRecord:
        _require_utc("observed_at", observed_at)
        _require_text("reason", reason)
        with self._lock:
            with self._conn:
                cursor = self._conn.execute(
                    """
                    UPDATE reliability_outbox
                    SET status = 'pending', last_error_code = ?, updated_at = ?
                    WHERE delivery_id = ? AND status = 'failed'
                    """,
                    (
                        f"manual_requeue:{reason}",
                        observed_at.isoformat(),
                        delivery_id,
                    ),
                )
                if cursor.rowcount != 1:
                    self._raise_state(delivery_id, "failed")
                row = self._conn.execute(
                    "SELECT * FROM reliability_outbox WHERE delivery_id = ?",
                    (delivery_id,),
                ).fetchone()
        if row is None:
            raise OutboxNotFoundError(f"delivery not found: {delivery_id}")
        return row_to_outbox_record(row)

    def list_attempts(self, delivery_id: str) -> list[DeliveryAttemptRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM reliability_delivery_attempts
                WHERE delivery_id = ? ORDER BY attempt
                """,
                (delivery_id,),
            ).fetchall()
        return [row_to_attempt_record(row) for row in rows]

    def list_by_status(
        self,
        status: OutboxState,
        *,
        limit: int = 100,
    ) -> list[OutboxRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM reliability_outbox
                WHERE status = ? ORDER BY created_at, delivery_id LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        return [row_to_outbox_record(row) for row in rows]

    def _raise_state(self, delivery_id: str, expected: str) -> None:
        row = self._conn.execute(
            "SELECT status FROM reliability_outbox WHERE delivery_id = ?",
            (delivery_id,),
        ).fetchone()
        if row is None:
            raise OutboxNotFoundError(f"delivery not found: {delivery_id}")
        raise OutboxStateError(
            f"delivery {delivery_id!r} is {row['status']!r}, expected {expected}"
        )


def row_to_outbox_record(row: sqlite3.Row) -> OutboxRecord:
    media = json.loads(str(row["media_json"]))
    metadata = json.loads(str(row["metadata_json"]))
    if not isinstance(media, list) or not all(isinstance(item, str) for item in media):
        raise ValueError("outbox media_json must be a string array")
    if not isinstance(metadata, dict) or not all(
        isinstance(key, str) for key in metadata
    ):
        raise ValueError("outbox metadata_json must be an object")
    return OutboxRecord(
        delivery_id=str(row["delivery_id"]),
        idempotency_key=str(row["idempotency_key"]),
        turn_id=str(row["turn_id"]) if row["turn_id"] is not None else None,
        session_key=str(row["session_key"]),
        session_message_id=(
            str(row["session_message_id"])
            if row["session_message_id"] is not None
            else None
        ),
        channel=str(row["channel"]),
        chat_id=str(row["chat_id"]),
        content=str(row["content"]),
        thinking=str(row["thinking"]) if row["thinking"] is not None else None,
        media=tuple(cast(list[str], media)),
        metadata=cast(dict[str, object], metadata),
        lane=cast(DeliveryLane, str(row["lane"])),
        reason_code=str(row["reason_code"]),
        status=cast(OutboxState, str(row["status"])),
        attempt_count=int(row["attempt_count"]),
        lease_owner=str(row["lease_owner"]) if row["lease_owner"] is not None else None,
        lease_expires_at=_parse_optional_datetime(row["lease_expires_at"]),
        channel_message_id=(
            str(row["channel_message_id"])
            if row["channel_message_id"] is not None
            else None
        ),
        last_error_code=(
            str(row["last_error_code"])
            if row["last_error_code"] is not None
            else None
        ),
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
        sent_at=_parse_optional_datetime(row["sent_at"]),
    )


def row_to_attempt_record(row: sqlite3.Row) -> DeliveryAttemptRecord:
    return DeliveryAttemptRecord(
        delivery_id=str(row["delivery_id"]),
        attempt=int(row["attempt"]),
        status=cast(Any, str(row["status"])),
        started_at=_parse_datetime(row["started_at"]),
        finished_at=_parse_optional_datetime(row["finished_at"]),
        channel_message_id=(
            str(row["channel_message_id"])
            if row["channel_message_id"] is not None
            else None
        ),
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
    )


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty and trimmed")


def _require_utc(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    if value.utcoffset() != datetime.now(UTC).utcoffset():
        raise ValueError(f"{name} must use UTC")


def _parse_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    _require_utc("persisted datetime", parsed)
    return parsed


def _parse_optional_datetime(value: object) -> datetime | None:
    return _parse_datetime(value) if value is not None else None


__all__ = [
    "OutboxError",
    "OutboxNotFoundError",
    "OutboxRepository",
    "OutboxStateError",
]

