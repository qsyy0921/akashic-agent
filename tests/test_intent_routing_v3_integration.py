from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import agent.routing.dense as dense_module
import agent.routing.gate_v3 as gate_module
from agent.config_models import Config
from agent.control.context import current_turn_id
from agent.core.passive_turn import DefaultReasoner
from agent.core.runtime_support import LLMServices, ToolDiscoveryState
from agent.looping.ports import LLMConfig
from agent.plugins.snapshot import (
    RuntimeSnapshotCompiler,
    RuntimeSnapshotStore,
    bind_runtime_snapshot,
    reset_runtime_snapshot,
)
from agent.routing.advisor import (
    RoutingSnapshotMismatchError,
    build_discovery_snapshot,
    select_active_preloads,
    validate_route_advice,
)
from agent.routing.advisor_v3 import (
    INTENT_ROUTER_V3_VERSION,
    IntentV3TurnRouteAdvisor,
    UnavailableV3ShadowRouteAdvisor,
)
from agent.routing.config import IntentRoutingConfig
from agent.routing.contracts import (
    GoalRoute,
    GoalSpec,
    HypotheticalCapability,
    IntentGoal,
    IntentView,
    RouteAdvice,
    RouteContext,
    RouteRequest,
    ToolCandidate,
)
from agent.routing.dense import DenseRoutingError
from agent.routing.gate_v3 import V3QualityGateError, V3QualityGateEvidence
from agent.routing.hybrid import HybridMatch, HybridRouteRetriever
from agent.routing.intent_view import (
    INTENT_VIEW_PROMPT_VERSION,
    IntentViewAnalysis,
    IntentViewAnalyzer,
    IntentViewUnavailableError,
)
from agent.provider import LLMResponse, ToolCall
from agent.tools.base import Tool
from agent.tools.registry import ToolRegistry
from agent.tools.tool_search import ToolSearchTool
from bootstrap.tools import build_intent_route_advisor
from core.error_context import current_session_key

_EMBEDDING_MODEL = "qwen3-embedding:4b"
_EMBEDDING_DIMENSION = 1024
_INTENT_MODEL = "gpt-5.6-terra"


class _Tool(Tool):
    def __init__(self, name: str, description: str) -> None:
        self._name = name
        self._description = description
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }

    async def execute(self, **kwargs: Any) -> str:
        self.calls.append(dict(kwargs))
        return "not executed"


class _ReasonerProvider:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("provider.chat called more than expected")
        return self._responses.pop(0)


class _RecordingRouteAdvisor:
    mode = "active"

    def __init__(self, registry: ToolRegistry, names: tuple[str, ...]) -> None:
        self._registry = registry
        self._names = names
        self.requests: list[RouteRequest] = []

    async def advise(self, request: RouteRequest) -> RouteAdvice:
        self.requests.append(request)
        discovery = build_discovery_snapshot(
            self._registry,
            request.capability_snapshot_id,
        )
        documents = {item.tool_name: item for item in discovery.documents}
        routes: list[GoalRoute] = []
        for index, name in enumerate(self._names, start=1):
            document = documents[name]
            routes.append(
                GoalRoute(
                    goal=GoalSpec(
                        goal_id=f"goal-{index}",
                        statement=document.summary,
                        operation_query=document.summary,
                    ),
                    candidates=(
                        ToolCandidate(
                            tool_name=name,
                            operation_id=document.operation_id,
                            lexical_rank=1,
                            dense_rank=None,
                            llm_view_rank=1,
                            fused_rank=1,
                            reason_codes=("test_route",),
                        ),
                    ),
                )
            )
        return RouteAdvice(
            schema_version="3",
            router_version=INTENT_ROUTER_V3_VERSION,
            turn_id=request.turn_id,
            capability_snapshot_id=request.capability_snapshot_id,
            discovery_snapshot_id=discovery.snapshot_id,
            status="resolved",
            decision_band="high_margin",
            goals=tuple(routes),
            preloaded_tool_names=self._names,
            reason_codes=("test_route",),
            latency_ms=1,
        )


class _ReadyEncoder:
    model_id = _EMBEDDING_MODEL
    dimension = _EMBEDDING_DIMENSION
    events: list[str] = []

    def __init__(self, **_: object) -> None:
        self.events.append("encoder")

    def ensure_ready(self) -> None:
        self.events.append("ready")


class _Retriever:
    model_id = _EMBEDDING_MODEL

    def retrieve(
        self,
        query: str,
        snapshot: object,
        *,
        top_k: int = 3,
    ) -> tuple[HybridMatch, ...]:
        if query not in {"查杭州天气", "查询杭州天气"}:
            return ()
        document = cast(Any, snapshot).documents[0]
        return (
            HybridMatch(
                document=document,
                lexical_rank=1,
                dense_rank=1,
                fused_rank=1,
                reciprocal_rank_score=0.03,
                reason_codes=("rrf", "exact_operation"),
            ),
        )

    def retrieve_many(
        self,
        queries: tuple[str, ...],
        snapshot: object,
        *,
        top_k: int = 3,
    ) -> tuple[tuple[HybridMatch, ...], ...]:
        return tuple(
            self.retrieve(query, snapshot, top_k=top_k) for query in queries
        )


class _StaticAnalyzer:
    async def analyze(self, *_: object, **__: object) -> IntentViewAnalysis:
        return IntentViewAnalysis(
            view=IntentView(
                schema_version="3",
                goals=(
                    IntentGoal(
                        goal_id="goal-1",
                        statement="查询杭州天气",
                        rewritten_intent="查询杭州天气",
                        hypothetical_capabilities=(
                            HypotheticalCapability(
                                name="天气信息查询",
                                description="获取指定地点天气",
                                required_inputs=("地点",),
                                desired_outputs=("data",),
                            ),
                        ),
                    ),
                ),
                unresolved_references=(),
                tool_requirement="required",
            ),
            provider_calls=1,
            prompt_version=INTENT_VIEW_PROMPT_VERSION,
        )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        _Tool("weather_query", "查询城市天气预报"),
        operation_id="weather.query",
        summary="查询城市天气预报",
        examples=("查杭州天气",),
        output_kinds=("data", "text"),
    )
    return registry


def _routing(mode: str) -> IntentRoutingConfig:
    return IntentRoutingConfig(
        mode=cast(Any, mode),
        embedding_model=_EMBEDDING_MODEL,
        embedding_base_url="http://127.0.0.1:11434/v1",
        embedding_dimension=_EMBEDDING_DIMENSION,
    )


def _config(mode: str) -> Config:
    return Config(
        provider="terra-responses",
        model=_INTENT_MODEL,
        api_key="test-key",
        system_prompt="test",
        tool_search_enabled=mode == "active",
        intent_routing=_routing(mode),
    )


def _gate(intent_model_id: str = _INTENT_MODEL) -> V3QualityGateEvidence:
    return V3QualityGateEvidence(
        report_digest="sha256:" + "1" * 64,
        router_version=INTENT_ROUTER_V3_VERSION,
        router_digest="sha256:" + "2" * 64,
        retrieval_model_id=_EMBEDDING_MODEL,
        intent_model_id=intent_model_id,
        model_digest="sha256:" + "3" * 64,
        prompt_version=INTENT_VIEW_PROMPT_VERSION,
        prompt_digest="sha256:" + "4" * 64,
        dataset_digest="sha256:" + "5" * 64,
        catalog_digest="sha256:" + "6" * 64,
        configuration_digest="sha256:" + "7" * 64,
        metrics={},
    )


def _reasoner(
    registry: ToolRegistry,
    provider: _ReasonerProvider,
    advisor: _RecordingRouteAdvisor,
) -> DefaultReasoner:
    return DefaultReasoner(
        llm=cast(
            Any,
            LLMServices(
                provider=cast(Any, provider),
                light_provider=cast(Any, provider),
            ),
        ),
        llm_config=LLMConfig(model=_INTENT_MODEL, max_iterations=4, max_tokens=512),
        tools=registry,
        discovery=ToolDiscoveryState(),
        tool_search_enabled=True,
        memory_window=40,
        context=cast(
            Any,
            SimpleNamespace(
                render=lambda request, **_: SimpleNamespace(
                    messages=[
                        {"role": "user", "content": request.current_message}
                    ]
                )
            ),
        ),
        session_manager=cast(Any, SimpleNamespace()),
        route_advisor=cast(Any, advisor),
    )


def test_routing_defaults_off_and_has_no_legacy_v2_switch() -> None:
    routing = IntentRoutingConfig()

    assert routing.mode == "off"
    assert routing.gate_report_path.endswith("v3-gate-report.json")
    assert not hasattr(routing, "version")


def test_active_preloads_include_dependent_goals() -> None:
    root = GoalRoute(
        goal=GoalSpec(
            goal_id="goal-1",
            statement="读取 README",
            operation_query="读取 README",
        ),
        candidates=(
            ToolCandidate(
                tool_name="file_read",
                operation_id="file.read",
                lexical_rank=1,
                dense_rank=1,
                fused_rank=1,
                reason_codes=("rrf",),
            ),
        ),
    )
    dependent = GoalRoute(
        goal=GoalSpec(
            goal_id="goal-2",
            statement="保存摘要",
            operation_query="写入摘要",
            relation="after",
            depends_on=("goal-1",),
        ),
        candidates=(
            ToolCandidate(
                tool_name="file_write",
                operation_id="file.write",
                lexical_rank=1,
                dense_rank=1,
                fused_rank=1,
                reason_codes=("rrf",),
            ),
        ),
    )

    assert select_active_preloads([root, dependent]) == (
        "file_read",
        "file_write",
    )


def test_active_gate_is_checked_before_dense_initialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def reject_gate(*_: object, **__: object) -> object:
        raise V3QualityGateError("gate rejected")

    def unexpected_encoder(**_: object) -> object:
        raise AssertionError("dense initialized before V3 gate")

    monkeypatch.setattr(gate_module, "verify_v3_quality_gate", reject_gate)
    monkeypatch.setattr(dense_module, "build_dense_encoder", unexpected_encoder)

    with pytest.raises(V3QualityGateError, match="gate rejected"):
        build_intent_route_advisor(
            config=_config("active"),
            tools=_registry(),
            provider=cast(Any, SimpleNamespace()),
            application_root=tmp_path,
        )


def test_active_builds_only_after_exact_gate_and_dense_readiness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    _ReadyEncoder.events = events
    verified: dict[str, object] = {}

    def verify_gate(*_: object, **kwargs: object) -> V3QualityGateEvidence:
        events.append("gate")
        verified.update(kwargs)
        return _gate()

    monkeypatch.setattr(gate_module, "verify_v3_quality_gate", verify_gate)
    monkeypatch.setattr(
        dense_module,
        "build_dense_encoder",
        lambda **kwargs: _ReadyEncoder(**kwargs),
    )

    advisor = build_intent_route_advisor(
        config=_config("active"),
        tools=_registry(),
        provider=cast(Any, SimpleNamespace()),
        application_root=tmp_path,
    )

    assert isinstance(advisor, IntentV3TurnRouteAdvisor)
    assert advisor.mode == "active"
    assert events == ["gate", "encoder", "ready"]
    assert verified["expected_intent_model_id"] == _INTENT_MODEL
    configuration = cast(dict[str, object], verified["expected_configuration"])
    assert configuration["embedding_backend"] == "openai_compatible"
    assert configuration["dense_threads"] == 0


def test_shadow_reports_dense_failure_without_alternate_router(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        dense_module,
        "build_dense_encoder",
        lambda **_: (_ for _ in ()).throw(DenseRoutingError("missing")),
    )

    advisor = build_intent_route_advisor(
        config=_config("shadow"),
        tools=_registry(),
        provider=cast(Any, SimpleNamespace()),
        application_root=tmp_path,
    )

    assert isinstance(advisor, UnavailableV3ShadowRouteAdvisor)


@pytest.mark.asyncio
async def test_verified_active_preloads_current_candidate() -> None:
    registry = _registry()
    advisor = IntentV3TurnRouteAdvisor(
        registry,
        cast(HybridRouteRetriever, _Retriever()),
        cast(IntentViewAnalyzer, _StaticAnalyzer()),
        mode="active",
        intent_model_id=_INTENT_MODEL,
        gate_evidence=_gate(),
    )
    discovery = build_discovery_snapshot(registry, "static-runtime")

    advice = await advisor.advise(
        RouteRequest(
            turn_id="turn-active-v3",
            capability_snapshot_id=discovery.capability_snapshot_id,
            message="查杭州天气",
            context=RouteContext(),
        )
    )

    assert advice.status == "resolved"
    assert advice.preloaded_tool_names == ("weather_query",)
    assert advice.goals[0].candidates[0].operation_id == "weather.query"


@pytest.mark.asyncio
async def test_active_degrades_to_zero_preloads_when_intent_provider_is_missing() -> None:
    registry = _registry()
    advisor = IntentV3TurnRouteAdvisor(
        registry,
        cast(HybridRouteRetriever, _Retriever()),
        IntentViewAnalyzer(None),
        mode="active",
        intent_model_id=_INTENT_MODEL,
        gate_evidence=_gate(),
    )

    advice = await advisor.advise(
        RouteRequest(
            turn_id="turn-no-provider",
            capability_snapshot_id="static-runtime",
            message="查杭州天气",
            context=RouteContext(),
        )
    )

    discovery = build_discovery_snapshot(registry, "static-runtime")
    assert advice.status == "unavailable"
    assert advice.preloaded_tool_names == ()
    assert advice.discovery_snapshot_id == discovery.snapshot_id
    assert advice.reason_codes == ("intent_view_unavailable", "active_mode")


@pytest.mark.asyncio
async def test_advice_cannot_cross_runtime_snapshot_publication() -> None:
    registry = _registry()
    compiler = RuntimeSnapshotCompiler()
    first = compiler.compile({}, snapshot_revision="first")
    first.tool_registry = registry.fork()
    first_store = RuntimeSnapshotStore()
    first_store.install(first)
    first_lease = first_store.lease()
    first_token = bind_runtime_snapshot(first_lease)
    try:
        advisor = IntentV3TurnRouteAdvisor(
            registry,
            cast(HybridRouteRetriever, _Retriever()),
            cast(IntentViewAnalyzer, _StaticAnalyzer()),
            mode="active",
            intent_model_id=_INTENT_MODEL,
            gate_evidence=_gate(),
        )
        advice = await advisor.advise(
            RouteRequest(
                turn_id="turn-first",
                capability_snapshot_id=first.snapshot_id,
                message="查杭州天气",
                context=RouteContext(),
            )
        )
        assert validate_route_advice(
            advice,
            registry=registry,
            runtime_snapshot_id=first.snapshot_id,
            disabled_tools=set(),
        ) == ("weather_query",)
    finally:
        reset_runtime_snapshot(first_token)
        await first_lease.release()
        await first_store.close()

    second = compiler.compile({}, snapshot_revision="second")
    second.tool_registry = registry.fork()
    second_store = RuntimeSnapshotStore()
    second_store.install(second)
    second_lease = second_store.lease()
    second_token = bind_runtime_snapshot(second_lease)
    try:
        with pytest.raises(RoutingSnapshotMismatchError):
            validate_route_advice(
                advice,
                registry=registry,
                runtime_snapshot_id=second.snapshot_id,
                disabled_tools=set(),
            )
    finally:
        reset_runtime_snapshot(second_token)
        await second_lease.release()
        await second_store.close()


@pytest.mark.asyncio
async def test_reasoner_exposes_multiple_routed_tools_and_uses_history_facts() -> None:
    registry = ToolRegistry()
    registry.register(ToolSearchTool(registry), always_on=True)
    registry.register(
        _Tool("weather_query", "查询城市天气预报"),
        operation_id="weather.query",
        output_kinds=("data", "text"),
    )
    registry.register(
        _Tool("arxiv_search", "检索 arXiv 论文"),
        operation_id="paper.search",
        output_kinds=("data", "text"),
    )
    advisor = _RecordingRouteAdvisor(
        registry,
        ("weather_query", "arxiv_search"),
    )
    provider = _ReasonerProvider([LLMResponse(content="done", tool_calls=[])])
    reasoner = _reasoner(registry, provider, advisor)
    history = [
        {"role": "user", "content": "先查杭州天气"},
        {
            "role": "assistant",
            "content": "杭州今天晴。",
            "tools_used": ["weather_query"],
        },
    ]
    session = SimpleNamespace(
        key="cli:multi",
        metadata={},
        messages=history,
        last_consolidated=0,
        get_history=lambda max_messages=40, *, start_index=None: list(history),
    )
    msg = SimpleNamespace(
        content="再找两篇相关论文并总结",
        media=[],
        metadata={},
        channel="cli",
        chat_id="multi",
        timestamp=datetime(2026, 7, 23, 12, 0, 0),
    )
    snapshot = RuntimeSnapshotCompiler().compile({}, snapshot_revision="multi")
    snapshot.tool_registry = registry.fork()
    store = RuntimeSnapshotStore()
    store.install(snapshot)
    lease = store.lease()
    snapshot_token = bind_runtime_snapshot(lease)
    turn_token = current_turn_id.set("turn-multi")
    session_token = current_session_key.set(session.key)
    try:
        result = await reasoner.run_turn(msg=msg, session=cast(Any, session))
    finally:
        current_session_key.reset(session_token)
        current_turn_id.reset(turn_token)
        reset_runtime_snapshot(snapshot_token)
        await lease.release()
        await store.close()

    assert result.reply == "done"
    first_tool_names = [
        item["function"]["name"] for item in provider.calls[0]["tools"]
    ]
    assert first_tool_names == ["tool_search", "weather_query", "arxiv_search"]
    assert provider.calls[0]["tool_choice"] == "auto"
    assert len(advisor.requests) == 1
    route_context = advisor.requests[0].context
    assert route_context is not None
    assert route_context.previous_operation_ids == ("weather.query",)
    assert route_context.messages[-1].output_kinds == ("data", "text")


@pytest.mark.asyncio
async def test_high_confidence_opt_in_route_requires_only_the_first_tool_step() -> None:
    registry = ToolRegistry()
    registry.register(ToolSearchTool(registry), always_on=True)
    image_tool = _Tool("image_generate", "生成一张图片")
    registry.register(
        image_tool,
        risk="external-side-effect",
        operation_id="image.generate",
        output_kinds=("image", "text"),
        force_tool_choice_on_high_confidence_route=True,
    )
    advisor = _RecordingRouteAdvisor(registry, ("image_generate",))
    provider = _ReasonerProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        "image-1",
                        "image_generate",
                        {
                            "query": "blue circle",
                        },
                    )
                ],
            ),
            LLMResponse(content="done", tool_calls=[]),
        ]
    )
    reasoner = _reasoner(registry, provider, advisor)
    session = SimpleNamespace(
        key="cli:image",
        metadata={},
        messages=[],
        last_consolidated=0,
        get_history=lambda max_messages=40, *, start_index=None: [],
    )
    msg = SimpleNamespace(
        content="生成一张简单的蓝色圆形图",
        media=[],
        metadata={},
        channel="cli",
        chat_id="image",
        timestamp=datetime(2026, 7, 23, 12, 0, 30),
    )
    snapshot = RuntimeSnapshotCompiler().compile({}, snapshot_revision="image")
    snapshot.tool_registry = registry.fork()
    store = RuntimeSnapshotStore()
    store.install(snapshot)
    lease = store.lease()
    snapshot_token = bind_runtime_snapshot(lease)
    turn_token = current_turn_id.set("turn-image")
    session_token = current_session_key.set(session.key)
    try:
        result = await reasoner.run_turn(msg=msg, session=cast(Any, session))
    finally:
        current_session_key.reset(session_token)
        current_turn_id.reset(turn_token)
        reset_runtime_snapshot(snapshot_token)
        await lease.release()
        await store.close()

    assert result.reply == "done"
    assert image_tool.calls == [{"query": "blue circle"}]
    assert provider.calls[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "image_generate"},
    }
    assert provider.calls[1]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_route_visibility_does_not_grant_current_turn_search_authority() -> None:
    registry = ToolRegistry()
    registry.register(ToolSearchTool(registry), always_on=True)
    protected = _Tool("agent_restart", "重启 Agent 服务")
    registry.register(
        protected,
        risk="external-side-effect",
        preloadable=False,
        requires_turn_search=True,
        operation_id="agent.restart",
    )
    advisor = _RecordingRouteAdvisor(registry, ("agent_restart",))
    provider = _ReasonerProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCall("restart-1", "agent_restart", {"query": "reload"})
                ],
            ),
            LLMResponse(content="blocked safely", tool_calls=[]),
        ]
    )
    reasoner = _reasoner(registry, provider, advisor)
    session = SimpleNamespace(
        key="cli:guard",
        metadata={},
        messages=[],
        last_consolidated=0,
        get_history=lambda max_messages=40, *, start_index=None: [],
    )
    msg = SimpleNamespace(
        content="重启服务",
        media=[],
        metadata={},
        channel="cli",
        chat_id="guard",
        timestamp=datetime(2026, 7, 23, 12, 1, 0),
    )
    snapshot = RuntimeSnapshotCompiler().compile({}, snapshot_revision="guard")
    snapshot.tool_registry = registry.fork()
    store = RuntimeSnapshotStore()
    store.install(snapshot)
    lease = store.lease()
    snapshot_token = bind_runtime_snapshot(lease)
    turn_token = current_turn_id.set("turn-guard")
    session_token = current_session_key.set(session.key)
    try:
        result = await reasoner.run_turn(msg=msg, session=cast(Any, session))
    finally:
        current_session_key.reset(session_token)
        current_turn_id.reset(turn_token)
        reset_runtime_snapshot(snapshot_token)
        await lease.release()
        await store.close()

    first_tool_names = [
        item["function"]["name"] for item in provider.calls[0]["tools"]
    ]
    assert "agent_restart" in first_tool_names
    assert protected.calls == []
    assert result.tools_used == []
    assert any(
        "必须在当前 turn" in str(call.get("result", ""))
        for step in result.tool_chain
        for call in cast(list[dict[str, object]], step.get("calls", []))
    )
