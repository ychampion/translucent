"""S4a request lifecycle: the computational happy path, approval bound to the exact version, idempotency keys,
people-only capabilities, the experiment budget, auto-approval, conclusions, and insert-only new modules."""
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from support.analytic_system import simulate
from support.loop_systems import PREDICTION, LoopCell, objective, register

from translucent import chain, systems
from translucent.executors import executor_digest
from translucent.ledger import AdmissionError, Ledger
from translucent.locks import ExclusiveFileLock
from translucent.loop import RUNNER, NotAllowed, StaleRequest, claim_path, request_states
from translucent.loop_runner import LoopRunner
from translucent.loop_service import CAPABILITIES, Actor, LoopService, allowed, planner_tools

ROOT = Path(__file__).resolve().parents[1]
ANA = Actor("person", "Ana", "http")
BEN = Actor("person", "Ben", "http")
MUSE = Actor("agent", "muse-test", "planner")


@pytest.fixture(autouse=True)
def adapter(monkeypatch):
    return register(monkeypatch)


@pytest.fixture
def L(tmp_path):
    led = Ledger(tmp_path / "ledger")
    yield led
    led.db.close()


def open_loop(L, **kw):
    return LoopService(L, ANA).open_campaign(objective(**kw), "Faster charge")["campaign_id"]


def propose(L, cid, actor=MUSE, current=2.0, key=None, **kw):
    return LoopService(L, actor, idempotency_key=key).propose(
        cid, kw.pop("action", "simulate_charge"), {"current_A": current, "ambient_C": 25.0},
        rationale="Higher current should shorten the charge.", prediction=PREDICTION,
        expected_effect="If it stays under 45 degC we try a higher current next.", **kw)


def loop_types(L, cid):
    return [r.type for r in chain.records(L, cid)]


# ------------------------------------------------------------------ 1. computational happy path

def test_computational_happy_path_and_view_states(L):
    cid = open_loop(L)
    person, agent = LoopService(L, ANA), LoopService(L, MUSE)
    req = propose(L, cid)
    assert req["state"] == "awaiting_approval" and req["version"] == 1 and req["proposed_by"] == "agent:muse-test"
    view = agent.view(cid)
    assert view["next_action"]["kind"] == "awaiting_approval"
    assert view["next_action"]["content_hash"] == req["content_hash"] == chain.content_hash(
        chain.typed(L.get("loop", req["record_id"])))
    assert view["requests"][0]["params"]["current_A"] == {"value": 2.0, "unit": "A"}

    queued = person.approve(cid, req["request_id"], req["content_hash"], note="Go")
    assert queued["state"] == "queued" and queued["approved_by"] == "person:Ana"
    assert person.view(cid)["next_action"]["kind"] == "running"

    runner = LoopRunner(L.root, process="cli", workers=1)
    assert runner.tick(cid) == 1 and runner.problems == []
    done = agent.request(cid, req["request_id"])
    assert done["state"] == "completed" and done["attempts"] == 1
    assert [s["state"] for s in done["timeline"]] == ["awaiting_approval", "queued", "running", "completed"]
    assert [s["actor"] for s in done["timeline"]] == ["agent:muse-test", "person:Ana", "system:runner",
                                                      "system:runner"]
    t, y, charge = simulate(2.0, 25.0)
    values = {m["quantity"]: m["value"] for m in done["result"]["measurements"]}
    assert values == {"max_temperature": max(y), "charge_time_80": charge}
    assert {m["h"] for m in done["result"]["measurements"]} == {1e-9}          # the solver's declared bound
    series = agent.series(cid, req["request_id"], "temperature", max_points=50)
    assert series["n"] == len(t) and series["downsampled"] and series["unit"] == "degC"
    assert agent.view(cid)["next_action"] == {"kind": "none"}
    assert runner.tick(cid) == 0                                               # nothing left to run
    assert chain.problems(L, cid) == []
    result = L.get("loop", done["result"]["result_id"])
    run = L.get("run", result.data["run_id"])
    assert run.tool == "execute:loop-test-cell.simulate_charge" and run.provider == "local" and run.code_sha
    assert "output.json" in result.data["files"] and result.data["executed"]["configuration"] == \
        {"current_A": 2.0, "ambient_C": 25.0}
    # the measurement events and tool.finished committed with the result, before its loop record; its comparison
    # (loop-compare/1) commits with it
    types = [e.type for e in L.events(cid)]
    assert types[-5:] == ["measurement.recorded", "measurement.recorded", "tool.finished", "loop.recorded",
                          "loop.recorded"]
    assert [r.type for r in chain.records(L, cid)][-2:] == ["result", "comparison"]


# ------------------------------------------------------------------ 2. approval binds to the exact version

def test_stale_hash_and_superseded_version_are_refused_and_v2_approves(L):
    cid = open_loop(L)
    person = LoopService(L, ANA)
    v1 = propose(L, cid)
    v2 = person.revise(cid, v1["request_id"], {"current_A": 1.5}, "2 A is above the cell's rating")
    assert v2["version"] == 2 and v2["content_hash"] != v1["content_hash"] and v2["proposed_by"] == "agent:muse-test"
    assert v2["params"]["current_A"]["value"] == 1.5 and v2["prediction"] == {}
    assert "Revised by Ana: 2 A is above" in v2["rationale"]
    with pytest.raises(StaleRequest, match="This request changed after you read it; review version 2"):
        person.approve(cid, v1["request_id"], v1["content_hash"])
    with pytest.raises(StaleRequest, match="review version 2"):
        person.approve(cid, v1["request_id"], "f" * 64)
    with pytest.raises(StaleRequest):
        person.reject(cid, v1["request_id"], v1["content_hash"], "too hot")
    # the superseded version's record, named directly, is refused by admission too
    with pytest.raises(StaleRequest):
        chain.append(L, cid, "approval", {"request_id": v1["request_id"], "record_id": v1["record_id"],
                                          "content_hash": v1["content_hash"], "author": "Ana"},
                     "person:Ana", "apr_forged")
    assert "approval" not in loop_types(L, cid)
    ok = person.approve(cid, v1["request_id"], v2["content_hash"])
    assert ok["state"] == "queued" and ok["approved_hash"] == v2["content_hash"]
    states = [s["state"] for s in ok["timeline"]]
    assert states == ["awaiting_approval", "superseded", "awaiting_approval", "queued"]
    with pytest.raises(AdmissionError, match="is queued; only a request awaiting approval is revised"):
        person.revise(cid, v1["request_id"], {"current_A": 1.0}, "too late")
    assert chain.problems(L, cid) == []


def test_an_agent_revision_must_be_its_own(L):
    cid = open_loop(L)
    req = propose(L, cid)
    st = request_states(L, cid)[req["request_id"]]
    body = st.data.model_dump(mode="json") | {"version": 2, "supersedes": st.record.id, "rationale": "mine now"}
    body["content_hash"] = chain.content_hash(body)
    with pytest.raises(AdmissionError, match="only its own"):
        chain.append(L, cid, "request", body, "agent:other-model", "req_other")
    chain.append(L, cid, "request", body, "agent:muse-test", "req_own")
    assert request_states(L, cid)[req["request_id"]].version == 2


# ------------------------------------------------------------------ 3. idempotency keys

def test_duplicate_approval_under_one_key_returns_one_record_and_a_different_body_conflicts(L):
    cid = open_loop(L)
    req = propose(L, cid)
    first = LoopService(L, ANA, idempotency_key="click-1").approve(cid, req["request_id"], req["content_hash"], "ok")
    again = LoopService(L, ANA, idempotency_key="click-1").approve(cid, req["request_id"], req["content_hash"], "ok")
    assert first == again and loop_types(L, cid).count("approval") == 1
    with pytest.raises(chain.ChainConflict, match="already records something else"):
        LoopService(L, ANA, idempotency_key="click-1").approve(cid, req["request_id"], req["content_hash"],
                                                              "a different note")
    with pytest.raises(chain.ChainConflict):
        LoopService(L, BEN, idempotency_key="click-1").approve(cid, req["request_id"], req["content_hash"], "ok")
    # a second approval under another id is refused by the state it finds
    with pytest.raises(AdmissionError, match="queued, not awaiting approval"):
        LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"], "ok")
    assert loop_types(L, cid).count("approval") == 1


def test_proposals_are_idempotent_by_key_and_by_pending_configuration(L):
    cid = open_loop(L)
    a = propose(L, cid, key="k1")
    b = propose(L, cid, key="k1")
    assert a == b and loop_types(L, cid).count("request") == 1
    same_config = propose(L, cid)                       # no key, same configuration, still pending
    assert same_config["request_id"] == a["request_id"] and loop_types(L, cid).count("request") == 1
    other = propose(L, cid, current=3.0)
    assert other["request_id"] != a["request_id"]
    with pytest.raises(chain.ChainConflict):            # same key, a different body
        propose(L, cid, key="k1", current=4.0)


def test_campaign_opening_is_idempotent_by_key(L):
    a = LoopService(L, ANA, idempotency_key="open-1").open_campaign(objective(), "Faster charge")
    b = LoopService(L, ANA, idempotency_key="open-1").open_campaign(objective(), "Faster charge")
    assert a == b | {"created": True} and not b["created"] and len(L.list("campaign")) == 1   # a replay says so
    with pytest.raises(chain.ChainConflict):
        LoopService(L, ANA, idempotency_key="open-1").open_campaign(objective(statement="Other"), "Faster charge")


# ------------------------------------------------------------------ 9. people's decisions stay with people

@pytest.mark.parametrize("call", [
    lambda s, cid, rq, h: s.approve(cid, rq, h),
    lambda s, cid, rq, h: s.reject(cid, rq, h, "no"),
    lambda s, cid, rq, h: s.revise(cid, rq, {"current_A": 1.0}, "mine"),
    lambda s, cid, rq, h: s.submit_result(cid, {"request_id": rq, "content_hash": h, "executed_configuration": {},
                                                "author": "muse-test"}, {}),
    lambda s, cid, rq, h: s.amend(cid, "looser", constraints=[]),
    lambda s, cid, rq, h: s.note(cid, "hello"),
])
def test_an_agent_cannot_approve_submit_results_or_amend(L, call):
    cid = open_loop(L)
    req = propose(L, cid)
    before = len(chain.records(L, cid))
    with pytest.raises(NotAllowed):
        call(LoopService(L, MUSE), cid, req["request_id"], req["content_hash"])
    assert len(chain.records(L, cid)) == before


def test_the_planner_tool_list_excludes_people_and_system_capabilities():
    tools = planner_tools()
    for name in ("approve", "reject", "revise", "submit_result", "amend", "note", "claim", "record_result",
                 "auto_approve", "record_comparison"):
        assert name not in tools
    assert {"propose", "cancel", "conclude", "define_measurement", "view", "request"} <= set(tools)
    assert allowed("person") >= set(tools) - {"claim"} and "approve" in allowed("person")
    assert all(cap.actors and cap.summary for cap in CAPABILITIES.values())


@pytest.mark.parametrize("type, data", [
    ("approval", lambda rq, h, rid: {"request_id": rq, "record_id": rid, "content_hash": h, "author": "muse-test"}),
    ("rejection", lambda rq, h, rid: {"request_id": rq, "record_id": rid, "content_hash": h, "author": "muse-test",
                                      "reason": "no"}),
    ("amendment", lambda rq, h, rid: {"version": 2, "author": "muse-test", "reason": "looser",
                                      "constraints": []}),
    ("execution", lambda rq, h, rid: {"request_id": rq, "content_hash": h, "attempt": 1, "phase": "started",
                                      "worker": {}, "executor_digest": "x"}),
])
def test_admission_refuses_agent_actors_without_the_service(L, type, data):
    cid = open_loop(L)
    req = propose(L, cid)
    with pytest.raises(AdmissionError):
        chain.append(L, cid, type, data(req["request_id"], req["content_hash"], req["record_id"]),
                     "agent:muse-test", f"{type}_by_agent")


def test_hooks_run_in_a_process_that_never_imported_the_lifecycle_module(L):
    cid = open_loop(L)
    req = propose(L, cid)
    script = textwrap.dedent(f"""
        import sys
        from translucent import chain
        from translucent.ledger import AdmissionError, Ledger
        assert "translucent.loop" not in sys.modules
        L = Ledger({str(L.root)!r})
        try:
            chain.append(L, {cid!r}, "approval", {{"request_id": {req['request_id']!r},
                         "record_id": {req['record_id']!r}, "content_hash": {req['content_hash']!r}, "author": "m"}},
                         "agent:m", "apr_x")
        except AdmissionError as e:
            print("refused:", e)
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert "refused: an agent cannot approve" in out.stdout, out.stderr


@pytest.mark.parametrize("change, match", [
    (dict(cost={"usd": 0.0, "wall_s": 0.0}), "the cost must be the system's estimate"),
    (dict(units={"current_A": "mA", "ambient_C": "degC"}), "parameter units must be the system's"),
    (dict(configuration_hash="c" * 64), "configuration hash does not match"),
    (dict(params={"current_A": 50.0, "ambient_C": 25.0}), r"current_A must be in \[0.5, 10.0\]"),
    (dict(prediction={"charge_time_80": {"lo": 1.0, "hi": 2.0, "unit": "s"}}), "give a number as value"),
    (dict(prediction={"charge_time_80": {"value": 5.0, "lo": 6.0, "unit": "s"}}), "lo <= value <= hi"),
    (dict(prediction={"charge_time_80": {"value": 5.0, "unit": "V"}}), "does not measure the same thing"),
    (dict(objective_version=2), "objective version 2"),
    (dict(executor="human"), "computational executor"),
    (dict(evidence_ids=["m_from_elsewhere"]), "not a record of this campaign"),
    (dict(content_hash="0" * 64), "content hash does not match"),
])
def test_admission_refuses_a_request_body_the_adapter_did_not_make(L, change, match):
    cid = open_loop(L)
    rq = propose(L, cid)["request_id"]
    st = request_states(L, cid)[rq]
    body = st.data.model_dump(mode="json") | {"request_id": "rq_" + "a" * 20, "configuration_hash": "",
                                              "params": {"current_A": 3.0, "ambient_C": 25.0}}
    from translucent.loop import adapter_for, current_objective
    body["configuration_hash"] = chain.digest(adapter_for(current_objective(L, cid)).configuration(
        "simulate_charge", body["params"]))
    body |= change
    if "content_hash" not in change:
        body["content_hash"] = chain.content_hash(body)
    with pytest.raises(AdmissionError, match=match):
        chain.append(L, cid, "request", body, "agent:muse-test", "req_forged")


# ------------------------------------------------------------------ 10. the experiment budget

def test_a_proposal_beyond_the_experiment_budget_is_refused(L):
    cid = open_loop(L, budget={"model_usd": 0.0, "experiments": 2, "experiment_usd": 0.0})
    first = propose(L, cid, current=1.0)
    propose(L, cid, current=2.0)
    with pytest.raises(AdmissionError, match="experiment budget is spent: 2 of 2"):
        propose(L, cid, current=3.0)
    LoopService(L, ANA).reject(cid, first["request_id"], first["content_hash"], "too slow to matter")
    assert propose(L, cid, current=3.0)["state"] == "awaiting_approval"     # a rejected request frees its slot
    with pytest.raises(AdmissionError, match="budget"):
        propose(L, cid, current=4.0)
    spend = LoopService(L, MUSE).view(cid)["spend"]
    assert (spend["experiments_used"], spend["experiments_budget"]) == (2, 2)


# ------------------------------------------------------------------ cancellation, auto-approval, registration

def test_cancellation_rules(L):
    cid = open_loop(L)
    mine, theirs = propose(L, cid, current=1.0), propose(L, cid, Actor("person", "Ben"), current=2.0)
    with pytest.raises(AdmissionError, match="only its own"):
        LoopService(L, MUSE).cancel(cid, theirs["request_id"], "not mine")
    LoopService(L, ANA).approve(cid, mine["request_id"], mine["content_hash"])
    with pytest.raises(AdmissionError, match="only before approval"):
        LoopService(L, MUSE).cancel(cid, mine["request_id"], "changed my mind")
    assert LoopService(L, ANA).cancel(cid, mine["request_id"], "rig is down")["state"] == "cancelled"
    assert LoopService(L, MUSE).cancel(cid, propose(L, cid, current=3.0)["request_id"], "dup")["state"] == "cancelled"
    assert LoopRunner(L.root, process="cli").tick(cid) == 0                 # a cancelled request never runs


def test_auto_approval_rules_are_the_registering_persons_and_counted(L):
    cid = open_loop(L, auto_approve=[{"executor": "computational", "max_usd": 0.0, "max_wall_s": 60.0,
                                      "max_count": 1}])
    a, b = propose(L, cid, current=1.0), propose(L, cid, current=2.0)
    assert (a["state"], a["approved_by"]) == ("queued", "policy:auto/1")
    assert b["state"] == "awaiting_approval"                                 # the rule's count is used up
    with pytest.raises(AdmissionError, match="outside auto-approval rule 1"):
        chain.append(L, cid, "approval", {"request_id": b["request_id"], "record_id": b["record_id"],
                                          "content_hash": b["content_hash"], "author": "Ana", "rule": "auto/1"},
                     "policy:auto/1", "apr_extra")


def test_an_agent_registered_campaign_cannot_pre_approve(L):
    agent = LoopService(L, MUSE)
    with pytest.raises(AdmissionError, match="cannot pre-approve"):
        agent.open_campaign(objective(auto_approve=[{"executor": "computational", "max_count": 5}]), "Mine")
    with pytest.raises(AdmissionError, match="external"):
        agent.open_campaign(objective(planner={"kind": "muse", "profile": "meta", "client": None}), "Mine")
    cid = agent.open_campaign(objective(), "Mine")["campaign_id"]
    req = propose(L, cid)
    assert req["state"] == "awaiting_approval"


def test_registration_checks_the_objective_against_the_system(L):
    with pytest.raises(AdmissionError, match="not defined"):
        LoopService(L, ANA).open_campaign(objective(target={"quantity": "plating", "unit": "s",
                                                            "direction": "minimize"}), "x")
    with pytest.raises(AdmissionError, match="does not measure the same thing"):
        LoopService(L, ANA).open_campaign(objective(quantities=[
            {"name": "charge_time_80", "unit": "V", "source": "result"},
            {"name": "max_temperature", "unit": "degC", "source": "result"}]), "x")
    with pytest.raises(NotAllowed):
        LoopService(L, ANA).open_campaign(objective(registered_by="Ben"), "x")
    assert L.list("campaign") == []


def test_baseline_simulation_requests_and_approves_each_baseline_at_registration(L):
    from translucent.contracts import Uncertainty
    from translucent.loop_contracts import DatasetRecord, Provenance, Specimen, StoredChannel, StoredRecord
    series = L.put_json({"t": [0.0, 10.0], "t_unit": "s", "y": [25.0, 26.0], "unit": "degC"}, kind="series")
    record = StoredRecord(id="baseline-1", role="baseline", specimen=Specimen(id="cell-1"),
                          configuration={"current_A": 2000.0, "ambient_C": 25.0},
                          configuration_units={"current_A": "mA", "ambient_C": "degC"}, configuration_hash="0" * 64,
                          conditions={}, conditions_units={}, time=None,
                          channels=[StoredChannel(column="T", quantity="temperature", unit="degC",
                                                  uncertainty=Uncertainty(value=0.5, kind="std", method="spec"),
                                                  series_id=series.id, n=2, t_range=(0.0, 10.0))],
                          scalars=[], media=[], provenance=Provenance(source="test"), files={})
    L.add(DatasetRecord(id="bench", manifest_version=2, manifest_id=series.id, title="t", author="Ana", citation="c",
                        licence="internal", system="loop-test-cell", records=[record], created_at=0.0))
    cid = open_loop(L, datasets=["bench"], baseline=["bench/baseline-1"], baseline_simulation=True,
                    budget={"model_usd": 0.0, "experiments": 1, "experiment_usd": 0.0})
    (st,) = request_states(L, cid).values()
    assert st.state == "queued" and st.proposed_by == st.approved_by == "policy:registration"
    assert st.data.params == {"current_A": 2.0, "ambient_C": 25.0}                    # mA converted to A
    assert propose(L, cid, current=3.0)["state"] == "awaiting_approval"              # not counted in the budget
    assert LoopRunner(L.root, process="cli", workers=1).tick(cid) == 1
    assert request_states(L, cid)[st.request_id].state == "completed"
    # a derived measurement (ops/1 stub) applies to the baseline record and the completed result
    out = LoopService(L, MUSE).define_measurement(cid, "peak_temperature", {"op": "max", "channel": "temperature",
                                                                           "unit": "degC"})
    values = {m["subject"]: m["value"] for m in out["measurements"]}
    assert values["bench/baseline-1"] == 26.0 and values[st.request_id] == max(simulate(2.0, 25.0)[1])
    assert {m["quantity"] for m in out["measurements"]} == {"derived:peak_temperature"}
    with pytest.raises(AdmissionError, match="delta_degC"):
        LoopService(L, MUSE).define_measurement(cid, "rise", {"op": "delta", "channel": "temperature",
                                                              "unit": "degC"})
    # ops/1 beyond the extremes, with its uncertainty rules: a difference of two readings, a threshold crossing
    rise = LoopService(L, MUSE).define_measurement(cid, "rise", {"op": "delta", "channel": "temperature",
                                                                 "unit": "delta_degC"})
    m = next(m for m in rise["measurements"] if m["subject"] == "bench/baseline-1")
    assert m["value"] == pytest.approx(1.0) and m["uncertainty"]["value"] == pytest.approx(0.5 * 2 ** 0.5)
    hot = LoopService(L, MUSE).define_measurement(cid, "warm_at", {"op": "time_to_threshold", "channel": "temperature",
                                                                   "threshold": 25.1, "direction": "rising",
                                                                   "unit": "s"})
    m = next(m for m in hot["measurements"] if m["subject"] == "bench/baseline-1")
    assert m["value"] == pytest.approx(1.0) and m["uncertainty"] == {
        "value": 5.0, "kind": "resolution", "method": "half the sample interval at the crossing"}


def test_a_request_never_runs_on_code_other_than_the_campaign_registered(L):
    cid = open_loop(L)
    req = propose(L, cid)
    LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])

    class Edited(LoopCell):                       # the adapter file changed after registration
        def code_digest(self):
            return "f" * 64
    systems.register(Edited())
    runner = LoopRunner(L.root, process="cli")
    assert runner.tick(cid) == 0 and "differs from what the campaign registered" in runner.problems[0]
    assert request_states(L, cid)[req["request_id"]].state == "queued"
    lock = ExclusiveFileLock(claim_path(L.root, req["request_id"]))
    assert lock.try_acquire()
    try:                                          # and the ledger refuses it from any other writer
        with pytest.raises(AdmissionError, match="differs from what the campaign registered"):
            chain.append(L, cid, "execution", {"request_id": req["request_id"], "content_hash": req["content_hash"],
                                               "attempt": 1, "phase": "started", "worker": {"host": "h", "pid": 1, "process": "cli"},
                                               "executor_digest": executor_digest(Edited())}, RUNNER, "exe_x")
    finally:
        lock.release()


# ------------------------------------------------------------------ conclusion

def test_the_conclusion_outcome_is_computed_and_nothing_follows_it(L):
    cid = open_loop(L)
    req = propose(L, cid)
    with pytest.raises(AdmissionError, match="outcome is computed"):
        chain.append(L, cid, "conclusion", {"outcome": "improved", "comparison_id": None, "text": "It worked",
                                            "unresolved_reasons": [], "author": "agent:muse-test"},
                     "agent:muse-test", "con_forged")
    done = LoopService(L, MUSE).conclude(cid, "No comparison was possible.", ["no result yet"])
    assert done["data"]["outcome"] == "unresolved"
    with pytest.raises(AdmissionError, match="concluded"):
        LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])


def test_a_campaign_is_not_concluded_while_a_computation_is_queued(L):
    cid = open_loop(L)
    req = propose(L, cid)
    LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])
    with pytest.raises(AdmissionError, match=f"{req['request_id']} is queued or running; wait for the result"):
        LoopService(L, MUSE).conclude(cid, "Nothing beat the baseline.")
    LoopService(L, ANA).cancel(cid, req["request_id"], "rig is down")
    assert LoopService(L, MUSE).conclude(cid, "Nothing was run.")["data"]["outcome"] == "unresolved"


# ------------------------------------------------------------------ 11. insert-only

def test_the_new_modules_never_update_or_delete():
    for name in ("loop", "loop_service", "loop_runner", "executors", "exec_child"):
        text = (ROOT / "translucent" / f"{name}.py").read_text(encoding="utf-8")
        assert not re.search(r"\b(UPDATE|DELETE)\s", text), name
