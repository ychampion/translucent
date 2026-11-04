"""The fast-charge lab evaluation harness (evals/fastcharge_lab.py): planning, objectives, the two numerical
policies, the assessment's scoring and forecast errors, the per-arm summary and criteria, the report rebuilt from
saved metrics, and the refusal of paid arms without --live and --cap-usd.

No test runs PyBaMM or contacts a provider: the policies run against a scripted executor, and the assessment against
stand-ins for the hidden-condition charge and the forecast.
"""
import json
from types import SimpleNamespace

import pytest

from evals import fastcharge_lab as fl
from tests.test_eval_fastcharge import FIRST, _propose, no_provider, scripted, solver_calls  # noqa: F401 - fixtures
from translucent import chain, exec_child, executors
from translucent.ledger import Ledger
from translucent.loop import campaign_ids
from translucent.loop_planner import allowed_action
from translucent.systems import lgm50_fastcharge as fc
from translucent.systems import lgm50_lab as lab
from translucent.systems.base import ExecutionOutput

DESIGN = [1.5, 1.3, 1.0, 0.75]


# ------------------------------------------------------------------ planning and names

def test_planning_runs_the_fixed_model_once_per_repeat_for_every_case():
    jobs = fl.planned(["N", "C"], ["calibrate_once", "fixed_model_meta"], [1, 2])
    assert [fl.run_name(*j) for j in jobs] == ["N-calibrate_once-1", "C-calibrate_once-1", "N-calibrate_once-2",
                                               "C-calibrate_once-2", "all-fixed_model_meta-1",
                                               "all-fixed_model_meta-2"]
    for j in jobs:
        assert fl.parse_name(fl.run_name(*j)) == j
    for bad in ("all-calibrate_once-1", "N-fixed_model_meta-1", "X-adaptive_numeric-1", "N-bo-1", "N-calibrate_once-x"):
        assert fl.parse_name(bad) is None


def test_plan_lists_runs_and_runs_nothing(tmp_path, capsys):
    assert fl.main(["plan", "--out", str(tmp_path / "out"), "--arms", "adaptive_meta", "calibrate_once",
                    "--repeats", "1"]) == 0
    text = capsys.readouterr().out
    assert "N-adaptive_meta-1: to run; paid" in text and "O-calibrate_once-1: to run; no model calls" in text
    assert "worst-case model spend $12.50" in text
    assert not (tmp_path / "out").exists()


# ------------------------------------------------------------------ objectives

def test_lab_objectives_carry_the_budgets_limits_and_action_lists():
    for arm in fl.LAB_ARMS:
        o = fl.lab_objective(arm)
        assert o["system"]["id"] == "pybamm-fastcharge-lab" and o["kind"] == "fastest_feasible"
        assert o["budget"]["lab_tests"] == 4 and o["budget"]["experiments"] == 12
        assert o["budget"]["model_usd"] == (2.5 if arm in fl.MODEL_ARMS else 0.0)
        assert {(c["quantity"], c["limit"]) for c in o["constraints"]} == {("min_anode_potential", 25.0),
                                                                           ("max_temperature", 45.0)}
        assert o["auto_approve"][0]["max_count"] == 12 and o["auto_approve"][0]["executor"] == "computational"
        assert "simulated lab" in o["statement"] and "25 degC" in o["statement"]
    plain = SimpleNamespace(planner=fl.lab_objective("no_diagnostics_meta")["planner"])
    assert [a for a in fl.ALL_ACTIONS if allowed_action(plain, a)] == ["lab_charge", "forecast_protocol",
                                                                        "optimize_protocol"]
    full = SimpleNamespace(planner=fl.lab_objective("adaptive_meta")["planner"])
    assert all(allowed_action(full, a) for a in fl.ALL_ACTIONS) and not allowed_action(full, "bench_charge")
    assert fl.lab_objective("adaptive_meta")["planner"]["kind"] == "muse"
    assert fl.lab_objective("calibrate_once")["planner"]["kind"] == "external"


def test_the_fixed_model_arm_is_the_m2_loop_with_the_same_limits():
    o = fl.fixed_objective()
    assert o["system"]["id"] == "pybamm-fastcharge" and o["kind"] == "fastest_feasible"
    assert o["planner"]["actions"] == ["simulate_protocol"] and o["planner"]["kind"] == "muse"
    assert o["budget"]["experiments"] == 8 and o["budget"]["model_usd"] == 2.5
    assert {(c["quantity"], c["limit"]) for c in o["constraints"]} == {("min_anode_potential", 25.0),
                                                                       ("max_temperature", 45.0)}


# ------------------------------------------------------------------ the numerical policies

class Script:
    """A stand-in for LedgerExecutor: canned results per action, every call recorded."""

    def __init__(self, *, f2_adequate=True, f1_adequate=True, rank=(3, 2.0), confirm_T=44.0, checks=None,
                 found=True):
        self.calls, self.f2, self.f1, self.rank = [], f2_adequate, f1_adequate, rank
        self.confirm_T, self.checks, self.found = confirm_T, list(checks or [0.2, 0.1]), found

    def __call__(self, action, params, why):
        self.calls.append((action, dict(params)))
        if action == "optimize_protocol":
            if not self.found:
                return {"ok": True, "scalars": {"status": "ok", "design_found": "no"}}
            return {"ok": True, "scalars": {"status": "ok", "design_found": "yes", **dict(zip(fc.C_RATES, DESIGN))}}
        if action == "lab_charge":
            hot = params["rest_min"] == 0.0
            return {"ok": True, "scalars": {"status": "ok", "max_temperature": self.confirm_T if hot else 40.0}}
        if action == "fit_thermal":
            adequate = self.f2 if params["family"] == 2 else self.f1
            return {"ok": True, "scalars": {"status": "ok", "fit_adequate": "yes" if adequate else "no",
                                            "thermal_fit_rmse": 0.2 if params["family"] == 2 else 0.5}}
        if action == "rank_measurements":
            return {"ok": True, "scalars": {"status": "ok", "best_measurement": self.rank[0],
                                            "best_value": self.rank[1]}}
        if action == "check_model":
            return {"ok": True, "scalars": {"status": "ok", "heldout_rmse": self.checks.pop(0)}}
        raise AssertionError(action)


def _actions(s):
    return [a for a, _ in s.calls]


def test_calibrate_once_optimizes_tests_fits_family_1_and_optimizes_again():
    s = Script()
    p = fl.calibrate_once(fl.Policy(s))
    assert _actions(s) == ["optimize_protocol", "lab_charge", "fit_thermal", "optimize_protocol"]
    assert s.calls[0][1] == {"ambient": 25.0, "model_version": 0}
    assert s.calls[1][1] == {**dict(zip(fc.C_RATES, DESIGN)), "ambient": 25.0, "rest_min": 60.0}
    assert s.calls[2][1] == {"family": 1, "fit_on": 1}
    assert s.calls[3][1]["model_version"] == 1 and p.stop == "policy finished"


def test_calibrate_once_stops_without_a_nominal_design():
    s = Script(found=False)
    assert fl.calibrate_once(fl.Policy(s)).stop == "no design under the nominal model" and len(s.calls) == 1


def test_adaptive_policy_with_an_adequate_family_2_fit():
    s = Script(rank=(3, 2.0), confirm_T=44.0)
    p = fl.adaptive_numeric(fl.Policy(s))
    assert _actions(s) == ["optimize_protocol", "lab_charge", "fit_thermal", "rank_measurements", "lab_charge",
                           "check_model", "fit_thermal", "optimize_protocol", "lab_charge"]
    rank = s.calls[3][1]
    assert rank["model_version"] == 1 and [rank[c] for c in fc.C_RATES] == DESIGN
    assert all(rank[f"test_{c}"] == 1.0 for c in fc.C_RATES) and rank["test_rest_min"] == 60.0
    assert s.calls[4][1] == {**{c: 1.0 for c in fc.C_RATES}, "ambient": 25.0, "rest_min": 60.0}   # option 3
    assert s.calls[5][1] == {"model_version": 1, "test_on": 2}        # the version fitted on test 1, on test 2
    assert s.calls[6][1] == {"family": 2, "fit_on": 3}                # refit on both tests
    assert s.calls[7][1]["model_version"] == 2
    assert s.calls[8][1]["rest_min"] == 0.0                           # the temperature confirmation
    assert p.budget.used <= 12 and p.budget.tests <= 4 and p.stop == "policy finished"


def test_adaptive_policy_falls_back_to_family_1_and_keeps_the_better_checked_version():
    s = Script(f2_adequate=False, f1_adequate=True, rank=(1, 0.5), checks=[0.9, 0.3], confirm_T=46.0)
    p = fl.adaptive_numeric(fl.Policy(s))
    acts = _actions(s)
    assert acts[:4] == ["optimize_protocol", "lab_charge", "fit_thermal", "fit_thermal"]
    assert s.calls[3][1] == {"family": 1, "fit_on": 1}
    assert s.calls[4][1]["model_version"] == 1                       # ranked under the lower-RMSE fit (family 2)
    assert s.calls[5][1]["rest_min"] == 60.0 and [s.calls[5][1][c] for c in fc.C_RATES] == DESIGN   # option 1
    assert [c[1]["model_version"] for c in s.calls if c[0] == "check_model"] == [1, 2]
    refit = [c[1] for c in s.calls if c[0] == "fit_thermal"][2]
    assert refit == {"family": 1, "fit_on": 3}                       # family 1 checked better (0.3 < 0.9)
    # 12 requests: the confirmation ran hot, but no budget is left for a refit and a design
    assert acts[-1] == "lab_charge" and p.budget.used == 11 and "skipped" not in str(p.trace[-1])
    assert p.budget.used <= 12 and p.budget.tests <= 4


def test_adaptive_policy_redesigns_after_a_hot_confirmation_when_budget_allows():
    s = Script(rank=(3, 2.0), confirm_T=45.3)
    p = fl.adaptive_numeric(fl.Policy(s))
    assert _actions(s)[-3:] == ["lab_charge", "fit_thermal", "optimize_protocol"]
    assert s.calls[-2][1] == {"family": 2, "fit_on": 7}               # tests 1, 2 and the confirmation
    assert s.calls[-1][1]["model_version"] == 3 and p.budget.used == 11


def test_adaptive_policy_runs_no_second_test_when_none_is_worth_its_cost():
    s = Script(rank=(0, -0.4))
    fl.adaptive_numeric(fl.Policy(s))
    assert _actions(s) == ["optimize_protocol", "lab_charge", "fit_thermal", "rank_measurements",
                           "optimize_protocol", "lab_charge"]
    assert s.calls[4][1]["model_version"] == 1


def test_the_policy_never_passes_its_budget():
    s = Script()
    p = fl.Policy(s, fl.Budget(requests=3, lab_tests=1))
    fl.adaptive_numeric(p)
    assert len(s.calls) == 3 and p.trace[-1]["skipped"] == "budget"


# ------------------------------------------------------------------ the assessment

def _truth(anode=30.0, T=44.0, status="ok", t80=1800.0):
    t = [float(i * 10) for i in range(181)]
    return {"status": status, "t80_s": t80, "min_anode": anode, "max_T": T, "t": t,
            "T": [25.0 + 19.0 * x / 1800 for x in t], "phi": [100.0] * len(t)}


def test_feasibility_uses_the_objectives_limits_exactly():
    assert fl.score(_truth(anode=25.0, T=45.0))["feasible"] is True
    s = fl.score(_truth(anode=24.9, T=45.2))
    assert s["feasible"] is False
    assert s["violations"] == [{"limit": "min_anode_potential", "by": 0.1, "unit": "mV"},
                               {"limit": "max_temperature", "by": 0.2, "unit": "K"}]
    assert fl.score(_truth(status="not_reached", t80=None))["feasible"] is False


def test_assessment_runs_the_hidden_condition_and_scores_the_forecast():
    seen = {}

    def charge(job):
        seen["job"] = job
        return _truth(anode=20.0, T=40.0)

    def forecast(params, model):
        seen["forecast"] = (params, model)
        grid = [float(i * 10) for i in range(181)]
        return {"status": "ok", "scalars": {"max_temperature": (45.0, "degC"), "min_anode_potential": (26.0, "mV")},
                "series": {"t": grid, "channels": {"cell_temperature": ("degC", [26.0 + 19.0 * x / 1800
                                                                                 for x in grid])}},
                "details": {"members": [{"h": 6.0, "k": 1.0, "max_temperature_degC": 44.0,
                                         "min_anode_potential_mV": 27.0}]}}
    model = {"version": 1, "members": [{"h": 6.0, "k": 1.0}]}
    a = fl.assess_design({"c_rates": DESIGN, "model_version": 1}, model, fl.CASES["O"], charge_fn=charge,
                         forecast_fn=forecast)
    assert seen["job"] == (DESIGN, 28.0, 0.0, 6.0, 1.0, None)          # setpoint + the chamber offset
    assert seen["forecast"][0] == {**dict(zip(fc.C_RATES, DESIGN)), "ambient": 25.0, "rest_min": 0.0}
    assert a["feasible"] is False and a["violations"][0]["limit"] == "min_anode_potential"
    f = a["forecast"]
    assert f["temperature_rmse_K"] == pytest.approx(1.0)
    assert f["max_temperature_error_K"] == 5.0 and f["max_temperature_error_estimate_K"] == 4.0
    assert f["min_anode_error_mV"] == 6.0 and "simulation-only" in f["anode_note"]


def test_a_campaign_without_a_design_is_scored_as_no_design():
    a = fl.assess_design(None, None, fl.CASES["C"], charge_fn=lambda j: pytest.fail("no run without a design"))
    assert a["note"] == "no design" and a["feasible"] is False and a["true_t80_min"] is None


# ------------------------------------------------------------------ summary, criteria, report

def _arm(runs, feasible, t80):
    return {"runs": runs, "feasible": feasible, "median_true_t80_feasible_min": t80}


def test_meta_adds_only_when_feasible_more_often_or_a_minute_faster():
    assert fl.compare(_arm(3, 3, 40.0), _arm(3, 2, 35.0))["verdict"] == "adds"
    assert fl.compare(_arm(3, 3, 39.0), _arm(3, 3, 40.0))["verdict"] == "adds"
    assert fl.compare(_arm(3, 3, 39.5), _arm(3, 3, 40.0))["verdict"] == "equal"
    assert fl.compare(_arm(3, 3, 41.0), _arm(3, 3, 40.0))["verdict"] == "worse"
    assert fl.compare(_arm(3, 1, 30.0), _arm(3, 2, 40.0))["verdict"] == "worse"
    assert fl.compare(_arm(3, 0, None), _arm(3, 0, None))["verdict"] == "equal"
    assert fl.compare(None, _arm(3, 0, None))["decided"] is False


def _metrics(run, case, arm, n, feasible, t80, **extra):
    return {"run": run, "case": case, "arm": arm, "n": n, "stop": "policy finished", "campaign_wall_min": 12.0,
            "problems": [], "requests": 9, "runs_by_action": {}, "failed_runs": 0, "model_usd": 0.0,
            "interventions": {"total": 0}, "lab_tests": 3, "model_versions": 2,
            "final_protocol": {"c_rates": DESIGN, "model_version": 2},
            "per_case": {case: {"true_t80_min": t80, "true_min_anode_mV": 27.0, "true_max_temperature_degC": 43.0,
                                "feasible": feasible, "violations": [], "forecast": {"temperature_rmse_K": 0.3},
                                **extra}}}


def test_report_rebuilds_from_saved_metrics_without_the_ledgers(tmp_path):
    out = tmp_path / "out"
    rows = [("C", "adaptive_numeric", 1, True, 38.0), ("C", "adaptive_numeric", 2, True, 39.0),
            ("C", "adaptive_numeric", 3, False, 35.0), ("C", "calibrate_once", 1, False, 36.0)]
    for case, arm, n, feas, t80 in rows:
        d = out / fl.run_name(case, arm, n)
        d.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps({"stop": "policy finished"}), encoding="utf-8")
        (d / "metrics.json").write_text(json.dumps(_metrics(d.name, case, arm, n, feas, t80)), encoding="utf-8")
    fixed = out / "all-fixed_model_meta-1"
    fixed.mkdir()
    (fixed / "run.json").write_text("{}", encoding="utf-8")
    m = _metrics(fixed.name, "C", "fixed_model_meta", 1, False, 33.0)
    m["per_case"]["N"] = dict(m["per_case"]["C"], feasible=True)
    (fixed / "metrics.json").write_text(json.dumps(m), encoding="utf-8")
    md = tmp_path / "report.md"
    data = fl.report(out, md)
    arms = data["summary"]["C"]["arms"]
    assert arms["adaptive_numeric"]["feasible"] == 2 and arms["adaptive_numeric"]["median_true_t80_feasible_min"] == 38.5
    assert arms["fixed_model_meta"]["feasible"] == 0 and data["summary"]["N"]["arms"]["fixed_model_meta"]["feasible"] == 1
    cr = data["summary"]["C"]["criteria"]
    assert cr["numeric_vs_calibrate_once"]["verdict"] == "adds" and cr["numeric_vs_fixed_model"]["verdict"] == "adds"
    assert cr["meta_vs_numeric"]["decided"] is False and cr["changed_cooling"]["decided"] is False
    text = md.read_text(encoding="utf-8")
    assert "computational" in text and "## Case C: changed cooling" in text and "## Case N: nominal" in text
    assert json.loads((out / "results.json").read_text(encoding="utf-8"))["summary"] == data["summary"]


def test_case_n_and_o_criteria():
    n = {"calibrate_once": {**_arm(3, 3, 40.0), "gratuitous_changes": 2},
         "adaptive_numeric": {**_arm(3, 3, 40.0), "gratuitous_changes": 1}}
    cr = fl.criteria("N", n)["no_gratuitous_change"]
    assert cr["calibrate_once"]["met"] is False and cr["adaptive_numeric"]["met"] is True
    o = {"adaptive_numeric": {**_arm(3, 1, 40.0), "inadequate_fit_flagged": 3},
         "calibrate_once": {**_arm(1, 1, 40.0), "inadequate_fit_flagged": 1},
         "no_diagnostics_meta": {**_arm(3, 1, 40.0), "inadequate_fit_flagged": 0}}
    cr = fl.criteria("O", o)["inadequate_fit_flagged"]
    assert cr["adaptive_numeric"]["met"] is True and "no_diagnostics_meta" not in cr
    assert cr["calibrate_once"] == {"decided": False, "note": "fewer than 3 runs", "flagged": 1, "runs": 1}
    assert fl.design_change([1.5, 1.3, 1.0, 0.75], [1.5, 1.25, 1.0, 0.75]) == 0.05


def test_case_u_and_m_criteria():
    u = {"adaptive_numeric": {**_arm(3, 3, 36.8), "stopped_learning": 3, "stopped_and_feasible": 2,
                              "median_lab_tests_after_first_fit": 1},
         "calibrate_once": {**_arm(3, 3, 36.8), "stopped_learning": 3, "stopped_and_feasible": 1,
                            "median_lab_tests_after_first_fit": 0}}
    cr = fl.criteria("U", u)["stops_learning"]
    assert cr["adaptive_numeric"]["met"] is True and cr["calibrate_once"]["met"] is False
    m = {"adaptive_numeric": {**_arm(3, 2, 40.0), "final_version_adequate": 3, "inadequate_fit_flagged": 3},
         "calibrate_once": {**_arm(3, 0, None), "final_version_adequate": 0, "inadequate_fit_flagged": 3}}
    cr = fl.criteria("M", m)["thermal_mass_handled"]
    assert cr["adaptive_numeric"]["met"] is True and cr["calibrate_once"]["met"] is False


def test_the_cases_are_n_c_u_m_o_and_u_seals_its_own_noise(tmp_path):
    assert list(fl.CASES) == ["N", "C", "U", "M", "O"]
    assert fl.CASES["U"].noise_K == 1.0 and all(c.noise_K == 0.15 for n, c in fl.CASES.items() if n != "U")
    L, cid = fl.open_run(tmp_path / "ledger", fl.CASES["U"], "calibrate_once", "U-calibrate_once-1")
    try:
        sealed = json.loads(lab.sealed_path(L.root, cid).read_text(encoding="utf-8"))
    finally:
        L.db.close()
    assert (sealed["h"], sealed["k"], sealed["noise_K"], sealed["ambient_offset_K"]) == (7.0, 1.0, 1.0, 0.0)


# ------------------------------------------------------------------ paid arms refuse to start

@pytest.mark.parametrize("arm", fl.MODEL_ARMS)
def test_paid_arms_refuse_without_live_and_a_cap(tmp_path, arm):
    with pytest.raises(fl.HarnessError, match="--live"):
        fl.run_one(tmp_path / "out", "C", arm, 1)
    with pytest.raises(fl.HarnessError, match="--cap-usd"):
        fl.run_one(tmp_path / "out", "C", arm, 1, live=True)
    with pytest.raises(fl.HarnessError, match="--cap-usd"):
        fl.run_one(tmp_path / "out", "C", arm, 1, live=True, cap_usd=0.0)
    assert not (tmp_path / "out").exists()


def test_the_command_line_refuses_paid_arms_before_creating_anything(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        fl.main(["run", "--out", str(tmp_path / "out"), "--arms", "adaptive_meta", "--cases", "C"])
    assert e.value.code == 2 and "--live" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        fl.main(["run", "--out", str(tmp_path / "out"), "--arms", "fixed_model_meta", "--live"])
    assert not (tmp_path / "out").exists()


def test_a_spent_cap_stops_a_paid_run_before_it_starts(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(fl.GlobalCap, "spent", lambda self: 5.0)
    with pytest.raises(fl.HarnessError, match="has spent"):
        fl.run_one(out, "C", "adaptive_meta", 1, live=True, cap_usd=5.0)
    assert list(out.iterdir()) == []


# ------------------------------------------------------------------ the paid arms' wiring, with a scripted planner

PENDING = {"awaiting_approval", "queued", "running", "awaiting_result"}


def _stub_truth(job):
    rates, ambient, rest, h, k, prefix = job
    return _truth(anode=30.0 - (h - 6.0), T=44.0 - (h - 6.0) / 2)


def _stub_forecast(params, model):
    grid = [float(i * 10) for i in range(181)]
    return {"status": "ok", "scalars": {"max_temperature": (44.0, "degC"), "min_anode_potential": (30.0, "mV")},
            "series": {"t": grid, "channels": {"cell_temperature": ("degC", [25.0 + 19.0 * x / 1800 for x in grid])}},
            "details": {"members": []}}


def test_fixed_model_arm_runs_the_m2_loop_and_is_assessed_under_every_case(tmp_path, solver_calls,  # noqa: F811
                                                                            no_provider):  # noqa: F811
    def rule(ctx, messages):
        if any(r["state"] in PENDING for r in ctx["requests"]):
            return [("wait", {"reason": "pending"})]
        used = ctx["budget"]["experiments_used"]
        if used < 3:
            return [_propose([FIRST[0], (1.0, 1.0, 1.0, 0.55), FIRST[2]][used])]
        return [("conclude", {"text": "Three protocols simulated."})]
    out = tmp_path / "out"
    info = fl.run_one(out, "C", "fixed_model_meta", 1, live=True, cap_usd=1.0, client_factory=scripted(rule),
                      charge_fn=_stub_truth, forecast_fn=_stub_forecast, log=lambda *_: None)
    assert info["run"] == "all-fixed_model_meta-1" and info["stop"] == "concluded"
    assert [a for a, _ in solver_calls] == ["simulate_protocol"] * 3
    run = out / "all-fixed_model_meta-1"
    a = json.loads((run / "assessment.json").read_text(encoding="utf-8"))
    assert sorted(a["cases"]) == ["C", "M", "N", "O", "U"]
    # the stand-in's only protocol of the three that meets +25 mV: 1.0, 1.0, 1.0, 0.55 C (+26 mV, 34.6 degC)
    assert a["final_protocol"]["c_rates"] == [1.0, 1.0, 1.0, 0.55]
    assert a["cases"]["N"]["feasible"] is True and a["cases"]["C"]["feasible"] is False
    assert not (run / "revealed-condition.json").exists()           # no lab, nothing sealed
    m = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
    assert m["lab_tests"] == 0 and m["runs_by_action"] == {"simulate_protocol": 3} and set(m["per_case"]) == set("NCUMO")


def lab_standin(calls: list):
    """The lab system's child replaced by fixed answers in its result schema (lab_charge and optimize_protocol)."""
    def run_child(adapter, action, params, workdir, wall_s):
        calls.append((action, dict(params)))
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "inputs.json").unlink(missing_ok=True)
        if action == "optimize_protocol":
            sc = {"status": ("ok", ""), "design_found": ("yes", ""), "evaluations": (12, "1"),
                  "model_version": (int(params["model_version"]), "1"), "charge_time_80": (40.0, "min"),
                  "min_anode_potential": (26.0, "mV"), "max_temperature": (44.0, "degC"),
                  **{c: (r, "1/h") for c, r in zip(fc.C_RATES, DESIGN)}}
            series = None
        elif action == "lab_charge":
            sc = {"status": ("ok", ""), "max_temperature": (40.0, "degC"), "temperature_end": (26.0, "degC"),
                  "noise_temperature": (0.15, "delta_degC"), "charge_time_80": (40.0, "min")}
            t = [i * 10.0 for i in range(50)]
            series = {"time_unit": "s", "t": t, "channels": {
                "surface_temperature": {"unit": "degC", "values": [25.0 + 15.0 * i / 49 for i in range(50)]},
                "voltage": {"unit": "V", "values": [3.6] * 50}, "current": {"unit": "A", "values": [5.0] * 50}}}
        else:
            raise AssertionError(f"{action} is not scripted")
        body = {"schema": lab.RESULT_SCHEMA, "action": action, "params": lab.canonical(action, params),
                "basis": "computational", "status": "ok", "error": None,
                "scalars": {k: {"value": v, "unit": u} for k, (v, u) in sc.items()}, "series": series,
                "solver": {"name": "stand-in"}, "details": None}
        (workdir / "result.json").write_text(json.dumps(body), encoding="utf-8")
        out = ExecutionOutput(table=fc.table_from_result(body), files={"result.json": workdir / "result.json"},
                              solver={"name": "stand-in"}, executed=lab.canonical(action, params))
        enc = {"output": exec_child.encode_output(out, workdir)}
        return enc, executors.result_digest(enc)
    return run_child


PREDICT = {"fit_thermal": {"heat_transfer_coefficient": {"value": 10.0, "lo": 5.0, "hi": 20.0, "unit": "W/m^2/K"}},
           "optimize_protocol": {"model_version": {"value": 0, "lo": 0, "hi": 0, "unit": "1"}},
           "lab_charge": {"max_temperature": {"value": 40.0, "lo": 35.0, "hi": 45.0, "unit": "degC"}}}


def _lab_propose(action, params):
    return ("propose_experiment", {"action": action, "params": params, "hypothesis": "The cooling differs.",
                                   "prediction": PREDICT[action], "rationale": "Scripted.",
                                   "expected_effect": "Scripted."})


def test_lab_model_arms_offer_only_their_actions_and_score_the_best_design(tmp_path, monkeypatch,
                                                                           no_provider):  # noqa: F811
    calls = []
    monkeypatch.setattr(executors, "run_child", lab_standin(calls))

    def rule(ctx, messages):
        if any(m.get("role") == "tool" for m in messages):
            return [("wait", {"reason": "proposed"})]
        if any(r["state"] in PENDING for r in ctx["requests"]):
            return [("wait", {"reason": "pending"})]
        used = ctx["budget"]["experiments_used"]
        if used == 0:
            return [_lab_propose("fit_thermal", {"family": 1, "fit_on": 1}),
                    _lab_propose("optimize_protocol", {"ambient": 25.0, "model_version": 0})]
        if used == 1:
            return [_lab_propose("lab_charge", {**dict(zip(fc.C_RATES, DESIGN)), "ambient": 25.0, "rest_min": 60.0})]
        return [("conclude", {"text": "Designed under the nominal model and tested once."})]
    out = tmp_path / "out"
    info = fl.run_one(out, "C", "no_diagnostics_meta", 1, live=True, cap_usd=1.0, client_factory=scripted(rule),
                      charge_fn=_stub_truth, forecast_fn=_stub_forecast, log=lambda *_: None)
    assert info["stop"] == "concluded"
    assert [a for a, _ in calls] == ["optimize_protocol", "lab_charge"]   # the fit was refused, never proposed
    L = Ledger(out / "C-no_diagnostics_meta-1" / "ledger")
    try:
        answers = [str(r.data["payload"].get("result")) for r in chain.records(L, campaign_ids(L)[0])
                   if r.type == "planner" and r.data["phase"] == "tool_result"]
    finally:
        L.db.close()
    assert any("fit_thermal is not among the actions" in a for a in answers)
    run = out / "C-no_diagnostics_meta-1"
    a = json.loads((run / "assessment.json").read_text(encoding="utf-8"))
    assert a["final_protocol"]["c_rates"] == DESIGN and a["final_protocol"]["model_version"] == 0
    assert a["cases"]["C"]["true_min_anode_mV"] == 21.0 and a["cases"]["C"]["feasible"] is False
    revealed = json.loads((run / "revealed-condition.json").read_text(encoding="utf-8"))
    assert revealed["h"] == 15.0 and revealed["k"] == 1.0
    m = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
    assert m["lab_tests"] == 1 and m["model_versions"] == 0 and m["nominal_design"] == DESIGN
