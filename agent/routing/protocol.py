from __future__ import annotations

import re

_CONTROL_COMMANDS = frozenset(
    {
        "批准",
        "拒绝",
        "取消",
        "状态",
        "帮助",
        "approve",
        "reject",
        "cancel",
        "status",
        "help",
    }
)
_SLASH_CONTROL = re.compile(r"^/(?:approve|reject|cancel|status|help)(?:\s+\S+)?$")


class ProtocolIntentGate:
    def is_control(self, message: str) -> bool:
        normalized = message.strip().lower()
        return normalized in _CONTROL_COMMANDS or bool(
            _SLASH_CONTROL.fullmatch(normalized)
        )


__all__ = ["ProtocolIntentGate"]
