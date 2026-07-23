from __future__ import annotations

import os
from typing import IO


def acquire_file_lock(stream: IO[str], *, blocking: bool) -> None:
    """Acquire one exclusive kernel lock without hiding platform semantics."""

    if os.name == "nt":
        import msvcrt

        # msvcrt locks a byte range. Keep byte zero reserved for the lock so
        # diagnostics can be read from byte one even while another owner holds it.
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write("\0")
            stream.flush()
        stream.seek(0)
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        msvcrt.locking(stream.fileno(), mode, 1)
        return

    import fcntl

    flags = fcntl.LOCK_EX
    if not blocking:
        flags |= fcntl.LOCK_NB
    fcntl.flock(stream.fileno(), flags)


def release_file_lock(stream: IO[str]) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def write_lock_owner(stream: IO[str], owner: str) -> None:
    stream.seek(1 if os.name == "nt" else 0)
    stream.truncate()
    stream.write(owner)
    stream.flush()


def read_lock_owner(stream: IO[str]) -> str:
    stream.seek(1 if os.name == "nt" else 0)
    return stream.read().strip() or "unknown"
