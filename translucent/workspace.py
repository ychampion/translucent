"""A data root holding many ledgers, and where an API server keeps its state for each.

Layout under a data root D (`serve --data-root D`, `translucent init <id>`):
  D/ledgers/<id>/ledger.sqlite, blobs/, locks/, work/   one ledger per id
  D/ledgers/<id>/workspace.json                         {"id", "title", "created_at"}: shown in ledger lists
  D/state/<id>/                                         the API's jobs, uploads, idempotency replies, export cache
  D/state/server.lock                                   one server process per data root

Legacy mode maps explicit ids to ledger folders: `serve --ledger X --state Y` is Workspace(ledgers={"default": X},
state_root=Y), and the default ledger's state stays directly in Y, as before.

Ledger ids match LEDGER_ID and are never a Windows device name, so an id is always one plain folder name: it cannot
reach outside D/ledgers. An id that is malformed, or has no ledger.sqlite, is UnknownLedger. Nothing here creates a
ledger except `create`, so a mistyped id is a 404 and leaves no folder behind.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import time
from typing import Iterator
import uuid

from translucent.ledger import Ledger

LEDGER_ID = re.compile(r"[a-z0-9][a-z0-9_-]{1,63}\Z")
DEFAULT = "default"     # the ledger the legacy /api/... routes serve
_DEVICES = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))})
_TITLE = re.compile(r"[^\x00-\x1f\x7f]{1,200}\Z")


class UnknownLedger(LookupError):
    """No ledger has this id here (or the id is malformed). HTTP 404."""


class LedgerExists(FileExistsError):
    """`create` was asked for an id that already has a ledger."""


def device_name(name: str) -> bool:
    """A Windows device name as a file name: NUL, CON.csv, com1.txt (the part before the first dot, any case). Such
    a file is the device, not a file: a write goes nowhere and a read can block."""
    return name.split(".")[0].rstrip(" ").lower() in _DEVICES | {"conin$", "conout$", "clock$"}


def valid_id(lid) -> bool:
    return isinstance(lid, str) and bool(LEDGER_ID.fullmatch(lid)) and lid not in _DEVICES


def check_id(lid) -> str:
    if not valid_id(lid):
        raise ValueError(f"{lid!r} is not a ledger id: use 2-64 lowercase letters, digits, '-' or '_', "
                         "starting with a letter or digit")
    return lid


class Workspace:
    def __init__(self, data_root: Path | str | None = None, *, ledgers: dict[str, Path | str] | None = None,
                 state_root: Path | str | None = None):
        if (data_root is None) == (ledgers is None):
            raise ValueError("give a data root or an explicit mapping of ledger ids to folders, not both")
        if data_root is not None:
            self.root = Path(data_root)
            self.mapping = None
            self.state_root = Path(state_root) if state_root is not None else self.root / "state"
        else:
            if state_root is None:
                raise ValueError("explicit ledgers need a state root")
            self.root = None
            self.mapping = {check_id(lid): Path(p) for lid, p in ledgers.items()}
            self.state_root = Path(state_root)

    @property
    def legacy(self) -> bool:
        return self.mapping is not None

    def ids(self) -> list[str]:
        """Ids with a ledger: the explicit mapping, or every ledgers/<id>/ledger.sqlite under the data root."""
        if self.mapping is not None:
            return sorted(self.mapping)
        folder = self.root / "ledgers"
        if not folder.is_dir():
            return []
        return sorted(p.name for p in folder.iterdir() if valid_id(p.name) and (p / "ledger.sqlite").is_file())

    def exists(self, lid) -> bool:
        try:
            self.path(lid)
        except UnknownLedger:
            return False
        return True

    def path(self, lid) -> Path:
        """The ledger's folder; UnknownLedger when there is none. Never creates anything. An explicit mapping is the
        operator's configuration: its folder is returned as given (`open` creates the ledger there, as the
        single-ledger server always has; the CLI refuses a missing one first)."""
        if not valid_id(lid):
            raise UnknownLedger(lid)
        if self.mapping is not None:
            if lid not in self.mapping:
                raise UnknownLedger(lid)
            return self.mapping[lid]
        path = self.root / "ledgers" / lid
        if not (path / "ledger.sqlite").is_file():
            raise UnknownLedger(lid)
        return path

    def state_dir(self, lid) -> Path:
        """Where a server keeps this ledger's jobs, uploads, idempotency replies and export cache (not created here).
        Legacy mode keeps the default ledger's state in the state root itself, where it always was."""
        self.path(lid)
        if self.mapping is not None:
            return self.state_root if lid == DEFAULT else self.state_root / "ledgers" / lid
        return self.state_root / lid

    def title(self, lid) -> str:
        """The title given at `create`; the id when there is none (a legacy ledger, or an unreadable file)."""
        try:
            info = json.loads((self.path(lid) / "workspace.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return lid
        title = info.get("title") if isinstance(info, dict) else None
        return title if isinstance(title, str) and title else lid

    @contextmanager
    def open(self, lid) -> Iterator[Ledger]:
        L = Ledger(self.path(lid))
        try:
            yield L
        finally:
            L.db.close()

    def create(self, lid: str, *, title: str | None = None) -> Path:
        """Make a new, empty ledger under the data root (`translucent init <id>`). LedgerExists if it has one."""
        if self.mapping is not None:
            raise ValueError("ledgers are created under a data root; this workspace maps explicit folders")
        check_id(lid)
        title = (title or lid).strip()
        if not _TITLE.fullmatch(title):
            raise ValueError("a ledger title is 1-200 characters without control characters")
        path = self.root / "ledgers" / lid
        if (path / "ledger.sqlite").is_file():
            raise LedgerExists(f"ledger {lid} already exists at {path}")
        path.mkdir(parents=True, exist_ok=True)
        info = path / "workspace.json"
        temp = path / f"workspace.json.{uuid.uuid4().hex}.tmp"
        try:
            temp.write_text(json.dumps({"id": lid, "title": title, "created_at": time.time()}), encoding="utf-8")
            os.replace(temp, info)
        finally:
            temp.unlink(missing_ok=True)
        Ledger(path).db.close()     # ledger.sqlite last: the id counts as a ledger only once it is complete
        return path


def init_ledger(data_root: Path | str, lid: str, *, title: str | None = None) -> Path:
    """`translucent init <id> --data-root D`: create ledger <id> under D. ValueError for a bad id or title,
    LedgerExists when it is already there."""
    return Workspace(data_root).create(lid, title=title)


def ledger_ids(data_root: Path | str) -> list[str]:
    return Workspace(data_root).ids()
