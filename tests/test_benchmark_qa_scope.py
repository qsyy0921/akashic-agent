from __future__ import annotations

from types import SimpleNamespace

import pytest

from eval.longmemeval.dataset import LMEInstance
from eval.longmemeval.qa_runner import run_qa_instance as run_lme_qa_instance
from eval.personamem.dataset import PersonaMemInstance
from eval.personamem.qa_runner import run_qa_instance as run_personamem_qa_instance


class _FakeSessionManager:
    def __init__(self) -> None:
        self._cache: dict[str, object] = {}

    def get_or_create(self, _key: str) -> SimpleNamespace:
        return SimpleNamespace(messages=[])


@pytest.mark.asyncio
async def test_longmemeval_qa_uses_ingest_memory_scope() -> None:
    captured: dict[str, object] = {}

    async def _process(msg, *, session_key: str, dispatch_outbound: bool):
        captured["msg"] = msg
        captured["session_key"] = session_key
        captured["dispatch_outbound"] = dispatch_outbound
        return SimpleNamespace(content="ok")

    rt = SimpleNamespace(
        core=SimpleNamespace(
            loop=SimpleNamespace(_process=_process),
            session_manager=_FakeSessionManager(),
        )
    )
    instance = LMEInstance(
        question_id="case-1",
        question_type="single-session-user",
        question="What happened?",
        answer="answer",
        question_date="2026-01-01",
        haystack_session_ids=[],
        haystack_dates=[],
        haystack_sessions=[],
    )

    await run_lme_qa_instance(rt, instance, timeout_s=1)

    msg = captured["msg"]
    assert msg.channel == "lme"
    assert msg.chat_id == "case-1"
    assert captured["session_key"] == "lme:case-1:qa"
    assert captured["dispatch_outbound"] is False


@pytest.mark.asyncio
async def test_personamem_qa_uses_ingest_memory_scope(tmp_path) -> None:
    captured: dict[str, object] = {}

    async def _process(msg, *, session_key: str, dispatch_outbound: bool):
        captured["msg"] = msg
        captured["session_key"] = session_key
        captured["dispatch_outbound"] = dispatch_outbound
        return SimpleNamespace(content="(a)")

    rt = SimpleNamespace(
        workspace=tmp_path,
        core=SimpleNamespace(
            loop=SimpleNamespace(_process=_process),
            session_manager=_FakeSessionManager(),
        ),
    )
    instance = PersonaMemInstance(
        question_id="pm-case-1",
        question_type="recall_user_shared_facts",
        question="Which option fits?",
        gold_label="(a)",
        gold_option="first",
        all_options=["first", "second"],
        persona_id="persona-1",
        topic="test",
        shared_context_id="ctx-1",
        end_index_in_shared_context=1,
    )

    await run_personamem_qa_instance(rt, instance, timeout_s=1)

    msg = captured["msg"]
    assert msg.channel == "pm"
    assert msg.chat_id == "pm-case-1"
    assert captured["session_key"].startswith("pm:pm-case-1:qa:")
    assert captured["dispatch_outbound"] is False
