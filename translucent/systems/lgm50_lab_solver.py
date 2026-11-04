"""The pybamm-fastcharge-lab solver, run in a child process (needs `uv sync --group solver`).

    python -m translucent.systems.lgm50_lab_solver < request.json

The request is {"action", "params", "inputs"}: params as sealed in the request (bound values included), inputs as
staged by the adapter (the sources' recorded series, the sealed lab condition). Stdout is one JSON object
{"result", "sha256", "wall_s"}; refused parameters give {"refused": [reasons]} and exit status 2.

Every action runs the same charge: four constant-current steps over the SOC windows of pybamm-fastcharge (DFN with
partially reversible plating, lumped thermal, OKane2022), the constant-voltage fallback when 4.2 V cuts a step short,
then an optional rest. The thermal model is two numbers: the heat-transfer coefficient h (W/m^2/K) and the thermal-
mass scale k, which multiplies every specific heat capacity of the parameter set.

Result schema (pybamm-fastcharge-lab-result/1):
    schema, action, params (as run), status, error, basis ("computational"),
    scalars {quantity: {value, unit}}, series {time_unit, t, channels {quantity: {unit, values}}} or null,
    solver {...}, details (per action; never the sealed lab condition)
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import math
import multiprocessing as mp
import sys
import time
from typing import Any

from translucent.systems import lgm50_fastcharge as fc
from translucent.systems import lgm50_lab as lab
from translucent.systems.lgm50_solver import _pybamm, _r, _solver_info

HEAT_CAPACITIES = tuple(f"{part} specific heat capacity [J.kg-1.K-1]" for part in (
    "Negative electrode", "Positive electrode", "Separator", "Negative current collector",
    "Positive current collector"))
SAMPLE_S = 10.0          # the lab's logging period, and the grid every forecast is reported on
WORKERS = 6
PREPOSTERIOR_DRAWS = 2048  # seeded noise draws per candidate test in rank_measurements (half of them antithetic)
PREPOSTERIOR_SEED = 20261101
V_MIN_DISCHARGE = 2.5    # a record test discharges at most to the parameter set's lower cut-off


# ------------------------------------------------------------------ one charge

def _parameters(pybamm, ambient: float, h: float, k: float):
    pv = pybamm.ParameterValues(fc.PARAMETER_SET)
    tk = 273.15 + ambient
    pv.update({"Ambient temperature [K]": tk, "Initial temperature [K]": tk,
               "Total heat transfer coefficient [W.m-2.K-1]": h})
    pv.update({name: pv[name] * k for name in HEAT_CAPACITIES})
    return pv


def _steps(pybamm, rates: list[float], rest_min: float, prefix: int | None):
    """The charge as PyBaMM steps; `prefix` keeps only the first n constant-current steps (no fallback, no rest)."""
    import numpy as np

    edges = np.linspace(fc.SOC_START, fc.SOC_END, fc.STEPS + 1)
    target = (fc.SOC_END - fc.SOC_START) * fc.CAPACITY_AH
    steps = []
    for i, c in enumerate(rates[: prefix or fc.STEPS]):
        q_end = (edges[i + 1] - fc.SOC_START) * fc.CAPACITY_AH
        done = pybamm.step.CustomTermination(
            name=f"charged {q_end:.3f} A.h",
            event_function=(lambda q: lambda variables: variables["Discharge capacity [A.h]"] + q)(q_end))
        steps.append(pybamm.step.c_rate(-c, duration=fc.TIME_CAP_S, termination=[done, f"{fc.V_MAX} V"],
                                        period=SAMPLE_S))
    if prefix:
        return steps
    full = pybamm.step.CustomTermination(
        name="charged target", event_function=lambda variables: variables["Discharge capacity [A.h]"] + target)
    steps.append(pybamm.step.voltage(fc.V_MAX, duration=fc.TIME_CAP_S, termination=[full, "C/50"],
                                     period=SAMPLE_S))
    if rest_min > 0:
        steps.append(pybamm.step.rest(duration=rest_min * 60, period=SAMPLE_S))
    return steps


def charge(job: tuple) -> dict:
    """One charge under one thermal model: arrays, the charge-phase summary and the heat generation.

    job = (rates, ambient, rest_min, h, k, prefix). Top level, so a process pool can run it."""
    rates, ambient, rest_min, h, k, prefix = job
    pybamm = _pybamm()
    import numpy as np

    model = pybamm.lithium_ion.DFN(options=dict(fc.MODEL_OPTIONS))
    solver = pybamm.IDAKLUSolver()
    sim = pybamm.Simulation(model, parameter_values=_parameters(pybamm, ambient, h, k),
                            experiment=pybamm.Experiment(_steps(pybamm, list(rates), rest_min, prefix)),
                            solver=solver)
    try:
        sol = sim.solve(initial_soc=fc.SOC_START)
    except Exception as exc:  # the solver's own failure is a recorded outcome
        return {"status": "solver_error", "error": f"{type(exc).__name__}: {exc}"[:300]}
    t = sol["Time [s]"].entries
    q = -sol["Discharge capacity [A.h]"].entries
    out = {"t": t, "q": q, "v": sol["Voltage [V]"].entries, "T": sol["X-averaged cell temperature [C]"].entries,
           "phi": sol["Negative electrode surface potential difference at separator interface [V]"].entries * 1000,
           "plated": sol["Loss of capacity to negative lithium plating [A.h]"].entries,
           "current": -sol["Current [A]"].entries, "heat": sol["Total heating [W]"].entries}
    target = (fc.SOC_END - fc.SOC_START) * fc.CAPACITY_AH
    if prefix:
        target = (prefix / fc.STEPS) * target
    reached = q.max() >= target - 1e-3
    kk = int(np.argmax(q >= target - 1e-3)) if reached else len(t) - 1
    cut = slice(0, kk + 1)
    finite = all(np.all(np.isfinite(a)) for a in out.values())
    sane = finite and out["T"].max() < 120 and out["T"].min() > ambient - 1 and \
        out["v"][cut].max() < fc.V_MAX + 0.05 and out["plated"].min() > -1e-6
    out["status"] = ("ok" if reached else "not_reached") if sane else "nonsense"
    out["error"] = None if sane else "failed sanity checks"
    out["t80_s"] = (float(np.interp(target, q[: kk + 1], t[: kk + 1])) if kk > 0 else float(t[kk])) \
        if reached else None
    # the lumped balance's constants, so a fit can integrate it without the DFN: C (J/K, at this k) and A (m^2)
    out["heat_capacity"] = float(sol["Volume-averaged effective heat capacity [J.K-1.m-3]"].entries[0]) * \
        float(sim.parameter_values["Cell volume [m3]"])
    out["area"] = float(sim.parameter_values["Cell cooling surface area [m2]"])
    out["min_anode"] = float(out["phi"][cut].min())
    out["max_T"] = float(out["T"].max())
    out["plated_end"] = float(out["plated"][kk])
    out["solver"] = _solver_info(pybamm, solver, model, {"Total heat transfer coefficient [W.m-2.K-1]": "model",
                                                         "specific heat capacities": "model"},
                                 dict(fc.MODEL_OPTIONS))
    return out


def charges(jobs: list[tuple]) -> list[dict]:
    """Several charges, in parallel when there is more than one (fork: PyBaMM is already imported)."""
    if len(jobs) == 1:
        return [charge(jobs[0])]
    _pybamm()
    try:
        ctx = mp.get_context("fork")
    except ValueError:                  # no fork (Windows): one after another
        return [charge(j) for j in jobs]
    with cf.ProcessPoolExecutor(min(WORKERS, len(jobs)), mp_context=ctx) as pool:
        return list(pool.map(charge, jobs))


# ------------------------------------------------------------------ a record test's stimulus

def record_run(job: tuple) -> dict:
    """A record test's constant current under one thermal model: from the stated SOC, at the recorded current, in the
    recorded (or documented) ambient, from the recorded start temperature, for the record's duration or until the
    cell's voltage limit (2.5 V discharging, 4.2 V charging).

    job = (mode, current_A, start_soc, ambient, t0_degC, duration_s, h, k). Top level, for a process pool."""
    mode, current, start_soc, ambient, t0, duration, h, k = job
    pybamm = _pybamm()

    model = pybamm.lithium_ion.DFN(options=dict(fc.MODEL_OPTIONS))
    solver = pybamm.IDAKLUSolver()
    pv = _parameters(pybamm, ambient, h, k)
    pv.update({"Initial temperature [K]": 273.15 + t0})
    signed = abs(current) if int(mode) == 1 else -abs(current)
    limit = f"{V_MIN_DISCHARGE} V" if int(mode) == 1 else f"{fc.V_MAX} V"
    step = pybamm.step.current(signed, duration=max(float(duration), SAMPLE_S), termination=limit, period=SAMPLE_S)
    sim = pybamm.Simulation(model, parameter_values=pv, experiment=pybamm.Experiment([step]), solver=solver)
    try:
        sol = sim.solve(initial_soc=float(start_soc))
    except Exception as exc:  # the solver's own failure is a recorded outcome
        return {"status": "solver_error", "error": f"{type(exc).__name__}: {exc}"[:300]}
    out = {"t": sol["Time [s]"].entries, "T": sol["X-averaged cell temperature [C]"].entries,
           "v": sol["Voltage [V]"].entries, "heat": sol["Total heating [W]"].entries}
    out["status"], out["error"], out["t80_s"] = "ok", None, None
    out["heat_capacity"] = float(sol["Volume-averaged effective heat capacity [J.K-1.m-3]"].entries[0]) * \
        float(sim.parameter_values["Cell volume [m3]"])
    out["area"] = float(sim.parameter_values["Cell cooling surface area [m2]"])
    out["solver"] = _solver_info(pybamm, solver, model, {"Total heat transfer coefficient [W.m-2.K-1]": "model",
                                                         "specific heat capacities": "model"},
                                 dict(fc.MODEL_OPTIONS))
    return out


def _is_record(src: dict) -> bool:
    return (src.get("params") or {}).get("kind") == "record"


def _source_job(src: dict, h: float, k: float) -> tuple:
    p = src["params"]
    if _is_record(src):
        return ("record", (p["mode"], p["current_A"], p["start_soc"], p["ambient"], p["t0_degC"], p["duration_s"],
                           h, k))
    return ("charge", ([p[c] for c in fc.C_RATES], p["ambient"], p["rest_min"], h, k, None))


def _run_job(tagged: tuple) -> dict:
    kind, job = tagged
    return record_run(job) if kind == "record" else charge(job)


def source_runs(tagged: list[tuple]) -> list[dict]:
    """charges() for a mix of lab-test charges and record tests; plain charges() when there is no record test."""
    if all(kind == "charge" for kind, _ in tagged):
        return charges([job for _, job in tagged])
    if len(tagged) == 1:
        return [_run_job(tagged[0])]
    _pybamm()
    try:
        ctx = mp.get_context("fork")
    except ValueError:
        return [_run_job(j) for j in tagged]
    with cf.ProcessPoolExecutor(min(WORKERS, len(tagged)), mp_context=ctx) as pool:
        return list(pool.map(_run_job, tagged))


def record_test(params: dict, inputs: dict) -> dict:
    """A record test: the segment's series rebased to its start, and what it measured. Computed from the staged record;
    refused (status nonsense) when the current is not constant within lab.CC_DEPARTURE_MAX or the voltage contradicts
    the stated mode."""
    import numpy as np

    rec = inputs.get("record")
    if not isinstance(rec, dict):
        raise ValueError("the record's series were not staged")
    win, ser = rec["window"], rec["series"]
    st = ser["surface_temperature"]
    cur = ser["current"]
    t = np.asarray(st["t"], float)
    if len(t) < 3 or cur["t"] != st["t"]:
        return {"status": "nonsense", "error": "the surface temperature and current are not on one clock in this "
                                               "segment"}
    y = np.asarray(st["y"], float)
    i = np.abs(np.asarray(cur["y"], float))
    med = float(np.median(i))
    departure = float(np.max(np.abs(i - med)) / med) if med > 0 else math.inf
    t0 = float(t[0])
    if "chamber_temperature" in ser and ser["chamber_temperature"]["t"] == st["t"]:
        ambient = float(np.median(np.asarray(ser["chamber_temperature"]["y"], float)))
        ambient_source = "recorded chamber, median over the segment"
    elif win.get("documented_ambient_degC") is not None:
        ambient = float(win["documented_ambient_degC"])
        ambient_source = "the record's documented conditions"
    else:
        return {"status": "nonsense", "error": "no recorded or documented ambient"}
    mode = int(params["mode"])
    v = ser.get("voltage")
    trend = None
    if v is not None and v["t"] == st["t"]:
        vv = np.asarray(v["y"], float)
        trend = "falling" if vv[-1] < vv[0] - 0.005 else "rising" if vv[-1] > vv[0] + 0.005 else "flat"
    ok = departure <= lab.CC_DEPARTURE_MAX and (trend == ("falling" if mode == 1 else "rising"))
    status = "ok" if ok else "nonsense"
    error = None if ok else (f"the current departs {departure:.1%} from its median" if
                             departure > lab.CC_DEPARTURE_MAX else f"the voltage is {trend}")
    tr = (t - t0).tolist()
    channels = {"surface_temperature": ("degC", [_r(x, 4) for x in y]),
                "current": ("A", [_r(x, 5) for x in i])}
    if v is not None and v["t"] == st["t"]:
        channels["voltage"] = ("V", [_r(x, 5) for x in v["y"]])
    duration_min = (float(t[-1]) - t0) / 60.0
    return {"status": status, "error": error,
            "scalars": {"status": (status, ""), "test_current": (_r(med, 5), "A"),
                        "test_c_rate": (_r(med / float(win["capacity_Ah"]), 4), "1/h"),
                        "test_duration": (_r(duration_min, 3), "min"),
                        "temperature_start": (_r(float(y[0]), 4), "degC"),
                        "temperature_rise": (_r(float(y.max() - y[0]), 4), "delta_degC"),
                        "ambient_temperature": (_r(ambient, 4), "degC"),
                        "current_departure": (_r(departure, 5), "1")},
            "series": {"t": tr, "channels": channels}, "solver": {"name": "record", "version": "1"},
            "details": {"record_window_s": [win["t_start_s"], win["t_end_s"]], "segment": win.get("segment"),
                        "ambient_source": ambient_source, "voltage_trend": trend,
                        "mode": "discharge" if mode == 1 else "charge", "start_soc_assumed": float(params["start_soc"]),
                        "limits": "a record segment as logged; its start state is the proposer's stated assumption"}}


def _grid(run: dict, end_s: float | None = None) -> list[float]:
    end = float(run["t"][-1]) if end_s is None else end_s
    n = int(math.floor(end / SAMPLE_S + 1e-9))
    return [i * SAMPLE_S for i in range(n + 1)]


def _on(grid, run: dict, key: str):
    import numpy as np

    return np.interp(grid, run["t"], run[key])


# ------------------------------------------------------------------ the simulated lab

def lab_charge(params: dict, condition: dict, request_id: str) -> dict:
    """What a lab logs: surface temperature, voltage and current every 10 s, with the condition's noise. Nothing
    of the condition itself leaves this function."""
    import numpy as np

    rates = [params[c] for c in fc.C_RATES]
    # a chamber offset (an omitted mechanism: the chamber runs warmer or colder than its setpoint) when sealed
    chamber = params["ambient"] + float(condition.get("ambient_offset_K", 0.0))
    run = charge((rates, chamber, params["rest_min"], condition["h"], condition["k"], None))
    if run["status"] == "solver_error":
        return {"status": "solver_error", "error": "the simulated lab could not run this test"}
    grid = _grid(run)
    seed = int(hashlib.sha256(f"{condition['seed']}:{request_id}".encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    temp = _on(grid, run, "T") + rng.normal(0.0, condition["noise_K"], len(grid))
    volt = _on(grid, run, "v") + rng.normal(0.0, condition["noise_V"], len(grid))
    cur = _on(grid, run, "current")
    temp, volt = np.round(temp, 2), np.round(volt, 4)
    scalars = {"status": (run["status"], ""), "max_temperature": (_r(temp.max(), 2), "degC"),
               "temperature_end": (_r(temp[-1], 2), "degC"),
               "noise_temperature": (condition["noise_K"], "delta_degC")}
    if run["t80_s"] is not None:
        scalars["charge_time_80"] = (_r(run["t80_s"] / 60, 2), "min")
    return {"status": run["status"], "error": None if run["status"] != "nonsense" else "the test failed its checks",
            "scalars": scalars,
            "series": {"t": grid, "channels": {"surface_temperature": ("degC", [float(x) for x in temp]),
                                               "voltage": ("V", [float(x) for x in volt]),
                                               "current": ("A", [_r(x, 4) for x in cur])}},
            "solver": {"name": "simulated lab", "model": "the hidden condition's DFN; values withheld",
                       "noise": f"surface temperature sigma {condition['noise_K']} K, voltage sigma "
                                f"{condition['noise_V'] * 1000:g} mV, every {SAMPLE_S:g} s"},
            "details": None}


# ------------------------------------------------------------------ forecasts under a model version

def _members(model: dict) -> list[tuple[float, float]]:
    return [(float(m["h"]), float(m["k"])) for m in model["members"]]


def _extrapolation(model: dict, params: dict) -> list[str]:
    """Where a forecast leaves the model's validity range."""
    v, out = model.get("validity") or {}, []
    amb = v.get("ambient_degC")
    if amb and not amb[0] - 1.0 <= params["ambient"] <= amb[1] + 1.0:
        out.append(f"ambient {params['ambient']:g} degC is outside the fitted {amb[0]:g}-{amb[1]:g} degC")
    rates = v.get("c_rate")
    hi = max(params.get(c, 0.0) for c in fc.C_RATES)
    if rates and hi > rates[1] + 1e-9:
        out.append(f"{hi:g}C is above the fitted {rates[1]:g}C")
    return out


def forecast(params: dict, model: dict) -> dict:
    import numpy as np

    rates = [params[c] for c in fc.C_RATES]
    members = _members(model)
    runs = charges([(rates, params["ambient"], params["rest_min"], h, k, None) for h, k in members])
    bad = next((r for r in runs if r["status"] == "solver_error"), None)
    if bad:
        return {"status": "solver_error", "error": bad["error"]}
    est = runs[0]
    grid = _grid(est)
    temps = np.array([_on(grid, r, "T") for r in runs])
    lo, hi = temps.min(axis=0), temps.max(axis=0)
    t80 = [r["t80_s"] for r in runs]
    status = "ok" if all(r["status"] == "ok" for r in runs) else \
        ("not_reached" if all(r["status"] in ("ok", "not_reached") for r in runs) else "nonsense")
    anodes = [r["min_anode"] for r in runs]
    scalars = {"status": (status, ""), "min_anode_potential": (_r(min(anodes), 3), "mV"),
               "max_temperature": (_r(max(r["max_T"] for r in runs), 3), "degC"),
               "anode_spread": (_r(max(anodes) - min(anodes), 3), "mV"),
               "temperature_spread": (_r(float((hi - lo).max()), 3), "delta_degC"),
               "members": (len(runs), ""), "model_version": (int(model["version"]), ""),
               "extrapolation": ("yes" if _extrapolation(model, params) else "no", "")}
    if all(x is not None for x in t80):
        scalars["charge_time_80"] = (_r(max(t80) / 60, 3), "min")
    return {"status": status, "error": None if status != "nonsense" else "failed sanity checks",
            "scalars": scalars,
            "series": {"t": grid, "channels": {
                "cell_temperature": ("degC", [_r(x, 3) for x in temps[0]]),
                "temperature_lo": ("degC", [_r(x, 3) for x in lo]),
                "temperature_hi": ("degC", [_r(x, 3) for x in hi]),
                "anode_potential": ("mV", [_r(x, 3) for x in _on(grid, est, "phi")]),
                "voltage": ("V", [_r(x, 5) for x in _on(grid, est, "v")]),
                "current": ("A", [_r(x, 4) for x in _on(grid, est, "current")])}},
            "solver": est["solver"],
            "details": {"members": [{"h": h, "k": k, "min_anode_potential_mV": _r(r["min_anode"], 3),
                                     "max_temperature_degC": _r(r["max_T"], 3),
                                     "charge_time_80_min": _r(r["t80_s"] / 60, 3) if r["t80_s"] else None,
                                     "plated_lithium_Ah": _r(r["plated_end"], 7)}
                                    for (h, k), r in zip(members, runs)],
                        "extrapolation": _extrapolation(model, params)}}


# ------------------------------------------------------------------ fitting

def _sse(t, obs, heat, ambient: float, hs, ks, c0: float, area: float, start: float | None = None):
    """Sum of squared residuals of the lumped balance C0 k dT/dt = Q(t) - h A (T - T_amb) against `obs`, integrated
    exactly for Q linear in each interval (exponential integrator) from the ambient temperature (or `start`, a record
    test's recorded start temperature), for every (h, k) pair at once."""
    import numpy as np

    hs, ks = np.asarray(hs, float), np.asarray(ks, float)
    a = hs * area / (c0 * ks)
    temp = np.full(hs.shape, float(ambient if start is None else start))
    sse = (obs[0] - temp) ** 2
    for i in range(1, len(t)):
        e = np.exp(-a * (t[i] - t[i - 1]))
        q = 0.5 * (heat[i] + heat[i - 1]) / (hs * area)
        temp = ambient + (temp - ambient) * e + q * (1 - e)
        sse = sse + (obs[i] - temp) ** 2
    return sse


def _source_arrays(src: dict):
    import numpy as np

    s = src["series"]
    return np.asarray(s["t"], float), np.asarray(s["surface_temperature"], float)


def _sse_grid(sources, heats, hs, ks, c0: float, area: float):
    """The lumped balance's sum of squared residuals over every source, for each (h, k) on the grid."""
    import numpy as np

    H, K = np.meshgrid(hs, ks, indexing="ij")
    total = np.zeros(H.size)
    for src, heat in zip(sources, heats):
        t, obs = _source_arrays(src)
        total += _sse(t, obs, heat, src["params"]["ambient"], H.ravel(), K.ravel(), c0, area,
                      src["params"]["t0_degC"] if _is_record(src) else None)
    return total.reshape(H.shape)


def _heat_on_sources(sources, h: float, k: float):
    """Each source's heat generation on its own sample times, from a DFN solve under (h, k)."""
    import numpy as np

    runs = source_runs([_source_job(s, h, k) for s in sources])
    for r in runs:
        if r["status"] == "solver_error":
            raise RuntimeError(r["error"])
    for s, r in zip(sources, runs):
        # a record test is compared only where the model runs too: the first solve fixes the cut (2 % inside the
        # model's end, which a later (h, k) moves a little), kept for every later solve
        if _is_record(s) and "cut_s" not in s:
            end = min(float(r["t"][-1]), float(s["series"]["t"][-1]))
            s["cut_s"] = 0.98 * end
            keep = [j for j, x in enumerate(s["series"]["t"]) if x <= s["cut_s"]]
            s["series"] = {key: [vals[j] for j in keep] for key, vals in s["series"].items()}
    return [np.interp(_source_arrays(s)[0], r["t"], r["heat"]) for s, r in zip(sources, runs)], runs


def _residual_structure(t, obs, model_T, rest_from_s):
    import numpy as np

    res = obs - model_T
    charge_part, rest_part = res[t < rest_from_s], res[t >= rest_from_s]
    lag1 = float(np.corrcoef(res[:-1], res[1:])[0, 1]) if len(res) > 3 and np.std(res) > 0 else 0.0
    return {"rmse_K": _r(np.sqrt(np.mean(res ** 2)), 4), "bias_K": _r(np.mean(res), 4),
            "max_abs_K": _r(np.max(np.abs(res)), 4),
            "charge_rmse_K": _r(np.sqrt(np.mean(charge_part ** 2)), 4) if len(charge_part) else None,
            "rest_rmse_K": _r(np.sqrt(np.mean(rest_part ** 2)), 4) if len(rest_part) else None,
            "lag1_autocorrelation": _r(lag1, 3)}


def _n_eff(res) -> float:
    """Samples divided by the residuals' integrated autocorrelation time (to the first negative lag)."""
    import numpy as np

    r = res - res.mean()
    var = float(np.dot(r, r))
    if var <= 0:
        return float(len(r))
    tau = 1.0
    for lag in range(1, min(len(r) // 2, 200)):
        rho = float(np.dot(r[:-lag], r[lag:])) / var
        if rho <= 0:
            break
        tau += 2 * rho
    return max(1.0, len(r) / tau)


FIT_COARSE_H, FIT_COARSE_K = 121, 41      # the whole family: h log-spaced over H_BOUNDS, k over K_BOUNDS
FIT_FINE_H, FIT_FINE_K = 161, 61          # a local grid around the coarse minimum, widened until it holds the region
FIT_FINE_HALF = 3                         # the local grid's first half-width, in coarse steps


def _local_grid(h0: float, k0: float, family: int, widen: int):
    """A fine grid around (h0, k0): FIT_FINE_HALF coarse steps each side, doubled `widen` times, clipped to the
    family's bounds."""
    import numpy as np

    step_h = math.log(lab.H_BOUNDS[1] / lab.H_BOUNDS[0]) / (FIT_COARSE_H - 1)
    half_h = FIT_FINE_HALF * step_h * 2 ** widen
    lo, hi = max(lab.H_BOUNDS[0], h0 * math.exp(-half_h)), min(lab.H_BOUNDS[1], h0 * math.exp(half_h))
    hs = np.exp(np.linspace(math.log(lo), math.log(hi), FIT_FINE_H))
    if family != 2:
        return hs, np.array([1.0])
    half_k = FIT_FINE_HALF * (lab.K_BOUNDS[1] - lab.K_BOUNDS[0]) / (FIT_COARSE_K - 1) * 2 ** widen
    ks = np.linspace(max(lab.K_BOUNDS[0], k0 - half_k), min(lab.K_BOUNDS[1], k0 + half_k), FIT_FINE_K)
    return hs, ks


def _minimum(sources, heats, h0: float, k0: float, family: int, c0: float, area: float):
    """The least-squares (h, k): the coarse grid over the family, then a fine grid around its minimum (the coarse
    h step, 3.3 %, is wider than one good test's uncertainty)."""
    import numpy as np

    hs = np.exp(np.linspace(math.log(lab.H_BOUNDS[0]), math.log(lab.H_BOUNDS[1]), FIT_COARSE_H))
    ks = np.linspace(lab.K_BOUNDS[0], lab.K_BOUNDS[1], FIT_COARSE_K) if family == 2 else np.array([1.0])
    sse = _sse_grid(sources, heats, hs, ks, c0, area)
    i, j = np.unravel_index(int(np.argmin(sse)), sse.shape)
    hs, ks = _local_grid(float(hs[i]), float(ks[j]), family, 0)
    sse = _sse_grid(sources, heats, hs, ks, c0, area)
    i, j = np.unravel_index(int(np.argmin(sse)), sse.shape)
    return float(hs[i]), float(ks[j])


def fit_thermal(params: dict, base: dict, sources: list[dict]) -> dict:
    import numpy as np

    family = int(params["family"])
    h_est, k_est = float(base["estimate"]["h"]), float(base["estimate"]["k"])
    history = []
    for _ in range(4):           # heat generation at the estimate, refit, until h moves less than 1 %
        heats, runs = _heat_on_sources(sources, h_est, k_est)
        c0, area = runs[0]["heat_capacity"] / k_est, runs[0]["area"]
        h_new, k_new = _minimum(sources, heats, h_est, k_est, family, c0, area)
        history.append({"h": _r(h_new, 3), "k": _r(k_new, 3)})
        moved = max(abs(h_new - h_est) / h_est, abs(k_new - k_est) / k_est)
        h_est, k_est = h_new, k_new
        if moved < 0.01:
            break
    heats, runs = _heat_on_sources(sources, h_est, k_est)
    c0, area = runs[0]["heat_capacity"] / k_est, runs[0]["area"]
    # the full DFN at the estimate: the residuals reported, and the noise scale and effective sample size
    per, res_all = [], []
    for s, r in zip(sources, runs):
        t, obs = _source_arrays(s)
        model_T = np.interp(t, r["t"], r["T"])
        rest_from = r["t80_s"] if r["t80_s"] is not None else t[-1] + 1
        per.append({"lab_test": s["n"], "result_id": s["result_id"], **_residual_structure(t, obs, model_T, rest_from)})
        res_all.append(obs - model_T)
    res = np.concatenate(res_all)
    n = len(res)
    noise = max(float(s.get("noise_K") or lab.NOISE_K) for s in sources)
    p = 2 if family == 2 else 1
    sigma2 = max(noise ** 2, float(np.sum(res ** 2)) / max(1, n - p))
    # samples count once the residuals decorrelate, and at most once a minute: 10 s samples of a thermal system whose
    # time constant is tens of minutes are not that many independent observations
    minutes = sum((_source_arrays(src)[0][-1] - _source_arrays(src)[0][0]) / 60 for src in sources)
    scale = min(_n_eff(res), max(1.0, minutes)) / n
    limit = 5.99 if family == 2 else 3.84
    for widen in range(6):       # a local grid fine enough to resolve the region, widened until the region fits in it
        hs, ks = _local_grid(h_est, k_est, family, widen)
        sse = _sse_grid(sources, heats, hs, ks, c0, area)
        i, j = np.unravel_index(int(np.argmin(sse)), sse.shape)
        dchi = (sse - sse[i, j]) / sigma2 * scale
        inside = np.argwhere(dchi <= limit)
        cut = (inside[:, 0].min() == 0 and hs[0] > lab.H_BOUNDS[0] * 1.0001) or \
            (inside[:, 0].max() == len(hs) - 1 and hs[-1] < lab.H_BOUNDS[1] * 0.9999) or \
            (family == 2 and ((inside[:, 1].min() == 0 and ks[0] > lab.K_BOUNDS[0] + 1e-9) or
                              (inside[:, 1].max() == len(ks) - 1 and ks[-1] < lab.K_BOUNDS[1] - 1e-9)))
        if not cut:
            break
    pts = {(float(hs[a]), float(ks[b])) for a, b in inside}
    est = (float(hs[i]), float(ks[j]))
    extremes = [min(pts, key=lambda x: x[0]), max(pts, key=lambda x: x[0])]
    if family == 2:
        extremes += [min(pts, key=lambda x: x[1]), max(pts, key=lambda x: x[1])]
    members = [est] + [m for m in dict.fromkeys(extremes) if m != est]
    h_nom, k_nom = float(base["estimate"]["h"]), float(base["estimate"]["k"])
    nom_sse = float(_sse_grid(sources, heats, [h_nom], [k_nom], c0, area)[0, 0])
    nom_dchi = (nom_sse - float(sse[i, j])) / sigma2 * scale
    consistent = bool(nom_dchi <= limit)
    ambients = [s["params"]["ambient"] for s in sources]
    charging = [s for s in sources if not _is_record(s)]
    recorded = [s for s in sources if _is_record(s)]
    rates = [s["params"][c] for s in charging for c in fc.C_RATES] + \
        [abs(s["params"]["current_A"]) / fc.CAPACITY_AH for s in recorded]
    validity = {"ambient_degC": [min(ambients), max(ambients)], "c_rate": [_r(min(rates), 4), _r(max(rates), 4)],
                "rest_min": sorted({s["params"]["rest_min"] for s in charging}),
                "lab_tests": [s["n"] for s in sources]}
    if recorded:
        validity["record_tests"] = [{"test": s["n"], "mode": "discharge" if s["params"]["mode"] == 1 else "charge",
                                     "c_rate": _r(abs(s["params"]["current_A"]) / fc.CAPACITY_AH, 4),
                                     "start_soc": s["params"]["start_soc"]} for s in recorded]
    h_rng = [min(x[0] for x in pts), max(x[0] for x in pts)]
    k_rng = [min(x[1] for x in pts), max(x[1] for x in pts)]
    # the family's bounds cut the region: the evidence allows values the family does not include
    at_bound = h_rng[0] <= lab.H_BOUNDS[0] * 1.0001 or h_rng[1] >= lab.H_BOUNDS[1] * 0.9999 or \
        (family == 2 and (k_rng[0] <= lab.K_BOUNDS[0] + 1e-9 or k_rng[1] >= lab.K_BOUNDS[1] - 1e-9))
    rmse = float(np.sqrt(np.mean(res ** 2)))
    # out of family: no member explains the tests within the noise; the region then says nothing reliable
    adequate = rmse <= max(lab.ADEQUATE_NOISE_MULTIPLE * noise, lab.ADEQUATE_FLOOR_K)
    index = {(float(hs[a]), float(ks[b])): (a, b) for a in range(len(hs)) for b in range(len(ks))}
    return {"status": "ok", "error": None,
            "scalars": {"status": ("ok", ""), "heat_transfer_coefficient": (_r(est[0], 3), "W/m^2/K"),
                        "thermal_mass_scale": (_r(est[1], 3), ""),
                        "thermal_fit_rmse": (_r(rmse, 4), "delta_degC"),
                        "consistent_with_nominal": ("yes" if consistent else "no", ""),
                        "fit_adequate": ("yes" if adequate else "no", ""),
                        "members": (len(members), "")},
            "series": None, "solver": runs[0]["solver"],
            "details": {
                "family": lab.FAMILIES[family], "estimate": {"h": _r(est[0], 3), "k": _r(est[1], 3)},
                "region_95": {"h": [_r(x, 3) for x in h_rng], "k": [_r(x, 3) for x in k_rng]},
                "members": [{"h": _r(h, 3), "k": _r(k, 3), "delta_chi2": _r(float(dchi[index[(h, k)]]), 3)}
                            for h, k in members],
                "adequate": adequate, "effective_ratio": _r(scale, 5),
                "adequacy_rule": f"RMSE at most {lab.ADEQUATE_NOISE_MULTIPLE:g} x the noise (floor "
                                 f"{lab.ADEQUATE_FLOOR_K:g} K); otherwise the family is missing something and the "
                                 "region is not an uncertainty",
                "nominal": {"h": h_nom, "k": k_nom, "delta_chi2": _r(nom_dchi, 3), "limit": limit,
                            "inside_region": consistent},
                "residuals": per, "noise_K": noise, "sigma_used_K": _r(math.sqrt(sigma2), 4),
                "effective_samples": _r(scale * n, 1), "samples": n, "iterations": history,
                "validity": validity, "region_at_bound": bool(at_bound),
                "method": ("heat generation Q(t) from the DFN at the estimate; the lumped balance C k dT/dt = Q - h A "
                           "(T - T_amb) fitted to the surface temperature by least squares on a grid (h log-spaced "
                           f"{lab.H_BOUNDS[0]:g}-{lab.H_BOUNDS[1]:g}" +
                           (f", k {lab.K_BOUNDS[0]:g}-{lab.K_BOUNDS[1]:g}" if family == 2 else ", k = 1") +
                           ", then a fine local grid around its minimum, widened until it holds the region), repeated "
                           "until h and k move under 1 %; region: delta chi^2 under the 95 % limit, with sigma "
                           "the larger of the lab's noise and the fit's RMSE and the samples discounted by the "
                           "residuals' autocorrelation and capped at one a minute; residuals reported from the full "
                           "DFN at the estimate"),
                "limits": "surface temperature only; says nothing direct about core temperature, anode potential "
                          "or plating"}}


# ------------------------------------------------------------------ held-out check

def check_model(params: dict, model: dict, sources: list[dict]) -> dict:
    import numpy as np

    members = _members(model)
    runs = source_runs([_source_job(s, h, k) for s in sources for h, k in members])
    if any(r["status"] == "solver_error" for r in runs):
        return {"status": "solver_error", "error": "a member's forecast failed"}
    per, all_res, covered, total = [], [], 0, 0
    for n_s, s in enumerate(sources):
        t, obs = _source_arrays(s)
        mine = runs[n_s * len(members):(n_s + 1) * len(members)]
        if _is_record(s):         # compared where every member's run reaches
            end = min(min(float(r["t"][-1]) for r in mine), float(t[-1]))
            keep = t <= 0.98 * end
            t, obs = t[keep], obs[keep]
        temps = np.array([np.interp(t, r["t"], r["T"]) for r in mine])
        # the band: the lab's noise, or the error the fit itself had to allow when that is larger
        noise = max(float(s.get("noise_K") or lab.NOISE_K), float(model.get("sigma_used_K") or 0.0))
        res = obs - temps[0]
        inside = (obs >= temps.min(axis=0) - 2 * noise) & (obs <= temps.max(axis=0) + 2 * noise)
        covered, total = covered + int(inside.sum()), total + len(obs)
        all_res.append(res)
        per.append({"lab_test": s["n"], "result_id": s["result_id"], "rmse_K": _r(np.sqrt(np.mean(res ** 2)), 4),
                    "max_abs_K": _r(np.max(np.abs(res)), 4), "bias_K": _r(np.mean(res), 4),
                    "max_temperature_observed": _r(obs.max(), 3), "max_temperature_forecast": _r(temps[0].max(), 3),
                    "coverage": _r(inside.mean(), 4)})
    res = np.concatenate(all_res)
    return {"status": "ok", "error": None,
            "scalars": {"status": ("ok", ""), "heldout_rmse": (_r(np.sqrt(np.mean(res ** 2)), 4), "delta_degC"),
                        "heldout_max_error": (_r(np.max(np.abs(res)), 4), "delta_degC"),
                        "heldout_coverage": (_r(covered / total, 4), ""),
                        "model_version": (int(model["version"]), "")},
            "series": None, "solver": runs[0]["solver"],
            "details": {"tests": per, "model_sources": (model.get("validity") or {}).get("lab_tests", []),
                        "coverage_rule": "observed inside the members' range widened by twice the larger of the lab's noise and the fit's sigma"}}


# ------------------------------------------------------------------ which measurement next

def menu(params: dict) -> list[dict]:
    """The candidate tests: four from the developer's menu, and the one the proposer composed (test_*)."""
    design = [params[c] for c in fc.C_RATES]
    composed = [params[f"test_{c}"] for c in fc.C_RATES]
    return [{"label": "the design, then a 60 min rest", "rates": design, "rest_min": 60.0},
            {"label": "the design, no rest", "rates": design, "rest_min": 0.0},
            {"label": "1C constant current, then a 60 min rest", "rates": [1.0] * 4, "rest_min": 60.0},
            {"label": "0.5C constant current, then a 60 min rest", "rates": [0.5] * 4, "rest_min": 60.0},
            {"label": "the composed test", "rates": composed, "rest_min": float(params["test_rest_min"])}]


def _weights(model: dict) -> list[float]:
    """Each member's weight from the fit's delta chi^2 (exp(-delta/2), normalised): numbers, not judgement. A model
    with one member has weight 1 on it."""
    d = [float(m.get("delta_chi2", 0.0)) for m in model["members"]]
    w = [math.exp(-x / 2) for x in d]
    return [x / sum(w) for x in w]


def _loss(run: dict, margin: float, t_limit: float) -> float:
    """Engineering loss in minutes: the charge time, plus the frozen penalty when the limits are not met."""
    if run["status"] != "ok" or run["t80_s"] is None:
        return fc.TIME_CAP_S / 60 + lab.VIOLATION_PENALTY_MIN
    t = run["t80_s"] / 60
    return t if _meets([run], margin, t_limit) else t + lab.VIOLATION_PENALTY_MIN


def _risk(table, w) -> tuple[float, int]:
    """min over protocols of the expected loss under weights w: (value, protocol index)."""
    scores = [sum(wi * li for wi, li in zip(w, row)) for row in table]
    i = min(range(len(scores)), key=scores.__getitem__)
    return scores[i], i


def preposterior(temps, table, w, noise: float, ratio: float, seed: int) -> float:
    """E over the truth (member j, weight w_j) and seeded noise draws of R(posterior): the expected risk after a test
    whose forecasts are temps (members x grid). An observation is member j's forecast plus N(0, noise^2 / ratio) at
    every grid point, the same discount the fit applies, so the update is Bayes' rule on the model it simulates."""
    import numpy as np

    temps = np.asarray(temps, dtype=float)
    z = np.random.default_rng(seed).standard_normal((PREPOSTERIOR_DRAWS // 2, temps.shape[1]))
    z = np.concatenate([z, -z])                    # antithetic pairs: same expectation, less sampling error
    logw, losses = np.log(np.maximum(np.asarray(w, dtype=float), 1e-300)), np.asarray(table, dtype=float).T
    after = 0.0
    for j in range(len(temps)):                    # member j as the truth, observed with noise
        diff = temps[j] - temps                    # members x grid
        d2 = ratio * np.sum(diff ** 2, axis=1) / noise ** 2
        cross = 2.0 * math.sqrt(ratio) / noise * (z @ diff.T)     # draws x members
        ll = logw - 0.5 * (d2 + cross)
        post = np.exp(ll - ll.max(axis=1, keepdims=True))
        post /= post.sum(axis=1, keepdims=True)
        after += w[j] * float(np.mean(np.min(post @ losses, axis=1)))
    return after


def rank_measurements(params: dict, model: dict) -> dict:
    """Which test is worth running, by what it would change in the decision.

    Belief: the model version's members, weighted by their fit (one member: nothing to value). Decisions: the design
    in question and the greedy design under each member, each charged under every member; loss = charge time, plus
    a frozen penalty when a member says the limits are broken. R(w) = min over decisions of the expected loss. For
    each candidate test, every member in turn is taken as the truth and observed with seeded noise (the fit's noise,
    samples discounted as in the fit); the weights are updated by Bayes' rule on each draw and R is recomputed and
    averaged over the draws. value = R(now) - E[R(after)] - the test's frozen cost. These are forecasts from the candidate models,
    never from the lab; a model that fits badly makes them unreliable, and the result says so."""
    import numpy as np

    members, w = _members(model), _weights(model)
    margin, t_limit, ambient = float(params["margin_mV"]), float(params["t_limit_C"]), float(params["ambient"])
    design = [params[c] for c in fc.C_RATES]
    # the fit weighted its members with sigma_used (the larger of the noise and its RMSE): a test is no more informative
    noise = float(model.get("sigma_used_K") or model.get("noise_K") or lab.NOISE_K)
    ratio = float(model.get("effective_ratio") or 1.0)
    per_member = designs_per_member(members, ambient, margin, t_limit)
    decisions = [{"label": "the design in question", "rates": design}] + \
        [{"label": f"best for member {i + 1}", "rates": d} for i, d in enumerate(per_member) if d and d != design]
    unique, seen = [], set()
    for d in decisions:
        if tuple(d["rates"]) not in seen:
            seen.add(tuple(d["rates"]))
            unique.append(d)
    options = menu(params)
    jobs = [(d["rates"], ambient, 0.0, h, k, None) for d in unique for h, k in members] + \
        [(o["rates"], ambient, o["rest_min"], h, k, None) for o in options for h, k in members]
    runs = charges(jobs)
    if any(r["status"] == "solver_error" for r in runs):
        return {"status": "solver_error", "error": "a member's forecast failed"}
    m = len(members)
    table = [[_loss(runs[a * m + i], margin, t_limit) for i in range(m)] for a in range(len(unique))]
    now, pick = _risk(table, w)
    off = len(unique) * m
    rows = []
    for n_o, o in enumerate(options):
        mine = runs[off + n_o * m: off + (n_o + 1) * m]
        end = min(float(r["t"][-1]) for r in mine)
        grid = _grid(mine[0], end)
        temps = np.array([_on(grid, r, "T") for r in mine])
        spread = temps.max(axis=0) - temps.min(axis=0)
        after = min(preposterior(temps, table, w, noise, ratio, PREPOSTERIOR_SEED + n_o), now)
        # the exact preposterior risk never exceeds R(now); the min trims the draws' sampling error
        rows.append({"option": n_o + 1, "label": o["label"], "c_rates": o["rates"], "rest_min": o["rest_min"],
                     "duration_min": _r(end / 60, 1), "disagreement_K": _r(spread.max(), 4),
                     "end_spread_K": _r(spread[-1], 4), "expected_risk_after_min": _r(after, 3),
                     "value_min": _r(now - after - lab.TEST_COST_MIN, 3)})
    best = max(rows, key=lambda r: (r["value_min"], -r["duration_min"]))
    worth = best["value_min"] > 0
    design_runs = runs[:m]
    anodes = [r["min_anode"] for r in design_runs]
    return {"status": "ok", "error": None,
            "scalars": {"status": ("ok", ""), "best_measurement": (best["option"] if worth else 0, ""),
                        "best_value": (best["value_min"], "min"),
                        "best_disagreement": (best["disagreement_K"], "delta_degC"),
                        "decision_risk": (_r(now, 3), "min"),
                        "decision_spread": (_r(max(anodes) - min(anodes), 3), "mV"),
                        "model_version": (int(model["version"]), "")},
            "series": None, "solver": runs[0]["solver"],
            "details": {
                "options": rows, "weights": [_r(x, 4) for x in w],
                "members": [{"h": h, "k": k} for h, k in members],
                "decisions": [{"label": d["label"], "c_rates": d["rates"],
                               "loss_per_member_min": [_r(x, 3) for x in table[a]],
                               "expected_loss_min": _r(sum(wi * li for wi, li in zip(w, table[a])), 3)}
                              for a, d in enumerate(unique)],
                "best_decision_now": unique[pick]["label"],
                "verdict": (f"run option {best['option']}: it is expected to lower the decision's loss by "
                            f"{best['value_min'] + lab.TEST_COST_MIN:.2f} min against a test cost of "
                            f"{lab.TEST_COST_MIN:g}") if worth else
                           ("no candidate test is expected to change the decision by more than its cost: design now "
                            "or stop learning" if m > 1 else
                            "the nominal model is a single model, so it cannot value a test: only a lab test checks it"
                            if int(model["version"]) == 0 else
                            "the evidence leaves one model: no test would change the design; design now or stop"),
                "fit_adequate": model.get("adequate", True),
                "caveat": None if model.get("adequate", True) else
                "the model version fits its tests worse than the noise allows: these values assume a family that "
                "the evidence does not support",
                "rule": ("belief: members weighted exp(-delta chi^2 / 2); loss: charge time + "
                         f"{lab.VIOLATION_PENALTY_MIN:g} min when a member breaks a limit; observation: each member's "
                         "forecast taken as the truth in turn, the weights updated with the lab's noise and the fit's "
                         f"sample discount; test cost {lab.TEST_COST_MIN:g} min")}}


# ------------------------------------------------------------------ robust design

def _meets(runs: list[dict], margin: float, t_limit: float) -> bool:
    return all(r["status"] == "ok" and r["min_anode"] >= margin + 0.5 and r["max_T"] <= t_limit - 0.1
               for r in runs)


def _greedy(members, ambient: float, margin: float, t_limit: float, run) -> tuple[list[float] | None, int, list]:
    """Greedy step-down: each step the highest lattice C-rate, not above the previous step's, at which every member
    meets the anode margin and the temperature limit over the steps so far (bisection on prefix charges); then the
    full charge is checked and later steps come down until every member meets both. Not an optimum.
    Returns (C-rates or None, charges simulated, the last full charges)."""
    lattice = [round(fc.C_LO + i * fc.C_GRID, 2) for i in range(int(round((fc.C_HI - fc.C_LO) / fc.C_GRID)) + 1)]
    evaluations = 0

    def ok(rates, prefix):
        nonlocal evaluations
        runs = run([(rates + [rates[-1]] * (fc.STEPS - len(rates)), ambient, 0.0, h, k, prefix) for h, k in members])
        evaluations += len(runs)
        return _meets(runs, margin, t_limit)

    chosen: list[float] = []
    for step in range(1, fc.STEPS + 1):
        allowed = [c for c in lattice if not chosen or c <= chosen[-1] + 1e-9]
        lo, hi = -1, len(allowed)                  # lo: highest index known to meet; hi: lowest known not to
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if ok(chosen + [allowed[mid]], step):
                lo = mid
            else:
                hi = mid
        if lo < 0:
            return None, evaluations, []
        chosen.append(allowed[lo])
    while True:                                    # the full charge, with the fallback, must meet them too
        runs = run([(chosen, ambient, 0.0, h, k, None) for h, k in members])
        evaluations += len(runs)
        if _meets(runs, margin, t_limit):
            return chosen, evaluations, runs
        above = [i for i, c in enumerate(chosen) if c > fc.C_LO + 1e-9]
        if not above:
            return None, evaluations, runs
        chosen[above[-1]] = round(chosen[above[-1]] - fc.C_GRID, 2)


def _greedy_one(job: tuple) -> list[float] | None:
    h, k, ambient, margin, t_limit = job
    return _greedy([(h, k)], ambient, margin, t_limit, lambda jobs: [charge(j) for j in jobs])[0]


def designs_per_member(members, ambient: float, margin: float, t_limit: float) -> list:
    """The greedy design under each member alone, one member per process."""
    jobs = [(h, k, ambient, margin, t_limit) for h, k in members]
    _pybamm()
    try:
        ctx = mp.get_context("fork")
    except ValueError:
        return [_greedy_one(j) for j in jobs]
    with cf.ProcessPoolExecutor(min(WORKERS, len(jobs)), mp_context=ctx) as pool:
        return list(pool.map(_greedy_one, jobs))


def optimize_protocol(params: dict, model: dict) -> dict:
    import numpy as np

    members = _members(model)
    margin, t_limit, ambient = float(params["margin_mV"]), float(params["t_limit_C"]), float(params["ambient"])
    chosen, evaluations, runs = _greedy(members, ambient, margin, t_limit, charges)
    if chosen is None:
        return {"status": "ok", "error": None,
                "scalars": {"status": ("ok", ""), "design_found": ("no", ""), "evaluations": (evaluations, ""),
                            "model_version": (int(model["version"]), "")},
                "series": None, "solver": {"name": "PyBaMM", "search": "greedy step-down"},
                "details": {"reason": f"no charge from {fc.C_LO}C up meets the limits for every member"}}
    est = runs[0]
    grid = _grid(est)
    temps = np.array([_on(grid, r, "T") for r in runs])
    anodes = [r["min_anode"] for r in runs]
    scalars = {"status": ("ok", ""), "design_found": ("yes", ""),
               **{c: (chosen[i], "1/h") for i, c in enumerate(fc.C_RATES)},
               "charge_time_80": (_r(max(r["t80_s"] for r in runs) / 60, 3), "min"),
               "min_anode_potential": (_r(min(anodes), 3), "mV"),
               "max_temperature": (_r(max(r["max_T"] for r in runs), 3), "degC"),
               "anode_spread": (_r(max(anodes) - min(anodes), 3), "mV"),
               "evaluations": (evaluations, ""), "members": (len(runs), ""),
               "model_version": (int(model["version"]), "")}
    return {"status": "ok", "error": None, "scalars": scalars,
            "series": {"t": grid, "channels": {
                "cell_temperature": ("degC", [_r(x, 3) for x in temps[0]]),
                "temperature_lo": ("degC", [_r(x, 3) for x in temps.min(axis=0)]),
                "temperature_hi": ("degC", [_r(x, 3) for x in temps.max(axis=0)]),
                "anode_potential": ("mV", [_r(x, 3) for x in _on(grid, est, "phi")]),
                "current": ("A", [_r(x, 4) for x in _on(grid, est, "current")])}},
            "solver": est["solver"],
            "details": {"members": [{"h": h, "k": k, "min_anode_potential_mV": _r(r["min_anode"], 3),
                                     "max_temperature_degC": _r(r["max_T"], 3),
                                     "charge_time_80_min": _r(r["t80_s"] / 60, 3)}
                                    for (h, k), r in zip(members, runs)],
                        "limits": {"margin_mV": margin, "t_limit_C": t_limit, "rule": "anode >= margin + 0.5 mV, "
                                   "temperature <= limit - 0.1 degC, for every member"},
                        "search": "greedy step-down on the 0.05C lattice (bisection per step on prefix charges), then "
                                  "later steps lowered until the full charge meets the limits; not an optimum",
                        "fit_adequate": model.get("adequate", True),
                        "extrapolation": _extrapolation(model, {**params, **dict(zip(fc.C_RATES, chosen))})}}


# ------------------------------------------------------------------ entry

def run(action: str, params: dict, inputs: dict) -> dict:
    problems = lab.check_params(action, params)
    if problems:
        raise ValueError("; ".join(problems))
    p = lab.canonical(action, params)
    if action == "lab_charge":
        condition = inputs.get("condition")
        if not isinstance(condition, dict) or lab.commitment(condition) != p["lab"]:
            raise ValueError("the staged lab condition does not match the request's commitment")
        body = lab_charge(p, condition, inputs.get("request_id", ""))
    elif action == lab.RECORD_ACTION:
        body = record_test(p, inputs)
    elif action == "forecast_protocol":
        body = forecast(p, json.loads(p["model"]))
    elif action == "fit_thermal":
        body = fit_thermal(p, json.loads(p["base"]), lab.staged_sources(p, inputs))
    elif action == "check_model":
        body = check_model(p, json.loads(p["model"]), lab.staged_sources(p, inputs))
    elif action == "rank_measurements":
        body = rank_measurements(p, json.loads(p["model"]))
    elif action == "optimize_protocol":
        body = optimize_protocol(p, json.loads(p["model"]))
    else:
        raise ValueError(f"{action} is not run by the solver")
    series = body.get("series")
    return {"schema": lab.RESULT_SCHEMA, "action": action, "params": p, "basis": "computational",
            "status": body["status"], "error": body.get("error"),
            # a dimensionless number's unit is "1"; a category has none
            "scalars": {n: {"value": v, "unit": u or ("" if isinstance(v, str) else "1")}
                        for n, (v, u) in (body.get("scalars") or {}).items()},
            "series": None if not series else {
                "time_unit": "s", "t": [_r(x, 3) for x in series["t"]],
                "channels": {n: {"unit": u, "values": vs} for n, (u, vs) in series["channels"].items()}},
            "solver": body.get("solver") or {}, "details": body.get("details")}


def main() -> int:
    stdout, sys.stdout = sys.stdout, sys.stderr  # nothing but the JSON on stdout
    start = time.perf_counter()
    try:
        request = json.loads(sys.stdin.read())
        problems = lab.check_params(request["action"], request.get("params", {}))
        if problems:
            raise ValueError("; ".join(problems))
        result = run(request["action"], request.get("params", {}), request.get("inputs") or {})
    except ValueError as exc:
        stdout.write(json.dumps({"refused": str(exc).split("; ")}))
        return 2
    stdout.write(json.dumps({"result": result, "sha256": fc.result_hash(result),
                             "wall_s": round(time.perf_counter() - start, 2)}, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
