"""A fast stand-in for the pybamm-fastcharge-lab solver child: the REAL lgm50_lab_solver logic (lab_charge, forecast,
fit_thermal, check_model, rank_measurements, optimize_protocol, run, main) over a lumped stand-in for `charge()`.

    python -m support.lab_fake_solver < request.json      (tests/ on PYTHONPATH)

The stand-in charge, per (rates, ambient, rest_min, h, k, prefix):
  * four constant-current steps over the SOC windows (no constant-voltage phase: the voltage stays under 4.2 V on the
    lattice), then an optional rest;
  * heat Q = I^2 R (constant within a step), lumped balance C dT/dt = Q - h A (T - T_amb) with C = 42.86 k J/K and
    A = 0.00531 m^2, solved exactly per step from the ambient temperature;
  * terminal voltage = OCV(SOC) + R_V I - 3 mV/K (T - 25); anode potential = P0 - B C-rate - S SOC + G (T - 25) mV:
    it falls with current and rises with temperature, so better cooling lowers the anode margin, as in the DFN.
It returns every key the real `charge()` returns.

Importing this module (as `support.lab_fake_solver`) also registers `FakeLabAdapter` as the pybamm-fastcharge-lab
adapter: exec_child imports the parent's adapter module before it looks the system up, so the child process runs
this stand-in too. Tests import it inside a fixture after isolating the registry (support.loop_systems.register).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from translucent import systems
from translucent.systems import lgm50_fastcharge as fc
from translucent.systems import lgm50_lab as lab
from translucent.systems import lgm50_lab_solver as S
from translucent.systems.base import file_digest

C0_J_K = 42.86                 # lumped heat capacity at k = 1
AREA_M2 = 0.00531
R_HEAT = 0.022                 # ohm: heat I^2 R
R_V = 0.012                    # ohm: overpotential in the terminal voltage
DT_S = 5.0                     # internal output period
P0_MV, B_MV, S_MV, G_MV_K = 175.0, 97.0, 125.0, 3.0
SOLVER = {"name": "lab-fake-solver", "model": "lumped stand-in for tests (not PyBaMM)"}


def _ocv(soc):
    return 3.50 + 0.65 * np.asarray(soc)


def charge(job: tuple) -> dict:
    rates, ambient, rest_min, h, k, prefix = job
    rates = [float(c) for c in rates]
    cap, area = C0_J_K * float(k), AREA_M2
    a = float(h) * area / cap
    window = (fc.SOC_END - fc.SOC_START) / fc.STEPS
    n_steps = prefix or fc.STEPS
    i0 = rates[0] * fc.CAPACITY_AH
    t_all, T_all, I_all = [np.array([0.0])], [np.array([float(ambient)])], [np.array([i0])]
    Q_all, soc_all = [np.array([i0 ** 2 * R_HEAT])], [np.array([fc.SOC_START])]
    t0, T0, soc0 = 0.0, float(ambient), fc.SOC_START
    for c in rates[:n_steps]:
        current = c * fc.CAPACITY_AH
        heat = current ** 2 * R_HEAT
        dur = window * 3600.0 / c
        tau = np.append(np.arange(DT_S, dur, DT_S), dur)
        e = np.exp(-a * tau)
        Ts = ambient + (T0 - ambient) * e + heat / (float(h) * area) * (1 - e)
        t_all.append(t0 + tau)
        T_all.append(Ts)
        I_all.append(np.full(tau.shape, current))
        Q_all.append(np.full(tau.shape, heat))
        soc_all.append(soc0 + c * tau / 3600.0)
        t0, T0, soc0 = t0 + dur, float(Ts[-1]), soc0 + window
    t_charge = t0
    if not prefix and rest_min > 0:
        dur = float(rest_min) * 60.0
        tau = np.append(np.arange(DT_S, dur, DT_S), dur)
        Ts = ambient + (T0 - ambient) * np.exp(-a * tau)
        t_all.append(t0 + tau)
        T_all.append(Ts)
        I_all.append(np.zeros(tau.shape))
        Q_all.append(np.zeros(tau.shape))
        soc_all.append(np.full(tau.shape, soc0))
    t, T, current, heat, soc = (np.concatenate(x) for x in (t_all, T_all, I_all, Q_all, soc_all))
    soc = np.minimum(soc, fc.SOC_START + n_steps * window)
    q = (soc - fc.SOC_START) * fc.CAPACITY_AH
    v = _ocv(soc) + R_V * current - 0.003 * (T - 25.0)
    crate = current / fc.CAPACITY_AH
    frac = (soc - fc.SOC_START) / (fc.SOC_END - fc.SOC_START)
    phi = P0_MV - B_MV * crate - S_MV * frac ** 2 + G_MV_K * (T - 25.0)
    plated = np.concatenate([[0.0], np.cumsum(np.maximum(0.0, -phi[1:]) * np.diff(t) * 1e-7)])
    target = (fc.SOC_END - fc.SOC_START) * fc.CAPACITY_AH
    if prefix:
        target = (prefix / fc.STEPS) * target
    kk = int(np.argmax(q >= target - 1e-3))
    cut = slice(0, kk + 1)
    sane = bool(np.all(np.isfinite(T)) and T.max() < 120 and T.min() > ambient - 1 and v[cut].max() < fc.V_MAX + 0.05)
    return {"t": t, "q": q, "v": v, "T": T, "phi": phi, "plated": plated, "current": current, "heat": heat,
            "heat_capacity": cap, "area": area, "status": "ok" if sane else "nonsense",
            "error": None if sane else "failed sanity checks", "t80_s": float(t_charge),
            "min_anode": float(phi[cut].min()), "max_T": float(T.max()), "plated_end": float(plated[kk]),
            "solver": dict(SOLVER)}


def record_run(job: tuple) -> dict:
    """A record test's constant current (job as lgm50_lab_solver.record_run) under the same lumped stand-in."""
    mode, current, start_soc, ambient, t0, duration, h, k = job
    cap, area = C0_J_K * float(k), AREA_M2
    a = float(h) * area / cap
    heat = float(current) ** 2 * R_HEAT
    tau = np.append(np.arange(0.0, float(duration), DT_S), float(duration))
    e = np.exp(-a * tau)
    T = ambient + (float(t0) - ambient) * e + heat / (float(h) * area) * (1 - e)
    soc = float(start_soc) + (-1 if int(mode) == 1 else 1) * float(current) * tau / 3600.0 / fc.CAPACITY_AH
    v = _ocv(soc) - 0.003 * (T - 25.0)
    return {"t": tau, "T": T, "v": v, "heat": np.full(tau.shape, heat), "heat_capacity": cap, "area": area,
            "status": "ok", "error": None, "t80_s": None, "solver": dict(SOLVER)}


def install() -> None:
    """The real solver module with the stand-in charge; no PyBaMM import anywhere."""
    S.charge = charge
    S.record_run = record_run
    S._pybamm = lambda: None


class FakeLabAdapter(lab.LabAdapter):
    """pybamm-fastcharge-lab with this module as its solver child."""

    solver_module = "support.lab_fake_solver"

    def code_digest(self) -> str:
        return file_digest(Path(lab.__file__), Path(lab.__file__).with_name("lgm50_lab_solver.py"),
                           Path(lab.__file__).with_name("lgm50_solver.py"),
                           Path(lab.__file__).with_name("lgm50_fastcharge.py"), Path(__file__))


def register() -> FakeLabAdapter:
    adapter = FakeLabAdapter()
    systems.register(adapter)
    return adapter


if __name__ == "__main__":
    install()
    raise SystemExit(S.main())

register()
