from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from agent.model_runtime.errors import (
    AuthenticationError,
    ContextWindowError,
    QuotaError,
    RateLimitError,
    RetryableTransportError,
    TransportError,
)


class FailureDomain(StrEnum):
    PROVIDER = "provider"
    TOOL = "tool"
    CONTEXT = "context"
    CONTROL_FLOW = "control_flow"


class FailureClass(StrEnum):
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    SERVICE_UNAVAILABLE = "service_unavailable"
    AUTHENTICATION = "authentication"
    QUOTA = "quota"
    CONTEXT_WINDOW = "context_window"
    CONTENT_SAFETY = "content_safety"
    INVALID_INPUT = "invalid_input"
    PERMISSION_DENIED = "permission_denied"
    PROTOCOL = "protocol"
    TOOL_EXECUTION = "tool_execution"
    INTERNAL = "internal"


class RecoveryAction(StrEnum):
    RETRY_SAME_PATH = "retry_same_path"
    REDUCE_CONTEXT = "reduce_context"
    CLARIFY = "clarify"
    ABORT = "abort"


@dataclass(frozen=True, slots=True)
class FailureRecord:
    domain: FailureDomain
    failure_class: FailureClass
    code: str
    fingerprint: str

    def to_dict(self) -> dict[str, str]:
        return {
            "domain": self.domain.value,
            "failure_class": self.failure_class.value,
            "code": self.code,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    action: RecoveryAction
    attempts_used: int
    max_attempts: int
    exhausted: bool
    reason: str

    @property
    def retryable(self) -> bool:
        return self.action in {
            RecoveryAction.RETRY_SAME_PATH,
            RecoveryAction.REDUCE_CONTEXT,
        }

    def to_dict(self) -> dict[str, str | int | bool]:
        return {
            "action": self.action.value,
            "attempts_used": self.attempts_used,
            "max_attempts": self.max_attempts,
            "exhausted": self.exhausted,
            "reason": self.reason,
        }


class RecoveryPolicy:
    """Map classified failures to one bounded recovery action."""

    def __init__(
        self,
        *,
        transient_max_attempts: int = 2,
        context_max_attempts: int = 7,
        same_fingerprint_limit: int = 2,
    ) -> None:
        if min(
            transient_max_attempts,
            context_max_attempts,
            same_fingerprint_limit,
        ) <= 0:
            raise ValueError("recovery limits must be positive")
        self._transient_max_attempts = transient_max_attempts
        self._context_max_attempts = context_max_attempts
        self._same_fingerprint_limit = same_fingerprint_limit

    def decide(
        self,
        failure: FailureRecord,
        *,
        attempts_used: int = 1,
        same_fingerprint_count: int = 1,
    ) -> RecoveryDecision:
        if attempts_used <= 0 or same_fingerprint_count <= 0:
            raise ValueError("recovery counters must be positive")

        action, max_attempts = self._base_action(failure.failure_class)
        can_retry = action in {
            RecoveryAction.RETRY_SAME_PATH,
            RecoveryAction.REDUCE_CONTEXT,
        }
        exhausted = can_retry and (
            attempts_used >= max_attempts
            or same_fingerprint_count >= self._same_fingerprint_limit
        )
        if exhausted:
            return RecoveryDecision(
                action=RecoveryAction.ABORT,
                attempts_used=attempts_used,
                max_attempts=max_attempts,
                exhausted=True,
                reason="recovery_budget_exhausted",
            )
        return RecoveryDecision(
            action=action,
            attempts_used=attempts_used,
            max_attempts=max_attempts,
            exhausted=False,
            reason="policy_selected",
        )

    def _base_action(self, failure_class: FailureClass) -> tuple[RecoveryAction, int]:
        if failure_class in {
            FailureClass.RATE_LIMIT,
            FailureClass.TIMEOUT,
            FailureClass.CONNECTION,
            FailureClass.SERVICE_UNAVAILABLE,
        }:
            return RecoveryAction.RETRY_SAME_PATH, self._transient_max_attempts
        if failure_class is FailureClass.CONTEXT_WINDOW:
            return RecoveryAction.REDUCE_CONTEXT, self._context_max_attempts
        if failure_class is FailureClass.INVALID_INPUT:
            return RecoveryAction.CLARIFY, 1
        return RecoveryAction.ABORT, 1


def classify_exception(
    exc: Exception,
    *,
    domain: FailureDomain,
    code: str | None = None,
) -> FailureRecord:
    """Classify a known boundary exception without retaining its message."""

    failure_class, default_code = _exception_class(exc, domain=domain)
    return _build_record(
        domain=domain,
        failure_class=failure_class,
        code=code or default_code,
        source_type=type(exc).__qualname__,
    )


def failure_for_condition(
    *,
    domain: FailureDomain,
    failure_class: FailureClass,
    code: str,
) -> FailureRecord:
    """Create a failure for a non-exception terminal condition."""

    return _build_record(
        domain=domain,
        failure_class=failure_class,
        code=code,
        source_type="condition",
    )


def _exception_class(
    exc: Exception,
    *,
    domain: FailureDomain,
) -> tuple[FailureClass, str]:
    from agent.provider import (
        ContentSafetyError,
        ContextLengthError,
        LLMNetworkTimeoutError,
    )

    if isinstance(exc, RateLimitError) or _is_openai_exception(exc, "RateLimitError"):
        return FailureClass.RATE_LIMIT, "provider_rate_limited"
    if isinstance(exc, (LLMNetworkTimeoutError, TimeoutError)) or _is_openai_exception(
        exc, "APITimeoutError"
    ):
        return FailureClass.TIMEOUT, f"{domain.value}_timeout"
    if isinstance(exc, (RetryableTransportError, ConnectionError)) or _is_openai_exception(
        exc, "APIConnectionError"
    ):
        return FailureClass.CONNECTION, f"{domain.value}_connection_error"
    if isinstance(exc, AuthenticationError) or _is_openai_exception(
        exc, "AuthenticationError"
    ):
        return FailureClass.AUTHENTICATION, "provider_auth_error"
    if isinstance(exc, QuotaError):
        return FailureClass.QUOTA, "provider_quota_error"
    if isinstance(exc, (ContextLengthError, ContextWindowError)):
        return FailureClass.CONTEXT_WINDOW, "context_window_exceeded"
    if isinstance(exc, ContentSafetyError):
        return FailureClass.CONTENT_SAFETY, "content_safety"
    if isinstance(exc, PermissionError) or _is_openai_exception(
        exc, "PermissionDeniedError"
    ):
        return FailureClass.PERMISSION_DENIED, f"{domain.value}_permission_denied"
    if _is_openai_exception(exc, "APIStatusError"):
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int) and status_code >= 500:
            return FailureClass.SERVICE_UNAVAILABLE, "provider_service_unavailable"
        return FailureClass.PROTOCOL, "provider_rejected"
    if isinstance(exc, TransportError):
        return FailureClass.PROTOCOL, "provider_transport_error"
    if isinstance(exc, (TypeError, ValueError)):
        return FailureClass.INVALID_INPUT, f"{domain.value}_invalid_input"
    if domain is FailureDomain.TOOL:
        return FailureClass.TOOL_EXECUTION, "tool_execution_error"
    return FailureClass.INTERNAL, f"{domain.value}_internal_error"


def _is_openai_exception(exc: Exception, class_name: str) -> bool:
    return any(
        candidate.__name__ == class_name
        and candidate.__module__.split(".", 1)[0] == "openai"
        for candidate in type(exc).__mro__
    )


def _build_record(
    *,
    domain: FailureDomain,
    failure_class: FailureClass,
    code: str,
    source_type: str,
) -> FailureRecord:
    normalized_code = code.strip()
    if not normalized_code:
        raise ValueError("failure code must not be empty")
    raw = "\0".join(
        (
            domain.value,
            failure_class.value,
            normalized_code,
            source_type,
        )
    )
    return FailureRecord(
        domain=domain,
        failure_class=failure_class,
        code=normalized_code,
        fingerprint=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
    )
