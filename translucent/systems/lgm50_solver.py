"""The pybamm-fastcharge solver, run in a child process (needs `uv sync --group solver`).

    python -m translucent.systems.lgm50_solver < request.json

The request is {"action": ..., "params": {...}} plus, for calibrate_thermal, "records": a
list of Maccor CSV paths (default: the fetched Chen 2020 records). Stdout is one JSON object
{"result": ..., "sha256": ..., "wall_s": ..., "solve_wall_s": ...}; the sha256 covers the
result only, so identical inputs on one platform give the same hash. Refused parameters
give {"refused": [reasons]} and exit status 2. Solver chatter goes to stderr.

Result schema (pybamm-fastcharge-result/1):
    schema, action, params (as run), status (ok | not_reached | nonsense | solver_error),
    error (str or null), basis ("computational"), validity {label, notes},
    scalars {quantity: {value, unit}},
    series {time_unit, t, channels {quantity: {unit, values}}} or null,
    solver {name, version, casadi, pybammsolvers, numpy, python, platform, parameter_set,
            model, model_options, parameter_overrides, solver, rtol, atol, mesh_points},
    details (calibrate_thermal: per-rate fit table, grid, record sha256s)
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
import sys
import time
import warnings
from importlib import metadata
from pathlib import Path
from typing import Any

from translucent.systems import lgm50_fastcharge as fc

H_GRID = (4.0, 5.0, 6.0, 7.0, 8.0, 10.0)  # includes the parameter set's default of 10
DISCHARGE_RATES = (0.5, 1.0, 1.5)


def _pybamm():
    warnings.filterwarnings("ignore")
    import pybamm

    pybamm.set_logging_level("ERROR")
    return pybamm


def _solver_info(pybamm, solver, model, overrides: dict, options: dict) -> dict:
    import numpy

    return {
        "name": "PyBaMM",
        "version": pybamm.__version__,
        "casadi": metadata.version("casadi"),
        "pybammsolvers": metadata.version("pybammsolvers"),
        "numpy": numpy.__version__,
        "python": platform.python_version(),
        "platform": f"{sys.platform}-{platform.machine()}",
        "parameter_set": fc.PARAMETER_SET,
        "model": fc.MODEL,
        "model_options": options,
        "parameter_overrides": overrides,
        "solver": type(solver).__name__,
        "rtol": solver.rtol,
        "atol": solver.atol,
        "mesh_points": {str(k): v for k, v in sorted(model.default_var_pts.items())},
    }


def _r(x: float, digits: int) -> float:
    return round(float(x), digits)


def simulate_protocol(params: dict[str, float]) -> dict[str, Any]:
    """One four-step charge from 10 to 80 %; the prototype's semantics, h = 6 W/m^2/K."""
    pybamm = _pybamm()
    import numpy as np

    rates = [params[c] for c in fc.C_RATES]
    ambient = params["ambient"]
    model = pybamm.lithium_ion.DFN(options=dict(fc.MODEL_OPTIONS))
    pv = pybamm.ParameterValues(fc.PARAMETER_SET)
    tk = 273.15 + ambient
    overrides = {
        "Ambient temperature [K]": tk,
        "Initial temperature [K]": tk,
        "Total heat transfer coefficient [W.m-2.K-1]": fc.HEAT_TRANSFER_W_M2K,
    }
    pv.update(overrides)
    edges = np.linspace(fc.SOC_START, fc.SOC_END, fc.STEPS + 1)
    target = (fc.SOC_END - fc.SOC_START) * fc.CAPACITY_AH
    steps = []
    for i, c in enumerate(rates):
        q_end = (edges[i + 1] - fc.SOC_START) * fc.CAPACITY_AH  # cumulative Ah charged
        # Discharge capacity is negative while charging; stop when the window is full.
        done = pybamm.step.CustomTermination(
            name=f"charged {q_end:.3f} A.h",
            event_function=(
                lambda q: lambda variables: variables["Discharge capacity [A.h]"] + q
            )(q_end),
        )
        steps.append(
            pybamm.step.c_rate(
                -c,
                duration=fc.TIME_CAP_S,
                termination=[done, f"{fc.V_MAX} V"],
                period=max(1.0, 3600 / c / 60 / fc.STEPS),
            )
        )
    full = pybamm.step.CustomTermination(
        name="charged target",
        event_function=lambda variables: variables["Discharge capacity [A.h]"] + target,
    )
    # Reached only when 4.2 V cut a step short; otherwise skipped (PyBaMM skip_ok).
    steps.append(
        pybamm.step.voltage(
            fc.V_MAX, duration=fc.TIME_CAP_S, termination=[full, "C/50"], period=20.0
        )
    )
    solver = pybamm.IDAKLUSolver()
    sim = pybamm.Simulation(
        model,
        parameter_values=pv,
        experiment=pybamm.Experiment(steps),
        solver=solver,
    )
    result = {
        "status": None,
        "error": None,
        "solver": _solver_info(
            pybamm, solver, model, overrides, dict(fc.MODEL_OPTIONS)
        ),
        "scalars": {},
        "series": None,
    }
    try:
        sol = sim.solve(initial_soc=fc.SOC_START)
    except Exception as exc:  # the solver's own failure is a recorded outcome
        result.update(status="solver_error", error=f"{type(exc).__name__}: {exc}"[:300])
        return result
    t = sol["Time [s]"].entries
    q = -sol["Discharge capacity [A.h]"].entries
    v = sol["Voltage [V]"].entries
    temp = sol["X-averaged cell temperature [C]"].entries
    plated = sol["Loss of capacity to negative lithium plating [A.h]"].entries
    phi = sol[
        "Negative electrode surface potential difference at separator interface [V]"
    ].entries
    current = -sol["Current [A]"].entries
    reached = q.max() >= target - 1e-3
    k = int(np.argmax(q >= target - 1e-3)) if reached else len(t) - 1
    t80 = None
    if reached:
        t80 = float(np.interp(target, q[: k + 1], t[: k + 1])) if k > 0 else float(t[k])
    cut = slice(0, k + 1)
    finite = all(np.all(np.isfinite(a[cut])) for a in (t, q, v, temp, plated, phi))
    sane = (
        finite
        and temp[cut].max() < 120
        and temp[cut].min() > ambient - 1
        and v[cut].max() < fc.V_MAX + 0.05
        and plated[cut].min() > -1e-6
    )
    status = ("ok" if reached else "not_reached") if sane else "nonsense"
    if not finite:
        result.update(status=status, error="non-finite solver output")
        return result
    # Series up to the first point at 80 %. Each step boundary emits points about 1e-13 s
    # apart (the event, then the re-initialised next step); the first of each cluster within
    # 1 ms is kept, so time is strictly increasing after rounding and the end state of the
    # finished step survives. Scalars use every solver point, so no extreme is lost.
    keep = [0]
    for i in range(1, k + 1):
        if t[i] - t[keep[-1]] >= 1e-3:
            keep.append(i)
    channels = {
        "voltage": ("V", [_r(v[i], 5) for i in keep]),
        "current": ("A", [_r(current[i], 5) for i in keep]),
        "cell_temperature": ("degC", [_r(temp[i], 3) for i in keep]),
        "anode_potential": ("mV", [_r(phi[i] * 1000, 3) for i in keep]),
        "state_of_charge": (
            "%",
            [_r(100 * (fc.SOC_START + q[i] / fc.CAPACITY_AH), 4) for i in keep],
        ),
    }
    scalars = {
        "status": (status, ""),
        "max_temperature": (_r(temp[cut].max(), 3), "degC"),
        "min_anode_potential": (_r(phi[cut].min() * 1000, 3), "mV"),
        "max_voltage": (_r(v[cut].max(), 5), "V"),
        "max_current": (_r(current[cut].max(), 5), "A"),
        "plated_lithium": (_r(plated[k], 7), "Ah"),
        "plated_lithium_peak": (_r(plated[cut].max(), 7), "Ah"),
        "cv_entered": ("yes" if bool(np.any(v[cut] >= fc.V_MAX - 1e-3)) else "no", ""),
    }
    if t80 is not None:
        scalars["charge_time_80"] = (_r(t80 / 60, 3), "min")
    result.update(
        status=status,
        error=None if sane else "failed sanity checks",
        scalars={n: {"value": val, "unit": u} for n, (val, u) in scalars.items()},
        series={
            "time_unit": "s",
            "t": [_r(t[i], 3) for i in keep],
            "channels": {
                n: {"unit": u, "values": vs} for n, (u, vs) in channels.items()
            },
        },
    )
    return result


def load_discharges(path: Path) -> dict[float, dict[str, Any]]:
    """Constant-current discharges from full in a Maccor export, keyed by C-rate.

    The current column is a magnitude; the Md column says C, D or R. A step counts when it
    is a D step at 0.5, 1 or 1.5C (within 2 %) whose preceding step ended at 4.1 V or more
    (a rest after a full charge; the first logged point is already under load).
    """
    import numpy as np

    with open(path, encoding="latin-1", newline="") as fh:
        rows = list(csv.reader(fh))
    head = next(i for i, r in enumerate(rows) if r and r[0] == "Rec")
    col = {name: i for i, name in enumerate(rows[head])}
    steps: dict[int, list[list[str]]] = {}
    for r in rows[head + 1 :]:
        if len(r) >= len(col) and r[0]:
            steps.setdefault(int(r[col["Step"]]), []).append(r)
    out, before = {}, 0.0
    for _, rs in sorted(steps.items()):
        start, before = before, float(rs[-1][col["Voltage [V]"]])
        if rs[0][col["Md"]] != "D" or len(rs) < 10:
            continue

        def g(name):
            return np.array([float(x[col[name]]) for x in rs])

        current, volt = g("Current [A]"), g("Voltage [V]")
        rate = float(np.median(current)) / fc.CAPACITY_AH
        match = [c for c in DISCHARGE_RATES if abs(rate - c) <= 0.02 * c]
        if match and start >= 4.1 and match[0] not in out:
            out[match[0]] = {
                "current": float(np.median(current)),
                "q": g("Capacity [Ah]"),
                "v": volt,
                "surface": g("Temperature Cell [degC]"),
                "chamber": g("Temperature Chamber [degC]"),
            }
    return out


def _discharge(pybamm, current: float, ambient: float, h: float):
    model = pybamm.lithium_ion.DFN(options={"thermal": "lumped"})
    pv = pybamm.ParameterValues(fc.PARAMETER_SET)
    tk = 273.15 + ambient
    pv.update(
        {
            "Ambient temperature [K]": tk,
            "Initial temperature [K]": tk,
            "Total heat transfer coefficient [W.m-2.K-1]": h,
        }
    )
    step = pybamm.step.current(
        current, duration=6 * 3600, termination="2.5 V", period=10
    )
    solver = pybamm.IDAKLUSolver()
    sim = pybamm.Simulation(
        model,
        parameter_values=pv,
        experiment=pybamm.Experiment([step]),
        solver=solver,
    )
    sol = sim.solve(initial_soc=1.0)
    return (
        sol["Discharge capacity [A.h]"].entries,
        sol["Voltage [V]"].entries,
        sol["X-averaged cell temperature [C]"].entries,
        solver,
        model,
    )


def calibrate_thermal(records: list[str | Path]) -> dict[str, Any]:
    """Fit h to the recorded surface-temperature rise of the 0.5, 1 and 1.5C discharges.

    For each h on a grid, simulate each rate once (mean recorded current, mean chamber
    temperature as ambient, from full to 2.5 V) and compare the modelled rise with the mean
    measured rise over the cells. The fit is the vertex of a parabola through the best grid
    point and its neighbours, reported with the error and voltage RMSE re-simulated there.
    Lumped model temperature is compared with the surface thermocouple.
    """
    pybamm = _pybamm()
    import numpy as np

    paths = [Path(p) for p in records]
    digests = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    cells = [load_discharges(p) for p in paths]
    rates = [c for c in DISCHARGE_RATES if all(c in cell for cell in cells)]
    if not rates:
        raise ValueError("no 0.5, 1 or 1.5C discharge from full found in every record")
    measured = {
        c: {
            "current": float(np.mean([cell[c]["current"] for cell in cells])),
            "ambient": float(
                np.mean([np.median(cell[c]["chamber"]) for cell in cells])
            ),
            "rise": [
                float(cell[c]["surface"].max() - cell[c]["surface"][0])
                for cell in cells
            ],
        }
        for c in rates
    }

    def evaluate(h):
        rows = {}
        for c in rates:
            m = measured[c]
            q, v, temp, solver, model = _discharge(
                pybamm, m["current"], m["ambient"], h
            )
            rmse = []
            for cell in cells:
                d = cell[c]
                grid = np.linspace(0.05, 0.9 * min(q.max(), d["q"].max()), 200)
                gap = np.interp(grid, d["q"], d["v"]) - np.interp(grid, q, v)
                rmse.append(float(np.sqrt(np.mean(gap**2))) * 1000)
            rows[c] = {
                "model_rise": float(temp.max() - temp[0]),
                "voltage_rmse_mV": float(np.mean(rmse)),
            }
        sse = sum(
            (float(np.mean(measured[c]["rise"])) - rows[c]["model_rise"]) ** 2
            for c in rates
        )
        return rows, sse, solver, model

    grid = {h: evaluate(h) for h in H_GRID}
    sse = [grid[h][1] for h in H_GRID]
    i = int(np.argmin(sse))
    h_fit = H_GRID[i]
    if 0 < i < len(H_GRID) - 1:
        (x0, x1, x2), (y0, y1, y2) = H_GRID[i - 1 : i + 2], sse[i - 1 : i + 2]
        den = (x0 - x1) * (x0 - x2) * (x1 - x2)
        a = (x2 * (y1 - y0) + x1 * (y0 - y2) + x0 * (y2 - y1)) / den
        b = (x2 * x2 * (y0 - y1) + x1 * x1 * (y2 - y0) + x0 * x0 * (y1 - y2)) / den
        if a > 0:
            h_fit = min(max(-b / (2 * a), x0), x2)
    h_fit = _r(h_fit, 2)
    rows, fit_sse, solver, model = grid[h_fit] if h_fit in grid else evaluate(h_fit)
    overrides = {"Total heat transfer coefficient [W.m-2.K-1]": "fitted"}
    return {
        "status": "ok",
        "error": None,
        "solver": _solver_info(pybamm, solver, model, overrides, {"thermal": "lumped"}),
        "scalars": {
            "heat_transfer_coefficient": {"value": h_fit, "unit": "W/m^2/K"},
            "thermal_fit_rmse": {
                "value": _r(math.sqrt(fit_sse / len(rates)), 3),
                "unit": "delta_degC",
            },
            "voltage_rmse": {
                "value": _r(np.mean([rows[c]["voltage_rmse_mV"] for c in rates]), 1),
                "unit": "mV",
            },
        },
        "series": None,
        "details": {
            "records_sha256": digests,
            "rates": {
                f"{c:g}C": {
                    "current_A": _r(measured[c]["current"], 4),
                    "ambient_degC": _r(measured[c]["ambient"], 3),
                    "measured_rise_K": [_r(x, 3) for x in measured[c]["rise"]],
                    "measured_rise_mean_K": _r(np.mean(measured[c]["rise"]), 3),
                    "model_rise_K": _r(rows[c]["model_rise"], 3),
                    "voltage_rmse_mV": _r(rows[c]["voltage_rmse_mV"], 1),
                }
                for c in rates
            },
            "grid": {
                f"{h:g}": {
                    "sse_K2": _r(grid[h][1], 4),
                    "model_rise_K": {
                        f"{c:g}C": _r(grid[h][0][c]["model_rise"], 3) for c in rates
                    },
                }
                for h in H_GRID
            },
            "method": (
                "lumped DFN (no plating), each rate from full to 2.5 V at the mean recorded "
                "current and chamber temperature; least squares on the mean surface rise; "
                "parabolic vertex around the best grid point"
            ),
        },
    }


def run(action: str, params: dict[str, Any], records: list | None = None) -> dict:
    """Run one action and return its result dict; ValueError lists refused parameters."""
    problems = fc.check_params(action, params)
    if not problems and fc.SPEC.action(action).executor != "computational":
        problems = [f"{action} is run by a person, not by the solver"]
    if problems:
        raise ValueError("; ".join(problems))
    p = fc.canonical(action, params)
    label = fc.validity(action, p)
    if action == "simulate_protocol":
        body = simulate_protocol(p)
        if body["scalars"]:
            body["scalars"]["validity"] = {"value": label["label"], "unit": ""}
    else:
        body = calibrate_thermal(records or fc.default_records())
    return {
        "schema": fc.RESULT_SCHEMA,
        "action": action,
        "params": p,
        "basis": "computational",
        "validity": label,
        "details": None,
        **body,
    }


def main() -> int:
    stdout, sys.stdout = sys.stdout, sys.stderr  # nothing but the JSON on stdout
    start = time.perf_counter()
    try:
        request = json.loads(sys.stdin.read())
        problems = fc.check_params(request["action"], request.get("params", {}))
        if problems:  # refuse before paying for the solver import
            raise ValueError("; ".join(problems))
        _pybamm()  # import time counts in wall_s, not in solve_wall_s
        solve_start = time.perf_counter()
        result = run(
            request["action"], request.get("params", {}), request.get("records")
        )
    except ValueError as exc:
        stdout.write(json.dumps({"refused": str(exc).split("; ")}))
        return 2
    end = time.perf_counter()
    stdout.write(
        json.dumps(
            {
                "result": result,
                "sha256": fc.result_hash(result),
                "wall_s": round(end - start, 2),
                "solve_wall_s": round(end - solve_start, 2),
            },
            sort_keys=True,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
