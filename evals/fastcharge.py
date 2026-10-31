"""Fast-charge loop evaluation harness: runs the arms of the frozen protocol (evals/FASTCHARGE-PROTOCOL.md) and
computes its metrics from the run ledgers.

    python -m evals.fastcharge run --records <dir> --out runs/fastcharge [--case A B C] \
        [--arm loop no_feedback bo passive reference] [--repeat N] [--seed N] [--live]
    python -m evals.fastcharge report --out runs/fastcharge [--md evals/FASTCHARGE.md]

One ledger per run, at <out>/<case>-<arm>-<repeat or seed>/ledger, so each run verifies on its own with
`translucent verify`. Every run imports the Chen 2020 LG M50 records (their sha256 pinned by
scripts/fetch_lgm50_data.py) and registers, as the engineer, the case's objective with the failing baseline
re-run on the solver and an auto-approval rule for the computational actions up to the experiment budget. Turns run
in this process (loop_planner.run_turn), and the runner executes one request at a time (LoopRunner.tick, limit 1).

Arms: loop (planner kind muse), no_feedback (muse_no_feedback), bo (domain_baseline: ConstrainedBO, seeds 0-4),
passive (imports and analyses the records; no model, no solver) and reference (the 0.7C row, one solver run).
The model arms run only with --live and make paid calls to Muse Wonder on the meta profile. Before every provider
call the harness adds the spend recorded in every run ledger under <out> (and the counterfactual copies) to the
call's worst case, and stops the turn with status `global_budget` if the sum would pass $25.

Case B: after the fourth completed experiment the harness, as the engineer, amends the plating margin to +25 mV with
the protocol's text. For a loop run it first copies the ledger (SQLite backup plus blobs) to
<run>/counterfactual/ledger and takes one planner turn there without the amendment; the replay compares that turn's
proposal with the first proposal after the amendment in the run itself.

`report` reads only the run ledgers (the folder name gives case, arm and repeat) and writes <out>/results.json and
the markdown report. Run the model arms one process at a time: the $25 check reads the ledgers, it does not lock.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import runpy
import shutil
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from translucent import chain, datasets
from translucent.intervention_chat import ChatSession, Settings, StopCampaign
from translucent.ledger import Ledger
from translucent.loop import (PENDING, REGISTRATION, actor_kind, baseline_request_id, campaign_ids,
                              current_objective, request_states)
from translucent.loop_planner import (LENGTH_MAX_TOKENS, MAX_TOKENS, STEP_CAP, DomainBaselinePlanner,
                                      fastcharge_objective, model_spend, next_turn, run_turn, turn_due)
from translucent.loop_runner import LoopRunner
from translucent.loop_service import Actor, LoopService
from translucent.systems import lgm50_fastcharge as fc

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MANIFEST = ROOT / "data" / "chen2020-lgm50" / "manifest.json"
FETCH_SCRIPT = ROOT / "scripts" / "fetch_lgm50_data.py"
PROTOCOL = HERE / "FASTCHARGE-PROTOCOL.md"
REPORT_MD = HERE / "FASTCHARGE.md"

ENGINEER = "engineer"
PERSON = Actor("person", ENGINEER, "cli")
EXPERIMENTS = 8                       # computational experiments per search run, calibration included
MODEL_USD = 1.50                      # model budget per model run
TOTAL_USD = 25.0                      # model spend of the whole evaluation
REPEATS = (1, 2, 3)                   # loop and no-feedback
SEEDS = (0, 1, 2, 3, 4)               # Bayesian optimisation
AMEND_AFTER = 4                       # case B: the amendment lands after this many completed experiments
AMENDED_MARGIN = 25.0
AMENDMENT_TEXT = ("Raise the plating margin to +25 mV. The model's voltage gap to the records is 70–160 mV, so "
                  "+10 mV is not a safe margin.")
ANSWER = ("No person answers questions during this evaluation. Decide from the campaign state and the evidence "
          "you have.")
MAX_ANSWERS = 3                       # questions answered per run; later ones stay open
MAX_STEPS = 200                       # harness iterations per run (a guard; a run ends long before)
REFERENCE_C = 0.7                     # the manufacturer's maximum, the reference row
NOMINAL_AH = fc.CAPACITY_AH

ARMS = ("loop", "no_feedback", "bo", "passive", "reference")
MODEL_ARMS = ("loop", "no_feedback")
SEARCH_ARMS = ("loop", "no_feedback", "bo")
KIND = {"loop": "muse", "no_feedback": "muse_no_feedback", "bo": "domain_baseline", "passive": "external",
        "reference": "external"}
ARM_LABEL = {"loop": "Loop", "no_feedback": "Same model, no feedback", "bo": "Constrained BO",
             "passive": "Passive pipeline", "reference": "Reference row (0.7C)"}


@dataclass(frozen=True)
class Case:
    name: str
    ambient: float
    margin: float
    amended_margin: float | None
    t_limit: float
    reference_t80: float                       # reference optimum, min
    reference_protocol: tuple[float, ...]
    single_step_c: float                       # best feasible single step
    single_step_t80: float
    extrapolation: bool = False

    @property
    def final_margin(self) -> float:
        return self.amended_margin if self.amended_margin is not None else self.margin


CASES = {
    "A": Case("A", 25.0, 10.0, None, 45.0, 33.55, (1.5, 1.5, 1.05, 1.1), 1.05, 40.0),
    "B": Case("B", 25.0, 10.0, AMENDED_MARGIN, 45.0, 37.13, (1.5, 1.5, 1.05, 0.8), 0.60, 70.0),
    "C": Case("C", 15.0, 10.0, None, 45.0, 34.13, (1.5, 1.5, 1.35, 0.85), 0.55, 76.4, extrapolation=True),
}
CRITERIA = {"A": {"within_min": 1.0}, "B": {"within_min": 1.5, "replay_passes": 2}}


class HarnessError(RuntimeError):
    """The harness refuses to start a run, with the reason."""


# ------------------------------------------------------------------ run names and folders

def run_name(case: str, arm: str, n: int) -> str:
    return f"{case}-{arm}-{n}"


def parse_name(name: str) -> tuple[str, str, int] | None:
    case, _, rest = name.partition("-")
    arm, _, n = rest.rpartition("-")
    if case not in CASES or arm not in ARMS or not n.isdigit():
        return None
    return case, arm, int(n)


def run_dirs(out: Path) -> list[Path]:
    return sorted(p for p in Path(out).iterdir() if p.is_dir() and parse_name(p.name) and
                  (p / "ledger" / "ledger.sqlite").is_file()) if Path(out).is_dir() else []


def planned(cases, arms, repeat: int | None = None, seed: int | None = None) -> list[tuple[str, str, int]]:
    out = []
    for case in cases:
        for arm in arms:
            if arm in MODEL_ARMS:
                ns = [repeat] if repeat is not None else list(REPEATS)
            elif arm == "bo":
                ns = [seed] if seed is not None else list(SEEDS)
            else:
                ns = [1]
            out += [(case, arm, n) for n in ns]
    return out


# ------------------------------------------------------------------ the records and the objective

def check_records(records: Path) -> None:
    """The three Chen 2020 files, byte for byte the published ones (sha256 pinned by the fetch script)."""
    pins = runpy.run_path(str(FETCH_SCRIPT))["FILES"]
    for name, (size, sha) in pins.items():
        path = Path(records) / name
        if not path.is_file():
            raise HarnessError(f"{name} is not in {records}; fetch it with scripts/fetch_lgm50_data.py")
        if path.stat().st_size != size or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise HarnessError(f"{path} is not the published {name} (its sha256 differs from the pinned one)")


def check_calibration_records() -> None:
    """calibrate_thermal runs in a solver child that reads the records from the adapter's own folder, not from
    --records. A model arm may ask for it, so its records must be there."""
    try:
        check_records(fc.DATA)
    except HarnessError as e:
        raise HarnessError(f"calibrate_thermal reads the records from {fc.DATA}: {e}") from None


def manifest_for(case: Case) -> dict:
    """The import manifest. The failing baseline record runs at the case's ambient: in case C (15 degC) it is a copy
    of the manifest's baseline with the ambient changed, under its own dataset and record ids."""
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    base = next(r for r in m["records"] if r["role"] == "baseline")
    if float(base["configuration"]["ambient"]) == case.ambient:
        return m
    tag = f"{case.ambient:g}c".replace(".", "p")
    m["id"] = f"{m['id']}-{tag}"
    m["title"] += f"; failing baseline at {case.ambient:g} degC ambient"
    base["id"] = f"{base['id']}-{tag}"
    base["configuration"]["ambient"] = case.ambient
    base["provenance"]["notes"] = (f"The failing protocol at {case.ambient:g} degC ambient (case {case.name}). "
                                   "No measurement exists for it; its outcome comes from the solver.")
    return m


def statement(case: Case) -> str:
    text = (f"Charge the LG M50 from 10 to 80 % faster than the failing 1.5C four-step baseline while the anode "
            f"potential stays at or above +{case.margin:g} mV vs Li/Li+ and the cell at or below {case.t_limit:g} "
            f"degC. The ambient is {case.ambient:g} degC: every simulated protocol runs at ambient = "
            f"{case.ambient:g} degC.")
    if case.extrapolation:
        text += (" This ambient is outside the parameter set's fitted range, so every result is an "
                 "extrapolation.")
    return text


def objective_for(case: Case, arm: str, dataset_id: str, baseline_ref: str) -> dict:
    model = arm in MODEL_ARMS
    experiments = EXPERIMENTS if arm in SEARCH_ARMS else (1 if arm == "reference" else 0)
    data = fastcharge_objective(kind=KIND[arm], profile="meta" if model else None,
                                model_usd=MODEL_USD if model else 0.0, experiments=experiments,
                                registered_by=ENGINEER)
    data["statement"] = statement(case)
    data["datasets"] = [dataset_id]
    data["baseline"] = [baseline_ref]
    data["baseline_simulation"] = arm != "passive"
    data["auto_approve"] = [{"executor": "computational", "max_usd": 0.0, "max_wall_s": 300.0,
                             "max_count": experiments}] if experiments else []
    return data


def amended_constraints(case: Case) -> list[dict]:
    return [{"quantity": "min_anode_potential", "op": ">=", "limit": case.amended_margin, "unit": "mV",
             "label": f"Plating margin: anode potential at or above +{case.amended_margin:g} mV"},
            {"quantity": "max_temperature", "op": "<=", "limit": case.t_limit, "unit": "degC",
             "label": f"Cell at or below {case.t_limit:g} degC"}]


def open_run(root: Path, case: Case, arm: str, records: Path, name: str) -> tuple[Ledger, str]:
    L = Ledger(root)
    ds = datasets.import_dataset(L, manifest_for(case), records)
    ref = next(r.id for r in ds.records if r.role == "baseline")
    obj = objective_for(case, arm, ds.id, f"{ds.id}/{ref}")
    cid = LoopService(L, PERSON, idempotency_key=name).open_campaign(
        obj, f"Fast-charge evaluation, case {case.name}, {ARM_LABEL[arm]}", memory="off")["campaign_id"]
    return L, cid


# ------------------------------------------------------------------ the global $25 limit

def _db_rows(db: Path, sql: str) -> list:
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def ledger_spend(db: Path, after_seq: int = 0) -> float:
    """Model dollars recorded by planner model_request receipts in one ledger file (seq > after_seq)."""
    rows = _db_rows(db, "SELECT json_extract(body,'$.seq'), json_extract(body,'$.data.payload.cost_usd') FROM record "
                        "WHERE kind='loop' AND json_extract(body,'$.type')='planner' "
                        "AND json_extract(body,'$.data.phase')='model_request'")
    return math.fsum(float(c or 0.0) for s, c in rows if s > after_seq)


def amendment_seq(db: Path) -> int | None:
    rows = _db_rows(db, "SELECT json_extract(body,'$.seq') FROM record WHERE kind='loop' "
                        "AND json_extract(body,'$.type')='amendment' ORDER BY json_extract(body,'$.seq') LIMIT 1")
    return rows[0][0] if rows else None


def counterfactual_spend(run_dir: Path) -> float:
    """Spend of the counterfactual turn alone: the copy also holds the prefix's receipts, already counted in the
    run's own ledger. The prefix ends just before the run's amendment; without one, the whole copy counts."""
    copy = run_dir / "counterfactual" / "ledger" / "ledger.sqlite"
    if not copy.is_file():
        return 0.0
    seq = amendment_seq(run_dir / "ledger" / "ledger.sqlite")
    return ledger_spend(copy, 0 if seq is None else seq - 1)


class GlobalCap:
    """The evaluation's model-spend limit, read from every run ledger under `out` before each provider call."""

    def __init__(self, out: Path, limit_usd: float = TOTAL_USD):
        self.out, self.limit_usd = Path(out), float(limit_usd)

    def spent(self) -> float:
        """Every run's model spend, including runs set aside under <out>/void/ (they are not reported, but what they
        cost still counts toward the limit)."""
        total = 0.0
        dirs = [p for base in (self.out, self.out / "void") if base.is_dir() for p in base.iterdir() if p.is_dir()]
        for d in sorted(d for d in dirs if d.name != "void"):
            db = d / "ledger" / "ledger.sqlite"
            if db.is_file():
                total += ledger_spend(db)
            total += counterfactual_spend(d)
        return total


class CappedSession(ChatSession):
    """A metered ChatSession that refuses a provider call (each retry included) that would take the evaluation's
    recorded spend past the global limit. The turn records the stop as status `global_budget`."""

    def __init__(self, settings: Settings, store: Path, *, cap: GlobalCap, client=None):
        super().__init__(settings, store, client=client)
        self.cap = cap
        self._chat_start = 0.0

    def chat(self, messages, tools=None):
        self._chat_start = self.spent_usd           # earlier calls of this turn are in the ledger by now
        return super().chat(messages, tools)

    def _live(self, request, worst):
        spent = self.cap.spent() + (self.spent_usd - self._chat_start)
        if spent + worst > self.cap.limit_usd + 1e-12:
            raise StopCampaign("global_budget",
                               f"The evaluation's model spend is ${spent:.4f}; the next call reserves ${worst:.4f}, "
                               f"which would pass the ${self.cap.limit_usd:.2f} limit")
        return super()._live(request, worst)


def model_session_for(cap: GlobalCap, client_factory: Callable | None = None):
    """loop_planner.default_session_for(live=True), metered on the meta profile, with the global limit. A client
    factory replaces the provider client (tests: ScriptedChatClient); None means the real paid client."""
    def session_for(L: Ledger, cid: str, obj):
        left = float(obj.budget.get("model_usd", 0.0)) - model_spend(L, cid)
        if left <= 0:
            return None
        settings = Settings(profile=obj.planner.get("profile") or "meta", budget_usd=left, max_tokens=MAX_TOKENS,
                            step_cap=STEP_CAP, length_max_tokens=LENGTH_MAX_TOKENS)
        store = Path(L.root) / "planner" / cid / f"turn-{next_turn(L, cid)}"
        return CappedSession(settings, store, cap=cap, client=client_factory() if client_factory else None)
    return session_for


# ------------------------------------------------------------------ driving a run

def search_requests(states) -> list:
    return [s for s in states.values() if s.proposed_by != REGISTRATION]


def completed_experiments(L: Ledger, cid: str) -> int:
    return sum(s.result is not None for s in search_requests(request_states(L, cid)))


def computational_used(states) -> int:
    return sum(s.executor == "computational" and s.state not in ("rejected", "cancelled")
               for s in search_requests(states))


def answer_questions(L: Ledger, cid: str) -> int:
    """Answer open planner questions with the fixed text, as the engineer (at most MAX_ANSWERS per run)."""
    events = L.events(cid)
    answered = {str((e.data or {}).get("question_id")) for e in events if e.type == "question.answered"}
    given = len(answered)
    n = 0
    for e in events:
        if e.type == "question.asked" and str(e.seq) not in answered and given < MAX_ANSWERS:
            L.emit(cid, "question.answered", PERSON.label, f"{ENGINEER} answered: {ANSWER}", detail=ANSWER,
                   data={"question_id": str(e.seq), "text": ANSWER, "author": ENGINEER})
            given += 1
            n += 1
    return n


def amend(L: Ledger, cid: str, case: Case) -> None:
    LoopService(L, PERSON).amend(cid, AMENDMENT_TEXT, constraints=amended_constraints(case))


def copy_ledger(L: Ledger, dest: Path) -> None:
    """A consistent copy of the ledger: the SQLite backup API for the database, hard links (or copies) for the
    content-addressed blobs and the planner tapes. Locks and work folders are not copied."""
    dest.mkdir(parents=True, exist_ok=False)
    target = sqlite3.connect(dest / "ledger.sqlite")
    try:
        L.db.backup(target)
    finally:
        target.close()

    def link(src, dst):
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
    for sub in ("blobs", "planner"):
        if (Path(L.root) / sub).is_dir():
            shutil.copytree(Path(L.root) / sub, dest / sub, copy_function=link)


def drive(L: Ledger, cid: str, case: Case, session_for, *, before_amendment=None, log=print) -> dict:
    """Run the campaign's turns and its queued requests in this process until it concludes, the budget is spent or
    the planner stops. Case B: the engineer's amendment lands right after the fourth completed experiment, and the
    next turn runs before any further request executes. Returns {"stop", "turns", "runner_problems"}."""
    runner = LoopRunner(L.root, process="cli", workers=1)
    turns, problems = [], []
    amended = any(r.type == "amendment" for r in chain.records(L, cid))
    for _ in range(MAX_STEPS):
        while True:
            advanced = runner.tick(cid, limit=1)
            problems += runner.problems
            runner.problems = []
            if not advanced:
                break
            if case.amended_margin is not None and not amended and completed_experiments(L, cid) >= AMEND_AFTER:
                if before_amendment is not None:
                    before_amendment(L, cid)
                amend(L, cid, case)
                amended = True
                log(f"  amendment: plating margin +{case.amended_margin:g} mV")
                break
        if chain.concluded(L, cid):
            return {"stop": "concluded", "turns": turns, "runner_problems": problems}
        answer_questions(L, cid)
        if turn_due(L, cid):
            session = session_for(L, cid, current_objective(L, cid))
            if session is None:
                return {"stop": "model budget spent", "turns": turns, "runner_problems": problems}
            result = run_turn(L, cid, session, lessons=None, only_if_due=True)
            if result is not None:
                turns.append(result)
                log(f"  turn {result['turn']}: {result['status']} ({len(result.get('proposed') or [])} proposed)")
                if result["status"] == "busy":
                    return {"stop": "another process holds the turn lock", "turns": turns,
                            "runner_problems": problems}
            if chain.concluded(L, cid):
                return {"stop": "concluded", "turns": turns, "runner_problems": problems}
            continue
        states = request_states(L, cid)
        if any(s.executor == "computational" and s.state in ("queued", "running") for s in states.values()):
            continue
        last = turns[-1] if turns else None
        if computational_used(states) >= int(current_objective(L, cid).budget.get("experiments") or 0):
            stop = "experiment budget spent"
        elif last is not None:
            stop = f"planner stopped: {last['status']}: {last['reason']}"
        else:
            stop = "nothing to do"
        return {"stop": stop, "turns": turns, "runner_problems": problems}
    return {"stop": f"harness step cap of {MAX_STEPS} reached", "turns": turns, "runner_problems": problems}


def bo_session_for(case: Case, seed: int):
    def session_for(L, cid, obj):
        return DomainBaselinePlanner(L, cid, policy=fc.ConstrainedBO(seed=seed, ambient=case.ambient,
                                                                     margin_mV=case.margin, t_limit_C=case.t_limit))
    return session_for


def counterfactual_hook(run_dir: Path, session_for, log=print):
    """Case B, loop arm: before the amendment, copy the ledger and take one planner turn in the copy."""
    def hook(L: Ledger, cid: str):
        dest = run_dir / "counterfactual" / "ledger"
        copy_ledger(L, dest)
        C = Ledger(dest)
        try:
            session = session_for(C, cid, current_objective(C, cid))
            if session is None:
                log("  counterfactual: no model budget left; no turn")
                return
            result = run_turn(C, cid, session, lessons=None, only_if_due=True)
            log(f"  counterfactual turn: {result and result['status']}")
        finally:
            C.db.close()
    return hook


def run_one(out: Path, case_name: str, arm: str, n: int, records: Path, *, live: bool = False,
            client_factory: Callable | None = None, check_pins: bool = True, log=print) -> dict:
    """One run of one arm in one case, in <out>/<case>-<arm>-<n>. Model arms refuse without `live`."""
    case = CASES[case_name]
    if arm not in ARMS:
        raise HarnessError(f"unknown arm {arm}")
    if arm in MODEL_ARMS and not live:
        raise HarnessError(f"the {arm} arm calls a paid model: run it with --live")
    name = run_name(case_name, arm, n)
    run_dir = Path(out) / name
    if (run_dir / "run.json").is_file():
        raise HarnessError(f"{name} has already run")
    if run_dir.exists():
        raise HarnessError(f"{name} was interrupted: its folder exists without run.json; remove it to run again")
    if check_pins:
        check_records(records)
    cap = GlobalCap(out)
    if arm in MODEL_ARMS:
        if check_pins and client_factory is None:
            check_calibration_records()
        if (spent := cap.spent()) >= TOTAL_USD:
            raise HarnessError(f"the evaluation has spent ${spent:.4f} of ${TOTAL_USD:.2f}; {name} is not started")
    started = time.time()
    run_dir.mkdir(parents=True)
    L, cid = open_run(run_dir / "ledger", case, arm, records, name)
    try:
        if arm == "passive":
            outcome = run_passive(L, cid)
        elif arm == "reference":
            outcome = run_reference(L, cid, case)
        else:
            if arm == "bo":
                session_for = bo_session_for(case, n)
            else:
                session_for = model_session_for(cap, client_factory)
            hook = counterfactual_hook(run_dir, session_for, log) if (arm == "loop" and
                                                                      case.amended_margin is not None) else None
            outcome = drive(L, cid, case, session_for, before_amendment=hook, log=log)
    finally:
        L.db.close()
    info = {"run": name, "case": case_name, "arm": arm, "n": n, "campaign_id": cid, "live": live,
            "started_at": started, "ended_at": time.time(), "harness_wall_s": round(time.time() - started, 1),
            **{k: v for k, v in outcome.items() if k != "turns"},
            "turns": [{k: t.get(k) for k in ("turn", "status", "reason", "proposed", "spent_usd")}
                      for t in outcome.get("turns", [])]}
    (run_dir / "run.json").write_text(json.dumps(info, indent=1, sort_keys=True), encoding="utf-8")
    return info


# ------------------------------------------------------------------ the passive pipeline and the reference row

def charge_analysis(L: Ledger, dataset_id: str) -> dict:
    """The records' charges, measured: each constant-current charge step (current steady, voltage rising), its
    C-rate against the nominal 5.0 Ah and its time from 10 to 80 % state of charge, coulomb counted from the start
    of the charge (every charge in these records starts from a discharge to 2.5 V)."""
    ds = L.get("dataset", dataset_id)
    charges = []
    for rec in ds.records:
        have = {c.quantity for c in rec.channels}
        if not {"lab:step", "lab:current_magnitude", "lab:step_capacity", "voltage"} <= have:
            continue
        s = {q: datasets.series(L, dataset_id, rec.id, q)[1] for q in
             ("lab:step", "lab:current_magnitude", "lab:step_capacity", "voltage")}
        surf = datasets.series(L, dataset_id, rec.id, "surface_temperature")[1] \
            if "surface_temperature" in have else None
        t, step = s["lab:step"]["t"], s["lab:step"]["y"]
        i = 0
        while i < len(t):
            j = i
            while j + 1 < len(t) and step[j + 1] == step[i]:
                j += 1
            current = s["lab:current_magnitude"]["y"][i:j + 1]
            volts = s["voltage"]["y"][i:j + 1]
            q = s["lab:step_capacity"]["y"][i:j + 1]
            tt = t[i:j + 1]
            mid = statistics.median(current) if current else 0.0
            steady = mid > 0.05 and (max(current) - min(current)) <= 0.05 * mid
            lo, hi = 0.10 * NOMINAL_AH, 0.80 * NOMINAL_AH
            if steady and volts[-1] > volts[0] and q and q[0] <= lo and q[-1] >= hi:
                def crossing(level):
                    k = next(k for k, v in enumerate(q) if v >= level)
                    if k == 0 or q[k] == q[k - 1]:
                        return tt[k]
                    return tt[k - 1] + (level - q[k - 1]) * (tt[k] - tt[k - 1]) / (q[k] - q[k - 1])
                t10, t80 = crossing(lo), crossing(hi)
                peak = max((y for x, y in zip(surf["t"], surf["y"]) if tt[0] <= x <= tt[-1]), default=None) \
                    if surf else None
                charges.append({"record": rec.id, "step": step[i], "current_A": round(mid, 4),
                                "c_rate": round(mid / NOMINAL_AH, 3), "t10_80_min": round((t80 - t10) / 60, 2),
                                "peak_surface_degC": None if peak is None else round(peak, 2)})
            i = j + 1
    if not charges:
        return {"charges": [], "best": None}
    fastest = max(round(c["c_rate"] / fc.C_GRID) * fc.C_GRID for c in charges)
    at = [c for c in charges if abs(round(c["c_rate"] / fc.C_GRID) * fc.C_GRID - fastest) < 1e-9]
    best = {"c_rate": round(fastest, 2), "t10_80_min": round(statistics.median(c["t10_80_min"] for c in at), 2),
            "charges": len(at), "records": sorted({c["record"] for c in at}),
            "peak_surface_degC": max((c["peak_surface_degC"] for c in at if c["peak_surface_degC"] is not None),
                                     default=None)}
    return {"charges": charges, "best": best}


def run_passive(L: Ledger, cid: str) -> dict:
    """Import (done) and analyse the records; no experiment, no model, no solver. The answer is recorded as the
    engineer's conclusion; its numbers are recomputed from the imported records by `report`."""
    obj = current_objective(L, cid)
    analysis = charge_analysis(L, obj.datasets[0])
    best = analysis["best"]
    if best is None:
        text = "The records hold no constant-current charge from 10 to 80 %; they support no charge protocol."
    else:
        text = (f"The records support {best['c_rate']:g}C constant current then constant voltage: "
                f"{best['t10_80_min']:g} min from 10 to 80 % (median of {best['charges']} measured charges). No record "
                "holds a faster charge and none measures the anode potential.")
    LoopService(L, PERSON).conclude(cid, text, ["no experiment was run: the passive pipeline only analyses the "
                                                "records"])
    return {"stop": "concluded"}


def run_reference(L: Ledger, cid: str, case: Case) -> dict:
    """The 0.7C reference row: the engineer requests 0.7C in every window at the case's ambient (auto-approved),
    the runner executes it and the failing baseline's re-run, and the engineer concludes."""
    params = {c: REFERENCE_C for c in fc.C_RATES} | {"ambient": case.ambient}
    LoopService(L, PERSON).propose(cid, "simulate_protocol", params,
                                   rationale="The manufacturer's maximum charge rate, as a reference row.",
                                   prediction={}, expected_effect="None: a reference row, not a competitor.")
    runner = LoopRunner(L.root, process="cli", workers=1)
    while runner.tick(cid, limit=1):
        pass
    problems = runner.problems
    LoopService(L, PERSON).conclude(cid, "Reference row: 0.7C constant current then constant voltage, simulated.")
    return {"stop": "concluded", "runner_problems": problems}


# ------------------------------------------------------------------ metrics, from the ledger alone

def _num(L: Ledger, result_rec, q: str):
    mid = result_rec.data["measurements"].get(q)
    m = L.get("measurement", mid) if mid else None
    return None if m is None else m.value


def _limits_at(recs, seq: int) -> tuple[float, float]:
    """(plating margin, temperature limit) of the objective version in force at `seq`."""
    margin = t_limit = None
    for r in recs:
        if r.seq > seq:
            break
        if r.type in ("objective", "amendment") and r.data.get("constraints") is not None:
            for c in r.data["constraints"]:
                if c["quantity"] == "min_anode_potential":
                    margin = float(c["limit"])
                elif c["quantity"] == "max_temperature":
                    t_limit = float(c["limit"])
    return margin, t_limit


def _raw_ok(sc: dict, margin: float, t_limit: float) -> bool:
    return sc.get("status") == "ok" and sc["min_anode_potential"] is not None and \
        sc["max_temperature"] is not None and sc["min_anode_potential"] >= margin and sc["max_temperature"] <= t_limit


def _turns(recs) -> list[dict]:
    out = {}
    for r in recs:
        if r.type != "planner":
            continue
        p = r.data["payload"]
        if r.data["phase"] == "turn_started":
            out[p["turn"]] = {"turn": p["turn"], "started": r.seq, "finished": None, "status": None, "reason": None}
        elif r.data["phase"] == "turn_finished" and p.get("turn") in out:
            out[p["turn"]] |= {"finished": r.seq, "status": p.get("status"), "reason": p.get("reason")}
    return list(out.values())


def turn_proposals(recs, after_seq: int) -> dict | None:
    """The first planner turn started after `after_seq` and the requests it proposed, in order."""
    turn = next((t for t in _turns(recs) if t["started"] > after_seq), None)
    if turn is None:
        return None
    end = turn["finished"] if turn["finished"] is not None else math.inf
    props = [{"action": r.data["action"], "params": dict(r.data["params"]), "prediction": dict(r.data["prediction"])}
             for r in recs if r.type == "request" and r.data["version"] == 1 and actor_kind(r.actor) == "agent"
             and turn["started"] < r.seq < end]
    return {"turn": turn["turn"], "status": turn["status"], "proposals": props}


def _same(a: dict, b: dict) -> bool:
    if a["action"] != b["action"]:
        return False
    try:
        return fc.canonical(a["action"], a["params"]) == fc.canonical(b["action"], b["params"])
    except (KeyError, TypeError, ValueError):
        return a["params"] == b["params"]


def replay_result(run_dir: Path) -> dict:
    """Case B counterfactual: the first proposal after the amendment (run ledger) against the first proposal of the
    turn taken without it (the copy). Passes when they differ and the amended proposal predicts a lowest anode
    potential of at least the amended margin."""
    copy = run_dir / "counterfactual" / "ledger"
    if not (copy / "ledger.sqlite").is_file():
        return {"ran": False, "passes": False, "reason": "no counterfactual copy was made"}
    L, C = Ledger(run_dir / "ledger"), Ledger(copy)
    try:
        cid = campaign_ids(L)[0]
        recs, crecs = chain.records(L, cid), chain.records(C, cid)
        amendment = next((r for r in recs if r.type == "amendment"), None)
        if amendment is None:
            return {"ran": False, "passes": False, "reason": "the run has no amendment"}
        with_ = turn_proposals(recs, amendment.seq)
        without = turn_proposals(crecs, amendment.seq - 1)
    finally:
        L.db.close()
        C.db.close()
    a = (with_ or {}).get("proposals") or []
    b = (without or {}).get("proposals") or []
    pred = (a[0]["prediction"].get("min_anode_potential") or {}).get("value") if a else None
    differ = bool(a and b) and not _same(a[0], b[0])
    passes = differ and isinstance(pred, (int, float)) and pred >= AMENDED_MARGIN
    reason = ("no proposal in the turn after the amendment" if not a else
              "no proposal in the turn without the amendment" if not b else
              "the two proposals are the same" if not differ else
              "the amended proposal predicts no lowest anode potential" if pred is None else
              f"the amended proposal predicts {pred:g} mV, below +{AMENDED_MARGIN:g} mV" if not passes else "")
    return {"ran": True, "passes": passes, "reason": reason, "with": a[0] if a else None,
            "without": b[0] if b else None, "with_turn_status": (with_ or {}).get("status"),
            "without_turn_status": (without or {}).get("status"),
            "predicted_min_anode_potential_mV": pred}


def _bounds_refusal(text: str) -> bool:
    return any(k in text for k in (" is above ", " is below ", " is not on the ", " is outside ", "must be in ["))


def run_metrics(run_dir: Path) -> dict:
    """Every metric of one run, from its ledger (and its counterfactual copy) alone."""
    case_name, arm, n = parse_name(run_dir.name)
    case = CASES[case_name]
    L = Ledger(run_dir / "ledger")
    try:
        cid = campaign_ids(L)[0]
        recs = chain.records(L, cid)
        obj, final = ObjectiveFirst(recs), current_objective(L, cid, recs)
        states = request_states(L, cid, recs)
        events = L.events(cid)
        margin_final, t_final = _limits_at(recs, recs[-1].seq)
        amendment = next((r for r in recs if r.type == "amendment"), None)
        out = {"run": run_dir.name, "case": case_name, "arm": arm, "n": n, "campaign_id": cid,
               "planner_kind": final.planner.get("kind"),
               "ambient_in_statement": f"ambient = {case.ambient:g} degC" in obj["statement"],
               "final_margin_mV": margin_final, "temperature_limit_degC": t_final}
        # the failing baseline's re-run, as every arm received it
        bst = states.get(baseline_request_id(cid, final.baseline[0])) if final.baseline else None
        out["baseline"] = None if bst is None or bst.result is None else {
            "params": dict(bst.data.params), "status": bst.result.data.get("categories", {}).get("status"),
            **{q: _num(L, bst.result, q) for q in ("charge_time_80", "min_anode_potential", "max_temperature")}}

        if arm == "passive":
            analysis = charge_analysis(L, final.datasets[0])
            done = chain.concluded(L, cid)
            out |= {"passive": analysis, "experiments": [], "model_usd": 0.0,
                    "conclusion": None if done is None else done.data["text"]}
            return out

        exps, seen_cfg = [], {}
        order = sorted(search_requests(states), key=lambda s: s.versions[0].seq)
        for k, st in enumerate(order, start=1):
            e = {"ordinal": k, "request_id": st.request_id, "action": st.action, "executor": st.executor,
                 "params": dict(st.data.params), "state": st.state, "proposed_seq": st.versions[0].seq,
                 "proposed_by": st.proposed_by, "approved_by": st.approved_by,
                 "attempts": st.attempt, "failed": st.failure is not None,
                 "abandoned": sum(x.data["phase"] == "abandoned" for x in st.executions),
                 "prediction": dict(st.data.prediction)}
            cfg = st.data.configuration_hash
            e["duplicate_of"] = seen_cfg.get(cfg)
            seen_cfg.setdefault(cfg, st.request_id)
            e["off_case"] = st.action == "simulate_protocol" and \
                abs(float(st.data.params.get("ambient", case.ambient)) - case.ambient) > 1e-9
            started = [x.seq for x in st.executions if x.data["phase"] == "started"]
            e["executed_seq"] = started[0] if started else None
            if st.result is not None:
                r = st.result
                e["result_seq"] = r.seq
                e["retried"] = (r.data.get("attempt") or 1) > 1
                if st.action == "simulate_protocol":
                    sc = {"status": r.data.get("categories", {}).get("status"),
                          **{q: _num(L, r, q) for q in ("charge_time_80", "min_anode_potential", "max_temperature")}}
                    m_at, t_at = _limits_at(recs, r.seq)
                    e["scalars"] = sc
                    e["margin_at_result_mV"] = m_at
                    e["feasible_at_time"] = fc.feasible(sc, m_at, t_at)
                    e["feasible_final"] = fc.feasible(sc, margin_final, t_final)
                    e["marginal_final"] = _raw_ok(sc, margin_final, t_final) and not e["feasible_final"]
                    e["marginal_at_time"] = _raw_ok(sc, m_at, t_at) and not e["feasible_at_time"]
                else:
                    e["scalars"] = {q: _num(L, r, q) for q in r.data["measurements"]}
            exps.append(e)
        out["experiments"] = exps

        comp = [e for e in exps if e["executor"] == "computational"]
        sims = [e for e in comp if e["action"] == "simulate_protocol" and "scalars" in e and not e["off_case"]]
        feas = [e for e in sims if e["feasible_final"]]
        best = min(feas, key=lambda e: (e["scalars"]["charge_time_80"], e["ordinal"])) if feas else None
        unresolved = []
        if best is None:
            unresolved.append("no protocol meets the feasibility rule within the budget")
            if any(e["marginal_final"] for e in sims):
                unresolved.append("the best candidate is marginal")
        elif best["retried"]:
            unresolved.append("the candidate needed a retry after a solver failure")
        candidate = best if not unresolved else None
        t80 = candidate["scalars"]["charge_time_80"] if candidate else None
        out["metric1"] = {"best_feasible_t80_min": t80,
                          "regret_min": None if t80 is None else round(t80 - case.reference_t80, 4),
                          "candidate_params": None if candidate is None else candidate["params"],
                          "candidate_scalars": None if candidate is None else candidate["scalars"],
                          "resolved": candidate is not None, "unresolved_reasons": unresolved}
        ran = sorted((e for e in comp if e["executed_seq"] is not None), key=lambda e: e["executed_seq"])
        before = next((i for i, e in enumerate(ran) if e in sims and e["feasible_final"]
                       and e["scalars"]["charge_time_80"] < case.single_step_t80), None)
        out["metric2"] = {"experiments_before_beating_single_step": before,
                          "single_step_t80_min": case.single_step_t80, "single_step_c": case.single_step_c}

        planner_results = [(r, next((c for c in recs if c.type == "planner" and c.data["phase"] == "tool_call"
                                     and c.data["payload"].get("turn") == r.data["payload"].get("turn")
                                     and c.data["payload"].get("step") == r.data["payload"].get("step")), None))
                           for r in recs if r.type == "planner" and r.data["phase"] == "tool_result"
                           and r.data["payload"].get("tool") == "propose_experiment"]
        errors = [str(r.data["payload"]["result"].get("error")) for r, _ in planner_results
                  if isinstance(r.data["payload"].get("result"), dict) and "error" in r.data["payload"]["result"]]
        merged = sum(bool((r.data["payload"].get("result") or {}).get("duplicate_of_pending"))
                     for r, _ in planner_results)
        out["metric3"] = {
            "infeasible_experiments": sum(not e["feasible_at_time"] for e in sims),
            "marginal_experiments": sum(e["marginal_at_time"] for e in sims),
            "duplicate_experiments": sum(e["duplicate_of"] is not None for e in exps),
            "duplicate_proposals_merged": merged,
            "refused_for_bounds": sum(_bounds_refusal(x) for x in errors),
            "refused_other": sum(not _bounds_refusal(x) for x in errors),
            "rejected": sum(r.type == "rejection" for r in recs),
            "off_case_ambient_experiments": sum(e["off_case"] for e in comp),
            "computational_experiments": len(comp), "completed": sum("scalars" in e for e in comp),
            "calibrations": sum(e["action"] == "calibrate_thermal" for e in comp)}

        times = [e.t for e in events]
        cf_usd = counterfactual_spend(run_dir)
        out["metric4"] = {"model_usd": round(model_spend(L, cid, recs), 6),
                          "counterfactual_model_usd": round(cf_usd, 6),
                          "wall_min": round((max(times) - min(times)) / 60, 2) if times else None,
                          "solver_failures": sum(r.type == "failure" for r in recs)
                          + sum(r.type == "execution" and r.data["phase"] == "abandoned" for r in recs)}
        if case.amended_margin is not None:
            after = [e for e in sims if amendment is not None and e["result_seq"] > amendment.seq]
            out["metric5"] = {"amended": amendment is not None,
                              "amended_after_completed": None if amendment is None else
                              sum(1 for e in exps if e.get("result_seq") and e["result_seq"] < amendment.seq),
                              "amendment_reason": None if amendment is None else amendment.data["reason"],
                              "infeasible_after_change": sum(not e["feasible_final"] for e in after),
                              "results_after_change": len(after),
                              "replay": replay_result(run_dir) if arm == "loop" else None}
        asked = [e for e in events if e.type == "question.asked"]
        answered = {str((e.data or {}).get("question_id")) for e in events if e.type == "question.answered"}
        physical_pending = sum(s.executor == "human" and s.state in PENDING for s in search_requests(states))
        burden = {"person_approvals": sum(r.type == "approval" and actor_kind(r.actor) == "person" for r in recs),
                  "amendments": sum(r.type == "amendment" and actor_kind(r.actor) == "person" for r in recs),
                  "answers": len(answered), "unanswered_questions": sum(str(e.seq) not in answered for e in asked),
                  "pending_physical_requests": physical_pending,
                  "auto_approval_rules_written": len(obj["auto_approve"])}
        burden["total"] = burden["person_approvals"] + burden["amendments"] + burden["answers"] + \
            burden["unanswered_questions"] + burden["pending_physical_requests"]
        out["metric6"] = burden
        out["physical_claim_unresolved"] = physical_pending > 0

        done = chain.concluded(L, cid)
        turns = _turns(recs)
        last = turns[-1] if turns else None
        if done is not None:
            stop = "concluded by the planner" if actor_kind(done.actor) == "agent" else "concluded by the engineer"
        elif last is not None and last["status"] in ("budget", "global_budget"):
            stop = f"{last['status']}: {last['reason']}"
        elif computational_used(states) >= int(final.budget.get("experiments") or 0):
            stop = "experiment budget spent; the planner did not conclude"
        elif last is not None:
            stop = f"planner stopped: {last['status']}"
        else:
            stop = "no planner turn"
        out["stop"] = stop
        out["turns"] = [{k: t[k] for k in ("turn", "status")} for t in turns]
        out["conclusion"] = None if done is None else {"outcome": done.data["outcome"],
                                                       "unresolved_reasons": done.data["unresolved_reasons"]}
        return out
    finally:
        L.db.close()


def ObjectiveFirst(recs) -> dict:
    return next(r.data for r in recs if r.type == "objective")


# ------------------------------------------------------------------ the report

def _median(values: list) -> float | None:
    """Median with None (no feasible result) as +inf; None when the median itself is infinite or nothing ran."""
    xs = [math.inf if v is None else float(v) for v in values]
    if not xs:
        return None
    m = statistics.median(xs)
    return None if math.isinf(m) else round(m, 4)


def _key(v):
    return math.inf if v is None else v


def compare_arms(loop: dict, other: dict) -> str:
    """'better', 'worse' or 'tie' for the loop against another arm: median best time, then median infeasible."""
    a, b = _key(loop["best_feasible_t80_min"]), _key(other["best_feasible_t80_min"])
    if abs(a - b) > 1e-9 if math.isfinite(a) and math.isfinite(b) else a != b:
        return "better" if a < b else "worse"
    ia, ib = loop["infeasible_experiments"], other["infeasible_experiments"]
    if ia is None or ib is None or ia == ib:
        return "tie"
    return "better" if ia < ib else "worse"


def summarise(runs: list[dict]) -> dict:
    cases = {}
    for name, case in CASES.items():
        rows = [r for r in runs if r["case"] == name]
        if not rows:
            continue
        arms = {}
        for arm in SEARCH_ARMS:
            rs = [r for r in rows if r["arm"] == arm]
            if not rs:
                continue
            m = {"runs": len(rs),
                 "best_feasible_t80_min": _median([r["metric1"]["best_feasible_t80_min"] for r in rs]),
                 "experiments_before_beating_single_step":
                     _median([r["metric2"]["experiments_before_beating_single_step"] for r in rs]),
                 "infeasible_experiments": _median([r["metric3"]["infeasible_experiments"] for r in rs]),
                 "duplicate_experiments": _median([r["metric3"]["duplicate_experiments"] for r in rs]),
                 "refused_for_bounds": _median([r["metric3"]["refused_for_bounds"] for r in rs]),
                 "model_usd": _median([r["metric4"]["model_usd"] for r in rs]),
                 "wall_min": _median([r["metric4"]["wall_min"] for r in rs]),
                 "solver_failures": _median([r["metric4"]["solver_failures"] for r in rs]),
                 "burden": _median([r["metric6"]["total"] for r in rs]),
                 "resolved_runs": sum(r["metric1"]["resolved"] for r in rs)}
            m["regret_min"] = None if m["best_feasible_t80_min"] is None else \
                round(m["best_feasible_t80_min"] - case.reference_t80, 4)
            if case.amended_margin is not None:
                m["infeasible_after_change"] = _median([r["metric5"]["infeasible_after_change"] for r in rs])
                if arm == "loop":
                    m["replay_passes"] = sum(bool((r["metric5"]["replay"] or {}).get("passes")) for r in rs)
            arms[arm] = m
        ref = next((r for r in rows if r["arm"] == "reference"), None)
        passive = next((r for r in rows if r["arm"] == "passive"), None)
        base = next((r["baseline"] for r in rows if r.get("baseline")), None)
        cases[name] = {"arms": arms, "reference_row": reference_row(ref, case), "passive_row": passive_row(passive),
                       "failing_baseline": baseline_row(base, case),
                       "criteria": criteria(name, case, arms), "claim": claim(case, arms, base, ref)}
    return cases


def reference_row(ref: dict | None, case: Case) -> dict | None:
    if ref is None:
        return None
    e = next((x for x in ref["experiments"] if "scalars" in x), None)
    if e is None:
        return {"ran": False}
    return {"ran": True, "c_rate": REFERENCE_C, "ambient": case.ambient, **e["scalars"],
            "feasible_final": fc.feasible(e["scalars"], case.final_margin, case.t_limit)}


def passive_row(p: dict | None) -> dict | None:
    if p is None:
        return None
    return {"answer": p["passive"]["best"], "charges": len(p["passive"]["charges"])}


def baseline_row(base: dict | None, case: Case) -> dict | None:
    if base is None or base.get("charge_time_80") is None:
        return None
    sc = {"status": base["status"], **{q: base[q] for q in ("charge_time_80", "min_anode_potential",
                                                           "max_temperature")}}
    return {**sc, "ambient": base["params"].get("ambient"),
            "plating_violated": not (sc["min_anode_potential"] >= case.final_margin + 0.5),
            "temperature_violated": not (sc["max_temperature"] <= case.t_limit - 0.1),
            "feasible_final": fc.feasible(sc, case.final_margin, case.t_limit)}


def criteria(name: str, case: Case, arms: dict) -> dict:
    loop = arms.get("loop")
    if name == "C":
        return {"judged": False, "note": "case C is reported separately as extrapolation, with no success criterion"}
    if loop is None:
        return {"judged": False, "note": "no loop runs"}
    t = loop["best_feasible_t80_min"]
    within = CRITERIA[name]["within_min"]
    out = {"judged": True, "loop_median_t80_min": t, "reference_t80_min": case.reference_t80,
           "within_min": within, "within": t is not None and t - case.reference_t80 <= within + 1e-9}
    if name == "A":
        vs = {arm: compare_arms(loop, arms[arm]) for arm in ("bo", "no_feedback") if arm in arms}
        out["versus"] = vs
        missing = [a for a in ("bo", "no_feedback") if a not in arms]
        out["missing_arms"] = missing
        out["met"] = out["within"] and not missing and all(v != "worse" for v in vs.values())
        # met only through a full tie (same median time and same median infeasible count): reported as a tie
        out["outcome"] = ("tie" if "tie" in vs.values() else "success") if out["met"] else "loss"
    else:
        need = CRITERIA["B"]["replay_passes"]
        out["replay_passes"] = loop.get("replay_passes", 0)
        out["replay_needed"] = need
        out["met"] = out["within"] and out["replay_passes"] >= need
        out["outcome"] = "success" if out["met"] else "loss"
    return out


def claim(case: Case, arms: dict, base: dict | None, ref: dict | None) -> dict | None:
    loop = arms.get("loop")
    if loop is None or loop["best_feasible_t80_min"] is None:
        return None
    t = loop["best_feasible_t80_min"]
    b = baseline_row(base, case)
    r = reference_row(ref, case)
    return {"loop_median_t80_min": t,
            "baseline_t80_min": None if b is None else b["charge_time_80"],
            "baseline_feasible": None if b is None else b["feasible_final"],
            "slower_than_baseline_min": None if b is None else round(t - b["charge_time_80"], 2),
            "reference_t80_min": None if not r or not r.get("ran") else r["charge_time_80"],
            "faster_than_reference_min": None if not r or not r.get("ran") else round(r["charge_time_80"] - t, 2)}


def report(out: Path, md: Path | None = REPORT_MD) -> dict:
    runs = [run_metrics(d) for d in run_dirs(out)]
    data = {"protocol": "evals/FASTCHARGE-PROTOCOL.md",
            "protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest() if PROTOCOL.is_file() else None,
            "budgets": {"experiments": EXPERIMENTS, "model_usd_per_run": MODEL_USD, "model_usd_total": TOTAL_USD},
            "total_model_usd": round(math.fsum(r.get("metric4", {}).get("model_usd", 0.0)
                                               + r.get("metric4", {}).get("counterfactual_model_usd", 0.0)
                                               for r in runs), 6),
            "runs": runs, "cases": summarise(runs)}
    # runs moved to <out>/void/ before scoring are not reported; what they cost still counts
    void = Path(out) / "void"
    data["set_aside"] = {"runs": sum(d.is_dir() for d in void.iterdir()) if void.is_dir() else 0,
                         "model_usd": round(GlobalCap(out).spent() - data["total_model_usd"], 6)}
    (Path(out) / "results.json").write_text(json.dumps(data, indent=1, sort_keys=True, allow_nan=False) + "\n",
                                            encoding="utf-8")
    if md is not None:
        Path(md).write_text(markdown(data), encoding="utf-8")
    return data


# ------------------------------------------------------------------ the markdown

def _f(v, fmt="{:.2f}", none="none") -> str:
    return none if v is None else fmt.format(v)


def _protocol_text(p: dict | None) -> str:
    if not p:
        return "none"
    if p.get("action") == "calibrate_thermal":
        return "thermal calibration"
    params = p.get("params") or p
    return ", ".join(f"{params[c]:g}" for c in fc.C_RATES if c in params) + " C"


def markdown(data: dict) -> str:
    runs, cases = data["runs"], data["cases"]
    lines = ["# Fast-charge loop evaluation", "",
             "All results are computational: claims about the frozen model, not about a physical cell. The protocol "
             "was fixed before any arm was scored. Every number below is computed from the run ledgers.", ""]
    if not runs:
        return "\n".join(lines + ["No runs have been recorded yet.", ""])
    lines += ["## Method", "",
              "Each run is a separate campaign on the LG M50 records with the failing 1.5C baseline re-run on the "
              "solver. The engineer registers the objective and an auto-approval rule for up to eight computational "
              "experiments before the campaign starts. Search arms have eight computational experiments, "
              "calibration included; model arms have a model budget of $1.50 per run. A result is feasible when its "
              "status is ok, its lowest anode potential is at least the margin plus 0.5 mV and its peak temperature "
              "is at most the limit minus 0.1 K; a marginal result counts as infeasible. Best feasible times use the "
              "margin in force at the end of the run; infeasible counts use the margin in force when each result "
              "arrived. Experiments at an ambient other than the case's are outside the case's action space: they "
              "are counted and never taken as candidates. Medians treat a run without a feasible result as "
              "infinitely slow.", "",
              f"Total model spend recorded: ${data['total_model_usd']:.2f} of the ${TOTAL_USD:.0f} limit."
              + (f" A further ${data['set_aside']['model_usd']:.2f} was spent on {data['set_aside']['runs']} runs set "
                 "aside before scoring and repeated in full; it counts toward the limit and is not reported." if data.get("set_aside", {}).get("runs") else ""), ""]
    for name, case in CASES.items():
        c = cases.get(name)
        if c is None:
            continue
        title = {"A": "Case A: 25 °C, +10 mV", "B": "Case B: 25 °C, +10 mV raised to +25 mV after the fourth "
                 "experiment", "C": "Case C: 15 °C, +10 mV (extrapolation, held out)"}[name]
        lines += [f"## {title}", "",
                  f"Reference optimum: {_protocol_text({c_: v for c_, v in zip(fc.C_RATES, case.reference_protocol)})}"
                  f", {case.reference_t80:.2f} min. Best feasible single step: {case.single_step_c:.2f}C, "
                  f"{case.single_step_t80:.1f} min.", ""]
        b = c["failing_baseline"]
        if b:
            lines += [f"Failing baseline (1.5C in every window, simulated): {b['charge_time_80']:.2f} min, "
                      f"{b['max_temperature']:.1f} °C, {b['min_anode_potential']:+.1f} mV. It violates "
                      + (" and ".join(w for w, v in (("the plating margin", b["plating_violated"]),
                                                     ("the temperature limit", b["temperature_violated"])) if v)
                         or "no constraint") + ".", ""]
        lines += ["| Arm | Run | Best feasible t80 (min) | Regret (min) | Protocol | Before beating single step | "
                  "Infeasible | Duplicates | Refused (bounds) | Model $ | Wall (min) | Solver failures | Burden | "
                  "Ends |", "|" + "---|" * 14]
        for r in [r for r in runs if r["case"] == name and r["arm"] in SEARCH_ARMS]:
            m1, m3 = r["metric1"], r["metric3"]
            ends = "candidate" if m1["resolved"] else "unresolved: " + "; ".join(m1["unresolved_reasons"])
            label = f"seed {r['n']}" if r["arm"] == "bo" else f"repeat {r['n']}"
            lines.append(f"| {ARM_LABEL[r['arm']]} | {label} | {_f(m1['best_feasible_t80_min'])} | "
                         f"{_f(m1['regret_min'], '{:+.2f}')} | {_protocol_text(m1['candidate_params'])} | "
                         f"{_f(r['metric2']['experiments_before_beating_single_step'], '{:d}', 'never')} | "
                         f"{m3['infeasible_experiments']} | {m3['duplicate_experiments']} | {m3['refused_for_bounds']} | "
                         f"{r['metric4']['model_usd']:.2f} | {_f(r['metric4']['wall_min'], '{:.1f}')} | "
                         f"{r['metric4']['solver_failures']} | {r['metric6']['total']} | {ends} |")
        lines += ["", "Medians:", "",
                  "| Arm | Runs | Best feasible t80 (min) | Regret (min) | Before beating single step | Infeasible | "
                  "Model $ | Wall (min) | Burden |" + (" Infeasible after change |" if case.amended_margin else ""),
                  "|" + "---|" * (9 + (1 if case.amended_margin else 0))]
        for arm, m in c["arms"].items():
            lines.append(f"| {ARM_LABEL[arm]} | {m['runs']} | {_f(m['best_feasible_t80_min'])} | "
                         f"{_f(m['regret_min'], '{:+.2f}')} | "
                         f"{_f(m['experiments_before_beating_single_step'], '{:g}', 'never')} | "
                         f"{_f(m['infeasible_experiments'], '{:g}')} | {_f(m['model_usd'])} | "
                         f"{_f(m['wall_min'], '{:.1f}')} | {_f(m['burden'], '{:g}')} |"
                         + (f" {_f(m.get('infeasible_after_change'), '{:g}')} |" if case.amended_margin else ""))
        ref, pas = c["reference_row"], c["passive_row"]
        lines.append("")
        if ref and ref.get("ran"):
            lines.append(f"Reference row, not a competitor: 0.7C constant current then constant voltage at "
                         f"{ref['ambient']:g} °C, simulated: {ref['charge_time_80']:.2f} min, "
                         f"{ref['max_temperature']:.1f} °C, {ref['min_anode_potential']:+.1f} mV; "
                         + ("feasible" if ref["feasible_final"] else "not feasible") + " under this case's rule.")
        if pas and pas.get("answer"):
            a = pas["answer"]
            lines.append(f"Passive pipeline: it runs no experiment. The best protocol the records support is "
                         f"{a['c_rate']:g}C constant current then constant voltage, {a['t10_80_min']:.1f} min from 10 "
                         f"to 80 % measured (median of {a['charges']} charges on {len(a['records'])} cells). The "
                         "records hold no anode potential, so the plating margin cannot be checked on them; its "
                         "report of the failing baseline is the simulated baseline above.")
        if case.amended_margin is not None:
            reps = [r for r in runs if r["case"] == name and r["arm"] == "loop"]
            if reps:
                lines += ["", "Counterfactual replay (loop runs): one turn from the same four-experiment prefix with "
                          "the amendment and one without it.", "",
                          "| Repeat | Proposal with amendment | Predicted lowest anode potential (mV) | "
                          "Proposal without | Passes |", "|---|---|---|---|---|"]
                for r in reps:
                    rp = r["metric5"]["replay"] or {}
                    lines.append(f"| {r['n']} | {_protocol_text(rp.get('with'))} | "
                                 f"{_f(rp.get('predicted_min_anode_potential_mV'), '{:+.1f}')} | "
                                 f"{_protocol_text(rp.get('without'))} | "
                                 f"{'yes' if rp.get('passes') else 'no' + (': ' + rp['reason'] if rp.get('reason') else '')} |")
        cr = c["criteria"]
        lines += ["", "Success criteria:", ""]
        if not cr["judged"]:
            lines.append(cr["note"][0].upper() + cr["note"][1:] + ".")
        else:
            lines.append(f"- Median best feasible time {_f(cr['loop_median_t80_min'])} min against the reference "
                         f"{cr['reference_t80_min']:.2f} min (allowed {cr['within_min']:.1f} min): "
                         + ("met." if cr["within"] else "not met."))
            if name == "A":
                words = {"better": "better than", "worse": "worse than", "tie": "tied with"}
                names = {"bo": "constrained BO", "no_feedback": "the same model without feedback"}
                for arm, v in cr["versus"].items():
                    lines.append(f"- The loop is {words[v]} {names[arm]} (median best feasible time, then median "
                                 "infeasible experiments).")
                for arm in cr["missing_arms"]:
                    lines.append(f"- {ARM_LABEL[arm]} has no runs, so the comparison cannot be judged.")
            else:
                lines.append(f"- The counterfactual replay passes in {cr['replay_passes']} of the loop runs "
                             f"(needed: {cr['replay_needed']}).")
            lines.append(f"- Outcome: {cr['outcome']}." + (" The criteria hold only through a tie, so the result is "
                                                             "reported as a tie." if cr["outcome"] == "tie" else ""))
        cl = c["claim"]
        if cl:
            lines += ["", "The improvement that may be claimed is computational, under the frozen model: "
                      f"the loop's median best feasible protocol takes {cl['loop_median_t80_min']:.2f} min"
                      + (f" and meets every constraint; the failing baseline takes {cl['baseline_t80_min']:.2f} min"
                         + (" and is feasible" if cl["baseline_feasible"] else " and breaks a constraint")
                         + f", so feasibility costs {cl['slower_than_baseline_min']:+.2f} min"
                         if cl["baseline_t80_min"] is not None else "")
                      + "." + (f" It is {cl['faster_than_reference_min']:.2f} min faster than the 0.7C reference row."
                               if cl["faster_than_reference_min"] is not None else "")]
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ the command line

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.fastcharge", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run arms; the model arms need --live (paid calls)")
    r.add_argument("--records", type=Path, required=True, help="folder with the three Chen 2020 LG M50 CSV files")
    r.add_argument("--out", type=Path, required=True, help="runs folder, e.g. runs/fastcharge")
    r.add_argument("--case", nargs="+", choices=sorted(CASES), default=sorted(CASES))
    r.add_argument("--arm", nargs="+", choices=ARMS, default=list(ARMS))
    r.add_argument("--repeat", type=int, choices=REPEATS, help="one repeat of the model arms (default: all)")
    r.add_argument("--seed", type=int, choices=SEEDS, help="one seed of Bayesian optimisation (default: all)")
    r.add_argument("--live", action="store_true", help="required for the model arms: paid Muse Wonder calls")
    p = sub.add_parser("report", help="metrics from the run ledgers: results.json and the markdown report")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--md", type=Path, default=REPORT_MD)
    args = ap.parse_args(argv)
    if args.command == "report":
        data = report(args.out, args.md)
        print(f"{len(data['runs'])} runs -> {args.out / 'results.json'} and {args.md}")
        return 0
    if any(a in MODEL_ARMS for a in args.arm) and not args.live:
        ap.error("the loop and no_feedback arms call a paid model: add --live, or choose --arm bo passive reference")
    try:
        check_records(args.records)
        if args.live and any(a in MODEL_ARMS for a in args.arm):
            check_calibration_records()
    except HarnessError as e:
        ap.error(str(e))
    args.out.mkdir(parents=True, exist_ok=True)
    failed = 0
    for case, arm, n in planned(args.case, args.arm, args.repeat, args.seed):
        name = run_name(case, arm, n)
        if (args.out / name / "run.json").is_file():
            print(f"{name}: already run")
            continue
        print(f"{name}: running", flush=True)
        try:
            info = run_one(args.out, case, arm, n, args.records, live=args.live)
        except HarnessError as e:
            print(f"{name}: {e}", file=sys.stderr)
            failed += 1
            continue
        print(f"{name}: {info['stop']} ({info['harness_wall_s']:.0f} s)", flush=True)
        if any(t["status"] == "global_budget" for t in info["turns"]):
            print(f"The ${TOTAL_USD:.0f} model-spend limit is reached; no further model run starts.", file=sys.stderr)
            args.arm = [a for a in args.arm if a not in MODEL_ARMS]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
