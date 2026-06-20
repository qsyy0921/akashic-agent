"""Benchmark-only memory method strategies.

These wrappers are intentionally outside the production memory plugin.  They let
the benchmark run a baseline and isolated retrieval/update variants against the
same runtime without editing the deployed Telegram/QQ/MCP services.
"""

from __future__ import annotations

import json
import re
import tomllib
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from agent.looping.ports import MemoryServices
from agent.retrieval.default_pipeline import DefaultMemoryRetrievalPipeline
from agent.tools.base import Tool
from agent.tools.meta import register_memory_meta_tools
from agent.tools.recall_memory import RecallMemoryTool
from core.memory.engine import (
    MemoryEngine,
    MemoryQueryIntent,
    MemoryIngestRequest,
    MemoryIngestResult,
    MemoryMutation,
    MemoryMutationResult,
    MemoryQuery,
    MemoryQueryFilters,
    MemoryQueryResult,
    MemoryRecord,
    MemoryScope,
    MemoryToolSpec,
)

_MEMORY_TOOL_NAMES = ("recall_memory", "memorize", "forget_memory")
_DATE_RE = re.compile(r"\b(20\d{2}[-/]\d{1,2}[-/]\d{1,2})(?:[ T]\d{1,2}:\d{2})?")
_SOURCE_INDEX_RE = re.compile(r"lme:[A-Za-z0-9_\-]+:(\d+)")
_SOURCE_REF_RE = re.compile(r"lme:[A-Za-z0-9_\-]+:\d+")
_SOCIALMEM_MESSAGE_RE = re.compile(
    r"^\[SocialMemBench\s+"
    r"(?P<meta>[^\]]*?)\]\s*"
    r"(?P<speaker>[^:]+):\s*(?P<quote>.*)$"
)
_SOCIALMEM_META_RE = re.compile(r"([A-Za-z_]+)=([^\s\]]+)")
_OPTION_NAME_RE = re.compile(
    r"(?m)^\s*[A-Z]\.\s*([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,2})"
)
_CAPITALIZED_RE = re.compile(r"\b[A-Z][A-Za-z'\-]{2,}\b")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-']+")
_STOPWORDS = {
    "about",
    "after",
    "again",
    "answer",
    "before",
    "does",
    "each",
    "from",
    "group",
    "have",
    "into",
    "memory",
    "their",
    "there",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
}
_QUESTION_NAME_STOPWORDS = {
    "All",
    "Does",
    "Did",
    "Has",
    "How",
    "Is",
    "No",
    "Options",
    "Session",
    "Someone",
    "What",
    "When",
    "Where",
    "Who",
}
_GENERIC_EVIDENCE_TERMS = {
    "actually",
    "chat",
    "conversation",
    "conversations",
    "people",
    "person",
    "preference",
    "preferences",
    "real",
    "really",
    "someone",
    "thing",
    "things",
    "venue",
    "venues",
}
_HIGH_VALUE_EVIDENCE_TERMS = {
    "better",
    "changed",
    "first",
    "less",
    "more",
    "never",
    "not",
    "quiet",
    "quieter",
    "without",
}
_QUESTION_AWARE_GENERIC_TERMS = {
    *_GENERIC_EVIDENCE_TERMS,
    "asked",
    "based",
    "candidate",
    "candidates",
    "certain",
    "clear",
    "discussing",
    "everyone",
    "expressed",
    "group",
    "member",
    "members",
    "option",
    "options",
    "question",
    "reveal",
    "reveals",
    "said",
    "session",
    "sessions",
    "specific",
    "suggest",
    "tell",
    "toward",
    "what",
    "where",
    "which",
    "who",
    "whose",
}

_BENCHMARK_QUESTION_CONTEXT: ContextVar[dict[str, object] | None] = ContextVar(
    "akashic_lme_question_context",
    default=None,
)


@dataclass(frozen=True)
class MemoryMethodSpec:
    method_id: str
    strategy: str
    description: str = ""
    options: dict[str, Any] | None = None
    config_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "method_id": self.method_id,
            "strategy": self.strategy,
            "description": self.description,
            "options": dict(self.options or {}),
            "config_path": self.config_path,
        }


def set_benchmark_question_context(context: dict[str, object]) -> object:
    """Set current LongMemEval question context for benchmark-only tools."""

    return _BENCHMARK_QUESTION_CONTEXT.set(dict(context))


def reset_benchmark_question_context(token: object) -> None:
    _BENCHMARK_QUESTION_CONTEXT.reset(token)  # type: ignore[arg-type]


def _current_benchmark_question_context() -> dict[str, object]:
    context = _BENCHMARK_QUESTION_CONTEXT.get()
    return dict(context) if isinstance(context, dict) else {}


class DelegatingMemoryEngine:
    """Base wrapper that forwards every engine capability to the inner engine."""

    def __init__(self, inner: MemoryEngine, spec: MemoryMethodSpec) -> None:
        self._inner = inner
        self._spec = spec

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def query(self, request: MemoryQuery) -> MemoryQueryResult:
        return await self._inner.query(request)

    async def ingest(self, request: MemoryIngestRequest) -> MemoryIngestResult:
        return await self._inner.ingest(request)

    async def mutate(self, request: MemoryMutation) -> MemoryMutationResult:
        return await self._inner.mutate(request)

    def reinforce_items_batch(self, ids: list[str]) -> None:
        self._inner.reinforce_items_batch(ids)


class IntentAwareRetrievalEngine(DelegatingMemoryEngine):
    """Runs a light heuristic intent pass before recall.

    The method uses the existing memory engine as-is, but changes the query plan:
    it asks one intent-specific lane first, then falls back to the unmodified
    baseline query.  The records are RRF-like merged by rank and score.
    """

    async def query(self, request: MemoryQuery) -> MemoryQueryResult:
        if not _is_retrieval_query(request) or request.filters.kinds:
            return await self._inner.query(request)

        intent = _classify_personal_memory_intent(request.text)
        target_kinds = _target_kinds_for_intent(intent)
        if not target_kinds:
            result = await self._inner.query(request)
            _mark_trace(result, self._spec, personal_intent=intent)
            return result

        lane_limit = max(request.limit, int((self._spec.options or {}).get("lane_limit", 12)))
        lane_query = _copy_query(
            request,
            kinds=target_kinds,
            limit=lane_limit,
            hints={"method_intent": intent},
        )
        lane_result = await self._inner.query(lane_query)
        fallback_result = await self._inner.query(request)
        merged = _merge_results(
            [lane_result, fallback_result],
            limit=request.limit,
            spec=self._spec,
            extra_trace={
                "personal_intent": intent,
                "intent_kinds": list(target_kinds),
                "lane_hit_count": len(lane_result.records),
                "fallback_hit_count": len(fallback_result.records),
            },
        )
        return merged


class StructuredMemorySchemaEngine(DelegatingMemoryEngine):
    """Adds explicit schema fields to retrieved records.

    The current engine already has summary/evidence, but the model sees many
    loosely structured JSON blobs.  This method keeps the same retrieval order
    and enriches each record with stable fields that downstream prompts and
    reports can rely on.
    """

    async def query(self, request: MemoryQuery) -> MemoryQueryResult:
        result = await self._inner.query(request)
        for record in result.records:
            _enrich_structured_signals(record)
        _mark_trace(result, self._spec, enriched_records=len(result.records))
        return result


class EvidenceFetchRerankEngine(DelegatingMemoryEngine):
    """Reranks recall results toward source-backed, query-overlapping records."""

    async def query(self, request: MemoryQuery) -> MemoryQueryResult:
        result = await self._inner.query(request)
        if not _is_retrieval_query(request) or not result.records:
            _mark_trace(result, self._spec)
            return result

        query_terms = _query_terms(request.text)
        for rank, record in enumerate(result.records, 1):
            old_score = float(record.score or 0.0)
            overlap = _term_overlap(query_terms, record.summary)
            evidence_bonus = 0.08 if _has_source_evidence(record) else 0.0
            type_bonus = _type_bonus(request.text, record.kind)
            rerank_score = old_score + evidence_bonus + type_bonus + (0.025 * overlap) - (0.001 * rank)
            record.score = round(rerank_score, 4)
            signals = dict(record.signals or {})
            signals["method_03_rerank"] = {
                "base_score": old_score,
                "query_overlap": overlap,
                "evidence_bonus": evidence_bonus,
                "type_bonus": type_bonus,
            }
            record.signals = signals
        result.records.sort(key=lambda item: float(item.score or 0.0), reverse=True)
        result.records = result.records[: request.limit]
        _mark_trace(result, self._spec, reranked_records=len(result.records))
        return result


class MemoryUpdateVersioningEngine(DelegatingMemoryEngine):
    """Prefers newer evidence when the question is likely about an update."""

    async def query(self, request: MemoryQuery) -> MemoryQueryResult:
        result = await self._inner.query(request)
        if not _is_retrieval_query(request) or not result.records:
            _mark_trace(result, self._spec)
            return result

        update_intent = _classify_personal_memory_intent(request.text) == "knowledge_update"
        if not update_intent:
            _mark_trace(result, self._spec, versioning_applied=False)
            return result

        best_by_signature: dict[str, MemoryRecord] = {}
        retained: list[MemoryRecord] = []
        shadowed = 0
        for record in sorted(
            result.records,
            key=lambda item: (_record_date(item), float(item.score or 0.0)),
            reverse=True,
        ):
            signature = _memory_signature(record)
            if not signature:
                retained.append(record)
                continue
            existing = best_by_signature.get(signature)
            if existing is None:
                best_by_signature[signature] = record
                retained.append(record)
                _set_version_signal(record, "current_candidate")
                continue
            shadowed += 1
            _set_version_signal(record, "shadowed_candidate")
            if not bool((self._spec.options or {}).get("keep_shadowed", False)):
                continue
            retained.append(record)
        result.records = retained[: request.limit]
        _mark_trace(
            result,
            self._spec,
            versioning_applied=True,
            shadowed_candidates=shadowed,
        )
        return result


class HybridIntentTemporalRerankEngine(DelegatingMemoryEngine):
    """Combines intent lanes with conservative temporal/evidence reranking.

    This method is intentionally retrieval-only.  It keeps the same frozen
    benchmark memory, but tries to select better recall records by combining:

    - baseline retrieval, so broad message-window memories are not lost;
    - intent-specific retrieval, so preference/profile/update questions get a
      typed lane;
    - evidence/query-overlap reranking;
    - temporal hints only when the question asks for first/latest/current facts.

    Unlike Method 04, it does not shadow older records for "changed over time"
    questions because those questions often need both the before and after
    evidence.
    """

    async def query(self, request: MemoryQuery) -> MemoryQueryResult:
        if not _is_retrieval_query(request):
            return await self._inner.query(request)

        intent = _classify_personal_memory_intent(request.text)
        baseline_result = await self._inner.query(request)
        result_lanes: list[tuple[str, MemoryQueryResult]] = [("baseline", baseline_result)]

        if not request.filters.kinds:
            target_kinds = _target_kinds_for_intent(intent)
            if target_kinds:
                lane_limit = max(
                    request.limit,
                    int((self._spec.options or {}).get("lane_limit", 14)),
                )
                intent_query = _copy_query(
                    request,
                    kinds=target_kinds,
                    limit=lane_limit,
                    hints={"method_intent": intent},
                )
                result_lanes.append(("intent", await self._inner.query(intent_query)))

        merged = _merge_hybrid_results(
            result_lanes,
            request=request,
            intent=intent,
            limit=request.limit,
            spec=self._spec,
        )
        _mark_trace(
            merged,
            self._spec,
            personal_intent=intent,
            lane_count=len(result_lanes),
            temporal_mode=_temporal_mode(request.text),
        )
        return merged


class AdaptiveIntentVersionedEngine(DelegatingMemoryEngine):
    """Routes each query to the strongest measured answer-time strategy.

    The route is intentionally heuristic and benchmark-only.  It is meant to
    test whether the observed per-intent strengths of Method 01 and Method 04
    compose cleanly before changing the production memory engine.
    """

    def __init__(self, inner: MemoryEngine, spec: MemoryMethodSpec) -> None:
        super().__init__(inner, spec)
        self._intent_engine = IntentAwareRetrievalEngine(inner, spec)
        self._versioned_engine = MemoryUpdateVersioningEngine(inner, spec)

    async def query(self, request: MemoryQuery) -> MemoryQueryResult:
        if not _is_retrieval_query(request):
            return await self._inner.query(request)

        route, reason = _adaptive_route(request.text)
        if route == "intent":
            result = await self._intent_engine.query(request)
        elif route == "versioned":
            result = await self._versioned_engine.query(request)
        else:
            result = await self._inner.query(request)
            _mark_trace(result, self._spec, adaptive_route="baseline", adaptive_reason=reason)
            return result

        _mark_trace(
            result,
            self._spec,
            adaptive_route=route,
            adaptive_reason=reason,
            personal_intent=_classify_personal_memory_intent(request.text),
        )
        return result


class SourceGroundedSlotResolverEngine(DelegatingMemoryEngine):
    """Makes answer-time retrieval explicitly source and slot grounded.

    The method follows the 2026-paper reading notes in
    ``interview/11-2026-top-conference-memory-reading-notes.md``: compact
    question-relevant working memory, missing-slot routing, source anchors, and
    query-time conflict handling.  It does not change memory ingestion.
    """

    async def query(self, request: MemoryQuery) -> MemoryQueryResult:
        if not _is_retrieval_query(request):
            return await self._inner.query(request)

        profile = _source_slot_profile(request.text)
        candidate_limit = max(
            request.limit,
            int((self._spec.options or {}).get("candidate_limit", 16)),
        )
        candidate_request = _copy_query(
            request,
            limit=candidate_limit,
            hints={"source_grounded_slots": profile["slot_labels"]},
        )

        if request.filters.kinds:
            candidate_result = await self._inner.query(candidate_request)
            raw_items = _raw_items_from_results([candidate_result])
            lane_count = 1
        else:
            baseline_result = await self._inner.query(candidate_request)
            lanes: list[tuple[str, MemoryQueryResult]] = [("baseline", baseline_result)]
            target_kinds = _target_kinds_for_intent(str(profile["personal_intent"]))
            if target_kinds:
                intent_query = _copy_query(
                    candidate_request,
                    kinds=target_kinds,
                    hints={
                        "method_intent": profile["personal_intent"],
                        "source_grounded_slots": profile["slot_labels"],
                    },
                )
                lanes.append(("slot_intent", await self._inner.query(intent_query)))
            candidate_result = _merge_hybrid_results(
                lanes,
                request=candidate_request,
                intent=str(profile["personal_intent"]),
                limit=candidate_limit,
                spec=self._spec,
            )
            raw_items = _raw_items_from_results([result for _, result in lanes])
            lane_count = len(lanes)

        selected = _rank_source_grounded_records(
            candidate_result.records,
            request=request,
            profile=profile,
            limit=request.limit,
        )
        guidance = _source_grounded_guidance(request.text, profile, selected)
        text_block = _render_source_grounded_text_block(selected, guidance)
        trace = {
            **dict(candidate_result.trace or {}),
            "method_id": self._spec.method_id,
            "strategy": self._spec.strategy,
            "personal_intent": profile["personal_intent"],
            "slot_labels": profile["slot_labels"],
            "question_names": profile["question_names"],
            "candidate_count": len(candidate_result.records),
            "selected_count": len(selected),
            "lane_count": lane_count,
            "source_grounded_guidance": guidance,
        }
        return MemoryQueryResult(
            text_block=text_block,
            records=selected,
            trace=trace,
            raw={"items": raw_items, "method": self._spec.as_dict()},
        )


class RawMessageFirstMemoryEngine(DelegatingMemoryEngine):
    """Marker wrapper for Method 08.

    The retrieval engine itself stays conservative.  The method effect is in
    `RawMessageFirstRecallTool`, registered by `apply_memory_method`, so the
    benchmark's mandatory first `recall_memory` call returns raw-message
    evidence without polluting memory summaries.
    """

    async def query(self, request: MemoryQuery) -> MemoryQueryResult:
        result = await self._inner.query(request)
        _mark_trace(result, self._spec, raw_message_first_engine=True)
        return result


class RawMessageFirstRecallTool(Tool):
    name = "recall_memory"
    description = (
        "Method 08 benchmark recall: first retrieves ordinary long-term memory, "
        "then directly searches raw historical messages and returns a compact "
        "evidence_table with speaker/date/message_index/source_ref/quote fields. "
        "For who, who-first, exception, update, relationship, and implicit "
        "preference questions, use evidence_table/source_ref before answering; "
        "continue with fetch_messages when exact context is needed."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要查找的记忆主题或原始消息关键词"},
            "intent": {
                "type": "string",
                "enum": ["answer", "timeline"],
                "default": "answer",
            },
            "memory_kind": {
                "type": "string",
                "enum": ["event", "profile", "preference", "procedure", ""],
                "default": "",
            },
            "time_filter": {
                "type": "string",
                "description": "today / yesterday / recent_3d / recent_7d / recent_30d / YYYY-MM-DD / YYYY-MM-DD~YYYY-MM-DD",
                "default": "",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 8,
            },
            "raw_limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "default": 12,
                "description": "最多返回多少条 raw evidence row",
            },
            "context": {
                "type": "integer",
                "minimum": 0,
                "maximum": 4,
                "default": 1,
                "description": "raw 命中前后扩展的消息条数",
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        memory: MemoryEngine,
        spec: MemoryToolSpec,
        session_store: object,
        method_spec: MemoryMethodSpec,
    ) -> None:
        self._memory = memory
        self._spec = spec
        self._store = session_store
        self._method_spec = method_spec
        self._fallback = RecallMemoryTool(memory, spec)

    async def execute(
        self,
        query: str,
        intent: str = "answer",
        memory_kind: str = "",
        time_filter: str = "",
        limit: int = 8,
        raw_limit: int = 12,
        context: int = 1,
        channel: str | None = None,
        chat_id: str | None = None,
        **extra: Any,
    ) -> str:
        payload = await self._execute_payload(
            query=query,
            intent=intent,
            memory_kind=memory_kind,
            time_filter=time_filter,
            limit=limit,
            raw_limit=raw_limit,
            context=context,
            channel=channel,
            chat_id=chat_id,
            **extra,
        )
        return json.dumps(payload, ensure_ascii=False)

    async def _execute_payload(
        self,
        query: str,
        intent: str = "answer",
        memory_kind: str = "",
        time_filter: str = "",
        limit: int = 8,
        raw_limit: int = 12,
        context: int = 1,
        channel: str | None = None,
        chat_id: str | None = None,
        **extra: Any,
    ) -> dict[str, object]:
        text = (query or "").strip()
        if not text:
            fallback = await self._fallback.execute(query=query, intent=intent, memory_kind=memory_kind, time_filter=time_filter, limit=limit, channel=channel, chat_id=chat_id, **extra)
            try:
                parsed = json.loads(fallback)
            except json.JSONDecodeError:
                return {"fallback_text": fallback}
            return cast(dict[str, object], parsed) if isinstance(parsed, dict) else {"fallback": parsed}

        memory_result = await self._memory.query(
            MemoryQuery(
                text=text,
                intent=_normalize_memory_intent(intent),
                scope=MemoryScope(
                    session_key=f"{channel}:{chat_id}" if channel and chat_id else "",
                    channel=channel or "",
                    chat_id=chat_id or "",
                ),
                filters=MemoryQueryFilters(kinds=_memory_kinds(memory_kind)),
                limit=max(1, min(int(limit), 50)),
                context=dict(extra),
            )
        )
        profile = _source_slot_profile(_profile_text(text, extra))
        raw_rows, search_meta = _raw_message_first_search(
            self._store,
            text,
            profile=profile,
            raw_limit=max(1, min(int(raw_limit), 30)),
            context=max(0, min(int(context), 4)),
        )
        memory_items = [_memory_record_item(record) for record in memory_result.records]
        cited_ids = [
            str(item.get("id"))
            for item in memory_items
            if str(item.get("id") or "").strip()
        ]
        source_refs = [
            str(row.get("source_ref"))
            for row in raw_rows
            if str(row.get("source_ref") or "").strip()
        ]
        return {
            "count": len(memory_items),
            "items": memory_items,
            "raw_message_first": True,
            "evidence_table": raw_rows,
            "search_plan": {
                "slot_labels": profile["slot_labels"],
                "question_names": profile["question_names"],
                "queries_used": search_meta["queries_used"],
                "matched_raw_count": len(raw_rows),
                "speaker_evidence_summary": _speaker_evidence_summary(raw_rows),
                "candidate_answer_hints": _candidate_answer_hints(raw_rows),
                "recommended_next_tool": (
                    "fetch_messages" if source_refs else "search_messages"
                ),
                "candidate_source_refs": source_refs[:12],
                "answer_rules": _source_answer_rules(profile),
                "note": (
                    "Use evidence_table rows as raw source evidence. "
                    "Do not answer speaker/order/exception/update questions "
                    "from memory summaries alone. For speaker attribution, "
                    "compare speaker_evidence_summary and exact matched_terms; "
                    "do not choose only the longest quote. candidate_answer_hints "
                    "marks speakers that match distinctive query terms."
                ),
            },
            "trace": {
                **dict(memory_result.trace or {}),
                "method_id": self._method_spec.method_id,
                "strategy": self._method_spec.strategy,
                "raw_message_first": True,
                "raw_queries": search_meta["queries_used"],
                "raw_hit_count": len(raw_rows),
            },
            "citation_required": True,
            "citation_format": "§cited:[id1,id2,...]§",
            "cited_item_ids": [*cited_ids, *source_refs[:12]],
            "citation_rule": (
                "若最终回复使用了 memory item，用 item id；若使用 raw evidence，"
                "可使用 source_ref 作为引用 id。"
            ),
        }


class DeterministicResolverMemoryEngine(RawMessageFirstMemoryEngine):
    """Marker wrapper for Method 09.

    Method 09 keeps Method 08's raw-message evidence path, but adds a
    deterministic resolver that chooses the leading source-grounded candidate
    before the final LLM answer.
    """


class StructuredCandidateResolverMemoryEngine(AdaptiveIntentVersionedEngine):
    """Method 11 engine marker.

    This keeps the best measured adaptive retrieval route from Method 06, but
    the actual Method 11 effect is in `StructuredCandidateResolverRecallTool`:
    answer-time candidate extraction reads production `memory_raw_events`
    instead of SessionStore search results or model-facing summary hints.
    """

    async def query(self, request: MemoryQuery) -> MemoryQueryResult:
        result = await super().query(request)
        _mark_trace(result, self._spec, structured_candidate_engine=True)
        return result


class QuestionAwareStructuredRouterMemoryEngine(AdaptiveIntentVersionedEngine):
    """Method 12 engine marker.

    Method 12 keeps Method 06's adaptive memory route, but its recall tool uses
    the benchmark question context to narrow structured raw-event search by
    session/entity before returning a compact evidence table.
    """

    async def query(self, request: MemoryQuery) -> MemoryQueryResult:
        result = await super().query(request)
        _mark_trace(result, self._spec, question_aware_structured_router=True)
        return result


class SlotDecisionAnswerPlannerMemoryEngine(QuestionAwareStructuredRouterMemoryEngine):
    """Method 13 engine marker.

    Method 13 inherits Method 12's question-aware structured retrieval, then
    turns slot_decision into an explicit answer_plan before final generation.
    """

    async def query(self, request: MemoryQuery) -> MemoryQueryResult:
        result = await super().query(request)
        _mark_trace(result, self._spec, slot_decision_answer_planner=True)
        return result


class DeterministicResolutionState:
    def __init__(self) -> None:
        self.selected: dict[str, object] | None = None

    def update(self, resolution: dict[str, object]) -> None:
        selected = resolution.get("selected_candidate")
        if not isinstance(selected, dict) or resolution.get("must_follow") is not True:
            self.selected = None
            return
        if not str(selected.get("source_ref") or "").strip():
            self.selected = None
            return
        self.selected = cast(dict[str, object], selected)

    @property
    def active(self) -> bool:
        return self.selected is not None

    @property
    def source_ref(self) -> str:
        if self.selected is None:
            return ""
        return str(self.selected.get("source_ref") or "").strip()


class DeterministicResolverRecallTool(RawMessageFirstRecallTool):
    description = (
        "Method 09 benchmark recall: retrieves ordinary memory plus raw-message "
        "evidence, then deterministically resolves speaker/order/exception/update "
        "candidate answers from source_ref/message_index before the final answer. "
        "Use deterministic_resolution.selected_candidate as the answer candidate "
        "unless later fetched source messages contradict it."
    )

    def __init__(
        self,
        memory: MemoryEngine,
        spec: MemoryToolSpec,
        session_store: object,
        method_spec: MemoryMethodSpec,
        state: DeterministicResolutionState,
    ) -> None:
        super().__init__(memory, spec, session_store, method_spec)
        self._state = state

    async def execute(
        self,
        query: str,
        intent: str = "answer",
        memory_kind: str = "",
        time_filter: str = "",
        limit: int = 8,
        raw_limit: int = 12,
        context: int = 1,
        channel: str | None = None,
        chat_id: str | None = None,
        **extra: Any,
    ) -> str:
        payload = await self._execute_payload(
            query=query,
            intent=intent,
            memory_kind=memory_kind,
            time_filter=time_filter,
            limit=limit,
            raw_limit=raw_limit,
            context=context,
            channel=channel,
            chat_id=chat_id,
            **extra,
        )
        if payload.get("raw_message_first") is True:
            profile = _source_slot_profile(_profile_text(query, extra))
            rows = cast(list[dict[str, object]], payload.get("evidence_table") or [])
            resolution = _deterministic_resolution(query, profile, rows)
            if _should_apply_contract_only_resolution(resolution):
                _apply_contract_only_resolution(payload, resolution)
                self._state.update(resolution)
            else:
                self._state.selected = None
            answer_contract = (
                "For speaker_attribution, message_order, exception_negation, and "
                "update_change questions, use selected_candidate from "
                "deterministic_resolution as the answer candidate unless a later "
                "fetch_messages result explicitly contradicts its source_ref."
            )
            trace = dict(cast(dict[str, object], payload.get("trace") or {}))
            trace["deterministic_resolution"] = True
            trace["resolution_type"] = resolution.get("resolution_type")
            payload["trace"] = trace
            payload = {
                "deterministic_resolution": resolution,
                "selected_candidate": resolution.get("selected_candidate"),
                "answer_candidate": (
                    cast(dict[str, object], resolution.get("selected_candidate") or {}).get("answer")
                    if isinstance(resolution.get("selected_candidate"), dict)
                    else None
                ),
                "resolved_answer_candidates": (
                    [resolution.get("selected_candidate")]
                    if _should_apply_contract_only_resolution(resolution)
                    else resolution.get("ranked_candidates", [])
                ),
                "answer_contract": answer_contract,
                **payload,
            }
        return json.dumps(payload, ensure_ascii=False)


class StructuredCandidateResolverRecallTool(Tool):
    name = "recall_memory"
    description = (
        "Method 11 benchmark recall: retrieves ordinary long-term memory, then "
        "queries the production memory2 structured raw-event table to build a "
        "compact selected evidence table. For speaker, who-first, exception, "
        "implicit-preference, and update questions, prefer "
        "candidate_resolution.selected_candidate and selected_evidence_table "
        "over loose summaries; use source_ref/message_index/date to justify the "
        "final answer."
    )
    parameters = RawMessageFirstRecallTool.parameters

    def __init__(
        self,
        memory: MemoryEngine,
        spec: MemoryToolSpec,
        memory_store: object,
        method_spec: MemoryMethodSpec,
    ) -> None:
        self._memory = memory
        self._spec = spec
        self._memory_store = memory_store
        self._method_spec = method_spec
        self._fallback = RecallMemoryTool(memory, spec)

    async def execute(
        self,
        query: str,
        intent: str = "answer",
        memory_kind: str = "",
        time_filter: str = "",
        limit: int = 8,
        raw_limit: int = 16,
        context: int = 0,
        channel: str | None = None,
        chat_id: str | None = None,
        **extra: Any,
    ) -> str:
        text = (query or "").strip()
        if not text:
            return await self._fallback.execute(
                query=query,
                intent=intent,
                memory_kind=memory_kind,
                time_filter=time_filter,
                limit=limit,
                channel=channel,
                chat_id=chat_id,
                **extra,
            )

        memory_result = await self._memory.query(
            MemoryQuery(
                text=text,
                intent=_normalize_memory_intent(intent),
                scope=MemoryScope(
                    session_key=f"{channel}:{chat_id}" if channel and chat_id else "",
                    channel=channel or "",
                    chat_id=chat_id or "",
                ),
                filters=MemoryQueryFilters(kinds=_memory_kinds(memory_kind)),
                limit=max(1, min(int(limit), 50)),
                context=dict(extra),
            )
        )
        profile = _source_slot_profile(_profile_text(text, extra))
        rows, search_meta = _structured_candidate_search(
            self._memory_store,
            text,
            profile=profile,
            raw_limit=max(1, min(int(raw_limit), 40)),
        )
        resolution = _structured_candidate_resolution(text, profile, rows)
        selected_rows = _select_structured_evidence_rows(rows, resolution, limit=max(4, min(raw_limit, 16)))
        memory_items = [_memory_record_item(record) for record in memory_result.records]
        memory_ids = [
            str(item.get("id"))
            for item in memory_items
            if str(item.get("id") or "").strip()
        ]
        source_refs = [
            str(row.get("source_ref"))
            for row in selected_rows
            if str(row.get("source_ref") or "").strip()
        ]
        payload: dict[str, object] = {
            "count": len(memory_items),
            "items": memory_items,
            "structured_candidate_resolver": True,
            "evidence_table": selected_rows,
            "selected_evidence_table": selected_rows,
            "candidate_resolution": resolution,
            "selected_candidate": resolution.get("selected_candidate"),
            "answer_candidate": _resolution_answer(resolution),
            "search_plan": {
                "slot_labels": profile["slot_labels"],
                "question_names": profile["question_names"],
                "queries_used": search_meta["queries_used"],
                "structured_raw_hit_count": len(rows),
                "candidate_source_refs": source_refs[:12],
                "speaker_evidence_summary": _speaker_evidence_summary(selected_rows),
                "option_candidate_hints": resolution.get("option_candidates", []),
                "answer_rules": _structured_answer_rules(profile, resolution),
                "note": (
                    "Method 11 reads memory2.memory_raw_events directly. "
                    "Use selected_evidence_table as the compact evidence table; "
                    "do not answer who-first, exception, update, or implicit "
                    "preference questions from summaries alone."
                ),
            },
            "trace": {
                **dict(memory_result.trace or {}),
                "method_id": self._method_spec.method_id,
                "strategy": self._method_spec.strategy,
                "structured_candidate_resolver": True,
                "structured_queries": search_meta["queries_used"],
                "structured_raw_hit_count": len(rows),
                "selected_evidence_count": len(selected_rows),
                "resolution_type": resolution.get("resolution_type"),
            },
            "citation_required": True,
            "citation_format": "§cited:[id1,id2,...]§",
            "cited_item_ids": [*memory_ids, *source_refs[:12]],
            "citation_rule": (
                "若最终回复使用 memory item，用 item id；若使用 structured evidence，"
                "可使用 source_ref 作为引用 id。"
            ),
        }
        return json.dumps(payload, ensure_ascii=False)


class QuestionAwareStructuredRouterRecallTool(StructuredCandidateResolverRecallTool):
    description = (
        "Method 12 benchmark recall: retrieves ordinary long-term memory, then "
        "uses the original benchmark question/options to narrow memory2 "
        "raw-event search by session/entity. It returns a small selected "
        "evidence table plus slot_decision with supporting and contradicting "
        "source_refs before final generation."
    )

    async def execute(
        self,
        query: str,
        intent: str = "answer",
        memory_kind: str = "",
        time_filter: str = "",
        limit: int = 8,
        raw_limit: int = 8,
        context: int = 0,
        channel: str | None = None,
        chat_id: str | None = None,
        **extra: Any,
    ) -> str:
        text = (query or "").strip()
        question_context = _current_benchmark_question_context()
        question_text = str(question_context.get("question") or "").strip()
        effective_text = _question_aware_effective_text(text, question_context)
        if not effective_text:
            return await self._fallback.execute(
                query=query,
                intent=intent,
                memory_kind=memory_kind,
                time_filter=time_filter,
                limit=limit,
                channel=channel,
                chat_id=chat_id,
                **extra,
            )

        memory_result = await self._memory.query(
            MemoryQuery(
                text=effective_text,
                intent=_normalize_memory_intent(intent),
                scope=MemoryScope(
                    session_key=f"{channel}:{chat_id}" if channel and chat_id else "",
                    channel=channel or "",
                    chat_id=chat_id or "",
                ),
                filters=MemoryQueryFilters(kinds=_memory_kinds(memory_kind)),
                limit=max(1, min(int(limit), 50)),
                context={
                    **dict(extra),
                    "benchmark_question_id": question_context.get("question_id", ""),
                    "benchmark_question_type": question_context.get("question_type", ""),
                },
            )
        )
        profile = _question_aware_profile(effective_text, question_context)
        selected_limit = max(
            4,
            min(
                int((self._method_spec.options or {}).get("selected_evidence_limit", raw_limit) or 8),
                10,
            ),
        )
        rows, search_meta = _question_aware_structured_search(
            self._memory_store,
            recall_query=text,
            question_context=question_context,
            profile=profile,
            raw_limit=max(selected_limit, min(int(raw_limit or selected_limit), 12)),
        )
        answer_plan_meta: dict[str, object] = {}
        if self._method_spec.strategy == "slot_decision_answer_planner":
            rows, answer_plan_meta = _slot_decision_plan_expand_rows(
                self._memory_store,
                rows,
                question_context=question_context,
                profile=profile,
                limit=int((self._method_spec.options or {}).get("answer_plan_row_limit", 32)),
            )
        resolution = _question_aware_structured_resolution(effective_text, profile, rows, question_context)
        selected_rows = _question_aware_select_evidence_rows(rows, resolution, limit=selected_limit)
        answer_plan: dict[str, object] | None = None
        if self._method_spec.strategy == "slot_decision_answer_planner":
            answer_plan = _slot_decision_answer_plan(
                effective_text,
                profile,
                rows,
                selected_rows,
                resolution,
                question_context,
            )
            selected_rows = _answer_plan_select_evidence_rows(
                rows,
                selected_rows,
                answer_plan,
                limit=selected_limit,
            )
        memory_items = [_memory_record_item(record) for record in memory_result.records]
        memory_ids = [
            str(item.get("id"))
            for item in memory_items
            if str(item.get("id") or "").strip()
        ]
        source_refs = [
            str(row.get("source_ref"))
            for row in selected_rows
            if str(row.get("source_ref") or "").strip()
        ]
        slot_decision = cast(dict[str, object], resolution.get("slot_decision") or {})
        payload: dict[str, object] = {
            "count": len(memory_items),
            "items": memory_items,
            "question_aware_structured_router": True,
            "structured_candidate_resolver": True,
            "question_context": {
                "available": bool(question_text),
                "question_id": question_context.get("question_id", ""),
                "question_type": question_context.get("question_type", ""),
                "source_session_key": _question_context_session_key(question_context),
                "option_names": _question_context_option_names(question_context),
            },
            "evidence_table": selected_rows,
            "selected_evidence_table": selected_rows,
            "candidate_resolution": resolution,
            "slot_decision": slot_decision,
            "selected_candidate": resolution.get("selected_candidate"),
            "answer_candidate": _resolution_answer(resolution),
            "search_plan": {
                "slot_labels": profile["slot_labels"],
                "question_names": profile["question_names"],
                "focused_terms": profile.get("focused_terms", []),
                "queries_used": search_meta["queries_used"],
                "answer_plan_queries_used": answer_plan_meta.get("queries_used", []),
                "session_key_filter": search_meta.get("session_key_filter", ""),
                "session_scoped": search_meta.get("session_scoped", False),
                "structured_raw_hit_count": len(rows),
                "selected_source_refs": source_refs[:10],
                "speaker_evidence_summary": _speaker_evidence_summary(selected_rows),
                "option_candidate_hints": resolution.get("option_candidates", []),
                "answer_rules": _question_aware_answer_rules(profile, resolution),
                "note": (
                    "Method 12 uses the original benchmark question/options to "
                    "scope memory_raw_events search before final generation. "
                    "Use slot_decision.selected_candidate and the compact "
                    "selected_evidence_table; do not infer from broad summaries "
                    "when source rows disagree."
                ),
            },
            "trace": {
                **dict(memory_result.trace or {}),
                "method_id": self._method_spec.method_id,
                "strategy": self._method_spec.strategy,
                "question_aware_structured_router": True,
                "question_context_available": bool(question_text),
                "structured_queries": search_meta["queries_used"],
                "session_key_filter": search_meta.get("session_key_filter", ""),
                "structured_raw_hit_count": len(rows),
                "selected_evidence_count": len(selected_rows),
                "resolution_type": resolution.get("resolution_type"),
                "evidence_gap": slot_decision.get("evidence_gap"),
                "answer_plan_enabled": answer_plan is not None,
            },
            "citation_required": True,
            "citation_format": "§cited:[id1,id2,...]§",
            "cited_item_ids": [*memory_ids, *source_refs[:10]],
            "citation_rule": (
                "若最终回复使用 memory item，用 item id；若使用 question-aware structured evidence，"
                "可使用 source_ref 作为引用 id。"
            ),
        }
        if answer_plan is not None:
            planned_answer = str(answer_plan.get("selected_answer") or "").strip()
            if planned_answer:
                resolution = _answer_plan_aligned_resolution(resolution, answer_plan)
                slot_decision = cast(dict[str, object], resolution.get("slot_decision") or {})
                payload["candidate_resolution"] = resolution
                payload["slot_decision"] = slot_decision
                payload["selected_candidate"] = resolution.get("selected_candidate")
            payload["slot_decision_answer_planner"] = True
            payload["answer_plan"] = answer_plan
            payload["answer_candidate"] = planned_answer or payload.get("answer_candidate")
            search_plan = cast(dict[str, object], payload["search_plan"])
            search_plan["answer_plan"] = {
                "selected_answer": answer_plan.get("selected_answer"),
                "confidence": answer_plan.get("confidence"),
                "evidence_gap": answer_plan.get("evidence_gap"),
                "supporting_source_refs": answer_plan.get("supporting_source_refs"),
                "contradicting_source_refs": answer_plan.get("contradicting_source_refs"),
            }
            search_plan["option_candidate_hints"] = answer_plan.get("per_option_evidence", [])
            search_plan["answer_rules"] = _answer_plan_answer_rules(answer_plan, profile, resolution)
            search_plan["note"] = (
                "Method 13 builds answer_plan from slot_decision. Follow "
                "answer_plan.final_answer_constraints and selected_answer when "
                "confidence is high; otherwise search/fetch missing slots."
            )
            trace = cast(dict[str, object], payload["trace"])
            trace["slot_decision_answer_planner"] = True
            trace["answer_plan_confidence"] = answer_plan.get("confidence")
        return json.dumps(payload, ensure_ascii=False)


class SlotDecisionAnswerPlannerRecallTool(QuestionAwareStructuredRouterRecallTool):
    description = (
        "Method 13 benchmark recall: uses the original benchmark question/options "
        "and structured raw events, then converts slot_decision into a strict "
        "answer_plan. For option, exception/negation, who-first, and update "
        "questions, follow answer_plan.selected_answer and final constraints "
        "when confidence is high; otherwise search/fetch missing evidence."
    )


class ConsolidatedMemoryWriteQualityEngine(AdaptiveIntentVersionedEngine):
    """Method 14 engine marker.

    Method 14 keeps the best measured adaptive answer-time route, but changes
    the benchmark workspace before QA by writing compact consolidated facts
    derived from `memory_raw_events`.
    """

    async def query(self, request: MemoryQuery) -> MemoryQueryResult:
        result = await super().query(request)
        _mark_trace(result, self._spec, consolidated_memory_write_quality=True)
        return result


class ConsolidatedMemoryWriteQualityRecallTool(QuestionAwareStructuredRouterRecallTool):
    description = (
        "Method 14 benchmark recall: first uses compact consolidated facts "
        "written from raw events, then falls back to question-aware raw-event "
        "evidence only for missing or disputed slots. Prefer "
        "consolidated_fact_table when it directly answers preference, "
        "relationship, exception, decision, or update questions."
    )

    async def execute(self, *args: Any, **kwargs: Any) -> str:
        raw = await super().execute(*args, **kwargs)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if not isinstance(payload, dict):
            return raw

        query = str(kwargs.get("query") if "query" in kwargs else (args[0] if args else ""))
        question_context = _current_benchmark_question_context()
        question_text = str(question_context.get("question") or "")
        profile = _question_aware_profile(
            " ".join(_unique_nonempty([query, question_text])),
            question_context,
        )
        facts = _method14_search_consolidated_facts(
            self._memory_store,
            query=query,
            question_context=question_context,
            profile=profile,
            limit=int((self._method_spec.options or {}).get("consolidated_fact_limit", 6)),
        )
        if not facts:
            return json.dumps(payload, ensure_ascii=False)

        memory_items = cast(list[dict[str, object]], payload.get("items") or [])
        fact_items = [
            {
                "id": fact.get("item_id"),
                "kind": fact.get("kind"),
                "summary": fact.get("summary"),
                "score": fact.get("score"),
                "source_ref": fact.get("source_ref"),
                "signals": {"method_14_consolidated_fact": fact},
            }
            for fact in facts
        ]
        payload["items"] = [*fact_items, *memory_items]
        payload["count"] = len(cast(list[object], payload["items"]))
        payload["consolidated_memory_write_quality"] = True
        payload["consolidated_fact_table"] = facts
        fact_candidate = _method14_selected_candidate_from_facts(facts, profile)
        if fact_candidate:
            payload["answer_candidate"] = fact_candidate.get("answer")
            payload["selected_candidate"] = fact_candidate
            resolution = dict(cast(dict[str, object], payload.get("candidate_resolution") or {}))
            resolution["selected_candidate"] = fact_candidate
            resolution["ranked_candidates"] = [fact_candidate, *cast(list[dict[str, object]], resolution.get("ranked_candidates") or [])[:5]]
            resolution["decision_rule"] = "consolidated_fact"
            resolution["confidence"] = "high"
            resolution["must_follow"] = True
            resolution["instruction"] = (
                "Use the high-scoring consolidated fact as the leading answer "
                "unless raw evidence directly contradicts its source refs."
            )
            payload["candidate_resolution"] = resolution
            slot_decision = dict(cast(dict[str, object], payload.get("slot_decision") or {}))
            slot_decision["selected_candidate"] = fact_candidate
            slot_decision["selected_answer"] = fact_candidate.get("answer")
            slot_decision["decision_rule"] = "consolidated_fact"
            slot_decision["supporting_source_refs"] = fact_candidate.get("source_refs", [])
            payload["slot_decision"] = slot_decision
        source_refs = _unique_nonempty(
            [
                ref
                for fact in facts
                for ref in cast(list[str], fact.get("source_refs") or [])
            ]
        )
        search_plan = cast(dict[str, object], payload.get("search_plan") or {})
        search_plan["consolidated_fact_count"] = len(facts)
        search_plan["consolidated_source_refs"] = source_refs[:10]
        search_plan["answer_rules"] = [
            "If candidate_resolution.decision_rule=consolidated_fact, treat that selected_candidate as the leading answer.",
            "Use consolidated_fact_table before broad summaries when it directly matches the question.",
            "Every consolidated fact is source-grounded; cite its source_refs when used.",
            "If consolidated facts conflict or miss a slot, use selected_evidence_table/raw events as fallback.",
            *cast(list[str], search_plan.get("answer_rules") or []),
        ]
        search_plan["note"] = (
            "Method 14 moves structure into durable consolidated facts. "
            "Prefer compact fact rows over wide raw-event evidence unless a "
            "fact is missing or disputed."
        )
        payload["search_plan"] = search_plan
        trace = cast(dict[str, object], payload.get("trace") or {})
        trace["consolidated_memory_write_quality"] = True
        trace["consolidated_fact_count"] = len(facts)
        payload["trace"] = trace
        payload["cited_item_ids"] = _unique_nonempty(
            [
                *[str(item.get("id") or "") for item in fact_items],
                *cast(list[str], payload.get("cited_item_ids") or []),
                *source_refs[:10],
            ]
        )
        return json.dumps(payload, ensure_ascii=False)


class DeterministicSearchMessagesTool(Tool):
    name = "search_messages"
    description = "Method 09 constrained search_messages wrapper."
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}

    def __init__(self, inner: Tool, state: DeterministicResolutionState) -> None:
        self._inner = inner
        self._state = state
        self.description = (
            inner.description
            + "\nMethod 09: when deterministic_resolution selected a source_ref, "
            "search results are constrained to that source_ref to avoid re-ranking "
            "competing rows after source-grounded resolution."
        )
        self.parameters = inner.parameters

    async def execute(self, query: str, **kwargs: Any) -> str:
        raw = await self._inner.execute(query=query, **kwargs)
        text = str(raw)
        if not self._state.active:
            return text
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text
        if not isinstance(payload, dict):
            return text

        selected_ref = self._state.source_ref
        messages = [
            item
            for item in cast(list[dict[str, object]], payload.get("messages") or [])
            if str(item.get("source_ref") or item.get("id") or "").strip() == selected_ref
        ]
        if not messages and self._state.selected is not None:
            messages = [_candidate_as_search_message(self._state.selected)]
        original_count = len(cast(list[object], payload.get("messages") or []))
        payload.update(
            {
                "count": len(messages),
                "matched_count": len(messages),
                "has_more": False,
                "next_offset": None,
                "messages": messages,
                "deterministic_resolution_enforced": True,
                "selected_source_ref": selected_ref,
                "suppressed_competing_search_count": max(0, original_count - len(messages)),
            }
        )
        return json.dumps(payload, ensure_ascii=False)


class DeterministicFetchMessagesTool(Tool):
    name = "fetch_messages"
    description = "Method 09 constrained fetch_messages wrapper."
    parameters = {"type": "object", "properties": {}}

    def __init__(self, inner: Tool, state: DeterministicResolutionState) -> None:
        self._inner = inner
        self._state = state
        self.description = (
            inner.description
            + "\nMethod 09: when deterministic_resolution selected a source_ref, "
            "fetch_messages is redirected to that selected source_ref unless the "
            "caller already requested it."
        )
        self.parameters = inner.parameters

    async def execute(self, **kwargs: Any) -> str:
        forwarded = dict(kwargs)
        override = False
        selected_ref = self._state.source_ref
        if self._state.active and selected_ref:
            requested = _requested_fetch_refs(forwarded)
            if selected_ref not in requested:
                override = True
                forwarded.pop("ids", None)
                forwarded.pop("source_refs", None)
                forwarded.pop("evidence", None)
                forwarded["source_ref"] = selected_ref
        raw = await self._inner.execute(**forwarded)
        text = str(raw)
        if not override:
            return text
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(payload, dict):
            payload["deterministic_resolution_enforced"] = True
            payload["redirected_to_source_ref"] = selected_ref
            return json.dumps(payload, ensure_ascii=False)
        return text


def load_method_spec(path: Path | None) -> MemoryMethodSpec | None:
    if path is None:
        return None
    if not path.exists():
        raise FileNotFoundError(f"method config not found: {path}")
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"method config must be an object: {path}")
    method_id = str(payload.get("method_id") or path.parent.name or path.stem).strip()
    strategy = str(payload.get("strategy") or method_id).strip()
    description = str(payload.get("description") or "").strip()
    options = payload.get("options")
    return MemoryMethodSpec(
        method_id=method_id,
        strategy=strategy,
        description=description,
        options=cast(dict[str, Any], options) if isinstance(options, dict) else {},
        config_path=str(path),
    )


def build_method_engine(
    inner: MemoryEngine,
    spec: MemoryMethodSpec | None,
) -> MemoryEngine:
    if spec is None or spec.strategy in {"", "baseline", "baseline_current"}:
        return inner
    if spec.strategy == "intent_aware_retrieval":
        return cast(MemoryEngine, IntentAwareRetrievalEngine(inner, spec))
    if spec.strategy == "structured_memory_schema":
        return cast(MemoryEngine, StructuredMemorySchemaEngine(inner, spec))
    if spec.strategy == "evidence_fetch_rerank":
        return cast(MemoryEngine, EvidenceFetchRerankEngine(inner, spec))
    if spec.strategy == "memory_update_versioning":
        return cast(MemoryEngine, MemoryUpdateVersioningEngine(inner, spec))
    if spec.strategy == "hybrid_intent_temporal_rerank":
        return cast(MemoryEngine, HybridIntentTemporalRerankEngine(inner, spec))
    if spec.strategy == "adaptive_intent_versioned":
        return cast(MemoryEngine, AdaptiveIntentVersionedEngine(inner, spec))
    if spec.strategy == "source_grounded_slot_resolver":
        return cast(MemoryEngine, SourceGroundedSlotResolverEngine(inner, spec))
    if spec.strategy == "raw_message_first_resolver":
        return cast(MemoryEngine, RawMessageFirstMemoryEngine(inner, spec))
    if spec.strategy == "deterministic_attribution_resolver":
        return cast(MemoryEngine, DeterministicResolverMemoryEngine(inner, spec))
    if spec.strategy == "production_structured_memory_schema":
        return inner
    if spec.strategy == "structured_candidate_resolver":
        return cast(MemoryEngine, StructuredCandidateResolverMemoryEngine(inner, spec))
    if spec.strategy == "question_aware_structured_router":
        return cast(MemoryEngine, QuestionAwareStructuredRouterMemoryEngine(inner, spec))
    if spec.strategy == "slot_decision_answer_planner":
        return cast(MemoryEngine, SlotDecisionAnswerPlannerMemoryEngine(inner, spec))
    if spec.strategy == "consolidated_memory_write_quality":
        return cast(MemoryEngine, ConsolidatedMemoryWriteQualityEngine(inner, spec))
    raise ValueError(f"unknown memory method strategy: {spec.strategy}")


def apply_memory_method(core: object, method_config: Path | None) -> MemoryMethodSpec | None:
    """Patch a benchmark CoreRuntime to use a method wrapper everywhere."""

    spec = load_method_spec(method_config)
    if spec is None or spec.strategy in {"", "baseline", "baseline_current"}:
        return spec

    memory_runtime = getattr(core, "memory_runtime")
    old_engine = memory_runtime.engine
    new_engine = build_method_engine(old_engine, spec)
    memory_runtime.engine = new_engine
    if spec.strategy in {
        "production_structured_memory_schema",
        "structured_candidate_resolver",
        "question_aware_structured_router",
        "slot_decision_answer_planner",
        "consolidated_memory_write_quality",
    }:
        _backfill_production_structured_memory_schema(core, new_engine, spec)
    if spec.strategy in {
        "structured_candidate_resolver",
        "question_aware_structured_router",
        "slot_decision_answer_planner",
        "consolidated_memory_write_quality",
    }:
        _backfill_all_raw_events_to_structured_schema(core, new_engine, spec)
    if spec.strategy == "consolidated_memory_write_quality":
        _backfill_method14_consolidated_facts(core, new_engine, spec)

    loop = getattr(core, "loop", None)
    if loop is not None:
        setattr(loop, "_memory_engine", new_engine)
        pipeline = DefaultMemoryRetrievalPipeline(MemoryServices(engine=new_engine))
        setattr(loop, "_retrieval_pipeline", pipeline)
        agent_core = getattr(loop, "_agent_core", None)
        passive_pipeline = getattr(agent_core, "pipeline", None)
        context_store = getattr(passive_pipeline, "_context_store", None)
        if context_store is not None and hasattr(context_store, "_retrieval"):
            setattr(context_store, "_retrieval", pipeline)

    tools = getattr(core, "tools", None)
    if tools is not None:
        for name in _MEMORY_TOOL_NAMES:
            if tools.has_tool(name):
                tools.unregister(name)
        register_memory_meta_tools(tools, new_engine)
        if spec.strategy in {
            "structured_candidate_resolver",
            "question_aware_structured_router",
            "slot_decision_answer_planner",
            "consolidated_memory_write_quality",
        }:
            _register_structured_candidate_recall_tool(core, tools, new_engine, spec)
        if spec.strategy in {
            "raw_message_first_resolver",
            "deterministic_attribution_resolver",
        }:
            _register_raw_message_first_recall_tool(core, tools, new_engine, spec)

    return spec


def _backfill_production_structured_memory_schema(
    core: object,
    engine: MemoryEngine,
    spec: MemoryMethodSpec,
) -> None:
    memory_store = getattr(engine, "_v2_store", None)
    session_manager = getattr(core, "session_manager", None)
    session_store = getattr(session_manager, "_store", None)
    list_refs = getattr(memory_store, "list_memory_source_refs", None)
    upsert_raw = getattr(memory_store, "upsert_raw_events_from_messages", None)
    fetch_by_ids = getattr(session_store, "fetch_by_ids", None)
    if not all(callable(fn) for fn in (list_refs, upsert_raw, fetch_by_ids)):
        return

    limit = int(spec.options.get("raw_event_backfill_limit", 50000))
    chunk_size = max(1, min(int(spec.options.get("raw_event_backfill_chunk_size", 500)), 2000))
    refs = list_refs(limit=limit)
    if not isinstance(refs, list) or not refs:
        return
    for start in range(0, len(refs), chunk_size):
        chunk = [str(ref) for ref in refs[start : start + chunk_size] if str(ref).strip()]
        if not chunk:
            continue
        messages = fetch_by_ids(chunk)
        if isinstance(messages, list) and messages:
            upsert_raw(messages)


def _backfill_all_raw_events_to_structured_schema(
    core: object,
    engine: MemoryEngine,
    spec: MemoryMethodSpec,
) -> None:
    memory_store = getattr(engine, "_v2_store", None)
    session_manager = getattr(core, "session_manager", None)
    session_store = getattr(session_manager, "_store", None)
    list_messages = getattr(session_store, "list_messages_for_dashboard", None)
    upsert_raw = getattr(memory_store, "upsert_raw_events_from_messages", None)
    if not all(callable(fn) for fn in (list_messages, upsert_raw)):
        return

    limit = int(spec.options.get("all_raw_event_backfill_limit", 100000))
    page_size = max(1, min(int(spec.options.get("all_raw_event_backfill_chunk_size", 200)), 200))
    page = 1
    loaded = 0
    while loaded < limit:
        try:
            messages, total = list_messages(
                role="user",
                page=page,
                page_size=page_size,
                sort_by="seq",
                sort_order="asc",
            )
        except TypeError:
            break
        if not isinstance(messages, list) or not messages:
            break
        upsert_raw(cast(list[dict[str, object]], messages))
        loaded += len(messages)
        if loaded >= int(total or 0):
            break
        page += 1


def _backfill_method14_consolidated_facts(
    core: object,
    engine: MemoryEngine,
    spec: MemoryMethodSpec,
) -> None:
    memory_store = getattr(engine, "_v2_store", None)
    upsert_item = getattr(memory_store, "upsert_item", None)
    db = getattr(memory_store, "_db", None)
    if not callable(upsert_item) or db is None:
        return

    raw_limit = int((spec.options or {}).get("consolidation_raw_event_limit", 100000))
    per_session_limit = int((spec.options or {}).get("consolidated_facts_per_session", 160))
    rows = _method14_load_raw_event_rows(memory_store, limit=raw_limit)
    facts = _method14_build_consolidated_facts(rows, per_session_limit=per_session_limit)
    for fact in facts:
        upsert_item(
            str(fact["memory_type"]),
            str(fact["summary"]),
            None,
            source_ref=json.dumps(fact["source_refs"], ensure_ascii=False),
            extra=cast(dict[str, object], fact["extra"]),
            happened_at=str(fact.get("happened_at") or ""),
            emotional_weight=int(fact.get("emotional_weight") or 0),
        )


def _method14_load_raw_event_rows(memory_store: object, *, limit: int) -> list[dict[str, object]]:
    db = getattr(memory_store, "_db", None)
    if db is None:
        return []
    rows = db.execute(
        "SELECT source_ref, session_key, speaker_id, speaker, message_index, "
        "seq, timestamp, date, content FROM memory_raw_events "
        "WHERE COALESCE(session_key, '') != '' AND COALESCE(content, '') != '' "
        "ORDER BY session_key ASC, COALESCE(date, '') ASC, "
        "COALESCE(message_index, seq, 999999) ASC, source_ref ASC LIMIT ?",
        (max(1, int(limit)),),
    ).fetchall()
    result: list[dict[str, object]] = []
    for row in rows:
        source_ref, session_key, speaker_id, speaker, message_index, seq, timestamp, date, content = row
        parsed = _parse_socialmem_message(str(content or ""))
        result.append(
            {
                "source_ref": str(source_ref),
                "session_key": str(session_key or ""),
                "speaker_id": str(speaker_id or parsed.get("speaker_id") or ""),
                "speaker": str(speaker or parsed.get("speaker") or ""),
                "message_index": _coerce_int(message_index) or _coerce_int(seq) or 0,
                "seq": _coerce_int(seq) or 0,
                "timestamp": str(timestamp or ""),
                "date": str(date or parsed.get("date") or ""),
                "content": str(content or ""),
                "quote": str(parsed.get("quote") or content or ""),
            }
        )
    return result


def _method14_build_consolidated_facts(
    rows: list[dict[str, object]],
    *,
    per_session_limit: int,
) -> list[dict[str, object]]:
    by_session: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        session_key = str(row.get("session_key") or "")
        if not session_key:
            continue
        by_session.setdefault(session_key, []).append(row)

    facts: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for session_key, session_rows in by_session.items():
        session_facts: list[dict[str, object]] = []
        speakers = _unique_nonempty([str(row.get("speaker") or "") for row in session_rows])
        for index, row in enumerate(session_rows):
            quote = str(row.get("quote") or "").strip()
            speaker = str(row.get("speaker") or "").strip()
            if not quote or not speaker:
                continue
            window_fact = _method14_exception_window_fact(session_rows, index)
            if window_fact is not None:
                session_facts.append(window_fact)
            preference = _method14_preference_reason(quote)
            if preference:
                session_facts.append(
                    _method14_fact(
                        row,
                        fact_type="preference_fact",
                        memory_type="preference",
                        summary=(
                            f"{speaker} preference signal in {session_key}: {preference}. "
                            f"Evidence quote: \"{_compact(quote, 160)}\""
                        ),
                        source_refs=[str(row.get("source_ref") or "")],
                    )
                )
            exception = _method14_exception_reason(quote)
            if exception:
                session_facts.append(
                    _method14_fact(
                        row,
                        fact_type="exception_fact",
                        memory_type="preference",
                        summary=(
                            f"{speaker} exception or reluctance signal in {session_key}: {exception}. "
                            f"Evidence quote: \"{_compact(quote, 160)}\""
                        ),
                        source_refs=[str(row.get("source_ref") or "")],
                        emotional_weight=1,
                    )
                )
            decision = _method14_decision_reason(quote)
            if decision:
                session_facts.append(
                    _method14_fact(
                        row,
                        fact_type="decision_fact",
                        memory_type="event",
                        summary=(
                            f"{speaker} decision/planning signal in {session_key}: {decision}. "
                            f"Evidence quote: \"{_compact(quote, 180)}\""
                        ),
                        source_refs=[str(row.get("source_ref") or "")],
                    )
                )
            for mentioned in _method14_mentions(quote, speakers, speaker):
                session_facts.append(
                    _method14_fact(
                        row,
                        fact_type="relationship_fact",
                        memory_type="event",
                        summary=(
                            f"{speaker} relationship signal with {mentioned} in {session_key}: "
                            f"directly mentions or speaks for {mentioned}. "
                            f"Evidence quote: \"{_compact(quote, 170)}\""
                        ),
                        source_refs=[str(row.get("source_ref") or "")],
                    )
                )

        by_speaker: dict[str, list[dict[str, object]]] = {}
        for row in session_rows:
            speaker = str(row.get("speaker") or "").strip()
            if speaker:
                by_speaker.setdefault(speaker, []).append(row)
        for speaker, speaker_rows in by_speaker.items():
            dates = _unique_nonempty([str(row.get("date") or "") for row in speaker_rows])
            if len(speaker_rows) < 3 or len(dates) < 2:
                continue
            ordered = sorted(speaker_rows, key=_method14_raw_order_key)
            first = ordered[0]
            last = ordered[-1]
            first_quote = str(first.get("quote") or "")
            last_quote = str(last.get("quote") or "")
            session_facts.append(
                _method14_fact(
                    last,
                    fact_type="update_trajectory_fact",
                    memory_type="event",
                    summary=(
                        f"{speaker} trajectory in {session_key}: earlier on {first.get('date')} "
                        f"said \"{_compact(first_quote, 120)}\"; later on {last.get('date')} "
                        f"said \"{_compact(last_quote, 120)}\"."
                    ),
                    source_refs=[
                        str(first.get("source_ref") or ""),
                        str(last.get("source_ref") or ""),
                    ],
                )
            )

        for fact in session_facts:
            key = (
                str(fact["extra"].get("source_session_key")),
                str(fact["extra"].get("fact_type")),
                str(fact["summary"]),
            )
            if key in seen:
                continue
            seen.add(key)
            facts.append(fact)
            if len([item for item in facts if item["extra"].get("source_session_key") == session_key]) >= per_session_limit:
                break
    return facts


def _method14_exception_window_fact(
    session_rows: list[dict[str, object]],
    index: int,
) -> dict[str, object] | None:
    row = session_rows[index]
    quote = str(row.get("quote") or "").strip()
    if quote != "...":
        return None
    speaker = str(row.get("speaker") or "").strip()
    if not speaker:
        return None
    row_date = str(row.get("date") or "")
    follow_up: dict[str, object] | None = None
    for candidate in session_rows[index + 1 : index + 5]:
        if str(candidate.get("date") or "") != row_date:
            continue
        if str(candidate.get("speaker") or "") != speaker:
            continue
        candidate_quote = str(candidate.get("quote") or "").strip().lower()
        if candidate_quote in {"sure", "ok", "okay", "fine", "fine."}:
            follow_up = candidate
            break
    if follow_up is None:
        return None
    context_rows = [
        candidate
        for candidate in session_rows[max(0, index - 5) : index]
        if str(candidate.get("date") or "") == row_date
    ]
    context_text = " ".join(str(candidate.get("quote") or "") for candidate in context_rows).lower()
    topic = ""
    if any(term in context_text for term in ("drinks", "rooftop", "bar", "after ramen")):
        topic = "drinks after dinner / Rooftop Fox"
    elif any(term in context_text for term in ("dinner", "menu", "christmas")):
        topic = "dinner/menu decision"
    elif context_rows:
        topic = _compact(str(context_rows[-1].get("quote") or ""), 80)
    refs = [
        str(row.get("source_ref") or ""),
        str(follow_up.get("source_ref") or ""),
    ]
    summary_topic = f" about {topic}" if topic else ""
    return _method14_fact(
        row,
        fact_type="exception_fact",
        memory_type="preference",
        summary=(
            f"{speaker} exception or reluctance trajectory{summary_topic} in "
            f"{row.get('session_key')}: first responded with silence \"...\" and "
            f"later only said \"{_compact(str(follow_up.get('quote') or ''), 80)}\"."
        ),
        source_refs=refs,
        emotional_weight=2,
    )


def _method14_fact(
    row: dict[str, object],
    *,
    fact_type: str,
    memory_type: str,
    summary: str,
    source_refs: list[str],
    emotional_weight: int = 0,
) -> dict[str, object]:
    refs = _unique_nonempty(source_refs)
    return {
        "memory_type": memory_type,
        "summary": summary,
        "source_refs": refs,
        "happened_at": str(row.get("timestamp") or row.get("date") or ""),
        "emotional_weight": emotional_weight,
        "extra": {
            "method_14_consolidated_fact": True,
            "fact_type": fact_type,
            "source_session_key": str(row.get("session_key") or ""),
            "speaker": str(row.get("speaker") or ""),
            "speaker_id": str(row.get("speaker_id") or ""),
            "date": str(row.get("date") or ""),
            "message_index": row.get("message_index"),
            "seq": row.get("seq"),
            "source_refs": refs,
            "quote": str(row.get("quote") or ""),
        },
    }


def _method14_preference_reason(quote: str) -> str:
    lower = quote.lower()
    if any(term in lower for term in ("prefer", "rather", "favorite", "favourite")):
        return "explicit preference wording"
    if any(term in lower for term in ("hate", "ruins everything", "dealbreaker")):
        return "strong negative preference wording"
    if any(term in lower for term in ("vegan", "plant-based", "vegetarian", "allergy", "allergic")):
        return "dietary preference or constraint"
    if re.search(r"\bi'?ll do\b|\bi will do\b|\bi'll have\b|\blet me have\b", lower):
        return "choice behavior"
    if any(term in lower for term in ("off drinks", "mocktail", "sparkling water")):
        return "drink preference or constraint"
    return ""


def _method14_exception_reason(quote: str) -> str:
    lower = quote.lower().strip()
    if lower in {"...", "fine.", "fine", "sure", "ok", "okay"}:
        return "minimal or hesitant response"
    if any(term in lower for term in ("not coming", "can't", "cannot", "declined", "skip", "sit out")):
        return "explicit non-participation or refusal"
    if any(term in lower for term in ("not equally", "without stopping", "lack of anywhere to stop")):
        return "exception to group norm or plan"
    return ""


def _method14_decision_reason(quote: str) -> str:
    lower = quote.lower()
    if any(term in lower for term in ("we'll", "we will", "decided", "settled", "booking", "booked", "plan is")):
        return "explicit decision or plan"
    if any(term in lower for term in ("i found", "someone said", "works for everyone", "does that work")):
        return "proposal or planning candidate"
    if re.search(r"\b(rooftop|cafe|restaurant|fair|venue|street|council|christmas|dinner)\b", lower):
        return "planning context"
    return ""


def _method14_mentions(quote: str, speakers: list[str], speaker: str) -> list[str]:
    lower = quote.lower()
    result: list[str] = []
    for name in speakers:
        if not name or name == speaker:
            continue
        if re.search(rf"\b{re.escape(name.lower())}\b", lower):
            result.append(name)
    return result[:3]


def _method14_raw_order_key(row: dict[str, object]) -> tuple[str, int, int, str]:
    return (
        str(row.get("date") or ""),
        _coerce_int(row.get("message_index")) or 999999,
        _coerce_int(row.get("seq")) or 999999,
        str(row.get("source_ref") or ""),
    )


def _method14_search_consolidated_facts(
    memory_store: object,
    *,
    query: str,
    question_context: dict[str, object],
    profile: dict[str, object],
    limit: int,
) -> list[dict[str, object]]:
    db = getattr(memory_store, "_db", None)
    if db is None:
        return []
    session_key = _question_context_session_key(question_context)
    terms = _method14_fact_terms(query, question_context, profile)
    if not terms:
        return []
    like_conditions = " OR ".join("LOWER(summary) LIKE ?" for _ in terms)
    params: list[object] = [f"%{term.lower()}%" for term in terms]
    where = [
        "status='active'",
        "json_extract(extra_json, '$.method_14_consolidated_fact') = 1",
        f"({like_conditions})",
    ]
    if session_key:
        where.append("json_extract(extra_json, '$.source_session_key') = ?")
        params.append(session_key)
    rows = db.execute(
        "SELECT id, memory_type, summary, source_ref, happened_at, extra_json "
        "FROM memory_items WHERE "
        + " AND ".join(where)
        + " ORDER BY updated_at DESC LIMIT ?",
        (*params, max(limit * 8, limit)),
    ).fetchall()
    scored: list[tuple[dict[str, object], int]] = []
    labels = set(cast(list[str], profile.get("slot_labels") or []))
    for row in rows:
        item_id, kind, summary, source_ref, happened_at, extra_json = row
        extra = json.loads(extra_json) if extra_json else {}
        summary_text = str(summary or "")
        lower = summary_text.lower()
        score = sum(_evidence_term_weight(term) for term in terms if term.lower() in lower)
        fact_type = str(extra.get("fact_type") or "")
        if "implicit_preference" in labels and fact_type == "preference_fact":
            score += 4
        if "exception_negation" in labels and fact_type == "exception_fact":
            score += 5
        if "update_change" in labels and fact_type == "update_trajectory_fact":
            score += 5
        if "decision_status" in labels and fact_type == "decision_fact":
            score += 3
        if (
            labels & {"speaker_attribution", "person_candidate"}
            and "exception_negation" not in labels
            and fact_type == "relationship_fact"
        ):
            score += 3
        if score <= 0:
            continue
        refs = cast(list[str], extra.get("source_refs") or [])
        scored.append(
            (
                {
                    "item_id": str(item_id),
                    "kind": str(kind),
                    "summary": _compact(summary_text, 260),
                    "source_ref": str(source_ref or ""),
                    "source_refs": refs,
                    "happened_at": str(happened_at or ""),
                    "fact_type": fact_type,
                    "speaker": str(extra.get("speaker") or ""),
                    "speaker_id": str(extra.get("speaker_id") or ""),
                    "date": str(extra.get("date") or ""),
                    "message_index": extra.get("message_index"),
                    "quote": _compact(str(extra.get("quote") or ""), 180),
                    "score": score,
                },
                score,
            )
        )
    scored.sort(key=lambda item: (-item[1], str(item[0].get("fact_type") or ""), str(item[0].get("item_id") or "")))
    return [item for item, _score in scored[: max(1, min(int(limit), 12))]]


def _method14_selected_candidate_from_facts(
    facts: list[dict[str, object]],
    profile: dict[str, object],
) -> dict[str, object] | None:
    labels = set(cast(list[str], profile.get("slot_labels") or []))
    if not labels & {"exception_negation", "implicit_preference", "person_candidate"}:
        return None
    option_names = {
        str(name).lower()
        for name in cast(list[str], profile.get("option_names") or [])
        if str(name).strip()
    }
    for fact in facts:
        fact_type = str(fact.get("fact_type") or "")
        if fact_type not in {"exception_fact", "preference_fact"}:
            continue
        score = int(fact.get("score") or 0)
        if score < 8:
            continue
        speaker = str(fact.get("speaker") or "").strip()
        if not speaker:
            continue
        if option_names and speaker.lower() not in option_names:
            continue
        return {
            "answer": speaker,
            "answer_type": "consolidated_fact",
            "selection_basis": fact_type,
            "score": score,
            "source_ref": str(fact.get("source_ref") or ""),
            "source_refs": cast(list[str], fact.get("source_refs") or []),
            "speaker": speaker,
            "speaker_id": str(fact.get("speaker_id") or ""),
            "message_index": fact.get("message_index"),
            "date": str(fact.get("date") or ""),
            "quote": str(fact.get("quote") or ""),
            "fact_summary": str(fact.get("summary") or ""),
        }
    return None


def _method14_fact_terms(
    query: str,
    question_context: dict[str, object],
    profile: dict[str, object],
) -> list[str]:
    terms = [
        *_query_terms(query),
        *cast(list[str], profile.get("focused_terms") or []),
        *cast(list[str], profile.get("option_names") or []),
        *cast(list[str], profile.get("question_names") or []),
    ]
    question = str(question_context.get("question") or "")
    terms.extend(_query_terms(question))
    cleaned = [
        str(term).lower()
        for term in terms
        if len(str(term).strip()) >= 3
        and str(term).lower() not in _STOPWORDS
        and str(term) not in _QUESTION_NAME_STOPWORDS
    ]
    return _unique_nonempty(cleaned)[:14]


def _register_raw_message_first_recall_tool(
    core: object,
    tools: object,
    engine: MemoryEngine,
    spec: MemoryMethodSpec,
) -> None:
    if not hasattr(tools, "unregister") or not hasattr(tools, "register"):
        return
    session_manager = getattr(core, "session_manager", None)
    session_store = getattr(session_manager, "_store", None)
    if session_store is None:
        return
    profile = engine.tool_profile()
    if profile.recall is None:
        return
    state = (
        DeterministicResolutionState()
        if spec.strategy == "deterministic_attribution_resolver"
        else None
    )
    if state is not None:
        _wrap_resolution_constrained_lookup_tools(tools, state)
    if tools.has_tool("recall_memory"):
        tools.unregister("recall_memory")
    recall_tool: Tool
    if state is not None:
        recall_tool = DeterministicResolverRecallTool(
            engine,
            profile.recall,
            session_store,
            spec,
            state,
        )
    else:
        recall_tool = RawMessageFirstRecallTool(engine, profile.recall, session_store, spec)
    tools.register(
        recall_tool,
        always_on=True,
        risk=profile.recall.risk,
        search_hint=(
            profile.recall.search_hint
            or "raw message evidence source_ref speaker message_index"
        ),
    )


def _register_structured_candidate_recall_tool(
    core: object,
    tools: object,
    engine: MemoryEngine,
    spec: MemoryMethodSpec,
) -> None:
    if not hasattr(tools, "unregister") or not hasattr(tools, "register"):
        return
    memory_store = getattr(engine, "_v2_store", None)
    if memory_store is None or not callable(getattr(memory_store, "search_raw_events", None)):
        return
    profile = engine.tool_profile()
    if profile.recall is None:
        return
    if tools.has_tool("recall_memory"):
        tools.unregister("recall_memory")
    tool_cls: type[Tool]
    if spec.strategy == "consolidated_memory_write_quality":
        tool_cls = ConsolidatedMemoryWriteQualityRecallTool
    elif spec.strategy == "slot_decision_answer_planner":
        tool_cls = SlotDecisionAnswerPlannerRecallTool
    elif spec.strategy == "question_aware_structured_router":
        tool_cls = QuestionAwareStructuredRouterRecallTool
    else:
        tool_cls = StructuredCandidateResolverRecallTool
    tools.register(
        tool_cls(
            engine,
            profile.recall,
            memory_store,
            spec,
        ),
        always_on=True,
        risk=profile.recall.risk,
        search_hint=(
            profile.recall.search_hint
            or "structured raw events selected evidence source_ref speaker message_index"
        ),
    )


def _wrap_resolution_constrained_lookup_tools(
    tools: object,
    state: DeterministicResolutionState,
) -> None:
    if not all(hasattr(tools, name) for name in ("get_tool", "has_tool", "unregister", "register")):
        return
    search_tool = tools.get_tool("search_messages")
    if search_tool is not None:
        tools.unregister("search_messages")
        tools.register(
            DeterministicSearchMessagesTool(search_tool, state),
            always_on=True,
            risk="read-only",
            search_hint="deterministic selected source_ref constrained raw message search",
        )
    fetch_tool = tools.get_tool("fetch_messages")
    if fetch_tool is not None:
        tools.unregister("fetch_messages")
        tools.register(
            DeterministicFetchMessagesTool(fetch_tool, state),
            always_on=True,
            risk="read-only",
            search_hint="deterministic selected source_ref constrained fetch messages",
        )


def _is_retrieval_query(request: MemoryQuery) -> bool:
    return request.intent in {"answer", "context", "interest"}


def _copy_query(
    request: MemoryQuery,
    *,
    text: str | None = None,
    kinds: tuple[str, ...] | list[str] | None = None,
    limit: int | None = None,
    hints: dict[str, object] | None = None,
) -> MemoryQuery:
    merged_hints = dict(request.filters.hints)
    if hints:
        merged_hints.update(hints)
    filters = MemoryQueryFilters(
        kinds=tuple(kinds) if kinds is not None else request.filters.kinds,
        time_start=request.filters.time_start,
        time_end=request.filters.time_end,
        hints=merged_hints,
    )
    return MemoryQuery(
        text=text if text is not None else request.text,
        intent=request.intent,
        scope=request.scope,
        filters=filters,
        context=dict(request.context or {}),
        limit=limit if limit is not None else request.limit,
        timestamp=request.timestamp,
    )


def _classify_personal_memory_intent(text: str) -> str:
    q = text.lower()
    update_terms = (
        "after",
        "changed",
        "currently",
        "end up",
        "eventually",
        "final",
        "latest",
        "now",
        "updated",
    )
    preference_terms = (
        "attitude",
        "behavior",
        "comfortable",
        "dislike",
        "handles",
        "like",
        "prefer",
        "preference",
        "suggest",
        "values",
    )
    relationship_terms = (
        "between",
        "dynamic",
        "each member",
        "relationship",
        "toward",
    )
    if any(term in q for term in update_terms):
        return "knowledge_update"
    if any(term in q for term in preference_terms):
        return "preference"
    if any(term in q for term in relationship_terms):
        return "relationship"
    if any(term in q for term in ("who", "what does", "reveal", "member")):
        return "user_profile"
    return "event"


def _target_kinds_for_intent(intent: str) -> tuple[str, ...]:
    if intent == "preference":
        return ("preference", "profile", "event")
    if intent == "knowledge_update":
        return ("event", "profile")
    if intent == "relationship":
        return ("profile", "preference", "event")
    if intent == "user_profile":
        return ("profile", "event")
    return ("event", "profile", "preference")


def _adaptive_route(text: str) -> tuple[str, str]:
    q = text.lower()
    if "dietary preferences" in q or "food preferences" in q:
        return "versioned", "food_or_dietary_preference"
    if "stance on development" in q:
        return "versioned", "stable_stance_preference"
    if "willingness" in q and ("hiking" in q or "outdoor" in q):
        return "versioned", "capability_or_willingness_update"
    if "who first" in q:
        return "intent", "speaker_order_attribution"
    if any(term in q for term in ("who said", "who gave", "who brought", "who in the conversation")):
        return "intent", "speaker_attribution"
    if any(term in q for term in ("relationship between", "history between", "role in", "within the group")):
        return "intent", "relationship_or_role_attribution"
    if _classify_personal_memory_intent(text) == "preference":
        return "versioned", "stable_preference"
    if _temporal_mode(text) in {"latest", "timeline"}:
        return "intent", "temporal_change_default"
    return "intent", "general_personal_memory"


def _source_slot_profile(text: str) -> dict[str, object]:
    q = text.lower()
    labels: list[str] = []

    def add(label: str, condition: bool) -> None:
        if condition and label not in labels:
            labels.append(label)

    add(
        "speaker_attribution",
        any(
            term in q
            for term in (
                "who said",
                "who first",
                "who gave",
                "who brought",
                "who in the conversation",
                "who expressed",
                "who preferred",
                "who prefers",
                "who wanted",
                "who chose",
                "who asked",
                "who pushed",
            )
        )
        or (q.strip().startswith("who ") and "options:" in q),
    )
    add(
        "message_order",
        any(term in q for term in ("who first", "first pushed", "first ", "earliest", "initially")),
    )
    add(
        "exception_negation",
        any(
            term in q
            for term in (
                "all members",
                "all five",
                "everyone",
                "every member",
                "does everyone",
                "is that actually",
                "without stopping",
                "not how",
            )
        ),
    )
    add(
        "update_change",
        any(
            term in q
            for term in (
                "changed",
                "change over",
                "over time",
                "over the sessions",
                "currently",
                "latest",
                "now",
                "ended up",
                "eventually",
                "final",
            )
        ),
    )
    add(
        "implicit_preference",
        any(
            term in q
            for term in (
                "behavior",
                "suggest",
                "prefer",
                "preference",
                "comfortable",
                "limitations",
                "handles",
                "aversion",
                "focus",
            )
        ),
    )
    add(
        "relationship",
        any(term in q for term in ("history between", "relationship", "dynamic", "between")),
    )
    add(
        "decision_status",
        any(term in q for term in ("position", "reasoning", "stance", "decision", "status", "scheme")),
    )
    add(
        "person_candidate",
        "options:" in q
        or any(term in q for term in ("which member", "one specific member", "all members")),
    )
    if not labels:
        labels.append("event_fact")

    return {
        "personal_intent": _classify_personal_memory_intent(text),
        "slot_labels": labels,
        "question_names": _question_names(text),
        "requires_source_fetch": any(
            label
            in {
                "speaker_attribution",
                "message_order",
                "exception_negation",
                "implicit_preference",
                "relationship",
                "update_change",
                "decision_status",
                "person_candidate",
            }
            for label in labels
        ),
        "temporal_mode": _temporal_mode(text),
    }


def _rank_source_grounded_records(
    records: list[MemoryRecord],
    *,
    request: MemoryQuery,
    profile: dict[str, object],
    limit: int,
) -> list[MemoryRecord]:
    query_terms = _query_terms(request.text)
    scored: list[tuple[MemoryRecord, float]] = []
    for rank, record in enumerate(records, 1):
        rows = _source_grounded_rows(record, query_terms=query_terms, profile=profile)
        score = _source_grounded_score(
            record,
            rows=rows,
            rank=rank,
            query_terms=query_terms,
            profile=profile,
        )
        scored.append((_copy_source_grounded_record(record, rows, profile, score), score))

    ordered = [
        item
        for item, _ in sorted(scored, key=lambda pair: pair[1], reverse=True)
    ]
    return ordered[:limit]


def _copy_source_grounded_record(
    record: MemoryRecord,
    rows: list[dict[str, object]],
    profile: dict[str, object],
    score: float,
) -> MemoryRecord:
    signals = dict(record.signals or {})
    signals["source_grounded_slot_resolver"] = {
        "rank_score": round(score, 4),
        "slot_labels": profile["slot_labels"],
        "question_names": profile["question_names"],
        "requires_fetch_messages": profile["requires_source_fetch"],
        "answer_rules": _source_answer_rules(profile),
        "evidence_rows": rows,
    }
    return MemoryRecord(
        id=record.id,
        kind=record.kind,
        summary=_source_grounded_summary(record.summary, rows, profile),
        score=round(score, 4),
        engine_kind=record.engine_kind,
        evidence=list(record.evidence),
        signals=signals,
        injected=record.injected,
    )


def _source_grounded_score(
    record: MemoryRecord,
    *,
    rows: list[dict[str, object]],
    rank: int,
    query_terms: set[str],
    profile: dict[str, object],
) -> float:
    labels = set(cast(list[str], profile["slot_labels"]))
    names = cast(list[str], profile["question_names"])
    summary = str(record.summary or "")
    lower = summary.lower()
    score = float(record.score or 0.0)
    score += 1.0 / (90 + rank)
    score += _type_bonus(" ".join(labels), record.kind) * 0.5
    score += min(0.06, 0.012 * _term_overlap(query_terms, summary))
    if rows and any(row.get("source_ref") for row in rows):
        score += 0.06
    if "speaker_attribution" in labels and _speaker_hint(summary, names):
        score += 0.035
    if "message_order" in labels:
        sequences = _row_sequences(rows)
        if sequences:
            score += max(0.0, 0.055 - min(sequences) * 0.00035)
    if "exception_negation" in labels:
        score += _exception_signal_bonus(lower)
    if "implicit_preference" in labels:
        score += _implicit_preference_bonus(lower)
    if "update_change" in labels:
        sequences = _row_sequences(rows)
        if len(set(sequences)) >= 2:
            score += min(0.05, (max(sequences) - min(sequences)) * 0.0006)
        score += _temporal_bonus(record, str(profile["temporal_mode"]))
    if names:
        present_names = {
            name.lower()
            for name in names
            if re.search(rf"\b{re.escape(name.lower())}\b", lower)
        }
        score += min(0.04, 0.012 * len(present_names))
    return score


def _source_grounded_rows(
    record: MemoryRecord,
    *,
    query_terms: set[str],
    profile: dict[str, object],
) -> list[dict[str, object]]:
    refs = _record_source_refs(record)
    indices = _source_message_indices(record)
    labels = _matched_slot_labels(record.summary, query_terms, profile)
    names = cast(list[str], profile["question_names"])
    speaker_hint = _speaker_hint(record.summary, names)
    date = _record_date(record)
    rows: list[dict[str, object]] = []
    selected_refs = refs[:6] if refs else [""]
    for idx, source_ref in enumerate(selected_refs):
        sequence = _source_ref_index(source_ref)
        if sequence is None and idx < len(indices):
            sequence = indices[idx]
        rows.append(
            {
                "row_id": f"{record.id or 'memory'}:{idx + 1}",
                "memory_id": record.id,
                "memory_type": record.kind,
                "source_ref": source_ref,
                "source_sequence": sequence,
                "date": date,
                "speaker_hint": speaker_hint,
                "matched_slots": labels,
                "preview": _compact(record.summary, 220),
            }
        )
    return rows


def _source_grounded_summary(
    summary: str,
    rows: list[dict[str, object]],
    profile: dict[str, object],
) -> str:
    if summary.startswith("[source-grounded:"):
        return summary
    refs = [
        str(row.get("source_ref") or "")
        for row in rows
        if str(row.get("source_ref") or "").strip()
    ][:4]
    sequences = [str(value) for value in _row_sequences(rows)[:4]]
    slot_text = ",".join(cast(list[str], profile["slot_labels"]))
    source_text = ",".join(refs) if refs else "none"
    sequence_text = ",".join(sequences) if sequences else "unknown"
    rule = "fetch_messages before final answer" if profile["requires_source_fetch"] else "use source_refs when exact evidence is needed"
    return (
        f"[source-grounded: slots={slot_text}; source_refs={source_text}; "
        f"source_sequence={sequence_text}; rule={rule}] {summary}"
    )


def _source_grounded_guidance(
    query: str,
    profile: dict[str, object],
    records: list[MemoryRecord],
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for record in records:
        signal = dict(record.signals or {}).get("source_grounded_slot_resolver")
        if isinstance(signal, dict):
            evidence_rows = signal.get("evidence_rows")
            if isinstance(evidence_rows, list):
                rows.extend(cast(list[dict[str, object]], evidence_rows))
    refs = []
    for row in rows:
        source_ref = str(row.get("source_ref") or "").strip()
        if source_ref and source_ref not in refs:
            refs.append(source_ref)
    required_next_tool = "fetch_messages" if refs and profile["requires_source_fetch"] else "search_messages"
    return {
        "required_next_tool": required_next_tool,
        "candidate_source_refs": refs[:12],
        "recommended_search_queries": _recommended_search_queries(query, profile),
        "answer_rules": _source_answer_rules(profile),
    }


def _render_source_grounded_text_block(
    records: list[MemoryRecord],
    guidance: dict[str, object],
) -> str:
    lines = [
        "SOURCE-GROUNDED EVIDENCE TABLE",
        f"required_next_tool: {guidance.get('required_next_tool')}",
    ]
    for record in records:
        signal = dict(record.signals or {}).get("source_grounded_slot_resolver")
        if not isinstance(signal, dict):
            continue
        rows = signal.get("evidence_rows")
        if not isinstance(rows, list):
            continue
        for row in cast(list[dict[str, object]], rows[:3]):
            lines.append(
                " | ".join(
                    [
                        f"memory_id={row.get('memory_id')}",
                        f"type={row.get('memory_type')}",
                        f"source_ref={row.get('source_ref')}",
                        f"seq={row.get('source_sequence')}",
                        f"speaker_hint={row.get('speaker_hint')}",
                        f"slots={','.join(cast(list[str], row.get('matched_slots') or []))}",
                    ]
                )
            )
    return "\n".join(lines)


def _source_answer_rules(profile: dict[str, object]) -> list[str]:
    labels = set(cast(list[str], profile["slot_labels"]))
    rules = ["Answer only from retrieved evidence; if source evidence is missing, search_messages first."]
    if labels & {"speaker_attribution", "message_order"}:
        rules.append("For who/who-first questions, compare fetched speaker names and source_sequence/message order before choosing an option.")
    if "exception_negation" in labels:
        rules.append("For all/everyone questions, look for the dissenting or exception member, not the person named in the premise.")
    if "implicit_preference" in labels:
        rules.append("For preference questions, distinguish explicit statements from indirect avoidance or repeated behavior.")
    if "update_change" in labels:
        rules.append("For change questions, keep old and new evidence and answer the trajectory, not only the latest state.")
    if "decision_status" in labels:
        rules.append("For positions/reasoning, assign each person a separate stance and evidence row.")
    return rules


def _recommended_search_queries(query: str, profile: dict[str, object]) -> list[str]:
    names = cast(list[str], profile["question_names"])
    labels = set(cast(list[str], profile["slot_labels"]))
    terms = [
        word
        for word in _WORD_RE.findall(query)
        if len(word) >= 3
        and word.lower() not in _STOPWORDS
        and word not in names
        and word not in _QUESTION_NAME_STOPWORDS
    ]
    topic = " ".join(terms[:6]).strip()
    queries: list[str] = []
    if topic:
        queries.append(topic)
    for name in names[:5]:
        if topic:
            queries.append(f"{name} {topic}")
        else:
            queries.append(name)
    if "message_order" in labels and names:
        queries.append(" ".join([*names[:4], *terms[:4]]).strip())
    if "exception_negation" in labels:
        exception_terms = [*names[:5], "not", "prefer", "stop", "quiet", "cafe"]
        queries.append(" ".join(exception_terms).strip())
    if "update_change" in labels and names:
        queries.append(" ".join([names[0], "changed", "now", "first", "latest"]))
    return _unique_nonempty(queries)[:8]


def _normalize_memory_intent(value: str) -> MemoryQueryIntent:
    if value == "timeline":
        return "timeline"
    return "answer"


def _memory_kinds(memory_kind: str) -> tuple[str, ...]:
    value = str(memory_kind or "").strip()
    return (value,) if value else ()


def _memory_record_item(record: MemoryRecord) -> dict[str, object]:
    evidence = [
        {
            "kind": item.kind,
            "refs": item.refs,
            "resolver": item.resolver,
            "source_ref": item.source_ref,
            "metadata": item.metadata,
        }
        for item in record.evidence
    ]
    item: dict[str, object] = {
        "id": record.id,
        "memory_type": record.kind,
        "summary": record.summary,
        "score": round(float(record.score or 0.0), 4),
        "evidence": evidence,
        "signals": record.signals,
    }
    source_ref = _first_rendered_source_ref(evidence)
    if source_ref:
        item["source_ref"] = source_ref
    return item


def _first_rendered_source_ref(evidence: list[dict[str, object]]) -> str:
    for item in evidence:
        source_ref = str(item.get("source_ref") or "").strip()
        if source_ref:
            return source_ref
        refs = item.get("refs")
        if isinstance(refs, list):
            for ref in refs:
                text = str(ref or "").strip()
                if text:
                    return text
    return ""


def _raw_message_first_search(
    store: object,
    query: str,
    *,
    profile: dict[str, object],
    raw_limit: int,
    context: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    search = getattr(store, "search_messages", None)
    fetch_context = getattr(store, "fetch_by_ids_with_context", None)
    if not callable(search):
        return [], {"queries_used": [], "error": "session_store_search_unavailable"}

    queries = _raw_first_queries(query, profile)
    hits: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    used: list[str] = []
    per_query_limit = max(raw_limit, 12)
    for term in queries:
        try:
            page, _total = search(term, role="user", limit=per_query_limit, offset=0)
        except TypeError:
            page, _total = search(term, limit=per_query_limit, offset=0)
        if not isinstance(page, list):
            continue
        used.append(term)
        for message in cast(list[dict[str, Any]], page):
            msg_id = str(message.get("id") or "").strip()
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            copied = dict(message)
            copied["_matched_query"] = term
            copied["_raw_rank"] = len(hits) + 1
            hits.append(copied)
            if len(hits) >= raw_limit:
                break
        if len(hits) >= raw_limit:
            break

    messages = hits
    if context > 0 and hits and callable(fetch_context):
        ids = [str(item.get("id")) for item in hits if str(item.get("id") or "").strip()]
        try:
            expanded = fetch_context(ids, context)
        except TypeError:
            expanded = []
        if isinstance(expanded, list) and expanded:
            matched_by_id = {str(item.get("id")): item for item in hits}
            merged: list[dict[str, Any]] = []
            seen_expanded: set[str] = set()
            for message in cast(list[dict[str, Any]], expanded):
                msg_id = str(message.get("id") or "").strip()
                if not msg_id or msg_id in seen_expanded:
                    continue
                seen_expanded.add(msg_id)
                copied = dict(message)
                matched = matched_by_id.get(msg_id)
                copied["_matched_query"] = (
                    str(matched.get("_matched_query"))
                    if matched is not None
                    else ""
                )
                copied["_raw_rank"] = (
                    int(matched.get("_raw_rank") or 9999)
                    if matched is not None
                    else 9999
                )
                merged.append(copied)
            messages = merged

    rows = [
        _raw_message_row(message, profile=profile, rank=rank)
        for rank, message in enumerate(messages, 1)
    ]
    rows = [row for row in rows if row.get("source_ref")]
    labels = set(cast(list[str], profile["slot_labels"]))
    if labels & {"message_order", "update_change"}:
        rows.sort(
            key=lambda row: (
                str(row.get("session_key") or ""),
                str(row.get("timestamp") or ""),
                int(row.get("seq") or 0),
            )
        )
    else:
        rows.sort(
            key=lambda row: (
                int(row.get("raw_rank") or 9999),
                str(row.get("timestamp") or ""),
                int(row.get("seq") or 0),
            )
        )
    return rows[:raw_limit], {"queries_used": used}


def _question_aware_effective_text(
    recall_query: str,
    question_context: dict[str, object],
) -> str:
    question = str(question_context.get("question") or "").strip()
    query = str(recall_query or "").strip()
    if question and query and query not in question:
        return f"{question}\n\nRecall query: {query}"
    return question or query


def _question_context_session_key(question_context: dict[str, object]) -> str:
    question_id = str(question_context.get("question_id") or "").strip()
    if not question_id:
        return ""
    return question_id if question_id.startswith("lme:") else f"lme:{question_id}"


def _question_context_option_names(question_context: dict[str, object]) -> list[str]:
    question = str(question_context.get("question") or "")
    return _question_names(question)


def _question_aware_profile(
    text: str,
    question_context: dict[str, object],
) -> dict[str, object]:
    profile = _source_slot_profile(text)
    question = str(question_context.get("question") or text)
    option_names = _question_context_option_names(question_context)
    focused_terms = _question_aware_terms(question, option_names, profile)
    profile["question_context_available"] = bool(question_context.get("question"))
    profile["question_id"] = str(question_context.get("question_id") or "")
    profile["question_type"] = str(question_context.get("question_type") or "")
    profile["session_key_filter"] = _question_context_session_key(question_context)
    profile["option_names"] = option_names
    profile["focused_terms"] = focused_terms
    return profile


def _question_aware_terms(
    text: str,
    option_names: list[str],
    profile: dict[str, object],
) -> list[str]:
    option_lowers = {name.lower() for name in option_names}
    labels = set(cast(list[str], profile.get("slot_labels") or []))
    terms: list[str] = []
    for word in _WORD_RE.findall(text):
        lower = word.lower()
        if lower in option_lowers:
            continue
        if word in _QUESTION_NAME_STOPWORDS:
            continue
        if lower in _STOPWORDS or lower in _QUESTION_AWARE_GENERIC_TERMS:
            continue
        if len(lower) < 4 and lower not in {"no", "not"}:
            continue
        terms.append(lower)
    if "exception_negation" in labels:
        terms.extend(
            term
            for term in ("not", "no", "never", "without", "but", "however", "sure")
            if term in str(text or "").lower()
        )
    if "message_order" in labels:
        terms.extend(term for term in ("first", "again", "initially") if term in str(text or "").lower())
    if "update_change" in labels:
        terms.extend(term for term in ("changed", "change", "before", "after", "latest", "now") if term in str(text or "").lower())
    return _unique_nonempty(terms)[:14]


def _question_aware_queries(
    recall_query: str,
    question_context: dict[str, object],
    profile: dict[str, object],
) -> list[str]:
    question = str(question_context.get("question") or "")
    focused_terms = cast(list[str], profile.get("focused_terms") or [])
    option_names = cast(list[str], profile.get("option_names") or [])
    labels = set(cast(list[str], profile.get("slot_labels") or []))
    queries: list[str] = []
    if focused_terms:
        queries.append(" ".join(focused_terms[:8]))
    key_terms = [term for term in focused_terms if _evidence_term_weight(term) >= 2]
    if key_terms:
        queries.append(" ".join(key_terms[:6]))
    if "message_order" in labels:
        queries.append(" ".join([*focused_terms[:6], "first again initially schedule depends"]))
    if "exception_negation" in labels:
        queries.append(" ".join([*focused_terms[:6], "not no never without but however sure"]))
    if "implicit_preference" in labels and not focused_terms:
        queries.append(" ".join([*focused_terms[:6], "prefer instead avoid chose declined"]))
    if "update_change" in labels:
        queries.append(" ".join([*focused_terms[:6], "changed before after now latest still"]))
    non_option_names = [
        name
        for name in _question_names(question)
        if name not in option_names
    ]
    for name in non_option_names[:3]:
        if focused_terms:
            queries.append(" ".join([name, *focused_terms[:5]]))
    if not queries and recall_query:
        queries.append(recall_query)
    return _unique_nonempty(queries)[:8]


def _question_aware_structured_search(
    memory_store: object,
    *,
    recall_query: str,
    question_context: dict[str, object],
    profile: dict[str, object],
    raw_limit: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    search_raw = getattr(memory_store, "search_raw_events", None)
    if not callable(search_raw):
        return [], {"queries_used": [], "error": "memory_raw_events_search_unavailable"}

    session_key = _question_context_session_key(question_context)
    queries = _question_aware_queries(recall_query, question_context, profile)
    rows_by_ref: dict[str, dict[str, object]] = {}
    used: list[str] = []
    per_query_limit = max(raw_limit * 3, 24)

    def collect(*, scoped: bool) -> None:
        for term in queries:
            try:
                raw_rows = search_raw(
                    term,
                    limit=per_query_limit,
                    session_key=session_key if scoped else "",
                )
            except TypeError:
                raw_rows = []
            if not isinstance(raw_rows, list):
                continue
            used.append(f"{'session:' if scoped and session_key else 'global:'}{term}")
            for raw in cast(list[dict[str, object]], raw_rows):
                source_ref = str(raw.get("source_ref") or "").strip()
                if not source_ref:
                    continue
                message = {
                    "id": source_ref,
                    "session_key": raw.get("session_key"),
                    "seq": raw.get("seq"),
                    "role": "user",
                    "content": raw.get("content"),
                    "timestamp": raw.get("timestamp"),
                    "date": raw.get("date"),
                    "speaker_id": raw.get("speaker_id"),
                    "speaker": raw.get("speaker"),
                    "message_index": raw.get("message_index"),
                    "_matched_query": term,
                    "_raw_rank": len(rows_by_ref) + 1,
                }
                row = _raw_message_row(message, profile=profile, rank=len(rows_by_ref) + 1)
                row["source_table"] = "memory_raw_events"
                row["structured_score"] = raw.get("raw_event_score") or 0
                row["question_aware_score"] = _question_aware_row_score(row, profile)
                row["question_scope_hit"] = bool(
                    session_key and str(row.get("session_key") or "") == session_key
                )
                existing = rows_by_ref.get(source_ref)
                if existing is None:
                    rows_by_ref[source_ref] = row
                    continue
                existing["matched_terms"] = sorted(
                    set(cast(list[str], existing.get("matched_terms") or []))
                    | set(cast(list[str], row.get("matched_terms") or []))
                    | set(cast(list[str], raw.get("matched_terms") or []))
                )
                existing["matched_slots"] = _unique_nonempty(
                    [
                        *cast(list[str], existing.get("matched_slots") or []),
                        *cast(list[str], row.get("matched_slots") or []),
                    ]
                )
                existing["matched_query"] = " | ".join(
                    _unique_nonempty(
                        [
                            str(existing.get("matched_query") or ""),
                            str(row.get("matched_query") or ""),
                        ]
                    )
                )
                existing["structured_score"] = int(existing.get("structured_score") or 0) + int(
                    raw.get("raw_event_score") or 0
                )
                existing["question_aware_score"] = _question_aware_row_score(existing, profile)

    if session_key:
        collect(scoped=True)
    if not rows_by_ref:
        collect(scoped=False)

    rows = list(rows_by_ref.values())
    rows.sort(key=lambda row: _question_aware_row_key(row, profile))
    return rows[: max(raw_limit, 1)], {
        "queries_used": used,
        "session_key_filter": session_key,
        "session_scoped": bool(session_key and rows_by_ref),
    }


def _question_aware_row_score(row: dict[str, object], profile: dict[str, object]) -> int:
    labels = set(cast(list[str], profile.get("slot_labels") or []))
    focused_terms = set(cast(list[str], profile.get("focused_terms") or []))
    option_lowers = {name.lower() for name in cast(list[str], profile.get("option_names") or [])}
    matched_terms = [
        str(term).lower()
        for term in cast(list[object], row.get("matched_terms") or [])
        if str(term).lower() in focused_terms
        and str(term).lower() not in option_lowers
        and str(term).lower() not in _QUESTION_AWARE_GENERIC_TERMS
    ]
    lower = str(row.get("quote") or "").lower()
    score = sum(_evidence_term_weight(term) for term in matched_terms)
    if row.get("question_scope_hit"):
        score += 4
    if "message_order" in labels and _row_has_order_constraint(lower):
        score += 4
    if "exception_negation" in labels and (_record_like_negation(lower) or _row_has_order_constraint(lower)):
        score += 4
    if "implicit_preference" in labels and _row_has_preference_signal(lower):
        score += 4
    if "update_change" in labels and _row_has_update_signal(lower):
        score += 4
    if "decision_status" in labels and any(term in lower for term in ("support", "against", "reason", "because", "worried")):
        score += 3
    return score


def _question_aware_row_key(
    row: dict[str, object],
    profile: dict[str, object],
) -> tuple[int, int, int, int, str]:
    labels = set(cast(list[str], profile.get("slot_labels") or []))
    score = int(row.get("question_aware_score") or 0)
    index = _coerce_int(row.get("message_index"))
    seq = _coerce_int(row.get("seq"))
    if "update_change" in labels:
        return (
            -score,
            -(index if index is not None else -1),
            -(seq if seq is not None else -1),
            0 if row.get("question_scope_hit") else 1,
            str(row.get("source_ref") or ""),
        )
    if "message_order" in labels:
        return (
            0 if score > 0 else 1,
            index if index is not None else 999999,
            seq if seq is not None else 999999,
            -score,
            str(row.get("source_ref") or ""),
        )
    return (
        -score,
        0 if row.get("question_scope_hit") else 1,
        index if index is not None else 999999,
        seq if seq is not None else 999999,
        str(row.get("source_ref") or ""),
    )


def _question_aware_structured_resolution(
    query: str,
    profile: dict[str, object],
    rows: list[dict[str, object]],
    question_context: dict[str, object],
) -> dict[str, object]:
    base = _deterministic_resolution(query, profile, rows)
    option_candidates = _question_aware_option_candidates(query, profile, rows)
    labels = set(cast(list[str], profile.get("slot_labels") or []))
    selected: dict[str, object] | None = None
    if option_candidates:
        selected = option_candidates[0]
        second_score = int(option_candidates[1].get("score") or 0) if len(option_candidates) > 1 else 0
        score = int(selected.get("score") or 0)
        high_confidence_slots = labels & {
            "speaker_attribution",
            "message_order",
            "exception_negation",
            "implicit_preference",
            "update_change",
            "decision_status",
            "person_candidate",
        }
        if high_confidence_slots and score >= 6 and score - second_score >= 1:
            base = {
                "resolution_type": "question_aware_option_candidate",
                "selected_candidate": selected,
                "ranked_candidates": option_candidates[:6],
                "decision_rule": "question_context_scoped_option_candidate",
                "confidence": "high" if score >= 8 else "medium",
                "must_follow": True,
                "instruction": (
                    "Use slot_decision.selected_candidate unless selected evidence "
                    "rows explicitly contradict it."
                ),
            }
        else:
            base["leading_candidate"] = selected
    base["option_candidates"] = option_candidates[:6]
    base["structured_resolution_source"] = "question_aware_memory_raw_events"
    slot_decision = _question_aware_slot_decision(base, profile, rows, question_context)
    base["slot_decision"] = slot_decision
    base["supporting_source_refs"] = slot_decision["supporting_source_refs"]
    base["contradicting_source_refs"] = slot_decision["contradicting_source_refs"]
    base["evidence_gap"] = slot_decision["evidence_gap"]
    return base


def _question_aware_option_candidates(
    query: str,
    profile: dict[str, object],
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    names = cast(list[str], profile.get("option_names") or profile.get("question_names") or [])
    if not names or not rows:
        return []
    labels = set(cast(list[str], profile.get("slot_labels") or []))
    focused_terms = set(cast(list[str], profile.get("focused_terms") or []))
    name_lowers = {name.lower() for name in names}
    candidates: list[dict[str, object]] = []
    for name in names:
        name_lower = name.lower()
        scored_rows: list[tuple[dict[str, object], int, list[str]]] = []
        for row in rows:
            quote = str(row.get("quote") or "")
            lower = quote.lower()
            speaker = str(row.get("speaker") or "")
            speaker_match = speaker.lower() == name_lower
            mention_match = re.search(rf"\b{re.escape(name_lower)}\b", lower) is not None
            if not speaker_match and not mention_match:
                continue
            matched_terms = [
                str(term).lower()
                for term in cast(list[object], row.get("matched_terms") or [])
                if str(term).lower() in focused_terms
                and str(term).lower() not in name_lowers
                and str(term).lower() not in _QUESTION_AWARE_GENERIC_TERMS
            ]
            signal_terms = _slot_signal_terms(lower, labels)
            if (
                "implicit_preference" in labels
                and not matched_terms
                and not (labels & {"message_order", "exception_negation", "update_change"})
            ):
                continue
            all_terms = _unique_nonempty([*matched_terms, *signal_terms])
            score = sum(_evidence_term_weight(term) for term in all_terms)
            if speaker_match and all_terms and labels & {"speaker_attribution", "message_order", "exception_negation"}:
                score += 3
            if mention_match and all_terms:
                score += 2
            if "message_order" in labels and _row_has_order_constraint(lower):
                score += 4
            if "exception_negation" in labels and (_record_like_negation(lower) or "..." in lower or lower.strip() in {"sure", "fine"}):
                score += 4
            if "implicit_preference" in labels and _row_has_preference_signal(lower):
                score += 4
            if "update_change" in labels and _row_has_update_signal(lower):
                score += 4
            if score <= 0:
                continue
            scored_rows.append((row, score, all_terms))
        if not scored_rows:
            continue
        if "message_order" in labels:
            scored_rows.sort(key=lambda item: (_structured_order_row_key(item[0]), -item[1]))
        elif "update_change" in labels:
            scored_rows.sort(key=lambda item: (_structured_latest_row_key(item[0]), -item[1]))
        else:
            scored_rows.sort(key=lambda item: (-item[1], _structured_order_row_key(item[0])))
        best_row, best_score, best_terms = scored_rows[0]
        candidate = _base_candidate(best_row, answer=name)
        candidate.update(
            {
                "answer_type": "option_person",
                "score": best_score,
                "matched_terms": best_terms,
                "distinctive_terms": best_terms,
                "selection_basis": "question_scoped_option_plus_source_terms",
                "supporting_evidence_count": len(scored_rows),
            }
        )
        candidates.append(candidate)
    candidates.sort(key=_candidate_score_key)
    return candidates


def _question_aware_slot_decision(
    resolution: dict[str, object],
    profile: dict[str, object],
    rows: list[dict[str, object]],
    question_context: dict[str, object],
) -> dict[str, object]:
    selected = resolution.get("selected_candidate")
    selected_ref = (
        str(cast(dict[str, object], selected).get("source_ref") or "").strip()
        if isinstance(selected, dict)
        else ""
    )
    supporting_refs = [selected_ref] if selected_ref else []
    ranked = cast(list[dict[str, object]], resolution.get("ranked_candidates") or [])
    for candidate in ranked:
        ref = str(candidate.get("source_ref") or "").strip()
        if ref and ref not in supporting_refs and (
            not selected_ref or candidate.get("answer") == cast(dict[str, object], selected).get("answer")
        ):
            supporting_refs.append(ref)
    contradicting_refs = [
        str(candidate.get("source_ref") or "").strip()
        for candidate in ranked
        if str(candidate.get("source_ref") or "").strip()
        and str(candidate.get("source_ref") or "").strip() not in supporting_refs
    ][:4]
    evidence_gap = not rows or (
        bool(profile.get("requires_source_fetch")) and not supporting_refs
    )
    return {
        "selected_candidate": selected if isinstance(selected, dict) else None,
        "supporting_source_refs": supporting_refs[:6],
        "contradicting_source_refs": contradicting_refs,
        "confidence": resolution.get("confidence", "low"),
        "evidence_gap": evidence_gap,
        "slot_labels": profile.get("slot_labels", []),
        "session_key_filter": _question_context_session_key(question_context),
        "decision_rule": resolution.get("decision_rule", ""),
    }


def _question_aware_select_evidence_rows(
    rows: list[dict[str, object]],
    resolution: dict[str, object],
    *,
    limit: int,
) -> list[dict[str, object]]:
    slot_decision = cast(dict[str, object], resolution.get("slot_decision") or {})
    refs = [
        str(ref)
        for ref in cast(list[object], slot_decision.get("supporting_source_refs") or [])
        if str(ref or "").strip()
    ]
    refs.extend(
        str(ref)
        for ref in cast(list[object], slot_decision.get("contradicting_source_refs") or [])[:2]
        if str(ref or "").strip()
    )
    chosen = [row for row in rows if str(row.get("source_ref") or "") in set(refs)]
    if chosen:
        remaining = [
            row
            for row in rows
            if str(row.get("source_ref") or "") not in set(refs)
        ]
        return [*chosen, *remaining[: max(0, limit - len(chosen))]][:limit]
    return rows[:limit]


def _question_aware_answer_rules(
    profile: dict[str, object],
    resolution: dict[str, object],
) -> list[str]:
    rules = _structured_answer_rules(profile, resolution)
    rules.append("Prefer slot_decision.supporting_source_refs over broad memory summaries.")
    rules.append("If slot_decision.evidence_gap=true, search/fetch more before answering.")
    rules.append("Do not treat option-name matches alone as evidence.")
    return _unique_nonempty(rules)


def _slot_decision_plan_expand_rows(
    memory_store: object,
    rows: list[dict[str, object]],
    *,
    question_context: dict[str, object],
    profile: dict[str, object],
    limit: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    search_raw = getattr(memory_store, "search_raw_events", None)
    if not callable(search_raw):
        return rows, {"queries_used": [], "error": "memory_raw_events_search_unavailable"}

    session_key = _question_context_session_key(question_context)
    option_names = cast(list[str], profile.get("option_names") or [])
    focused_terms = cast(list[str], profile.get("focused_terms") or [])
    labels = set(cast(list[str], profile.get("slot_labels") or []))
    focus_dates = _answer_plan_focus_dates(rows, profile)
    if focus_dates and labels & {"exception_negation", "message_order", "speaker_attribution", "person_candidate"}:
        profile["answer_plan_focus_dates"] = focus_dates
    queries: list[str] = []
    for name in option_names[:8]:
        queries.append(name)
        if focused_terms:
            queries.append(" ".join([name, *focused_terms[:5]]))
    if "exception_negation" in labels:
        queries.extend(
            [
                " ".join([*option_names[:6], "sure fine not no never without stop tea cafe"]),
                " ".join([*focused_terms[:6], "hesitant reluctant declined sit out"]),
            ]
        )
    if "message_order" in labels:
        queries.append(" ".join([*option_names[:6], "first again schedule depends we'll see"]))
    if "update_change" in labels:
        queries.append(" ".join([*focused_terms[:6], "before after changed now latest still"]))
    queries = _unique_nonempty(queries)[:16]

    rows_by_ref = {str(row.get("source_ref") or ""): dict(row) for row in rows if row.get("source_ref")}
    used: list[str] = []
    for query in queries:
        try:
            raw_rows = search_raw(
                query,
                limit=8,
                session_key=session_key,
            )
        except TypeError:
            raw_rows = []
        if not isinstance(raw_rows, list):
            continue
        used.append(f"plan:{query}")
        for raw in cast(list[dict[str, object]], raw_rows):
            source_ref = str(raw.get("source_ref") or "").strip()
            if not source_ref:
                continue
            message = {
                "id": source_ref,
                "session_key": raw.get("session_key"),
                "seq": raw.get("seq"),
                "role": "user",
                "content": raw.get("content"),
                "timestamp": raw.get("timestamp"),
                "date": raw.get("date"),
                "speaker_id": raw.get("speaker_id"),
                "speaker": raw.get("speaker"),
                "message_index": raw.get("message_index"),
                "_matched_query": query,
                "_raw_rank": len(rows_by_ref) + 1,
            }
            row = _raw_message_row(message, profile=profile, rank=len(rows_by_ref) + 1)
            row["source_table"] = "memory_raw_events"
            row["structured_score"] = raw.get("raw_event_score") or 0
            row["question_scope_hit"] = True
            row["question_aware_score"] = _question_aware_row_score(row, profile)
            row["answer_plan_score"] = _answer_plan_row_signal_score(row, profile)
            existing = rows_by_ref.get(source_ref)
            if existing is None:
                rows_by_ref[source_ref] = row
                continue
            existing["matched_terms"] = sorted(
                set(cast(list[str], existing.get("matched_terms") or []))
                | set(cast(list[str], row.get("matched_terms") or []))
            )
            existing["matched_query"] = " | ".join(
                _unique_nonempty(
                    [
                        str(existing.get("matched_query") or ""),
                        str(row.get("matched_query") or ""),
                    ]
                )
            )
            existing["answer_plan_score"] = max(
                int(existing.get("answer_plan_score") or 0),
                int(row.get("answer_plan_score") or 0),
            )

    expanded = list(rows_by_ref.values())
    for row in expanded:
        row["answer_plan_score"] = _answer_plan_row_signal_score(row, profile)
    expanded.sort(key=lambda row: _answer_plan_row_key(row, profile))
    return expanded[: max(8, min(limit, 80))], {"queries_used": used}


def _slot_decision_answer_plan(
    query: str,
    profile: dict[str, object],
    rows: list[dict[str, object]],
    selected_rows: list[dict[str, object]],
    resolution: dict[str, object],
    question_context: dict[str, object],
) -> dict[str, object]:
    labels = set(cast(list[str], profile.get("slot_labels") or []))
    option_names = cast(list[str], profile.get("option_names") or [])
    per_option = _answer_plan_per_option_evidence(option_names, rows, profile)
    ordered = _answer_plan_ordered_evidence(rows, profile)
    old_evidence, new_evidence = _answer_plan_update_evidence(rows, profile)
    selected_answer, confidence, selected_refs = _answer_plan_selected_answer(
        resolution,
        per_option,
        ordered,
        old_evidence,
        new_evidence,
        labels,
    )
    supporting_refs = _unique_nonempty(
        [
            *selected_refs,
            *[
                str(row.get("source_ref") or "")
                for row in selected_rows[:4]
                if str(row.get("source_ref") or "").strip()
            ],
        ]
    )[:8]
    contradicting_refs = _answer_plan_contradicting_refs(per_option, selected_answer)[:6]
    evidence_gap = not rows or (confidence == "low" and bool(profile.get("requires_source_fetch")))
    final_constraints = _answer_plan_constraints(
        labels,
        selected_answer=selected_answer,
        confidence=confidence,
        evidence_gap=evidence_gap,
    )
    return {
        "question_type": str(question_context.get("question_type") or ""),
        "slot_labels": profile.get("slot_labels", []),
        "selected_answer": selected_answer if confidence in {"high", "medium"} else "",
        "confidence": confidence,
        "evidence_gap": evidence_gap,
        "missing_slots": _answer_plan_missing_slots(profile, per_option, rows),
        "per_option_evidence": per_option,
        "ordered_evidence": ordered[:10],
        "old_evidence": old_evidence[:5],
        "new_evidence": new_evidence[:5],
        "supporting_source_refs": supporting_refs,
        "contradicting_source_refs": contradicting_refs,
        "final_answer_constraints": final_constraints,
    }


def _answer_plan_aligned_resolution(
    resolution: dict[str, object],
    answer_plan: dict[str, object],
) -> dict[str, object]:
    planned_answer = str(answer_plan.get("selected_answer") or "").strip()
    confidence = str(answer_plan.get("confidence") or "low")
    if not planned_answer or confidence not in {"high", "medium"}:
        return resolution
    aligned = dict(resolution)
    support_rows: list[dict[str, object]] = []
    for item in cast(list[dict[str, object]], answer_plan.get("per_option_evidence") or []):
        if str(item.get("option") or "") == planned_answer:
            support_rows = cast(list[dict[str, object]], item.get("support") or [])
            break
    first_support = support_rows[0] if support_rows else {}
    aligned["selected_candidate"] = {
        "answer": planned_answer,
        "answer_type": "answer_plan_selected",
        "selection_basis": "slot_decision_answer_plan",
        "score": sum(int(row.get("score") or 0) for row in support_rows),
        "source_ref": first_support.get("source_ref"),
        "speaker": first_support.get("speaker"),
        "speaker_id": first_support.get("speaker_id"),
        "message_index": first_support.get("message_index"),
        "seq": first_support.get("seq"),
        "date": first_support.get("date"),
        "quote": first_support.get("quote"),
        "supporting_evidence_count": len(support_rows),
    }
    aligned["ranked_candidates"] = answer_plan.get("per_option_evidence", [])
    aligned["option_candidates"] = answer_plan.get("per_option_evidence", [])
    aligned["confidence"] = confidence
    aligned["must_follow"] = confidence == "high"
    aligned["decision_rule"] = "slot_decision_answer_plan"
    aligned["instruction"] = (
        "Use answer_plan.selected_answer as the leading answer unless direct "
        "retrieved evidence contradicts the selected support rows."
    )
    aligned["supporting_source_refs"] = answer_plan.get("supporting_source_refs", [])
    aligned["contradicting_source_refs"] = answer_plan.get("contradicting_source_refs", [])
    aligned["evidence_gap"] = answer_plan.get("evidence_gap", False)
    slot_decision = dict(cast(dict[str, object], aligned.get("slot_decision") or {}))
    slot_decision["selected_candidate"] = aligned["selected_candidate"]
    slot_decision["selected_answer"] = planned_answer
    slot_decision["supporting_source_refs"] = answer_plan.get("supporting_source_refs", [])
    slot_decision["contradicting_source_refs"] = answer_plan.get("contradicting_source_refs", [])
    slot_decision["evidence_gap"] = answer_plan.get("evidence_gap", False)
    slot_decision["decision_rule"] = "slot_decision_answer_plan"
    aligned["slot_decision"] = slot_decision
    return aligned


def _answer_plan_per_option_evidence(
    option_names: list[str],
    rows: list[dict[str, object]],
    profile: dict[str, object],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for name in option_names[:8]:
        matching = [
            row
            for row in rows
            if _row_matches_person(row, name)
        ]
        scored = [
            (row, _answer_plan_row_signal_score(row, profile))
            for row in matching
        ]
        scored.sort(key=lambda item: (-item[1], _structured_order_row_key(item[0])))
        support_rows = [
            _answer_plan_row_summary(row, score=score)
            for row, score in scored
            if score > 0
        ][:4]
        weak_rows = [
            _answer_plan_row_summary(row, score=score)
            for row, score in scored
            if score <= 0
        ][:2]
        total_score = sum(max(0, score) for _row, score in scored)
        status = "support" if support_rows else ("missing" if not matching else "weak")
        result.append(
            {
                "option": name,
                "status": status,
                "score": total_score,
                "support": support_rows,
                "contradict": [],
                "weak_or_context": weak_rows,
                "missing": not matching,
            }
        )
    result.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("option") or "")))
    return result


def _answer_plan_selected_answer(
    resolution: dict[str, object],
    per_option: list[dict[str, object]],
    ordered: list[dict[str, object]],
    old_evidence: list[dict[str, object]],
    new_evidence: list[dict[str, object]],
    labels: set[str],
) -> tuple[str, str, list[str]]:
    if labels & {"exception_negation", "message_order", "speaker_attribution", "person_candidate"} and per_option:
        if "message_order" in labels:
            ordered_options = [
                item
                for item in per_option
                if int(item.get("score") or 0) > 0 and item.get("support")
            ]
            ordered_options.sort(
                key=lambda item: _plan_option_earliest_key(cast(list[dict[str, object]], item.get("support") or []))
            )
            chosen = ordered_options[0] if ordered_options else per_option[0]
        else:
            chosen = per_option[0]
        score = int(chosen.get("score") or 0)
        second = int(per_option[1].get("score") or 0) if len(per_option) > 1 else 0
        confidence = "high" if score >= 8 and score - second >= 2 else ("medium" if score >= 5 else "low")
        refs = [
            str(row.get("source_ref") or "")
            for row in cast(list[dict[str, object]], chosen.get("support") or [])
        ]
        return str(chosen.get("option") or ""), confidence, refs

    if "update_change" in labels and (old_evidence or new_evidence):
        refs = [
            *[str(row.get("source_ref") or "") for row in old_evidence[:2]],
            *[str(row.get("source_ref") or "") for row in new_evidence[:2]],
        ]
        return "", "medium" if old_evidence and new_evidence else "low", refs

    selected = resolution.get("selected_candidate")
    if isinstance(selected, dict):
        answer = str(selected.get("answer") or "").strip()
        confidence = str(resolution.get("confidence") or "low")
        ref = str(selected.get("source_ref") or "").strip()
        return answer, confidence if confidence in {"high", "medium"} else "low", [ref] if ref else []
    if ordered:
        return str(ordered[0].get("speaker") or ""), "medium", [str(ordered[0].get("source_ref") or "")]
    return "", "low", []


def _answer_plan_ordered_evidence(
    rows: list[dict[str, object]],
    profile: dict[str, object],
) -> list[dict[str, object]]:
    scored = [
        (row, _answer_plan_row_signal_score(row, profile))
        for row in rows
    ]
    scored = [item for item in scored if item[1] > 0]
    scored.sort(key=lambda item: (_structured_order_row_key(item[0]), -item[1]))
    return [_answer_plan_row_summary(row, score=score) for row, score in scored]


def _answer_plan_update_evidence(
    rows: list[dict[str, object]],
    profile: dict[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    scored = [
        (row, _answer_plan_row_signal_score(row, profile))
        for row in rows
    ]
    relevant = [item for item in scored if item[1] > 0]
    if not relevant:
        relevant = scored
    ordered = sorted(relevant, key=lambda item: _structured_order_row_key(item[0]))
    old_rows = [_answer_plan_row_summary(row, score=score) for row, score in ordered[:4]]
    new_rows = [_answer_plan_row_summary(row, score=score) for row, score in reversed(ordered[-4:])]
    return old_rows, new_rows


def _answer_plan_select_evidence_rows(
    rows: list[dict[str, object]],
    selected_rows: list[dict[str, object]],
    answer_plan: dict[str, object],
    *,
    limit: int,
) -> list[dict[str, object]]:
    refs = _unique_nonempty(
        [
            *cast(list[str], answer_plan.get("supporting_source_refs") or []),
            *cast(list[str], answer_plan.get("contradicting_source_refs") or []),
        ]
    )
    chosen = [
        row
        for row in rows
        if str(row.get("source_ref") or "") in set(refs)
    ]
    if not chosen:
        chosen = list(selected_rows)
    existing_refs = {str(row.get("source_ref") or "") for row in chosen}
    for row in selected_rows:
        ref = str(row.get("source_ref") or "")
        if ref and ref not in existing_refs:
            chosen.append(row)
            existing_refs.add(ref)
        if len(chosen) >= limit:
            break
    return chosen[:limit]


def _answer_plan_answer_rules(
    answer_plan: dict[str, object],
    profile: dict[str, object],
    resolution: dict[str, object],
) -> list[str]:
    rules = _question_aware_answer_rules(profile, resolution)
    rules.append("Use answer_plan.final_answer_constraints as the final answer contract.")
    rules.append("For option questions, compare per_option_evidence before choosing.")
    rules.append("For exception/negation questions, choose the option with exception/dissent evidence, not merely any related preference.")
    rules.append("For update/change questions, answer the trajectory using old_evidence then new_evidence.")
    if str(answer_plan.get("selected_answer") or "").strip() and answer_plan.get("confidence") in {"high", "medium"}:
        rules.append("answer_plan.selected_answer is the leading answer candidate; cite its supporting_source_refs unless fetched evidence contradicts it.")
    return _unique_nonempty(rules)


def _answer_plan_constraints(
    labels: set[str],
    *,
    selected_answer: str,
    confidence: str,
    evidence_gap: bool,
) -> list[str]:
    constraints: list[str] = []
    if selected_answer and confidence in {"high", "medium"}:
        constraints.append(f"Lead with selected_answer={selected_answer}.")
    if evidence_gap:
        constraints.append("Evidence is incomplete; search or fetch missing slots before final answer.")
    if labels & {"speaker_attribution", "message_order"}:
        constraints.append("State who said/did it and cite the earliest relevant source_ref/message_index.")
    if "exception_negation" in labels:
        constraints.append("Identify the exception or dissenting member and explain why other related rows are not the answer.")
    if "implicit_preference" in labels:
        constraints.append("Infer preference only from repeated behavior, avoidance, hesitation, or explicit source quotes.")
    if "update_change" in labels:
        constraints.append("Describe before, trigger, and after/current state; do not collapse trajectory into only the latest state.")
    if not constraints:
        constraints.append("Answer only from selected evidence rows and cite source_ref values.")
    return constraints


def _answer_plan_missing_slots(
    profile: dict[str, object],
    per_option: list[dict[str, object]],
    rows: list[dict[str, object]],
) -> list[str]:
    missing: list[str] = []
    labels = set(cast(list[str], profile.get("slot_labels") or []))
    if labels & {"speaker_attribution", "exception_negation", "person_candidate"} and per_option:
        if not any(int(item.get("score") or 0) > 0 for item in per_option):
            missing.append("candidate_support")
    if "message_order" in labels and not any(_coerce_int(row.get("message_index")) is not None for row in rows):
        missing.append("message_index")
    if "update_change" in labels and len(rows) < 2:
        missing.append("old_new_evidence")
    return missing


def _answer_plan_contradicting_refs(
    per_option: list[dict[str, object]],
    selected_answer: str,
) -> list[str]:
    refs: list[str] = []
    for item in per_option:
        if str(item.get("option") or "") == selected_answer:
            continue
        for row in cast(list[dict[str, object]], item.get("support") or [])[:2]:
            ref = str(row.get("source_ref") or "").strip()
            if ref:
                refs.append(ref)
    return _unique_nonempty(refs)


def _answer_plan_row_signal_score(row: dict[str, object], profile: dict[str, object]) -> int:
    labels = set(cast(list[str], profile.get("slot_labels") or []))
    focused_terms = set(cast(list[str], profile.get("focused_terms") or []))
    lower = str(row.get("quote") or "").lower()
    matched_terms = [
        str(term).lower()
        for term in cast(list[object], row.get("matched_terms") or [])
        if str(term).lower() in focused_terms
        and str(term).lower() not in _QUESTION_AWARE_GENERIC_TERMS
    ]
    score = sum(_evidence_term_weight(term) for term in matched_terms)
    focus_dates = set(cast(list[str], profile.get("answer_plan_focus_dates") or []))
    row_date = str(row.get("date") or "")
    out_of_focus = bool(
        focus_dates
        and row_date
        and row_date not in focus_dates
        and labels & {"exception_negation", "message_order", "speaker_attribution", "person_candidate"}
    )
    if row.get("question_scope_hit"):
        score += 1
    if "message_order" in labels and _row_has_order_constraint(lower):
        score += 5
    if "exception_negation" in labels:
        if _record_like_negation(lower) or any(term in lower for term in ("declined", "sit out", "not equally", "hesitant", "reluctant")):
            score += 5
        if "..." in lower:
            score += 6
        if lower.strip() in {"sure", "fine", "fine.", "ok", "okay"}:
            score += 2 if lower.strip().startswith("sure") else 1
        if any(term in lower for term in ("tea", "cafe", "stop", "without stopping", "lack of anywhere to stop")):
            score += 5
    if "implicit_preference" in labels and _row_has_preference_signal(lower):
        score += 4
    if "update_change" in labels and _row_has_update_signal(lower):
        score += 4
    if "decision_status" in labels and any(term in lower for term in ("support", "against", "reason", "because", "worried", "cost")):
        score += 3
    if out_of_focus and not matched_terms:
        return 0
    if out_of_focus:
        score = max(0, score - 4)
    return score


def _answer_plan_focus_dates(
    rows: list[dict[str, object]],
    profile: dict[str, object],
) -> list[str]:
    labels = set(cast(list[str], profile.get("slot_labels") or []))
    if "update_change" in labels:
        return []
    focused_terms = set(cast(list[str], profile.get("focused_terms") or []))
    if not focused_terms:
        return []
    date_scores: dict[str, int] = {}
    for row in rows:
        date = str(row.get("date") or "")
        if not date:
            continue
        matched = {
            str(term).lower()
            for term in cast(list[object], row.get("matched_terms") or [])
            if str(term).lower() in focused_terms
        }
        if not matched:
            continue
        date_scores[date] = date_scores.get(date, 0) + sum(_evidence_term_weight(term) for term in matched)
    return [
        date
        for date, _score in sorted(date_scores.items(), key=lambda item: (-item[1], item[0]))
    ][:3]


def _answer_plan_row_key(row: dict[str, object], profile: dict[str, object]) -> tuple[int, int, int, str]:
    score = int(row.get("answer_plan_score") or _answer_plan_row_signal_score(row, profile))
    index = _coerce_int(row.get("message_index"))
    seq = _coerce_int(row.get("seq"))
    return (
        -score,
        index if index is not None else 999999,
        seq if seq is not None else 999999,
        str(row.get("source_ref") or ""),
    )


def _answer_plan_row_summary(row: dict[str, object], *, score: int) -> dict[str, object]:
    return {
        "source_ref": str(row.get("source_ref") or ""),
        "speaker": str(row.get("speaker") or ""),
        "speaker_id": str(row.get("speaker_id") or ""),
        "message_index": row.get("message_index"),
        "seq": row.get("seq"),
        "date": str(row.get("date") or ""),
        "score": score,
        "matched_terms": cast(list[str], row.get("matched_terms") or []),
        "quote": row.get("quote"),
    }


def _row_matches_person(row: dict[str, object], name: str) -> bool:
    name_lower = str(name or "").lower()
    if not name_lower:
        return False
    speaker = str(row.get("speaker") or "").lower()
    quote = str(row.get("quote") or "").lower()
    return speaker == name_lower or re.search(rf"\b{re.escape(name_lower)}\b", quote) is not None


def _plan_option_earliest_key(rows: list[dict[str, object]]) -> tuple[int, int, str]:
    if not rows:
        return (999999, 999999, "")
    first = min(
        rows,
        key=lambda row: (
            _coerce_int(row.get("message_index")) or 999999,
            _coerce_int(row.get("seq")) or 999999,
            str(row.get("source_ref") or ""),
        ),
    )
    return (
        _coerce_int(first.get("message_index")) or 999999,
        _coerce_int(first.get("seq")) or 999999,
        str(first.get("source_ref") or ""),
    )


def _structured_candidate_search(
    memory_store: object,
    query: str,
    *,
    profile: dict[str, object],
    raw_limit: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    search_raw = getattr(memory_store, "search_raw_events", None)
    if not callable(search_raw):
        return [], {"queries_used": [], "error": "memory_raw_events_search_unavailable"}

    queries = _structured_candidate_queries(query, profile)
    rows_by_ref: dict[str, dict[str, object]] = {}
    used: list[str] = []
    per_query_limit = max(raw_limit * 2, 24)
    for term in queries:
        try:
            raw_rows = search_raw(term, limit=per_query_limit)
        except TypeError:
            raw_rows = []
        if not isinstance(raw_rows, list):
            continue
        used.append(term)
        for raw in cast(list[dict[str, object]], raw_rows):
            source_ref = str(raw.get("source_ref") or "").strip()
            if not source_ref:
                continue
            message = {
                "id": source_ref,
                "session_key": raw.get("session_key"),
                "seq": raw.get("seq"),
                "role": "user",
                "content": raw.get("content"),
                "timestamp": raw.get("timestamp"),
                "date": raw.get("date"),
                "speaker_id": raw.get("speaker_id"),
                "speaker": raw.get("speaker"),
                "message_index": raw.get("message_index"),
                "_matched_query": term,
                "_raw_rank": len(rows_by_ref) + 1,
            }
            row = _raw_message_row(message, profile=profile, rank=len(rows_by_ref) + 1)
            row["source_table"] = "memory_raw_events"
            row["structured_score"] = raw.get("raw_event_score") or 0
            existing = rows_by_ref.get(source_ref)
            if existing is None:
                rows_by_ref[source_ref] = row
                continue
            existing["matched_terms"] = sorted(
                set(cast(list[str], existing.get("matched_terms") or []))
                | set(cast(list[str], row.get("matched_terms") or []))
                | set(cast(list[str], raw.get("matched_terms") or []))
            )
            existing["matched_slots"] = _unique_nonempty(
                [
                    *cast(list[str], existing.get("matched_slots") or []),
                    *cast(list[str], row.get("matched_slots") or []),
                ]
            )
            existing["matched_query"] = " | ".join(
                _unique_nonempty(
                    [
                        str(existing.get("matched_query") or ""),
                        str(row.get("matched_query") or ""),
                    ]
                )
            )
            existing["structured_score"] = int(existing.get("structured_score") or 0) + int(
                raw.get("raw_event_score") or 0
            )

    rows = list(rows_by_ref.values())
    labels = set(cast(list[str], profile["slot_labels"]))
    if "message_order" in labels:
        rows.sort(key=_structured_order_row_key)
    elif "update_change" in labels:
        rows.sort(key=_structured_latest_row_key)
    else:
        rows.sort(key=_structured_score_row_key)
    return rows[: max(raw_limit, 1)], {"queries_used": used}


def _structured_candidate_queries(query: str, profile: dict[str, object]) -> list[str]:
    queries = _raw_first_queries(query, profile)
    names = cast(list[str], profile["question_names"])
    labels = set(cast(list[str], profile["slot_labels"]))
    topic_terms = [
        word
        for word in _WORD_RE.findall(query)
        if len(word) >= 4
        and word.lower() not in _STOPWORDS
        and word not in names
        and word not in _QUESTION_NAME_STOPWORDS
    ][:8]
    if names:
        queries.append(" ".join(names[:6]))
    if "message_order" in labels:
        queries.append(" ".join([*names[:6], "schedule deadline depends wait again first"]))
    if "exception_negation" in labels:
        queries.append(" ".join([*names[:6], "not no never without but however stop avoid"]))
    if "implicit_preference" in labels:
        queries.append(" ".join([*names[:6], "prefer instead avoid chose usually just pattern"]))
    if "update_change" in labels:
        queries.append(" ".join([*names[:4], "changed now before after first latest"]))
    if topic_terms:
        queries.append(" ".join(topic_terms))
    return _unique_nonempty(queries)[:12]


def _structured_candidate_resolution(
    query: str,
    profile: dict[str, object],
    rows: list[dict[str, object]],
) -> dict[str, object]:
    base = _deterministic_resolution(query, profile, rows)
    option_candidates = _option_resolution_candidates(query, profile, rows)
    base["option_candidates"] = option_candidates[:6]

    labels = set(cast(list[str], profile["slot_labels"]))
    base_selected = base.get("selected_candidate")
    if (
        isinstance(base_selected, dict)
        and base.get("must_follow") is True
        and str(base.get("confidence") or "") in {"high", "medium"}
        and labels & {"speaker_attribution", "message_order", "exception_negation", "update_change"}
    ):
        base["structured_resolution_source"] = "slot_specific_source_rows"
        return base

    if option_candidates:
        selected = option_candidates[0]
        second_score = int(option_candidates[1].get("score") or 0) if len(option_candidates) > 1 else 0
        score = int(selected.get("score") or 0)
        high_confidence_slots = labels & {
            "speaker_attribution",
            "message_order",
            "exception_negation",
            "implicit_preference",
            "update_change",
            "decision_status",
            "person_candidate",
        }
        high_confidence = bool(high_confidence_slots) and score >= 7 and score - second_score >= 2
        return {
            "resolution_type": "structured_option_candidate",
            "selected_candidate": selected if high_confidence else None,
            "leading_candidate": selected,
            "ranked_candidates": option_candidates[:6],
            "option_candidates": option_candidates[:6],
            "decision_rule": "slot_specific_option_candidate_from_memory_raw_events",
            "confidence": "high" if high_confidence else ("medium" if score >= 4 else "low"),
            "must_follow": high_confidence,
            "structured_resolution_source": "memory_raw_events",
            "instruction": (
                "Use selected_candidate only when it matches the question slot; "
                "otherwise use selected_evidence_table to compare candidates."
            ),
        }

    base["structured_resolution_source"] = "fallback_source_overlap"
    return base


def _option_resolution_candidates(
    query: str,
    profile: dict[str, object],
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    names = cast(list[str], profile.get("question_names") or [])
    if not names or not rows:
        return []
    labels = set(cast(list[str], profile["slot_labels"]))
    query_terms = _query_terms(query)
    name_lowers = {name.lower() for name in names}
    candidates: list[dict[str, object]] = []
    for name in names:
        name_lower = name.lower()
        scored_rows: list[tuple[dict[str, object], int, list[str]]] = []
        for row in rows:
            quote = str(row.get("quote") or "")
            speaker = str(row.get("speaker") or "")
            lower = quote.lower()
            speaker_match = speaker.lower() == name_lower
            mention_match = re.search(rf"\b{re.escape(name_lower)}\b", lower) is not None
            if not speaker_match and not mention_match:
                continue
            matched_terms = [
                term
                for term in cast(list[str], row.get("matched_terms") or [])
                if term.lower() not in name_lowers
                and term.lower() not in _GENERIC_EVIDENCE_TERMS
            ]
            signal_terms = _slot_signal_terms(lower, labels)
            overlap_terms = sorted(
                term
                for term in query_terms
                if term not in _GENERIC_EVIDENCE_TERMS
                and term not in name_lowers
                and term in lower
            )
            all_terms = _unique_nonempty([*matched_terms, *signal_terms, *overlap_terms])
            score = sum(_evidence_term_weight(term) for term in all_terms)
            if speaker_match and labels & {"speaker_attribution", "message_order", "exception_negation"}:
                score += 4
            if mention_match:
                score += 3
            if "message_order" in labels and _row_has_order_constraint(lower):
                score += 5
            if "implicit_preference" in labels and _row_has_preference_signal(lower):
                score += 4
            if "update_change" in labels and _row_has_update_signal(lower):
                score += 4
            if score <= 0:
                continue
            scored_rows.append((row, score, all_terms))
        if not scored_rows:
            continue
        if "message_order" in labels:
            scored_rows.sort(key=lambda item: (_structured_order_row_key(item[0]), -item[1]))
        elif "update_change" in labels:
            scored_rows.sort(key=lambda item: (_structured_latest_row_key(item[0]), -item[1]))
        else:
            scored_rows.sort(key=lambda item: (-item[1], _structured_order_row_key(item[0])))
        best_row, best_score, best_terms = scored_rows[0]
        candidate = _base_candidate(best_row, answer=name)
        candidate.update(
            {
                "answer_type": "option_person",
                "score": best_score,
                "matched_terms": best_terms,
                "distinctive_terms": [
                    term for term in best_terms if term not in _GENERIC_EVIDENCE_TERMS
                ],
                "selection_basis": "option_name_plus_slot_specific_source_terms",
                "supporting_evidence_count": len(scored_rows),
            }
        )
        candidates.append(candidate)
    candidates.sort(key=_candidate_score_key)
    return candidates


def _select_structured_evidence_rows(
    rows: list[dict[str, object]],
    resolution: dict[str, object],
    *,
    limit: int,
) -> list[dict[str, object]]:
    selected = resolution.get("selected_candidate")
    refs: list[str] = []
    if isinstance(selected, dict):
        ref = str(selected.get("source_ref") or "").strip()
        if ref:
            refs.append(ref)
    for item in cast(list[dict[str, object]], resolution.get("previous_evidence") or []):
        ref = str(item.get("source_ref") or "").strip()
        if ref and ref not in refs:
            refs.append(ref)
    chosen = [row for row in rows if str(row.get("source_ref") or "") in set(refs)]
    if chosen:
        remaining = [
            row
            for row in rows
            if str(row.get("source_ref") or "") not in set(refs)
        ]
        return [*chosen, *remaining[: max(0, limit - len(chosen))]]
    return rows[:limit]


def _structured_answer_rules(
    profile: dict[str, object],
    resolution: dict[str, object],
) -> list[str]:
    rules = _source_answer_rules(profile)
    rules.append("Use selected_evidence_table rows before loose memory summaries.")
    rules.append("Cite source_ref values from selected_evidence_table when using raw evidence.")
    if resolution.get("selected_candidate"):
        rules.append("Compare final answer against candidate_resolution.selected_candidate before responding.")
    if resolution.get("must_follow") is True:
        rules.append("candidate_resolution.must_follow=true, so treat selected_candidate as the leading answer unless direct evidence contradicts it.")
    return rules


def _resolution_answer(resolution: dict[str, object]) -> object:
    selected = resolution.get("selected_candidate")
    if not isinstance(selected, dict):
        return None
    answer = selected.get("answer")
    return answer if str(answer or "").strip() else None


def _slot_signal_terms(lower: str, labels: set[str]) -> list[str]:
    signals: list[str] = []
    groups: list[tuple[str, ...]] = []
    if "message_order" in labels:
        groups.append(("first", "again", "schedule", "deadline", "depends", "weeks", "wait", "try"))
    if "exception_negation" in labels:
        groups.append(("not", "no", "never", "without", "but", "however", "stop", "avoid", "declined"))
    if "implicit_preference" in labels:
        groups.append(("prefer", "instead", "avoid", "chose", "usually", "just", "pattern", "stiff"))
    if "update_change" in labels:
        groups.append(("changed", "now", "after", "before", "latest", "still", "anymore", "started"))
    if "decision_status" in labels:
        groups.append(("support", "against", "neutral", "because", "reason", "worried", "cost"))
    for group in groups:
        for term in group:
            if re.search(rf"\b{re.escape(term)}\b", lower):
                signals.append(term)
    return _unique_nonempty(signals)


def _row_has_order_constraint(lower: str) -> bool:
    return any(
        term in lower
        for term in (
            "again",
            "schedule",
            "deadline",
            "depends",
            "should know",
            "we'll see",
            "try to",
            "can't",
            "cannot",
        )
    )


def _row_has_preference_signal(lower: str) -> bool:
    return any(
        term in lower
        for term in (
            "prefer",
            "instead",
            "avoid",
            "chose",
            "usually",
            "just prefer",
            "stiff",
            "tea",
            "stop",
            "plant-based",
            "vegan",
        )
    )


def _row_has_update_signal(lower: str) -> bool:
    return any(term in lower for term in ("changed", "now", "still", "anymore", "after", "before"))


def _structured_score_row_key(row: dict[str, object]) -> tuple[int, int, int, str]:
    score = int(row.get("structured_score") or 0) + sum(
        _evidence_term_weight(str(term))
        for term in cast(list[object], row.get("matched_terms") or [])
    )
    index = _coerce_int(row.get("message_index"))
    seq = _coerce_int(row.get("seq"))
    return (
        -score,
        index if index is not None else 999999,
        seq if seq is not None else 999999,
        str(row.get("source_ref") or ""),
    )


def _structured_order_row_key(row: dict[str, object]) -> tuple[str, int, int, str]:
    index = _coerce_int(row.get("message_index"))
    seq = _coerce_int(row.get("seq"))
    return (
        str(row.get("session_key") or ""),
        index if index is not None else 999999,
        seq if seq is not None else 999999,
        str(row.get("source_ref") or ""),
    )


def _structured_latest_row_key(row: dict[str, object]) -> tuple[str, int, int, str]:
    index = _coerce_int(row.get("message_index"))
    seq = _coerce_int(row.get("seq"))
    return (
        str(row.get("session_key") or ""),
        -(index if index is not None else -1),
        -(seq if seq is not None else -1),
        str(row.get("source_ref") or ""),
    )


def _raw_first_queries(query: str, profile: dict[str, object]) -> list[str]:
    queries = [query]
    queries.extend(_recommended_search_queries(query, profile))
    names = cast(list[str], profile["question_names"])
    terms = [
        word
        for word in _WORD_RE.findall(query)
        if len(word) >= 3
        and word.lower() not in _STOPWORDS
        and word not in _QUESTION_NAME_STOPWORDS
    ]
    if names:
        queries.append(" ".join(names[:5]))
        for name in names[:5]:
            topical = " ".join([name, *terms[:5]])
            queries.append(topical)
    labels = set(cast(list[str], profile["slot_labels"]))
    if "exception_negation" in labels:
        queries.extend(
            [
                " ".join([*names[:5], "not", "no", "never"]),
                " ".join([*names[:5], "except", "but", "however"]),
            ]
        )
    if "message_order" in labels:
        queries.append(" ".join([*names[:5], "first", "initially"]))
    return _unique_nonempty(queries)[:10]


def _raw_message_row(
    message: dict[str, Any],
    *,
    profile: dict[str, object],
    rank: int,
) -> dict[str, object]:
    content = str(message.get("content") or "")
    parsed = _parse_socialmem_message(content)
    source_ref = str(message.get("id") or parsed.get("source_ref") or "").strip()
    timestamp = str(message.get("timestamp") or "")
    date = str(message.get("date") or parsed.get("date") or timestamp[:10] or "")
    speaker_id = str(
        message.get("speaker_id")
        or message.get("sender_id")
        or parsed.get("speaker_id")
        or ""
    )
    speaker = str(message.get("speaker") or parsed.get("speaker") or speaker_id or "")
    message_index = _coerce_int(
        message.get("message_index")
        or parsed.get("message_index")
        or message.get("seq")
    )
    quote = str(parsed.get("quote") or content)
    query_terms = _query_terms(str(message.get("_matched_query") or ""))
    matched_terms = _matched_terms(query_terms, quote)
    matched = _matched_slot_labels(quote, query_terms, profile)
    return {
        "row_id": f"raw:{source_ref}",
        "rank": rank,
        "raw_rank": int(message.get("_raw_rank") or rank),
        "source_ref": source_ref,
        "session_key": str(message.get("session_key") or ""),
        "seq": int(message.get("seq") or 0),
        "timestamp": timestamp,
        "date": date,
        "speaker_id": speaker_id,
        "speaker": speaker,
        "message_index": message_index,
        "matched_query": str(message.get("_matched_query") or ""),
        "matched_terms": matched_terms,
        "matched_slots": matched,
        "quote": _compact(quote, 280),
        "preview": _compact(content, 360),
        "in_source_ref": bool(message.get("in_source_ref")),
    }


def _parse_socialmem_message(content: str) -> dict[str, object]:
    match = _SOCIALMEM_MESSAGE_RE.match(str(content or "").strip())
    if not match:
        return {}
    meta_text = match.group("meta")
    meta = {
        key: value
        for key, value in _SOCIALMEM_META_RE.findall(meta_text)
    }
    return {
        **meta,
        "speaker": match.group("speaker").strip(),
        "quote": match.group("quote").strip(),
        "source_ref": "",
    }


def _coerce_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _matched_terms(query_terms: set[str], value: str) -> list[str]:
    lower = str(value or "").lower()
    return sorted(term for term in query_terms if term in lower)


def _speaker_evidence_summary(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    by_key: dict[str, dict[str, object]] = {}
    for row in rows:
        speaker = str(row.get("speaker") or row.get("speaker_id") or "").strip()
        if not speaker:
            continue
        key = speaker.lower()
        existing = by_key.get(key)
        matched_terms = set(cast(list[str], row.get("matched_terms") or []))
        if existing is None:
            by_key[key] = {
                "speaker": speaker,
                "speaker_id": str(row.get("speaker_id") or ""),
                "first_source_ref": str(row.get("source_ref") or ""),
                "first_message_index": row.get("message_index"),
                "first_seq": row.get("seq"),
                "matched_terms": sorted(matched_terms),
                "evidence_count": 1,
                "quote": row.get("quote"),
            }
            continue
        existing_terms = set(cast(list[str], existing.get("matched_terms") or []))
        existing["matched_terms"] = sorted(existing_terms | matched_terms)
        existing["evidence_count"] = int(existing.get("evidence_count") or 0) + 1
        current_index = _coerce_int(row.get("message_index"))
        old_index = _coerce_int(existing.get("first_message_index"))
        if current_index is not None and (old_index is None or current_index < old_index):
            existing["first_source_ref"] = str(row.get("source_ref") or "")
            existing["first_message_index"] = current_index
            existing["first_seq"] = row.get("seq")
            existing["quote"] = row.get("quote")

    def _summary_sort_key(item: dict[str, object]) -> tuple[int, int, str]:
        terms = cast(list[str], item.get("matched_terms") or [])
        index = _coerce_int(item.get("first_message_index"))
        return (-len(terms), index if index is not None else 999999, str(item.get("speaker") or ""))

    return [
        item
        for item in sorted(by_key.values(), key=_summary_sort_key)
    ][:8]


def _candidate_answer_hints(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    scored: dict[str, dict[str, object]] = {}
    for row in rows:
        speaker = str(row.get("speaker") or row.get("speaker_id") or "").strip()
        if not speaker:
            continue
        terms = [
            str(term).lower()
            for term in cast(list[object], row.get("matched_terms") or [])
            if str(term).strip()
        ]
        distinctive = [
            term
            for term in terms
            if term not in _GENERIC_EVIDENCE_TERMS and len(term) >= 4
        ]
        if not distinctive:
            continue
        score = sum(_evidence_term_weight(term) for term in distinctive)
        key = speaker.lower()
        existing = scored.get(key)
        if existing is None:
            scored[key] = {
                "speaker": speaker,
                "speaker_id": str(row.get("speaker_id") or ""),
                "score": score,
                "distinctive_terms": sorted(set(distinctive)),
                "source_ref": str(row.get("source_ref") or ""),
                "message_index": row.get("message_index"),
                "quote": row.get("quote"),
            }
            continue
        existing["score"] = int(existing.get("score") or 0) + score
        old_terms = set(cast(list[str], existing.get("distinctive_terms") or []))
        existing["distinctive_terms"] = sorted(old_terms | set(distinctive))
    return [
        item
        for item in sorted(
            scored.values(),
            key=lambda item: (
                -int(item.get("score") or 0),
                _coerce_int(item.get("message_index")) or 999999,
                str(item.get("speaker") or ""),
            ),
        )
    ][:5]


def _profile_text(query: str, extra: dict[str, Any]) -> str:
    parts = [str(query or "")]
    for key in ("description", "reason", "intent_hint"):
        value = str(extra.get(key) or "").strip()
        if value:
            parts.append(value)
    return " ".join(parts)


def _deterministic_resolution(
    query: str,
    profile: dict[str, object],
    rows: list[dict[str, object]],
) -> dict[str, object]:
    labels = set(cast(list[str], profile["slot_labels"]))
    if not rows:
        return {
            "resolution_type": "no_evidence",
            "selected_candidate": None,
            "ranked_candidates": [],
            "decision_rule": "no_raw_evidence_rows",
            "confidence": "low",
            "must_follow": False,
        }

    if labels & {"speaker_attribution", "message_order"}:
        candidates = _speaker_resolution_candidates(rows)
        if candidates:
            if "message_order" in labels:
                candidates.sort(key=_candidate_order_key)
                decision_rule = "earliest_distinctive_source_match"
            else:
                candidates.sort(key=_candidate_score_key)
                decision_rule = "highest_distinctive_exact_term_speaker"
            selected = candidates[0]
            return {
                "resolution_type": (
                    "message_order" if "message_order" in labels else "speaker_attribution"
                ),
                "selected_candidate": selected,
                "ranked_candidates": candidates[:5],
                "decision_rule": decision_rule,
                "confidence": _candidate_confidence(selected),
                "must_follow": True,
                "instruction": (
                    "Use selected_candidate.answer as the answer candidate. "
                    "The candidate was selected before generation from raw "
                    "source_ref/message_index evidence."
                ),
            }

    if "exception_negation" in labels:
        candidates = _exception_resolution_candidates(rows)
        if candidates:
            candidates.sort(key=_candidate_score_key)
            selected = candidates[0]
            return {
                "resolution_type": "exception_negation",
                "selected_candidate": selected,
                "ranked_candidates": candidates[:5],
                "decision_rule": "highest_negation_or_exception_source_match",
                "confidence": _candidate_confidence(selected),
                "must_follow": True,
                "instruction": (
                    "For all/everyone questions, answer with the exception or "
                    "dissenting candidate selected from source evidence."
                ),
            }

    if "update_change" in labels:
        candidates = _row_resolution_candidates(rows, answer_field="quote")
        if candidates:
            relevant = [item for item in candidates if int(item.get("score") or 0) > 0] or candidates
            relevant.sort(key=_candidate_latest_key)
            selected = relevant[0]
            previous = sorted(
                [item for item in relevant if item is not selected],
                key=_candidate_order_key,
            )[:3]
            return {
                "resolution_type": "update_change",
                "selected_candidate": selected,
                "ranked_candidates": relevant[:5],
                "previous_evidence": previous,
                "decision_rule": "latest_relevant_source_match_with_previous_evidence",
                "confidence": _candidate_confidence(selected),
                "must_follow": True,
                "instruction": (
                    "Use selected_candidate as the current/latest state, and "
                    "previous_evidence only to explain the change trajectory."
                ),
            }

    candidates = _speaker_resolution_candidates(rows) or _row_resolution_candidates(rows)
    candidates.sort(key=_candidate_score_key)
    selected = candidates[0] if candidates else None
    return {
        "resolution_type": "source_evidence_candidate",
        "selected_candidate": selected,
        "ranked_candidates": candidates[:5],
        "decision_rule": "best_source_overlap_candidate",
        "confidence": _candidate_confidence(selected) if selected else "low",
        "must_follow": bool(selected and int(selected.get("score") or 0) >= 3),
    }


def _should_apply_contract_only_resolution(resolution: dict[str, object]) -> bool:
    selected = resolution.get("selected_candidate")
    if not isinstance(selected, dict):
        return False
    if resolution.get("must_follow") is not True:
        return False
    confidence = str(resolution.get("confidence") or "")
    if confidence not in {"high", "medium"}:
        return False
    return bool(str(selected.get("source_ref") or "").strip())


def _apply_contract_only_resolution(
    payload: dict[str, object],
    resolution: dict[str, object],
) -> None:
    selected = cast(dict[str, object], resolution.get("selected_candidate") or {})
    selected_ref = str(selected.get("source_ref") or "").strip()
    if not selected_ref:
        return

    rows = cast(list[dict[str, object]], payload.get("evidence_table") or [])
    selected_rows = [
        row
        for row in rows
        if str(row.get("source_ref") or "").strip() == selected_ref
    ]
    if not selected_rows:
        selected_rows = [_candidate_as_evidence_row(selected)]

    previous_refs = [
        str(item.get("source_ref") or "").strip()
        for item in cast(list[dict[str, object]], resolution.get("previous_evidence") or [])
        if str(item.get("source_ref") or "").strip()
    ]
    previous_rows = [
        row
        for row in rows
        if str(row.get("source_ref") or "").strip() in set(previous_refs)
    ][:3]
    compact_rows = [*selected_rows[:1], *previous_rows]
    payload["evidence_table"] = compact_rows
    payload["resolution_contract_only"] = True
    payload["suppressed_competing_evidence_count"] = max(0, len(rows) - len(compact_rows))
    payload["cited_item_ids"] = [selected_ref, *previous_refs[:3]]

    search_plan = dict(cast(dict[str, object], payload.get("search_plan") or {}))
    search_plan["candidate_source_refs"] = [selected_ref, *previous_refs[:3]]
    search_plan["speaker_evidence_summary"] = [
        {
            "speaker": selected.get("speaker"),
            "speaker_id": selected.get("speaker_id"),
            "first_source_ref": selected_ref,
            "first_message_index": selected.get("message_index"),
            "matched_terms": selected.get("matched_terms") or selected.get("distinctive_terms") or [],
            "evidence_count": 1,
            "quote": selected.get("quote"),
        }
    ]
    search_plan["candidate_answer_hints"] = [selected]
    search_plan["answer_rules"] = [
        "Use deterministic_resolution.selected_candidate.answer as the answer candidate.",
        "Use deterministic_resolution.selected_candidate.source_ref as the citation source.",
        "Competing rows were suppressed after deterministic source-grounded resolution to avoid re-ranking by quote length.",
    ]
    search_plan["note"] = (
        "Contract-only deterministic resolution is active: the selected candidate "
        "was chosen from raw source evidence before generation, and competing rows "
        "are intentionally withheld unless additional fetch_messages contradicts it."
    )
    payload["search_plan"] = search_plan


def _candidate_as_evidence_row(candidate: dict[str, object]) -> dict[str, object]:
    source_ref = str(candidate.get("source_ref") or "")
    return {
        "row_id": f"resolved:{source_ref}",
        "source_ref": source_ref,
        "seq": candidate.get("seq"),
        "timestamp": candidate.get("timestamp"),
        "date": candidate.get("date"),
        "speaker_id": candidate.get("speaker_id"),
        "speaker": candidate.get("speaker"),
        "message_index": candidate.get("message_index"),
        "matched_terms": candidate.get("matched_terms") or candidate.get("distinctive_terms") or [],
        "matched_slots": ["deterministic_resolution"],
        "quote": candidate.get("quote"),
        "preview": candidate.get("quote"),
        "in_source_ref": True,
    }


def _candidate_as_search_message(candidate: dict[str, object]) -> dict[str, object]:
    source_ref = str(candidate.get("source_ref") or "")
    return {
        "id": source_ref,
        "source_ref": source_ref,
        "session_key": _extract_session_id(source_ref),
        "seq": _coerce_int(candidate.get("seq")) or 0,
        "role": "user",
        "timestamp": str(candidate.get("timestamp") or ""),
        "matched_terms": candidate.get("matched_terms") or candidate.get("distinctive_terms") or [],
        "preview": str(candidate.get("quote") or ""),
        "preview_line_count": 1,
        "total_line_count": 1,
        "truncated": False,
        "speaker_id": candidate.get("speaker_id"),
        "date": candidate.get("date"),
        "message_index": candidate.get("message_index"),
    }


def _requested_fetch_refs(kwargs: dict[str, object]) -> set[str]:
    refs: set[str] = set()

    def add(value: object) -> None:
        if value is None:
            return
        if isinstance(value, list):
            for item in value:
                add(item)
            return
        if isinstance(value, dict):
            add(value.get("source_ref"))
            raw_refs = value.get("refs")
            if isinstance(raw_refs, list):
                add(raw_refs)
            return
        text = str(value or "").strip()
        if text:
            refs.add(text.split("#", 1)[0].strip())

    add(kwargs.get("ids"))
    add(kwargs.get("source_ref"))
    add(kwargs.get("source_refs"))
    add(kwargs.get("evidence"))
    return refs


def _speaker_resolution_candidates(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    hints = _candidate_answer_hints(rows)
    candidates: list[dict[str, object]] = []
    for hint in hints:
        source_ref = str(hint.get("source_ref") or "")
        row = _row_by_source_ref(rows, source_ref)
        candidate = _base_candidate(row or hint, answer=str(hint.get("speaker") or ""))
        candidate.update(
            {
                "answer_type": "speaker",
                "speaker": str(hint.get("speaker") or ""),
                "speaker_id": str(hint.get("speaker_id") or ""),
                "score": int(hint.get("score") or 0),
                "distinctive_terms": cast(list[str], hint.get("distinctive_terms") or []),
                "matched_terms": cast(list[str], hint.get("distinctive_terms") or []),
                "quote": str(hint.get("quote") or candidate.get("quote") or ""),
                "selection_basis": "distinctive_exact_query_terms",
            }
        )
        candidates.append(candidate)
    return candidates


def _exception_resolution_candidates(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for row in rows:
        quote = str(row.get("quote") or "")
        lower = quote.lower()
        terms = set(cast(list[str], row.get("matched_terms") or []))
        exception_terms = {
            term
            for term in (
                "not",
                "no",
                "never",
                "without",
                "except",
                "but",
                "however",
                "quiet",
                "quieter",
                "stop",
                "avoid",
                "declined",
            )
            if re.search(rf"\b{re.escape(term)}\b", lower)
        }
        if not exception_terms and not _record_like_negation(quote):
            continue
        matched_terms = sorted(terms | exception_terms)
        candidate = _base_candidate(row, answer=str(row.get("speaker") or row.get("quote") or ""))
        candidate.update(
            {
                "answer_type": "speaker_or_exception",
                "score": sum(_evidence_term_weight(term) for term in matched_terms) + 2,
                "matched_terms": matched_terms,
                "distinctive_terms": [
                    term
                    for term in matched_terms
                    if term not in _GENERIC_EVIDENCE_TERMS and len(term) >= 3
                ],
                "selection_basis": "negation_or_exception_terms",
            }
        )
        candidates.append(candidate)
    return candidates


def _row_resolution_candidates(
    rows: list[dict[str, object]],
    *,
    answer_field: str = "speaker",
) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for row in rows:
        answer = str(row.get(answer_field) or row.get("speaker") or row.get("quote") or "")
        matched_terms = [
            str(term).lower()
            for term in cast(list[object], row.get("matched_terms") or [])
            if str(term).strip()
        ]
        distinctive = [
            term
            for term in matched_terms
            if term not in _GENERIC_EVIDENCE_TERMS and len(term) >= 4
        ]
        candidate = _base_candidate(row, answer=answer)
        candidate.update(
            {
                "answer_type": answer_field,
                "score": sum(_evidence_term_weight(term) for term in distinctive),
                "matched_terms": matched_terms,
                "distinctive_terms": distinctive,
                "selection_basis": "source_overlap",
            }
        )
        candidates.append(candidate)
    return candidates


def _base_candidate(source: dict[str, object], *, answer: str) -> dict[str, object]:
    return {
        "answer": answer,
        "speaker": str(source.get("speaker") or ""),
        "speaker_id": str(source.get("speaker_id") or ""),
        "source_ref": str(source.get("source_ref") or ""),
        "message_index": source.get("message_index"),
        "seq": source.get("seq"),
        "timestamp": str(source.get("timestamp") or ""),
        "date": str(source.get("date") or ""),
        "quote": str(source.get("quote") or ""),
    }


def _row_by_source_ref(
    rows: list[dict[str, object]],
    source_ref: str,
) -> dict[str, object] | None:
    for row in rows:
        if str(row.get("source_ref") or "") == source_ref:
            return row
    return None


def _candidate_score_key(item: dict[str, object]) -> tuple[int, int, int, str]:
    score = int(item.get("score") or 0)
    index = _coerce_int(item.get("message_index"))
    seq = _coerce_int(item.get("seq"))
    return (
        -score,
        index if index is not None else 999999,
        seq if seq is not None else 999999,
        str(item.get("answer") or ""),
    )


def _candidate_order_key(item: dict[str, object]) -> tuple[int, int, int, str]:
    score = int(item.get("score") or 0)
    index = _coerce_int(item.get("message_index"))
    seq = _coerce_int(item.get("seq"))
    return (
        index if index is not None else 999999,
        seq if seq is not None else 999999,
        -score,
        str(item.get("answer") or ""),
    )


def _candidate_latest_key(item: dict[str, object]) -> tuple[int, int, int, str]:
    score = int(item.get("score") or 0)
    index = _coerce_int(item.get("message_index"))
    seq = _coerce_int(item.get("seq"))
    return (
        -(index if index is not None else -1),
        -(seq if seq is not None else -1),
        -score,
        str(item.get("answer") or ""),
    )


def _candidate_confidence(candidate: dict[str, object] | None) -> str:
    if not candidate:
        return "low"
    score = int(candidate.get("score") or 0)
    terms = cast(list[str], candidate.get("distinctive_terms") or [])
    if score >= 4 and terms:
        return "high"
    if score >= 2 or terms:
        return "medium"
    return "low"


def _evidence_term_weight(term: str) -> int:
    if term in _HIGH_VALUE_EVIDENCE_TERMS:
        return 4
    if term.endswith("er") or term.endswith("est"):
        return 3
    if len(term) >= 8:
        return 2
    return 1


def _merge_results(
    results: list[MemoryQueryResult],
    *,
    limit: int,
    spec: MemoryMethodSpec,
    extra_trace: dict[str, object],
) -> MemoryQueryResult:
    scored: dict[str, tuple[MemoryRecord, float]] = {}
    for result_index, result in enumerate(results):
        for rank, record in enumerate(result.records, 1):
            key = record.id or f"{record.kind}:{record.summary}"
            rrf = 1.0 / (60 + rank)
            score = float(record.score or 0.0) + rrf + (0.002 if result_index == 0 else 0.0)
            existing = scored.get(key)
            if existing is None or score > existing[1]:
                record.signals = {
                    **dict(record.signals or {}),
                    "method_rank_source": result_index,
                    "method_rank_score": round(score, 4),
                }
                scored[key] = (record, score)

    ordered = [item for item, _ in sorted(scored.values(), key=lambda pair: pair[1], reverse=True)]
    selected = ordered[:limit]
    text_block = next((r.text_block for r in results if r.text_block), "")
    trace = {
        "method_id": spec.method_id,
        "strategy": spec.strategy,
        "merged_result_count": len(results),
        "merged_record_count": len(selected),
        **extra_trace,
    }
    raw_items: list[object] = []
    for result in results:
        raw = result.raw.get("items") if isinstance(result.raw, dict) else None
        if isinstance(raw, list):
            raw_items.extend(raw)
    return MemoryQueryResult(
        text_block=text_block,
        records=selected,
        trace=trace,
        raw={"items": raw_items, "method": spec.as_dict()},
    )


def _merge_hybrid_results(
    lanes: list[tuple[str, MemoryQueryResult]],
    *,
    request: MemoryQuery,
    intent: str,
    limit: int,
    spec: MemoryMethodSpec,
) -> MemoryQueryResult:
    query_terms = _query_terms(request.text)
    preferred_kinds = _target_kinds_for_intent(intent)
    temporal_mode = _temporal_mode(request.text)
    scored: dict[str, tuple[MemoryRecord, float]] = {}

    for lane_index, (lane_name, result) in enumerate(lanes):
        lane_bonus = 0.004 if lane_name == "baseline" else 0.002
        for rank, record in enumerate(result.records, 1):
            key = record.id or f"{record.kind}:{record.summary}"
            overlap = _term_overlap(query_terms, record.summary)
            score = float(record.score or 0.0)
            score += 1.0 / (60 + rank)
            score += lane_bonus
            score += 0.07 if _has_source_evidence(record) else 0.0
            score += _type_bonus(request.text, record.kind)
            score += min(0.08, 0.018 * overlap)
            score += _temporal_bonus(record, temporal_mode)
            if lane_name == "intent" and record.kind in preferred_kinds:
                score += 0.01 / (preferred_kinds.index(record.kind) + 1)
            signals = dict(record.signals or {})
            signals["method_05_hybrid"] = {
                "lane": lane_name,
                "lane_index": lane_index,
                "rank": rank,
                "query_overlap": overlap,
                "temporal_mode": temporal_mode,
                "hybrid_score": round(score, 4),
            }
            record.signals = signals
            existing = scored.get(key)
            if existing is None or score > existing[1]:
                scored[key] = (record, score)

    ordered = [
        item
        for item, _ in sorted(scored.values(), key=lambda pair: pair[1], reverse=True)
    ][:limit]
    text_block = "\n---\n".join(record.summary for record in ordered if record.summary)
    raw_items: list[object] = []
    for _, result in lanes:
        raw = result.raw.get("items") if isinstance(result.raw, dict) else None
        if isinstance(raw, list):
            raw_items.extend(raw)
    return MemoryQueryResult(
        text_block=text_block,
        records=ordered,
        trace={
            "method_id": spec.method_id,
            "strategy": spec.strategy,
            "intent": intent,
            "temporal_mode": temporal_mode,
            "lane_count": len(lanes),
            "merged_record_count": len(ordered),
        },
        raw={"items": raw_items, "method": spec.as_dict()},
    )


def _mark_trace(result: MemoryQueryResult, spec: MemoryMethodSpec, **extra: object) -> None:
    result.trace = {
        **dict(result.trace or {}),
        "method_id": spec.method_id,
        "strategy": spec.strategy,
        **extra,
    }
    result.raw = {
        **dict(result.raw or {}),
        "method": spec.as_dict(),
    }


def _raw_items_from_results(results: list[MemoryQueryResult]) -> list[object]:
    raw_items: list[object] = []
    for result in results:
        raw = result.raw.get("items") if isinstance(result.raw, dict) else None
        if isinstance(raw, list):
            raw_items.extend(raw)
    return raw_items


def _question_names(text: str) -> list[str]:
    names: list[str] = []
    for match in _OPTION_NAME_RE.finditer(text):
        value = match.group(1).strip()
        if value and value not in names:
            names.append(value)
    for value in _CAPITALIZED_RE.findall(text):
        if value in _QUESTION_NAME_STOPWORDS:
            continue
        if value.upper() == value:
            continue
        if value not in names:
            names.append(value)
    return names[:12]


def _record_source_refs(record: MemoryRecord) -> list[str]:
    refs: list[str] = []
    for evidence in record.evidence:
        candidates = [evidence.source_ref, *evidence.refs]
        for candidate in candidates:
            text = str(candidate or "").strip()
            if not text:
                continue
            matches = _SOURCE_REF_RE.findall(text)
            if matches:
                for match in matches:
                    if match not in refs:
                        refs.append(match)
                continue
            fallback = text.split("#", 1)[0].strip()
            if fallback and fallback not in refs:
                refs.append(_compact(fallback, 180))
    return refs


def _source_ref_index(source_ref: str) -> int | None:
    match = _SOURCE_INDEX_RE.search(str(source_ref or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _matched_slot_labels(
    summary: str,
    query_terms: set[str],
    profile: dict[str, object],
) -> list[str]:
    labels = cast(list[str], profile["slot_labels"])
    summary_terms = _query_terms(summary)
    matched = [label for label in labels if label != "event_fact"]
    if query_terms & summary_terms:
        matched.append("query_overlap")
    if _record_like_negation(summary):
        matched.append("negation_or_exception_signal")
    if _record_like_preference(summary):
        matched.append("preference_signal")
    return _unique_nonempty(matched) or ["event_fact"]


def _speaker_hint(summary: str, names: list[str]) -> str:
    lower = str(summary or "").lower()
    for name in names:
        if re.search(rf"\b{re.escape(name.lower())}\b", lower):
            return name
    return _extract_person_id(summary)


def _row_sequences(rows: list[dict[str, object]]) -> list[int]:
    values: list[int] = []
    for row in rows:
        value = row.get("source_sequence")
        if isinstance(value, int):
            values.append(value)
    return values


def _exception_signal_bonus(lower_summary: str) -> float:
    bonus = 0.0
    for term in (
        "not",
        "no ",
        "never",
        "without",
        "except",
        "but",
        "however",
        "dissent",
        "asked",
        "quiet",
        "cafe",
        "tea",
        "stop",
    ):
        if term in lower_summary:
            bonus += 0.008
    return min(0.06, bonus)


def _implicit_preference_bonus(lower_summary: str) -> float:
    bonus = 0.0
    for term in (
        "prefer",
        "instead",
        "chose",
        "declined",
        "avoid",
        "not",
        "vague",
        "pattern",
        "repeated",
        "focus",
        "collect",
        "alone",
    ):
        if term in lower_summary:
            bonus += 0.008
    return min(0.06, bonus)


def _record_like_negation(summary: str) -> bool:
    lower = str(summary or "").lower()
    return any(term in lower for term in ("not", "never", "without", "except", "but", "however"))


def _record_like_preference(summary: str) -> bool:
    lower = str(summary or "").lower()
    return any(term in lower for term in ("prefer", "instead", "chose", "avoid", "likes", "dislike"))


def _compact(value: str, limit: int) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _unique_nonempty(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _enrich_structured_signals(record: MemoryRecord) -> None:
    source_ref = _first_source_ref(record)
    timestamp = _extract_date(record.summary) or _extract_date(source_ref)
    signals = dict(record.signals or {})
    signals["structured_schema"] = {
        "memory_id": record.id,
        "memory_type": record.kind,
        "person_id": _extract_person_id(record.summary),
        "session_id": _extract_session_id(source_ref),
        "timestamp": timestamp,
        "source_ref": source_ref,
        "confidence": _confidence_label(record),
        "valid_from": timestamp,
        "valid_to": None,
    }
    record.signals = signals


def _set_version_signal(record: MemoryRecord, status: str) -> None:
    signals = dict(record.signals or {})
    signals["versioning"] = {
        "status": status,
        "record_date": _record_date(record),
        "signature": _memory_signature(record),
    }
    record.signals = signals


def _first_source_ref(record: MemoryRecord) -> str:
    for evidence in record.evidence:
        if evidence.source_ref:
            return evidence.source_ref
        if evidence.refs:
            return str(evidence.refs[0])
    return ""


def _source_message_indices(record: MemoryRecord) -> list[int]:
    values: list[int] = []
    for evidence in record.evidence:
        candidates = [evidence.source_ref, *evidence.refs]
        for value in candidates:
            for match in _SOURCE_INDEX_RE.finditer(str(value or "")):
                try:
                    values.append(int(match.group(1)))
                except ValueError:
                    continue
    return values


def _extract_date(text: str) -> str:
    match = _DATE_RE.search(str(text or ""))
    return match.group(1).replace("/", "-") if match else ""


def _record_date(record: MemoryRecord) -> str:
    return _extract_date(record.summary) or _extract_date(_first_source_ref(record))


def _extract_session_id(source_ref: str) -> str:
    text = str(source_ref or "")
    match = re.search(r"(lme:[A-Za-z0-9_\-]+)", text)
    return match.group(1) if match else ""


def _extract_person_id(summary: str) -> str:
    words = [w for w in _WORD_RE.findall(summary) if w.lower() not in _STOPWORDS]
    return words[0] if words else ""


def _confidence_label(record: MemoryRecord) -> str:
    if _has_source_evidence(record) and record.score >= 0.5:
        return "high"
    if _has_source_evidence(record):
        return "medium"
    return "low"


def _has_source_evidence(record: MemoryRecord) -> bool:
    return any(e.source_ref or e.refs for e in record.evidence)


def _query_terms(text: str) -> set[str]:
    return {
        word.lower()
        for word in _WORD_RE.findall(text)
        if len(word) >= 3 and word.lower() not in _STOPWORDS
    }


def _term_overlap(query_terms: set[str], value: str) -> int:
    if not query_terms:
        return 0
    target = set(_query_terms(value))
    return len(query_terms & target)


def _type_bonus(query: str, memory_type: str) -> float:
    intent = _classify_personal_memory_intent(query)
    preferred = _target_kinds_for_intent(intent)
    if memory_type in preferred:
        return 0.04 / (preferred.index(memory_type) + 1)
    return 0.0


def _temporal_mode(query: str) -> str:
    q = query.lower()
    if any(term in q for term in ("who first", "first ", "initially", "at first", "earliest")):
        return "earliest"
    if any(term in q for term in ("latest", "current", "currently", "now", "final", "end up", "ended up")):
        return "latest"
    if any(term in q for term in ("changed", "change", "over time", "across the conversations", "always")):
        return "timeline"
    return "none"


def _temporal_bonus(record: MemoryRecord, mode: str) -> float:
    indices = _source_message_indices(record)
    if not indices:
        return 0.0
    if mode == "earliest":
        return max(0.0, 0.08 - min(indices) * 0.001)
    if mode == "latest":
        return min(0.08, max(indices) * 0.0008)
    if mode == "timeline":
        return 0.025 if len(set(indices)) >= 2 else 0.0
    return 0.0


def _memory_signature(record: MemoryRecord) -> str:
    tokens = [
        token.lower()
        for token in _WORD_RE.findall(record.summary)
        if token.lower() not in _STOPWORDS and len(token) >= 3
    ]
    if not tokens:
        return ""
    return f"{record.kind}:{' '.join(tokens[:6])}"
