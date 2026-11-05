"""pybamm-fastcharge-lab: learn the cell's thermal behaviour from lab tests, then design the charge with what was learned.

The cell, the charge and the limits are pybamm-fastcharge's (LG M50, DFN with partially reversible plating, lumped
thermal, OKane2022; four constant-current steps from 10 to 80 % SOC). What differs is how the thermal model is held:
not one fixed heat-transfer coefficient, but versions of a small model family, each fitted to recorded lab tests and
kept with its sources, parameters, uncertainty and validity range.

Thermal model family (developer-provided):
    v0  nominal: h = 6 W/m^2/K, thermal-mass scale k = 1 (the calibration on the Chen 2020 discharges in their
        fixture; evals/thermal_records.py tests it on withheld records)
    1   cooling: h fitted, k = 1
    2   cooling and thermal mass: h and k fitted (k multiplies every specific heat capacity of the parameter set)
A fit returns the estimate, the 95 % region and its members (the estimate and the region's extremes, at most five).
Forecasts and the design search run every member; constraint quantities are the worst member's.

Actions
    lab_charge         a lab test in the simulated lab: the charge (and a rest), logging surface temperature,
                       voltage and current. Computational: the lab is a separate simulation whose condition is sealed
                       per campaign (`<ledger>/sealed/lab/<campaign>.json`) and never shown; the request carries only
                       its commitment. No anode potential: a lab cannot measure it.
    bench_charge       the same test on a real bench, run and uploaded by a qualified person (human executor).
    forecast_protocol  a charge under one model version: the estimate's series and every member's extremes.
    fit_thermal        fit family 1 or 2 to chosen lab tests; the result is the next model version.
    check_model        a model version's forecast of lab tests it did not fit (a held-out check).
    rank_measurements  which of four candidate lab tests the members disagree on most, and how far they disagree on
                       the design's anode margin (developer-provided acquisition menu).
    optimize_protocol  greedy step-down search for the fastest charge every member says meets the objective's limits.

Parameters the agent does not choose are bound by Translucent when a request is proposed (`bind`): the model version's
definition, the lab tests' series ids, the sealed lab's commitment, and the limits of the objective in force. Admission
derives them again from the ledger and refuses a request whose bound values differ (`admission_problems`), so a model,
a source or a limit cannot be typed in. Model revision never changes the objective: an engineer amends it.

What this does not show: a surface-temperature fit says nothing direct about core temperature, anode potential,
plating or high-rate behaviour; the simulated lab is the same model family with a different h (and k), an identifiable
mismatch by construction; robustness across members is not a physical safety guarantee.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

from translucent import units
from translucent.systems import lgm50_fastcharge as fc
from translucent.systems.base import (ActionSpec, Cost, ExecutionOutput, NotExecutable, Parameter, Quantity,
                                      ResultTable, SystemSpec, check_params as _check_params, file_digest)

SYSTEM_ID = "pybamm-fastcharge-lab"
RESULT_SCHEMA = "pybamm-fastcharge-lab-result/1"
NOMINAL = {"h": fc.HEAT_TRANSFER_W_M2K, "k": 1.0}
H_BOUNDS = (1.0, 50.0)
K_BOUNDS = (0.5, 2.5)
NOISE_K = 0.15                     # the simulated lab's thermocouple noise; a bench upload's default
NOISE_V = 0.001
FAMILIES = {1: "cooling", 2: "cooling and thermal mass"}
VIOLATION_PENALTY_MIN = 100.0      # rank_measurements: loss of a charge a member says breaks a limit (frozen)
TEST_COST_MIN = 1.0                # rank_measurements: one lab test's cost in the same minutes (frozen)
ADEQUATE_NOISE_MULTIPLE = 2.0      # a fit is in family when its RMSE is at most this many noise sigmas
ADEQUATE_FLOOR_K = 0.3
REST_CHOICES = (0.0, 30.0, 60.0)
MAX_TESTS = 16                     # tests (lab and record) a bitmask can name
MAX_VERSIONS = 30
LAB_ACTIONS = ("lab_charge", "bench_charge")
RECORD_ACTION = "record_test"      # a constant-current stretch of an imported record, as a test
TEST_ACTIONS = LAB_ACTIONS + (RECORD_ACTION,)
# The prior of an investigation from the engineer's own records: the parameter set as shipped, calibrated to no
# fixture (NOMINAL is the calibration on the Chen 2020 discharges, which such an investigation may be given).
SHIPPED = {"h": 10.0, "k": 1.0}
CC_DEPARTURE_MAX = 0.02            # a record test's current stays within 2 % of its median
MODEL_ACTIONS = ("forecast_protocol", "check_model", "rank_measurements", "optimize_protocol")
SEALED = ("sealed", "lab")         # under the ledger root

_PROTOCOL = {p.name: p for p in fc.SPEC.action("simulate_protocol").parameters}
_REST = Parameter("rest_min", "min", choices=REST_CHOICES, label="Rest after the charge, logged")
_VERSION = Parameter("model_version", "", lo=0, hi=MAX_VERSIONS, integer=True,
                     label="Model version: 0 is nominal, n the n-th thermal fit")
_MODEL = Parameter("model", "", label="The model version's definition (set by Translucent)")
_SOURCES = Parameter("sources", "", label="The lab tests used: number, result id, series id (set by Translucent)")
_LAB = Parameter("lab", "", label="Commitment to the simulated lab's sealed condition (set by Translucent)")
_BASE = Parameter("base", "", label="The nominal model the fit starts from (set by Translucent)")
_MARGIN = Parameter("margin_mV", "mV", lo=-100, hi=500, label="Plating margin of the objective in force")
_TLIMIT = Parameter("t_limit_C", "degC", lo=0, hi=120, label="Temperature limit of the objective in force")
_PROTO = tuple(_PROTOCOL[c] for c in fc.C_RATES) + (_PROTOCOL["ambient"],)

BOUND = {"lab_charge": ("lab",), "forecast_protocol": ("model",), "fit_thermal": ("sources", "base"),
         "check_model": ("model", "sources"), "rank_measurements": ("model", "margin_mV", "t_limit_C"),
         "optimize_protocol": ("model", "margin_mV", "t_limit_C"),
         RECORD_ACTION: ("record_ref", "window", "series")}
TEXT = {"model", "sources", "lab", "base", "record_ref", "window", "series"}

QUANTITIES = (
    Quantity("charge_time_80", "min", label="Time from 10 to 80 % SOC (a forecast: the slowest member)",
             tolerance=0.05),
    Quantity("min_anode_potential", "mV", label="Lowest anode potential vs Li/Li+ at the separator (a forecast: the "
             "lowest member; a lab cannot measure it)", tolerance=0.5),
    Quantity("max_temperature", "degC", label="Highest cell temperature (lab: surface thermocouple; forecast: the "
             "lumped temperature of the hottest member)", tolerance=0.1),
    Quantity("temperature_end", "degC", label="Surface temperature at the end of the test"),
    Quantity("noise_temperature", "delta_degC", label="The lab's stated thermocouple noise (1 sigma)"),
    Quantity("anode_spread", "mV", label="Members' range of the lowest anode potential"),
    Quantity("temperature_spread", "delta_degC", label="Members' widest temperature range"),
    Quantity("members", "1", label="Models run (the estimate and the region's extremes)"),
    Quantity("model_version", "1", label="Model version"),
    Quantity("extrapolation", "", kind="category", categories=("yes", "no"),
             label="Outside the model version's validity range"),
    Quantity("heat_transfer_coefficient", "W/m^2/K", label="Fitted heat-transfer coefficient h"),
    Quantity("thermal_mass_scale", "1", label="Fitted thermal-mass scale k"),
    Quantity("thermal_fit_rmse", "delta_degC", label="Fit residual, full DFN at the estimate"),
    Quantity("consistent_with_nominal", "", kind="category", categories=("yes", "no"),
             label="The nominal model lies inside the fit's 95 % region"),
    Quantity("heldout_rmse", "delta_degC", label="Held-out surface-temperature RMSE"),
    Quantity("heldout_max_error", "delta_degC", label="Held-out largest surface-temperature error"),
    Quantity("heldout_coverage", "1", label="Held-out samples inside the members' range (+- 2 noise)"),
    Quantity("best_measurement", "1", label="The test worth running (option number; 0: none is worth its cost)"),
    Quantity("best_disagreement", "delta_degC", label="Members' largest disagreement on that test"),
    Quantity("decision_spread", "mV", label="Members' range of the design's lowest anode potential"),
    Quantity("best_value", "min", label="The best test's expected reduction of the decision's loss, less its cost"),
    Quantity("decision_risk", "min", label="Expected loss of the best decision now (charge time + violation "
             "penalty)"),
    Quantity("fit_adequate", "", kind="category", categories=("yes", "no"),
             label="The fit explains its tests within the noise (no: a mechanism outside the family)"),
    Quantity("design_found", "", kind="category", categories=("yes", "no"),
             label="A design meets the limits for every member"),
    Quantity("evaluations", "1", label="Charges simulated by the search"),
    *(Quantity(c, "1/h", label=f"Design C-rate, {w} SOC") for c, w in zip(fc.C_RATES, fc.WINDOWS)),
    Quantity("status", "", kind="category", categories=("ok", "not_reached", "nonsense"), label="Run status"),
    Quantity("surface_temperature", "degC", kind="series", label="Surface temperature (lab)"),
    Quantity("cell_temperature", "degC", kind="series", label="Cell temperature (forecast, the estimate)"),
    Quantity("temperature_lo", "degC", kind="series", label="Lowest member's temperature"),
    Quantity("temperature_hi", "degC", kind="series", label="Highest member's temperature"),
    Quantity("anode_potential", "mV", kind="series", label="Anode potential (forecast, the estimate)"),
    Quantity("voltage", "V", kind="series", label="Terminal voltage"),
    Quantity("current", "A", kind="series", label="Charge current"),
    Quantity("chamber_temperature", "degC", kind="series", label="Chamber (ambient) temperature, as recorded"),
    Quantity("test_current", "A", label="Record test: median current magnitude"),
    Quantity("test_c_rate", "1/h", label="Record test: current per nominal capacity"),
    Quantity("test_duration", "min", label="Record test: duration"),
    Quantity("temperature_start", "degC", label="Record test: surface temperature at its start"),
    Quantity("temperature_rise", "delta_degC", label="Record test: highest surface temperature less the start"),
    Quantity("ambient_temperature", "degC", label="Record test: chamber median (recorded), or the documented value"),
    Quantity("current_departure", "1", label="Record test: largest relative departure of the current from its median"),
)

_LAB_PRODUCES = ("status", "max_temperature", "temperature_end", "surface_temperature", "voltage", "current")
ACTIONS = (
    ActionSpec("lab_charge", "Lab test in the simulated lab (hidden condition; computational)", "computational",
               _PROTO + (_REST, _LAB), _LAB_PRODUCES + ("noise_temperature",), deterministic=True, destructive=False,
               max_wall_s=180.0),
    ActionSpec("bench_charge", "Lab test on a real bench (run and uploaded by a qualified person)", "human",
               _PROTO + (_REST,), _LAB_PRODUCES, destructive=False,
               procedure=("In the test fixture at {ambient} degC, from 10 % SOC after a rest to ambient: charge at "
                          "{c_rate_1}C to 27.5 %, {c_rate_2}C to 45 %, {c_rate_3}C to 62.5 %, {c_rate_4}C to 80 % "
                          "(4.2 V constant voltage if reached), then rest {rest_min} min. Log surface temperature, "
                          "voltage and current every 10 s. Only within the cell's rating and the lab's interlocks.")),
    ActionSpec(RECORD_ACTION, "A constant-current stretch of an imported record, as a test (computed from the record)",
               "computational",
               (Parameter("record", "", lo=1, hi=500, integer=True,
                          label="Imported record n, in the order the campaign state lists them"),
                Parameter("segment", "", lo=1, hi=100000, integer=True,
                          label="Segment of that record, as Translucent computed them (constant current only)"),
                Parameter("mode", "", choices=(1.0, 2.0),
                          label="Your reading of the segment: 1 a discharge, 2 a charge (checked against the voltage)"),
                Parameter("start_soc", "1", lo=0.0, hi=1.0,
                          label="State of charge at the segment's start: your assumption, stated with its evidence"),
                Parameter("record_ref", "", label="dataset/record (set by Translucent)"),
                Parameter("window", "", label="The segment's window and documented ambient (set by Translucent)"),
                Parameter("series", "", label="The record's series ids (set by Translucent)")),
               ("status", "surface_temperature", "voltage", "current", "test_current", "test_c_rate",
                "test_duration", "temperature_start", "temperature_rise", "ambient_temperature",
                "current_departure"), deterministic=True, destructive=False, max_wall_s=60.0),
    ActionSpec("forecast_protocol", "Forecast a charge under a model version", "computational",
               _PROTO + (_REST, _VERSION, _MODEL),
               ("status", "min_anode_potential", "max_temperature", "anode_spread", "temperature_spread", "members",
                "model_version", "extrapolation", "cell_temperature", "temperature_lo", "temperature_hi",
                "anode_potential", "voltage", "current"), deterministic=True, destructive=False, max_wall_s=240.0),
    ActionSpec("fit_thermal", "Fit the thermal model to tests (lab or record; a new model version)", "computational",
               (Parameter("family", "", choices=(1.0, 2.0), label="1: cooling h; 2: cooling h and thermal mass k"),
                Parameter("fit_on", "", lo=1, hi=2 ** MAX_TESTS - 1, integer=True,
                          label="Tests fitted, as a bitmask: test n is bit n-1 (1 = test 1, 3 = tests 1 and 2)"),
                _SOURCES, _BASE),
               ("status", "heat_transfer_coefficient", "thermal_mass_scale", "thermal_fit_rmse",
                "consistent_with_nominal", "fit_adequate", "members"), deterministic=True, destructive=False, max_wall_s=420.0),
    ActionSpec("check_model", "Check a model version on tests it did not fit", "computational",
               (_VERSION, Parameter("test_on", "", lo=1, hi=2 ** MAX_TESTS - 1, integer=True,
                                    label="Tests checked, as a bitmask; none may be the model's own"),
                _MODEL, _SOURCES),
               ("status", "heldout_rmse", "heldout_max_error", "heldout_coverage", "model_version"),
               deterministic=True, destructive=False, max_wall_s=300.0),
    ActionSpec("rank_measurements", "Value candidate lab tests by how much they would change the design",
               "computational",
               _PROTO + tuple(dataclasses.replace(_PROTOCOL[c], name=f"test_{c}",
                                                  label=f"Composed test: {_PROTOCOL[c].label or c}")
                              for c in fc.C_RATES) +
               (dataclasses.replace(_REST, name="test_rest_min", label="Composed test: rest after the charge"),
                _VERSION, _MODEL, _MARGIN, _TLIMIT),
               ("status", "best_measurement", "best_value", "best_disagreement", "decision_risk", "decision_spread",
                "model_version"), deterministic=True, destructive=False, max_wall_s=2400.0),
    ActionSpec("optimize_protocol", "Search for the fastest charge every member says meets the limits",
               "computational", (_PROTOCOL["ambient"], _VERSION, _MODEL, _MARGIN, _TLIMIT),
               ("status", "design_found", "evaluations", "model_version"), deterministic=True, destructive=False,
               max_wall_s=900.0),
)

PLANNER_GUIDANCE = """How this system works
- A lab test (lab_charge) returns measured surface temperature, voltage and current, logged every 10 s. No lab test \
measures the anode potential: the plating margin is always a forecast from a model version.
- Model versions: 0 is nominal (the engineer's calibration in another fixture). fit_thermal makes the next version \
from the lab tests you name (fit_on, a bitmask: test n is bit n-1). Versions never change; later actions name the \
version they use (model_version). Fitting changes forecasts only: never the lab, the objective or its limits.
- The numbers come from tools: residual structure, the 95 % region and its members with their weights, held-out \
errors, the value of candidate tests. Never invent a probability, a parameter range or a confidence; cite the tool's.
- Choose the kind of work worth doing next, from the evidence: investigate a discrepancy (read_series on a lab test, \
forecast_protocol of the tested charge under a version), request a lab test (compose it within the parameters, or \
value candidates with rank_measurements, which can also value a test you compose), fit or compare models \
(fit_thermal family 1 or 2; check_model on lab tests a version did not fit), or design (optimize_protocol). Not every \
uncertainty changes the design: when rank_measurements says no test is worth its cost, design now or stop learning. \
Do not request a test whose explanations the recorded tests already tell apart.
- fit_adequate "no" means no member explains the tests within the noise: a mechanism outside the family. Its region \
is then not an uncertainty. Say so; prefer a test that exposes the mechanism, or a design that keeps clear of what \
you cannot model.
- A good fit is not proof of a mechanism. During a rest the cooling trace fixes only the ratio of thermal mass to \
cooling (family 2), not each alone.
- Recommend a design only from an optimize_protocol result, under a version you have checked on a test it did not \
fit. A lab test of a design shows its temperatures, not its anode margin."""

OBJECTIVE_TEMPLATE = {
    "evaluator": "loop-compare/3",
    "kinds": ["fastest_feasible"],
    "default_kind": "fastest_feasible",
    "statement": {"fastest_feasible": "Find the fastest protocol meeting these constraints in the lab's fixture."},
    "target": {"quantity": "charge_time_80", "unit": "min", "direction": "minimize", "margin": 0.0},
    "constraints": [
        {"quantity": "min_anode_potential", "op": ">=", "limit": 25.0, "unit": "mV",
         "label": "Plating margin: anode potential at or above +25 mV"},
        {"quantity": "max_temperature", "op": "<=", "limit": fc.T_LIMIT_C, "unit": "degC",
         "label": f"Cell at or below {fc.T_LIMIT_C:g} degC"},
    ],
    "quantities": [
        {"name": "charge_time_80", "unit": "min", "source": "result"},
        {"name": "min_anode_potential", "unit": "mV", "source": "result"},
        {"name": "max_temperature", "unit": "degC", "source": "result"},
    ],
    "summary": {"fastest_feasible": "Fastest 10-80 % charge in the lab's fixture",
                "terms": {"min_anode_potential": {"name": "anode potential", "signed": True},
                          "max_temperature": {"name": "peak temperature"}}},
    "parameter_defaults": {"ambient": 25.0, "rest_min": 0.0, "model_version": 0},
    "bound_parameters": {a: list(names) for a, names in BOUND.items()},
    "nominal_model": {"version": 0, **NOMINAL},
    "planner_guidance": PLANNER_GUIDANCE,
    "model_uncertainty": (
        "The nominal thermal model was calibrated on the Chen 2020 discharges in their fixture; this lab's fixture "
        "may cool the cell differently. A lab test measures surface temperature, voltage and current, never the anode "
        "potential: the plating margin is always a forecast. Surface-temperature agreement does not validate the "
        "anode potential, core temperature or plating."),
}

SPEC = SystemSpec(
    id=SYSTEM_ID, version="1",
    title="LG M50 fast charge with a learned thermal model (PyBaMM DFN, plating, lumped thermal)",
    description=(
        "Four-step constant-current charge of an LG INR21700 M50 from 10 to 80 % SOC. The engineer's thermal model "
        "may not match the lab's fixture: lab tests log surface temperature, voltage and current; fits give model "
        "versions with their uncertainty; forecasts and the design search run every model the evidence still allows. "
        "Lab tests here come from a simulated lab with a hidden condition (computational), or from a real bench "
        "uploaded by a person. The anode potential is never measured: it is a forecast."),
    quantities=QUANTITIES, actions=ACTIONS, citation=fc.SPEC.citation, licence=fc.SPEC.licence,
    solver={**(fc.SPEC.solver or {}), "thermal": "lumped, h and thermal-mass scale k per model version"},
    objective_template=OBJECTIVE_TEMPLATE,
)


# ------------------------------------------------------------------ parameters

def canonical(action: str, params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in SPEC.action(action).parameters:
        v = params[p.name]
        if p.name in TEXT:
            out[p.name] = str(v)
        elif p.name.removeprefix("test_") in fc.C_RATES:
            out[p.name] = round(float(v), 2)
        elif p.integer or p.name == "family":
            out[p.name] = int(v)
        else:
            out[p.name] = float(v)
    return out


def check_params(action: str, params: dict[str, Any]) -> list[str]:
    try:
        spec = SPEC.action(action)
    except KeyError as e:
        return [str(e.args[0])]
    declared = {p.name for p in spec.parameters}
    numbers = {k: v for k, v in params.items() if k not in TEXT}
    problems = [p for p in _check_params(SPEC, action, numbers)
                if not any(p == f"Missing parameter {t}" for t in TEXT)]
    problems += [f"Unknown parameter {k}" for k in sorted(params) if k in TEXT and k not in declared]
    for p in spec.parameters:
        if p.name in TEXT:
            v = params.get(p.name)
            if not isinstance(v, str) or not v:
                problems.append(f"Missing parameter {p.name}")
            elif len(v) > 40_000:
                problems.append(f"{p.name} is too long")
            elif p.name == "lab" and not (len(v) == 64 and all(c in "0123456789abcdef" for c in v)):
                problems.append("lab must be a sha256 commitment")
            elif p.name not in ("lab", "record_ref"):
                try:
                    json.loads(v)
                except ValueError:
                    problems.append(f"{p.name} must be JSON")
    for name in (*fc.C_RATES, *(f"test_{c}" for c in fc.C_RATES)):
        v = params.get(name)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and \
                abs(v / fc.C_GRID - round(v / fc.C_GRID)) > 1e-6:
            problems.append(f"{name} = {v}C is not on the {fc.C_GRID}C grid")
    return problems


def commitment(condition: dict) -> str:
    return hashlib.sha256(fc.canonical_json(condition)).hexdigest()


def bits(mask: int) -> list[int]:
    return [n for n in range(1, MAX_TESTS + 1) if int(mask) >> (n - 1) & 1]


# ------------------------------------------------------------------ the ledger's lab tests and model versions

def from_records(obj) -> bool:
    """An investigation that starts from the engineer's uploaded records (translucent.records)."""
    return bool(getattr(obj, "uploads", None))


def nominal_model(obj=None) -> dict:
    if from_records(obj):
        return {"version": 0, "family": "nominal", "estimate": dict(SHIPPED), "members": [dict(SHIPPED)],
                "result_id": None,
                "validity": {"ambient_degC": [fc.AMBIENT_IN_RANGE[0], fc.AMBIENT_IN_RANGE[1]], "c_rate": [0.5, 1.5],
                             "lab_tests": []},
                "source": "the parameter set as shipped (OKane2022: h 10 W/m^2/K, thermal mass as published), "
                          "calibrated to no fixture"}
    return {"version": 0, "family": "nominal", "estimate": dict(NOMINAL), "members": [dict(NOMINAL)],
            "result_id": None,
            "validity": {"ambient_degC": [fc.AMBIENT_IN_RANGE[0], fc.AMBIENT_IN_RANGE[1]], "c_rate": [0.5, 1.5],
                         "lab_tests": []},
            "source": "calibrated on the Chen 2020 discharges (0.5-1.5C, about 26.7 degC) in their fixture"}


def _done(states, actions) -> list:
    """Requests of these actions with a result, in the order the results were recorded."""
    return sorted((s for s in states.values() if s.action in actions and s.result is not None),
                  key=lambda s: s.result.seq)


def lab_tests(states) -> list:
    """Every test a fit or check can name, numbered from 1 in the order its result was recorded: lab tests and,
    in an investigation from records, record tests. Without record tests these are exactly the lab tests."""
    return _done(states, TEST_ACTIONS)


def _result_json(L, st) -> dict:
    aid = st.result.data["files"].get("result.json")
    if aid is None:
        raise KeyError(f"{st.result.id} has no result.json")
    return json.loads(L.blob_path(aid).read_text(encoding="utf-8"))


TRACE_STEP_S = 60.0


def _trace(observed: dict | None, forecast: dict | None) -> dict | None:
    """The prediction-vs-observation chart's data: the lab test's surface temperature once a minute, and the forecast
    made before it (worst member, with the members' band) at the same times; None without a recorded series."""
    obs = (observed or {}).get("series")
    if not obs or "surface_temperature" not in obs["channels"]:
        return None
    t, y = obs["t"], obs["channels"]["surface_temperature"]["values"]
    picks = [i for i, x in enumerate(t) if i == 0 or int(x // TRACE_STEP_S) > int(t[i - 1] // TRACE_STEP_S)]
    out = {"t_min": [round(t[i] / 60.0, 2) for i in picks], "observed_degC": [y[i] for i in picks],
           "forecast_degC": None, "forecast_lo_degC": None, "forecast_hi_degC": None}
    fs = (forecast or {}).get("series")
    if not fs:
        return out

    def at(name: str) -> list | None:
        ch = fs["channels"].get(name)
        if ch is None:
            return None
        ft, fv, res, j = fs["t"], ch["values"], [], 0
        for i in picks:
            x = t[i]
            if x > ft[-1]:
                res.append(None)
                continue
            while j + 1 < len(ft) and ft[j + 1] < x:
                j += 1
            if j + 1 >= len(ft) or ft[j + 1] == ft[j]:
                res.append(round(fv[j], 3))
            else:
                w = (x - ft[j]) / (ft[j + 1] - ft[j])
                res.append(round(fv[j] + w * (fv[j + 1] - fv[j]), 3))
        return res

    return out | {"forecast_degC": at("cell_temperature"), "forecast_lo_degC": at("temperature_lo"),
                  "forecast_hi_degC": at("temperature_hi")}


def model_versions(L, states, obj=None) -> list[dict]:
    """v0, then one version per fit_thermal result with status ok, numbered in the order recorded."""
    out = [nominal_model(obj)]
    for st in _done(states, ("fit_thermal",)):
        body = _result_json(L, st)
        if body.get("status") != "ok":
            continue
        d = body["details"]
        out.append({"version": len(out), "family": d["family"], "estimate": d["estimate"], "members": d["members"],
                    "region_95": d["region_95"], "result_id": st.result.id, "validity": d["validity"],
                    "consistent_with_nominal": bool(d["nominal"]["inside_region"]),
                    "fit_rmse_K": body["scalars"]["thermal_fit_rmse"]["value"], "adequate": bool(d["adequate"]),
                    "noise_K": d["noise_K"], "sigma_used_K": d["sigma_used_K"],
                    "effective_ratio": d["effective_ratio"]})
    return out


STANDING_RULE = (f"a model version stands behind a design when its fit, if it has one, met the adequacy rule (RMSE "
                 f"at most {ADEQUATE_NOISE_MULTIPLE:g} x the noise, floor {ADEQUATE_FLOOR_K:g} K) and a check on "
                 "tests it did not fit, in the lab where the design would run, met the same rule, with no such "
                 "check failing it or failing to run; a check on the supplied records alone (an earlier fixture) "
                 "is reported, but neither gives nor takes standing for a lab design")


def heldout_checks(L, states) -> dict[int, list[dict]]:
    """model version -> its held-out checks, in result order: {request_id, result_id, rmse_K, records_only}. A
    check whose solver could not forecast the held-out tests has rmse_K None: it counts against the model, never
    for it. records_only: every test it held out is a record test (the supplied records' fixture)."""
    tests = lab_tests(states)
    out: dict[int, list[dict]] = {}
    for st in _done(states, ("check_model",)):
        body = _result_json(L, st)
        ok = body.get("status") == "ok"
        held = [tests[n - 1] for n in bits(st.data.params["test_on"]) if n <= len(tests)]
        out.setdefault(int(st.data.params["model_version"]), []).append(
            {"request_id": st.request_id, "result_id": st.result.id,
             "rmse_K": float(body["scalars"]["heldout_rmse"]["value"]) if ok else None,
             "records_only": bool(held) and all(t.data.action == RECORD_ACTION for t in held)})
    return out


def model_standing(version: dict, checks: list[dict]) -> dict:
    """Whether a model version may stand behind a design (STANDING_RULE): {"supported", "reason"}. A good fit alone
    never qualifies; a held-out check in the lab that fails the rule outweighs any that pass. Checks on the
    supplied records alone are about an earlier fixture: they are named in the reason, and do not decide."""
    limit = max(ADEQUATE_NOISE_MULTIPLE * float(version.get("noise_K") or NOISE_K), ADEQUATE_FLOOR_K)
    v = version["version"]
    if version.get("adequate") is False:
        return {"supported": False,
                "reason": f"model version {v} does not fit its own tests within the noise (fit RMSE "
                          f"{version.get('fit_rmse_K')} K, rule {limit:g} K): a mechanism outside its family"}
    records = [c for c in checks if c.get("records_only")]
    checks = [c for c in checks if not c.get("records_only")]
    out = _lab_standing(v, checks, limit)
    if records:
        worst = [c["rmse_K"] for c in records]
        told = ("could not be forecast" if None in worst else
                f"fail the rule (held-out RMSE {max(worst):g} K)" if any(not x <= limit for x in worst) else
                f"meet the rule (held-out RMSE {max(worst):g} K)")
        out["reason"] += f"; on the supplied records alone, an earlier fixture, its forecasts {told}"
    return out


GAP_OFFSET_SHARE = 0.7      # a misfit whose mean is at least this share of its RMSE is a steady offset
GAP_PHASE_RATIO = 2.0       # a misfit this many times larger in one phase (charge or rest) than the other is in it

# what a recommendation needs, by where the misfit sits: (kind, what, what it would establish)
GAP_NEEDS = {
    "offset": [
        ("evidence", "the temperature the cell's surroundings actually hold during a lab test: an air or chamber "
                     "sensor at the cell, logged with the surface temperature, through the charge and a rest with no "
                     "charge. No action of this system logs it; a person adds the sensor to the bench test",
         "whether the cell settles at the stated ambient: a settled difference of about {bias:+.1f} K is the "
         "surroundings, not cooling or heat generation, and the stated ambient is then the wrong boundary"),
        ("capability", "a model that takes the measured surroundings temperature as its boundary instead of the "
                       "stated ambient",
         "the fits can then be judged against the noise again; until then no family here can stand behind a "
         "design")],
    "charge": [
        ("evidence", "a lab test at a different charge current, with the same rest",
         "how the heat the families miss scales with current (a resistive term grows with its square; one that "
         "does not is another source)"),
        ("capability", "a model family with a heat source beyond the electrochemical heat both families share",
         "a fit that explains the charge, which a design's temperature forecast rests on")],
    "rest": [
        ("evidence", "a lab test with a longer rest after the charge",
         "the shape of the cooling: one exponential, as both families assume, or more than one"),
        ("capability", "a model family with more than one thermal mass, or a cooling that changes with temperature",
         "a fit that explains the rest, which separates cooling from heat generation")],
    "mixed": [
        ("evidence", "the residuals of the failed tests read against their phases, then a lab test that changes one "
                     "factor of them only (current, rest or ambient)",
         "which part of the charge-and-rest cycle the families miss"),
        ("capability", "a model family with the mechanism those residuals show",
         "a fit within the noise, without which no design here is a recommendation")],
}


def model_gap(versions: list[dict], standing: list[dict], fit_details: dict[int, dict], test_actions: list[str],
              heldout: dict[int, list[dict]]) -> dict | None:
    """When every model family this system supports has been fitted to the lab's own tests and none may stand behind
    a design (each fit misses the adequacy rule, or a check in the lab refutes it): what the tests show the families
    lack, and the evidence or modelling capability a recommendation needs. None while a family is untried or a
    fitted version is still open (adequate and not refuted). The adequacy rule is the one fits are judged by."""
    lab_fits = []
    for v in versions[1:]:
        sources = (v.get("validity") or {}).get("lab_tests") or []
        if any(0 < n <= len(test_actions) and test_actions[n - 1] in LAB_ACTIONS for n in sources):
            lab_fits.append(v)
    if not lab_fits or {v["family"] for v in lab_fits} != set(FAMILIES.values()):
        return None
    rows = []
    for v in lab_fits:
        n = v["version"]
        limit = max(ADEQUATE_NOISE_MULTIPLE * float(v.get("noise_K") or NOISE_K), ADEQUATE_FLOOR_K)
        lab_checks = [c for c in heldout.get(n, []) if not c.get("records_only")]
        refuted = [c for c in lab_checks if c["rmse_K"] is None or not c["rmse_K"] <= limit]
        if v.get("adequate") is not False and not refuted:
            return None             # adequate and not refuted in the lab: the family may yet explain the tests
        rows.append({"version": n, "family": v["family"], "fit_rmse_K": v.get("fit_rmse_K"), "rule_K": limit,
                     "lab_tests": (v.get("validity") or {}).get("lab_tests") or [],
                     "why": standing[n]["reason"]})
    if any(standing[r["version"]]["supported"] for r in rows):
        return None
    worst = min((r for r in rows if r["fit_rmse_K"] is not None), key=lambda r: r["fit_rmse_K"], default=rows[-1])
    res = (fit_details.get(worst["version"]) or {}).get("residuals") or []

    def mean(k):
        got = [x[k] for x in res if x.get(k) is not None]
        return sum(got) / len(got) if got else None
    bias, rmse, charge_r, rest_r = mean("bias_K"), mean("rmse_K"), mean("charge_rmse_K"), mean("rest_rmse_K")
    if bias is not None and rmse and abs(bias) >= GAP_OFFSET_SHARE * rmse:
        pattern = "offset"
    elif charge_r is not None and rest_r is not None and charge_r >= GAP_PHASE_RATIO * rest_r:
        pattern = "charge"
    elif charge_r is not None and rest_r is not None and rest_r >= GAP_PHASE_RATIO * charge_r:
        pattern = "rest"
    else:
        pattern = "mixed"
    where = {"offset": f"a steady offset of about {bias or 0:+.1f} K in the charge and the rest alike",
             "charge": "mostly in the charge", "rest": "mostly in the rest", "mixed": "in no single phase"}[pattern]
    tried = "; ".join(f"{r['family']} (v{r['version']}: fit RMSE {r['fit_rmse_K']} K, rule {r['rule_K']:g} K)"
                      for r in rows)
    return {"established": True, "families": rows, "pattern": pattern,
            "residual": {"model_version": worst["version"], "bias_K": None if bias is None else round(bias, 3),
                         "rmse_K": None if rmse is None else round(rmse, 3),
                         "charge_rmse_K": None if charge_r is None else round(charge_r, 3),
                         "rest_rmse_K": None if rest_r is None else round(rest_r, 3)},
            "summary": f"No model family this system supports explains the lab's tests: {tried}. The misfit is "
                       f"{where}. No design is a recommendation, and the adequacy rule is not relaxed to make one.",
            "needed": [{"kind": k, "what": what, "establishes": est.format(bias=bias or 0.0)}
                       for k, what, est in GAP_NEEDS[pattern]]}


def _lab_standing(v: int, checks: list[dict], limit: float) -> dict:
    unrun = [c for c in checks if c["rmse_K"] is None]
    if unrun:
        return {"supported": False,
                "reason": f"model version {v} could not forecast the tests a check held out "
                          f"({unrun[0]['result_id']}: the solver failed)"}
    failed = [c for c in checks if not c["rmse_K"] <= limit]       # NaN fails too
    if failed:
        return {"supported": False,
                "reason": f"model version {v} failed a check on tests it did not fit (held-out RMSE "
                          f"{failed[0]['rmse_K']:g} K, rule {limit:g} K, {failed[0]['result_id']})"}
    if not checks:
        return {"supported": False,
                "reason": f"model version {v} has not been checked on tests it did not fit"}
    return {"supported": True,
            "reason": f"model version {v} met the rule on tests it did not fit (held-out RMSE "
                      f"{max(c['rmse_K'] for c in checks):g} K, rule {limit:g} K)"}


def standings(L, states, obj=None) -> list[dict]:
    """Each model version's standing, by version number."""
    checks = heldout_checks(L, states)
    return [model_standing(v, checks.get(v["version"], [])) for v in model_versions(L, states, obj)]


def _sources(L, obj, states, mask: int) -> list[dict]:
    tests = lab_tests(states)
    want = bits(mask)
    noun = "test" if from_records(obj) else "lab test"     # a lab campaign keeps the words its turns saw
    if not want:
        raise ValueError(f"name at least one {noun}")
    missing = [n for n in want if n > len(tests)]
    if missing:
        raise ValueError(f"{noun} {missing[0]} has no result yet (there are {len(tests)})")
    out = []
    for n in want:
        st = tests[n - 1]
        aid = st.result.data["series"].get("surface_temperature")
        if aid is None:
            raise ValueError(f"{noun} {n} has no surface temperature series")
        out.append({"n": n, "result_id": st.result.id, "series": aid})
    return out


def _limit(obj, quantity: str, op: str, unit: str) -> float:
    """The strictest `quantity op` limit in the objective, in `unit` (mV, degC)."""
    found = [c for c in obj.constraints if c.quantity == quantity and c.op == op]
    if not found:
        raise ValueError(f"the objective has no {quantity} {op} constraint to design against")
    try:
        limits = [float(units.convert(float(c.limit), c.unit, unit)) for c in found]
    except (KeyError, ValueError) as e:
        raise ValueError(f"the {quantity} limit's unit cannot be read as {unit}: {e}") from None
    return max(limits) if op == ">=" else min(limits)


def sealed_path(root: Path, cid: str) -> Path:
    return Path(root).joinpath(*SEALED, f"{cid}.json")


def seal_lab(root: Path, cid: str, h: float, k: float = 1.0, noise_K: float = NOISE_K,
             ambient_offset_K: float = 0.0) -> str:
    """Write a campaign's hidden lab condition (administrator or evaluation harness); returns its commitment."""
    if not (H_BOUNDS[0] <= h <= H_BOUNDS[1] and K_BOUNDS[0] <= k <= K_BOUNDS[1]):
        raise ValueError("h or k outside the model family's bounds")
    path = sealed_path(root, cid)
    if path.exists():
        raise FileExistsError(f"{cid} already has a sealed lab condition")
    condition = {"h": float(h), "k": float(k), "noise_K": float(noise_K), "noise_V": NOISE_V,
                 "ambient_offset_K": float(ambient_offset_K), "seed": secrets.token_hex(16)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(fc.canonical_json(condition))
    return commitment(condition)


def _condition(root: Path, cid: str) -> dict:
    path = sealed_path(root, cid)
    if not path.is_file():
        raise ValueError("this campaign has no simulated lab; a person uploads a bench test instead")
    return json.loads(path.read_text(encoding="utf-8"))


def derive(L, cid: str, obj, states, action: str, params: dict) -> dict:
    """The bound parameters of a request, from the ledger and the objective in force. ValueError says why not."""
    if action == "lab_charge":
        return {"lab": commitment(_condition(L.root, cid))}
    if action == RECORD_ACTION:
        return record_binding(L, cid, obj, params)
    out: dict[str, Any] = {}
    if action in MODEL_ACTIONS:
        versions = model_versions(L, states, obj)
        v = params.get("model_version")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v != int(v) or \
                not 0 <= v < len(versions):
            raise ValueError(f"model_version must be 0 to {len(versions) - 1}: the versions recorded so far")
        model = versions[int(v)]
        out["model"] = json.dumps(model, sort_keys=True, separators=(",", ":"))
        if action == "check_model":
            mask = params.get("test_on")
            if not isinstance(mask, (int, float)) or isinstance(mask, bool) or not math.isfinite(mask) or \
                    mask != int(mask) or mask < 1:
                raise ValueError("test_on must name at least one lab test")
            own = set(model["validity"].get("lab_tests", []))
            if overlap := sorted(own & set(bits(int(mask)))):
                raise ValueError(f"lab test {overlap[0]} was fitted by model version {int(v)}; a held-out check uses "
                                 "tests the model did not fit")
            out["sources"] = json.dumps(_sources(L, obj, states, int(mask)), sort_keys=True, separators=(",", ":"))
        if action in ("optimize_protocol", "rank_measurements"):
            out["margin_mV"] = _limit(obj, "min_anode_potential", ">=", "mV")
            out["t_limit_C"] = _limit(obj, "max_temperature", "<=", "degC")
    if action == "fit_thermal":
        mask = params.get("fit_on")
        if not isinstance(mask, (int, float)) or isinstance(mask, bool) or not math.isfinite(mask) or \
                    mask != int(mask) or mask < 1:
            raise ValueError("fit_on must name at least one lab test")
        out["sources"] = json.dumps(_sources(L, obj, states, int(mask)), sort_keys=True, separators=(",", ":"))
        out["base"] = json.dumps(nominal_model(obj), sort_keys=True, separators=(",", ":"))
    return out


RECORD_QUANTITIES = ("surface_temperature", "current", "voltage", "chamber_temperature")


def _record_arrays(L, record, quantity: str):
    """(series id, t, values in the system's unit) of a record's channel, or None."""
    from translucent import units

    ch = next((c for c in record.channels if c.quantity == quantity), None)
    if ch is None:
        return None
    body = json.loads(L.blob_path(ch.series_id).read_text(encoding="utf-8"))
    unit = next(q.unit for q in SPEC.quantities if q.name == quantity)
    return ch.series_id, body["t"], units.convert(body["y"], ch.unit, unit)


def documented_ambient(record) -> float | None:
    """The chamber or ambient temperature the record's documented conditions state, in degC; None when none does."""
    from translucent import units

    for key in ("chamber", "chamber_temperature", "ambient", "ambient_temperature"):
        v = record.conditions.get(key)
        u = record.conditions_units.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and u and units.same_dimension(u, "degC"):
            return float(units.convert(v, u, "degC"))
    return None


def record_segments(L, record) -> list[dict]:
    """The record's segments (translucent.records.segments) on its current, with voltage, surface and chamber."""
    from translucent import records

    cur = _record_arrays(L, record, "current")
    if cur is None:
        raise ValueError(f"record {record.id} has no current series; a record test needs the current")
    _, t, i = cur
    extra = {}
    for q, key in (("voltage", "voltage"), ("surface_temperature", "temperature"), ("chamber_temperature", "chamber")):
        got = _record_arrays(L, record, q)
        if got is not None and got[1] == t:
            extra[key] = got[2]
    cap = record.specimen.properties.get("nominal_capacity_Ah")
    return records.segments(t, i, capacity_ah=float(cap) if isinstance(cap, (int, float)) else fc.CAPACITY_AH,
                            **extra)


def record_binding(L, cid: str, obj, params: dict) -> dict:
    """A record test's bound values: which record, the segment's window and the documented ambient, and the series
    ids. ValueError when the segment is not a constant-current stretch, its voltage contradicts the stated mode, or
    nothing records or documents the ambient."""
    from translucent import records

    found = records.campaign_records(L, obj, cid)
    n = params.get("record")
    if isinstance(n, bool) or not isinstance(n, (int, float)) or n != int(n) or not 1 <= n <= len(found):
        raise ValueError(f"record must be 1 to {len(found)}: the records imported so far" if found else
                         "no record is imported yet: import the uploaded files first")
    ref, record = found[int(n) - 1]
    segs = record_segments(L, record)
    k = params.get("segment")
    if isinstance(k, bool) or not isinstance(k, (int, float)) or k != int(k) or not 1 <= k <= len(segs):
        raise ValueError(f"segment must be 1 to {len(segs)} for record {int(n)}")
    seg = segs[int(k) - 1]
    if seg["kind"] != "constant current":
        raise ValueError(f"segment {int(k)} of record {int(n)} is {seg['kind']}; a record test is a constant-current "
                         "stretch")
    trend = seg.get("voltage_trend")
    mode = params.get("mode")
    if trend is None:
        raise ValueError(f"record {int(n)} has no voltage series on the current's clock; a record test needs it to "
                         "check the segment's direction")
    if mode == 1 and trend != "falling" or mode == 2 and trend != "rising":
        raise ValueError(f"the voltage is {trend} over segment {int(k)}: that is not a "
                         f"{'discharge' if mode == 1 else 'charge'}")
    series = {}
    for q in RECORD_QUANTITIES:
        got = _record_arrays(L, record, q)
        if got is not None:
            series[q] = got[0]
    if "surface_temperature" not in series:
        raise ValueError(f"record {int(n)} has no surface_temperature series")
    ambient = documented_ambient(record)
    if "chamber_temperature" not in series and ambient is None:
        raise ValueError(f"record {int(n)} neither records the chamber temperature nor documents it in its "
                         "conditions; ask the engineer rather than assume one")
    win = {"t_start_s": seg["t_start_s"], "t_end_s": seg["t_end_s"], "segment": int(k),
           "documented_ambient_degC": ambient,
           "capacity_Ah": float(record.specimen.properties.get("nominal_capacity_Ah") or fc.CAPACITY_AH)}
    return {"record_ref": ref, "window": json.dumps(win, sort_keys=True, separators=(",", ":")),
            "series": json.dumps(series, sort_keys=True, separators=(",", ":"))}


def staged_sources(params: dict, inputs: dict) -> list[dict]:
    """The staged series of a request's sources, in the request's order, each checked against its sealed id."""
    sealed = json.loads(params["sources"])
    staged = {s["result_id"]: s for s in inputs.get("sources", [])}
    out = []
    for s in sealed:
        got = staged.get(s["result_id"])
        if got is None or got["series"] != s["series"]:
            raise ValueError(f"lab test {s['n']} was not staged as sealed")
        body = got["body"]
        if got.get("raw_sha256") != s["series"]:
            raise ValueError(f"lab test {s['n']}: the staged series is not the sealed one")
        out.append({"n": s["n"], "result_id": s["result_id"], "params": got["params"],
                    "noise_K": got.get("noise_K"),
                    "series": {"t": body["t"], "surface_temperature": body["y"]}})
    return out


# ------------------------------------------------------------------ the adapter

def _child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "HOME", "LANG", "TMP", "TEMP", "TMPDIR",
                                                         "PYTHONPATH", "USERPROFILE", "LOCALAPPDATA")}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[2]),
                                                      env.get("PYTHONPATH", "")]))
    env["PYTHONSAFEPATH"] = "1"                  # never import from the work folder the child runs in
    return env


class LabAdapter:
    """pybamm-fastcharge-lab behind the system adapter boundary."""

    spec = SPEC
    solver_module = "translucent.systems.lgm50_lab_solver"     # the child; tests substitute a fast stand-in

    def code_digest(self) -> str:
        here = Path(__file__)
        return file_digest(here, here.with_name("lgm50_lab_solver.py"), here.with_name("lgm50_solver.py"),
                           here.with_name("lgm50_fastcharge.py"))

    def check_params(self, action: str, params: dict[str, Any]) -> list[str]:
        return check_params(action, params)

    def configuration(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"action": action, **canonical(action, params)}

    def estimate_cost(self, action: str, params: dict[str, Any]) -> Cost:
        if action == "bench_charge":
            minutes = sum((fc.SOC_END - fc.SOC_START) / fc.STEPS * 60 / float(params[c]) for c in fc.C_RATES)
            return Cost(person_minutes=round(minutes + float(params["rest_min"]) + 60, 1),
                        other=(("lab_tests", 1.0),))
        wall = {"lab_charge": 30.0, "forecast_protocol": 30.0, "fit_thermal": 120.0, "check_model": 60.0,
                "rank_measurements": 240.0, "optimize_protocol": 300.0, RECORD_ACTION: 5.0}[action]
        return Cost(wall_s=wall, other=(("lab_tests", 1.0),) if action == "lab_charge" else ())

    # bound parameters ------------------------------------------------

    def bind(self, L, cid: str, obj, states, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """The proposer's parameters with the bound ones filled in; ValueError when a bound one was typed in or
        cannot be derived."""
        typed = [n for n in BOUND.get(action, ()) if n in params]
        if typed:
            raise ValueError(f"{', '.join(typed)} {'is' if len(typed) == 1 else 'are'} set by Translucent, not by "
                             "the proposer")
        return {**params, **derive(L, cid, obj, states, action, params)}

    def admission_problems(self, L, cid: str, obj, req, states) -> list[str]:
        cap = obj.budget.get("lab_tests")
        if req.action in LAB_ACTIONS and req.version == 1 and cap is not None:
            used = sum(s.action in LAB_ACTIONS and s.request_id != req.request_id and
                       s.state not in ("rejected", "cancelled") for s in states.values())
            if used >= cap:
                return [f"the lab-test budget is spent: {used} of {cap:g} lab tests are requested or done"]
        names = BOUND.get(req.action, ())
        if not names:
            return []
        if req.action == "lab_charge" and not sealed_path(L.root, cid).is_file():
            return []        # verified away from the lab's machine: the sealed condition is not there to check
        try:
            want = derive(L, cid, obj, states, req.action, dict(req.params))
        except (ValueError, KeyError) as e:
            return [str(e.args[0]) if e.args else type(e).__name__]
        return [f"{n} differs from what the ledger and the objective in force give" for n in names
                if req.params.get(n) != want.get(n)]

    def stage(self, L, cid: str, req, workdir: Path) -> None:
        """Before the child runs: the sources' recorded series and, for a lab test, the sealed condition."""
        from translucent.loop import request_states

        params = dict(req.data.params)
        inputs: dict[str, Any] = {"request_id": req.request_id}
        if req.action == "lab_charge":
            inputs["condition"] = _condition(L.root, cid)
        if req.action == RECORD_ACTION:
            from translucent import records, units

            win = json.loads(params["window"])
            got = {}
            for q, sid in json.loads(params["series"]).items():
                body = json.loads(L.blob_path(sid).read_text(encoding="utf-8"))
                unit = next(x.unit for x in SPEC.quantities if x.name == q)
                idx = records.window(body["t"], win["t_start_s"], win["t_end_s"])
                got[q] = {"t": [body["t"][i] for i in idx],
                          "y": units.convert([body["y"][i] for i in idx], body["unit"], unit), "unit": unit}
            inputs["record"] = {"window": win, "series": got}
        if "sources" in params:
            states = request_states(L, cid)
            by_result = {s.result.id: s for s in lab_tests(states)}
            inputs["sources"] = []
            for s in json.loads(params["sources"]):
                st = by_result[s["result_id"]]
                body = json.loads(L.blob_path(s["series"]).read_text(encoding="utf-8"))
                noise = st.result.data["measurements"].get("noise_temperature")
                m = L.get("measurement", noise) if noise else None
                if st.action == RECORD_ACTION:
                    meas = {q: L.get("measurement", mid).value for q, mid in st.result.data["measurements"].items()}
                    src_params = {"kind": "record", "mode": int(st.data.params["mode"]),
                                  "start_soc": float(st.data.params["start_soc"]),
                                  "current_A": float(meas["test_current"]),
                                  "ambient": float(meas["ambient_temperature"]),
                                  "t0_degC": float(meas["temperature_start"]),
                                  "duration_s": float(meas["test_duration"]) * 60.0}
                else:
                    src_params = {k: v for k, v in st.data.params.items() if k in (*fc.C_RATES, "ambient", "rest_min")}
                inputs["sources"].append({
                    "result_id": s["result_id"], "series": s["series"], "body": body,
                    "raw_sha256": "sha256:" + hashlib.sha256(L.blob_path(s["series"]).read_bytes()).hexdigest(),
                    "params": src_params,
                    "noise_K": float(m.value) if m is not None and isinstance(m.value, float) else None})
        Path(workdir).mkdir(parents=True, exist_ok=True)
        (Path(workdir) / "inputs.json").write_bytes(fc.canonical_json(inputs))

    # execution -------------------------------------------------------

    def execute(self, action: str, params: dict[str, Any], workdir: Path) -> ExecutionOutput:
        workdir = Path(workdir)
        staged = workdir / "inputs.json"
        try:
            inputs = json.loads(staged.read_text(encoding="utf-8")) if staged.exists() else {}
        finally:
            staged.unlink(missing_ok=True)       # the sealed condition must not stay in the work folder
        if SPEC.action(action).executor != "computational":
            raise NotExecutable(f"{action} is run by a person, not by the solver")
        if problems := self.check_params(action, params):
            raise ValueError("; ".join(problems))
        request = {"action": action, "params": canonical(action, params), "inputs": inputs}
        proc = subprocess.run([sys.executable, "-m", self.solver_module],
                              input=fc.canonical_json(request), capture_output=True,
                              timeout=SPEC.action(action).max_wall_s, env=_child_env(), cwd=workdir)
        del request, inputs
        log = workdir / "solver.log"
        log.write_bytes(proc.stderr if action != "lab_charge" else b"")
        if proc.returncode != 0:
            if action == "lab_charge":
                raise RuntimeError(f"the simulated lab exited {proc.returncode}")
            tail = proc.stdout.decode(errors="replace")[-400:] or proc.stderr.decode(errors="replace")[-400:]
            raise RuntimeError(f"solver child exited {proc.returncode}: {tail}")
        out = json.loads(proc.stdout)
        result = out["result"]
        if fc.result_hash(result) != out["sha256"]:
            raise RuntimeError("solver output does not match its own sha256")
        path = workdir / "result.json"
        path.write_bytes(fc.canonical_json(result))
        if result["status"] == "solver_error":
            raise RuntimeError(f"solver error: {result.get('error')}")
        return ExecutionOutput(table=fc.table_from_result(result), files={"result.json": path, "solver.log": log},
                               solver={k: v if isinstance(v, str) else json.dumps(v, sort_keys=True)
                                       for k, v in {**result["solver"], "result_sha256": out["sha256"]}.items()},
                               executed={k: v for k, v in result["params"].items()})

    def check_result(self, action: str, params: dict[str, Any], table: ResultTable) -> list[str]:
        s = {k: v[0] for k, v in table.scalars.items()}
        problems = []
        if action == RECORD_ACTION:
            if s.get("status") != "ok":
                problems.append(f"the record test is {s.get('status')}")
        if action in LAB_ACTIONS:
            temps = table.series.get("surface_temperature")
            if not temps or not temps[1]:
                problems.append("a lab test logs the surface temperature")
            elif min(temps[1]) < float(params["ambient"]) - 3.0:
                problems.append("the surface temperature fell more than 3 K below ambient")
        if action == "fit_thermal":
            h = s.get("heat_transfer_coefficient")
            if not isinstance(h, (int, float)) or not H_BOUNDS[0] <= h <= H_BOUNDS[1]:
                problems.append(f"fitted h {h} is outside {H_BOUNDS[0]:g}-{H_BOUNDS[1]:g} W/m^2/K")
        if action in ("forecast_protocol", "optimize_protocol") and s.get("status") == "nonsense":
            problems.append("the solution failed its sanity checks")
        return problems

    def procedure(self, action: str, params: dict[str, Any]) -> str | None:
        template = SPEC.action(action).procedure
        if template is None:
            return None
        p = canonical(action, params)
        return template.format(**{k: f"{v:g}" for k, v in p.items() if not isinstance(v, str)})

    def result_details(self, L, st) -> dict | None:
        """What a result found beyond its measurements, for the planner: a fit's region, members and residuals, a
        check's per-test errors, a ranking's values and verdict, a design's members."""
        body = _result_json(L, st)
        d = body.get("details") or {}
        if st.action == "fit_thermal" and body.get("status") == "ok":
            from translucent.loop import current_objective, request_states
            cid = st.result.campaign_id
            versions = model_versions(L, request_states(L, cid), current_objective(L, cid))
            n = next((v["version"] for v in versions if v.get("result_id") == st.result.id), None)
            return {"model_version": n, **{k: d.get(k) for k in ("family", "estimate", "region_95", "members",
                                                                   "nominal", "residuals", "adequate", "validity",
                                                                   "sigma_used_K", "effective_samples", "samples")}}
        if st.action == "check_model":
            return {"tests": d.get("tests"), "model_sources": d.get("model_sources")}
        if st.action == "rank_measurements":
            return {k: d.get(k) for k in ("options", "weights", "decisions", "best_decision_now", "verdict", "caveat")}
        if st.action in ("forecast_protocol", "optimize_protocol"):
            out = {k: d.get(k) for k in ("members", "extrapolation", "fit_adequate", "reason") if d.get(k) is not None}
            if body.get("status") == "ok":
                from translucent.loop import current_objective, request_states
                cid = st.result.campaign_id
                found = standings(L, request_states(L, cid), current_objective(L, cid))
                out["model_standing"] = {**found[int(st.data.params["model_version"])], "rule": STANDING_RULE}
            return out
        return None

    def forecast_basis_problem(self, L, cid: str, states, st) -> str | None:
        """Why a result's forecast values may not be compared with the objective's limits, or None. A forecast or
        design under a model version without standing (STANDING_RULE) is a lead, never a feasible result: its values
        are withheld from the comparison, so no outcome or recommendation rests on it."""
        if st.action not in ("forecast_protocol", "optimize_protocol"):
            return None
        from translucent.loop import current_objective
        v = int(st.data.params["model_version"])
        s = standings(L, states, current_objective(L, cid))[v]
        return None if s["supported"] else f"a forecast without a model standing behind it: {s['reason']}"

    def campaign_summary(self, L, cid: str, states) -> dict:
        """thermal-learning/1, the view's `learning`: what the campaign believes and why, for the four panels (model in
        use, evidence just received, next experiment, best design) and the before/after and prediction-vs-observation
        views. Everything is derived from the ledger; nothing here is hidden lab data."""
        from translucent.loop import current_objective

        versions = model_versions(L, states, current_objective(L, cid))
        standing = standings(L, states, current_objective(L, cid))
        order = sorted((s for s in states.values() if s.result is not None), key=lambda s: s.result.seq)
        body = {s.request_id: _result_json(L, s) for s in order if s.action != "bench_charge"}

        def scal(rq, name):
            v = (body.get(rq, {}).get("scalars") or {}).get(name)
            return v["value"] if isinstance(v, dict) else None

        protocol = lambda p: {**{c: p.get(c) for c in fc.C_RATES}, "ambient": p.get("ambient"),
                              "rest_min": p.get("rest_min")}
        checks: dict[int, list] = {}
        for st in order:
            if st.action == "check_model" and body[st.request_id].get("status") == "ok":
                v = int(st.data.params["model_version"])
                checks.setdefault(v, []).append({
                    "request_id": st.request_id, "result_id": st.result.id,
                    "lab_tests": [t["lab_test"] for t in body[st.request_id]["details"]["tests"]],
                    "heldout_rmse_K": scal(st.request_id, "heldout_rmse"),
                    "heldout_max_error_K": scal(st.request_id, "heldout_max_error"),
                    "coverage": scal(st.request_id, "heldout_coverage")})
        version_rows = []
        for v in versions:
            fit = next((s for s in order if s.result.id == v.get("result_id")), None)
            version_rows.append({k: v.get(k) for k in ("version", "family", "estimate", "members", "region_95",
                                                         "validity", "adequate", "consistent_with_nominal",
                                                         "fit_rmse_K", "result_id", "source")} |
                                {"request_id": fit.request_id if fit else None,
                                 "sources": (v.get("validity") or {}).get("lab_tests", []),
                                 "checks": checks.get(v["version"], []),
                                 "standing": standing[v["version"]]})
        tests = []
        for n, st in enumerate(lab_tests(states), start=1):
            p = dict(st.data.params)
            before = [s for s in order if s.action == "forecast_protocol" and s.result.seq < st.result.seq and
                      protocol(dict(s.data.params)) == protocol(p) and body[s.request_id].get("status") == "ok"]
            fc_row = None
            if before:
                f = before[-1]
                fc_row = {"request_id": f.request_id, "result_id": f.result.id,
                          "model_version": int(f.data.params["model_version"]),
                          "max_temperature": scal(f.request_id, "max_temperature"),
                          "min_anode_potential": scal(f.request_id, "min_anode_potential")}
            m = st.result.data["measurements"]
            value = lambda q: (L.get("measurement", m[q]).value if q in m else None)
            row = {}
            if st.action == RECORD_ACTION:     # a stretch of the engineer's record: what was cut and what it showed
                window = json.loads(p["window"])
                row = {"record": {"ref": p["record_ref"], "segment": int(p["segment"]),
                                  "mode": "discharge" if float(p["mode"]) == 1.0 else "charge",
                                  "t_start_s": window["t_start_s"], "t_end_s": window["t_end_s"],
                                  **{q: value(q) for q in ("test_current", "test_c_rate", "test_duration",
                                                           "temperature_start", "temperature_rise",
                                                           "ambient_temperature", "current_departure")}}}
            tests.append({"n": n, "request_id": st.request_id, "result_id": st.result.id, "action": st.action,
                          "basis": "simulated lab (computational)" if st.action == "lab_charge" else
                          "the engineer's record (uploaded)" if st.action == RECORD_ACTION else
                          st.result.data["basis"], "protocol": protocol(p), **row,
                          "observed": {"max_temperature": value("max_temperature"),
                                       "temperature_end": value("temperature_end"),
                                       "charge_time_80": value("charge_time_80")},
                          "prediction": (st.data.prediction or {}).get("max_temperature"),
                          "forecast": fc_row,
                          "trace": _trace(body.get(st.request_id), body.get(fc_row["request_id"]) if fc_row else None)})
        designs = []
        for st in order:
            if st.action == "optimize_protocol" and body[st.request_id].get("status") == "ok":
                found = scal(st.request_id, "design_found") == "yes"
                mv = int(st.data.params["model_version"])
                designs.append({"request_id": st.request_id, "result_id": st.result.id,
                                "model_version": mv, "design_found": found,
                                "model_adequate": versions[mv].get("adequate"),
                                "model_supported": standing[mv]["supported"], "standing": standing[mv]["reason"],
                                "c_rates": [scal(st.request_id, c) for c in fc.C_RATES] if found else None,
                                "charge_time_80": scal(st.request_id, "charge_time_80"),
                                "min_anode_potential": scal(st.request_id, "min_anode_potential"),
                                "max_temperature": scal(st.request_id, "max_temperature"),
                                "limits": {"margin_mV": float(st.data.params["margin_mV"]),
                                           "t_limit_C": float(st.data.params["t_limit_C"])}})
        found = [d for d in designs if d["design_found"]]
        ranking = None
        ranks = [s for s in order if s.action == "rank_measurements" and body[s.request_id].get("status") == "ok"]
        if ranks:
            st = ranks[-1]
            d = body[st.request_id]["details"]
            ranking = {"request_id": st.request_id, "result_id": st.result.id,
                       "model_version": int(st.data.params["model_version"]),
                       "best_measurement": scal(st.request_id, "best_measurement"), "verdict": d.get("verdict"),
                       "decision_risk_min": scal(st.request_id, "decision_risk"),
                       "options": [{k: o[k] for k in ("option", "label", "c_rates", "rest_min", "value_min",
                                                       "disagreement_K", "duration_min")} for o in d["options"]]}
        in_use = next((int(s.data.params["model_version"]) for s in reversed(order)
                       if s.action in MODEL_ACTIONS), 0)
        pending = [s for s in states.values() if s.state in ("awaiting_approval", "queued", "running",
                                                             "awaiting_result")]
        nxt = pending[0] if pending else None
        return {
            "kind": "thermal-learning/1",
            "versions": version_rows, "model_in_use": in_use,
            "lab_tests": tests, "latest_lab_test": tests[-1]["n"] if tests else None,
            "next_experiment": None if nxt is None else {
                "request_id": nxt.request_id, "action": nxt.action, "state": nxt.state,
                "params": {k: v for k, v in nxt.data.params.items() if k not in BOUND.get(nxt.action, ())},
                "rationale": nxt.data.rationale, "expected_effect": nxt.data.expected_effect,
                "prediction": dict(nxt.data.prediction or {})},
            "designs": designs, "first_design": found[0] if found else None,
            "best_design": found[-1] if found else None, "ranking": ranking,
            # the latest design a model with standing stands behind; None means no design is recommended yet
            "recommended_design": next((d for d in reversed(found) if d["model_supported"]), None),
            # every supported family tried on the lab's tests and none stands: what a recommendation needs (or None)
            "model_gap": model_gap(versions, standing,
                                   {r["version"]: (body.get(r["request_id"]) or {}).get("details") or {}
                                    for r in version_rows if r["request_id"]},
                                   [s.action for s in lab_tests(states)], heldout_checks(L, states)),
            "standing_rule": STANDING_RULE,
            "limits": "lab tests measure surface temperature, voltage and current; the anode potential is always a "
                      "forecast; designs meet the limits for every member of the model version they used",
        }

    def baseline_policy(self):
        return None

    def evidence_problems(self, ledger, campaign_id: str) -> list[str]:
        """Every request's bound values against the ledger as it stood when the request was admitted."""
        return []
