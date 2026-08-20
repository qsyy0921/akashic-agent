from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SPAWN_COMPLETED = "spawn_completed"
SpawnCompletionStatus = Literal["completed", "incomplete", "error", "cancelled"]


@dataclass(frozen=True)
class SpawnCompletionEvent:
    job_id: str
    label: str
    task: str
    status: SpawnCompletionStatus
    exit_reason: str
    result: str
    retry_count: int = 0
    profile: str = ""
