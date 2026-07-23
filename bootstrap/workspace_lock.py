from __future__ import annotations

import os
from pathlib import Path
from typing import IO

from core.common.file_lock import (
    acquire_file_lock,
    read_lock_owner,
    release_file_lock,
    write_lock_owner,
)


class WorkspaceInstanceLock:
    """保证一个 workspace 同时只有一个 runtime owner。"""

    def __init__(self, workspace: Path) -> None:
        self.path = workspace / ".instance.lock"
        self._stream: IO[str] | None = None

    def acquire(self) -> None:
        """非阻塞获取进程锁；冲突时保留 owner 信息并明确失败。"""

        # 1. 锁文件本身可持久存在，内核 flock 才是 owner 真相。
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+", encoding="utf-8")
        try:
            acquire_file_lock(stream, blocking=False)
        except OSError as exc:
            owner = read_lock_owner(stream)
            stream.close()
            raise RuntimeError(
                f"workspace 已由其他 runtime 占用: {self.path} owner={owner}"
            ) from exc

        # 2. 获取后刷新诊断 owner，不把文件存在误当成锁。
        try:
            write_lock_owner(stream, str(os.getpid()))
        except Exception:
            release_file_lock(stream)
            stream.close()
            raise
        self._stream = stream

    def release(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            release_file_lock(stream)
        finally:
            stream.close()
