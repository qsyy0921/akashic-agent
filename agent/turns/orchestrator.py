from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from agent.turns.outbound import OutboundDispatch, OutboundPort
from agent.turns.result import TurnResult, TurnSideEffect
from session.reliability_records import OutboundIntentDraft

if TYPE_CHECKING:
    from agent.core.runtime_support import SessionLike
    from agent.looping.ports import SessionServices

logger = logging.getLogger("agent.turn_orchestrator")


@dataclass
class TurnOrchestratorDeps:
    session: SessionServices
    outbound: OutboundPort


class TurnOrchestrator:
    def __init__(self, deps: TurnOrchestratorDeps) -> None:
        self._session = deps.session
        self._outbound = deps.outbound

    async def handle_proactive_turn(
        self,
        *,
        result: TurnResult,
        session_key: str,
        channel: str,
        chat_id: str,
    ) -> bool:
        # 1. proactive 先处理 skip：不发消息，只跑 skip 路径副作用。
        if result.decision == "skip":
            await self._run_side_effects(result)
            return False

        if result.outbound is None:
            raise ValueError("proactive reply result requires outbound")

        content = result.outbound.content
        media = list(result.outbound.media or [])
        delivery_id = uuid4().hex
        # 2. 先写可恢复的会话消息和 outbound intent，再允许外部发送。
        await self._run_effects(result.side_effects)
        session = self._session.session_manager.get_or_create(session_key)
        message = self._persist_proactive_session(
            session=session,
            content=content,
            media=media,
            result=result,
            delivery_id=delivery_id,
        )
        record = await self._session.session_manager.append_messages_with_outbound(
            session,
            [message],
            OutboundIntentDraft(
                delivery_id=delivery_id,
                idempotency_key=f"proactive:{delivery_id}",
                turn_id=None,
                session_key=session_key,
                channel=channel,
                chat_id=chat_id,
                content=content,
                media=tuple(media),
                metadata={"delivery_id": delivery_id},
                lane="proactive",
                reason_code="proactive_reply",
            ),
        )
        sent = await self._outbound.dispatch(
            OutboundDispatch(
                channel=channel,
                chat_id=chat_id,
                content=content,
                metadata=dict(record.metadata),
                media=media,
                session_message_id=record.session_message_id,
            )
        )

        # 3. Delivery truth 决定 presence 与后置副作用，消息历史保留 delivery_id 供对账。
        if sent:
            if self._session.presence:
                self._session.presence.record_proactive_sent(session_key)
            await self._run_effects(result.success_side_effects)
        else:
            await self._run_effects(result.failure_side_effects)

        return sent

    async def _run_side_effects(self, result: TurnResult) -> None:
        await self._run_effects(result.side_effects)

    async def _run_effects(self, effects: list[TurnSideEffect]) -> None:
        for effect in effects:
            try:
                await effect.run()
            except Exception as e:
                logger.warning("turn side effect failed: %s", e)

    def _persist_proactive_session(
        self,
        *,
        session: SessionLike,
        content: str,
        media: list[str],
        result: TurnResult,
        delivery_id: str,
    ) -> dict[str, object]:
        source_refs = []
        state_summary_tag = "none"
        if result.trace is not None and isinstance(result.trace.extra, dict):
            raw_refs = result.trace.extra.get("source_refs", [])
            if isinstance(raw_refs, list):
                source_refs = [ref for ref in raw_refs if isinstance(ref, dict)]
            state_summary_tag = str(result.trace.extra.get("state_summary_tag", "none"))
        return session.add_message(
            "assistant",
            content,
            media=media if media else None,
            proactive=True,
            delivery_id=delivery_id,
            tools_used=["message_push"],
            evidence_item_ids=[str(item_id) for item_id in result.evidence],
            source_refs=source_refs,
            state_summary_tag=state_summary_tag,
        )
