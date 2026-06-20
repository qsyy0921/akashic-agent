from __future__ import annotations

import json
import asyncio
from pathlib import Path

from core.memory.engine import (
    EngineProfile,
    EvidenceRef,
    MemoryCapability,
    MemoryEngineDescriptor,
    MemoryMutationResult,
    MemoryQuery,
    MemoryQueryResult,
    MemoryRecord,
    MemoryToolSpec,
    MemoryToolProfile,
)
from eval.longmemeval.methods import (
    ConsolidatedFactPrecisionRecallTool,
    ConsolidatedMemoryWriteQualityRecallTool,
    ConservativePrecisionGateRecallTool,
    DeterministicResolutionState,
    DeterministicResolverRecallTool,
    IntentAwareRetrievalEngine,
    QuestionAwareStructuredRouterRecallTool,
    RawMessageFirstRecallTool,
    SlotDecisionAnswerPlannerRecallTool,
    SourceGroundedSlotResolverEngine,
    StructuredAnswerContractRecallTool,
    StructuredCandidateResolverRecallTool,
    MemoryMethodSpec,
    apply_structured_answer_contract_to_result,
    load_method_spec,
    reset_benchmark_question_context,
    set_benchmark_question_context,
)
from eval.longmemeval.summarize_method_results import summarize_payload
from memory2.store import MemoryStore2


class FakeMemoryEngine:
    DESCRIPTOR = MemoryEngineDescriptor(
        name="fake",
        profile=EngineProfile.RICH_MEMORY_ENGINE,
        capabilities=frozenset({MemoryCapability.RETRIEVE_STRUCTURED_HITS}),
    )

    def __init__(self) -> None:
        self.requests: list[MemoryQuery] = []

    async def query(self, request: MemoryQuery) -> MemoryQueryResult:
        self.requests.append(request)
        kinds = ",".join(request.filters.kinds) if request.filters.kinds else "all"
        return MemoryQueryResult(
            records=[
                MemoryRecord(
                    id=f"{len(self.requests)}:{kinds}",
                    kind=request.filters.kinds[0] if request.filters.kinds else "event",
                    summary=f"{kinds} result",
                    score=0.5,
                    engine_kind="fake",
                    evidence=[
                        EvidenceRef(
                            source_ref=f"lme:fake_query:{len(self.requests)}",
                            refs=[f"lme:fake_query:{len(self.requests)}"],
                        )
                    ],
                )
            ],
            trace={"source": "fake"},
            raw={"items": [{"kinds": kinds}]},
        )

    def tool_profile(self) -> MemoryToolProfile:
        return MemoryToolProfile(
            recall=MemoryToolSpec(
                description="fake recall",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            )
        )

    def describe(self) -> MemoryEngineDescriptor:
        return self.DESCRIPTOR

    async def ingest(self, request):
        raise AssertionError("not used")

    async def mutate(self, request):
        return MemoryMutationResult(accepted=False)

    def reinforce_items_batch(self, ids: list[str]) -> None:
        return None


def test_load_method_spec_json(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "method_id": "method_01",
                "strategy": "intent_aware_retrieval",
                "options": {"lane_limit": 10},
            }
        ),
        encoding="utf-8",
    )

    spec = load_method_spec(path)

    assert spec is not None
    assert spec.method_id == "method_01"
    assert spec.strategy == "intent_aware_retrieval"
    assert spec.options == {"lane_limit": 10}


def test_intent_aware_retrieval_runs_intent_lane_then_fallback(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "method_id": "method_01",
                "strategy": "intent_aware_retrieval",
                "options": {"lane_limit": 10},
            }
        ),
        encoding="utf-8",
    )
    spec = load_method_spec(config)
    fake = FakeMemoryEngine()
    engine = IntentAwareRetrievalEngine(fake, spec)  # type: ignore[arg-type]

    async def _run() -> tuple[MemoryQueryResult, FakeMemoryEngine]:
        result = await engine.query(
            MemoryQuery(
                text="What does Josh's behavior suggest about how he handles career questions?",
                limit=3,
            )
        )
        return result, fake

    result, fake_engine = asyncio.run(_run())

    assert len(fake_engine.requests) == 2
    assert fake_engine.requests[0].filters.kinds == ("preference", "profile", "event")
    assert fake_engine.requests[1].filters.kinds == ()
    assert result.trace["strategy"] == "intent_aware_retrieval"
    assert result.records


def test_source_grounded_slot_resolver_adds_evidence_signals(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "method_id": "method_07",
                "strategy": "source_grounded_slot_resolver",
                "options": {"candidate_limit": 4},
            }
        ),
        encoding="utf-8",
    )
    spec = load_method_spec(config)
    fake = FakeMemoryEngine()
    engine = SourceGroundedSlotResolverEngine(fake, spec)  # type: ignore[arg-type]

    async def _run() -> MemoryQueryResult:
        return await engine.query(
            MemoryQuery(
                text="Who first pushed back on committing to specific dates?",
                limit=2,
            )
        )

    result = asyncio.run(_run())

    assert result.trace["strategy"] == "source_grounded_slot_resolver"
    assert result.trace["slot_labels"] == ["speaker_attribution", "message_order"]
    assert result.records
    signal = result.records[0].signals["source_grounded_slot_resolver"]
    assert signal["requires_fetch_messages"] is True
    assert signal["evidence_rows"][0]["source_ref"].startswith("lme:fake_query:")
    assert result.records[0].summary.startswith("[source-grounded:")


class FakeSessionStore:
    def __init__(self) -> None:
        self.messages = [
            {
                "id": "lme:raw_case:0",
                "session_key": "lme:raw_case",
                "seq": 0,
                "role": "user",
                "content": (
                    "[SocialMemBench network=grp session=grp_s01 "
                    "session_index=1 date=2025-02-03 turn_id=t0 "
                    "speaker_id=p_emeka message_index=1] "
                    "Emeka: We'll see what Adaeze's schedule looks like"
                ),
                "timestamp": "2025-02-03T00:00:00+00:00",
            },
            {
                "id": "lme:raw_case:1",
                "session_key": "lme:raw_case",
                "seq": 1,
                "role": "user",
                "content": (
                    "[SocialMemBench network=grp session=grp_s01 "
                    "session_index=1 date=2025-02-03 turn_id=t1 "
                    "speaker_id=p_adaora message_index=2] "
                    "Adaora: I can try to come. Depends on thesis deadlines"
                ),
                "timestamp": "2025-02-03T00:00:00+00:00",
            },
        ]

    def search_messages(self, query: str, **_: object):
        terms = [term.lower() for term in query.split() if term]
        hits = [
            message
            for message in self.messages
            if any(term in message["content"].lower() for term in terms)
        ]
        return hits, len(hits)

    def fetch_by_ids_with_context(self, ids: list[str], context: int):
        return [
            message
            for message in self.messages
            if message["id"] in ids or context > 0
        ]


class QuietVenueSessionStore:
    def __init__(self) -> None:
        self.messages = [
            {
                "id": "lme:socialmem_Q4_d4e5f6a7:13",
                "session_key": "lme:socialmem_Q4_d4e5f6a7",
                "seq": 13,
                "role": "user",
                "content": (
                    "[SocialMemBench network=grp session=grp_s04 "
                    "session_index=4 date=2026-04-02 turn_id=t13 "
                    "speaker_id=p_j2b3c4d5 message_index=14] "
                    "Jordan: Quieter. Better."
                ),
                "timestamp": "2026-04-02T00:00:00+00:00",
            },
            {
                "id": "lme:socialmem_Q4_d4e5f6a7:15",
                "session_key": "lme:socialmem_Q4_d4e5f6a7",
                "seq": 15,
                "role": "user",
                "content": (
                    "[SocialMemBench network=grp session=grp_s04 "
                    "session_index=4 date=2026-04-02 turn_id=t15 "
                    "speaker_id=p_p3c4d5e6 message_index=16] "
                    "Priya: yes please! honestly I just want somewhere you can actually have a conversation"
                ),
                "timestamp": "2026-04-02T00:00:00+00:00",
            },
        ]

    def search_messages(self, query: str, **_: object):
        terms = [term.lower() for term in query.split() if term]
        hits = [
            message
            for message in self.messages
            if any(term in message["content"].lower() for term in terms)
        ]
        return hits, len(hits)

    def fetch_by_ids_with_context(self, ids: list[str], context: int):
        return [
            message
            for message in self.messages
            if message["id"] in ids or context > 0
        ]


class DrinksDecisionSessionStore:
    def __init__(self) -> None:
        self.messages = [
            {
                "id": "lme:socialmem_Q2_e1f2a3b4:42",
                "session_key": "lme:socialmem_Q2_e1f2a3b4",
                "seq": 42,
                "role": "user",
                "content": (
                    "[SocialMemBench network=grp session=grp_s03 "
                    "session_index=3 date=2025-01-20 turn_id=t42 "
                    "speaker_id=p_lena message_index=43] "
                    "Lena: Rooftop Fox after dinner?"
                ),
                "timestamp": "2025-01-20T00:00:00+00:00",
            },
            {
                "id": "lme:socialmem_Q2_e1f2a3b4:43",
                "session_key": "lme:socialmem_Q2_e1f2a3b4",
                "seq": 43,
                "role": "user",
                "content": (
                    "[SocialMemBench network=grp session=grp_s03 "
                    "session_index=3 date=2025-01-20 turn_id=t43 "
                    "speaker_id=p_jordan message_index=44] "
                    "Jordan: Fine."
                ),
                "timestamp": "2025-01-20T00:00:00+00:00",
            },
            {
                "id": "lme:socialmem_Q2_e1f2a3b4:44",
                "session_key": "lme:socialmem_Q2_e1f2a3b4",
                "seq": 44,
                "role": "user",
                "content": (
                    "[SocialMemBench network=grp session=grp_s03 "
                    "session_index=3 date=2025-01-20 turn_id=t44 "
                    "speaker_id=p_sam message_index=45] "
                    "Sam: ..."
                ),
                "timestamp": "2025-01-20T00:00:00+00:00",
            },
            {
                "id": "lme:socialmem_Q2_e1f2a3b4:45",
                "session_key": "lme:socialmem_Q2_e1f2a3b4",
                "seq": 45,
                "role": "user",
                "content": (
                    "[SocialMemBench network=grp session=grp_s03 "
                    "session_index=3 date=2025-01-20 turn_id=t45 "
                    "speaker_id=p_sam message_index=46] "
                    "Sam: sure"
                ),
                "timestamp": "2025-01-20T00:00:00+00:00",
            },
            {
                "id": "lme:socialmem_Q2_e1f2a3b4:46",
                "session_key": "lme:socialmem_Q2_e1f2a3b4",
                "seq": 46,
                "role": "user",
                "content": (
                    "[SocialMemBench network=grp session=grp_s03 "
                    "session_index=3 date=2025-01-20 turn_id=t46 "
                    "speaker_id=p_priya message_index=47] "
                    "Priya: perfect"
                ),
                "timestamp": "2025-01-20T00:00:00+00:00",
            },
        ]


def test_raw_message_first_recall_returns_evidence_table() -> None:
    fake = FakeMemoryEngine()
    spec = fake.tool_profile().recall
    assert spec is not None
    method_spec = MemoryMethodSpec(
        method_id="method_08",
        strategy="raw_message_first_resolver",
    )
    tool = RawMessageFirstRecallTool(fake, spec, FakeSessionStore(), method_spec)  # type: ignore[arg-type]

    async def _run() -> dict[str, object]:
        raw = await tool.execute(
            query="Who first pushed back on Christmas dates Emeka Adaora",
            raw_limit=5,
            context=1,
        )
        return json.loads(raw)

    payload = asyncio.run(_run())

    assert payload["raw_message_first"] is True
    assert payload["items"][0]["summary"] == "all result"
    assert not payload["items"][0]["summary"].startswith("[source-grounded:")
    rows = payload["evidence_table"]
    assert rows
    assert rows[0]["source_ref"] == "lme:raw_case:0"
    assert rows[0]["speaker"] == "Emeka"
    assert rows[0]["speaker_id"] == "p_emeka"
    assert rows[0]["message_index"] == 1


def test_deterministic_resolver_selects_exact_term_speaker() -> None:
    fake = FakeMemoryEngine()
    spec = fake.tool_profile().recall
    assert spec is not None
    method_spec = MemoryMethodSpec(
        method_id="method_09",
        strategy="deterministic_attribution_resolver",
    )
    state = DeterministicResolutionState()
    tool = DeterministicResolverRecallTool(
        fake,
        spec,
        QuietVenueSessionStore(),
        method_spec,
        state,
    )  # type: ignore[arg-type]

    async def _run() -> dict[str, object]:
        raw = await tool.execute(
            query="Who preferred quieter venues, Jordan or Priya?",
            raw_limit=5,
            context=1,
        )
        return json.loads(raw)

    payload = asyncio.run(_run())

    selected = payload["selected_candidate"]
    assert selected["answer"] == "Jordan"
    assert selected["source_ref"] == "lme:socialmem_Q4_d4e5f6a7:13"
    assert payload["deterministic_resolution"]["resolution_type"] == "speaker_attribution"
    assert payload["deterministic_resolution"]["must_follow"] is True
    assert state.source_ref == "lme:socialmem_Q4_d4e5f6a7:13"


def test_structured_candidate_resolver_selects_from_memory_raw_events(tmp_path: Path) -> None:
    fake = FakeMemoryEngine()
    spec = fake.tool_profile().recall
    assert spec is not None
    method_spec = MemoryMethodSpec(
        method_id="method_11",
        strategy="structured_candidate_resolver",
    )
    store = MemoryStore2(tmp_path / "memory2.db")
    store.upsert_raw_events_from_messages(QuietVenueSessionStore().messages)
    tool = StructuredCandidateResolverRecallTool(fake, spec, store, method_spec)  # type: ignore[arg-type]

    async def _run() -> dict[str, object]:
        raw = await tool.execute(
            query=(
                "Someone expressed a clear preference for quieter venues where "
                "people can actually have a real conversation. Who said it?\n\n"
                "Options:\nA. Jordan\nB. Maya\nC. Priya\nD. Sam"
            ),
            raw_limit=8,
        )
        return json.loads(raw)

    payload = asyncio.run(_run())

    assert payload["structured_candidate_resolver"] is True
    selected = payload["selected_candidate"]
    assert selected["answer"] == "Jordan"
    assert selected["source_ref"] == "lme:socialmem_Q4_d4e5f6a7:13"
    assert payload["candidate_resolution"]["resolution_type"] == "speaker_attribution"
    rows = payload["selected_evidence_table"]
    assert rows[0]["source_table"] == "memory_raw_events"
    assert rows[0]["speaker"] == "Jordan"


def test_question_aware_structured_router_scopes_to_original_question(tmp_path: Path) -> None:
    fake = FakeMemoryEngine()
    spec = fake.tool_profile().recall
    assert spec is not None
    method_spec = MemoryMethodSpec(
        method_id="method_12",
        strategy="question_aware_structured_router",
        options={"selected_evidence_limit": 8},
    )
    store = MemoryStore2(tmp_path / "memory2.db")
    store.upsert_raw_events_from_messages(
        [
            *QuietVenueSessionStore().messages,
            {
                "id": "lme:other_case:1",
                "session_key": "lme:other_case",
                "seq": 1,
                "role": "user",
                "content": (
                    "[SocialMemBench network=grp session=other_s01 "
                    "session_index=1 date=2026-04-02 turn_id=t1 "
                    "speaker_id=p_priya message_index=1] "
                    "Priya: I want somewhere you can actually have a conversation"
                ),
                "timestamp": "2026-04-02T00:00:00+00:00",
            },
        ]
    )
    tool = QuestionAwareStructuredRouterRecallTool(fake, spec, store, method_spec)  # type: ignore[arg-type]
    token = set_benchmark_question_context(
        {
            "question_id": "socialmem_Q4_d4e5f6a7",
            "question_type": "single-session-user",
            "question": (
                "Someone in the group expressed a clear preference for quieter "
                "venues where people can actually have a real conversation. Who said it?\n\n"
                "Options:\nA. Jordan\nB. Maya\nC. Priya\nD. Sam"
            ),
        }
    )

    async def _run() -> dict[str, object]:
        raw = await tool.execute(query="Who said it?", raw_limit=8)
        return json.loads(raw)

    try:
        payload = asyncio.run(_run())
    finally:
        reset_benchmark_question_context(token)

    assert payload["question_aware_structured_router"] is True
    assert payload["question_context"]["source_session_key"] == "lme:socialmem_Q4_d4e5f6a7"
    selected = payload["selected_candidate"]
    assert selected["answer"] == "Jordan"
    assert selected["source_ref"] == "lme:socialmem_Q4_d4e5f6a7:13"
    assert payload["slot_decision"]["supporting_source_refs"] == [
        "lme:socialmem_Q4_d4e5f6a7:13"
    ]
    rows = payload["selected_evidence_table"]
    assert len(rows) <= 8
    assert all(row["session_key"] == "lme:socialmem_Q4_d4e5f6a7" for row in rows)
    assert all(row["source_ref"] != "lme:other_case:1" for row in rows)


def test_slot_decision_answer_planner_selects_exception_from_options(tmp_path: Path) -> None:
    fake = FakeMemoryEngine()
    spec = fake.tool_profile().recall
    assert spec is not None
    method_spec = MemoryMethodSpec(
        method_id="method_13",
        strategy="slot_decision_answer_planner",
        options={"selected_evidence_limit": 8, "answer_plan_row_limit": 32},
    )
    store = MemoryStore2(tmp_path / "memory2.db")
    store.upsert_raw_events_from_messages(DrinksDecisionSessionStore().messages)
    tool = SlotDecisionAnswerPlannerRecallTool(fake, spec, store, method_spec)  # type: ignore[arg-type]
    token = set_benchmark_question_context(
        {
            "question_id": "socialmem_Q2_e1f2a3b4",
            "question_type": "single-session-user",
            "question": (
                "What did the group decide about where to go for drinks after dinner? "
                "Did everyone seem equally on board?\n\n"
                "Options:\nA. Jordan\nB. Maya\nC. Priya\nD. Sam"
            ),
        }
    )

    async def _run() -> dict[str, object]:
        raw = await tool.execute(query="Where did they go for drinks, and who was not equally on board?")
        return json.loads(raw)

    try:
        payload = asyncio.run(_run())
    finally:
        reset_benchmark_question_context(token)

    assert payload["slot_decision_answer_planner"] is True
    assert payload["answer_candidate"] == "Sam"
    assert payload["selected_candidate"]["answer"] == "Sam"
    assert payload["candidate_resolution"]["selected_candidate"]["answer"] == "Sam"
    assert payload["candidate_resolution"]["decision_rule"] == "slot_decision_answer_plan"
    plan = payload["answer_plan"]
    assert plan["selected_answer"] == "Sam"
    assert plan["confidence"] in {"high", "medium"}
    sam = next(item for item in plan["per_option_evidence"] if item["option"] == "Sam")
    jordan = next(item for item in plan["per_option_evidence"] if item["option"] == "Jordan")
    assert sam["score"] > jordan["score"]
    assert "lme:socialmem_Q2_e1f2a3b4:44" in plan["supporting_source_refs"]


def test_consolidated_memory_write_quality_prioritizes_fact_table(tmp_path: Path) -> None:
    fake = FakeMemoryEngine()
    spec = fake.tool_profile().recall
    assert spec is not None
    method_spec = MemoryMethodSpec(
        method_id="method_14",
        strategy="consolidated_memory_write_quality",
        options={"selected_evidence_limit": 8, "consolidated_fact_limit": 4},
    )
    store = MemoryStore2(tmp_path / "memory2.db")
    store.upsert_raw_events_from_messages(DrinksDecisionSessionStore().messages)
    store.upsert_item(
        "preference",
        (
            "Sam exception or reluctance signal in lme:socialmem_Q2_e1f2a3b4 "
            "for drinks after dinner: minimal response. Evidence quote: \"...\""
        ),
        None,
        source_ref=json.dumps(["lme:socialmem_Q2_e1f2a3b4:44"]),
        extra={
            "method_14_consolidated_fact": True,
            "fact_type": "exception_fact",
            "source_session_key": "lme:socialmem_Q2_e1f2a3b4",
            "speaker": "Sam",
            "speaker_id": "p_s4d5e6f7",
            "date": "2025-01-20",
            "message_index": 6,
            "source_refs": ["lme:socialmem_Q2_e1f2a3b4:44"],
            "quote": "...",
        },
        happened_at="2025-01-20T00:00:00+00:00",
    )
    tool = ConsolidatedMemoryWriteQualityRecallTool(fake, spec, store, method_spec)  # type: ignore[arg-type]
    token = set_benchmark_question_context(
        {
            "question_id": "socialmem_Q2_e1f2a3b4",
            "question_type": "single-session-user",
            "question": (
                "What did the group decide about where to go for drinks after dinner? "
                "Did everyone seem equally on board?\n\n"
                "Options:\nA. Jordan\nB. Maya\nC. Priya\nD. Sam"
            ),
        }
    )

    async def _run() -> dict[str, object]:
        raw = await tool.execute(query="Who was not equally on board for drinks after dinner?")
        return json.loads(raw)

    try:
        payload = asyncio.run(_run())
    finally:
        reset_benchmark_question_context(token)

    assert payload["consolidated_memory_write_quality"] is True
    facts = payload["consolidated_fact_table"]
    assert facts[0]["fact_type"] == "exception_fact"
    assert facts[0]["speaker"] == "Sam"
    assert facts[0]["source_refs"] == ["lme:socialmem_Q2_e1f2a3b4:44"]
    assert payload["items"][0]["signals"]["method_14_consolidated_fact"]["speaker"] == "Sam"
    assert payload["answer_candidate"] == "Sam"
    assert payload["selected_candidate"]["answer"] == "Sam"
    assert payload["candidate_resolution"]["decision_rule"] == "consolidated_fact"


def test_consolidated_fact_precision_rerank_prefers_slot_evidence(tmp_path: Path) -> None:
    fake = FakeMemoryEngine()
    spec = fake.tool_profile().recall
    assert spec is not None
    method_spec = MemoryMethodSpec(
        method_id="method_15",
        strategy="consolidated_fact_precision_rerank",
        options={
            "selected_evidence_limit": 8,
            "consolidated_fact_limit": 4,
            "precision_raw_scan_limit": 16,
        },
    )
    store = MemoryStore2(tmp_path / "memory2.db")
    store.upsert_raw_events_from_messages(QuietVenueSessionStore().messages)
    store.upsert_item(
        "preference",
        (
            "Sam preference signal in lme:socialmem_Q4_d4e5f6a7: "
            "explicit preference wording. Evidence quote: \"just prefer the chicken...\""
        ),
        None,
        source_ref=json.dumps(["lme:socialmem_Q4_d4e5f6a7:27"]),
        extra={
            "method_14_consolidated_fact": True,
            "fact_type": "preference_fact",
            "source_session_key": "lme:socialmem_Q4_d4e5f6a7",
            "speaker": "Sam",
            "speaker_id": "p_s4d5e6f7",
            "date": "2026-04-02",
            "message_index": 8,
            "source_refs": ["lme:socialmem_Q4_d4e5f6a7:27"],
            "quote": "just prefer the chicken...",
        },
        happened_at="2026-04-02T00:00:00+00:00",
    )
    tool = ConsolidatedFactPrecisionRecallTool(fake, spec, store, method_spec)  # type: ignore[arg-type]
    token = set_benchmark_question_context(
        {
            "question_id": "socialmem_Q4_d4e5f6a7",
            "question_type": "single-session-user",
            "question": (
                "Someone in the group expressed a clear preference for quieter "
                "venues where people can actually have a real conversation. Who said it?\n\n"
                "Options:\nA. Jordan\nB. Maya\nC. Priya\nD. Sam"
            ),
        }
    )

    async def _run() -> dict[str, object]:
        raw = await tool.execute(query="quieter venues preference conversation group member")
        return json.loads(raw)

    try:
        payload = asyncio.run(_run())
    finally:
        reset_benchmark_question_context(token)

    assert payload["consolidated_fact_precision_rerank"] is True
    assert payload["question_context"]["option_names"] == ["Jordan", "Maya", "Priya", "Sam"]
    assert payload["answer_candidate"] == "Jordan"
    assert payload["candidate_resolution"]["decision_rule"] == "consolidated_fact_precision_rerank"
    assert payload["candidate_precision_decision"]["selected_candidate"]["answer"] == "Jordan"
    assert payload["candidate_precision_decision"]["supporting_source_refs"] == [
        "lme:socialmem_Q4_d4e5f6a7:13"
    ]


def test_consolidated_fact_precision_rerank_selects_earliest_order_candidate(tmp_path: Path) -> None:
    fake = FakeMemoryEngine()
    spec = fake.tool_profile().recall
    assert spec is not None
    method_spec = MemoryMethodSpec(
        method_id="method_15",
        strategy="consolidated_fact_precision_rerank",
        options={"selected_evidence_limit": 8, "precision_raw_scan_limit": 16},
    )
    store = MemoryStore2(tmp_path / "memory2.db")
    store.upsert_raw_events_from_messages(FakeSessionStore().messages)
    tool = ConsolidatedFactPrecisionRecallTool(fake, spec, store, method_spec)  # type: ignore[arg-type]
    token = set_benchmark_question_context(
        {
            "question_id": "raw_case",
            "question_type": "single-session-user",
            "question": (
                "Who first pushed back on committing to specific Christmas dates "
                "by citing outside obligations as the reason?\n\n"
                "Options:\nA. Emeka\nB. Adaora"
            ),
        }
    )

    async def _run() -> dict[str, object]:
        raw = await tool.execute(query="Who first pushed back on Christmas dates?")
        return json.loads(raw)

    try:
        payload = asyncio.run(_run())
    finally:
        reset_benchmark_question_context(token)

    assert payload["consolidated_fact_precision_rerank"] is True
    assert payload["answer_candidate"] == "Emeka"
    assert payload["candidate_resolution"]["decision_rule"] == "consolidated_fact_precision_rerank"
    selected = payload["candidate_precision_decision"]["selected_candidate"]
    assert selected["answer"] == "Emeka"
    assert selected["source_refs"] == ["lme:raw_case:0"]


def test_conservative_precision_gate_uses_global_order_for_who_first(tmp_path: Path) -> None:
    fake = FakeMemoryEngine()
    spec = fake.tool_profile().recall
    assert spec is not None
    method_spec = MemoryMethodSpec(
        method_id="method_16",
        strategy="conservative_precision_gate",
        options={"selected_evidence_limit": 8, "precision_raw_scan_limit": 16},
    )
    messages = [
        {
            "id": "lme:who_first_case:9",
            "session_key": "lme:who_first_case",
            "seq": 9,
            "role": "user",
            "content": (
                "[SocialMemBench network=grp session=grp_s01 "
                "session_index=1 date=2025-02-03 turn_id=t9 "
                "speaker_id=p_emeka message_index=10] "
                "Emeka: We'll see what Adaeze's schedule looks like - should know more in a few weeks."
            ),
            "timestamp": "2025-02-03T00:00:00+00:00",
        },
        {
            "id": "lme:who_first_case:10",
            "session_key": "lme:who_first_case",
            "seq": 10,
            "role": "user",
            "content": (
                "[SocialMemBench network=grp session=grp_s01 "
                "session_index=1 date=2025-02-03 turn_id=t10 "
                "speaker_id=p_adaora message_index=11] "
                "Adaora: I mean thesis doesn't really pause for Christmas so I genuinely don't know yet."
            ),
            "timestamp": "2025-02-03T00:00:00+00:00",
        },
        {
            "id": "lme:who_first_case:55",
            "session_key": "lme:who_first_case",
            "seq": 55,
            "role": "user",
            "content": (
                "[SocialMemBench network=grp session=grp_s02 "
                "session_index=2 date=2025-03-03 turn_id=t55 "
                "speaker_id=p_adaora message_index=3] "
                "Adaora: For Christmas I was thinking of bringing a lentil dish."
            ),
            "timestamp": "2025-03-03T00:00:00+00:00",
        },
    ]
    store = MemoryStore2(tmp_path / "memory2.db")
    store.upsert_raw_events_from_messages(messages)
    tool = ConservativePrecisionGateRecallTool(fake, spec, store, method_spec)  # type: ignore[arg-type]
    token = set_benchmark_question_context(
        {
            "question_id": "who_first_case",
            "question_type": "single-session-user",
            "question": (
                "Who first pushed back on committing to specific Christmas dates "
                "by citing outside obligations as the reason?\n\n"
                "Options:\nA. Adaora\nB. Emeka"
            ),
        }
    )

    async def _run() -> dict[str, object]:
        raw = await tool.execute(query="who first pushed back on Christmas dates outside obligations")
        return json.loads(raw)

    try:
        payload = asyncio.run(_run())
    finally:
        reset_benchmark_question_context(token)

    assert payload["conservative_precision_gate"] is True
    assert payload["answer_candidate"] == "Emeka"
    assert payload["candidate_resolution"]["decision_rule"] == "conservative_precision_gate"
    selected = payload["candidate_precision_decision"]["selected_candidate"]
    assert selected["answer"] == "Emeka"
    assert selected["source_refs"] == ["lme:who_first_case:9"]


def test_conservative_precision_gate_skips_broad_option_question(tmp_path: Path) -> None:
    fake = FakeMemoryEngine()
    spec = fake.tool_profile().recall
    assert spec is not None
    method_spec = MemoryMethodSpec(
        method_id="method_16",
        strategy="conservative_precision_gate",
        options={"selected_evidence_limit": 8, "precision_raw_scan_limit": 16},
    )
    store = MemoryStore2(tmp_path / "memory2.db")
    store.upsert_raw_events_from_messages(FakeSessionStore().messages)
    tool = ConservativePrecisionGateRecallTool(fake, spec, store, method_spec)  # type: ignore[arg-type]
    token = set_benchmark_question_context(
        {
            "question_id": "raw_case",
            "question_type": "single-session-user",
            "question": (
                "What was decided about the Christmas dinner menu, and did all "
                "family members seem equally happy with that plan?\n\n"
                "Options:\nA. Adaora\nB. Chidi\nC. Emeka\nD. Ngozi"
            ),
        }
    )

    async def _run() -> dict[str, object]:
        raw = await tool.execute(query="Christmas dinner menu family members equally happy")
        return json.loads(raw)

    try:
        payload = asyncio.run(_run())
    finally:
        reset_benchmark_question_context(token)

    assert "conservative_precision_gate" not in payload
    assert "candidate_precision_decision" not in payload


def test_structured_answer_contract_overrides_verified_answer(tmp_path: Path) -> None:
    fake = FakeMemoryEngine()
    spec = fake.tool_profile().recall
    assert spec is not None
    method_spec = MemoryMethodSpec(
        method_id="method_17",
        strategy="structured_answer_contract",
        options={
            "selected_evidence_limit": 8,
            "consolidated_fact_limit": 4,
            "precision_raw_scan_limit": 16,
        },
    )
    store = MemoryStore2(tmp_path / "memory2.db")
    store.upsert_raw_events_from_messages(QuietVenueSessionStore().messages)
    tool = StructuredAnswerContractRecallTool(fake, spec, store, method_spec)  # type: ignore[arg-type]
    token = set_benchmark_question_context(
        {
            "question_id": "socialmem_Q4_d4e5f6a7",
            "question_type": "single-session-user",
            "question": (
                "Someone in the group expressed a clear preference for quieter "
                "venues where people can actually have a real conversation. Who said it?\n\n"
                "Options:\nA. Jordan\nB. Maya\nC. Priya\nD. Sam"
            ),
        }
    )

    async def _run() -> str:
        return await tool.execute(query="quieter venues preference conversation group member")

    try:
        raw = asyncio.run(_run())
    finally:
        reset_benchmark_question_context(token)

    payload = json.loads(raw)
    contract = payload["final_answer_contract"]
    assert contract["verifier_status"] == "verified"
    assert contract["answer"] == "Jordan"
    assert contract["option_label"] == "A. Jordan"
    assert contract["supporting_source_refs"] == ["lme:socialmem_Q4_d4e5f6a7:13"]

    result: dict[str, object] = {
        "predicted_answer": "C. Priya",
        "tool_chain": [{"calls": [{"name": "recall_memory", "result": raw}]}],
    }
    apply_structured_answer_contract_to_result(
        result,
        {"strategy": "structured_answer_contract"},
    )

    assert result["predicted_answer_before_contract"] == "C. Priya"
    assert str(result["predicted_answer"]).startswith('A. Jordan - "Quieter. Better."')
    assert "§cited:[lme:socialmem_Q4_d4e5f6a7:13]§" in str(result["predicted_answer"])


def test_structured_answer_contract_skips_broad_question(tmp_path: Path) -> None:
    fake = FakeMemoryEngine()
    spec = fake.tool_profile().recall
    assert spec is not None
    method_spec = MemoryMethodSpec(
        method_id="method_17",
        strategy="structured_answer_contract",
        options={"selected_evidence_limit": 8, "precision_raw_scan_limit": 16},
    )
    store = MemoryStore2(tmp_path / "memory2.db")
    store.upsert_raw_events_from_messages(FakeSessionStore().messages)
    tool = StructuredAnswerContractRecallTool(fake, spec, store, method_spec)  # type: ignore[arg-type]
    token = set_benchmark_question_context(
        {
            "question_id": "raw_case",
            "question_type": "single-session-user",
            "question": (
                "What was decided about the Christmas dinner menu, and did all "
                "family members seem equally happy with that plan?\n\n"
                "Options:\nA. Adaora\nB. Chidi\nC. Emeka\nD. Ngozi"
            ),
        }
    )

    async def _run() -> dict[str, object]:
        raw = await tool.execute(query="Christmas dinner menu family members equally happy")
        return json.loads(raw)

    try:
        payload = asyncio.run(_run())
    finally:
        reset_benchmark_question_context(token)

    assert "final_answer_contract" not in payload
    assert "structured_answer_contract" not in payload


def test_summarize_payload_keeps_token_usage_null(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    payload = {
        "data": "dataset.json",
        "workspace": "workspace",
        "limit": 1,
        "offset": 0,
        "results": [
            {
                "question_id": "q1",
                "question_type": "single-session-user",
                "question": "Where?",
                "gold_answer": "Paris",
                "predicted_answer": "Paris",
                "judge_correct": True,
                "elapsed_s": 1.5,
                "error": None,
                "tool_chain": [],
            }
        ],
    }

    metrics = summarize_payload(payload, method_id="baseline_current", result_path=result_path)

    assert metrics["n"] == 1
    assert metrics["scores"]["overall"]["judge_acc"] == 1.0
    assert metrics["token_usage"]["avg_token_usage"] is None
    assert metrics["latency"]["avg_elapsed_s"] == 1.5
