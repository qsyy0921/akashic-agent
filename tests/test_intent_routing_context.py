from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agent.routing.context import RouteContextBuilder
from agent.tools.base import Tool


class _ImageTool(Tool):
    name = "image_generate"
    description = "根据文字描述生成图片"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "generated"


def test_context_builder_uses_committed_facts_without_tool_results_or_secrets() -> None:
    descriptor = SimpleNamespace(
        operation_id="image.generate",
        output_kinds=("image",),
    )
    history: list[dict[str, object]] = [
        {"role": "user", "content": f"旧消息 {index}"}
        for index in range(8)
    ]
    history.extend(
        [
            {
                "role": "assistant",
                "content": "图片已生成，api_key=abcdefgh12345678",
                "tools_used": ["image_generate", "unknown_tool"],
                "tool_chain": [
                    {
                        "calls": [
                            {
                                "name": "image_generate",
                                "result": "FULL SECRET TOOL OUTPUT",
                            }
                        ]
                    }
                ],
                "media": ["result.png"],
            },
            {
                "role": "tool",
                "content": "THIS TOOL RESULT MUST NOT ENTER ROUTE CONTEXT",
            },
        ]
    )

    context = RouteContextBuilder().build(
        history=history,
        current_content=(
            "【你正在回复一条历史消息】\n"
            "被回复消息（来自 @user）：\n"
            "上一张西湖晚霞图\n\n"
            "【你当前新消息】\n颜色更紫一点"
        ),
        current_media=("reference.jpg", "brief.pdf"),
        message_metadata={"reply_to_message_id": 42},
        session_metadata={
            "pending_route_clarifications": ["是否沿用上一轮主题"]
        },
        descriptor_resolver=lambda name: (
            descriptor if name == "image_generate" else None
        ),
    )

    assert len(context.messages) == 8
    assert context.messages[-1].operation_ids == ("image.generate",)
    assert context.messages[-1].output_kinds == ("image",)
    assert "[REDACTED]" in context.messages[-1].content
    assert all("FULL SECRET TOOL OUTPUT" not in item.content for item in context.messages)
    assert all("THIS TOOL RESULT" not in item.content for item in context.messages)
    assert context.reply_excerpt == "上一张西湖晚霞图"
    assert context.attachment_kinds == ("image", "file")
    assert context.previous_operation_ids == ("image.generate",)
    assert context.pending_clarifications == ("是否沿用上一轮主题",)


def test_context_builder_enforces_total_budget_and_deterministic_recency() -> None:
    history = [
        {"role": "user", "content": f"{index}:" + "x" * 998}
        for index in range(12)
    ]

    context = RouteContextBuilder().build(
        history=history,
        current_content="继续",
        current_media=(),
        message_metadata={},
        session_metadata={},
        descriptor_resolver=lambda _: None,
    )

    assert len(context.messages) == 6
    assert sum(len(item.content) for item in context.messages) == 6_000
    assert context.messages[0].content.startswith("6:")
    assert context.messages[-1].content.startswith("11:")

