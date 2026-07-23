from __future__ import annotations

import json
from typing import Any

import pytest

from agent.tool_governance import ToolGovernor
from agent.tool_hooks import (
    HookContext,
    HookOutcome,
    ToolExecutionRequest,
    ToolExecutor,
)
from agent.tool_hooks.base import ToolHook
from session.manager import SessionManager
from session.tool_ledger_repository import (
    ToolLedgerRepository,
    canonical_arguments_hash,
)


def _runtime(tmp_path, *, risk: str = "read-only"):
    manager = SessionManager(tmp_path)
    repository = ToolLedgerRepository(manager.control_store)
    governor = ToolGovernor(
        repository,
        risk_resolver=lambda _name: risk,
    )
    return manager, repository, governor


def _request(
    *,
    call_id: str = "call-1",
    tool_name: str = "read_file",
    arguments: dict[str, Any] | None = None,
    source: str = "passive",
    risk: str = "",
    snapshot_id: str = "snapshot-a",
) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        call_id=call_id,
        tool_name=tool_name,
        arguments=arguments or {"path": "README.md"},
        source=source,  # type: ignore[arg-type]
        session_key="telegram:42",
        turn_id="turn-1",
        snapshot_id=snapshot_id,
        risk=risk,
    )


def test_reliability_schema_v2_is_applied_append_only(tmp_path):
    manager = SessionManager(tmp_path)
    conn = manager.control_store._conn

    versions = [
        int(row[0])
        for row in conn.execute(
            "SELECT version FROM reliability_schema_migrations ORDER BY version"
        ).fetchall()
    ]
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }

    assert versions == [1, 2]
    assert {
        "reliability_tool_calls",
        "reliability_tool_approvals",
        "reliability_audit_events",
    } <= tables
    manager.close()


@pytest.mark.asyncio
async def test_allowed_call_executes_once_and_records_success(tmp_path):
    manager, repository, governor = _runtime(tmp_path)
    executor = ToolExecutor(governor=governor)
    invocations: list[dict[str, Any]] = []

    async def invoke(_name: str, arguments: dict[str, Any]) -> str:
        invocations.append(arguments)
        return "ok"

    result = await executor.execute(_request(), invoke)
    replay = await executor.execute(_request(), invoke)

    assert result.status == "success"
    assert replay.status == "denied"
    assert len(invocations) == 1
    calls = repository.list_calls()
    assert len(calls) == 1
    assert calls[0].state == "succeeded"
    assert calls[0].result_digest is not None
    assert {event["event_type"] for event in repository.list_audit_events()} >= {
        "tool_policy_decision",
        "tool_call_executing",
        "tool_call_completed",
    }
    manager.close()


@pytest.mark.asyncio
async def test_unclassified_risk_denies_before_invocation(tmp_path):
    manager, repository, governor = _runtime(tmp_path, risk="unclassified")
    executor = ToolExecutor(governor=governor)
    invoked = False

    async def invoke(_name: str, _arguments: dict[str, Any]) -> str:
        nonlocal invoked
        invoked = True
        return "unexpected"

    result = await executor.execute(_request(tool_name="route_visible_tool"), invoke)

    assert result.status == "denied"
    assert invoked is False
    assert repository.list_calls()[0].state == "denied"
    manager.close()


@pytest.mark.asyncio
async def test_operator_approval_is_exact_and_single_use(tmp_path):
    manager, repository, governor = _runtime(
        tmp_path,
        risk="external-side-effect",
    )
    executor = ToolExecutor(governor=governor)
    request = _request(tool_name="agent_restart")
    invocations = 0

    async def invoke(_name: str, _arguments: dict[str, Any]) -> str:
        nonlocal invocations
        invocations += 1
        return "restarted"

    pending = await executor.execute(request, invoke)
    approval = repository.list_approvals(state="requested")[0]
    _ = governor.decide_approval(
        approval.approval_id,
        grant=True,
        actor_id="local-operator",
        reason_code="approved_for_test",
    )
    executed = await executor.execute(request, invoke)
    replay = await executor.execute(request, invoke)

    assert pending.status == "denied"
    assert approval.approval_id in str(pending.output)
    assert executed.status == "success"
    assert replay.status == "denied"
    assert invocations == 1
    assert repository.list_approvals()[0].state == "consumed"
    assert repository.list_calls()[0].state == "succeeded"
    manager.close()


@pytest.mark.asyncio
async def test_changed_arguments_cannot_consume_approval(tmp_path):
    manager, repository, governor = _runtime(
        tmp_path,
        risk="external-side-effect",
    )
    executor = ToolExecutor(governor=governor)
    original = _request(
        tool_name="workspace_mcp_apply",
        arguments={"server": "paper-search"},
    )
    changed = _request(
        tool_name="workspace_mcp_apply",
        arguments={"server": "different-server"},
    )

    pending = await executor.execute(original, _never_invoke)
    assert pending.status == "denied"
    approval = repository.list_approvals()[0]
    _ = governor.decide_approval(
        approval.approval_id,
        grant=True,
        actor_id="local-operator",
        reason_code="approved_for_test",
    )
    result = await executor.execute(changed, _never_invoke)

    assert result.status == "error"
    assert repository.list_approvals()[0].state == "granted"
    assert repository.list_calls()[0].state == "awaiting_approval"
    manager.close()


@pytest.mark.asyncio
async def test_changed_snapshot_cannot_consume_approval(tmp_path):
    manager, repository, governor = _runtime(
        tmp_path,
        risk="external-side-effect",
    )
    executor = ToolExecutor(governor=governor)
    request = _request(tool_name="agent_restart", snapshot_id="snapshot-a")

    _ = await executor.execute(request, _never_invoke)
    approval = repository.list_approvals()[0]
    _ = governor.decide_approval(
        approval.approval_id,
        grant=True,
        actor_id="local-operator",
        reason_code="approved_for_test",
    )
    stale = await executor.execute(
        _request(tool_name="agent_restart", snapshot_id="snapshot-b"),
        _never_invoke,
    )

    assert stale.status == "error"
    assert repository.list_approvals()[0].state == "granted"
    manager.close()


@pytest.mark.asyncio
async def test_external_exception_is_ambiguous_and_not_retried(tmp_path):
    manager, repository, governor = _runtime(
        tmp_path,
        risk="external-side-effect",
    )
    executor = ToolExecutor(governor=governor)
    invocations = 0

    async def invoke(_name: str, _arguments: dict[str, Any]) -> str:
        nonlocal invocations
        invocations += 1
        raise TimeoutError("outcome is not observable")

    result = await executor.execute(
        _request(tool_name="external_mcp_tool"),
        invoke,
    )
    replay = await executor.execute(
        _request(tool_name="external_mcp_tool"),
        invoke,
    )

    assert result.status == "error"
    assert replay.status == "denied"
    assert invocations == 1
    assert repository.list_calls()[0].state == "unknown"
    assert repository.list_calls()[0].error_code == "TimeoutError"
    manager.close()


class _RewriteHook(ToolHook):
    name = "rewrite"
    event = "pre_tool_use"

    def matches(self, _ctx: HookContext) -> bool:
        return True

    async def run(self, _ctx: HookContext) -> HookOutcome:
        return HookOutcome(updated_input={"path": "final.txt"})


@pytest.mark.asyncio
async def test_plugin_rewrite_precedes_policy_binding(tmp_path):
    manager, repository, governor = _runtime(tmp_path)
    executor = ToolExecutor([_RewriteHook()], governor=governor)
    received: list[dict[str, Any]] = []

    async def invoke(_name: str, arguments: dict[str, Any]) -> str:
        received.append(arguments)
        return "ok"

    result = await executor.execute(
        _request(arguments={"path": "original.txt"}),
        invoke,
    )
    call = repository.list_calls()[0]

    assert result.status == "success"
    assert received == [{"path": "final.txt"}]
    assert call.args_hash == canonical_arguments_hash({"path": "final.txt"})
    assert call.args_redacted == {"path": "final.txt"}
    manager.close()


@pytest.mark.asyncio
async def test_sensitive_arguments_are_redacted_and_bounded(tmp_path):
    manager, repository, governor = _runtime(tmp_path)
    executor = ToolExecutor(governor=governor)
    secret = "test-secret-value-that-must-not-be-persisted"
    arguments = {
        "apiKey": secret,
        "nested": {"telegram_token": secret},
        "body": "x" * 20_000,
    }

    result = await executor.execute(
        _request(arguments=arguments),
        _return_ok,
    )
    call = repository.list_calls()[0]
    dump = "\n".join(manager.control_store._conn.iterdump())

    assert result.status == "success"
    assert secret not in dump
    assert len(json.dumps(call.args_redacted, ensure_ascii=False)) < 9_000
    assert call.args_redacted["apiKey"] == "[REDACTED]"
    assert "chars omitted" in str(call.args_redacted["body"])
    manager.close()


@pytest.mark.asyncio
async def test_preflight_evaluates_without_persisting_or_consuming(tmp_path):
    manager, repository, governor = _runtime(tmp_path)
    executor = ToolExecutor(governor=governor)

    result = await executor.preflight(_request())

    assert result.status == "success"
    assert repository.list_calls() == []
    assert repository.list_approvals() == []
    manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["passive", "proactive", "subagent"])
async def test_all_runtime_tool_sources_share_the_same_ledger(tmp_path, source):
    manager, repository, governor = _runtime(tmp_path)
    executor = ToolExecutor(governor=governor)

    result = await executor.execute(
        _request(
            call_id=f"{source}-call",
            source=source,
        ),
        _return_ok,
    )

    assert result.status == "success"
    assert repository.list_calls()[0].source == source
    manager.close()


async def _never_invoke(_name: str, _arguments: dict[str, Any]) -> str:
    raise AssertionError("tool must not be invoked")


async def _return_ok(_name: str, _arguments: dict[str, Any]) -> str:
    return "ok"
