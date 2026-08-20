from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import cast

from eval.evolution.contracts import (
    EvolutionCandidate,
    EvolutionGateResult,
    EvolutionPolicy,
    EvolutionVerdict,
    SafetyEvidence,
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_FAILURE_TARGETS = {
    "unexpected_step": ("routing_oracle_review", "intent_routing.catalog"),
    "unexpected_trailing_step": (
        "routing_oracle_review",
        "intent_routing.catalog",
    ),
    "dependency_violation": (
        "tool_dependency_review",
        "tool_registry.dependencies",
    ),
    "retry_budget_exceeded": (
        "recovery_budget_review",
        "reliability.recovery_policy",
    ),
    "forbidden_clarification": (
        "clarification_policy_review",
        "intent_routing.clarification_policy",
    ),
    "missing_required_clarification": (
        "clarification_policy_review",
        "intent_routing.clarification_policy",
    ),
    "missing_step": ("turn_contract_review", "runtime.turn_contract"),
    "terminal_status_mismatch": (
        "turn_contract_review",
        "runtime.turn_contract",
    ),
}
_REQUIRED_GATES = (
    "unit",
    "trajectory_holdout",
    "safety",
    "manual_sdd_review",
)


def generate_evolution_bundle(
    report: Mapping[str, object],
    *,
    report_sha256: str,
    evidence_collected_at: datetime,
    as_of: datetime,
    safety: SafetyEvidence,
    policy: EvolutionPolicy | None = None,
) -> dict[str, object]:
    active_policy = policy or EvolutionPolicy()
    if not _SHA256.fullmatch(report_sha256):
        raise ValueError("evolution report digest is invalid")
    collected_at = _aware_utc("evidence_collected_at", evidence_collected_at)
    current = _aware_utc("as_of", as_of)
    if collected_at > current:
        raise ValueError("evolution evidence timestamp is in the future")
    summary = _parse_trajectory_report(report)
    source_digest = summary["source_digest"]
    attempt_count = summary["attempt_count"]
    failures = summary["failures"]
    unknown_reasons = sorted(set(failures) - set(_FAILURE_TARGETS))
    age = current - collected_at
    actionable = {
        reason: count
        for reason, count in failures.items()
        if reason in _FAILURE_TARGETS and count >= active_policy.min_failure_count
    }

    gates = (
        EvolutionGateResult(
            "boundary",
            not unknown_reasons,
            "ok" if not unknown_reasons else "unsupported_failure_reason",
        ),
        EvolutionGateResult(
            "retention",
            age <= timedelta(days=active_policy.max_evidence_age_days),
            (
                "ok"
                if age <= timedelta(days=active_policy.max_evidence_age_days)
                else "evidence_expired"
            ),
        ),
        EvolutionGateResult(
            "sample",
            attempt_count >= active_policy.min_attempts,
            "ok" if attempt_count >= active_policy.min_attempts else "sample_too_small",
        ),
        EvolutionGateResult(
            "safety",
            safety.safety_cases >= active_policy.min_safety_cases
            and safety.safety_violations == 0,
            _safety_reason(safety, active_policy),
        ),
        EvolutionGateResult(
            "holdout",
            safety.holdout_cases >= active_policy.min_holdout_cases
            and safety.holdout_regressions == 0,
            _holdout_reason(safety, active_policy),
        ),
        EvolutionGateResult(
            "actionable_signal",
            bool(actionable),
            "ok" if actionable else "no_actionable_signal",
        ),
    )
    all_passed = all(gate.passed for gate in gates)
    candidates = (
        _build_candidates(report_sha256, source_digest, attempt_count, actionable)
        if all_passed
        else []
    )
    verdict = (
        EvolutionVerdict.GO_FOR_HUMAN_REVIEW
        if all_passed
        else EvolutionVerdict.NO_GO
    )
    expires_at = min(
        current + timedelta(days=active_policy.candidate_retention_days),
        collected_at + timedelta(days=active_policy.max_evidence_age_days),
    )
    return {
        "schema_version": "1",
        "source_report_sha256": report_sha256,
        "trajectory_source_sha256": source_digest,
        "evidence_collected_at": _timestamp(collected_at),
        "created_at": _timestamp(current),
        "expires_at": _timestamp(expires_at),
        "evidence_age_seconds": age.total_seconds(),
        "safety_evidence": safety.to_dict(),
        "policy": active_policy.to_dict(),
        "automatic_application": False,
        "gates": [gate.to_dict() for gate in gates],
        "verdict": verdict.value,
        "candidates": [candidate.to_dict() for candidate in candidates],
    }


def _parse_trajectory_report(report: Mapping[str, object]) -> dict[str, object]:
    expected_keys = {
        "schema_version",
        "source_name",
        "source_sha256",
        "aggregate",
        "evaluations",
    }
    if set(report) != expected_keys or report.get("schema_version") != "1":
        raise ValueError("evolution trajectory report schema is invalid")
    source_digest = report.get("source_sha256")
    if not isinstance(source_digest, str) or not _SHA256.fullmatch(source_digest):
        raise ValueError("evolution trajectory source digest is invalid")
    if not isinstance(report.get("evaluations"), list):
        raise ValueError("evolution trajectory evaluations must be an array")
    aggregate_raw = report.get("aggregate")
    if not isinstance(aggregate_raw, Mapping):
        raise ValueError("evolution trajectory aggregate must be an object")
    aggregate = cast(Mapping[str, object], aggregate_raw)
    attempt_count = aggregate.get("attempt_count")
    if isinstance(attempt_count, bool) or not isinstance(attempt_count, int):
        raise ValueError("evolution trajectory attempt_count must be an integer")
    if attempt_count < 0:
        raise ValueError("evolution trajectory attempt_count must be non-negative")
    failures_raw = aggregate.get("first_error_counts")
    if not isinstance(failures_raw, Mapping):
        raise ValueError("evolution first_error_counts must be an object")
    failures_payload = cast(Mapping[object, object], failures_raw)
    failures: dict[str, int] = {}
    for reason, count in failures_payload.items():
        if not isinstance(reason, str) or not reason:
            raise ValueError("evolution failure reason must be a string")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("evolution failure count must be non-negative")
        if count:
            failures[reason] = count
    if sum(failures.values()) > attempt_count:
        raise ValueError("evolution failure counts exceed attempt count")
    return {
        "source_digest": source_digest,
        "attempt_count": attempt_count,
        "failures": failures,
    }


def _build_candidates(
    report_digest: str,
    source_digest: object,
    attempt_count: object,
    failures: dict[str, int],
) -> list[EvolutionCandidate]:
    assert isinstance(source_digest, str)
    assert isinstance(attempt_count, int) and attempt_count > 0
    candidates: list[EvolutionCandidate] = []
    for reason, count in sorted(failures.items()):
        kind, target = _FAILURE_TARGETS[reason]
        identity = hashlib.sha256(
            f"{report_digest}\0{source_digest}\0{reason}\0{target}".encode("utf-8")
        ).hexdigest()[:24]
        rate = count / attempt_count
        candidates.append(
            EvolutionCandidate(
                candidate_id=f"evolution:{identity}",
                kind=kind,
                target=target,
                failure_reason=reason,
                failure_count=count,
                failure_rate=rate,
                priority="high" if rate >= 0.2 else "medium",
                required_gates=_REQUIRED_GATES,
            )
        )
    return candidates


def _safety_reason(safety: SafetyEvidence, policy: EvolutionPolicy) -> str:
    if safety.safety_cases < policy.min_safety_cases:
        return "safety_sample_too_small"
    if safety.safety_violations:
        return "safety_violation"
    return "ok"


def _holdout_reason(safety: SafetyEvidence, policy: EvolutionPolicy) -> str:
    if safety.holdout_cases < policy.min_holdout_cases:
        return "holdout_sample_too_small"
    if safety.holdout_regressions:
        return "holdout_regression"
    return "ok"


def _aware_utc(label: str, value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"evolution {label} must include a timezone")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
