"""Milestone 2 semantics: loop-compare/3 keeps feasibility, speed and the objective apart.

A feasible result against a failing baseline is "feasible only", never "improved" or "faster"; speed is compared only
with a reference that meets the same constraints (the baseline when it does, otherwise the system's reference search,
run by bisection and rerun when an amendment changes the constraints); a hard target changes only by a person's
amendment; a loop-compare/2 campaign keeps its recorded outcome while its assessment says what it showed; verify
recomputes all of it.

RefCell is the analytic lumped cell (support.analytic_system) with an objective template that declares a reference
search over the charge current. At 40 degC ambient its peak temperature is 40 + 0.04 I^2 degC (the charge outlasts the
thermal time constant many times over) and its 80 % charge time 14400 / I s, so a 42 degC limit allows at most
sqrt(50) = 7.07 A, and the search's 0.5 A lattice finds 7.0 A.
"""
import math
from pathlib import Path

import pytest
from support.analytic_system import simulate
from support.loop_systems import LOOP_SPEC, PREDICTION, LoopCell, objective, register

from translucent import chain, exec_child, executors, loop_compare, loop_export, loop_reference
from translucent.contracts import Uncertainty
from translucent.ledger import AdmissionError, Ledger
from translucent.loop import REFERENCE, NotAllowed, experiments_used, request_states
from translucent.loop_contracts import (DatasetRecord, LoopView, Provenance, ScalarSpec, Specimen, StoredChannel,
                                        StoredRecord)
from translucent.loop_runner import LoopRunner
from translucent.loop_service import Actor, LoopService
from translucent.systems.base import SystemSpec, file_digest
from translucent.verify import verify

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "web" / "public" / "fixtures" / "loop" / "fastcharge.loop.json"
ANA = Actor("person", "Ana", "http")
MUSE = Actor("agent", "muse-test", "planner")
AMBIENT = 40.0
MODEL_WORDS = "Not quantified: a closed-form lumped cell for tests; it is not compared with any measured cell."

TEMPLATE = {
    "evaluator": "loop-compare/3", "kinds": ["fastest_feasible", "hard_target"], "default_kind": "fastest_feasible",
    "statement": {"fastest_feasible": "Find the fastest protocol meeting these constraints.",
                  "hard_target": "Meet {target} while meeting these constraints."},
    "hard_target": {"quantity": "charge_time_80", "unit": "s", "op": "<=", "label": "Time to 80 % at most",
                    "words": "an 80 % charge in at most {limit:g} {unit}"},
    "summary": {"fastest_feasible": "Fastest charge to 80 %",
                "hard_target": "Charge to 80 % in at most {limit:g} {unit}",
                "terms": {"max_temperature": {"name": "peak temperature"}}},
    "reference_search": {"action": "simulate_charge", "label": "Constant-current charge",
                         "parameter": {"name": "current_A", "unit": "A", "unit_label": "A", "lo": 0.5, "hi": 10.0,
                                       "step": 0.5, "sets": ["current_A"], "faster": "higher"},
                         "fixed": {"ambient_C": AMBIENT}, "max_runs": 8, "tolerance": 0.5,
                         "method": "bisection on the charge current, 0.5 A lattice"},
    "model_uncertainty": MODEL_WORDS,
}
REF_SPEC = SystemSpec(id="loop-ref-cell", version="1", title="Analytic cell with a reference search",
                      description=LOOP_SPEC.description, quantities=LOOP_SPEC.quantities, actions=LOOP_SPEC.actions,
                      solver=LOOP_SPEC.solver, objective_template=TEMPLATE)


class RefCell(LoopCell):
    spec = REF_SPEC

    def code_digest(self) -> str:
        return file_digest(Path(__file__), Path(__file__).parent / "support" / "loop_systems.py",
                           Path(__file__).parent / "support" / "analytic_system.py")


@pytest.fixture(autouse=True)
def adapters(monkeypatch):
    register(monkeypatch)
    systems = register(None, RefCell)

    def child(adapter, action, params, workdir, wall_s):     # the executor's child, in this process (RefCell is here)
        body = {"output": exec_child.encode_output(adapter.execute(action, params, workdir), workdir)}
        return body, executors.result_digest(body)
    monkeypatch.setattr(executors, "run_child", child)
    return systems


@pytest.fixture
def L(tmp_path):
    led = Ledger(tmp_path / "ledger")
    yield led
    led.db.close()


def bench(L, system="loop-ref-cell", current_A=9.0):
    """A measured baseline at `current_A` and 40 degC; at 9 A it breaks a 42 degC limit (43.2 degC)."""
    t, y, charge = simulate(current_A, AMBIENT)
    s = L.put_json({"t": t, "t_unit": "s", "y": y, "unit": "degC"}, kind="series")
    record = StoredRecord(id="baseline-1", role="baseline", specimen=Specimen(id="cell-1"),
                          configuration={"current_A": current_A, "ambient_C": AMBIENT},
                          configuration_units={"current_A": "A", "ambient_C": "degC"}, configuration_hash="0" * 64,
                          conditions={}, conditions_units={}, time=None,
                          channels=[StoredChannel(column="T", quantity="temperature", unit="degC",
                                                  uncertainty=Uncertainty(value=0.1, kind="std", method="spec"),
                                                  series_id=s.id, n=len(t), t_range=(0.0, t[-1]))],
                          scalars=[ScalarSpec(quantity="charge_time_80", value=charge, unit="s", source="log"),
                                   ScalarSpec(quantity="max_temperature", value=max(y), unit="degC", source="log",
                                              uncertainty=Uncertainty(value=0.1, kind="std", method="spec"))],
                          media=[], provenance=Provenance(source="test"), files={})
    L.add(DatasetRecord(id="bench", manifest_version=2, manifest_id=s.id, title="t", author="Ana", citation="c",
                        licence="internal", system=system, records=[record], created_at=0.0))


LIMIT_42 = [{"quantity": "max_temperature", "op": "<=", "limit": 42.0, "unit": "degC",
             "label": "Cell at or below 42 degC"}]


def open_ref(L, *, kind="fastest_feasible", limit=None, baseline_A=9.0, constraints=None, evaluator="loop-compare/3",
             system="loop-ref-cell"):
    bench(L, system, baseline_A)
    target = {"quantity": "charge_time_80", "unit": "s", "direction": "minimize"} | \
        ({"limit": limit} if limit is not None else {})
    data = objective(system={"id": system}, datasets=["bench"], baseline=["bench/baseline-1"],
                     baseline_simulation=True, target=target, constraints=constraints or LIMIT_42,
                     evaluator=evaluator, statement=TEMPLATE["statement"]["fastest_feasible"] if limit is None else
                     TEMPLATE["statement"]["hard_target"].format(
                         target=TEMPLATE["hard_target"]["words"].format(limit=limit, unit="s")))
    if evaluator == "loop-compare/3":
        data["kind"] = kind
    return LoopService(L, ANA).open_campaign(data, "Fast charge")["campaign_id"]


def tick(L, cid, rounds=12):
    """Run the runner until nothing is queued (each reference result queues the next step)."""
    runner = LoopRunner(L.root, process="cli", workers=1)
    for _ in range(rounds):
        if not runner.tick(cid):
            break
    assert runner.problems == []


def simulate_at(L, cid, current):
    req = LoopService(L, MUSE).propose(cid, "simulate_charge", {"current_A": current, "ambient_C": AMBIENT},
                                       rationale="Try this current.", prediction=PREDICTION, expected_effect="x")
    LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])
    tick(L, cid)
    return req["request_id"]


def latest(L, cid, rq):
    return [r for r in chain.records(L, cid) if r.type == "comparison" and
            r.data["candidate"]["request_id"] == rq][-1]


def view(L, cid):
    v = LoopService(L, ANA).view(cid)
    from pydantic import TypeAdapter
    TypeAdapter(LoopView).validate_python(v)
    return v


def references(L, cid):
    return [s for s in request_states(L, cid).values() if s.proposed_by == REFERENCE]


T42 = 14400 / 7.0                         # the 7.0 A reference under the 42 degC limit


# ------------------------------------------------------------------ the reference search

def test_the_reference_search_runs_at_registration_and_finds_the_fastest_single_step_meeting_the_limit(L):
    cid = open_ref(L)
    assert references(L, cid) == []                                  # it waits for the baseline (which breaks 42)
    assert loop_reference.state(L, cid, *_ctx(L, cid))["status"] == "waiting_for_baseline"
    LoopRunner(L.root, process="cli", workers=1).tick(cid)           # the baseline's result queues step 1
    queued = references(L, cid)
    assert len(queued) == 1 and queued[0].approval.actor == REFERENCE       # pre-approved with the registration
    assert queued[0].data.params == {"current_A": 5.0, "ambient_C": AMBIENT}
    tick(L, cid)
    s = loop_reference.state(L, cid, *_ctx(L, cid))
    assert s["status"] == "found" and s["found"]["value"] == 7.0 and s["objective_version"] == 1
    assert [(x["value"], x["meets"]) for x in s["steps"]] == [(5.0, True), (7.5, False), (6.0, True), (6.5, True),
                                                             (7.0, True)]
    assert len(references(L, cid)) == 5 <= TEMPLATE["reference_search"]["max_runs"]
    assert experiments_used(request_states(L, cid)) == 0             # system runs, outside the budget
    v = view(L, cid)
    assert sorted(r["role"] for r in v["requests"]) == ["baseline"] + ["reference"] * 5
    assert v["spend"]["experiments_used"] == 0
    assert v["objective"]["kind"] == "fastest_feasible" and v["objective"]["statement_version"] == 1
    assert v["objective"]["summary"] == "Fastest charge to 80 % with peak temperature <= 42 degC"


def _ctx(L, cid):
    from translucent.loop import current_objective
    recs = chain.records(L, cid)
    return current_objective(L, cid, recs), request_states(L, cid, recs), recs


def test_a_reference_step_is_only_the_searchs_own_next_step(L):
    cid = open_ref(L)
    LoopRunner(L.root, process="cli", workers=1).tick(cid)           # the baseline ran; step 1 is queued
    (first,) = references(L, cid)
    tick(L, cid, rounds=1)                                           # step 1 ran; step 2 (7.5 A) is queued
    obj, states, recs = _ctx(L, cid)
    nxt = loop_reference.state(L, cid, obj, states, recs)
    assert nxt["status"] == "running"
    # an agent or a person cannot write a reference run; a forged step is refused even under the reference actor
    with pytest.raises(AdmissionError, match="not the reference search's next step"):
        chain.append(L, cid, "request", first.versions[0].data | {"request_id": "rq_forged"}, REFERENCE, "req_forged")
    with pytest.raises(AdmissionError):
        chain.append(L, cid, "request", first.versions[0].data | {"request_id": "rq_forged"}, "agent:muse-test",
                     "req_forged2")
    with pytest.raises(NotAllowed):
        LoopService(L, MUSE).approve(cid, first.request_id, first.content_hash)
    assert loop_reference.queue(L, cid) is None                       # nothing due while a step runs


def test_a_restart_resumes_the_search(L, monkeypatch):
    cid = open_ref(L)
    real = loop_reference.queue
    monkeypatch.setattr(loop_reference, "queue", lambda L, cid: None)       # the process dies before queuing
    LoopRunner(L.root, process="cli", workers=1).tick(cid)           # the baseline ran; step 1 was never queued
    assert loop_reference.state(L, cid, *_ctx(L, cid))["status"] == "due"
    assert references(L, cid) == []
    monkeypatch.setattr(loop_reference, "queue", real)
    tick(L, cid)                                                     # the next process's first tick queues it
    assert loop_reference.state(L, cid, *_ctx(L, cid))["status"] == "found"


# ------------------------------------------------------------------ outcomes

def test_feasibility_recovered_but_slower_than_the_reference_is_feasible_only(L):
    cid = open_ref(L)
    tick(L, cid)
    rq = simulate_at(L, cid, 3.0)                                    # 4800 s, 40.36 degC: meets 42 degC
    c = latest(L, cid, rq).data
    assert c["feasibility"] == {"baseline": "fails", "candidate": "meets"}
    assert c["speed"]["status"] == "not_faster" and c["speed"]["faster"] is False
    assert c["speed"]["reference"]["source"] == "reference_search"
    assert c["speed"]["reference"]["value"] == pytest.approx(T42)
    assert c["target"]["against"] == "reference_search" and c["target"]["baseline"] == pytest.approx(1600.0)
    assert c["verdict"] == "not_better" and c["objective"] == {"kind": "fastest_feasible", "status": "open",
                                                                "target_limit": None}
    done = LoopService(L, MUSE).conclude(cid, "Meets the limit; slower than the reference.")["data"]
    assert done["outcome"] == "feasible_only_in_simulation"
    assert done["findings"]["feasibility"]["recovered"] is True and done["findings"]["speed"]["status"] == "not_faster"
    v = view(L, cid)
    a = v["assessment"]
    assert a["headline"].startswith("Feasibility recovered in simulation:")
    words = " ".join([a["headline"], *a["lines"], v["current_result"]["sentence"]]).lower()
    assert "improved" not in words and "faster than the" not in words.replace("not shown faster than the", "")
    assert v["conclusion"]["findings"] == done["findings"]


def test_a_result_faster_than_the_reference_says_so(L):
    cid = open_ref(L)
    tick(L, cid)
    rq = simulate_at(L, cid, 7.07)                                   # 41.999 degC, 2036.8 s
    c = latest(L, cid, rq).data
    assert c["speed"]["status"] == "faster" and c["verdict"] == "candidate_better"
    assert c["speed"]["delta"] == pytest.approx(T42 - 14400 / 7.07)
    done = LoopService(L, MUSE).conclude(cid, "Faster than the reference.")["data"]
    assert done["outcome"] == "faster_than_reference_in_simulation"
    a = view(L, cid)["assessment"]
    assert a["headline"].startswith("Faster than the reference in simulation:")
    assert a["uncertainty"]["model"] == MODEL_WORDS and a["uncertainty"]["solver"]["rtol"] is None


def test_no_result_meeting_the_constraints_is_no_feasible_result(L):
    cid = open_ref(L)
    tick(L, cid)
    rq = simulate_at(L, cid, 9.5)
    assert latest(L, cid, rq).data["verdict"] == "violates_constraints"
    assert LoopService(L, MUSE).conclude(cid, "Nothing meets it.")["data"]["outcome"] == \
        "no_feasible_result_in_simulation"
    assert view(L, cid)["assessment"]["headline"] == "No result meets the constraints in simulation."


def test_a_conclusion_waits_for_the_reference_search(L):
    cid = open_ref(L)
    with pytest.raises(AdmissionError, match="queued or running"):
        LoopService(L, MUSE).conclude(cid, "Too early.")


def test_a_met_hard_target_is_objective_met_and_no_reference_search_runs(L):
    cid = open_ref(L, kind="hard_target", limit=3000.0)
    assert references(L, cid) == []                                  # a hard target needs no speed reference
    tick(L, cid)
    rq = simulate_at(L, cid, 6.0)                                    # 2400 s, 41.44 degC
    c = latest(L, cid, rq).data
    assert c["objective"] == {"kind": "hard_target", "status": "met", "target_limit": 3000.0}
    assert LoopService(L, MUSE).conclude(cid, "Within 3000 s.")["data"]["outcome"] == "objective_met_in_simulation"
    v = view(L, cid)
    assert v["objective"]["kind"] == "hard_target"
    assert v["objective"]["statement"] == "Meet an 80 % charge in at most 3000 s while meeting these constraints."
    assert v["assessment"]["headline"].startswith("Objective met in simulation:")


def test_a_hard_target_is_not_relaxed_except_by_a_persons_amendment(L):
    cid = open_ref(L, kind="hard_target", limit=3000.0)
    tick(L, cid)
    rq = simulate_at(L, cid, 3.0)                                    # 4800 s: meets 42 degC, misses 3000 s
    c = latest(L, cid, rq).data
    assert c["objective"]["status"] == "not_met" and "misses the hard target of 3000 s" in " ".join(c["reasons"])
    assert loop_compare.outcome(L, cid)[0] == "feasible_only_in_simulation"
    with pytest.raises(NotAllowed):                                  # the agent cannot move it
        LoopService(L, MUSE).amend(cid, "relax", target={"quantity": "charge_time_80", "unit": "s",
                                                          "direction": "minimize", "limit": 6000.0})
    with pytest.raises(AdmissionError, match="keeps the objective's kind"):     # nor drop it
        LoopService(L, ANA).amend(cid, "no target", target={"quantity": "charge_time_80", "unit": "s",
                                                            "direction": "minimize"})
    LoopService(L, ANA).amend(cid, "The pack allows 5000 s.", target={"quantity": "charge_time_80", "unit": "s",
                                                                       "direction": "minimize", "limit": 5000.0})
    c2 = latest(L, cid, rq).data
    assert c2["objective_version"] == 2 and c2["objective"]["status"] == "met"
    assert loop_compare.outcome(L, cid)[0] == "objective_met_in_simulation"
    assert view(L, cid)["objective"]["summary"] == "Charge to 80 % in at most 5000 s with peak temperature <= 42 degC"


# ------------------------------------------------------------------ amendments

def test_an_amendment_to_the_constraints_reruns_the_reference_and_never_compares_across_versions(L):
    cid = open_ref(L)
    tick(L, cid)
    rq = simulate_at(L, cid, 6.5)                                    # 41.69 degC: meets 42
    assert latest(L, cid, rq).data["speed"]["reference"]["objective_version"] == 1
    v1_refs = {s.request_id for s in references(L, cid)}
    LoopService(L, ANA).amend(cid, "3.5 K of headroom", constraints=[
        {"quantity": "max_temperature", "op": "<=", "limit": 41.5, "unit": "degC", "label": "41.5 degC"}])
    c = latest(L, cid, rq).data
    assert c["objective_version"] == 2 and c["feasibility"]["candidate"] == "fails"     # 41.69 > 41.5
    obj, states, recs = _ctx(L, cid)
    s = loop_reference.state(L, cid, obj, states, recs)
    assert s["objective_version"] == 2 and s["status"] == "running"  # a new search, queued with the amendment
    rq2 = simulate_at(L, cid, 5.5)                                   # 41.21 degC; the v2 search is still running
    early = [r for r in chain.records(L, cid) if r.type == "comparison" and
             r.data["candidate"]["request_id"] == rq2]
    assert all(r.data["speed"]["reference"] is None or r.data["speed"]["reference"]["objective_version"] == 2
               for r in early)
    tick(L, cid)
    s = loop_reference.state(L, cid, *_ctx(L, cid))
    assert s["status"] == "found" and s["found"]["value"] == 6.0      # 41.44 <= 41.5 < 41.69
    assert not v1_refs & {x["request_id"] for x in s["steps"]}
    for r in chain.records(L, cid):                                  # no comparison mixes versions
        if r.type == "comparison" and r.data["speed"]["reference"] is not None:
            assert r.data["speed"]["reference"]["objective_version"] == \
                loop_reference.constraints_version(chain.records(L, cid), r.data["objective_version"])
    c2 = latest(L, cid, rq2).data
    assert c2["speed"]["status"] == "not_faster" and c2["speed"]["reference"]["value"] == pytest.approx(2400.0)
    v = view(L, cid)
    assert v["objective"]["statement"] == "Find the fastest protocol meeting these constraints."
    assert v["objective"]["statement_version"] == 1 and v["objective"]["version"] == 2
    assert v["objective"]["summary"] == "Fastest charge to 80 % with peak temperature <= 41.5 degC"


def test_an_amendment_that_leaves_the_constraints_keeps_the_reference(L):
    cid = open_ref(L)
    tick(L, cid)
    n = len(references(L, cid))
    LoopService(L, ANA).amend(cid, "Tighter budget.", budget={"experiments": 6})
    LoopService(L, ANA).amend(cid, "Same limit, new words.", constraints=[dict(LIMIT_42[0], label="42 degC")])
    tick(L, cid)
    assert len(references(L, cid)) == n
    rq = simulate_at(L, cid, 3.0)
    assert latest(L, cid, rq).data["speed"]["reference"]["objective_version"] == 1


# ------------------------------------------------------------------ legacy campaigns

def test_a_loop_compare_2_campaign_keeps_its_outcome_and_its_assessment_says_feasibility_only(L):
    cid = open_ref(L, evaluator="loop-compare/2")
    assert references(L, cid) == []                                  # /2 runs no reference search
    tick(L, cid)
    rq = simulate_at(L, cid, 3.0)
    c = latest(L, cid, rq)
    assert c.data["evaluator"] == "loop-compare/2" and c.data["verdict"] == "candidate_better"
    assert c.data["target"]["decided_by"] == "feasibility" and "speed" not in c.data
    done = LoopService(L, MUSE).conclude(cid, "Meets the limit.")["data"]
    assert done["outcome"] == "improved_in_simulation" and "findings" not in done
    v = view(L, cid)
    assert v["objective"]["kind"] == "legacy" and "findings" not in v["conclusion"]
    a = v["assessment"]
    assert a["feasibility"]["recovered"] is True and a["speed"]["status"] == "not_compared"
    assert a["headline"].startswith("Feasibility recovered in simulation:") and a["headline"].endswith(
        "speed not compared.")


def test_the_recorded_fixture_still_verifies_and_its_assessment_is_feasibility_only():
    if not FIXTURE.is_file():
        pytest.skip("the recorded fast-charge fixture is not in this checkout")
    import json
    r = verify(FIXTURE)
    assert r.ok, r.problems
    v = json.loads(FIXTURE.read_text(encoding="utf-8"))["view"]
    assert v["conclusion"]["outcome"] == "improved_in_simulation"   # its recorded words are never rewritten


# ------------------------------------------------------------------ verify recomputes /3

def test_admission_and_verify_recompute_loop_compare_3(L, tmp_path):
    cid = open_ref(L)
    tick(L, cid)
    rq = simulate_at(L, cid, 3.0)
    real = latest(L, cid, rq)
    forged = dict(real.data, speed=dict(real.data["speed"], status="faster", faster=True), verdict="candidate_better")
    with pytest.raises(AdmissionError, match="is not what loop-compare/3 computes"):
        chain.append(L, cid, "comparison", forged, "system:comparator", "cmp_forged")
    from translucent.loop import computed_outcome
    outcome, comparison = computed_outcome(L, cid)
    good = {"outcome": outcome, "comparison_id": comparison, "text": "x", "unresolved_reasons": [],
            "author": "agent:muse-test", "findings": loop_compare.findings(L, cid)}
    bad = dict(good, findings=dict(good["findings"], speed=dict(good["findings"]["speed"], status="faster")))
    with pytest.raises(AdmissionError, match="findings are computed"):
        chain.append(L, cid, "conclusion", bad, "agent:muse-test", "con_forged")
    with pytest.raises(AdmissionError, match="outcome is computed"):
        chain.append(L, cid, "conclusion", dict(good, outcome="improved_in_simulation"), "agent:muse-test", "con_x")
    LoopService(L, MUSE).conclude(cid, "Feasible only.")
    assert verify(L.root).ok
    path = loop_export.export(L, cid, tmp_path / "ref.loop.json")
    assert verify(path).ok
    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["view"]["assessment"]["speed"]["status"] == "not_faster"
    data["view"]["conclusion"]["findings"]["speed"]["status"] = "faster"
    path.write_text(json.dumps(data), encoding="utf-8")
    r = verify(path)
    assert not r.ok and "Loop export view differs from the recorded evidence" in r.problems


def test_comparison_payloads_are_finite_and_typed():
    """compare3 alone (no ledger): the payload chain.payload admits, for each feasibility case."""
    from translucent.loop_contracts import ObjectiveData
    o = ObjectiveData.model_validate(objective(constraints=LIMIT_42, evaluator="loop-compare/3",
                                               kind="fastest_feasible"))
    v = {"charge_time_80": {"value": 2000.0, "unit": "s", "h": 0.0},
         "max_temperature": {"value": 41.0, "unit": "degC", "h": 0.0}}
    hot = dict(v, max_temperature={"value": 43.0, "unit": "degC", "h": 0.0})
    ref = {"label": "r", "source": "reference_search", "objective_version": 1, "request_id": "rq_r",
           "values": dict(v, charge_time_80={"value": 2100.0, "unit": "s", "h": 0.0})}
    for base, cand, reference, status in ((hot, v, ref, "faster"), (hot, v, None, "not_compared"),
                                         (v, hot, ref, "not_compared")):
        out = loop_compare.compare3(o, "computational", {"refs": ["b/1"], "values": base},
                                    {"request_id": "rq_x", "result_id": "res_x", "values": cand}, reference,
                                    reference_reason="none")
        chain.payload("comparison", out)
        assert out["speed"]["status"] == status
        assert math.isfinite(out["target"]["candidate"])


# ------------------------------------------------------------------ amendments withdraw unapproved proposals

def propose(L, cid, current, actor=MUSE):
    return LoopService(L, actor).propose(cid, "simulate_charge", {"current_A": current, "ambient_C": AMBIENT},
                                         rationale="Try this current.", prediction=PREDICTION, expected_effect="x")


def test_an_amendment_withdraws_the_proposals_still_awaiting_approval(L):
    cid = open_ref(L, evaluator="loop-compare/2")                   # any evaluator: this is the request lifecycle
    tick(L, cid)
    waiting = [propose(L, cid, c)["request_id"] for c in (2.0, 3.0, 4.0)]
    approved = propose(L, cid, 5.0)
    LoopService(L, ANA).approve(cid, approved["request_id"], approved["content_hash"])      # queued: untouched
    assert experiments_used(request_states(L, cid)) == 4
    LoopService(L, ANA).amend(cid, "Headroom.", constraints=[dict(LIMIT_42[0], limit=41.5)])
    states = request_states(L, cid)
    for rq in waiting:
        st = states[rq]
        assert st.state == "cancelled" and st.closed_by.actor == "system:objective"
        assert st.closed_by.data == {"request_id": rq, "author": "system:objective",
                                     "reason": "objective amended to v2"}
    assert states[approved["request_id"]].state == "queued"
    assert experiments_used(states) == 1                             # their budget is free again
    again = propose(L, cid, 3.0)                                     # proposed again under version 2
    assert request_states(L, cid)[again["request_id"]].data.objective_version == 2
    v = view(L, cid)
    assert [t["actor"] for t in next(r for r in v["requests"] if r["request_id"] == waiting[0])["timeline"]][-1] == \
        "system:objective"
    tick(L, cid)
    assert verify(L.root).ok                                        # verify re-admits the withdrawals


def test_the_system_withdraws_only_superseded_unapproved_proposals(L):
    cid = open_ref(L, evaluator="loop-compare/2")
    current = propose(L, cid, 2.0)
    approved = propose(L, cid, 3.0)
    LoopService(L, ANA).approve(cid, approved["request_id"], approved["content_hash"])

    def withdraw(rq, reason="objective amended to v1"):
        chain.append(L, cid, "cancellation", {"request_id": rq, "author": "system:objective", "reason": reason},
                     "system:objective", chain.natural_id("cnl", rq))
    with pytest.raises(AdmissionError, match="proposed under an older objective version"):
        withdraw(current["request_id"])                              # proposed under the current version
    LoopService(L, ANA).amend(cid, "Headroom.", constraints=[dict(LIMIT_42[0], limit=41.5)])  # supersedes it
    # (a budget-only amendment no longer withdraws anything: test_7_a_budget_or_label_amendment_...)
    assert request_states(L, cid)[current["request_id"]].state == "cancelled"
    with pytest.raises(AdmissionError, match="proposed under an older objective version"):
        withdraw(approved["request_id"], "objective amended to v2")  # approved: never the system's to withdraw
    with pytest.raises(AdmissionError):
        chain.append(L, cid, "cancellation", {"request_id": approved["request_id"], "author": "objective",
                                              "reason": "x"}, "system:other", "cnl_other")


def test_a_baseline_planner_keeps_no_more_than_its_batch_waiting_and_proposes_again_after_an_amendment(L):
    from translucent.loop_planner import DomainBaselinePlanner, run_turn

    class Pairs:
        """Two untried currents per call, 1 to 8 A: a policy that would stack proposals if it were let."""
        name = "pairs"

        def propose(self, history, remaining):
            tried = {h["params"]["current_A"] for h in history if h.get("action") == "simulate_charge"}
            return [("simulate_charge", {"current_A": float(c), "ambient_C": AMBIENT})
                    for c in range(1, 9) if float(c) not in tried][:min(2, remaining)]
    bench(L, "loop-test-cell", 1.0)
    cid = LoopService(L, ANA).open_campaign(objective(
        datasets=["bench"], baseline=["bench/baseline-1"],
        planner={"kind": "domain_baseline", "profile": None, "client": None}), "Sweep")["campaign_id"]
    planner = DomainBaselinePlanner(L, cid, Pairs())
    run_turn(L, cid, planner)
    first = [s.request_id for s in request_states(L, cid).values() if s.state == "awaiting_approval"]
    assert len(first) == 2                                           # the policy's own batch, in one turn
    LoopService(L, ANA).note(cid, "Looking at these.")               # starts a turn; nothing is approved yet
    run_turn(L, cid, DomainBaselinePlanner(L, cid, Pairs()))
    assert len(request_states(L, cid)) == 2                          # it waits rather than stacking more
    LoopService(L, ANA).amend(cid, "Cooler.", constraints=[{"quantity": "max_temperature", "op": "<=",
                                                            "limit": 44.0, "unit": "degC", "label": "44 degC"}])
    assert all(request_states(L, cid)[rq].state == "cancelled" for rq in first)
    run_turn(L, cid, DomainBaselinePlanner(L, cid, Pairs()))
    fresh = [s for s in request_states(L, cid).values() if s.state == "awaiting_approval"]
    assert sorted(s.data.params["current_A"] for s in fresh) == [1.0, 2.0]        # the same points, again
    assert {s.data.objective_version for s in fresh} == {2} and experiments_used(request_states(L, cid)) == 2


# ------------------------------------------------------------------ a revised request names its reviser

def test_a_revised_request_shows_who_wrote_the_current_version(L):
    cid = open_ref(L, evaluator="loop-compare/2")
    req = propose(L, cid, 2.0)
    r1 = next(r for r in view(L, cid)["requests"] if r["request_id"] == req["request_id"])
    assert r1["proposed_by"] == "agent:muse-test" and "revision" not in r1
    LoopService(L, ANA).revise(cid, req["request_id"], {"current_A": 2.5, "ambient_C": AMBIENT}, "Less heat.")
    r2 = next(r for r in view(L, cid)["requests"] if r["request_id"] == req["request_id"])
    assert r2["proposed_by"] == "agent:muse-test" and r2["version"] == 2
    assert r2["revision"] == {"by": "person:Ana", "version": 2, "words": "revised by Ana (v2)"}


# ------------------------------------------------------------------ review fixes (m2-semantics-review.md, 1-9)

def _v(x, h=0.0, unit="s"):
    return {"value": x, "unit": unit, "h": h}


def _obj3(**target):
    from translucent.loop_contracts import ObjectiveData
    return ObjectiveData.model_validate(objective(
        constraints=LIMIT_42, evaluator="loop-compare/3", kind="fastest_feasible",
        target={"quantity": "charge_time_80", "unit": "s", "direction": "minimize"} | target))


HOT = {"charge_time_80": _v(1600.0), "max_temperature": _v(43.2, unit="degC")}
REF_2100 = {"label": "r", "source": "reference_search", "objective_version": 1, "request_id": "rq_r",
            "values": {"charge_time_80": _v(2100.0), "max_temperature": _v(41.8, unit="degC")}}


def _cand(t80, temp=41.0):
    return {"request_id": "rq_x", "result_id": "res_x",
            "values": {"charge_time_80": t80 if isinstance(t80, dict) else _v(t80),
                       "max_temperature": _v(temp, unit="degC")}}


def test_1_a_negative_margin_never_makes_a_slower_result_faster(L):
    out = loop_compare.compare3(_obj3(margin=-300.0), "computational", {"refs": ["b/1"], "values": HOT},
                                _cand(2300.0), REF_2100)        # 200 s SLOWER than the reference
    assert out["speed"]["status"] == "not_faster" and out["speed"]["faster"] is False
    assert out["verdict"] == "not_better" and "-300" not in out["speed"]["reason"]
    fast = loop_compare.compare3(_obj3(margin=-300.0), "computational", {"refs": ["b/1"], "values": HOT},
                                 _cand(2000.0), REF_2100)
    assert fast["speed"]["status"] == "faster"                  # actually faster still counts
    # loop-compare/3 refuses a negative margin at registration and by amendment, in a plain sentence
    bench(L)
    data = objective(system={"id": "loop-ref-cell"}, datasets=["bench"], baseline=["bench/baseline-1"],
                     constraints=LIMIT_42, evaluator="loop-compare/3", kind="fastest_feasible",
                     target={"quantity": "charge_time_80", "unit": "s", "direction": "minimize", "margin": -300.0})
    with pytest.raises(AdmissionError, match="margin cannot be negative"):
        LoopService(L, ANA).open_campaign(data, "Slower allowed")
    cid = LoopService(L, ANA).open_campaign(dict(data, target=dict(data["target"], margin=0.0)), "x")["campaign_id"]
    with pytest.raises(AdmissionError, match="margin cannot be negative"):
        LoopService(L, ANA).amend(cid, "Allow slower.", target=dict(data["target"], margin=-1.0))
    # loop-compare/2 keeps its rules: a negative margin is still a slowdown it allows
    LoopService(L, ANA).open_campaign(dict(data, evaluator="loop-compare/2", kind=None), "legacy")


def test_2_the_campaign_list_says_feasibility_recovered_never_improved(L):
    from pydantic import TypeAdapter
    from translucent.loop_contracts import CampaignSummary
    cid = open_ref(L, evaluator="loop-compare/2")
    tick(L, cid)
    simulate_at(L, cid, 3.0)                                         # meets 42 degC; the 9 A baseline breaks it
    assert LoopService(L, MUSE).conclude(cid, "Meets the limit.")["data"]["outcome"] == "improved_in_simulation"
    (item,) = LoopService(L, ANA).campaigns()["items"]
    TypeAdapter(CampaignSummary).validate_python(item)
    assert item["outcome"] == "improved_in_simulation"               # the recorded value is never rewritten
    f = item["findings"]
    assert f["basis"] == "computational" and f["feasibility"]["recovered"] is True
    assert f["speed"]["status"] == "not_compared"
    assert item["assessment_headline"].startswith("Feasibility recovered in simulation:")
    assert "improved" not in item["assessment_headline"].lower()


def test_3_the_summary_is_built_from_the_current_limits_never_a_label(L):
    bench(L, "loop-test-cell", 1.0)
    cid = LoopService(L, ANA).open_campaign(objective(datasets=["bench"], baseline=["bench/baseline-1"]),
                                            "S")["campaign_id"]
    c0 = objective()["constraints"][0]                               # label "Cell stays at or below 45 degC"
    LoopService(L, ANA).amend(cid, "Cooler.", constraints=[dict(c0, limit=40.0)])     # the label is left as it was
    v = view(L, cid)
    assert v["objective"]["summary"] == "Minimise time to 80 % charge with peak temperature <= 40 degC"
    assert "45" in v["objective"]["statement"] and v["objective"]["statement_version"] == 1     # as registered
    (item,) = LoopService(L, ANA).campaigns()["items"]
    assert item["objective"] == v["objective"]["summary"]           # the list follows the current version too


@pytest.mark.parametrize("evaluator", ["loop-compare/3", "loop-compare/2"])
def test_4_results_compared_but_all_undecided_are_said_so(L, evaluator):
    late = {"name": "late_T", "unit": "degC", "source": "operator",       # no result has a value at 1e9 s
            "operator": {"op": "value_at", "channel": "temperature", "at_s": 1e9, "unit": "degC"}}
    bench(L)
    data = objective(system={"id": "loop-ref-cell"}, datasets=["bench"], baseline=["bench/baseline-1"],
                     baseline_simulation=True, evaluator=evaluator,
                     constraints=LIMIT_42 + [{"quantity": "late_T", "op": "<=", "limit": 100.0, "unit": "degC",
                                              "label": "late"}],
                     quantities=objective()["quantities"] + [late])
    cid = LoopService(L, ANA).open_campaign(data, "x")["campaign_id"]
    tick(L, cid)
    rq = simulate_at(L, cid, 3.0)
    assert latest(L, cid, rq).data["verdict"] == "not_comparable"
    a = view(L, cid)["assessment"]
    assert a["headline"].startswith("1 result, none decided in simulation: ") and "late_T" in a["headline"]
    assert a["feasibility"]["best"] == "unknown" and a["feasibility"]["best_request_id"] == rq


OP_TARGET = {"name": "time_to_41_5", "unit": "s", "source": "operator",       # minimised; never reached below 6.1 A
             "operator": {"op": "time_to_threshold", "channel": "temperature", "threshold": 41.5,
                          "direction": "rising", "unit": "s"}}


def test_5_a_target_never_reached_is_not_feasible_in_the_comparison_and_the_search(L):
    never = loop_compare.compare3(_obj3(), "computational", {"refs": ["b/1"], "values": HOT},
                                  _cand({"value": None, "unit": "s", "h": 0.0, "not_reached_within": [0, 7200]}),
                                  None, reference_reason="none")
    assert never["feasibility"] == {"baseline": "fails", "candidate": "fails"}
    assert never["verdict"] == "violates_constraints" and never["speed"]["status"] == "not_compared"
    bench(L)
    data = objective(system={"id": "loop-ref-cell"}, datasets=["bench"], baseline=["bench/baseline-1"],
                     baseline_simulation=True, evaluator="loop-compare/3", kind="fastest_feasible",
                     target={"quantity": "time_to_41_5", "unit": "s", "direction": "minimize"},
                     constraints=LIMIT_42, quantities=objective()["quantities"] + [OP_TARGET])
    cid = LoopService(L, ANA).open_campaign(data, "x")["campaign_id"]
    tick(L, cid)
    s = loop_reference.state(L, cid, *_ctx(L, cid))
    # 5.0 A never reaches 41.5 degC: too slow, so the search moves up (not down) and finds 7.0 A
    assert [(x["value"], x["meets"]) for x in s["steps"]] == [(5.0, False), (7.5, False), (6.0, False), (6.5, True),
                                                             (7.0, True)]
    assert s["status"] == "found" and s["found"]["value"] == 7.0
    rq = simulate_at(L, cid, 5.0)                                    # meets 42 degC, never reaches the target
    c = latest(L, cid, rq).data
    assert c["feasibility"]["candidate"] == "fails" and c["verdict"] == "violates_constraints"
    done = LoopService(L, MUSE).conclude(cid, "x")["data"]
    assert done["outcome"] == "no_feasible_result_in_simulation"
    assert done["findings"]["feasibility"]["recovered"] is False
    v = view(L, cid)
    assert v["assessment"]["headline"].startswith("No result is feasible in simulation: ")
    assert "never reaches its target" in v["current_result"]["sentence"]
    assert "outside a constraint" not in v["current_result"]["sentence"]


def test_6_a_baseline_that_meets_the_constraints_is_the_reference_and_no_search_runs(L):
    cid = open_ref(L, baseline_A=5.0)                                # 41.0 degC: meets 42
    assert references(L, cid) == []                                  # nothing runs before the baseline is known
    tick(L, cid, rounds=1)
    assert references(L, cid) == []
    s = loop_reference.state(L, cid, *_ctx(L, cid))
    assert s["status"] == "not_needed" and s["next"] is None
    LoopService(L, MUSE).conclude(cid, "Nothing tried.")             # not blocked by a search it does not need


def test_6b_with_a_feasible_baseline_speed_is_compared_with_the_baseline(L):
    cid = open_ref(L, baseline_A=5.0)
    tick(L, cid)
    rq = simulate_at(L, cid, 6.0)                                    # 41.44 degC, 2400 s against 2880 s
    c = latest(L, cid, rq).data
    assert c["speed"]["reference"]["source"] == "baseline" and c["speed"]["status"] == "faster"
    assert references(L, cid) == []


def test_7_a_budget_or_label_amendment_leaves_waiting_proposals_alone(L):
    cid = open_ref(L, evaluator="loop-compare/2")
    w = propose(L, cid, 2.0)["request_id"]
    LoopService(L, ANA).amend(cid, "Fewer runs.", budget={"experiments": 6})      # (raising one is an admin's)
    LoopService(L, ANA).amend(cid, "New words.", constraints=[dict(LIMIT_42[0], label="42 degC")])
    assert request_states(L, cid)[w].state == "awaiting_approval"
    with pytest.raises(AdmissionError, match="changed the target or the constraints"):
        chain.append(L, cid, "cancellation", {"request_id": w, "author": "system:objective",
                                              "reason": "objective amended to v3"},
                     "system:objective", chain.natural_id("cnl", w))
    LoopService(L, ANA).amend(cid, "Cooler.", constraints=[dict(LIMIT_42[0], limit=41.5)])
    st = request_states(L, cid)[w]
    assert st.state == "cancelled" and st.closed_by.data["reason"] == "objective amended to v4"
    assert verify(L.root).ok


def test_8_a_loop_compare_3_export_cannot_drop_its_assessment_or_findings(L, tmp_path):
    import json
    cid = open_ref(L)
    tick(L, cid)
    simulate_at(L, cid, 3.0)
    LoopService(L, MUSE).conclude(cid, "Feasible only.")
    path = loop_export.export(L, cid, tmp_path / "ref.loop.json")
    assert verify(path).ok
    data = json.loads(path.read_text(encoding="utf-8"))
    stripped = loop_export.without_m2_fields(data["view"])
    for v in (stripped, {k: x for k, x in data["view"].items() if k != "assessment"}):
        path.write_text(json.dumps(dict(data, view=v)), encoding="utf-8")
        r = verify(path)
        assert not r.ok and "Loop export view differs from the recorded evidence" in r.problems


CAP_TEMPLATE = TEMPLATE | {"reference_search": TEMPLATE["reference_search"] | {"max_runs": 2}}


class CapCell(RefCell):
    spec = SystemSpec(id="loop-ref-cap", version="1", title="Analytic cell, a two-run reference search",
                      description=LOOP_SPEC.description, quantities=LOOP_SPEC.quantities,
                      actions=LOOP_SPEC.actions, solver=LOOP_SPEC.solver, objective_template=CAP_TEMPLATE)


def test_9_the_run_cap_is_reported_as_the_cap_with_the_bracket(L):
    register(None, CapCell)
    cid = open_ref(L, system="loop-ref-cap", constraints=[dict(LIMIT_42[0], limit=40.05)])    # 1.0 A and below
    tick(L, cid)
    s = loop_reference.state(L, cid, *_ctx(L, cid))
    assert s["status"] == "stopped_at_cap" and s["bracket"] == [None, 2.5]
    rq = simulate_at(L, cid, 0.5)                                    # 40.005 degC: meets 40.05
    why = latest(L, cid, rq).data["speed"]["reason"]
    assert "run cap" in why and "before the search finished" in why and "2.5 A" in why
    assert "found no single-step protocol" not in why
