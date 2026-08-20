from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class EvolutionVerdict(StrEnum):
    GO_FOR_HUMAN_REVIEW = "GO_FOR_HUMAN_REVIEW"
    NO_GO = "NO_GO"


@dataclass(frozen=True, slots=True)
class EvolutionPolicy:
    min_attempts: int = 30
    min_failure_count: int = 3
    max_evidence_age_days: int = 30
    candidate_retention_days: int = 14
    min_safety_cases: int = 20
    min_holdout_cases: int = 20

    def __post_init__(self) -> None:
        for label, value in (
            ("min_attempts", self.min_attempts),
            ("min_failure_count", self.min_failure_count),
            ("max_evidence_age_days", self.max_evidence_age_days),
            ("candidate_retention_days", self.candidate_retention_days),
            ("min_safety_cases", self.min_safety_cases),
            ("min_holdout_cases", self.min_holdout_cases),
        ):
            if isinstance(value, bool) or value < 1:
                raise ValueError(f"evolution policy {label} must be positive")
        if self.max_evidence_age_days > 365:
            raise ValueError("evolution evidence age cannot exceed 365 days")
        if self.candidate_retention_days > self.max_evidence_age_days:
            raise ValueError("evolution candidate retention exceeds evidence age")

    def to_dict(self) -> dict[str, int]:
        return {
            "min_attempts": self.min_attempts,
            "min_failure_count": self.min_failure_count,
            "max_evidence_age_days": self.max_evidence_age_days,
            "candidate_retention_days": self.candidate_retention_days,
            "min_safety_cases": self.min_safety_cases,
            "min_holdout_cases": self.min_holdout_cases,
        }


@dataclass(frozen=True, slots=True)
class SafetyEvidence:
    safety_report_sha256: str
    safety_cases: int
    safety_violations: int
    holdout_report_sha256: str
    holdout_cases: int
    holdout_regressions: int

    def __post_init__(self) -> None:
        for label, digest in (
            ("safety_report_sha256", self.safety_report_sha256),
            ("holdout_report_sha256", self.holdout_report_sha256),
        ):
            if not _SHA256.fullmatch(digest):
                raise ValueError(f"evolution evidence {label} is invalid")
        for label, value in (
            ("safety_cases", self.safety_cases),
            ("safety_violations", self.safety_violations),
            ("holdout_cases", self.holdout_cases),
            ("holdout_regressions", self.holdout_regressions),
        ):
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"evolution evidence {label} must be non-negative")
        if self.safety_violations > self.safety_cases:
            raise ValueError("evolution safety violations exceed case count")
        if self.holdout_regressions > self.holdout_cases:
            raise ValueError("evolution holdout regressions exceed case count")

    def to_dict(self) -> dict[str, object]:
        return {
            "safety_report_sha256": self.safety_report_sha256,
            "safety_cases": self.safety_cases,
            "safety_violations": self.safety_violations,
            "holdout_report_sha256": self.holdout_report_sha256,
            "holdout_cases": self.holdout_cases,
            "holdout_regressions": self.holdout_regressions,
        }


@dataclass(frozen=True, slots=True)
class EvolutionGateResult:
    name: str
    passed: bool
    reason_code: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "passed": self.passed,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class EvolutionCandidate:
    candidate_id: str
    kind: str
    target: str
    failure_reason: str
    failure_count: int
    failure_rate: float
    priority: str
    required_gates: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "kind": self.kind,
            "target": self.target,
            "failure_reason": self.failure_reason,
            "failure_count": self.failure_count,
            "failure_rate": self.failure_rate,
            "priority": self.priority,
            "required_gates": list(self.required_gates),
        }
