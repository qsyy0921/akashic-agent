"""控制面领域错误。"""

from agent.reliability.failures import FailureRecord, RecoveryDecision


class ControlError(RuntimeError):
    """表示可安全映射到协议边界的控制面错误。"""


class ThreadNotFoundError(ControlError):
    pass


class ThreadBusyError(ControlError):
    pass


class TurnNotFoundError(ControlError):
    pass


class TurnStateTransitionError(ControlError):
    pass


class SlowConsumerError(ControlError):
    pass


class RuntimeClosedError(ControlError):
    pass


class ControlExecutionError(ControlError):
    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        retryable: bool,
        failure: FailureRecord | None = None,
        recovery: RecoveryDecision | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable
        self.failure = failure
        self.recovery = recovery

    @classmethod
    def from_failure(
        cls,
        failure: FailureRecord,
        recovery: RecoveryDecision,
        message: str,
    ) -> "ControlExecutionError":
        return cls(
            failure.code,
            message,
            retryable=recovery.retryable,
            failure=failure,
            recovery=recovery,
        )
