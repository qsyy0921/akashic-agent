from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from eval.trajectory.contracts import TrajectoryRun
from eval.trajectory.scoring import aggregate_evaluations, evaluate_trajectory
from infra.persistence.json_store import atomic_save_json


def evaluate_jsonl(
    source: Path,
    *,
    pass_k: tuple[int, ...] = (1, 2, 3),
) -> dict[str, object]:
    source_bytes = source.read_bytes()
    runs: list[TrajectoryRun] = []
    for line_number, raw_line in enumerate(source_bytes.decode("utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid trajectory JSONL at line {line_number}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"trajectory JSONL line {line_number} must be an object")
        try:
            runs.append(TrajectoryRun.from_dict(cast(dict[str, object], payload)))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid trajectory record at line {line_number}: {exc}"
            ) from exc

    evaluations = [evaluate_trajectory(run) for run in runs]
    aggregate = aggregate_evaluations(evaluations, pass_k=pass_k)
    return {
        "schema_version": "1",
        "source_name": source.name,
        "source_sha256": "sha256:" + hashlib.sha256(source_bytes).hexdigest(),
        "aggregate": aggregate,
        "evaluations": [row.to_dict() for row in evaluations],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate normalized Agent trajectories")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pass-k", type=int, nargs="+", default=[1, 2, 3])
    args = parser.parse_args(argv)
    report = evaluate_jsonl(args.input, pass_k=tuple(args.pass_k))
    atomic_save_json(args.output, report, domain="trajectory_eval")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
