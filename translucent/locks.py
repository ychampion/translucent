"""Exclusive OS file locks for work only one process may do at a time (request execution, planner turns).

The kernel owns the lock and releases it when the holding process dies, so a stale lock file needs no repair or
deletion. Locks are never evidence: ledger records say what happened. This is server._StateLock's logic with a
non-raising try_acquire; on one host only (not across a network filesystem).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


class ExclusiveFileLock:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.handle = None

    @property
    def held(self) -> bool:
        return self.handle is not None

    def try_acquire(self) -> bool:
        """Take the lock without waiting; False when another holder has it (in this process or another)."""
        if self.handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if not handle.tell():
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self):
        if self.handle is None:
            return
        handle, self.handle = self.handle, None
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "ExclusiveFileLock":
        if not self.try_acquire():
            raise RuntimeError(f"{self.path.name} is held by another process")
        return self

    def __exit__(self, *exc):
        self.release()


def campaign_lock(ledger_root: Path | str, campaign_id: str) -> ExclusiveFileLock:
    """The lock a legacy (video or intervention) campaign's writer holds: the API worker for a whole run, an MCP
    session for each write call. Loop campaigns need none; chain admission serializes their writers."""
    key = hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:16]
    return ExclusiveFileLock(Path(ledger_root) / "locks" / f"campaign-{key}.lock")
