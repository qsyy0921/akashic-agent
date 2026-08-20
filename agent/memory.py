import hashlib
import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypedDict, cast

from infra.persistence.json_store import atomic_save_json, atomic_write_text, load_json
from utils.helpers import ensure_dir

logger = logging.getLogger(__name__)

_CONSOLIDATION_MARKER_PREFIX = "<!-- consolidation:"
_CONSOLIDATION_MARKER_SUFFIX = " -->"
_CONSOLIDATION_TAIL_BYTES = 1024 * 1024
DEFAULT_SELF_MD = """# Akashic 的自我认知

## 人格与形象
- 我是 Akashic，一个直接、温暖、主动参与思考的长期协作伙伴。
- 我优先给出结论，再补充必要细节；不把自己伪装成没有立场的工具。

## 我对当前用户的理解
- 我会从长期记忆中逐步形成对当前用户的理解，不在缺少证据时编造画像。

## 我们关系的定义
- 我与当前用户的关系以透明、尊重边界和持续协作为基础。
"""


def _content_digest(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


_PublicationStatus = Literal["prepared", "publishing", "published", "committed"]


class _PublicationState(TypedDict):
    schema_version: str
    transaction_id: str
    status: _PublicationStatus
    pending_sha256: str
    memory_sha256: str | None
    updated_at: str


class MemoryStore:
    """Markdown 记忆文件：
    - MEMORY.md：稳定用户档案
    - SELF.md：Akashic 自我认知
    - PENDING.md：对话中提取的长期记忆候选
    - RECENT_CONTEXT.md：近期语境摘要
    """

    def __init__(self, workspace: Path):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.recent_context_file = self.memory_dir / "RECENT_CONTEXT.md"
        self.pending_file = self.memory_dir / "PENDING.md"
        self.self_file = self.memory_dir / "SELF.md"
        self._consolidation_db = self.memory_dir / "consolidation_writes.db"
        self._publication_state_file = self.memory_dir / "PENDING.snapshot.state.json"
        self._publication_ledger_file = self.memory_dir / "publication-ledger.jsonl"
        self._consolidation_lock = threading.Lock()
        # 确保 PENDING.md 始终存在，避免首次运行时找不到文件
        if not self.pending_file.exists():
            self.pending_file.touch()
        self._init_consolidation_db()
        # 崩溃恢复：启动时若遗留 snapshot，回滚合并
        self._recover_pending_snapshot()

    # ── 长期记忆（MEMORY.md）─────────────────────────────

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str) -> None:
        atomic_write_text(self.memory_file, content, domain="memory")

    # ── RECENT_CONTEXT.md（压缩后的近期语境）──────────────

    def read_recent_context(self) -> str:
        if self.recent_context_file.exists():
            return self.recent_context_file.read_text(encoding="utf-8")
        return ""

    def write_recent_context(self, content: str) -> None:
        atomic_write_text(self.recent_context_file, content, domain="memory")

    # ── SELF.md（Akashic 自我模型）─────────────────────────────

    def read_self(self) -> str:
        if self.self_file.exists():
            return self.self_file.read_text(encoding="utf-8")
        return ""

    def write_self(self, content: str) -> None:
        atomic_write_text(self.self_file, content, domain="memory")

    # ── 待处理事实（对话 → optimizer 缓冲区）───────────

    def read_pending(self) -> str:
        if self.pending_file.exists():
            return self._strip_consolidation_markers(
                self.pending_file.read_text(encoding="utf-8")
            )
        return ""

    def append_pending(self, facts: str) -> None:
        """追加对话中提取的增量事实片段，不触碰 MEMORY.md。"""
        if not facts or not facts.strip():
            return
        with self._consolidation_lock:
            with open(self.pending_file, "a", encoding="utf-8") as f:
                _ = f.write(facts.rstrip() + "\n")

    def append_pending_once(
        self,
        facts: str,
        *,
        source_ref: str,
        kind: str = "pending",
    ) -> bool:
        """按 source_ref 幂等追加 PENDING，避免重启后重复 consolidation。"""
        text = facts.strip()
        if not text:
            return False
        return self._append_once_with_index(
            target_file=self.pending_file,
            text=text,
            source_ref=source_ref,
            kind=kind,
            trailing_blank_line=False,
        )

    def clear_pending(self) -> None:
        """optimizer 归档后清空 PENDING.md。"""
        with self._consolidation_lock:
            atomic_write_text(self.pending_file, "", domain="memory")

    # ── 两阶段提交（供 MemoryOptimizer 使用）──────────────────────

    @property
    def _snapshot_path(self) -> Path:
        return self.pending_file.with_name("PENDING.snapshot.md")

    def snapshot_pending(self) -> str:
        """Phase-1：原子移走 PENDING.md，返回其内容。

        rename 之后 append_pending 会写入新建的 PENDING.md，
        与本次快照完全隔离，不会丢失后续增量。
        调用前会自动处理上次崩溃遗留的 snapshot。
        """
        self._recover_pending_snapshot()
        with self._consolidation_lock:
            if not self.pending_file.exists() or self.pending_file.stat().st_size == 0:
                return ""
            # 同卷 rename 后，新追加写入全新的 PENDING.md。
            _ = self.pending_file.rename(self._snapshot_path)
            snapshot_text = self._snapshot_path.read_text(encoding="utf-8")
            state: _PublicationState = {
                "schema_version": "1",
                "transaction_id": f"memory-publication:{uuid.uuid4().hex}",
                "status": "prepared",
                "pending_sha256": _content_digest(snapshot_text),
                "memory_sha256": None,
                "updated_at": _utc_now(),
            }
            try:
                atomic_save_json(
                    self._publication_state_file,
                    state,
                    domain="memory_publication",
                )
            except BaseException:
                _ = self._snapshot_path.replace(self.pending_file)
                raise
            self.pending_file.touch()
            return self._strip_consolidation_markers(snapshot_text)

    def pending_publication_id(self) -> str | None:
        state = self._read_publication_state()
        if state is None:
            return None
        return state["transaction_id"]

    def prepare_pending_publication(self, memory_content: str) -> None:
        with self._consolidation_lock:
            state = self._require_publication_state("prepared")
            self._verify_snapshot_digest(state)
            state["status"] = "publishing"
            state["memory_sha256"] = _content_digest(memory_content)
            state["updated_at"] = _utc_now()
            atomic_save_json(
                self._publication_state_file,
                state,
                domain="memory_publication",
            )

    def confirm_pending_publication(self) -> None:
        with self._consolidation_lock:
            state = self._require_publication_state("publishing")
            expected = state["memory_sha256"]
            actual = _content_digest(self.read_long_term())
            if actual != expected:
                raise RuntimeError("published MEMORY.md digest does not match manifest")
            state["status"] = "published"
            state["updated_at"] = _utc_now()
            atomic_save_json(
                self._publication_state_file,
                state,
                domain="memory_publication",
            )

    def pending_publication_matches_memory(self) -> bool:
        state = self._read_publication_state()
        if state is None or state["status"] not in {"publishing", "published"}:
            return False
        expected = state["memory_sha256"]
        if not self.memory_file.is_file():
            return False
        return expected is not None and _content_digest(self.read_long_term()) == expected

    def append_publication_audit(self, payload: dict[str, object]) -> None:
        record: dict[str, object] = {
            "schema_version": "1",
            "occurred_at": _utc_now(),
            **payload,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._consolidation_lock:
            with self._publication_ledger_file.open("a", encoding="utf-8") as stream:
                _ = stream.write(line + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def commit_pending_snapshot(self) -> None:
        """Phase-2 成功：merge 已完成，删除快照。"""
        with self._consolidation_lock:
            state = self._read_publication_state()
            if state is not None:
                state["status"] = "committed"
                state["updated_at"] = _utc_now()
                atomic_save_json(
                    self._publication_state_file,
                    state,
                    domain="memory_publication",
                )
            if self._snapshot_path.exists():
                self._snapshot_path.unlink()
            if self._publication_state_file.exists():
                self._publication_state_file.unlink()
            # 保持 PENDING.md 常驻，避免“已归档后文件消失”带来的状态歧义
            if not self.pending_file.exists():
                self.pending_file.touch()

    def rollback_pending_snapshot(self) -> None:
        """Phase-2 失败：将快照内容合并回 PENDING.md，不丢失任何数据。

        快照（较旧）在前，运行期新追加（较新）在后。
        """
        with self._consolidation_lock:
            if not self._snapshot_path.exists():
                if self._publication_state_file.exists():
                    raise RuntimeError(
                        "memory publication manifest exists without pending snapshot"
                    )
                return
            snap_text = self._snapshot_path.read_text(encoding="utf-8")
            new_text = (
                self.pending_file.read_text(encoding="utf-8")
                if self.pending_file.exists()
                else ""
            )
            merged = (
                snap_text.rstrip() + "\n" + new_text if new_text.strip() else snap_text
            )
            atomic_write_text(self.pending_file, merged, domain="memory")
            self._snapshot_path.unlink()
            if self._publication_state_file.exists():
                self._publication_state_file.unlink()
        logger.info("[memory] PENDING snapshot 已回滚合并")

    def _recover_pending_snapshot(self) -> None:
        """启动时或 snapshot_pending 前调用，处理上次崩溃遗留的快照。"""
        snapshot_exists = self._snapshot_path.exists()
        state = self._read_publication_state()
        if not snapshot_exists:
            if state is None:
                return
            if state["status"] == "committed":
                self._publication_state_file.unlink()
                return
            raise RuntimeError("memory publication manifest exists without pending snapshot")
        if state is None:
            logger.warning("[memory] 检测到旧版 PENDING snapshot，执行崩溃回滚")
            self.rollback_pending_snapshot()
            return
        self._verify_snapshot_digest(state)
        status = state["status"]
        if status == "committed":
            self._finish_recovered_publication()
            return
        if status in {"publishing", "published"}:
            expected = state["memory_sha256"]
            actual = _content_digest(self.read_long_term())
            if expected is not None and actual == expected:
                logger.warning(
                    "[memory] 检测到已写入的发布事务，完成 snapshot commit"
                )
                self._finish_recovered_publication()
                return
        logger.warning("[memory] 检测到未完成的发布事务，执行崩溃回滚")
        self.rollback_pending_snapshot()

    def _finish_recovered_publication(self) -> None:
        with self._consolidation_lock:
            if self._snapshot_path.exists():
                self._snapshot_path.unlink()
            if self._publication_state_file.exists():
                self._publication_state_file.unlink()
            if not self.pending_file.exists():
                self.pending_file.touch()

    def _read_publication_state(self) -> _PublicationState | None:
        raw = load_json(
            self._publication_state_file,
            default=None,
            domain="memory_publication",
        )
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise RuntimeError("memory publication manifest must be an object")
        payload = cast(dict[str, object], raw)
        required = {
            "schema_version",
            "transaction_id",
            "status",
            "pending_sha256",
            "memory_sha256",
            "updated_at",
        }
        if set(payload) != required or payload.get("schema_version") != "1":
            raise RuntimeError("memory publication manifest schema is invalid")
        status = payload.get("status")
        if status not in {"prepared", "publishing", "published", "committed"}:
            raise RuntimeError("memory publication manifest status is invalid")
        for key in ("transaction_id", "pending_sha256", "updated_at"):
            if not isinstance(payload.get(key), str) or not payload[key]:
                raise RuntimeError(f"memory publication manifest {key} is invalid")
        memory_sha256 = payload.get("memory_sha256")
        if memory_sha256 is not None and not isinstance(memory_sha256, str):
            raise RuntimeError("memory publication manifest memory_sha256 is invalid")
        return {
            "schema_version": "1",
            "transaction_id": cast(str, payload["transaction_id"]),
            "status": cast(_PublicationStatus, status),
            "pending_sha256": cast(str, payload["pending_sha256"]),
            "memory_sha256": memory_sha256,
            "updated_at": cast(str, payload["updated_at"]),
        }

    def _require_publication_state(self, status: _PublicationStatus) -> _PublicationState:
        state = self._read_publication_state()
        if state is None or state["status"] != status:
            raise RuntimeError(
                f"memory publication state must be {status} before this operation"
            )
        return state

    def _verify_snapshot_digest(self, state: _PublicationState) -> None:
        if not self._snapshot_path.exists():
            raise RuntimeError("memory publication snapshot is missing")
        actual = _content_digest(self._snapshot_path.read_text(encoding="utf-8"))
        if actual != state["pending_sha256"]:
            raise RuntimeError("memory publication snapshot digest mismatch")

    def get_memory_context(self) -> str:
        long_term = self.read_long_term()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    @staticmethod
    def _consolidation_marker(source_ref: str, kind: str) -> str:
        src = source_ref.replace("\n", " ").strip()
        kd = kind.replace("\n", " ").strip()
        return f"{_CONSOLIDATION_MARKER_PREFIX}{src}:{kd}{_CONSOLIDATION_MARKER_SUFFIX}"

    @staticmethod
    def _strip_consolidation_markers(text: str) -> str:
        lines = text.splitlines()
        kept = [
            line
            for line in lines
            if not (
                line.startswith(_CONSOLIDATION_MARKER_PREFIX)
                and line.endswith(_CONSOLIDATION_MARKER_SUFFIX)
            )
        ]
        return "\n".join(kept).strip()

    def _init_consolidation_db(self) -> None:
        conn = sqlite3.connect(str(self._consolidation_db))
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS consolidation_writes (
                    source_ref TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT,
                    trailing_blank_line INTEGER NOT NULL DEFAULT 0,
                    done_at TEXT NOT NULL,
                    PRIMARY KEY (source_ref, kind)
                )""")
            cols = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(consolidation_writes)"
                ).fetchall()
            }
            if "payload" not in cols:
                conn.execute("ALTER TABLE consolidation_writes ADD COLUMN payload TEXT")
            if "trailing_blank_line" not in cols:
                conn.execute(
                    "ALTER TABLE consolidation_writes ADD COLUMN trailing_blank_line INTEGER NOT NULL DEFAULT 0"
                )
            conn.commit()
        finally:
            conn.close()

    def _append_once_with_index(
        self,
        *,
        target_file: Path,
        text: str,
        source_ref: str,
        kind: str,
        trailing_blank_line: bool,
    ) -> bool:
        """在文件和 SQLite 索引之间执行一次幂等追加。"""
        # 1. 校验调用方已建立的字符串契约，并生成稳定 marker。
        src = source_ref.strip()
        kd = kind.strip()
        if not src or not kd or not text:
            return False
        marker = self._consolidation_marker(src, kd)

        # 2. 锁定索引事务，恢复已记录但文件缺失的写入。
        with self._consolidation_lock:
            conn = sqlite3.connect(str(self._consolidation_db), timeout=30.0)
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT payload, trailing_blank_line FROM consolidation_writes WHERE source_ref=? AND kind=?",
                    (src, kd),
                ).fetchone()
                if row is not None:
                    existing_payload = row[0]
                    existing_trailing_raw = row[1]
                    if existing_payload is not None and not isinstance(
                        existing_payload, str
                    ):
                        raise TypeError("consolidation payload must be text")
                    if not isinstance(existing_trailing_raw, int):
                        raise TypeError("consolidation trailing flag must be an integer")
                    if existing_trailing_raw not in (0, 1):
                        raise ValueError("consolidation trailing flag must be 0 or 1")
                    existing_trailing = bool(existing_trailing_raw)
                    if not self._file_contains_marker(target_file, marker):
                        if not existing_payload:
                            raise ValueError(
                                "consolidation index payload is missing for file recovery"
                            )
                        with open(target_file, "a", encoding="utf-8") as f:
                            f.write(marker + "\n")
                            f.write(existing_payload.rstrip() + "\n")
                            if existing_trailing:
                                f.write("\n")
                    conn.execute("COMMIT")
                    return False

                # 恢复路径：若历史崩溃发生在“文件已写，索引未写”，用尾部扫描补索引并跳过重复写。
                if self._tail_contains_marker(target_file, marker):
                    conn.execute(
                        "INSERT OR REPLACE INTO consolidation_writes(source_ref, kind, payload, trailing_blank_line, done_at) VALUES (?, ?, ?, ?, datetime('now'))",
                        (src, kd, text, 1 if trailing_blank_line else 0),
                    )
                    conn.execute("COMMIT")
                    return False

                # 3. 先追加 marker 和内容，再提交索引事务。
                with open(target_file, "a", encoding="utf-8") as f:
                    f.write(marker + "\n")
                    f.write(text.rstrip() + "\n")
                    if trailing_blank_line:
                        f.write("\n")

                conn.execute(
                    "INSERT OR REPLACE INTO consolidation_writes(source_ref, kind, payload, trailing_blank_line, done_at) VALUES (?, ?, ?, ?, datetime('now'))",
                    (src, kd, text, 1 if trailing_blank_line else 0),
                )
                conn.execute("COMMIT")
                return True
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                conn.close()

    @staticmethod
    def _tail_contains_marker(path: Path, marker: str) -> bool:
        if not path.exists():
            return False
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            take = min(size, _CONSOLIDATION_TAIL_BYTES)
            if take <= 0:
                return False
            f.seek(size - take)
            tail = f.read(take).decode("utf-8")
            return marker in tail

    @staticmethod
    def _file_contains_marker(path: Path, marker: str) -> bool:
        if not path.exists():
            return False
        needle = marker.encode("utf-8")
        if not needle:
            return False
        carry = b""
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                data = carry + chunk
                if needle in data:
                    return True
                if len(needle) > 1:
                    carry = data[-(len(needle) - 1) :]
                else:
                    carry = b""
        return False
