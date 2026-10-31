"""Evidence ledger: SQLite records + content-addressed blob store. Insert-only.

- Records are the frozen contracts (translucent.contracts), stored as JSON with indexed keys.
- UPDATE and DELETE are rejected by triggers. Status changes are new rows: a Review for a claim, an
  Invalidation for a run. Derived state (claim status, staleness) is computed from history, never stored.
- Admission rules (checked on insert, before anything references the record):
  * a Measurement cites an existing Run whose status is "ok"; its calibration_ids are existing Measurements;
    its artifact_ids exist;
  * a Run's outputs and inputs exist (artifacts or measurements);
  * a Claim's predicate cites existing Measurements; a Review cites an existing Claim.
- Events get a strictly increasing per-campaign `seq` assigned inside the insert transaction, and a time never
  earlier than the campaign's previous event.
- `transaction()` groups writes into one BEGIN IMMEDIATE; it is re-entrant, and `emit` uses it, so events can be
  written inside a larger transaction. Loop records (translucent.chain) are appended only inside one.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import sqlite3
import time
import uuid
from time import monotonic, sleep
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from translucent.contracts import (Artifact, Campaign, Claim, Decision, Event, Fork, Lesson, Measurement, Protocol,
                                   Review, Run, Source, Transform)
from translucent.intervention_records import InterventionRecord
from translucent.loop_contracts import DatasetRecord, LoopRecord

# Each kind is a `kind` value in the one record table: a new kind needs no new table or trigger.
TABLES = {
    "campaign": Campaign, "artifact": Artifact, "run": Run, "measurement": Measurement,
    "claim": Claim, "review": Review, "lesson": Lesson, "fork": Fork, "protocol": Protocol, "decision": Decision,
    "intervention": InterventionRecord, "dataset": DatasetRecord, "loop": LoopRecord,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS record (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    campaign_id TEXT,
    ref TEXT,               -- run_id for measurements, claim_id for reviews
    body TEXT NOT NULL,
    inserted_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS record_kind ON record(kind, campaign_id);
CREATE INDEX IF NOT EXISTS record_ref ON record(ref);
CREATE TABLE IF NOT EXISTS event (
    campaign_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (campaign_id, seq)
);
CREATE TABLE IF NOT EXISTS invalidation (
    run_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    at REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS record_no_update BEFORE UPDATE ON record BEGIN SELECT RAISE(ABORT, 'ledger is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS record_no_delete BEFORE DELETE ON record BEGIN SELECT RAISE(ABORT, 'ledger is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS event_no_update BEFORE UPDATE ON event BEGIN SELECT RAISE(ABORT, 'ledger is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS event_no_delete BEFORE DELETE ON event BEGIN SELECT RAISE(ABORT, 'ledger is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS inval_no_update BEFORE UPDATE ON invalidation BEGIN SELECT RAISE(ABORT, 'ledger is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS inval_no_delete BEFORE DELETE ON invalidation BEGIN SELECT RAISE(ABORT, 'ledger is insert-only'); END;
"""


class AdmissionError(ValueError):
    """A record was refused because it cites missing, failed or wrong-kind evidence."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Ledger:
    _depth = 0  # open transaction() levels on this connection (class default: verify builds Ledgers without __init__)

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.blobs = self.root / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "ledger.sqlite", isolation_level=None, check_same_thread=False)
        # the API thread writes people's actions while the campaign worker writes evidence: wait, don't fail
        self.db.execute("PRAGMA busy_timeout=15000")
        # Switching to WAL and creating tables take the write lock; another process writing at that moment must not
        # make opening the ledger fail.
        self._retry_locked(lambda: self.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
                           or self.db.execute("PRAGMA journal_mode=WAL"))
        self._retry_locked(lambda: self.db.executescript(SCHEMA))

    # ------------------------------------------------------------ blobs

    def blob_path(self, artifact_id: str) -> Path:
        hexd = artifact_id.removeprefix("sha256:")
        return self.blobs / hexd[:2] / hexd

    def put_file(self, path: Path | str, *, kind, media_type, label=None, shape=None, axes=None,
                 source: Source | None = None, transform: Transform | None = None) -> Artifact:
        """Store a file by content hash and record it. Re-adding identical bytes returns the existing artifact."""
        path = Path(path)
        aid = "sha256:" + sha256_file(path)
        existing = self.get("artifact", aid)
        if existing:
            return existing
        if transform:
            self._require(transform.parent_ids, ("artifact", "measurement"), "transform parent")
        dst = self.blob_path(aid)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f"{dst.name}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")  # one per writer
        shutil.copyfile(path, tmp)
        if "sha256:" + sha256_file(tmp) != aid:  # file changed while copying
            tmp.unlink()
            raise AdmissionError(f"{path} changed during import")
        try:
            tmp.replace(dst)
        except PermissionError:  # Windows: another writer stored the same bytes and has the blob open
            tmp.unlink(missing_ok=True)
            if not dst.exists() or "sha256:" + sha256_file(dst) != aid:
                raise
        art = Artifact(id=aid, kind=kind, media_type=media_type, bytes=dst.stat().st_size, shape=shape, axes=axes,
                       source=source, transform=transform, label=label, created_at=time.time())
        try:
            self._insert("artifact", art)
        except AdmissionError:  # another writer recorded the same bytes first: content addressing makes it this file
            if existing := self.get("artifact", aid):
                return existing
            raise
        return art

    def put_bytes(self, data: bytes, *, suffix=".bin", **kw) -> Artifact:
        tmp = self.root / f".incoming-{hashlib.sha256(data).hexdigest()}-{os.getpid()}-{uuid.uuid4().hex[:8]}{suffix}"
        tmp.write_bytes(data)
        try:
            return self.put_file(tmp, **kw)
        finally:
            tmp.unlink(missing_ok=True)

    def put_json(self, obj, **kw) -> Artifact:
        return self.put_bytes(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode(), suffix=".json",
                              media_type="application/json", **kw)

    # ------------------------------------------------------------ records

    def add(self, rec):
        kind = next(k for k, cls in TABLES.items() if isinstance(rec, cls))
        if kind == "intervention":
            from translucent.intervention import admit
            admit(self, rec)
        elif kind == "loop":
            from translucent.chain import admit
            admit(self, rec)
        elif kind == "dataset":
            self._require([rec.manifest_id], ("artifact",), "dataset manifest")
            for r in rec.records:
                self._require([c.series_id for c in r.channels], ("artifact",), f"dataset {rec.id}/{r.id} series")
                self._require(list(r.files.values()), ("artifact",), f"dataset {rec.id}/{r.id} file")
                self._require([m["artifact_id"] for m in r.media if "artifact_id" in m], ("artifact",),
                              f"dataset {rec.id}/{r.id} media")
        elif kind == "run":
            self._require(rec.input_ids, ("artifact", "measurement"), "run input")
            self._require(rec.output_ids, ("artifact", "measurement"), "run output")
            self._require([rec.campaign_id], ("campaign",), "run campaign")
        elif kind == "measurement":
            run = self.get("run", rec.run_id)
            if run is None:
                raise AdmissionError(f"measurement {rec.id} cites unknown run {rec.run_id}")
            if run.status != "ok":
                raise AdmissionError(f"measurement {rec.id} cites run {rec.run_id} with status {run.status}")
            if run.measurement_ids is not None and rec.id not in run.measurement_ids:
                raise AdmissionError(f"measurement {rec.id} was not produced by run {rec.run_id}")
            self._require(rec.calibration_ids, ("measurement",), "calibration")
            self._require(rec.artifact_ids, ("artifact",), "measurement source")
            for a in rec.artifact_ids:
                if why := self.unsound_source(a):
                    raise AdmissionError(f"measurement {rec.id}: {why}")
        elif kind == "claim":
            self._require([rec.campaign_id], ("campaign",), "claim campaign")
            self._require(rec.predicate.measurement_ids, ("measurement",), "claim evidence")
            if rec.supersedes:
                self._require([rec.supersedes], ("claim",), "superseded claim")
        elif kind == "review":
            self._require([rec.claim_id], ("claim",), "reviewed claim")
            self._require(rec.artifact_ids, ("artifact",), "review evidence")
        elif kind == "lesson":
            self._require(rec.evidence_ids, ("run", "measurement", "claim"), "lesson evidence")
            if rec.contradicts:
                self._require([rec.contradicts], ("lesson",), "contradicted lesson")
        elif kind == "protocol":
            from translucent.protocol import protocol_problems
            self._require([rec.campaign_id], ("campaign",), "protocol campaign")
            if problems := protocol_problems(rec):
                raise AdmissionError(f"protocol {rec.id}: {'; '.join(problems)}")
            started = self.db.execute("SELECT COUNT(*) FROM event WHERE campaign_id=? AND body LIKE ?",
                                      (rec.campaign_id, '%"type":"campaign.started"%')).fetchone()[0]
            work = self.db.execute("SELECT COUNT(*) FROM record WHERE campaign_id=? AND kind NOT IN ('campaign')",
                                   (rec.campaign_id,)).fetchone()[0]
            if started or work:
                raise AdmissionError(f"protocol {rec.id}: metrics are fixed before the campaign starts")
        elif kind == "decision":
            self._require([rec.campaign_id], ("campaign",), "decision campaign")
            ids = [rec.winner_claim_id, rec.verification_claim_id, *rec.attempt_claim_ids]
            self._require([i for i in ids if i], ("claim",), "decision finding")
            self._require(rec.replication_run_ids, ("run",), "replication run")
        elif kind == "fork":
            self._require([rec.campaign_id, rec.parent_campaign_id], ("campaign",), "fork campaign")
            if rec.campaign_id == rec.parent_campaign_id:
                raise AdmissionError(f"fork {rec.id} branches a campaign from itself")
            if rec.claim_id is not None:
                claim = self.get("claim", rec.claim_id)
                if claim is None or claim.campaign_id != rec.parent_campaign_id:
                    raise AdmissionError(f"fork {rec.id} re-examines {rec.claim_id}, not a finding of its parent")
        self._insert(kind, rec)
        return rec

    def lineage(self, artifact_ids) -> set[str]:
        """These artifacts and everything they were derived from (transform parents, recursively)."""
        out, todo = set(), list(artifact_ids)
        while todo:
            a = todo.pop()
            if a not in out:
                out.add(a)
                art = self.get("artifact", a)
                todo += art.transform.parent_ids if art and art.transform else []
        return out

    def recordings(self, measurement_id: str, _seen=None) -> set[str]:
        """The recorded videos a measurement's data comes from: its artifacts' lineage, plus whatever the
        measurements its run read (other than scale and clock) came from."""
        _seen = _seen if _seen is not None else set()
        if measurement_id in _seen:
            return set()
        _seen.add(measurement_id)
        m = self.get("measurement", measurement_id)
        vids = {a for a in self.lineage(m.artifact_ids) if (x := self.get("artifact", a)) and x.kind == "video"}
        run = self.get("run", m.run_id)
        for i in run.input_ids if run else []:
            if i not in m.calibration_ids and self.kind_of(i) == "measurement" and \
                    self.get("measurement", i).quantity not in ("spatial_scale", "time_base"):
                vids |= self.recordings(i, _seen)
        return vids

    def unsound_source(self, artifact_id: str) -> str | None:
        """Why an artifact can't back a measurement: it was derived only by runs that did not succeed."""
        rows = self.db.execute("SELECT body FROM record WHERE kind='run' AND body LIKE ?", (f'%"{artifact_id}"%',))
        makers = [r for r in (Run.model_validate_json(b) for (b,) in rows) if artifact_id in r.output_ids]
        bad = self.invalid_runs()
        if makers and not any(r.status == "ok" and r.id not in bad for r in makers):
            return (f"{artifact_id} was made only by runs that did not succeed "
                    f"({', '.join(sorted({r.status for r in makers}))})")
        return None

    def admission_problem(self, m: Measurement, pending: Iterable[str] = ()) -> str | None:
        """Why a measurement would be refused for its sources (calibrations, artifacts), checked before its run is
        recorded so a run never lists measurements the ledger then refuses."""
        try:
            self._require([c for c in m.calibration_ids if c not in set(pending)], ("measurement",), "calibration")
            self._require(m.artifact_ids, ("artifact",), "measurement source")
        except AdmissionError as e:
            return str(e)
        return next((w for a in m.artifact_ids if (w := self.unsound_source(a))), None)

    def _insert(self, kind, rec):
        campaign = getattr(rec, "campaign_id", None) or (rec.id if kind == "campaign" else None)
        ref = getattr(rec, "run_id", None) or getattr(rec, "claim_id", None)
        identity = getattr(self, "intervention_identity", None)
        inserted_at = identity.timestamp() if identity is not None else time.time()
        try:
            self.db.execute("INSERT INTO record VALUES (?,?,?,?,?,?)",
                            (rec.id, kind, campaign, ref, rec.model_dump_json(), inserted_at))
        except sqlite3.IntegrityError as e:
            raise AdmissionError(f"{kind} {rec.id} already exists") from e

    def _require(self, ids: Iterable[str], kinds: tuple[str, ...], what: str):
        for i in ids:
            row = self.db.execute("SELECT kind FROM record WHERE id=?", (i,)).fetchone()
            if row is None or row[0] not in kinds:
                raise AdmissionError(f"{what} {i} is not a recorded {' or '.join(kinds)}")

    def get(self, kind: str, rid: str):
        row = self.db.execute("SELECT body FROM record WHERE id=? AND kind=?", (rid, kind)).fetchone()
        return TABLES[kind].model_validate_json(row[0]) if row else None

    def kind_of(self, rid: str) -> str | None:
        row = self.db.execute("SELECT kind FROM record WHERE id=?", (rid,)).fetchone()
        return row[0] if row else None

    def list(self, kind: str, campaign_id: str | None = None, ref: str | None = None) -> list:
        q, args = "SELECT body FROM record WHERE kind=?", [kind]
        if campaign_id is not None:
            q, args = q + " AND campaign_id=?", args + [campaign_id]
        if ref is not None:
            q, args = q + " AND ref=?", args + [ref]
        return [TABLES[kind].model_validate_json(r[0]) for r in self.db.execute(q + " ORDER BY inserted_at, rowid", args)]

    # ------------------------------------------------------------ invalidation + derived state

    def invalidate_run(self, run_id: str, reason: str):
        """Mark a run's outputs as no longer trusted (e.g. wrong calibration found). Dependents become stale."""
        self._require([run_id], ("run",), "invalidated run")
        self.db.execute("INSERT OR IGNORE INTO invalidation VALUES (?,?,?)", (run_id, reason, time.time()))

    def invalid_runs(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT run_id, reason FROM invalidation"))

    def stale_reason(self, measurement_id: str, _seen=None) -> str | None:
        """Why a measurement is no longer trustworthy (its run, or any measurement it was derived from, was invalidated)."""
        _seen = _seen or set()
        if measurement_id in _seen:
            return None
        _seen.add(measurement_id)
        m = self.get("measurement", measurement_id)
        bad = self.invalid_runs()
        if m.run_id in bad:
            return f"run {m.run_id} invalidated: {bad[m.run_id]}"
        if bad:  # an artifact it was measured from (or derived from) came out of an invalidated run
            lin = self.lineage(m.artifact_ids)
            for rid, why in bad.items():
                r = self.get("run", rid)
                if r and lin & set(r.output_ids):
                    return f"its source came from run {rid}, invalidated: {why}"
        run = self.get("run", m.run_id)
        upstream = list(m.calibration_ids) + [i for i in (run.input_ids if run else []) if self.kind_of(i) == "measurement"]
        for c in upstream:  # calibrations and every measurement the producing run read (derived analyses)
            r = self.stale_reason(c, _seen)
            if r:
                return f"depends on {c}: {r}"
        return None

    def claim_status(self, claim_id: str) -> str:
        """Latest gate verdict, or 'stale' if any cited evidence was invalidated after review. Critics and people
        never decide."""
        claim = self.get("claim", claim_id)
        if any(self.stale_reason(m) for m in claim.predicate.measurement_ids):
            return "stale"
        gate = [r for r in self.list("review", ref=claim_id) if r.reviewer == "gate"]
        return gate[-1].verdict if gate else "proposed"

    # ------------------------------------------------------------ events

    def in_transaction(self) -> bool:
        return self._depth > 0

    @contextmanager
    def transaction(self) -> Iterator["Ledger"]:
        """One atomic group of writes. The outermost level takes BEGIN IMMEDIATE (SQLite's write lock, which
        serializes writers across processes) and commits, or rolls back on any exception. A nested level is a
        savepoint: it joins the outer transaction, and its own failure undoes only its writes. put_file copies bytes
        and belongs outside."""
        if self._depth:
            name = f"nested_{self._depth}"
            self.db.execute(f"SAVEPOINT {name}")
            self._depth += 1
            try:
                yield self
            except BaseException:
                self.db.execute(f"ROLLBACK TO {name}")
                self.db.execute(f"RELEASE {name}")
                raise
            else:
                self.db.execute(f"RELEASE {name}")
            finally:
                self._depth -= 1
            return
        self._begin_immediate()
        self._depth = 1
        try:
            yield self
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        finally:
            self._depth = 0

    WRITE_WAIT_S = 120.0

    def _retry_locked(self, step) -> None:
        deadline = monotonic() + self.WRITE_WAIT_S
        while True:
            try:
                step()
                return
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) and "busy" not in str(e) or monotonic() >= deadline:
                    raise
                sleep(random.uniform(0.01, 0.1))

    def _begin_immediate(self) -> None:
        # SQLite's busy handler is not a queue: under load one process can lose the write lock to the others for
        # longer than busy_timeout and get "database is locked". Keep asking, with jitter, until WRITE_WAIT_S.
        deadline = monotonic() + self.WRITE_WAIT_S
        while True:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) and "busy" not in str(e) or monotonic() >= deadline:
                    raise
                sleep(random.uniform(0.01, 0.1))

    def next_event(self, campaign_id: str) -> tuple[int, float]:
        """The campaign's next event seq and time, inside a transaction. The time is never earlier than the previous
        event's, so a clock that steps back (another process, NTP) cannot make verify see time run backwards."""
        if not self._depth:
            raise RuntimeError("next_event is read inside Ledger.transaction()")
        row = self.db.execute("SELECT seq, body FROM event WHERE campaign_id=? ORDER BY seq DESC LIMIT 1",
                              (campaign_id,)).fetchone()
        identity = getattr(self, "intervention_identity", None)
        now = identity.timestamp() if identity is not None else time.time()
        if row is None:
            return 1, now
        return row[0] + 1, max(now, json.loads(row[1])["t"])

    def write_event(self, ev: Event) -> Event:
        """Insert an event built from next_event(), inside the same transaction."""
        if not self._depth:
            raise RuntimeError("write_event runs inside Ledger.transaction()")
        self.db.execute("INSERT INTO event VALUES (?,?,?)", (ev.campaign_id, ev.seq, ev.model_dump_json()))
        return ev

    def emit(self, campaign_id: str, type: str, actor: str, title: str, *, detail=None, refs=(), data=None) -> Event:
        if len(title) > 90:  # titles are shown whole: cut at a word, never mid-word
            title = title[:89].rsplit(" ", 1)[0].rstrip(",;:") + "…"
        with self.transaction():
            seq, event_time = self.next_event(campaign_id)
            ev = Event(seq=seq, campaign_id=campaign_id, t=event_time, type=type, actor=actor, title=title,
                       detail=detail, refs=list(refs), data=data or {})
            return self.write_event(ev)

    def events(self, campaign_id: str, after_seq: int = 0) -> list[Event]:
        rows = self.db.execute("SELECT body FROM event WHERE campaign_id=? AND seq>? ORDER BY seq",
                               (campaign_id, after_seq))
        return [Event.model_validate_json(r[0]) for r in rows]
