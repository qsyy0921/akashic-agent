from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast


class TrajectoryOutcome(StrEnum):
    OBSERVED = "observed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class TrajectoryTerminalStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    INCOMPLETE = "incomplete"
    CANCELLED = "cancelled"


class ClarificationPolicy(StrEnum):
    OPTIONAL = "optional"
    REQUIRED = "required"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class TrajectoryStep:
    step_id: str
    kind: str
    subject: str
    outcome: TrajectoryOutcome = TrajectoryOutcome.OBSERVED
    attempt: int = 1
    latency_ms: float = 0.0
    input_tokens: int = 0

    def __post_init__(self) -> None:
        _identity("step_id", self.step_id)
        _identity("kind", self.kind)
        _identity("subject", self.subject)
        if isinstance(self.attempt, bool) or self.attempt < 1:
            raise ValueError("trajectory step attempt must be positive")
        if isinstance(self.input_tokens, bool) or self.input_tokens < 0:
            raise ValueError("trajectory step input_tokens must be non-negative")
        if isinstance(self.latency_ms, bool) or self.latency_ms < 0:
            raise ValueError("trajectory step latency_ms must be non-negative")

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> TrajectoryStep:
        _exact_keys(
            raw,
            required={"step_id", "kind", "subject"},
            optional={"outcome", "attempt", "latency_ms", "input_tokens"},
            context="trajectory step",
        )
        return cls(
            step_id=_string(raw, "step_id"),
            kind=_string(raw, "kind"),
            subject=_string(raw, "subject"),
            outcome=TrajectoryOutcome(str(raw.get("outcome", "observed"))),
            attempt=_integer(raw, "attempt", default=1),
            latency_ms=_number(raw, "latency_ms", default=0.0),
            input_tokens=_integer(raw, "input_tokens", default=0),
        )


@dataclass(frozen=True, slots=True)
class StepMatcher:
    kind: str
    subjects: tuple[str, ...] = ()
    outcomes: tuple[TrajectoryOutcome, ...] = ()

    def __post_init__(self) -> None:
        _identity("matcher kind", self.kind)
        for subject in self.subjects:
            _identity("matcher subject", subject)
        if len(set(self.subjects)) != len(self.subjects):
            raise ValueError("trajectory matcher subjects must be unique")
        if len(set(self.outcomes)) != len(self.outcomes):
            raise ValueError("trajectory matcher outcomes must be unique")

    def matches(self, step: TrajectoryStep) -> bool:
        return (
            step.kind == self.kind
            and (not self.subjects or step.subject in self.subjects)
            and (not self.outcomes or step.outcome in self.outcomes)
        )

    def describe(self) -> str:
        subjects = "|".join(self.subjects) if self.subjects else "*"
        outcomes = (
            "|".join(outcome.value for outcome in self.outcomes)
            if self.outcomes
            else "*"
        )
        return f"{self.kind}:{subjects}:{outcomes}"

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> StepMatcher:
        _exact_keys(
            raw,
            required={"kind"},
            optional={"subjects", "outcomes"},
            context="trajectory matcher",
        )
        return cls(
            kind=_string(raw, "kind"),
            subjects=tuple(_string_list(raw.get("subjects", []), "subjects")),
            outcomes=tuple(
                TrajectoryOutcome(value)
                for value in _string_list(raw.get("outcomes", []), "outcomes")
            ),
        )


@dataclass(frozen=True, slots=True)
class TrajectoryOracle:
    expected_steps: tuple[StepMatcher, ...]
    terminal_status: TrajectoryTerminalStatus
    clarification: ClarificationPolicy = ClarificationPolicy.OPTIONAL
    max_retries: int = 0
    dependencies: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    allow_trailing_steps: bool = False

    def __post_init__(self) -> None:
        dependencies = self.dependencies or {}
        object.__setattr__(self, "dependencies", dict(dependencies))
        if isinstance(self.max_retries, bool) or self.max_retries < 0:
            raise ValueError("trajectory max_retries must be non-negative")
        for consumer, producers in dependencies.items():
            _identity("dependency consumer", consumer)
            if len(set(producers)) != len(producers):
                raise ValueError("trajectory dependency producers must be unique")
            for producer in producers:
                _identity("dependency producer", producer)
                if producer == consumer:
                    raise ValueError("trajectory dependency cannot reference itself")

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> TrajectoryOracle:
        _exact_keys(
            raw,
            required={"expected_steps", "terminal_status"},
            optional={
                "clarification",
                "max_retries",
                "dependencies",
                "allow_trailing_steps",
            },
            context="trajectory oracle",
        )
        expected_raw = _object_list(raw.get("expected_steps"), "expected_steps")
        dependencies_raw = raw.get("dependencies", {})
        if not isinstance(dependencies_raw, Mapping):
            raise ValueError("trajectory dependencies must be an object")
        dependencies_payload = cast(Mapping[object, object], dependencies_raw)
        dependencies: dict[str, tuple[str, ...]] = {}
        for key, value in dependencies_payload.items():
            if not isinstance(key, str):
                raise ValueError("trajectory dependency key must be a string")
            dependencies[key] = tuple(_string_list(value, f"dependencies.{key}"))
        return cls(
            expected_steps=tuple(StepMatcher.from_dict(item) for item in expected_raw),
            terminal_status=TrajectoryTerminalStatus(_string(raw, "terminal_status")),
            clarification=ClarificationPolicy(
                str(raw.get("clarification", "optional"))
            ),
            max_retries=_integer(raw, "max_retries", default=0),
            dependencies=dependencies,
            allow_trailing_steps=_boolean(raw, "allow_trailing_steps", default=False),
        )


@dataclass(frozen=True, slots=True)
class TrajectoryRun:
    case_id: str
    attempt: int
    terminal_status: TrajectoryTerminalStatus
    steps: tuple[TrajectoryStep, ...]
    oracle: TrajectoryOracle

    def __post_init__(self) -> None:
        _identity("case_id", self.case_id)
        if isinstance(self.attempt, bool) or self.attempt < 1:
            raise ValueError("trajectory run attempt must be positive")
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("trajectory run step ids must be unique")

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> TrajectoryRun:
        _exact_keys(
            raw,
            required={"case_id", "attempt", "terminal_status", "steps", "oracle"},
            optional=set(),
            context="trajectory run",
        )
        oracle_raw = raw.get("oracle")
        if not isinstance(oracle_raw, Mapping):
            raise ValueError("trajectory oracle must be an object")
        return cls(
            case_id=_string(raw, "case_id"),
            attempt=_integer(raw, "attempt"),
            terminal_status=TrajectoryTerminalStatus(_string(raw, "terminal_status")),
            steps=tuple(
                TrajectoryStep.from_dict(item)
                for item in _object_list(raw.get("steps"), "steps")
            ),
            oracle=TrajectoryOracle.from_dict(cast(Mapping[str, object], oracle_raw)),
        )


def _identity(label: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 128
        or any(character in value for character in "\r\n\t")
    ):
        raise ValueError(f"trajectory {label} is invalid")


def _exact_keys(
    raw: Mapping[str, object],
    *,
    required: set[str],
    optional: set[str],
    context: str,
) -> None:
    keys = set(raw)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise ValueError(f"{context} missing fields: {sorted(missing)}")
    if unknown:
        raise ValueError(f"{context} has unknown fields: {sorted(unknown)}")


def _string(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"trajectory {key} must be a string")
    return value


def _integer(raw: Mapping[str, object], key: str, default: int | None = None) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"trajectory {key} must be an integer")
    return value


def _number(raw: Mapping[str, object], key: str, default: float) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"trajectory {key} must be numeric")
    return float(value)


def _boolean(raw: Mapping[str, object], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"trajectory {key} must be boolean")
    return value


def _string_list(raw: object, label: str) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError(f"trajectory {label} must be an array of strings")
    return cast(list[str], raw)


def _object_list(raw: object, label: str) -> list[Mapping[str, object]]:
    if not isinstance(raw, list) or not all(isinstance(item, Mapping) for item in raw):
        raise ValueError(f"trajectory {label} must be an array of objects")
    return cast(list[Mapping[str, object]], raw)
