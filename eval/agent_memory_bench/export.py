from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from .dataset import (
    AgentMemoryCase,
    ama_rows_to_cases,
    evermem_rows_to_cases,
    groupmem_domain_to_probe_cases,
    memoryarena_rows_to_cases,
    socialmem_rows_to_cases,
    write_longmemeval_json,
)
from .fetch import fetch_hf_all_row_payloads, fetch_hf_rows, row_payloads


MEMORYARENA_CONFIGS = (
    "bundled_shopping",
    "progressive_search",
    "group_travel_planner",
    "formal_reasoning_math",
    "formal_reasoning_phys",
)

GROUPMEM_FILES = {
    "Finance": "data/final/Finance/synthetic_domain_channels_rolevariants_Finance.json",
    "Technology": "data/final/Technology/synthetic_domain_channels_rolevariants_Technology.json",
    "Healthcare": "data/final/Healthcare/synthetic_domain_channels_rolevariants_Healthcare.json",
    "Manufacturing": "data/final/Manufacturing/synthetic_domain_channels_rolevariants_Manufacturing.json",
}


def _fetch_ama(*, limit: int, offset: int, max_qa_per_episode: int) -> list[AgentMemoryCase]:
    data = fetch_hf_rows(
        dataset="AMA-bench/AMA-bench",
        config="default",
        split="test",
        offset=offset,
        length=limit,
    )
    return ama_rows_to_cases(
        row_payloads(data),
        max_qa_per_episode=max_qa_per_episode,
    )


def _fetch_memoryarena(
    *,
    subset: str,
    limit: int,
    offset: int,
    max_steps_per_row: int,
) -> list[AgentMemoryCase]:
    subsets = MEMORYARENA_CONFIGS if subset == "all" else (subset,)
    cases: list[AgentMemoryCase] = []
    for config in subsets:
        data = fetch_hf_rows(
            dataset="ZexueHe/memoryarena",
            config=config,
            split="test",
            offset=offset,
            length=limit,
        )
        cases.extend(
            memoryarena_rows_to_cases(
                row_payloads(data),
                subset=config,
                max_steps_per_row=max_steps_per_row,
            )
        )
    return cases


def _fetch_evermem(
    *,
    limit: int,
    offset: int,
    dialogue_limit: int,
    max_refs_per_case: int,
    max_messages_per_ref: int,
) -> list[AgentMemoryCase]:
    qars = row_payloads(
        fetch_hf_rows(
            dataset="EverMind-AI/EverMemBench-Dynamic",
            config="qars",
            split="train",
            offset=offset,
            length=limit,
        )
    )
    dialogue_rows = fetch_hf_all_row_payloads(
        dataset="EverMind-AI/EverMemBench-Dynamic",
        config="dialogues",
        split="train",
        limit=dialogue_limit,
    )
    return evermem_rows_to_cases(
        qars,
        dialogue_rows,
        max_refs_per_case=max_refs_per_case,
        max_messages_per_ref=max_messages_per_ref,
    )


def _fetch_socialmem(
    *,
    limit: int,
    offset: int,
    context: str,
    conversation_limit: int,
) -> list[AgentMemoryCase]:
    qa_rows = row_payloads(
        fetch_hf_rows(
            dataset="anon4data/socialmembench",
            config="qa",
            split="train",
            offset=offset,
            length=limit,
        )
    )
    conversation_rows = fetch_hf_all_row_payloads(
        dataset="anon4data/socialmembench",
        config="conversations",
        split="train",
        limit=conversation_limit,
        page_size=1000,
    )
    return socialmem_rows_to_cases(
        qa_rows,
        conversation_rows,
        context=context,
    )


def _download_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "akashic-agent-memory-bench/0.1"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_groupmem(
    *,
    subset: str,
    limit: int,
    max_context_messages: int,
) -> list[AgentMemoryCase]:
    domains = list(GROUPMEM_FILES) if subset == "all" else [subset]
    cases: list[AgentMemoryCase] = []
    for domain in domains:
        filename = GROUPMEM_FILES.get(domain)
        if not filename:
            raise ValueError(
                f"unknown GroupMemBench subset {domain!r}; choices: all, "
                + ", ".join(GROUPMEM_FILES)
            )
        url = (
            "https://huggingface.co/datasets/kimperyang/GroupMemBench/resolve/main/"
            + filename
        )
        domain_data = _download_json(url)
        remaining = max(0, limit - len(cases)) if limit > 0 else 0
        domain_limit = remaining if limit > 0 else 10
        cases.extend(
            groupmem_domain_to_probe_cases(
                domain,
                domain_data,
                limit=domain_limit,
                max_context_messages=max_context_messages,
            )
        )
        if limit > 0 and len(cases) >= limit:
            break
    return cases[:limit] if limit > 0 else cases


def _write_summary(cases: list[AgentMemoryCase], path: Path) -> None:
    counts: dict[str, int] = {}
    turn_counts: list[int] = []
    for case in cases:
        key = f"{case.benchmark}:{case.subset}"
        counts[key] = counts.get(key, 0) + 1
        turn_counts.append(sum(len(session) for session in case.haystack_sessions))
    summary = {
        "cases": len(cases),
        "by_source": counts,
        "haystack_turns": {
            "min": min(turn_counts or [0]),
            "max": max(turn_counts or [0]),
            "avg": round(sum(turn_counts) / len(turn_counts), 2) if turn_counts else 0.0,
        },
    }
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export public agent-memory benchmarks into LongMemEval-compatible JSON."
    )
    parser.add_argument(
        "benchmark",
        choices=["ama", "memoryarena", "evermem", "socialmem", "groupmem"],
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--subset", default="progressive_search")
    parser.add_argument("--max-qa-per-episode", type=int, default=2)
    parser.add_argument("--max-steps-per-row", type=int, default=2)
    parser.add_argument(
        "--dialogue-limit",
        type=int,
        default=0,
        help="EverMemBench dialogue rows to index; 0 fetches all dialogue rows.",
    )
    parser.add_argument("--max-refs-per-case", type=int, default=0)
    parser.add_argument("--max-messages-per-ref", type=int, default=0)
    parser.add_argument(
        "--social-context",
        choices=["network", "evidence"],
        default="network",
        help="SocialMemBench context to ingest: full network or evidence anchors only.",
    )
    parser.add_argument(
        "--conversation-limit",
        type=int,
        default=0,
        help="SocialMemBench conversation rows to index; 0 fetches all rows.",
    )
    parser.add_argument(
        "--max-context-messages",
        type=int,
        default=120,
        help="GroupMemBench messages to ingest per channel for generated probes.",
    )
    args = parser.parse_args()

    if args.benchmark == "ama":
        cases = _fetch_ama(
            limit=args.limit,
            offset=args.offset,
            max_qa_per_episode=args.max_qa_per_episode,
        )
    elif args.benchmark == "memoryarena":
        cases = _fetch_memoryarena(
            subset=args.subset,
            limit=args.limit,
            offset=args.offset,
            max_steps_per_row=args.max_steps_per_row,
        )
    elif args.benchmark == "evermem":
        cases = _fetch_evermem(
            limit=args.limit,
            offset=args.offset,
            dialogue_limit=args.dialogue_limit,
            max_refs_per_case=args.max_refs_per_case,
            max_messages_per_ref=args.max_messages_per_ref,
        )
    elif args.benchmark == "socialmem":
        cases = _fetch_socialmem(
            limit=args.limit,
            offset=args.offset,
            context=args.social_context,
            conversation_limit=args.conversation_limit,
        )
    else:
        cases = _fetch_groupmem(
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
