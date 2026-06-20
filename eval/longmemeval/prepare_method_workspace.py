"""Prepare a frozen-memory workspace for method QA-only evaluation."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy an already-ingested baseline workspace for QA-only method evaluation."
    )
    parser.add_argument("--source-workspace", required=True, type=Path)
    parser.add_argument("--target-workspace", required=True, type=Path)
    parser.add_argument(
        "--archive-existing",
        action="store_true",
        help="Move an existing target workspace to runtime/eval/archive before copying.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    source = args.source_workspace.resolve()
    target = args.target_workspace.resolve()
    if not source.exists():
        raise SystemExit(f"source workspace does not exist: {source}")
    if not source.is_dir():
        raise SystemExit(f"source workspace is not a directory: {source}")
    if target.exists():
        if not args.archive_existing:
            raise SystemExit(
                f"target workspace already exists: {target}; pass --archive-existing"
            )
        _archive_target(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=_ignore_runtime_noise)
    cleaned = _clean_qa_artifacts(target)
    print(f"copied {source} -> {target}")
    print(f"cleaned_qa_artifacts={cleaned}")


def _archive_target(target: Path) -> None:
    archive_root = Path("runtime/eval/archive").resolve()
    archive_root.mkdir(parents=True, exist_ok=True)
    try:
        target.relative_to(Path.cwd().resolve())
    except ValueError as exc:
        raise SystemExit(f"refusing to archive target outside cwd: {target}") from exc
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive = archive_root / f"{stamp}-{target.name}"
    shutil.move(str(target), str(archive))
    print(f"archived {target} -> {archive}")


def _ignore_runtime_noise(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in {"__pycache__", ".pytest_cache"}:
            ignored.add(name)
    return ignored


def _clean_qa_artifacts(workspace: Path) -> int:
    cleaned = 0
    for path in workspace.glob("*/result.json"):
        path.unlink(missing_ok=True)
        cleaned += 1
    for path in workspace.glob("*/trace.log"):
        path.unlink(missing_ok=True)
        cleaned += 1
    for db_path in workspace.glob("*/sessions.db"):
        cleaned += _delete_qa_sessions(db_path)
    return cleaned


def _delete_qa_sessions(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT key FROM sessions WHERE key LIKE ?",
            ("%:qa",),
        ).fetchall()
        keys = [str(row[0]) for row in rows if str(row[0]).strip()]
        if not keys:
            return 0
        placeholders = ",".join("?" for _ in keys)
        conn.execute(
            f"DELETE FROM messages WHERE session_key IN ({placeholders})",
            tuple(keys),
        )
        conn.execute(
            f"DELETE FROM sessions WHERE key IN ({placeholders})",
            tuple(keys),
        )
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        conn.commit()
        return len(keys)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
