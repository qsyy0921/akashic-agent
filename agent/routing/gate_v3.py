from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, cast

V3_QUALITY_GATE_SCHEMA_VERSION = "3"
V3_QUALITY_GATE_GENERATOR = "eval/intent_routing/run_v3_eval.py"
V3_MINIMUM_THRESHOLDS: dict[str, float] = {
    "operation_recall_at_3": 0.95,
    "operation_recall_at_1": 0.90,
    "complete_operation_set_recall": 0.90,
    "no_tool_precision": 0.95,
    "no_tool_recall": 0.95,
    "context_resolution_accuracy": 0.90,
    "equivalent_operation_set_correctness": 1.0,
    "required_clarification_recall": 0.90,
    "end_to_end_visibility_rate": 1.0,
    "intent_view_success_rate": 1.0,
}
V3_MAXIMUM_THRESHOLDS: dict[str, float] = {
    "unnecessary_clarification_rate": 0.10,
    "local_warm_latency_p95_ms": 200.0,
}
V3_OPENAI_COMPATIBLE_WARM_LATENCY_P95_MS = 500.0
V3_QWEN3_EMBEDDING_4B_WARM_LATENCY_P95_MS = 3_000.0
V3_ROUTER_SOURCE_FILES = (
    "agent/core/passive_turn.py",
    "agent/tools/registry.py",
    "agent/routing/advisor.py",
    "agent/routing/advisor_v3.py",
    "agent/routing/config.py",
    "agent/routing/context.py",
    "agent/routing/contracts.py",
    "agent/routing/dense.py",
    "agent/routing/gate_v3.py",
    "agent/routing/hybrid.py",
    "agent/routing/intent_view.py",
    "agent/routing/protocol.py",
    "agent/routing/uncertainty.py",
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_METRIC_KEYS = frozenset(
    {
        "operation_recall_at_1",
        "operation_recall_at_3",
        "complete_operation_set_recall",
        "complete_operation_set_exact_match",
        "no_tool_precision",
        "no_tool_recall",
        "context_resolution_accuracy",
        "equivalent_operation_set_correctness",
        "required_clarification_recall",
        "unnecessary_clarification_rate",
        "end_to_end_visibility_rate",
        "intent_view_success_rate",
        "status_accuracy",
        "cold_start_latency_ms",
        "local_warm_latency_p95_ms",
        "external_llm_latency_p95_ms",
    }
)


class V3QualityGateError(RuntimeError):
    """Raised when V3 quality evidence is missing, stale, or below threshold."""


@dataclass(frozen=True, slots=True)
class V3QualityGateEvidence:
    report_digest: str
    router_version: str
    router_digest: str
    retrieval_model_id: str
    intent_model_id: str
    model_digest: str
    prompt_version: str
    prompt_digest: str
    dataset_digest: str
    catalog_digest: str
    configuration_digest: str
    metrics: dict[str, float]


def build_v3_runtime_configuration(
    *,
    dense_min_similarity: float,
    inner_rrf_k: int,
    outer_rrf_k: int,
    original_lexical_weight: float,
    original_dense_weight: float,
    llm_view_weight: float,
    derived_query_max_characters: int,
    dense_threads: int,
    low_margin: float,
    intent_max_tokens: int,
    intent_timeout_seconds: float,
    embedding_backend: str = "fastembed",
    embedding_dimension: int = 512,
    embedding_batch_size: int = 10,
    embedding_timeout_seconds: float = 30.0,
) -> dict[str, object]:
    return {
        "candidate_top_k": 3,
        "channel_weights": {
            "original_dense": original_dense_weight,
            "original_lexical": original_lexical_weight,
            "llm_view": llm_view_weight,
        },
        "context_limits": {
            "characters": 6_000,
            "messages": 8,
        },
        "dense_min_similarity": dense_min_similarity,
        "dense_threads": dense_threads,
        "embedding_backend": embedding_backend,
        "embedding_batch_size": embedding_batch_size,
        "embedding_dimension": embedding_dimension,
        "embedding_timeout_seconds": float(embedding_timeout_seconds),
        "derived_query_max_characters": derived_query_max_characters,
        "inner_rrf_k": inner_rrf_k,
        "intent_max_tokens": intent_max_tokens,
        "intent_timeout_seconds": float(intent_timeout_seconds),
        "low_margin": low_margin,
        "max_candidates_per_goal": 3,
        "max_goals": 4,
        "max_hypothetical_capabilities_per_goal": 3,
        "max_preloads": 5,
        "outer_rrf_k": outer_rrf_k,
        "provider_selection_policy": "snapshot-health-stable-name-v1",
        "retrieval_top_k": 8,
    }


def build_v3_quality_gate_report(
    *,
    generated_at: str,
    application_root: Path,
    router_version: str,
    retrieval_model_id: str,
    intent_model_id: str,
    prompt_version: str,
    prompt_digest: str,
    dataset_path: Path,
    catalog_path: Path,
    case_count: int,
    goal_count: int,
    tool_count: int,
    configuration: Mapping[str, object],
    metrics: Mapping[str, float],
) -> dict[str, object]:
    checked_metrics = _validate_metrics(metrics)
    thresholds = v3_quality_gate_thresholds(
        configuration,
        retrieval_model_id=retrieval_model_id,
    )
    failures = v3_quality_gate_failures(
        checked_metrics,
        thresholds=thresholds,
    )
    router_digest = v3_router_digest(application_root)
    model_digest = v3_model_digest(retrieval_model_id, intent_model_id)
    configuration_digest = canonical_digest(configuration)
    payload: dict[str, object] = {
        "schema_version": V3_QUALITY_GATE_SCHEMA_VERSION,
        "generated_by": V3_QUALITY_GATE_GENERATOR,
        "generated_at": generated_at,
        "router": {"version": router_version, "digest": router_digest},
        "models": {
            "retrieval_model_id": retrieval_model_id,
            "intent_model_id": intent_model_id,
            "digest": model_digest,
        },
        "prompt": {"version": prompt_version, "digest": prompt_digest},
        "dataset": {
            "path": _relative_input_path(application_root, dataset_path),
            "sha256": file_sha256(dataset_path),
            "case_count": case_count,
            "goal_count": goal_count,
        },
        "catalog": {
            "path": _relative_input_path(application_root, catalog_path),
            "sha256": file_sha256(catalog_path),
            "tool_count": tool_count,
        },
        "configuration": {
            "values": dict(configuration),
            "digest": configuration_digest,
        },
        "metrics": checked_metrics,
        "thresholds": {
            "minimum": dict(thresholds["minimum"]),
            "maximum": dict(thresholds["maximum"]),
        },
        "passed": not failures,
        "failures": failures,
    }
    payload["artifact_digest"] = canonical_digest(payload)
    return payload


def verify_v3_quality_gate(
    report_path: Path,
    *,
    application_root: Path,
    dataset_path: Path,
    catalog_path: Path,
    expected_router_version: str,
    expected_retrieval_model_id: str,
    expected_intent_model_id: str,
    expected_prompt_version: str,
    expected_prompt_digest: str,
    expected_configuration: Mapping[str, object],
) -> V3QualityGateEvidence:
    report = _read_object(report_path, "report")
    _require_exact_keys(
        report,
        {
            "schema_version",
            "generated_by",
            "generated_at",
            "router",
            "models",
            "prompt",
            "dataset",
            "catalog",
            "configuration",
            "metrics",
            "thresholds",
            "passed",
            "failures",
            "artifact_digest",
        },
        "report",
    )
    artifact_digest = _require_digest(report["artifact_digest"], "artifact")
    if artifact_digest != canonical_digest(report):
        raise V3QualityGateError("V3 quality gate artifact digest mismatch")
    if report["schema_version"] != V3_QUALITY_GATE_SCHEMA_VERSION:
        raise V3QualityGateError("V3 quality gate schema mismatch")
    if report["generated_by"] != V3_QUALITY_GATE_GENERATOR:
        raise V3QualityGateError("V3 quality gate generator mismatch")
    _validate_generated_at(report["generated_at"])

    router = _require_mapping(report["router"], "router")
    _require_exact_keys(router, {"version", "digest"}, "router")
    expected_router_digest = v3_router_digest(application_root)
    if router["version"] != expected_router_version:
        raise V3QualityGateError("V3 quality gate router version mismatch")
    if _require_digest(router["digest"], "router.digest") != expected_router_digest:
        raise V3QualityGateError("V3 quality gate router digest mismatch")

    models = _require_mapping(report["models"], "models")
    _require_exact_keys(
        models,
        {"retrieval_model_id", "intent_model_id", "digest"},
        "models",
    )
    if models["retrieval_model_id"] != expected_retrieval_model_id:
        raise V3QualityGateError("V3 retrieval model mismatch")
    if models["intent_model_id"] != expected_intent_model_id:
        raise V3QualityGateError("V3 intent model mismatch")
    expected_model_digest = v3_model_digest(
        expected_retrieval_model_id,
        expected_intent_model_id,
    )
    if _require_digest(models["digest"], "models.digest") != expected_model_digest:
        raise V3QualityGateError("V3 model digest mismatch")

    prompt = _require_mapping(report["prompt"], "prompt")
    _require_exact_keys(prompt, {"version", "digest"}, "prompt")
    if prompt["version"] != expected_prompt_version:
        raise V3QualityGateError("V3 intent prompt version mismatch")
    if _require_digest(prompt["digest"], "prompt.digest") != expected_prompt_digest:
        raise V3QualityGateError("V3 intent prompt digest mismatch")

    dataset = _verify_input_artifact(
        report["dataset"],
        name="dataset",
        path=dataset_path,
        application_root=application_root,
        count_keys=("case_count", "goal_count"),
        expected_counts=_dataset_counts(dataset_path),
    )
    catalog = _verify_input_artifact(
        report["catalog"],
        name="catalog",
        path=catalog_path,
        application_root=application_root,
        count_keys=("tool_count",),
        expected_counts=(_catalog_tool_count(catalog_path),),
    )

    configuration = _require_mapping(report["configuration"], "configuration")
    _require_exact_keys(configuration, {"values", "digest"}, "configuration")
    values = _require_mapping(configuration["values"], "configuration.values")
    expected_values = dict(expected_configuration)
    if values != expected_values:
        raise V3QualityGateError("V3 quality gate configuration mismatch")
    expected_configuration_digest = canonical_digest(expected_values)
    if (
        _require_digest(configuration["digest"], "configuration.digest")
        != expected_configuration_digest
    ):
        raise V3QualityGateError("V3 quality gate configuration digest mismatch")

    expected_thresholds = v3_quality_gate_thresholds(
        expected_values,
        retrieval_model_id=expected_retrieval_model_id,
    )
    if report["thresholds"] != expected_thresholds:
        raise V3QualityGateError("V3 quality gate thresholds were modified")
    metrics = _validate_metrics(_require_mapping(report["metrics"], "metrics"))
    expected_failures = v3_quality_gate_failures(
        metrics,
        thresholds=expected_thresholds,
    )
    failures = report["failures"]
    if not isinstance(failures, list) or any(
        not isinstance(item, str) for item in failures
    ):
        raise V3QualityGateError("V3 quality gate failures are invalid")
    if failures != expected_failures:
        raise V3QualityGateError("V3 quality gate failure summary mismatch")
    if report["passed"] is not (not expected_failures):
        raise V3QualityGateError("V3 quality gate passed flag is inconsistent")
    if report["passed"] is not True:
        raise V3QualityGateError("V3 quality gate did not pass")
    return V3QualityGateEvidence(
        report_digest=artifact_digest,
        router_version=expected_router_version,
        router_digest=expected_router_digest,
        retrieval_model_id=expected_retrieval_model_id,
        intent_model_id=expected_intent_model_id,
        model_digest=expected_model_digest,
        prompt_version=expected_prompt_version,
        prompt_digest=expected_prompt_digest,
        dataset_digest=cast(str, dataset["sha256"]),
        catalog_digest=cast(str, catalog["sha256"]),
        configuration_digest=expected_configuration_digest,
        metrics=metrics,
    )


def v3_quality_gate_thresholds(
    configuration: Mapping[str, object],
    *,
    retrieval_model_id: str = "",
) -> dict[str, dict[str, float]]:
    backend = configuration.get("embedding_backend")
    if backend not in {"fastembed", "openai_compatible"}:
        raise V3QualityGateError("V3 embedding backend is unsupported")
    maximum = dict(V3_MAXIMUM_THRESHOLDS)
    if backend == "openai_compatible":
        maximum["local_warm_latency_p95_ms"] = (
            V3_QWEN3_EMBEDDING_4B_WARM_LATENCY_P95_MS
            if retrieval_model_id == "qwen3-embedding:4b"
            else V3_OPENAI_COMPATIBLE_WARM_LATENCY_P95_MS
        )
    return {
        "minimum": dict(V3_MINIMUM_THRESHOLDS),
        "maximum": maximum,
    }


def v3_quality_gate_failures(
    metrics: Mapping[str, float],
    *,
    thresholds: Mapping[str, Mapping[str, float]] | None = None,
) -> list[str]:
    active_thresholds = thresholds or {
        "minimum": V3_MINIMUM_THRESHOLDS,
        "maximum": V3_MAXIMUM_THRESHOLDS,
    }
    failures = [
        name
        for name, threshold in active_thresholds["minimum"].items()
        if metrics[name] < threshold
    ]
    failures.extend(
        name
        for name, threshold in active_thresholds["maximum"].items()
        if metrics[name] > threshold
    )
    return failures


def v3_router_digest(application_root: Path) -> str:
    files: list[dict[str, str]] = []
    root = application_root.resolve()
    for relative in V3_ROUTER_SOURCE_FILES:
        path = root / relative
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise V3QualityGateError(
                f"cannot read V3 router source: {relative}"
            ) from exc
        files.append({"path": relative, "sha256": digest})
    return canonical_digest({"files": files})


def v3_model_digest(retrieval_model_id: str, intent_model_id: str) -> str:
    return canonical_digest(
        {
            "intent_model_id": intent_model_id,
            "retrieval_model_id": retrieval_model_id,
        }
    )


def file_sha256(path: Path) -> str:
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise V3QualityGateError(f"cannot read V3 gate input: {path.name}") from exc
    return f"sha256:{digest}"


def canonical_digest(payload: Mapping[str, object]) -> str:
    unsigned = {
        key: value for key, value in payload.items() if key != "artifact_digest"
    }
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _validate_metrics(raw: Mapping[str, object]) -> dict[str, float]:
    if set(raw) != _METRIC_KEYS:
        raise V3QualityGateError("V3 quality gate metric set mismatch")
    metrics: dict[str, float] = {}
    for name, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise V3QualityGateError(f"V3 quality gate metric is not numeric: {name}")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise V3QualityGateError(f"V3 quality gate metric is invalid: {name}")
        if not name.endswith("_ms") and numeric > 1:
            raise V3QualityGateError(f"V3 quality gate rate is invalid: {name}")
        metrics[name] = numeric
    return metrics


def _verify_input_artifact(
    raw: object,
    *,
    name: str,
    path: Path,
    application_root: Path,
    count_keys: tuple[str, ...],
    expected_counts: tuple[int, ...],
) -> dict[str, object]:
    artifact = _require_mapping(raw, name)
    _require_exact_keys(
        artifact,
        {"path", "sha256", *count_keys},
        name,
    )
    if artifact["path"] != _relative_input_path(application_root, path):
        raise V3QualityGateError(f"V3 quality gate {name} path mismatch")
    if _require_digest(artifact["sha256"], f"{name}.sha256") != file_sha256(path):
        raise V3QualityGateError(f"V3 quality gate {name} digest mismatch")
    for key, expected in zip(count_keys, expected_counts, strict=True):
        if artifact[key] != expected:
            raise V3QualityGateError(f"V3 quality gate {name} {key} mismatch")
    return artifact


def _relative_input_path(application_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(application_root.resolve()).as_posix()
    except ValueError as exc:
        raise V3QualityGateError("V3 gate input must stay under application root") from exc


def _dataset_counts(path: Path) -> tuple[int, int]:
    try:
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]
    except (OSError, json.JSONDecodeError) as exc:
        raise V3QualityGateError("V3 gate dataset is invalid") from exc
    if any(not isinstance(row, dict) for row in rows):
        raise V3QualityGateError("V3 gate dataset rows must be objects")
    goal_count = 0
    for row in rows:
        goals = row.get("goals")
        if not isinstance(goals, list):
            raise V3QualityGateError("V3 gate dataset goals are invalid")
        goal_count += len(goals)
    return len(rows), goal_count


def _catalog_tool_count(path: Path) -> int:
    catalog = _read_object(path, "catalog")
    tools = catalog.get("tools")
    if not isinstance(tools, list):
        raise V3QualityGateError("V3 gate catalog tools are invalid")
    return len(tools)


def _read_object(path: Path, name: str) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise V3QualityGateError(f"V3 quality gate {name} cannot be read") from exc
    if not isinstance(raw, dict):
        raise V3QualityGateError(f"V3 quality gate {name} must be an object")
    return cast(dict[str, object], raw)


def _require_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise V3QualityGateError(f"V3 quality gate {name} must be an object")
    return cast(dict[str, object], value)


def _require_exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    name: str,
) -> None:
    if set(value) != expected:
        raise V3QualityGateError(f"V3 quality gate {name} fields mismatch")


def _require_digest(value: object, name: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise V3QualityGateError(f"V3 quality gate {name} is invalid")
    return value


def _validate_generated_at(value: object) -> None:
    if not isinstance(value, str):
        raise V3QualityGateError("V3 quality gate timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise V3QualityGateError("V3 quality gate timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise V3QualityGateError("V3 quality gate timestamp needs a timezone")


__all__ = [
    "V3_MAXIMUM_THRESHOLDS",
    "V3_OPENAI_COMPATIBLE_WARM_LATENCY_P95_MS",
    "V3_QWEN3_EMBEDDING_4B_WARM_LATENCY_P95_MS",
    "V3_MINIMUM_THRESHOLDS",
    "V3_QUALITY_GATE_GENERATOR",
    "V3_QUALITY_GATE_SCHEMA_VERSION",
    "V3_ROUTER_SOURCE_FILES",
    "V3QualityGateError",
    "V3QualityGateEvidence",
    "build_v3_quality_gate_report",
    "build_v3_runtime_configuration",
    "canonical_digest",
    "file_sha256",
    "v3_model_digest",
    "v3_quality_gate_failures",
    "v3_quality_gate_thresholds",
    "v3_router_digest",
    "verify_v3_quality_gate",
]
