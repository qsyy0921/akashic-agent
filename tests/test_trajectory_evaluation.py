import hashlib
import json

import pytest

from eval.trajectory import (
    ClarificationPolicy,
    StepMatcher,
    TrajectoryOracle,
    TrajectoryOutcome,
    TrajectoryRun,
    TrajectoryStep,
    TrajectoryTerminalStatus,
    aggregate_evaluations,
    evaluate_trajectory,
)
from eval.trajectory.run import evaluate_jsonl, main


def _step(
    index: int,
    kind: str,
    subject: str,
    outcome: TrajectoryOutcome = TrajectoryOutcome.OBSERVED,
    *,
    attempt: int = 1,
) -> TrajectoryStep:
    return TrajectoryStep(
        step_id=f"step-{index}",
        kind=kind,
        subject=subject,
        outcome=outcome,
        attempt=attempt,
        latency_ms=10.0,
        input_tokens=5,
    )


def _oracle(
    *matchers: StepMatcher,
    status: TrajectoryTerminalStatus = TrajectoryTerminalStatus.SUCCEEDED,
    clarification: ClarificationPolicy = ClarificationPolicy.OPTIONAL,
    max_retries: int = 0,
    dependencies: dict[str, tuple[str, ...]] | None = None,
) -> TrajectoryOracle:
    return TrajectoryOracle(
        expected_steps=matchers,
        terminal_status=status,
        clarification=clarification,
        max_retries=max_retries,
        dependencies=dependencies or {},
    )


def _run(
    *steps: TrajectoryStep,
    oracle: TrajectoryOracle,
    case_id: str = "case-1",
    attempt: int = 1,
    status: TrajectoryTerminalStatus = TrajectoryTerminalStatus.SUCCEEDED,
) -> TrajectoryRun:
    return TrajectoryRun(
        case_id=case_id,
        attempt=attempt,
        terminal_status=status,
        steps=steps,
        oracle=oracle,
    )


def test_trajectory_success_satisfies_dependency_and_metrics():
    steps = (
        _step(0, "tool_call", "fetch"),
        _step(1, "tool_result", "fetch", TrajectoryOutcome.SUCCEEDED),
        _step(2, "tool_call", "summarize"),
        _step(3, "tool_result", "summarize", TrajectoryOutcome.SUCCEEDED),
        _step(4, "reply", "final", TrajectoryOutcome.SUCCEEDED),
    )
    oracle = _oracle(
        StepMatcher("tool_call", ("fetch",)),
        StepMatcher("tool_result", ("fetch",), (TrajectoryOutcome.SUCCEEDED,)),
        StepMatcher("tool_call", ("summarize",)),
        StepMatcher(
            "tool_result", ("summarize",), (TrajectoryOutcome.SUCCEEDED,)
        ),
        StepMatcher("reply", ("final",), (TrajectoryOutcome.SUCCEEDED,)),
        dependencies={"summarize": ("fetch",)},
    )

    result = evaluate_trajectory(_run(*steps, oracle=oracle))

    assert result.success
    assert result.first_error is None
    assert result.correct_prefix_length == 5
    assert result.prefix_rate == 1.0
    assert result.input_tokens == 25
    assert result.latency_ms == 50.0


def test_trajectory_locates_first_unexpected_step_and_prefix():
    oracle = _oracle(
        StepMatcher("route", ("research",)),
        StepMatcher("tool_call", ("arxiv_search", "web_search")),
        StepMatcher("reply", ("final",)),
    )
    result = evaluate_trajectory(
        _run(
            _step(0, "route", "research"),
            _step(1, "tool_call", "image_generate"),
            _step(2, "reply", "final"),
            oracle=oracle,
        )
    )

    assert not result.success
    assert result.first_error is not None
    assert result.first_error.index == 1
    assert result.first_error.reason_code == "unexpected_step"
    assert result.correct_prefix_length == 1
    assert result.prefix_rate == pytest.approx(1 / 3)


def test_trajectory_dependency_requires_successful_prior_result():
    oracle = _oracle(
        StepMatcher("tool_call", ("summarize",)),
        dependencies={"summarize": ("fetch",)},
    )
    result = evaluate_trajectory(
        _run(_step(0, "tool_call", "summarize"), oracle=oracle)
    )

    assert result.first_error is not None
    assert result.first_error.reason_code == "dependency_violation"
    assert result.first_error.expected == "completed:fetch"


def test_trajectory_retry_budget_is_independent_first_error():
    oracle = _oracle(StepMatcher("tool_call", ("fetch",)), max_retries=0)
    result = evaluate_trajectory(
        _run(_step(0, "tool_call", "fetch", attempt=2), oracle=oracle)
    )

    assert result.first_error is not None
    assert result.first_error.reason_code == "retry_budget_exceeded"
    assert result.retry_count == 1


@pytest.mark.parametrize(
    ("policy", "steps", "reason"),
    [
        (
            ClarificationPolicy.FORBIDDEN,
            (_step(0, "clarification", "scope"),),
            "forbidden_clarification",
        ),
        (ClarificationPolicy.REQUIRED, (), "missing_required_clarification"),
    ],
)
def test_trajectory_clarification_policy(policy, steps, reason):
    oracle = _oracle(clarification=policy)
    result = evaluate_trajectory(_run(*steps, oracle=oracle))

    assert result.first_error is not None
    assert result.first_error.reason_code == reason


def test_trajectory_reports_missing_step_before_terminal_mismatch():
    oracle = _oracle(
        StepMatcher("route", ("research",)),
        StepMatcher("reply", ("final",)),
    )
    result = evaluate_trajectory(
        _run(
            _step(0, "route", "research"),
            oracle=oracle,
            status=TrajectoryTerminalStatus.FAILED,
        )
    )

    assert result.first_error is not None
    assert result.first_error.reason_code == "missing_step"
    assert result.first_error.index == 1


def test_trajectory_aggregate_computes_pass_power_k():
    success_oracle = _oracle()
    failure_oracle = _oracle(status=TrajectoryTerminalStatus.SUCCEEDED)
    evaluations = [
        evaluate_trajectory(
            _run(oracle=success_oracle, case_id="a", attempt=1)
        ),
        evaluate_trajectory(
            _run(oracle=success_oracle, case_id="a", attempt=2)
        ),
        evaluate_trajectory(
            _run(oracle=success_oracle, case_id="b", attempt=1)
        ),
        evaluate_trajectory(
            _run(
                oracle=failure_oracle,
                case_id="b",
                attempt=2,
                status=TrajectoryTerminalStatus.FAILED,
            )
        ),
    ]

    aggregate = aggregate_evaluations(evaluations)

    assert aggregate["attempt_success_rate"] == 0.75
    assert aggregate["case_all_attempts_success_rate"] == 0.5
    pass_power = aggregate["pass_power_k"]
    assert isinstance(pass_power, dict)
    assert pass_power["1"] == {
        "eligible_cases": 2,
        "passed_cases": 2,
        "rate": 1.0,
    }
    assert pass_power["2"] == {
        "eligible_cases": 2,
        "passed_cases": 1,
        "rate": 0.5,
    }
    assert pass_power["3"]["rate"] is None


def test_trajectory_aggregate_rejects_noncontiguous_attempts():
    oracle = _oracle()
    rows = [
        evaluate_trajectory(_run(oracle=oracle, attempt=1)),
        evaluate_trajectory(_run(oracle=oracle, attempt=3)),
    ]

    with pytest.raises(ValueError, match="contiguous"):
        aggregate_evaluations(rows)


def test_trajectory_jsonl_runner_binds_digest_and_cli_output(tmp_path):
    record = {
        "case_id": "case-1",
        "attempt": 1,
        "terminal_status": "succeeded",
        "steps": [
            {
                "step_id": "step-1",
                "kind": "reply",
                "subject": "final",
                "outcome": "succeeded",
                "latency_ms": 12,
                "input_tokens": 7,
            }
        ],
        "oracle": {
            "expected_steps": [
                {
                    "kind": "reply",
                    "subjects": ["final"],
                    "outcomes": ["succeeded"],
                }
            ],
            "terminal_status": "succeeded",
        },
    }
    source = tmp_path / "runs.jsonl"
    source_bytes = (json.dumps(record) + "\n").encode()
    source.write_bytes(source_bytes)

    report = evaluate_jsonl(source)

    assert report["source_sha256"] == (
        "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    )
    assert report["aggregate"]["attempt_success_rate"] == 1.0

    output = tmp_path / "report.json"
    assert main(["--input", str(source), "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == "1"


def test_trajectory_jsonl_rejects_unknown_fields(tmp_path):
    source = tmp_path / "runs.jsonl"
    source.write_text(
        json.dumps(
            {
                "case_id": "case-1",
                "attempt": 1,
                "terminal_status": "succeeded",
                "steps": [],
                "oracle": {
                    "expected_steps": [],
                    "terminal_status": "succeeded",
                },
                "message_content": "must not be accepted",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown fields"):
        evaluate_jsonl(source)
