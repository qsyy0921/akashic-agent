from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .metrics import score_results


def _load_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object payload in {path}")
    if not isinstance(payload.get("results"), list):
        raise ValueError(f"missing results array in {path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge sharded LongMemEval result JSON files.")
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    inputs: list[dict[str, Any]] = []
    for path in args.results:
        payload = _load_payload(path)
        inputs.append(
            {
                "path": str(path),
                "data": payload.get("data"),
                "offset": payload.get("offset"),
                "limit": payload.get("limit"),
                "n": len(payload.get("results") or []),
            }
        )
        for result in payload["results"]:
            question_id = str(result.get("question_id") or "")
            key = question_id or json.dumps(result, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            merged.append(result)

    judged = [r for r in merged if r.get("judge_correct") is not None]
    judge_acc = sum(1 for r in judged if r["judge_correct"]) / len(judged) if judged else None
    output_payload = {
        "timestamp": datetime.now().isoformat(),
        "merged_from": inputs,
        "scores": score_results(merged),
        "judge_acc": judge_acc,
        "results": merged,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"merged {len(merged)} unique results -> {args.output}")


if __name__ == "__main__":
    main()
