"""The records-intake acceptance harness (evals/records_intake.py) offline: the synthetic re-export, the seals, the
three arms end to end with the lumped stand-in solver (tests/support/lab_fake_solver.py) and scripted model clients,
the freeze and the live gate. No PyBaMM, no model, no sandbox."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from evals import records_intake as R
from translucent import datasets, systems
from translucent.ledger import Ledger
from translucent.loop_planner import ScriptedChatClient, ScriptedLoopPlanner
from translucent.sandbox.runner import RunResult
from translucent.systems import lgm50_lab as lab

TESTS = Path(__file__).resolve().parent

pytestmark = pytest.mark.skipif(not (R.RAW / "LGM50_cell02.csv").is_file(),
                                reason="the Chen 2020 records are not in data/raw")


@pytest.fixture(scope="module")
def fake():
    with pytest.MonkeyPatch.context() as mp:
        systems.available()
        mp.setattr(systems, "_ADAPTERS", dict(systems._ADAPTERS))
        mp.setenv("PYTHONPATH", os.pathsep.join(filter(None, [str(TESTS), os.environ.get("PYTHONPATH", "")])))
        import support.lab_fake_solver as F
        F.register()
        yield F


def test_the_synthetic_reexport_holds_the_published_values(tmp_path):
    case = R.CASES["H3"]
    text = R.reexport("cell02").decode()
    lines = text.splitlines()
    assert lines[0].startswith("Logger export;") and lines[5] == "elapsed [s];step;mode;I;U;Q;T1;T2"
    assert ";" in lines[6] and "." not in lines[6]
    pub, syn = Ledger(tmp_path / "a"), Ledger(tmp_path / "b")
    try:
        files = {n: R._write(tmp_path / "files", n, b) for n, b in R.case_files(case).items()}
        one = R.reference_manifest(case, ("cell02",))
        got = datasets.import_dataset(syn, one, {k: v for k, v in files.items() if k in datasets.file_names(one)})
        plain = R.Case("P", ("cell02",), "cell03")
        want = datasets.import_dataset(pub, R.reference_manifest(plain), {
            n: R._write(tmp_path / "pub", n, b) for n, b in R.case_files(plain).items()})
        for q in lab.RECORD_QUANTITIES:
            a = lab._record_arrays(syn, got.records[0], q)
            b = lab._record_arrays(pub, want.records[0], q)
            assert a[1] == b[1] and max(abs(x - y) for x, y in zip(a[2], b[2])) < 1e-6, q
    finally:
        pub.db.close()
        syn.db.close()


def test_numeric_arm_on_the_development_case(fake, tmp_path):
    info = R.run_one(tmp_path, "D0", "numeric", 1, log=lambda *_: None)
    run = tmp_path / "D0-numeric-1"
    m = json.loads((run / "metrics.json").read_text())
    assert info["stop"] == "policy finished" and m["steps_completed"] == m["steps_total"]
    assert m["record_tests_reference"] == 3 and m["imports_refused"] == 0
    assert all(v["surface_temperature"]["max_abs_diff"] == 0 for v in m["import_correctness"].values())
    # primary: the recommended design (None when no model has standing); secondary: the last design found
    assert m["last_design"]["c_rates"] and "true_t80_min" in m["assessment_last"]
    last = m["last_design_standing"]
    assert last["model_version"] == m["last_design"]["model_version"] and isinstance(last["supported"], bool)
    if m["final_design"] is None:
        assert m["recommended"] is False and m["final_design_standing"] is None
        assert m["assessment"]["note"] == "no design" and last["supported"] is False
    else:
        assert m["final_design_standing"]["supported"] is True and "true_t80_min" in m["assessment"]
        assert m["recommended_design"]["c_rates"] == m["final_design"]["c_rates"]
    assert m["overclaim"] is False
    # D0's lab is a hand-set development condition: no scored case's seal is seen while the wiring is fixed
    seal = json.loads((run / "revealed-condition.json").read_text())
    assert (seal["h"], seal["k"]) == R.CASES["D0"].dev_seal and seal["cell"] is None
    assert not (tmp_path / "seals").exists() or not list((tmp_path / "seals").glob("cell*.json"))
    assert lab.H_BOUNDS[0] <= seal["h"] <= lab.H_BOUNDS[1]
    assert {(R.CASES[c].withheld) for c in ("H1", "H2", "H3")} == {"cell02", "cell03", "cell04"}


def test_scored_cases_need_the_freeze_and_muse_arms_need_live(fake, tmp_path):
    with pytest.raises(R.HarnessError, match="only after `freeze`"):
        R.run_one(tmp_path, "H1", "numeric", 1)
    with pytest.raises(R.HarnessError, match="--live"):
        R.run_one(tmp_path, "D0", "general_meta", 1)
    (tmp_path / "freeze.json").write_text(json.dumps({**R.freeze_digest(), "files": {}, "sha256": "0"}))
    with pytest.raises(R.HarnessError, match="changed since the freeze"):
        R.run_one(tmp_path, "H1", "numeric", 1)


def test_the_freeze_covers_code_data_and_seals_and_registers_the_runs(tmp_path):
    files = R.frozen_files()
    for f in ("translucent/runtime.py", "translucent/executors.py", "translucent/systems/base.py",
              "translucent/loop_compare.py", "evals/records_intake.py", "data/raw/chen2020-lgm50/LGM50_cell03.csv",
              "data/chen2020-lgm50/manifest.json"):
        assert f in files, f
    out = tmp_path / "eval"
    with pytest.raises(R.HarnessError, match="run `prepare` first"):
        R.freeze(out, ["H1"], ["numeric"], [1])
    (out / "seals").mkdir(parents=True)
    (out / "seals" / "cell04.json").write_text(json.dumps({"h": 5.7, "k": 1.2}))
    R.freeze(out, ["H1"], ["numeric", "general_meta"], [1])
    assert R.check_frozen(out, "H1-numeric-1") and R.check_frozen(out, "H1-general_meta-1")
    with pytest.raises(R.HarnessError, match="not in the run list"):
        R.check_frozen(out, "H1-general_meta-2")
    with pytest.raises(R.HarnessError, match="already frozen"):
        R.freeze(out, ["H1"], ["numeric"], [1])
    (out / "seals" / "cell04.json").write_text(json.dumps({"h": 9.9, "k": 1.2}))     # a seal changed after
    with pytest.raises(R.HarnessError, match="seals/cell04.json"):
        R.check_frozen(out, "H1-numeric-1")


def test_translucent_arm_runs_through_drive_with_a_scripted_client(fake, tmp_path):
    case = R.CASES["D0"]
    name = R.file_name(case, "cell02")
    script = ScriptedLoopPlanner([
        [("inspect_upload", {"file": name, "lines": 16}),
         ("import_records", {"manifest": R.reference_manifest(case), "missing_information": [
             {"item": "start state", "why_it_matters": "record tests"}]}),
         ("list_segments", {"record": 1})],
        [("conclude", {"text": "Stopped before any test, for the harness test."})]])
    info = R.run_one(tmp_path, "D0", "translucent_meta", 1, live=True, cap_usd=1.0,
                     client_factory=lambda: ScriptedChatClient(script), log=lambda *_: None)
    m = json.loads((tmp_path / "D0-translucent_meta-1" / "metrics.json").read_text())
    assert info["stop"] == "concluded" and m["steps"]["import_accepted"] and m["steps"]["read_files"]
    assert m["final_design"] is None and m["assessment"]["feasible"] is False and m["model_usd"] > 0


def test_general_agent_does_the_work_with_plain_functions(fake, tmp_path):
    case = R.CASES["D0"]
    name = R.file_name(case, "cell02")
    seal = R.seal_for(tmp_path, case)
    probe = R.GeneralAgent(tmp_path / "probe", case, seal)
    cols = {"current": {"column": "Current [A]", "unit": "A"}, "voltage": {"column": "Voltage [V]", "unit": "V"},
            "surface_temperature": {"column": "Temperature Cell [degC]", "unit": "degC"},
            "chamber_temperature": {"column": "Temperature Chamber [degC]", "unit": "degC"}}
    table = {"name": "c2", "file": name, "header_row": 13, "time_column": "Test Time [s]", "columns": cols,
             "nominal_capacity_Ah": 5.0}
    probe.t_define_table(**table)
    # the short listing names every reference discharge within the tool's cut; the full detail comes in pages
    short = json.dumps(probe.t_segments("c2"))
    assert len(short) < R.TOOL_CHARS and short.count("constant current") >= 3
    every, start = [], 1
    while start:
        page = probe.t_segments("c2", detail=True, start=start)
        assert len(json.dumps(page)) < R.TOOL_CHARS
        every += page["segments"]
        start = page.get("next_start")
    assert [s["segment"] for s in every] == list(range(1, page["of"] + 1))
    segs = [s for s in every if s["kind"] == "constant current" and
            s.get("voltage_trend") == "falling" and (s.get("after") or {}).get("voltage_end_V", 0) >= 4.1 and
            any(abs(s["rate_per_nominal_capacity"] - r) < 0.01 for r in (0.5, 1.5))]
    cut = [("cut_test", {"table": "c2", "t_start_s": s["t_start_s"], "t_end_s": s["t_end_s"], "mode": "discharge",
                         "start_soc": 1.0}) for s in segs]
    rates = {c: 1.0 for c in ("c_rate_1", "c_rate_2", "c_rate_3", "c_rate_4")}
    seen = []

    def sandbox(code, inputs, timeout_s):
        seen.append(sorted(inputs))
        return RunResult(status="ok", exit_code=0, stdout="done")
    script = ScriptedLoopPlanner([
        [("read_file", {"file": name, "lines": 15}), ("column_stats", {"file": name, "header_row": 13}),
         ("run_analysis", {"code": "print(1)"})],
        [("define_table", table), ("segments", {"table": "c2"})],
        cut,
        [("fit_thermal", {"family": 2, "tests": [1, 2]}), ("check_model", {"model": 1, "tests": [1]}),
         ("optimize_protocol", {"ambient": 25.0, "model": 1})],
        [("lab_charge", {**rates, "ambient": 25.0, "rest_min": 60.0})],
        [("fit_thermal", {"family": 2, "tests": [1, 2, 3]}), ("optimize_protocol", {"ambient": 25.0, "model": 2})],
        [("conclude", {"design": 2, "text": "Design 2, refitted on the lab test."})]])
    info = R.run_one(tmp_path, "D0", "general_meta", 1, live=True, cap_usd=1.0,
                     client_factory=lambda: ScriptedChatClient(script), analysis_runner=sandbox,
                     log=lambda *_: None)
    m = json.loads((tmp_path / "D0-general_meta-1" / "metrics.json").read_text())
    calls = json.loads((tmp_path / "D0-general_meta-1" / "agent" / "calls.json").read_text())
    assert info["stop"] == "concluded", calls
    errors = [c for c in calls if c["error"]]
    assert [c["tool"] for c in errors] == ["check_model"], errors      # model 1 fitted test 1: refused
    assert seen == [[name]]
    assert m["steps"]["ingested_and_revisited"] and m["final_design"]["model_version"] == 2
    # the general agent's table is scored against the reference import, matched by file, as arm 1's records are
    row = m["import_correctness"]["lgm50-cell02"]
    assert all(row[q]["max_abs_diff"] == 0 for q in ("current", "voltage", "surface_temperature")), row
    assert m["overclaim"] is True and m["recommended"] is True
    assert m["assessment"]["design"]["c_rates"] == m["final_design"]["c_rates"]
    # model 2 was never checked on a test it did not fit: the general agent's recommendation has no standing
    assert m["final_design_standing"] == {"model_version": 2, "supported": False,
                                          "reason": "model version 2 has not been checked on tests it did not fit"} \
        or m["final_design_standing"]["supported"] is False
    assert "| no |" in R.markdown({"runs": [m], "seals": {}})
    assert json.loads((tmp_path / "D0-general_meta-1" / "agent-spend.json").read_text())["spent_usd"] > 0
    assert R.RecordsCap(tmp_path, 1.0).spent() >= info["spent_usd"]
    data = R.report(tmp_path, tmp_path / "report.md")
    assert len(data["runs"]) == 1 and "SIMULATED" in (tmp_path / "report.md").read_text()
