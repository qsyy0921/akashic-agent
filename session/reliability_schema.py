from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


class ReliabilitySchemaError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReliabilityMigration:
    version: int
    statements: tuple[str, ...]


RELIABILITY_MIGRATIONS: tuple[ReliabilityMigration, ...] = (
    ReliabilityMigration(
        version=1,
        statements=(
            """
            CREATE TABLE reliability_outbox (
                delivery_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                turn_id TEXT,
                session_key TEXT NOT NULL,
                session_message_id TEXT,
                channel TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                content TEXT NOT NULL,
                thinking TEXT,
                media_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                lane TEXT NOT NULL CHECK (lane IN ('passive','proactive','system')),
                reason_code TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('pending','sending','sent','failed','unknown','cancelled')
                ),
                attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                lease_owner TEXT,
                lease_expires_at TEXT,
                channel_message_id TEXT,
                last_error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                sent_at TEXT
            )
            """,
            """
            CREATE INDEX reliability_outbox_ready_idx
            ON reliability_outbox(status, created_at, delivery_id)
            """,
            """
            CREATE TABLE reliability_delivery_attempts (
                delivery_id TEXT NOT NULL REFERENCES reliability_outbox(delivery_id),
                attempt INTEGER NOT NULL CHECK (attempt > 0),
                status TEXT NOT NULL CHECK (
                    status IN ('started','sent','failed','unknown','cancelled')
                ),
                started_at TEXT NOT NULL,
                finished_at TEXT,
                channel_message_id TEXT,
                error_code TEXT,
                PRIMARY KEY (delivery_id, attempt)
            )
            """,
        ),
    ),
    ReliabilityMigration(
        version=2,
        statements=(
            """
            CREATE TABLE reliability_tool_calls (
                ledger_id TEXT PRIMARY KEY,
                call_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                session_key TEXT NOT NULL,
                source TEXT NOT NULL CHECK (source IN ('passive','proactive','subagent')),
                snapshot_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                risk TEXT NOT NULL,
                args_hash TEXT NOT NULL,
                args_redacted_json TEXT NOT NULL,
                policy_decision TEXT NOT NULL CHECK (
                    policy_decision IN ('allow','deny','require_approval')
                ),
                state TEXT NOT NULL CHECK (
                    state IN ('prepared','awaiting_approval','executing','succeeded',
                              'failed','unknown','denied','cancelled')
                ),
                result_digest TEXT,
                error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (turn_id, call_id)
            )
            """,
            """
            CREATE INDEX reliability_tool_calls_turn_idx
            ON reliability_tool_calls(turn_id, created_at, ledger_id)
            """,
            """
            CREATE TABLE reliability_tool_approvals (
                approval_id TEXT PRIMARY KEY,
                binding_hash TEXT NOT NULL UNIQUE,
                ledger_id TEXT NOT NULL REFERENCES reliability_tool_calls(ledger_id),
                turn_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                args_hash TEXT NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN ('requested','granted','consumed','denied','expired','revoked')
                ),
                approver_type TEXT,
                approver_id TEXT,
                reason_code TEXT,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX reliability_tool_approvals_state_idx
            ON reliability_tool_approvals(state, expires_at, approval_id)
            """,
            """
            CREATE TABLE reliability_audit_events (
                audit_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                actor_id TEXT,
                turn_id TEXT,
                subject_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                decision TEXT,
                reason_code TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX reliability_audit_subject_idx
            ON reliability_audit_events(subject_type, subject_id, created_at)
            """,
        ),
    ),
)


def apply_reliability_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reliability_schema_migrations (
            version INTEGER PRIMARY KEY CHECK (version > 0),
            applied_at TEXT NOT NULL
        )
        """
    )
    applied = {
        int(row[0])
        for row in conn.execute(
            "SELECT version FROM reliability_schema_migrations"
        ).fetchall()
    }
    supported = {migration.version for migration in RELIABILITY_MIGRATIONS}
    unknown = sorted(applied - supported)
    if unknown:
        raise ReliabilitySchemaError(
            f"reliability schema newer than runtime: {unknown}"
        )
    expected = list(range(1, len(RELIABILITY_MIGRATIONS) + 1))
    actual = [migration.version for migration in RELIABILITY_MIGRATIONS]
    if actual != expected:
        raise ReliabilitySchemaError(
            f"reliability migrations must be contiguous: {actual}"
        )
    for migration in RELIABILITY_MIGRATIONS:
        if migration.version in applied:
            continue
        savepoint = f"reliability_migration_{migration.version}"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            for statement in migration.statements:
                conn.execute(statement)
            conn.execute(
                """
                INSERT INTO reliability_schema_migrations (version, applied_at)
                VALUES (?, ?)
                """,
                (migration.version, datetime.now(UTC).isoformat()),
            )
        except BaseException:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
