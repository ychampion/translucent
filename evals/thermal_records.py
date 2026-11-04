"""Held-out study: learning the lumped thermal model of the LG M50 cell from the Chen 2020 discharge records.

    python -m evals.thermal_records --out evals/results/thermal-records.json [--records <dir>] [--workers 8]
    python -m evals.thermal_records --tables evals/results/thermal-records.json

Numerical only: PyBaMM solves (the `solver` group), no model calls, no network. Deterministic on one platform: the
grid, the splits and every rule below are fixed here, before any fit was computed.

Frozen study (written before the first fit)
-------------------------------------------
Data. The three Chen 2020 records (cells 02, 03, 04), each with a constant-current discharge from full at 0.5, 1 and
1.5C (`lgm50_solver.load_discharges`): nine trajectories, keyed `cell02@0.5C` and so on. Measured quantity: the
surface thermocouple, as its rise above the first logged point of the discharge.

Model. DFN, lumped thermal, OKane2022 (the solver's `_discharge`): the trajectory's median recorded current from
full (initial SOC 1) to 2.5 V, ambient and initial temperature both the median chamber temperature of that
discharge, output every 10 s. Two thermal parameters: the total heat transfer coefficient h (W/m^2/K) and the
thermal-mass scale k, which multiplies all five specific heat capacities (negative and positive electrode,
separator, negative and positive current collector). The model temperature is the lumped cell temperature, as its
rise above its start; it is compared with the surface thermocouple.

Prior: the parameter set as shipped, h = 10, k = 1.
Families: F1, h free with k = 1; F2, h and k free.
Grid: h in H_GRID (1-50), k in K_GRID (0.5-2.5); every trajectory is solved at every grid point once.

Fit target. For each trajectory, 200 points evenly spaced in discharged capacity on [0, Q], Q the smaller of the
measured and modelled discharged capacity at the end of the discharge. Capacity, not time: heat generation follows
the state of charge; under constant current at the recorded median current, capacity and time are proportional in
the model and nearly so in the record, and aligning on capacity compares like states of charge without a time shift
when the model's usable capacity differs from the cell's. Residual: model rise minus measured rise (K) at those points.
Objective: weighted least squares, J = mean over the fitted trajectories of each trajectory's mean squared residual
(each trajectory weighs the same, whatever its rate or sampling). RMSE = sqrt(J).

Estimate. F1: the best grid point on the k = 1 row, refined by the vertex of a parabola in log h through it and its
neighbours (clamped to them). F2: the best grid point, refined by the vertex of a quadratic surface in (log h, k)
fitted to its 3 x 3 neighbourhood when that surface is convex (clamped to the neighbourhood); else the grid point.
Estimates are rounded (h to 0.01, k to 0.001) and re-solved with the full DFN; every reported number at an estimate
comes from that re-solve, not from interpolation.

Uncertainty. A 95 % profile interval over the grid: the profile of a parameter is J minimised over the other free
parameter on the grid (F1: the k = 1 row itself), with the re-solved estimate added as a point. The interval is the
contiguous set around the minimum where N_eff * (profile / J_min - 1) <= 3.841 (chi^2, 1 dof), crossings by linear
interpolation (in log h for h). N_eff corrects for the residuals' serial correlation: the sum over fitted
trajectories of n (1 - rho) / (1 + rho), each clipped to [1, n], rho the lag-1 autocorrelation of that trajectory's
residual at the estimate. An end that reaches the grid's edge is reported open.
Identifiable: a parameter is identifiable from the fitted data when both ends of its interval close inside the grid.

Splits (whole trajectories held out; no split inside a trajectory, so no neighbouring timestamps):
  A-cell02, A-cell03, A-cell04: leave one cell out; fit on the other two cells at all three rates, predict the held
      cell at all three rates.
  B-rate1.5: withheld rate; fit on 0.5 and 1C (all cells), predict 1.5C (all cells).
In-sample context (not held out): F1 and F2 fitted on all nine trajectories.

Metrics, per split, for prior, F1 and F2:
  heldout_rmse_K: sqrt of the mean over held-out trajectories of the mean squared residual;
  heldout_max_rise_error_K: per held-out trajectory, modelled max rise minus measured max rise (signed), and the
      mean absolute value;
  fitted parameters with their intervals and the identifiable flags; train_rmse_K;
  heldout_voltage_rmse_mV (context): terminal voltage RMSE on the same capacity points, mean over held-out
      trajectories.

Scope. Surface temperature agreement says nothing about the core temperature, the anode potential or lithium
plating: none of them is measured in these records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from importlib import metadata
from pathlib import Path

import numpy as np

from translucent.systems import lgm50_fastcharge as fc
from translucent.systems import lgm50_solver as ls

SCHEMA = "thermal-records/1"
CELLS = ("02", "03", "04")
RATES = (0.5, 1.0, 1.5)
PRIOR = {"h": 10.0, "k": 1.0}
H_GRID = (1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 12.0, 14.0, 17.0, 20.0, 25.0, 30.0, 40.0, 50.0)
K_GRID = (0.5, 0.625, 0.75, 0.875, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5)
POINTS = 200
CHI2_95_1DOF = 3.841
PERIOD_S = 10.0
HEAT_CAPACITIES = (
    "Negative electrode specific heat capacity [J.kg-1.K-1]",
    "Positive electrode specific heat capacity [J.kg-1.K-1]",
    "Separator specific heat capacity [J.kg-1.K-1]",
    "Negative current collector specific heat capacity [J.kg-1.K-1]",
    "Positive current collector specific heat capacity [J.kg-1.K-1]",
)
FAMILIES = ("prior", "F1", "F2")


def key(cell: str, rate: float) -> str:
    return f"cell{cell}@{rate:g}C"


ALL = tuple(key(c, r) for c in CELLS for r in RATES)


def splits() -> dict[str, dict[str, list[str]]]:
    """The frozen splits: name -> {train, test} (trajectory keys, in ALL order)."""
    out = {}
    for held in CELLS:
        out[f"A-cell{held}"] = {
            "train": [key(c, r) for c in CELLS if c != held for r in RATES],
            "test": [key(held, r) for r in RATES],
        }
    out["B-rate1.5"] = {
        "train": [key(c, r) for c in CELLS for r in RATES if r != 1.5],
        "test": [key(c, 1.5) for c in CELLS],
    }
    return out


# ------------------------------------------------------------------ records


def load_records(paths: list[Path]) -> dict[str, dict]:
    """The nine measured trajectories, keyed by ALL; each with current, ambient, q, v and the surface rise."""
    by_cell = {}
    for p in paths:
        cell = next((c for c in CELLS if f"cell{c}" in p.name), None)
        if cell is None:
            raise ValueError(f"{p.name}: not one of cells {CELLS}")
        by_cell[cell] = ls.load_discharges(p)
    out = {}
    for c in CELLS:
        for r in RATES:
            d = by_cell.get(c, {}).get(r)
            if d is None:
                raise ValueError(f"no {r:g}C discharge from full in the cell{c} record")
            out[key(c, r)] = {
                "current": float(d["current"]),
                "ambient": float(np.median(d["chamber"])),
                "q": np.asarray(d["q"], float),
                "v": np.asarray(d["v"], float),
                "rise": np.asarray(d["surface"], float) - float(d["surface"][0]),
            }
    return out


# ------------------------------------------------------------------ comparison (pure numpy)


def lag1(r: np.ndarray) -> float:
    den = float(np.dot(r, r))
    return float(np.dot(r[1:], r[:-1]) / den) if den > 0 else 0.0


def compare(meas: dict, q: np.ndarray, v: np.ndarray, rise: np.ndarray) -> dict:
    """One trajectory: model (q, v, rise) against the measured one on the frozen capacity points."""
    top = min(float(meas["q"][-1]), float(q[-1]))
    grid = np.linspace(0.0, top, POINTS)
    r = np.interp(grid, q, rise) - np.interp(grid, meas["q"], meas["rise"])
    dv = np.interp(grid, q, v) - np.interp(grid, meas["q"], meas["v"])
    return {
        "mse_K2": float(np.mean(r**2)),
        "rmse_K": float(math.sqrt(np.mean(r**2))),
        "max_rise_error_K": float(rise.max() - meas["rise"].max()),
        "model_max_rise_K": float(rise.max()),
        "measured_max_rise_K": float(meas["rise"].max()),
        "voltage_rmse_mV": float(math.sqrt(np.mean(dv**2)) * 1000),
        "rho": lag1(r),
        "capacity_compared_Ah": top,
        "model_capacity_Ah": float(q[-1]),
    }


def objective(rows: dict[str, dict], keys: list[str]) -> float:
    return float(np.mean([rows[k]["mse_K2"] for k in keys]))


def n_eff(rhos: list[float], n: int = POINTS) -> float:
    total = 0.0
    for rho in rhos:
        rho = min(max(rho, -0.99), 0.999999)
        total += min(max(n * (1 - rho) / (1 + rho), 1.0), float(n))
    return total


def vertex_1d(xs: list[float], ys: list[float], i: int) -> float:
    """Vertex of the parabola through point i and its neighbours, clamped to them; xs[i] at an edge or if concave."""
    if not 0 < i < len(xs) - 1:
        return xs[i]
    (x0, x1, x2), (y0, y1, y2) = xs[i - 1 : i + 2], ys[i - 1 : i + 2]
    den = (x0 - x1) * (x0 - x2) * (x1 - x2)
    a = (x2 * (y1 - y0) + x1 * (y0 - y2) + x0 * (y2 - y1)) / den
    b = (x2 * x2 * (y0 - y1) + x1 * x1 * (y2 - y0) + x0 * x0 * (y1 - y2)) / den
    if a <= 0:
        return xs[i]
    return min(max(-b / (2 * a), x0), x2)


def vertex_2d(xs: list[float], ys: list[float], z: np.ndarray, i: int, j: int) -> tuple[float, float]:
    """Vertex of a quadratic surface fitted to the 3 x 3 neighbourhood of (i, j), clamped to it; the grid point if
    the surface is not convex or the neighbourhood has fewer than six finite points."""
    ii = range(max(i - 1, 0), min(i + 2, len(xs)))
    jj = range(max(j - 1, 0), min(j + 2, len(ys)))
    pts = [(xs[a], ys[b], z[a, b]) for a in ii for b in jj if np.isfinite(z[a, b])]
    if len(pts) < 6 or len(ii) < 2 or len(jj) < 2:
        return xs[i], ys[j]
    x0, y0 = xs[i], ys[j]
    A = np.array([[1, x - x0, y - y0, (x - x0) ** 2, (x - x0) * (y - y0), (y - y0) ** 2] for x, y, _ in pts])
    c = np.linalg.lstsq(A, np.array([p[2] for p in pts]), rcond=None)[0]
    H = np.array([[2 * c[3], c[4]], [c[4], 2 * c[5]]])
    eig = np.linalg.eigvalsh(H)
    if eig.min() <= 1e-9 * max(1.0, float(np.abs(c).max())):  # flat or saddle: no vertex
        return x0, y0
    dx, dy = np.linalg.solve(H, -c[1:3])
    x = min(max(x0 + dx, xs[min(ii)]), xs[max(ii)])
    y = min(max(y0 + dy, ys[min(jj)]), ys[max(jj)])
    return float(x), float(y)


def profile_interval(xs: list[float], prof: list[float], j_min: float, neff: float) -> dict:
    """The contiguous region around the profile's minimum where neff * (prof / j_min - 1) <= chi^2_95(1 dof).

    xs must be increasing (transformed coordinates allowed); ends by linear interpolation; an end at the first or
    last point is open."""
    thr = j_min * (1 + CHI2_95_1DOF / neff)
    xs, prof = list(xs), [p if np.isfinite(p) else math.inf for p in prof]
    m = int(np.argmin(prof))
    lo, hi = m, m
    while lo > 0 and prof[lo - 1] <= thr:
        lo -= 1
    while hi < len(xs) - 1 and prof[hi + 1] <= thr:
        hi += 1

    def cross(a, b):  # a inside, b outside
        if not math.isfinite(prof[b]):
            return xs[a]
        return xs[a] + (thr - prof[a]) * (xs[b] - xs[a]) / (prof[b] - prof[a])

    return {
        "lo": xs[0] if lo == 0 else cross(lo, lo - 1),
        "hi": xs[-1] if hi == len(xs) - 1 else cross(hi, hi + 1),
        "lo_open": lo == 0,
        "hi_open": hi == len(xs) - 1,
        "threshold_K2": thr,
    }


# ------------------------------------------------------------------ solver (child processes)

_MEASURED: dict[str, dict] = {}


def _init(paths: list[str]) -> None:
    _MEASURED.update(load_records([Path(p) for p in paths]))


def discharge(pybamm, current: float, ambient: float, h: float, k: float):
    """`lgm50_solver._discharge` with every specific heat capacity scaled by k; returns (q, v, temperature degC)."""
    model = pybamm.lithium_ion.DFN(options={"thermal": "lumped"})
    pv = pybamm.ParameterValues(fc.PARAMETER_SET)
    tk = 273.15 + ambient
    pv.update(
        {
            "Ambient temperature [K]": tk,
            "Initial temperature [K]": tk,
            "Total heat transfer coefficient [W.m-2.K-1]": h,
            **{name: pv[name] * k for name in HEAT_CAPACITIES},
        }
    )
    step = pybamm.step.current(current, duration=6 * 3600, termination="2.5 V", period=PERIOD_S)
    sim = pybamm.Simulation(
        model, parameter_values=pv, experiment=pybamm.Experiment([step]), solver=pybamm.IDAKLUSolver()
    )
    sol = sim.solve(initial_soc=1.0)
    return (
        sol["Discharge capacity [A.h]"].entries,
        sol["Voltage [V]"].entries,
        sol["X-averaged cell temperature [C]"].entries,
    )


def solve_job(job: tuple[str, float, float]) -> tuple[tuple[str, float, float], dict]:
    name, h, k = job
    meas = _MEASURED[name]
    pybamm = ls._pybamm()
    try:
        q, v, temp = discharge(pybamm, meas["current"], meas["ambient"], h, k)
    except Exception as exc:  # a solver failure is a recorded outcome, scored as missing
        return job, {"status": "solver_error", "error": f"{type(exc).__name__}: {exc}"[:200], "mse_K2": math.inf}
    if not (np.all(np.isfinite(temp)) and len(q) > 2):
        return job, {"status": "nonsense", "error": "non-finite or empty solution", "mse_K2": math.inf}
    return job, {"status": "ok", **compare(meas, np.asarray(q), np.asarray(v), np.asarray(temp) - temp[0])}


def solve_all(jobs: list[tuple[str, float, float]], paths: list[Path], workers: int, log) -> dict:
    jobs = sorted(set(jobs))
    out, started = {}, time.perf_counter()
    with ProcessPoolExecutor(workers, initializer=_init, initargs=([str(p) for p in paths],)) as pool:
        for n, (job, row) in enumerate(pool.map(solve_job, jobs, chunksize=1), 1):
            out[job] = row
            if n % 50 == 0 or n == len(jobs):
                log(f"{n}/{len(jobs)} solves, {time.perf_counter() - started:.0f} s")
    return out


# ------------------------------------------------------------------ fitting (pure, on the solved grid)


def grid_estimate(table: dict, train: list[str], family: str, h_grid=H_GRID, k_grid=K_GRID) -> dict:
    """Grid stage of a fit: the estimate (rounded) and the J surface over the family's free parameters."""
    lh = [math.log(h) for h in h_grid]
    if family == "F1":
        J = [objective({n: table[(n, h, 1.0)] for n in train}, train) for h in h_grid]
        i = int(np.argmin(J))
        h = math.exp(vertex_1d(lh, J, i))
        return {"h": round(h, 2), "k": 1.0, "J": J}
    Z = np.array([[objective({n: table[(n, h, k)] for n in train}, train) for k in k_grid] for h in h_grid])
    i, j = np.unravel_index(int(np.argmin(Z)), Z.shape)
    x, y = vertex_2d(lh, list(k_grid), Z, int(i), int(j))
    return {"h": round(math.exp(x), 2), "k": round(y, 3), "Z": Z}


def intervals(est: dict, family: str, j_fit: float, neff: float, h_grid=H_GRID, k_grid=K_GRID) -> dict:
    """Profile intervals; the re-solved estimate (J = j_fit) is added to each profile as a point."""
    j_min = min(j_fit, *(est["J"] if family == "F1" else est["Z"].ravel()))

    def one(xs, prof, x_est, log):
        pts = dict(zip(xs, prof))
        pts[x_est] = min(j_fit, pts.get(x_est, math.inf))
        pts = sorted(pts.items())
        tx = [math.log(x) if log else x for x, _ in pts]
        iv = profile_interval(tx, [p for _, p in pts], j_min, neff)
        back = math.exp if log else (lambda t: t)
        return {
            "lo": round(back(iv["lo"]), 4), "hi": round(back(iv["hi"]), 4),
            "lo_open": iv["lo_open"], "hi_open": iv["hi_open"],
        }

    if family == "F1":
        return {"h": one(h_grid, est["J"], est["h"], True)}
    Z = est["Z"]
    return {
        "h": one(h_grid, list(Z.min(axis=1)), est["h"], True),
        "k": one(k_grid, list(Z.min(axis=0)), est["k"], False),
    }


def summarise(rows: dict[str, dict], train: list[str], test: list[str]) -> dict:
    ok = all(rows[n].get("status") == "ok" for n in train + test)
    if not ok:
        return {"status": "solver_error",
                "errors": {n: rows[n].get("error") for n in train + test if rows[n].get("status") != "ok"}}
    held = {
        n: {f: round(rows[n][f], 4) for f in ("rmse_K", "max_rise_error_K", "model_max_rise_K",
                                              "measured_max_rise_K", "voltage_rmse_mV")}
        for n in test
    }
    return {
        "status": "ok",
        "train_rmse_K": round(math.sqrt(objective(rows, train)), 4),
        "heldout_rmse_K": round(math.sqrt(objective(rows, test)), 4) if test else None,
        "heldout_mean_abs_max_rise_error_K": (
            round(float(np.mean([abs(rows[n]["max_rise_error_K"]) for n in test])), 4) if test else None
        ),
        "heldout_voltage_rmse_mV": (
            round(float(np.mean([rows[n]["voltage_rmse_mV"] for n in test])), 2) if test else None
        ),
        "heldout": held,
    }


# ------------------------------------------------------------------ the study


def _versions() -> dict:
    def v(name):
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return None

    return {
        "name": "PyBaMM", "version": v("pybamm"), "casadi": v("casadi"), "pybammsolvers": v("pybammsolvers"),
        "numpy": np.__version__, "python": platform.python_version(),
        "platform": f"{sys.platform}-{platform.machine()}", "parameter_set": fc.PARAMETER_SET,
        "model": "DFN", "model_options": {"thermal": "lumped"}, "solver": "IDAKLUSolver", "period_s": PERIOD_S,
    }


def run_study(paths: list[Path], workers: int = 8, log=print, h_grid=H_GRID, k_grid=K_GRID) -> dict:
    if 1.0 not in k_grid:
        raise ValueError("the k grid must contain 1 (family F1)")
    measured = load_records(paths)
    grid_jobs = [(n, h, k) for n in ALL for h in h_grid for k in k_grid]
    prior = (PRIOR["h"], PRIOR["k"])
    if prior[0] not in h_grid or prior[1] not in k_grid:
        grid_jobs += [(n, *prior) for n in ALL]
    table = solve_all(grid_jobs, paths, workers, log)

    fits = {name: dict(s) for name, s in splits().items()}
    fits["in-sample"] = {"train": list(ALL), "test": []}
    for s in fits.values():
        s["est"] = {f: grid_estimate(table, s["train"], f, h_grid, k_grid) for f in ("F1", "F2")}
    extra = {(n, e["h"], e["k"]) for s in fits.values() for e in s["est"].values() for n in ALL}
    table.update(solve_all([j for j in extra if j not in table], paths, workers, log))

    out_splits = {}
    for name, s in fits.items():
        entry = {"train": s["train"], "test": s["test"], "models": {}}
        for fam in FAMILIES:
            h, k = prior if fam == "prior" else (s["est"][fam]["h"], s["est"][fam]["k"])
            rows = {n: table[(n, h, k)] for n in ALL}
            m = {"params": {"h": h, "k": k}, **summarise(rows, s["train"], s["test"])}
            if fam != "prior" and m["status"] == "ok":
                neff = n_eff([rows[n]["rho"] for n in s["train"]])
                iv = intervals(s["est"][fam], fam, objective(rows, s["train"]), neff, h_grid, k_grid)
                m["n_eff"] = round(neff, 2)
                m["intervals_95"] = iv
                m["identifiable"] = {p: not (b["lo_open"] or b["hi_open"]) for p, b in iv.items()}
            entry["models"][fam] = m
        out_splits[name] = entry

    def compact(row):
        if row.get("status") != "ok":
            return [row.get("status")]
        return [round(row["rmse_K"], 4), round(row["max_rise_error_K"], 4), round(row["voltage_rmse_mV"], 2)]

    return {
        "schema": SCHEMA,
        "study": {
            "prior": PRIOR,
            "families": {"F1": "h free, k = 1", "F2": "h and k free; k scales all five specific heat capacities"},
            "h_grid": list(h_grid),
            "k_grid": list(k_grid),
            "points_per_trajectory": POINTS,
            "fit_target": "surface temperature rise above its start vs lumped model rise, on discharged capacity",
            "objective": "mean over fitted trajectories of the mean squared residual (each trajectory weighs the same)",
            "interval": "95 % profile over the grid, N_eff * (J / J_min - 1) <= 3.841, N_eff from lag-1 autocorrelation",
            "identifiable": "both ends of the 95 % interval inside the grid",
            "splits": splits(),
        },
        "records_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        "trajectories": {
            n: {
                "current_A": round(m["current"], 4),
                "ambient_degC": round(m["ambient"], 3),
                "measured_max_rise_K": round(float(m["rise"].max()), 3),
                "measured_capacity_Ah": round(float(m["q"][-1]), 4),
            }
            for n, m in measured.items()
        },
        "solver": _versions(),
        "grid": {
            "columns": ["h", "k", "rmse_K", "max_rise_error_K", "voltage_rmse_mV"],
            "rows": {n: [[h, k, *compact(table[(n, h, k)])] for h in h_grid for k in k_grid] for n in ALL},
        },
        "splits": out_splits,
    }


# ------------------------------------------------------------------ report tables


def _iv(m: dict, p: str) -> str:
    b = m.get("intervals_95", {}).get(p)
    if b is None:
        return "fixed"
    lo = f"<={b['lo']:g}" if b["lo_open"] else f"{b['lo']:g}"
    hi = f">={b['hi']:g}" if b["hi_open"] else f"{b['hi']:g}"
    return f"[{lo}, {hi}]"


def tables(res: dict) -> str:
    lines = [
        "| split | model | h | h 95 % | k | k 95 % | train RMSE (K) | held-out RMSE (K) | held-out mean abs "
        "max-rise error (K) | held-out voltage RMSE (mV) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, s in res["splits"].items():
        for fam, m in s["models"].items():
            p = m["params"]
            ho = m.get("heldout_rmse_K")
            lines.append(
                f"| {name} | {fam} | {p['h']:g} | {_iv(m, 'h')} | {p['k']:g} | {_iv(m, 'k')} | "
                f"{m.get('train_rmse_K', '-')} | {'-' if ho is None else ho} | "
                f"{m.get('heldout_mean_abs_max_rise_error_K') or '-'} | {m.get('heldout_voltage_rmse_mV') or '-'} |"
            )
    lines += ["", "| split | held-out trajectory | measured max rise (K) | prior error (K) | F1 error (K) | "
              "F2 error (K) | prior RMSE (K) | F1 RMSE (K) | F2 RMSE (K) |", "|---|---|---|---|---|---|---|---|---|"]
    for name, s in res["splits"].items():
        for n in s["test"]:
            ms = [s["models"][f]["heldout"][n] for f in FAMILIES]
            lines.append(
                f"| {name} | {n} | {ms[0]['measured_max_rise_K']:.2f} | "
                + " | ".join(f"{m['max_rise_error_K']:+.2f}" for m in ms) + " | "
                + " | ".join(f"{m['rmse_K']:.2f}" for m in ms) + " |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.thermal_records", description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, help="write the results JSON here (runs the study)")
    ap.add_argument("--records", type=Path, help="folder with the three Chen 2020 CSVs (default: the fetched ones)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--tables", type=Path, help="print the report tables from a results JSON")
    a = ap.parse_args(argv)
    if a.tables:
        print(tables(json.loads(a.tables.read_text())))
        return 0
    if not a.out:
        ap.error("--out or --tables is required")
    paths = [a.records / n for n in fc.RECORDS] if a.records else fc.default_records()
    log = lambda msg: print(msg, file=sys.stderr, flush=True)  # noqa: E731
    res = run_study(paths, a.workers, log)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1, sort_keys=False, allow_nan=False) + "\n")
    log(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
