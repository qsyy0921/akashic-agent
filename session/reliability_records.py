from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


OutboxState = Literal[
    "pending",
    "sending",
    "sent",
    "failed",
    "unknown",
    "cancelled",
]
DeliveryLane = Literal["passive", "proactive", "system"]
ToolPolicyDecision = Literal["allow", "deny", "require_approval"]
ToolCallState = Literal[
    "prepared",
    "awaiting_approval",
    "executing",
    "succeeded",
    "failed",
    "unknown",
    "denied",
    "cancelled",
]
ApprovalState = Literal[
    "requested",
    "granted",
    "consumed",
    "denied",
    "expired",
    "revoked",
]


@dataclass(frozen=True, slots=True)
class OutboundIntentDraft:
    delivery_id: str
    idempotency_key: str
    turn_id: str | None
    session_key: str
    channel: str
    chat_id: str
    content: str
    thinking: str | None = None
    media: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict[str, object])
    lane: DeliveryLane = "passive"
    reason_code: str = "passive_reply"
    session_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    delivery_id: str
    idempotency_key: str
    turn_id: str | None
    session_key: str
    session_message_id: str | None
    channel: str
    chat_id: str
    content: str
    thinking: str | None
    media: tuple[str, ...]
    metadata: dict[str, object]
    lane: DeliveryLane
    reason_code: str
    status: OutboxState
    attempt_count: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    channel_message_id: str | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None


@dataclass(frozen=True, slots=True)
class DeliveryAttemptRecord:
    delivery_id: str
    attempt: int
    status: Literal["started", "sent", "failed", "unknown", "cancelled"]
    started_at: datetime
    finished_at: datetime | None
    channel_message_id: str | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    ledger_id: str
    call_id: str
    turn_id: str
    session_key: str
    source: str
    snapshot_id: str
    tool_name: str
    risk: str
    args_hash: str
    args_redacted: dict[str, object]
    policy_decision: ToolPolicyDecision
    state: ToolCallState
    result_digest: str | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ToolApprovalRecord:
    approval_id: str
    binding_hash: str
    ledger_id: str
    turn_id: str
    snapshot_id: str
    tool_name: str
    args_hash: str
    state: ApprovalState
    approver_type: str | None
    approver_id: str | None
    reason_code: str | None
    expires_at: datetime
    consumed_at: datetime | None
    created_at: datetime
    updated_at: datetime
