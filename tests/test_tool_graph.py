import asyncio
from typing import Any, cast

import pytest

from agent.core.passive_turn import DefaultReasoner
from agent.core.runtime_support import LLMServices, ToolDiscoveryState
from agent.looping.ports import LLMConfig
from agent.planning import ToolGraph, build_task_plan
from agent.provider import LLMResponse, ToolCall
from agent.routing.contracts import (
    GoalRoute,
    GoalSpec,
    RouteAdvice,
    ToolCandidate,
)
from agent.tool_hooks.base import ToolHook
from agent.tool_hooks.types import HookContext, HookOutcome
from agent.tools.base import Tool
from agent.tools.registry import ToolRegistry


class _Tool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return f"{self._name}-ok"


class _Provider:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("provider.chat called more than expected")
        return self._responses.pop(0)


class _FailingTool(_Tool):
    async def execute(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        raise RuntimeError("expected tool failure")


class _DenyFetchHook(ToolHook):
    name = "deny_fetch"
    event = "pre_tool_use"

    def matches(self, ctx: HookContext) -> bool:
        return ctx.request.tool_name == "fetch"

    async def run(self, ctx: HookContext) -> HookOutcome:
        return HookOutcome(decision="deny", reason="expected denial")


def _register(
    registry: ToolRegistry,
    name: str,
    operation_id: str,
    *,
    requires: tuple[str, ...] = (),
) -> _Tool:
    tool = _Tool(name)
    registry.register(
        tool,
        operation_id=operation_id,
        consumes=("request",),
        produces=(operation_id,),
        requires_operations=requires,
    )
    return tool


def _candidate(name: str, operation_id: str) -> ToolCandidate:
    return ToolCandidate(
        tool_name=name,
        operation_id=operation_id,
        lexical_rank=1,
        dense_rank=None,
        fused_rank=1,
        reason_codes=("test",),
    )


def _advice(*routes: GoalRoute) -> RouteAdvice:
    names = tuple(route.candidates[0].tool_name for route in routes)
    return RouteAdvice(
        schema_version="3",
        router_version="test",
        turn_id="turn-1",
        capability_snapshot_id="snapshot-1",
        discovery_snapshot_id=f"sha256:{'0' * 64}",
        status="resolved",
        decision_band="high_margin",
        goals=tuple(routes),
        preloaded_tool_names=names,
        reason_codes=("test",),
        latency_ms=1,
    )


def test_registry_planning_metadata_is_optional_and_validated() -> None:
    registry = ToolRegistry()
    registry.register(_Tool("plain"), operation_id="plain.run")

    meta = registry.get_tool_meta("plain")
    document = registry.get_document("plain")

    assert meta is not None
    assert document is not None
    assert meta.consumes == ()
    assert document.requires_operations == ()
    with pytest.raises(ValueError, match="requires_operations"):
        registry.register(
            _Tool("recursive"),
            operation_id="recursive.run",
            requires_operations=("recursive.run",),
        )


def test_tool_graph_orders_explicit_dependencies_deterministically() -> None:
    registry = ToolRegistry()
    _register(registry, "publish", "item.publish")
    _register(registry, "fetch", "item.fetch")
    graph = ToolGraph.from_registry(registry)

    plan = graph.plan(
        ("publish", "fetch"),
        dependencies={"publish": ("fetch",), "fetch": ()},
    )

    assert plan.ready
    assert plan.tool_names == ("fetch", "publish")
    assert plan.steps[1].depends_on == ("fetch",)
    assert ToolGraph.from_registry(registry).digest == graph.digest


def test_tool_graph_expands_one_unique_operation_provider() -> None:
    registry = ToolRegistry()
    _register(registry, "fetch", "item.fetch")
    _register(
        registry,
        "publish",
        "item.publish",
        requires=("item.fetch",),
    )

    plan = ToolGraph.from_registry(registry).plan(("publish",))

    assert plan.ready
    assert plan.tool_names == ("fetch", "publish")
    assert plan.steps[1].depends_on == ("fetch",)


def test_tool_graph_fails_closed_for_missing_and_ambiguous_provider() -> None:
    missing_registry = ToolRegistry()
    _register(
        missing_registry,
        "publish",
        "item.publish",
        requires=("item.fetch",),
    )
    missing = ToolGraph.from_registry(missing_registry).plan(("publish",))

    ambiguous_registry = ToolRegistry()
    _register(ambiguous_registry, "fetch_a", "item.fetch")
    _register(ambiguous_registry, "fetch_b", "item.fetch")
    _register(
        ambiguous_registry,
        "publish",
        "item.publish",
        requires=("item.fetch",),
    )
    ambiguous = ToolGraph.from_registry(ambiguous_registry).plan(("publish",))

    assert missing.status == "missing_capability"
    assert missing.unresolved_operations == ("item.fetch",)
    assert ambiguous.status == "ambiguous_capability"
    assert ambiguous.unresolved_operations == ("item.fetch",)


def test_tool_graph_rejects_cycle_disabled_tool_and_step_overflow() -> None:
    cycle_registry = ToolRegistry()
    _register(cycle_registry, "one", "chain.one", requires=("chain.two",))
    _register(cycle_registry, "two", "chain.two", requires=("chain.one",))
    cycle = ToolGraph.from_registry(cycle_registry).plan(("one",))

    disabled = ToolGraph.from_registry(cycle_registry).plan(
        ("one",),
        disabled_tools=frozenset({"one"}),
    )

    chain_registry = ToolRegistry()
    for index in range(1, 7):
        requires = (f"chain.step{index + 1}",) if index < 6 else ()
        _register(
            chain_registry,
            f"step_{index}",
            f"chain.step{index}",
            requires=requires,
        )
    overflow = ToolGraph.from_registry(chain_registry).plan(("step_1",))

    assert cycle.status == "cycle"
    assert disabled.status == "missing_capability"
    assert overflow.status == "too_large"


def test_route_advice_goal_dependencies_build_task_plan() -> None:
    registry = ToolRegistry()
    _register(registry, "fetch", "item.fetch")
    _register(registry, "publish", "item.publish")
    advice = _advice(
        GoalRoute(
            goal=GoalSpec("fetch-goal", "fetch", "fetch"),
            candidates=(_candidate("fetch", "item.fetch"),),
        ),
        GoalRoute(
            goal=GoalSpec(
                "publish-goal",
                "publish",
                "publish",
                relation="on_success",
                depends_on=("fetch-goal",),
            ),
            candidates=(_candidate("publish", "item.publish"),),
        ),
    )

    plan = build_task_plan(advice, registry=registry)

    assert plan.ready
    assert plan.tool_names == ("fetch", "publish")


def test_reasoner_blocks_dependency_until_prerequisite_succeeds() -> None:
    registry = ToolRegistry()
    fetch = _register(registry, "fetch", "item.fetch")
    publish = _register(
        registry,
        "publish",
        "item.publish",
        requires=("item.fetch",),
    )
    plan = ToolGraph.from_registry(registry).plan(("publish",))
    provider = _Provider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCall("publish-early", "publish", {}),
                    ToolCall("fetch", "fetch", {}),
                ],
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCall("publish-retry", "publish", {})],
            ),
            LLMResponse(content="done", tool_calls=[]),
        ]
    )
    reasoner = DefaultReasoner(
        llm=cast(
            Any,
            LLMServices(
                provider=cast(Any, provider),
                light_provider=cast(Any, provider),
            ),
        ),
        llm_config=LLMConfig(model="m", max_iterations=5, max_tokens=512),
        tools=registry,
        discovery=ToolDiscoveryState(),
        tool_search_enabled=False,
        memory_window=40,
    )
    canonical_messages = [{"role": "user", "content": "fetch then publish"}]

    result = asyncio.run(reasoner.run(canonical_messages, task_plan=plan))

    assert result.reply == "done"
    assert fetch.calls == [{}]
    assert publish.calls == [{}]
    assert result.metadata["tool_chain"][0]["calls"][0]["status"] == "blocked"
    assert result.metadata["tool_chain"][0]["calls"][1]["status"] == "success"
    assert result.metadata["tool_chain"][1]["calls"][0]["status"] == "success"
    assert any(
        "## task_plan" in str(message.get("content", ""))
        for message in provider.calls[0]["messages"]
    )
    assert not any(
        "## task_plan" in str(message.get("content", ""))
        for message in canonical_messages
    )


@pytest.mark.parametrize("failure_mode", ["error", "denied"])
def test_reasoner_failure_or_denial_does_not_satisfy_dependency(
    failure_mode: str,
) -> None:
    registry = ToolRegistry()
    fetch: _Tool
    if failure_mode == "error":
        fetch = _FailingTool("fetch")
        registry.register(
            fetch,
            operation_id="item.fetch",
            produces=("item.fetch",),
        )
    else:
        fetch = _register(registry, "fetch", "item.fetch")
    publish = _register(
        registry,
        "publish",
        "item.publish",
        requires=("item.fetch",),
    )
    plan = ToolGraph.from_registry(registry).plan(("publish",))
    provider = _Provider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCall("fetch", "fetch", {}),
                    ToolCall("publish", "publish", {}),
                ],
            ),
            LLMResponse(content="done", tool_calls=[]),
        ]
    )
    reasoner = DefaultReasoner(
        llm=cast(
            Any,
            LLMServices(
                provider=cast(Any, provider),
                light_provider=cast(Any, provider),
            ),
        ),
        llm_config=LLMConfig(model="m", max_iterations=4, max_tokens=512),
        tools=registry,
        discovery=ToolDiscoveryState(),
        tool_search_enabled=False,
        memory_window=40,
    )
    if failure_mode == "denied":
        reasoner.add_tool_hooks([_DenyFetchHook()])

    result = asyncio.run(
        reasoner.run(
            [{"role": "user", "content": "fetch then publish"}],
            task_plan=plan,
        )
    )

    calls = result.metadata["tool_chain"][0]["calls"]
    assert calls[0]["status"] == failure_mode
    assert calls[1]["status"] == "blocked"
    assert publish.calls == []
