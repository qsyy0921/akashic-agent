from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from bus.events import OutboundMessage
from bus.queue import MessageBus, NoOutboundSubscriberError
from session.outbox_repository import OutboxRepository
from session.reliability_records import OutboxRecord

logger = logging.getLogger(__name__)


class DeliverySupervisor:
    """Single-host outbox worker. It never retries a claimed attempt."""

    def __init__(
        self,
        repository: OutboxRepository,
        bus: MessageBus,
        *,
        worker_id: str | None = None,
        send_timeout_seconds: float = 90.0,
        poll_interval_seconds: float = 0.25,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if send_timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("delivery timeouts must be positive")
        self.repository = repository
        self._bus = bus
        self._worker_id = worker_id or f"delivery-{uuid4().hex}"
        self._send_timeout_seconds = float(send_timeout_seconds)
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._wake = asyncio.Event()
        self._running = False
        self._ready = asyncio.Event()
        self._completion_events: dict[str, asyncio.Event] = {}

    @property
    def running(self) -> bool:
        return self._running

    async def run(self) -> None:
        if self._running:
            raise RuntimeError("delivery supervisor already running")
        self._running = True
        try:
            recovered = self.repository.reconcile_abandoned_sending(
                observed_at=self._clock()
            )
            if recovered:
                logger.warning(
                    "delivery startup classified abandoned attempts as unknown count=%d",
                    recovered,
                )
            self._ready.set()
            while self._running:
                if await self.run_once():
                    continue
                self._wake.clear()
                try:
                    await asyncio.wait_for(
                        self._wake.wait(),
                        timeout=self._poll_interval_seconds,
                    )
                except TimeoutError:
                    pass
        finally:
            self._running = False
            self._ready.clear()

    def stop(self) -> None:
        self._running = False
        self._wake.set()

    def notify(self) -> None:
        self._wake.set()

    async def wait_until_ready(self) -> None:
        await self._ready.wait()

    async def wait_for_terminal(self, delivery_id: str) -> OutboxRecord:
        event = self._completion_events.setdefault(delivery_id, asyncio.Event())
        self.notify()
        timeout = self._send_timeout_seconds + 10.0
        try:
            while True:
                record = self.repository.get(delivery_id)
                if record is None:
                    raise RuntimeError(f"delivery does not exist: {delivery_id}")
                if record.status in {
                    "sent",
                    "failed",
                    "unknown",
                    "cancelled",
                }:
                    return record
                event.clear()
                await asyncio.wait_for(event.wait(), timeout=timeout)
        finally:
            if self._completion_events.get(delivery_id) is event:
                self._completion_events.pop(delivery_id, None)

    async def run_once(self) -> bool:
        now = self._clock()
        record = self.repository.claim_next(
            self._worker_id,
            now=now,
            lease_expires_at=now
            + timedelta(seconds=self._send_timeout_seconds + 5.0),
        )
        if record is None:
            return False
        try:
            await self._deliver(record)
        finally:
            event = self._completion_events.get(record.delivery_id)
            if event is not None:
                event.set()
        return True

    async def _deliver(self, record: OutboxRecord) -> None:
        message = OutboundMessage(
            channel=record.channel,
            chat_id=record.chat_id,
            content=record.content,
            thinking=record.thinking,
            media=list(record.media),
            metadata=dict(record.metadata),
            control_turn_id=record.turn_id,
            session_message_id=record.session_message_id,
        )
        try:
            await asyncio.wait_for(
                self._bus.deliver_outbound_once(message, lane=record.lane),
                timeout=self._send_timeout_seconds,
            )
        except asyncio.CancelledError:
            self.repository.mark_unknown(
                record.delivery_id,
                record.attempt_count,
                observed_at=self._clock(),
                error_code="send_cancelled",
            )
            raise
        except NoOutboundSubscriberError:
            self.repository.mark_failed(
                record.delivery_id,
                record.attempt_count,
                observed_at=self._clock(),
                error_code="channel_not_registered",
            )
        except TimeoutError:
            self.repository.mark_unknown(
                record.delivery_id,
                record.attempt_count,
                observed_at=self._clock(),
                error_code="send_timeout",
            )
        except Exception:
            logger.exception(
                "delivery callback raised delivery_id=%s channel=%s",
                record.delivery_id,
                record.channel,
            )
            self.repository.mark_unknown(
                record.delivery_id,
                record.attempt_count,
                observed_at=self._clock(),
                error_code="send_exception",
            )
        else:
            self.repository.mark_sent(
                record.delivery_id,
                record.attempt_count,
                observed_at=self._clock(),
            )

