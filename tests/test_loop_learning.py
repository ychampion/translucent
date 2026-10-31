"""S8 learning capture and export: one schema-valid episode per request version (plus amendment episodes) derived
from the ledger alone; other ledgers' campaigns excluded; free text scrubbed; series omitted unless asked; a
deterministic, recorded held-out split; the engineer's corrections on revised and rejected episodes; and a lesson
scoped to a system loading in a later loop campaign on that system and not on another."""
import json
import time

import pytest
from support.loop_systems import LABELLED_SPEC, LOOP_SPEC, PREDICTION, LabelledCell, LoopCell, objective, register

from translucent import chain, exec_child, loop_learning, loop_runner, memory, systems
from translucent.cli import main as cli
from translucent.contracts import Lesson, Uncertainty
from translucent.executors import ingest_output
from translucent.ledger import Ledger
from translucent.loop import RUNNER, request_states
from translucent.loop_contracts import DatasetRecord, Provenance, ScalarSpec, Specimen, StoredChannel, StoredRecord
from translucent.loop_learning import Episode, Header, export, heldout_campaigns, heldout_from, read
from translucent.loop_planner import ScriptedLoopPlanner, planner_context, planner_tick
from translucent.loop_runner import LoopRunner
from translucent.loop_service import Actor, LoopService

ANA = Actor("person", "Ana", "http")
MUSE = Actor("agent", "muse-test", "planner")
SECRET_KEY = "sk-ABCDEFGHIJKLMNOPqrstuv1234"
SECRET_TOKEN = "Bearer abcDEF123456ghiJKL"
SECRET_ASSIGNED = "api_key=Zx9Yw8Vu7T6s"
CHILD = loop_runner.run_computational                 # the real child-process path, kept before `inline` patches it
PREDICT = PREDICTION | {"max_temperature": {"value": 30.0, "lo": 25.0, "hi": 45.0, "unit": "degC"}}


@pytest.fixture(autouse=True)
def adapters(monkeypatch):
    register(monkeypatch, LabelledCell)
    systems.register(LoopCell())


@pytest.fixture(autouse=True)
def inline(monkeypatch):
    """Run computational requests in this process (no child), as test_loop_planner's `inline` does."""
    def run(L, cid, req, adapter, *, worker, workdir, timeout_s):
        out = adapter.execute(req.action, dict(req.data.params), workdir)
        body = exec_child.encode_output(out)
        body.pop("files")
        return ingest_output(L, cid, req, out, adapter=adapter, basis="computational", submitted_by=RUNNER,
                             attempt=req.attempt, payload_sha256=chain.digest(body), run_params={"worker": worker})
    monkeypatch.setattr(loop_runner, "run_computational", run)
    monkeypatch.setattr(LoopRunner, "worker", property(lambda self: {"host": "test", "pid": 0,
                                                                     "process": self.process}))


def bench(L, system):
    if L.get("dataset", "bench") is not None:
        return
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
                        licence="internal", system=system, records=[record], created_at=0.0))


def run(L, cid):
    runner = LoopRunner(L.root, process="cli", workers=1)
    while runner.tick(cid):
        pass
    assert not runner.problems, runner.problems


def proposal(current):
    return ("propose_experiment", {
        "action": "labelled_charge", "params": {"current_A": current, "ambient_C": 25.0},
        "hypothesis": "Joule heating sets the peak temperature.", "prediction": PREDICT,
        "rationale": f"Try {current:g} A. The lab key {SECRET_KEY} was pasted here by mistake.",
        "expected_effect": "Under the limit, this current is the candidate."})


def tick(L, cid, planner):
    return planner_tick(L.root, lambda *_: planner, cid)


def campaign_with_corrections(L):
    """A scripted planner on the labelled cell, with a computational baseline: 2 A runs; 4 A is revised by Ana to
    3 A and approved; 6 A is rejected; Ana tightens the temperature limit; a direct 9 A request fails."""
    bench(L, LABELLED_SPEC.id)
    cid = LoopService(L, ANA).open_campaign(
        objective(system={"id": LABELLED_SPEC.id}, datasets=["bench"], baseline=["bench/baseline-1"],
                  baseline_simulation=True, planner={"kind": "scripted", "profile": None, "client": None}),
        "Labelled")["campaign_id"]
    run(L, cid)                                          # the registration re-run of the baseline
    planner = ScriptedLoopPlanner([[proposal(2.0)], [proposal(4.0)], [proposal(6.0)]])

    def latest():
        return max(request_states(L, cid).values(), key=lambda s: s.versions[0].seq)
    tick(L, cid, planner)
    first = latest()
    LoopService(L, ANA).approve(cid, first.request_id, first.content_hash)
    run(L, cid)
    tick(L, cid, planner)
    second = latest()
    LoopService(L, ANA).revise(cid, second.request_id, {"current_A": 3.0},
                               f"4 A is past the connector rating; {SECRET_ASSIGNED}", approve=True)
    run(L, cid)
    tick(L, cid, planner)
    third = latest()
    LoopService(L, ANA).reject(cid, third.request_id, third.content_hash, f"Too hot for this pack. {SECRET_TOKEN}")
    LoopService(L, ANA).amend(cid, "The pack needs 3 K more headroom.",
                              constraints=[{"quantity": "max_temperature", "op": "<=", "limit": 42.0, "unit": "degC",
                                            "label": "Cell stays at or below 42 degC"}])
    fail = LoopService(L, MUSE).propose(cid, "labelled_charge", {"current_A": 9.0, "ambient_C": 25.0},
                                        rationale="Probe the top of the range.", prediction=PREDICT,
                                        expected_effect="e")
    LoopService(L, ANA).approve(cid, fail["request_id"], fail["content_hash"])
    inline, loop_runner.run_computational = loop_runner.run_computational, CHILD
    try:                                                 # a real child: the garbled category fails the request
        run(L, cid)
    finally:
        loop_runner.run_computational = inline
    return cid, {"first": first.request_id, "revised": second.request_id, "rejected": third.request_id,
                 "failed": fail["request_id"]}


@pytest.fixture
def acme(tmp_path):
    L = Ledger(tmp_path / "acme")
    cid, ids = campaign_with_corrections(L)
    yield L, cid, ids
    L.db.close()


def by_request(eps, rq):
    return sorted((e for e in eps if e["kind"] == "request" and e["request_id"] == rq),
                  key=lambda e: e["proposal"]["version"])


# ------------------------------------------------------------------ 1. the schema; one episode per request version

def test_every_line_validates_and_there_is_one_episode_per_request_version(acme, tmp_path):
    L, cid, ids = acme
    out = tmp_path / "out" / "acme.jsonl"
    done = export(L, scope="ledger:acme", out=out)
    lines = out.read_text(encoding="utf-8").splitlines()
    header = Header.model_validate_json(lines[0])
    assert header.schema_ == "translucent.learning/1" and header.customer_scope == "ledger:acme"
    eps = [Episode.validate_json(x).model_dump(mode="json") for x in lines[1:]]
    versions = [r.id for r in chain.records(L, cid) if r.type == "request"]
    assert sorted(e["episode_id"] for e in eps if e["kind"] == "request") == sorted(versions)
    assert done["episodes"] == len(lines) - 1 == len(versions) + 1          # one amendment
    assert header.counts == {"request_versions": len(versions), "amendments": 1}
    receipt = header.receipts[0]
    assert receipt.campaign_id == cid and receipt.chain_head_sha256 == chain.latest(L, cid).digest
    # an extra field or a wrong decision is refused by the schema
    bad = json.loads(lines[1]) | {"surprise": 1}
    with pytest.raises(ValueError):
        Episode.validate_python(bad)

    first = by_request(eps, ids["first"])[0]
    assert first["proposal"]["by"] == "agent:scripted" and first["engineer"]["decision"] == "approved"
    assert first["context"]["context_sha256"] and first["context"]["turn"] == 1
    assert first["context"]["seen_record_ids"] and all(
        L.get("loop", i).type != "planner" for i in first["context"]["seen_record_ids"])
    assert first["outcome"]["state"] == "completed" and first["outcome"]["categories"] == {"run_status": "ok"}
    assert first["outcome"]["values"]["charge_time_80"]["unit"] == "s"
    check = first["validation"]["prediction"]["charge_time_80"]
    assert check["abs_err"] == pytest.approx(abs(check["predicted"] - check["measured"]))
    assert first["validation"]["comparison"]["verdict"] in ("candidate_better", "not_better", "violates_constraints",
                                                            "inconclusive")
    assert first["labels"]["changed_best"] is not None and first["labels"]["violated_constraint"] is not None
    failed = by_request(eps, ids["failed"])[0]
    assert failed["outcome"]["state"] == "failed" and "garbled" in failed["outcome"]["failure"]["error"]
    assert failed["context"]["context_sha256"] is None             # proposed outside a planner turn
    registration = [e for e in eps if e["kind"] == "request" and e["proposal"]["by"] == "policy:registration"]
    assert registration and registration[0]["engineer"]["decision"] == "auto_approved"
    assert read(out)[1] == eps


# ------------------------------------------------------------------ 2. scope, scrubbing, series

def test_other_ledgers_campaigns_are_excluded_and_the_scope_must_name_this_ledger(acme, tmp_path):
    L, cid, _ = acme
    other = Ledger(tmp_path / "globex")
    try:
        ocid = LoopService(other, ANA).open_campaign(objective(system={"id": LOOP_SPEC.id}), "Other")["campaign_id"]
        LoopService(other, MUSE).propose(ocid, "simulate_charge", {"current_A": 2.0, "ambient_C": 25.0},
                                         rationale="r", prediction=PREDICTION, expected_effect="e")
    finally:
        other.db.close()
    done = export(L, scope="ledger:acme", campaign_ids=[cid, ocid], out=tmp_path / "mixed.jsonl")
    header, eps = read(done["path"])
    assert header["campaign_ids"] == [cid]
    assert header["excluded"] == [{"campaign_id": ocid, "reason": "not a loop campaign of ledger acme"}]
    assert {e["campaign_id"] for e in eps} == {cid}
    with pytest.raises(ValueError, match="does not name this ledger"):
        export(L, scope="ledger:globex", campaign_ids=[ocid], out=tmp_path / "x.jsonl")


def test_free_text_is_scrubbed_and_series_are_omitted_unless_asked(acme, tmp_path):
    L, cid, ids = acme
    raw = json.dumps([r.data for r in chain.records(L, cid)])
    for secret in (SECRET_KEY, "Zx9Yw8Vu7T6s", "abcDEF123456ghiJKL"):
        assert secret not in raw                  # scrubbed where it entered: the insert-only ledger never held it
    path = tmp_path / "default.jsonl"
    export(L, scope="ledger:acme", out=path)
    text = path.read_text(encoding="utf-8")
    for secret in (SECRET_KEY, "Zx9Yw8Vu7T6s", "abcDEF123456ghiJKL"):
        assert secret not in text
    assert "[secret removed]" in text
    header, eps = read(path)
    assert header["redaction"] == {"free_text": "scrubbed", "series": "omitted"}
    completed = [e for e in eps if e["kind"] == "request" and e["outcome"]["result_id"]]
    assert completed and all(e["outcome"]["series"] is None for e in completed)
    series_ids = {aid for st in request_states(L, cid).values() if st.result for aid in st.result.data["series"].values()}
    assert series_ids and not any(aid in text for aid in series_ids)

    header, eps = read(export(L, scope="ledger:acme", out=tmp_path / "series.jsonl", include_series=True)["path"])
    assert header["redaction"]["series"] == "included"
    first = by_request(eps, ids["first"])[0]
    temp = first["outcome"]["series"]["temperature"]
    assert temp["unit"] == "degC" and len(temp["t"]) == len(temp["y"]) > 2


def test_every_string_in_an_episode_is_scrubbed_except_ids_and_hashes():
    from translucent.scrub import scrub_strings
    rid = "rq_3751395b9a0361d2a8d5"
    ep = {"episode_id": rid, "proposal": {"params": {"mode": SECRET_KEY}, "evidence_ids": [rid],
                                          "prediction": {"q": {"note": f"key {SECRET_KEY}"}}},
          "context": {"context_sha256": "9f" * 32}, "amendment": {"changes": [{"label": SECRET_KEY}]}}
    out = scrub_strings(ep)
    assert SECRET_KEY not in json.dumps(out)
    assert out["episode_id"] == rid and out["proposal"]["evidence_ids"] == [rid]
    assert out["context"]["context_sha256"] == "9f" * 32


# ------------------------------------------------------------------ 3. the held-out split

def test_the_campaign_split_is_deterministic_for_a_seed_and_independent_of_order():
    ids = [f"loop_{i:04x}" for i in range(20)]
    a = heldout_campaigns(ids, 0.2, seed=7)
    assert a == heldout_campaigns(list(reversed(ids)), 0.2, seed=7) and len(a) == 4
    assert any(heldout_campaigns(ids, 0.2, seed=s) != a for s in range(1, 6))
    assert heldout_campaigns(ids[:2], 0.2, seed=0) and len(heldout_campaigns(ids[:2], 0.2, seed=0)) == 1
    assert heldout_campaigns(ids[:1], 0.5, seed=0) == [] and heldout_campaigns(ids, 0.0, seed=0) == []
    assert heldout_from([5.0, 1.0, 3.0, 2.0, 4.0], 0.4) == 4.0


def test_the_split_is_recorded_in_the_header_and_matches_every_episode(acme, tmp_path):
    L, cid, _ = acme
    second, _ = campaign_with_corrections(L)
    runs = [read(export(L, scope="ledger:acme", out=tmp_path / f"s{i}.jsonl", seed=3)["path"]) for i in range(2)]
    (h1, e1), (h2, e2) = runs
    assert h1["split"] == h2["split"] and [e["split"] for e in e1] == [e["split"] for e in e2]
    assert h1["split"]["rule"] == "campaign" and h1["split"]["seed"] == 3
    assert h1["split"]["heldout_campaigns"] == heldout_campaigns([cid, second], 0.2, 3)
    assert len(h1["split"]["heldout_campaigns"]) == 1
    assert all((e["split"] == "heldout") == (e["campaign_id"] in h1["split"]["heldout_campaigns"]) for e in e1)

    h, eps = read(export(L, scope="ledger:acme", out=tmp_path / "t.jsonl", heldout="time", heldout_fraction=0.3)["path"])
    cut = h["split"]["heldout_from"]
    assert h["split"]["rule"] == "time" and cut is not None
    assert all((e["split"] == "heldout") == (e["t"] >= cut) for e in eps)
    assert 0 < sum(e["split"] == "heldout" for e in eps) < len(eps)


# ------------------------------------------------------------------ 4. amendments and the engineer's corrections

def test_amendment_episodes_and_the_engineers_corrections(acme, tmp_path):
    L, cid, ids = acme
    _, eps = read(export(L, scope="ledger:acme", out=tmp_path / "e.jsonl")["path"])
    (amendment,) = [e for e in eps if e["kind"] == "amendment"]
    assert amendment["by"] == "person:Ana" and amendment["version"] == 2
    assert amendment["before"]["constraints"][0]["limit"] == 45.0 and amendment["after"]["constraints"][0]["limit"] == 42.0
    assert amendment["changes"] == ["max_temperature 45 -> 42 degC"]

    v1, v2 = by_request(eps, ids["revised"])
    assert v1["engineer"]["decision"] == "revised" and v1["engineer"]["by"] == "person:Ana"
    assert v1["engineer"]["revision"]["params_diff"] == {"current_A": [4.0, 3.0]}
    assert v1["engineer"]["reason"].startswith("4 A is past the connector rating")
    assert v1["outcome"]["state"] == "superseded"
    assert v2["proposal"]["by"] == "person:Ana" and v2["proposal"]["supersedes"] == v1["episode_id"]
    assert v2["engineer"]["decision"] == "approved" and v2["outcome"]["state"] == "completed"

    (rejected,) = by_request(eps, ids["rejected"])
    assert rejected["engineer"]["decision"] == "rejected" and rejected["engineer"]["by"] == "person:Ana"
    assert rejected["engineer"]["reason"].startswith("Too hot for this pack.")
    assert rejected["outcome"]["state"] == "rejected" and rejected["validation"]["comparison"] is None


# ------------------------------------------------------------------ 5. a system-scoped lesson carries over

def test_a_system_lesson_loads_in_a_later_campaign_on_that_system_and_not_on_another(acme):
    L, cid, ids = acme
    result = request_states(L, cid)[ids["first"]].result
    evidence = next(iter(result.data["measurements"].values()))
    L.add(Lesson(id="les_sys", text="Above 3 A the labelled cell's connector limits the current.",
                 scope=f"system:{LABELLED_SPEC.id}", evidence_ids=[evidence], learned_in=cid, created_at=time.time()))
    same = LoopService(L, ANA).open_campaign(objective(system={"id": LABELLED_SPEC.id}), "Again")["campaign_id"]
    other = LoopService(L, ANA).open_campaign(objective(system={"id": LOOP_SPEC.id}), "Other cell")["campaign_id"]
    off = LoopService(L, ANA).open_campaign(objective(system={"id": LABELLED_SPEC.id}), "No memory",
                                            memory="off")["campaign_id"]

    assert memory.relation(L, L.get("lesson", "les_sys"), L.get("campaign", same)) == "same system"
    assert memory.relation(L, L.get("lesson", "les_sys"), L.get("campaign", other)) is None
    assert [d["lesson"].id for d in memory.select(L, L.get("campaign", same))] == ["les_sys"]
    assert memory.select(L, L.get("campaign", other)) == []
    assert memory.select(L, L.get("campaign", cid)) == []                       # not where it was learned

    loaded = planner_context(L, same, lessons=loop_learning.lessons)["lessons"]
    assert [x["id"] for x in loaded] == ["les_sys"] and loaded[0]["reason"] == "same system"
    assert "lessons" not in planner_context(L, other, lessons=loop_learning.lessons)
    assert "lessons" not in planner_context(L, off, lessons=loop_learning.lessons)
    header, _ = read(export(L, scope="ledger:acme", campaign_ids=[cid], out=L.root.parent / "l.jsonl")["path"])
    assert [x["id"] for x in header["lessons"]] == ["les_sys"]


# ------------------------------------------------------------------ the CLI

def test_the_cli_writes_the_export(acme, tmp_path, capsys):
    L, cid, _ = acme
    out = tmp_path / "cli.jsonl"
    assert cli(["learning-export", str(out), "--ledger", str(L.root), "--seed", "2", "--heldout", "time"]) == 0
    assert "episodes from 1 campaign(s)" in capsys.readouterr().out
    header, eps = read(out)
    assert header["split"]["rule"] == "time" and header["split"]["seed"] == 2 and eps


def test_the_learning_module_never_writes_to_the_ledger():
    import inspect
    source = inspect.getsource(loop_learning)
    for word in ("chain.append", "L.add(", ".emit(", "INSERT", "UPDATE", "DELETE"):
        assert word not in source
