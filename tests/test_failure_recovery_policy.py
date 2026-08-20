from __future__ import annotations

from agent.model_runtime.errors import AuthenticationError, ContextWindowError, RateLimitError
from agent.reliability.failures import (
    FailureClass,
    FailureDomain,
    RecoveryAction,
    RecoveryPolicy,
    classify_exception,
)


def test_transient_failure_retries_once_then_exhausts() -> None:
    failure = classify_exception(
        RateLimitError("secret provider detail"),
        domain=FailureDomain.PROVIDER,
    )
    policy = RecoveryPolicy(
        transient_max_attempts=2,
        same_fingerprint_limit=2,
    )

    first = policy.decide(failure, attempts_used=1, same_fingerprint_count=1)
    exhausted = policy.decide(failure, attempts_used=2, same_fingerprint_count=2)

    assert failure.failure_class is FailureClass.RATE_LIMIT
    assert failure.code == "provider_rate_limited"
    assert first.action is RecoveryAction.RETRY_SAME_PATH
    assert first.retryable is True
    assert exhausted.action is RecoveryAction.ABORT
    assert exhausted.retryable is False
    assert exhausted.exhausted is True


def test_non_retryable_failures_never_gain_retry_action() -> None:
    policy = RecoveryPolicy()
    cases = (
        (AuthenticationError("bad key"), FailureDomain.PROVIDER, RecoveryAction.ABORT),
        (PermissionError("blocked"), FailureDomain.TOOL, RecoveryAction.ABORT),
        (ValueError("bad input"), FailureDomain.TOOL, RecoveryAction.CLARIFY),
    )

    for error, domain, expected_action in cases:
        failure = classify_exception(error, domain=domain)
        decision = policy.decide(failure)
        assert decision.action is expected_action
        assert decision.retryable is False


def test_context_window_uses_reduced_context_not_provider_fallback() -> None:
    failure = classify_exception(
        ContextWindowError("too long"),
        domain=FailureDomain.CONTEXT,
    )

    decision = RecoveryPolicy().decide(failure)

    assert failure.failure_class is FailureClass.CONTEXT_WINDOW
    assert decision.action is RecoveryAction.REDUCE_CONTEXT
    assert decision.retryable is True


def test_failure_fingerprint_does_not_include_exception_message() -> None:
    left = classify_exception(
        TimeoutError("token=first-secret"),
        domain=FailureDomain.TOOL,
    )
    right = classify_exception(
        TimeoutError("token=second-secret"),
        domain=FailureDomain.TOOL,
    )

    assert left.fingerprint == right.fingerprint
    assert "secret" not in str(left.to_dict())


def test_unknown_tool_exception_aborts_as_tool_execution_failure() -> None:
    failure = classify_exception(
        RuntimeError("unexpected"),
        domain=FailureDomain.TOOL,
    )

    decision = RecoveryPolicy().decide(failure)

    assert failure.failure_class is FailureClass.TOOL_EXECUTION
    assert decision.action is RecoveryAction.ABORT


def test_optional_openai_exception_is_classified_without_importing_sdk() -> None:
    external_type = type(
        "APIStatusError",
        (Exception,),
        {"__module__": "openai._exceptions"},
    )
    external = external_type("unavailable")
    external.status_code = 503

    failure = classify_exception(external, domain=FailureDomain.PROVIDER)

    assert failure.failure_class is FailureClass.SERVICE_UNAVAILABLE
    assert failure.code == "provider_service_unavailable"
