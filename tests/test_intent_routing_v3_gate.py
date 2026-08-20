from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from agent.routing.gate_v3 import (
    V3_ROUTER_SOURCE_FILES,
    V3QualityGateError,
    build_v3_quality_gate_report,
    build_v3_runtime_configuration,
    canonical_digest,
    file_sha256,
    v3_router_digest,
    verify_v3_quality_gate,
)


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "application"
    for relative in V3_ROUTER_SOURCE_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"source:{relative}\n", encoding="utf-8")
    dataset = root / "eval/intent_routing/v3_dataset.zh.jsonl"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text(
        json.dumps({"id": "case-1", "goals": []}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    catalog = root / "eval/intent_routing/catalog.zh.json"
    catalog.write_text(
        json.dumps({"schema_version": "1", "tools": [{"name": "one"}]}),
        encoding="utf-8",
    )
    return root, dataset, catalog


def _configuration() -> dict[str, object]:
    return build_v3_runtime_configuration(
        dense_min_similarity=0.56,
        inner_rrf_k=60,
        outer_rrf_k=60,
        original_lexical_weight=1.0,
        original_dense_weight=1.0,
        llm_view_weight=2.0,
        derived_query_max_characters=96,
        dense_threads=2,
        low_margin=0.001,
        intent_max_tokens=1_000,
        intent_timeout_seconds=12.0,
    )


def _metrics() -> dict[str, float]:
    return {
        "operation_recall_at_1": 0.95,
        "operation_recall_at_3": 1.0,
        "complete_operation_set_recall": 0.95,
        "complete_operation_set_exact_match": 0.90,
        "no_tool_precision": 1.0,
        "no_tool_recall": 1.0,
        "context_resolution_accuracy": 0.95,
        "equivalent_operation_set_correctness": 1.0,
        "required_clarification_recall": 1.0,
        "unnecessary_clarification_rate": 0.05,
        "end_to_end_visibility_rate": 1.0,
        "intent_view_success_rate": 1.0,
        "status_accuracy": 0.95,
        "cold_start_latency_ms": 100.0,
        "local_warm_latency_p95_ms": 10.0,
        "external_llm_latency_p95_ms": 800.0,
    }


def _report(root: Path, dataset: Path, catalog: Path) -> dict[str, object]:
    return build_v3_quality_gate_report(
        generated_at=datetime.now(UTC).isoformat(),
        application_root=root,
        router_version="intent-routing-v3-llm-hybrid-1",
        retrieval_model_id="BAAI/bge-small-zh-v1.5",
        intent_model_id="deepseek-v4-flash",
        prompt_version="intent-view-v3-8",
        prompt_digest="sha256:" + "a" * 64,
        dataset_path=dataset,
        catalog_path=catalog,
        case_count=1,
        goal_count=0,
        tool_count=1,
        configuration=_configuration(),
        metrics=_metrics(),
    )


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")


def _verify(
    report_path: Path,
    root: Path,
    dataset: Path,
    catalog: Path,
    **overrides: object,
):
    kwargs: dict[str, object] = {
        "application_root": root,
        "dataset_path": dataset,
        "catalog_path": catalog,
        "expected_router_version": "intent-routing-v3-llm-hybrid-1",
        "expected_retrieval_model_id": "BAAI/bge-small-zh-v1.5",
        "expected_intent_model_id": "deepseek-v4-flash",
        "expected_prompt_version": "intent-view-v3-8",
        "expected_prompt_digest": "sha256:" + "a" * 64,
        "expected_configuration": _configuration(),
    }
    kwargs.update(overrides)
    return verify_v3_quality_gate(report_path, **kwargs)  # type: ignore[arg-type]


def test_v3_gate_binds_all_authoritative_digests(tmp_path: Path) -> None:
    root, dataset, catalog = _inputs(tmp_path)
    report = _report(root, dataset, catalog)
    report_path = root / "eval/intent_routing/v3-gate-report.json"
    _write_report(report_path, report)

    evidence = _verify(report_path, root, dataset, catalog)

    assert evidence.router_digest == cast(dict[str, str], report["router"])["digest"]
    assert evidence.model_digest == cast(dict[str, str], report["models"])["digest"]
    assert evidence.prompt_digest == "sha256:" + "a" * 64
    assert evidence.configuration_digest == cast(
        dict[str, object], report["configuration"]
    )["digest"]


def test_v3_gate_uses_calibrated_qwen4b_embedding_latency_slo(
    tmp_path: Path,
) -> None:
    root, dataset, catalog = _inputs(tmp_path)
    configuration = {
        **_configuration(),
        "embedding_backend": "openai_compatible",
        "dense_threads": 0,
    }
    report = build_v3_quality_gate_report(
        generated_at=datetime.now(UTC).isoformat(),
        application_root=root,
        router_version="intent-routing-v3-llm-hybrid-1",
        retrieval_model_id="qwen3-embedding:4b",
        intent_model_id="deepseek-v4-flash",
        prompt_version="intent-view-v3-8",
        prompt_digest="sha256:" + "a" * 64,
        dataset_path=dataset,
        catalog_path=catalog,
        case_count=1,
        goal_count=0,
        tool_count=1,
        configuration=configuration,
        metrics={**_metrics(), "local_warm_latency_p95_ms": 2_900.0},
    )

    thresholds = cast(dict[str, dict[str, float]], report["thresholds"])
    assert thresholds["maximum"]["local_warm_latency_p95_ms"] == 3_000.0
    assert report["passed"] is True


def test_v3_gate_keeps_default_http_embedding_latency_slo(tmp_path: Path) -> None:
    root, dataset, catalog = _inputs(tmp_path)
    configuration = {
        **_configuration(),
        "embedding_backend": "openai_compatible",
        "dense_threads": 0,
    }
    report = build_v3_quality_gate_report(
        generated_at=datetime.now(UTC).isoformat(),
        application_root=root,
        router_version="intent-routing-v3-llm-hybrid-1",
        retrieval_model_id="other-embedding-model",
        intent_model_id="gpt-5.6-terra",
        prompt_version="intent-view-v3-8",
        prompt_digest="sha256:" + "a" * 64,
        dataset_path=dataset,
        catalog_path=catalog,
        case_count=1,
        goal_count=0,
        tool_count=1,
        configuration=configuration,
        metrics={**_metrics(), "local_warm_latency_p95_ms": 501.0},
    )

    thresholds = cast(dict[str, dict[str, float]], report["thresholds"])
    assert thresholds["maximum"]["local_warm_latency_p95_ms"] == 500.0
    assert report["passed"] is False


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"expected_intent_model_id": "other-model"}, "intent model"),
        ({"expected_prompt_digest": "sha256:" + "b" * 64}, "prompt digest"),
        (
            {
                "expected_configuration": {
                    **_configuration(),
                    "low_margin": 0.002,
                }
            },
            "configuration",
        ),
    ],
)
def test_v3_gate_rejects_expected_runtime_drift(
    tmp_path: Path,
    override: dict[str, object],
    message: str,
) -> None:
    root, dataset, catalog = _inputs(tmp_path)
    report_path = root / "report.json"
    _write_report(report_path, _report(root, dataset, catalog))

    with pytest.raises(V3QualityGateError, match=message):
        _verify(report_path, root, dataset, catalog, **override)


def test_v3_gate_rejects_router_and_dataset_mutation(tmp_path: Path) -> None:
    root, dataset, catalog = _inputs(tmp_path)
    report_path = root / "report.json"
    report = _report(root, dataset, catalog)
    _write_report(report_path, report)

    first_source = root / V3_ROUTER_SOURCE_FILES[0]
    first_source.write_text("mutated\n", encoding="utf-8")
    with pytest.raises(V3QualityGateError, match="router digest"):
        _verify(report_path, root, dataset, catalog)

    first_source.write_text(
        f"source:{V3_ROUTER_SOURCE_FILES[0]}\n",
        encoding="utf-8",
    )
    dataset.write_text(
        dataset.read_text(encoding="utf-8")
        + json.dumps({"id": "case-2", "goals": []})
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(V3QualityGateError, match="dataset digest"):
        _verify(report_path, root, dataset, catalog)


def test_v3_gate_rejects_modified_thresholds_even_with_new_artifact_digest(
    tmp_path: Path,
) -> None:
    root, dataset, catalog = _inputs(tmp_path)
    report = _report(root, dataset, catalog)
    thresholds = cast(dict[str, dict[str, float]], report["thresholds"])
    thresholds["minimum"]["operation_recall_at_1"] = 0.1
    report["artifact_digest"] = canonical_digest(report)
    report_path = root / "report.json"
    _write_report(report_path, report)

    with pytest.raises(V3QualityGateError, match="thresholds"):
        _verify(report_path, root, dataset, catalog)


def test_v3_gate_never_verifies_a_below_threshold_report(tmp_path: Path) -> None:
    root, dataset, catalog = _inputs(tmp_path)
    metrics = _metrics()
    metrics["operation_recall_at_1"] = 0.5
    report = build_v3_quality_gate_report(
        generated_at=datetime.now(UTC).isoformat(),
        application_root=root,
        router_version="intent-routing-v3-llm-hybrid-1",
        retrieval_model_id="BAAI/bge-small-zh-v1.5",
        intent_model_id="deepseek-v4-flash",
        prompt_version="intent-view-v3-8",
        prompt_digest="sha256:" + "a" * 64,
        dataset_path=dataset,
        catalog_path=catalog,
        case_count=1,
        goal_count=0,
        tool_count=1,
        configuration=_configuration(),
        metrics=metrics,
    )
    report_path = root / "report.json"
    _write_report(report_path, report)

    assert report["passed"] is False
    with pytest.raises(V3QualityGateError, match="did not pass"):
        _verify(report_path, root, dataset, catalog)


def test_checked_in_v3_gate_report_matches_current_sources() -> None:
    root = Path(__file__).resolve().parents[1]
    report_path = root / "eval/intent_routing/v3-gate-report.json"
    report = cast(
        dict[str, object],
        json.loads(report_path.read_text(encoding="utf-8")),
    )
    router = cast(dict[str, object], report["router"])
    dataset = cast(dict[str, object], report["dataset"])
    catalog = cast(dict[str, object], report["catalog"])

    assert report["passed"] is True
    assert report["failures"] == []
    assert router["digest"] == v3_router_digest(root)
    assert dataset["sha256"] == file_sha256(
        root / "eval/intent_routing/v3_dataset.zh.jsonl"
    )
    assert catalog["sha256"] == file_sha256(
        root / "eval/intent_routing/catalog.zh.json"
    )
    assert report["artifact_digest"] == canonical_digest(report)
