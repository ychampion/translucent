"""Offline consistency checks. Source ledgers are opened read-only; gate writes stay in a scratch database."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
import uuid

from translucent import gate, past_gates
from translucent.contracts import Event, Measurement, ReplayBundle, Review
from translucent.ledger import Ledger, SCHEMA, TABLES, sha256_file
from translucent.intervention_gate import install as _install_intervention_gate

_install_intervention_gate()

HASH = re.compile(r"[0-9a-f]{64}\Z")
IN_FLIGHT = re.compile(r"[0-9a-f]{64}\.[0-9]+-[0-9a-f]{8}\.tmp\Z")  # Ledger.put_file's per-writer temp name


@dataclass
class Result:
    findings: int = 0
    matched: int = 0
    files: int = 0
    decisions: int = 0                                 # re-checked decisions; named in the summary only when present
    requests: int = 0                                  # re-checked loop requests; likewise
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # informational only; never a problem

    @property
    def ok(self):
        return not self.problems

    def summary(self):
        text = f"{self.findings} findings re-checked, {self.matched} match; {self.files} files intact"
        text += f", {self.decisions} decisions re-checked" if self.decisions else ""
        return text + (f", {self.requests} requests re-checked" if self.requests else "")

    def as_dict(self):
        out = {"ok": self.ok, "summary": self.summary(), "findings": self.findings,
               "matched": self.matched, "files": self.files, "problems": self.problems}
        if self.notes:
            out["notes"] = self.notes
        if self.decisions:
            out["decisions"] = self.decisions
        if self.requests:
            out["requests"] = self.requests
        return out

    @property
    def matches(self):
        return self.matched

    def to_dict(self):
        return self.as_dict()


@contextmanager
def read_ledger(root):
    root = Path(root).resolve()
    path = root / "ledger.sqlite"
    if not path.is_file():
        raise ValueError(f"Ledger not found: {path}")
    L = Ledger.__new__(Ledger)
    L.root, L.blobs = root, root / "blobs"
    L.db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None)
    L.db.execute("PRAGMA query_only=ON")
    try:
        yield L
    finally:
        L.db.close()


@contextmanager
def _scratch():
    parent = Path(tempfile.gettempdir()).resolve()
    root = parent / ("translucent-verify-" + uuid.uuid4().hex)
    root.mkdir()
    try:
        yield root
    finally:
        if root.resolve().parent == parent and root.name.startswith("translucent-verify-"):
            shutil.rmtree(root)


@contextmanager
def snapshot(root):
    """A writable scratch copy of the ledger at one point in time. A ledger with a write-ahead log may have writers
    (an API runner, MCP processes): it is copied with SQLite's backup API from a read-only connection, which sees one
    committed state. A ledger without one is copied as files, so verify never adds -wal or -shm files beside it."""
    with _scratch() as directory:
        source = Path(root).resolve()
        if not (source / "ledger.sqlite").is_file():
            raise ValueError(f"Ledger not found: {source}")
        scratch = Ledger.__new__(Ledger)
        scratch.root, scratch.blobs = Path(directory), source / "blobs"
        if (source / "ledger.sqlite-wal").exists():
            scratch.db = sqlite3.connect(scratch.root / "ledger.sqlite", isolation_level=None)
            try:
                live = sqlite3.connect((source / "ledger.sqlite").as_uri() + "?mode=ro", uri=True, timeout=15)
                try:
                    live.backup(scratch.db)
                finally:
                    live.close()
                yield scratch
            finally:
                scratch.db.close()
            return
        copied_root = scratch.root / "source"
        copied_root.mkdir()
        paths = [source / "ledger.sqlite", source / "ledger.sqlite-wal"]
        def stamps():
            return [(p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None for p in paths]
        for attempt in range(3):
            before = stamps()
            try:
                for path, stamp in zip(paths, before):
                    dst = copied_root / path.name
                    if stamp is None:
                        dst.unlink(missing_ok=True)
                    else:
                        shutil.copyfile(path, dst)
                if before == stamps():
                    break
            except FileNotFoundError:
                pass
            if attempt == 2:
                raise ValueError("Ledger changed while copying; retry verification")
            time.sleep(0.02)
        scratch.db = sqlite3.connect(scratch.root / "ledger.sqlite", isolation_level=None)
        try:
            copied = sqlite3.connect(copied_root / "ledger.sqlite")
            try:
                copied.backup(scratch.db)
            finally:
                copied.close()
            yield scratch
        finally:
            scratch.db.close()


def copy_snapshot(L, path, campaign_id, records=None):
    """Copy original record/event order and insert-only protections into a receipt."""
    target = sqlite3.connect(path)
    try:
        if records is None:
            L.db.backup(target)
        else:
            target.executescript(SCHEMA)
            with target:
                for row in L.db.execute("SELECT id, kind, campaign_id, ref, body, inserted_at FROM record "
                                        "ORDER BY rowid"):
                    if row[0] in records:
                        target.execute("INSERT INTO record VALUES (?,?,?,?,?,?)", row)
                for row in L.db.execute("SELECT campaign_id, seq, body FROM event WHERE campaign_id=? ORDER BY rowid",
                                        (campaign_id,)):
                    target.execute("INSERT INTO event VALUES (?,?,?)", row)
                for row in L.db.execute("SELECT run_id, reason, at FROM invalidation ORDER BY rowid"):
                    if row[0] in records:
                        target.execute("INSERT INTO invalidation VALUES (?,?,?)", row)
    finally:
        target.close()


def export_snapshot(L, out, campaign_id, records=None):
    """A sibling receipt preserves ReplayBundle v1 and includes all historical gate inputs.

    With `records` (a set of record ids), the receipt's ledger holds only those records, in their original order,
    and only this campaign's events: enough to re-check this campaign's evidence, not the rest of the ledger."""
    out = Path(out)
    root = out / "verification"
    root.mkdir(parents=True, exist_ok=True)
    copy_snapshot(L,root / 'ledger.sqlite',campaign_id,records)
    with read_ledger(root) as saved:
        artifacts = saved.list("artifact")
    paths = [out / "bundle.json", out / "movie.mp4", root / "ledger.sqlite"]
    for art in artifacts:
        hexd = art.id.removeprefix("sha256:")
        if not HASH.fullmatch(hexd) or art.id != "sha256:" + hexd:
            raise ValueError("Malformed artifact content address")
        dst = root / "blobs" / hexd[:2] / hexd
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(L.blob_path(art.id), dst)
        paths.append(dst)
    receipt = {"version": 1, "campaign_id": campaign_id, "files": [
        {"path": p.relative_to(out).as_posix(), "sha256": sha256_file(p), "bytes": p.stat().st_size}
        for p in paths]}
    (out / "verification.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")


def export_evidence(L, out, records=None):
    campaign_id = json.loads((Path(out) / "bundle.json").read_text(encoding="utf-8"))["campaign"]["id"]
    export_snapshot(L, out, campaign_id, records)


def evidence(L, claim_id):
    """The transitive evidence graph, also used by the read-only MCP tools."""
    claim = L.get("claim", claim_id)
    if claim is None:
        raise ValueError(f"Finding not found: {claim_id}")
    found = {"measurement": {}, "run": {}, "artifact": {}}
    todo = [("measurement", i) for i in claim.predicate.measurement_ids]
    problems = []
    invalid = L.invalid_runs()
    makers = {}
    for run in L.list("run"):
        for aid in run.output_ids:
            makers.setdefault(aid, []).append(run)
    while todo:
        kind, rid = todo.pop()
        if rid in found[kind]:
            continue
        rec = L.get(kind, rid)
        if rec is None:
            problems.append(f"Missing {kind} {rid}")
            continue
        found[kind][rid] = rec
        if kind == "measurement":
            todo += [("run", rec.run_id)] + [("measurement", i) for i in rec.calibration_ids]
            todo += [("artifact", i) for i in rec.artifact_ids]
            run = L.get("run", rec.run_id)
            if run and run.measurement_ids is not None and rec.id not in run.measurement_ids:
                problems.append(f"Measurement {rec.id} was not produced by run {run.id}")
        elif kind == "run":
            if rec.status != "ok" or rec.id in invalid:
                problems.append(f"Evidence cites failed run {rid} or a later invalidation")
            for i in rec.input_ids:
                k = L.kind_of(i)
                if k in ("artifact", "measurement"):
                    todo.append((k, i))
                else:
                    problems.append(f"Run {rid} cites missing evidence {i}")
        else:
            producers = makers.get(rid, [])
            successful = [r for r in producers if r.status == "ok" and r.id not in invalid]
            if producers and not successful:
                problems.append(f"Artifact {rid} came only from failed or invalidated runs")
            todo += [("run", r.id) for r in successful]
            if rec.transform:
                for i in rec.transform.parent_ids:
                    k = L.kind_of(i)
                    if k in ("artifact", "measurement"):
                        todo.append((k, i))
                    else:
                        problems.append(f"Artifact {rid} cites missing parent {i}")
    return {"claim": claim.model_dump(mode="json"),
            "measurements": [m.model_dump(mode="json") for m in found["measurement"].values()],
            "runs": [r.model_dump(mode="json") for r in found["run"].values()],
            "artifacts": [a.model_dump(mode="json") for a in found["artifact"].values()],
            "reviews": [r.model_dump(mode="json") for r in L.list("review", ref=claim_id)],
            "problems": problems}


def _events(L, result):
    last, times = {}, {}
    measured, finished = set(), {}
    for cid, seq, body in L.db.execute("SELECT campaign_id, seq, body FROM event ORDER BY rowid"):
        ev = Event.model_validate_json(body)
        if seq != last.get(cid, 0) + 1:
            result.problems.append(f"Event sequence gap or changed order in {cid} at {seq}")
        if ev.seq != seq or ev.campaign_id != cid:
            result.problems.append(f"Event identity changed in {cid} at {seq}")
        if ev.t < times.get(cid, ev.t):
            result.problems.append(f"Event clock went backwards in {cid} at {seq}")
        if L.get("campaign", cid) is None:
            result.problems.append(f"Event {seq} cites missing campaign {cid}")
        if ev.type == "claim.reviewed" and "review" in ev.data:
            recorded = Review.model_validate(ev.data["review"])
            if L.get("review", recorded.id) != recorded:
                result.problems.append(f"Review {recorded.id} differs from its recorded event")
        if ev.type == "measurement.recorded":
            if "measurement" not in ev.data:
                result.problems.append(f"Measurement event {seq} in {cid} lacks its recorded value")
            else:
                recorded = Measurement.model_validate(ev.data["measurement"])
                measured.add(recorded.id)
                if L.get("measurement", recorded.id) != recorded or ev.refs != [recorded.id]:
                    result.problems.append(f"Measurement {recorded.id} differs from its recorded event")
        if ev.type == "tool.finished":
            run = L.get("run", ev.refs[0]) if ev.refs else None
            if run is None:
                result.problems.append(f"Tool completion {seq} in {cid} cites a missing run")
            else:
                mids = run.measurement_ids if run.measurement_ids is not None else \
                    [m.id for m in L.list("measurement", ref=run.id)]
                finished[run.id] = mids
                if (ev.refs != [run.id] + run.output_ids + mids or ev.data.get("status") != run.status or
                        ev.actor != run.tool or ev.campaign_id != run.campaign_id):
                    result.problems.append(f"Run {run.id} differs from its recorded completion")
        last[cid], times[cid] = seq, ev.t
    for rid, mids in finished.items():
        for mid in mids:
            if mid not in measured:
                result.problems.append(f"Measurement {mid} from run {rid} has no recorded event")


def _blobs(L, result):
    artifacts = {a.id: a for a in L.list("artifact")}
    seen = set()
    for path in sorted(L.blobs.rglob("*")):
        if not path.is_file():
            continue
        hexd = path.name
        if IN_FLIGHT.fullmatch(hexd) and path.parent.name == hexd[:2]:
            continue                                   # a writer is storing this blob now; not evidence yet
        if (not HASH.fullmatch(hexd) or path.parent.name != hexd[:2] or
                not path.resolve().is_relative_to(L.blobs.resolve())):
            result.problems.append(f"Blob has an invalid content address: {path.name}")
            continue
        aid = "sha256:" + hexd
        seen.add(aid)
        if sha256_file(path) != hexd:
            result.problems.append(f"Blob bytes changed; do not match content address: {aid}")
        elif aid in artifacts and path.stat().st_size != artifacts[aid].bytes:
            result.problems.append(f"Blob size does not match its record: {aid}")
        else:
            result.files += 1
    for aid in artifacts.keys() - seen:
        result.problems.append(f"Recorded blob is missing: {aid}")


def _recorded_gate(L, cid):
    """The sha256 of the gate code a campaign's setup record names, or None for a campaign recorded without one."""
    for (body,) in L.db.execute("SELECT body FROM event WHERE campaign_id=? AND body LIKE '%campaign.started%' "
                                "ORDER BY seq", (cid,)):
        ev = Event.model_validate_json(body)
        setup = ev.data.get("setup_id") if ev.type == "campaign.started" else None
        if not isinstance(setup, str) or not HASH.fullmatch(setup.removeprefix("sha256:")):
            continue
        try:
            return json.loads(L.blob_path(setup).read_text(encoding="utf-8"))["code"]["files"]["gate.py"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def _gate_rules(L, cid):
    """The gate commit a campaign's findings were recorded under: the one its setup record names, else (campaigns
    recorded before setups named their gate code) the one current when the campaign started. None: no frozen copy
    matches, and the current gate re-checks."""
    recorded = _recorded_gate(L, cid)
    if recorded is not None:
        return past_gates.by_hash(recorded)
    campaign = L.get("campaign", cid)
    return past_gates.in_force(campaign.created_at) if campaign is not None else None


def _reviews(L, result, campaign_id):
    claims = L.list("claim", campaign_id=campaign_id)
    result.findings = len(claims)
    rules = {}
    for claim in claims:
        if claim.campaign_id not in rules:
            rules[claim.campaign_id] = _gate_rules(L, claim.campaign_id)
        commit = rules[claim.campaign_id]
        # intervention findings have their own gate (intervention_gate dispatches them through gate.review)
        rule = gate if commit is None or L.kind_of(claim.id) == "intervention" else past_gates.module(commit)
        reviews = L.db.execute("SELECT rowid, inserted_at, body FROM record WHERE kind='review' AND ref=? "
                               "ORDER BY inserted_at, rowid", (claim.id,)).fetchall()
        gate_reviews = [(row, at, Review.model_validate_json(body)) for row, at, body in reviews
                        if Review.model_validate_json(body).reviewer == "gate"]
        if not gate_reviews:
            result.problems.append(f"Finding {claim.id} has no recorded gate verdict")
            continue
        matches = True
        for row, at, saved in gate_reviews:
            L.db.execute("SAVEPOINT recheck")
            try:
                L.db.execute("DROP TRIGGER IF EXISTS record_no_delete")
                L.db.execute("DROP TRIGGER IF EXISTS inval_no_delete")
                L.db.execute("DELETE FROM record WHERE rowid>=?", (row,))
                L.db.execute("DELETE FROM invalidation WHERE at>?", (at,))
                fresh = rule.review(L, claim.id)
                if (fresh.verdict, fresh.checks, fresh.reason) != (saved.verdict, saved.checks, saved.reason):
                    result.problems.append(f"Finding {claim.id}: recorded {saved.verdict}, re-check gives {fresh.verdict}; "
                                           "verdict, checks or reason differ")
                    matches = False
                if saved.verdict == "supported":
                    problems = evidence(L, claim.id)["problems"]
                    result.problems.extend(f"Finding {claim.id}: {p}" for p in problems)
                    matches = matches and not problems
            finally:
                L.db.execute("ROLLBACK TO recheck")
                L.db.execute("RELEASE recheck")
        latest = gate_reviews[-1][2]
        if rule is not gate:  # recorded under earlier rules: say so when today's gate would decide otherwise
            row, at, _ = gate_reviews[-1]
            L.db.execute("SAVEPOINT today")
            try:
                L.db.execute("DROP TRIGGER IF EXISTS record_no_delete")
                L.db.execute("DROP TRIGGER IF EXISTS inval_no_delete")
                L.db.execute("DELETE FROM record WHERE rowid>=?", (row,))
                L.db.execute("DELETE FROM invalidation WHERE at>?", (at,))
                today = gate.review(L, claim.id).verdict
            finally:
                L.db.execute("ROLLBACK TO today")
                L.db.execute("RELEASE today")
            if today != latest.verdict:
                result.notes.append(f"Finding {claim.id}: {latest.verdict} under the gate it was recorded with "
                                    f"({commit}); today's gate gives {today}")
        if latest.verdict != "unsupported":
            status = L.claim_status(claim.id)
            current = evidence(L, claim.id)["problems"]
            if status == "stale":
                result.problems.append(f"Finding {claim.id}: {latest.verdict} when reviewed, evidence since invalidated")
            result.problems.extend(f"Finding {claim.id}: {p}" for p in current)
            matches = matches and status != "stale" and not current
        result.matched += int(matches)


def _ledger(root, result, campaign_id=None):
    with snapshot(root) as L:
        if L.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            result.problems.append("The ledger database failed its integrity check")
        expected = re.findall(r"CREATE TRIGGER.*?END;", SCHEMA)
        normal = lambda s: " ".join(s.replace("IF NOT EXISTS ", "").rstrip(";").split()).lower()
        actual = {normal(sql) for (sql,) in L.db.execute("SELECT sql FROM sqlite_master WHERE type='trigger'")}
        if any(normal(sql) not in actual for sql in expected):
            result.problems.append("The ledger's insert-only protections are missing or changed")
        for rid, kind, cid, ref, body in L.db.execute("SELECT id, kind, campaign_id, ref, body FROM record"):
            rec = TABLES[kind].model_validate_json(body)
            want_cid = getattr(rec, "campaign_id", None) or (rec.id if kind == "campaign" else None)
            want_ref = getattr(rec, "run_id", None) or getattr(rec, "claim_id", None)
            if (rid, cid, ref) != (rec.id, want_cid, want_ref):
                result.problems.append(f"Record {rid} differs from its stored identity or references")
        _blobs(L, result)
        _events(L, result)
        if campaign_id is not None and L.get("campaign", campaign_id) is None:
            raise ValueError(f"Campaign not found: {campaign_id}")
        _setup_note(L, result, campaign_id)
        _reviews(L, result, campaign_id)
        _forks(L, result, campaign_id)
        _decisions(L, result, campaign_id)
        from translucent.intervention_verify_cache import verification_cache
        # Historical legacy reviews above deliberately change their scratch
        # view. Intervention checks below use one immutable current snapshot.
        with verification_cache(L):
            _interventions(L, result, campaign_id)
        from translucent.loop_verify import check as _loops
        _loops(L, result, campaign_id)


def _interventions(L, result, campaign_id):
    from translucent.intervention import problems as intervention_problems
    from translucent.intervention import records as intervention_records
    from translucent.intervention_bench import evaluate_claim

    result.problems.extend(intervention_problems(L, campaign_id))
    cids = {
        r.campaign_id for r in L.list("intervention")
        if campaign_id is None or r.campaign_id == campaign_id
    }
    for cid in cids:
        rs = intervention_records(L, cid)
        for claim in (r for r in rs if r.type == "claim"):
            result.findings += 1
            reviews = [r for r in rs if r.type == "review" and r.data["claim_id"] == claim.id]
            if not reviews:
                result.problems.append(f"Finding {claim.id} has no recorded intervention gate verdict")
            elif all(r.data == evaluate_claim(L, claim) for r in reviews):
                result.matched += 1
            else:
                result.problems.append(
                    f"Finding {claim.id} has a recorded intervention gate verdict that differs from recomputation"
                )
        result.decisions += sum(
            r.type == "decision" and r.data["scored"] and not r.data.get("parent_decision_id")
            for r in rs
        )


def _forks(L, result, campaign_id):
    """What-if branches: each override must touch the finding it re-examines, and a changed assumption may only
    appear in the branch it was declared for. Nothing is checked on a ledger without forks."""
    from translucent.rerun import unused_overrides
    forks = [TABLES["fork"].model_validate_json(b) for (b,) in
             L.db.execute("SELECT body FROM record WHERE kind='fork' ORDER BY rowid")]
    for fork in forks:
        if campaign_id is not None and campaign_id not in (fork.campaign_id, fork.parent_campaign_id):
            continue
        try:
            if unused_overrides(L, fork):
                result.problems.append(f"Fork {fork.id} overrides a quantity its finding never used")
        except (ValueError, AttributeError):
            result.problems.append(f"Fork {fork.id} re-examines a finding that is not recorded")
    rows = L.db.execute("SELECT body FROM record WHERE kind='run' AND body LIKE '%\"assumption\"%'")
    for (body,) in rows:
        run = TABLES["run"].model_validate_json(body)
        if "assumption" not in run.params or (campaign_id is not None and run.campaign_id != campaign_id):
            continue
        fork = L.get("fork", run.params["assumption"]) if isinstance(run.params["assumption"], str) else None
        if fork is None or fork.campaign_id != run.campaign_id:
            result.problems.append(f"Branch measurement claims a what-if it was not created under (run {run.id})")


def _decisions(L, result, campaign_id):
    """Pre-registered metrics: each protocol precedes its campaign's start, and each recorded decision re-checks to the
    same outcome and checks on the ledger as it stood. Nothing is checked on a ledger without protocols."""
    from translucent import protocol
    for (cid,) in L.db.execute("SELECT DISTINCT campaign_id FROM record WHERE kind='protocol' ORDER BY rowid").fetchall():
        if campaign_id is not None and cid != campaign_id:
            continue
        if why := protocol.pre_registered(L, cid):
            result.problems.append(why)
    rows = L.db.execute("SELECT rowid, inserted_at, body FROM record WHERE kind='decision' ORDER BY rowid").fetchall()
    for row, at, body in rows:
        saved = TABLES["decision"].model_validate_json(body)
        if campaign_id is not None and saved.campaign_id != campaign_id:
            continue
        L.db.execute("SAVEPOINT recheck")
        try:
            L.db.execute("DROP TRIGGER IF EXISTS record_no_delete")
            L.db.execute("DROP TRIGGER IF EXISTS inval_no_delete")
            L.db.execute("DELETE FROM record WHERE rowid>=?", (row,))
            L.db.execute("DELETE FROM invalidation WHERE at>?", (at,))
            outcome, checks = protocol.decide(L, saved.campaign_id, saved.metric_id, saved.winner_claim_id,
                                              saved.verification_claim_id)
            if (outcome, checks) != (saved.outcome, saved.checks):
                result.problems.append(f"Decision {saved.id}: recorded {saved.outcome}, re-check gives {outcome}; "
                                       "outcome or checks differ")
        finally:
            L.db.execute("ROLLBACK TO recheck")
            L.db.execute("RELEASE recheck")
        result.decisions += 1


def _setup_note(L, result, campaign_id):
    """Informational lines naming the gate code that re-checks the findings: the code each campaign's setup record
    names when it has one, else the gate current when a campaign with findings started (past_gates)."""
    recorded, dated = set(), {}
    cids = [cid for (cid,) in L.db.execute("SELECT DISTINCT campaign_id FROM event ORDER BY rowid")]
    claimed = {cid for (cid,) in L.db.execute("SELECT DISTINCT campaign_id FROM record WHERE kind='claim'")}
    for cid in cids:
        if campaign_id is not None and cid != campaign_id:
            continue
        sha = _recorded_gate(L, cid)
        if sha is not None:
            recorded.add(sha)
        elif cid in claimed and (commit := _gate_rules(L, cid)) not in (None, past_gates.VERSIONS[-1][0]):
            dated[cid] = commit
    if dated:
        result.notes.append("No gate code recorded for " + ", ".join(f"{cid} (started under {c})" for cid, c in
                            dated.items()) + "; re-checked with the gate current when each started")
    if not recorded:
        return
    from translucent.runtime import code_version
    now = code_version()["files"]["gate.py"]
    if recorded == {now}:
        result.notes.append(f"Re-checked with the gate code the findings were recorded with ({now[:12]})")
    elif all(past_gates.by_hash(h) for h in recorded):
        result.notes.append("Re-checked with the gate code the findings were recorded with "
                            f"({', '.join(sorted(h[:12] for h in recorded))})")
    else:
        result.notes.append(f"Recorded with gate code {', '.join(sorted(h[:12] for h in recorded))}; "
                            f"re-checked with gate code {now[:12]}")


def _bundle(path, result):
    root = path.parent if path.is_file() else path
    candidate = path if path.is_file() and path.suffix=='.json' else root/'bundle.json'
    head = json.loads(candidate.read_text(encoding='utf-8')) if candidate.is_file() else {}
    if isinstance(head, dict) and head.get('intervention') is not None:
        from translucent.intervention_export import verify_bundle
        return verify_bundle(candidate,result)
    if isinstance(head, dict) and 'loop_export_version' in head:
        from translucent.loop_export import verify_export
        return verify_export(candidate, result, head)
    receipt_path = root / "verification.json"
    if not receipt_path.is_file():
        raise ValueError("Bundle lacks verification data; export it again with translucent export")
    bundle = ReplayBundle.model_validate_json((root / "bundle.json").read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict) or not isinstance(receipt.get("files"), list):
        raise ValueError("Bundle's evidence file list is missing or malformed")
    if receipt.get("version") != 1 or receipt.get("campaign_id") != bundle.campaign.id:
        raise ValueError("Bundle verification manifest has a different version or campaign")
    listed = set()
    for item in receipt["files"]:
        relative = Path(item["path"])
        target = (root / relative).resolve()
        if relative.is_absolute() or not target.is_relative_to(root.resolve()):
            raise ValueError("Bundle manifest path leaves the bundle directory")
        listed.add(relative.as_posix())
        if (not target.is_file() or target.stat().st_size != item["bytes"] or
                sha256_file(target) != item["sha256"]):
            result.problems.append(f"Exported file changed or is missing: {relative.as_posix()}")
    if not {"bundle.json", "movie.mp4", "verification/ledger.sqlite"} <= listed:
        raise ValueError("Bundle manifest omits required evidence files")
    with snapshot(root / "verification") as L:
        if L.get("campaign", bundle.campaign.id) != bundle.campaign:
            result.problems.append("Bundle campaign differs from the evidence snapshot")
        if L.events(bundle.campaign.id) != bundle.events:
            result.problems.append("Bundle events differ from the recorded sequence")
        if L.list("claim", campaign_id=bundle.campaign.id) != bundle.claims:
            result.problems.append("Bundle findings differ from the recorded findings")
        expected_reviews = [r for c in bundle.claims for r in L.list("review", ref=c.id)]
        if bundle.reviews != expected_reviews:
            result.problems.append("Bundle review differs from the recorded reviews")
        for kind, records in (("measurement", bundle.measurements), ("artifact", bundle.artifacts),
                              ("lesson", bundle.lessons), ("run", bundle.runs), ("fork", bundle.forks),
                              ("decision", bundle.decisions)):
            if any(L.get(kind, rec.id) != rec for rec in records):
                result.problems.append(f"Bundle {kind} records differ from the evidence snapshot")
        if bundle.protocol is not None and L.get("protocol", bundle.protocol.id) != bundle.protocol:
            result.problems.append("Bundle protocol differs from the evidence snapshot")
        if not bundle.campaign.inputs or sha256_file(root / "movie.mp4") != \
                bundle.campaign.inputs[0].removeprefix("sha256:"):
            result.problems.append("Bundle movie differs from the recorded video")
    _ledger(root / "verification", result, bundle.campaign.id)


def verify(path, campaign_id=None):
    result = Result()
    try:
        path = Path(path)
        if (path / "ledger.sqlite").is_file():
            _ledger(path, result, campaign_id)
        elif path.name == "ledger.sqlite" and path.is_file():
            _ledger(path.parent, result, campaign_id)
        else:
            _bundle(path, result)
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        result.problems.append(f"Could not re-check evidence: {exc}")
    result.problems = list(dict.fromkeys(" ".join(p.splitlines()) for p in result.problems))
    return result
