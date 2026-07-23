from __future__ import annotations

import re
import sys
from collections import deque

from agent.lifecycle.types import PreToolCtx
from agent.plugins import McpServerSpec, Plugin, on_tool_pre
from agent.tool_hooks.types import HookOutcome

_TOOL_NAME = "mcp_gpt_image__generate_image"
_MAX_TRACKED_TURNS = 512


class TerraImageGenerationPlugin(Plugin):
    name = "terra_imagegen"
    version = "2.0.0"
    desc = "Generate workspace-owned images through the configured Terra Responses runtime."

    def __init__(self) -> None:
        self._consumed_turns: set[str] = set()
        self._turn_order: deque[str] = deque()

    @classmethod
    def mcp_servers(cls) -> list[McpServerSpec]:
        return [
            McpServerSpec(
                name="gpt_image",
                command=(sys.executable, "mcp/server.py"),
                env={
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONUTF8": "1",
                },
                call_timeout_seconds=320.0,
                media_output_roots=("generated_images/terra_imagegen",),
            )
        ]

    @on_tool_pre(tool_name=_TOOL_NAME)
    async def authorize_generation(self, event: PreToolCtx) -> HookOutcome:
        if event.source != "passive":
            return HookOutcome(
                decision="deny",
                reason="图片生成只允许由用户当前被动对话明确触发。",
            )
        if not _is_explicit_image_request(event.request_text):
            return HookOutcome(
                decision="deny",
                reason="当前用户消息没有明确要求生成图片。",
            )
        batch_count = sum(
            1
            for item in event.tool_batch
            if str(item.get("name") or "") == _TOOL_NAME
        )
        if batch_count > 1:
            return HookOutcome(
                decision="deny",
                reason="同一批次只能调用一次生图工具；多图请使用 n 参数。",
            )
        if event.is_preflight:
            return HookOutcome()
        turn_id = event.turn_id.strip()
        if not turn_id:
            return HookOutcome(
                decision="deny",
                reason="图片生成缺少可审计的 turn_id。",
            )
        if turn_id in self._consumed_turns:
            return HookOutcome(
                decision="deny",
                reason="本轮已经执行过图片生成；请勿自动重试或轮询。",
            )
        self._remember_consumed_turn(turn_id)
        return HookOutcome()

    def _remember_consumed_turn(self, turn_id: str) -> None:
        self._consumed_turns.add(turn_id)
        self._turn_order.append(turn_id)
        while len(self._turn_order) > _MAX_TRACKED_TURNS:
            expired = self._turn_order.popleft()
            self._consumed_turns.discard(expired)


def _is_explicit_image_request(text: str) -> bool:
    normalized = " ".join((text or "").casefold().split())
    if not normalized:
        return False
    direct_phrases = (
        "生图",
        "生成图片",
        "生成一张",
        "生成两张",
        "帮我画",
        "给我画",
        "请画",
        "画一张",
        "绘制一张",
        "做一张图",
        "创建图片",
        "再来一张",
        "换一张",
        "重新画",
        "重画",
    )
    if any(phrase in normalized for phrase in direct_phrases):
        return True
    chinese_actions = ("生成", "画", "绘制", "创作", "制作", "创建")
    chinese_objects = ("图", "图片", "照片", "海报", "头像", "插画", "壁纸", "封面")
    if any(action in normalized for action in chinese_actions) and any(
        target in normalized for target in chinese_objects
    ):
        return True
    return re.search(
        r"\b(generate|create|draw|paint|make)\b.*"
        r"\b(image|picture|photo|illustration|poster|avatar|wallpaper)\b",
        normalized,
    ) is not None
