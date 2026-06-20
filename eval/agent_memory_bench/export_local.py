from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .dataset import (
    AgentMemoryCase,
    evermem_rows_to_cases,
    groupmem_domain_to_probe_cases,
    socialmem_rows_to_cases,
    write_longmemeval_json,
)


GROUPMEM_FILES = {
    "Finance": "data/final/Finance/synthetic_domain_channels_rolevariants_Finance.json",
    "Technology": "data/final/Technology/synthetic_domain_channels_rolevariants_Technology.json",
    "Healthcare": "data/final/Healthcare/synthetic_domain_channels_rolevariants_Healthcare.json",
    "Manufacturing": "data/final/Manufacturing/synthetic_domain_channels_rolevariants_Manufacturing.json",
}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_parquet_rows(path: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "pyarrow is required for local SocialMemBench export; install it with "
            "`uv pip install --python .\\.venv\\Scripts\\python.exe pyarrow`."
        ) from exc
    return pq.read_table(path).to_pylist()


def _export_evermem(
    dataset_root: Path,
    *,
    topics: list[str],
    max_refs_per_case: int,
    max_messages_per_ref: int,
) -> list[AgentMemoryCase]:
    root = dataset_root / "evermembench_dynamic"
    qa_rows: list[dict[str, Any]] = []
    dialogue_rows: list[dict[str, Any]] = []
    for topic in topics:
        qa_path = root / topic / f"qa_{topic}.json"
        dialogue_path = root / topic / "dialogue.json"
        if not qa_path.exists() or not dialogue_path.exists():
            raise FileNotFoundError(f"missing EverMemBench topic files for {topic}: {root / topic}")
        qa_data = _load_json(qa_path)
        dialogue_data = _load_json(dialogue_path)
        if not isinstance(qa_data, list):
            raise ValueError(f"expected list in {qa_path}")
        if not isinstance(dialogue_data, list):
            raise ValueError(f"expected list in {dialogue_path}")
        qa_rows.extend(qa_data)
        dialogue_rows.extend(dialogue_data)
    return evermem_rows_to_cases(
        qa_rows,
        dialogue_rows,
        max_refs_per_case=max_refs_per_case,
        max_messages_per_ref=max_messages_per_ref,
    )


def _export_socialmem(
    dataset_root: Path,
    *,
    context: str,
) -> list[AgentMemoryCase]:
    root = dataset_root / "socialmembench"
    qa_path = root / "qa.parquet"
    conversations_path = root / "conversations.parquet"
    if not qa_path.exists() or not conversations_path.exists():
        raise FileNotFoundError(f"missing SocialMemBench parquet files under {root}")
    return socialmem_rows_to_cases(
        _read_parquet_rows(qa_path),
        _read_parquet_rows(conversations_path),
        context=context,
    )


def _export_groupmem(
    dataset_root: Path,
    *,
    subset: str,
    limit: int,
    max_context_messages: int,
) -> list[AgentMemoryCase]:
    root = dataset_root / "groupmembench"
    domains = list(GROUPMEM_FILES) if subset == "all" else [subset]
    cases: list[AgentMemoryCase] = []
    for domain in domains:
        filename = GROUPMEM_FILES.get(domain)
        if filename is None:
            raise ValueError(f"unknown GroupMemBench subset {domain!r}")
        path = root / filename
        if not path.exists():
            raise FileNotFoundError(f"missing GroupMemBench file: {path}")
        remaining = limit - len(cases) if limit > 0 else 0
        if limit > 0 and remaining <= 0:
            break
        cases.extend(
            groupmem_domain_to_probe_cases(
                domain,
                _load_json(path),
                limit=remaining if limit > 0 else 0,
                max_context_messages=max_context_messages,
            )
        )
    return cases[:limit] if limit > 0 else cases


def _write_summary(cases: list[AgentMemoryCase], path: Path) -> None:
    counts: dict[str, int] = {}
    question_types: dict[str, int] = {}
    turn_counts: list[int] = []
    session_counts: list[int] = []
    for case in cases:
        source_key = f"{case.benchmark}:{case.subset}"
        counts[source_key] = counts.get(source_key, 0) + 1
        question_types[case.question_type] = question_types.get(case.question_type, 0) + 1
        session_counts.append(len(case.haystack_sessions))
        turn_counts.append(sum(len(session) for session in case.haystack_sessions))
    summary = {
        "cases": len(cases),
        "by_source": counts,
        "by_question_type": question_types,
        "haystack_sessions": {
            "min": min(session_counts or [0]),
            "max": max(session_counts or [0]),
            "avg": round(sum(session_counts) / len(session_counts), 2) if session_counts else 0.0,
        },
        "haystack_turns": {
            "min": min(turn_counts or [0]),
            "max": max(turn_counts or [0]),
            "avg": round(sum(turn_counts) / len(turn_counts), 2) if turn_counts else 0.0,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def _topics(value: str) -> list[str]:
    topics = [item.strip() for item in value.split(",") if item.strip()]
    if not topics:
        raise argparse.ArgumentTypeError("expected at least one topic id")
    return topics


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export locally downloaded agent-memory benchmarks to LongMemEval JSON."
    )
    parser.add_argument(
        "benchmark",
        choices=["evermem-full", "socialmem-full", "groupmem-probe"],
    )
    parser.add_argument("--dataset-root", type=Path, default=Path("eval/datasets"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, default=None)
    parser.add_argument("--topics", type=_topics, default="01,02,03,04,05")
    parser.add_argument("--max-refs-per-case", type=int, default=0)
    parser.add_argument("--max-messages-per-ref", type=int, default=0)
    parser.add_argument("--social-context", choices=["network", "evidence"], default="network")
    parser.add_argument("--subset", default="all")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Case cap for generated GroupMemBench probes; 0 means all probes.",
    )
    parser.add_argument(
        "--max-context-messages",
        type=int,
        default=120,
        help="Messages per GroupMemBench channel used as context; 0 means full channel.",
    )
    args = parser.parse_args()

    if args.benchmark == "evermem-full":
        cases = _export_evermem(
            args.dataset_root,
            topics=args.topics,
            max_refs_per_case=args.max_refs_per_case,
            max_messages_per_ref=args.max_messages_per_ref,
        )
    elif args.benchmark == "socialmem-full":
        cases = _export_socialmem(
            args.dataset_root,
            context=args.social_context,
        )
    else:
        cases = _export_groupmem(
            args.dataset_root,
            subset=args.subset,
            limit=args.limit,
            max_context_messages=args.max_context_messages,
        )

    write_longmemeval_json(cases, args.output)
    summary_path = args.summary_output or args.output.with_suffix(".summary.json")
    _write_summary(cases, summary_path)
    print(f"exported {len(cases)} cases -> {args.output}")
    print(f"summary -> {summary_path}")


if __name__ == "__main__":
    main()
