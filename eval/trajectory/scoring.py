from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from eval.trajectory.contracts import (
    ClarificationPolicy,
    TrajectoryOutcome,
    TrajectoryRun,
    TrajectoryStep,
)


@dataclass(frozen=True, slots=True)
class TrajectoryFirstError:
    index: int
    reason_code: str
    expected: str
    actual: str

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "reason_code": self.reason_code,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass(frozen=True, slots=True)
class TrajectoryEvaluation:
    case_id: str
    attempt: int
    success: bool
    first_error: TrajectoryFirstError | None
    correct_prefix_length: int
    expected_step_count: int
    prefix_rate: float
    terminal_status: str
    clarification_observed: bool
    retry_count: int
    input_tokens: int
    latency_ms: float

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "attempt": self.attempt,
            "success": self.success,
            "first_error": (
                self.first_error.to_dict() if self.first_error is not None else None
            ),
            "correct_prefix_length": self.correct_prefix_length,
            "expected_step_count": self.expected_step_count,
            "prefix_rate": self.prefix_rate,
            "terminal_status": self.terminal_status,
            "clarification_observed": self.clarification_observed,
            "retry_count": self.retry_count,
            "input_tokens": self.input_tokens,
            "latency_ms": self.latency_ms,
        }


def evaluate_trajectory(run: TrajectoryRun) -> TrajectoryEvaluation:
    first_error: TrajectoryFirstError | None = None
    completed_tools: set[str] = set()
    clarification_observed = any(step.kind == "clarification" for step in run.steps)
    retry_count = _retry_count(run.steps)
    expected_steps = run.oracle.expected_steps

    for index, step in enumerate(run.steps):
        error = _step_policy_error(run, step, index, completed_tools)
        if error is None:
            if index < len(expected_steps):
                matcher = expected_steps[index]
                if not matcher.matches(step):
                    error = TrajectoryFirstError(
                        index=index,
                        reason_code="unexpected_step",
                        expected=matcher.describe(),
                        actual=_describe_step(step),
                    )
            elif not run.oracle.allow_trailing_steps:
                error = TrajectoryFirstError(
                    index=index,
                    reason_code="unexpected_trailing_step",
                    expected="<end>",
                    actual=_describe_step(step),
                )
        if error is not None:
            first_error = error
            break
        if step.kind == "tool_result" and step.outcome is TrajectoryOutcome.SUCCEEDED:
            completed_tools.add(step.subject)

    if first_error is None and len(run.steps) < len(expected_steps):
        first_error = TrajectoryFirstError(
            index=len(run.steps),
            reason_code="missing_step",
            expected=expected_steps[len(run.steps)].describe(),
            actual="<end>",
        )
    if (
        first_error is None
        and run.oracle.clarification is ClarificationPolicy.REQUIRED
        and not clarification_observed
    ):
        first_error = TrajectoryFirstError(
            index=len(run.steps),
            reason_code="missing_required_clarification",
            expected="clarification:*:*",
            actual="<end>",
        )
    if first_error is None and run.terminal_status is not run.oracle.terminal_status:
        first_error = TrajectoryFirstError(
            index=len(run.steps),
            reason_code="terminal_status_mismatch",
            expected=run.oracle.terminal_status.value,
            actual=run.terminal_status.value,
        )

    correct_prefix_length = (
        len(expected_steps)
        if first_error is None
        else min(first_error.index, len(expected_steps))
    )
    prefix_rate = (
        1.0
        if not expected_steps and first_error is None
        else (
            correct_prefix_length / len(expected_steps) if expected_steps else 0.0
        )
    )
    return TrajectoryEvaluation(
        case_id=run.case_id,
        attempt=run.attempt,
        success=first_error is None,
        first_error=first_error,
        correct_prefix_length=correct_prefix_length,
        expected_step_count=len(expected_steps),
        prefix_rate=prefix_rate,
        terminal_status=run.terminal_status.value,
        clarification_observed=clarification_observed,
        retry_count=retry_count,
        input_tokens=sum(step.input_tokens for step in run.steps),
        latency_ms=sum(step.latency_ms for step in run.steps),
    )


def aggregate_evaluations(
    evaluations: Iterable[TrajectoryEvaluation],
    *,
    pass_k: tuple[int, ...] = (1, 2, 3),
) -> dict[str, object]:
    rows = list(evaluations)
    if len({(row.case_id, row.attempt) for row in rows}) != len(rows):
        raise ValueError("trajectory evaluations contain duplicate case attempts")
    if not pass_k or any(isinstance(k, bool) or k < 1 for k in pass_k):
        raise ValueError("trajectory pass_k values must be positive")
    if len(set(pass_k)) != len(pass_k):
        raise ValueError("trajectory pass_k values must be unique")

    grouped: dict[str, list[TrajectoryEvaluation]] = defaultdict(list)
    for row in rows:
        grouped[row.case_id].append(row)
    for attempts in grouped.values():
        attempts.sort(key=lambda row: row.attempt)
        observed_attempts = [row.attempt for row in attempts]
        expected_attempts = list(range(1, len(attempts) + 1))
        if observed_attempts != expected_attempts:
            raise ValueError("trajectory case attempts must be contiguous from 1")

    error_counts = Counter(
        row.first_error.reason_code
        for row in rows
        if row.first_error is not None
    )
    pass_power: dict[str, object] = {}
    for k in sorted(pass_k):
        eligible = [attempts for attempts in grouped.values() if len(attempts) >= k]
        passed = sum(all(row.success for row in attempts[:k]) for attempts in eligible)
        pass_power[str(k)] = {
            "eligible_cases": len(eligible),
            "passed_cases": passed,
            "rate": passed / len(eligible) if eligible else None,
        }

    successful_attempts = sum(row.success for row in rows)
    all_pass_cases = sum(
        bool(attempts) and all(row.success for row in attempts)
        for attempts in grouped.values()
    )
    latencies = [row.latency_ms for row in rows]
    return {
        "attempt_count": len(rows),
        "case_count": len(grouped),
        "successful_attempts": successful_attempts,
        "attempt_success_rate": successful_attempts / len(rows) if rows else None,
        "all_attempts_passed_cases": all_pass_cases,
        "case_all_attempts_success_rate": (
            all_pass_cases / len(grouped) if grouped else None
        ),
        "mean_prefix_rate": (
            sum(row.prefix_rate for row in rows) / len(rows) if rows else None
        ),
        "first_error_counts": dict(sorted(error_counts.items())),
        "pass_power_k": pass_power,
        "input_tokens_total": sum(row.input_tokens for row in rows),
        "latency_ms_total": sum(latencies),
        "latency_ms_p95": _nearest_rank_percentile(latencies, 0.95),
    }


def _step_policy_error(
    run: TrajectoryRun,
    step: TrajectoryStep,
    index: int,
    completed_tools: set[str],
) -> TrajectoryFirstError | None:
    if (
        step.kind == "clarification"
        and run.oracle.clarification is ClarificationPolicy.FORBIDDEN
    ):
        return TrajectoryFirstError(
            index=index,
            reason_code="forbidden_clarification",
            expected="no clarification",
            actual=_describe_step(step),
        )
    if step.kind == "tool_call":
        required = set(run.oracle.dependencies.get(step.subject, ()))
        missing = sorted(required - completed_tools)
        if missing:
            return TrajectoryFirstError(
                index=index,
                reason_code="dependency_violation",
                expected="completed:" + "|".join(missing),
                actual=_describe_step(step),
            )
        if step.attempt > run.oracle.max_retries + 1:
            return TrajectoryFirstError(
                index=index,
                reason_code="retry_budget_exceeded",
                expected=f"attempt<={run.oracle.max_retries + 1}",
                actual=f"attempt={step.attempt}",
            )
    return None


def _retry_count(steps: tuple[TrajectoryStep, ...]) -> int:
    max_attempts: dict[str, int] = {}
    for step in steps:
        if step.kind == "tool_call":
            max_attempts[step.subject] = max(
                max_attempts.get(step.subject, 0),
                step.attempt,
            )
    return sum(maximum - 1 for maximum in max_attempts.values())


def _describe_step(step: TrajectoryStep) -> str:
    return f"{step.kind}:{step.subject}:{step.outcome.value}"


def _nearest_rank_percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]
