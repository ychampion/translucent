"""An investigation from the engineer's own records, end to end without PyBaMM or a model: the uploads, the planner's
records tools (read, import with a manifest it writes, list segments), record tests cut from a real Maccor export
(Chen 2020, cell 02), a fit and a held-out check on them, a design, a lab test in the simulated lab and a refit, all
through LoopService, LoopRunner and the planner's own turn loop with scripted replies.

The solver child is the real lgm50_lab_solver logic over the lumped stand-in (tests/support/lab_fake_solver.py)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from translucent import chain, records, systems
from translucent.ledger import Ledger
from translucent.loop import current_objective, request_states
from translucent.loop_planner import (ScriptedLoopPlanner, offered_tools, planner_context, run_turn,
                                      system_prompt, tool_schemas)
from translucent.loop_runner import LoopRunner
from translucent.loop_service import Actor, LoopService
from translucent.systems import lgm50_fastcharge as fc
from translucent.systems import lgm50_lab as lab

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
CELL = REPO / "data" / "raw" / "chen2020-lgm50" / "LGM50_cell02.csv"
ANA = Actor("person", "Ana", "http")
DOC = ("Maccor exports of LG M50 validation tests, one file per cell. Columns: Test Time [s], Current [A] "
       "(magnitude; the Md column gives C, D or R), Voltage [V], Temperature Cell [degC] (thermocouple on the can), "
       "Temperature Chamber [degC]. Discharges at C/2, 1C and 1.5C from full after a CC-CV charge.")

pytestmark = pytest.mark.skipif(not CELL.is_file(), reason="the Chen 2020 records are not in data/raw")


@pytest.fixture(scope="module")
def fake():
    with pytest.MonkeyPatch.context() as mp:
        systems.available()
        mp.setattr(systems, "_ADAPTERS", dict(systems._ADAPTERS))
        mp.setenv("PYTHONPATH", os.pathsep.join(filter(None, [str(TESTS), os.environ.get("PYTHONPATH", "")])))
        import support.lab_fake_solver as F
        F.register()
        yield F


@pytest.fixture
def L(fake, tmp_path):
    led = Ledger(tmp_path / "ledger")
    yield led
    led.db.close()


def objective(uploads, **kw) -> dict:
    t = lab.OBJECTIVE_TEMPLATE
    return {"statement": t["statement"]["fastest_feasible"], "system": {"id": lab.SYSTEM_ID}, "datasets": [],
            "baseline": [], "target": {k: t["target"][k] for k in ("quantity", "unit", "direction")},
            "constraints": [dict(c) for c in t["constraints"]], "quantities": [dict(q) for q in t["quantities"]],
            "budget": {"model_usd": 0.0, "experiments": 40, "experiment_usd": 0.0, "lab_tests": 2},
            "planner": {"kind": "scripted", "profile": None, "client": None},
            "auto_approve": [{"executor": "computational", "max_usd": 0.0, "max_wall_s": 900.0, "max_count": 40}],
            "registered_by": "Ana", "evaluator": "loop-compare/3", "kind": "fastest_feasible",
            "uploads": uploads, "documentation": DOC} | kw


def open_records(L) -> str:
    files = {CELL.name: CELL.read_bytes()}
    entries = records.describe(files)
    cid = LoopService(L, ANA).open_campaign(objective(entries), "From records")["campaign_id"]
    records.stage_uploads(L.root, cid, files, expected=entries)
    return cid


def manifest(header_row=13, chamber=True) -> dict:
    cols = {"Voltage [V]": {"quantity": "voltage", "unit": "V"},
            "Temperature Cell [degC]": {"quantity": "surface_temperature", "unit": "degC",
                                        "label": "thermocouple on the can"},
            "Current [A]": {"quantity": "current", "unit": "A", "label": "magnitude; Md gives the direction"}}
    if chamber:
        cols["Temperature Chamber [degC]"] = {"quantity": "chamber_temperature", "unit": "degC"}
    return {"manifest_version": 2, "id": "lgm50-cells", "title": "LG M50 validation", "author": "the lab",
            "citation": "engineer's upload", "licence": "internal",
            "records": [{"id": "cell02", "specimen": {"id": "lgm50-cell02", "properties": {"nominal_capacity_Ah": 5.0}},
                         "time": {"unit": "s", "origin": "start of the Maccor test", "source": "Test Time [s]"},
                         "tables": [{"file": CELL.name, "header_row": header_row, "time_column": "Test Time [s]",
                                     "columns": cols}],
                         "provenance": {"source": "Maccor export"}}]}


MISSING = [{"item": "thermocouple calibration", "why_it_matters": "the fit's noise floor"}]
RMSE = {"thermal_fit_rmse": {"value": 1.0, "lo": 0.1, "hi": 3.0, "unit": "K"}}
HELDOUT = {"heldout_rmse": {"value": 1.5, "lo": 0.1, "hi": 5.0, "unit": "K"}}
EVALS = {"evaluations": {"value": 50, "lo": 10, "hi": 500, "unit": "1"}}


def call(tool, **args):
    return (tool, args)


def propose(action, params, prediction=None):
    return call("propose_experiment", action=action, params=params, hypothesis="h", prediction=prediction or {},
                rationale="r", expected_effect="e")


def results(L, cid, tool):
    return [r.data["payload"]["result"] for r in chain.records(L, cid)
            if r.type == "planner" and r.data.get("phase") == "tool_result" and r.data["payload"].get("tool") == tool]


def turn(L, cid, planner):
    out = run_turn(L, cid, planner)
    runner = LoopRunner(L.root, process="cli", workers=1)
    runner.tick(cid)
    assert runner.problems == [], runner.problems
    return out


def segment_of(L, cid, rate):
    obj = current_objective(L, cid)
    (_, rec), = records.campaign_records(L, obj, cid)
    return next(s for s in lab.record_segments(L, rec) if s["kind"] == "constant current" and
                s.get("voltage_trend") == "falling" and abs(s.get("rate_per_nominal_capacity", 0) - rate) < 0.01 and
                (s.get("after") or {}).get("voltage_end_V", 0) >= 4.1)["segment"]


def test_campaigns_without_uploads_keep_their_tools_and_prompt(L):
    cid = LoopService(L, ANA).open_campaign(objective(None), "Lab")["campaign_id"]
    obj = current_objective(L, cid)
    assert not {"inspect_upload", "import_records", "list_segments"} & set(offered_tools(obj))
    assert "own records" not in system_prompt(obj)
    assert "investigation" not in planner_context(L, cid)
    assert lab.model_versions(L, request_states(L, cid), obj)[0]["estimate"] == lab.NOMINAL


def test_an_investigation_from_a_raw_export_reaches_a_revised_design(L):
    cid = open_records(L)
    obj = current_objective(L, cid)
    names = {s["function"]["name"] for s in tool_schemas(obj)}
    assert {"inspect_upload", "import_records", "list_segments"} <= names
    assert "own records" in system_prompt(obj)
    assert lab.model_versions(L, request_states(L, cid), obj)[0]["estimate"] == lab.SHIPPED

    # turn 1: read the file, a refused manifest (header one line early), the corrected one, the first record test
    planner = ScriptedLoopPlanner([
        [call("inspect_upload", file=CELL.name, lines=16), call("inspect_upload", file=CELL.name, header_row=13),
         call("import_records", manifest=manifest(header_row=12), missing_information=MISSING)],
        [call("import_records", manifest=manifest(), missing_information=MISSING),
         call("list_segments", record=1)],
        [call("wait", reason="segments read")]])
    turn(L, cid, planner)
    seen = results(L, cid, "inspect_upload")
    assert seen[0]["lines"][13]["text"].startswith("Rec,Cycle P")
    cols = {c["column"]: c for c in seen[1]["table"]["columns"]}
    assert cols["Test Time [s]"]["never_decreases"] and {"R", "D", "C"} <= set(cols["Md"]["distinct"])
    imports = results(L, cid, "import_records")
    assert "error" in imports[0] and "Test Time [s]" in imports[0]["error"]
    assert imports[1]["dataset_id"] == "lgm50-cells" and imports[1]["records"][0]["record"] == 1
    ctx = planner_context(L, cid)
    inv = ctx["investigation"]
    assert inv["missing_information"] == MISSING and inv["documentation"] == DOC
    assert inv["instrument_facts"][0]["ref"] == "lgm50-cells/cell02"
    assert any("constant current" in row for row in inv["segments"]["1"])
    assert inv["model_assumptions"]["prior"]["h"] == lab.SHIPPED["h"]

    s05, s10, s15 = (segment_of(L, cid, r) for r in (0.5, 1.0, 1.5))
    rise = {"temperature_rise": {"value": 10.0, "lo": 1.0, "hi": 40.0, "unit": "K"}}
    rt = lambda seg, mode=1: propose("record_test", {"record": 1, "segment": seg, "mode": mode, "start_soc": 1.0}, rise)
    planner = ScriptedLoopPlanner([[rt(s05)], [rt(s10)], [rt(s15)],
                                   # a charge it is not: the voltage falls, so it is refused before anything runs
                                   [rt(s05, mode=2), propose("fit_thermal", {"family": 2, "fit_on": 3}, RMSE)],
                                   [propose("check_model", {"model_version": 1, "test_on": 4}, HELDOUT)],
                                   [propose("optimize_protocol", {"ambient": 25.0, "model_version": 1}, EVALS)]])
    for _ in range(6):
        turn(L, cid, planner)
    states = request_states(L, cid)
    done = {st.action: st for st in states.values() if st.state == "completed"}
    assert sum(st.action == "record_test" for st in states.values() if st.state == "completed") == 3
    errors = [r.get("error", "") for r in results(L, cid, "propose_experiment")]
    assert any("that is not a charge" in e for e in errors)
    fit = lab._result_json(L, done["fit_thermal"])
    assert fit["status"] == "ok" and fit["details"]["validity"]["record_tests"][0]["mode"] == "discharge"
    assert fit["details"]["nominal"]["h"] == lab.SHIPPED["h"]
    check = lab._result_json(L, done["check_model"])
    assert check["status"] == "ok" and check["details"]["tests"][0]["lab_test"] == 3
    design = lab._result_json(L, done["optimize_protocol"])
    assert design["scalars"]["design_found"]["value"] == "yes"
    rates = {c: design["scalars"][c]["value"] for c in fc.C_RATES}

    # the next experiment in the simulated lab, then a refit on records and lab test together
    lab.seal_lab(L.root, cid, 9.0, 1.3)
    planner = ScriptedLoopPlanner([
        [propose("lab_charge", {**rates, "ambient": 25.0, "rest_min": 60.0},
                 {"max_temperature": {"value": 40.0, "lo": 35.0, "hi": 45.0, "unit": "degC"}})],
        [propose("fit_thermal", {"family": 2, "fit_on": 1 + 2 + 8}, RMSE)],
        [call("conclude", text="done")]])
    for _ in range(3):
        turn(L, cid, planner)
    states = request_states(L, cid)
    tests = lab.lab_tests(states)
    assert [st.action for st in tests] == ["record_test"] * 3 + ["lab_charge"]
    fits = [st for st in states.values() if st.action == "fit_thermal" and st.state == "completed"]
    assert len(fits) == 2
    refit = lab._result_json(L, sorted(fits, key=lambda s: s.result.seq)[-1])
    assert refit["details"]["validity"]["lab_tests"] == [1, 2, 4]
    view = LoopService(L, ANA).view(cid)
    assert view["investigation"]["tests"][3]["kind"] == "lab"
    rows = view["learning"]["lab_tests"]
    assert rows[0]["record"]["mode"] == "discharge" and rows[0]["record"]["test_c_rate"] == pytest.approx(0.5, abs=0.01)
    assert rows[0]["basis"].startswith("the engineer") and "record" not in rows[3]
    assert len(view["learning"]["versions"]) == 3


def test_evidence_may_cite_the_imported_record_and_this_campaigns_requests(L):
    # the planner sees records as "dataset/record" and requests by request id: both are this campaign's evidence
    cid = open_records(L)
    rise = {"temperature_rise": {"value": 10.0, "lo": 1.0, "hi": 40.0, "unit": "K"}}
    run_turn(L, cid, ScriptedLoopPlanner([
        [call("import_records", manifest=manifest(), missing_information=MISSING)], [call("wait")]]))
    s15 = segment_of(L, cid, 1.5)
    first = dict(propose("record_test", {"record": 1, "segment": s15, "mode": 1, "start_soc": 1.0}, rise)[1],
                 evidence_ids=["lgm50-cells/cell02", "lgm50-cells"])
    run_turn(L, cid, ScriptedLoopPlanner([[("propose_experiment", first)], [call("wait")]]))
    got = results(L, cid, "propose_experiment")
    assert "error" not in got[-1], got[-1]
    rid = got[-1]["request_id"]
    s10 = segment_of(L, cid, 1.0)
    second = dict(propose("record_test", {"record": 1, "segment": s10, "mode": 1, "start_soc": 1.0}, rise)[1],
                  evidence_ids=[rid])
    other = dict(second, evidence_ids=["rq_00000000000000000000"])
    run_turn(L, cid, ScriptedLoopPlanner([[("propose_experiment", other), ("propose_experiment", second)],
                                          [call("wait")]]))
    got = results(L, cid, "propose_experiment")
    assert "not a record of this campaign" in got[-2].get("error", "")       # an id from nowhere is still refused
    assert "error" not in got[-1], got[-1]


def test_a_record_without_a_chamber_log_or_documented_ambient_is_refused(L):
    cid = open_records(L)
    planner = ScriptedLoopPlanner([
        [call("import_records", manifest=manifest(chamber=False), missing_information=MISSING),
         propose("record_test", {"record": 1, "segment": 27, "mode": 1, "start_soc": 1.0},
                 {"temperature_rise": {"value": 10.0, "lo": 1.0, "hi": 40.0, "unit": "K"}})],
        [call("wait")]])
    run_turn(L, cid, planner)
    errors = [r.get("error", "") for r in results(L, cid, "propose_experiment")]
    assert any("neither records the chamber temperature nor documents it" in e for e in errors)


def test_inspect_reads_only_this_campaigns_unchanged_uploads(L):
    cid = open_records(L)
    obj = current_objective(L, cid)
    with pytest.raises(ValueError, match="no uploaded file"):
        records.upload_path(L.root, cid, obj.uploads, "manifest.json")
    path = records.upload_dir(L.root, cid) / CELL.name
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="changed after it was uploaded"):
        records.upload_path(L.root, cid, obj.uploads, CELL.name)


def test_run_analysis_gets_only_the_campaigns_uploads_and_returns_text(L, monkeypatch):
    from translucent import loop_planner
    from translucent.sandbox.runner import RunResult
    seen = []

    def runner(code, inputs, timeout_s):
        seen.append((code, sorted(inputs), timeout_s))
        return RunResult(status="ok", exit_code=0, stdout="rows 1000\n", outputs={"summary.txt": b"ok"},
                         input_sha256={n: "x" for n in inputs})
    monkeypatch.setattr(loop_planner, "ANALYSIS_RUNNER", runner)
    cid = open_records(L)
    planner = ScriptedLoopPlanner([
        [call("run_analysis", code="print(1)", outputs=["summary.txt"], timeout_s=30),
         call("run_analysis", code="print(2)", files=["manifest.json"])],
        [call("wait")]])
    run_turn(L, cid, planner)
    first, second = results(L, cid, "run_analysis")
    assert first["stdout"] == "rows 1000\n" and first["outputs"] == {"summary.txt": "ok"}
    assert seen == [("print(1)", [CELL.name, "__outputs__.json"], 30.0)]
    assert "no uploaded file" in second["error"]
    # a changed upload is refused before anything runs
    path = records.upload_dir(L.root, cid) / CELL.name
    path.write_bytes(path.read_bytes() + b"\n")
    run_turn(L, cid, ScriptedLoopPlanner([[call("run_analysis", code="print(3)")], [call("wait")]]))
    assert "changed after it was uploaded" in results(L, cid, "run_analysis")[-1]["error"] and len(seen) == 1
