"""Loop chain foundations: re-entrant transactions, clamped event time, chain append and admission, the
multi-process append race (ported from the design's chain_proto.py), locks, the LoopView sample, and the gate pin."""
import hashlib
import json
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from translucent import chain
from translucent import ledger as ledger_module
from translucent.contracts import Artifact, Campaign, Event
from translucent.ledger import TABLES, AdmissionError, Ledger
from translucent.locks import ExclusiveFileLock
from translucent.loop_contracts import (PAYLOADS, DatasetRecord, LoopRecord, LoopView, ObjectiveData, Provenance,
                                        RequestData, Specimen, StoredRecord)

ROOT = Path(__file__).resolve().parents[1]
T = time.time()
CID = "loop_0123456789abcdef"


def objective(**kw):
    return ObjectiveData(
        statement="Charge to 80% faster while staying at or below 45 degC.",
        system={"id": "toy", "version": "1", "code_digest": "0" * 64, "spec": {}},
        datasets=["toy-data"], baseline=["toy-data/baseline-1c"],
        target={"quantity": "charge_time_80", "unit": "s", "direction": "minimize"},
        constraints=[{"quantity": "max_temperature", "op": "<=", "limit": 45, "unit": "degC", "label": "45 degC"}],
        quantities=[{"name": "charge_time_80", "unit": "s", "source": "result"},
                    {"name": "max_temperature", "unit": "degC", "source": "result"}],
        budget={"model_usd": 5.0, "experiments": 4, "experiment_usd": 0.0},
        planner={"kind": "scripted", "profile": None, "client": None}, registered_by="Ana",
        **({"evaluator": "loop-compare/2"} | kw))      # milestone 2 made /3 the default; these tests keep /2


def planner(i, **payload):
    return {"phase": "tool_call", "payload": {"i": i, **payload}}


@pytest.fixture
def L(tmp_path):
    led = Ledger(tmp_path / "ledger")
    led.add(Campaign(id=CID, objective="Faster charge", title="Toy", inputs=[], budget_usd=5.0, created_at=T))
    return led


@pytest.fixture
def opened(L):
    chain.append(L, CID, "objective", objective(), "person:Ana", chain.natural_id("obj", CID))
    return L


def other_connection(L, timeout_ms=15000):
    db = sqlite3.connect(L.root / "ledger.sqlite", isolation_level=None)
    db.execute(f"PRAGMA busy_timeout={timeout_ms}")
    return db


def event_count(db, cid=CID):
    return db.execute("SELECT COUNT(*) FROM event WHERE campaign_id=?", (cid,)).fetchone()[0]


# ------------------------------------------------------------------ Ledger.transaction and emit

def test_nested_transactions_and_emit_commit_together(L):
    peer = other_connection(L)
    with L.transaction():
        L.emit(CID, "human.note", "person:Ana", "outer")
        with L.transaction():
            L.emit(CID, "human.note", "person:Ana", "inner")
            assert L.in_transaction()
        assert event_count(peer) == 0          # nothing is visible to another process before the outer commit
    assert not L.in_transaction() and not L.db.in_transaction
    assert [e.title for e in L.events(CID)] == ["outer", "inner"]
    assert event_count(peer) == 2


def test_an_exception_rolls_back_every_level(L):
    with pytest.raises(RuntimeError, match="boom"):
        with L.transaction():
            L.emit(CID, "human.note", "person:Ana", "outer")
            with L.transaction():
                L.emit(CID, "human.note", "person:Ana", "inner")
                raise RuntimeError("boom")
    assert L.events(CID) == [] and not L.db.in_transaction and not L.in_transaction()
    L.emit(CID, "human.note", "person:Ana", "after")   # the ledger is usable afterwards
    assert [e.seq for e in L.events(CID)] == [1]


def test_a_caught_nested_failure_undoes_only_its_own_writes(L):
    with L.transaction():
        L.emit(CID, "human.note", "person:Ana", "kept")
        with pytest.raises(ValueError):
            with L.transaction():
                L.emit(CID, "human.note", "person:Ana", "undone")
                raise ValueError("nested")
        L.emit(CID, "human.note", "person:Ana", "also kept")
    assert [(e.seq, e.title) for e in L.events(CID)] == [(1, "kept"), (2, "also kept")]


def test_emit_outside_a_transaction_commits_at_once_as_before(L):
    ev = L.emit(CID, "campaign.started", "runtime", "Started")
    assert ev.seq == 1 and event_count(other_connection(L)) == 1


def test_emit_inside_a_foreign_raw_transaction_is_still_refused(L):
    L.db.execute("BEGIN")
    try:
        with pytest.raises(sqlite3.OperationalError, match="within a transaction"):
            L.emit(CID, "human.note", "person:Ana", "x")
    finally:
        L.db.execute("ROLLBACK")


def test_emit_never_writes_a_time_earlier_than_the_last_event(L, monkeypatch):
    clock = iter([1000.0, 990.0, 1005.0, 500.0])
    monkeypatch.setattr(ledger_module, "time", type("Clock", (), {"time": staticmethod(lambda: next(clock))}))
    for i in range(4):
        L.emit(CID, "human.note", "person:Ana", f"n{i}")
    assert [e.t for e in L.events(CID)] == [1000.0, 1000.0, 1005.0, 1005.0]


def test_the_clamp_is_per_campaign(L, monkeypatch):
    L.add(Campaign(id="other", objective="o", title="o", inputs=[], budget_usd=1.0, created_at=T))
    clock = iter([2000.0, 1500.0])
    monkeypatch.setattr(ledger_module, "time", type("Clock", (), {"time": staticmethod(lambda: next(clock))}))
    L.emit(CID, "human.note", "person:Ana", "late")
    L.emit("other", "human.note", "person:Ana", "earlier clock, other campaign")
    assert L.events("other")[0].t == 1500.0


def test_event_helpers_refuse_to_run_outside_a_transaction(L):
    with pytest.raises(RuntimeError):
        L.next_event(CID)
    with pytest.raises(RuntimeError):
        L.write_event(Event(seq=1, campaign_id=CID, t=T, type="human.note", actor="a", title="t"))


def test_a_held_write_lock_serializes_another_writer(opened):
    peer = Ledger.__new__(Ledger)
    peer.root, peer.blobs = opened.root, opened.blobs
    peer.db = other_connection(opened, timeout_ms=50)
    with opened.transaction():
        chain.append(opened, CID, "planner", planner(0), "agent:t", "pl_0")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            chain.append(peer, CID, "planner", planner(1), "agent:t", "pl_1")
    assert not peer.in_transaction() and not peer.db.in_transaction
    chain.append(peer, CID, "planner", planner(1), "agent:t", "pl_1")
    assert [r.id for r in chain.records(opened, CID)][1:] == ["pl_0", "pl_1"] and chain.problems(opened, CID) == []


# ------------------------------------------------------------------ chain append and admission

def test_append_links_records_and_mirrors_each_as_an_event(opened):
    L = opened
    L.emit(CID, "cost.updated", "runtime", "Spend updated")     # other events take seq numbers between loop records
    second = chain.append(L, CID, "planner", planner(1), "agent:muse", "pl_1")
    first = chain.records(L, CID)[0]
    assert (first.seq, second.seq) == (1, 3)
    assert second.previous_hash == first.digest == chain.record_digest(first)
    ev = L.events(CID)[2]
    assert ev.type == "loop.recorded" and ev.refs == ["pl_1"] and ev.t == second.created_at
    assert ev.data["record"] == second.model_dump(mode="json") and ev.title == chain.TITLES["planner"]
    assert L.get("loop", "pl_1") == second and chain.latest(L, CID) == second
    assert chain.problems(L, CID) == []


def test_identical_replay_returns_the_stored_record(opened):
    rec, created = chain.write(opened, CID, "planner", planner(1), "agent:muse", "pl_1")
    again, created_again = chain.write(opened, CID, "planner", planner(1), "agent:muse", "pl_1")
    assert created and not created_again and again == rec
    assert event_count(opened.db) == 2


@pytest.mark.parametrize("change", [dict(data=planner(2)), dict(actor="agent:other"), dict(type="failure")])
def test_a_different_body_under_the_same_id_is_a_conflict(opened, change):
    chain.append(opened, CID, "planner", planner(1), "agent:muse", "pl_1")
    args = dict(type="planner", data=planner(1), actor="agent:muse") | change
    if args["type"] == "failure":
        args["data"] = {"request_id": "rq", "attempt": 1, "error": "x", "retryable": False}
    with pytest.raises(chain.ChainConflict, match="already records something else"):
        chain.append(opened, CID, args["type"], args["data"], args["actor"], "pl_1")
    assert event_count(opened.db) == 2 and not opened.db.in_transaction


def test_an_id_held_by_another_record_kind_is_a_conflict(opened):
    opened.add(Artifact(id="sha256:" + "a" * 64, kind="text", media_type="text/plain", bytes=1, created_at=T))
    with pytest.raises(chain.ChainConflict):
        chain.append(opened, CID, "planner", planner(1), "agent:muse", "sha256:" + "a" * 64)


def test_the_campaign_starts_with_its_objective_once(L):
    with pytest.raises(AdmissionError, match="starts with its objective"):
        chain.append(L, CID, "planner", planner(0), "agent:muse", "pl_0")
    chain.append(L, CID, "objective", objective(), "person:Ana", "obj_1")
    with pytest.raises(AdmissionError, match="registered once, first"):
        chain.append(L, CID, "objective", objective(), "person:Ana", "obj_2")
    assert [r.id for r in chain.records(L, CID)] == ["obj_1"] and event_count(L.db) == 1


def test_loop_records_need_a_recorded_campaign(L):
    with pytest.raises(AdmissionError, match="loop campaign"):
        chain.append(L, "loop_missing", "objective", objective(), "person:Ana", "obj_x")


def test_writes_after_the_conclusion_are_refused_except_planner_receipts(opened):
    conclusion = {"outcome": "unresolved", "comparison_id": None, "text": "Budget spent.",
                  "unresolved_reasons": ["budget spent"], "author": "agent:muse"}
    done = chain.append(opened, CID, "conclusion", conclusion, "agent:muse", chain.natural_id("con", CID))
    with pytest.raises(AdmissionError, match=f"concluded at seq {done.seq}"):
        chain.append(opened, CID, "cancellation", {"request_id": "rq", "author": "Ana", "reason": "late"},
                     "person:Ana", "cnl_1")
    chain.append(opened, CID, "planner", {"phase": "turn_finished", "payload": {}}, "agent:muse", "pl_end")
    assert chain.problems(opened, CID) == []


@pytest.mark.parametrize("type, data, match", [
    ("bogus", {}, "unknown loop record type"),
    ("planner", {"phase": "dreaming", "payload": {}}, "phase"),
    ("planner", {"phase": "tool_call", "payload": {}, "extra": 1}, "extra"),
    ("failure", {"request_id": "rq", "attempt": 1, "error": "x", "retryable": False, "seq": 3}, "seq"),
    ("approval", {"request_id": "rq", "record_id": "req_1", "author": "Ana"}, "content_hash"),
])
def test_payloads_are_validated_before_anything_is_written(opened, type, data, match):
    with pytest.raises(AdmissionError, match=match):
        chain.append(opened, CID, type, data, "agent:muse", "x_1")
    assert event_count(opened.db) == 1


def test_non_finite_numbers_are_refused(opened):
    # JSON mode would otherwise store NaN as null without a word
    with pytest.raises(AdmissionError, match="finite"):
        chain.append(opened, CID, "planner", {"phase": "tool_call", "payload": {"x": float("nan")}}, "a", "x_1")
    with pytest.raises(AdmissionError, match="finite"):
        chain.append(opened, CID, "comparison", {"evaluator": "loop-compare/1", "objective_version": 1,
                                                 "basis": "computational", "baseline": {"h": float("inf")},
                                                 "candidate": {}, "target": {}, "constraints": [],
                                                 "verdict": "inconclusive", "reasons": []}, "a", "x_2")
    assert event_count(opened.db) == 1 and not opened.db.in_transaction


def test_payload_is_stored_in_canonical_form(opened, monkeypatch):
    monkeypatch.setattr(chain, "HOOK_MODULES", ())      # the lifecycle hooks would refuse an approval of nothing
    monkeypatch.setattr(chain, "_HOOKS", {})
    rec = chain.append(opened, CID, "approval", {"request_id": "rq", "record_id": "req_1", "content_hash": "h",
                                                 "author": "Ana"}, "person:Ana", "apr_1")
    assert rec.data == {"request_id": "rq", "record_id": "req_1", "content_hash": "h", "author": "Ana", "note": "",
                        "rule": None}


def test_direct_add_outside_append_is_refused(opened):
    prev = chain.latest(opened, CID)
    rec = LoopRecord(id="pl_x", campaign_id=CID, type="planner", seq=2, actor="a", data=planner(0), created_at=T,
                     previous_hash=prev.digest, digest="")
    rec = rec.model_copy(update={"digest": chain.record_digest(rec)})
    with pytest.raises(AdmissionError, match="written with chain.append"):
        opened.add(rec)


@pytest.mark.parametrize("tamper, match", [
    (dict(previous_hash="0" * 64), "hash chain differs"),
    (dict(digest="0" * 64), "hash chain differs"),
    (dict(seq=5), "next event"),
    (dict(data={"phase": "tool_call", "payload": {}, "note": "x"}), "Extra inputs"),
    (dict(type="approval", data={"request_id": "rq", "record_id": "r", "content_hash": "h", "author": "Ana"}),
     "canonical form"),                                     # valid, but not stored as the payload's JSON form
])
def test_admission_refuses_a_forged_record(opened, tamper, match):
    prev = chain.latest(opened, CID)
    fields = dict(id="pl_x", campaign_id=CID, type="planner", seq=2, actor="a", data=planner(0), created_at=T,
                  previous_hash=prev.digest, digest="") | {k: v for k, v in tamper.items() if k != "digest"}
    rec = LoopRecord(**fields)
    rec = rec.model_copy(update={"digest": tamper.get("digest") or chain.record_digest(rec)})
    with opened.transaction():
        with pytest.raises(AdmissionError, match=match):
            opened.add(rec)
    assert chain.problems(opened, CID) == [] and event_count(opened.db) == 1


def test_the_offline_check_finds_a_forked_or_unmirrored_chain(opened):
    a = chain.append(opened, CID, "planner", planner(1), "agent:muse", "pl_1")
    assert chain.problems(opened, CID) == []
    # what a writer without BEGIN IMMEDIATE produces: a second record linked to the same predecessor
    fork = LoopRecord(id="pl_fork", campaign_id=CID, type="planner", seq=3, actor="agent:muse", data=planner(9),
                      created_at=T, previous_hash=a.previous_hash, digest="")
    fork = fork.model_copy(update={"digest": chain.record_digest(fork)})
    opened.db.execute("INSERT INTO record VALUES (?,?,?,?,?,?)", (fork.id, "loop", CID, None, fork.model_dump_json(), T))
    found = chain.problems(opened, CID)
    assert any("does not link" in p for p in found) and any("differs from its event" in p for p in found)


def test_admission_hooks_run_inside_the_append_transaction(opened, monkeypatch):
    seen = []
    monkeypatch.setattr(chain, "HOOK_MODULES", ())      # only this test's hook, not the lifecycle's
    monkeypatch.setattr(chain, "_HOOKS", {})

    @chain.admission("request")
    def no_hot_requests(L, rec, req):
        seen.append((L.in_transaction(), type(req).__name__, chain.latest(L, rec.campaign_id).type))
        if req.params.get("c_rate_1", 0) > 3:
            raise AdmissionError("above the datasheet limit")

    def request(rate, rid):
        body = dict(request_id=rid, version=1, supersedes=None, action="simulate", executor="computational",
                    params={"c_rate_1": rate}, units={"c_rate_1": "1/h"}, configuration_hash="c" * 64,
                    rationale="why", prediction={}, expected_effect="what changes", cost={"usd": 0},
                    objective_version=1, content_hash="")
        return body | {"content_hash": chain.content_hash(body)}

    with pytest.raises(AdmissionError, match="datasheet"):
        chain.append(opened, CID, "request", request(3.5, "rq_a"), "agent:muse", "req_a")
    assert event_count(opened.db) == 1 and opened.get("loop", "req_a") is None
    rec = chain.append(opened, CID, "request", request(2.5, "rq_b"), "agent:muse", "req_b")
    assert isinstance(chain.typed(rec), RequestData) and chain.typed(rec).content_hash == chain.content_hash(rec.data)
    assert seen == [(True, "RequestData", "objective")] * 2


def test_hook_registration_is_checked_and_replaces_a_reimported_hook(monkeypatch):
    monkeypatch.setattr(chain, "HOOK_MODULES", ())
    monkeypatch.setattr(chain, "_HOOKS", {})
    with pytest.raises(ValueError, match="unknown loop record type"):
        chain.admission("requests")
    for _ in range(2):
        @chain.admission("approval", "rejection")
        def check(L, rec, data):
            pass
    assert len(chain.hooks("approval")) == len(chain.hooks("rejection")) == 1


def test_hook_modules_are_imported_before_admission(opened, monkeypatch, tmp_path):
    (tmp_path / "s0_hook_module.py").write_text(textwrap.dedent("""
        from translucent import chain
        from translucent.ledger import AdmissionError

        @chain.admission("cancellation")
        def refuse(L, rec, data):
            raise AdmissionError("cancellations are refused by s0_hook_module")
    """))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(chain, "_HOOKS", {})
    monkeypatch.setattr(chain, "HOOK_MODULES", ("s0_hook_module",))
    monkeypatch.delitem(sys.modules, "s0_hook_module", raising=False)
    with pytest.raises(AdmissionError, match="s0_hook_module"):
        chain.append(opened, CID, "cancellation", {"request_id": "rq", "author": "Ana", "reason": "r"},
                     "person:Ana", "cnl_1")


def test_ids_and_hashes_are_deterministic():
    assert chain.natural_id("apr", "rq_1", "h") == chain.natural_id("apr", "rq_1", "h") != \
        chain.natural_id("apr", "rq_1", "h2")
    assert chain.natural_id("res", "rq_1").startswith("res_") and len(chain.natural_id("res", "rq_1")) == 28
    assert chain.key_id("req", CID, "request", "agent", "k1") == chain.natural_id("req", CID, "request", "agent", "k1")
    assert chain.canonical({"b": 1, "a": [1.5, None]}) == b'{"a":[1.5,null],"b":1}'
    body = dict(request_id="rq_1", version=1, supersedes=None, action="simulate", executor="computational",
                params={"c_rate_1": 2}, units={"c_rate_1": "1/h"}, configuration_hash="c", rationale="r",
                prediction={}, expected_effect="e", cost={}, objective_version=1)
    stored = RequestData.model_validate(body | {"content_hash": "ignored"})
    # the hash is over the stored JSON form: defaults filled in, 2 stored as 2.0, content_hash left out
    assert chain.content_hash(body) == chain.content_hash(stored) == chain.digest(
        {k: v for k, v in stored.model_dump(mode="json").items() if k != "content_hash"})
    assert chain.content_hash(body | {"rationale": "other"}) != chain.content_hash(body)
    with pytest.raises(ValueError):
        chain.canonical({"x": float("inf")})


def test_every_loop_type_has_a_payload_and_a_title():
    assert set(PAYLOADS) == set(chain.TITLES) == set(LoopRecord.model_fields["type"].annotation.__args__)
    assert all(len(t) <= 90 for t in chain.TITLES.values())


# ------------------------------------------------------------------ the dataset record kind

def test_dataset_and_loop_are_ledger_kinds_and_datasets_are_ledger_scoped(L):
    assert TABLES["dataset"] is DatasetRecord and TABLES["loop"] is LoopRecord
    manifest = L.put_json({"manifest_version": 2}, kind="text")
    record = StoredRecord(id="baseline-1c", role="baseline", specimen=Specimen(id="cell-1"), configuration={},
                          configuration_units={}, configuration_hash="0" * 64, conditions={}, conditions_units={},
                          time=None, channels=[], scalars=[], media=[], provenance=Provenance(source="test"),
                          files={"a.csv": manifest.id})
    ds = DatasetRecord(id="toy-data", manifest_version=2, manifest_id=manifest.id, title="t", author="Ana",
                       citation="c", licence="internal", system=None, records=[record], created_at=T)
    L.add(ds)
    assert L.get("dataset", "toy-data") == ds and L.list("dataset") == [ds]
    assert L.db.execute("SELECT campaign_id FROM record WHERE id='toy-data'").fetchone() == (None,)
    with pytest.raises(AdmissionError, match="already exists"):
        L.add(ds)
    missing = ds.model_copy(update={"id": "other", "manifest_id": "sha256:" + "9" * 64})
    with pytest.raises(AdmissionError, match="dataset manifest"):
        L.add(missing)


# ------------------------------------------------------------------ the multi-process race (design §2.10)

WORKER = """
import json, sys, time
from pathlib import Path
from translucent import chain
from translucent.ledger import Ledger

root, cid, w, n, go = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), Path(sys.argv[5])
L = Ledger(root)
while not go.exists():
    time.sleep(0.005)
out = {"inserted": 0, "existing": 0, "notes": 0}
for i in range(n):
    own = chain.write(L, cid, "planner", {"phase": "tool_call", "payload": {"w": w, "i": i}}, "agent:race", f"w{w}-{i}")
    out["inserted" if own[1] else "existing"] += 1
    shared = chain.write(L, cid, "planner", {"phase": "tool_call", "payload": {"i": i}}, "agent:race", f"shared-{i}")
    out["inserted" if shared[1] else "existing"] += 1
    if i % 10 == 0:   # other events of the campaign interleave with the chain
        L.emit(cid, "human.note", "person:race", f"note {w}/{i}")
        out["notes"] += 1
print(json.dumps(out))
"""


def test_four_process_append_race_leaves_one_linear_mirrored_chain(opened, tmp_path):
    procs, n = 4, 150
    go = tmp_path / "go"
    workers = [subprocess.Popen([sys.executable, "-c", WORKER, str(opened.root), CID, str(w), str(n), str(go)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT)
               for w in range(procs)]
    time.sleep(1.0)        # let every worker import and open the ledger, then start them together
    go.touch()
    outs = []
    for p in workers:
        stdout, stderr = p.communicate(timeout=300)
        assert p.returncode == 0, stderr
        outs.append(json.loads(stdout))

    recs = chain.records(opened, CID)
    assert len(recs) == 1 + procs * n + n                       # objective + own + one per shared natural id
    assert sum(o["inserted"] for o in outs) == procs * n + n
    assert sum(o["existing"] for o in outs) == (procs - 1) * n   # every shared id: one insert, three replays
    assert chain.problems(opened, CID) == []                     # linear chain, every record mirrored at its seq
    events = opened.events(CID)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert len(events) == len(recs) + sum(o["notes"] for o in outs)
    assert all(a.t <= b.t for a, b in zip(events, events[1:]))  # verify's clock rule holds across processes
    loop_events = [e for e in events if e.type == "loop.recorded"]
    assert [e.refs[0] for e in loop_events] == [r.id for r in recs] and [e.seq for e in loop_events] == \
        [r.seq for r in recs]


# ------------------------------------------------------------------ locks

def test_exclusive_lock_within_one_process(tmp_path):
    path = tmp_path / "locks" / "req-abc.lock"
    a, b = ExclusiveFileLock(path), ExclusiveFileLock(path)
    assert a.try_acquire() and a.held and a.try_acquire()
    assert not b.try_acquire() and not b.held
    a.release()
    a.release()
    assert b.try_acquire()
    with pytest.raises(RuntimeError, match="held by another process"):
        with ExclusiveFileLock(path):
            pass
    b.release()
    with ExclusiveFileLock(path) as c:
        assert c.held
    assert not c.held


def test_a_dead_holder_releases_the_lock(tmp_path):
    path = tmp_path / "locks" / "turn.lock"
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import sys, time
        from translucent.locks import ExclusiveFileLock
        lock = ExclusiveFileLock({str(path)!r})
        assert lock.try_acquire()
        print("held", flush=True)
        time.sleep(60)
    """)], stdout=subprocess.PIPE, text=True, cwd=ROOT)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert not ExclusiveFileLock(path).try_acquire()
    finally:
        holder.kill()
        holder.wait(timeout=30)
    # Windows releases a killed process's byte-range locks asynchronously, with no stated bound.
    lock = ExclusiveFileLock(path)
    deadline = time.monotonic() + 5
    while not lock.try_acquire():
        assert time.monotonic() < deadline, "the dead holder's lock was not released within 5 s"
        time.sleep(0.05)
    lock.release()


# ------------------------------------------------------------------ web data contract

def test_loop_view_sample_matches_the_contract():
    view = json.loads((ROOT / "tests" / "data" / "loop_view_sample.json").read_text(encoding="utf-8"))
    TypeAdapter(LoopView).validate_python(view, strict=False)
    with pytest.raises(Exception):
        TypeAdapter(LoopView).validate_python(view | {"unexpected": 1})
    requests = {r["request_id"]: r for r in view["requests"]}
    comparisons = {c["id"]: c for c in view["comparisons"]}
    assert view["conclusion"]["comparison_id"] in comparisons
    assert view["current_result"]["comparison_id"] in comparisons
    assert all(c["request_id"] in requests for c in comparisons.values())
    assert view["source"]["as_of_seq"] == view["events_cursor"]
    for r in requests.values():
        seqs = [s["seq"] for s in r["timeline"]]
        assert seqs == sorted(seqs) and r["timeline"][-1]["state"] == r["state"]
        assert set(r["params"]) == {"c_rate_1", "switch_soc"}    # the toy system has two parameters


# ------------------------------------------------------------------ gate.py stays byte-identical

def test_gate_is_byte_identical():
    # line endings normalised as runtime.code_version does, so a Windows and a Linux checkout agree
    body = (ROOT / "translucent" / "gate.py").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(body).hexdigest() == "4245d5a3599bd946e790de43910a36fb92e244de32b2d95aa2e34ecef1cac8c9"


def test_a_writer_that_loses_the_lock_past_busy_timeout_keeps_waiting(tmp_path):
    """Under load, SQLite can starve one writer past busy_timeout; transaction() retries instead of failing."""
    import threading
    L = Ledger(tmp_path / "l")
    L.db.execute("PRAGMA busy_timeout=50")
    holder = sqlite3.connect(tmp_path / "l" / "ledger.sqlite", isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    threading.Timer(0.6, holder.execute, args=("COMMIT",)).start()
    t0 = time.monotonic()
    with L.transaction():
        pass
    assert time.monotonic() - t0 >= 0.5
    holder.close()
