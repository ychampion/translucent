"""evals/thermal_records.py: the frozen splits, the comparison and fitting arithmetic, and the record loader.

The last test runs two PyBaMM discharges and is opt-in (the solver group and TRANSLUCENT_SOLVER_TESTS=1, plus the
fetched records); everything else is numpy only.
"""
import importlib.util
import math
import os

import numpy as np
import pytest

from evals import thermal_records as tr
from translucent.systems import lgm50_fastcharge as fc

RECORDS = [fc.DATA / n for n in fc.RECORDS]
have_records = pytest.mark.skipif(not all(p.exists() for p in RECORDS), reason="records not fetched")


def test_the_frozen_splits():
    s = tr.splits()
    assert list(s) == ["A-cell02", "A-cell03", "A-cell04", "B-rate1.5"]
    for name, sp in s.items():
        assert not set(sp["train"]) & set(sp["test"])
        assert sorted(sp["train"] + sp["test"]) == sorted(tr.ALL)
    for cell in tr.CELLS:
        sp = s[f"A-cell{cell}"]
        assert sp["test"] == [f"cell{cell}@0.5C", f"cell{cell}@1C", f"cell{cell}@1.5C"]
        assert len(sp["train"]) == 6 and all(f"cell{cell}" not in n for n in sp["train"])
    assert s["B-rate1.5"]["test"] == ["cell02@1.5C", "cell03@1.5C", "cell04@1.5C"]
    assert all(not n.endswith("@1.5C") for n in s["B-rate1.5"]["train"])


def test_the_frozen_constants():
    assert tr.PRIOR == {"h": 10.0, "k": 1.0}
    assert 10.0 in tr.H_GRID and 1.0 in tr.K_GRID
    assert min(tr.H_GRID) == 1.0 and max(tr.H_GRID) == 50.0 and min(tr.K_GRID) == 0.5 and max(tr.K_GRID) == 2.5
    assert list(tr.H_GRID) == sorted(tr.H_GRID) and list(tr.K_GRID) == sorted(tr.K_GRID)
    assert len(tr.HEAT_CAPACITIES) == 5 and all("specific heat capacity" in n for n in tr.HEAT_CAPACITIES)
    assert tr.POINTS == 200 and tr.CHI2_95_1DOF == pytest.approx(3.841)


def _meas(q, rise, v):
    return {"q": np.asarray(q, float), "rise": np.asarray(rise, float), "v": np.asarray(v, float)}


def test_compare_on_the_capacity_points():
    q = np.linspace(0, 5, 501)
    rise = 8 * (q / 5) ** 2
    v = 4.1 - 0.2 * q
    meas = _meas(q, rise, v)
    same = tr.compare(meas, q, v, rise)
    assert same["rmse_K"] == 0 and same["max_rise_error_K"] == 0 and same["voltage_rmse_mV"] == 0
    hot = tr.compare(meas, q, v + 0.01, rise + 0.5)  # a uniform +0.5 K and +10 mV
    assert hot["rmse_K"] == pytest.approx(0.5) and hot["mse_K2"] == pytest.approx(0.25)
    assert hot["max_rise_error_K"] == pytest.approx(0.5) and hot["voltage_rmse_mV"] == pytest.approx(10)
    # the model runs out earlier: compared only up to its own end, max rise over each whole trajectory
    short = tr.compare(meas, q[:401], v[:401], rise[:401])
    assert short["capacity_compared_Ah"] == pytest.approx(4.0) and short["rmse_K"] == pytest.approx(0, abs=1e-12)
    assert short["max_rise_error_K"] == pytest.approx(8 * 0.8**2 - 8)


def test_objective_weighs_each_trajectory_the_same():
    rows = {"a": {"mse_K2": 1.0}, "b": {"mse_K2": 9.0}, "c": {"mse_K2": 100.0}}
    assert tr.objective(rows, ["a", "b"]) == 5.0


def test_n_eff_from_serial_correlation():
    assert tr.n_eff([0.0, 0.0]) == 400
    assert tr.n_eff([0.9999]) == 1.0                      # floor: a smooth residual counts once
    assert tr.n_eff([0.5]) == pytest.approx(200 / 3)
    assert tr.n_eff([-0.9]) == 200                        # cap: never more than the points
    r = np.cos(np.linspace(0, 1, 200))
    assert tr.lag1(r) > 0.99 and tr.lag1(np.zeros(5)) == 0.0


def test_vertex_1d_is_exact_on_a_parabola_and_clamped():
    xs = [0.0, 1.0, 3.0, 4.0]
    ys = [(x - 1.7) ** 2 for x in xs]
    assert tr.vertex_1d(xs, ys, 1) == pytest.approx(1.7)
    assert tr.vertex_1d(xs, ys, 0) == 0.0                 # at the edge, no refinement
    assert tr.vertex_1d([0.0, 1.0, 2.0], [0.0, 1.0, 0.0], 1) == 1.0  # concave
    assert tr.vertex_1d([0.0, 1.0, 2.0], [10.0, 1.0, 0.9], 1) <= 2.0


def test_vertex_2d_is_exact_on_a_convex_quadratic():
    xs, ys = [0.0, 1.0, 2.0, 3.0], [0.0, 0.5, 1.0, 1.5]
    z = np.array([[(x - 1.3) ** 2 + 2 * (y - 0.6) ** 2 + 0.5 * (x - 1.3) * (y - 0.6) for y in ys] for x in xs])
    assert tr.vertex_2d(xs, ys, z, 1, 1) == pytest.approx((1.3, 0.6))
    flat = np.array([[x - y for y in ys] for x in xs])   # not convex: the grid point
    assert tr.vertex_2d(xs, ys, flat, 1, 2) == (1.0, 1.0)


def test_profile_interval_closed_and_open():
    xs = list(np.linspace(-3, 3, 13))
    prof = [1 + x**2 for x in xs]
    iv = tr.profile_interval(xs, prof, 1.0, tr.CHI2_95_1DOF)  # threshold J = 2: |x| <= 1
    assert iv["lo"] == pytest.approx(-1.0) and iv["hi"] == pytest.approx(1.0)
    assert not iv["lo_open"] and not iv["hi_open"]
    flat = tr.profile_interval(xs, [1 + 0.01 * x for x in xs], 1 - 0.03, 1.0)
    assert flat["lo_open"] and flat["hi_open"] and (flat["lo"], flat["hi"]) == (-3, 3)
    one = tr.profile_interval(xs, [1 + abs(x) for x in xs], 1.0, 1e6)  # many points: shrinks to the minimum
    assert one["lo"] == pytest.approx(0, abs=1e-5) and one["hi"] == pytest.approx(0, abs=1e-5)


def _synthetic_table(h0, k0, h_grid, k_grid, extra=()):
    """Every trajectory's mse is a convex bowl in (log h, k) with its minimum at (h0, k0)."""
    def row(h, k):
        mse = 0.04 + (math.log(h / h0)) ** 2 + 0.3 * (k - k0) ** 2
        return {"status": "ok", "mse_K2": mse, "rmse_K": math.sqrt(mse), "max_rise_error_K": k - k0,
                "model_max_rise_K": 10.0, "measured_max_rise_K": 10.0, "voltage_rmse_mV": 50.0, "rho": 0.99}
    pts = [(h, k) for h in h_grid for k in k_grid] + list(extra)
    return {(n, h, k): row(h, k) for n in tr.ALL for h, k in pts}


def test_fitting_recovers_a_known_minimum_and_reports_intervals():
    h_grid, k_grid = (2.0, 4.0, 6.0, 8.0, 12.0, 20.0), (0.5, 1.0, 1.5, 2.0, 2.5)
    table = _synthetic_table(6.5, 1.0, h_grid, k_grid)
    train = tr.splits()["A-cell04"]["train"]
    f1 = tr.grid_estimate(table, train, "F1", h_grid, k_grid)
    assert f1["k"] == 1.0 and f1["h"] == pytest.approx(6.5, abs=0.05)
    f2 = tr.grid_estimate(table, train, "F2", h_grid, k_grid)
    assert f2["h"] == pytest.approx(6.5, abs=0.05) and f2["k"] == pytest.approx(1.0, abs=0.01)
    table.update(_synthetic_table(6.5, 1.0, (), (), extra=[(f2["h"], f2["k"]), (f1["h"], f1["k"])]))
    rows = {n: table[(n, f2["h"], f2["k"])] for n in tr.ALL}
    neff = tr.n_eff([rows[n]["rho"] for n in train])
    iv = tr.intervals(f2, "F2", tr.objective(rows, train), neff, h_grid, k_grid)
    assert iv["h"]["lo"] < 6.5 < iv["h"]["hi"] and iv["k"]["lo"] < 1.0 < iv["k"]["hi"]
    s = tr.summarise(rows, train, tr.splits()["A-cell04"]["test"])
    assert s["status"] == "ok" and s["heldout_rmse_K"] == pytest.approx(0.2, abs=1e-3)
    assert set(s["heldout"]) == {"cell04@0.5C", "cell04@1C", "cell04@1.5C"}


def test_summarise_marks_a_solver_failure():
    rows = {n: {"status": "ok", "mse_K2": 1.0} for n in tr.ALL}
    rows["cell02@1C"] = {"status": "solver_error", "error": "boom", "mse_K2": math.inf}
    s = tr.summarise(rows, list(tr.ALL[:6]), list(tr.ALL[6:]))
    assert s == {"status": "solver_error", "errors": {"cell02@1C": "boom"}}


@have_records
def test_the_records_give_nine_trajectories():
    m = tr.load_records(RECORDS)
    assert list(m) == list(tr.ALL)
    for n, d in m.items():
        assert d["rise"][0] == 0 and d["rise"].max() > 5
        assert 4.85 < d["q"][-1] < 5.0 and d["v"][-1] == pytest.approx(2.5, abs=0.01)
        assert 25 < d["ambient"] < 30
    assert m["cell02@1.5C"]["current"] == pytest.approx(7.5, rel=0.01)


@pytest.mark.solver
@pytest.mark.skipif(
    importlib.util.find_spec("pybamm") is None or os.environ.get("TRANSLUCENT_SOLVER_TESTS") != "1"
    or not all(p.exists() for p in RECORDS),
    reason="solver tests are opt-in: the solver group, TRANSLUCENT_SOLVER_TESTS=1 and the fetched records",
)
def test_k_one_is_the_shipped_discharge_and_more_mass_heats_less():
    from translucent.systems import lgm50_solver as ls

    tr._init([str(p) for p in RECORDS])
    name = "cell02@1.5C"
    meas = tr._MEASURED[name]
    _, base = tr.solve_job((name, 10.0, 1.0))
    _, heavy = tr.solve_job((name, 10.0, 2.0))
    q, v, temp, _, _ = ls._discharge(ls._pybamm(), meas["current"], meas["ambient"], 10.0)
    assert base["status"] == "ok" and heavy["status"] == "ok"
    assert base["model_max_rise_K"] == pytest.approx(float(temp.max() - temp[0]), abs=1e-6)
    assert heavy["model_max_rise_K"] < base["model_max_rise_K"]
