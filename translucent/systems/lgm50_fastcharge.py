"""pybamm-fastcharge: computational fast-charge protocol design for the LG M50 cell.

Every result from this system is computational: a PyBaMM model run, not a measurement.

The model. PyBaMM 26.10.1.1 (pinned in the `solver` dependency group), Doyle-Fuller-Newman
with partially reversible lithium plating and a lumped (single-temperature) thermal model,
parameter set OKane2022 (Chen 2020 LG M50 electrochemistry plus O'Kane 2022 plating), IDAKLU
solver at its default tolerances, default mesh. The heat-transfer coefficient is 6 W/m^2/K,
calibrated on the Chen 2020 discharge records (the parameter set's 10 W/m^2/K under-predicts
the measured temperature rise by 25-40 %); `calibrate_thermal` reproduces that fit.

The action. One charge of a 5.0 Ah cell from 10 % to 80 % state of charge (coulomb counted
against the nominal capacity) in four constant-current steps over equal windows (10-27.5,
27.5-45, 45-62.5, 62.5-80 %). If the voltage reaches 4.2 V, the remaining higher-current
steps are skipped and the charge finishes at 4.2 V constant voltage; the charge gives up
after 4 h. It starts from rest with the cell at the ambient temperature.

What it reports. Time from 10 to 80 % (minutes); the lowest anode potential against
lithium at the separator (mV), the plating-onset criterion; the peak cell temperature
(degC); the plated-lithium amount, which is indicative only (the plating constants were
never fitted to data, and in this model the amount even falls as the current rises).

Validity, in plain words.
- Step currents must be 0.2-1.5C on a 0.05C grid. 1.5C is the largest current in the
  validation records, and those are discharges: no record holds a charge faster than 0.3C,
  so every charge above 0.3C is a model prediction the records do not check.
- Ambient 5-40 degC runs; only 23-27 degC is within range (the parameters were fitted near
  25 degC and the records were taken at about 26.7 degC). Anything else is labelled
  extrapolation: the temperature dependence is borrowed from other cells.
- The model voltage sits 70-160 mV above the records during 0.5-1.5C discharges, a gap no
  simple resistance explains. If part of it is in the anode, the anode potential is
  optimistic; that is why the plating constraint keeps a margin.
- The temperature is the lumped cell temperature, compared with a surface thermocouple; a
  21700 core runs hotter than its surface. The anode is graphite-SiOx modelled as graphite.
- Above 0.7C a protocol exceeds the manufacturer's charge rating. Computational runs may go
  there; a physical test request says so and needs the engineer's approval.

Solver numerics (mesh doubled, tolerance tightened) move the outputs by at most 0.01 min,
0.2 mV and 0.06 K; the declared bounds are 0.05 min, 0.5 mV and 0.1 K.

The solver never runs in this process: `execute` starts `python -m
translucent.systems.lgm50_solver` as a child process, and this module imports no solver
library, so the API and MCP processes stay light.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

from translucent.systems.base import (
    ActionSpec,
    Cost,
    ExecutionOutput,
    NotExecutable,
    Parameter,
    Quantity,
    ResultTable,
    SystemSpec,
    check_params as _check_params,
    file_digest,
)

SYSTEM_ID = "pybamm-fastcharge"
PYBAMM_VERSION = "26.10.1.1"
PARAMETER_SET = "OKane2022"
MODEL = "DFN"
MODEL_OPTIONS = {"lithium plating": "partially reversible", "thermal": "lumped"}
HEAT_TRANSFER_W_M2K = (
    6.0  # calibrated on the Chen 2020 discharges; parameter set says 10
)
CAPACITY_AH = 5.0  # nominal; the record cells measure 4.92-4.93 Ah
SOC_START, SOC_END = 0.10, 0.80
V_MAX = 4.2
TIME_CAP_S = 4 * 3600
STEPS = 4
C_LO, C_HI, C_GRID = 0.2, 1.5, 0.05
AMBIENT_LO, AMBIENT_HI = (
    5.0,
    40.0,
)  # robustness-tested range (60 random protocols, 0 failures)
AMBIENT_IN_RANGE = (23.0, 27.0)  # fitted near 25 degC, records at about 26.7 degC
MANUFACTURER_MAX_C = 0.7
PLATING_MARGIN_MV = 10.0  # case A and C; case B raises it to 25
T_LIMIT_C = 45.0
DATA = Path(__file__).resolve().parents[2] / "data" / "raw" / "chen2020-lgm50"
RECORDS = ("LGM50_cell02.csv", "LGM50_cell03.csv", "LGM50_cell04.csv")
RESULT_SCHEMA = "pybamm-fastcharge-result/1"

C_RATES = tuple(f"c_rate_{i}" for i in range(1, STEPS + 1))
WINDOWS = tuple(
    f"{100 * (SOC_START + i * (SOC_END - SOC_START) / STEPS):g}-"
    f"{100 * (SOC_START + (i + 1) * (SOC_END - SOC_START) / STEPS):g} %"
    for i in range(STEPS)
)


def _protocol_parameters(tolerance_c: float = 1e-6, tolerance_t: float = 0.0):
    return tuple(
        Parameter(
            name,
            "1/h",
            lo=C_LO,
            hi=C_HI,
            tolerance=tolerance_c,
            label=f"Charge C-rate, {window} state of charge",
        )
        for name, window in zip(C_RATES, WINDOWS)
    ) + (
        Parameter(
            "ambient",
            "degC",
            lo=AMBIENT_LO,
            hi=AMBIENT_HI,
            tolerance=tolerance_t,
            label="Ambient temperature (cell starts at ambient)",
        ),
    )


SPEC = SystemSpec(
    id=SYSTEM_ID,
    version="1",
    title="LG M50 fast-charge protocol, PyBaMM DFN with plating and lumped thermal",
    description=(
        "Four-step constant-current charge of an LG INR21700 M50 (5.0 Ah) from 10 to 80 % "
        "state of charge, simulated. Minimise the charge time while the anode potential "
        "stays above a plating margin and the cell stays at or below 45 degC. Results are "
        "computational."
    ),
    quantities=(
        Quantity(
            "charge_time_80", "min", label="Time from 10 to 80 % SOC", tolerance=0.05
        ),
        Quantity(
            "min_anode_potential",
            "mV",
            label="Lowest anode potential vs Li/Li+ at the separator (plating onset below 0)",
            tolerance=0.5,
        ),
        Quantity(
            "max_temperature",
            "degC",
            label="Peak lumped cell temperature",
            tolerance=0.1,
        ),
        Quantity(
            "plated_lithium",
            "Ah",
            label="Capacity lost to plated lithium at 80 % (indicative, unvalidated)",
        ),
        Quantity(
            "plated_lithium_peak",
            "Ah",
            label="Peak plated lithium during the charge (indicative, unvalidated)",
        ),
        Quantity("max_voltage", "V", label="Peak terminal voltage"),
        Quantity("max_current", "A", label="Peak charge current"),
        Quantity(
            "cv_entered",
            "",
            kind="category",
            categories=("yes", "no"),
            label="Reached 4.2 V and finished at constant voltage",
        ),
        Quantity(
            "status",
            "",
            kind="category",
            categories=("ok", "not_reached", "nonsense"),
            label="ok, not_reached (no 80 % within 4 h) or nonsense (failed sanity checks)",
        ),
        Quantity(
            "validity",
            "",
            kind="category",
            categories=("within_range", "extrapolation"),
            label="Whether the inputs lie inside the model's stated range",
        ),
        Quantity("voltage", "V", kind="series", label="Terminal voltage"),
        Quantity(
            "current", "A", kind="series", label="Charge current (positive charging)"
        ),
        Quantity(
            "cell_temperature", "degC", kind="series", label="Lumped cell temperature"
        ),
        Quantity(
            "anode_potential",
            "mV",
            kind="series",
            label="Anode potential vs Li/Li+ at the separator",
        ),
        Quantity("state_of_charge", "%", kind="series", label="State of charge"),
        Quantity(
            "heat_transfer_coefficient",
            "W/m^2/K",
            label="Fitted lumped heat-transfer coefficient",
        ),
        Quantity(
            "thermal_fit_rmse",
            "delta_degC",
            label="RMS error of the modelled temperature rise at the fit",
        ),
        Quantity(
            "voltage_rmse",
            "mV",
            label="Mean voltage RMS error over the fitted discharges (0-90 % capacity)",
        ),
        Quantity(
            "surface_temperature",
            "degC",
            kind="series",
            label="Cell surface thermocouple",
        ),
        Quantity(
            "max_surface_temperature", "degC", label="Peak measured surface temperature"
        ),
        Quantity(
            "relaxation_plateau",
            "",
            kind="category",
            categories=("observed", "not_observed", "unclear"),
            label="Stripping plateau in the post-charge rest voltage (sign of plating)",
        ),
    ),
    actions=(
        ActionSpec(
            name="simulate_protocol",
            label="Simulate a four-step fast charge from 10 to 80 % SOC",
            executor="computational",
            parameters=_protocol_parameters(),
            produces=(
                "status",
                "validity",
                "charge_time_80",
                "min_anode_potential",
                "max_temperature",
                "plated_lithium",
                "plated_lithium_peak",
                "max_voltage",
                "max_current",
                "cv_entered",
                "voltage",
                "current",
                "cell_temperature",
                "anode_potential",
                "state_of_charge",
            ),
            deterministic=True,
            destructive=False,
            max_wall_s=120.0,
        ),
        ActionSpec(
            name="calibrate_thermal",
            label=(
                "Fit the heat-transfer coefficient to the recorded 0.5, 1 and 1.5C "
                "discharges (cells 02-04)"
            ),
            executor="computational",
            parameters=(),
            produces=("heat_transfer_coefficient", "thermal_fit_rmse", "voltage_rmse"),
            deterministic=True,
            destructive=False,
            max_wall_s=300.0,
        ),
        ActionSpec(
            name="physical_confirmation",
            label="Charge a fresh cell with the protocol in a chamber (a person runs it)",
            executor="human",
            parameters=_protocol_parameters(tolerance_c=0.02, tolerance_t=1.0),
            produces=(
                "charge_time_80",
                "max_surface_temperature",
                "surface_temperature",
                "relaxation_plateau",
            ),
            deterministic=False,
            destructive=True,
            procedure=(
                "Fresh LG INR21700 M50 at 10 % state of charge, rested at {ambient} degC in a "
                "temperature chamber, surface thermocouple at mid-height. Charge at "
                "{c_rate_1}C from 10 to 27.5 %, {c_rate_2}C to 45 %, {c_rate_3}C to 62.5 % "
                "and {c_rate_4}C to 80 % (5.0 Ah nominal, coulomb counted); hold 4.2 V "
                "constant voltage if it is reached. Stop at once if the surface exceeds "
                "50 degC or the voltage exceeds 4.2 V. Rest 2 h and log the voltage at 1 s "
                "or faster. Return the time to 80 %, the surface temperature trace and "
                "whether the rest voltage shows a stripping plateau or dV/dt minimum."
            ),
            max_wall_s=6 * 3600.0,
        ),
    ),
    citation=(
        "Chen et al., J. Electrochem. Soc. 167, 080534 (2020); O'Kane et al., Phys. Chem. "
        "Chem. Phys. 24, 7909 (2022); records: Zenodo doi:10.5281/zenodo.4032561"
    ),
    licence="Records CC BY 4.0; PyBaMM BSD-3-Clause",
    solver={
        "name": "PyBaMM",
        "version": PYBAMM_VERSION,
        "model": f"{MODEL} {json.dumps(MODEL_OPTIONS, sort_keys=True)}",
        "parameter_set": PARAMETER_SET,
        "heat_transfer_W_m2K": str(HEAT_TRANSFER_W_M2K),
        "validity": (
            "0.2-1.5C steps, 23-27 degC within range, 5-40 degC extrapolation; "
            "charge above 0.3C unvalidated by records; computational only"
        ),
    },
)


def canonical_json(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def result_hash(result: dict) -> str:
    """sha256 of a solver result: everything except wall time, which stays outside it."""
    return hashlib.sha256(canonical_json(result)).hexdigest()


def canonical(action: str, params: dict[str, Any]) -> dict[str, float]:
    """Parameters as the solver runs them: C-rates snapped to the 0.05C grid (within 1e-6)."""
    out = {}
    for p in SPEC.action(action).parameters:
        v = float(params[p.name])
        out[p.name] = round(v, 2) if p.name in C_RATES else v
    return out


def _plain_bound(name: str, value: Any) -> str:
    if name in C_RATES:
        window = WINDOWS[C_RATES.index(name)]
        if value > C_HI:
            return (
                f"{name} = {value}C ({window}) is above {C_HI}C, the largest current in "
                "the validation records; the model is not validated there"
            )
        return (
            f"{name} = {value}C ({window}) is below {C_LO}C, the smallest step this "
            "campaign allows (a slower charge would not reach 80 % in 4 h)"
        )
    return (
        f"ambient = {value} degC is outside {AMBIENT_LO:g}-{AMBIENT_HI:g} degC, the range "
        "the solver was checked over; the parameters were fitted near 25 degC"
    )


def check_params(action: str, params: dict[str, Any]) -> list[str]:
    """Shared structure checks, then plain-language bounds and the 0.05C grid."""
    problems = _check_params(SPEC, action, params)
    if action not in {a.name for a in SPEC.actions}:
        return problems
    out = []
    for problem in problems:
        name = problem.split(" ", 1)[0]
        if " must be in [" in problem:
            out.append(_plain_bound(name, params[name]))
        else:
            out.append(problem)
    for name in C_RATES:
        v = params.get(name)
        if (
            isinstance(v, (int, float))
            and not isinstance(v, bool)
            and math.isfinite(v)
            and C_LO <= v <= C_HI
            and abs(v / C_GRID - round(v / C_GRID)) > 1e-6
        ):
            lo = math.floor(v / C_GRID) * C_GRID
            out.append(
                f"{name} = {v}C is not on the {C_GRID}C grid; use {lo:.2f} or "
                f"{lo + C_GRID:.2f}"
            )
    return out


def validity(action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Range label and plain notes for valid parameters; every result carries these."""
    notes = ["Computational result from a model, not a measurement."]
    label = "within_range"
    if action in ("simulate_protocol", "physical_confirmation"):
        amb = float(params["ambient"])
        if not AMBIENT_IN_RANGE[0] <= amb <= AMBIENT_IN_RANGE[1]:
            label = "extrapolation"
            notes.append(
                f"Extrapolation: ambient {amb:g} degC. The parameters were fitted near 25 "
                "degC and the records were taken at about 26.7 degC; the temperature "
                "dependence is borrowed from other cells."
            )
        top = max(float(params[c]) for c in C_RATES)
        if top > 0.3:
            notes.append(
                "No record holds a charge faster than 0.3C; currents up to 1.5C are "
                "checked on discharge only."
            )
        if top > MANUFACTURER_MAX_C:
            notes.append(
                f"Above the manufacturer's {MANUFACTURER_MAX_C}C charge rating; a "
                "physical test of this protocol needs the engineer's approval."
            )
    if action == "physical_confirmation":
        notes[0] = "Physical test run by a person; the result is a measurement."
    return {"label": label, "notes": notes}


def feasible(
    scalars: dict[str, Any],
    margin_mV: float = PLATING_MARGIN_MV,
    t_limit_C: float = T_LIMIT_C,
) -> bool:
    """The frozen feasibility rule: ok, and clear of each limit by its solver bound."""
    try:
        return (
            scalars.get("status", "ok") == "ok"
            and float(scalars["min_anode_potential"]) >= margin_mV + 0.5
            and float(scalars["max_temperature"]) <= t_limit_C - 0.1
            and math.isfinite(float(scalars["charge_time_80"]))
        )
    except (KeyError, TypeError, ValueError):
        return False


def table_from_result(result: dict) -> ResultTable:
    series = result.get("series") or {}
    return ResultTable(
        time_unit=series.get("time_unit"),
        t=tuple(series.get("t", ())),
        series={
            k: (v["unit"], tuple(v["values"]))
            for k, v in (series.get("channels") or {}).items()
        },
        scalars={k: (v["value"], v["unit"]) for k, v in result["scalars"].items()},
    )


def _child_env() -> dict[str, str]:
    """The parent's environment without provider keys or tokens: the child needs none."""
    secret = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    return {
        k: v for k, v in os.environ.items() if not any(s in k.upper() for s in secret)
    }


# --- Domain baseline: constrained Bayesian optimisation --------------------------------

_LATTICE = np.round(np.arange(C_LO, C_HI + C_GRID / 2, C_GRID), 2)  # 27 values
_N = len(_LATTICE)
_LENGTH_SCALES = (0.1, 0.2, 0.35, 0.5, 0.75, 1.0, 1.5, 2.0)
_JITTER = 1e-6


def _sobol(n: int, seed: int) -> np.ndarray:
    """First n points of a 4-D Sobol sequence (Joe-Kuo directions), digitally shifted.

    The shift comes from sha256(seed, dim), so the points do not depend on the numpy
    version's random streams.
    """
    bits = 30
    # (s, a, m) per dimension; dimension 1 is van der Corput
    params = [(1, 0, [1]), (2, 1, [1, 3]), (3, 1, [1, 3, 1])]
    v = [[1 << (bits - k) for k in range(1, bits + 1)]]
    for s, a, m in params:
        d = [m[k] << (bits - 1 - k) for k in range(s)]
        for k in range(s, bits):
            x = d[k - s] ^ (d[k - s] >> s)
            for j in range(1, s):
                if (a >> (s - 1 - j)) & 1:
                    x ^= d[k - j]
            d.append(x)
        v.append(d)
    out = np.empty((n, STEPS))
    for dim in range(STEPS):
        shift = int.from_bytes(
            hashlib.sha256(f"sobol:{seed}:{dim}".encode()).digest()[:4], "big"
        ) >> (32 - bits)
        for i in range(n):
            g, x, k = i ^ (i >> 1), 0, 0
            while g:
                if g & 1:
                    x ^= v[dim][k]
                g >>= 1
                k += 1
            out[i, dim] = (x ^ shift) / (1 << bits)
    return out


def _matern52(a: np.ndarray, b: np.ndarray, ls: float) -> np.ndarray:
    d = np.sqrt(np.maximum(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1), 0.0)) / ls
    s5 = math.sqrt(5.0) * d
    return (1.0 + s5 + 5.0 / 3.0 * d * d) * np.exp(-s5)


class _GP:
    """Zero-mean GP on standardised targets; isotropic Matern 5/2; length scale by grid ML."""

    def __init__(self, x: np.ndarray, y: np.ndarray):
        self.x, self.mu = x, float(y.mean())
        self.sd = float(y.std()) or 1.0
        z = (y - self.mu) / self.sd
        best = None
        for ls in _LENGTH_SCALES:
            k = _matern52(x, x, ls) + _JITTER * np.eye(len(x))
            chol = np.linalg.cholesky(k)
            alpha = np.linalg.solve(chol.T, np.linalg.solve(chol, z))
            lml = -0.5 * float(z @ alpha) - float(np.log(np.diag(chol)).sum())
            if best is None or lml > best[0] + 1e-12:
                best = (lml, ls, chol, alpha)
        _, self.ls, self.chol, self.alpha = best

    def predict(self, xs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        ks = _matern52(xs, self.x, self.ls)
        mean = ks @ self.alpha
        w = np.linalg.solve(self.chol, ks.T)
        var = np.maximum(1.0 - (w * w).sum(0), 1e-12)
        return mean * self.sd + self.mu, np.sqrt(var) * self.sd


_erfc = np.frompyfunc(math.erfc, 1, 1)


def _log_cdf(z: np.ndarray) -> np.ndarray:
    """log Phi(z), with the asymptotic tail below -30 where erfc underflows."""
    out = np.empty_like(z)
    tail = z < -30
    out[~tail] = np.log(
        np.maximum(0.5 * _erfc(-z[~tail] / math.sqrt(2.0)).astype(float), 1e-300)
    )
    zt = z[tail]
    out[tail] = -0.5 * zt * zt - np.log(-zt) - 0.5 * math.log(2 * math.pi)
    return out


def _scalar(entry: dict, name: str):
    value = (entry.get("result") or {}).get(name)
    if isinstance(value, dict):
        value = value.get("value")
    elif isinstance(value, (list, tuple)):
        value = value[0]
    return value


class ConstrainedBO:
    """Constrained Bayesian optimisation over the 4-D 0.05C lattice (Attia et al. 2020).

    Three Gaussian processes (isotropic Matern 5/2, length scale by maximum marginal
    likelihood on a fixed grid) model charge time, lowest anode potential and peak
    temperature. Three Sobol points start it; then each step proposes the unrun lattice
    point maximising constrained expected improvement (Gardner et al. 2014): expected
    improvement of the charge time times the probability of meeting both limits with the
    feasibility rule's solver bounds, or the probability alone until a feasible run exists.
    Same budget as the agent (8). numpy only, no random streams: deterministic for a seed.

    History entries are dicts {"action", "params", "result": {quantity: value}}; a run with
    no result (failed) counts as tried. An entry {"constraints": {"min_anode_potential": 25}}
    (an amendment, keyed by the objective's constraint quantities; max_temperature likewise)
    changes the limit from then on (case B). Runs at another ambient are ignored.
    """

    name = "constrained-bo"

    def __init__(
        self,
        seed: int = 0,
        ambient: float = 25.0,
        margin_mV: float = PLATING_MARGIN_MV,
        t_limit_C: float = T_LIMIT_C,
        n_init: int = 3,
    ):
        self.seed, self.ambient = seed, float(ambient)
        self.margin_mV, self.t_limit_C, self.n_init = margin_mV, t_limit_C, n_init

    def _params(self, idx: tuple[int, ...]) -> dict[str, float]:
        return {
            **{c: float(_LATTICE[i]) for c, i in zip(C_RATES, idx)},
            "ambient": self.ambient,
        }

    def propose(self, history: list[dict], remaining: int):
        if remaining <= 0:
            return []
        margin, t_limit = self.margin_mV, self.t_limit_C
        tried, rows = set(), []
        for entry in history:
            if "constraints" in entry:
                # an amendment, keyed by the objective's constraint quantities
                limits = entry["constraints"]
                margin = float(limits.get("min_anode_potential", margin))
                t_limit = float(limits.get("max_temperature", t_limit))
                continue
            p = entry.get("params") or {}
            if entry.get("action") != "simulate_protocol" or p.get("ambient") is None:
                continue
            if abs(float(p["ambient"]) - self.ambient) > 1e-9:
                continue
            idx = tuple(int(round((float(p[c]) - C_LO) / C_GRID)) for c in C_RATES)
            tried.add(idx)
            rows.append((idx, entry))
        if len(tried) < self.n_init:
            out, i = [], 0
            want = min(remaining, self.n_init - len(tried))
            points = _sobol(self.n_init + 16, self.seed)
            while len(out) < want and i < len(points):
                idx = tuple(int(min(_N - 1, u * _N)) for u in points[i])
                i += 1
                if idx not in tried:
                    tried.add(idx)
                    out.append(("simulate_protocol", self._params(idx)))
            return out
        idx = self._next(rows, tried, margin, t_limit)
        return [] if idx is None else [("simulate_protocol", self._params(idx))]

    def _next(self, rows, tried, margin, t_limit):
        def data(name, ok_only=False):
            xs, ys = [], []
            for idx, entry in rows:
                v = _scalar(entry, name)
                if ok_only and _scalar(entry, "status") not in (None, "ok"):
                    continue
                if isinstance(v, (int, float)) and math.isfinite(v):
                    xs.append(np.array(idx) / (_N - 1))
                    ys.append(float(v))
            return (np.array(xs), np.array(ys)) if xs else None

        t80, phi, tmax = (
            data("charge_time_80", True),
            data("min_anode_potential"),
            data("max_temperature"),
        )
        best = None
        for _, entry in rows:
            scalars = {
                k: _scalar(entry, k)
                for k in (
                    "status",
                    "charge_time_80",
                    "min_anode_potential",
                    "max_temperature",
                )
            }
            if scalars["status"] is None:
                scalars["status"] = "ok"
            if feasible(scalars, margin, t_limit):
                t = float(scalars["charge_time_80"])
                best = t if best is None else min(best, t)
        gps = [
            None if d is None else _GP(*d)
            for d in (t80 if best is not None else None, phi, tmax)
        ]
        top, top_idx = -np.inf, None
        chunk = 1 << 15
        for start in range(0, _N**STEPS, chunk):
            flat = np.arange(start, min(start + chunk, _N**STEPS))
            digits = np.stack(
                [(flat // _N ** (STEPS - 1 - d)) % _N for d in range(STEPS)], 1
            )
            xs = digits / (_N - 1)
            score = np.zeros(len(flat))
            if gps[1] is not None:
                m, s = gps[1].predict(xs)
                score += _log_cdf((m - (margin + 0.5)) / s)
            if gps[2] is not None:
                m, s = gps[2].predict(xs)
                score += _log_cdf(((t_limit - 0.1) - m) / s)
            if gps[0] is not None:
                m, s = gps[0].predict(xs)
                z = (best - m) / s
                ei = s * (
                    z * np.exp(_log_cdf(z))
                    + np.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
                )
                score = score + np.log(np.maximum(ei, 1e-300))
            for i in np.argsort(-score, kind="stable"):
                if not np.isfinite(score[i]) or score[i] <= top:
                    break
                idx = tuple(int(v) for v in digits[i])
                if idx not in tried:
                    top, top_idx = float(score[i]), idx
                    break
        return top_idx


# --- The adapter --------------------------------------------------------------------------


class FastChargeAdapter:
    """pybamm-fastcharge behind the system adapter boundary."""

    spec = SPEC

    def __init__(self, records: list[Path] | None = None):
        self.records = [Path(p) for p in records] if records else None

    def code_digest(self) -> str:
        here = Path(__file__)
        return file_digest(here, here.with_name("lgm50_solver.py"))

    def check_params(self, action: str, params: dict[str, Any]) -> list[str]:
        return check_params(action, params)

    def configuration(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"action": action, **canonical(action, params)}

    def validity(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        return validity(action, params)

    def estimate_cost(self, action: str, params: dict[str, Any]) -> Cost:
        """Wall time from the prototype runs: about 12 s to start the solver, then 3-25 s."""
        if action == "calibrate_thermal":
            return Cost(wall_s=75.0)
        if action == "physical_confirmation":
            minutes = sum(
                (SOC_END - SOC_START) / STEPS * 60 / float(params[c]) for c in C_RATES
            )
            return Cost(person_minutes=round(minutes + 120 + 30, 1), specimens=1)
        cold = float(params.get("ambient", 25.0)) < AMBIENT_IN_RANGE[0]
        return Cost(wall_s=30.0 if cold else 20.0)

    def execute(
        self, action: str, params: dict[str, Any], workdir: Path
    ) -> ExecutionOutput:
        if SPEC.action(action).executor != "computational":
            raise NotExecutable(f"{action} is run by a person, not by the solver")
        problems = self.check_params(action, params)
        if problems:
            raise ValueError("; ".join(problems))
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        request = {"action": action, "params": canonical(action, params)}
        if action == "calibrate_thermal":
            request["records"] = [str(p) for p in self.records or default_records()]
        proc = subprocess.run(
            [sys.executable, "-m", "translucent.systems.lgm50_solver"],
            input=canonical_json(request),
            capture_output=True,
            timeout=SPEC.action(action).max_wall_s,
            env=_child_env(),
            cwd=workdir,
        )
        log = workdir / "solver.log"
        log.write_bytes(proc.stderr)
        if proc.returncode != 0:
            tail = (
                proc.stdout.decode(errors="replace")[-400:]
                or proc.stderr.decode(errors="replace")[-400:]
            )
            raise RuntimeError(f"solver child exited {proc.returncode}: {tail}")
        out = json.loads(proc.stdout)
        result = out["result"]
        if result_hash(result) != out["sha256"]:
            raise RuntimeError("solver output does not match its own sha256")
        path = workdir / "result.json"
        path.write_bytes(canonical_json(result))
        if result["status"] == "solver_error":
            raise RuntimeError(f"solver error: {result.get('error')}")
        table = table_from_result(result)
        if "validity" in SPEC.action(action).produces:
            # the range label the request showed before approval, now on the result it qualifies
            table.scalars["validity"] = (validity(action, params)["label"], "")
        return ExecutionOutput(
            table=table,
            files={"result.json": path, "solver.log": log},
            solver={
                k: v if isinstance(v, str) else json.dumps(v, sort_keys=True)
                for k, v in {**result["solver"], "result_sha256": out["sha256"]}.items()
            },
            executed=dict(result["params"]),
        )

    def check_result(
        self, action: str, params: dict[str, Any], table: ResultTable
    ) -> list[str]:
        s = {k: v[0] for k, v in table.scalars.items()}
        problems = []
        if action == "calibrate_thermal":
            h = s.get("heat_transfer_coefficient")
            if not isinstance(h, (int, float)) or not 1.0 <= h <= 50.0:
                problems.append(
                    f"fitted heat-transfer coefficient {h} is outside 1-50 W/m^2/K"
                )
            return problems
        if action != "simulate_protocol":
            return problems
        if s.get("status") == "not_reached":
            problems.append("the charge did not reach 80 % state of charge within 4 h")
        elif s.get("status") != "ok":
            problems.append(
                "the solution failed its sanity checks (non-finite values, a temperature "
                "below ambient, voltage above 4.25 V or negative plated lithium)"
            )
        t80 = s.get("charge_time_80")
        tol = SPEC.quantities[0].tolerance  # t80 is rounded; allow its solver bound
        if isinstance(t80, (int, float)) and table.t and table.t[-1] < (t80 - tol) * 60:
            problems.append("the time series stops before the charge reached 80 %")
        # Scalars come from every solver point, the series merges points under 1 ms apart
        # at step boundaries: they agree within each quantity's solver bound.
        bounds = {q.name: q.tolerance for q in SPEC.quantities}
        for name, scalar, pick in (
            ("cell_temperature", "max_temperature", max),
            ("anode_potential", "min_anode_potential", min),
            ("voltage", "max_voltage", max),
        ):
            series = table.series.get(name)
            if series and series[1] and scalar in s:
                if abs(pick(series[1]) - s[scalar]) > max(bounds[scalar], 1e-3):
                    problems.append(f"{scalar} differs from the {name} series")
        temps = table.series.get("cell_temperature")
        if temps and temps[1] and min(temps[1]) < float(params["ambient"]) - 1.0:
            problems.append("the cell temperature fell more than 1 K below ambient")
        return problems

    def procedure(self, action: str, params: dict[str, Any]) -> str | None:
        template = SPEC.action(action).procedure
        if template is None:
            return None
        p = canonical(action, params)
        text = template.format(**{k: f"{v:g}" for k, v in p.items()})
        if max(p[c] for c in C_RATES) > MANUFACTURER_MAX_C:
            text += (
                f" Above the manufacturer's {MANUFACTURER_MAX_C}C charge rating: requires "
                "engineer approval."
            )
        return text

    def baseline_policy(
        self,
        seed: int = 0,
        ambient: float = 25.0,
        margin_mV: float = PLATING_MARGIN_MV,
        t_limit_C: float = T_LIMIT_C,
    ) -> ConstrainedBO:
        return ConstrainedBO(seed, ambient, margin_mV, t_limit_C)

    def evidence_problems(self, ledger, campaign_id: str) -> list[str]:
        # Verify never re-runs the solver; it checks the recorded output hashes.
        return []


def default_records() -> list[Path]:
    """The fetched Chen 2020 records (scripts/fetch_lgm50_data.py)."""
    paths = [DATA / name for name in RECORDS]
    missing = [p.name for p in paths if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"{', '.join(missing)} not in {DATA}; run: uv run python "
            "scripts/fetch_lgm50_data.py"
        )
    return paths
