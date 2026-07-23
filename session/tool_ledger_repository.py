from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Iterable, Literal, cast
from uuid import uuid4

from session.reliability_records import (
    ApprovalState,
    ToolApprovalRecord,
    ToolCallRecord,
    ToolCallState,
    ToolPolicyDecision,
)
from session.store import SessionStore

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|token|secret|password|credential|cookie|authorization)",
    re.IGNORECASE,
)
_REDACTED_SUMMARY_MAX_BYTES = 8_192


class ToolLedgerError(RuntimeError):
    pass


class ToolLedgerStateError(ToolLedgerError):
    pass


class ToolLedgerRepository:
    def __init__(self, store: SessionStore) -> None:
        self._conn = store._conn
        self._lock = store._lock

    def prepare_call(
        self,
        *,
        call_id: str,
        turn_id: str,
        session_key: str,
        source: str,
        snapshot_id: str,
        tool_name: str,
        risk: str,
        arguments: dict[str, Any],
        decision: ToolPolicyDecision,
        state: ToolCallState,
        reason_code: str,
        now: datetime,
    ) -> ToolCallRecord:
        for name, value in (
            ("call_id", call_id),
            ("turn_id", turn_id),
            ("session_key", session_key),
            ("source", source),
            ("snapshot_id", snapshot_id),
            ("tool_name", tool_name),
            ("risk", risk),
            ("reason_code", reason_code),
        ):
            _require_text(name, value)
        _require_utc(now)
        args_hash = canonical_arguments_hash(arguments)
        redacted = redact_arguments(arguments)
        with self._lock:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                existing = self._conn.execute(
                    """
                    SELECT * FROM reliability_tool_calls
                    WHERE turn_id = ? AND call_id = ?
                    """,
                    (turn_id, call_id),
                ).fetchone()
                if existing is not None:
                    record = row_to_tool_call(existing)
                    expected = (
                        session_key,
                        source,
                        snapshot_id,
                        tool_name,
                        risk,
                        args_hash,
                        decision,
                    )
                    actual = (
                        record.session_key,
                        record.source,
                        record.snapshot_id,
                        record.tool_name,
                        record.risk,
                        record.args_hash,
                        record.policy_decision,
                    )
                    if actual != expected:
                        raise ToolLedgerStateError(
                            "tool call id was reused with a different binding"
                        )
                    return record
                ledger_id = uuid4().hex
                now_text = now.isoformat()
                self._conn.execute(
                    """
                    INSERT INTO reliability_tool_calls (
                        ledger_id, call_id, turn_id, session_key, source,
                        snapshot_id, tool_name, risk, args_hash,
                        args_redacted_json, policy_decision, state,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ledger_id,
                        call_id,
                        turn_id,
                        session_key,
                        source,
                        snapshot_id,
                        tool_name,
                        risk,
                        args_hash,
                        json.dumps(redacted, ensure_ascii=False, sort_keys=True),
                        decision,
                        state,
                        now_text,
                        now_text,
                    ),
                )
                self._audit_locked(
                    event_type="tool_policy_decision",
                    actor_type="runtime",
                    actor_id=source,
                    turn_id=turn_id,
                    subject_type="tool_call",
                    subject_id=ledger_id,
                    decision=decision,
                    reason_code=reason_code,
                    payload={
                        "tool_name": tool_name,
                        "risk": risk,
                        "snapshot_id": snapshot_id,
                        "args_hash": args_hash,
                    },
                    now=now,
                )
                row = self._conn.execute(
                    "SELECT * FROM reliability_tool_calls WHERE ledger_id = ?",
                    (ledger_id,),
                ).fetchone()
        if row is None:
            raise RuntimeError("tool call insert did not return a record")
        return row_to_tool_call(row)

    def request_approval(
        self,
        record: ToolCallRecord,
        *,
        binding_hash: str,
        expires_at: datetime,
        now: datetime,
    ) -> ToolApprovalRecord:
        _require_text("binding_hash", binding_hash)
        _require_utc(now)
        _require_utc(expires_at)
        if expires_at <= now:
            raise ValueError("approval expiry must be in the future")
        with self._lock:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    """
                    SELECT * FROM reliability_tool_approvals
                    WHERE binding_hash = ?
                    """,
                    (binding_hash,),
                ).fetchone()
                if row is None:
                    approval_id = uuid4().hex
                    now_text = now.isoformat()
                    self._conn.execute(
                        """
                        INSERT INTO reliability_tool_approvals (
                            approval_id, binding_hash, ledger_id, turn_id,
                            snapshot_id, tool_name, args_hash, state,
                            expires_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'requested', ?, ?, ?)
                        """,
                        (
                            approval_id,
                            binding_hash,
                            record.ledger_id,
                            record.turn_id,
                            record.snapshot_id,
                            record.tool_name,
                            record.args_hash,
                            expires_at.isoformat(),
                            now_text,
                            now_text,
                        ),
                    )
                    self._audit_locked(
                        event_type="tool_approval_requested",
                        actor_type="runtime",
                        actor_id=record.source,
                        turn_id=record.turn_id,
                        subject_type="tool_approval",
                        subject_id=approval_id,
                        decision="require_approval",
                        reason_code="approval_required",
                        payload={"ledger_id": record.ledger_id},
                        now=now,
                    )
                    row = self._conn.execute(
                        """
                        SELECT * FROM reliability_tool_approvals
                        WHERE approval_id = ?
                        """,
                        (approval_id,),
                    ).fetchone()
        if row is None:
            raise RuntimeError("approval insert did not return a record")
        approval = row_to_approval(row)
        expected = (
            record.turn_id,
            record.snapshot_id,
            record.tool_name,
            record.args_hash,
        )
        actual = (
            approval.turn_id,
            approval.snapshot_id,
            approval.tool_name,
            approval.args_hash,
        )
        if actual != expected:
            raise ToolLedgerStateError("approval binding hash collision")
        return approval

    def get_approval_for_binding(
        self,
        binding_hash: str,
        *,
        now: datetime,
    ) -> ToolApprovalRecord | None:
        _require_utc(now)
        with self._lock:
            with self._conn:
                row = self._conn.execute(
                    """
                    SELECT * FROM reliability_tool_approvals
                    WHERE binding_hash = ?
                    """,
                    (binding_hash,),
                ).fetchone()
                if row is None:
                    return None
                approval = row_to_approval(row)
                if (
                    approval.state in {"requested", "granted"}
                    and approval.expires_at <= now
                ):
                    self._conn.execute(
                        """
                        UPDATE reliability_tool_approvals
                        SET state = 'expired', reason_code = 'approval_expired',
                            updated_at = ?
                        WHERE approval_id = ? AND state IN ('requested','granted')
                        """,
                        (now.isoformat(), approval.approval_id),
                    )
                    row = self._conn.execute(
                        """
                        SELECT * FROM reliability_tool_approvals
                        WHERE approval_id = ?
                        """,
                        (approval.approval_id,),
                    ).fetchone()
                    self._audit_locked(
                        event_type="tool_approval_expired",
                        actor_type="runtime",
                        actor_id=None,
                        turn_id=approval.turn_id,
                        subject_type="tool_approval",
                        subject_id=approval.approval_id,
                        decision="expired",
                        reason_code="approval_expired",
                        payload={},
                        now=now,
                    )
        return row_to_approval(row) if row is not None else None

    def decide_approval(
        self,
        approval_id: str,
        *,
        grant: bool,
        actor_id: str,
        reason_code: str,
        now: datetime,
    ) -> ToolApprovalRecord:
        _require_utc(now)
        for name, value in (
            ("approval_id", approval_id),
            ("actor_id", actor_id),
            ("reason_code", reason_code),
        ):
            _require_text(name, value)
        target: ApprovalState = "granted" if grant else "denied"
        with self._lock:
            with self._conn:
                cursor = self._conn.execute(
                    """
                    UPDATE reliability_tool_approvals
                    SET state = ?, approver_type = 'operator', approver_id = ?,
                        reason_code = ?, updated_at = ?
                    WHERE approval_id = ? AND state = 'requested'
                      AND expires_at > ?
                    """,
                    (
                        target,
                        actor_id,
                        reason_code,
                        now.isoformat(),
                        approval_id,
                        now.isoformat(),
                    ),
                )
                if cursor.rowcount != 1:
                    self._raise_approval_state(approval_id, "unexpired requested")
                row = self._conn.execute(
                    """
                    SELECT * FROM reliability_tool_approvals
                    WHERE approval_id = ?
                    """,
                    (approval_id,),
                ).fetchone()
                self._audit_locked(
                    event_type="tool_approval_decided",
                    actor_type="operator",
                    actor_id=actor_id,
                    turn_id=str(row["turn_id"]),
                    subject_type="tool_approval",
                    subject_id=approval_id,
                    decision=target,
                    reason_code=reason_code,
                    payload={},
                    now=now,
                )
        return row_to_approval(row)

    def consume_approval(
        self,
        approval_id: str,
        *,
        now: datetime,
    ) -> ToolApprovalRecord:
        _require_utc(now)
        with self._lock:
            with self._conn:
                cursor = self._conn.execute(
                    """
                    UPDATE reliability_tool_approvals
                    SET state = 'consumed', consumed_at = ?, updated_at = ?
                    WHERE approval_id = ? AND state = 'granted' AND expires_at > ?
                    """,
                    (now.isoformat(), now.isoformat(), approval_id, now.isoformat()),
                )
                if cursor.rowcount != 1:
                    self._raise_approval_state(approval_id, "unexpired granted")
                row = self._conn.execute(
                    """
                    SELECT * FROM reliability_tool_approvals
                    WHERE approval_id = ?
                    """,
                    (approval_id,),
                ).fetchone()
                self._audit_locked(
                    event_type="tool_approval_consumed",
                    actor_type="runtime",
                    actor_id=None,
                    turn_id=str(row["turn_id"]),
                    subject_type="tool_approval",
                    subject_id=approval_id,
                    decision="consumed",
                    reason_code="approved_tool_execution",
                    payload={"ledger_id": str(row["ledger_id"])},
                    now=now,
                )
        return row_to_approval(row)

    def mark_executing(self, ledger_id: str, *, now: datetime) -> ToolCallRecord:
        return self._transition_call(
            ledger_id,
            expected={"prepared", "awaiting_approval"},
            target="executing",
            now=now,
        )

    def complete_call(
        self,
        ledger_id: str,
        *,
        state: Literal["succeeded", "failed", "unknown"],
        result: object | None,
        error_code: str | None,
        now: datetime,
    ) -> ToolCallRecord:
        _require_utc(now)
        result_digest = _result_digest(result)
        if error_code is not None:
            _require_text("error_code", error_code)
        with self._lock:
            with self._conn:
                cursor = self._conn.execute(
                    """
                    UPDATE reliability_tool_calls
                    SET state = ?, result_digest = ?, error_code = ?, updated_at = ?
                    WHERE ledger_id = ? AND state = 'executing'
                    """,
                    (state, result_digest, error_code, now.isoformat(), ledger_id),
                )
                if cursor.rowcount != 1:
                    self._raise_call_state(ledger_id, "executing")
                row = self._conn.execute(
                    "SELECT * FROM reliability_tool_calls WHERE ledger_id = ?",
                    (ledger_id,),
                ).fetchone()
                self._audit_locked(
                    event_type="tool_call_completed",
                    actor_type="runtime",
                    actor_id=str(row["source"]),
                    turn_id=str(row["turn_id"]),
                    subject_type="tool_call",
                    subject_id=ledger_id,
                    decision=state,
                    reason_code=error_code or "tool_result_recorded",
                    payload={
                        "result_digest": result_digest or "",
                        "tool_name": str(row["tool_name"]),
                    },
                    now=now,
                )
        return row_to_tool_call(row)

    def get_call(self, ledger_id: str) -> ToolCallRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM reliability_tool_calls WHERE ledger_id = ?",
                (ledger_id,),
            ).fetchone()
        return row_to_tool_call(row) if row is not None else None

    def list_calls(self, *, limit: int = 100) -> list[ToolCallRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM reliability_tool_calls
                ORDER BY created_at, ledger_id LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [row_to_tool_call(row) for row in rows]

    def list_approvals(
        self,
        *,
        state: ApprovalState | None = None,
        limit: int = 100,
    ) -> list[ToolApprovalRecord]:
        query = "SELECT * FROM reliability_tool_approvals"
        params: list[object] = []
        if state is not None:
            query += " WHERE state = ?"
            params.append(state)
        query += " ORDER BY created_at, approval_id LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [row_to_approval(row) for row in rows]

    def list_audit_events(self, *, limit: int = 100) -> list[dict[str, object]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM reliability_audit_events
                ORDER BY created_at, audit_id LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _transition_call(
        self,
        ledger_id: str,
        *,
        expected: Iterable[ToolCallState],
        target: ToolCallState,
        now: datetime,
    ) -> ToolCallRecord:
        _require_utc(now)
        expected_values = tuple(expected)
        placeholders = ",".join("?" for _ in expected_values)
        with self._lock:
            with self._conn:
                cursor = self._conn.execute(
                    f"""
                    UPDATE reliability_tool_calls SET state = ?, updated_at = ?
                    WHERE ledger_id = ? AND state IN ({placeholders})
                    """,
                    (target, now.isoformat(), ledger_id, *expected_values),
                )
                if cursor.rowcount != 1:
                    self._raise_call_state(ledger_id, "/".join(expected_values))
                row = self._conn.execute(
                    "SELECT * FROM reliability_tool_calls WHERE ledger_id = ?",
                    (ledger_id,),
                ).fetchone()
                self._audit_locked(
                    event_type="tool_call_executing",
                    actor_type="runtime",
                    actor_id=str(row["source"]),
                    turn_id=str(row["turn_id"]),
                    subject_type="tool_call",
                    subject_id=ledger_id,
                    decision="allow",
                    reason_code="invocation_started",
                    payload={"tool_name": str(row["tool_name"])},
                    now=now,
                )
        return row_to_tool_call(row)

    def _raise_call_state(self, ledger_id: str, expected: str) -> None:
        row = self._conn.execute(
            "SELECT state FROM reliability_tool_calls WHERE ledger_id = ?",
            (ledger_id,),
        ).fetchone()
        if row is None:
            raise ToolLedgerStateError(f"tool call not found: {ledger_id}")
        raise ToolLedgerStateError(
            f"tool call {ledger_id!r} is {row['state']!r}, expected {expected}"
        )

    def _raise_approval_state(self, approval_id: str, expected: str) -> None:
        row = self._conn.execute(
            "SELECT state FROM reliability_tool_approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise ToolLedgerStateError(f"approval not found: {approval_id}")
        raise ToolLedgerStateError(
            f"approval {approval_id!r} is {row['state']!r}, expected {expected}"
        )

    def _audit_locked(
        self,
        *,
        event_type: str,
        actor_type: str,
        actor_id: str | None,
        turn_id: str | None,
        subject_type: str,
        subject_id: str,
        decision: str | None,
        reason_code: str,
        payload: dict[str, object],
        now: datetime,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO reliability_audit_events (
                audit_id, event_type, actor_type, actor_id, turn_id,
                subject_type, subject_id, decision, reason_code,
                payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                event_type,
                actor_type,
                actor_id,
                turn_id,
                subject_type,
                subject_id,
                decision,
                reason_code,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                now.isoformat(),
            ),
        )


def canonical_arguments_hash(arguments: dict[str, Any]) -> str:
    payload = json.dumps(
        _normalize_json(arguments),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def approval_binding_hash(
    *,
    turn_id: str,
    snapshot_id: str,
    tool_name: str,
    args_hash: str,
) -> str:
    payload = "\0".join((turn_id, snapshot_id, tool_name, args_hash)).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def redact_arguments(arguments: dict[str, Any]) -> dict[str, object]:
    redacted = cast(dict[str, object], _redact(arguments))
    encoded = json.dumps(
        redacted,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) <= _REDACTED_SUMMARY_MAX_BYTES:
        return redacted
    return {
        "_truncated": True,
        "_top_level_keys": sorted(str(key) for key in arguments)[:64],
    }


def _normalize_json(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {
            str(key): _normalize_json(item)
            for key, item in cast(dict[object, object], value).items()
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]
    raise TypeError(f"tool arguments must be JSON-compatible: {type(value).__name__}")


def _redact(value: object, *, key: str = "") -> object:
    if key and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): _redact(item, key=str(item_key))
            for item_key, item in cast(dict[object, object], value).items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str) and len(value) > 512:
        return value[:512] + f"...[{len(value) - 512} chars omitted]"
    return _normalize_json(value)


def row_to_tool_call(row: Any) -> ToolCallRecord:
    redacted = json.loads(str(row["args_redacted_json"]))
    return ToolCallRecord(
        ledger_id=str(row["ledger_id"]),
        call_id=str(row["call_id"]),
        turn_id=str(row["turn_id"]),
        session_key=str(row["session_key"]),
        source=str(row["source"]),
        snapshot_id=str(row["snapshot_id"]),
        tool_name=str(row["tool_name"]),
        risk=str(row["risk"]),
        args_hash=str(row["args_hash"]),
        args_redacted=cast(dict[str, object], redacted),
        policy_decision=cast(ToolPolicyDecision, str(row["policy_decision"])),
        state=cast(ToolCallState, str(row["state"])),
        result_digest=(
            str(row["result_digest"]) if row["result_digest"] is not None else None
        ),
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )


def row_to_approval(row: Any) -> ToolApprovalRecord:
    return ToolApprovalRecord(
        approval_id=str(row["approval_id"]),
        binding_hash=str(row["binding_hash"]),
        ledger_id=str(row["ledger_id"]),
        turn_id=str(row["turn_id"]),
        snapshot_id=str(row["snapshot_id"]),
        tool_name=str(row["tool_name"]),
        args_hash=str(row["args_hash"]),
        state=cast(ApprovalState, str(row["state"])),
        approver_type=(
            str(row["approver_type"]) if row["approver_type"] is not None else None
        ),
        approver_id=str(row["approver_id"]) if row["approver_id"] is not None else None,
        reason_code=str(row["reason_code"]) if row["reason_code"] is not None else None,
        expires_at=_parse_datetime(row["expires_at"]),
        consumed_at=(
            _parse_datetime(row["consumed_at"])
            if row["consumed_at"] is not None
            else None
        ),
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty and trimmed")


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != datetime.now(UTC).utcoffset():
        raise ValueError("datetime must use UTC")


def _parse_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    _require_utc(parsed)
    return parsed


def _result_digest(result: object | None) -> str | None:
    if result is None:
        return None
    try:
        payload = json.dumps(
            _normalize_json(result),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except TypeError:
        payload = str(result)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "ToolLedgerError",
    "ToolLedgerRepository",
    "ToolLedgerStateError",
    "approval_binding_hash",
    "canonical_arguments_hash",
    "redact_arguments",
]
