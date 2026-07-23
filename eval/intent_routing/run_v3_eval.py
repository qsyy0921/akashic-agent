from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from agent.config_models import Config
from agent.routing.advisor import select_active_preloads
from agent.routing.advisor_v3 import (
    INTENT_ROUTER_V3_VERSION,
    V3_DERIVED_QUERY_MAX_CHARACTERS,
    V3_LOW_MARGIN,
    V3_LLM_VIEW_WEIGHT,
    V3_ORIGINAL_DENSE_WEIGHT,
    V3_ORIGINAL_LEXICAL_WEIGHT,
    V3_RRF_K,
    IntentV3TurnRouteAdvisor,
)
from agent.routing.contracts import (
    RouteAdvice,
    RouteContext,
    RouteContextMessage,
    RouteRequest,
)
from agent.routing.dense import (
    DenseRouteRetriever,
    build_dense_encoder,
)
from agent.routing.gate_v3 import (
    build_v3_quality_gate_report,
    build_v3_runtime_configuration,
    v3_quality_gate_thresholds,
    verify_v3_quality_gate,
)
from agent.routing.hybrid import DEFAULT_RRF_K, HybridRouteRetriever
from agent.routing.intent_view import (
    INTENT_VIEW_PROMPT_VERSION,
    IntentViewAnalysis,
    IntentViewAnalyzer,
    IntentViewError,
    IntentViewInvalidError,
    IntentViewProvider,
    IntentViewUnavailableError,
    LLMIntentViewProvider,
    intent_view_prompt_digest,
)
from agent.routing.lexical import MetadataLexicalRouteRetriever
from bootstrap.providers import build_providers
from eval.intent_routing.catalog import EvaluationInputs, load_evaluation_inputs

APPLICATION_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = Path("eval/intent_routing/v3_dataset.zh.jsonl")
CATALOG_PATH = Path("eval/intent_routing/catalog.zh.json")
DEFAULT_REPORT_PATH = Path("eval/intent_routing/v3-gate-report.json")
DEFAULT_WORKSPACE = Path.home() / ".akashic" / "workspace"


class V3EvaluationError(RuntimeError):
    """Raised when the real V3 evaluation cannot produce auditable metrics."""


@dataclass(frozen=True, slots=True)
class CaseResult:
    case: dict[str, Any]
    advice: RouteAdvice
    analysis: IntentViewAnalysis | None
    intent_failure_reason: str | None
    intent_failure_detail: str | None
    provider_calls: int
    external_llm_latency_ms: float
    local_latency_ms: float


class _TimedIntentViewProvider:
    def __init__(self, delegate: IntentViewProvider) -> None:
        self._delegate = delegate
        self.calls = 0
        self.duration_ms = 0.0

    async def emit_intent_view(
        self,
        message: str,
        context: RouteContext,
    ) -> object:
        self.calls += 1
        started = time.perf_counter()
        try:
            return await self._delegate.emit_intent_view(message, context)
        finally:
            self.duration_ms += (time.perf_counter() - started) * 1000.0


@dataclass(frozen=True, slots=True)
class _AnalysisAttempt:
    case: dict[str, Any]
    analysis: IntentViewAnalysis | None
    error: IntentViewError | None
    failure_reason: str | None
    failure_detail: str | None
    provider_calls: int
    external_llm_latency_ms: float


class _ReplayIntentViewAnalyzer:
    def __init__(self, attempt: _AnalysisAttempt) -> None:
        self._attempt = attempt

    async def analyze(
        self,
        message: str,
        context: RouteContext,
        *,
        forbidden_tool_names: frozenset[str] = frozenset(),
    ) -> IntentViewAnalysis:
        if self._attempt.error is not None:
            raise self._attempt.error
        if self._attempt.analysis is None:
            raise IntentViewUnavailableError("evaluation analysis is unavailable")
        return self._attempt.analysis


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate Akashic Intent Routing V3 with one real LLM view."
    )
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        raise V3EvaluationError("workers must be between one and eight")
    return asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> int:
    config = Config.load(args.config, workspace=args.workspace)
    routing = config.intent_routing
    if routing.mode == "off":
        raise V3EvaluationError("V3 evaluation requires intent routing to be enabled")
    inputs = load_evaluation_inputs(args.dataset, args.catalog)

    main_provider, _, agent_provider = build_providers(config)
    analysis_provider = agent_provider or main_provider
    analysis_model = config.agent_model or config.model
    semaphore = asyncio.Semaphore(args.workers)

    async def analyze_case(case: dict[str, Any]) -> _AnalysisAttempt:
        async with semaphore:
            timed_provider = _TimedIntentViewProvider(
                LLMIntentViewProvider(
                    analysis_provider,
                    model=analysis_model,
                    max_tokens=routing.intent_max_tokens,
                    timeout_seconds=routing.intent_timeout_seconds,
                )
            )
            analyzer = IntentViewAnalyzer(timed_provider)
            analysis: IntentViewAnalysis | None = None
            error: IntentViewError | None = None
            try:
                analysis = await analyzer.analyze(
                    _require_string(case, "utterance"),
                    _route_context(case),
                    forbidden_tool_names=frozenset(
                        inputs.registry.get_registered_names()
                    ),
                )
            except IntentViewError as exc:
                error = exc
            return _AnalysisAttempt(
                case=case,
                analysis=analysis,
                error=error,
                failure_reason=(
                    _intent_failure_reason(error) if error is not None else None
                ),
                failure_detail=(
                    _intent_failure_detail(error) if error is not None else None
                ),
                provider_calls=timed_provider.calls,
                external_llm_latency_ms=timed_provider.duration_ms,
            )

    attempts = await asyncio.gather(
        *(analyze_case(case) for case in inputs.cases)
    )
    cold_started = time.perf_counter()
    encoder = build_dense_encoder(
        backend="openai_compatible",
        model_id=routing.embedding_model,
        dimension=routing.embedding_dimension,
        cache_dir=APPLICATION_ROOT / "runtime" / "models" / "intent-routing",
        threads=None,
        base_url=routing.embedding_base_url,
        api_key=routing.embedding_api_key,
        batch_size=routing.embedding_batch_size,
        timeout_seconds=routing.embedding_timeout_seconds,
    )
    encoder.ensure_ready()
    dense = DenseRouteRetriever(
        encoder,
        minimum_similarity=routing.dense_min_similarity,
    )
    retriever = HybridRouteRetriever(
        MetadataLexicalRouteRetriever(),
        dense,
        rrf_k=DEFAULT_RRF_K,
    )
    await retriever.aprepare(inputs.snapshot)
    cold_start_ms = (time.perf_counter() - cold_started) * 1000.0

    results: list[CaseResult] = []
    for attempt in attempts:
        advisor = IntentV3TurnRouteAdvisor(
            inputs.registry,
            retriever,
            cast(
                IntentViewAnalyzer,
                _ReplayIntentViewAnalyzer(attempt),
            ),
            mode="shadow",
            intent_model_id=analysis_model,
        )
        started = time.perf_counter()
        advice = await advisor.advise(
                RouteRequest(
                    turn_id=_require_string(attempt.case, "id"),
                    capability_snapshot_id=inputs.snapshot.capability_snapshot_id,
                    message=_require_string(attempt.case, "utterance"),
                    disabled_tools=frozenset(
                        _require_string_list(attempt.case, "disabled_tools")
                    ),
                    context=_route_context(attempt.case),
                )
            )
        results.append(
            CaseResult(
                case=attempt.case,
                advice=advice,
                analysis=attempt.analysis,
                intent_failure_reason=attempt.failure_reason,
                intent_failure_detail=attempt.failure_detail,
                provider_calls=attempt.provider_calls,
                external_llm_latency_ms=attempt.external_llm_latency_ms,
                local_latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        )
    runtime_configuration = build_v3_runtime_configuration(
        dense_min_similarity=routing.dense_min_similarity,
        inner_rrf_k=DEFAULT_RRF_K,
        outer_rrf_k=V3_RRF_K,
        original_lexical_weight=V3_ORIGINAL_LEXICAL_WEIGHT,
        original_dense_weight=V3_ORIGINAL_DENSE_WEIGHT,
        llm_view_weight=V3_LLM_VIEW_WEIGHT,
        derived_query_max_characters=V3_DERIVED_QUERY_MAX_CHARACTERS,
        dense_threads=0,
        low_margin=V3_LOW_MARGIN,
        intent_max_tokens=routing.intent_max_tokens,
        intent_timeout_seconds=routing.intent_timeout_seconds,
        embedding_backend="openai_compatible",
        embedding_dimension=routing.embedding_dimension,
        embedding_batch_size=routing.embedding_batch_size,
        embedding_timeout_seconds=routing.embedding_timeout_seconds,
    )
    warm_latency_limit_ms = v3_quality_gate_thresholds(
        runtime_configuration,
        retrieval_model_id=routing.embedding_model,
    )["maximum"]["local_warm_latency_p95_ms"]
    metrics, misses = _evaluate_results(
        results,
        inputs,
        cold_start_ms=cold_start_ms,
        local_latency_outlier_ms=warm_latency_limit_ms,
    )
    report = build_v3_quality_gate_report(
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        application_root=APPLICATION_ROOT,
        router_version=INTENT_ROUTER_V3_VERSION,
        retrieval_model_id=routing.embedding_model,
        intent_model_id=analysis_model,
        prompt_version=INTENT_VIEW_PROMPT_VERSION,
        prompt_digest=intent_view_prompt_digest(),
        dataset_path=args.dataset,
        catalog_path=args.catalog,
        case_count=len(inputs.cases),
        goal_count=inputs.goal_count,
        tool_count=inputs.tool_count,
        configuration=runtime_configuration,
        metrics=metrics,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    if report["passed"] is True:
        _ = verify_v3_quality_gate(
            args.output,
            application_root=APPLICATION_ROOT,
            dataset_path=args.dataset,
            catalog_path=args.catalog,
            expected_router_version=INTENT_ROUTER_V3_VERSION,
            expected_retrieval_model_id=routing.embedding_model,
            expected_intent_model_id=analysis_model,
            expected_prompt_version=INTENT_VIEW_PROMPT_VERSION,
            expected_prompt_digest=intent_view_prompt_digest(),
            expected_configuration=runtime_configuration,
        )
    print(
        json.dumps(
            {
                "report": str(args.output),
                "passed": report["passed"],
                "metrics": metrics,
                "failures": report["failures"],
                "misses": misses,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["passed"] is True else 1


def _evaluate_results(
    results: list[CaseResult],
    inputs: EvaluationInputs,
    *,
    cold_start_ms: float,
    local_latency_outlier_ms: float,
) -> tuple[dict[str, float], list[dict[str, object]]]:
    operation_hits_at_1 = 0
    operation_hits_at_3 = 0
    operation_goal_count = 0
    complete_recall_hits = 0
    complete_exact_hits = 0
    routable_case_count = 0
    true_no_tool: set[str] = set()
    predicted_no_tool: set[str] = set()
    context_hits = 0
    context_count = 0
    equivalent_hits = 0
    equivalent_count = 0
    clarification_true_positives = 0
    clarification_required_count = 0
    unnecessary_clarifications = 0
    clarification_negative_count = 0
    visibility_hits = 0
    visibility_count = 0
    intent_successes = 0
    status_hits = 0
    local_latencies: list[float] = []
    external_latencies: list[float] = []
    misses: list[dict[str, object]] = []

    context_categories = {"follow_up", "intent_shift", "fake_shift"}
    for result in results:
        case = result.case
        advice = result.advice
        case_id = _require_string(case, "id")
        category = _require_string(case, "category")
        expected_status = _require_string(case, "expected_status")
        goals = _require_list(case, "goals")
        status_hits += int(advice.status == expected_status)
        local_latencies.append(result.local_latency_ms)
        external_latencies.append(result.external_llm_latency_ms)
        if result.local_latency_ms > local_latency_outlier_ms:
            query_count, maximum_query_characters = _retrieval_query_shape(result)
            misses.append(
                {
                    "case_id": case_id,
                    "kind": "local_latency_outlier",
                    "latency_ms": round(result.local_latency_ms, 3),
                    "query_count": query_count,
                    "maximum_query_characters": maximum_query_characters,
                }
            )
        intent_ok = result.analysis is not None and result.provider_calls == 1
        intent_successes += int(intent_ok)
        if not intent_ok:
            misses.append(
                {
                    "case_id": case_id,
                    "kind": "intent_view_failure",
                    "reason": result.intent_failure_reason or "unknown",
                    "detail": result.intent_failure_detail or "unknown",
                    "provider_calls": result.provider_calls,
                }
            )

        routes = {route.goal.goal_id: route for route in advice.goals}
        predicted_top_1: set[str] = set()
        predicted_top_3: set[str] = set()
        expected_case_operations: set[str] = set()
        if expected_status not in {"no_tool", "unavailable"}:
            routable_case_count += 1
            for raw_goal in goals:
                goal = _require_object(raw_goal, "goal")
                goal_id = _require_string(goal, "goal_id")
                expected = set(_require_string_list(goal, "expected_operation_ids"))
                expected_case_operations.update(expected)
                operation_goal_count += 1
                route = routes.get(goal_id)
                candidates = route.candidates if route is not None else ()
                candidate_operations = [
                    candidate.operation_id for candidate in candidates
                ]
                if candidate_operations:
                    predicted_top_1.add(candidate_operations[0])
                predicted_top_3.update(candidate_operations[:3])
                operation_hits_at_1 += int(
                    bool(expected.intersection(candidate_operations[:1]))
                )
                operation_hits_at_3 += int(
                    bool(expected.intersection(candidate_operations[:3]))
                )
                if not expected.intersection(candidate_operations[:1]):
                    misses.append(
                        {
                            "case_id": case_id,
                            "kind": "operation_top_1_miss",
                            "goal_id": goal_id,
                            "expected_operations": sorted(expected),
                            "predicted_operations": candidate_operations[:1],
                        }
                    )
                if not expected.intersection(candidate_operations[:3]):
                    misses.append(
                        {
                            "case_id": case_id,
                            "kind": "operation_top_3_miss",
                            "goal_id": goal_id,
                            "expected_operations": sorted(expected),
                            "predicted_operations": candidate_operations[:3],
                        }
                    )
            complete_recall = expected_case_operations.issubset(predicted_top_3)
            complete_exact = expected_case_operations == predicted_top_1
            complete_recall_hits += int(complete_recall)
            complete_exact_hits += int(complete_exact)
            if not complete_recall:
                misses.append(
                    {
                        "case_id": case_id,
                        "kind": "operation_set_miss",
                        "expected_operations": sorted(expected_case_operations),
                        "predicted_operations": sorted(predicted_top_3),
                    }
                )

        if expected_status == "no_tool":
            true_no_tool.add(case_id)
        if advice.status == "no_tool":
            predicted_no_tool.add(case_id)

        expected_clarification = _require_bool(case, "expected_clarification")
        predicted_clarification = advice.status in {
            "resolved",
            "ambiguous",
        } and bool(advice.uncertainties)
        if expected_clarification:
            clarification_required_count += 1
            clarification_true_positives += int(predicted_clarification)
            if not predicted_clarification:
                misses.append(
                    {
                        "case_id": case_id,
                        "kind": "missing_required_clarification",
                    }
                )
        else:
            clarification_negative_count += 1
            unnecessary_clarifications += int(predicted_clarification)
            if predicted_clarification:
                misses.append(
                    {
                        "case_id": case_id,
                        "kind": "unnecessary_clarification",
                        "uncertainty_kinds": [
                            item.kind for item in advice.uncertainties
                        ],
                        "uncertainty_fields": [
                            item.field
                            for item in advice.uncertainties
                            if item.field is not None
                        ],
                    }
                )

        if category in context_categories:
            context_count += 1
            expected_references = set(
                _require_string_list(case, "expected_unresolved_references")
            )
            predicted_references = {
                item.field
                for item in advice.uncertainties
                if item.kind == "unresolved_reference" and item.field is not None
            }
            context_hits += int(
                expected_case_operations.issubset(predicted_top_3)
                and expected_references == predicted_references
            )

        equivalent_sets = case.get("equivalent_provider_sets", [])
        if equivalent_sets:
            equivalent_count += 1
            allowed_sets = [set(_require_string_list_value(item)) for item in equivalent_sets]
            selected_names = {
                route.candidates[0].tool_name
                for route in advice.goals
                if route.candidates
            }
            equivalent_hits += int(
                expected_case_operations.issubset(predicted_top_3)
                and all(bool(selected_names.intersection(group)) for group in allowed_sets)
            )

        if expected_status == "resolved":
            active_preloads = set(select_active_preloads(list(advice.goals)))
            for raw_goal in goals:
                goal = _require_object(raw_goal, "goal")
                visibility_count += 1
                route = routes.get(_require_string(goal, "goal_id"))
                expected = set(_require_string_list(goal, "expected_operation_ids"))
                visible = (
                    advice.status == "resolved"
                    and route is not None
                    and bool(route.candidates)
                    and route.candidates[0].operation_id in expected
                    and route.candidates[0].tool_name in active_preloads
                    and inputs.registry.get_document(
                        route.candidates[0].tool_name
                    )
                    is not None
                )
                visibility_hits += int(visible)

        if advice.status != expected_status:
            misses.append(
                {
                    "case_id": case_id,
                    "kind": "status_miss",
                    "expected_status": expected_status,
                    "predicted_status": advice.status,
                    "reason_codes": list(advice.reason_codes),
                    "uncertainty_kinds": [
                        item.kind for item in advice.uncertainties
                    ],
                }
            )

    no_tool_true_positives = len(true_no_tool.intersection(predicted_no_tool))
    metrics = {
        "operation_recall_at_1": _rate(
            operation_hits_at_1, operation_goal_count
        ),
        "operation_recall_at_3": _rate(
            operation_hits_at_3, operation_goal_count
        ),
        "complete_operation_set_recall": _rate(
            complete_recall_hits, routable_case_count
        ),
        "complete_operation_set_exact_match": _rate(
            complete_exact_hits, routable_case_count
        ),
        "no_tool_precision": _ratio(
            no_tool_true_positives, len(predicted_no_tool)
        ),
        "no_tool_recall": _rate(no_tool_true_positives, len(true_no_tool)),
        "context_resolution_accuracy": _rate(context_hits, context_count),
        "equivalent_operation_set_correctness": _rate(
            equivalent_hits, equivalent_count
        ),
        "required_clarification_recall": _rate(
            clarification_true_positives, clarification_required_count
        ),
        "unnecessary_clarification_rate": _ratio(
            unnecessary_clarifications, clarification_negative_count
        ),
        "end_to_end_visibility_rate": _rate(
            visibility_hits, visibility_count
        ),
        "intent_view_success_rate": _rate(intent_successes, len(results)),
        "status_accuracy": _rate(status_hits, len(results)),
        "cold_start_latency_ms": round(cold_start_ms, 3),
        "local_warm_latency_p95_ms": round(_percentile_95(local_latencies), 3),
        "external_llm_latency_p95_ms": round(
            _percentile_95(external_latencies), 3
        ),
    }
    return metrics, misses


def _route_context(case: dict[str, Any]) -> RouteContext:
    payload = _require_object(case.get("context"), "context")
    return RouteContext(
        messages=tuple(
            RouteContextMessage(
                role=cast(Any, _require_string(item, "role")),
                content=_require_string(item, "content"),
                operation_ids=tuple(_require_string_list(item, "operation_ids")),
                output_kinds=cast(
                    Any, tuple(_require_string_list(item, "output_kinds"))
                ),
            )
            for item in (
                _require_object(raw, "context message")
                for raw in _require_list(payload, "messages")
            )
        ),
        reply_excerpt=_optional_string(payload, "reply_excerpt"),
        attachment_kinds=cast(
            Any, tuple(_require_string_list(payload, "attachment_kinds"))
        ),
        previous_operation_ids=tuple(
            _require_string_list(payload, "previous_operation_ids")
        ),
        pending_clarifications=tuple(
            _require_string_list(payload, "pending_clarifications")
        ),
    )


def _retrieval_query_shape(result: CaseResult) -> tuple[int, int]:
    texts = [_require_string(result.case, "utterance")]
    if result.analysis is not None:
        for goal in result.analysis.view.goals:
            texts.append(
                goal.rewritten_intent[:V3_DERIVED_QUERY_MAX_CHARACTERS]
            )
            texts.extend(
                capability.retrieval_query[:V3_DERIVED_QUERY_MAX_CHARACTERS]
                for capability in goal.hypothetical_capabilities
            )
            texts.extend(
                query[:V3_DERIVED_QUERY_MAX_CHARACTERS]
                for query in goal.alternative_capability_queries
            )
    unique = tuple(dict.fromkeys(text.casefold() for text in texts))
    return len(unique), max((len(text) for text in unique), default=0)


def _intent_failure_reason(error: IntentViewError) -> str:
    causes: list[str] = []
    current: BaseException | None = error
    while current is not None and len(causes) < 6:
        causes.append(type(current).__name__)
        current = current.__cause__
    if "JSONDecodeError" in causes:
        return "malformed_tool_json"
    if "TimeoutError" in causes:
        return "provider_timeout"
    if isinstance(error, IntentViewInvalidError):
        return "schema_invalid"
    if isinstance(error, IntentViewUnavailableError):
        return "provider_unavailable"
    return "intent_view_error"


def _intent_failure_detail(error: IntentViewError) -> str:
    cause_types: list[str] = []
    current: BaseException | None = error
    while current is not None and len(cause_types) < 6:
        cause_types.append(type(current).__name__)
        if isinstance(current, ValidationError):
            errors = current.errors()
            if not errors:
                return "validation_error:root"
            first = errors[0]
            location = ".".join(str(item) for item in first.get("loc", ()))
            return f"{first.get('type', 'validation_error')}:{location or 'root'}"
        current = current.__cause__
    return ">".join(cause_types)


def _rate(numerator: int, denominator: int) -> float:
    if denominator < 1:
        raise V3EvaluationError("required evaluation denominator is empty")
    return round(numerator / denominator, 6)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _percentile_95(values: list[float]) -> float:
    if not values:
        raise V3EvaluationError("latency sample set is empty")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _require_object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise V3EvaluationError(f"{field} must be an object")
    return cast(dict[str, Any], value)


def _require_list(value: dict[str, Any], field: str) -> list[Any]:
    item = value.get(field)
    if not isinstance(item, list):
        raise V3EvaluationError(f"{field} must be a list")
    return cast(list[Any], item)


def _require_string(value: dict[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item or item != item.strip():
        raise V3EvaluationError(f"{field} must be a trimmed string")
    return item


def _require_string_list(value: dict[str, Any], field: str) -> list[str]:
    return _require_string_list_value(value.get(field))


def _require_string_list_value(value: object) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item or item != item.strip()
        for item in value
    ):
        raise V3EvaluationError("value must be a trimmed string list")
    return cast(list[str], value)


def _require_bool(value: dict[str, Any], field: str) -> bool:
    item = value.get(field)
    if not isinstance(item, bool):
        raise V3EvaluationError(f"{field} must be a boolean")
    return item


def _optional_string(value: dict[str, Any], field: str) -> str | None:
    item = value.get(field)
    if item is None:
        return None
    if not isinstance(item, str) or not item or item != item.strip():
        raise V3EvaluationError(f"{field} must be null or a trimmed string")
    return item


if __name__ == "__main__":
    raise SystemExit(main())
