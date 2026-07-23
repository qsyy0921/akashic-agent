from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.context import ContextBuilder
from agent.core.passive_turn import AgentCore, AgentCoreDeps, ContextStore, Reasoner
from agent.core.runtime_support import TurnRunResult
from agent.core.types import ContextBundle
from agent.delivery.supervisor import DeliverySupervisor
from agent.looping.ports import SessionServices
from agent.tools.registry import ToolRegistry
from agent.turns.orchestrator import TurnOrchestrator, TurnOrchestratorDeps
from agent.turns.outbound import DurableOutboundPort
from agent.turns.result import TurnOutbound, TurnResult
from bus.events import InboundMessage
from bus.events import OutboundMessage
from bus.queue import MessageBus
from session.manager import SessionManager
from session.outbox_repository import OutboxRepository, OutboxStateError
from session.reliability_records import OutboundIntentDraft


def _draft(
    delivery_id: str = "delivery-1",
    *,
    channel: str = "telegram",
    lane: str = "passive",
) -> OutboundIntentDraft:
    return OutboundIntentDraft(
        delivery_id=delivery_id,
        idempotency_key=f"intent:{delivery_id}",
        turn_id="turn-1",
        session_key="telegram:42",
        channel=channel,
        chat_id="42",
        content="reply",
        media=("image.png",),
        metadata={"source": "test"},
        lane=lane,  # type: ignore[arg-type]
        reason_code="passive_reply",
    )


@pytest.mark.asyncio
async def test_production_bus_can_fail_closed_on_ephemeral_outbound():
    bus = MessageBus()
    bus.disable_ephemeral_outbound()

    with pytest.raises(RuntimeError, match="persist an outbox intent"):
        await bus.publish_outbound(
            OutboundMessage(channel="telegram", chat_id="42", content="legacy")
        )


@pytest.mark.asyncio
async def test_messages_and_outbox_commit_atomically(tmp_path):
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("telegram:42")
    user = session.add_message("user", "hello", client_message_id="client-1")
    assistant = session.add_message("assistant", "reply", delivery_id="delivery-1")

    record = await manager.append_messages_with_outbound(
        session,
        [user, assistant],
        _draft(),
    )

    assert record.status == "pending"
    assert record.session_message_id == assistant["id"]
    assert record.metadata["persisted_user_message_id"] == user["id"]
    assert record.metadata["client_message_id"] == "client-1"
    manager.close()


@pytest.mark.asyncio
async def test_outbox_insert_fault_rolls_back_messages_and_memory(tmp_path):
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("telegram:42")
    user = session.add_message("user", "hello")
    assistant = session.add_message("assistant", "reply")
    with manager.control_store._lock, manager.control_store._conn:
        manager.control_store._conn.execute(
            """
            CREATE TRIGGER reject_test_outbox
            BEFORE INSERT ON reliability_outbox
            BEGIN SELECT RAISE(ABORT, 'injected outbox failure'); END
            """
        )

    with pytest.raises(sqlite3.IntegrityError):
        await manager.append_messages_with_outbound(
            session,
            [user, assistant],
            _draft(),
        )

    assert manager.control_store.fetch_session_messages(session.key) == []
    assert session.messages == []
    manager.close()


@pytest.mark.asyncio
async def test_supervisor_delivers_once_and_duplicate_notify_is_harmless(tmp_path):
    manager = SessionManager(tmp_path)
    repository = OutboxRepository(manager.control_store)
    _ = repository.enqueue(_draft())
    bus = MessageBus()
    received: list[OutboundMessage] = []

    async def send(message: OutboundMessage) -> None:
        received.append(message)

    bus.subscribe_outbound("telegram", send)
    supervisor = DeliverySupervisor(repository, bus)

    assert await supervisor.run_once() is True
    supervisor.notify()
    supervisor.notify()
    assert await supervisor.run_once() is False
    assert len(received) == 1
    assert repository.get("delivery-1").status == "sent"  # type: ignore[union-attr]
    assert len(repository.list_attempts("delivery-1")) == 1
    manager.close()


@pytest.mark.asyncio
async def test_no_subscriber_is_known_failure_without_retry(tmp_path):
    manager = SessionManager(tmp_path)
    repository = OutboxRepository(manager.control_store)
    _ = repository.enqueue(_draft())
    supervisor = DeliverySupervisor(repository, MessageBus())

    assert await supervisor.run_once() is True
    assert await supervisor.run_once() is False
    record = repository.get("delivery-1")
    assert record is not None
    assert record.status == "failed"
    assert record.last_error_code == "channel_not_registered"
    assert record.attempt_count == 1
    manager.close()


@pytest.mark.asyncio
async def test_callback_exception_is_unknown_until_explicit_reconciliation(tmp_path):
    manager = SessionManager(tmp_path)
    repository = OutboxRepository(manager.control_store)
    _ = repository.enqueue(_draft())
    bus = MessageBus()
    calls = 0

    async def ambiguous_send(_message: OutboundMessage) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("connection lost after possible send")

    bus.subscribe_outbound("telegram", ambiguous_send)
    supervisor = DeliverySupervisor(repository, bus)

    assert await supervisor.run_once() is True
    record = repository.get("delivery-1")
    assert record is not None and record.status == "unknown"
    assert calls == 1
    with pytest.raises(OutboxStateError):
        repository.requeue_failed(
            "delivery-1",
            observed_at=datetime.now(UTC),
            reason="operator retry",
        )
    _ = repository.confirm_unknown_absent(
        "delivery-1",
        observed_at=datetime.now(UTC),
        reason="telegram history checked",
    )
    requeued = repository.requeue_failed(
        "delivery-1",
        observed_at=datetime.now(UTC),
        reason="confirmed absent",
    )
    assert requeued.status == "pending"
    manager.close()


def test_restart_reconciles_inflight_as_unknown(tmp_path):
    manager = SessionManager(tmp_path)
    repository = OutboxRepository(manager.control_store)
    _ = repository.enqueue(_draft())
    now = datetime.now(UTC)
    claimed = repository.claim_next(
        "worker-before-crash",
        now=now,
        lease_expires_at=now + timedelta(minutes=2),
    )
    assert claimed is not None and claimed.status == "sending"
    manager.close()

    restarted_manager = SessionManager(tmp_path)
    restarted_repository = OutboxRepository(restarted_manager.control_store)
    assert restarted_repository.reconcile_abandoned_sending(
        observed_at=datetime.now(UTC)
    ) == 1
    record = restarted_repository.get("delivery-1")
    assert record is not None
    assert record.status == "unknown"
    assert record.last_error_code == "process_restarted_during_send"
    restarted_manager.close()


@pytest.mark.asyncio
async def test_supervisor_run_wakes_and_waits_for_terminal(tmp_path):
    manager = SessionManager(tmp_path)
    repository = OutboxRepository(manager.control_store)
    _ = repository.enqueue(_draft())
    bus = MessageBus()
    bus.subscribe_outbound("telegram", lambda _message: asyncio.sleep(0))
    supervisor = DeliverySupervisor(repository, bus, poll_interval_seconds=10)
    task = asyncio.create_task(supervisor.run())
    await supervisor.wait_until_ready()
    try:
        record = await supervisor.wait_for_terminal("delivery-1")
        assert record.status == "sent"
    finally:
        supervisor.stop()
        await task
        manager.close()


@pytest.mark.asyncio
async def test_passive_pipeline_commits_before_real_delivery(tmp_path):
    manager = SessionManager(tmp_path)
    repository = OutboxRepository(manager.control_store)
    bus = MessageBus()
    supervisor = DeliverySupervisor(repository, bus)
    port = DurableOutboundPort(repository, supervisor)
    observed: list[tuple[str, int, str]] = []

    async def channel_send(message: OutboundMessage) -> None:
        delivery_id = cast(str, message.metadata["delivery_id"])
        persisted = manager.control_store.fetch_session_messages("telegram:42")
        record = repository.get(delivery_id)
        assert record is not None
        observed.append((message.content, len(persisted), record.status))

    bus.subscribe_outbound("telegram", channel_send)
    context = SimpleNamespace(
        render=MagicMock(
            return_value=SimpleNamespace(system_prompt="system", messages=[])
        ),
        last_debug_breakdown=[],
    )
    agent_core = AgentCore(
        AgentCoreDeps(
            session=SessionServices(session_manager=manager, presence=None),
            context_store=cast(
                ContextStore,
                SimpleNamespace(
                    prepare=AsyncMock(return_value=ContextBundle()),
                ),
            ),
            context=cast(ContextBuilder, context),
            tools=cast(ToolRegistry, SimpleNamespace(set_context=MagicMock())),
            reasoner=cast(
                Reasoner,
                SimpleNamespace(
                    run_turn=AsyncMock(
                        return_value=TurnRunResult(
                            reply="durable reply",
                            media=["image.png"],
                        )
                    )
                ),
            ),
            outbound_port=port,
        )
    )
    task = asyncio.create_task(supervisor.run())
    await supervisor.wait_until_ready()
    try:
        result = await agent_core.process(
            InboundMessage("telegram", "user", "42", "hello"),
            "telegram:42",
            dispatch_outbound=True,
        )
        assert result.content == "durable reply"
        assert observed == [("durable reply", 2, "sending")]
        sent = repository.list_by_status("sent")
        assert len(sent) == 1
        assert sent[0].session_message_id == "telegram:42:1"
    finally:
        supervisor.stop()
        await task
        manager.close()


@pytest.mark.asyncio
async def test_proactive_pipeline_commits_before_real_delivery(tmp_path):
    manager = SessionManager(tmp_path)
    repository = OutboxRepository(manager.control_store)
    bus = MessageBus()
    supervisor = DeliverySupervisor(repository, bus)
    port = DurableOutboundPort(repository, supervisor)
    observed: list[tuple[int, str]] = []

    async def channel_send(message: OutboundMessage) -> None:
        delivery_id = cast(str, message.metadata["delivery_id"])
        persisted = manager.control_store.fetch_session_messages("telegram:42")
        record = repository.get(delivery_id)
        assert record is not None
        observed.append((len(persisted), record.status))

    bus.subscribe_outbound("telegram", channel_send)
    orchestrator = TurnOrchestrator(
        TurnOrchestratorDeps(
            session=SessionServices(session_manager=manager, presence=None),
            outbound=port,
        )
    )
    task = asyncio.create_task(supervisor.run())
    await supervisor.wait_until_ready()
    try:
        sent = await orchestrator.handle_proactive_turn(
            result=TurnResult(
                decision="reply",
                outbound=TurnOutbound(
                    session_key="telegram:42",
                    content="proactive durable reply",
                ),
            ),
            session_key="telegram:42",
            channel="telegram",
            chat_id="42",
        )
        assert sent is True
        assert observed == [(1, "sending")]
        assert repository.list_by_status("sent")[0].lane == "proactive"
    finally:
        supervisor.stop()
        await task
        manager.close()
