"""S4b offline verify, export and view of loop campaigns: a clean campaign verifies; each tamper is detected even when
the forger keeps every digest, link and mirror event consistent; an export verifies and one edited byte of its view
is a problem; verify succeeds while a runner thread appends; the view and the campaign list keep the web contract."""
import json
import sqlite3
import threading
import time

import pytest
from pydantic import TypeAdapter
from support.loop_systems import PREDICTION, LoopCell, objective, register

from translucent import chain, systems
from translucent.cli import main as cli
from translucent.contracts import Event, Measurement, Uncertainty
from translucent.ledger import Ledger
from translucent.loop_contracts import (CampaignSummary, DatasetRecord, LoopRecord, LoopView, Provenance, ScalarSpec,
                                        Specimen, StoredChannel, StoredRecord)
from translucent.loop_export import export
from translucent.loop_runner import LoopRunner
from translucent.loop_service import Actor, LoopService
from translucent.systems.base import Cost
from translucent.verify import verify

ANA = Actor("person", "Ana", "http")
MUSE = Actor("agent", "muse-test", "planner")


@pytest.fixture(autouse=True)
def adapter(monkeypatch):
    return register(monkeypatch)


def bench(L):
    s = L.put_json({"t": [0.0, 600.0, 1200.0], "t_unit": "s", "y": [25.0, 26.0, 26.5], "unit": "degC"},
                   kind="series")
    record = StoredRecord(id="baseline-1", role="baseline", specimen=Specimen(id="cell-1"),
                          configuration={"current_A": 1.0, "ambient_C": 25.0},
                          configuration_units={"current_A": "A", "ambient_C": "degC"}, configuration_hash="0" * 64,
                          conditions={}, conditions_units={}, time=None,
                          channels=[StoredChannel(column="T", quantity="temperature", unit="degC",
                                                  uncertainty=Uncertainty(value=0.1, kind="std", method="spec"),
                                                  series_id=s.id, n=3, t_range=(0.0, 1200.0))],
                          scalars=[ScalarSpec(quantity="charge_time_80", value=14400.0, unit="s", source="log"),
                                   ScalarSpec(quantity="max_temperature", value=26.5, unit="degC", source="log")],
                          media=[], provenance=Provenance(source="test"), files={})
    L.add(DatasetRecord(id="bench", manifest_version=2, manifest_id=s.id, title="t", author="Ana", citation="c",
                        licence="internal", system="loop-test-cell", records=[record], created_at=0.0))


def propose(L, cid, current, approve=True):
    req = LoopService(L, MUSE).propose(cid, "simulate_charge", {"current_A": current, "ambient_C": 25.0},
                                       rationale="Try this current.", prediction=PREDICTION, expected_effect="x")
    if approve:
        LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])
    return req


@pytest.fixture
def campaign(tmp_path):
    """A campaign with a computational baseline, two simulated candidates, a derived measurement and one request
    still awaiting approval. Returns (ledger root, campaign id, ids); the ledger is closed."""
    root = tmp_path / "ledger"
    L = Ledger(root)
    bench(L)
    cid = LoopService(L, ANA).open_campaign(objective(datasets=["bench"], baseline=["bench/baseline-1"],
                                                      baseline_simulation=True), "Faster charge")["campaign_id"]
    fast, slow = propose(L, cid, 2.0), propose(L, cid, 0.8)
    LoopRunner(root, process="cli", workers=1).tick(cid)
    LoopService(L, MUSE).define_measurement(cid, "peak", {"op": "max", "channel": "temperature", "unit": "degC"})
    pending = propose(L, cid, 3.0, approve=False)
    ids = {"fast": fast["request_id"], "slow": slow["request_id"], "pending": pending}
    L.db.close()
    return root, cid, ids


def records(root, cid):
    L = Ledger(root)
    try:
        return chain.records(L, cid)
    finally:
        L.db.close()


def problems_of(root):
    r = verify(root)
    return r, "\n".join(r.problems)


# ------------------------------------------------------------------ the forger's tools

def _open(root):
    db = sqlite3.connect(root / "ledger.sqlite", isolation_level=None)
    triggers = [sql for (sql,) in db.execute("SELECT sql FROM sqlite_master WHERE type='trigger'")]
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
        db.execute(f"DROP TRIGGER {name}")
    return db, triggers


def _close(db, triggers):
    for sql in triggers:                    # the insert-only protections look untouched afterwards
        db.execute(sql)
    db.close()


def rechain(root, cid, mutate):
    """Rewrite the campaign's loop records through `mutate` (a list of record dicts in seq order -> a new list), then
    make everything consistent again: seq and times kept per slot, digests and links recomputed, mirror events
    rewritten, and the record table rebuilt so its stored order follows the new chain."""
    db, triggers = _open(root)
    rows = db.execute("SELECT id, kind, campaign_id, ref, body, inserted_at FROM record ORDER BY rowid").fetchall()
    loops = sorted((LoopRecord.model_validate_json(r[4]) for r in rows if r[1] == "loop" and r[2] == cid),
                   key=lambda r: r.seq)
    slots = [(r.seq, r.created_at) for r in loops]
    rebuilt, previous = [], ""
    for (seq, t), d in zip(slots, mutate([r.model_dump(mode="json") for r in loops])):
        rec = LoopRecord.model_validate(d | {"seq": seq, "created_at": t, "previous_hash": previous, "digest": ""})
        rec = rec.model_copy(update={"digest": chain.record_digest(rec)})
        previous = rec.digest
        rebuilt.append(rec)
        (body,) = db.execute("SELECT body FROM event WHERE campaign_id=? AND seq=?", (cid, seq)).fetchone()
        ev = Event.model_validate_json(body).model_copy(update={
            "refs": [rec.id], "actor": rec.actor, "title": chain.TITLES[rec.type],
            "data": {"record": rec.model_dump(mode="json")}})
        db.execute("UPDATE event SET body=? WHERE campaign_id=? AND seq=?", (ev.model_dump_json(), cid, seq))
    positions = [i for i, r in enumerate(rows) if r[1] == "loop" and r[2] == cid]
    for i, rec in zip(positions, rebuilt):
        rows[i] = (rec.id, "loop", cid, None, rec.model_dump_json(), rows[i][5])
    db.execute("DELETE FROM record")
    db.executemany("INSERT INTO record VALUES (?,?,?,?,?,?)", rows)
    _close(db, triggers)


# ------------------------------------------------------------------ a clean campaign

def test_a_clean_campaign_verifies_and_counts_its_requests(campaign):
    root, cid, _ = campaign
    r, text = problems_of(root)
    assert r.ok, text
    assert r.requests == 4 and r.summary().endswith(", 4 requests re-checked")
    assert r.notes == [] and r.as_dict()["requests"] == 4


def test_a_system_that_is_not_installed_skips_its_domain_checks_only(campaign, monkeypatch):
    root, cid, _ = campaign
    monkeypatch.setattr(systems, "_ADAPTERS", {k: a for k, a in systems._ADAPTERS.items() if k != "loop-test-cell"})
    r, text = problems_of(root)
    assert r.ok, text
    assert f"System loop-test-cell is not installed; domain checks of {cid} skipped" in r.notes



def test_an_adapter_edited_after_the_campaign_is_not_mistaken_for_tampering(campaign):
    root, cid, _ = campaign

    class Edited(LoopCell):                       # its cost model was retuned after the campaign ran
        def code_digest(self):
            return "e" * 64

        def estimate_cost(self, action, params):
            return Cost(wall_s=0.02)
    systems.register(Edited())
    r, text = problems_of(root)
    assert r.ok, text
    assert f"System loop-test-cell installed here differs from the code {cid} registered; domain checks skipped"         in r.notes
    assert any("checked without their system's parameter rules" in n for n in r.notes)

# ------------------------------------------------------------------ tamper tests

def test_an_edited_result_blob_is_detected(campaign):
    root, cid, ids = campaign
    result = next(r for r in records(root, cid) if r.type == "result" and r.data["request_id"] == ids["fast"])
    path = root / "blobs" / result.data["series"]["temperature"].removeprefix("sha256:")[:2] / \
        result.data["series"]["temperature"].removeprefix("sha256:")
    body = json.loads(path.read_text())
    body["y"][-1] -= 1.0
    path.write_text(json.dumps(body))
    r, text = problems_of(root)
    assert not r.ok
    assert "Blob bytes changed" in text
    assert f"Result {result.id}: the temperature series differs from the solver's output" in text


def test_a_raw_approval_with_a_wrong_hash_is_detected(campaign):
    root, cid, ids = campaign
    last = records(root, cid)[-1]
    db = sqlite3.connect(root / "ledger.sqlite", isolation_level=None)     # insert-only allows inserts
    (seq, body) = db.execute("SELECT seq, body FROM event WHERE campaign_id=? ORDER BY seq DESC LIMIT 1",
                             (cid,)).fetchone()
    t = json.loads(body)["t"] + 1
    rec = LoopRecord(id="apr_forged", campaign_id=cid, type="approval", seq=seq + 1, actor="person:Ana",
                     data={"request_id": ids["pending"]["request_id"], "record_id": ids["pending"]["record_id"],
                           "content_hash": "0" * 64, "author": "Ana", "note": "", "rule": None},
                     created_at=t, previous_hash=last.digest, digest="")
    rec = rec.model_copy(update={"digest": chain.record_digest(rec)})
    ev = Event(seq=seq + 1, campaign_id=cid, t=t, type="loop.recorded", actor=rec.actor, title="Experiment approved",
               refs=[rec.id], data={"record": rec.model_dump(mode="json")})
    db.execute("INSERT INTO record VALUES (?,?,?,?,?,?)", (rec.id, "loop", cid, None, rec.model_dump_json(), t))
    db.execute("INSERT INTO event VALUES (?,?,?)", (cid, seq + 1, ev.model_dump_json()))
    db.close()
    r, text = problems_of(root)
    assert not r.ok
    assert "Loop record apr_forged (approval, seq" in text and "This request changed after you read it" in text
    assert "digest" not in text and "differs from its event" not in text          # the chain itself is consistent


def test_a_reordered_record_is_detected(campaign):
    root, cid, ids = campaign

    def approve_before_request(recs):
        i = next(n for n, r in enumerate(recs) if r["type"] == "request" and r["data"]["request_id"] == ids["slow"])
        j = next(n for n, r in enumerate(recs) if r["type"] == "approval" and r["data"]["request_id"] == ids["slow"])
        recs[i], recs[j] = recs[j], recs[i]
        return recs

    rechain(root, cid, approve_before_request)
    r, text = problems_of(root)
    assert not r.ok
    assert "(approval, seq" in text and f"request {ids['slow']} is not a request of this campaign" in text
    assert "digest" not in text and "stored in the order" not in text


def test_a_changed_operator_value_is_detected(campaign):
    root, cid, ids = campaign
    db, triggers = _open(root)
    (mid, body) = db.execute("SELECT id, body FROM record WHERE kind='measurement' AND body LIKE '%derived:peak%' "
                             "AND body LIKE ?", (f"%{ids['fast']}%",)).fetchone()
    m = Measurement.model_validate_json(body)
    forged = m.model_copy(update={"value": m.value - 0.5})
    db.execute("UPDATE record SET body=? WHERE id=?", (forged.model_dump_json(), mid))
    for seq, ev_body in db.execute("SELECT seq, body FROM event WHERE campaign_id=?", (cid,)).fetchall():
        ev = Event.model_validate_json(ev_body)
        if ev.type == "measurement.recorded" and ev.refs == [mid]:      # its event too, so they agree
            ev = ev.model_copy(update={"data": {"value": forged.value, "unit": forged.unit,
                                                "measurement": forged.model_dump(mode="json")}})
            db.execute("UPDATE event SET body=? WHERE campaign_id=? AND seq=?", (ev.model_dump_json(), cid, seq))
    _close(db, triggers)
    r, text = problems_of(root)
    assert not r.ok
    assert f"Measurement {mid} (derived:peak of {ids['fast']}) differs from ops/1 recomputed from its series" in text
    assert "differs from its recorded event" not in text


def test_a_changed_comparison_verdict_is_detected(campaign):
    root, cid, ids = campaign

    def say_not_better(recs):
        for r in recs:
            if r["type"] == "comparison" and r["data"]["candidate"]["request_id"] == ids["fast"]:
                r["data"]["verdict"] = "not_better"
        return recs

    rechain(root, cid, say_not_better)
    r, text = problems_of(root)
    assert not r.ok
    assert "is not what loop-compare/2 computes: recorded not_better, recomputed candidate_better" in text
    assert "digest" not in text and "differs from its event" not in text


def test_a_conclusion_rewritten_to_improved_is_detected(campaign):
    root, cid, _ = campaign
    L = Ledger(root)
    LoopService(L, MUSE).conclude(cid, "Faster in simulation.")
    L.db.close()
    assert verify(root).ok

    def claim_improved(recs):
        recs[-1]["data"]["outcome"] = "improved"
        return recs

    rechain(root, cid, claim_improved)
    r, text = problems_of(root)
    assert "(conclusion, seq" in text and "the outcome is computed from the comparisons: improved_in_simulation" in text


# ------------------------------------------------------------------ export

def test_export_verifies_and_one_edited_byte_of_the_view_is_a_problem(campaign, tmp_path, capsys):
    root, cid, ids = campaign
    L = Ledger(root)
    LoopService(L, MUSE).conclude(cid, "Faster in simulation.")
    path = export(L, cid, tmp_path / "out" / "fastcharge.loop.json", ledger="acme")
    live = LoopService(L, ANA).view(cid, api="http://127.0.0.1:8090", ledger="acme")
    L.db.close()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["loop_export_version"] == 1 and data["receipt"]["campaign_id"] == cid
    source = data["view"]["source"]
    assert source["kind"] == "recorded" and source["file"] == "fastcharge.loop.json" and source["verified"] is None
    assert {k: v for k, v in data["view"].items() if k != "source"} == \
        {k: v for k, v in live.items() if k != "source"}                     # one shape, live or recorded
    r = verify(path)
    assert r.ok, r.problems
    assert r.requests == 4 and r.files == len(data["receipt"]["blobs"])
    assert cli(["verify", str(path)]) == 0
    assert capsys.readouterr().out.startswith(f"0 findings re-checked, 0 match; {r.files} files intact, 4 requests")

    text = path.read_text(encoding="utf-8")
    edited = text.replace("Faster in simulation.", "Faster in simulatiom.", 1)
    assert len(edited) == len(text) and sum(a != b for a, b in zip(text, edited)) == 1
    path.write_text(edited, encoding="utf-8")
    r = verify(path)
    assert r.problems == ["Loop export view differs from the recorded evidence"]
    assert cli(["verify", str(path)]) == 1


def test_an_export_holds_only_its_campaigns_evidence(campaign, tmp_path):
    root, cid, _ = campaign
    L = Ledger(root)
    other = LoopService(L, ANA).open_campaign(objective(), "Another campaign")["campaign_id"]
    propose(L, other, 4.0)
    path = export(L, cid, tmp_path / "x")
    L.db.close()
    assert path.name == f"{cid}.loop.json"
    assert verify(path).ok
    assert other not in path.read_text(encoding="utf-8")


# ------------------------------------------------------------------ verify while a runner appends

def test_verify_succeeds_while_a_runner_thread_appends(campaign):
    root, cid, _ = campaign
    stop, appended, errors = threading.Event(), [], []

    def runner():                                   # 4 of the 5 experiments left (the planner holds one at times)
        L = Ledger(root)
        try:
            for current in (4.0, 5.0, 6.0, 7.0):
                if stop.is_set():
                    break
                req = LoopService(L, MUSE).propose(cid, "simulate_charge", {"current_A": current, "ambient_C": 30.0},
                                                   rationale="Keep going.", prediction={}, expected_effect="x")
                LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])
                LoopRunner(root, process="cli", workers=1).advance(cid, req["request_id"])
                appended.append(req["request_id"])
        except Exception as e:  # noqa: BLE001 - reported below
            errors.append(e)
        finally:
            L.db.close()

    def planner():                                  # many small appends: propose, then withdraw, again and again
        L = Ledger(root)
        try:
            while not stop.is_set():
                req = LoopService(L, MUSE).propose(cid, "simulate_charge", {"current_A": 9.0, "ambient_C": 40.0},
                                                   rationale="Maybe.", prediction={}, expected_effect="x")
                LoopService(L, MUSE).cancel(cid, req["request_id"], "withdrawn")
                withdrawn.append(req["request_id"])
        except Exception as e:  # noqa: BLE001 - reported below
            errors.append(e)
        finally:
            L.db.close()

    withdrawn = []
    L = Ledger(root)                                  # an open writer keeps the write-ahead log in place
    threads = [threading.Thread(target=runner, daemon=True), threading.Thread(target=planner, daemon=True)]
    for thread in threads:
        thread.start()
    try:
        results, during = [], 0
        deadline = time.monotonic() + 300
        while (threads[0].is_alive() or len(results) < 3) and time.monotonic() < deadline:
            alive = threads[0].is_alive()
            results.append(verify(root))
            during += alive and threads[0].is_alive()   # this verify ran wholly while the runner was appending
        assert all(r.ok for r in results), [r.problems for r in results]
        assert during >= 2 and len({r.requests for r in results}) > 1
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=120)
        L.db.close()
    assert not errors, errors
    assert len(appended) == 4 and len(withdrawn) > 20 and verify(root).ok


# ------------------------------------------------------------------ the web data contract

def test_the_view_and_campaign_list_keep_the_contract(campaign):
    root, cid, ids = campaign
    L = Ledger(root)
    try:
        person = LoopService(L, ANA)
        v = person.view(cid, api="http://host", ledger="acme")
        TypeAdapter(LoopView).validate_python(v)
        assert v["source"] | {"generated_at": 0} == {"kind": "live", "api": "http://host", "ledger": "acme",
                                                     "as_of_seq": v["events_cursor"], "generated_at": 0}
        assert v["campaign"]["status"] == "waiting_for_approval" and v["next_action"]["kind"] == "awaiting_approval"
        assert v["next_action"]["request_id"] == ids["pending"]["request_id"]
        assert v["next_action"]["title"] == "Simulate a constant-current charge: current_A 3 A, ambient_C 25 degC"
        assert [b["basis"] for b in v["baseline"]] == ["physical", "computational"]
        assert v["current_result"]["basis"] == "computational" and \
            v["current_result"]["sentence"].startswith("In simulation, ")
        fast = next(r for r in v["requests"] if r["request_id"] == ids["fast"])
        assert {m["quantity"] for m in fast["result"]["measurements"]} == {"charge_time_80", "max_temperature",
                                                                         "derived:peak"}
        assert fast["result"]["series"][0]["url"].startswith("/api/v1/ledgers/acme/artifacts/sha256:")
        assert fast["result"]["prediction_error"]["charge_time_80"] == {"predicted": 3000.0, "measured": 7200.0,
                                                                        "inside": False}
        assert v["measurements_defined"] == [{"name": "peak", "op": "max", "unit": "degC",
                                              "defined_by": "agent:muse-test"}]
        assert v["spend"] == {"model_usd": 0.0, "model_budget_usd": 0.0, "experiments_used": 3,
                              "experiments_budget": 8}
        person.amend(cid, "Cooler.", constraints=[{"quantity": "max_temperature", "op": "<=", "limit": 40.0,
                                                   "unit": "degC", "label": "At or below 40 degC"}])
        LoopService(L, MUSE).cancel(cid, ids["pending"]["request_id"], "no longer needed")
        LoopService(L, MUSE).conclude(cid, "Done.")
        v = person.view(cid)
        TypeAdapter(LoopView).validate_python(v)
        assert v["objective"]["version"] == 2 and v["objective"]["amendments"][0]["changes"] == \
            ["max_temperature 45 -> 40 degC"]
        assert v["campaign"]["status"] == "concluded" and v["next_action"] == {"kind": "none"}
        assert v["conclusion"]["outcome"] == "improved_in_simulation"
        assert v["current_result"]["comparison_id"] == v["conclusion"]["comparison_id"]
        (item,) = person.campaigns(ledger="acme")["items"]
        TypeAdapter(CampaignSummary).validate_python(item)
        assert item | {"updated_at": 0, "created_at": 0} == {
            "id": cid, "ledger": "acme", "kind": "loop", "title": "Faster charge", "status": "concluded",
            "created_at": 0, "updated_at": 0, "objective": objective()["statement"][:140], "next_action": "none",
            "outcome": "improved_in_simulation", "spend_usd": 0.0}
    finally:
        L.db.close()


def test_the_new_modules_never_update_or_delete():
    import re
    from pathlib import Path
    for name in ("loop_compare", "loop_verify", "loop_export", "loop_view"):
        text = (Path(__file__).resolve().parents[1] / "translucent" / f"{name}.py").read_text(encoding="utf-8")
        assert not re.search(r"\b(UPDATE|DELETE)\s", text), name
