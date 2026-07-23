from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from agent.delivery.supervisor import DeliverySupervisor
from bus.events import OutboundMessage
from bus.queue import MessageBus
from session.outbox_repository import OutboxRepository
from session.reliability_records import OutboundIntentDraft


@dataclass
class OutboundDispatch:
    channel: str
    chat_id: str
    content: str
    thinking: str | None = None
    metadata: dict[str, object] = field(default_factory=dict[str, object])
    media: list[str] = field(default_factory=list[str])
    session_message_id: str | None = None


class OutboundPort(Protocol):
    async def dispatch(self, outbound: OutboundDispatch) -> bool: ...


class BusOutboundPort:
    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus

    async def dispatch(self, outbound: OutboundDispatch) -> bool:
        await self._bus.publish_outbound(
            OutboundMessage(
                channel=outbound.channel,
                chat_id=outbound.chat_id,
                content=outbound.content,
                thinking=outbound.thinking,
                metadata=dict(outbound.metadata),
                media=list(outbound.media),
                session_message_id=outbound.session_message_id,
            )
        )
        return True


class DurableOutboundPort:
    def __init__(
        self,
        repository: OutboxRepository,
        supervisor: DeliverySupervisor,
    ) -> None:
        self._repository = repository
        self._supervisor = supervisor

    async def dispatch(self, outbound: OutboundDispatch) -> bool:
        delivery_id = outbound.metadata.get("delivery_id")
        if not isinstance(delivery_id, str) or not delivery_id:
            raise RuntimeError("durable outbound requires a persisted delivery_id")
        record = self._repository.get(delivery_id)
        if record is None:
            raise RuntimeError(f"durable outbound is not persisted: {delivery_id}")
        if (
            record.channel != outbound.channel
            or record.chat_id != outbound.chat_id
            or record.content != outbound.content
            or record.media != tuple(outbound.media)
            or record.session_message_id != outbound.session_message_id
        ):
            raise RuntimeError(f"durable outbound payload mismatch: {delivery_id}")
        terminal = await self._supervisor.wait_for_terminal(delivery_id)
        return terminal.status == "sent"

    async def submit_standalone(
        self,
        outbound: OutboundDispatch,
        *,
        session_key: str,
        turn_id: str | None,
        reason_code: str,
        lane: str = "system",
    ) -> bool:
        delivery_id = uuid4().hex
        metadata = dict(outbound.metadata)
        metadata["delivery_id"] = delivery_id
        _ = self._repository.enqueue(
            OutboundIntentDraft(
                delivery_id=delivery_id,
                idempotency_key=f"standalone:{delivery_id}",
                turn_id=turn_id,
                session_key=session_key,
                session_message_id=outbound.session_message_id,
                channel=outbound.channel,
                chat_id=outbound.chat_id,
                content=outbound.content,
                thinking=outbound.thinking,
                media=tuple(outbound.media),
                metadata=metadata,
                lane=lane,  # type: ignore[arg-type]
                reason_code=reason_code,
            )
        )
        terminal = await self._supervisor.wait_for_terminal(delivery_id)
        return terminal.status == "sent"


class PushToolOutboundPort:
    def __init__(self, push_tool: Any) -> None:
        self._push = push_tool

    async def dispatch(self, outbound: OutboundDispatch) -> bool:
        message = outbound.content.strip()
        channel = outbound.channel.strip()
        chat_id = outbound.chat_id.strip()
        media = [item.strip() for item in outbound.media if item.strip()]
        if (not message and not media) or not channel or not chat_id:
            return False
        result = await self._push.execute(
            channel=channel,
            chat_id=chat_id,
            message=message,
            image=media[0] if media else None,
            _outbound_metadata=dict(outbound.metadata),
        )
        for image in media[1:]:
            result = await self._push.execute(
                channel=channel,
                chat_id=chat_id,
                image=image,
            )
        return "已发送" in str(result)
