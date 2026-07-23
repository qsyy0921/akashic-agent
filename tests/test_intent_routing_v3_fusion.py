from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

from agent.routing.advisor import build_discovery_snapshot
from agent.routing.advisor_v3 import IntentV3TurnRouteAdvisor
from agent.routing.contracts import DiscoverySnapshot, RouteContext, RouteRequest
from agent.routing.hybrid import HybridMatch, HybridRouteRetriever
from agent.routing.intent_view import IntentViewAnalyzer
from agent.tools.base import Tool
from agent.tools.registry import ToolRegistry


class _Tool(Tool):
    def __init__(self, name: str, description: str) -> None:
        self._name = name
        self._description = description

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
        return "not executed"


class _ViewProvider:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[tuple[str, RouteContext]] = []

    async def emit_intent_view(
        self,
        message: str,
        context: RouteContext,
    ) -> object:
        self.calls.append((message, context))
        return self.payload


@dataclass(frozen=True, slots=True)
class _MatchSpec:
    tool_name: str
    lexical_rank: int | None
    dense_rank: int | None
    fused_rank: int
    reason_codes: tuple[str, ...] = ("rrf",)


class _Retriever:
    model_id = "test/v3-dense"

    def __init__(self, results: dict[str, tuple[_MatchSpec, ...]]) -> None:
        self.results = results
        self.calls: list[str] = []

    def retrieve(
        self,
        query: str,
        snapshot: DiscoverySnapshot,
        *,
        top_k: int = 3,
    ) -> tuple[HybridMatch, ...]:
        self.calls.append(query)
        documents = {document.tool_name: document for document in snapshot.documents}
        return tuple(
            HybridMatch(
                document=documents[spec.tool_name],
                lexical_rank=spec.lexical_rank,
                dense_rank=spec.dense_rank,
                fused_rank=spec.fused_rank,
                reciprocal_rank_score=0.0,
                reason_codes=spec.reason_codes,
            )
            for spec in self.results.get(query, ())[:top_k]
        )

    def retrieve_many(
        self,
        queries: tuple[str, ...],
        snapshot: DiscoverySnapshot,
        *,
        top_k: int = 3,
    ) -> tuple[tuple[HybridMatch, ...], ...]:
        return tuple(
            self.retrieve(query, snapshot, top_k=top_k) for query in queries
        )


def _register(
    registry: ToolRegistry,
    name: str,
    operation_id: str,
    summary: str,
    *,
    output_kinds: tuple[str, ...] = ("text",),
) -> None:
    registry.register(
        _Tool(name, summary),
        operation_id=operation_id,
        summary=summary,
        examples=(summary,),
        output_kinds=output_kinds,
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    _register(registry, "weather_query", "weather.query", "查询城市天气预报")
    _register(registry, "web_search", "web.search", "联网检索最新公开信息")
    _register(registry, "image_create", "image.generate", "根据文字描述生成新图片")
    return registry


def _capability(
    name: str,
    description: str,
) -> str:
    return f"{name} {description}"


def _view_payload(
    *,
    rewritten_intent: str,
    capabilities: list[str],
    tool_requirement: str = "required",
    desired_outputs: list[str] | None = None,
    required_inputs: list[str] | None = None,
) -> dict[str, object]:
    disabled = {
        "enabled": False,
        "statement": "",
        "rewritten_intent": "",
        "capability_queries": [],
        "alternative_capability_queries": [],
        "required_inputs": [],
        "desired_outputs": [],
        "missing_required_inputs": [],
        "relation": "independent",
        "depends_on_goal_numbers": [],
    }
    return {
        "schema_version": "3",
        "goal_1": {
            **disabled,
            "enabled": True,
            "statement": "完成用户当前目标",
            "rewritten_intent": rewritten_intent,
            "capability_queries": capabilities,
            "desired_outputs": desired_outputs or [],
            "required_inputs": required_inputs or [],
        },
        "goal_2": dict(disabled),
        "goal_3": dict(disabled),
        "goal_4": dict(disabled),
        "unresolved_references": [],
        "tool_requirement": tool_requirement,
    }


def _tool_free_payload() -> dict[str, object]:
    return _view_payload(
        rewritten_intent="与用户进行普通对话",
        capabilities=[],
        tool_requirement="none",
    )


def _request(registry: ToolRegistry, message: str) -> RouteRequest:
    snapshot = build_discovery_snapshot(registry, "test-runtime")
    return RouteRequest(
        turn_id="turn-v3-fusion",
        capability_snapshot_id=snapshot.snapshot_id,
        message=message,
        context=RouteContext(),
    )


def _advisor(
    registry: ToolRegistry,
    retriever: _Retriever,
    provider: _ViewProvider,
) -> IntentV3TurnRouteAdvisor:
    return IntentV3TurnRouteAdvisor(
        registry,
        cast(HybridRouteRetriever, retriever),
        IntentViewAnalyzer(provider),
    )


@pytest.mark.asyncio
async def test_implicit_intent_is_recalled_only_by_hypothetical_capability() -> None:
    registry = _registry()
    capability = _capability("地点信息检索", "查找附近符合条件的现实地点")
    capability_query = "地点信息检索 查找附近符合条件的现实地点"
    provider = _ViewProvider(
        _view_payload(
            rewritten_intent="寻找附近适合散步的地点",
            capabilities=[capability],
        )
    )
    retriever = _Retriever(
        {
            capability_query: (
                _MatchSpec("web_search", None, None, 1),
            )
        }
    )

    advice = await _advisor(registry, retriever, provider).advise(
        _request(registry, "附近有适合饭后走走的地方吗")
    )

    assert advice.status == "resolved"
    candidate = advice.goals[0].candidates[0]
    assert candidate.operation_id == "web.search"
    assert candidate.lexical_rank is None
    assert candidate.dense_rank is None
    assert candidate.llm_view_rank == 1
    assert "llm_hypothetical" in candidate.reason_codes
    assert retriever.calls == [
        "附近有适合饭后走走的地方吗",
        "寻找附近适合散步的地点",
        capability_query,
    ]
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_operation_collapses_provider_ranks_across_all_three_branches() -> None:
    registry = ToolRegistry()
    _register(registry, "alpha_image", "image.generate", "创建图片服务甲")
    _register(registry, "zeta_image", "image.generate", "创建图片服务乙")
    capability = _capability("图像创作", "根据描述产生一张新图片")
    capability_query = "图像创作 根据描述产生一张新图片"
    provider = _ViewProvider(
        _view_payload(
            rewritten_intent="创作一张新图片",
            capabilities=[capability],
        )
    )
    retriever = _Retriever(
        {
            "给我画一张晚霞": (
                _MatchSpec(
                    "alpha_image", 1, 3, 1, ("rrf", "exact_operation")
                ),
                _MatchSpec("zeta_image", 2, 1, 2),
            ),
            capability_query: (
                _MatchSpec("zeta_image", None, None, 1),
            ),
        }
    )

    advice = await _advisor(registry, retriever, provider).advise(
        _request(registry, "给我画一张晚霞")
    )

    candidate = advice.goals[0].candidates[0]
    assert candidate.tool_name == "alpha_image"
    assert candidate.operation_id == "image.generate"
    assert candidate.lexical_rank == 1
    assert candidate.dense_rank == 1
    assert candidate.llm_view_rank == 1
    assert set(candidate.reason_codes) >= {
        "original_lexical",
        "original_dense",
        "llm_hypothetical",
        "deterministic_provider",
    }


@pytest.mark.asyncio
async def test_repeated_llm_views_contribute_only_one_peak_rank_per_operation() -> None:
    registry = _registry()
    first = _capability("事实查询", "获得一个事实结果")
    second = _capability("补充查询", "获得补充事实结果")
    first_query = "事实查询 获得一个事实结果"
    second_query = "补充查询 获得补充事实结果"
    provider = _ViewProvider(
        _view_payload(
            rewritten_intent="查询当前天气事实",
            capabilities=[first, second],
        )
    )
    retriever = _Retriever(
        {
            "帮我查一下": (
                _MatchSpec("weather_query", 1, 1, 1),
            ),
            "查询当前天气事实": (
                _MatchSpec("weather_query", None, None, 1),
                _MatchSpec("web_search", None, None, 2),
            ),
            first_query: (_MatchSpec("web_search", None, None, 1),),
            second_query: (_MatchSpec("web_search", None, None, 1),),
        }
    )

    advice = await _advisor(registry, retriever, provider).advise(
        _request(registry, "帮我查一下")
    )

    candidates = advice.goals[0].candidates
    assert [candidate.operation_id for candidate in candidates[:2]] == [
        "weather.query",
        "web.search",
    ]
    assert candidates[1].llm_view_rank == 1


@pytest.mark.asyncio
async def test_original_message_is_retrieved_once_and_never_replaced() -> None:
    registry = _registry()
    message = "查询杭州天气"
    capability = _capability("天气信息查询", "获得指定城市的天气情况")
    capability_query = "天气信息查询 获得指定城市的天气情况"
    provider = _ViewProvider(
        _view_payload(
            rewritten_intent=message,
            capabilities=[capability],
        )
    )
    retriever = _Retriever(
        {
            message: (_MatchSpec("weather_query", 1, 1, 1),),
            capability_query: (_MatchSpec("weather_query", None, None, 1),),
        }
    )

    advice = await _advisor(registry, retriever, provider).advise(
        _request(registry, message)
    )

    assert advice.status == "resolved"
    assert retriever.calls.count(message) == 1
    assert retriever.calls[0] == message
    assert retriever.calls == [message, capability_query]


@pytest.mark.asyncio
async def test_goal_specific_view_outweighs_cross_goal_original_message_noise() -> None:
    registry = ToolRegistry()
    _register(registry, "file_read", "file.read", "读取指定文本文件")
    _register(registry, "file_write", "file.write", "写入指定文本文件")
    capability = _capability("文件读取", "只读获取指定文本文件内容")
    capability_query = "文件读取 只读获取指定文本文件内容"
    provider = _ViewProvider(
        _view_payload(
            rewritten_intent="读取项目 README 文件内容",
            capabilities=[capability],
        )
    )
    retriever = _Retriever(
        {
            "读取 README，总结后保存到 notes.md": (
                _MatchSpec("file_write", 1, 1, 1),
                _MatchSpec("file_read", 2, 2, 2),
            ),
            "读取项目 README 文件内容": (
                _MatchSpec("file_read", None, None, 1),
                _MatchSpec("file_write", None, None, 3),
            ),
            capability_query: (
                _MatchSpec("file_read", None, None, 1),
                _MatchSpec("file_write", None, None, 3),
            ),
        }
    )

    advice = await _advisor(registry, retriever, provider).advise(
        _request(registry, "读取 README，总结后保存到 notes.md")
    )

    assert advice.goals[0].candidates[0].operation_id == "file.read"


@pytest.mark.asyncio
async def test_derived_intent_rank_beats_multi_channel_raw_message_noise() -> None:
    registry = ToolRegistry()
    _register(registry, "weather_query", "weather.query", "查询城市天气预报")
    _register(registry, "schedule_create", "schedule.create", "创建日程提醒")
    provider = _ViewProvider(
        _view_payload(
            rewritten_intent="查询周六天气判断降雨是否影响烧烤",
            capabilities=[_capability("天气查询", "查询指定日期的降雨概率")],
        )
    )
    retriever = _Retriever(
        {
            "周六准备烧烤，看看会不会被雨打乱": (
                _MatchSpec("schedule_create", 1, 1, 1),
                _MatchSpec("weather_query", None, 2, 2),
            ),
            "查询周六天气判断降雨是否影响烧烤": (
                _MatchSpec("weather_query", 1, 1, 1),
                _MatchSpec("schedule_create", 2, 2, 2),
            ),
            "天气查询 查询指定日期的降雨概率": (
                _MatchSpec("weather_query", 1, 1, 1),
                _MatchSpec("schedule_create", 2, 2, 2),
            ),
        }
    )

    advice = await _advisor(registry, retriever, provider).advise(
        _request(registry, "周六准备烧烤，看看会不会被雨打乱")
    )

    assert advice.status == "resolved"
    assert advice.goals[0].candidates[0].operation_id == "weather.query"


@pytest.mark.asyncio
async def test_equivalent_provider_selection_is_stable_across_registration_order() -> None:
    payload = _view_payload(
        rewritten_intent="根据文字描述创作图片",
        capabilities=[_capability("图像创作", "产生一张新的图片")],
    )
    selected: list[str] = []
    for names in (("z_cloud", "a_local"), ("a_local", "z_cloud")):
        registry = ToolRegistry()
        for name in names:
            _register(registry, name, "image.generate", "根据描述生成图片")
        provider = _ViewProvider(payload)
        retriever = _Retriever(
            {
                "根据文字描述创作图片": (
                    _MatchSpec("z_cloud", None, None, 1),
                )
            }
        )

        advice = await _advisor(registry, retriever, provider).advise(
            _request(registry, "请画一张图片")
        )
        selected.append(advice.goals[0].candidates[0].tool_name)

    assert selected == ["a_local", "a_local"]


@pytest.mark.asyncio
async def test_llm_no_tool_cannot_suppress_strong_original_evidence() -> None:
    registry = _registry()
    provider = _ViewProvider(_tool_free_payload())
    retriever = _Retriever(
        {
            "查询杭州天气": (
                _MatchSpec(
                    "weather_query",
                    1,
                    1,
                    1,
                    ("rrf", "exact_operation"),
                ),
            )
        }
    )

    advice = await _advisor(registry, retriever, provider).advise(
        _request(registry, "查询杭州天气")
    )

    assert advice.status == "ambiguous"
    assert advice.reason_codes[0] == "intent_view_raw_conflict"
    assert advice.goals[0].candidates[0].operation_id == "weather.query"
    assert advice.uncertainties[0].kind == "operation_conflict"
    assert advice.uncertainties[0].alternatives == ("no_tool", "weather.query")
    assert retriever.calls == ["查询杭州天气"]


@pytest.mark.asyncio
async def test_llm_no_tool_with_only_weak_raw_evidence_routes_to_chat() -> None:
    registry = _registry()
    provider = _ViewProvider(_tool_free_payload())
    retriever = _Retriever(
        {
            "你好": (
                _MatchSpec("web_search", None, 1, 1, ("rrf", "dense")),
            )
        }
    )

    advice = await _advisor(registry, retriever, provider).advise(
        _request(registry, "你好")
    )

    assert advice.status == "no_tool"
    assert advice.goals == ()
    assert advice.reason_codes[0] == "intent_view_no_tool"


@pytest.mark.asyncio
async def test_required_intent_without_real_candidate_is_unavailable() -> None:
    registry = _registry()
    provider = _ViewProvider(
        _view_payload(
            rewritten_intent="使用不存在的外部能力完成任务",
            capabilities=[_capability("未知外部能力", "完成目录中不存在的动作")],
        )
    )
    retriever = _Retriever({})

    advice = await _advisor(registry, retriever, provider).advise(
        _request(registry, "替我完成一个当前系统不支持的动作")
    )

    assert advice.status == "unavailable"
    assert advice.reason_codes[0] == "required_goal_has_no_candidate"
    assert advice.goals[0].candidates == ()


@pytest.mark.asyncio
async def test_desired_output_kind_rejects_semantically_wrong_operation() -> None:
    registry = ToolRegistry()
    _register(
        registry,
        "image_edit",
        "image.edit",
        "编辑已有图片",
        output_kinds=("image",),
    )
    capability = _capability("视频变速", "将视频处理为慢动作文件")
    capability_query = "视频变速 将视频处理为慢动作文件"
    provider = _ViewProvider(
        _view_payload(
            rewritten_intent="把已有视频转换为四倍慢动作",
            capabilities=[capability],
            desired_outputs=["file"],
        )
    )
    retriever = _Retriever(
        {
            capability_query: (
                _MatchSpec("image_edit", None, 1, 1),
            )
        }
    )

    advice = await _advisor(registry, retriever, provider).advise(
        _request(registry, "把这个视频转成四倍慢动作")
    )

    assert advice.status == "unavailable"
    assert advice.goals[0].candidates == ()


@pytest.mark.asyncio
async def test_secondary_hypothetical_match_without_original_support_is_rejected() -> None:
    registry = ToolRegistry()
    _register(
        registry,
        "file_write",
        "file.write",
        "把文本写入指定文件",
        output_kinds=("file", "text"),
    )
    capability = _capability("文件恢复", "恢复永久删除的本地文件")
    capability_query = "文件恢复 恢复永久删除的本地文件"
    provider = _ViewProvider(
        _view_payload(
            rewritten_intent="恢复永久删除的本地文件",
            capabilities=[capability],
            desired_outputs=["file"],
        )
    )
    retriever = _Retriever(
        {
            capability_query: (
                _MatchSpec("file_write", None, 2, 2),
            )
        }
    )

    advice = await _advisor(registry, retriever, provider).advise(
        _request(registry, "把昨天永久删除的文件恢复回来")
    )

    assert advice.status == "unavailable"
    assert advice.goals[0].candidates == ()


@pytest.mark.asyncio
async def test_llm_derived_retrieval_queries_are_character_bounded() -> None:
    registry = _registry()
    capability = "能" * 80
    required_inputs = [str(index) + "入" * 78 for index in range(8)]
    provider = _ViewProvider(
        _view_payload(
            rewritten_intent="改" * 800,
            capabilities=[capability],
            required_inputs=required_inputs,
        )
    )
    retriever = _Retriever({})

    await _advisor(registry, retriever, provider).advise(
        _request(registry, "执行一个复杂目标")
    )

    assert retriever.calls[0] == "执行一个复杂目标"
    assert all(len(query) <= 96 for query in retriever.calls[1:])


@pytest.mark.asyncio
async def test_protocol_control_bypasses_intent_view_and_retrieval() -> None:
    registry = _registry()
    provider = _ViewProvider(_tool_free_payload())
    retriever = _Retriever({})
    snapshot = build_discovery_snapshot(registry, "test-runtime")
    request = RouteRequest(
        turn_id="turn-protocol",
        capability_snapshot_id=snapshot.snapshot_id,
        message="/status",
    )

    advice = await _advisor(registry, retriever, provider).advise(request)

    assert advice.status == "no_tool"
    assert advice.decision_band == "not_applicable"
    assert provider.calls == []
    assert retriever.calls == []


def test_discovery_digest_changes_when_static_catalog_changes() -> None:
    registry = _registry()
    before = build_discovery_snapshot(registry, "test-runtime")
    _register(registry, "schedule_create", "schedule.create", "创建定时提醒")
    after = build_discovery_snapshot(registry, "test-runtime")

    assert before.snapshot_id != after.snapshot_id
