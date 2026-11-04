"""Fast-charge design with a learned thermal model: runs the arms of the protocol (evals/FASTCHARGE-LAB-PROTOCOL.md)
on the simulated lab (system pybamm-fastcharge-lab) and scores each final protocol on the hidden condition.

    python -m evals.fastcharge_lab plan --out runs/fastcharge-lab [--cases N C M O] [--arms ...] [--repeats 1 2 3]
    python -m evals.fastcharge_lab run --out runs/fastcharge-lab [--cases ...] [--arms ...] [--repeats ...] \
        [--workers 2] [--live --cap-usd X]
    python -m evals.fastcharge_lab report --out runs/fastcharge-lab [--md evals/FASTCHARGE-LAB.md]

Everything here is computational. The lab is a separate simulation whose condition (heat-transfer coefficient h,
thermal-mass scale k, chamber offset) is sealed per campaign; the campaign sees only its commitment. After a campaign
stops, the harness takes its final protocol, runs it once under the hidden condition (the assessment, counted apart
from the campaign's budget) and only then copies the sealed condition into the run folder.

One folder per run, <out>/<case>-<arm>-<repeat>: the ledger, run.json (how the run ended), assessment.json (the
hidden-condition run of the final protocol and the forecast errors), metrics.json (every metric, so the report can be
rebuilt after a ledger is deleted for disk space) and revealed-condition.json. The fixed-model arm needs no lab: its
final protocol does not depend on the case, so one run per repeat, <out>/all-fixed_model_meta-<repeat>, is assessed
under every case.

Arms: adaptive_meta, no_diagnostics_meta and fixed_model_meta make paid calls to Muse (Meta profile) and refuse to
start without --live and --cap-usd; before every provider call the harness adds the spend recorded in every run
ledger under <out> to the call's worst case and stops the turn with status `global_budget` if the sum would pass the
cap. calibrate_once and adaptive_numeric make no model call: a fixed policy proposes as an agent, and the objective's
auto-approval rule approves.
"""
from __future__ import annotations

import argparse
import bisect
import concurrent.futures as cf
import hashlib
import json
import math
import shutil
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from evals.fastcharge import GlobalCap, drive, model_session_for
from translucent import chain
from translucent.ledger import Ledger
from translucent.loop import actor_kind, campaign_ids, request_states
from translucent.loop_planner import fastcharge_objective, model_spend
from translucent.loop_runner import LoopRunner
from translucent.loop_service import Actor, LoopService
from translucent.systems import lgm50_fastcharge as fc
from translucent.systems import lgm50_lab as lab

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / "FASTCHARGE-LAB-PROTOCOL.md"
REPORT_MD = HERE / "FASTCHARGE-LAB.md"

ENGINEER = "engineer"
PERSON = Actor("person", ENGINEER, "cli")
AMBIENT = 25.0                        # degC, the setpoint every case and every design uses
MARGIN_MV = 25.0                      # the objective's plating margin
T_LIMIT_C = 45.0                      # the objective's temperature limit
LAB_TESTS = 4                         # lab tests per lab campaign
EXPERIMENTS = 12                      # requests per lab campaign, lab tests included
FIXED_EXPERIMENTS = 8                 # simulate_protocol runs of the fixed-model arm
MODEL_USD = 2.50                      # model budget per model run
REPEATS = (1, 2, 3)
STEP_C = fc.C_GRID                    # one lattice step: case N allows a design change of at most this
U_TESTS_AFTER_FIT = 1                 # case U: lab tests after the first fit that still count as stopping (a
                                      # temperature confirmation of the final design)
FASTER_MIN = 1.0                      # "faster" between arms: at least this many minutes, median of feasible runs
MAX_TICKS = 4                         # runner ticks per numerical-policy request (a guard)


@dataclass(frozen=True)
class Case:
    name: str
    h: float                          # W/m^2/K
    k: float                          # thermal-mass scale
    offset_K: float                   # chamber above its setpoint
    title: str
    note: str
    noise_K: float = lab.NOISE_K      # the lab's thermocouple noise (1 sigma)


CASES = {
    "N": Case("N", 6.0, 1.0, 0.0, "nominal",
              "The lab matches the engineer's model (h 6, k 1)."),
    "C": Case("C", 15.0, 1.0, 0.0, "changed cooling",
              "Better cooling (h 15): the cell runs colder and the anode margin shrinks, which no lab test shows."),
    "U": Case("U", 7.0, 1.0, 0.0, "uncertainty that does not change the design",
              "Slightly better cooling (h 7) seen through a noisy thermocouple (1 K): the nominal design breaks the "
              "anode margin, so the first test matters; after one fit the remaining uncertainty does not change the "
              "design, and the right move is to stop learning.", noise_K=1.0),
    "M": Case("M", 6.0, 1.6, 0.0, "thermal mass (outside the one-parameter family)",
              "Extra thermal mass (k 1.6): family 1 (h only) cannot fit it and says so; family 2 (h and k) can. The "
              "test is whether the arm notices the inadequate fit instead of trusting its design."),
    "O": Case("O", 6.0, 1.0, 3.0, "omitted mechanism",
              "The chamber runs 3 K above its setpoint: outside the model family; fits should say so."),
}

LAB_ARMS = ("adaptive_meta", "no_diagnostics_meta", "calibrate_once", "adaptive_numeric")
MODEL_ARMS = ("adaptive_meta", "no_diagnostics_meta", "fixed_model_meta")
NUMERIC_ARMS = ("calibrate_once", "adaptive_numeric")
ARMS = ("adaptive_meta", "no_diagnostics_meta", "fixed_model_meta", "calibrate_once", "adaptive_numeric")
FIXED_CASE = "all"                    # the fixed-model arm's folder: one run serves every case
ARM_LABEL = {"adaptive_meta": "1. Adaptive (Muse)", "no_diagnostics_meta": "2. No diagnostics (Muse)",
             "fixed_model_meta": "3. Fixed model (Muse, M2 loop)", "calibrate_once": "4. Calibrate once (numerical)",
             "adaptive_numeric": "5. Adaptive policy (numerical)"}
# the computational lab-system actions; bench_charge (a real bench, a person) is not offered in a simulated evaluation
ALL_ACTIONS = ("lab_charge", "forecast_protocol", "fit_thermal", "check_model", "rank_measurements",
               "optimize_protocol")
NO_DIAGNOSTICS_ACTIONS = ("lab_charge", "forecast_protocol", "optimize_protocol")
COMPOSED_TEST = {"test_c_rate_1": 1.0, "test_c_rate_2": 1.0, "test_c_rate_3": 1.0, "test_c_rate_4": 1.0,
                 "test_rest_min": 60.0}


class HarnessError(RuntimeError):
    """The harness refuses to start a run, with the reason."""


# ------------------------------------------------------------------ run names and folders

def run_name(case: str, arm: str, n: int) -> str:
    return f"{FIXED_CASE if arm == 'fixed_model_meta' else case}-{arm}-{n}"


def parse_name(name: str) -> tuple[str, str, int] | None:
    case, _, rest = name.partition("-")
    arm, _, n = rest.rpartition("-")
    if arm not in ARMS or not n.isdigit():
        return None
    if (arm == "fixed_model_meta") != (case == FIXED_CASE) or (case != FIXED_CASE and case not in CASES):
        return None
    return case, arm, int(n)


def run_dirs(out: Path) -> list[Path]:
    """Finished runs: a run.json, and either the ledger or the metrics saved at the end of the run."""
    out = Path(out)
    if not out.is_dir():
        return []
    return sorted(p for p in out.iterdir() if p.is_dir() and parse_name(p.name) and (p / "run.json").is_file())


def planned(cases, arms, repeats) -> list[tuple[str, str, int]]:
    """(case, arm, repeat) in run order; the fixed-model arm once per repeat whatever the cases."""
    out, seen = [], set()
    for arm in arms:
        for n in repeats:
            for case in ([FIXED_CASE] if arm == "fixed_model_meta" else cases):
                if (case, arm, n) not in seen:
                    seen.add((case, arm, n))
                    out.append((case, arm, n))
    return out


# ------------------------------------------------------------------ objectives

def lab_objective(arm: str) -> dict:
    """The lab campaign's objective: fastest 10-80 % charge at 25 degC, anode >= +25 mV, cell <= 45 degC, with the
    budgets and the auto-approval rule the registering engineer writes before the campaign starts."""
    model = arm in MODEL_ARMS
    planner = {"kind": "muse" if model else "external", "profile": "meta" if model else None, "client": None,
               "actions": list(NO_DIAGNOSTICS_ACTIONS if arm == "no_diagnostics_meta" else ALL_ACTIONS)}
    t = lab.OBJECTIVE_TEMPLATE
    return {"system": {"id": lab.SYSTEM_ID}, "datasets": [], "baseline": [], "evaluator": t["evaluator"],
            "kind": "fastest_feasible",
            "statement": (f"{t['statement']['fastest_feasible']} The ambient setpoint is {AMBIENT:g} degC. Lab tests "
                          "run in a simulated lab whose fixture the engineer's model may not match (computational)."),
            "target": {k: v for k, v in t["target"].items() if k != "margin"},
            "constraints": [dict(c) for c in t["constraints"]], "quantities": [dict(q) for q in t["quantities"]],
            "budget": {"model_usd": MODEL_USD if model else 0.0, "experiments": EXPERIMENTS, "experiment_usd": 0.0,
                       "lab_tests": LAB_TESTS},
            "planner": planner,
            "auto_approve": [{"executor": "computational", "max_usd": 0.0, "max_wall_s": 900.0,
                              "max_count": EXPERIMENTS}],
            "registered_by": ENGINEER}


def fixed_objective() -> dict:
    """Arm 3: the M2 loop on pybamm-fastcharge v1 (h fixed at 6), simulate_protocol only, the same limits."""
    data = fastcharge_objective(kind="muse", profile="meta", model_usd=MODEL_USD, experiments=FIXED_EXPERIMENTS,
                                registered_by=ENGINEER, objective_kind="fastest_feasible")
    data["constraints"] = [{"quantity": "min_anode_potential", "op": ">=", "limit": MARGIN_MV, "unit": "mV",
                            "label": f"Plating margin: anode potential at or above +{MARGIN_MV:g} mV"},
                           {"quantity": "max_temperature", "op": "<=", "limit": T_LIMIT_C, "unit": "degC",
                            "label": f"Cell at or below {T_LIMIT_C:g} degC"}]
    data["statement"] += f" The ambient is {AMBIENT:g} degC: every simulated protocol runs at ambient = {AMBIENT:g} degC."
    data["planner"]["actions"] = ["simulate_protocol"]
    data["auto_approve"] = [{"executor": "computational", "max_usd": 0.0, "max_wall_s": 300.0,
                             "max_count": FIXED_EXPERIMENTS}]
    return data


def open_run(root: Path, case: Case | None, arm: str, name: str) -> tuple[Ledger, str]:
    L = Ledger(root)
    obj = fixed_objective() if arm == "fixed_model_meta" else lab_objective(arm)
    title = f"Fast-charge lab evaluation, {ARM_LABEL[arm]}" + (f", case {case.name}" if case else "")
    cid = LoopService(L, PERSON, idempotency_key=name).open_campaign(obj, title, memory="off")["campaign_id"]
    if case is not None:
        lab.seal_lab(L.root, cid, case.h, case.k, noise_K=case.noise_K, ambient_offset_K=case.offset_K)
    return L, cid


# ------------------------------------------------------------------ the numerical policies

class Budget:
    def __init__(self, requests: int = EXPERIMENTS, lab_tests: int = LAB_TESTS):
        self.requests, self.lab_tests = requests, lab_tests
        self.used, self.tests = 0, 0

    def left(self) -> int:
        return self.requests - self.used

    def can(self, requests: int = 1, lab_tests: int = 0) -> bool:
        return self.used + requests <= self.requests and self.tests + lab_tests <= self.lab_tests


class Policy:
    """A fixed script over the lab system's actions. `do(action, params, why)` proposes one request and returns
    {"ok", "scalars", "details", "failure"} once it has run (LedgerExecutor; tests pass a stand-in). The policy keeps
    its own account of lab tests and model versions in the order the ledger numbers them."""

    def __init__(self, do: Callable[[str, dict, str], dict], budget: Budget | None = None):
        self.do, self.budget = do, budget or Budget()
        self.versions: list[dict] = [{"n": 0, "family": 0, "adequate": True, "rmse": None, "tests": []}]
        self.tests: list[dict] = []           # lab tests that returned a result, in order
        self.designs: list[dict] = []         # found designs, in order
        self.trace: list[dict] = []
        self.stop = ""

    def _step(self, action: str, params: dict, why: str) -> dict | None:
        lab_test = action == "lab_charge"
        if not self.budget.can(1, 1 if lab_test else 0):
            self.trace.append({"action": action, "skipped": "budget"})
            return None
        self.budget.used += 1
        self.budget.tests += lab_test
        r = self.do(action, params, why)
        self.trace.append({"action": action, "params": params, "ok": r["ok"],
                           "scalars": r.get("scalars"), "failure": r.get("failure")})
        return r if r["ok"] else None

    def optimize(self, version: int) -> list[float] | None:
        r = self._step("optimize_protocol", {"ambient": AMBIENT, "model_version": version},
                       f"Design under model version {version}.")
        if r is None or r["scalars"].get("design_found") != "yes":
            return None
        rates = [float(r["scalars"][c]) for c in fc.C_RATES]
        self.designs.append({"rates": rates, "model_version": version})
        return rates

    def lab_test(self, rates: list[float], rest_min: float, why: str) -> dict | None:
        r = self._step("lab_charge", {**dict(zip(fc.C_RATES, rates)), "ambient": AMBIENT, "rest_min": rest_min}, why)
        if r is None:
            return None
        t = {"n": len(self.tests) + 1, "rates": rates, "rest_min": rest_min,
             "max_temperature": r["scalars"].get("max_temperature")}
        self.tests.append(t)
        return t

    def fit(self, family: int, tests: list[int]) -> dict | None:
        mask = sum(1 << (n - 1) for n in tests)
        r = self._step("fit_thermal", {"family": family, "fit_on": mask},
                       f"Fit family {family} on lab test{'s' if len(tests) > 1 else ''} "
                       f"{', '.join(map(str, tests))}.")
        if r is None or r["scalars"].get("status", "ok") != "ok":
            return None
        v = {"n": len(self.versions), "family": family, "adequate": r["scalars"].get("fit_adequate") == "yes",
             "rmse": r["scalars"].get("thermal_fit_rmse"), "tests": list(tests)}
        self.versions.append(v)
        return v

    def check(self, version: dict, tests: list[int]) -> float | None:
        mask = sum(1 << (n - 1) for n in tests)
        r = self._step("check_model", {"model_version": version["n"], "test_on": mask},
                       f"Check model version {version['n']} on lab tests it did not fit.")
        return None if r is None else r["scalars"].get("heldout_rmse")

    def rank(self, rates: list[float], version: int) -> dict | None:
        return self._step("rank_measurements", {**dict(zip(fc.C_RATES, rates)), "ambient": AMBIENT,
                                                **COMPOSED_TEST, "model_version": version},
                          f"Value candidate lab tests for the design under model version {version}.")

    def latest_adequate(self) -> dict:
        fitted = self.versions[1:]
        good = [v for v in fitted if v["adequate"]]
        return good[-1] if good else (fitted[-1] if fitted else self.versions[0])


# the solver's acquisition menu (lgm50_lab_solver.menu): option -> (rates, rest) given the design in question
def menu_option(option: int, design: list[float]) -> tuple[list[float], float]:
    return {1: (design, 60.0), 2: (design, 0.0), 3: ([1.0] * 4, 60.0), 4: ([0.5] * 4, 60.0),
            5: ([COMPOSED_TEST[f"test_{c}"] for c in fc.C_RATES], COMPOSED_TEST["test_rest_min"])}[option]


def calibrate_once(p: Policy) -> Policy:
    """Arm 4: optimize under v0; lab test of that design with a 60 min rest; fit family 1 on it; optimize under the
    fitted version; stop."""
    design = p.optimize(0)
    if design is None:
        p.stop = "no design under the nominal model"
        return p
    if p.lab_test(design, 60.0, "Lab test of the nominal design, with a 60 min rest.") is None:
        p.stop = "the lab test returned no result"
        return p
    v = p.fit(1, [1])
    if v is None:
        p.stop = "the fit returned no model version"
        return p
    p.optimize(v["n"])
    p.stop = "policy finished"
    return p


def adaptive_numeric(p: Policy) -> Policy:
    """Arm 5, the fixed adaptive policy (written out in the protocol as coded here)."""
    design0 = p.optimize(0)                                                             # 1
    if design0 is None:
        p.stop = "no design under the nominal model"
        return p
    if p.lab_test(design0, 60.0, "Lab test of the nominal design, with a 60 min rest.") is None:      # 2
        p.stop = "the lab test returned no result"
        return p
    f2 = p.fit(2, [1])                                                                  # 3
    candidates = [v for v in (f2,) if v is not None]
    if f2 is None or not f2["adequate"]:                                                # 4
        f1 = p.fit(1, [1])
        if f1 is not None:
            candidates.append(f1)
    if not candidates:
        p.stop = "no fit returned a model version"
        return p
    current = min(candidates, key=lambda v: (v["rmse"] if v["rmse"] is not None else math.inf, -v["family"]))
    r = p.rank(design0, current["n"])                                                   # 5
    test2 = None
    if r is not None:                                                                   # 6
        option, value = int(r["scalars"].get("best_measurement") or 0), r["scalars"].get("best_value")
        if option > 0 and isinstance(value, (int, float)) and value > 0 and p.budget.can(1, 1):
            rates, rest = menu_option(option, design0)
            test2 = p.lab_test(rates, rest, f"The test rank_measurements recommends (option {option}).")
    if test2 is not None:                                                               # 7
        checked = [(p.check(v, [test2["n"]]), v) for v in candidates]
        checked = [(e, v) for e, v in checked if e is not None]
        if checked:
            current = min(checked, key=lambda ev: ev[0])[1]
        refit = p.fit(current["family"], [t["n"] for t in p.tests])
        if refit is not None:
            current = refit
    use = p.latest_adequate()                                                           # 8
    p.optimize(use["n"])
    if not p.designs:
        p.stop = "no design found"
        return p
    final = p.designs[-1]
    confirm = p.lab_test(final["rates"], 0.0, "Temperature confirmation of the design (no rest).")     # 9
    if confirm is not None and isinstance(confirm["max_temperature"], (int, float)) and \
            confirm["max_temperature"] > T_LIMIT_C and p.budget.can(2):                 # 10
        refit = p.fit(current["family"], [t["n"] for t in p.tests])
        if refit is not None:
            p.optimize(p.latest_adequate()["n"])
    p.stop = "policy finished"
    return p


POLICIES = {"calibrate_once": calibrate_once, "adaptive_numeric": adaptive_numeric}


class LedgerExecutor:
    """Proposes as the numerical arm's agent; the objective's auto-approval rule approves; the runner executes."""

    def __init__(self, L: Ledger, cid: str, arm: str):
        self.L, self.cid = L, cid
        self.svc = LoopService(L, Actor("agent", arm, "cli"))
        self.runner = LoopRunner(L.root, process="cli", workers=1)
        self.problems: list[str] = []

    def __call__(self, action: str, params: dict, why: str) -> dict:
        try:
            rq = self.svc.propose(self.cid, action, params, rationale=why, prediction={},
                                  expected_effect="A fixed policy step.")["request_id"]
        except Exception as e:  # noqa: BLE001 - a refused proposal is a recorded outcome of the run
            self.problems.append(f"{action}: proposal refused: {type(e).__name__}: {e}")
            return {"ok": False, "failure": f"refused: {e}"}
        for _ in range(MAX_TICKS):
            st = request_states(self.L, self.cid)[rq]
            if st.state not in ("queued", "running"):
                break
            self.runner.tick(self.cid, limit=1)
            self.problems += self.runner.problems
            self.runner.problems = []
        st = request_states(self.L, self.cid)[rq]
        if st.result is None:
            failure = st.failure.data if st.failure is not None else f"left {st.state}"
            return {"ok": False, "failure": failure}
        body = lab._result_json(self.L, st)
        return {"ok": True, "scalars": {k: v["value"] for k, v in body["scalars"].items()},
                "details": body.get("details")}


# ------------------------------------------------------------------ the assessment (never visible to the planner)

def _interp(x: float, xs: list[float], ys: list[float]) -> float:
    i = bisect.bisect_left(xs, x)
    if i <= 0:
        return ys[0]
    if i >= len(xs):
        return ys[-1]
    x0, x1 = xs[i - 1], xs[i]
    return ys[i - 1] + (ys[i] - ys[i - 1]) * ((x - x0) / (x1 - x0) if x1 > x0 else 0.0)


def _lists(run: dict) -> dict:
    return {k: [float(v) for v in run[k]] for k in ("t", "T", "phi") if k in run}


def truth_run(rates: list[float], case: Case, charge_fn=None) -> dict:
    """The final protocol under the hidden condition: one charge at the setpoint plus the chamber offset, no rest,
    no noise."""
    if charge_fn is None:
        from translucent.systems import lgm50_lab_solver as solver
        charge_fn = solver.charge
    run = charge_fn((list(rates), AMBIENT + case.offset_K, 0.0, case.h, case.k, None))
    if run.get("status") == "solver_error":
        return {"status": "solver_error", "error": run.get("error")}
    return {"status": run["status"], "t80_s": run.get("t80_s"), "min_anode": float(run["min_anode"]),
            "max_T": float(run["max_T"]), **_lists(run)}


def score(truth: dict) -> dict:
    """Feasible: status ok, anode >= the margin and cell <= the limit, exactly the objective's limits."""
    if truth.get("status") != "ok" or truth.get("t80_s") is None:
        return {"true_t80_min": None, "true_min_anode_mV": truth.get("min_anode"),
                "true_max_temperature_degC": truth.get("max_T"), "feasible": False,
                "violations": [{"limit": "status", "value": truth.get("status")}]}
    v = []
    if truth["min_anode"] < MARGIN_MV:
        v.append({"limit": "min_anode_potential", "by": round(MARGIN_MV - truth["min_anode"], 3), "unit": "mV"})
    if truth["max_T"] > T_LIMIT_C:
        v.append({"limit": "max_temperature", "by": round(truth["max_T"] - T_LIMIT_C, 3), "unit": "K"})
    return {"true_t80_min": round(truth["t80_s"] / 60, 3), "true_min_anode_mV": round(truth["min_anode"], 3),
            "true_max_temperature_degC": round(truth["max_T"], 3), "feasible": not v, "violations": v}


def forecast_errors(fcst: dict, truth: dict) -> dict:
    """The design's forecast under the model version it used against the hidden-condition run. Temperatures are the
    lumped cell temperature (the lab's surface reading without noise); the anode potential is a simulation-only
    quantity, never measurable in a lab."""
    if fcst.get("status") not in ("ok", "not_reached") or truth.get("status") == "solver_error":
        return {"forecast_status": fcst.get("status")}
    sc = {k: (v[0] if isinstance(v, (tuple, list)) else v.get("value") if isinstance(v, dict) else v)
          for k, v in fcst["scalars"].items()}
    ch = fcst["series"]["channels"]
    est = ch["cell_temperature"]
    est = est[1] if isinstance(est, (tuple, list)) and len(est) == 2 and isinstance(est[1], list) else \
        est["values"] if isinstance(est, dict) else est
    grid = list(fcst["series"]["t"])
    end = min(grid[-1], truth["t"][-1])
    pairs = [(e, _interp(t, truth["t"], truth["T"])) for t, e in zip(grid, est) if t <= end]
    rmse = math.sqrt(sum((e - o) ** 2 for e, o in pairs) / len(pairs)) if pairs else None
    members = (fcst.get("details") or {}).get("members") or []
    first = members[0] if members else {}
    out = {"forecast_status": fcst.get("status"),
           "temperature_rmse_K": None if rmse is None else round(rmse, 4),
           "max_temperature_error_K": round(sc["max_temperature"] - truth["max_T"], 4),
           "max_temperature_error_estimate_K": None if first.get("max_temperature_degC") is None else
           round(first["max_temperature_degC"] - truth["max_T"], 4),
           "min_anode_error_mV": round(sc["min_anode_potential"] - truth["min_anode"], 4),
           "min_anode_error_estimate_mV": None if first.get("min_anode_potential_mV") is None else
           round(first["min_anode_potential_mV"] - truth["min_anode"], 4),
           "anode_note": "simulation-only quantity: no lab measures the anode potential",
           "temperature_note": "lumped cell temperature over the charge, the model estimate against the hidden run",
           "members": len(members)}
    return out


def assess_design(design: dict | None, model: dict | None, case: Case, *, charge_fn=None, forecast_fn=None) -> dict:
    """One final protocol under one hidden case: truth, feasibility, violations and forecast errors."""
    if design is None or not design.get("c_rates"):
        return {"case": case.name, "design": None, "note": "no design", "feasible": False, "true_t80_min": None,
                "violations": [{"limit": "no design"}]}
    rates = [float(x) for x in design["c_rates"]]
    truth = truth_run(rates, case, charge_fn)
    out = {"case": case.name, "design": {"c_rates": rates, "model_version": design.get("model_version")},
           "truth_status": truth["status"], **score(truth)}
    if model is not None:
        if forecast_fn is None:
            from translucent.systems import lgm50_lab_solver as solver
            forecast_fn = solver.forecast
        fcst = forecast_fn({**dict(zip(fc.C_RATES, rates)), "ambient": AMBIENT, "rest_min": 0.0}, model)
        out["forecast"] = forecast_errors(fcst, truth)
    return out


def final_lab_design(L: Ledger, cid: str) -> tuple[dict | None, dict | None]:
    """A lab campaign's final protocol (view.learning.best_design) and the model version it used."""
    from translucent import loop_view
    learning = loop_view.view(L, cid, {"kind": "live", "api": "", "ledger": "eval"}).get("learning") or {}
    best = learning.get("best_design")
    if not best:
        return None, None
    versions = lab.model_versions(L, request_states(L, cid))
    return best, versions[int(best["model_version"])]


def final_fixed_design(L: Ledger, cid: str) -> tuple[dict | None, dict]:
    """The fixed-model arm's final protocol: the comparisons' incumbent when it meets the constraints."""
    from translucent import loop_compare
    a = loop_compare.assess(L, cid, chain.records(L, cid))
    best = a["best"]
    if best is None or a["findings"]["feasibility"]["best"] != "meets":
        return None, lab.nominal_model()
    st = request_states(L, cid)[best.data["candidate"]["request_id"]]
    return ({"c_rates": [float(st.data.params[c]) for c in fc.C_RATES], "model_version": 0,
             "request_id": st.request_id}, lab.nominal_model())


# ------------------------------------------------------------------ one run

class _NoAmendment:
    amended_margin = None


def check_live(arm: str, live: bool, cap_usd: float | None) -> None:
    if arm in MODEL_ARMS and not live:
        raise HarnessError(f"the {arm} arm calls a paid model: it runs only with --live and --cap-usd")
    if arm in MODEL_ARMS and (cap_usd is None or not math.isfinite(cap_usd) or cap_usd <= 0):
        raise HarnessError(f"the {arm} arm needs --cap-usd: the evaluation's total model-spend limit in dollars")


def run_one(out: Path, case_name: str, arm: str, n: int, *, live: bool = False, cap_usd: float | None = None,
            client_factory: Callable | None = None, charge_fn=None, forecast_fn=None, log=print) -> dict:
    """One run in <out>/<case>-<arm>-<n>: the campaign, then the assessment, then the reveal."""
    if arm not in ARMS:
        raise HarnessError(f"unknown arm {arm}")
    check_live(arm, live, cap_usd)
    name = run_name(case_name, arm, n)
    run_dir = Path(out) / name
    if (run_dir / "run.json").is_file():
        raise HarnessError(f"{name} has already run")
    if run_dir.exists():
        raise HarnessError(f"{name} was interrupted: its folder exists without run.json; remove it to run again")
    cap = GlobalCap(out, cap_usd) if arm in MODEL_ARMS else None
    if cap is not None and (spent := cap.spent()) >= cap.limit_usd:
        raise HarnessError(f"the evaluation has spent ${spent:.4f} of ${cap.limit_usd:.2f}; {name} is not started")
    case = None if arm == "fixed_model_meta" else CASES[case_name]
    started = time.time()
    run_dir.mkdir(parents=True)
    L, cid = open_run(run_dir / "ledger", case, arm, name)
    problems: list[str] = []
    try:
        if arm in MODEL_ARMS:
            # drive answers up to three planner questions with the fixed text, as the engineer
            outcome = drive(L, cid, _NoAmendment, model_session_for(cap, client_factory), log=log)
            problems += outcome.get("runner_problems", [])
            info = {"stop": outcome["stop"],
                    "turns": [{k: t.get(k) for k in ("turn", "status", "reason", "proposed", "spent_usd")}
                              for t in outcome.get("turns", [])]}
        else:
            executor = LedgerExecutor(L, cid, arm)
            policy = POLICIES[arm](Policy(executor))
            problems += executor.problems
            info = {"stop": policy.stop, "trace": policy.trace}
            try:
                LoopService(L, Actor("agent", arm, "cli")).conclude(
                    cid, f"The {arm} policy finished: {policy.stop}. Its final protocol is the last design found.")
                info["concluded"] = True
            except Exception as e:  # noqa: BLE001 - recorded: the run's protocol is scored either way
                info["concluded"] = False
                problems.append(f"conclude: {type(e).__name__}: {e}")
        campaign_wall = round(time.time() - started, 1)
        # the assessment: after the campaign stops, outside its budget
        if arm == "fixed_model_meta":
            design, model = final_fixed_design(L, cid)
            assessments = {c.name: assess_design(design, model, c, charge_fn=charge_fn, forecast_fn=forecast_fn)
                           for c in CASES.values()}
        else:
            design, model = final_lab_design(L, cid)
            assessments = {case.name: assess_design(design, model, case, charge_fn=charge_fn,
                                                    forecast_fn=forecast_fn)}
        (run_dir / "assessment.json").write_text(json.dumps({"final_protocol": design, "cases": assessments},
                                                            indent=1, sort_keys=True), encoding="utf-8")
        if case is not None:                       # revealed only after scoring
            shutil.copy2(lab.sealed_path(L.root, cid), run_dir / "revealed-condition.json")
    finally:
        L.db.close()
    info = {"run": name, "case": case_name, "arm": arm, "n": n, "campaign_id": cid, "live": live,
            "started_at": started, "ended_at": time.time(), "campaign_wall_s": campaign_wall,
            "harness_wall_s": round(time.time() - started, 1), "problems": problems, **info}
    (run_dir / "run.json").write_text(json.dumps(info, indent=1, sort_keys=True), encoding="utf-8")
    metrics = run_metrics(run_dir)
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=1, sort_keys=True), encoding="utf-8")
    return info


# ------------------------------------------------------------------ metrics, from the ledger and the assessment

def _scal(L: Ledger, st, name: str):
    try:
        v = (lab._result_json(L, st).get("scalars") or {}).get(name)
    except (KeyError, OSError, ValueError):
        return None
    return v.get("value") if isinstance(v, dict) else None


def campaign_metrics(L: Ledger, cid: str, arm: str) -> dict:
    recs = chain.records(L, cid)
    states = request_states(L, cid, recs)
    reqs = [s for s in states.values() if actor_kind(s.proposed_by) == "agent"]
    by_action: dict[str, int] = {}
    for s in reqs:
        if s.result is not None:
            by_action[s.action] = by_action.get(s.action, 0) + 1
    events = L.events(cid)
    out = {"requests": len(reqs), "runs_by_action": by_action,
           "failed_runs": sum(s.failure is not None for s in reqs),
           "refused_or_left": sum(s.result is None and s.failure is None for s in reqs),
           "model_usd": round(model_spend(L, cid, recs), 6),
           "interventions": {"questions": sum(e.type == "question.asked" for e in events),
                             "answers": sum(e.type == "question.answered" for e in events),
                             "notes": sum(e.type == "human.note" for e in events),
                             "rejections": sum(r.type == "rejection" for r in recs)},
           "concluded": chain.concluded(L, cid) is not None}
    out["interventions"]["total"] = sum(out["interventions"].values())
    if arm == "fixed_model_meta":
        out["lab_tests"] = 0
        out["model_versions"] = 0
        return out
    out["lab_tests"] = sum(s.action in lab.LAB_ACTIONS and s.result is not None for s in reqs)
    versions = lab.model_versions(L, states)
    out["model_versions"] = len(versions) - 1
    out["versions"] = [{"version": v["version"], "family": v["family"], "estimate": v["estimate"],
                        "adequate": v.get("adequate"), "fit_rmse_K": v.get("fit_rmse_K"),
                        "consistent_with_nominal": v.get("consistent_with_nominal"),
                        "lab_tests": (v.get("validity") or {}).get("lab_tests")} for v in versions[1:]]
    out["inadequate_fit_flagged"] = any(v.get("adequate") is False for v in versions[1:])
    fits = sorted((s for s in reqs if s.action == "fit_thermal" and s.result is not None), key=lambda s: s.result.seq)
    out["lab_tests_after_first_fit"] = None if not fits else \
        sum(s.action in lab.LAB_ACTIONS and s.result is not None and s.result.seq > fits[0].result.seq for s in reqs)
    nominal = sorted((s for s in reqs if s.action == "optimize_protocol" and s.result is not None and
                      int(s.data.params["model_version"]) == 0 and _scal(L, s, "design_found") == "yes"),
                     key=lambda s: s.result.seq)
    out["nominal_design"] = [float(_scal(L, nominal[0], c)) for c in fc.C_RATES] if nominal else None
    out["checks"] = [{"model_version": int(s.data.params["model_version"]),
                      "heldout_rmse_K": _scal(L, s, "heldout_rmse")}
                     for s in reqs if s.action == "check_model" and s.result is not None]
    return out


def design_change(design: list[float] | None, nominal: list[float] | None) -> float | None:
    """Largest change of any window's C-rate against the nominal design."""
    if design is None or nominal is None:
        return None
    return round(max(abs(a - b) for a, b in zip(design, nominal)), 4)


def run_metrics(run_dir: Path) -> dict:
    """Every metric of one run: from the ledger when it is there, else the metrics saved at the end of the run."""
    run_dir = Path(run_dir)
    info = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    db = run_dir / "ledger" / "ledger.sqlite"
    if not db.is_file():
        saved = run_dir / "metrics.json"
        if not saved.is_file():
            raise HarnessError(f"{run_dir.name} has neither its ledger nor its saved metrics")
        return json.loads(saved.read_text(encoding="utf-8"))
    case_name, arm, n = parse_name(run_dir.name)
    L = Ledger(run_dir / "ledger")
    try:
        cid = campaign_ids(L)[0]
        camp = campaign_metrics(L, cid, arm)
    finally:
        L.db.close()
    assessment = json.loads((run_dir / "assessment.json").read_text(encoding="utf-8")) \
        if (run_dir / "assessment.json").is_file() else {"final_protocol": None, "cases": {}}
    final = assessment.get("final_protocol")
    rates = final.get("c_rates") if final else None
    out = {"run": run_dir.name, "case": case_name, "arm": arm, "n": n, "stop": info.get("stop"),
           "campaign_wall_min": round(info.get("campaign_wall_s", info.get("harness_wall_s", 0.0)) / 60, 2),
           "problems": info.get("problems", []), **camp,
           "final_protocol": None if not final else {"c_rates": rates, "model_version": final.get("model_version")},
           "per_case": {}}
    for cname, a in assessment.get("cases", {}).items():
        row = {k: a.get(k) for k in ("true_t80_min", "true_min_anode_mV", "true_max_temperature_degC", "feasible",
                                     "violations", "forecast")}
        if cname == "N" and arm != "fixed_model_meta":
            change = design_change(rates, camp.get("nominal_design"))
            row["design_change_C"] = change
            row["non_nominal_adopted"] = bool(final) and final.get("model_version") not in (0, None)
            row["gratuitous_change"] = change is not None and change > STEP_C + 1e-9
        if cname in ("M", "O") and arm != "fixed_model_meta":
            row["inadequate_fit_flagged"] = camp.get("inadequate_fit_flagged")
            row["final_version_adequate"] = None if not final or not final.get("model_version") else \
                next((v["adequate"] for v in camp.get("versions", []) if v["version"] == final["model_version"]),
                     None)
        if cname == "U" and arm != "fixed_model_meta":
            row["lab_tests_after_first_fit"] = camp.get("lab_tests_after_first_fit")
            row["stopped_learning"] = row["lab_tests_after_first_fit"] is not None and \
                row["lab_tests_after_first_fit"] <= U_TESTS_AFTER_FIT
        out["per_case"][cname] = row
    return out


# ------------------------------------------------------------------ the summary and the criteria

def _median(xs: list) -> float | None:
    xs = [float(x) for x in xs if isinstance(x, (int, float))]
    return round(statistics.median(xs), 4) if xs else None


def per_case_rows(runs: list[dict]) -> list[dict]:
    """One row per (run, case): the fixed-model arm's runs are assessed under every case."""
    rows = []
    for r in runs:
        for cname, a in r["per_case"].items():
            rows.append({**{k: v for k, v in r.items() if k != "per_case"}, "case": cname, **a})
    return rows


def summarise(runs: list[dict]) -> dict:
    rows = per_case_rows(runs)
    out: dict = {}
    for cname in CASES:
        arms = {}
        for arm in ARMS:
            rs = [r for r in rows if r["case"] == cname and r["arm"] == arm]
            if not rs:
                continue
            feas = [r for r in rs if r.get("feasible")]

            def fe(key: str, rs=rs) -> float | None:
                return _median([(r.get("forecast") or {}).get(key) for r in rs])
            m = {"runs": len(rs), "feasible": len(feas),
                 "median_true_t80_feasible_min": _median([r["true_t80_min"] for r in feas]),
                 "median_true_min_anode_mV": _median([r.get("true_min_anode_mV") for r in rs]),
                 "median_true_max_temperature_degC": _median([r.get("true_max_temperature_degC") for r in rs]),
                 "median_lab_tests": _median([r.get("lab_tests") for r in rs]),
                 "median_requests": _median([r.get("requests") for r in rs]),
                 "failed_runs": sum(r.get("failed_runs") or 0 for r in rs),
                 "model_usd": round(math.fsum(r.get("model_usd") or 0.0 for r in rs), 4),
                 "median_wall_min": _median([r.get("campaign_wall_min") for r in rs]),
                 "interventions": sum((r.get("interventions") or {}).get("total", 0) for r in rs),
                 "median_model_versions": _median([r.get("model_versions") for r in rs]),
                 "median_temperature_rmse_K": fe("temperature_rmse_K"),
                 "median_max_temperature_error_K": fe("max_temperature_error_K"),
                 "median_min_anode_error_mV": fe("min_anode_error_mV")}
            if cname == "N" and arm != "fixed_model_meta":
                m["gratuitous_changes"] = sum(bool(r.get("gratuitous_change")) for r in rs)
                m["non_nominal_adopted"] = sum(bool(r.get("non_nominal_adopted")) for r in rs)
            if cname in ("M", "O") and arm != "fixed_model_meta":
                m["inadequate_fit_flagged"] = sum(bool(r.get("inadequate_fit_flagged")) for r in rs)
            if cname == "M" and arm != "fixed_model_meta":
                m["final_version_adequate"] = sum(r.get("final_version_adequate") is True for r in rs)
            if cname == "U" and arm != "fixed_model_meta":
                m["stopped_learning"] = sum(bool(r.get("stopped_learning")) for r in rs)
                m["stopped_and_feasible"] = sum(bool(r.get("stopped_learning")) and bool(r.get("feasible"))
                                                for r in rs)
                m["median_lab_tests_after_first_fit"] = _median([r.get("lab_tests_after_first_fit") for r in rs])
            arms[arm] = m
        if arms:
            out[cname] = {"arms": arms, "criteria": criteria(cname, arms)}
    return out


def compare(a: dict | None, b: dict | None) -> dict:
    """Arm a against arm b in one case: 'adds' when a is feasible in more repeats, or as often (at least once) and at
    least FASTER_MIN faster by the median true t80 of its feasible runs; 'worse' when a is feasible in fewer repeats, or
    as often and at least FASTER_MIN slower; else 'equal'. Never 'optimal'."""
    if a is None or b is None:
        return {"decided": False, "note": "one of the arms has no runs"}
    fa, fb = a["feasible"], b["feasible"]
    ta, tb = a["median_true_t80_feasible_min"], b["median_true_t80_feasible_min"]
    if fa != fb:
        verdict = "adds" if fa > fb else "worse"
    elif fa and ta is not None and tb is not None and tb - ta >= FASTER_MIN - 1e-9:
        verdict = "adds"
    elif fa and ta is not None and tb is not None and ta - tb >= FASTER_MIN - 1e-9:
        verdict = "worse"
    else:
        verdict = "equal"
    return {"decided": True, "verdict": verdict, "feasible": [fa, fb], "runs": [a["runs"], b["runs"]],
            "median_t80_min": [ta, tb]}


def _complete(*arms: dict | None) -> str | None:
    """Why a "2 of 3" criterion cannot be decided yet, or None."""
    if any(a is None for a in arms):
        return "one of the arms has no runs"
    if any(a["runs"] < len(REPEATS) for a in arms):
        return f"fewer than {len(REPEATS)} runs"
    return None


def criteria(cname: str, arms: dict) -> dict:
    """The protocol's acceptance criteria for one case; a criterion whose arms have not all their repeats is not
    decided ("2 of 3" needs the three)."""
    g = arms.get
    out = {"meta_vs_numeric": compare(g("adaptive_meta"), g("adaptive_numeric")),
           "meta_vs_no_diagnostics": compare(g("adaptive_meta"), g("no_diagnostics_meta")),
           "numeric_vs_calibrate_once": compare(g("adaptive_numeric"), g("calibrate_once")),
           "numeric_vs_fixed_model": compare(g("adaptive_numeric"), g("fixed_model_meta"))}
    if cname == "C":
        a, f = g("adaptive_meta"), g("fixed_model_meta")
        why = _complete(a, f)
        out["changed_cooling"] = {"decided": False, "note": why} if why else \
            {"decided": True, "met": a["feasible"] >= 2 and f["runs"] - f["feasible"] >= 2,
             "adaptive_feasible": a["feasible"], "fixed_infeasible": f["runs"] - f["feasible"]}
    if cname == "N":
        out["no_gratuitous_change"] = {
            arm: ({"decided": False, "note": why} if (why := _complete(m)) else
                  {"decided": True, "met": m["runs"] - m["gratuitous_changes"] >= 2})
            | {"gratuitous_changes": m["gratuitous_changes"], "runs": m["runs"]}
            for arm, m in arms.items() if "gratuitous_changes" in m}
    if cname == "M":
        out["thermal_mass_handled"] = {
            arm: ({"decided": False, "note": why} if (why := _complete(m)) else
                  {"decided": True, "met": m["final_version_adequate"] >= 2 and m["feasible"] >= 2})
            | {"final_version_adequate": m["final_version_adequate"], "feasible": m["feasible"], "runs": m["runs"]}
            for arm, m in arms.items() if "final_version_adequate" in m and arm != "no_diagnostics_meta"}
    if cname == "U":
        out["stops_learning"] = {
            arm: ({"decided": False, "note": why} if (why := _complete(m)) else
                  {"decided": True, "met": m["stopped_and_feasible"] >= 2})
            | {"stopped_learning": m["stopped_learning"], "stopped_and_feasible": m["stopped_and_feasible"],
               "median_lab_tests_after_first_fit": m["median_lab_tests_after_first_fit"], "runs": m["runs"]}
            for arm, m in arms.items() if "stopped_learning" in m and arm != "no_diagnostics_meta"}
    if cname == "O":
        out["inadequate_fit_flagged"] = {
            arm: ({"decided": False, "note": why} if (why := _complete(m)) else
                  {"decided": True, "met": m["inadequate_fit_flagged"] >= 2})
            | {"flagged": m["inadequate_fit_flagged"], "runs": m["runs"]}
            for arm, m in arms.items() if "inadequate_fit_flagged" in m and arm != "no_diagnostics_meta"}
    return out


# ------------------------------------------------------------------ the report

def report(out: Path, md: Path | None = REPORT_MD) -> dict:
    runs = [run_metrics(d) for d in run_dirs(out)]
    data = {"protocol": "evals/FASTCHARGE-LAB-PROTOCOL.md",
            "protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest() if PROTOCOL.is_file() else None,
            "basis": "computational: a simulated lab with hidden conditions; no physical cell",
            "budgets": {"lab_tests": LAB_TESTS, "requests": EXPERIMENTS, "fixed_model_experiments": FIXED_EXPERIMENTS,
                        "model_usd_per_run": MODEL_USD},
            "limits": {"min_anode_potential_mV": MARGIN_MV, "max_temperature_degC": T_LIMIT_C,
                       "ambient_degC": AMBIENT},
            "cases": {c.name: {"title": c.title, "note": c.note, "h": c.h, "k": c.k, "offset_K": c.offset_K,
                               "noise_K": c.noise_K} for c in CASES.values()},
            "total_model_usd": round(math.fsum(r.get("model_usd") or 0.0 for r in runs), 6),
            "runs": runs, "summary": summarise(runs)}
    (Path(out) / "results.json").write_text(json.dumps(data, indent=1, sort_keys=True, allow_nan=False) + "\n",
                                            encoding="utf-8")
    if md is not None:
        Path(md).write_text(markdown(data), encoding="utf-8")
    return data


def _f(v, fmt="{:.2f}", none="none") -> str:
    return none if v is None else fmt.format(v)


def _rates(p: dict | None) -> str:
    if not p or not p.get("c_rates"):
        return "no design"
    return ", ".join(f"{c:g}" for c in p["c_rates"]) + f" C (v{p.get('model_version')})"


def _violations(v: list | None) -> str:
    if not v:
        return "none"
    return "; ".join(f"{x['limit']} by {x['by']:g} {x['unit']}" if "by" in x else str(x.get("limit")) for x in v)


def markdown(data: dict) -> str:
    runs, summary = data["runs"], data["summary"]
    lines = ["# Fast-charge design with a learned thermal model: evaluation", "",
             "All results are computational. The lab is a simulation with a hidden condition per case; no physical "
             "cell was tested. Every number below is computed by `python -m evals.fastcharge_lab report` from the run "
             "folders. No result is called optimal: the design search is a greedy step-down, not an optimiser with a "
             "proof.", ""]
    if not runs:
        return "\n".join(lines + ["No runs have been recorded yet.", ""])
    lines += ["## Method", "",
              f"Objective: fastest 10-80 % charge at {AMBIENT:g} °C with the anode potential at or above "
              f"+{MARGIN_MV:g} mV and the cell at or below {T_LIMIT_C:g} °C. Lab campaigns have {LAB_TESTS} lab tests "
              f"and {EXPERIMENTS} requests in all; the fixed-model arm has {FIXED_EXPERIMENTS} simulations. Model "
              f"arms have ${MODEL_USD:.2f} each. After a campaign stops its final protocol is run once under the "
              "hidden condition (the assessment, outside the budget). Feasible means the hidden run reaches 80 %, its "
              f"lowest anode potential is at least +{MARGIN_MV:g} mV and its peak cell temperature at most "
              f"{T_LIMIT_C:g} °C. The anode potential is a simulation-only quantity: no lab measures it.", "",
              f"Model spend recorded: ${data['total_model_usd']:.2f}.", "",
              "| Case | Hidden condition | What it tests |", "|---|---|---|"]
    for c in CASES.values():
        lines.append(f"| {c.name} | h {c.h:g} W/m²K, k {c.k:g}, chamber {c.offset_K:+g} K, noise {c.noise_K:g} K | "
                     f"{c.note} |")
    lines.append("")
    rows = per_case_rows(runs)
    for cname, c in CASES.items():
        s = summary.get(cname)
        if s is None:
            continue
        lines += [f"## Case {cname}: {c.title}", "",
                  "| Arm | Repeat | Final protocol | True t80 (min) | Lowest anode (mV, sim.) | Peak T (°C) | Feasible | "
                  "Violations | Lab tests | Requests | Failed | Model versions | Model $ | Wall (min) | T RMSE (K) | "
                  "Peak T error (K) | Anode error (mV, sim.) |", "|" + "---|" * 17]
        for r in sorted((r for r in rows if r["case"] == cname), key=lambda r: (ARMS.index(r["arm"]), r["n"])):
            f = r.get("forecast") or {}
            lines.append(f"| {ARM_LABEL[r['arm']]} | {r['n']} | {_rates(r.get('final_protocol'))} | "
                         f"{_f(r.get('true_t80_min'))} | {_f(r.get('true_min_anode_mV'), '{:+.1f}')} | "
                         f"{_f(r.get('true_max_temperature_degC'), '{:.1f}')} | "
                         f"{'yes' if r.get('feasible') else 'no'} | {_violations(r.get('violations'))} | "
                         f"{r.get('lab_tests', 0)} | {r.get('requests', 0)} | {r.get('failed_runs', 0)} | "
                         f"{r.get('model_versions', 0)} | {r.get('model_usd', 0.0):.2f} | "
                         f"{_f(r.get('campaign_wall_min'), '{:.1f}')} | {_f(f.get('temperature_rmse_K'))} | "
                         f"{_f(f.get('max_temperature_error_K'), '{:+.2f}')} | "
                         f"{_f(f.get('min_anode_error_mV'), '{:+.1f}')} |")
        lines += ["", "Per arm (medians over repeats; true t80 over the feasible repeats only):", "",
                  "| Arm | Runs | Feasible | Median true t80 (min) | Median lab tests | Median requests | Failed runs | "
                  "Model $ | Median wall (min) | Interventions | Median T RMSE (K) |", "|" + "---|" * 11]
        for arm, m in s["arms"].items():
            lines.append(f"| {ARM_LABEL[arm]} | {m['runs']} | {m['feasible']} | "
                         f"{_f(m['median_true_t80_feasible_min'])} | {_f(m['median_lab_tests'], '{:g}')} | "
                         f"{_f(m['median_requests'], '{:g}')} | {m['failed_runs']} | {m['model_usd']:.2f} | "
                         f"{_f(m['median_wall_min'], '{:.1f}')} | {m['interventions']} | "
                         f"{_f(m['median_temperature_rmse_K'])} |")
        lines += ["", "Criteria:", ""]
        names = {"meta_vs_numeric": ("adaptive (Muse)", "the adaptive numerical policy"),
                 "meta_vs_no_diagnostics": ("adaptive (Muse)", "Muse without diagnostics"),
                 "numeric_vs_calibrate_once": ("the adaptive numerical policy", "calibrate-once"),
                 "numeric_vs_fixed_model": ("the adaptive numerical policy", "the fixed model")}
        words = {"adds": "adds over", "worse": "is worse than", "equal": "is equal to"}
        cr = s["criteria"]
        for key, (a, b) in names.items():
            x = cr[key]
            if x["decided"]:
                lines.append(f"- {a[0].upper() + a[1:]} {words[x['verdict']]} {b}: feasible in {x['feasible'][0]} of "
                             f"{x['runs'][0]} against {x['feasible'][1]} of {x['runs'][1]}; median true t80 "
                             f"{_f(x['median_t80_min'][0])} against {_f(x['median_t80_min'][1])} min.")
            else:
                lines.append(f"- {a[0].upper() + a[1:]} against {b}: not decided ({x['note']}).")
        if "changed_cooling" in cr:
            x = cr["changed_cooling"]
            lines.append("- Changed cooling, adaptive (Muse) feasible in at least 2 of 3 where the fixed model is not: "
                         + (("met" if x["met"] else "not met") + f" (adaptive feasible {x['adaptive_feasible']}, fixed "
                            f"model infeasible {x['fixed_infeasible']})." if x["decided"] else f"not decided ({x['note']})."))
        verdict = lambda x: ("met" if x["met"] else "not met") if x["decided"] else f"not decided, {x['note']}"  # noqa: E731
        for arm, x in (cr.get("no_gratuitous_change") or {}).items():
            lines.append(f"- {ARM_LABEL[arm]}: final design within one {STEP_C:g}C step of the nominal design in at "
                         f"least 2 of 3: {verdict(x)} ({x['gratuitous_changes']} of {x['runs']} changed more).")
        for arm, x in (cr.get("thermal_mass_handled") or {}).items():
            lines.append(f"- {ARM_LABEL[arm]}: final design under an adequate model version and feasible in at least "
                         f"2 of 3: {verdict(x)} (adequate {x['final_version_adequate']}, feasible {x['feasible']} of "
                         f"{x['runs']}).")
        for arm, x in (cr.get("stops_learning") or {}).items():
            lines.append(f"- {ARM_LABEL[arm]}: stops learning (at most {U_TESTS_AFTER_FIT} lab test after the first "
                         f"fit) with a feasible design in at least 2 of 3: {verdict(x)} (stopped "
                         f"{x['stopped_learning']}, stopped and feasible {x['stopped_and_feasible']} of {x['runs']}; "
                         f"median lab tests after the first fit {_f(x['median_lab_tests_after_first_fit'], '{:g}')}).")
        for arm, x in (cr.get("inadequate_fit_flagged") or {}).items():
            lines.append(f"- {ARM_LABEL[arm]}: an inadequate fit flagged in at least 2 of 3: {verdict(x)} "
                         f"({x['flagged']} of {x['runs']}).")
        lines.append("")
    probs = [(r["run"], p) for r in runs for p in r.get("problems") or []]
    if probs:
        lines += ["## Run problems", "", *[f"- {name}: {p}" for name, p in probs], ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ the command line

def _run_job(args: tuple) -> tuple[str, str]:
    out, case, arm, n, live, cap = args
    name = run_name(case, arm, n)
    try:
        info = run_one(Path(out), case, arm, n, live=live, cap_usd=cap, log=lambda *_: None)
    except HarnessError as e:
        return name, f"refused: {e}"
    return name, f"{info['stop']} ({info['harness_wall_s']:.0f} s)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.fastcharge_lab", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for name, text in (("plan", "list the runs a run command would start, with budgets; runs nothing"),
                       ("run", "run arms; the model arms need --live and --cap-usd (paid calls)")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--out", type=Path, required=True, help="runs folder, e.g. runs/fastcharge-lab")
        p.add_argument("--cases", nargs="+", choices=sorted(CASES), default=list(CASES))
        p.add_argument("--arms", nargs="+", choices=ARMS, default=list(NUMERIC_ARMS))
        p.add_argument("--repeats", nargs="+", type=int, choices=REPEATS, default=list(REPEATS))
        if name == "run":
            p.add_argument("--workers", type=int, default=1, help="campaigns at once (each solver action forks "
                                                                  "up to six processes)")
            p.add_argument("--live", action="store_true", help="required for the model arms: paid Muse calls")
            p.add_argument("--cap-usd", type=float, help="required for the model arms: total model-spend limit")
    p = sub.add_parser("report", help="metrics from the run folders: results.json and the markdown report")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--md", type=Path, default=REPORT_MD)
    args = ap.parse_args(argv)
    if args.command == "report":
        data = report(args.out, args.md)
        print(f"{len(data['runs'])} runs -> {args.out / 'results.json'} and {args.md}")
        return 0
    jobs = planned(args.cases, args.arms, args.repeats)
    if args.command == "plan":
        worst = sum(MODEL_USD for _, arm, _ in jobs if arm in MODEL_ARMS)
        for case, arm, n in jobs:
            name = run_name(case, arm, n)
            done = (args.out / name / "run.json").is_file()
            print(f"{name}: {'done' if done else 'to run'}; {'paid' if arm in MODEL_ARMS else 'no model calls'}")
        print(f"{len(jobs)} runs; worst-case model spend ${worst:.2f}")
        return 0
    if any(a in MODEL_ARMS for a in args.arms):
        try:
            for a in args.arms:
                check_live(a, args.live, args.cap_usd)
        except HarnessError as e:
            ap.error(str(e))
    args.out.mkdir(parents=True, exist_ok=True)
    todo = [(str(args.out), c, a, n, args.live, args.cap_usd) for c, a, n in jobs
            if not (args.out / run_name(c, a, n) / "run.json").is_file()]
    failed = 0
    if args.workers <= 1 or any(a in MODEL_ARMS for _, _, a, *_ in todo):
        results = map(_run_job, todo)               # model arms one at a time: the spend check reads, not locks
    else:
        pool = cf.ProcessPoolExecutor(args.workers)
        results = pool.map(_run_job, todo)
    for name, status in results:
        print(f"{name}: {status}", flush=True)
        failed += status.startswith("refused")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
