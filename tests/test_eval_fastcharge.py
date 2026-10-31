"""The fast-charge evaluation harness (evals/fastcharge.py): arms, case B amendment and counterfactual, the $25 stop,
the passive and reference rows, and a report reproduced from the ledgers alone.

No test makes a paid call. Model arms run through a real metered ChatSession against ScriptedChatClient. Most tests
use small synthetic Maccor-format records and a cheap stand-in for the solver child (executors.run_child returns a
surrogate's output), so the runner, ingestion, comparisons and verify all run for real. The BO case A test and the
reference row on the real PyBaMM solver are opt-in (TRANSLUCENT_SOLVER_TESTS=1) and need the fetched records.
"""
import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest

from evals import fastcharge as fx
from translucent import chain, exec_child, executors, runtime
from translucent.ledger import Ledger
from translucent.loop import REGISTRATION, campaign_ids, request_states
from translucent.loop_planner import ScriptedChatClient, ScriptedLoopPlanner
from translucent.systems import lgm50_fastcharge as fc
from translucent.systems.base import ExecutionOutput, ResultTable
from translucent.verify import verify

ROOT = Path(__file__).resolve().parents[1]
PENDING = {"awaiting_approval", "queued", "running", "awaiting_result"}


# ------------------------------------------------------------------ synthetic records in the manifest's format

HEADER = ["Rec", "Cycle P", "Cycle C", "Step", "Test Time [s]", "Step Time [s]", "Capacity [Ah]", "Energy [Wh]",
          "Current [A]", "Voltage [V]", "Md", "ES", "DPT Time", "Temperature Cell [degC]",
          "Temperature Chamber [degC]"]


def _csv(path: Path):
    """Rest, a 0.1C discharge, rest, a 0.3C (1.5 A) constant-current charge to 4.2 V, constant voltage."""
    rows, t = [], 0.0

    def add(step, n, dt, current, v0, v1, cap0, cap1, md, temp=26.8):
        nonlocal t
        for k in range(n):
            f = k / max(n - 1, 1)
            rows.append([len(rows) + 1, 0, 1, step, round(t, 3), round(k * dt, 3), round(cap0 + f * (cap1 - cap0), 5),
                         0, current, round(v0 + f * (v1 - v0), 4), md, 0, "x", round(temp + f, 3), 26.7])
            t += dt
    add(1, 4, 10.0, 0, 3.13, 3.13, 0, 0, "R")
    add(2, 6, 60.0, 0.5, 3.1, 2.5, 0, 0.05, "D")
    add(3, 4, 10.0, 0, 2.6, 2.8, 0, 0, "R")
    add(4, 181, 60.0, 1.5, 2.9, 4.2, 0.0, 4.5, "C")       # 1.5 A for 3 h: 0 -> 4.5 Ah
    add(5, 10, 60.0, 0.05, 4.2, 4.2, 4.5, 4.9, "C")
    pre = ["Name:,x"] + [f"Line{i},x" for i in range(12)]
    path.write_text("\n".join(pre + [",".join(HEADER)] + [",".join(map(str, r)) for r in rows]) + "\n",
                    encoding="utf-8")


@pytest.fixture
def records(tmp_path):
    d = tmp_path / "records"
    d.mkdir()
    for name in fc.RECORDS:
        _csv(d / name)
    return d


# ------------------------------------------------------------------ the stand-in solver

A_PHI = (67.0, 57.0, 47.0, 37.0)


def surrogate(params: dict) -> dict:
    """A cheap monotone stand-in for the DFN: 1.5C everywhere gives 28 min, +7 mV and about 51 degC."""
    c = [float(params[k]) for k in fc.C_RATES]
    t80 = sum(10.5 / x for x in c)
    phi = min(a - 20.0 * x for a, x in zip(A_PHI, c))
    peak = float(params["ambient"]) + 11.64 * sum(x * x for x in c) / 4
    return {"charge_time_80": round(t80, 2), "min_anode_potential": round(phi, 1), "max_temperature": round(peak, 2)}


def standin_run_child(calls: list):
    def run_child(adapter, action, params, workdir, wall_s):
        calls.append((action, dict(params)))
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "result.json").write_text(json.dumps({"stand_in": True, "params": params}), encoding="utf-8")
        if action == "calibrate_thermal":
            table = ResultTable(time_unit=None, t=(), series={}, scalars={
                "heat_transfer_coefficient": (6.0, "W/m^2/K"), "thermal_fit_rmse": (0.4, "delta_degC"),
                "voltage_rmse": (90.0, "mV")})
            executed = {}
        else:
            s = surrogate(params)
            n, amb = 40, float(params["ambient"])
            end = s["charge_time_80"] * 60
            t = tuple(end * k / (n - 1) for k in range(n))
            ramp = lambda a, b: tuple(a + (b - a) * k / (n - 1) for k in range(n))  # noqa: E731
            table = ResultTable(time_unit="s", t=t, series={
                "voltage": ("V", ramp(3.5, 4.15)), "current": ("A", (5.0,) * n),
                "cell_temperature": ("degC", ramp(amb, s["max_temperature"])),
                "anode_potential": ("mV", ramp(120.0, s["min_anode_potential"])),
                "state_of_charge": ("%", ramp(10.0, 80.0))},
                scalars={"status": ("ok", ""), "validity": (fc.validity(action, params)["label"], ""),
                         "charge_time_80": (s["charge_time_80"], "min"),
                         "min_anode_potential": (s["min_anode_potential"], "mV"),
                         "max_temperature": (s["max_temperature"], "degC"), "plated_lithium": (0.0, "Ah"),
                         "plated_lithium_peak": (0.0, "Ah"), "max_voltage": (4.15, "V"), "max_current": (5.0, "A"),
                         "cv_entered": ("no", "")})
            executed = fc.canonical(action, params)
        out = ExecutionOutput(table=table, files={"result.json": workdir / "result.json"},
                              solver={"name": "stand-in"}, executed=executed)
        body = {"output": exec_child.encode_output(out)}
        return body, executors.result_digest(body)
    return run_child


@pytest.fixture
def solver_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(executors, "run_child", standin_run_child(calls))
    return calls


@pytest.fixture
def no_provider(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a provider client was constructed")
    monkeypatch.setattr(runtime, "_client", refuse)


# ------------------------------------------------------------------ a scripted planner that reads its context

FIRST = [(1.0, 1.0, 1.0, 1.0), (1.2, 1.2, 1.0, 0.8), (1.5, 1.3, 1.0, 0.8), (1.5, 1.5, 1.05, 1.1)]
GRID = [(a, b, c, d) for a in (1.5, 1.2, 0.9) for b in (1.5, 1.2, 0.9) for c in (1.5, 1.3, 1.05, 0.8)
        for d in (1.5, 1.1, 0.8, 0.55) if (a, b, c, d) not in FIRST]


def _params(p, ambient=25.0):
    return dict(zip(fc.C_RATES, p)) | {"ambient": ambient}


def _propose(p, ambient=25.0):
    s = surrogate(_params(p, ambient))
    pred = {q: {"value": v, "lo": round(v - 1, 2), "hi": round(v + 1, 2), "unit": u}
            for (q, v), u in zip(s.items(), ("min", "mV", "degC"))}
    return ("propose_experiment", {"action": "simulate_protocol", "params": _params(p, ambient),
                                   "hypothesis": "The fourth window sets the lowest anode potential.",
                                   "prediction": pred, "rationale": "From the surrogate.",
                                   "expected_effect": "Feasible: the next step is faster; not: slower."})


def candidates(margin):
    ok = [p for p in GRID if fc.feasible({"status": "ok", **surrogate(_params(p))}, margin, 45.0)]
    return sorted(ok, key=lambda p: (surrogate(_params(p))["charge_time_80"], p))


def loop_rule(ctx, messages):
    """Four fixed probes, then the fastest untried protocol the surrogate predicts feasible under the current
    margin; conclude when the budget is spent."""
    if any(r["state"] in PENDING for r in ctx["requests"]):
        return [("wait", {"reason": "pending"})]
    used, left = ctx["budget"]["experiments_used"], ctx["budget"]["experiments_remaining"]
    if left == 0:
        return [("conclude", {"text": "The experiment budget is spent."})]
    if used < len(FIRST):
        return [_propose(FIRST[used])]
    margin = next(c["limit"] for c in ctx["objective"]["constraints"] if c["quantity"] == "min_anode_potential")
    pool = candidates(margin)
    return [_propose(pool[(used - len(FIRST)) % len(pool)])]


def no_feedback_rule(ctx, messages):
    return [_propose(p) for p in FIRST + candidates(10.0)[:4]]


def scripted(rule, made: list | None = None):
    def factory():
        planner = ScriptedLoopPlanner(rule)
        if made is not None:
            made.append(planner)
        return ScriptedChatClient(planner)
    return factory


def metrics(out, name):
    return fx.run_metrics(Path(out) / name)


# ------------------------------------------------------------------ 2. case B end to end with scripted model arms

def test_case_b_loop_amends_after_the_fourth_result_and_the_replay_passes(tmp_path, records, solver_calls,
                                                                         no_provider):
    out = tmp_path / "runs"
    info = fx.run_one(out, "B", "loop", 1, records, live=True, client_factory=scripted(loop_rule), check_pins=False,
                      log=lambda *_: None)
    assert info["stop"] == "concluded"
    run = out / "B-loop-1"
    L = Ledger(run / "ledger")
    try:
        cid = campaign_ids(L)[0]
        recs = chain.records(L, cid)
        amendment = next(r for r in recs if r.type == "amendment")
        assert amendment.actor == "person:engineer" and amendment.data["reason"] == fx.AMENDMENT_TEXT
        results = [r for r in recs if r.type == "result" and r.actor == "system:runner"
                   and request_states(L, cid)[r.data["request_id"]].proposed_by != REGISTRATION]
        assert [r.seq < amendment.seq for r in results] == [True] * 4 + [False] * 4
        # nothing ran between the fourth result and the amendment, and the next turn came before the fifth run
        fifth = sorted(s.versions[0].seq for s in request_states(L, cid).values()
                       if s.proposed_by != REGISTRATION)[4]
        assert fifth > amendment.seq
        assert chain.concluded(L, cid).data["outcome"] == "improved_in_simulation"   # loop-compare/2
    finally:
        L.db.close()
    assert verify(run / "ledger").ok
    assert verify(run / "counterfactual" / "ledger").ok
    m = metrics(out, "B-loop-1")
    rp = m["metric5"]["replay"]
    assert rp["ran"] and rp["passes"], rp
    assert rp["predicted_min_anode_potential_mV"] >= 25.0
    assert fc.canonical("simulate_protocol", rp["with"]["params"]) != \
        fc.canonical("simulate_protocol", rp["without"]["params"])
    # without the amendment the planner kept the +10 mV choice; with it, the first +25 mV candidate
    assert tuple(rp["without"]["params"][c] for c in fc.C_RATES) == candidates(10.0)[0]
    assert tuple(rp["with"]["params"][c] for c in fc.C_RATES) == candidates(25.0)[0]
    assert m["metric5"]["amended_after_completed"] == 4
    assert m["metric1"]["best_feasible_t80_min"] == min(surrogate(_params(p))["charge_time_80"]
                                                        for p in candidates(25.0)[:4])
    assert m["metric4"]["model_usd"] > 0 and m["metric4"]["counterfactual_model_usd"] > 0
    assert m["metric6"]["amendments"] == 1 and m["metric6"]["person_approvals"] == 0
    # 8 experiments and the baseline re-run on the stand-in; the counterfactual copy ran nothing
    assert len(solver_calls) == 9


def test_case_b_no_feedback_proposes_the_batch_in_one_turn_and_never_sees_a_result(tmp_path, records, solver_calls,
                                                                                  no_provider):
    out = tmp_path / "runs"
    made = []
    info = fx.run_one(out, "B", "no_feedback", 1, records, live=True, client_factory=scripted(no_feedback_rule, made),
                      check_pins=False, log=lambda *_: None)
    assert [t["status"] for t in info["turns"]] == ["proposed"] and len(info["turns"][0]["proposed"]) == 8
    assert info["stop"] == "experiment budget spent"
    assert len(made) == 1 and len(made[0].seen) == 1                       # one model call, ever
    ctx = json.loads(made[0].seen[0][1]["content"])
    assert not any("result" in r for r in ctx["requests"])
    assert "Propose all 8 experiments now" in made[0].seen[0][0]["content"]
    m = metrics(out, "B-no_feedback-1")
    assert m["metric5"]["amended"] and m["metric5"]["amended_after_completed"] == 4
    assert m["metric5"]["replay"] is None
    # the batch was chosen for +10 mV; under +25 mV its later protocols are infeasible
    assert m["metric5"]["infeasible_after_change"] == m["metric5"]["results_after_change"] == 4
    assert len(solver_calls) == 9
    assert verify(out / "B-no_feedback-1" / "ledger").ok


def test_bo_case_b_reads_the_amended_margin(tmp_path, records, solver_calls):
    out = tmp_path / "runs"
    info = fx.run_one(out, "B", "bo", 1, records, check_pins=False, log=lambda *_: None)
    assert info["stop"] == "concluded"
    m = metrics(out, "B-bo-1")
    assert m["metric3"]["computational_experiments"] == 8 and m["metric5"]["amended"]
    assert m["metric4"]["model_usd"] == 0.0 and m["planner_kind"] == "domain_baseline"


# ------------------------------------------------------------------ 3. --live and the $25 stop

def test_model_arms_refuse_without_live(tmp_path, records, capsys):
    out = tmp_path / "runs"
    for arm in ("loop", "no_feedback"):
        with pytest.raises(fx.HarnessError, match="--live"):
            fx.run_one(out, "A", arm, 1, records, check_pins=False)
        with pytest.raises(SystemExit) as e:
            fx.main(["run", "--records", str(records), "--out", str(out), "--case", "A", "--arm", arm])
        assert e.value.code == 2
    assert "add --live" in capsys.readouterr().err
    assert not out.exists()


def _fake_spend(out: Path, usd: float):
    L = Ledger(out / "Z-fake-1" / "ledger")
    try:
        from translucent.loop_planner import fastcharge_objective
        from translucent.loop_service import Actor, LoopService
        cid = LoopService(L, Actor("person", "engineer")).open_campaign(
            fastcharge_objective(registered_by="engineer"), "fake spend")["campaign_id"]
        chain.append(L, cid, "planner", {"phase": "model_request", "payload": {"turn": 1, "cost_usd": usd}},
                     "agent:fake", chain.natural_id("pln", cid, "fake"))
    finally:
        L.db.close()


def test_runs_set_aside_under_void_still_count_toward_the_limit(tmp_path):
    out = tmp_path / "runs"
    out.mkdir()
    _fake_spend(out, 3.5)
    (out / "void").mkdir()
    (out / "Z-fake-1").rename(out / "void" / "Z-fake-1")
    assert fx.GlobalCap(out).spent() == pytest.approx(3.5)
    assert fx.run_dirs(out) == []                                   # and they are not reported


def test_the_global_limit_stops_a_call_that_would_pass_25_dollars(tmp_path, records, solver_calls, no_provider):
    out = tmp_path / "runs"
    out.mkdir()
    _fake_spend(out, 24.95)
    assert fx.GlobalCap(out).spent() == pytest.approx(24.95)
    info = fx.run_one(out, "A", "loop", 1, records, live=True, client_factory=scripted(loop_rule), check_pins=False,
                      log=lambda *_: None)
    assert [t["status"] for t in info["turns"]] == ["global_budget"]
    assert "$25.00 limit" in info["turns"][0]["reason"]
    m = metrics(out, "A-loop-1")
    assert m["stop"].startswith("global_budget") and m["metric4"]["model_usd"] == 0.0
    assert m["metric3"]["computational_experiments"] == 0
    assert len(solver_calls) == 1                                   # the baseline re-run only
    # at or past the limit, a model run does not start at all
    L = Ledger(out / "Z-fake-1" / "ledger")
    try:
        cid = campaign_ids(L)[0]
        chain.append(L, cid, "planner", {"phase": "model_request", "payload": {"turn": 2, "cost_usd": 0.05}},
                     "agent:fake", chain.natural_id("pln", cid, "fake2"))
    finally:
        L.db.close()
    with pytest.raises(fx.HarnessError, match="not started"):
        fx.run_one(out, "A", "loop", 2, records, live=True, client_factory=scripted(loop_rule), check_pins=False)
    assert not (out / "A-loop-2").exists()


def test_the_limit_counts_a_counterfactual_copy_only_after_its_prefix(tmp_path, records, solver_calls, no_provider):
    out = tmp_path / "runs"
    fx.run_one(out, "B", "loop", 1, records, live=True, client_factory=scripted(loop_rule), check_pins=False,
               log=lambda *_: None)
    run = out / "B-loop-1"
    main = fx.ledger_spend(run / "ledger" / "ledger.sqlite")
    copy_all = fx.ledger_spend(run / "counterfactual" / "ledger" / "ledger.sqlite")
    cf = fx.counterfactual_spend(run)
    assert 0 < cf < copy_all
    assert fx.GlobalCap(out).spent() == pytest.approx(main + cf)


# ------------------------------------------------------------------ 4. passive and reference rows

def test_the_passive_row_uses_no_model_and_no_solver(tmp_path, records, solver_calls, no_provider):
    out = tmp_path / "runs"
    info = fx.run_one(out, "A", "passive", 1, records, check_pins=False, log=lambda *_: None)
    assert info["stop"] == "concluded" and solver_calls == []
    m = metrics(out, "A-passive-1")
    best = m["passive"]["best"]
    assert best["c_rate"] == 0.3 and best["t10_80_min"] == pytest.approx(140.0, abs=0.01)
    assert best["charges"] == 3 and len(best["records"]) == 3
    assert m["model_usd"] == 0.0
    assert verify(out / "A-passive-1" / "ledger").ok


def test_the_reference_row_runs_0p7c_and_the_baseline_without_a_model(tmp_path, records, solver_calls, no_provider):
    out = tmp_path / "runs"
    fx.run_one(out, "C", "reference", 1, records, check_pins=False, log=lambda *_: None)
    assert sorted(p["c_rate_1"] for _, p in solver_calls) == [0.7, 1.5]
    assert {p["ambient"] for _, p in solver_calls} == {15.0}           # case C: the baseline runs at 15 degC too
    m = metrics(out, "C-reference-1")
    e = m["experiments"][0]
    assert e["params"]["c_rate_4"] == 0.7 and e["scalars"]["charge_time_80"] == surrogate(e["params"])["charge_time_80"]
    assert m["baseline"]["params"]["ambient"] == 15.0
    assert verify(out / "C-reference-1" / "ledger").ok


def test_the_reference_row_is_judged_under_the_cases_final_margin(tmp_path, records, solver_calls):
    """0.7C gives +23 mV on the stand-in (and +22.9 mV on the solver): feasible in case A, not under case B's +25."""
    out = tmp_path / "runs"
    fx.run_one(out, "B", "reference", 1, records, check_pins=False, log=lambda *_: None)
    m = metrics(out, "B-reference-1")
    assert m["experiments"][0]["scalars"]["min_anode_potential"] == 23.0
    assert fx.reference_row(m, fx.CASES["A"])["feasible_final"] is True
    assert fx.reference_row(m, fx.CASES["B"])["feasible_final"] is False


# ------------------------------------------------------------------ 5. the report from the ledgers alone

def test_report_reproduces_results_and_markdown_from_the_ledgers_alone(tmp_path, records, solver_calls, no_provider):
    out = tmp_path / "runs"
    log = lambda *_: None  # noqa: E731
    fx.run_one(out, "A", "loop", 1, records, live=True, client_factory=scripted(loop_rule), check_pins=False, log=log)
    fx.run_one(out, "A", "no_feedback", 1, records, live=True, client_factory=scripted(no_feedback_rule),
               check_pins=False, log=log)
    fx.run_one(out, "A", "bo", 0, records, check_pins=False, log=log)
    fx.run_one(out, "A", "passive", 1, records, check_pins=False, log=log)
    fx.run_one(out, "A", "reference", 1, records, check_pins=False, log=log)
    md = tmp_path / "FASTCHARGE.md"
    first = fx.report(out, md)
    a, b = (out / "results.json").read_bytes(), md.read_bytes()
    for p in out.glob("*/run.json"):
        p.unlink()
    (out / "results.json").unlink()
    md.unlink()
    assert fx.main(["report", "--out", str(out), "--md", str(md)]) == 0
    assert (out / "results.json").read_bytes() == a and md.read_bytes() == b
    case = first["cases"]["A"]
    assert set(case["arms"]) == {"loop", "no_feedback", "bo"}
    assert case["criteria"]["judged"] and case["criteria"]["outcome"] in ("success", "tie", "loss")
    assert case["passive_row"]["answer"]["c_rate"] == 0.3
    assert case["reference_row"]["ran"] and case["failing_baseline"]["plating_violated"]
    text = md.read_text(encoding="utf-8")
    assert "loop_" not in text and "rq_" not in text and str(tmp_path) not in text
    assert "Case A" in text and "Success criteria" in text


def test_metrics_judge_feasibility_with_the_margin_in_force(tmp_path, records, solver_calls, no_provider):
    """A result feasible under +10 mV but not +25 mV counts as feasible at its time and not at the end (case B)."""
    out = tmp_path / "runs"
    fx.run_one(out, "B", "no_feedback", 1, records, live=True, client_factory=scripted(no_feedback_rule),
               check_pins=False, log=lambda *_: None)
    m = metrics(out, "B-no_feedback-1")
    for e in m["experiments"]:
        sc = e["scalars"]
        assert e["feasible_at_time"] == fc.feasible(sc, e["margin_at_result_mV"], 45.0)
        assert e["feasible_final"] == fc.feasible(sc, 25.0, 45.0)
    assert any(e["feasible_at_time"] and not e["feasible_final"] for e in m["experiments"])


# ------------------------------------------------------------------ 1. BO, case A, seed 0, on the real solver

def _real_records() -> Path | None:
    for p in (os.environ.get("TRANSLUCENT_LGM50_RECORDS"), fc.DATA, ROOT.parents[2] / "data" / "raw" / "chen2020-lgm50"):
        if p and all((Path(p) / n).is_file() for n in fc.RECORDS):
            return Path(p)
    return None


solver = pytest.mark.skipif(
    importlib.util.find_spec("pybamm") is None or os.environ.get("TRANSLUCENT_SOLVER_TESTS") != "1"
    or _real_records() is None,
    reason="solver tests are opt-in: the solver group, TRANSLUCENT_SOLVER_TESTS=1 and the fetched records")


@pytest.mark.solver
@solver
def test_bo_case_a_seed_0_on_the_real_solver(tmp_path):
    out = tmp_path / "runs"
    info = fx.run_one(out, "A", "bo", 0, _real_records(), log=lambda *_: None)
    assert info["stop"] == "concluded"
    run = out / "A-bo-0"
    assert verify(run / "ledger").ok
    m = fx.run_metrics(run)
    # an independent recomputation from the results: best feasible time by feasible() at +10 mV
    L = Ledger(run / "ledger")
    try:
        cid = campaign_ids(L)[0]
        best = None
        for st in request_states(L, cid).values():
            if st.proposed_by == REGISTRATION or st.result is None:
                continue
            sc = {q: L.get("measurement", mid).value for q, mid in st.result.data["measurements"].items()}
            sc["status"] = st.result.data["categories"]["status"]
            if fc.feasible(sc, 10.0, 45.0):
                best = sc["charge_time_80"] if best is None else min(best, sc["charge_time_80"])
        assert chain.concluded(L, cid).actor == "agent:baseline-constrained-bo"
    finally:
        L.db.close()
    assert m["metric1"]["best_feasible_t80_min"] == best
    assert m["metric3"]["computational_experiments"] == 8 and m["metric4"]["solver_failures"] == 0
    assert m["baseline"]["charge_time_80"] == pytest.approx(28.2, abs=0.1)
    print(json.dumps({k: m[k] for k in ("metric1", "metric2", "metric3", "metric4", "metric6")}, indent=1))
    shutil.copy(run / "run.json", tmp_path / "keep.json")


@pytest.mark.solver
@solver
def test_reference_row_case_a_on_the_real_solver(tmp_path):
    out = tmp_path / "runs"
    fx.run_one(out, "A", "reference", 1, _real_records(), log=lambda *_: None)
    row = fx.reference_row(fx.run_metrics(out / "A-reference-1"), fx.CASES["A"])
    assert row["charge_time_80"] == pytest.approx(60.0, abs=0.1)
    assert row["max_temperature"] == pytest.approx(35.2, abs=0.1)
    assert row["min_anode_potential"] == pytest.approx(22.9, abs=0.1)
    assert verify(out / "A-reference-1" / "ledger").ok
