from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Literal, Protocol

RoutingEventName = Literal["routing_started", "routing_completed", "routing_failed"]
RoutingMode = Literal["shadow", "active"]
RoutingBackend = Literal["lexical", "hybrid", "llm_hybrid"]

logger = logging.getLogger("agent.routing")


@dataclass(frozen=True, slots=True)
class RoutingObservation:
    schema_version: str
    event: RoutingEventName
    turn_id: str
    mode: RoutingMode
    backend: RoutingBackend
    router_version: str
    route_status: str
    reason_code: str
    candidate_count: int
    preload_count: int
    duration_ms: int

    def __post_init__(self) -> None:
        if self.schema_version != "1":
            raise ValueError("unsupported routing observation schema version")
        for field_name in (
            "turn_id",
            "router_version",
            "route_status",
            "reason_code",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{field_name} must be non-empty and trimmed")
        for field_name in ("candidate_count", "preload_count", "duration_ms"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class RoutingObserver(Protocol):
    def emit(self, observation: RoutingObservation) -> None: ...


class LoggerRoutingObserver:
    def emit(self, observation: RoutingObservation) -> None:
        logger.info(
            "routing_event %s",
            json.dumps(observation.to_dict(), ensure_ascii=True, sort_keys=True),
        )


__all__ = [
    "LoggerRoutingObserver",
    "RoutingBackend",
    "RoutingEventName",
    "RoutingMode",
    "RoutingObservation",
    "RoutingObserver",
]
