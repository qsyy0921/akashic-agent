"""Summarize a memory-method benchmark result into reproducible artifacts."""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

from .metrics import exact_match, token_f1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create metrics.json, error_cases.json and comparison tables for a memory method."
    )
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--method-dir", required=True, type=Path)
    parser.add_argument("--method-id", default="")
    parser.add_argument("--run-log", type=Path, default=None)
    parser.add_argument(
        "--comparison-dir",
        type=Path,
        default=Path("eval/results/memory_methods"),
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    payload = _load_json(args.result)
    method_dir = args.method_dir
    method_dir.mkdir(parents=True, exist_ok=True)

    method_id = args.method_id or _method_id(payload, method_dir)
    metrics = summarize_payload(payload, method_id=method_id, result_path=args.result)
    errors = error_cases(payload)

    (method_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (method_dir / "error_cases.json").write_text(
        json.dumps(errors, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if args.run_log is not None and args.run_log.exists():
        shutil.copy2(args.run_log, method_dir / "run.log")
    elif not (method_dir / "run.log").exists():
        (method_dir / "run.log").write_text(
            "No external run log was provided for this summarized result.\n",
            encoding="utf-8",
        )

    update_comparison(
        methods_root=method_dir.parent,
        comparison_dir=args.comparison_dir,
    )


def summarize_payload(
    payload: dict[str, Any],
    *,
    method_id: str,
    result_path: Path,
) -> dict[str, Any]:
    results = _results(payload)
    latencies = [float(r.get("elapsed_s") or 0.0) for r in results]
    tool_counts = [_tool_call_count(r) for r in results]
    tool_result_chars = [_tool_result_chars(r) for r in results]
    answer_chars = [len(str(r.get("predicted_answer") or "")) for r in results]
    input_token_estimates = [
        _react_stat_number(r, "turn_input_sum_tokens")
        for r in results
        if _react_stat_number(r, "turn_input_sum_tokens") is not None
    ]
    final_call_token_estimates = [
        _react_stat_number(r, "final_call_input_tokens")
        for r in results
        if _react_stat_number(r, "final_call_input_tokens") is not None
    ]
    cache_prompt_tokens = [
        _react_stat_number(r, "cache_prompt_tokens")
        for r in results
        if _react_stat_number(r, "cache_prompt_tokens") is not None
    ]
    cache_hit_tokens = [
        _react_stat_number(r, "cache_hit_tokens")
        for r in results
        if _react_stat_number(r, "cache_hit_tokens") is not None
    ]
    judged = [r for r in results if r.get("judge_correct") is not None]
    judge_acc = (
        sum(1 for r in judged if r.get("judge_correct") is True) / len(judged)
        if judged
        else None
    )
    computed_scores = _compute_scores(results)
    error_labels = Counter(label for r in results for label in classify_error(r))
    return {
        "method_id": method_id,
        "generated_at": datetime.now().isoformat(),
        "result_path": str(result_path),
        "dataset": payload.get("data"),
        "workspace": payload.get("workspace"),
        "memory_method": payload.get("memory_method"),
        "limit": payload.get("limit"),
        "offset": payload.get("offset"),
        "n": len(results),
        "scores": computed_scores,
        "judge_acc": judge_acc,
        "latency": {
            "avg_elapsed_s": _avg(latencies),
            "p50_elapsed_s": _p50(latencies),
            "p95_elapsed_s": _p95(latencies),
        },
        "token_usage": {
            "avg_token_usage": _avg(input_token_estimates),
            "avg_token_usage_source": (
                "react_stats.turn_input_sum_tokens estimate"
                if input_token_estimates
                else None
            ),
            "actual_avg_total_tokens": None,
            "note": (
                "Provider prompt/completion usage is not stored yet. "
                "avg_token_usage uses the agent loop's input-token estimate when react_stats exists; "
                "proxy character fields are kept separate and must not be reported as token counts."
            ),
            "avg_input_tokens_estimate": _avg(input_token_estimates),
            "avg_final_call_input_tokens_estimate": _avg(final_call_token_estimates),
            "avg_cache_prompt_tokens": _avg(cache_prompt_tokens),
            "avg_cache_hit_tokens": _avg(cache_hit_tokens),
            "avg_answer_chars": _avg(answer_chars),
            "avg_tool_result_chars": _avg(tool_result_chars),
            "avg_tool_calls": _avg(tool_counts),
        },
        "failure_counts": {
            "runtime_errors": sum(1 for r in results if r.get("error")),
            "judge_false": sum(1 for r in results if r.get("judge_correct") is False),
            "judge_null": sum(1 for r in results if r.get("judge_correct") is None),
            "error_type_counts": dict(sorted(error_labels.items())),
        },
    }


def error_cases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    cases = []
    for result in _results(payload):
        labels = classify_error(result)
        if not labels and result.get("judge_correct") is True and not result.get("error"):
            continue
        cases.append(
            {
                "question_id": result.get("question_id"),
                "question_type": result.get("question_type"),
                "labels": labels or ["judge_uncertain"],
                "judge_correct": result.get("judge_correct"),
                "f1": round(token_f1(
                    str(result.get("predicted_answer") or ""),
                    str(result.get("gold_answer") or ""),
                ), 4),
                "em": exact_match(
                    str(result.get("predicted_answer") or ""),
                    str(result.get("gold_answer") or ""),
                ),
                "question": result.get("question"),
                "predicted_answer": result.get("predicted_answer"),
                "gold_answer": result.get("gold_answer"),
                "tool_summary": _tool_summary(result),
                "elapsed_s": result.get("elapsed_s"),
                "error": result.get("error"),
            }
        )
    return cases


def classify_error(result: dict[str, Any]) -> list[str]:
    if result.get("judge_correct") is True and not result.get("error"):
        return []
    labels: list[str] = []
    predicted = str(result.get("predicted_answer") or "").strip()
    qtype = str(result.get("question_type") or "")
    tool_summary = _tool_summary(result)
    recall_items = tool_summary["recall_items"]
    search_calls = tool_summary["search_messages_calls"]
    fetch_calls = tool_summary["fetch_messages_calls"]
    if result.get("error"):
        labels.append("runtime_error")
    if not predicted or _looks_like_refusal(predicted):
        labels.append("answer_format_error")
    if recall_items == 0 and search_calls > 0:
        labels.append("memory_write_failure")
    if recall_items == 0 and search_calls == 0:
        labels.append("retrieval_failure")
    if recall_items > 0 and fetch_calls == 0:
        labels.append("retrieved_but_evidence_unused")
    if recall_items > 0 and fetch_calls > 0:
        labels.append("retrieved_but_answer_wrong")
    if qtype == "knowledge-update":
        labels.append("memory_update_not_versioned")
    if qtype == "single-session-preference":
        labels.append("personal_preference_attribution_error")
    return _dedupe(labels)


def update_comparison(*, methods_root: Path, comparison_dir: Path) -> None:
    comparison_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for metrics_path in sorted(methods_root.glob("*/metrics.json")):
        metrics = _load_json(metrics_path)
        overall = ((metrics.get("scores") or {}).get("overall") or {})
        latency = metrics.get("latency") or {}
        failures = metrics.get("failure_counts") or {}
        rows.append(
            {
                "method_id": metrics.get("method_id") or metrics_path.parent.name,
                "n": metrics.get("n"),
                "judge_acc": overall.get("judge_acc") or metrics.get("judge_acc"),
                "f1": overall.get("f1"),
                "em": overall.get("em"),
                "avg_elapsed_s": latency.get("avg_elapsed_s"),
                "runtime_errors": failures.get("runtime_errors"),
                "judge_false": failures.get("judge_false"),
                "metrics_path": str(metrics_path),
            }
        )
    (comparison_dir / "comparison.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (comparison_dir / "comparison.md").write_text(
        _comparison_markdown(rows),
        encoding="utf-8",
    )


def _comparison_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Memory Method Comparison",
        "",
        "| method | n | judge_acc | F1 | EM | avg_elapsed_s | judge_false |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {method_id} | {n} | {judge_acc} | {f1} | {em} | {avg_elapsed_s} | {judge_false} |".format(
                method_id=row.get("method_id"),
                n=_fmt(row.get("n")),
                judge_acc=_fmt(row.get("judge_acc")),
                f1=_fmt(row.get("f1")),
                em=_fmt(row.get("em")),
                avg_elapsed_s=_fmt(row.get("avg_elapsed_s")),
                judge_false=_fmt(row.get("judge_false")),
            )
        )
    return "\n".join(lines) + "\n"


def _compute_scores(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        by_type.setdefault(str(result.get("question_type") or "unknown"), []).append(result)

    def agg(items: list[dict[str, Any]]) -> dict[str, Any]:
        judged = [r for r in items if r.get("judge_correct") is not None]
        return {
            "f1": round(mean(
                token_f1(str(r.get("predicted_answer") or ""), str(r.get("gold_answer") or ""))
                for r in items
            ), 4) if items else 0.0,
            "em": round(mean(
                1.0 if exact_match(
                    str(r.get("predicted_answer") or ""),
                    str(r.get("gold_answer") or ""),
                ) else 0.0
                for r in items
            ), 4) if items else 0.0,
            "judge_acc": round(
                sum(1 for r in judged if r.get("judge_correct") is True) / len(judged),
                4,
            ) if judged else None,
            "n": len(items),
            "errors": sum(1 for r in items if r.get("error")),
        }

    return {
        "overall": agg(results),
        "by_type": {key: agg(value) for key, value in sorted(by_type.items())},
    }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("results")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _method_id(payload: dict[str, Any], method_dir: Path) -> str:
    method = payload.get("memory_method")
    if isinstance(method, dict) and method.get("method_id"):
        return str(method["method_id"])
    return method_dir.name


def _tool_call_count(result: dict[str, Any]) -> int:
    return sum(len(group.get("calls") or []) for group in result.get("tool_chain") or [])


def _react_stat_number(result: dict[str, Any], key: str) -> float | None:
    stats = result.get("react_stats")
    if not isinstance(stats, dict):
        return None
    value = stats.get(key)
    if not isinstance(value, int | float):
        return None
    return float(value)


def _tool_result_chars(result: dict[str, Any]) -> int:
    total = 0
    for group in result.get("tool_chain") or []:
        for call in group.get("calls") or []:
            total += len(str(call.get("result") or ""))
    return total


def _tool_summary(result: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "recall_memory_calls": 0,
        "recall_items": 0,
        "search_messages_calls": 0,
        "fetch_messages_calls": 0,
        "tool_names": [],
    }
    names: list[str] = []
    for group in result.get("tool_chain") or []:
        for call in group.get("calls") or []:
            name = str(call.get("name") or "")
            names.append(name)
            if name == "recall_memory":
                summary["recall_memory_calls"] += 1
                parsed = _parse_tool_json(call.get("result"))
                items = parsed.get("items")
                if isinstance(items, list):
                    summary["recall_items"] += len(items)
            elif name == "search_messages":
                summary["search_messages_calls"] += 1
            elif name == "fetch_messages":
                summary["fetch_messages_calls"] += 1
    summary["tool_names"] = names
    return summary


def _parse_tool_json(value: object) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _looks_like_refusal(text: str) -> bool:
    lower = text.lower()
    return any(
        phrase in lower
        for phrase in (
            "i don't know",
            "i do not know",
            "can't find",
            "cannot find",
            "not enough information",
            "no memory",
        )
    )


def _dedupe(labels: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for label in labels:
        if label not in seen:
            seen.add(label)
            result.append(label)
    return result


def _avg(values: list[float | int]) -> float | None:
    return round(float(mean(values)), 4) if values else None


def _p50(values: list[float]) -> float | None:
    return round(float(median(values)), 4) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
    return round(float(ordered[index]), 4)


def _fmt(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


if __name__ == "__main__":
    main()
