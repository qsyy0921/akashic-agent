from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest

from agent.provider import LLMProvider, LLMResponse, ToolCall
from agent.model_runtime.errors import RetryableTransportError, TransportError
from agent.routing.contracts import RouteContext, RouteContextMessage
from agent.routing.intent_view import (
    INTENT_VIEW_PROMPT_VERSION,
    IntentViewAnalyzer,
    IntentViewInvalidError,
    IntentViewUnavailableError,
    LLMIntentViewProvider,
)


def _context() -> RouteContext:
    return RouteContext(
        messages=(
            RouteContextMessage(
                role="assistant",
                content="已经生成上一张西湖晚霞图片。",
                operation_ids=("image.generate",),
                output_kinds=("image",),
            ),
        ),
        reply_excerpt="上一张西湖晚霞图片",
        previous_operation_ids=("image.generate",),
    )


def _image_payload() -> dict[str, object]:
    return _wire_payload(
        _goal_slot(
            enabled=True,
            statement="再次生成颜色更紫的西湖晚霞图片",
            rewritten_intent="基于上一轮主题生成紫色更明显的西湖晚霞图片",
            capability_queries=["根据主题和颜色约束生成一张新图片"],
            required_inputs=["画面主题", "颜色要求"],
            desired_outputs=["image"],
        )
    )


def _goal_slot(
    *,
    enabled: bool,
    statement: str = "",
    rewritten_intent: str = "",
    capability_queries: list[str] | None = None,
    required_inputs: list[str] | None = None,
    desired_outputs: list[str] | None = None,
    relation: str = "independent",
    depends_on_goal_numbers: list[int] | None = None,
) -> dict[str, object]:
    return {
        "enabled": enabled,
        "statement": statement,
        "rewritten_intent": rewritten_intent,
        "capability_queries": capability_queries or [],
        "alternative_capability_queries": [],
        "required_inputs": required_inputs or [],
        "desired_outputs": desired_outputs or [],
        "missing_required_inputs": [],
        "relation": relation,
        "depends_on_goal_numbers": depends_on_goal_numbers or [],
    }


def _wire_payload(
    first_goal: dict[str, object],
    *,
    tool_requirement: str = "required",
) -> dict[str, object]:
    return {
        "schema_version": "3",
        "goal_1": first_goal,
        "goal_2": _goal_slot(enabled=False),
        "goal_3": _goal_slot(enabled=False),
        "goal_4": _goal_slot(enabled=False),
        "unresolved_references": [],
        "tool_requirement": tool_requirement,
    }


class _IntentProvider:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, RouteContext]] = []

    async def emit_intent_view(
        self,
        message: str,
        context: RouteContext,
    ) -> object:
        self.calls.append((message, context))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _ChatProvider:
    def __init__(self, response: LLMResponse, *, delay: float = 0.0) -> None:
        self.response = response
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.response


class _SequencedChatProvider:
    def __init__(self, results: list[LLMResponse | Exception]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.asyncio
async def test_analyzer_calls_provider_exactly_once_and_parses_view() -> None:
    provider = _IntentProvider(_image_payload())
    analyzer = IntentViewAnalyzer(provider)

    result = await analyzer.analyze("再来一张，颜色更紫", _context())

    assert result.provider_calls == 1
    assert result.prompt_version == INTENT_VIEW_PROMPT_VERSION
    assert len(provider.calls) == 1
    assert result.view.goals[0].rewritten_intent.startswith("基于上一轮")
    assert (
        result.view.goals[0].hypothetical_capabilities[0].name
        == "根据主题和颜色约束生成一张新图片"
    )


@pytest.mark.asyncio
async def test_invalid_payload_fails_once_without_repair_call() -> None:
    payload = _image_payload()
    cast(dict[str, object], payload["goal_1"])["tool_name"] = "image_generate_cloud"
    provider = _IntentProvider(payload)

    with pytest.raises(IntentViewInvalidError):
        await IntentViewAnalyzer(provider).analyze("再来一张", _context())

    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_required_view_rejects_missing_capability_and_bad_graph() -> None:
    missing_capability = _image_payload()
    first_goal = cast(dict[str, object], missing_capability["goal_1"])
    first_goal["capability_queries"] = []
    with pytest.raises(IntentViewInvalidError):
        await IntentViewAnalyzer(_IntentProvider(missing_capability)).analyze(
            "再来一张", _context()
        )

    cyclic = _image_payload()
    first = cast(dict[str, object], cyclic["goal_1"])
    first["relation"] = "after"
    first["depends_on_goal_numbers"] = [2]
    cyclic["goal_2"] = _goal_slot(
        enabled=True,
        statement="第二个目标",
        rewritten_intent="执行第二个目标",
        capability_queries=["读取完成目标所需的数据"],
        desired_outputs=["data"],
        relation="after",
        depends_on_goal_numbers=[1],
    )
    with pytest.raises(IntentViewInvalidError):
        await IntentViewAnalyzer(_IntentProvider(cyclic)).analyze(
            "执行两个相互依赖目标", _context()
        )


@pytest.mark.asyncio
async def test_wire_missing_required_inputs_are_projected_into_internal_contract() -> None:
    payload = _image_payload()
    goal = cast(dict[str, object], payload["goal_1"])
    goal["missing_required_inputs"] = ["不存在的输入"]

    result = await IntentViewAnalyzer(_IntentProvider(payload)).analyze(
        "再来一张",
        _context(),
    )

    capability = result.view.goals[0].hypothetical_capabilities[0]
    assert capability.required_inputs == (
        "画面主题",
        "颜色要求",
        "不存在的输入",
    )
    assert capability.missing_required_inputs == ("不存在的输入",)


@pytest.mark.asyncio
async def test_unknown_wire_field_still_fails_closed() -> None:
    payload = _image_payload()
    goal = cast(dict[str, object], payload["goal_1"])
    goal["goal_1_placeholder"] = "不要猜测"

    with pytest.raises(IntentViewInvalidError):
        await IntentViewAnalyzer(_IntentProvider(payload)).analyze(
            "再来一张",
            _context(),
        )


@pytest.mark.asyncio
async def test_tool_free_view_has_goal_but_no_hypothetical_capability() -> None:
    payload = _wire_payload(
        _goal_slot(
            enabled=True,
            statement="解释递归",
            rewritten_intent="用通俗语言解释递归概念",
        ),
        tool_requirement="none",
    )

    result = await IntentViewAnalyzer(_IntentProvider(payload)).analyze(
        "解释一下递归", RouteContext()
    )

    assert result.view.tool_requirement == "none"


@pytest.mark.asyncio
async def test_mixed_view_projects_independent_non_routing_goal_out() -> None:
    payload = _wire_payload(
        _goal_slot(
            enabled=True,
            statement="取消尚未发送的旧消息",
            rewritten_intent="不再执行上一轮尚未发送的消息",
        )
    )
    payload["goal_2"] = _goal_slot(
        enabled=True,
        statement="创建个人提醒",
        rewritten_intent="明早八点提醒用户交周报",
        capability_queries=["在明确时间创建个人提醒"],
        required_inputs=["提醒时间", "提醒内容"],
        desired_outputs=["text"],
    )

    result = await IntentViewAnalyzer(_IntentProvider(payload)).analyze(
        "不用发给他了，改成明早八点提醒我",
        RouteContext(),
    )

    assert [goal.goal_id for goal in result.view.goals] == ["goal-1"]
    assert result.view.goals[0].statement == "创建个人提醒"


@pytest.mark.asyncio
async def test_routing_goal_cannot_depend_on_projected_non_routing_goal() -> None:
    payload = _wire_payload(
        _goal_slot(
            enabled=True,
            statement="先解释背景",
            rewritten_intent="向用户解释背景",
        )
    )
    payload["goal_2"] = _goal_slot(
        enabled=True,
        statement="创建提醒",
        rewritten_intent="解释后创建提醒",
        capability_queries=["创建个人提醒"],
        desired_outputs=["text"],
        relation="after",
        depends_on_goal_numbers=[1],
    )

    with pytest.raises(IntentViewInvalidError, match="non-routing goal"):
        await IntentViewAnalyzer(_IntentProvider(payload)).analyze(
            "解释后提醒我",
            RouteContext(),
        )


@pytest.mark.asyncio
async def test_first_goal_self_after_is_projected_only_with_prior_route_evidence() -> None:
    payload = _wire_payload(
        _goal_slot(
            enabled=True,
            statement="保存上一轮摘要",
            rewritten_intent="把上一轮摘要保存到 summary.md",
            capability_queries=["把已有文本保存到指定文件"],
            desired_outputs=["file"],
            relation="after",
            depends_on_goal_numbers=[1],
        )
    )

    result = await IntentViewAnalyzer(_IntentProvider(payload)).analyze(
        "把刚才的摘要保存到 summary.md",
        _context(),
    )

    assert result.view.goals[0].relation == "independent"
    assert result.view.goals[0].depends_on == ()

    with pytest.raises(IntentViewInvalidError, match="earlier goals"):
        await IntentViewAnalyzer(_IntentProvider(payload)).analyze(
            "保存摘要",
            RouteContext(),
        )


@pytest.mark.asyncio
async def test_registered_tool_identifier_is_rejected_from_llm_language() -> None:
    payload = _image_payload()
    goal = cast(dict[str, object], payload["goal_1"])
    goal["rewritten_intent"] = "调用 image_generate_cloud 生成图片"

    with pytest.raises(IntentViewInvalidError, match="registered tool"):
        await IntentViewAnalyzer(_IntentProvider(payload)).analyze(
            "生成图片",
            RouteContext(),
            forbidden_tool_names=frozenset({"image_generate_cloud"}),
        )


@pytest.mark.asyncio
async def test_provider_failure_is_typed_unavailable_without_alternate_call() -> None:
    provider = _IntentProvider(ConnectionError("offline"))

    with pytest.raises(IntentViewUnavailableError):
        await IntentViewAnalyzer(provider).analyze("生成图片", RouteContext())

    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_llm_adapter_forces_one_strict_call_and_serializes_bounded_context() -> None:
    chat = _ChatProvider(
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call-1",
                    name="emit_intent_view",
                    arguments=cast(dict[str, Any], _image_payload()),
                )
            ],
        )
    )
    provider = LLMIntentViewProvider(
        cast(LLMProvider, chat),
        model="deepseek-chat",
    )

    result = await provider.emit_intent_view("再来一张，颜色更紫", _context())

    assert result == _image_payload()
    assert len(chat.calls) == 1
    request = chat.calls[0]
    assert request["disable_thinking"] is False
    assert request.get("extra_body") is None
    assert request["tool_choice"] == {
        "type": "function",
        "function": {"name": "emit_intent_view"},
    }
    assert request["tools"][0]["function"]["strict"] is True
    user_payload = json.loads(request["messages"][1]["content"])
    assert user_payload["current_message"] == "再来一张，颜色更紫"
    assert user_payload["route_context"]["previous_operation_ids"] == [
        "image.generate"
    ]
    assert "tool_chain" not in request["messages"][1]["content"]


@pytest.mark.asyncio
async def test_llm_adapter_timeout_fails_explicitly() -> None:
    chat = _ChatProvider(LLMResponse(content=None), delay=0.05)
    provider = LLMIntentViewProvider(
        cast(LLMProvider, chat),
        model="deepseek-chat",
        timeout_seconds=0.01,
    )

    with pytest.raises(IntentViewUnavailableError, match="timed out"):
        await provider.emit_intent_view("生成图片", RouteContext())

    assert len(chat.calls) == 1


@pytest.mark.asyncio
async def test_llm_adapter_retries_one_retryable_transport_failure() -> None:
    response = LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(
                id="call-1",
                name="emit_intent_view",
                arguments=cast(dict[str, Any], _image_payload()),
            )
        ],
    )
    chat = _SequencedChatProvider(
        [RetryableTransportError("temporary"), response]
    )
    provider = LLMIntentViewProvider(
        cast(LLMProvider, chat),
        model="deepseek-chat",
    )

    result = await IntentViewAnalyzer(provider).analyze(
        "生成图片",
        RouteContext(),
    )

    assert result.provider_calls == 1
    assert len(chat.calls) == 2


@pytest.mark.asyncio
async def test_llm_adapter_bounds_retry_and_rejects_non_retryable_errors() -> None:
    retryable = _SequencedChatProvider(
        [
            RetryableTransportError("temporary-1"),
            RetryableTransportError("temporary-2"),
        ]
    )
    retryable_provider = LLMIntentViewProvider(
        cast(LLMProvider, retryable),
        model="deepseek-chat",
    )
    with pytest.raises(IntentViewUnavailableError):
        await IntentViewAnalyzer(retryable_provider).analyze(
            "生成图片",
            RouteContext(),
        )
    assert len(retryable.calls) == 2

    non_retryable = _SequencedChatProvider([TransportError("invalid")])
    non_retryable_provider = LLMIntentViewProvider(
        cast(LLMProvider, non_retryable),
        model="deepseek-chat",
    )
    with pytest.raises(IntentViewUnavailableError):
        await IntentViewAnalyzer(non_retryable_provider).analyze(
            "生成图片",
            RouteContext(),
        )
    assert len(non_retryable.calls) == 1


@pytest.mark.asyncio
async def test_llm_adapter_rejects_plain_text_or_multiple_calls() -> None:
    plain = LLMIntentViewProvider(
        cast(LLMProvider, _ChatProvider(LLMResponse(content="plain"))),
        model="deepseek-chat",
    )
    with pytest.raises(IntentViewInvalidError, match="exactly one"):
        await plain.emit_intent_view("生成图片", RouteContext())

    multiple = _ChatProvider(
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCall("call-1", "emit_intent_view", {}),
                ToolCall("call-2", "emit_intent_view", {}),
            ],
        )
    )
    provider = LLMIntentViewProvider(
        cast(LLMProvider, multiple),
        model="deepseek-chat",
    )
    with pytest.raises(IntentViewInvalidError, match="exactly one"):
        await provider.emit_intent_view("生成图片", RouteContext())

    assert len(multiple.calls) == 1
