"""pybamm-fastcharge with the real solver: determinism, refusals, the failing baseline, calibration, wall time.

Opt-in: needs `uv sync --group solver` (or the pinned packages installed) and TRANSLUCENT_SOLVER_TESTS=1, plus the
fetched records for calibration (scripts/fetch_lgm50_data.py). Runs one solver child at a time, never in parallel:
each child holds about 340 MB. Determinism is checked on this platform only.
"""

import importlib.util
import json
import os
import subprocess
import sys
import time

import pytest

from translucent import systems
from translucent.systems import lgm50_fastcharge as fc

pytestmark = [
    pytest.mark.solver,
    pytest.mark.skipif(
        importlib.util.find_spec("pybamm") is None or os.environ.get("TRANSLUCENT_SOLVER_TESTS") != "1",
        reason="solver tests are opt-in: uv sync --group solver and TRANSLUCENT_SOLVER_TESTS=1",
    ),
]

BASELINE = {"c_rate_1": 1.5, "c_rate_2": 1.5, "c_rate_3": 1.5, "c_rate_4": 1.5, "ambient": 25.0}


def child(request):
    started = time.perf_counter()
    out = subprocess.run(
        [sys.executable, "-m", "translucent.systems.lgm50_solver"],
        input=json.dumps(request).encode(),
        capture_output=True,
        timeout=600,
    )
    return out.returncode, json.loads(out.stdout), time.perf_counter() - started


@pytest.fixture(scope="module")
def baseline_runs(tmp_path_factory):
    """The failing baseline twice through the adapter's child process, one after the other."""
    adapter = systems.get("pybamm-fastcharge")
    runs = []
    for n in (1, 2):
        workdir = tmp_path_factory.mktemp(f"baseline{n}")
        started = time.perf_counter()
        output = adapter.execute("simulate_protocol", BASELINE, workdir)
        runs.append((output, workdir, time.perf_counter() - started))
    return adapter, runs


def test_same_parameters_give_the_same_output_hash(baseline_runs):
    _, [(a, dir_a, _), (b, dir_b, _)] = baseline_runs
    assert a.solver["result_sha256"] == b.solver["result_sha256"]
    assert (dir_a / "result.json").read_bytes() == (dir_b / "result.json").read_bytes()
    assert fc.result_hash(json.loads((dir_a / "result.json").read_bytes())) == a.solver["result_sha256"]


def test_every_result_names_solver_version_parameter_set_and_model_options(baseline_runs):
    _, [(output, workdir, _), _] = baseline_runs
    result = json.loads((workdir / "result.json").read_bytes())
    assert result["schema"] == fc.RESULT_SCHEMA and result["basis"] == "computational"
    solver = result["solver"]
    assert solver["version"] == fc.PYBAMM_VERSION and solver["casadi"] == "3.8.1"
    assert solver["pybammsolvers"] == "0.11.1" and solver["parameter_set"] == "OKane2022"
    assert solver["model"] == "DFN" and solver["model_options"] == fc.MODEL_OPTIONS
    assert solver["parameter_overrides"]["Total heat transfer coefficient [W.m-2.K-1]"] == 6.0
    assert solver["solver"] == "IDAKLUSolver" and solver["rtol"] > 0 and solver["atol"] > 0
    assert output.solver["parameter_set"] == "OKane2022"
    assert output.executed == BASELINE
    assert result["validity"]["label"] == "within_range"
    assert any("0.7C charge rating" in n for n in result["validity"]["notes"])


def test_failing_baseline_reproduces_its_violation(baseline_runs):
    adapter, [(output, _, _), _] = baseline_runs
    table = output.table
    assert adapter.check_result("simulate_protocol", BASELINE, table) == []
    produced = set(table.scalars) | set(table.series)
    assert produced >= set(adapter.spec.action("simulate_protocol").produces)
    s = {k: v[0] for k, v in table.scalars.items()}
    tol = {q.name: q.tolerance for q in adapter.spec.quantities}
    # Campaign choice: t80 28.2 min, Tmax 51.2 degC (fails 45 by 6.2 K), +7.3 mV (fails the +10 mV margin)
    assert abs(s["max_temperature"] - 51.2) <= tol["max_temperature"] and s["max_temperature"] > fc.T_LIMIT_C
    assert abs(s["min_anode_potential"] - 7.3) <= tol["min_anode_potential"]
    assert s["min_anode_potential"] < fc.PLATING_MARGIN_MV
    assert abs(s["charge_time_80"] - 28.2) <= tol["charge_time_80"]
    assert abs(s["plated_lithium"] - 0.0166) < 0.0005
    assert s["status"] == "ok" and s["cv_entered"] == "yes" and not fc.feasible(s)
    assert all(b > a for a, b in zip(table.t, table.t[1:]))
    assert table.series["current"][1][0] == pytest.approx(7.5, abs=0.01)


def test_wall_time_stays_under_the_stated_bounds(baseline_runs):
    adapter, runs = baseline_runs
    limit = adapter.spec.action("simulate_protocol").max_wall_s
    for output, workdir, wall in runs:
        assert wall < limit
    # the report's per-run solve bound (3-25 s on this laptop), excluding the solver import
    code, out, wall = child({"action": "simulate_protocol", "params": BASELINE})
    assert code == 0 and out["solve_wall_s"] < 25 and wall < limit
    assert out["sha256"] == runs[0][0].solver["result_sha256"]


def test_out_of_range_parameters_are_refused_before_the_solver_starts():
    code, out, wall = child({"action": "simulate_protocol", "params": {**BASELINE, "c_rate_4": 1.6}})
    assert code == 2 and "above 1.5C" in out["refused"][0]
    code, out, _ = child({"action": "simulate_protocol", "params": {**BASELINE, "ambient": -5.0}})
    assert code == 2 and "outside 5-40 degC" in out["refused"][0]
    code, out, _ = child({"action": "physical_confirmation", "params": BASELINE})
    assert code == 2 and "run by a person" in out["refused"][0]
    assert wall < 10  # no PyBaMM import on the refusal path


@pytest.mark.skipif(not all((fc.DATA / n).exists() for n in fc.RECORDS), reason="records not fetched")
def test_calibrate_thermal_fits_h_near_6_from_the_records(tmp_path):
    adapter = systems.get("pybamm-fastcharge")
    started = time.perf_counter()
    output = adapter.execute("calibrate_thermal", {}, tmp_path)
    assert time.perf_counter() - started < adapter.spec.action("calibrate_thermal").max_wall_s
    assert adapter.check_result("calibrate_thermal", {}, output.table) == []
    s = {k: v[0] for k, v in output.table.scalars.items()}
    assert abs(s["heat_transfer_coefficient"] - 6.0) <= 0.5
    assert s["thermal_fit_rmse"] < 1.5
    result = json.loads((tmp_path / "result.json").read_bytes())
    rates = result["details"]["rates"]
    assert set(rates) == {"0.5C", "1C", "1.5C"}
    for rate in rates.values():  # model within the cell-to-cell spread of the measured rise
        assert min(rate["measured_rise_K"]) - 1.0 <= rate["model_rise_K"] <= max(rate["measured_rise_K"]) + 1.0
    grid = result["details"]["grid"]
    assert grid["10"]["sse_K2"] > 10 * grid["6"]["sse_K2"]  # the parameter set's default h is far off
    assert set(result["details"]["records_sha256"]) == set(fc.RECORDS)
