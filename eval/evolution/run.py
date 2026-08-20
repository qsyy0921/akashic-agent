from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import cast

from eval.evolution.candidates import generate_evolution_bundle
from eval.evolution.contracts import EvolutionPolicy, SafetyEvidence
from infra.persistence.json_store import atomic_save_json


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate review-only evolution candidates from trajectory evidence"
    )
    parser.add_argument("--trajectory-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-collected-at", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--safety-report-sha256", required=True)
    parser.add_argument("--safety-cases", type=int, required=True)
    parser.add_argument("--safety-violations", type=int, required=True)
    parser.add_argument("--holdout-report-sha256", required=True)
    parser.add_argument("--holdout-cases", type=int, required=True)
    parser.add_argument("--holdout-regressions", type=int, required=True)
    parser.add_argument("--min-attempts", type=int, default=30)
    parser.add_argument("--min-failure-count", type=int, default=3)
    parser.add_argument("--max-evidence-age-days", type=int, default=30)
    parser.add_argument("--candidate-retention-days", type=int, default=14)
    args = parser.parse_args(argv)
    report_bytes = args.trajectory_report.read_bytes()
    try:
        report = json.loads(report_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("offline evolution trajectory report is invalid JSON") from exc
    if not isinstance(report, dict):
        raise ValueError("offline evolution trajectory report must be an object")
    bundle = generate_evolution_bundle(
        cast(dict[str, object], report),
        report_sha256="sha256:" + hashlib.sha256(report_bytes).hexdigest(),
        evidence_collected_at=_parse_timestamp(args.evidence_collected_at),
        as_of=_parse_timestamp(args.as_of),
        safety=SafetyEvidence(
            safety_report_sha256=args.safety_report_sha256,
            safety_cases=args.safety_cases,
            safety_violations=args.safety_violations,
            holdout_report_sha256=args.holdout_report_sha256,
            holdout_cases=args.holdout_cases,
            holdout_regressions=args.holdout_regressions,
        ),
        policy=EvolutionPolicy(
            min_attempts=args.min_attempts,
            min_failure_count=args.min_failure_count,
            max_evidence_age_days=args.max_evidence_age_days,
            candidate_retention_days=args.candidate_retention_days,
        ),
    )
    atomic_save_json(args.output, bundle, domain="offline_evolution")
    return 0


def _parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid ISO timestamp: {value}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
