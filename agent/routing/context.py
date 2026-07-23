from __future__ import annotations

import mimetypes
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from agent.routing.contracts import (
    ContextRole,
    OutputKind,
    RouteContext,
    RouteContextMessage,
)

_MAX_MESSAGES = 8
_MAX_MESSAGE_CHARS = 1_000
_MAX_TOTAL_CHARS = 6_000
_MAX_REPLY_CHARS = 800

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{24,}\b"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|token|password|secret)\b"
        r"\s*[:=]\s*['\"]?[^\s'\"]{8,}"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*"),
)
_REPLY_PATTERN = re.compile(
    r"\A【你正在回复一条历史消息】\s*\n"
    r"被回复消息（来自 [^\n]*）：\s*\n"
    r"(?P<reply>.*?)\n\n【你当前新消息】",
    re.DOTALL,
)

ToolDescriptorResolver = Callable[[str], object | None]


class RouteContextBuilder:
    """Build a bounded routing-only view from committed session facts."""

    def build(
        self,
        *,
        history: Sequence[Mapping[str, object]],
        current_content: str,
        current_media: Sequence[str],
        message_metadata: Mapping[str, object],
        session_metadata: Mapping[str, object],
        descriptor_resolver: ToolDescriptorResolver,
    ) -> RouteContext:
        messages = self._history_messages(history, descriptor_resolver)
        reply_excerpt = self._reply_excerpt(
            current_content=current_content,
            message_metadata=message_metadata,
        )
        previous_operation_ids = _unique_limited(
            operation_id
            for message in messages
            for operation_id in message.operation_ids
        )
        pending_clarifications = _pending_clarifications(
            message_metadata,
            session_metadata,
        )
        return RouteContext(
            messages=messages,
            reply_excerpt=reply_excerpt,
            attachment_kinds=_media_output_kinds(current_media),
            previous_operation_ids=previous_operation_ids,
            pending_clarifications=pending_clarifications,
        )

    def _history_messages(
        self,
        history: Sequence[Mapping[str, object]],
        descriptor_resolver: ToolDescriptorResolver,
    ) -> tuple[RouteContextMessage, ...]:
        selected: list[RouteContextMessage] = []
        remaining_chars = _MAX_TOTAL_CHARS
        for raw in reversed(history):
            if len(selected) >= _MAX_MESSAGES or remaining_chars <= 0:
                break
            raw_role = raw.get("role")
            if raw_role == "user":
                role: ContextRole = "user"
            elif raw_role == "assistant":
                role = "assistant"
            else:
                continue
            content = _sanitize_text(raw.get("content"), _MAX_MESSAGE_CHARS)
            if not content:
                continue
            content = content[:remaining_chars].rstrip()
            if not content:
                continue
            operation_ids, descriptor_outputs = _tool_facts(
                raw.get("tools_used"), descriptor_resolver
            )
            output_kinds = _merge_output_kinds(
                descriptor_outputs,
                _media_output_kinds(_string_sequence(raw.get("media"))),
            )
            selected.append(
                RouteContextMessage(
                    role=role,
                    content=content,
                    operation_ids=operation_ids,
                    output_kinds=output_kinds,
                )
            )
            remaining_chars -= len(content)
        selected.reverse()
        return tuple(selected)

    @staticmethod
    def _reply_excerpt(
        *,
        current_content: str,
        message_metadata: Mapping[str, object],
    ) -> str | None:
        explicit = _sanitize_text(
            message_metadata.get("reply_excerpt"), _MAX_REPLY_CHARS
        )
        if explicit:
            return explicit
        if not message_metadata.get("reply_to_message_id"):
            return None
        match = _REPLY_PATTERN.search(current_content)
        if match is None:
            return None
        return _sanitize_text(match.group("reply"), _MAX_REPLY_CHARS) or None


def _tool_facts(
    raw_tools: object,
    descriptor_resolver: ToolDescriptorResolver,
) -> tuple[tuple[str, ...], tuple[OutputKind, ...]]:
    operation_ids: list[str] = []
    output_kinds: list[OutputKind] = []
    for tool_name in _string_sequence(raw_tools):
        descriptor = descriptor_resolver(tool_name)
        if descriptor is None:
            continue
        operation_id = getattr(descriptor, "operation_id", None)
        if isinstance(operation_id, str) and operation_id:
            operation_ids.append(operation_id)
        raw_outputs = getattr(descriptor, "output_kinds", ())
        for output_kind in _string_sequence(raw_outputs):
            if output_kind in {"text", "image", "file", "data", "mixed"}:
                output_kinds.append(output_kind)  # type: ignore[arg-type]
    return _unique_limited(operation_ids, maximum=4), _unique_output_kinds(
        output_kinds
    )


def _pending_clarifications(
    message_metadata: Mapping[str, object],
    session_metadata: Mapping[str, object],
) -> tuple[str, ...]:
    values = [
        *_string_sequence(session_metadata.get("pending_route_clarifications")),
        *_string_sequence(message_metadata.get("pending_route_clarifications")),
    ]
    sanitized = (
        text
        for item in values
        if (text := _sanitize_text(item, 400))
    )
    return _unique_limited(sanitized, maximum=4)


def _media_output_kinds(paths: Sequence[str]) -> tuple[OutputKind, ...]:
    kinds: list[OutputKind] = []
    for raw_path in paths:
        path = Path(raw_path)
        mime, _ = mimetypes.guess_type(path.name)
        kinds.append("image" if mime and mime.startswith("image/") else "file")
    return _unique_output_kinds(kinds)


def _merge_output_kinds(
    first: tuple[OutputKind, ...],
    second: tuple[OutputKind, ...],
) -> tuple[OutputKind, ...]:
    return _unique_output_kinds([*first, *second])


def _unique_output_kinds(values: Sequence[OutputKind]) -> tuple[OutputKind, ...]:
    return tuple(dict.fromkeys(values))


def _unique_limited(
    values: object,
    *,
    maximum: int = 8,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        iterable = (str(values),)
    else:
        try:
            iterable = iter(values)  # type: ignore[arg-type]
        except TypeError:
            return ()
    result: list[str] = []
    seen: set[str] = set()
    for raw in iterable:
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) >= maximum:
            break
    return tuple(result)


def _string_sequence(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _sanitize_text(value: object, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(
        character
        for character in value
        if ord(character) >= 32 or character in "\n\t"
    ).strip()
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    if len(text) > maximum:
        text = text[:maximum].rstrip()
    return text


__all__ = ["RouteContextBuilder", "ToolDescriptorResolver"]
