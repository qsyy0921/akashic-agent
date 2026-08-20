"""Core-owned reliability contracts."""

from agent.reliability.failures import (
    FailureClass,
    FailureDomain,
    FailureRecord,
    RecoveryAction,
    RecoveryDecision,
    RecoveryPolicy,
    classify_exception,
    failure_for_condition,
)

__all__ = [
    "FailureClass",
    "FailureDomain",
    "FailureRecord",
    "RecoveryAction",
    "RecoveryDecision",
    "RecoveryPolicy",
    "classify_exception",
    "failure_for_condition",
]
