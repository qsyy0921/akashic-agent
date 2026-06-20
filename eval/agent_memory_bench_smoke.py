from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


HF_ROWS_URL = "https://datasets-server.huggingface.co/rows"
MEMORYARENA_CONFIGS = (
    "bundled_shopping",
    "progressive_search",
    "group_travel_planner",
    "formal_reasoning_math",
    "formal_reasoning_phys",
)


def _fetch_rows(
    *,
    dataset: str,
    config: str,
    split: str = "test",
    offset: int = 0,
    length: int = 5,
    timeout_s: float = 90.0,
    retries: int = 3,
) -> dict[str, Any]:
    params = urllib.parse.urlencode(
        {
            "dataset": dataset,
            "config": config,
            "split": split,
            "offset": str(offset),
            "length": str(length),
        }
    )
    url = f"{HF_ROWS_URL}?{params}"
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "akashic-agent-memory-bench-smoke/0.1",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(2 * attempt, 6))
    raise RuntimeError(f"failed to fetch {dataset}/{config}: {last_error}") from last_error


def _avg(values: list[int]) -> float:
    return round(float(statistics.mean(values)), 2) if values else 0.0


def _row_payloads(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [item.get("row") or {} for item in data.get("rows") or []]


def _summarize_ama(sample_len: int) -> dict[str, Any]:
    data = _fetch_rows(
        dataset="AMA-bench/AMA-bench",
        config="default",
        length=sample_len,
    )
    rows = _row_payloads(data)
    num_turns = [int(row.get("num_turns") or 0) for row in rows]
    total_tokens = [int(row.get("total_tokens") or 0) for row in rows]
    qa_counts = [len(row.get("qa_pairs") or []) for row in rows]
    traj_counts = [len(row.get("trajectory") or []) for row in rows]
    qa_types: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    task_types: Counter[str] = Counter()
    missing_required: list[dict[str, Any]] = []
    required = {"episode_id", "task", "domain", "trajectory", "qa_pairs"}

    for idx, row in enumerate(rows):
        domains.update([str(row.get("domain") or "")])
        task_types.update([str(row.get("task_type") or "")])
        for pair in row.get("qa_pairs") or []:
            qa_types.update([str(pair.get("type") or "")])
        missing = sorted(key for key in required if key not in row)
        if missing:
            missing_required.append({"sample_index": idx, "missing": missing})

    preview = {}
    if rows:
        row = rows[0]
        preview = {
            "episode_id": row.get("episode_id"),
            "domain": row.get("domain"),
            "task_type": row.get("task_type"),
            "num_turns": row.get("num_turns"),
            "total_tokens": row.get("total_tokens"),
            "first_trajectory_keys": sorted((row.get("trajectory") or [{}])[0].keys()),
            "first_qa_keys": sorted((row.get("qa_pairs") or [{}])[0].keys()),
        }

    return {
        "dataset": "AMA-bench/AMA-bench",
        "config": "default",
        "total_rows": data.get("num_rows_total"),
        "sampled_rows": len(rows),
        "num_turns": {"min": min(num_turns or [0]), "max": max(num_turns or [0]), "avg": _avg(num_turns)},
        "trajectory_items": {"min": min(traj_counts or [0]), "max": max(traj_counts or [0]), "avg": _avg(traj_counts)},
        "total_tokens": {"min": min(total_tokens or [0]), "max": max(total_tokens or [0]), "avg": _avg(total_tokens)},
        "qa_pairs": {"min": min(qa_counts or [0]), "max": max(qa_counts or [0]), "avg": _avg(qa_counts)},
        "qa_type_counts_sample": dict(sorted(qa_types.items())),
        "domain_counts_sample": dict(sorted(domains.items())),
        "task_type_counts_sample": dict(sorted(task_types.items())),
        "missing_required": missing_required,
        "adapter_fit": {
            "trajectory_to_session": "action/observation can be replayed as assistant/tool-style turns",
            "qa_to_eval": "qa_pairs can be scored with LLM-as-judge or exact semantic judge",
        },
        "preview": preview,
    }


def _summarize_memoryarena(sample_len: int) -> dict[str, Any]:
    configs: dict[str, Any] = {}
    total_rows = 0
    for config in MEMORYARENA_CONFIGS:
        data = _fetch_rows(
            dataset="ZexueHe/memoryarena",
            config=config,
            length=sample_len,
        )
        rows = _row_payloads(data)
        total = int(data.get("num_rows_total") or 0)
        total_rows += total
        q_counts = [len(row.get("questions") or []) for row in rows]
        a_counts = [len(row.get("answers") or []) for row in rows]
        background_types: Counter[str] = Counter()
        category_values: Counter[str] = Counter()
        for row in rows:
            if "backgrounds" in row:
                background_types.update([type(row.get("backgrounds")).__name__])
            elif "base_person" in row:
                background_types.update(["base_person"])
            else:
                background_types.update(["none"])
            if row.get("category"):
                category_values.update([str(row.get("category"))])
        preview = {}
        if rows:
            row = rows[0]
            preview = {
                "id": row.get("id"),
                "keys": sorted(row.keys()),
                "question_count": len(row.get("questions") or []),
                "answer_count": len(row.get("answers") or []),
            }
        configs[config] = {
            "total_rows": total,
            "sampled_rows": len(rows),
            "questions": {"min": min(q_counts or [0]), "max": max(q_counts or [0]), "avg": _avg(q_counts)},
            "answers": {"min": min(a_counts or [0]), "max": max(a_counts or [0]), "avg": _avg(a_counts)},
            "background_types_sample": dict(sorted(background_types.items())),
            "category_counts_sample": dict(sorted(category_values.items())),
            "preview": preview,
        }

    return {
        "dataset": "ZexueHe/memoryarena",
        "total_rows": total_rows,
        "configs": configs,
        "adapter_fit": {
            "multi_session": "questions can be replayed sequentially as subtasks in one benchmark workspace",
            "memory_dependency": "later subtasks should depend on facts, constraints, or results from earlier subtasks",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test public agent-memory benchmark datasets.")
    parser.add_argument("--sample-len", type=int, default=5)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("eval/agent_memory_bench_smoke_report.json"),
    )
    args = parser.parse_args()

    report = {
        "generated_at": int(time.time()),
        "sample_len": args.sample_len,
        "benchmarks": {
            "ama_bench": _summarize_ama(args.sample_len),
            "memoryarena": _summarize_memoryarena(args.sample_len),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
