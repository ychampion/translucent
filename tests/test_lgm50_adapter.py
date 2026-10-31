"""pybamm-fastcharge adapter without the solver: bounds, labels, cost, baseline policy, manifest, fetch."""

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_systems import check_protocol

from translucent import systems
from translucent.systems import lgm50_fastcharge as fc
from translucent.systems.base import Cost, NotExecutable, ResultTable

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "chen2020-lgm50" / "manifest.json"
spec = importlib.util.spec_from_file_location("fetch_lgm50_data", ROOT / "scripts" / "fetch_lgm50_data.py")
fetch_lgm50 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_lgm50)

BASELINE = {"c_rate_1": 1.5, "c_rate_2": 1.5, "c_rate_3": 1.5, "c_rate_4": 1.5, "ambient": 25.0}


def protocol(*rates, ambient=25.0):
    return {**dict(zip(fc.C_RATES, rates)), "ambient": ambient}


@pytest.fixture
def adapter():
    return systems.get("pybamm-fastcharge")


def test_registered_and_satisfies_the_protocol(adapter):
    assert isinstance(adapter, fc.FastChargeAdapter)
    check_protocol(adapter)
    executors = {a.name: a.executor for a in adapter.spec.actions}
    assert executors == {
        "simulate_protocol": "computational",
        "calibrate_thermal": "computational",
        "physical_confirmation": "human",
    }
    assert adapter.spec.solver["version"] == fc.PYBAMM_VERSION
    assert adapter.spec.action("simulate_protocol").deterministic


def test_the_api_process_never_imports_the_solver():
    """The registry and adapter load without PyBaMM; only the child process imports it."""
    code = (
        "import sys; from translucent import systems; a = systems.get('pybamm-fastcharge'); "
        "a.baseline_policy().propose([], 3); a.check_params('simulate_protocol', "
        f"{BASELINE!r}); print(sorted(m for m in ('pybamm', 'casadi') if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def test_bounds_are_refused_in_plain_words(adapter):
    check = adapter.check_params
    assert check("simulate_protocol", BASELINE) == []
    assert check("simulate_protocol", protocol(0.2, 0.25, 1.45, 1.5)) == []
    [above] = check("simulate_protocol", protocol(1.6, 1.0, 1.0, 1.0))
    assert "c_rate_1 = 1.6C" in above and "above 1.5C" in above and "validation records" in above
    [below] = check("simulate_protocol", protocol(1.0, 1.0, 1.0, 0.15))
    assert "c_rate_4 = 0.15C" in below and "below 0.2C" in below and "62.5-80 %" in below
    [grid] = check("simulate_protocol", protocol(1.0, 0.33, 1.0, 1.0))
    assert grid == "c_rate_2 = 0.33C is not on the 0.05C grid; use 0.30 or 0.35"
    [hot] = check("simulate_protocol", protocol(1.0, 1.0, 1.0, 1.0, ambient=50.0))
    assert "ambient = 50.0 degC is outside 5-40 degC" in hot
    assert check("simulate_protocol", {k: v for k, v in BASELINE.items() if k != "ambient"}) == [
        "Missing parameter ambient"
    ]
    for bad in (True, float("nan"), "1.5", None):
        assert check("simulate_protocol", {**BASELINE, "c_rate_3": bad}) == ["c_rate_3 must be a number"]
    assert check("calibrate_thermal", {}) == []
    assert check("calibrate_thermal", {"h": 6}) == ["Unknown parameter h"]
    assert check("physical_confirmation", protocol(2.0, 1.0, 1.0, 1.0))
    assert check("no_such_action", {}) == ["Unknown action no_such_action"]


def test_grid_values_canonicalise_without_moving(adapter):
    near = protocol(0.35000000001, 1.0, 1.0, 1.0, ambient=24.123)
    assert adapter.check_params("simulate_protocol", near) == []
    config = adapter.configuration("simulate_protocol", near)
    assert config == {"action": "simulate_protocol", **protocol(0.35, 1.0, 1.0, 1.0, ambient=24.123)}
    tol = {p.name: p.tolerance for p in adapter.spec.action("simulate_protocol").parameters}
    assert abs(config["c_rate_1"] - near["c_rate_1"]) <= tol["c_rate_1"]
    assert config["ambient"] == near["ambient"]  # never rounded: computational tolerance is 0


def test_validity_labels_extrapolation_and_manufacturer_rating(adapter):
    inside = adapter.validity("simulate_protocol", protocol(0.3, 0.3, 0.3, 0.3))
    assert inside["label"] == "within_range"
    assert inside["notes"] == ["Computational result from a model, not a measurement."]
    cold = adapter.validity("simulate_protocol", protocol(1.5, 1.5, 1.35, 0.85, ambient=15.0))
    assert cold["label"] == "extrapolation"
    assert any(n.startswith("Extrapolation: ambient 15 degC") for n in cold["notes"])
    assert any("manufacturer's 0.7C charge rating" in n for n in cold["notes"])
    assert any("No record holds a charge faster than 0.3C" in n for n in cold["notes"])
    assert adapter.validity("simulate_protocol", protocol(0.7, 0.7, 0.7, 0.7, ambient=27.0))["label"] == "within_range"
    assert adapter.validity("calibrate_thermal", {})["label"] == "within_range"


def test_costs_are_wall_time_and_zero_dollars(adapter):
    sim = adapter.estimate_cost("simulate_protocol", BASELINE)
    assert isinstance(sim, Cost) and sim.usd == 0 and 0 < sim.wall_s < adapter.spec.action("simulate_protocol").max_wall_s
    cold = adapter.estimate_cost("simulate_protocol", {**BASELINE, "ambient": 10.0})
    assert cold.wall_s > sim.wall_s
    cal = adapter.estimate_cost("calibrate_thermal", {})
    assert cal.usd == 0 and 0 < cal.wall_s < adapter.spec.action("calibrate_thermal").max_wall_s
    person = adapter.estimate_cost("physical_confirmation", protocol(0.7, 0.7, 0.7, 0.7))
    assert person.specimens == 1 and person.person_minutes == 60 + 120 + 30


def test_physical_request_procedure_states_values_limits_and_approval(adapter):
    text = adapter.procedure("physical_confirmation", protocol(1.5, 1.5, 1.05, 0.8))
    assert "1.5C from 10 to 27.5 %" in text and "1.05C to 62.5 %" in text and "0.8C to 80 %" in text
    assert "exceeds 50 degC" in text and "requires engineer approval" in text
    gentle = adapter.procedure("physical_confirmation", protocol(0.7, 0.7, 0.5, 0.3))
    assert "approval" not in gentle and "{" not in gentle
    assert adapter.procedure("simulate_protocol", BASELINE) is None


def test_execute_refuses_people_work_and_bad_parameters_without_a_child(adapter, tmp_path):
    with pytest.raises(NotExecutable):
        adapter.execute("physical_confirmation", BASELINE, tmp_path)
    with pytest.raises(ValueError, match="above 1.5C"):
        adapter.execute("simulate_protocol", {**BASELINE, "c_rate_2": 2.0}, tmp_path / "x")
    assert not (tmp_path / "x").exists()


def solver_like_table(t80=28.2, tmax=51.19, phi=7.3, status="ok", temps=(25.0, 40.0, 51.19)):
    return ResultTable(
        time_unit="s",
        t=(0.0, 800.0, 1700.0),
        series={
            "cell_temperature": ("degC", temps),
            "anode_potential": ("mV", (60.0, 20.0, phi)),
            "voltage": ("V", (3.6, 4.0, 4.2)),
        },
        scalars={
            "status": (status, ""),
            "charge_time_80": (t80, "min"),
            "max_temperature": (tmax, "degC"),
            "min_anode_potential": (phi, "mV"),
            "max_voltage": (4.2, "V"),
        },
    )


def test_result_checks_catch_inconsistent_solver_output(adapter):
    assert adapter.check_result("simulate_protocol", BASELINE, solver_like_table()) == []
    assert adapter.check_result("simulate_protocol", BASELINE, solver_like_table(tmax=50.0)) == [
        "max_temperature differs from the cell_temperature series"
    ]
    assert "did not reach 80 %" in adapter.check_result(
        "simulate_protocol", BASELINE, solver_like_table(status="not_reached")
    )[0]
    assert "sanity checks" in adapter.check_result(
        "simulate_protocol", BASELINE, solver_like_table(status="nonsense")
    )[0]
    assert adapter.check_result("simulate_protocol", BASELINE, solver_like_table(t80=40.0)) == [
        "the time series stops before the charge reached 80 %"
    ]
    # t80 is rounded to 0.001 min, so it may sit a fraction of a second past the last point
    assert adapter.check_result("simulate_protocol", BASELINE, solver_like_table(t80=1700.02 / 60)) == []
    cold = solver_like_table(temps=(23.0, 40.0, 51.19))
    assert adapter.check_result("simulate_protocol", BASELINE, cold) == [
        "the cell temperature fell more than 1 K below ambient"
    ]
    fit = ResultTable(scalars={"heat_transfer_coefficient": (6.0, "W/m^2/K")})
    assert adapter.check_result("calibrate_thermal", {}, fit) == []
    bad_fit = ResultTable(scalars={"heat_transfer_coefficient": (0.2, "W/m^2/K")})
    assert adapter.check_result("calibrate_thermal", {}, bad_fit)


def test_feasibility_rule_keeps_the_solver_bounds():
    ok = {"status": "ok", "charge_time_80": 33.55, "min_anode_potential": 12.3, "max_temperature": 44.89}
    assert fc.feasible(ok)
    assert not fc.feasible({**ok, "max_temperature": 44.98})  # within 0.1 K of 45: marginal
    assert not fc.feasible({**ok, "min_anode_potential": 10.4})  # within 0.5 mV of +10: marginal
    assert not fc.feasible(ok, margin_mV=25.0)
    assert not fc.feasible({**ok, "status": "not_reached"})
    assert not fc.feasible({k: v for k, v in ok.items() if k != "charge_time_80"})


def test_result_hash_is_canonical_and_excludes_wall_time():
    a = {"b": [1.0, 2.5], "a": {"y": "ok", "x": 0.1}}
    b = json.loads(json.dumps({"a": {"x": 0.1, "y": "ok"}, "b": [1.0, 2.5]}))
    assert fc.result_hash(a) == fc.result_hash(b) == hashlib.sha256(fc.canonical_json(a)).hexdigest()
    assert fc.result_hash({**a, "b": [1.0, 2.5000001]}) != fc.result_hash(a)
    with pytest.raises(ValueError):
        fc.canonical_json({"x": float("nan")})


# --- the constrained Bayesian-optimisation baseline ----------------------------------------


def surrogate(params):
    """Analytic stand-in for the solver: time falls with current, heat and plating risk rise."""
    c = [params[k] for k in fc.C_RATES]
    t80 = sum(10.5 / x for x in c)
    tmax = params["ambient"] + 6.0 * sum(x * x for x in c) / 4 + 4.0 * c[3]
    phi = 45.0 - 12.0 * c[3] - 8.0 * c[2] - 2.0 * (c[0] + c[1])
    return {"status": "ok", "charge_time_80": t80, "max_temperature": tmax, "min_anode_potential": phi}


def run_policy(policy, budget=8, history=None, change_at=None):
    history = list(history or [])
    while (used := sum("action" in h for h in history)) < budget:
        if change_at is not None and used == change_at:
            history.append({"constraints": {"min_anode_potential": 25.0}})
        batch = policy.propose(history, budget - used)
        assert batch, "the policy stopped proposing before the budget"
        for action, params in batch:
            history.append({"action": action, "params": params, "result": surrogate(params)})
    return history


def test_baseline_policy_is_seeded_bo_with_three_sobol_points(adapter):
    policy = adapter.baseline_policy()
    assert isinstance(policy, fc.ConstrainedBO) and policy.name == "constrained-bo"
    first = policy.propose([], 8)
    assert len(first) == 3 and first == adapter.baseline_policy().propose([], 8)
    assert len({tuple(p.values()) for _, p in first}) == 3
    for action, params in first:
        assert action == "simulate_protocol" and adapter.check_params(action, params) == []
        assert params["ambient"] == 25.0
    assert policy.propose([], 2) == first[:2]
    assert policy.propose([], 0) == []
    assert adapter.baseline_policy(seed=1).propose([], 3) != first
    assert adapter.baseline_policy(ambient=15.0).propose([], 3)[0][1]["ambient"] == 15.0


def test_baseline_campaign_is_deterministic_in_budget_and_never_repeats(adapter):
    started = time.perf_counter()
    a = run_policy(adapter.baseline_policy(seed=3))
    elapsed = time.perf_counter() - started
    b = run_policy(adapter.baseline_policy(seed=3))
    assert a == b and len(a) == 8
    points = [tuple(h["params"][k] for k in fc.C_RATES) for h in a]
    assert len(set(points)) == 8
    assert all(adapter.check_params(h["action"], h["params"]) == [] for h in a)
    assert elapsed < 30, f"eight proposals took {elapsed:.1f} s"
    # the BO steps find feasible protocols on the surrogate, and better than the Sobol starts
    feasible = [h["result"]["charge_time_80"] for h in a if fc.feasible(h["result"])]
    assert feasible and min(feasible) < min(
        (h["result"]["charge_time_80"] for h in a[:3] if fc.feasible(h["result"])), default=1e9
    )


def test_baseline_reads_a_changed_margin_from_history(adapter):
    prefix = run_policy(adapter.baseline_policy(seed=0), budget=4)
    plain = adapter.baseline_policy(seed=0).propose(prefix, 4)
    changed = adapter.baseline_policy(seed=0).propose(prefix + [{"constraints": {"min_anode_potential": 25.0}}], 4)
    assert plain != changed
    [(_, params)] = changed
    assert surrogate(params)["min_anode_potential"] >= 25.0
    # a run that failed (no result) counts as tried and adds no data
    failed = prefix + [{"action": "simulate_protocol", "params": changed[0][1], "result": None}]
    assert adapter.baseline_policy(seed=0).propose(failed, 3) != changed


# --- records: fetch script and import manifest ----------------------------------------------


def test_fetch_script_pins_the_records_the_adapter_reads():
    assert set(fetch_lgm50.FILES) == set(fc.RECORDS)
    assert fetch_lgm50.DEST == fc.DATA
    for size, digest in fetch_lgm50.FILES.values():
        assert size > 0 and re.fullmatch(r"[0-9a-f]{64}", digest)
    assert fetch_lgm50.URL.startswith("https://zenodo.org/records/4032561/files/")
    assert "data/raw/" in (ROOT / ".gitignore").read_text()


def small(tmp_path, data):
    source = tmp_path / "published.csv"
    source.write_bytes(data)
    return {"x.csv": (len(data), hashlib.sha256(data).hexdigest())}, source.as_uri().replace("published.csv", "{name}")


def test_fetch_checks_then_keeps(tmp_path):
    files, url = small(tmp_path, b"published bytes")
    url = url.replace("{name}", "published.csv")
    line = fetch_lgm50.fetch("x.csv", tmp_path / "raw", files, url)
    assert "downloaded 15 bytes, checksum ok" in line
    assert sorted(p.name for p in (tmp_path / "raw").iterdir()) == ["x.csv"]
    assert "already present" in fetch_lgm50.fetch("x.csv", tmp_path / "raw", files, url)


def test_fetch_refuses_a_mismatch_and_never_writes_it(tmp_path):
    files, url = small(tmp_path, b"published bytes")
    (tmp_path / "published.csv").write_bytes(b"tampered bytes!")  # same size, other bytes
    with pytest.raises(ValueError, match="not the published file"):
        fetch_lgm50.fetch("x.csv", tmp_path / "raw", files, url.replace("{name}", "published.csv"))
    assert list((tmp_path / "raw").iterdir()) == []


def test_fetch_refuses_to_overwrite_a_different_local_file(tmp_path, capsys):
    files, url = small(tmp_path, b"published bytes")
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "x.csv").write_bytes(b"edited copy")
    with pytest.raises(ValueError, match="sha256 differs"):
        fetch_lgm50.fetch("x.csv", tmp_path / "raw", files, url)
    assert (tmp_path / "raw" / "x.csv").read_bytes() == b"edited copy"
    with pytest.raises(SystemExit) as stop:
        fetch_lgm50.main(["LGM50_cell09.csv"])
    assert stop.value.code == 2 and "choose from" in capsys.readouterr().err


# Field sets of the design's import v2 models (extra="forbid"), so a typo is caught here.
MANIFEST_KEYS = {"manifest_version", "id", "title", "author", "citation", "licence", "system", "records", "notes"}
RECORD_KEYS = {"id", "role", "specimen", "configuration", "configuration_units", "conditions",
               "conditions_units", "time", "tables", "scalars", "media", "provenance"}
TABLE_KEYS = {"file", "format", "delimiter", "header_row", "skip_rows", "decimal", "time_column", "columns"}
TIME_KEYS = {"unit", "origin", "offset_s", "source", "uncertainty_s"}
PROVENANCE_KEYS = {"source", "instrument", "operator", "acquired_at", "notes"}
CHANNEL_KEYS = {"quantity", "unit", "uncertainty", "label"}


def test_manifest_parses_with_the_fields_import_v2_names():
    m = json.loads(MANIFEST.read_text())
    assert set(m) <= MANIFEST_KEYS and m["manifest_version"] == 2
    assert re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", m["id"])
    assert all(isinstance(m[k], str) and m[k] for k in ("title", "author", "citation", "licence"))
    assert m["licence"] == "CC BY 4.0" and "10.5281/zenodo.4032561" in m["citation"]
    assert m["system"] == fc.SYSTEM_ID
    quantities = {q.name: q for q in fc.SPEC.quantities}
    assert 1 <= len(m["records"]) <= 500
    files = []
    for r in m["records"]:
        assert set(r) <= RECORD_KEYS
        assert re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", r["id"])
        assert r["role"] in ("baseline", "history", "reference")
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", r["specimen"]["id"])
        assert set(r["provenance"]) <= PROVENANCE_KEYS and r["provenance"]["source"]
        assert set(r["configuration"]) == set(r["configuration_units"])
        assert set(r["conditions"]) == set(r["conditions_units"])
        if r["tables"]:
            assert set(r["time"]) <= TIME_KEYS and r["time"]["origin"] and r["time"]["source"]
        for table in r["tables"]:
            assert set(table) <= TABLE_KEYS and table["format"] == "csv" and 0 <= table["header_row"] <= 200
            files.append(table["file"])
            for column, channel in table["columns"].items():
                assert set(channel) <= CHANNEL_KEYS
                name = channel["quantity"]
                assert name.startswith("lab:") or name in quantities, name
                if name in quantities:
                    assert channel["unit"] == quantities[name].unit
    assert sorted(files) == sorted(fc.RECORDS)
    [baseline] = [r for r in m["records"] if r["role"] == "baseline"]
    assert fc.check_params("simulate_protocol", baseline["configuration"]) == []
    assert baseline["configuration"] == BASELINE


@pytest.mark.skipif(not all((fc.DATA / n).exists() for n in fc.RECORDS), reason="records not fetched")
def test_manifest_columns_exist_in_the_fetched_records():
    m = json.loads(MANIFEST.read_text())
    for r in m["records"]:
        for table in r["tables"]:
            path = fc.DATA / table["file"]
            assert fetch_lgm50.sha256(path) == fetch_lgm50.FILES[table["file"]][1]
            lines = path.read_text(encoding="latin-1").splitlines()
            header = lines[table["header_row"]].split(",")
            assert table["time_column"] in header
            assert set(table["columns"]) <= set(header)
