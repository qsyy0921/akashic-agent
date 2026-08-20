from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, cast

_NAME_DISPLAY_LIMIT = 12
_NAME_CHAR_LIMIT = 64
_KNOWN_STATUSES = frozenset({"success", "denied", "error", "blocked"})


@dataclass(frozen=True)
class TurnStatusFrame:
    iteration: int
    max_iterations: int
    visible_tool_names: tuple[str, ...] | None
    tool_call_counts: tuple[tuple[str, int], ...]
    last_tool_name: str | None
    last_tool_status: str | None
    unlocked_tool_names: tuple[str, ...]
    required_tool_name: str | None
    disabled_tool_count: int

    @classmethod
    def from_runtime(
        cls,
        *,
        iteration: int,
        max_iterations: int,
        visible_tool_names: set[str] | None,
        tool_chain: Iterable[dict[str, Any]],
        unlocked_tool_names: Iterable[str],
        required_tool_name: str | None,
        disabled_tool_names: set[str],
    ) -> TurnStatusFrame:
        counts: Counter[str] = Counter()
        last_name: str | None = None
        last_status: str | None = None

        for group in tool_chain:
            calls = group.get("calls")
            if not isinstance(calls, list):
                continue
            for raw_call in cast(list[object], calls):
                if not isinstance(raw_call, dict):
                    continue
                call = cast(dict[str, object], raw_call)
                name = _normalize_name(call.get("name"))
                if not name:
                    continue
                counts[name] += 1
                last_name = name
                status = str(call.get("status") or "").strip()
                last_status = status if status in _KNOWN_STATUSES else "unknown"

        ordered_counts = tuple(
            sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        )
        visible = (
            tuple(
                sorted(
                    name
                    for raw_name in visible_tool_names
                    if (name := _normalize_name(raw_name))
                )
            )
            if visible_tool_names is not None
            else None
        )
        unlocked = tuple(
            dict.fromkeys(
                name
                for raw_name in unlocked_tool_names
                if (name := _normalize_name(raw_name))
            )
        )
        return cls(
            iteration=iteration,
            max_iterations=max_iterations,
            visible_tool_names=visible,
            tool_call_counts=ordered_counts,
            last_tool_name=last_name,
            last_tool_status=last_status,
            unlocked_tool_names=unlocked,
            required_tool_name=_normalize_name(required_tool_name) or None,
            disabled_tool_count=len(disabled_tool_names),
        )

    def render(self) -> str:
        current = self.iteration + 1
        if self.max_iterations > 0:
            remaining = max(0, self.max_iterations - current)
            iteration_text = (
                f"{current}/{self.max_iterations}，本轮后剩余 {remaining}"
            )
        else:
            iteration_text = f"{current}/无限制"

        if self.visible_tool_names is None:
            visible_text = "全部已注册工具"
        else:
            visible_text = (
                f"{len(self.visible_tool_names)} 个"
                f"（{_render_names(self.visible_tool_names)}）"
            )

        total_calls = sum(count for _, count in self.tool_call_counts)
        call_text = _render_counts(self.tool_call_counts)
        if self.last_tool_name is None:
            last_text = "无"
        else:
            last_text = f"{self.last_tool_name} [{self.last_tool_status}]"

        required = self.required_tool_name or "无"
        unlocked = _render_names(self.unlocked_tool_names)
        return "\n".join(
            [
                "当前 Agent 运行状态（由核心代码维护，模型和插件不可改写）：",
                f"- 迭代：{iteration_text}",
                f"- 可见工具：{visible_text}",
                f"- 工具执行：{total_calls} 次；计数：{call_text}",
                f"- 最近工具结果：{last_text}",
                f"- 已解锁工具：{unlocked}",
                f"- 首轮强制工具：{required}；禁用工具：{self.disabled_tool_count} 个",
                "- 完成判定：只有真实返回且状态为 success 的工具结果才算成功；"
                "denied、error、blocked、unknown 均不得宣称完成。",
            ]
        )


def _render_names(names: Iterable[str]) -> str:
    ordered = tuple(names)
    if not ordered:
        return "无"
    shown = ordered[:_NAME_DISPLAY_LIMIT]
    suffix = (
        f"，另有 {len(ordered) - len(shown)} 个"
        if len(ordered) > len(shown)
        else ""
    )
    return "、".join(shown) + suffix


def _render_counts(counts: tuple[tuple[str, int], ...]) -> str:
    if not counts:
        return "无"
    shown = counts[:_NAME_DISPLAY_LIMIT]
    suffix = (
        f"，另有 {len(counts) - len(shown)} 种"
        if len(counts) > len(shown)
        else ""
    )
    return "、".join(f"{name}={count}" for name, count in shown) + suffix


def _normalize_name(value: object) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= _NAME_CHAR_LIMIT:
        return text
    return text[: _NAME_CHAR_LIMIT - 3] + "..."
