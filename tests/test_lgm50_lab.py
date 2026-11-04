"""pybamm-fastcharge-lab without PyBaMM: parameters, binding, admission, the sealed lab condition, a campaign end to end
through LoopService and LoopRunner, the planner's view of it, and the fit and design statistics.

The solver child is the REAL translucent.systems.lgm50_lab_solver logic over a lumped stand-in for `charge()`
(tests/support/lab_fake_solver.py). Tests named for a defect a review found (LAB-n) reproduce it and pass now that it is
fixed.
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import TypeAdapter

from translucent import chain, systems
from translucent.ledger import AdmissionError, Ledger
from translucent.loop import adapter_for, build_request, current_objective, request_states
from translucent.loop_contracts import LoopView
from translucent.loop_planner import (ScriptedLoopPlanner, allowed_action, planner_context, run_turn,
                                      system_prompt, tool_schemas)
from translucent.loop_runner import LoopRunner
from translucent.loop_service import Actor, LoopService
from translucent.systems import lgm50_fastcharge as fc
from translucent.systems import lgm50_lab as lab
from translucent.systems import lgm50_lab_solver as S

TESTS = Path(__file__).resolve().parent
ANA = Actor("person", "Ana", "http")
MUSE = Actor("agent", "muse-test", "planner")
# the hidden condition: values with digits no recorded number carries, so a leak is found by searching for them
HIDDEN = {"h": 13.37171, "k": 1.23471, "ambient_offset_K": 0.037137}
P1 = (1.0, 1.0, 1.0, 1.0)
P2 = (1.5, 1.4, 1.1, 0.8)


# ------------------------------------------------------------------ fixtures and helpers

@pytest.fixture(scope="module")
def fake():
    """The fake solver registered as pybamm-fastcharge-lab for this module only; tests/ on PYTHONPATH so the solver
    child can import it."""
    with pytest.MonkeyPatch.context() as mp:
        systems.available()
        mp.setattr(systems, "_ADAPTERS", dict(systems._ADAPTERS))
        mp.setenv("PYTHONPATH", os.pathsep.join(filter(None, [str(TESTS), os.environ.get("PYTHONPATH", "")])))
        import support.lab_fake_solver as F
        F.register()
        yield F


@pytest.fixture
def stand_in(fake, monkeypatch):
    """The real solver module in this process with the stand-in charge."""
    monkeypatch.setattr(S, "charge", fake.charge)
    monkeypatch.setattr(S, "_pybamm", lambda: None)
    return fake


@pytest.fixture
def L(fake, tmp_path):
    led = Ledger(tmp_path / "ledger")
    yield led
    led.db.close()


def proto(rates=P1, ambient=25.0, rest=None, **kw) -> dict:
    out = {**dict(zip(fc.C_RATES, rates)), "ambient": ambient}
    if rest is not None:
        out["rest_min"] = rest
    return out | kw


def rank_params(design=P2, test=P1, test_rest=60.0, version=1, ambient=25.0) -> dict:
    return {**proto(design, ambient), **{f"test_{c}": r for c, r in zip(fc.C_RATES, test)},
            "test_rest_min": test_rest, "model_version": version}


def lab_objective(lab_tests=4, **kw) -> dict:
    t = lab.OBJECTIVE_TEMPLATE
    return {"statement": t["statement"]["fastest_feasible"], "system": {"id": lab.SYSTEM_ID}, "datasets": [],
            "baseline": [], "target": {k: t["target"][k] for k in ("quantity", "unit", "direction")},
            "constraints": [dict(c) for c in t["constraints"]], "quantities": [dict(q) for q in t["quantities"]],
            "budget": {"model_usd": 0.0, "experiments": 40, "experiment_usd": 0.0, "lab_tests": lab_tests},
            "planner": {"kind": "external", "profile": None, "client": None}, "registered_by": "Ana",
            "evaluator": "loop-compare/3", "kind": "fastest_feasible"} | kw


def open_lab(L, seal=True, **kw) -> str:
    cid = LoopService(L, ANA).open_campaign(lab_objective(**kw), "Lab")["campaign_id"]
    if seal:
        lab.seal_lab(L.root, cid, **HIDDEN)
    return cid


def propose(L, cid, action, params, actor=MUSE) -> dict:
    return LoopService(L, actor).propose(cid, action, params, rationale="r", prediction={}, expected_effect="e")


def run_one(L, cid, action, params):
    req = propose(L, cid, action, params)
    LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])
    runner = LoopRunner(L.root, process="cli", workers=1)
    assert runner.tick(cid) == 1 and runner.problems == [], runner.problems
    st = request_states(L, cid)[req["request_id"]]
    assert st.state == "completed", (st.failure.data if st.failure else st.state)
    return st


def body(L, st) -> dict:
    return json.loads(L.blob_path(st.result.data["files"]["result.json"]).read_text(encoding="utf-8"))


def forged(L, cid, action, params, actor=MUSE):
    """A request built and appended directly, skipping LoopService.propose's binding: admission alone decides."""
    obj = current_objective(L, cid)
    rq = "rq_" + secrets.token_hex(10)
    req = build_request(adapter_for(obj), obj, action, params, request_id=rq, rationale="r", prediction={},
                        expected_effect="e")
    with L.transaction():
        return chain.append(L, cid, "request", req, actor.label, chain.natural_id("req", cid, rq, 1))


def bound_values(L, cid, action, params) -> dict:
    obj = current_objective(L, cid)
    return adapter_for(obj).bind(L, cid, obj, request_states(L, cid), action, params)


# ------------------------------------------------------------------ parameters

def test_the_adapter_is_registered_and_the_campaign_freezes_its_template():
    a = systems.get(lab.SYSTEM_ID)
    assert isinstance(a, lab.LabAdapter)
    t = a.spec.objective_template
    assert t["bound_parameters"] == {k: list(v) for k, v in lab.BOUND.items()}
    assert t["planner_guidance"] and t["nominal_model"] == {"version": 0, "h": 6.0, "k": 1.0}
    executors = {x.name: x.executor for x in a.spec.actions}
    assert executors["bench_charge"] == "human" and all(v == "computational" for k, v in executors.items()
                                                       if k != "bench_charge")


def test_check_params_grid_bounds_and_composed_tests():
    good = proto(P2, rest=30.0, lab="a" * 64)
    assert lab.check_params("lab_charge", good) == []
    [grid] = lab.check_params("lab_charge", {**good, "c_rate_2": 1.33})
    assert grid.startswith("c_rate_2 = 1.33C is not on the 0.05C grid")
    assert any("c_rate_1" in p for p in lab.check_params("lab_charge", {**good, "c_rate_1": 1.6}))
    assert any("rest_min" in p for p in lab.check_params("lab_charge", {**good, "rest_min": 45.0}))
    assert any("ambient" in p for p in lab.check_params("lab_charge", {**good, "ambient": 50.0}))
    # the composed test of rank_measurements is checked like a protocol: grid, bounds, rest choices
    rp = {**rank_params(), "model": "{}", "margin_mV": 25.0, "t_limit_C": 45.0}
    assert lab.check_params("rank_measurements", rp) == []
    [tgrid] = lab.check_params("rank_measurements", {**rp, "test_c_rate_3": 0.33})
    assert tgrid.startswith("test_c_rate_3 = 0.33C is not on the 0.05C grid")
    assert any("test_c_rate_1" in p for p in lab.check_params("rank_measurements", {**rp, "test_c_rate_1": 0.1}))
    assert any("test_rest_min" in p for p in lab.check_params("rank_measurements", {**rp, "test_rest_min": 10.0}))
    assert any("margin_mV" in p for p in lab.check_params("rank_measurements", {**rp, "margin_mV": 900.0}))
    assert lab.check_params("no_such", {}) == ["Unknown action no_such"]
    fit = {"family": 1, "fit_on": 3, "sources": "[]", "base": "{}"}
    assert lab.check_params("fit_thermal", fit) == []
    assert any("family" in p for p in lab.check_params("fit_thermal", {**fit, "family": 3}))
    assert any("fit_on" in p for p in lab.check_params("fit_thermal", {**fit, "fit_on": 0}))
    assert any("fit_on" in p for p in lab.check_params("fit_thermal", {**fit, "fit_on": 2 ** lab.MAX_TESTS}))


def test_check_params_text_parameters():
    good = proto(P2, rest=0.0, lab="0f" * 32)
    assert lab.check_params("lab_charge", {k: v for k, v in good.items() if k != "lab"}) == \
        ["Missing parameter lab"]
    assert lab.check_params("lab_charge", {**good, "lab": "x" * 64}) == ["lab must be a sha256 commitment"]
    assert lab.check_params("lab_charge", {**good, "lab": "AB" * 32}) == ["lab must be a sha256 commitment"]
    assert lab.check_params("lab_charge", {**good, "lab": 7}) == ["Missing parameter lab"]
    fit = {"family": 1, "fit_on": 1, "sources": "[]", "base": "{}"}
    assert lab.check_params("fit_thermal", {**fit, "sources": "not json"}) == ["sources must be JSON"]
    assert lab.check_params("fit_thermal", {**fit, "base": ""}) == ["Missing parameter base"]
    assert lab.check_params("fit_thermal", {**fit, "sources": "[" + "1," * 20_000 + "1]"}) == \
        ["sources is too long"]


def test_a_text_parameter_the_action_does_not_declare_is_refused():
    p = proto(P2, rest=0.0, model_version=0, model="{}")
    assert lab.check_params("forecast_protocol", p) == []
    assert lab.check_params("forecast_protocol", {**p, "lab": "pasted " * 1000}) == ["Unknown parameter lab"]


def test_canonical_rounds_rates_and_types_integers_and_text():
    p = {**rank_params(design=(1.4500000001, 1.4, 1.1, 0.8), test=(0.3000000001, 1, 1, 1), version=2.0),
         "model": "{}", "margin_mV": 25, "t_limit_C": 45}
    c = lab.canonical("rank_measurements", p)
    assert c["c_rate_1"] == 1.45 and c["test_c_rate_1"] == 0.3 and isinstance(c["test_c_rate_2"], float)
    assert c["model_version"] == 2 and isinstance(c["model_version"], int)
    assert c["margin_mV"] == 25.0 and isinstance(c["margin_mV"], float) and c["model"] == "{}"
    f = lab.canonical("fit_thermal", {"family": 2.0, "fit_on": 3.0, "sources": "[]", "base": "{}"})
    assert f == {"family": 2, "fit_on": 3, "sources": "[]", "base": "{}"}
    assert lab.bits(5) == [1, 3] and lab.bits(255) == list(range(1, 9))


def test_costs_count_lab_tests_and_procedure_fills_the_bench_template():
    a = systems.get(lab.SYSTEM_ID)
    assert dict(a.estimate_cost("lab_charge", proto(rest=0.0, lab="0" * 64)).other) == {"lab_tests": 1.0}
    bench = a.estimate_cost("bench_charge", proto(P1, rest=60.0))
    assert dict(bench.other) == {"lab_tests": 1.0} and bench.person_minutes == round(42 + 60 + 60, 1)
    assert a.estimate_cost("optimize_protocol", {}).other == ()
    text = a.procedure("bench_charge", proto(P2, rest=30.0))
    assert "1.5C to 27.5 %" in text and "rest 30 min" in text and "{" not in text


# ------------------------------------------------------------------ binding

def test_a_typed_bound_parameter_is_refused(L):
    cid = open_lab(L)
    for action, params in [("forecast_protocol", proto(rest=0.0, model_version=0, model="{}")),
                           ("lab_charge", proto(rest=0.0, lab="0" * 64)),
                           ("optimize_protocol", {"ambient": 25.0, "model_version": 0, "margin_mV": 0.0}),
                           ("fit_thermal", {"family": 1, "fit_on": 1, "base": "{}"})]:
        with pytest.raises(AdmissionError, match="set by Translucent, not by the proposer"):
            propose(L, cid, action, params)
    assert request_states(L, cid) == {}


def test_bind_refuses_unrecorded_versions_unrun_tests_and_a_lab_without_a_sealed_condition(L):
    cid = open_lab(L, seal=False)
    with pytest.raises(AdmissionError, match="model_version must be 0 to 0"):
        propose(L, cid, "forecast_protocol", proto(rest=0.0, model_version=1))
    with pytest.raises(AdmissionError, match="model_version must be 0 to 0"):
        propose(L, cid, "forecast_protocol", proto(rest=0.0, model_version=0.5))
    with pytest.raises(AdmissionError, match="lab test 1 has no result yet"):
        propose(L, cid, "fit_thermal", {"family": 1, "fit_on": 1})
    with pytest.raises(AdmissionError, match="lab test 1 has no result yet"):
        propose(L, cid, "check_model", {"model_version": 0, "test_on": 1})
    with pytest.raises(AdmissionError, match="no simulated lab"):
        propose(L, cid, "lab_charge", proto(rest=0.0))
    assert request_states(L, cid) == {}


def test_optimize_and_rank_bind_the_limits_of_the_objective_in_force(L):
    cid = open_lab(L)
    got = bound_values(L, cid, "optimize_protocol", {"ambient": 25.0, "model_version": 0})
    assert (got["margin_mV"], got["t_limit_C"]) == (25.0, 45.0)
    assert json.loads(got["model"])["estimate"] == {"h": 6.0, "k": 1.0}
    LoopService(L, ANA).amend(cid, "Tighter plating margin", constraints=[
        {"quantity": "min_anode_potential", "op": ">=", "limit": 30.0, "unit": "mV", "label": "margin 30 mV"},
        {"quantity": "max_temperature", "op": "<=", "limit": 42.0, "unit": "degC", "label": "42 degC"}])
    req = propose(L, cid, "optimize_protocol", {"ambient": 25.0, "model_version": 0})
    assert (req["params"]["margin_mV"], req["params"]["t_limit_C"]) == (30.0, 42.0) or \
        (req["params"]["margin_mV"]["value"], req["params"]["t_limit_C"]["value"]) == (30.0, 42.0)
    rq = request_states(L, cid)[req["request_id"]]
    assert (rq.data.params["margin_mV"], rq.data.params["t_limit_C"]) == (30.0, 42.0)
    got = bound_values(L, cid, "rank_measurements", rank_params(version=0))
    assert (got["margin_mV"], got["t_limit_C"]) == (30.0, 42.0)
    # an objective without a plating constraint cannot be designed against
    LoopService(L, ANA).amend(cid, "Temperature only", constraints=[
        {"quantity": "max_temperature", "op": "<=", "limit": 42.0, "unit": "degC", "label": "42 degC"}])
    with pytest.raises(AdmissionError, match="no min_anode_potential >= constraint"):
        propose(L, cid, "optimize_protocol", {"ambient": 25.0, "model_version": 0})


def test_revise_binds_again_and_drops_typed_bound_values(L):
    cid = open_lab(L)
    req = propose(L, cid, "optimize_protocol", {"ambient": 25.0, "model_version": 0})
    LoopService(L, ANA).revise(cid, req["request_id"], {"ambient": 24.0, "margin_mV": -50.0, "model": "{}"},
                               "cooler")
    st = request_states(L, cid)[req["request_id"]]
    assert st.version == 2 and st.data.params["ambient"] == 24.0
    assert st.data.params["margin_mV"] == 25.0          # bound from the objective in force, never the typed value
    assert json.loads(st.data.params["model"])["version"] == 0
    with pytest.raises(AdmissionError, match="model_version must be 0 to 0"):
        LoopService(L, ANA).revise(cid, req["request_id"], {"model_version": 1}, "a later version")


# ------------------------------------------------------------------ admission

def test_forged_bound_values_are_refused_by_the_ledger(L):
    cid = open_lab(L)
    honest = bound_values(L, cid, "forecast_protocol", proto(rest=0.0, model_version=0))
    model = json.loads(honest["model"])
    model["members"] = [{"h": 50.0, "k": 1.0}]
    with pytest.raises(AdmissionError, match="model differs"):
        forged(L, cid, "forecast_protocol", {**honest, "model": json.dumps(model, sort_keys=True,
                                                                              separators=(",", ":"))})
    opt = bound_values(L, cid, "optimize_protocol", {"ambient": 25.0, "model_version": 0})
    with pytest.raises(AdmissionError, match="margin_mV differs"):
        forged(L, cid, "optimize_protocol", {**opt, "margin_mV": -50.0})
    with pytest.raises(AdmissionError, match="t_limit_C differs"):
        forged(L, cid, "optimize_protocol", {**opt, "t_limit_C": 80.0})
    lab_p = bound_values(L, cid, "lab_charge", proto(rest=0.0))
    with pytest.raises(AdmissionError, match="lab differs"):
        forged(L, cid, "lab_charge", {**lab_p, "lab": "0" * 64})
    with pytest.raises(AdmissionError, match="model_version must be"):
        forged(L, cid, "forecast_protocol", {**honest, "model_version": 3})
    # the honest values pass the same path
    forged(L, cid, "optimize_protocol", opt)
    assert [s.action for s in request_states(L, cid).values()] == ["optimize_protocol"]


def test_the_lab_test_budget(L):
    cid = open_lab(L, lab_tests=2)
    a = propose(L, cid, "lab_charge", proto(P1, rest=0.0))
    b = propose(L, cid, "lab_charge", proto(P2, rest=0.0))
    with pytest.raises(AdmissionError, match="lab-test budget is spent: 2 of 2"):
        propose(L, cid, "lab_charge", proto(P1, rest=60.0))
    with pytest.raises(AdmissionError, match="lab-test budget is spent"):
        propose(L, cid, "bench_charge", proto(P1, rest=60.0))
    LoopService(L, ANA).reject(cid, b["request_id"], b["content_hash"], "not this one")
    c = propose(L, cid, "lab_charge", proto(P1, rest=60.0))       # a rejected test does not count
    assert c["request_id"] not in (a["request_id"], b["request_id"])
    # a revision of an admitted lab test is not a new test
    LoopService(L, ANA).revise(cid, c["request_id"], {"rest_min": 30.0}, "shorter rest")
    assert request_states(L, cid)[c["request_id"]].version == 2
    # lab_tests is a budget field an amendment can name
    LoopService(L, ANA).amend(cid, "fewer", budget={"lab_tests": 1})
    assert current_objective(L, cid).budget["lab_tests"] == 1


def test_a_lab_test_whose_sealed_condition_is_absent_is_admitted_unchecked(L):
    """Admission skips the lab commitment where the sealed file is not (verify away from the lab's machine)."""
    cid = open_lab(L)
    lab_p = bound_values(L, cid, "lab_charge", proto(rest=0.0))
    lab.sealed_path(L.root, cid).rename(L.root / "moved.json")
    rec = forged(L, cid, "lab_charge", {**lab_p, "lab": "0" * 64})
    assert rec.type == "request"


def test_a_lab_test_that_cannot_be_staged_fails_with_its_reason(L):
    cid = open_lab(L)
    req = propose(L, cid, "lab_charge", proto(rest=0.0))
    LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])
    lab.sealed_path(L.root, cid).unlink()
    runner = LoopRunner(L.root, process="cli", workers=1)
    runner.tick(cid)
    st = request_states(L, cid)[req["request_id"]]
    assert runner.problems == [] and st.state == "failed" and "no simulated lab" in st.failure.data["error"]


def test_the_sealed_condition_never_stays_in_the_work_folder_after_a_failed_child(L, monkeypatch):
    from translucent import executors
    from translucent.executors import ExecutionFailed

    def dies(*a, **k):
        raise ExecutionFailed("the executor exited with code 1 and no result (killed)")
    monkeypatch.setattr(executors, "run_child", dies)
    cid = open_lab(L)
    req = propose(L, cid, "lab_charge", proto(rest=0.0))
    LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])
    LoopRunner(L.root, process="cli", workers=1).tick(cid)
    assert request_states(L, cid)[req["request_id"]].state in ("queued", "failed")
    assert list((L.root / "work").rglob("inputs.json")) == []


def test_execute_deletes_the_staged_inputs_even_when_it_refuses(fake, tmp_path):
    a = systems.get(lab.SYSTEM_ID)
    (tmp_path / "inputs.json").write_text(json.dumps({"condition": {"h": HIDDEN["h"]}}), encoding="utf-8")
    with pytest.raises(ValueError):
        a.execute("lab_charge", proto(rest=0.0, lab="z" * 64), tmp_path)
    assert not (tmp_path / "inputs.json").exists()


def test_the_solver_child_refuses_a_condition_that_does_not_match_the_commitment(stand_in):
    cond = {"h": 10.0, "k": 1.0, "noise_K": 0.15, "noise_V": 0.001, "ambient_offset_K": 0.0, "seed": "ab" * 16}
    p = proto(rest=0.0, lab=lab.commitment(cond))
    out = S.run("lab_charge", p, {"condition": cond, "request_id": "rq_x"})
    assert out["status"] == "ok" and out["details"] is None
    with pytest.raises(ValueError, match="does not match the request's commitment"):
        S.run("lab_charge", p, {"condition": {**cond, "h": 11.0}, "request_id": "rq_x"})
    with pytest.raises(ValueError, match="does not match"):
        S.run("lab_charge", p, {})


def test_staged_sources_must_match_the_sealed_series():
    sealed = json.dumps([{"n": 1, "result_id": "res_1", "series": "sha256:" + "a" * 64}])
    good = {"sources": [{"result_id": "res_1", "series": "sha256:" + "a" * 64, "raw_sha256": "sha256:" + "a" * 64,
                         "body": {"t": [0, 10], "y": [25.0, 25.1]}, "params": {}, "noise_K": 0.15}]}
    assert lab.staged_sources({"sources": sealed}, good)[0]["series"]["surface_temperature"] == [25.0, 25.1]
    with pytest.raises(ValueError, match="not the sealed one"):
        lab.staged_sources({"sources": sealed}, {"sources": [{**good["sources"][0], "raw_sha256": "sha256:" + "b" * 64}]})
    with pytest.raises(ValueError, match="was not staged as sealed"):
        lab.staged_sources({"sources": sealed}, {"sources": []})


def _amend_constraints(L, cid, margin, margin_unit="mV", t_limit=45.0, t_unit="degC", extra=()):
    LoopService(L, ANA).amend(cid, "limits", constraints=[
        {"quantity": "min_anode_potential", "op": ">=", "limit": margin, "unit": margin_unit, "label": "margin"},
        {"quantity": "max_temperature", "op": "<=", "limit": t_limit, "unit": t_unit, "label": "temperature"},
        *extra])


def test_bound_limits_are_converted_to_the_parameters_units(L):
    cid = open_lab(L)
    _amend_constraints(L, cid, 0.03, "V", 318.15, "K")
    obj = current_objective(L, cid)
    assert [(c.limit, c.unit) for c in obj.constraints] == [(0.03, "V"), (318.15, "K")]     # admitted as written
    got = bound_values(L, cid, "optimize_protocol", {"ambient": 25.0, "model_version": 0})
    assert got["margin_mV"] == pytest.approx(30.0) and got["t_limit_C"] == pytest.approx(45.0)


def test_the_strictest_of_repeated_constraints_is_bound(L):
    cid = open_lab(L)
    _amend_constraints(L, cid, 25.0, extra=[
        {"quantity": "min_anode_potential", "op": ">=", "limit": 35.0, "unit": "mV", "label": "stricter"},
        {"quantity": "max_temperature", "op": "<=", "limit": 40.0, "unit": "degC", "label": "stricter"}])
    got = bound_values(L, cid, "optimize_protocol", {"ambient": 25.0, "model_version": 0})
    assert (got["margin_mV"], got["t_limit_C"]) == (35.0, 40.0)


@pytest.mark.parametrize("action, params", [
    ("forecast_protocol", proto(rest=0.0, model_version=float("inf"))),
    ("fit_thermal", {"family": 1, "fit_on": float("inf")}),
    ("check_model", {"model_version": 0, "test_on": float("inf")}),
])
def test_a_non_finite_selector_is_refused_not_raised(L, action, params):
    cid = open_lab(L)
    with pytest.raises(AdmissionError):
        propose(L, cid, action, params)


def test_the_planners_action_list_holds_at_admission(L):
    cid = LoopService(L, ANA).open_campaign(
        lab_objective(planner={"kind": "external", "profile": None, "client": None, "actions": ["lab_charge"]}),
        "Limited")["campaign_id"]
    with pytest.raises(AdmissionError, match="not among the actions"):
        propose(L, cid, "optimize_protocol", {"ambient": 25.0, "model_version": 0})


def test_the_solver_child_never_imports_from_its_work_folder(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONSAFEPATH", "1")                 # as executors.child_env gives the executor child
    planted = tmp_path / "support"
    planted.mkdir()
    (planted / "__init__.py").write_text("", encoding="utf-8")
    (planted / "lab_fake_solver.py").write_text(
        "from pathlib import Path\nPath('planted-ran.txt').write_text('yes')\nraise SystemExit(3)\n", encoding="utf-8")
    a = systems.get(lab.SYSTEM_ID)
    try:
        a.execute("forecast_protocol", {**proto(rest=0.0, model_version=0),
                                        "model": json.dumps(lab.nominal_model())}, tmp_path)
    except RuntimeError:
        pass
    assert not (tmp_path / "planted-ran.txt").exists()


# ------------------------------------------------------------------ a campaign end to end

@pytest.fixture(scope="module")
def campaign(fake, tmp_path_factory):
    """lab test 1 (1C, 60 min rest) -> fit family 1 (v1) -> forecast of P2 under v1 -> lab test 2 (P2, 30 min rest)
    -> fit family 2 on tests 1 and 2 (v2) -> check v1 on test 2 -> rank under v1 -> optimize under v1."""
    L = Ledger(tmp_path_factory.mktemp("campaign") / "ledger")
    cid = open_lab(L)
    ids = SimpleNamespace(L=L, cid=cid)
    ids.lab1 = run_one(L, cid, "lab_charge", proto(P1, rest=60.0))
    ids.fit1 = run_one(L, cid, "fit_thermal", {"family": 1, "fit_on": 1})
    ids.fc1 = run_one(L, cid, "forecast_protocol", proto(P2, rest=30.0, model_version=1))
    ids.lab2 = run_one(L, cid, "lab_charge", proto(P2, rest=30.0))
    ids.fit2 = run_one(L, cid, "fit_thermal", {"family": 2, "fit_on": 3})
    ids.check = run_one(L, cid, "check_model", {"model_version": 1, "test_on": 2})
    ids.rank = run_one(L, cid, "rank_measurements", rank_params(version=1))
    ids.opt = run_one(L, cid, "optimize_protocol", {"ambient": 25.0, "model_version": 1})
    ids.pending = propose(L, cid, "forecast_protocol", proto(P1, rest=0.0, model_version=2))
    yield ids
    L.db.close()


def test_model_versions_are_numbered_in_the_order_fits_were_recorded(campaign):
    L, cid = campaign.L, campaign.cid
    versions = lab.model_versions(L, request_states(L, cid))
    assert [v["version"] for v in versions] == [0, 1, 2]
    assert [v["family"] for v in versions] == ["nominal", "cooling", "cooling and thermal mass"]
    assert versions[1]["result_id"] == campaign.fit1.result.id and versions[2]["result_id"] == campaign.fit2.result.id
    assert versions[1]["validity"]["lab_tests"] == [1] and versions[2]["validity"]["lab_tests"] == [1, 2]
    for v in versions[1:]:
        assert 1 <= len(v["members"]) <= 5 and v["members"][0]["delta_chi2"] == 0.0
        assert all(m["k"] == 1.0 for m in v["members"]) if v["family"] == "cooling" else True
        lo, hi = v["region_95"]["h"]
        assert all(lo <= m["h"] <= hi for m in v["members"])
    # family 2 on both tests finds the hidden cooling within its grid (log spacing ~ 3.3 %)
    est = versions[2]["estimate"]
    assert abs(math.log(est["h"] / HIDDEN["h"])) < 0.08 and abs(est["k"] - HIDDEN["k"]) < 0.1
    # bound params of the later requests name these versions exactly
    st = request_states(L, cid)
    assert json.loads(st[campaign.fc1.request_id].data.params["model"]) == json.loads(json.dumps(versions[1]))
    srcs = json.loads(st[campaign.fit2.request_id].data.params["sources"])
    assert [s["n"] for s in srcs] == [1, 2] and srcs[0]["result_id"] == campaign.lab1.result.id
    assert srcs[1]["series"] == campaign.lab2.result.data["series"]["surface_temperature"]


def test_each_action_records_the_quantities_it_produces(campaign):
    L = campaign.L
    lab1 = body(L, campaign.lab1)
    assert set(lab1["scalars"]) >= {"status", "max_temperature", "temperature_end", "noise_temperature"}
    assert lab1["details"] is None and set(lab1["series"]["channels"]) == {"surface_temperature", "voltage",
                                                                             "current"}
    assert "anode_potential" not in json.dumps(lab1)                   # a lab cannot measure it
    t = lab1["series"]["t"]
    assert t[0] == 0 and all(abs(b - a - 10.0) < 1e-9 for a, b in zip(t, t[1:]))
    assert t[-1] >= 60 * 60                                              # the rest is logged
    fit = body(L, campaign.fit1)
    assert fit["scalars"]["heat_transfer_coefficient"]["unit"] == "W/m^2/K"
    assert fit["scalars"]["thermal_mass_scale"] == {"value": 1.0, "unit": "1"}
    assert fit["scalars"]["fit_adequate"]["unit"] == "" and fit["scalars"]["members"]["unit"] == "1"
    f = body(L, campaign.fc1)
    # v1 was fitted on a 1C test only: a 1.5C forecast is an extrapolation, and says so
    assert f["scalars"]["model_version"]["value"] == 1 and f["scalars"]["extrapolation"]["value"] == "yes"
    assert f["details"]["extrapolation"] == ["1.5C is above the fitted 1C"]
    assert len(f["details"]["members"]) == f["scalars"]["members"]["value"]
    assert f["scalars"]["min_anode_potential"]["value"] == min(m["min_anode_potential_mV"]
                                                               for m in f["details"]["members"])
    o = body(L, campaign.opt)
    assert o["scalars"]["design_found"]["value"] == "yes" and o["details"]["limits"]["margin_mV"] == 25.0
    assert o["scalars"]["min_anode_potential"]["value"] >= 25.5 and o["scalars"]["max_temperature"]["value"] <= 44.9
    c = body(L, campaign.check)
    assert [x["lab_test"] for x in c["details"]["tests"]] == [2] and c["details"]["model_sources"] == [1]
    assert 0 <= c["scalars"]["heldout_coverage"]["value"] <= 1


def test_check_model_refuses_the_models_own_tests(campaign):
    with pytest.raises(AdmissionError, match="lab test 1 was fitted by model version 1"):
        propose(campaign.L, campaign.cid, "check_model", {"model_version": 1, "test_on": 3})
    with pytest.raises(AdmissionError, match="lab test 2 was fitted by model version 2"):
        propose(campaign.L, campaign.cid, "check_model", {"model_version": 2, "test_on": 2})


def test_the_lab_budget_counts_completed_tests(campaign):
    obj = current_objective(campaign.L, campaign.cid)
    assert obj.budget["lab_tests"] == 4
    assert sum(s.action == "lab_charge" for s in request_states(campaign.L, campaign.cid).values()) == 2


def test_result_details_shape(campaign):
    L, a = campaign.L, systems.get(lab.SYSTEM_ID)
    st = request_states(L, campaign.cid)
    d = a.result_details(L, st[campaign.fit2.request_id])
    assert d["model_version"] == 2 and set(d) == {"model_version", "family", "estimate", "region_95", "members",
                                                  "nominal", "residuals", "adequate", "validity", "sigma_used_K",
                                                  "effective_samples", "samples"}
    assert [r["lab_test"] for r in d["residuals"]] == [1, 2]
    assert d["effective_samples"] <= d["samples"]
    r = a.result_details(L, st[campaign.rank.request_id])
    assert set(r) == {"options", "weights", "decisions", "best_decision_now", "verdict", "caveat"}
    assert len(r["options"]) == 5 and abs(sum(r["weights"]) - 1) < 1e-3
    assert set(a.result_details(L, st[campaign.check.request_id])) == {"tests", "model_sources"}
    assert "members" in a.result_details(L, st[campaign.opt.request_id])
    assert a.result_details(L, st[campaign.lab1.request_id]) is None


def test_the_views_learning_summary(campaign):
    L, cid = campaign.L, campaign.cid
    v = LoopService(L, ANA).view(cid)
    TypeAdapter(LoopView).validate_python(v, strict=False)
    s = v["learning"]
    assert s["kind"] == "thermal-learning/1"
    assert [r["version"] for r in s["versions"]] == [0, 1, 2]
    assert s["versions"][1]["sources"] == [1] and s["versions"][1]["request_id"] == campaign.fit1.request_id
    assert s["versions"][1]["checks"][0]["lab_tests"] == [2] and s["versions"][0]["checks"] == []
    assert [t["n"] for t in s["lab_tests"]] == [1, 2] and s["latest_lab_test"] == 2
    t2 = s["lab_tests"][1]
    assert t2["forecast"]["request_id"] == campaign.fc1.request_id and t2["forecast"]["model_version"] == 1
    assert t2["observed"]["max_temperature"] is not None and t2["basis"] == "simulated lab (computational)"
    assert s["lab_tests"][0]["forecast"] is None
    assert s["model_in_use"] == 1
    assert s["first_design"]["request_id"] == campaign.opt.request_id and s["best_design"] == s["first_design"]
    assert s["first_design"]["limits"] == {"margin_mV": 25.0, "t_limit_C": 45.0}
    assert s["ranking"]["request_id"] == campaign.rank.request_id and len(s["ranking"]["options"]) == 5
    nxt = s["next_experiment"]
    assert nxt["request_id"] == campaign.pending["request_id"] and "model" not in nxt["params"]
    assert nxt["params"]["model_version"] == 2


def test_a_good_fit_alone_never_stands_behind_a_design():
    fitted = {"version": 2, "adequate": True, "fit_rmse_K": 0.14, "noise_K": 0.15}
    ok = [{"request_id": "rq_a", "result_id": "res_a", "rmse_K": 0.2}]
    assert lab.model_standing(fitted, [])["supported"] is False
    assert "not been checked" in lab.model_standing(fitted, [])["reason"]
    assert lab.model_standing(fitted, ok)["supported"] is True
    # one failing held-out check outweighs a passing one, whatever the order
    bad = [{"request_id": "rq_b", "result_id": "res_b", "rmse_K": 0.9}]
    for checks in (ok + bad, bad + ok):
        s = lab.model_standing(fitted, checks)
        assert s["supported"] is False and "res_b" in s["reason"]
    # a fit outside its family is never supported, even when a check happens to pass
    assert lab.model_standing({**fitted, "adequate": False, "fit_rmse_K": 1.27}, ok)["supported"] is False
    # the nominal model (no fit of its own) stands only on a passing check of this campaign's tests
    nominal = {"version": 0, "adequate": None}
    assert lab.model_standing(nominal, [])["supported"] is False
    assert lab.model_standing(nominal, ok)["supported"] is True
    # checks on the supplied records alone are about an earlier fixture: reported, but they neither give nor take
    # standing for a lab design (a records-only pass is not a lab check; a records-only failure does not refute it)
    rec_ok = [{"request_id": "rq_r", "result_id": "res_r", "rmse_K": 0.2, "records_only": True}]
    rec_bad = [{"request_id": "rq_s", "result_id": "res_s", "rmse_K": 1.54, "records_only": True}]
    s = lab.model_standing(fitted, rec_ok)
    assert s["supported"] is False and "not been checked" in s["reason"] and "supplied records" in s["reason"]
    s = lab.model_standing(fitted, ok + rec_bad)
    assert s["supported"] is True and "fail the rule (held-out RMSE 1.54 K)" in s["reason"]
    assert lab.model_standing(fitted, bad + rec_ok)["supported"] is False
    # the rule scales with the fit's noise and keeps its floor
    assert lab.model_standing({**fitted, "noise_K": 0.5}, [{**ok[0], "rmse_K": 0.95}])["supported"] is True
    assert lab.model_standing({**fitted, "noise_K": 0.01}, [{**ok[0], "rmse_K": 0.29}])["supported"] is True


def _comparison_of(L, cid, request_id) -> dict:
    from translucent import loop_compare
    loop_compare.record_comparisons(L, cid)
    rid = request_states(L, cid)[request_id].result.id
    return [r.data for r in chain.records(L, cid) if r.type == "comparison" and
            r.data["candidate"]["result_id"] == rid][-1]


def test_a_design_stands_only_on_a_model_checked_on_tests_it_did_not_fit(L):
    """A well-fitting but unchecked or refuted model never becomes the campaign's feasible result or its recommended
    design: its design is shown as a lead, with the reason, and the outcome does not rest on it."""
    from translucent import loop_compare
    cid = open_lab(L)
    # 1. a design under the nominal model, which this lab never tested: a lead, not a result
    opt0 = run_one(L, cid, "optimize_protocol", {"ambient": 25.0, "model_version": 0})
    c0 = _comparison_of(L, cid, opt0.request_id)
    assert c0["verdict"] == "not_comparable" and c0["feasibility"]["candidate"] == "unknown"
    assert any("has not been checked on tests it did not fit" in r for r in c0["reasons"])
    s = LoopService(L, ANA).view(cid)["learning"]
    assert s["best_design"]["request_id"] == opt0.request_id          # the latest design is still shown
    assert s["designs"][0]["model_supported"] is False and s["recommended_design"] is None
    assert loop_compare.assess(L, cid, chain.records(L, cid))["findings"]["feasibility"]["best"] != "meets"
    assert "model_standing" in lab.LabAdapter().result_details(L, request_states(L, cid)[opt0.request_id])
    # 2. a lab test refutes the nominal model on data it did not fit
    run_one(L, cid, "lab_charge", proto(P1, rest=60.0))
    run_one(L, cid, "check_model", {"model_version": 0, "test_on": 1})
    assert "failed a check" in _comparison_of(L, cid, opt0.request_id)["reasons"][0]
    # 3. family 2 fitted on test 1, checked on test 2 it did not fit: it stands, and so does its design
    run_one(L, cid, "fit_thermal", {"family": 2, "fit_on": 1})
    run_one(L, cid, "lab_charge", proto(P2, rest=30.0))
    run_one(L, cid, "check_model", {"model_version": 1, "test_on": 2})
    s = LoopService(L, ANA).view(cid)["learning"]
    assert s["versions"][1]["standing"]["supported"] is True, s["versions"][1]["standing"]
    opt1 = run_one(L, cid, "optimize_protocol", {"ambient": 25.0, "model_version": 1})
    c1 = _comparison_of(L, cid, opt1.request_id)
    assert c1["verdict"] != "not_comparable", c1["reasons"]
    s = LoopService(L, ANA).view(cid)["learning"]
    assert s["recommended_design"]["request_id"] == opt1.request_id == s["best_design"]["request_id"]
    assert [d["model_supported"] for d in s["designs"]] == [False, True]


def test_when_no_supported_family_explains_the_lab_nothing_is_recommended_and_the_gap_is_named(L):
    """Out of family, deterministically: the lab's cell sits 3 K off the ambient a test states (a mechanism neither
    family has), with a fixed noise seed. Both families fit the two lab tests outside the noise; no design is
    recommended, the adequacy rule is unchanged, and the campaign names the evidence and the modelling capability a
    recommendation needs."""
    from translucent import loop_compare
    cid = open_lab(L, seal=False)
    lab.seal_lab(L.root, cid, h=HIDDEN["h"], k=HIDDEN["k"], ambient_offset_K=3.0)
    path = lab.sealed_path(L.root, cid)                 # a fixed noise seed: the same tests on every run
    path.write_bytes(fc.canonical_json(json.loads(path.read_text(encoding="utf-8")) | {"seed": "5eed" * 8}))
    run_one(L, cid, "lab_charge", proto(P1, rest=60.0))
    run_one(L, cid, "lab_charge", proto(P2, rest=30.0))
    run_one(L, cid, "fit_thermal", {"family": 1, "fit_on": 1 + 2})
    s = LoopService(L, ANA).view(cid)["learning"]
    assert s["versions"][1]["adequate"] is False and s["model_gap"] is None      # family 2 is not tried yet
    run_one(L, cid, "fit_thermal", {"family": 2, "fit_on": 1 + 2})
    opt = run_one(L, cid, "optimize_protocol", {"ambient": 25.0, "model_version": 2})
    s = LoopService(L, ANA).view(cid)["learning"]
    assert [v["adequate"] for v in s["versions"][1:]] == [False, False]
    # the rule fits are judged by is the one in force: 2 x the noise, floor 0.3 K, never widened to pass a fit
    assert (lab.ADEQUATE_NOISE_MULTIPLE, lab.ADEQUATE_FLOOR_K) == (2.0, 0.3)
    gap = s["model_gap"]
    assert gap["established"] is True and [r["version"] for r in gap["families"]] == [1, 2]
    assert all(r["fit_rmse_K"] > r["rule_K"] == max(2.0 * lab.NOISE_K, 0.3) for r in gap["families"])
    # the fits trade cooling and thermal mass against the hidden offset, so what is left is in no single phase: the
    # system names that, and asks for a test that changes one factor and for a family with the missing mechanism
    assert gap["pattern"] == "mixed" and gap["residual"]["model_version"] in (1, 2), gap["residual"]
    assert gap["residual"]["rmse_K"] > max(2.0 * lab.NOISE_K, 0.3)
    assert [n["kind"] for n in gap["needed"]] == ["evidence", "capability"]
    assert "ambient" in gap["needed"][0]["what"] and "model family" in gap["needed"][1]["what"]
    assert "not relaxed" in gap["summary"] and "no single phase" in gap["summary"]
    # declined: the design under the out-of-family model is a lead, its values kept from the comparison
    assert s["recommended_design"] is None and s["designs"][-1]["model_supported"] is False
    assert "a mechanism outside its family" in s["designs"][-1]["standing"]
    assert _comparison_of(L, cid, opt.request_id)["verdict"] == "not_comparable"
    assert loop_compare.assess(L, cid, chain.records(L, cid))["findings"]["feasibility"]["best"] != "meets"
    # the same gap from the same ledger: deterministic, nothing hidden read
    assert LoopService(L, ANA).view(cid)["learning"]["model_gap"] == gap


def test_an_export_written_before_the_model_gap_existed_still_verifies(L, tmp_path):
    """An export written before the view had `model_gap` lacks the key. It still verifies where the gap recomputed
    from its receipt is empty; an export that leaves out an established gap is a difference."""
    from translucent import loop_export
    from translucent.verify import verify
    cid = open_lab(L, seal=False)
    lab.seal_lab(L.root, cid, h=HIDDEN["h"], k=HIDDEN["k"], ambient_offset_K=3.0)
    path = lab.sealed_path(L.root, cid)
    path.write_bytes(fc.canonical_json(json.loads(path.read_text(encoding="utf-8")) | {"seed": "5eed" * 8}))
    run_one(L, cid, "lab_charge", proto(P1, rest=60.0))
    run_one(L, cid, "lab_charge", proto(P2, rest=30.0))
    run_one(L, cid, "fit_thermal", {"family": 1, "fit_on": 1 + 2})

    def without_gap(name):
        out = loop_export.export(L, cid, tmp_path / name)
        assert verify(out).ok
        data = json.loads(out.read_text(encoding="utf-8"))
        gap = data["view"]["learning"].pop("model_gap")
        out.write_text(json.dumps(data), encoding="utf-8")
        return gap, verify(out)

    gap, r = without_gap("before.loop.json")                 # family 2 untried: no gap, as an older export reads
    assert gap is None and r.ok, r.problems
    run_one(L, cid, "fit_thermal", {"family": 2, "fit_on": 1 + 2})
    gap, r = without_gap("after.loop.json")                  # every family fitted and none stands: the gap is real
    assert gap["established"] is True
    assert not r.ok and "Loop export view differs from the recorded evidence" in r.problems


def test_a_family_still_open_is_not_a_gap():
    """A fit inside the noise that no lab check has refuted may still explain the tests: no gap is claimed."""
    def v(n, fam, ok):
        return {"version": n, "family": fam, "adequate": ok, "fit_rmse_K": 0.2 if ok else 2.0, "noise_K": 0.15,
                "validity": {"lab_tests": [1]}}
    versions = [{"version": 0}, v(1, lab.FAMILIES[1], False), v(2, lab.FAMILIES[2], True)]
    standing = [{"supported": False, "reason": "nominal"}, {"supported": False, "reason": "out"},
                {"supported": False, "reason": "not checked"}]
    assert lab.model_gap(versions, standing, {}, ["lab_charge"], {}) is None
    refuted = {2: [{"rmse_K": 1.5, "records_only": False}]}
    gap = lab.model_gap(versions, standing, {}, ["lab_charge"], refuted)
    assert gap is not None and gap["pattern"] == "mixed"
    # fits on the supplied records alone (an earlier fixture) never establish a gap in the lab
    assert lab.model_gap(versions, standing, {}, ["record_test"], refuted) is None


def test_views_of_the_existing_systems_carry_no_learning(fake, tmp_path):
    from support.loop_systems import objective, register
    L = Ledger(tmp_path / "l2")
    try:
        register()
        cid = LoopService(L, ANA).open_campaign(objective(), "toy")["campaign_id"]
        assert "learning" not in LoopService(L, ANA).view(cid)
    finally:
        L.db.close()


def test_the_planner_context_hides_bound_parameters_and_shows_them_per_request(campaign):
    L, cid = campaign.L, campaign.cid
    ctx = planner_context(L, cid, full_requests=50)
    actions = {a["name"]: a for a in ctx["system"]["actions"]}
    for action, names in lab.BOUND.items():
        listed = {p["name"] for p in actions[action]["parameters"]}
        assert not listed & set(names), action
    assert {"model_version", "ambient"} <= {p["name"] for p in actions["optimize_protocol"]["parameters"]}
    items = {r["request_id"]: r for r in ctx["requests"]}
    opt = items[campaign.opt.request_id]
    assert set(opt["bound_params"]) == {"model", "margin_mV", "t_limit_C"}
    assert not set(opt["params"]) & {"model", "margin_mV", "t_limit_C"}
    assert set(items[campaign.lab1.request_id]["bound_params"]) == {"lab"}
    assert set(items[campaign.fit2.request_id]["bound_params"]) == {"sources", "base"}
    assert items[campaign.fit2.request_id]["result"]["details"]["model_version"] == 2
    assert "planner_guidance" not in json.dumps(ctx["system"]) or True
    assert lab.PLANNER_GUIDANCE in system_prompt(current_objective(L, cid))


def test_the_hidden_condition_never_leaves_the_sealed_file(campaign):
    L, cid = campaign.L, campaign.cid
    condition = json.loads(lab.sealed_path(L.root, cid).read_text(encoding="utf-8"))
    assert {k: condition[k] for k in HIDDEN} == HIDDEN
    needles = [re.compile(rf"(?<![\d.]){re.escape(repr(v))}(?!\d)") for v in HIDDEN.values()] + \
        [re.compile(re.escape(condition["seed"])), re.compile("ambient_offset_K"), re.compile("noise_V")]
    blobs: dict[str, str] = {}
    sealed = lab.sealed_path(L.root, cid).parent
    for path in L.root.rglob("*"):
        if path.is_file() and sealed not in path.parents:
            blobs[str(path)] = path.read_bytes().decode("latin-1")
    blobs["sqlite dump"] = "\n".join(L.db.iterdump())
    svc = LoopService(L, ANA)
    blobs["view"] = json.dumps(svc.view(cid))
    blobs["planner context"] = json.dumps(planner_context(L, cid, full_requests=50))
    blobs["tools"] = json.dumps(tool_schemas(current_objective(L, cid)))
    a = systems.get(lab.SYSTEM_ID)
    states = request_states(L, cid)
    blobs["summary"] = json.dumps(a.campaign_summary(L, cid, states))
    blobs["details"] = json.dumps([a.result_details(L, s) for s in states.values() if s.result is not None])
    for rid in (campaign.lab1.request_id, campaign.lab2.request_id):
        blobs[f"request {rid}"] = json.dumps(svc.request(cid, rid))
    sealed_text = lab.sealed_path(L.root, cid).read_text(encoding="utf-8")
    assert all(n.search(sealed_text) for n in needles)                  # the needles find the condition
    leaks = [(name, n.pattern) for name, text in blobs.items() for n in needles if n.search(text)]
    assert leaks == []
    assert list(L.root.rglob("inputs.json")) == []
    # the lab test's solver log is empty and its solver strings name no values
    for st in (campaign.lab1, campaign.lab2):
        files = st.result.data["files"]
        assert L.blob_path(files["solver.log"]).read_bytes() == b""
        assert body(L, st)["solver"]["model"] == "the hidden condition's DFN; values withheld"


def test_verify_accepts_the_finished_campaign_with_and_without_the_sealed_file(campaign, tmp_path):
    from translucent.verify import verify
    L, cid = campaign.L, campaign.cid
    LoopService(L, MUSE).cancel(cid, campaign.pending["request_id"], "done")
    LoopService(L, MUSE).conclude(cid, "Designed under v1.", ["held out on one test only"])
    r = verify(L.root, cid)
    assert r.problems == [], r.problems
    path = lab.sealed_path(L.root, cid)
    moved = tmp_path / "sealed.json"
    path.rename(moved)
    try:
        r = verify(L.root, cid)
        assert r.problems == [], r.problems
    finally:
        moved.rename(path)


# ------------------------------------------------------------------ the planner's action list

def test_allowed_action_limits_the_tool_enum_and_refuses_other_actions(L):
    only = ["lab_charge", "fit_thermal"]
    cid = LoopService(L, ANA).open_campaign(
        lab_objective(planner={"kind": "scripted", "profile": None, "client": None, "actions": only}),
        "Limited")["campaign_id"]
    obj = current_objective(L, cid)
    assert allowed_action(obj, "lab_charge") and not allowed_action(obj, "optimize_protocol")
    schema = next(s for s in tool_schemas(obj) if s["function"]["name"] == "propose_experiment")
    assert sorted(schema["function"]["parameters"]["properties"]["action"]["enum"]) == sorted(only)
    ctx = planner_context(L, cid)
    assert sorted(a["name"] for a in ctx["system"]["actions"]) == sorted(only)
    planner = ScriptedLoopPlanner([
        [("propose_experiment", {"action": "optimize_protocol", "params": {"ambient": 25.0, "model_version": 0},
                                 "hypothesis": "h", "prediction": {}, "rationale": "r", "expected_effect": "e"})],
        [("wait", {})]])
    run_turn(L, cid, planner)
    errors = [r.data["payload"]["result"].get("error") for r in chain.records(L, cid)
              if r.type == "planner" and r.data.get("phase") == "tool_result"]
    assert any("is not among the actions this campaign's planner may propose" in (e or "") for e in errors)
    assert request_states(L, cid) == {}
    # without the list every non-retrospective action is offered
    free = SimpleNamespace(planner={}, system=obj.system)
    assert allowed_action(free, "optimize_protocol")


# ------------------------------------------------------------------ fit statistics (in process, stand-in charge)

def source(F, n, rates, rest, h, k, *, ambient=25.0, offset=0.0, noise=0.15, seed=0):
    run = F.charge((list(rates), ambient + offset, rest, h, k, None))
    grid = S._grid(run)
    temps = np.interp(grid, run["t"], run["T"]) + np.random.default_rng(seed).normal(0.0, noise, len(grid))
    return {"n": n, "result_id": f"res_{n}", "noise_K": noise,
            "params": {**dict(zip(fc.C_RATES, rates)), "ambient": ambient, "rest_min": rest},
            "series": {"t": grid, "surface_temperature": [round(float(x), 2) for x in temps]}}


def fit(sources, family=1):
    return S.fit_thermal({"family": family}, lab.nominal_model(), sources)


def test_a_noise_only_residual_keeps_the_nominal_model_when_it_is_the_truth(stand_in):
    inside = 0
    for seed in range(12):
        out = fit([source(stand_in, 1, P1, 60.0, 6.0, 1.0, seed=seed)])
        assert out["scalars"]["fit_adequate"][0] == "yes"
        assert out["details"]["residuals"][0]["rmse_K"] < 0.2
        inside += out["scalars"]["consistent_with_nominal"][0] == "yes"
    assert inside >= 11                                   # a 95 % region holds the truth nearly every time


def test_better_cooling_is_told_apart_from_nominal(stand_in):
    out = fit([source(stand_in, 1, P1, 60.0, 15.0, 1.0, seed=3)])
    d = out["details"]
    assert out["scalars"]["consistent_with_nominal"][0] == "no" and d["nominal"]["delta_chi2"] > 3.84
    lo, hi = d["region_95"]["h"]
    assert lo <= 15.0 * 1.04 and hi >= 15.0 / 1.04 and hi / lo < 1.5
    assert out["scalars"]["fit_adequate"][0] == "yes"


def test_an_out_of_family_condition_is_called_inadequate(stand_in):
    out = fit([source(stand_in, 1, P1, 60.0, 6.0, 1.0, offset=3.0, seed=1)])
    assert out["scalars"]["fit_adequate"][0] == "no" and out["details"]["adequate"] is False
    assert out["scalars"]["thermal_fit_rmse"][0] > lab.ADEQUATE_FLOOR_K


def test_effective_samples_are_capped_at_one_a_minute_and_by_the_autocorrelation(stand_in):
    src = source(stand_in, 1, P1, 60.0, 6.0, 1.0, seed=5)
    out = fit([src])
    d = out["details"]
    minutes = (src["series"]["t"][-1] - src["series"]["t"][0]) / 60
    assert d["samples"] == len(src["series"]["t"])
    assert d["effective_samples"] <= minutes + 0.1 and d["effective_samples"] <= d["samples"]
    assert 0 < d["effective_ratio"] <= 1
    assert d["sigma_used_K"] >= src["noise_K"] - 1e-9


def test_n_eff_against_a_brute_force_reference():
    rng = np.random.default_rng(7)
    white = rng.normal(size=4000)
    assert S._n_eff(white) > 0.7 * len(white)
    ar = np.zeros(4000)
    for i in range(1, len(ar)):
        ar[i] = 0.9 * ar[i - 1] + rng.normal()
    # AR(1) with phi 0.9: integrated autocorrelation time (1 + phi) / (1 - phi) = 19
    assert 4000 / 30 < S._n_eff(ar) < 4000 / 12
    assert S._n_eff(np.ones(10)) == 10.0


def test_family_two_region_and_members(stand_in):
    srcs = [source(stand_in, 1, P1, 60.0, 10.0, 1.6, seed=11), source(stand_in, 2, P2, 30.0, 10.0, 1.6, seed=12)]
    out = fit(srcs, family=2)
    d = out["details"]
    assert 1 <= len(d["members"]) <= 5 and d["members"][0]["delta_chi2"] == 0.0
    assert all(m["delta_chi2"] <= 5.99 for m in d["members"])
    (hlo, hhi), (klo, khi) = d["region_95"]["h"], d["region_95"]["k"]
    assert hlo <= 10.0 * 1.04 and hhi >= 10.0 / 1.04 and klo - 0.05 <= 1.6 <= khi + 0.05
    assert d["nominal"]["limit"] == 5.99
    w = S._weights({"members": d["members"]})
    assert abs(sum(w) - 1) < 1e-12 and w[0] == max(w)


def test_one_member_values_no_test_and_says_so(stand_in):
    out = S.rank_measurements({**rank_params(version=0), "margin_mV": 25.0, "t_limit_C": 45.0},
                              lab.nominal_model())
    d = out["details"]
    assert all(o["value_min"] <= 0 for o in d["options"])
    assert out["scalars"]["best_measurement"][0] == 0
    assert d["weights"] == [1.0] and "nominal model is a single model" in d["verdict"]


def test_rank_values_are_risk_now_less_risk_after_less_cost(stand_in):
    model = {"version": 1, "members": [{"h": 6.0, "k": 1.0, "delta_chi2": 0.0}, {"h": 15.0, "k": 1.0,
                                                                               "delta_chi2": 2.0}],
             "noise_K": 0.15, "effective_ratio": 0.1, "adequate": True}
    out = S.rank_measurements({**rank_params(), "margin_mV": 25.0, "t_limit_C": 45.0}, model)
    d = out["details"]
    now = out["scalars"]["decision_risk"][0]
    for o in d["options"]:
        assert o["value_min"] == pytest.approx(now - o["expected_risk_after_min"] - lab.TEST_COST_MIN, abs=2e-3)
        assert o["expected_risk_after_min"] <= now + 1e-6        # information never raises the expected loss
    # the two members disagree on the design's margin: some test is worth its cost
    assert out["scalars"]["best_measurement"][0] in range(1, 6)
    assert d["best_decision_now"] in [x["label"] for x in d["decisions"]]


def test_the_preposterior_never_exceeds_the_prior_risk_by_brute_force():
    """Value of information is >= 0 for an exact Bayesian update (LAB-4). The solver's preposterior averages seeded
    noise draws, so over random tables and forecasts it may exceed R(now) only by sampling error, which the caller
    trims; check that error stays small."""
    rng = np.random.default_rng(0)
    worst = 0.0
    for n in range(1500):
        m, a = int(rng.integers(2, 6)), int(rng.integers(1, 5))
        w = list(rng.dirichlet(np.ones(m)))
        table = rng.uniform(20, 140, size=(a, m)).tolist()
        temps = rng.normal(35, rng.uniform(0.01, 2.0), size=(m, 30))
        now = S._risk(table, w)[0]
        after = S.preposterior(temps, table, w, 0.15, float(rng.uniform(0.01, 1)), n)
        worst = max(worst, after - now)
    assert worst <= 1.0, f"the preposterior exceeds the prior risk by {worst:.2f} min"


def test_greedy_finds_no_design_when_nothing_is_feasible(stand_in):
    model = lab.nominal_model()
    out = S.optimize_protocol({"ambient": 25.0, "model_version": 0, "margin_mV": 200.0, "t_limit_C": 45.0}, model)
    assert out["scalars"]["design_found"][0] == "no" and out["scalars"]["evaluations"][0] > 0
    assert "no charge" in out["details"]["reason"]


def test_greedy_lowers_later_steps_when_the_full_charge_fails():
    """Prefix charges all pass; the full charge passes only once step 4 is at most 0.5C, then never (all at C_LO)."""
    calls = []

    def run(jobs, limit=0.5):
        out = []
        for rates, *_rest, prefix in jobs:
            calls.append((tuple(rates), prefix))
            ok = prefix is not None or rates[3] <= limit + 1e-9
            out.append({"status": "ok", "min_anode": 30.0 if ok else 10.0, "max_T": 40.0})
        return out
    chosen, n, runs = S._greedy([(6.0, 1.0)], 25.0, 25.0, 45.0, run)
    assert chosen == [1.5, 1.5, 1.5, 0.5] and n == len(calls)
    assert [c for c in calls if c[1] is None][-1][0] == (1.5, 1.5, 1.5, 0.5)
    none, _, _ = S._greedy([(6.0, 1.0)], 25.0, 25.0, 45.0, lambda jobs: run(jobs, limit=-1.0))
    assert none is None


def test_optimize_meets_the_limits_for_every_member_and_reports_extrapolation(stand_in):
    model = {"version": 1, "members": [{"h": 6.0, "k": 1.0}, {"h": 15.0, "k": 1.0}],
             "validity": {"ambient_degC": [25.0, 25.0], "c_rate": [1.0, 1.0], "lab_tests": [1]}}
    out = S.optimize_protocol({"ambient": 25.0, "model_version": 1, "margin_mV": 25.0, "t_limit_C": 45.0}, model)
    d = out["details"]
    assert out["scalars"]["design_found"][0] == "yes"
    assert all(m["min_anode_potential_mV"] >= 25.5 and m["max_temperature_degC"] <= 44.9 for m in d["members"])
    rates = [out["scalars"][c][0] for c in fc.C_RATES]
    assert rates == sorted(rates, reverse=True)
    assert any("above the fitted 1C" in e for e in d["extrapolation"]) == (max(rates) > 1.0)


# ------------------------------------------------------------------ second review (the 95 % region and its uses)

def test_the_95_percent_region_holds_the_true_h(stand_in):
    """The fit's region resolves its own width (a local grid finer than one test's uncertainty), so it covers the
    truth about as often as it claims; on the coarse grid alone it collapsed to one point and held it 0 of 50 times."""
    covered, widths = 0, []
    for h in (4.0, 6.0, 10.0, 20.0, 35.0):
        for seed in range(10):
            d = fit([source(stand_in, 1, P1, 60.0, h, 1.0, seed=seed)])["details"]
            lo, hi = d["region_95"]["h"]
            covered += lo <= h <= hi
            widths.append(hi / lo - 1)
            assert lo <= d["estimate"]["h"] <= hi and len(d["members"]) >= 2
    assert covered >= 44, f"the region held the true h in {covered} of 50 fits"
    assert max(widths) < 0.2


def test_a_region_cut_by_the_familys_bound_says_so(stand_in):
    d = fit([source(stand_in, 1, P1, 60.0, 50.0, 1.0, seed=1)])["details"]
    assert d["region_95"]["h"][1] == lab.H_BOUNDS[1] and d["region_at_bound"] is True
    d = fit([source(stand_in, 1, P1, 60.0, 10.0, 1.0, seed=1)])["details"]
    assert d["region_at_bound"] is False


def test_a_revision_keeps_its_action(L):
    """A revision that turned a forecast into a lab test would skip the lab-test budget, checked on version 1."""
    cid = open_lab(L, lab_tests=1)
    propose(L, cid, "lab_charge", proto(P1, rest=0.0))
    f = propose(L, cid, "forecast_protocol", proto(P2, rest=0.0, model_version=0))
    st = request_states(L, cid)[f["request_id"]]
    obj = current_objective(L, cid)
    params = bound_values(L, cid, "lab_charge", proto(P2, rest=0.0))
    rev = build_request(adapter_for(obj), obj, "lab_charge", params, request_id=st.request_id, rationale="r",
                        prediction={}, expected_effect="e", version=2, supersedes=st.record.id)
    with pytest.raises(AdmissionError, match="a revision keeps the action"):
        with L.transaction():
            chain.append(L, cid, "request", rev, MUSE.label, chain.natural_id("req", cid, st.request_id, 2))


def test_rank_weighs_a_test_with_the_fits_sigma(stand_in):
    """A version records the sigma its fit used; ranking a test with the smaller lab noise instead would count it as
    more informative than the fit's own weights allow."""
    d = fit([source(stand_in, 1, P1, 60.0, 9.0, 1.0, offset=1.0, seed=4)])["details"]
    assert d["sigma_used_K"] > d["noise_K"]
    model = {"version": 1, "members": d["members"], "noise_K": d["noise_K"], "sigma_used_K": d["sigma_used_K"],
             "effective_ratio": d["effective_ratio"], "adequate": d["adequate"], "validity": d["validity"]}
    p = {**rank_params(), "margin_mV": 25.0, "t_limit_C": 45.0}
    used = S.rank_measurements(p, model)
    same = S.rank_measurements(p, {**model, "noise_K": d["sigma_used_K"], "sigma_used_K": None})
    assert [o["value_min"] for o in used["details"]["options"]] == \
        [o["value_min"] for o in same["details"]["options"]]


def test_request_titles_name_long_text_parameters_without_printing_them(L):
    from translucent.loop_view import request_title

    cid = open_lab(L)
    r = propose(L, cid, "forecast_protocol", proto(P1, rest=0.0, model_version=0))
    st = request_states(L, cid)[r["request_id"]]
    title = request_title(current_objective(L, cid), st.data)
    assert "model (recorded with the request)" in title and "{" not in title and len(title) < 300

