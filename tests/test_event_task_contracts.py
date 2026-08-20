from datetime import UTC, datetime, timedelta

import pytest

from agent.background.state import (
    AsyncTaskState,
    AsyncTaskStatus,
    AsyncTaskTransitionError,
)
from bus.contracts import EventEnvelope
from bus.events import SpawnCompletionItem
from bus.internal_events import SpawnCompletionEvent


def test_event_envelope_create_normalizes_timestamp_and_exposes_header():
    payload = {"status": "ok"}
    envelope = EventEnvelope.create(
        event_type="agent.background.completed",
        source="agent.background",
        subject_kind="agent.task",
        subject_id="job-1",
        payload=payload,
        correlation_id="telegram:42",
        occurred_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
    )

    assert envelope.payload is payload
    assert envelope.event_id.startswith("event:")
    assert envelope.header()["occurred_at"] == "2026-08-19T12:00:00Z"
    assert "payload" not in envelope.header()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_type", "completed"),
        ("source", "Agent.Background"),
        ("subject_kind", "agent task"),
        ("subject_id", ""),
    ],
)
def test_event_envelope_rejects_invalid_identity(field: str, value: str):
    values = {
        "event_type": "agent.background.completed",
        "source": "agent.background",
        "subject_kind": "agent.task",
        "subject_id": "job-1",
    }
    values[field] = value

    with pytest.raises(ValueError):
        EventEnvelope.create(payload={}, **values)


def test_event_envelope_rejects_naive_timestamp():
    with pytest.raises(ValueError, match="timezone"):
        EventEnvelope.create(
            event_type="agent.background.completed",
            source="agent.background",
            subject_kind="agent.task",
            subject_id="job-1",
            payload={},
            occurred_at=datetime(2026, 8, 19, 12),
        )


def test_async_task_state_round_trip_and_terminal_transition():
    accepted_at = datetime(2026, 8, 19, 12, tzinfo=UTC)
    accepted = AsyncTaskState.accepted(
        task_id="job-1",
        task_kind="agent.conversation_spawn",
        attempt=2,
        now=accepted_at,
    )
    running = accepted.transition(
        AsyncTaskStatus.RUNNING,
        now=accepted_at + timedelta(seconds=1),
        reason_code="scheduled",
    )
    succeeded = running.transition(
        AsyncTaskStatus.SUCCEEDED,
        now=accepted_at + timedelta(seconds=2),
        reason_code="completed",
    )

    restored = AsyncTaskState.from_dict(succeeded.to_dict())
    assert restored == succeeded
    assert restored.status.is_terminal
    with pytest.raises(AsyncTaskTransitionError):
        restored.transition(AsyncTaskStatus.FAILED, error_code="late_failure")


def test_async_task_state_rejects_failed_without_error_and_time_regression():
    accepted_at = datetime(2026, 8, 19, 12, tzinfo=UTC)
    accepted = AsyncTaskState.accepted(
        task_id="job-1",
        task_kind="agent.conversation_spawn",
        now=accepted_at,
    )
    with pytest.raises(ValueError, match="error code"):
        accepted.transition(
            AsyncTaskStatus.FAILED,
            now=accepted_at + timedelta(seconds=1),
        )
    running = accepted.transition(
        AsyncTaskStatus.RUNNING,
        now=accepted_at + timedelta(seconds=2),
    )
    with pytest.raises(ValueError, match="precedes current state"):
        running.transition(
            AsyncTaskStatus.CANCELLED,
            now=accepted_at + timedelta(seconds=1),
        )


def test_spawn_completion_item_rejects_mismatched_envelope():
    event = SpawnCompletionEvent(
        job_id="job-1",
        label="job",
        task="work",
        status="completed",
        exit_reason="completed",
        result="done",
    )
    state = AsyncTaskState.accepted(
        task_id="job-1",
        task_kind="agent.conversation_spawn",
    ).transition(AsyncTaskStatus.CANCELLED)
    envelope = EventEnvelope.create(
        event_type="agent.background.completed",
        source="agent.background",
        subject_kind="agent.task",
        subject_id="another-job",
        payload=event,
    )

    with pytest.raises(ValueError, match="subject id"):
        SpawnCompletionItem(
            channel="telegram",
            chat_id="42",
            event=event,
            task_state=state,
            envelope=envelope,
        )
