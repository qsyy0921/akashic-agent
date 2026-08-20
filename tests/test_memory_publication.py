import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from core.memory.markdown import MarkdownMemoryStore
from core.memory.publication import (
    MemoryPublicationGate,
    MemoryPublicationPrivacyError,
    ensure_memory_has_no_secrets,
)
from proactive_v2.memory_optimizer import MemoryOptimizer


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content


_OLD_MEMORY = """# 用户长期记忆

## 用户事实
- 称呼: 旧名字

## 用户偏好
- 回复简洁

## 用户明确要求长期记住的关键内容
- 暂无
"""

_NEW_MEMORY = _OLD_MEMORY.replace("旧名字", "新名字")


def _optimizer(memory: MarkdownMemoryStore, *responses: str) -> tuple[MemoryOptimizer, Any]:
    provider = type("Provider", (), {})()
    provider.chat = AsyncMock(side_effect=[_Response(item) for item in responses])
    optimizer = MemoryOptimizer(memory, cast(Any, provider), "test-model")
    optimizer._STEP_DELAY_SECONDS = 0
    return optimizer, provider


def test_publication_gate_fuses_accept_reject_duplicate_and_conflict():
    gate = MemoryPublicationGate()
    secret = "12345678:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd"
    decision = gate.evaluate(
        pending=(
            "- [preference] 喜欢结构化答案\n"
            "- [preference] 喜欢结构化答案\n"
            f"- [key_info] bot token: {secret}\n"
            "- [identity] 称呼: 新名字\n"
            "- [correction] 称呼: 新名字\n"
        ),
        current_memory=_OLD_MEMORY,
    )

    assert [item.tag for item in decision.accepted] == ["preference", "correction"]
    assert {item.reason_code for item in decision.rejected} == {
        "duplicate_candidate",
        "privacy_secret",
        "conflict_requires_correction",
    }
    audit = json.dumps(decision.audit_payload(), ensure_ascii=False)
    assert secret not in audit
    assert "喜欢结构化答案" not in audit


def test_publication_gate_rejects_invalid_and_already_published_candidates():
    decision = MemoryPublicationGate().evaluate(
        pending="plain text\n- [unknown] value\n- 回复简洁",
        current_memory=_OLD_MEMORY,
    )

    assert decision.accepted == ()
    assert [item.reason_code for item in decision.rejected] == [
        "invalid_format",
        "unsupported_tag",
        "already_published",
    ]


def test_memory_output_privacy_check_rejects_secret():
    with pytest.raises(MemoryPublicationPrivacyError):
        ensure_memory_has_no_secrets(
            _OLD_MEMORY + "\n- API key: sk-abcdefghijklmnopqrstuvwxyz123456"
        )


@pytest.mark.asyncio
async def test_optimizer_drops_secret_candidate_without_calling_model(tmp_path):
    memory = MarkdownMemoryStore(tmp_path)
    memory.write_long_term(_OLD_MEMORY)
    secret = "12345678:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd"
    memory.append_pending(f"- [key_info] bot token: {secret}")
    optimizer, provider = _optimizer(memory)

    await optimizer.optimize()

    provider.chat.assert_not_awaited()
    assert memory.read_long_term() == _OLD_MEMORY
    assert memory.read_pending() == ""
    ledger = memory._publication_ledger_file.read_text(encoding="utf-8")
    assert "privacy_secret" in ledger
    assert secret not in ledger


@pytest.mark.asyncio
async def test_optimizer_rejects_conflict_but_accepts_explicit_correction(tmp_path):
    conflict_memory = MarkdownMemoryStore(tmp_path / "conflict")
    conflict_memory.write_long_term(_OLD_MEMORY)
    conflict_memory.append_pending("- [identity] 称呼: 新名字")
    conflict_optimizer, conflict_provider = _optimizer(conflict_memory)

    await conflict_optimizer.optimize()

    conflict_provider.chat.assert_not_awaited()
    assert conflict_memory.read_long_term() == _OLD_MEMORY

    correction_memory = MarkdownMemoryStore(tmp_path / "correction")
    correction_memory.write_long_term(_OLD_MEMORY)
    correction_memory.append_pending("- [correction] 称呼: 新名字")
    correction_optimizer, correction_provider = _optimizer(
        correction_memory,
        _NEW_MEMORY,
        "",
    )

    await correction_optimizer.optimize()

    assert correction_memory.read_long_term().strip() == _NEW_MEMORY.strip()
    merge_prompt = correction_provider.chat.await_args_list[0].kwargs["messages"][1][
        "content"
    ]
    assert "- [correction] 称呼: 新名字" in merge_prompt


@pytest.mark.asyncio
async def test_optimizer_rejects_secret_in_model_output_and_rolls_back(tmp_path):
    memory = MarkdownMemoryStore(tmp_path)
    memory.write_long_term(_OLD_MEMORY)
    memory.append_pending("- [preference] 喜欢结构化答案")
    unsafe_output = _NEW_MEMORY + "\n- API key: sk-abcdefghijklmnopqrstuvwxyz123456"
    optimizer, _provider = _optimizer(memory, unsafe_output)

    with pytest.raises(MemoryPublicationPrivacyError):
        await optimizer.optimize()

    assert memory.read_long_term() == _OLD_MEMORY
    assert "喜欢结构化答案" in memory.read_pending()
    assert not memory._snapshot_path.exists()
    assert not memory._publication_state_file.exists()


@pytest.mark.asyncio
async def test_optimizer_leaves_recoverable_manifest_when_write_raises_after_replace(
    tmp_path,
    monkeypatch,
):
    memory = MarkdownMemoryStore(tmp_path)
    memory.write_long_term(_OLD_MEMORY)
    memory.append_pending("- [correction] 称呼: 新名字")
    optimizer, _provider = _optimizer(memory, _NEW_MEMORY)
    original_write = memory.write_long_term

    def _write_then_raise(content: str) -> None:
        original_write(content)
        raise OSError("directory sync failed")

    monkeypatch.setattr(memory, "write_long_term", _write_then_raise)

    with pytest.raises(OSError, match="directory sync failed"):
        await optimizer.optimize()

    assert memory._snapshot_path.exists()
    assert memory._publication_state_file.exists()
    recovered = MarkdownMemoryStore(tmp_path)
    assert recovered.read_long_term().strip() == _NEW_MEMORY.strip()
    assert recovered.read_pending() == ""


def test_restart_finishes_publication_when_memory_digest_matches(tmp_path):
    memory = MarkdownMemoryStore(tmp_path)
    memory.write_long_term(_OLD_MEMORY)
    memory.append_pending("- [correction] 称呼: 新名字")
    _ = memory.snapshot_pending()
    memory.prepare_pending_publication(_NEW_MEMORY)
    memory.write_long_term(_NEW_MEMORY)

    recovered = MarkdownMemoryStore(tmp_path)

    assert recovered.read_long_term() == _NEW_MEMORY
    assert recovered.read_pending() == ""
    assert not recovered._snapshot_path.exists()
    assert not recovered._publication_state_file.exists()


def test_restart_rolls_back_when_published_memory_digest_does_not_match(tmp_path):
    memory = MarkdownMemoryStore(tmp_path)
    memory.write_long_term(_OLD_MEMORY)
    memory.append_pending("- [correction] 称呼: 新名字")
    _ = memory.snapshot_pending()
    memory.prepare_pending_publication(_NEW_MEMORY)

    recovered = MarkdownMemoryStore(tmp_path)

    assert recovered.read_long_term() == _OLD_MEMORY
    assert "称呼: 新名字" in recovered.read_pending()
    assert not recovered._snapshot_path.exists()
    assert not recovered._publication_state_file.exists()


def test_restart_fails_loud_on_corrupt_publication_manifest(tmp_path):
    memory = MarkdownMemoryStore(tmp_path)
    memory.append_pending("- [preference] value")
    _ = memory.snapshot_pending()
    memory._publication_state_file.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="manifest schema"):
        MarkdownMemoryStore(tmp_path)
