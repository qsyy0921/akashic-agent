import hashlib
import json
from datetime import UTC, datetime

import pytest

from eval.evolution import (
    EvolutionPolicy,
    SafetyEvidence,
    generate_evolution_bundle,
)
from eval.evolution.run import main

_REPORT_DIGEST = "sha256:" + "a" * 64
_SOURCE_DIGEST = "sha256:" + "b" * 64
_SAFETY_DIGEST = "sha256:" + "c" * 64
_HOLDOUT_DIGEST = "sha256:" + "d" * 64


def _report(
    failures: dict[str, int],
    *,
    attempts: int = 100,
    evaluations: list[object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "1",
        "source_name": "runs.jsonl",
        "source_sha256": _SOURCE_DIGEST,
        "aggregate": {
            "attempt_count": attempts,
            "first_error_counts": failures,
        },
        "evaluations": evaluations or [],
    }


def _safety(
    *,
    violations: int = 0,
    regressions: int = 0,
    safety_cases: int = 40,
    holdout_cases: int = 40,
) -> SafetyEvidence:
    return SafetyEvidence(
        safety_report_sha256=_SAFETY_DIGEST,
        safety_cases=safety_cases,
        safety_violations=violations,
        holdout_report_sha256=_HOLDOUT_DIGEST,
        holdout_cases=holdout_cases,
        holdout_regressions=regressions,
    )


def _generate(
    report: dict[str, object],
    *,
    collected_at: datetime | None = None,
    as_of: datetime | None = None,
    safety: SafetyEvidence | None = None,
    policy: EvolutionPolicy | None = None,
) -> dict[str, object]:
    return generate_evolution_bundle(
        report,
        report_sha256=_REPORT_DIGEST,
        evidence_collected_at=collected_at
        or datetime(2026, 8, 18, tzinfo=UTC),
        as_of=as_of or datetime(2026, 8, 19, tzinfo=UTC),
        safety=safety or _safety(),
        policy=policy,
    )


def _gate(bundle: dict[str, object], name: str) -> dict[str, object]:
    gates = bundle["gates"]
    assert isinstance(gates, list)
    return next(gate for gate in gates if gate["name"] == name)


def test_offline_evolution_go_produces_review_only_deterministic_candidate():
    report = _report({"dependency_violation": 20})

    first = _generate(report)
    second = _generate(report)

    assert first == second
    assert first["verdict"] == "GO_FOR_HUMAN_REVIEW"
    assert first["automatic_application"] is False
    candidates = first["candidates"]
    assert isinstance(candidates, list) and len(candidates) == 1
    candidate = candidates[0]
    assert candidate["target"] == "tool_registry.dependencies"
    assert candidate["kind"] == "tool_dependency_review"
    assert candidate["failure_rate"] == 0.2
    assert candidate["priority"] == "high"
    assert candidate["required_gates"][-1] == "manual_sdd_review"


def test_offline_evolution_unknown_failure_is_boundary_no_go():
    bundle = _generate(_report({"novel_failure": 10}))

    assert bundle["verdict"] == "NO_GO"
    assert bundle["candidates"] == []
    assert _gate(bundle, "boundary") == {
        "name": "boundary",
        "passed": False,
        "reason_code": "unsupported_failure_reason",
    }


def test_offline_evolution_expired_evidence_is_no_go_and_cannot_be_renewed():
    bundle = _generate(
        _report({"missing_step": 5}),
        collected_at=datetime(2026, 6, 1, tzinfo=UTC),
        as_of=datetime(2026, 8, 19, tzinfo=UTC),
    )

    assert bundle["verdict"] == "NO_GO"
    assert _gate(bundle, "retention")["reason_code"] == "evidence_expired"


@pytest.mark.parametrize(
    ("safety", "gate_name", "reason"),
    [
        (_safety(violations=1), "safety", "safety_violation"),
        (_safety(regressions=1), "holdout", "holdout_regression"),
        (_safety(safety_cases=5), "safety", "safety_sample_too_small"),
        (_safety(holdout_cases=5), "holdout", "holdout_sample_too_small"),
    ],
)
def test_offline_evolution_safety_or_holdout_failure_is_no_go(
    safety,
    gate_name,
    reason,
):
    bundle = _generate(_report({"unexpected_step": 10}), safety=safety)

    assert bundle["verdict"] == "NO_GO"
    assert bundle["candidates"] == []
    assert _gate(bundle, gate_name)["reason_code"] == reason


def test_offline_evolution_requires_sample_and_actionable_failure_count():
    small_sample = _generate(_report({"unexpected_step": 10}, attempts=20))
    weak_signal = _generate(_report({"unexpected_step": 2}, attempts=100))

    assert _gate(small_sample, "sample")["reason_code"] == "sample_too_small"
    assert _gate(weak_signal, "actionable_signal")["reason_code"] == (
        "no_actionable_signal"
    )
    assert small_sample["verdict"] == weak_signal["verdict"] == "NO_GO"


def test_offline_evolution_expires_no_later_than_source_evidence():
    bundle = _generate(
        _report({"terminal_status_mismatch": 8}),
        collected_at=datetime(2026, 8, 1, tzinfo=UTC),
        as_of=datetime(2026, 8, 20, tzinfo=UTC),
        policy=EvolutionPolicy(
            max_evidence_age_days=30,
            candidate_retention_days=14,
        ),
    )

    assert bundle["expires_at"] == "2026-08-31T00:00:00Z"


def test_offline_evolution_does_not_copy_evaluations_or_payloads():
    marker = "private message and tool arguments"
    bundle = _generate(
        _report(
            {"retry_budget_exceeded": 6},
            evaluations=[{"content": marker, "arguments": {"token": marker}}],
        )
    )

    encoded = json.dumps(bundle, ensure_ascii=False)
    assert marker not in encoded
    assert "evaluations" not in encoded


def test_offline_evolution_rejects_inconsistent_or_naive_evidence():
    with pytest.raises(ValueError, match="exceed attempt"):
        _generate(_report({"missing_step": 11}, attempts=10))
    with pytest.raises(ValueError, match="timezone"):
        _generate(
            _report({"missing_step": 5}),
            collected_at=datetime(2026, 8, 18),
        )


def test_offline_evolution_cli_writes_digest_bound_bundle(tmp_path):
    report_path = tmp_path / "trajectory.json"
    report_bytes = json.dumps(_report({"forbidden_clarification": 7})).encode()
    report_path.write_bytes(report_bytes)
    output = tmp_path / "bundle.json"

    exit_code = main(
        [
            "--trajectory-report",
            str(report_path),
            "--output",
            str(output),
            "--evidence-collected-at",
            "2026-08-18T00:00:00Z",
            "--as-of",
            "2026-08-19T00:00:00Z",
            "--safety-report-sha256",
            _SAFETY_DIGEST,
            "--safety-cases",
            "40",
            "--safety-violations",
            "0",
            "--holdout-report-sha256",
            _HOLDOUT_DIGEST,
            "--holdout-cases",
            "40",
            "--holdout-regressions",
            "0",
        ]
    )

    assert exit_code == 0
    bundle = json.loads(output.read_text(encoding="utf-8"))
    assert bundle["source_report_sha256"] == (
        "sha256:" + hashlib.sha256(report_bytes).hexdigest()
    )
    assert bundle["verdict"] == "GO_FOR_HUMAN_REVIEW"
