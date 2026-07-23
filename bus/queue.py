import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

from bus.events import InboundItem, OutboundMessage

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class NoOutboundSubscriberError(RuntimeError):
    pass


@dataclass
class _ChatLaneState:
    condition: asyncio.Condition
    active_users: int = 0
    passive_turns: int = 0
    passive_sends: int = 0
    next_non_passive_ticket: int = 0
    serving_non_passive_ticket: int = 0
    cancelled_non_passive_tickets: set[int] = field(
        default_factory=lambda: set[int]()
    )
    sending: bool = False


class ChatLane:
    def __init__(self) -> None:
        self._states: dict[tuple[str, str], _ChatLaneState] = {}

    def _acquire_state(
        self,
        channel: str,
        chat_id: str,
    ) -> tuple[tuple[str, str], _ChatLaneState]:
        key = (str(channel), str(chat_id))
        state = self._states.get(key)
        if state is None:
            state = _ChatLaneState(condition=asyncio.Condition())
            self._states[key] = state
        state.active_users += 1
        return key, state

    def _release_state(
        self,
        key: tuple[str, str],
        state: _ChatLaneState,
    ) -> None:
        state.active_users -= 1
        if (
            state.active_users
            or state.passive_turns
            or state.passive_sends
            or state.sending
            or state.next_non_passive_ticket != state.serving_non_passive_ticket
            or state.cancelled_non_passive_tickets
        ):
            return
        if self._states.get(key) is state:
            del self._states[key]

    def _skip_cancelled_non_passive(self, state: _ChatLaneState) -> None:
        while state.serving_non_passive_ticket in state.cancelled_non_passive_tickets:
            state.cancelled_non_passive_tickets.remove(
                state.serving_non_passive_ticket
            )
            state.serving_non_passive_ticket += 1

    async def mark_passive_pending(self, channel: str, chat_id: str) -> None:
        key, state = self._acquire_state(channel, chat_id)
        try:
            async with state.condition:
                state.passive_turns += 1
                state.condition.notify_all()
        finally:
            self._release_state(key, state)

    async def mark_passive_done(self, channel: str, chat_id: str) -> None:
        key, state = self._acquire_state(channel, chat_id)
        try:
            async with state.condition:
                if state.passive_turns > 0:
                    state.passive_turns -= 1
                state.condition.notify_all()
        finally:
            self._release_state(key, state)

    async def mark_passive_send_pending(self, channel: str, chat_id: str) -> None:
        key, state = self._acquire_state(channel, chat_id)
        try:
            async with state.condition:
                state.passive_sends += 1
                state.condition.notify_all()
        finally:
            self._release_state(key, state)

    async def run_passive(
        self,
        channel: str,
        chat_id: str,
        send: Callable[[], Awaitable[_T]],
    ) -> _T:
        key, state = self._acquire_state(channel, chat_id)
        try:
            async with state.condition:
                while state.sending:
                    _ = await state.condition.wait()
                state.sending = True
            try:
                return await send()
            finally:
                async with state.condition:
                    if state.passive_sends > 0:
                        state.passive_sends -= 1
                    state.sending = False
                    state.condition.notify_all()
        finally:
            self._release_state(key, state)

    async def run_non_passive(
        self,
        channel: str,
        chat_id: str,
        send: Callable[[], Awaitable[_T]],
    ) -> _T:
        key, state = self._acquire_state(channel, chat_id)
        ticket = -1
        sending = False
        try:
            try:
                async with state.condition:
                    ticket = state.next_non_passive_ticket
                    state.next_non_passive_ticket += 1
                    self._skip_cancelled_non_passive(state)
                    while (
                        state.sending
                        or state.passive_turns > 0
                        or state.passive_sends > 0
                        or ticket != state.serving_non_passive_ticket
                    ):
                        _ = await state.condition.wait()
                        self._skip_cancelled_non_passive(state)
                    state.sending = True
                    sending = True
                return await send()
            finally:
                async with state.condition:
                    if ticket >= 0:
                        if sending:
                            state.serving_non_passive_ticket += 1
                            state.sending = False
                        else:
                            state.cancelled_non_passive_tickets.add(ticket)
                        self._skip_cancelled_non_passive(state)
                    state.condition.notify_all()
        finally:
            self._release_state(key, state)


class OutboundSubscription:
    def __init__(
        self,
        bus: "MessageBus",
        channel: str,
        callback: Callable[[OutboundMessage], Awaitable[None]],
    ) -> None:
        self._bus = bus
        self._channel = channel
        self._callback = callback
        self._active = True

    def close(self) -> None:
        if not self._active:
            return
        self._active = False
        self._bus.unsubscribe_outbound(self._channel, self._callback)


class MessageBus:
    """agent 与各 channel 之间的异步消息总线"""

    def __init__(self, chat_lane: ChatLane | None = None) -> None:
        self._inbound: asyncio.Queue[InboundItem] = asyncio.Queue()
        self._outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self._subscribers: dict[
            str, list[Callable[[OutboundMessage], Awaitable[None]]]
        ] = {}
        self._chat_lane = chat_lane or ChatLane()
        self._running = False
        self._ephemeral_outbound_enabled = True
        self._delivery_observer: (
            Callable[[OutboundMessage, bool], Awaitable[None]] | None
        ) = None

    def bind_outbound_delivery_observer(
        self,
        callback: Callable[[OutboundMessage, bool], Awaitable[None]],
    ) -> None:
        """绑定唯一出站送达观察者。"""

        if self._delivery_observer is not None:
            raise RuntimeError("outbound delivery observer 已绑定")
        self._delivery_observer = callback

    async def publish_inbound(self, msg: InboundItem) -> None:
        """将渠道输入交给 Agent 消费。"""
        await self._chat_lane.mark_passive_pending(msg.channel, msg.chat_id)
        await self._inbound.put(msg)

    async def consume_inbound(self) -> InboundItem:
        """阻塞直到有消息可消费"""
        return await self._inbound.get()

    async def complete_inbound(self, msg: InboundItem) -> None:
        await self._chat_lane.mark_passive_done(msg.channel, msg.chat_id)

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """将 Agent 输出交给对应渠道发送。"""
        if not self._ephemeral_outbound_enabled:
            raise RuntimeError(
                "ephemeral outbound is disabled; persist an outbox intent instead"
            )
        await self._chat_lane.mark_passive_send_pending(msg.channel, msg.chat_id)
        await self._outbound.put(msg)

    def disable_ephemeral_outbound(self) -> None:
        if self._running or not self._outbound.empty():
            raise RuntimeError("cannot disable an active ephemeral outbound queue")
        self._ephemeral_outbound_enabled = False

    def subscribe_outbound(
        self,
        channel: str,
        callback: Callable[[OutboundMessage], Awaitable[None]],
    ) -> OutboundSubscription:
        """订阅某 channel 的出站消息"""
        self._subscribers.setdefault(channel, []).append(callback)
        return OutboundSubscription(self, channel, callback)

    def unsubscribe_outbound(
        self,
        channel: str,
        callback: Callable[[OutboundMessage], Awaitable[None]],
    ) -> None:
        callbacks = self._subscribers.get(channel)
        if callbacks is None:
            return
        try:
            callbacks.remove(callback)
        except ValueError:
            return
        if not callbacks:
            del self._subscribers[channel]

    async def dispatch_outbound(self) -> None:
        """兼容旧调用的内存队列 worker；每条消息只尝试一次。"""
        if not self._ephemeral_outbound_enabled:
            raise RuntimeError("ephemeral outbound is disabled")
        self._running = True
        while self._running:
            try:
                msg = await asyncio.wait_for(self._outbound.get(), timeout=1.0)
                try:
                    await self.deliver_outbound_once(
                        msg,
                        lane="passive",
                        passive_pending_already=True,
                    )
                except Exception:
                    logger.exception(
                        "ephemeral outbound delivery failed channel=%s chat_id=%s",
                        msg.channel,
                        msg.chat_id,
                    )
            except asyncio.TimeoutError:
                continue

    async def deliver_outbound_once(
        self,
        msg: OutboundMessage,
        *,
        lane: str,
        passive_pending_already: bool = False,
    ) -> None:
        """Invoke channel callbacks once and preserve ambiguous failure truth."""

        async def _send() -> None:
            await self._send_outbound_once(msg)

        try:
            if lane == "passive":
                if not passive_pending_already:
                    await self._chat_lane.mark_passive_send_pending(
                        msg.channel,
                        msg.chat_id,
                    )
                await self._chat_lane.run_passive(msg.channel, msg.chat_id, _send)
            elif lane in {"proactive", "system"}:
                await self._chat_lane.run_non_passive(msg.channel, msg.chat_id, _send)
            else:
                raise ValueError(f"unsupported delivery lane: {lane!r}")
        except BaseException:
            if self._delivery_observer is not None:
                await self._delivery_observer(msg, False)
            raise
        if self._delivery_observer is not None:
            await self._delivery_observer(msg, True)

    async def _send_outbound_once(self, msg: OutboundMessage) -> None:
        callbacks = tuple(self._subscribers.get(msg.channel, []))
        if not callbacks:
            raise NoOutboundSubscriberError(
                f"outbound channel is not registered: {msg.channel}"
            )
        for cb in callbacks:
            await cb(msg)

    async def _send_outbound(self, msg: OutboundMessage) -> bool:
        """Compatibility wrapper for tests and legacy callers."""

        await self._send_outbound_once(msg)
        return True

    def stop(self) -> None:
        self._running = False

    @property
    def chat_lane(self) -> ChatLane:
        return self._chat_lane

    @property
    def inbound_size(self) -> int:
        return self._inbound.qsize()

    @property
    def outbound_size(self) -> int:
        return self._outbound.qsize()
