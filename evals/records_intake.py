"""From an engineer's records to an investigation: the acceptance test of the records intake.

    python -m evals.records_intake prepare --out runs/records-intake          # case files, reference manifests, seals
    python -m evals.records_intake freeze --out runs/records-intake           # the implementation's sha256
    python -m evals.records_intake run --out runs/records-intake --cases H1 H2 H3 \
        --arms numeric translucent_meta general_meta --repeats 1 2 [--live --cap-usd X]
    python -m evals.records_intake report --out runs/records-intake [--md evals/RECORDS-INTAKE.md]

Task. From raw records, their documentation and an engineering objective (the fastest 10-80 % charge at 25 degC with
the cell at or below 45 degC and the anode at or above +25 mV, forecast), produce a checked thermal model and a design
from the numerical controller, run one follow-up lab test, ingest it, and revise the model or the design when the
evidence warrants it.

Data. Chen et al. 2020, LG M50 validation records: Maccor exports of three cells (data/raw/chen2020-lgm50, fetched by
scripts/fetch_lgm50_data.py). Case H3's files are a SYNTHETIC re-export of two of them: semicolon-delimited, decimal
comma, current in mA, temperatures in K, renamed headers and another preamble; every value is the published one,
converted.

Lab. The follow-up test runs in the SIMULATED lab (computational). Its sealed condition is the (h, k) that the same
fit gives on the withheld cell's records (family 2 on its C/2, 1C and 1.5C discharges from full; `prepare`, no model
call): a simulation parameterised from a withheld real cell, never a physical test. After a run stops, its final design
is run once under that condition and scored against the objective's limits (fastcharge_lab.assess_design).

Arms.
  translucent_meta  Translucent's investigation: Muse (Meta profile) plans a campaign opened from the uploads, with the
                    records tools (read, import with a manifest it writes, segments, sandbox analysis), record tests,
                    fit, check, forecast, ranking, design and the simulated lab.
  general_meta      the same model with the same files, documentation, numerical functions, simulated lab and
                    sandbox, in a plain tool loop: no manifest validation, no campaign state, no test numbering, no
                    segment binding, no view and no records guidance.
  numeric           the numerical controller on correctly prepared inputs: the reference manifest, every C/2, 1C and
                    1.5C discharge from full as a record test, family 2, a held-out check, a design, the follow-up
                    test of that design, a refit on everything and a final design. No model call: the attainable
                    result, never a Muse arm's ceiling.

The Muse arms make paid calls: they refuse to start without --live and --cap-usd, and before every provider call the
evaluation's recorded spend plus that call's worst case must stay under the cap. Scored cases (H1-H3) run only after
`freeze` recorded the implementation's sha256 and while it still matches. One folder per run,
<out>/<case>-<arm>-<repeat>: run.json, assessment.json, metrics.json and revealed-condition.json (copied only after
scoring). No engineer-time claim is made: there is no timed baseline.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from evals.fastcharge import CappedSession, GlobalCap, drive, model_session_for
from evals.fastcharge_lab import AMBIENT, MARGIN_MV, T_LIMIT_C, LedgerExecutor, _NoAmendment, assess_design
from evals.fastcharge_lab import Case as LabCase
from translucent import chain, datasets, records, systems, units
from translucent.intervention_chat import Settings, StopCampaign, assistant_message
from translucent.ledger import Ledger
from translucent.loop import actor_kind, current_objective, request_states
from translucent.loop_contracts import ChannelSpec, TableSpec
from translucent.loop_planner import (LENGTH_MAX_TOKENS, MAX_TOKENS, STEP_CAP, ScriptedLoopPlanner, model_spend,
                                      run_turn)
from translucent.loop_service import Actor, LoopService
from translucent.systems import lgm50_fastcharge as fc
from translucent.systems import lgm50_lab as lab

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RAW = ROOT / "data" / "raw" / "chen2020-lgm50"
REFERENCE = ROOT / "data" / "chen2020-lgm50" / "manifest.json"
REPORT_MD = HERE / "RECORDS-INTAKE.md"

ENGINEER = "engineer"
PERSON = Actor("person", ENGINEER, "cli")
MODEL_USD = 2.50                      # model budget per Muse run
EXPERIMENTS = 24                      # computational requests per campaign: record tests, fits, checks, designs, lab
LAB_TESTS = 2                         # simulated lab tests per run: the follow-up, and one more if the evidence asks
AGENT_CALLS = 120                     # tool calls of the general agent per run (a guard)
TOOL_CHARS = 12000                    # a general-agent tool result is cut to this many characters
SEGMENT_PAGE = 12                     # full segments per page of the general agent's segments(detail=true)
REPEATS = (1, 2)
RATES = (0.5, 1.0, 1.5)               # the reference record tests: the C/2, 1C and 1.5C discharges from full
FULL_V = 4.1                          # "from full": the rest before the discharge ends at or above this voltage
ARMS = ("translucent_meta", "general_meta", "numeric")
MODEL_ARMS = ("translucent_meta", "general_meta")
ARM_LABEL = {"translucent_meta": "1. Translucent investigation (Muse)", "general_meta": "2. General agent (Muse)",
             "numeric": "3. Numerical controller, prepared inputs"}
ACTIONS = ("record_test", "lab_charge", "forecast_protocol", "fit_thermal", "check_model", "rank_measurements",
           "optimize_protocol")
# the implementation the scored runs freeze: everything the investigation and the arms execute
FROZEN = ("translucent/records.py", "translucent/loop_planner.py", "translucent/loop_contracts.py",
          "translucent/loop_service.py", "translucent/loop.py", "translucent/loop_runner.py", "translucent/datasets.py",
          "translucent/loop_compare.py", "translucent/loop_view.py", "translucent/api_v1.py", "translucent/units.py",
          "translucent/intervention_chat.py", "translucent/sandbox/runner.py", "translucent/systems/lgm50_lab.py",
          "translucent/systems/lgm50_lab_solver.py", "translucent/systems/lgm50_fastcharge.py",
          "translucent/systems/lgm50_solver.py", "evals/records_intake.py", "evals/fastcharge.py",
          "evals/fastcharge_lab.py")

DOCUMENTATION = (
    "LG M50 cells (LG INR21700 M50, nominal capacity 5.0 Ah), one file per cell, exported from a Maccor cycler; the "
    "cells sat in a thermal chamber. Published validation tests: Chen et al. 2020, \"Development of Experimental "
    "Techniques for Parameterization of Multi-scale Lithium-ion Battery Models\", J. Electrochem. Soc. 167, 080534; "
    "data on Zenodo, doi:10.5281/zenodo.4032561 (CC BY 4.0).\n"
    "Procedure on every cell: a C/10 discharge; after each discharge a 0.3C constant-current charge to 4.2 V with a "
    "constant-voltage hold to 50 mA; discharges from full to 2.5 V at C/2, 1C and 1.5C; 2 h rests between steps.\n"
    "Instruments: a thermocouple taped to the cell surface, and a second sensor in the chamber air. The current is "
    "logged as a magnitude; the mode column says C (charge), D (discharge) or R (rest). The files hold no faster "
    "charge and no plating measurement. No thermocouple calibration is on file.")
SYNTHETIC_NOTE = ("\nThese files were re-exported through the lab's logging software with its regional settings: "
                  "column names, units and number format are as that export writes them. The export writes short "
                  "channel names without units, and which temperature channel holds which sensor was not recorded.")


class HarnessError(RuntimeError):
    """The harness refuses to start a run, with the reason."""


# ------------------------------------------------------------------ cases and their files

@dataclass(frozen=True)
class Case:
    name: str
    supplied: tuple[str, ...]         # cells whose files the engineer supplies
    withheld: str                     # the cell whose fit seals the simulated lab
    synthetic: bool = False
    scored: bool = True
    note: str = ""
    dev_seal: tuple[float, float] | None = None   # (h, k) set by hand for a development case: no cell's fit


CASES = {
    "D0": Case("D0", ("cell02",), "none", scored=False, dev_seal=(9.0, 1.15),
               note="development; not scored; its lab is a hand-set condition, not any scored case's"),
    "H1": Case("H1", ("cell02", "cell03"), "cell04", note="published files"),
    "H2": Case("H2", ("cell03", "cell04"), "cell02", note="published files"),
    "H3": Case("H3", ("cell02", "cell04"), "cell03", synthetic=True,
               note="SYNTHETIC re-export: ';' delimiter, decimal comma, unit-less channel names (current in "
                    "mA, temperatures in K, chamber before surface), other preamble; the documentation does not say "
                    "which temperature channel is which"),
}

PUBLISHED_HEADER_ROW = 13
SYNTH_PREAMBLE = ("Logger export;LG M50 validation", "Channel;{cell}", "Software;lab logger 4.2",
                  "Regional settings;de-DE", "")
# published column -> (re-exported name, conversion of the value, unit in the re-export)
# The names carry no units and do not say which temperature sensor is which (the chamber is written first): an
# importer must infer current in mA and temperatures in K from the values, and the chamber from its flat trace.
SYNTH_COLUMNS = {"Test Time [s]": ("elapsed [s]", None), "Step": ("step", None), "Md": ("mode", None),
                 "Current [A]": ("I", lambda x: x * 1000.0), "Voltage [V]": ("U", None),
                 "Capacity [Ah]": ("Q", None),
                 "Temperature Chamber [degC]": ("T1", lambda x: x + 273.15),
                 "Temperature Cell [degC]": ("T2", lambda x: x + 273.15)}


def raw(cell: str) -> bytes:
    path = RAW / f"LGM50_{cell}.csv"
    if not path.is_file():
        raise HarnessError(f"{path} is missing: fetch the records with scripts/fetch_lgm50_data.py")
    return path.read_bytes()


def file_name(case: Case, cell: str) -> str:
    return f"{cell}_export.csv" if case.synthetic else f"LGM50_{cell}.csv"


def _decimal_comma(text: str, convert) -> str:
    if convert is None or not text.strip():
        return text.replace(".", ",")
    return f"{convert(float(text)):.10g}".replace(".", ",")


def reexport(cell: str) -> bytes:
    """The SYNTHETIC re-export of a published file (case H3): the same rows and values, converted and reformatted."""
    lines = raw(cell).decode("utf-8-sig").splitlines()
    header = lines[PUBLISHED_HEADER_ROW].split(",")
    keep = [(header.index(src), dst, conv) for src, (dst, conv) in SYNTH_COLUMNS.items()]
    out = [line.format(cell=cell) for line in SYNTH_PREAMBLE]
    out.append(";".join(dst for _, dst, _ in keep))
    for line in lines[PUBLISHED_HEADER_ROW + 1:]:
        if not line.strip():
            continue
        cells = line.split(",")
        out.append(";".join(_decimal_comma(cells[i], conv) for i, _, conv in keep))
    return ("\r\n".join(out) + "\r\n").encode("utf-8")


def case_files(case: Case) -> dict[str, bytes]:
    return {file_name(case, c): reexport(c) if case.synthetic else raw(c) for c in case.supplied}


def documentation(case: Case) -> str:
    return DOCUMENTATION + (SYNTHETIC_NOTE if case.synthetic else "")


def reference_manifest(case: Case, cells: tuple[str, ...] | None = None) -> dict:
    """The correctly prepared import of a case's files (the numerical arm's input, and the yardstick for the agents'
    imports): the published manifest's records with the lab system's quantities."""
    ref = json.loads(REFERENCE.read_text(encoding="utf-8"))
    out = []
    for cell in cells or case.supplied:
        r = copy.deepcopy(next(x for x in ref["records"] if x["id"] == cell))
        r.pop("role", None)
        tb = r["tables"][0]
        cols = {"Voltage [V]": {"quantity": "voltage", "unit": "V"},
                "Temperature Cell [degC]": {"quantity": "surface_temperature", "unit": "degC",
                                            "label": "Cell surface thermocouple"},
                "Current [A]": {"quantity": "current", "unit": "A",
                                "label": "Current magnitude; the mode column gives its direction"},
                "Temperature Chamber [degC]": {"quantity": "chamber_temperature", "unit": "degC"}}
        tb.update(file=file_name(case, cell), columns=cols)
        if case.synthetic and cell in case.supplied:
            unit = {"Current [A]": "mA", "Temperature Cell [degC]": "K", "Temperature Chamber [degC]": "K"}
            tb.update(delimiter=";", decimal=",", header_row=len(SYNTH_PREAMBLE), skip_rows=0,
                      time_column=SYNTH_COLUMNS["Test Time [s]"][0],
                      columns={SYNTH_COLUMNS[k][0]: {**v, "unit": unit.get(k, v["unit"])} for k, v in cols.items()})
        out.append(r)
    return {**{k: ref[k] for k in ("manifest_version", "title", "author", "citation", "licence")},
            "id": f"chen2020-{case.name.lower()}", "system": lab.SYSTEM_ID, "records": out}


def reference_segments(L: Ledger, record) -> list[int]:
    """The C/2, 1C and 1.5C discharges from full of an imported record, by segment number."""
    out = []
    for s in lab.record_segments(L, record):
        rate = s.get("rate_per_nominal_capacity") or 0.0
        if s["kind"] == "constant current" and s.get("voltage_trend") == "falling" and \
                any(abs(rate - r) < 0.01 for r in RATES) and (s.get("after") or {}).get("voltage_end_V", 0) >= FULL_V:
            out.append(s["segment"])
    return out


# ------------------------------------------------------------------ objective and campaign

def objective(case: Case, arm: str, uploads: list[dict]) -> dict:
    t = lab.OBJECTIVE_TEMPLATE
    model = arm in MODEL_ARMS
    return {"system": {"id": lab.SYSTEM_ID}, "datasets": [], "baseline": [], "evaluator": t["evaluator"],
            "kind": "fastest_feasible",
            "statement": (f"{t['statement']['fastest_feasible']} The ambient setpoint is {AMBIENT:g} degC. The "
                          "supplied records come from earlier tests of the same cell type; lab tests run in a "
                          "simulated lab (computational) whose fixture may differ from the records'."),
            "target": {k: v for k, v in t["target"].items() if k != "margin"},
            "constraints": [dict(c) for c in t["constraints"]], "quantities": [dict(q) for q in t["quantities"]],
            "budget": {"model_usd": MODEL_USD if model else 0.0, "experiments": EXPERIMENTS, "experiment_usd": 0.0,
                       "lab_tests": LAB_TESTS},
            "planner": {"kind": "muse" if model else "external", "profile": "meta" if model else None,
                        "client": None, "actions": list(ACTIONS)},
            "auto_approve": [{"executor": "computational", "max_usd": 0.0, "max_wall_s": 900.0,
                              "max_count": EXPERIMENTS}],
            "registered_by": ENGINEER, "uploads": uploads, "documentation": documentation(case)}


def open_campaign(root: Path, case: Case, arm: str, name: str, seal: dict | None) -> tuple[Ledger, str]:
    files = case_files(case)
    uploads = records.describe(files)
    L = Ledger(root)
    title = f"Records intake, {ARM_LABEL.get(arm, arm)}, case {case.name}"
    cid = LoopService(L, PERSON, idempotency_key=name).open_campaign(objective(case, arm, uploads), title,
                                                                     memory="off")["campaign_id"]
    records.stage_uploads(L.root, cid, files, expected=uploads)
    if seal is not None:
        lab.seal_lab(L.root, cid, seal["h"], seal["k"])
    return L, cid


def import_reference(L: Ledger, cid: str, manifest: dict) -> dict:
    """The prepared import, through the planner's own import tool (one scripted turn, no model)."""
    run_turn(L, cid, ScriptedLoopPlanner([
        [("import_records", {"manifest": manifest, "missing_information": [
            {"item": "thermocouple calibration", "why_it_matters": "the fit's noise floor"}]})],
        [("wait", {"reason": "imported"})]]))
    got = [r.data["payload"]["result"] for r in chain.records(L, cid) if r.type == "planner" and
           r.data.get("phase") == "tool_result" and r.data["payload"].get("tool") == "import_records"]
    if not got or "error" in got[-1]:
        raise HarnessError(f"the reference import was refused: {got[-1].get('error') if got else 'no result'}")
    return got[-1]


# ------------------------------------------------------------------ the numerical arm (and the seals)

class Numeric:
    """The numerical controller on prepared inputs, through LedgerExecutor (proposals as an agent, auto-approved)."""

    def __init__(self, L: Ledger, cid: str):
        self.L, self.cid = L, cid
        self.do = LedgerExecutor(L, cid, "numeric")
        self.tests: list[dict] = []            # in the campaign's numbering
        self.versions = 0
        self.trace: list[dict] = []

    def step(self, action: str, params: dict, why: str) -> dict:
        r = self.do(action, params, why)
        self.trace.append({"action": action, "params": params, "ok": r["ok"], "scalars": r.get("scalars"),
                           "failure": r.get("failure")})
        if not r["ok"]:
            raise HarnessError(f"{action} failed: {r.get('failure')}")
        return r

    def record_tests(self) -> dict[int, list[int]]:
        obj = current_objective(self.L, self.cid)
        by_record = {}
        for n, (ref, rec) in enumerate(records.campaign_records(self.L, obj, self.cid), start=1):
            for seg in reference_segments(self.L, rec):
                self.step("record_test", {"record": n, "segment": seg, "mode": 1, "start_soc": 1.0},
                          f"Discharge from full, segment {seg} of {ref}.")
                self.tests.append({"kind": "record", "record": n, "segment": seg})
                by_record.setdefault(n, []).append(len(self.tests))
        return by_record

    def fit(self, tests: list[int]) -> dict:
        r = self.step("fit_thermal", {"family": 2, "fit_on": sum(1 << (n - 1) for n in tests)},
                      f"Family 2 on tests {tests}.")
        self.versions += 1
        return {"version": self.versions, **r["scalars"]}

    def check(self, version: int, tests: list[int]) -> dict:
        return self.step("check_model", {"model_version": version, "test_on": sum(1 << (n - 1) for n in tests)},
                         f"Version {version} on tests {tests}, which it did not fit.")["scalars"]

    def optimize(self, version: int) -> list[float] | None:
        sc = self.step("optimize_protocol", {"ambient": AMBIENT, "model_version": version},
                       f"Design under version {version}.")["scalars"]
        return [float(sc[c]) for c in fc.C_RATES] if sc.get("design_found") == "yes" else None

    def lab_test(self, rates: list[float]) -> dict:
        sc = self.step("lab_charge", {**dict(zip(fc.C_RATES, rates)), "ambient": AMBIENT, "rest_min": 60.0},
                       "The follow-up test of the design, with a 60 min rest.")["scalars"]
        self.tests.append({"kind": "lab", "rates": rates})
        return sc


def run_numeric(L: Ledger, cid: str, case: Case) -> dict:
    """Arm 3: prepared import; every reference record test; fit on one cell, check on the others (one cell: fit on
    C/2 and 1.5C, check on 1C); fit on all; design; the follow-up lab test of that design; refit on everything; final
    design."""
    import_reference(L, cid, reference_manifest(case))
    p = Numeric(L, cid)
    by_record = p.record_tests()
    groups = list(by_record.values())
    if len(groups) > 1:
        fit_on, held = groups[0], [n for g in groups[1:] for n in g]
    else:
        fit_on, held = [groups[0][0], groups[0][-1]], groups[0][1:-1]
    first = p.fit(fit_on)
    checked = p.check(first["version"], held)
    everything = p.fit([n for g in groups for n in g])
    design = p.optimize(everything["version"])
    out = {"fit_on": fit_on, "held_out": held, "first_fit": first, "check": checked, "fit_all": everything,
           "design_before": design}
    if design is not None:
        out["follow_up"] = p.lab_test(design)
        refit = p.fit(list(range(1, len(p.tests) + 1)))
        out["refit"] = refit
        out["design_after"] = p.optimize(refit["version"])
        out["revised"] = out["design_after"] != design
    LoopService(L, Actor("agent", "numeric", "cli")).conclude(
        cid, "The numerical controller on prepared inputs finished; its final design is the last one found.")
    out["trace"] = p.trace
    out["problems"] = p.do.problems
    return out


def seal_for(out: Path, case: Case) -> dict:
    """The withheld cell's (h, k): family 2 on its reference record tests, in a scratch ledger. Cached per cell.
    A development case's seal is set by hand and comes from no cell."""
    if case.dev_seal is not None:
        return {"cell": None, "h": case.dev_seal[0], "k": case.dev_seal[1], "region_95": None, "fit_rmse_K": None,
                "adequate": None, "tests": [], "how": "development seal set by hand; from no cell"}
    path = Path(out) / "seals" / f"{case.withheld}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    work = Path(out) / "seals" / f"work-{case.withheld}"
    if work.exists():
        shutil.rmtree(work)
    one = Case(f"seal-{case.withheld}", (case.withheld,), case.withheld, scored=False)
    L, cid = open_campaign(work / "ledger", one, "numeric", one.name, None)
    try:
        import_reference(L, cid, reference_manifest(one))
        p = Numeric(L, cid)
        tests = [n for g in p.record_tests().values() for n in g]
        fit = p.fit(tests)
        version = lab.model_versions(L, request_states(L, cid), current_objective(L, cid))[fit["version"]]
    finally:
        L.db.close()
    seal = {"cell": case.withheld, "h": version["estimate"]["h"], "k": version["estimate"]["k"],
            "region_95": version["region_95"], "fit_rmse_K": version["fit_rmse_K"], "adequate": version["adequate"],
            "tests": tests, "how": "family 2 on the withheld cell's C/2, 1C and 1.5C discharges from full"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seal, indent=1, sort_keys=True), encoding="utf-8")
    return seal


# ------------------------------------------------------------------ the general agent (arm 2)

GENERAL_PROMPT = """You are an engineering agent with a set of tools. An engineer gives you raw test records, their \
documentation and an objective. Work out what the files contain, prepare the analysis, run the numerical tools, \
request the next experiment, and revise your model or design when its result warrants it. Numbers come from the \
tools: never type a fitted value, a temperature or a design yourself. When you are done, call conclude with the \
design you recommend (one from optimize_protocol) and what it rests on."""


def _param_schema(p) -> dict:
    s: dict = {"description": " ".join(x for x in (p.label, f"[{p.unit}]" if p.unit else "") if x)}
    if p.choices:
        s.update(type="number", enum=list(p.choices))
    else:
        s["type"] = "integer" if p.integer else "number"
        if p.lo is not None:
            s["minimum"] = p.lo
        if p.hi is not None:
            s["maximum"] = p.hi
    return s


class GeneralAgent:
    """Arm 2: the same files, documentation, numerical functions (the lab system's own solver child), simulated lab
    and sandbox, as plain functions in one tool loop. What it does not get is Translucent's workflow: no manifest or
    importer, no campaign state or test numbering it did not keep itself, no segment binding, no view, no guidance."""

    BOUND = {"lab", "model", "sources", "base", "margin_mV", "t_limit_C", "record_ref", "window", "series"}

    def __init__(self, run_dir: Path, case: Case, seal: dict, analysis_runner: Callable | None = None):
        self.dir = Path(run_dir) / "agent"
        self.files_dir = self.dir / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.files = case_files(case)
        for name, data in self.files.items():
            (self.files_dir / name).write_bytes(data)
        self.case = case
        self.condition = {"h": float(seal["h"]), "k": float(seal["k"]), "noise_K": lab.NOISE_K,
                          "noise_V": lab.NOISE_V, "ambient_offset_K": 0.0, "seed": hashlib.sha256(
                              f"{case.name}:{run_dir}".encode()).hexdigest()[:32]}
        self.adapter = systems.get(lab.SYSTEM_ID)
        self.analysis_runner = analysis_runner
        self.tables: dict[str, dict] = {}
        self.tests: list[dict] = []
        self.models: list[dict] = [lab.nominal_model(SimpleNamespace(uploads=True))]
        self.designs: list[dict] = []
        self.lab_tests = 0
        self.calls: list[dict] = []
        self.checks: dict[int, list[dict]] = {}       # model -> its held-out checks (lab.heldout_checks' shape)
        self.concluded: dict | None = None
        self.runs = 0

    # the solver child, as the runner calls it ------------------------------------------------
    def _execute(self, action: str, params: dict, inputs: dict) -> dict:
        self.runs += 1
        work = self.dir / "work" / f"{self.runs:04d}-{action}"
        work.mkdir(parents=True, exist_ok=True)
        (work / "inputs.json").write_bytes(fc.canonical_json(inputs))
        self.adapter.execute(action, params, work)
        return json.loads((work / "result.json").read_text(encoding="utf-8"))

    def _model(self, n) -> dict:
        if isinstance(n, bool) or not isinstance(n, (int, float)) or n != int(n) or not 0 <= n < len(self.models):
            raise ValueError(f"model must be 0 to {len(self.models) - 1}")
        return self.models[int(n)]

    def _sources(self, tests) -> tuple[list[dict], list[dict]]:
        if not isinstance(tests, list) or not tests:
            raise ValueError("name at least one test by its number")
        sealed, staged = [], []
        for n in tests:
            if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= len(self.tests):
                raise ValueError(f"tests are 1 to {len(self.tests)}")
            t = self.tests[n - 1]
            body = t["body"]
            sid = "sha256:" + hashlib.sha256(fc.canonical_json(body)).hexdigest()
            sealed.append({"n": n, "result_id": f"test-{n}", "series": sid})
            staged.append({"result_id": f"test-{n}", "series": sid, "body": body, "raw_sha256": sid,
                           "params": t["source_params"], "noise_K": t.get("noise_K")})
        return sealed, staged

    @staticmethod
    def _json(v) -> str:
        return json.dumps(v, sort_keys=True, separators=(",", ":"))

    def _limits(self) -> dict:
        return {"margin_mV": MARGIN_MV, "t_limit_C": T_LIMIT_C}

    def _summary(self, result: dict) -> dict:
        out = {"status": result["status"], "error": result.get("error"),
               "scalars": {k: v["value"] for k, v in (result.get("scalars") or {}).items()}}
        if result.get("details") is not None:
            out["details"] = result["details"]
        return out

    # tools ------------------------------------------------------------------------------------
    def schemas(self) -> list[dict]:
        def fn(name, desc, props, required):
            return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
                "type": "object", "properties": props, "required": required}}}
        spec = {a.name: a for a in lab.SPEC.actions}

        def action_props(action):
            return {p.name: _param_schema(p) for p in spec[action].parameters if p.name not in self.BOUND
                    and p.name not in ("model_version", "fit_on", "test_on")}
        model = {"model": {"type": "integer", "minimum": 0, "description": "a model from fit_thermal (0: the "
                           "parameter set as shipped, calibrated to no fixture)"}}
        tests = {"type": "array", "items": {"type": "integer", "minimum": 1},
                 "description": "test numbers, as cut_test and lab_charge returned them"}
        table_cols = {"type": "object", "description": "role -> {column, unit}; roles: current (cell current), "
                      "voltage, surface_temperature (cell surface), chamber_temperature (chamber or ambient)",
                      "additionalProperties": {"type": "object", "properties": {
                          "column": {"type": "string"}, "unit": {"type": "string"}}, "required": ["column", "unit"]}}
        return [
            fn("read_file", "Raw lines of a supplied file.", {
                "file": {"type": "string"}, "start_line": {"type": "integer", "minimum": 1},
                "lines": {"type": "integer", "maximum": records.MAX_LINES}}, ["file"]),
            fn("column_stats", "Statistics of every column under a header row: count, numeric share, min, max, "
                               "first, last, never-decreasing, distinct values of small sets.", {
                "file": {"type": "string"}, "header_row": {"type": "integer", "minimum": 0,
                                                          "description": "lines before the header"},
                "delimiter": {"type": "string"}, "decimal": {"type": "string", "enum": [".", ","]},
                "skip_rows": {"type": "integer", "minimum": 0}}, ["file", "header_row"]),
            fn("define_table", "Read a file's columns into a named table: the time column (with its unit) and the "
                               "columns you assign to roles, each converted from the unit you give.", {
                "name": {"type": "string"}, "file": {"type": "string"},
                "header_row": {"type": "integer", "minimum": 0}, "delimiter": {"type": "string"},
                "decimal": {"type": "string", "enum": [".", ","]}, "skip_rows": {"type": "integer", "minimum": 0},
                "time_column": {"type": "string"}, "time_unit": {"type": "string"}, "columns": table_cols,
                "nominal_capacity_Ah": {"type": "number"}},
               ["name", "file", "header_row", "time_column", "columns"]),
            fn("segments", "A table's current divided into segments (rest, constant current, other), with measured "
                           "current, voltage, temperatures and chamber. By default one short line per segment of at "
                           f"least {records.SEGMENT_MINUTES:g} min; detail=true gives every segment in full, "
                           f"{SEGMENT_PAGE} per page from `start` (a segment number).",
               {"table": {"type": "string"}, "detail": {"type": "boolean"},
                "start": {"type": "integer", "minimum": 1}}, ["table"]),
            fn("cut_test", "A constant-current stretch of a table as a test: the series rebased to its start, its "
                           "current, C-rate, duration, start temperature, rise and ambient (the chamber median, or "
                           "ambient_degC when the table has no chamber column). Returns its test number.", {
                "table": {"type": "string"}, "t_start_s": {"type": "number"}, "t_end_s": {"type": "number"},
                "mode": {"type": "string", "enum": ["discharge", "charge"]},
                "start_soc": {"type": "number", "minimum": 0, "maximum": 1}, "ambient_degC": {"type": "number"}},
               ["table", "t_start_s", "t_end_s", "mode", "start_soc"]),
            fn("fit_thermal", "Fit the thermal model to tests; returns a new model number.",
               {**action_props("fit_thermal"), "tests": tests}, ["family", "tests"]),
            fn("check_model", "A model's errors on tests it did not fit.", {**model, "tests": tests},
               ["model", "tests"]),
            fn("forecast_protocol", "Forecast a charge under a model.", {**action_props("forecast_protocol"), **model},
               [p.name for p in spec["forecast_protocol"].parameters if p.name not in self.BOUND and
                p.name != "model_version"] + ["model"]),
            fn("rank_measurements", "Value candidate lab tests by how much they would change the design.",
               {**action_props("rank_measurements"), **model},
               [p.name for p in spec["rank_measurements"].parameters if p.name not in self.BOUND and
                p.name != "model_version"] + ["model"]),
            fn("optimize_protocol", "Search for the fastest charge every member of a model says meets the limits "
                                    f"(anode >= +{MARGIN_MV:g} mV, cell <= {T_LIMIT_C:g} degC).",
               {**action_props("optimize_protocol"), **model}, ["ambient", "model"]),
            fn("lab_charge", f"A lab test in the simulated lab (computational; at most {LAB_TESTS}): a 10-80 % "
                             "charge from 10 % SOC, then a rest. Returns its test number and what it measured.",
               action_props("lab_charge"),
               [p.name for p in spec["lab_charge"].parameters if p.name not in self.BOUND]),
            fn("run_analysis", "Run your own Python analysis of the supplied files in a fresh sandbox with no network "
                               "and no credentials (numpy, scipy, pandas; files at /work/inputs/<name>).", {
                "code": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}},
                "timeout_s": {"type": "number", "minimum": 5, "maximum": 120}}, ["code"]),
            fn("conclude", "Finish: the design you recommend (by its number from optimize_protocol) and why.", {
                "design": {"type": "integer", "minimum": 1}, "text": {"type": "string"}}, ["text"]),
        ]

    def call(self, name: str, args: dict) -> dict:
        try:
            out = getattr(self, "t_" + name)(**args)
        except AttributeError:
            out = {"error": f"no tool {name}"}
        except (TypeError, ValueError, KeyError, RuntimeError) as e:
            out = {"error": f"{type(e).__name__}: {e}"[:2000]}
        self.calls.append({"tool": name, "arguments": args, "error": out.get("error") if isinstance(out, dict) else None})
        return out

    def _path(self, file: str) -> Path:
        if file not in self.files:
            raise ValueError(f"no file {file!r}; the files are {', '.join(sorted(self.files))}")
        return self.files_dir / file

    def t_read_file(self, file, start_line=1, lines=30):
        return records.inspect(self._path(file), start_line=int(start_line), lines=int(lines))

    def t_column_stats(self, file, header_row, delimiter=None, decimal=".", skip_rows=0):
        return records.inspect(self._path(file), start_line=1, lines=1, header_row=int(header_row),
                               delimiter=delimiter, decimal=decimal, skip_rows=int(skip_rows))

    def t_define_table(self, name, file, header_row, time_column, columns, delimiter=None, decimal=".", skip_rows=0,
                       time_unit="s", nominal_capacity_Ah=None):
        roles = ("current", "voltage", "surface_temperature", "chamber_temperature")
        if not isinstance(columns, dict) or not columns or set(columns) - set(roles):
            raise ValueError(f"columns maps roles ({', '.join(roles)}) to {{column, unit}}")
        target = {q.name: q.unit for q in lab.SPEC.quantities}
        spec = TableSpec(file=file, delimiter=delimiter, header_row=int(header_row), skip_rows=int(skip_rows),
                         decimal=decimal, time_column=time_column,
                         columns={c["column"]: ChannelSpec(quantity=role, unit=c["unit"])
                                  for role, c in columns.items()})
        table = datasets._table(self._path(file), spec)
        t = units.convert(table.t, time_unit, "s") if time_unit != "s" else list(table.t)
        data = {role: units.convert(table.columns[c["column"]], c["unit"], target[role]) for role, c in columns.items()}
        self.tables[name] = {"t": t, "data": data, "file": file,
                             "capacity_Ah": float(nominal_capacity_Ah or fc.CAPACITY_AH)}
        return {"table": name, "rows": len(t), "t_range_s": [t[0], t[-1]],
                "columns": {role: {"min": min(v), "max": max(v), "unit": target[role]} for role, v in data.items()}}

    def _table(self, name) -> dict:
        if name not in self.tables:
            raise ValueError(f"no table {name!r}; define it first")
        return self.tables[name]

    def t_segments(self, table, detail=False, start=1):
        """The same computed segments arm 1 sees, in the same two forms: the short lines of its investigation
        (segments of at least records.SEGMENT_MINUTES) or every segment in full, paged so no listing is cut."""
        tb = self._table(table)
        if "current" not in tb["data"]:
            raise ValueError("the table has no current column")
        d = tb["data"]
        segs = records.segments(tb["t"], d["current"], voltage=d.get("voltage"),
                                temperature=d.get("surface_temperature"), chamber=d.get("chamber_temperature"),
                                capacity_ah=tb["capacity_Ah"])
        if not detail:
            shown = [records._segment_row(s) for s in segs if s["duration_min"] >= records.SEGMENT_MINUTES]
            return {"table": table, "segments": shown + [
                f"{len(segs) - len(shown)} shorter segments: segments(table, detail=true)"]}
        first = max(int(start), 1)
        page = [s for s in segs if first <= s["segment"] < first + SEGMENT_PAGE]
        more = first + SEGMENT_PAGE <= len(segs)
        return {"table": table, "of": len(segs), "segments": page,
                **({"next_start": first + SEGMENT_PAGE} if more else {})}

    def t_cut_test(self, table, t_start_s, t_end_s, mode, start_soc, ambient_degC=None):
        tb = self._table(table)
        if "surface_temperature" not in tb["data"] or "current" not in tb["data"]:
            raise ValueError("a test needs the table's surface_temperature and current")
        idx = records.window(tb["t"], float(t_start_s), float(t_end_s))
        series = {q: {"t": [tb["t"][i] for i in idx], "y": [v[i] for i in idx],
                      "unit": next(x.unit for x in lab.SPEC.quantities if x.name == q)}
                  for q, v in tb["data"].items()}
        win = {"t_start_s": float(t_start_s), "t_end_s": float(t_end_s), "segment": 1,
               "documented_ambient_degC": None if ambient_degC is None else float(ambient_degC),
               "capacity_Ah": tb["capacity_Ah"]}
        m = {"discharge": 1, "charge": 2}[mode]
        params = {"record": 1, "segment": 1, "mode": float(m), "start_soc": float(start_soc),
                  "record_ref": f"agent/{table}", "window": self._json(win),
                  "series": self._json({q: f"agent:{table}:{q}" for q in series})}
        result = self._execute(lab.RECORD_ACTION, params, {"record": {"window": win, "series": series}})
        out = self._summary(result)
        if result["status"] != "ok":
            return out
        sc = out["scalars"]
        ch = result["series"]
        body = {"t": ch["t"], "y": ch["channels"]["surface_temperature"]["values"], "unit": "degC"}
        self.tests.append({"kind": "record", "table": table, "window": win, "mode": m, "start_soc": float(start_soc),
                           "body": body, "source_params": {
                               "kind": "record", "mode": m, "start_soc": float(start_soc),
                               "current_A": float(sc["test_current"]), "ambient": float(sc["ambient_temperature"]),
                               "t0_degC": float(sc["temperature_start"]),
                               "duration_s": float(sc["test_duration"]) * 60.0}})
        return {"test": len(self.tests), **out}

    def t_fit_thermal(self, family, tests):
        sealed, staged = self._sources(tests)
        params = {"family": float(family), "fit_on": sum(1 << (n - 1) for n in tests),
                  "sources": self._json(sealed), "base": self._json(self.models[0])}
        result = self._execute("fit_thermal", params, {"sources": staged})
        out = self._summary(result)
        if result["status"] == "ok":
            d = result["details"]
            self.models.append({"version": len(self.models), "family": d["family"], "estimate": d["estimate"],
                                "members": d["members"], "region_95": d["region_95"], "result_id": None,
                                "validity": {**d["validity"], "lab_tests": list(tests)},
                                "consistent_with_nominal": bool(d["nominal"]["inside_region"]),
                                "fit_rmse_K": result["scalars"]["thermal_fit_rmse"]["value"],
                                "adequate": bool(d["adequate"]), "noise_K": d["noise_K"],
                                "sigma_used_K": d["sigma_used_K"], "effective_ratio": d["effective_ratio"]})
            out["model"] = len(self.models) - 1
        return out

    def t_check_model(self, model, tests):
        m = self._model(model)
        if overlap := sorted(set(m["validity"].get("lab_tests", [])) & set(tests)):
            raise ValueError(f"test {overlap[0]} was fitted by model {model}")
        sealed, staged = self._sources(tests)
        params = {"model_version": int(model), "test_on": sum(1 << (n - 1) for n in tests),
                  "model": self._json(m), "sources": self._json(sealed)}
        result = self._execute("check_model", params, {"sources": staged})
        self.checks.setdefault(int(model), []).append(
            {"request_id": f"check-{self.runs}", "result_id": f"check-{self.runs}",
             "rmse_K": float(result["scalars"]["heldout_rmse"]["value"]) if result["status"] == "ok" else None,
             "records_only": bool(tests) and all(self.tests[n - 1]["kind"] == "record" for n in tests)})
        return self._summary(result)

    def _model_action(self, action, model, args, limits=False):
        params = {**args, "model_version": int(model), "model": self._json(self._model(model)),
                  **(self._limits() if limits else {})}
        return self._execute(action, params, {})

    def t_forecast_protocol(self, model, **args):
        r = self._model_action("forecast_protocol", model, args)
        out = self._summary(r)
        out.pop("details", None)
        return out

    def t_rank_measurements(self, model, **args):
        return self._summary(self._model_action("rank_measurements", model, args, limits=True))

    def t_optimize_protocol(self, model, ambient=AMBIENT):
        out = self._summary(self._model_action("optimize_protocol", model, {"ambient": float(ambient)}, limits=True))
        sc = out["scalars"]
        if sc.get("design_found") == "yes":
            self.designs.append({"c_rates": [float(sc[c]) for c in fc.C_RATES], "model_version": int(model)})
            out["design"] = len(self.designs)
        return out

    def t_lab_charge(self, **args):
        if self.lab_tests >= LAB_TESTS:
            raise ValueError(f"the lab-test budget ({LAB_TESTS}) is spent")
        params = {**args, "lab": lab.commitment(self.condition)}
        result = self._execute("lab_charge", params, {"condition": self.condition, "request_id": f"lab-{self.runs}"})
        self.lab_tests += 1
        out = self._summary(result)
        if result["status"] != "ok":
            return out
        ch = result["series"]
        body = {"t": ch["t"], "y": ch["channels"]["surface_temperature"]["values"], "unit": "degC"}
        self.tests.append({"kind": "lab", "params": args, "body": body,
                           "source_params": {k: v for k, v in args.items() if k in (*fc.C_RATES, "ambient",
                                                                                     "rest_min")},
                           "noise_K": out["scalars"].get("noise_temperature")})
        picks = [i for i, x in enumerate(ch["t"]) if i == 0 or int(x // 60) > int(ch["t"][i - 1] // 60)]
        return {"test": len(self.tests), **out, "surface_temperature_each_minute": {
            "t_min": [round(ch["t"][i] / 60, 2) for i in picks], "degC": [body["y"][i] for i in picks]}}

    def t_run_analysis(self, code, files=None, timeout_s=60):
        if self.analysis_runner is None:
            from translucent.sandbox.runner import run_untrusted
            self.analysis_runner = run_untrusted
        names = files or sorted(self.files)
        inputs = {str(n): self._path(str(n)).read_bytes() for n in names}
        run = self.analysis_runner(code, inputs, timeout_s=float(timeout_s))
        return {"status": run.status, "exit_code": run.exit_code, "reason": run.reason,
                "stdout": run.stdout[-6000:], "stderr": run.stderr[-3000:]}

    def t_conclude(self, text, design=None):
        if design is not None and not (isinstance(design, int) and 1 <= design <= len(self.designs)):
            raise ValueError(f"design must be 1 to {len(self.designs)}" if self.designs else "no design yet")
        self.concluded = {"text": text, "design": design}
        return {"concluded": True}

    def final_design(self) -> tuple[dict | None, dict | None]:
        if not self.concluded or self.concluded.get("design") is None:
            return None, None
        d = self.designs[self.concluded["design"] - 1]
        return d, self.models[d["model_version"]]


def task_message(case: Case) -> str:
    t = lab.OBJECTIVE_TEMPLATE
    limits = "; ".join(c["label"] for c in t["constraints"] if c.get("label"))
    return (f"Objective: {t['statement']['fastest_feasible']} The ambient setpoint is {AMBIENT:g} degC. The supplied "
            "records come from earlier tests of the same cell type; lab tests run in a simulated lab (computational) "
            f"whose fixture may differ from the records'. Limits: {limits}. The charge is 10-80 % in four equal SOC "
            "steps, each at its own C-rate.\n\n"
            f"Files: {', '.join(f'{n} ({len(b)} bytes)' for n, b in sorted(case_files(case).items()))}.\n\n"
            f"Documentation from the engineer:\n{documentation(case)}\n\n"
            "How the tools work:\n" + general_guidance())


# the lab system's own working rules, worded for the general agent's functions (models by number, tests by number)
GUIDANCE_WORDS = (("0 is nominal (the engineer's calibration in another fixture)",
                   "0 is the parameter set as shipped, calibrated to no fixture"),
                  ("from the lab tests you name (fit_on, a bitmask: test n is bit n-1)",
                   "from the tests you name (cut_test and lab_charge number them)"),
                  ("read_series on a lab test, ", ""), ("model_version", "model"), ("Model versions", "Models"))


def general_guidance() -> str:
    text = lab.PLANNER_GUIDANCE
    for old, new in GUIDANCE_WORDS:
        if old not in text:
            raise HarnessError(f"the lab guidance no longer says {old!r}; reword the general agent's copy")
        text = text.replace(old, new)
    # the rule Translucent enforces on designs, stated (not enforced) for the general agent, so both arms know it
    return text + "\n- The rule for a recommendation: " + lab.STANDING_RULE.replace("model version", "model") + \
        ". When no model explains the records, say so in conclude, with what measurement or model would settle it."


def run_general(run_dir: Path, case: Case, seal: dict, cap: GlobalCap, *, client_factory=None,
                analysis_runner=None, log=print) -> dict:
    agent = GeneralAgent(run_dir, case, seal, analysis_runner)
    settings = Settings(profile="meta", budget_usd=MODEL_USD, max_tokens=MAX_TOKENS, step_cap=STEP_CAP,
                        length_max_tokens=LENGTH_MAX_TOKENS)
    session = CappedSession(settings, run_dir / "agent" / "tape", cap=cap,
                            client=client_factory() if client_factory else None)
    messages = [{"role": "system", "content": GENERAL_PROMPT}, {"role": "user", "content": task_message(case)}]
    schemas = agent.schemas()
    stop, nudges = "", 0
    spend = run_dir / "agent-spend.json"
    try:
        while len(agent.calls) < AGENT_CALLS and agent.concluded is None:
            try:
                response = session.chat(messages, schemas)
            finally:
                spend.write_text(json.dumps({"spent_usd": session.spent_usd}), encoding="utf-8")
            messages.append(assistant_message(response))
            calls = response.get("tool_calls") or []
            if not calls:
                nudges += 1
                if nudges > 2:
                    stop = "the agent stopped calling tools"
                    break
                messages.append({"role": "user", "content": "Continue with the tools, or call conclude."})
                continue
            for c in calls:
                f = c["function"]
                try:
                    args = json.loads(f.get("arguments") or "{}")
                except json.JSONDecodeError:
                    out = {"error": "the arguments are not JSON"}
                else:
                    out = agent.call(f["name"], args if isinstance(args, dict) else {})
                text = json.dumps(out, default=str)
                if len(text) > TOOL_CHARS:
                    text = text[:TOOL_CHARS] + f"... [cut: {len(text)} characters]"
                messages.append({"role": "tool", "tool_call_id": c.get("id"), "content": text})
                log(f"  {f['name']}: {'error' if isinstance(out, dict) and out.get('error') else 'ok'}")
        stop = stop or ("concluded" if agent.concluded else f"tool-call cap of {AGENT_CALLS} reached")
    except StopCampaign as e:
        stop = f"{e.args[0]}: {e.args[1] if len(e.args) > 1 else ''}"
    (run_dir / "agent" / "calls.json").write_text(json.dumps(agent.calls, indent=1, default=str), encoding="utf-8")
    return {"stop": stop, "agent": agent, "spent_usd": session.spent_usd}


class RecordsCap(GlobalCap):
    """The evaluation's model-spend limit: the run ledgers' spend (arm 1) plus each general agent's (arm 2)."""

    def spent(self) -> float:
        total = super().spent()
        for d in (p for base in (self.out, self.out / "void") if base.is_dir() for p in base.iterdir() if p.is_dir()):
            f = d / "agent-spend.json"
            if f.is_file():
                total += float(json.loads(f.read_text(encoding="utf-8"))["spent_usd"])
        return total


# ------------------------------------------------------------------ freeze

FROZEN_TREES = ("translucent", "evals")     # every Python module of the product and the harness
FROZEN_DATA = ("data/chen2020-lgm50/manifest.json", "data/raw/chen2020-lgm50/LGM50_cell02.csv",
               "data/raw/chen2020-lgm50/LGM50_cell03.csv", "data/raw/chen2020-lgm50/LGM50_cell04.csv")


def frozen_files() -> list[str]:
    found = {f for f in FROZEN} | set(FROZEN_DATA)
    for tree in FROZEN_TREES:
        found |= {p.relative_to(ROOT).as_posix() for p in (ROOT / tree).rglob("*.py")
                  if "__pycache__" not in p.parts}
    return sorted(found)


def freeze_digest(out: Path | None = None) -> dict:
    """sha256 of every frozen file and, given the evaluation folder, of its cached seals."""
    files = {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in frozen_files()}
    from importlib import metadata
    for pkg in ("pybamm", "numpy", "scipy", "casadi", "pydantic"):
        try:
            files[f"package/{pkg}"] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            files[f"package/{pkg}"] = "absent"
    if out is not None:
        for seal in sorted((Path(out) / "seals").glob("cell*.json")):
            files[f"seals/{seal.name}"] = hashlib.sha256(seal.read_bytes()).hexdigest()
    return {"files": files, "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


def plan_names(cases, arms, repeats) -> list[str]:
    return [run_name(c, a, n) for c in cases for a in arms for n in (repeats if a in MODEL_ARMS else [1])]


def freeze(out: Path, cases=("H1", "H2", "H3"), arms=ARMS, repeats=REPEATS) -> dict:
    """Record the implementation, data and seals, and pre-register the scored runs and the primary metric."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if (out / "freeze.json").is_file():
        raise HarnessError("already frozen; a new freeze needs a new --out")
    missing = [CASES[c].withheld for c in cases if CASES[c].dev_seal is None and
               not (out / "seals" / f"{CASES[c].withheld}.json").is_file()]
    if missing:
        raise HarnessError(f"no seal for {', '.join(sorted(set(missing)))}: run `prepare` first")
    d = freeze_digest(out)
    plan = {"runs": plan_names(cases, arms, repeats),
            "primary": "the recommended design (Translucent: the view's recommended_design; the general agent: its "
                       "concluded design) assessed under the seal, with the standing of its model; a run that "
                       "recommends nothing is reported as declined",
            "secondary": "the last design found, assessed the same way"}
    (out / "freeze.json").write_text(json.dumps({**d, "plan": plan, "at": time.time()}, indent=1), encoding="utf-8")
    return d


def check_frozen(out: Path, name: str | None = None) -> str:
    path = Path(out) / "freeze.json"
    if not path.is_file():
        raise HarnessError("scored cases run only after `freeze`")
    saved = json.loads(path.read_text(encoding="utf-8"))
    now = freeze_digest(out)
    if now["sha256"] != saved["sha256"]:
        changed = sorted(f for f in set(now["files"]) | set(saved["files"])
                         if now["files"].get(f) != saved["files"].get(f))
        raise HarnessError(f"the implementation changed since the freeze: {', '.join(changed)}")
    if name is not None and name not in (saved.get("plan") or {}).get("runs", []):
        raise HarnessError(f"{name} is not in the run list registered at the freeze")
    return now["sha256"]


# ------------------------------------------------------------------ one run

def campaign_designs(L: Ledger, cid: str) -> tuple[tuple[dict | None, dict | None], tuple[dict | None, dict | None]]:
    """((recommended design, its model), (last design found, its model)) of a campaign, from its view, with the
    model versions read under the campaign's objective (an investigation from records starts from the shipped
    parameter set, not the lab system's calibrated nominal)."""
    from translucent import loop_view
    learning = loop_view.view(L, cid, {"kind": "live", "api": "", "ledger": "eval"}).get("learning") or {}
    versions = lab.model_versions(L, request_states(L, cid), current_objective(L, cid))

    def pair(d):
        return (d, versions[int(d["model_version"])]) if d else (None, None)
    return pair(learning.get("recommended_design")), pair(learning.get("best_design"))


def run_name(case: str, arm: str, n: int) -> str:
    return f"{case}-{arm}-{n}"


def check_live(arm: str, live: bool, cap_usd: float | None) -> None:
    if arm in MODEL_ARMS and not live:
        raise HarnessError(f"the {arm} arm calls a paid model: it runs only with --live and --cap-usd")
    if arm in MODEL_ARMS and (cap_usd is None or not math.isfinite(cap_usd) or cap_usd <= 0):
        raise HarnessError(f"the {arm} arm needs --cap-usd: the evaluation's total model-spend limit in dollars")


def run_one(out: Path, case_name: str, arm: str, n: int, *, live: bool = False, cap_usd: float | None = None,
            client_factory: Callable | None = None, analysis_runner: Callable | None = None, log=print) -> dict:
    if arm not in ARMS or case_name not in CASES:
        raise HarnessError(f"unknown case or arm: {case_name} {arm}")
    check_live(arm, live, cap_usd)
    case = CASES[case_name]
    frozen = check_frozen(out, run_name(case_name, arm, n)) if case.scored else None
    name = run_name(case_name, arm, n)
    run_dir = Path(out) / name
    if (run_dir / "run.json").is_file():
        raise HarnessError(f"{name} has already run")
    if run_dir.exists():
        raise HarnessError(f"{name} was interrupted: its folder exists without run.json; remove it to run again")
    cap = RecordsCap(out, cap_usd) if arm in MODEL_ARMS else None
    if cap is not None and (spent := cap.spent()) >= cap.limit_usd:
        raise HarnessError(f"the evaluation has spent ${spent:.4f} of ${cap.limit_usd:.2f}; {name} is not started")
    seal = seal_for(out, case)
    started = time.time()
    run_dir.mkdir(parents=True)
    lab_case = LabCase(case.name, seal["h"], seal["k"], 0.0, seal["how"] if case.dev_seal else f"sealed from {case.withheld}",
                       "simulated follow-up, parameterised from a withheld real cell")
    info: dict = {"run": name, "case": case_name, "arm": arm, "n": n, "live": live, "frozen_sha256": frozen,
                  "started_at": started}
    previous = None
    if analysis_runner is not None:
        from translucent import loop_planner
        previous, loop_planner.ANALYSIS_RUNNER = loop_planner.ANALYSIS_RUNNER, analysis_runner
    try:
        if arm == "general_meta":
            got = run_general(run_dir, case, seal, cap, client_factory=client_factory,
                              analysis_runner=analysis_runner, log=log)
            agent = got["agent"]
            design, model = agent.final_design()
            last = (agent.designs[-1], agent.models[agent.designs[-1]["model_version"]]) if agent.designs else \
                (None, None)
            info.update(stop=got["stop"], spent_usd=got["spent_usd"], solver_runs=agent.runs)
            metrics = general_metrics(agent, case)
        else:
            L, cid = open_campaign(run_dir / "ledger", case, arm, name, seal)
            try:
                info["campaign_id"] = cid
                if arm == "numeric":
                    info["numeric"] = run_numeric(L, cid, case)
                    info["stop"] = "policy finished"
                else:
                    outcome = drive(L, cid, _NoAmendment, model_session_for(cap, client_factory), log=log)
                    info.update(stop=outcome["stop"], runner_problems=outcome.get("runner_problems", []),
                                turns=[{k: t.get(k) for k in ("turn", "status", "reason", "proposed", "spent_usd")}
                                       for t in outcome.get("turns", [])])
                (design, model), last = campaign_designs(L, cid)
                metrics = campaign_metrics(L, cid, case)
            finally:
                L.db.close()
        info["campaign_wall_s"] = round(time.time() - started, 1)
        # after the run stops, outside its budget. Primary: the design the run recommends (Translucent's
        # recommended design; the design the general agent concludes with), None when it recommends none.
        # Secondary: the last design found, recommended or not.
        assessment = assess_design(design, model, lab_case)
        assessment_last = assess_design(last[0], last[1], lab_case)
        (run_dir / "assessment.json").write_text(json.dumps(
            {"final_design": design, "case": assessment, "last_design": last[0], "last_design_case": assessment_last},
            indent=1, sort_keys=True, default=str), encoding="utf-8")
        (run_dir / "revealed-condition.json").write_text(json.dumps(seal, indent=1, sort_keys=True),
                                                         encoding="utf-8")
    finally:
        if analysis_runner is not None:
            from translucent import loop_planner
            loop_planner.ANALYSIS_RUNNER = previous
    info.update(ended_at=time.time(), harness_wall_s=round(time.time() - started, 1))
    (run_dir / "run.json").write_text(json.dumps(info, indent=1, sort_keys=True, default=str), encoding="utf-8")
    standing = metrics.get("final_design_standing") or {}
    metrics.update(final_design=design, assessment=assessment, last_design=last[0], assessment_last=assessment_last,
                   recommended=design is not None,
                   # a recommendation whose model never met the standing rule (only the general agent can make one)
                   overclaim=design is not None and standing.get("supported") is not True,
                   arm=arm, case=case_name, n=n,
                   stop=info.get("stop"), wall_s=info["campaign_wall_s"],
                   model_usd=info.get("spent_usd", metrics.get("model_usd")))
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=1, sort_keys=True, default=str),
                                          encoding="utf-8")
    return info


# ------------------------------------------------------------------ metrics

def _tool_results(recs, tool: str) -> list[tuple[dict, dict]]:
    calls = {}
    out = []
    for r in recs:
        if r.type != "planner":
            continue
        p = r.data.get("payload") or {}
        key = (p.get("turn"), p.get("step"))
        if r.data.get("phase") == "tool_call" and p.get("tool") == tool:
            calls[key] = p.get("arguments") or {}
        elif r.data.get("phase") == "tool_result" and p.get("tool") == tool:
            out.append((calls.get(key, {}), p.get("result") or {}))
    return out


def import_correctness(L: Ledger, obj, cid: str, case: Case) -> dict:
    """Arms 1 and 3: each supplied file's imported channels against the reference import (reference_correctness).
    Records are matched by the file they were read from, so the names an arm gives its specimens do not matter."""
    got = {}
    for ref, rec in records.campaign_records(L, obj, cid):
        for name in rec.files:
            got.setdefault(name, rec)

    def have(file, q):
        rec = got.get(file)
        found = lab._record_arrays(L, rec, q) if rec is not None else None
        return None if found is None else (found[1], found[2])
    return reference_correctness(Path(L.root).parent, case, have, set(got))


def table_correctness(agent: "GeneralAgent", case: Case) -> dict:
    """Arm 2: each supplied file's table (define_table) against the reference import, in the same terms."""
    tables = {}
    for tb in agent.tables.values():
        tables.setdefault(tb["file"], tb)

    def have(file, q):
        tb = tables.get(file)
        return None if tb is None or q not in tb["data"] else (list(tb["t"]), list(tb["data"][q]))
    return reference_correctness(agent.dir, case, have, set(tables))


def reference_correctness(folder: Path, case: Case, have, files: set) -> dict:
    """reference specimen id -> quantity -> {compared, of, max_abs_diff} (system units, at the reference's times),
    "missing", or "not imported". `have(file, quantity)` gives an arm's (t, values) for a file, or None."""
    scratch = Ledger(Path(folder) / "reference-import")
    try:
        stored = datasets.import_dataset(scratch, reference_manifest(case), {
            n: _write(Path(folder) / "reference-files", n, b) for n, b in case_files(case).items()})
        out = {}
        for r in stored.records:
            file = next((n for n in r.files if n in files), None)
            row = {}
            for q in lab.RECORD_QUANTITIES:
                want = lab._record_arrays(scratch, r, q)
                if want is None:
                    continue
                got = have(file, q) if file is not None else None
                if got is None:
                    row[q] = "missing"
                    continue
                if list(got[0]) == list(want[1]):    # one clock: element by element (the files repeat two times)
                    diffs = [abs(x - y) for x, y in zip(got[1], want[2])]
                else:
                    diffs = paired_diffs(got[0], got[1], want[1], want[2])
                row[q] = {"compared": len(diffs), "of": len(want[1]), "max_abs_diff": max(diffs) if diffs else None}
            out[r.specimen.id] = row if file is not None else "not imported"
        return out
    finally:
        scratch.db.close()


def paired_diffs(got_t, got_v, want_t, want_v) -> list[float]:
    """|got - want| at each reference time the arm also has. Rows that share a time (a step change logged twice)
    pair in file order, the k-th row at a time with the k-th row at that time, never the last row at a time with
    every row at it."""
    at: dict[float, list[float]] = {}
    for t, v in zip(got_t, got_v):
        at.setdefault(t, []).append(v)
    seen: dict[float, int] = {}
    out = []
    for t, y in zip(want_t, want_v):
        k = seen[t] = seen.get(t, -1) + 1
        if k < len(at.get(t, ())):
            out.append(abs(at[t][k] - y))
    return out


def _write(folder: Path, name: str, data: bytes) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return folder / name


def campaign_metrics(L: Ledger, cid: str, case: Case) -> dict:
    recs = chain.records(L, cid)
    states = request_states(L, cid, recs)
    obj = current_objective(L, cid)
    agent = [s for s in states.values() if actor_kind(s.proposed_by) == "agent"]
    done = [s for s in agent if s.result is not None]

    def by(action):
        return [s for s in done if s.action == action]
    imports = _tool_results(recs, "import_records")
    refused = [p for p in recs if p.type == "planner" and p.data.get("phase") == "tool_result" and
               "error" in ((p.data.get("payload") or {}).get("result") or {})]
    versions = lab.model_versions(L, states, obj)
    tests = lab.lab_tests(states)
    record_tests = [s for s in tests if s.action == lab.RECORD_ACTION]
    reference = {}
    for ref, rec in records.campaign_records(L, obj, cid):
        reference[ref] = set(reference_segments(L, rec))
    chosen = []
    for s in record_tests:
        p = dict(s.data.params)
        chosen.append({"record_ref": p["record_ref"], "segment": int(p["segment"]), "mode": int(float(p["mode"])),
                       "start_soc": float(p["start_soc"]),
                       "reference": int(p["segment"]) in reference.get(p["record_ref"], set())})
    fits = [(s, lab._result_json(L, s)) for s in by("fit_thermal")]
    from translucent import loop_view
    learning = loop_view.view(L, cid, {"kind": "live", "api": "", "ledger": "eval"}).get("learning") or {}
    designs = learning.get("designs") or []
    lab_charges = by("lab_charge")
    first_lab = lab_charges[0].result.seq if lab_charges else math.inf
    before_lab = [d for d in designs if d.get("request_id") and states[d["request_id"]].result.seq < first_lab]
    after_lab = [d for d in designs if d.get("request_id") and states[d["request_id"]].result.seq > first_lab]
    steps = {
        "read_files": len(_tool_results(recs, "inspect_upload")) > 0 or bool(imports),
        "import_accepted": any("error" not in r for _, r in imports),
        "missing_information_listed": any((a.get("missing_information") or []) for a, r in imports if "error" not in r),
        "record_tests": len(record_tests) > 0,
        "fit": any(b.get("status") == "ok" for _, b in fits),
        "held_out_check": any(lab._result_json(L, s).get("status") == "ok" for s in by("check_model")),
        "design_from_controller": any(d.get("design_found") for d in designs),
        "follow_up_lab_test": len(lab_charges) > 0,
        "ingested_and_revisited": bool(after_lab) or any(s.result.seq > first_lab for s, _ in fits),
        "concluded": chain.concluded(L, cid),
    }
    last = versions[-1] if len(versions) > 1 else None
    return {
        "steps": steps, "steps_completed": sum(bool(v) for v in steps.values()), "steps_total": len(steps),
        "import_attempts": len(imports), "imports_refused": sum("error" in r for _, r in imports),
        "missing_information": [a.get("missing_information") for a, r in imports if "error" not in r],
        "import_correctness": import_correctness(L, obj, cid, case) if any("error" not in r for _, r in imports)
        else None,
        "record_tests": chosen, "record_tests_reference": sum(c["reference"] for c in chosen),
        "fits": [{"family": b["details"]["family"] if b.get("details") else None, "status": b.get("status"),
                  "estimate": (b.get("details") or {}).get("estimate"), "sources": (b.get("details") or {}).get(
                      "validity", {}).get("lab_tests")} for _, b in fits],
        "final_model": None if last is None else {k: last.get(k) for k in ("version", "family", "estimate",
                                                                           "region_95", "adequate", "fit_rmse_K")},
        "designs": [{k: d.get(k) for k in ("model_version", "c_rates", "charge_time_80", "max_temperature",
                                            "min_anode_potential")} for d in designs],
        # whether the design the run ends with rests on a model with standing (lab.STANDING_RULE), what
        # Translucent recommends (None when no model with standing stands behind a design) and the outcome
        "final_design_standing": _standing_of(learning.get("recommended_design"), lab.standings(L, states, obj)),
        "last_design_standing": _standing_of(learning.get("best_design"), lab.standings(L, states, obj)),
        "recommended_design": {k: learning["recommended_design"].get(k) for k in ("model_version", "c_rates")}
        if learning.get("recommended_design") else None,
        "outcome": (lambda c: c.data.get("outcome") if c is not None else None)(chain.concluded(L, cid)),
        "revised_after_follow_up": bool(after_lab) and bool(before_lab) and
        after_lab[-1].get("c_rates") != before_lab[-1].get("c_rates"),
        "refused_tool_calls": len(refused),
        "questions_asked": sum(e.type == "question.asked" for e in L.events(cid)),
        "manual_corrections": 0, "code_or_config_changes": 0,
        "requests": len(agent), "requests_completed": len(done),
        "model_usd": round(model_spend(L, cid), 6),
    }


def _standing_of(design: dict | None, standing: list[dict]) -> dict | None:
    """The standing (lab.model_standing) of the model a final design used; None without a design."""
    if not design:
        return None
    v = int(design["model_version"])
    if not 0 <= v < len(standing):
        raise HarnessError(f"the design names model {v}; only {len(standing)} are recorded")
    return {"model_version": v, **standing[v]}


def general_metrics(agent: GeneralAgent, case: Case) -> dict:
    calls = agent.calls

    def used(tool):
        return [c for c in calls if c["tool"] == tool]

    def ok(tool):
        return [c for c in used(tool) if not c["error"]]
    fits = [m for m in agent.models[1:]]
    lab_n = [i for i, t in enumerate(agent.tests, start=1) if t["kind"] == "lab"]
    after = [d for d in agent.designs if lab_n and any(n in agent.models[d["model_version"]]["validity"].get(
        "lab_tests", []) for n in lab_n)]
    steps = {
        "read_files": bool(used("read_file") or used("column_stats")),
        "import_accepted": bool(ok("define_table")),
        "missing_information_listed": None,          # no interface for it in this arm; read from the transcript
        "record_tests": any(t["kind"] == "record" for t in agent.tests),
        "fit": bool(fits),
        "held_out_check": bool(ok("check_model")),
        "design_from_controller": bool(agent.designs),
        "follow_up_lab_test": bool(lab_n),
        "ingested_and_revisited": bool(after),
        "concluded": agent.concluded is not None,
    }
    tables = {}
    for name, tb in agent.tables.items():
        tables[name] = {"file": tb["file"], "roles": sorted(tb["data"]), "rows": len(tb["t"])}
    last = fits[-1] if fits else None
    return {
        "steps": steps, "steps_completed": sum(bool(v) for v in steps.values() if v is not None),
        "steps_total": sum(v is not None for v in steps.values()),
        "tables": tables, "import_correctness": table_correctness(agent, case) if agent.tables else None,
        "record_tests": [{k: t.get(k) for k in ("table", "window", "mode", "start_soc")}
                                           for t in agent.tests if t["kind"] == "record"],
        "final_model": None if last is None else {k: last.get(k) for k in ("version", "family", "estimate",
                                                                           "region_95", "adequate", "fit_rmse_K")},
        "designs": agent.designs, "concluded_with": agent.concluded,
        "final_design_standing": _standing_of(
            agent.final_design()[0], [lab.model_standing(m, agent.checks.get(m["version"], [])) for m in agent.models]),
        "refused_tool_calls": sum(bool(c["error"]) for c in calls), "tool_calls": len(calls),
        "manual_corrections": 0, "code_or_config_changes": 0, "solver_runs": agent.runs,
    }


# ------------------------------------------------------------------ report

def run_dirs(out: Path) -> list[Path]:
    return sorted(p for p in Path(out).iterdir() if p.is_dir() and (p / "metrics.json").is_file())


def report(out: Path, md: Path | None = REPORT_MD) -> dict:
    runs = [json.loads((d / "metrics.json").read_text(encoding="utf-8")) for d in run_dirs(out)]
    freeze_path = Path(out) / "freeze.json"
    frozen = json.loads(freeze_path.read_text(encoding="utf-8")) if freeze_path.is_file() else {}
    done = {run_name(m["case"], m["arm"], m["n"]) for m in runs}
    missing = [n for n in (frozen.get("plan") or {}).get("runs", []) if n not in done]
    other_freeze = []
    for d in run_dirs(out):
        info = json.loads((d / "run.json").read_text(encoding="utf-8")) if (d / "run.json").is_file() else {}
        if info.get("frozen_sha256") and frozen and info["frozen_sha256"] != frozen.get("sha256"):
            other_freeze.append(d.name)
    seals = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (Path(out) / "seals").glob("cell*.json")}
    data = {"runs": runs, "seals": seals, "freeze": frozen or None,
            "not_run": [{"run": n, "why": "crashed or not run: no metrics.json" if (Path(out) / n).exists()
                         else "not run"} for n in missing],
            "other_freeze": other_freeze,
            "oracle": json.loads((Path(out) / "oracle.json").read_text(encoding="utf-8"))
            if (Path(out) / "oracle.json").is_file() else {}}
    if md is not None:
        Path(md).write_text(markdown(data), encoding="utf-8")
    return data


def _stands(s: dict | None) -> str:
    return "-" if s is None else "yes" if s.get("supported") else "no"


def _f(v, fmt="{:.2f}", none="none") -> str:
    return none if v is None else fmt.format(v)


def markdown(data: dict) -> str:
    lines = ["# Records to investigation: acceptance test", "",
             "Every lab result here is SIMULATED: the follow-up lab's condition is the (h, k) fitted to a withheld "
             "real cell's records. Case H3's files are a SYNTHETIC re-export of published ones. No engineer-time "
             "claim is made.", "",
             "Primary result: the design the run RECOMMENDS (Translucent: the latest design under a model that met "
             "the standing rule; the general agent: the design it concluded with), assessed under the seal. "
             "'declined' means no design was recommended. An overclaim is a recommendation whose model never met "
             "the rule. Regret is the recommended design's true t80 less the controller's with the seal as its "
             "model (oracle.json), for feasible designs. The last design found is the secondary result.", "",
             "| Case | Arm | Run | Steps | Imports (refused) | Record tests (reference) | Final h / k | Recommended "
             "C-rates | Model stands | Overclaim | True t80 (min) | Feasible | Violations | Regret (min) | "
             "Last design: t80, feasible | Model $ | Wall s | Stop |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in sorted(data["runs"], key=lambda m: (m["case"], m["arm"], m["n"])):
        a = m.get("assessment") or {}
        est = (m.get("final_model") or {}).get("estimate") or {}
        rates = (m.get("final_design") or {}).get("c_rates")
        o = ((data.get("oracle") or {}).get(m["case"]) or {}).get("assessment") or {}
        regret = a["true_t80_min"] - o["true_t80_min"] if a.get("feasible") and o.get("true_t80_min") else None
        al = m.get("assessment_last") or {}
        lines.append(
            f"| {m['case']} | {ARM_LABEL.get(m['arm'], m['arm'])} | {m['n']} | {m['steps_completed']}/"
            f"{m['steps_total']} | {m.get('import_attempts', len(m.get('tables') or {}))} "
            f"({m.get('imports_refused', '-')}) | {len(m.get('record_tests') or [])} "
            f"({m.get('record_tests_reference', '-')}) | {_f(est.get('h'))} / {_f(est.get('k'))} | "
            f"{', '.join(f'{x:g}' for x in rates) if rates else 'declined'} | "
            f"{_stands(m.get('final_design_standing'))} | {'yes' if m.get('overclaim') else 'no'} | "
            f"{_f(a.get('true_t80_min'))} | {('yes' if a.get('feasible') else 'no') if rates else '-'} | "
            f"{'; '.join(v.get('limit', '') for v in a.get('violations') or []) if rates else '-'} | "
            f"{_f(regret)} | {_f(al.get('true_t80_min'))}, {'yes' if al.get('feasible') else 'no'} | "
            f"{_f(m.get('model_usd'), '{:.3f}')} | {_f(m.get('wall_s'), '{:.0f}')} | {m.get('stop')} |")
    lines += ["", "## Seals (hidden from every arm until scored)", ""]
    for cell, s in sorted(data["seals"].items()):
        lines.append(f"- {cell}: h {s['h']:.3f}, k {s['k']:.3f} ({s['how']}; fit RMSE {s['fit_rmse_K']:.3f} K, "
                     f"adequate {s['adequate']})")
    if data.get("not_run"):
        lines += ["", "## Registered runs without a result (counted as failures, never dropped)", ""]
        lines += [f"- {r['run']}: {r['why']}" for r in data["not_run"]]
    if data.get("other_freeze"):
        lines += ["", "Runs made under a different freeze (not comparable): " + ", ".join(data["other_freeze"])]
    if data.get("freeze"):
        lines += ["", f"Implementation frozen at sha256 {data['freeze']['sha256']}."]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ command line

def prepare(out: Path, cases=tuple(CASES), log=print) -> dict:
    """Case files (for inspection) and seals; no model call. The seals run the real solver."""
    out = Path(out)
    made = {}
    for name in cases:
        case = CASES[name]
        folder = out / "cases" / name
        folder.mkdir(parents=True, exist_ok=True)
        for n, b in case_files(case).items():
            (folder / n).write_bytes(b)
        (folder / "documentation.txt").write_text(documentation(case), encoding="utf-8")
        (folder / "reference-manifest.json").write_text(json.dumps(reference_manifest(case), indent=1),
                                                        encoding="utf-8")
        t0 = time.time()
        seal = seal_for(out, case)
        log(f"{name}: sealed from {case.withheld}: h {seal['h']:.3f}, k {seal['k']:.3f} ({time.time() - t0:.0f} s)")
        made[name] = seal
    return made


def oracle(out: Path, cases=("H1", "H2", "H3"), log=print) -> dict:
    """The numerical controller with the sealed condition as its only model member, for each case: the design the
    same search finds when the model is exactly the simulated lab, assessed under that lab. A reference for regret
    (true t80 of a run's design less this), not an optimum: the search is greedy and keeps its own margins. No model
    call; written to <out>/oracle.json. Run after scoring, or apart from the runs: it reads the seals."""
    from translucent.systems import lgm50_lab_solver as solver
    done = {}
    path = Path(out) / "oracle.json"
    if path.is_file():
        done = json.loads(path.read_text(encoding="utf-8"))
    for name in cases:
        case = CASES[name]
        if name in done:
            continue
        seal = seal_for(out, case)
        t0 = time.time()
        found = solver.optimize_protocol({"margin_mV": MARGIN_MV, "t_limit_C": T_LIMIT_C, "ambient": AMBIENT},
                                         {"version": 0, "members": [{"h": seal["h"], "k": seal["k"]}]})
        if found.get("status") != "ok":
            raise HarnessError(f"{name}: the oracle search failed: {found.get('error')}")
        sc = {k: v[0] for k, v in (found.get("scalars") or {}).items()}
        design = {"c_rates": [float(sc[c]) for c in fc.C_RATES], "model_version": 0} \
            if sc.get("design_found") == "yes" else None
        lab_case = LabCase(case.name, seal["h"], seal["k"], 0.0, "oracle", "the sealed condition as the model")
        done[name] = {"seal": {"h": seal["h"], "k": seal["k"]}, "design": design,
                      "assessment": assess_design(design, None, lab_case), "wall_s": round(time.time() - t0, 1)}
        path.write_text(json.dumps(done, indent=1, sort_keys=True, default=str), encoding="utf-8")
        log(f"{name}: oracle {design and design['c_rates']} t80 {done[name]['assessment'].get('true_t80_min')}")
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.records_intake")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--cases", nargs="+", default=list(CASES), choices=list(CASES))
    f = sub.add_parser("freeze")
    f.add_argument("--out", type=Path, required=True)
    f.add_argument("--cases", nargs="+", default=["H1", "H2", "H3"], choices=[c for c in CASES if CASES[c].scored])
    f.add_argument("--arms", nargs="+", default=list(ARMS), choices=list(ARMS))
    f.add_argument("--repeats", nargs="+", type=int, default=list(REPEATS))
    r = sub.add_parser("run")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--cases", nargs="+", required=True, choices=list(CASES))
    r.add_argument("--arms", nargs="+", required=True, choices=list(ARMS))
    r.add_argument("--repeats", nargs="+", type=int, default=[1])
    r.add_argument("--live", action="store_true")
    r.add_argument("--cap-usd", type=float)
    o = sub.add_parser("oracle")
    o.add_argument("--out", type=Path, required=True)
    o.add_argument("--cases", nargs="+", default=["H1", "H2", "H3"], choices=list(CASES))
    rp = sub.add_parser("report")
    rp.add_argument("--out", type=Path, required=True)
    rp.add_argument("--md", type=Path, default=REPORT_MD)
    a = ap.parse_args(argv)
    if a.cmd == "prepare":
        prepare(a.out, a.cases)
    elif a.cmd == "freeze":
        try:
            d = freeze(a.out, a.cases, a.arms, a.repeats)
        except HarnessError as e:
            raise SystemExit(str(e)) from None
        print(d["sha256"])
    elif a.cmd == "oracle":
        oracle(a.out, a.cases)
    elif a.cmd == "run":
        for case in a.cases:
            for arm in a.arms:
                for n in (a.repeats if arm in MODEL_ARMS else [1]):
                    try:
                        info = run_one(a.out, case, arm, n, live=a.live, cap_usd=a.cap_usd)
                        print(f"{run_name(case, arm, n)}: {info.get('stop')}")
                    except HarnessError as e:
                        print(f"{run_name(case, arm, n)}: not run: {e}", file=sys.stderr)
    else:
        report(a.out, a.md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
