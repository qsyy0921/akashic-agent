"""Scoring functions for LongMemEval results.

Primary metric: token-level F1 (same as SQuAD / LoCoMo papers).
Secondary: exact match (normalised).
Tertiary: LLM-as-judge (semantic correctness, handles paraphrase/translation).
Both are computed per question_type so you can see where the system
is strong vs weak (single-session vs multi-session vs temporal etc.).
"""

from __future__ import annotations

import logging
import os
import re
import string
from collections import Counter

logger = logging.getLogger(__name__)

_JUDGE_PROMPT = """\
Question: {question}
Gold answer: {gold}
Predicted answer: {predicted}

Is the predicted answer semantically correct and consistent with the gold answer?
Reply with exactly one word: yes or no."""

_JUDGE_SYSTEM_PROMPT = (
    "You are a strict binary judge for a long-term memory benchmark. "
    "Output only one word: yes or no. Do not explain."
)

_DEFAULT_JUDGE_MAX_TOKENS = 1024
_MIN_JUDGE_RETRY_MAX_TOKENS = 1024


# ── text normalisation ────────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokenise(text: str) -> list[str]:
    return _normalise(text).split()


# ── per-pair metrics ──────────────────────────────────────────────────────────

def token_f1(pred: str, gold: str) -> float:
    pred_tokens = _tokenise(pred)
    gold_tokens = _tokenise(gold)
    if not pred_tokens or not gold_tokens:
        return float(pred_tokens == gold_tokens)
    common = Counter(pred_tokens) & Counter(gold_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(pred_tokens)
    recall = num_same / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def exact_match(pred: str, gold: str) -> bool:
    return _normalise(pred) == _normalise(gold)


# ── llm judge ────────────────────────────────────────────────────────────────

def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(1, int(raw.strip()))
    except ValueError:
        logger.warning("invalid %s=%r; using %s", name, raw, default)
        return default


def default_judge_max_tokens() -> int:
    return _env_int("AKASHIC_LME_JUDGE_MAX_TOKENS", _DEFAULT_JUDGE_MAX_TOKENS)


def _judge_retry_max_tokens(max_tokens: int) -> int:
    env_value = _env_int("AKASHIC_LME_JUDGE_RETRY_MAX_TOKENS", 0)
    if env_value > 0:
        return env_value
    return max(_MIN_JUDGE_RETRY_MAX_TOKENS, max_tokens * 4)


def _parse_yes_no(content: str | None) -> bool | None:
    verdict = str(content or "").strip().lower()
    if not verdict:
        return None
    token = re.sub(r"[^a-z]", "", verdict.split()[0]) if verdict.split() else ""
    if token.startswith("yes"):
        return True
    if token.startswith("no"):
        return False
    return None


async def judge_answer(
    provider,
    model: str,
    *,
    question: str,
    gold: str,
    predicted: str,
    max_tokens: int | None = None,
) -> bool | None:
    """Single LLM call: returns True if predicted is semantically correct."""
    if not predicted or not predicted.strip():
        return False
    prompt = _JUDGE_PROMPT.format(
        question=question.strip(),
        gold=gold.strip(),
        predicted=predicted.strip(),
    )
    max_tokens = max(1, int(max_tokens or default_judge_max_tokens()))
    retry_max_tokens = _judge_retry_max_tokens(max_tokens)
    for attempt, token_budget in enumerate((max_tokens, retry_max_tokens), start=1):
        try:
            resp = await provider.chat(
                messages=[
                    {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                tools=[],
                model=model,
                max_tokens=token_budget,
                tool_choice="none",
                disable_thinking=True,
            )
            parsed = _parse_yes_no(getattr(resp, "content", None))
            if parsed is not None:
                return parsed
            logger.warning(
                "judge_answer returned no yes/no content on attempt %s with max_tokens=%s",
                attempt,
                token_budget,
            )
        except Exception as e:
            logger.warning("judge_answer failed on attempt %s: %s", attempt, e)
            return None
    return None


# ── dataset-level scoring ─────────────────────────────────────────────────────

def score_results(results: list[dict]) -> dict:
    """Compute aggregate and per-type scores.

    Args:
        results: List of dicts from qa_runner.run_qa_instance.

    Returns:
        {
            "overall": {"f1": float, "em": float, "n": int, "errors": int},
            "by_type": {question_type: {"f1": float, "em": float, "n": int}},
        }
    """
    by_type: dict[str, list[dict]] = {}
    for r in results:
        qt = r.get("question_type") or "unknown"
        by_type.setdefault(qt, []).append(r)

    def _agg(items: list[dict]) -> dict:
        errors = sum(1 for r in items if r.get("error"))
        f1s = [
            0.0 if r.get("error") else token_f1(r["predicted_answer"], r["gold_answer"])
            for r in items
        ]
        ems = [
            0.0 if r.get("error") else (1.0 if exact_match(r["predicted_answer"], r["gold_answer"]) else 0.0)
            for r in items
        ]
        judged = [r for r in items if r.get("judge_correct") is not None and not r.get("error")]
        judge_acc = round(sum(1 for r in judged if r["judge_correct"]) / len(judged), 4) if judged else None
        n = len(items)
        if n == 0:
            return {"f1": 0.0, "em": 0.0, "judge_acc": None, "n": 0, "errors": 0}
        result = {
            "f1": round(sum(f1s) / n, 4),
            "em": round(sum(ems) / n, 4),
            "n": n,
            "errors": errors,
        }
        if judge_acc is not None:
            result["judge_acc"] = judge_acc
        return result

    return {
        "overall": _agg(results),
        "by_type": {qt: _agg(items) for qt, items in sorted(by_type.items())},
    }
