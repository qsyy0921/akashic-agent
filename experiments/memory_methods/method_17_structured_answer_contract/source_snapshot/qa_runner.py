"""Phase 3: run the real agent loop for each QA pair."""

from __future__ import annotations

import asyncio
import logging
import time

from datetime import datetime, timezone

from bus.events import InboundMessage

from .dataset import LMEInstance
from .runtime import BenchmarkRuntime

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 180.0


def _parse_question_date(raw: str) -> datetime:
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(tz=timezone.utc)

_TOOL_EMOJI = {
    "recall_memory":    "🔍",
    "search_messages":  "🔎",
    "fetch_messages":   "📄",
    "memorize":         "💾",
    "web_search":       "🌐",
    "web_fetch":        "🌐",
    "shell":            "💻",
}
_DEFAULT_TOOL_EMOJI = "🔧"


def _extract_tool_trace(session_manager, qa_key: str) -> list[dict]:
    """Pull the tool_chain from the last assistant message in the QA session."""
    try:
        session_manager._cache.pop(qa_key, None)
        session = session_manager.get_or_create(qa_key)
        for msg in reversed(session.messages):
            if msg.get("role") == "assistant" and msg.get("tool_chain"):
                return msg["tool_chain"]
    except Exception as e:
        logger.debug("tool_trace extraction failed: %s", e)
    return []


def format_tool_trace(tool_chain: list[dict], *, width: int = 90) -> str:
    """Render tool_chain as a readable log block with emojis."""
    if not tool_chain:
        return "  (no tool calls)"

    lines = []
    for step_i, group in enumerate(tool_chain, 1):
        text = (group.get("text") or "").strip()
        calls = group.get("calls") or []

        if text:
            # Truncate long reasoning text
            preview = text[:300] + ("…" if len(text) > 300 else "")
            for ln in preview.splitlines():
                lines.append(f"  🧠 {ln}")

        for call in calls:
            name = call.get("name", "?")
            emoji = _TOOL_EMOJI.get(name, _DEFAULT_TOOL_EMOJI)
            args = call.get("arguments") or {}

            # Show the most informative argument
            arg_preview = ""
            for key in ("query", "ids", "source_ref", "source_refs", "command"):
                val = args.get(key)
                if val:
                    s = str(val)[:80]
                    arg_preview = f"{key}={s!r}"
                    break

            result_raw = str(call.get("result") or "")
            result_preview = result_raw[:120].replace("\n", " ")
            if len(result_raw) > 120:
                result_preview += "…"

            lines.append(f"  {emoji} {name}({arg_preview})")
            lines.append(f"     ↳ {result_preview}")

    return "\n".join(lines)


async def run_qa_instance(
    rt: BenchmarkRuntime,
    instance: LMEInstance,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> dict:
    """Run one QA turn and return a result dict with tool trace."""
    from .methods import (
        apply_structured_answer_contract_to_result,
        reset_benchmark_question_context,
        set_benchmark_question_context,
    )

    loop = rt.core.loop
    qa_key = instance.qa_session_key

    rt.core.session_manager._cache.pop(qa_key, None)

    t0 = time.monotonic()
    error: str | None = None
    predicted = ""

    try:
        question_dt = _parse_question_date(instance.question_date)
        msg = InboundMessage(
            channel="lme",
            sender="user",
            chat_id=instance.question_id,
            content=instance.question + "\n\n[Respond in English only. One sentence or short phrase.]",
            timestamp=question_dt,
        )
        context_token = set_benchmark_question_context(
            {
                "question_id": instance.question_id,
                "question_type": instance.question_type,
                "question": instance.question,
                "question_date": instance.question_date,
                "haystack_session_ids": list(instance.haystack_session_ids),
            }
        )
        try:
            outbound = await asyncio.wait_for(
                loop._process(msg, session_key=qa_key, dispatch_outbound=False),
                timeout=timeout_s,
            )
        finally:
            reset_benchmark_question_context(context_token)
        predicted = outbound.content if outbound else ""
        react_stats = _extract_react_stats_from_outbound(outbound)
    except asyncio.TimeoutError:
        error = f"timeout after {timeout_s}s"
        logger.warning("QA timeout: %s", instance.question_id)
        react_stats = {}
    except Exception as exc:
        error = str(exc)
        logger.exception("QA error: %s", instance.question_id)
        react_stats = {}

    elapsed = time.monotonic() - t0
    tool_chain = _extract_tool_trace(rt.core.session_manager, qa_key)

    result = {
        "question_id": instance.question_id,
        "question_type": instance.question_type,
        "question": instance.question,
        "gold_answer": instance.answer,
        "predicted_answer": predicted,
        "tool_chain": tool_chain,
        "react_stats": react_stats,
        "elapsed_s": round(elapsed, 2),
        "error": error,
    }
    apply_structured_answer_contract_to_result(result, rt.method)
    return result


def _extract_react_stats_from_outbound(outbound: object) -> dict[str, object]:
    metadata = getattr(outbound, "metadata", None)
    if not isinstance(metadata, dict):
        return {}
    context_retry = metadata.get("context_retry")
    if not isinstance(context_retry, dict):
        return {}
    react_stats = context_retry.get("react_stats")
    return dict(react_stats) if isinstance(react_stats, dict) else {}
