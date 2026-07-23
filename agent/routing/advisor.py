from __future__ import annotations

from typing import Literal, Protocol

from agent.plugins.snapshot import get_current_runtime_snapshot
from agent.routing.contracts import (
    DiscoverySnapshot,
    GoalRoute,
    RouteAdvice,
    RouteRequest,
    ToolDiscoveryDocument,
)
from agent.tools.registry import ToolRegistry


class RoutingUnavailableError(RuntimeError):
    """Raised when the configured authoritative routing path is unavailable."""


class RoutingSnapshotMismatchError(RoutingUnavailableError):
    """Raised when routing and execution do not share one runtime snapshot."""


class TurnRouteAdvisor(Protocol):
    mode: Literal["shadow", "active"]

    async def advise(self, request: RouteRequest) -> RouteAdvice: ...


def build_discovery_snapshot(
    registry: ToolRegistry,
    runtime_snapshot_id: str,
) -> DiscoverySnapshot:
    snapshot = get_current_runtime_snapshot()
    if snapshot is not None and snapshot.snapshot_id != runtime_snapshot_id:
        raise RoutingSnapshotMismatchError(
            "intent routing runtime snapshot changed before discovery"
        )
    documents: list[ToolDiscoveryDocument] = []
    for document in registry.get_documents():
        if document.name == "tool_search":
            continue
        source_type = _source_type(document.source_type)
        documents.append(
            ToolDiscoveryDocument(
                tool_name=document.name,
                operation_id=document.operation_id,
                summary=document.summary,
                parameter_terms=document.parameter_terms,
                examples=document.examples,
                output_kinds=tuple(document.output_kinds),  # type: ignore[arg-type]
                schema_digest=document.schema_digest,
                source_type=source_type,
                source_id=document.source_name or source_type,
                risk=_risk(document.risk),
                always_on=document.always_on,
                healthy=True,
            )
        )
    return DiscoverySnapshot.build(runtime_snapshot_id, tuple(documents))


def validate_route_advice(
    advice: RouteAdvice,
    *,
    registry: ToolRegistry,
    runtime_snapshot_id: str,
    disabled_tools: set[str],
) -> tuple[str, ...]:
    snapshot = get_current_runtime_snapshot()
    if snapshot is None or snapshot.snapshot_id != runtime_snapshot_id:
        raise RoutingSnapshotMismatchError(
            "intent route advice does not match the active runtime snapshot"
        )
    if advice.capability_snapshot_id != runtime_snapshot_id:
        raise RoutingSnapshotMismatchError(
            "intent route advice carries a different runtime snapshot"
        )
    current = build_discovery_snapshot(registry, runtime_snapshot_id)
    if advice.discovery_snapshot_id != current.snapshot_id:
        raise RoutingSnapshotMismatchError(
            "intent route advice carries a stale discovery snapshot"
        )
    registered = registry.get_registered_names()
    selected: list[str] = []
    for name in advice.preloaded_tool_names:
        if name in disabled_tools or name not in registered:
            raise RoutingSnapshotMismatchError(
                "intent route advice contains an ineligible tool"
            )
        selected.append(name)
    return tuple(selected)


def select_active_preloads(routes: list[GoalRoute]) -> tuple[str, ...]:
    selected: list[str] = []
    seen: set[str] = set()
    for route in routes:
        if not route.candidates:
            continue
        name = route.candidates[0].tool_name
        if name not in seen:
            selected.append(name)
            seen.add(name)
        if len(selected) == 5:
            break
    return tuple(selected)


def route_advice_trace_payload(advice: RouteAdvice) -> dict[str, object]:
    return {
        "schema_version": advice.schema_version,
        "router_version": advice.router_version,
        "runtime_snapshot_id": advice.capability_snapshot_id,
        "discovery_snapshot_id": advice.discovery_snapshot_id,
        "status": advice.status,
        "decision_band": advice.decision_band,
        "candidate_tool_names": [
            candidate.tool_name
            for route in advice.goals
            for candidate in route.candidates
        ],
        "preloaded_tool_names": list(advice.preloaded_tool_names),
        "reason_codes": list(advice.reason_codes),
        "latency_ms": advice.latency_ms,
    }


def _source_type(value: str) -> Literal["core", "plugin", "mcp"]:
    if value == "mcp":
        return "mcp"
    if value == "plugin":
        return "plugin"
    return "core"


def _risk(value: str) -> Literal["read", "write", "external_side_effect", "privileged"]:
    if value in {"external-side-effect", "external_side_effect"}:
        return "external_side_effect"
    if value in {"write", "read-write"}:
        return "write"
    if value == "privileged":
        return "privileged"
    return "read"


__all__ = [
    "RoutingSnapshotMismatchError",
    "RoutingUnavailableError",
    "TurnRouteAdvisor",
    "build_discovery_snapshot",
    "route_advice_trace_payload",
    "select_active_preloads",
    "validate_route_advice",
]
