from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, cast

from agent.tool_hooks.types import ToolExecutionRequest
from session.reliability_records import (
    ApprovalState,
    ToolApprovalRecord,
    ToolCallState,
    ToolPolicyDecision,
)
from session.tool_ledger_repository import (
    ToolLedgerRepository,
    approval_binding_hash,
    canonical_arguments_hash,
)

ToolRiskResolver = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class ToolPolicyVerdict:
    decision: ToolPolicyDecision
    risk: str
    reason_code: str
    message: str


@dataclass(frozen=True, slots=True)
class ToolAuthorization:
    allowed: bool
    decision: ToolPolicyDecision
    risk: str
    reason_code: str
    message: str
    ledger_id: str | None = None
    approval_id: str | None = None


class CoreToolPolicy:
    """Core-owned policy for tool execution.

    Discovery and plugin hooks may narrow or rewrite a call, but only this
    policy can authorize the final arguments passed to the invoker.
    """

    _ALLOWED_RISKS = frozenset(
        {"read-only", "write", "read-write", "external-side-effect"}
    )
    _ALWAYS_APPROVE = frozenset(
        {
            "agent_restart",
            "workspace_mcp_apply",
            "workspace_mcp_remove",
        }
    )

    def evaluate(
        self,
        request: ToolExecutionRequest,
        *,
        risk: str,
        upstream_denial: str = "",
    ) -> ToolPolicyVerdict:
        if upstream_denial:
            return ToolPolicyVerdict(
                decision="deny",
                risk=risk,
                reason_code="plugin_pre_hook_denied",
                message=upstream_denial,
            )
        if not all(
            (
                request.call_id.strip(),
                request.tool_name.strip(),
                request.session_key.strip(),
                request.turn_id.strip(),
            )
        ):
            return ToolPolicyVerdict(
                decision="deny",
                risk=risk,
                reason_code="invalid_execution_context",
                message="工具调用缺少受治理的回合或会话上下文，已拒绝执行。",
            )
        if risk == "destructive" or request.tool_name in self._ALWAYS_APPROVE:
            return ToolPolicyVerdict(
                decision="require_approval",
                risk=risk,
                reason_code="operator_approval_required",
                message="该工具会修改运行时或外部能力，需要操作员审批。",
            )
        if request.tool_name == "message_push" and request.source != "proactive":
            return ToolPolicyVerdict(
                decision="require_approval",
                risk=risk,
                reason_code="message_push_approval_required",
                message="被动或子任务主动发消息需要操作员审批。",
            )
        if risk not in self._ALLOWED_RISKS:
            return ToolPolicyVerdict(
                decision="deny",
                risk=risk,
                reason_code="unsupported_tool_risk",
                message=f"工具风险等级 {risk!r} 未被核心策略识别，已拒绝执行。",
            )
        if request.source == "proactive" and request.tool_name == "message_push":
            reason_code = "proactive_stage_authorized"
        elif risk == "external-side-effect":
            reason_code = "bounded_external_effect_allowed"
        else:
            reason_code = "risk_class_allowed"
        return ToolPolicyVerdict(
            decision="allow",
            risk=risk,
            reason_code=reason_code,
            message="",
        )


class ToolGovernor:
    def __init__(
        self,
        repository: ToolLedgerRepository,
        *,
        risk_resolver: ToolRiskResolver,
        policy: CoreToolPolicy | None = None,
        approval_ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        if approval_ttl <= timedelta(0):
            raise ValueError("approval_ttl must be positive")
        self._repository = repository
        self._risk_resolver = risk_resolver
        self._policy = policy or CoreToolPolicy()
        self._approval_ttl = approval_ttl

    def authorize(
        self,
        request: ToolExecutionRequest,
        final_arguments: dict[str, object],
        *,
        upstream_denial: str = "",
    ) -> ToolAuthorization:
        risk = request.risk.strip() or self._resolve_risk(request.tool_name)
        verdict = self._policy.evaluate(
            request,
            risk=risk,
            upstream_denial=upstream_denial,
        )
        if request.is_preflight:
            return ToolAuthorization(
                allowed=verdict.decision == "allow",
                decision=verdict.decision,
                risk=verdict.risk,
                reason_code=verdict.reason_code,
                message=verdict.message,
            )
        if verdict.reason_code == "invalid_execution_context":
            return ToolAuthorization(
                allowed=False,
                decision="deny",
                risk=verdict.risk,
                reason_code=verdict.reason_code,
                message=verdict.message,
            )

        now = datetime.now(UTC)
        snapshot_id = self._resolve_snapshot_id(request)
        initial_state: ToolCallState
        if verdict.decision == "allow":
            initial_state = "prepared"
        elif verdict.decision == "deny":
            initial_state = "denied"
        else:
            initial_state = "awaiting_approval"
        record = self._repository.prepare_call(
            call_id=request.call_id,
            turn_id=request.turn_id,
            session_key=request.session_key,
            source=request.source,
            snapshot_id=snapshot_id,
            tool_name=request.tool_name,
            risk=verdict.risk,
            arguments=final_arguments,
            decision=verdict.decision,
            state=initial_state,
            reason_code=verdict.reason_code,
            now=now,
        )
        if verdict.decision == "deny":
            return ToolAuthorization(
                allowed=False,
                decision=verdict.decision,
                risk=verdict.risk,
                reason_code=verdict.reason_code,
                message=verdict.message,
                ledger_id=record.ledger_id,
            )
        if verdict.decision == "allow":
            if record.state != "prepared":
                return self._replay_blocked(verdict, record.ledger_id)
            executing = self._repository.mark_executing(record.ledger_id, now=now)
            return ToolAuthorization(
                allowed=True,
                decision=verdict.decision,
                risk=verdict.risk,
                reason_code=verdict.reason_code,
                message="",
                ledger_id=executing.ledger_id,
            )
        return self._authorize_approved_call(
            request=request,
            arguments=final_arguments,
            verdict=verdict,
            ledger_id=record.ledger_id,
            record_state=record.state,
            snapshot_id=snapshot_id,
            now=now,
        )

    def complete(
        self,
        authorization: ToolAuthorization,
        *,
        result: object | None = None,
        error: BaseException | None = None,
    ) -> None:
        if not authorization.allowed or authorization.ledger_id is None:
            return
        if error is None:
            state = "succeeded"
            error_code = None
        elif authorization.risk in {"external-side-effect", "destructive"}:
            state = "unknown"
            error_code = type(error).__name__
        else:
            state = "failed"
            error_code = type(error).__name__
        _ = self._repository.complete_call(
            authorization.ledger_id,
            state=state,
            result=result,
            error_code=error_code,
            now=datetime.now(UTC),
        )

    def decide_approval(
        self,
        approval_id: str,
        *,
        grant: bool,
        actor_id: str,
        reason_code: str,
    ) -> ToolApprovalRecord:
        return self._repository.decide_approval(
            approval_id,
            grant=grant,
            actor_id=actor_id,
            reason_code=reason_code,
            now=datetime.now(UTC),
        )

    def list_approvals(
        self,
        *,
        state: str | None = None,
        limit: int = 100,
    ) -> list[ToolApprovalRecord]:
        if state is not None and state not in {
            "requested",
            "granted",
            "consumed",
            "denied",
            "expired",
            "revoked",
        }:
            raise ValueError(f"invalid approval state: {state!r}")
        return self._repository.list_approvals(
            state=cast(ApprovalState | None, state),
            limit=limit,
        )

    def _authorize_approved_call(
        self,
        *,
        request: ToolExecutionRequest,
        arguments: dict[str, object],
        verdict: ToolPolicyVerdict,
        ledger_id: str,
        record_state: str,
        snapshot_id: str,
        now: datetime,
    ) -> ToolAuthorization:
        if record_state != "awaiting_approval":
            return self._replay_blocked(verdict, ledger_id)
        args_hash = canonical_arguments_hash(arguments)
        binding_hash = approval_binding_hash(
            turn_id=request.turn_id,
            snapshot_id=snapshot_id,
            tool_name=request.tool_name,
            args_hash=args_hash,
        )
        approval = self._repository.get_approval_for_binding(binding_hash, now=now)
        if approval is None:
            record = self._repository.get_call(ledger_id)
            if record is None:
                raise RuntimeError(f"tool ledger disappeared: {ledger_id}")
            approval = self._repository.request_approval(
                record,
                binding_hash=binding_hash,
                expires_at=now + self._approval_ttl,
                now=now,
            )
        if approval.ledger_id != ledger_id:
            return ToolAuthorization(
                allowed=False,
                decision="deny",
                risk=verdict.risk,
                reason_code="approval_binding_reused",
                message="相同操作已绑定到另一个工具调用，已阻止重复执行。",
                ledger_id=ledger_id,
                approval_id=approval.approval_id,
            )
        if approval.state == "granted":
            _ = self._repository.consume_approval(approval.approval_id, now=now)
            _ = self._repository.mark_executing(ledger_id, now=now)
            return ToolAuthorization(
                allowed=True,
                decision=verdict.decision,
                risk=verdict.risk,
                reason_code="operator_approval_consumed",
                message="",
                ledger_id=ledger_id,
                approval_id=approval.approval_id,
            )
        if approval.state == "requested":
            return ToolAuthorization(
                allowed=False,
                decision=verdict.decision,
                risk=verdict.risk,
                reason_code=verdict.reason_code,
                message=(
                    f"{verdict.message} approval_id={approval.approval_id}。"
                    "审批后必须在同一回合、同一运行快照下使用完全相同的参数重试。"
                ),
                ledger_id=ledger_id,
                approval_id=approval.approval_id,
            )
        return ToolAuthorization(
            allowed=False,
            decision="deny",
            risk=verdict.risk,
            reason_code=f"approval_{approval.state}",
            message=f"工具审批状态为 {approval.state}，已拒绝执行。",
            ledger_id=ledger_id,
            approval_id=approval.approval_id,
        )

    def _resolve_risk(self, tool_name: str) -> str:
        risk = self._risk_resolver(tool_name)
        return str(risk or "unclassified").strip() or "unclassified"

    @staticmethod
    def _resolve_snapshot_id(request: ToolExecutionRequest) -> str:
        if request.snapshot_id.strip():
            return request.snapshot_id.strip()
        from agent.plugins.snapshot import get_current_runtime_snapshot

        snapshot = get_current_runtime_snapshot()
        return snapshot.snapshot_id if snapshot is not None else "runtime-static"

    @staticmethod
    def _replay_blocked(
        verdict: ToolPolicyVerdict,
        ledger_id: str,
    ) -> ToolAuthorization:
        return ToolAuthorization(
            allowed=False,
            decision="deny",
            risk=verdict.risk,
            reason_code="tool_call_replay_blocked",
            message="该工具调用已经进入终态或正在执行，已阻止重复执行。",
            ledger_id=ledger_id,
        )


__all__ = [
    "CoreToolPolicy",
    "ToolAuthorization",
    "ToolGovernor",
    "ToolPolicyVerdict",
]
