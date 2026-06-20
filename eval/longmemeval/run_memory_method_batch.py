"""Run memory-method evaluations sequentially.

This script is intentionally small: it preserves one workspace, one result
file, one log, and one metrics/error summary per method.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


DEFAULT_METHODS = [
    "method_01_intent_aware_retrieval",
    "method_02_structured_memory_schema",
    "method_03_evidence_fetch_rerank",
    "method_04_memory_update_versioning",
    "method_05_hybrid_intent_temporal_rerank",
    "method_06_adaptive_intent_versioned",
    "method_07_source_grounded_slot_resolver",
    "method_08_raw_message_first_resolver",
    "method_09_deterministic_attribution_resolver",
    "method_10_production_structured_memory_schema",
    "method_11_structured_candidate_resolver",
    "method_12_question_aware_structured_router",
    "method_13_slot_decision_answer_planner",
    "method_14_consolidated_memory_write_quality",
    "method_15_consolidated_fact_precision_rerank",
    "method_16_conservative_precision_gate",
    "method_17_structured_answer_contract",
]


def _run(cmd: list[str], *, cwd: Path, log_path: Path | None = None, env: dict[str, str]) -> None:
    if log_path is None:
        subprocess.run(cmd, cwd=cwd, env=env, check=True)
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="eval/agent_memory_bench_config.toml")
    parser.add_argument("--data", default="eval/agent_memory_bench_socialmem_full_local.json")
    parser.add_argument("--source-workspace", default="runtime/eval/socialmem_mimo_50")
    parser.add_argument("--workspace-root", default="runtime/eval/memory_methods")
    parser.add_argument("--experiments-root", default="experiments/memory_methods")
    parser.add_argument("--results-root", default="eval/results/memory_methods")
    parser.add_argument("--runs-root", default="eval/runs/memory_methods")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--methods", nargs="+", default=DEFAULT_METHODS)
    parser.add_argument(
        "--skip-existing-result",
        action="store_true",
        help="Skip methods whose final result JSON already exists.",
    )
    args = parser.parse_args(argv)

    cwd = Path.cwd()
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    for method_id in args.methods:
        result_path = Path(args.results_root) / f"{method_id}.json"
        method_dir = Path(args.experiments_root) / method_id
        config_path = method_dir / "config.json"
        workspace = Path(args.workspace_root) / method_id
        run_log = Path(args.runs_root) / f"{method_id}.log"

        if args.skip_existing_result and result_path.exists():
            print(f"[skip] {method_id}: result exists at {result_path}")
            continue

        print(f"[prepare] {method_id}")
        _run(
            [
                sys.executable,
                "-m",
                "eval.longmemeval.prepare_method_workspace",
                "--source-workspace",
                args.source_workspace,
                "--target-workspace",
                str(workspace),
                "--archive-existing",
            ],
            cwd=cwd,
            env=env,
        )

        print(f"[run] {method_id}")
        _run(
            [
                sys.executable,
                "-m",
                "eval.longmemeval.run",
                "--config",
                args.config,
                "--data",
                args.data,
                "--workspace",
                str(workspace),
                "--output",
                str(result_path),
                "--limit",
                str(args.limit),
                "--qa-only",
                "--resume-auto",
                "--timeout",
                str(args.timeout),
                "--method-config",
                str(config_path),
            ],
            cwd=cwd,
            log_path=run_log,
            env=env,
        )

        print(f"[summarize] {method_id}")
        _run(
            [
                sys.executable,
                "-m",
                "eval.longmemeval.summarize_method_results",
                "--result",
                str(result_path),
                "--method-dir",
                str(method_dir),
                "--method-id",
                method_id,
                "--run-log",
                str(run_log),
            ],
            cwd=cwd,
            env=env,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
