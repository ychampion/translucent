"""S4a results: the human path (a person returns a physical test's result; nothing is executed here), late,
duplicate and out-of-order results, a result days after approval across a restart, and refused solver output."""
import os
import time
from pathlib import Path

import pytest
from support.analytic_system import simulate
from support.loop_systems import PREDICTION, objective, register

from translucent import chain
from translucent.executors import ResultInvalid, child_env, decode_output
from translucent.ledger import Ledger
from translucent.loop import request_states
from translucent.loop_runner import LoopRunner
from translucent.loop_service import Actor, LoopService, ResultRefused

ANA = Actor("person", "Ana", "http")
MUSE = Actor("agent", "muse-test", "planner")


@pytest.fixture(autouse=True)
def adapter(monkeypatch):
    return register(monkeypatch)


@pytest.fixture
def L(tmp_path):
    led = Ledger(tmp_path / "ledger")
    yield led
    led.db.close()


@pytest.fixture
def cid(L):
    return LoopService(L, ANA).open_campaign(objective(), "Bench charge")["campaign_id"]


def bench_request(L, cid, specimen=None, current=2.0, approve=True):
    req = LoopService(L, MUSE).propose(cid, "cell_test", {"current_A": current, "ambient_C": 25.0},
                                       rationale="Measure the real cell at this current.", prediction=PREDICTION,
                                       expected_effect="Confirms the simulated peak temperature.",
                                       specimen=specimen if specimen is not None else {"id": "cell-07"})
    if approve:
        req = LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])
    return req


def upload(req, folder: Path, *, temp_unit="degC", current_mA=2000.0, content_hash=None, specimen="cell-07",
           notes="", name="run.csv"):
    """A complete, correct bench result for the analytic cell at 2 A / 25 degC, unless told otherwise."""
    t, y, charge = simulate(2.0, 25.0)
    path = folder / name
    path.write_text("# logger export\ntime_s,T\n" + "".join(f"{a!r},{b!r}\n" for a, b in zip(t, y)))
    body = {"request_id": req["request_id"], "content_hash": content_hash or req["content_hash"],
            "executed_configuration": {"current_A": current_mA, "ambient_C": 25.0},
            "executed_units": {"current_A": "mA", "ambient_C": "degC"}, "specimen_id": specimen,
            "table": {"file": name, "header_row": 1, "time_column": "time_s",
                      "columns": {"T": {"quantity": "temperature", "unit": temp_unit}}},
            "time": {"unit": "s", "origin": "charger switched on", "source": "logger clock"},
            "scalars": [{"quantity": "max_temperature", "value": max(y), "unit": "degC", "source": "logger peak",
                         "uncertainty": {"value": 0.5, "kind": "std", "method": "thermocouple, type T"}},
                        {"quantity": "charge_time_80", "value": charge, "unit": "s", "source": "charger log"}],
            "notes": notes, "author": "Ana"}
    return body, {name: path}


def counts(L, cid):
    types = [r.type for r in chain.records(L, cid)]
    return types.count("result"), types.count("result_rejected")


def refused(L, cid, body, files, match):
    with pytest.raises(ResultRefused, match=match) as e:
        LoopService(L, ANA).submit_result(cid, body, files)
    rec = e.value.record
    assert rec.type == "result_rejected" and rec.actor == "system:results"
    assert rec.data["submitted_by"] == "person:Ana" and rec.data["reasons"] == e.value.reasons
    return e.value


# ------------------------------------------------------------------ 6. the human path

@pytest.mark.parametrize("change, match", [
    (dict(temp_unit="V"), "temperature: cannot convert V to degC: they measure different things"),
    (dict(temp_unit="degF"), "unknown unit 'degF'"),
    (dict(current_mA=2200.0), r"current_A was 2.2 A; the approved request says 2 A \(tolerance 0.05\)"),
    (dict(specimen="cell-08"), "specimen cell-08 is not the requested specimen cell-07"),
    (dict(specimen=None), "must name the specimen tested"),
    (dict(content_hash="0" * 64), "unknown version"),
])
def test_a_wrong_upload_is_refused_recorded_and_the_request_keeps_waiting(L, cid, tmp_path, change, match):
    req = bench_request(L, cid)
    assert req["state"] == "awaiting_result" and req["procedure"].startswith("Charge the cell at 2.0 A")
    refused(L, cid, *upload(req, tmp_path, **change), match)
    view = LoopService(L, ANA).request(cid, req["request_id"])
    assert view["state"] == "awaiting_result" and len(view["rejections"]) == 1
    assert LoopService(L, ANA).submit_result(cid, *upload(req, tmp_path))["state"] == "completed"   # corrected
    assert counts(L, cid) == (1, 1) and chain.problems(L, cid) == []


def test_a_returned_table_has_the_row_cap_an_import_has_and_json_is_size_checked_before_parsing(
        L, cid, tmp_path, monkeypatch):
    """A 500 MB upload of short rows would be ~125M rows in memory: the human path refuses past datasets.MAX_POINTS
    while it streams, and a JSON table past MAX_JSON_TABLE_BYTES before json.loads."""
    import json
    from translucent import datasets
    req = bench_request(L, cid)
    body, files = upload(req, tmp_path)
    rows = len(simulate(2.0, 25.0)[0])
    monkeypatch.setattr(datasets, "MAX_POINTS", rows - 1)
    refused(L, cid, body, files, f"run.csv: more than {rows - 1:,} rows; split the file")
    t, y, _ = simulate(2.0, 25.0)
    (tmp_path / "run.json").write_text(json.dumps({"time_s": t, "T": y}))
    as_json = {**body, "table": {**body["table"], "file": "run.json", "format": "json", "header_row": 0}}
    refused(L, cid, as_json, {"run.json": tmp_path / "run.json"}, f"run.json: more than {rows - 1:,} rows")
    monkeypatch.setattr(datasets, "MAX_POINTS", rows)
    monkeypatch.setattr(datasets, "MAX_JSON_TABLE_BYTES", 1024)
    (tmp_path / "big.json").write_text("[" + "0," * 2000)     # not JSON either: parsing first would say so
    big = {**as_json, "table": {**as_json["table"], "file": "big.json"}}
    refused(L, cid, big, {"big.json": tmp_path / "big.json"}, "big.json: a JSON table can be up to 0 MB")
    assert LoopService(L, ANA).submit_result(cid, body, files)["state"] == "completed"   # at the cap: accepted


def test_a_result_naming_the_superseded_version_is_refused(L, cid, tmp_path):
    v1 = bench_request(L, cid, approve=False)
    v2 = LoopService(L, ANA).revise(cid, v1["request_id"], {"current_A": 2.0, "ambient_C": 24.0}, "chamber is 24",
                                    approve=True)
    assert v2["state"] == "awaiting_result" and v2["version"] == 2
    body, files = upload(v2, tmp_path, content_hash=v1["content_hash"])
    refused(L, cid, body, files, "the result names version 1; the approved version is 2")
    body, files = upload(v2, tmp_path)
    body["executed_configuration"]["ambient_C"] = 24.4                    # within the 1 degC tolerance of v2
    assert LoopService(L, ANA).submit_result(cid, body, files)["state"] == "completed"


def test_a_destructive_specimen_is_used_once(L, cid, tmp_path):
    first = bench_request(L, cid, specimen={"id": "cell-03"})
    LoopService(L, ANA).submit_result(cid, *upload(first, tmp_path, specimen="cell-03"))
    second = bench_request(L, cid, specimen={"batch": "lot-1"}, current=2.5)
    body, files = upload(second, tmp_path, specimen="cell-03", current_mA=2500.0)
    refused(L, cid, body, files, f"specimen cell-03 was consumed by destructive test {first['request_id']}")
    assert LoopService(L, ANA).request(cid, second["request_id"])["state"] == "awaiting_result"



def test_two_interleaved_uploads_cannot_both_consume_one_specimen(L, cid, tmp_path):
    first = bench_request(L, cid, specimen={"batch": "lot-1"})
    second = bench_request(L, cid, specimen={"batch": "lot-1"}, current=2.5)
    other, store = Ledger(L.root), L.put_file                 # another HTTP thread, its own connection

    def put_file(*a, **kw):                                   # the second upload lands while the first stores files
        if not getattr(put_file, "fired", False):
            put_file.fired = True
            LoopService(other, ANA).submit_result(cid, *upload(second, tmp_path, specimen="cell-03",
                                                               current_mA=2500.0, name="run2.csv"))
        return store(*a, **kw)
    L.put_file = put_file
    try:
        with pytest.raises(ResultRefused, match="specimen cell-03 was consumed by destructive test "
                                                f"{second['request_id']}"):
            LoopService(L, ANA).submit_result(cid, *upload(first, tmp_path, specimen="cell-03"))
    finally:
        other.db.close()
    states = request_states(L, cid)
    assert (states[first["request_id"]].state, states[second["request_id"]].state) == ("awaiting_result",
                                                                                         "completed")

def test_a_correct_upload_completes_an_identical_one_is_a_no_op_and_a_different_one_is_refused(L, cid, tmp_path):
    req = bench_request(L, cid)
    body, files = upload(req, tmp_path)
    done = LoopService(L, ANA).submit_result(cid, body, files)
    assert done["state"] == "completed" and done["result"]["basis"] == "physical"
    values = {m["quantity"]: (m["value"], m["unit"], m["h"]) for m in done["result"]["measurements"]}
    assert values["max_temperature"][1:] == ("degC", 0.5) and values["charge_time_80"][2] is None
    result = L.get("loop", done["result"]["result_id"])
    run = L.get("run", result.data["run_id"])
    assert (run.tool, run.provider) == ("human_result", "human") and run.params["author"] == "person:Ana"
    assert result.actor == "person:Ana" and result.data["attempt"] is None
    assert result.data["executed"]["configuration"] == {"current_A": 2.0, "ambient_C": 25.0}   # mA converted
    assert L.get("artifact", result.data["files"]["run.csv"]).source.uri == f"request:{req['request_id']}/run.csv"
    n_events = len(L.events(cid))

    again = LoopService(L, ANA).submit_result(cid, body, files)               # the same bytes, re-sent
    assert again == done and counts(L, cid) == (1, 0) and len(L.events(cid)) == n_events

    body2, files2 = upload(req, tmp_path, notes="second run on the same request", name="run2.csv")
    refused(L, cid, body2, files2, f"already completed by {done['result']['result_id']}; record a new request")
    assert counts(L, cid) == (1, 1)
    refused(L, cid, body2, files2, "already completed")                        # the same refusal, recorded once
    assert counts(L, cid) == (1, 1) and chain.problems(L, cid) == []


def test_an_uncertainty_stated_in_the_upload_unit_is_converted_with_its_value(L, cid, tmp_path):
    req = bench_request(L, cid)
    body, files = upload(req, tmp_path)
    charge = next(s for s in body["scalars"] if s["quantity"] == "charge_time_80")
    charge |= {"value": charge["value"] / 60.0, "unit": "min",
               "uncertainty": {"value": 1.0, "kind": "std", "method": "charger log, 1 min resolution"}}
    done = LoopService(L, ANA).submit_result(cid, body, files)
    values = {m["quantity"]: (m["value"], m["unit"], m["h"]) for m in done["result"]["measurements"]}
    assert values["charge_time_80"][1:] == ("s", 60.0)                          # 1 min, not 1 s
    assert values["max_temperature"][1:] == ("degC", 0.5)                       # an unconverted unit is untouched


# ------------------------------------------------------------------ 7. late and out-of-order results

def test_a_result_before_approval_is_refused_and_recorded(L, cid, tmp_path):
    req = bench_request(L, cid, approve=False)
    refused(L, cid, *upload(req, tmp_path), "the request is not approved")
    assert LoopService(L, ANA).request(cid, req["request_id"])["state"] == "awaiting_approval"
    assert counts(L, cid) == (0, 1)


@pytest.mark.parametrize("close", ["cancel", "reject"])
def test_a_late_result_after_cancel_or_reject_is_refused_and_recorded(L, cid, tmp_path, close):
    person = LoopService(L, ANA)
    req = bench_request(L, cid, approve=close == "cancel")
    if close == "cancel":
        person.cancel(cid, req["request_id"], "the chamber failed")
    else:
        person.reject(cid, req["request_id"], req["content_hash"], "use a fresh cell")
    seq = request_states(L, cid)[req["request_id"]].closed_by.seq
    state = "cancelled" if close == "cancel" else "rejected"
    refused(L, cid, *upload(req, tmp_path), f"the request was {state} at seq {seq}")
    assert person.request(cid, req["request_id"])["state"] == state and counts(L, cid) == (0, 1)


def test_an_upload_for_a_computational_request_is_refused(L, cid, tmp_path):
    req = LoopService(L, MUSE).propose(cid, "simulate_charge", {"current_A": 2.0, "ambient_C": 25.0},
                                       rationale="r", prediction={}, expected_effect="e")
    req = LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])
    body, files = upload(req, tmp_path)
    refused(L, cid, body, files, "is computational; its result does not come from an upload")


# ------------------------------------------------------------------ 8. days later, after a restart

def test_a_result_three_days_after_approval_across_a_restart_is_accepted(tmp_path, monkeypatch):
    root = tmp_path / "ledger"
    L = Ledger(root)
    cid = LoopService(L, ANA).open_campaign(objective(), "Bench charge")["campaign_id"]
    req = bench_request(L, cid)
    approved_at = request_states(L, cid)[req["request_id"]].approval.created_at
    L.db.close()                                                               # the server stops

    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3 * 86400)             # three days pass
    L2 = Ledger(root)                                                          # a new process opens the ledger
    try:
        assert LoopRunner(root, process="api").tick() == 0                    # nothing to repair or run
        done = LoopService(L2, ANA).submit_result(cid, *upload(req, tmp_path))
        assert done["state"] == "completed"
        result = L2.get("loop", done["result"]["result_id"])
        assert result.created_at - approved_at >= 3 * 86400 - 60
        assert chain.problems(L2, cid) == []
    finally:
        L2.db.close()


# ------------------------------------------------------------------ the solver's output is checked the same way

def test_solver_output_in_the_wrong_unit_becomes_a_failure(L, cid):
    req = LoopService(L, MUSE).propose(cid, "mislabelled_charge", {"current_A": 2.0, "ambient_C": 25.0},
                                       rationale="r", prediction={}, expected_effect="e")
    LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])
    runner = LoopRunner(L.root, process="cli", workers=1)
    assert runner.tick(cid) == 1 and runner.problems == []
    view = LoopService(L, ANA).request(cid, req["request_id"])
    assert view["state"] == "failed" and view["result"] is None
    assert "temperature: cannot convert V to degC" in view["failure"]["error"]
    assert view["failure"]["retryable"] is False


def test_a_run_past_its_wall_time_limit_becomes_a_failure(L, cid):
    req = LoopService(L, MUSE).propose(cid, "stuck_charge", {"current_A": 2.0, "ambient_C": 25.0},
                                       rationale="r", prediction={}, expected_effect="e")
    LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])
    LoopRunner(L.root, process="cli", workers=1).tick(cid)
    view = LoopService(L, ANA).request(cid, req["request_id"])
    # the child's watchdog cut the 5 s sleep (left alone, it would have completed); not the parent's backstop
    assert view["state"] == "failed" and view["result"] is None
    assert view["failure"]["error"] == "execution ran past its wall-time limit of 0.5 s"
    assert view["failure"]["retryable"] is True


def test_the_child_environment_has_no_keys_or_tokens(monkeypatch):
    # an allowlist: names no denylist foresaw (a database password, a DSN, a connection string) stay out too
    secret = ("OPENAI_API_KEY", "META_LLAMA_KEY", "TRANSLUCENT_ADMIN_TOKEN", "GITHUB_TOKEN", "MODAL_TOKEN_ID",
              "DB_PASSWORD", "FIREWORKS_ACCOUNT", "DB_PASS", "MYSQL_PWD", "SMTP_PASS", "SENTRY_DSN", "DATABASE_URL",
              "REDIS_URL", "TRANSLUCENT_TEAM_TOKEN", "PYBAMM_API_TOKEN", "OMP_SECRET")
    for k in secret:
        monkeypatch.setenv(k, "secret")
    for k in ("OMP_NUM_THREADS", "LC_ALL", "PYBAMM_DISABLE_TELEMETRY", "CI"):
        monkeypatch.setenv(k, "1")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(["/extra", ".", ""]))
    env = child_env(["/x"])
    assert not {k for k in env if k.upper() in secret} and "secret" not in env.values()
    # inherited entries absolute: the child runs in its work folder, where "." would point
    assert env["PYTHONPATH"].split(os.pathsep) == ["/x", str(Path("/extra").resolve()), str(Path.cwd())]
    assert "PATH" in {k.upper() for k in env} and env["PYTHONSAFEPATH"] == "1"   # the solver's own children too
    assert {"OMP_NUM_THREADS", "LC_ALL", "PYBAMM_DISABLE_TELEMETRY", "CI"} <= {k.upper() for k in env}
    if os.name == "nt":
        assert {"SYSTEMROOT", "TEMP"} <= {k.upper() for k in env}


def test_the_work_folder_is_not_on_the_childs_import_path(adapter, tmp_path):
    """The child runs in the work folder: a module planted there must not shadow one the child imports."""
    from translucent.executors import run_child
    work = tmp_path / "work"
    work.mkdir()
    (work / "json.py").write_text("raise SystemExit('a planted module ran')")
    body, _ = run_child(adapter, "simulate_charge", {"current_A": 2.0, "ambient_C": 25.0}, work, 30.0)
    assert body["output"]["table"]["scalars"]["max_temperature"][0] > 25


def test_output_files_must_stay_in_the_work_folder(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    work = tmp_path / "work"
    work.mkdir()
    body = {"output": {"table": {"time_unit": None, "t": [], "series": {}, "scalars": {}},
                       "files": {"leak": str(outside)}, "solver": {}, "executed": {}}}
    with pytest.raises(ResultInvalid, match="not a file in the run's work folder"):
        decode_output(body, work)
    with pytest.raises(ResultInvalid, match="malformed"):
        decode_output({"output": {"table": None}}, work)


def test_a_category_output_is_recorded_on_the_result_and_an_undeclared_one_is_refused(tmp_path, monkeypatch):
    from support.loop_systems import LABELLED_SPEC, LabelledCell

    from translucent.verify import verify
    register(monkeypatch, LabelledCell)
    L = Ledger(tmp_path / "ledger")
    cid = LoopService(L, ANA).open_campaign(objective(system={"id": LABELLED_SPEC.id}), "Labelled")["campaign_id"]
    ids = []
    for current in (2.0, 9.0):
        req = LoopService(L, MUSE).propose(cid, "labelled_charge", {"current_A": current, "ambient_C": 25.0},
                                           rationale="r", prediction=PREDICTION, expected_effect="e")
        LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])
        ids.append(req["request_id"])
    runner = LoopRunner(L.root, process="cli", workers=1)
    assert sum(runner.tick(cid) for _ in range(3)) == 2 and runner.problems == []
    states = request_states(L, cid)
    ok, garbled = states[ids[0]], states[ids[1]]
    assert ok.state == "completed"
    assert ok.result.data["categories"] == {"run_status": "ok"}
    assert "run_status" not in ok.result.data["measurements"]
    assert garbled.state == "failed"
    assert "run_status is 'garbled'; loop-test-labelled names ok, degraded" in \
        LoopService(L, ANA).request(cid, ids[1])["failure"]["error"]
    L.db.close()
    assert verify(tmp_path / "ledger").ok
