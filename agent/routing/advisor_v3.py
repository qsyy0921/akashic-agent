from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, cast

from agent.routing.advisor import (
    RoutingSnapshotMismatchError,
    RoutingUnavailableError,
    build_discovery_snapshot,
    select_active_preloads,
)
from agent.routing.contracts import (
    DiscoverySnapshot,
    GoalRoute,
    GoalSpec,
    IntentGoal,
    RouteAdvice,
    RouteRequest,
    RouteUncertainty,
    ToolCandidate,
    ToolDiscoveryDocument,
)
from agent.routing.dense import DenseRoutingError
from agent.routing.protocol import ProtocolIntentGate
from agent.routing.gate_v3 import V3QualityGateEvidence
from agent.routing.hybrid import HybridMatch, HybridRouteRetriever, HybridRoutingError
from agent.routing.intent_view import (
    IntentViewAnalyzer,
    IntentViewError,
    IntentViewInvalidError,
    IntentViewUnavailableError,
)
from agent.routing.observe import (
    LoggerRoutingObserver,
    RoutingEventName,
    RoutingMode,
    RoutingObservation,
    RoutingObserver,
)
from agent.routing.uncertainty import (
    intent_view_uncertainties,
    operation_conflict_uncertainty,
)
from agent.tools.registry import ToolRegistry

INTENT_ROUTER_V3_VERSION = "intent-routing-v3-llm-hybrid-1"
V3_RRF_K = 60
V3_LOW_MARGIN = 0.0
V3_ORIGINAL_LEXICAL_WEIGHT = 1.0
V3_ORIGINAL_DENSE_WEIGHT = 1.0
V3_LLM_VIEW_WEIGHT = 2.0
V3_DERIVED_QUERY_MAX_CHARACTERS = 96
V3_DENSE_THREADS = 2


@dataclass(slots=True)
class _OperationEvidence:
    operation_id: str
    original_lexical_rank: int | None = None
    original_dense_rank: int | None = None
    llm_view_rank: int | None = None
    original_reason_codes: set[str] = field(default_factory=lambda: set[str]())

    @property
    def score(self) -> float:
        return sum(
            weight / (V3_RRF_K + rank)
            for rank, weight in (
                (self.original_lexical_rank, V3_ORIGINAL_LEXICAL_WEIGHT),
                (self.original_dense_rank, V3_ORIGINAL_DENSE_WEIGHT),
                (self.llm_view_rank, V3_LLM_VIEW_WEIGHT),
            )
            if rank is not None
        )

    @property
    def has_strong_original_evidence(self) -> bool:
        if {
            "exact_name",
            "exact_operation",
            "metadata_high_evidence",
        }.intersection(self.original_reason_codes):
            return True
        return (
            self.original_lexical_rank is not None
            and self.original_dense_rank is not None
        )


@dataclass(frozen=True, slots=True)
class _RankedOperation:
    evidence: _OperationEvidence
    provider: ToolDiscoveryDocument
    rank: int


class IntentV3TurnRouteAdvisor:
    """Context-aware V3 advisor with one LLM view and deterministic rank fusion."""

    backend = "llm_hybrid"

    def __init__(
        self,
        registry: ToolRegistry,
        retriever: HybridRouteRetriever,
        intent_view_analyzer: IntentViewAnalyzer,
        *,
        mode: Literal["shadow", "active"] = "shadow",
        intent_model_id: str = "",
        gate_evidence: V3QualityGateEvidence | None = None,
        protocol_gate: ProtocolIntentGate | None = None,
        observer: RoutingObserver | None = None,
    ) -> None:
        if mode == "active":
            if gate_evidence is None:
                raise RoutingUnavailableError(
                    "active V3 intent routing requires verified V3 gate evidence"
                )
            if gate_evidence.router_version != INTENT_ROUTER_V3_VERSION:
                raise RoutingUnavailableError("V3 gate router version mismatch")
            if gate_evidence.retrieval_model_id != retriever.model_id:
                raise RoutingUnavailableError("V3 gate retrieval model mismatch")
            if gate_evidence.intent_model_id != intent_model_id:
                raise RoutingUnavailableError("V3 gate intent model mismatch")
        self._registry = registry
        self._retriever = retriever
        self._intent_view_analyzer = intent_view_analyzer
        self._protocol_gate = protocol_gate or ProtocolIntentGate()
        self._observer = observer or LoggerRoutingObserver()
        self._gate_evidence = gate_evidence
        self._intent_model_id = intent_model_id
        self.mode: RoutingMode = mode

    @property
    def model_id(self) -> str:
        return self._retriever.model_id

    async def advise(self, request: RouteRequest) -> RouteAdvice:
        started = time.perf_counter()
        self._emit(
            request,
            event="routing_started",
            route_status="started",
            reason_code="route_started",
            candidate_count=0,
            preload_count=0,
            duration_ms=0,
        )
        try:
            discovery = build_discovery_snapshot(
                self._registry,
                request.capability_snapshot_id,
            )
            advice = await self._route(request, discovery, started)
        except (
            DenseRoutingError,
            HybridRoutingError,
            IntentViewError,
            RoutingUnavailableError,
        ) as exc:
            reason_code = _expected_failure_reason(exc)
            if isinstance(exc, RoutingSnapshotMismatchError):
                self._emit(
                    request,
                    event="routing_failed",
                    route_status="unavailable",
                    reason_code=reason_code,
                    candidate_count=0,
                    preload_count=0,
                    duration_ms=_elapsed_ms(started),
                )
                raise
            discovery = build_discovery_snapshot(
                self._registry,
                request.capability_snapshot_id,
            )
            advice = _unavailable_advice(
                request,
                discovery_snapshot_id=discovery.snapshot_id,
                mode=self.mode,
                reason_code=reason_code,
                latency_ms=_elapsed_ms(started),
            )
        except Exception as exc:
            self._emit(
                request,
                event="routing_failed",
                route_status="unavailable",
                reason_code=type(exc).__name__,
                candidate_count=0,
                preload_count=0,
                duration_ms=_elapsed_ms(started),
            )
            raise
        self._emit(
            request,
            event="routing_completed",
            route_status=advice.status,
            reason_code=advice.reason_codes[0],
            candidate_count=sum(len(route.candidates) for route in advice.goals),
            preload_count=len(advice.preloaded_tool_names),
            duration_ms=advice.latency_ms,
        )
        return advice

    async def _route(
        self,
        request: RouteRequest,
        discovery: DiscoverySnapshot,
        started: float,
    ) -> RouteAdvice:
        if self._protocol_gate.is_control(request.message):
            return self._advice(
                request,
                discovery,
                started,
                status="no_tool",
                decision_band="not_applicable",
                goals=(),
                reason_codes=("protocol_control", f"{self.mode}_mode"),
            )
        if request.context is None:
            raise IntentViewInvalidError("V3 natural-language routing requires context")

        analysis = await self._intent_view_analyzer.analyze(
            request.message,
            request.context,
            forbidden_tool_names=frozenset(self._registry.get_registered_names()),
        )
        queries_by_goal = (
            tuple(() for _ in analysis.view.goals)
            if analysis.view.tool_requirement == "none"
            else tuple(
                _llm_retrieval_queries(goal, request.message)
                for goal in analysis.view.goals
            )
        )
        query_text_by_key = {request.message.casefold(): request.message}
        for queries in queries_by_goal:
            for query, _ in queries:
                query_text_by_key.setdefault(query.casefold(), query)
        query_keys = tuple(query_text_by_key)
        queries = tuple(query_text_by_key[key] for key in query_keys)
        async_retrieve_many = getattr(self._retriever, "aretrieve_many", None)
        if callable(async_retrieve_many):
            retrieve_many = cast(
                Callable[
                    ...,
                    Awaitable[tuple[tuple[HybridMatch, ...], ...]],
                ],
                async_retrieve_many,
            )
            batch_matches = await retrieve_many(
                queries,
                discovery,
                top_k=8,
            )
        else:
            batch_matches = self._retriever.retrieve_many(
                queries,
                discovery,
                top_k=8,
            )
        matches_by_key = dict(zip(query_keys, batch_matches, strict=True))
        original_matches = matches_by_key[request.message.casefold()]
        original_evidence = _original_evidence(original_matches)
        base_uncertainties = _context_adjusted_uncertainties(
            intent_view_uncertainties(analysis.view),
            request,
        )

        if analysis.view.tool_requirement == "none":
            return self._route_tool_free_view(
                request,
                discovery,
                analysis.view.goals[0],
                original_evidence,
                base_uncertainties,
                started,
            )

        routes: list[GoalRoute] = []
        uncertainties = list(base_uncertainties)
        any_evidence = False
        any_ineligible = False
        has_unresolved_goal = False
        has_ambiguous_goal = False
        for intent_goal, retrieval_queries in zip(
            analysis.view.goals,
            queries_by_goal,
            strict=True,
        ):
            evidence = _copy_evidence(original_evidence)
            alternative_top_operations: set[str] = set()
            for query, is_alternative in retrieval_queries:
                matches = matches_by_key[query.casefold()]
                _merge_llm_view(evidence, matches)
                if is_alternative and matches:
                    alternative_top_operations.add(
                        matches[0].document.operation_id
                    )
            any_evidence = any_evidence or bool(evidence)
            ranked = _rank_operations(
                evidence,
                discovery,
                request.disabled_tools,
                require_llm_view=True,
                desired_outputs=_goal_desired_outputs(intent_goal),
            )
            if evidence and not ranked:
                any_ineligible = True
            candidates = _tool_candidates(ranked)
            has_unresolved_goal = has_unresolved_goal or not candidates
            alternative_conflict = len(alternative_top_operations) > 1
            low_margin = _is_low_margin(ranked) or alternative_conflict
            has_ambiguous_goal = has_ambiguous_goal or low_margin
            if low_margin:
                uncertainties.append(
                    operation_conflict_uncertainty(
                        intent_goal.goal_id,
                        tuple(
                            sorted(alternative_top_operations)[:3]
                            if alternative_conflict
                            else [
                                item.evidence.operation_id for item in ranked[:3]
                            ]
                        ),
                        reason_code=(
                            "v3_alternative_operations"
                            if alternative_conflict
                            else "v3_score_tie"
                        ),
                    )
                )
            routes.append(
                GoalRoute(goal=_goal_spec(intent_goal), candidates=candidates)
            )

        if has_unresolved_goal:
            if analysis.view.tool_requirement == "optional" and not any_evidence:
                if uncertainties:
                    return self._advice(
                        request,
                        discovery,
                        started,
                        status="ambiguous",
                        decision_band="low_margin",
                        goals=tuple(routes),
                        uncertainties=tuple(uncertainties),
                        reason_codes=(
                            "v3_clarification_required",
                            "intent_view_v3",
                            f"{self.mode}_mode",
                        ),
                    )
                return self._advice(
                    request,
                    discovery,
                    started,
                    status="no_tool",
                    decision_band="no_match",
                    goals=(),
                    reason_codes=("optional_tool_no_match", f"{self.mode}_mode"),
                )
            return self._advice(
                request,
                discovery,
                started,
                status="unavailable",
                decision_band="no_match",
                goals=tuple(routes),
                reason_codes=(
                    "all_matches_ineligible"
                    if any_ineligible
                    else "required_goal_has_no_candidate",
                    "intent_view_v3",
                    f"{self.mode}_mode",
                ),
                uncertainties=tuple(uncertainties),
            )

        needs_clarification = bool(uncertainties)
        operation_is_ambiguous = has_ambiguous_goal or any(
            item.kind == "operation_conflict" for item in uncertainties
        )
        status: Literal["resolved", "ambiguous"] = (
            "ambiguous"
            if operation_is_ambiguous
            else "resolved"
        )
        preloads = (
            select_active_preloads(routes)
            if status == "resolved" and self.mode == "active"
            else ()
        )
        return self._advice(
            request,
            discovery,
            started,
            status=status,
            decision_band=(
                "low_margin"
                if operation_is_ambiguous
                else "high_margin"
            ),
            goals=tuple(routes),
            preloads=preloads,
            reason_codes=(
                "v3_clarification_required"
                if operation_is_ambiguous
                else "v3_missing_input"
                if needs_clarification
                else "v3_fused_match",
                "intent_view_v3",
                f"{self.mode}_mode",
            ),
            uncertainties=tuple(uncertainties),
        )

    def _route_tool_free_view(
        self,
        request: RouteRequest,
        discovery: DiscoverySnapshot,
        intent_goal: IntentGoal,
        original_evidence: dict[str, _OperationEvidence],
        base_uncertainties: tuple[RouteUncertainty, ...],
        started: float,
    ) -> RouteAdvice:
        strong = {
            operation_id: evidence
            for operation_id, evidence in original_evidence.items()
            if evidence.has_strong_original_evidence
        }
        if not strong:
            if base_uncertainties:
                return self._advice(
                    request,
                    discovery,
                    started,
                    status="ambiguous",
                    decision_band="low_margin",
                    goals=(
                        GoalRoute(goal=_goal_spec(intent_goal), candidates=()),
                    ),
                    uncertainties=base_uncertainties,
                    reason_codes=(
                        "v3_clarification_required",
                        "intent_view_no_tool",
                        f"{self.mode}_mode",
                    ),
                )
            return self._advice(
                request,
                discovery,
                started,
                status="no_tool",
                decision_band="no_match",
                goals=(),
                reason_codes=("intent_view_no_tool", f"{self.mode}_mode"),
            )
        ranked = _rank_operations(strong, discovery, request.disabled_tools)
        conflict_alternatives = (
            "no_tool",
            *(
                item.evidence.operation_id
                for item in ranked[:2]
            ),
        ) if ranked else (
            "no_tool",
            *tuple(sorted(strong))[:2],
        )
        conflict = operation_conflict_uncertainty(
            intent_goal.goal_id,
            conflict_alternatives,
            reason_code="intent_view_raw_conflict",
        )
        uncertainties = (*base_uncertainties, conflict)
        if not ranked:
            return self._advice(
                request,
                discovery,
                started,
                status="unavailable",
                decision_band="no_match",
                goals=(GoalRoute(goal=_goal_spec(intent_goal), candidates=()),),
                reason_codes=(
                    "all_matches_ineligible",
                    "intent_view_raw_conflict",
                    f"{self.mode}_mode",
                ),
                uncertainties=uncertainties,
            )
        return self._advice(
            request,
            discovery,
            started,
            status="ambiguous",
            decision_band="low_margin",
            goals=(
                GoalRoute(
                    goal=_goal_spec(intent_goal),
                    candidates=_tool_candidates(ranked),
                ),
            ),
            reason_codes=(
                "intent_view_raw_conflict",
                "strong_original_evidence",
                f"{self.mode}_mode",
            ),
            uncertainties=uncertainties,
        )

    def _advice(
        self,
        request: RouteRequest,
        discovery: DiscoverySnapshot,
        started: float,
        *,
        status: Literal["resolved", "no_tool", "ambiguous", "unavailable"],
        decision_band: Literal[
            "high_margin", "low_margin", "no_match", "not_applicable"
        ],
        goals: tuple[GoalRoute, ...],
        reason_codes: tuple[str, ...],
        preloads: tuple[str, ...] = (),
        uncertainties: tuple[RouteUncertainty, ...] = (),
    ) -> RouteAdvice:
        return RouteAdvice(
            schema_version="3",
            router_version=INTENT_ROUTER_V3_VERSION,
            turn_id=request.turn_id,
            capability_snapshot_id=request.capability_snapshot_id,
            discovery_snapshot_id=discovery.snapshot_id,
            status=status,
            decision_band=decision_band,
            goals=goals,
            preloaded_tool_names=preloads,
            reason_codes=reason_codes,
            latency_ms=_elapsed_ms(started),
            uncertainties=uncertainties,
        )

    def _emit(
        self,
        request: RouteRequest,
        *,
        event: RoutingEventName,
        route_status: str,
        reason_code: str,
        candidate_count: int,
        preload_count: int,
        duration_ms: int,
    ) -> None:
        self._observer.emit(
            RoutingObservation(
                schema_version="1",
                event=event,
                turn_id=request.turn_id,
                mode=self.mode,
                backend="llm_hybrid",
                router_version=INTENT_ROUTER_V3_VERSION,
                route_status=route_status,
                reason_code=reason_code,
                candidate_count=candidate_count,
                preload_count=preload_count,
                duration_ms=duration_ms,
            )
        )


class UnavailableV3ShadowRouteAdvisor:
    mode: RoutingMode = "shadow"
    backend = "llm_hybrid"

    def __init__(
        self,
        *,
        reason_code: str,
        observer: RoutingObserver | None = None,
    ) -> None:
        if not reason_code or reason_code != reason_code.strip():
            raise RoutingUnavailableError("V3 unavailable reason code is invalid")
        self._reason_code = reason_code
        self._observer = observer or LoggerRoutingObserver()

    async def advise(self, request: RouteRequest) -> RouteAdvice:
        self._observer.emit(
            RoutingObservation(
                schema_version="1",
                event="routing_started",
                turn_id=request.turn_id,
                mode="shadow",
                backend="llm_hybrid",
                router_version=INTENT_ROUTER_V3_VERSION,
                route_status="started",
                reason_code="route_started",
                candidate_count=0,
                preload_count=0,
                duration_ms=0,
            )
        )
        advice = _unavailable_advice(
            request,
            mode="shadow",
            reason_code=self._reason_code,
            latency_ms=0,
        )
        self._observer.emit(
            RoutingObservation(
                schema_version="1",
                event="routing_completed",
                turn_id=request.turn_id,
                mode="shadow",
                backend="llm_hybrid",
                router_version=INTENT_ROUTER_V3_VERSION,
                route_status="unavailable",
                reason_code=self._reason_code,
                candidate_count=0,
                preload_count=0,
                duration_ms=0,
            )
        )
        return advice


def _original_evidence(
    matches: tuple[HybridMatch, ...],
) -> dict[str, _OperationEvidence]:
    evidence: dict[str, _OperationEvidence] = {}
    for match in matches:
        item = evidence.setdefault(
            match.document.operation_id,
            _OperationEvidence(operation_id=match.document.operation_id),
        )
        item.original_lexical_rank = _minimum_rank(
            item.original_lexical_rank,
            match.lexical_rank,
        )
        item.original_dense_rank = _minimum_rank(
            item.original_dense_rank,
            match.dense_rank,
        )
        item.original_reason_codes.update(match.reason_codes)
    return evidence


def _copy_evidence(
    original: dict[str, _OperationEvidence],
) -> dict[str, _OperationEvidence]:
    return {
        operation_id: _OperationEvidence(
            operation_id=operation_id,
            original_lexical_rank=item.original_lexical_rank,
            original_dense_rank=item.original_dense_rank,
            original_reason_codes=set(item.original_reason_codes),
        )
        for operation_id, item in original.items()
    }


def _merge_llm_view(
    evidence: dict[str, _OperationEvidence],
    matches: tuple[HybridMatch, ...],
) -> None:
    for match in matches:
        if (
            match.dense_rank is None
            and "metadata_high_evidence" in match.reason_codes
            and not {"exact_name", "exact_operation"}.intersection(
                match.reason_codes
            )
        ):
            continue
        item = evidence.setdefault(
            match.document.operation_id,
            _OperationEvidence(operation_id=match.document.operation_id),
        )
        item.llm_view_rank = _minimum_rank(
            item.llm_view_rank,
            match.fused_rank,
        )


def _llm_retrieval_queries(
    goal: IntentGoal,
    original_message: str,
) -> tuple[tuple[str, bool], ...]:
    queries = (
        (_bounded_derived_query(goal.rewritten_intent), False),
        *(
            (_bounded_derived_query(capability.retrieval_query), False)
            for capability in goal.hypothetical_capabilities
        ),
        *(
            (_bounded_derived_query(query), True)
            for query in goal.alternative_capability_queries
        ),
    )
    original_key = original_message.casefold()
    selected: list[tuple[str, bool]] = []
    seen: set[str] = {original_key}
    for query, is_alternative in queries:
        key = query.casefold()
        if key not in seen:
            selected.append((query, is_alternative))
            seen.add(key)
    return tuple(selected)


def _bounded_derived_query(query: str) -> str:
    return query[:V3_DERIVED_QUERY_MAX_CHARACTERS].rstrip()


def _context_adjusted_uncertainties(
    uncertainties: tuple[RouteUncertainty, ...],
    request: RouteRequest,
) -> tuple[RouteUncertainty, ...]:
    context = request.context
    if context is None or not (
        context.reply_excerpt
        or context.previous_operation_ids
        or any(
            message.operation_ids or message.output_kinds
            for message in context.messages
        )
    ):
        return uncertainties
    return tuple(
        item for item in uncertainties if item.kind != "unresolved_reference"
    )


def _rank_operations(
    evidence: dict[str, _OperationEvidence],
    discovery: DiscoverySnapshot,
    disabled_tools: frozenset[str],
    *,
    require_llm_view: bool = False,
    desired_outputs: frozenset[str] = frozenset(),
) -> tuple[_RankedOperation, ...]:
    providers_by_operation: dict[str, list[ToolDiscoveryDocument]] = {}
    for document in discovery.documents:
        if document.healthy and document.tool_name not in disabled_tools:
            providers_by_operation.setdefault(document.operation_id, []).append(
                document
            )

    sortable: list[
        tuple[
            int,
            int,
            float,
            str,
            _OperationEvidence,
            ToolDiscoveryDocument,
        ]
    ] = []
    for operation_id, item in evidence.items():
        if require_llm_view:
            exact_original = bool(
                {"exact_name", "exact_operation"}.intersection(
                    item.original_reason_codes
                )
            )
            if item.llm_view_rank is None and not exact_original:
                continue
            if (
                item.llm_view_rank is not None
                and item.llm_view_rank > 1
                and not item.has_strong_original_evidence
                and not exact_original
            ):
                continue
        providers = providers_by_operation.get(operation_id, [])
        if desired_outputs and "mixed" not in desired_outputs:
            providers = [
                provider
                for provider in providers
                if "mixed" in provider.output_kinds
                or bool(desired_outputs.intersection(provider.output_kinds))
            ]
        if not providers:
            continue
        provider = min(providers, key=lambda document: document.tool_name)
        exact_priority = 0 if {
            "exact_name",
            "exact_operation",
        }.intersection(item.original_reason_codes) else 1
        llm_view_priority = (
            item.llm_view_rank
            if require_llm_view and item.llm_view_rank is not None
            else 0
        )
        sortable.append(
            (
                exact_priority,
                llm_view_priority,
                -item.score,
                operation_id,
                item,
                provider,
            )
        )

    sortable.sort(key=lambda item: item[:4])
    return tuple(
        _RankedOperation(evidence=item, provider=provider, rank=rank)
        for rank, (_, _, _, _, item, provider) in enumerate(sortable, start=1)
    )


def _goal_desired_outputs(goal: IntentGoal) -> frozenset[str]:
    return frozenset(
        output
        for capability in goal.hypothetical_capabilities
        for output in capability.desired_outputs
    )


def _tool_candidates(
    ranked: tuple[_RankedOperation, ...],
) -> tuple[ToolCandidate, ...]:
    return tuple(
        ToolCandidate(
            tool_name=item.provider.tool_name,
            operation_id=item.evidence.operation_id,
            lexical_rank=item.evidence.original_lexical_rank,
            dense_rank=item.evidence.original_dense_rank,
            llm_view_rank=item.evidence.llm_view_rank,
            fused_rank=candidate_rank,
            reason_codes=_candidate_reason_codes(item.evidence),
        )
        for candidate_rank, item in enumerate(ranked[:3], start=1)
    )


def _candidate_reason_codes(evidence: _OperationEvidence) -> tuple[str, ...]:
    codes = ["v3_rrf", "deterministic_provider"]
    if evidence.original_lexical_rank is not None:
        codes.append("original_lexical")
    if evidence.original_dense_rank is not None:
        codes.append("original_dense")
    if evidence.llm_view_rank is not None:
        codes.append("llm_hypothetical")
    for code in ("exact_name", "exact_operation", "metadata_high_evidence"):
        if code in evidence.original_reason_codes:
            codes.append(code)
    return tuple(codes)


def _is_low_margin(ranked: tuple[_RankedOperation, ...]) -> bool:
    if len(ranked) < 2:
        return False
    first, second = ranked[:2]
    if {
        "exact_name",
        "exact_operation",
    }.intersection(first.evidence.original_reason_codes) and not {
        "exact_name",
        "exact_operation",
    }.intersection(second.evidence.original_reason_codes):
        return False
    if first.evidence.llm_view_rank != second.evidence.llm_view_rank:
        return False
    return first.evidence.score - second.evidence.score <= V3_LOW_MARGIN


def _goal_spec(goal: IntentGoal) -> GoalSpec:
    return GoalSpec(
        goal_id=goal.goal_id,
        statement=goal.statement,
        operation_query=goal.rewritten_intent,
        relation=goal.relation,
        depends_on=goal.depends_on,
    )


def _minimum_rank(current: int | None, candidate: int | None) -> int | None:
    if candidate is None:
        return current
    return candidate if current is None else min(current, candidate)


def _unavailable_advice(
    request: RouteRequest,
    *,
    mode: RoutingMode,
    reason_code: str,
    latency_ms: int,
    discovery_snapshot_id: str | None = None,
) -> RouteAdvice:
    resolved_discovery_snapshot_id = (
        discovery_snapshot_id
        if discovery_snapshot_id is not None
        else DiscoverySnapshot.build(request.capability_snapshot_id, ()).snapshot_id
    )
    return RouteAdvice(
        schema_version="3",
        router_version=INTENT_ROUTER_V3_VERSION,
        turn_id=request.turn_id,
        capability_snapshot_id=request.capability_snapshot_id,
        discovery_snapshot_id=resolved_discovery_snapshot_id,
        status="unavailable",
        decision_band="not_applicable",
        goals=(),
        preloaded_tool_names=(),
        reason_codes=(reason_code, f"{mode}_mode"),
        latency_ms=latency_ms,
    )


def _expected_failure_reason(exc: Exception) -> str:
    if isinstance(exc, RoutingSnapshotMismatchError):
        return "snapshot_mismatch"
    if isinstance(exc, IntentViewInvalidError):
        return "intent_view_invalid"
    if isinstance(exc, IntentViewUnavailableError):
        return "intent_view_unavailable"
    if isinstance(exc, DenseRoutingError):
        return "dense_unavailable"
    if isinstance(exc, HybridRoutingError):
        return "hybrid_invalid"
    return "discovery_unavailable"


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


__all__ = [
    "INTENT_ROUTER_V3_VERSION",
    "IntentV3TurnRouteAdvisor",
    "UnavailableV3ShadowRouteAdvisor",
    "V3_LOW_MARGIN",
    "V3_ORIGINAL_LEXICAL_WEIGHT",
    "V3_ORIGINAL_DENSE_WEIGHT",
    "V3_LLM_VIEW_WEIGHT",
    "V3_DERIVED_QUERY_MAX_CHARACTERS",
    "V3_DENSE_THREADS",
    "V3_RRF_K",
]
