from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent.routing.contracts import (
    GoalSpec,
    HypotheticalCapability,
    IntentGoal,
    IntentView,
    RouteContext,
    RouteContextMessage,
    RouteContractError,
    RouteRequest,
)


def _image_capability() -> HypotheticalCapability:
    return HypotheticalCapability(
        name="图片生成",
        description="根据主题和颜色要求生成一张新图片",
        required_inputs=("画面主题", "颜色要求"),
        desired_outputs=("image",),
    )


def _intent_goal(
    goal_id: str = "goal-1",
    *,
    relation: str = "independent",
    depends_on: tuple[str, ...] = (),
) -> IntentGoal:
    return IntentGoal(
        goal_id=goal_id,
        statement="生成颜色更紫的西湖图片",
        rewritten_intent="基于上一轮主题再次生成紫色更明显的西湖图片",
        hypothetical_capabilities=(_image_capability(),),
        relation=relation,  # type: ignore[arg-type]
        depends_on=depends_on,
    )


def test_route_context_is_bounded_and_attaches_to_request() -> None:
    context = RouteContext(
        messages=(
            RouteContextMessage(
                role="assistant",
                content="已经生成上一张西湖晚霞图片。",
                operation_ids=("image.generate",),
                output_kinds=("image",),
            ),
        ),
        reply_excerpt="已经生成上一张西湖晚霞图片。",
        attachment_kinds=("image",),
        previous_operation_ids=("image.generate",),
        pending_clarifications=("确认是否沿用上一轮主题",),
    )

    request = RouteRequest(
        turn_id="turn-1",
        capability_snapshot_id="capability-v1:test",
        message="再来一张，颜色更紫一点",
        context=context,
    )

    assert request.context == context


def test_route_context_rejects_unbounded_text_and_invalid_operations() -> None:
    with pytest.raises(RouteContractError, match="total limit"):
        RouteContext(
            messages=tuple(
                RouteContextMessage(role="user", content=str(index) + "x" * 999)
                for index in range(7)
            )
        )

    with pytest.raises(RouteContractError, match="operation_id"):
        RouteContextMessage(
            role="assistant",
            content="完成",
            operation_ids=("Image Generate",),
        )


def test_intent_view_requires_strict_tool_requirement_consistency() -> None:
    view = IntentView(
        schema_version="3",
        goals=(_intent_goal(),),
        unresolved_references=(),
        tool_requirement="required",
    )

    assert view.goals[0].hypothetical_capabilities[0].retrieval_query == (
        "图片生成 根据主题和颜色要求生成一张新图片 画面主题 颜色要求"
    )

    with pytest.raises(RouteContractError, match="cannot declare"):
        replace(view, tool_requirement="none")

    without_capability = replace(
        view.goals[0], hypothetical_capabilities=()
    )
    with pytest.raises(RouteContractError, match="must declare"):
        replace(view, goals=(without_capability,))


def test_tool_free_intent_view_keeps_a_semantic_goal_without_tools() -> None:
    goal = IntentGoal(
        goal_id="goal-1",
        statement="解释递归",
        rewritten_intent="用通俗语言解释递归概念",
    )
    view = IntentView("3", (goal,), (), "none")

    assert view.tool_requirement == "none"
    assert view.goals[0].hypothetical_capabilities == ()


def test_intent_view_rejects_missing_or_cyclic_dependencies() -> None:
    missing = _intent_goal(
        relation="after",
        depends_on=("goal-missing",),
    )
    with pytest.raises(RouteContractError, match="missing"):
        IntentView("3", (missing,), (), "required")

    first = _intent_goal(
        "goal-1", relation="after", depends_on=("goal-2",)
    )
    second = _intent_goal(
        "goal-2", relation="after", depends_on=("goal-1",)
    )
    with pytest.raises(RouteContractError, match="DAG"):
        IntentView("3", (first, second), (), "required")


def test_v3_dataset_covers_required_categories_and_contract_shape() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "eval"
        / "intent_routing"
        / "v3_dataset.zh.jsonl"
    )
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(rows) >= 24
    assert len({row["id"] for row in rows}) == len(rows)
    assert {row["category"] for row in rows} == {
        "explicit_tool",
        "no_tool",
        "implicit_common_sense",
        "follow_up",
        "intent_shift",
        "fake_shift",
        "multi_intent",
        "ambiguous",
        "infeasible",
        "equivalent_provider",
        "cross_mcp_multi_tool",
        "metadata_bias",
    }

    for row in rows:
        context_payload = row["context"]
        context = RouteContext(
            messages=tuple(
                RouteContextMessage(
                    role=item["role"],
                    content=item["content"],
                    operation_ids=tuple(item["operation_ids"]),
                    output_kinds=tuple(item["output_kinds"]),
                )
                for item in context_payload["messages"]
            ),
            reply_excerpt=context_payload["reply_excerpt"],
            attachment_kinds=tuple(context_payload["attachment_kinds"]),
            previous_operation_ids=tuple(
                context_payload["previous_operation_ids"]
            ),
            pending_clarifications=tuple(
                context_payload["pending_clarifications"]
            ),
        )
        RouteRequest(
            turn_id=row["id"],
            capability_snapshot_id="capability-v1:dataset",
            message=row["utterance"],
            disabled_tools=frozenset(row["disabled_tools"]),
            context=context,
        )
        assert row["expected_status"] in {
            "resolved",
            "no_tool",
            "ambiguous",
            "unavailable",
        }
        assert row["expected_tool_requirement"] in {
            "required",
            "optional",
            "none",
        }
        assert isinstance(row["expected_clarification"], bool)
        goal_ids = {goal["goal_id"] for goal in row["goals"]}
        for goal in row["goals"]:
            GoalSpec(
                goal_id=goal["goal_id"],
                statement=goal["statement"],
                operation_query=goal["operation_query"],
                relation=goal["relation"],
                depends_on=tuple(goal["depends_on"]),
            )
            assert set(goal["depends_on"]).issubset(goal_ids)
            assert goal["expected_operation_ids"]
