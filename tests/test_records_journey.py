"""The investigation from the engineer's records, end to end through HTTP API v1 as the web app uses it.

Integration tests of the ordinary product: the server (translucent.server.create_app) with its own loop runner and
planner threads, every person's action over /api/v1 with a real credential, a ledger directory that outlives one app
instance. The planner is the paid one the web offers for records (kind muse): the server's planner thread runs its
turns in a metered ChatSession whose provider client is replaced by ScriptedChatClient over a scripted "model"
(`Brain`), so nothing calls a provider and nothing is spent. The solver child is the real lgm50_lab_solver logic over
the lumped stand-in (tests/support/lab_fake_solver.py). Real data: the Chen 2020 Maccor export of LG M50 cell 02.

Tests commented as regression tests reproduced a product defect when written; each defect is fixed.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from support.keys import add_key, bearer

from translucent import api_v1, chain, intervention_chat, records, runtime, server
from translucent.ledger import Ledger
from translucent.loop import request_states
from translucent.loop_planner import ScriptedChatClient, ScriptedLoopPlanner, _turns, model_spend
from translucent.systems import lgm50_fastcharge as fc
from translucent.systems import lgm50_lab as lab
from translucent.verify import verify
from translucent.workspace import init_ledger

TESTS = Path(__file__).resolve().parent
RAW = TESTS.parent / "data" / "raw" / "chen2020-lgm50"
CELL = RAW / "LGM50_cell02.csv"

TEAM_TOKEN = "team-secret-0123456789ab-32chars-minimum"         # the engineer Ana (TRANSLUCENT_TEAM_TOKEN)
TEAM = bearer(TEAM_TOKEN)
ADMIN_KEY, AGENT_KEY, VIEW_KEY = "admin-ada-0123456789abcdef-key", "planner-external-agent-0123456789", \
    "viewer-0123456789abcdef"
ADMIN, AGENT, VIEW = bearer(ADMIN_KEY), bearer(AGENT_KEY), bearer(VIEW_KEY)
LID = "lab"
BASE = f"/api/v1/ledgers/{LID}"
DOC = ("Maccor exports of LG M50 validation tests, one file per cell. Columns: Test Time [s], Current [A] "
       "(magnitude; the Md column gives C, D or R), Voltage [V], Temperature Cell [degC] (thermocouple on the can), "
       "Temperature Chamber [degC]. Discharges at C/2, 1C and 1.5C from full after a CC-CV charge.")
README = b"Cell 02 of the LG M50 validation set. Thermocouple on the can, chamber logged. Nominal 5 Ah.\n"
MISSING = [{"item": "thermocouple calibration", "why_it_matters": "the fit's noise floor"}]
RMSE = {"thermal_fit_rmse": {"value": 1.0, "lo": 0.1, "hi": 3.0, "unit": "K"}}
HELDOUT = {"heldout_rmse": {"value": 1.5, "lo": 0.1, "hi": 5.0, "unit": "K"}}
EVALS = {"evaluations": {"value": 50, "lo": 10, "hi": 500, "unit": "1"}}
RISE = {"temperature_rise": {"value": 10.0, "lo": 1.0, "hi": 40.0, "unit": "K"}}
PEAK = {"max_temperature": {"value": 40.0, "lo": 30.0, "hi": 50.0, "unit": "degC"}}
PENDING = ("awaiting_approval", "queued", "running", "awaiting_result")
BENCH_CONDITION = (9.0, 1.3)       # the bench fixture's h and k the uploaded result was "measured" under

pytestmark = pytest.mark.skipif(not CELL.is_file(), reason="the Chen 2020 records are not in data/raw")


# ------------------------------------------------------------------ the model: a scripted brain the server's turns call

def call(tool, **args):
    return (tool, args)


def propose(action, params, prediction, rationale="r"):
    return call("propose_experiment", action=action, params=params, hypothesis="h", prediction=prediction,
                rationale=rationale, expected_effect="e")


def wait(reason="nothing to do"):
    return [call("wait", reason=reason)]


class Brain:
    """What the server's planner turns ask. Scripts are found by campaign title (registered before the campaign is
    opened, so the first turn never races the test): a callable(ctx, messages, k, mem) deciding from the context the
    turn sends, or a list of turns, each a list of replies (a reply: tool calls, or callable(ctx, messages, mem)).
    k is the number of model calls already made in this turn; mem is per campaign and outlives an app restart."""

    def __init__(self):
        self.scripts: dict[str, object] = {}
        self.mem: dict[str, dict] = {}
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self, ctx, messages):
        with self.lock:
            self.calls += 1
            script = self.scripts.get(ctx["campaign"]["title"])
            mem = self.mem.setdefault(ctx["campaign"]["id"], {})
            k = sum(m["role"] == "assistant" for m in messages)
            if script is None:
                return wait("no script")
            if callable(script):
                return script(ctx, messages, k, mem)
            n = ctx["turn"]["number"]
            replies = script[n - 1] if n - 1 < len(script) else []
            item = replies[k] if k < len(replies) else wait("script finished")
            return item(ctx, messages, mem) if callable(item) else item


def tool_outputs(messages, key: str) -> list[dict]:
    out = []
    for m in messages:
        if m["role"] == "tool":
            body = json.loads(m["content"])
            if key in body:
                out.append(body)
    return out


def pick_segments(segs: list[dict]) -> list[int]:
    """The C/2, 1C and 1.5C discharges from full (as tests/test_records_intake.py's segment_of picks them)."""
    out = []
    for rate in (0.5, 1.0, 1.5):
        out.append(next(s["segment"] for s in segs if s["kind"] == "constant current" and
                        s.get("voltage_trend") == "falling" and
                        abs(s.get("rate_per_nominal_capacity", 0) - rate) < 0.01 and
                        (s.get("after") or {}).get("voltage_end_V", 0) >= 4.1))
    return out


def manifest(*, header_row=13, chamber=True, surface=True, current_unit="A", rid="cell02", file=CELL.name,
             conditions=None, extra_records=(), did="lgm50-cells", columns=None) -> dict:
    cols = {"Voltage [V]": {"quantity": "voltage", "unit": "V"},
            "Current [A]": {"quantity": "current", "unit": current_unit, "label": "magnitude; Md gives the direction"}}
    if surface:
        cols["Temperature Cell [degC]"] = {"quantity": "surface_temperature", "unit": "degC",
                                           "label": "thermocouple on the can"}
    if chamber:
        cols["Temperature Chamber [degC]"] = {"quantity": "chamber_temperature", "unit": "degC"}
    if columns is not None:
        cols = columns
    rec = {"id": rid, "specimen": {"id": f"lgm50-{rid}", "properties": {"nominal_capacity_Ah": 5.0}},
           "time": {"unit": "s", "origin": "start of the Maccor test", "source": "Test Time [s]"},
           "tables": [{"file": file, "header_row": header_row, "time_column": "Test Time [s]", "columns": cols}],
           "provenance": {"source": "Maccor export"}}
    if conditions:
        rec["conditions"], rec["conditions_units"] = conditions
    return {"manifest_version": 2, "id": did, "title": "LG M50 validation", "author": "the lab",
            "citation": "engineer's upload", "licence": "internal", "records": [rec, *extra_records]}


def _measured(req: dict) -> dict:
    return {m["quantity"]: m["value"] for m in ((req.get("result") or {}).get("measurements") or [])}


def journey_planner(ctx, messages, k, mem):
    """The investigation a careful planner runs, decided from the context each turn sends: read the export, import it
    (the first manifest is wrong: the header is one line early), cut three record tests, design under the unchecked
    prior (a lead, not a result), fit on two tests, check on the third, design under the checked fit, ask for a bench
    test of that design, refit with it, check again and design again."""
    inv = ctx["investigation"]
    if not inv["instrument_facts"]:
        first = [[call("inspect_upload", file=CELL.name, lines=16),
                  call("inspect_upload", file=CELL.name, header_row=13),
                  call("import_records", manifest=manifest(header_row=12), missing_information=MISSING)],
                 [call("import_records", manifest=manifest(), missing_information=MISSING),
                  call("list_segments", record=1)]]
        if k < 2:
            return first[k]
        if k > 2:
            return wait("the first turn stopped")
        mem["segments"] = pick_segments(tool_outputs(messages, "segments")[-1]["segments"])
    elif k > 0:
        return wait("a proposal was refused: " + messages[-1]["content"][:300])
    reqs = ctx["requests"]
    if any(r["state"] in PENDING for r in reqs):
        return wait("requests are pending")
    if any(r["state"] == "failed" for r in reqs):
        return wait("a request failed")
    done = lambda action: [r for r in reqs if r["action"] == action and r["state"] == "completed"]
    tests, fits, checks, designs = (len(done(a)) for a in ("record_test", "fit_thermal", "check_model",
                                                            "optimize_protocol"))
    bench = [r for r in reqs if r["action"] == "bench_charge" and r["state"] not in ("rejected", "cancelled")]
    if tests < 3:
        return [propose("record_test", {"record": 1, "segment": mem["segments"][tests], "mode": 1, "start_soc": 1.0},
                        RISE)]
    if designs == 0:          # under the prior, never checked: a lead at most
        return [propose("optimize_protocol", {"ambient": 25.0, "model_version": 0}, EVALS)]
    if fits == 0:
        return [propose("fit_thermal", {"family": 2, "fit_on": 1 + 2}, RMSE)]
    if checks == 0:
        return [propose("check_model", {"model_version": 1, "test_on": 4}, HELDOUT)]
    if designs == 1:
        return [propose("optimize_protocol", {"ambient": 25.0, "model_version": 1}, EVALS)]
    if not bench:
        design = _measured(done("optimize_protocol")[-1])
        mem["bench_rates"] = {c: design[c] for c in fc.C_RATES}
        return [propose("bench_charge", {**mem["bench_rates"], "ambient": 25.0, "rest_min": 60.0}, PEAK,
                        rationale="Test the checked design on the bench before anyone relies on it.")]
    if fits == 1:
        return [propose("fit_thermal", {"family": 2, "fit_on": 1 + 2 + 8}, RMSE)]
    if checks == 1:
        return [propose("check_model", {"model_version": 2, "test_on": 4}, HELDOUT)]
    if designs == 2:
        return [propose("optimize_protocol", {"ambient": 25.0, "model_version": 2}, EVALS)]
    return wait("the design is revised on the bench test; the engineer decides")


# ------------------------------------------------------------------ fixtures

@pytest.fixture(scope="module")
def fake():
    """The lumped stand-in registered as pybamm-fastcharge-lab for this module; tests/ on PYTHONPATH so the solver
    child imports it."""
    from translucent import systems
    with pytest.MonkeyPatch.context() as mp:
        systems.available()
        mp.setattr(systems, "_ADAPTERS", dict(systems._ADAPTERS))
        mp.setenv("PYTHONPATH", os.pathsep.join(filter(None, [str(TESTS), os.environ.get("PYTHONPATH", "")])))
        import support.lab_fake_solver as F
        F.register()
        yield F


class Env:
    def __init__(self, data: Path, brain: Brain):
        self.data, self.brain = data, brain
        self.root = data / "ledgers" / LID

    def app(self, loop_workers=1):
        return server.create_app(data_root=self.data, poll_interval=0.01, loop_workers=loop_workers)

    @contextmanager
    def client(self, loop_workers=1):
        with TestClient(self.app(loop_workers), headers=VIEW) as c:
            yield c

    @contextmanager
    def ledger(self):
        L = Ledger(self.root)
        try:
            yield L
        finally:
            L.db.close()


@pytest.fixture
def env(fake, tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Paid model calls are forbidden in these tests")
    monkeypatch.setattr(runtime, "_client", denied)
    brain = Brain()
    # the provider client of every metered planner session: the scripted brain, no provider, no shared budget file
    monkeypatch.setattr(intervention_chat, "_client", lambda provider: ScriptedChatClient(ScriptedLoopPlanner(brain)))
    for name in ("TRANSLUCENT_ADMIN_TOKEN", "TRANSLUCENT_CORS_ORIGINS", "TRANSLUCENT_IMPORT_ROOT",
                 "TRANSLUCENT_DATA_ROOT", "TRANSLUCENT_ADMIN_NAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRANSLUCENT_TEAM_TOKEN", TEAM_TOKEN)
    monkeypatch.setenv("TRANSLUCENT_TEAM_NAME", "Ana")
    monkeypatch.setenv("TRANSLUCENT_LIVE", "1")
    monkeypatch.setenv("TRANSLUCENT_PROFILE", "dev")
    monkeypatch.setattr(server, "_missing_keys", lambda profile: [])
    # the providers' shared spend files are never read: a fixed, untouched cap
    monkeypatch.setattr(api_v1, "_provider_spend", lambda profile: [
        {"provider": "fireworks", "cap_usd": 25.0, "spent_usd": 0.0, "remaining_usd": 25.0}])
    monkeypatch.setattr(server, "PLANNER_TICK_S", 0.05)
    monkeypatch.setattr(server, "LOOP_TICK_S", 0.05)
    data = tmp_path / "data"
    init_ledger(data, LID, title="Battery lab")
    add_key(data, "Ada", "admin", ADMIN_KEY)
    add_key(data, "external-agent", "planner", AGENT_KEY)
    add_key(data, "Vic", "viewer", VIEW_KEY)
    return Env(data, brain)


# ------------------------------------------------------------------ helpers (the calls web/lib/api.ts makes)

def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def upload(c, files: dict[str, bytes], headers=TEAM, upload_id=None) -> str:
    """v1.upload: every file to one fresh upload id (PUT .../uploads/{id}/{name})."""
    upload_id = upload_id or uuid.uuid4().hex
    for name, data in files.items():
        got = ok(c.put(f"{BASE}/uploads/{upload_id}/{name}", content=data, headers=headers), 201)
        assert got == {"name": name, "bytes": len(data)}
    return upload_id


def records_objective(*, model_usd=5.0, experiments=40, documentation=DOC, **kw) -> dict:
    """The objective web/lib/newcampaign.ts openBody builds for "From your records" with the paid planner and
    auto-approval of computational work (the form's options), from the system's template."""
    t = lab.OBJECTIVE_TEMPLATE
    return {"statement": t["statement"]["fastest_feasible"], "system": {"id": lab.SYSTEM_ID}, "datasets": [],
            "baseline": [], "baseline_simulation": False, "documentation": documentation,
            "target": {k: t["target"][k] for k in ("quantity", "unit", "direction", "margin")},
            "constraints": [dict(c) for c in t["constraints"]], "quantities": [dict(q) for q in t["quantities"]],
            "budget": {"model_usd": model_usd, "experiments": experiments, "experiment_usd": 0},
            "planner": {"kind": "muse", "profile": "dev", "client": None},
            "auto_approve": [{"executor": "computational", "max_usd": 0, "max_wall_s": 900, "max_count": 40}],
            "kind": "fastest_feasible"} | kw


def open_records(c, env, title, script, files=None, headers=ADMIN, key=None, **objective):
    env.brain.scripts[title] = script
    upload_id = upload(c, files if files is not None else {CELL.name: CELL.read_bytes()})
    body = {"title": title, "objective": records_objective(**objective), "memory": "off",
            "records_upload_id": upload_id}
    r = c.post(f"{BASE}/campaigns", json=body, headers=headers | ({"Idempotency-Key": key} if key else {}))
    return r, body


def view(c, cid):
    return ok(c.get(f"{BASE}/campaigns/{cid}/view"))


def planner_results(env, cid, tool=None) -> list[dict]:
    with env.ledger() as L:
        return [r.data["payload"] for r in chain.records(L, cid) if r.type == "planner" and
                r.data["phase"] == "tool_result" and (tool is None or r.data["payload"].get("tool") == tool)]


def turns(env, cid) -> list[dict]:
    with env.ledger() as L:
        return [{"turn": t["turn"], **({"status": t["finished"].data["payload"]["status"],
                                        "reason": t["finished"].data["payload"]["reason"]} if t["finished"] else {})}
                for t in _turns(chain.records(L, cid))]


def until(env, cid, predicate, what: str, timeout=240.0):
    """Poll the ledger until predicate(states) holds with no planner turn open; on timeout say where it stopped."""
    deadline = time.monotonic() + timeout
    while True:
        with env.ledger() as L:
            recs = chain.records(L, cid)
            states = request_states(L, cid, recs)
            ts = _turns(recs)
            idle = not ts or ts[-1]["finished"] is not None
            if idle and predicate(states):
                return states
        if time.monotonic() > deadline:
            summary = [(s.action, s.state, (s.failure.data["error"][:200] if s.failure else None))
                       for s in states.values()]
            raise AssertionError(f"timed out waiting for {what}: requests {summary}; turns {turns(env, cid)}; "
                                 f"last tool results {planner_results(env, cid)[-4:]}")
        time.sleep(0.1)


def by_action(states, action, state=None):
    return sorted((s for s in states.values() if s.action == action and (state is None or s.state == state)),
                  key=lambda s: s.versions[0].seq)


def bench_result(req: dict, *, h_k=BENCH_CONDITION, file="bench.csv", T_offset=0.0, key=None):
    """A bench test's returned result for the approved request: what the fixture logged (the stand-in charge under
    the bench's h and k, every 10 s), as the HumanUpload JSON plus its CSV."""
    import support.lab_fake_solver as F
    p = {k: v["value"] for k, v in req["params"].items()}
    rates = [p[c] for c in fc.C_RATES]
    run = F.charge((rates, p["ambient"], p["rest_min"], h_k[0], h_k[1], None))
    t = np.arange(0.0, float(run["t"][-1]), 10.0)
    T = np.interp(t, run["t"], run["T"]) + T_offset
    v = np.interp(t, run["t"], run["v"])
    i = np.interp(t, run["t"], run["current"])
    csv = "# bench logger export, LG M50 in the fixture\ntime_s,T_can_C,V_cell,I_A\n" + "".join(
        f"{a:.1f},{b:.3f},{x:.4f},{y:.4f}\n" for a, b, x, y in zip(t, T, v, i))
    body = {"request_id": req["request_id"], "content_hash": req["content_hash"],
            "executed_configuration": p, "executed_units": {k: v["unit"] for k, v in req["params"].items()},
            "specimen_id": "lgm50-bench-01",
            "table": {"file": file, "header_row": 1, "time_column": "time_s",
                      "columns": {"T_can_C": {"quantity": "surface_temperature", "unit": "degC"},
                                  "V_cell": {"quantity": "voltage", "unit": "V"},
                                  "I_A": {"quantity": "current", "unit": "A"}}},
            "time": {"unit": "s", "origin": "charger switched on", "source": "logger clock"},
            "scalars": [{"quantity": "status", "value": "ok", "unit": "", "source": "the operator: ran to the end"},
                        {"quantity": "max_temperature", "value": round(float(T.max()), 3), "unit": "degC",
                         "source": "logger peak"},
                        {"quantity": "temperature_end", "value": round(float(T[-1]), 3), "unit": "degC",
                         "source": "logger, end of the rest"}]}
    return body, {file: csv.encode()}


def submit(c, cid, body, files, headers=TEAM, key=None):
    if files:
        body = body | {"upload_id": upload(c, files)}
    return c.post(f"{BASE}/campaigns/{cid}/requests/{body['request_id']}/results", json=body,
                  headers=headers | ({"Idempotency-Key": key} if key else {}))


def count_kinds(L) -> dict:
    return dict(L.db.execute("SELECT kind, COUNT(*) FROM record GROUP BY kind").fetchall())


# ------------------------------------------------------------------ 1. the happy path, with a restart

def test_the_records_investigation_runs_end_to_end_over_api_v1(env, tmp_path):
    title = "Fast charge from cell 02's records"
    with env.client() as c:
        # the engineer's raw export and a note, uploaded as the web does; the administrator opens the paid planner
        assert {p["id"]: p["ready"] for p in ok(c.get(f"{BASE}/planners", headers=ADMIN))}["muse"] is True
        r, body = open_records(c, env, title, journey_planner, files={CELL.name: CELL.read_bytes(),
                                                                       "README.txt": README}, key="open-1")
        opened = ok(r, 201)
        cid = opened["campaign"]["campaign_id"]
        uploads = opened["campaign"]["objective"]["data"]["uploads"]
        assert uploads == records.describe({CELL.name: CELL.read_bytes(), "README.txt": README})
        assert opened["campaign"]["objective"]["data"]["documentation"] == DOC
        assert not (env.data / "state" / LID / "dataset-uploads" / body["records_upload_id"]).exists()
        again = ok(c.post(f"{BASE}/campaigns", json=body, headers=ADMIN | {"Idempotency-Key": "open-1"}), 201)
        assert again["campaign"]["campaign_id"] == cid                  # a retried open opens nothing new
        assert len(ok(c.get(f"{BASE}/campaigns"))["items"]) == 1

        # the planner reads, imports (refused once, then corrected), cuts record tests, fits, checks, designs, and
        # asks for a bench test of the checked design: a person's approval, then a person's upload
        states = until(env, cid, lambda s: any(x.action == "bench_charge" and x.state == "awaiting_approval"
                                               for x in s.values()), "the bench test's approval")
        imports = [p["result"] for p in planner_results(env, cid, "import_records")]
        assert "error" in imports[0] and "Test Time [s]" in imports[0]["error"], imports[0]
        assert imports[1]["dataset_id"] == "lgm50-cells" and imports[1]["records"][0]["record"] == 1
        seen = [p["result"] for p in planner_results(env, cid, "inspect_upload")]
        assert seen[0]["lines"][13]["text"].startswith("Rec,Cycle P")
        assert [d["id"] for d in ok(c.get(f"{BASE}/datasets"))] == ["lgm50-cells"]   # the refused one wrote nothing
        assert len(by_action(states, "record_test", "completed")) == 3
        assert all(s.approved_by == "policy:auto/1" for s in by_action(states, "record_test"))
        summary = ok(c.get(f"{BASE}/campaigns"))["items"][0]
        assert summary["status"] == "waiting_for_approval" and summary["next_action"] == "awaiting_approval"
        bench_id = by_action(states, "bench_charge")[0].request_id
        bench = ok(c.get(f"{BASE}/campaigns/{cid}/requests/{bench_id}"))
        assert bench["executor"] == "human" and bench["procedure"] and bench["proposed_by"].startswith("agent:")
        assert view(c, cid)["next_action"]["content_hash"] == bench["content_hash"]
        approved = ok(c.post(f"{BASE}/campaigns/{cid}/requests/{bench_id}/approve", headers=TEAM | {
            "Idempotency-Key": "approve-bench"}, json={"content_hash": bench["content_hash"], "note": "Bench booked"}))
        assert approved["state"] == "awaiting_result" and approved["approved_by"] == "person:Ana"

    # the process stops and a new one serves the same ledger directory
    with env.client() as c:
        replay = c.post(f"{BASE}/campaigns/{cid}/requests/{bench_id}/approve", headers=TEAM | {
            "Idempotency-Key": "approve-bench"}, json={"content_hash": bench["content_hash"], "note": "Bench booked"})
        assert (replay.status_code, replay.json()) == (200, approved)
        assert view(c, cid)["next_action"]["kind"] in ("awaiting_result", "upload_result"), view(c, cid)["next_action"]
        body, files = bench_result(ok(c.get(f"{BASE}/campaigns/{cid}/requests/{bench_id}")))
        done = ok(submit(c, cid, body, files, key="result-bench"))
        assert done["state"] == "completed" and done["result"]["basis"] == "physical"

        # the result wakes the planner: it refits on the record tests and the bench test, checks, designs again
        states = until(env, cid, lambda s: len(by_action(s, "optimize_protocol", "completed")) == 3,
                       "the revised design")
        fits = by_action(states, "fit_thermal", "completed")
        with env.ledger() as L:
            refit = lab._result_json(L, fits[-1])
        assert refit["details"]["validity"]["lab_tests"] == [1, 2, 4]
        v = view(c, cid)
        learning = v["learning"]
        assert [t["action"] for t in learning["lab_tests"]] == ["record_test"] * 3 + ["bench_charge"]
        assert len(learning["versions"]) == 3 and [len(x["checks"]) for x in learning["versions"]] == [0, 1, 1]
        assert v["investigation"]["tests"][3]["kind"] == "bench"
        assert v["investigation"]["missing_information"] == MISSING

        # honest standing: the design under the unchecked prior is never the recommendation, and nothing reports it
        # as a feasible result
        prior_design = by_action(states, "optimize_protocol")[0]
        assert int(prior_design.data.params["model_version"]) == 0
        assert "recommended_design" in learning, sorted(learning)
        rec = learning["recommended_design"]
        if rec is not None:
            row = learning["versions"][rec["model_version"]]
            assert rec["request_id"] != prior_design.request_id and row["checks"] and row["adequate"] is True
        for d in learning["designs"]:
            if d["request_id"] == prior_design.request_id:
                assert d["model_supported"] is False, d
        for d in learning["designs"]:       # a supported design needs an adequate fit checked on held-out tests
            row = learning["versions"][d["model_version"]]
            assert not d["model_supported"] or (row["adequate"] is True and row["checks"]), (d, row)
        cmp =[x for x in v["comparisons"] if x["request_id"] == prior_design.request_id]
        assert all(x["verdict"] == "not_comparable" and x["feasibility"]["candidate"] == "unknown" for x in cmp), cmp

        # the engineer concludes; the export the web downloads verifies offline
        concluded = ok(c.post(f"{BASE}/campaigns/{cid}/conclusion", headers=TEAM | {"Idempotency-Key": "con"},
                              json={"text": "Design revised on the bench test."}))
        assert concluded["type"] == "conclusion"
        if rec is None:      # no design has standing: the outcome claims nothing met or feasible
            assert not concluded["data"]["outcome"].startswith(("objective_met", "faster_than_reference",
                                                                "feasible_only", "improved")), concluded["data"]
        late = c.post(f"{BASE}/campaigns/{cid}/notes", headers=TEAM, json={"text": "late"})
        assert late.status_code == 409 and "concluded" in late.text
        export = c.get(f"{BASE}/campaigns/{cid}/export")
        assert export.status_code == 200 and f"{cid}.loop.json" in export.headers["content-disposition"]
        exported = export.json()
        assert exported["view"]["campaign"]["id"] == cid and exported["view"]["source"]["kind"] == "recorded"
        assert exported["view"]["investigation"]["uploads"] == uploads
    # the export's own receipt is re-checked in test_a_records_campaign_export_verifies_offline (a DEFECT); here the
    # ledger it was made from verifies
    report = verify(env.root, campaign_id=cid)
    assert report.ok, report.problems
    with env.ledger() as L:
        assert chain.problems(L, cid) == []
        recs = chain.records(L, cid)
        assert [r.actor for r in recs if r.type == "approval" and not r.actor.startswith("policy:")] == ["person:Ana"]
        assert sum(r.type == "result" for r in recs) == len([s for s in request_states(L, cid, recs).values()
                                                             if s.state == "completed"])
        assert model_spend(L, cid, recs) <= 5.0
    assert all(t.get("status") in ("proposed", "waiting") for t in turns(env, cid)), turns(env, cid)


# regression test: a defect this file found, now fixed
def test_a_records_campaign_export_verifies_offline(env, tmp_path):
    """The loop export the web downloads must re-check offline (`translucent verify <file>`). A record test is
    admitted against the records the campaign imported; the export's receipt has to carry them."""
    title = "Export of a records campaign"
    script = [[[call("import_records", manifest=manifest(), missing_information=MISSING), call("list_segments", record=1)],
               lambda ctx, messages, mem: [propose("record_test", {"record": 1, "segment": pick_segments(
                   tool_outputs(messages, "segments")[-1]["segments"])[0], "mode": 1, "start_soc": 1.0}, RISE)]]]
    with env.client() as c:
        cid = ok(open_records(c, env, title, script)[0], 201)["campaign"]["campaign_id"]
        until(env, cid, lambda s: len(by_action(s, "record_test", "completed")) == 1, "the record test")
        export = c.get(f"{BASE}/campaigns/{cid}/export")
        assert export.status_code == 200
    assert verify(env.root, campaign_id=cid).ok                  # the ledger itself verifies
    path = tmp_path / f"{cid}.loop.json"
    path.write_bytes(export.content)
    report = verify(path)
    assert report.ok, report.problems
    # an import note that was made cannot be left out of the export (an older export, written before notes, can)
    data = export.json()
    facts = data["view"]["investigation"]["instrument_facts"]
    assert facts and all(f["import_notes"] for f in facts)
    for f in facts:
        del f["import_notes"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert "Loop export view differs from the recorded evidence" in verify(path).problems


# ------------------------------------------------------------------ 2. failure matrix: uploads

def test_invalid_oversized_and_misused_uploads_are_refused_with_a_reason(env, monkeypatch):
    title = "Upload refusals"
    with env.client(loop_workers=0) as c:
        uid = uuid.uuid4().hex
        for name, status, words in [("my export.csv", 400, "plain file name"), ("manifest.json", 400, "plain file name"),
                                    (".hidden.csv", 400, "plain file name"), ("CON.csv", 422, "device name")]:
            r = c.put(f"{BASE}/uploads/{uid}/{name}", content=b"a,b\n1,2\n", headers=TEAM)
            assert r.status_code == status and words in r.text, (name, r.text)
        assert c.put(f"{BASE}/uploads/not-an-id/x.csv", content=b"1", headers=TEAM).status_code == 400
        # over the server's per-file limit while streaming: 413, nothing left behind
        monkeypatch.setattr(server, "UPLOAD_LIMIT", 1000)
        r = c.put(f"{BASE}/uploads/{uid}/big.csv", content=b"1,2\n" * 1000, headers=TEAM)
        assert r.status_code == 413 and "can be up to" in r.text, r.text
        folder = env.data / "state" / LID / "dataset-uploads" / uid
        assert not folder.exists() or not any(folder.iterdir())
        monkeypatch.setattr(server, "UPLOAD_LIMIT", 500 * 2 ** 20)
        # over the records limit (64 MiB, here 1000 bytes): accepted as an upload, refused when the campaign opens
        monkeypatch.setattr(records, "MAX_UPLOAD_BYTES", 1000)
        r, body = open_records(c, env, title, None, files={"big.csv": b"1,2\n" * 1000})
        assert r.status_code == 422 and "big.csv: larger than" in r.text, r.text
        assert ok(c.get(f"{BASE}/campaigns"))["items"] == []
        monkeypatch.setattr(records, "MAX_UPLOAD_BYTES", 64 * 1024 * 1024)
        monkeypatch.setattr(records, "MAX_UPLOADS", 2)
        r, _ = open_records(c, env, title, None, files={f"f{i}.csv": b"1,2\n" for i in range(3)})
        assert r.status_code == 422 and "give 1 to 2 files" in r.text, r.text
        monkeypatch.setattr(records, "MAX_UPLOADS", 32)
        # an upload id with no files, and one already used to open a campaign
        r = c.post(f"{BASE}/campaigns", headers=ADMIN, json=body | {"records_upload_id": uuid.uuid4().hex})
        assert r.status_code == 404 and "Upload the records first" in r.text
        r, used = open_records(c, env, title, None)
        assert r.status_code == 201, r.text
        again = c.post(f"{BASE}/campaigns", headers=ADMIN, json=used)       # no Idempotency-Key: a second decision
        assert again.status_code == 404 and "Upload the records first" in again.text
        assert len(ok(c.get(f"{BASE}/campaigns"))["items"]) == 1
        # the same file sent twice to one upload id is one file
        uid = upload(c, {"a.csv": b"t,I\n0,1\n"})
        upload(c, {"a.csv": b"t,I\n0,1\n"}, upload_id=uid)
        assert [p.name for p in (env.data / "state" / LID / "dataset-uploads" / uid).iterdir()] == ["a.csv"]


BINARY = b"PK\x03\x04\x14\x00\x06\x00" + bytes(range(256)) * 8      # a spreadsheet (zip) as the engineer saved it


def _binary_campaign(c, env, title):
    script = [[[call("inspect_upload", file="cells.xlsx", lines=5),
                call("import_records", manifest=manifest(file="cells.xlsx"), missing_information=MISSING)],
               wait("read")]]
    r, _ = open_records(c, env, title, script, files={"cells.xlsx": BINARY})
    cid = ok(r, 201)["campaign"]["campaign_id"]
    until(env, cid, lambda s: len(planner_results(env, cid)) >= 2, "the planner's reads")
    return cid


def test_an_unsupported_binary_upload_is_refused_at_import_with_its_reason(env):
    with env.client() as c:
        cid = _binary_campaign(c, env, "Binary import")
        got = planner_results(env, cid, "import_records")[0]["result"]
        assert "error" in got and "cells.xlsx" in got["error"], got
        assert ok(c.get(f"{BASE}/datasets")) == []


# regression test: a defect this file found, now fixed
def test_inspecting_a_binary_upload_says_it_is_not_text(env):
    with env.client() as c:
        cid = _binary_campaign(c, env, "Binary inspect")
        got = planner_results(env, cid, "inspect_upload")[0]["result"]
        assert "error" in got and "not text" in got["error"], got


# regression test: a defect this file found, now fixed
def test_documentation_over_the_limit_is_refused_not_cut(env):
    with env.client(loop_workers=0) as c:
        r, _ = open_records(c, env, "Long documentation", None, documentation="x" * (records.MAX_DOC_CHARS + 1))
        assert r.status_code == 422, r.text


# ------------------------------------------------------------------ 2. failure matrix: imports and record tests

def test_a_partial_or_mis_united_import_is_refused_and_writes_nothing(env):
    title = "Partial import"
    cell03 = RAW / "LGM50_cell03.csv"
    bad_column = manifest(extra_records=[manifest(rid="cell03", file=cell03.name, columns={
        "Voltage [V]": {"quantity": "voltage", "unit": "V"},
        "Temperature Cell [degF]": {"quantity": "surface_temperature", "unit": "degF"},
        "Current [A]": {"quantity": "current", "unit": "A"}})["records"][0]])
    script = [[[call("import_records", manifest=bad_column, missing_information=MISSING)],
               [call("import_records", manifest=manifest(current_unit="V"), missing_information=MISSING)],
               [propose("record_test", {"record": 1, "segment": 2, "mode": 1, "start_soc": 1.0}, RISE)],
               wait("refused")]]
    with env.client() as c:
        with env.ledger() as L:
            before = count_kinds(L)
        r, _ = open_records(c, env, title, script, files={CELL.name: CELL.read_bytes(),
                                                          cell03.name: cell03.read_bytes()})
        cid = ok(r, 201)["campaign"]["campaign_id"]
        until(env, cid, lambda s: len(planner_results(env, cid)) >= 4, "the planner's imports")
        first, second = (p["result"] for p in planner_results(env, cid, "import_records"))
        assert "Temperature Cell [degF]" in first["error"] and cell03.name in first["error"], first
        assert "error" in second and re.search(r"\bV\b", second["error"]) and "current" in second["error"], second
        refused = planner_results(env, cid, "propose_experiment")[0]["result"]
        assert "no record is imported yet" in refused["error"], refused
        assert ok(c.get(f"{BASE}/datasets")) == []
        assert view(c, cid)["investigation"]["instrument_facts"] == []
    with env.ledger() as L:
        after = count_kinds(L)
        assert not list(env.root.glob(".incoming-*"))
    grew = {k: after.get(k, 0) - before.get(k, 0) for k in set(after) | set(before)}
    assert grew.get("dataset", 0) == 0 and grew.get("artifact", 0) == 0, grew       # nothing of either record


def test_record_tests_need_a_surface_temperature_and_a_recorded_or_documented_ambient(env):
    title = "Missing measurements"
    nosurf = manifest(rid="nosurf", surface=False)["records"][0]
    nochamber = manifest(rid="nochamber", chamber=False)["records"][0]
    documented = manifest(rid="documented", chamber=False,
                          conditions=({"ambient": 25.0}, {"ambient": "degC"}))["records"][0]
    m = manifest() | {"records": [nosurf, nochamber, documented]}
    script = [[[call("import_records", manifest=m, missing_information=MISSING), call("list_segments", record=1)],
               wait("imported")]]
    with env.client() as c:
        cid = ok(open_records(c, env, title, script)[0], 201)["campaign"]["campaign_id"]
        until(env, cid, lambda s: len(planner_results(env, cid, "list_segments")) == 1, "the import")
        seg = pick_segments(planner_results(env, cid, "list_segments")[0]["result"]["segments"])[0]

        def test_of(record):
            return c.post(f"{BASE}/campaigns/{cid}/requests", headers=TEAM, json={
                "action": "record_test", "params": {"record": record, "segment": seg, "mode": 1, "start_soc": 1.0},
                "rationale": "the C/2 discharge", "prediction": RISE, "expected_effect": "a thermal test"})
        r = test_of(1)
        assert r.status_code == 422 and "no surface_temperature series" in r.text, r.text
        r = test_of(2)
        assert r.status_code == 422 and "neither records the chamber temperature nor documents it" in r.text, r.text
        rq = ok(test_of(3), 201)["request_id"]
        states = until(env, cid, lambda s: s[rq].state == "completed", "the documented-ambient record test")
        assert {s.action for s in states.values()} == {"record_test"} and len(states) == 1
        got = ok(c.get(f"{BASE}/campaigns/{cid}/requests/{rq}"))
        assert {m["quantity"]: m["value"] for m in got["result"]["measurements"]}["ambient_temperature"] == 25.0


# ------------------------------------------------------------------ 2. failure matrix: duplicates, stale and out-of-order

BENCH = {c: 1.0 for c in fc.C_RATES} | {"ambient": 25.0, "rest_min": 60.0}


def bench_script(*later_turns):
    """Import, then ask for one bench test at 1C throughout; later turns as given."""
    return [[[call("import_records", manifest=manifest(), missing_information=MISSING)],
             [propose("bench_charge", BENCH, PEAK)]], *later_turns]


def open_with_bench(c, env, title, **objective):
    cid = ok(open_records(c, env, title, bench_script(), **objective)[0], 201)["campaign"]["campaign_id"]
    states = until(env, cid, lambda s: any(x.action == "bench_charge" for x in s.values()), "the bench request")
    rq = by_action(states, "bench_charge")[0].request_id
    return cid, ok(c.get(f"{BASE}/campaigns/{cid}/requests/{rq}"))


def test_duplicate_uploads_approvals_and_results_are_counted_once(env):
    with env.client() as c:
        cid, req = open_with_bench(c, env, "Duplicates")
        rq = req["request_id"]
        # the same proposal again (an engineer re-sends it) is the pending one, not a second request
        again = ok(c.post(f"{BASE}/campaigns/{cid}/requests", headers=TEAM, json={
            "action": "bench_charge", "params": BENCH, "rationale": "r", "prediction": PEAK, "expected_effect": "e"}),
            201)
        assert again["request_id"] == rq
        first = ok(c.post(f"{BASE}/campaigns/{cid}/requests/{rq}/approve", headers=TEAM,
                          json={"content_hash": req["content_hash"]}))
        second = c.post(f"{BASE}/campaigns/{cid}/requests/{rq}/approve", headers=TEAM,
                        json={"content_hash": req["content_hash"]})
        # the same decision again (no Idempotency-Key): answered as it stands, not recorded twice (counted below)
        assert second.status_code == 200 and second.json()["state"] == first["state"] == "awaiting_result", second.text
        req = ok(c.get(f"{BASE}/campaigns/{cid}/requests/{rq}"))
        body, files = bench_result(req)
        done = ok(submit(c, cid, body, files))
        same = ok(submit(c, cid, body, files))            # the identical result again, sent with fresh files
        assert same["state"] == "completed" and same["result"]["result_id"] == done["result"]["result_id"]
        other, other_files = bench_result(req, T_offset=0.5)
        for _ in range(2):
            r = submit(c, cid, other, other_files)
            assert r.status_code == 422 and "already completed" in r.text, r.text
        until(env, cid, lambda s: True, "the planner's turn after the result")
    with env.ledger() as L:
        recs = chain.records(L, cid)
        kinds = [r.type for r in recs if r.data.get("request_id") == rq]
        assert kinds.count("approval") == 1 and kinds.count("result") == 1, kinds
        assert kinds.count("result_rejected") == 1, kinds          # the different result, refused once
        assert chain.problems(L, cid) == []


def test_a_stale_content_hash_is_never_approved(env):
    with env.client() as c:
        cid, v1 = open_with_bench(c, env, "Stale approvals")
        rq = v1["request_id"]
        v2 = ok(c.post(f"{BASE}/campaigns/{cid}/requests/{rq}/revise", headers=TEAM,
                       json={"params": {"rest_min": 30.0}, "reason": "Shorter rest in this fixture"}))
        assert v2["version"] == 2 and v2["content_hash"] != v1["content_hash"]
        stale = c.post(f"{BASE}/campaigns/{cid}/requests/{rq}/approve", headers=TEAM,
                       json={"content_hash": v1["content_hash"]})
        assert stale.status_code == 409 and "review version 2" in stale.text, stale.text
        assert ok(c.get(f"{BASE}/campaigns/{cid}/requests/{rq}"))["state"] == "awaiting_approval"
        # an amendment of the objective withdraws the proposal: its latest hash is now stale too
        ok(c.post(f"{BASE}/campaigns/{cid}/amendments", headers=TEAM, json={
            "reason": "Cooler fixture limit", "constraints": [
                dict(lab.OBJECTIVE_TEMPLATE["constraints"][0]),
                {"quantity": "max_temperature", "op": "<=", "limit": 40.0, "unit": "degC", "label": "40 degC"}]}))
        late = c.post(f"{BASE}/campaigns/{cid}/requests/{rq}/approve", headers=TEAM,
                      json={"content_hash": v2["content_hash"]})
        assert late.status_code == 422 and "is cancelled, not awaiting approval" in late.text, late.text
        assert ok(c.get(f"{BASE}/campaigns/{cid}/requests/{rq}"))["state"] == "cancelled"
        body, files = bench_result(v2)
        r = submit(c, cid, body, files)
        assert r.status_code == 422 and "cancelled" in r.text, r.text
        until(env, cid, lambda s: True, "the planner's turn after the amendment")
    with env.ledger() as L:
        assert [r.type for r in chain.records(L, cid) if r.type == "approval"] == []
        assert chain.problems(L, cid) == []


def test_results_out_of_order_are_refused_and_change_nothing(env):
    with env.client() as c:
        cid, v1 = open_with_bench(c, env, "Out of order")
        rq = v1["request_id"]
        body, files = bench_result(v1)
        r = submit(c, cid, body, files)                               # before any approval
        assert r.status_code == 422 and "not approved" in r.text, r.text
        r = submit(c, cid, body | {"request_id": "rq_" + "0" * 20}, files)
        assert r.status_code == 404, r.text
        r = c.post(f"{BASE}/campaigns/{cid}/requests/{rq}/results", headers=TEAM,
                   json=body | {"request_id": "rq_" + "1" * 20})
        assert r.status_code == 422 and "names request" in r.text
        # the engineer revises and approves version 2; a result for version 1 is refused
        v2 = ok(c.post(f"{BASE}/campaigns/{cid}/requests/{rq}/revise", headers=TEAM,
                       json={"params": {"rest_min": 30.0}, "reason": "Shorter rest", "approve": True}))
        assert v2["state"] == "awaiting_result"
        r = submit(c, cid, body, files)
        assert r.status_code == 422 and "the approved version is 2" in r.text, r.text
        # a computational request never takes an upload
        fq = ok(c.post(f"{BASE}/campaigns/{cid}/requests", headers=TEAM, json={
            "action": "forecast_protocol", "params": BENCH | {"model_version": 0}, "rationale": "r",
            "prediction": PEAK, "expected_effect": "e"}), 201)
        r = submit(c, cid, body | {"request_id": fq["request_id"], "content_hash": fq["content_hash"]}, files)
        assert r.status_code == 422 and "is computational" in r.text, r.text
        # a rejected request takes none either
        other = ok(c.post(f"{BASE}/campaigns/{cid}/requests", headers=TEAM, json={
            "action": "bench_charge", "params": BENCH | {"ambient": 30.0}, "rationale": "r", "prediction": PEAK,
            "expected_effect": "e"}), 201)
        ok(c.post(f"{BASE}/campaigns/{cid}/requests/{other['request_id']}/reject", headers=TEAM,
                  json={"content_hash": other["content_hash"], "reason": "Not this fixture"}))
        r = submit(c, cid, body | {"request_id": other["request_id"], "content_hash": other["content_hash"]}, files)
        assert r.status_code == 422 and "rejected" in r.text, r.text
        assert ok(c.get(f"{BASE}/campaigns/{cid}/requests/{rq}"))["state"] == "awaiting_result"
        until(env, cid, lambda s: s[fq["request_id"]].state == "completed", "the forecast")
    with env.ledger() as L:
        recs = chain.records(L, cid)
        assert not [r for r in recs if r.type == "result" and r.data["request_id"] in (rq, other["request_id"])]
        assert chain.problems(L, cid) == []


# ------------------------------------------------------------------ 2. failure matrix: budgets

def test_the_lab_test_budget_stops_further_lab_tests_with_its_reason(env):
    with env.client() as c:
        cid, first = open_with_bench(c, env, "Lab budget", budget={"model_usd": 5.0, "experiments": 40,
                                                                  "experiment_usd": 0, "lab_tests": 1})
        body = {"action": "bench_charge", "params": BENCH | {"ambient": 30.0}, "rationale": "r", "prediction": PEAK,
                "expected_effect": "e"}
        r = c.post(f"{BASE}/campaigns/{cid}/requests", headers=TEAM, json=body)
        assert r.status_code == 422 and "lab-test budget is spent: 1 of 1" in r.text, r.text
        r = c.post(f"{BASE}/campaigns/{cid}/requests", headers=AGENT, json=body)       # an agent's key alike
        assert r.status_code == 422 and "lab-test budget is spent" in r.text, r.text
        ok(c.post(f"{BASE}/campaigns/{cid}/requests/{first['request_id']}/reject", headers=TEAM,
                  json={"content_hash": first["content_hash"], "reason": "Bench unavailable"}))
        assert ok(c.post(f"{BASE}/campaigns/{cid}/requests", headers=TEAM, json=body), 201)["state"] == \
            "awaiting_approval"                                          # a rejected test does not count
        until(env, cid, lambda s: True, "the planner's turn after the rejection")
    with env.ledger() as L:
        states = request_states(L, cid)
        assert len([s for s in states.values() if s.action == "bench_charge"]) == 2


def test_the_model_budget_stops_the_planner_with_its_reason_and_spends_nothing_more(env):
    title = "Model budget"
    with env.client() as c:
        before = env.brain.calls
        cid = ok(open_records(c, env, title, [[[call("inspect_upload", file=CELL.name)]]], model_usd=0.001)[0],
                 201)["campaign"]["campaign_id"]
        until(env, cid, lambda s: len(turns(env, cid)) >= 1, "the first turn")
        first = turns(env, cid)
        assert first[0]["status"] == "budget" and "model budget" in first[0]["reason"], first
        summary = ok(c.get(f"{BASE}/campaigns"))["items"][0]
        assert summary["next_action"] == "planner_stopped", summary
        ok(c.post(f"{BASE}/campaigns/{cid}/notes", headers=TEAM, json={"text": "Please continue"}))
        until(env, cid, lambda s: len(turns(env, cid)) >= 2, "the turn after the note")
        assert all(t["status"] == "budget" for t in turns(env, cid))
        assert env.brain.calls == before                                 # no model call was made
    with env.ledger() as L:
        assert model_spend(L, cid) == 0.0


# regression test: a defect this file found, now fixed
def test_a_paid_planner_with_no_model_budget_is_refused_or_says_why(env):
    with env.client() as c:
        r, _ = open_records(c, env, "No model budget", wait("never"), model_usd=0.0)
        if r.status_code == 422:
            return
        cid = r.json()["campaign"]["campaign_id"]
        time.sleep(1.5)                                                   # thirty planner ticks
        summary = ok(c.get(f"{BASE}/campaigns"))["items"][0]
        assert turns(env, cid) or summary["next_action"] == "planner_stopped", (summary, turns(env, cid))


# ------------------------------------------------------------------ 2. failure matrix: interruption and restart

def test_requests_left_queued_or_running_when_the_process_dies_complete_after_restart(env):
    from translucent.locks import ExclusiveFileLock
    from translucent.loop import adapter_for, claim_path, current_objective
    from translucent.loop_runner import LoopRunner
    title = "Interrupted"
    with env.client() as c:                 # the planner imports and stops
        cid = ok(open_records(c, env, title, [[[call("import_records", manifest=manifest(),
                                                     missing_information=MISSING), call("list_segments", record=1)],
                                                   wait("imported")]])[0], 201)["campaign"]["campaign_id"]
        until(env, cid, lambda s: len(planner_results(env, cid, "list_segments")) == 1, "the import")
    segs = pick_segments(planner_results(env, cid, "list_segments")[0]["result"]["segments"])
    with env.client(loop_workers=0) as c:   # a process with no runner: the record tests stay queued
        ids = []
        for seg in segs[:2]:
            got = ok(c.post(f"{BASE}/campaigns/{cid}/requests", headers=TEAM, json={
                "action": "record_test", "params": {"record": 1, "segment": seg, "mode": 1, "start_soc": 1.0},
                "rationale": "r", "prediction": RISE, "expected_effect": "e"}), 201)
            assert got["state"] == "queued", got
            ids.append(got["request_id"])
    # a runner claims the second, records its start and dies before any result (its claim lock dies with it)
    lock = ExclusiveFileLock(claim_path(env.root, ids[1]))
    assert lock.try_acquire()
    try:
        with env.ledger() as L:
            runner = LoopRunner(env.root, process="api", workers=1)
            claimed, st = runner._claim(L, cid, ids[1], adapter_for(current_objective(L, cid)))
            assert claimed and st.state == "running"
    finally:
        lock.release()
    with env.client() as c:                 # the next process recovers both
        until(env, cid, lambda s: all(s[i].state == "completed" for i in ids), "the recovered requests")
        assert ok(c.get(f"{BASE}/campaigns"))["items"][0]["status"] == "running"
    with env.ledger() as L:
        phases = [(r.data["phase"], r.data["attempt"]) for r in chain.records(L, cid)
                  if r.type == "execution" and r.data["request_id"] == ids[1]]
        assert phases == [("started", 1), ("abandoned", 1), ("started", 2)], phases
        assert sum(r.type == "result" for r in chain.records(L, cid)) == 2
        assert chain.problems(L, cid) == []
    assert verify(env.root, campaign_id=cid).ok


# ------------------------------------------------------------------ 3. authorization

def test_only_a_person_with_the_right_role_uploads_approves_or_returns_results(env):
    init_ledger(env.data, "other", title="Another lab")
    other_key = "engineer-other-ledger-0123456789ab"
    add_key(env.data, "Oli", "engineer", other_key, ledgers=("other",))
    with env.client() as c:
        cid, req = open_with_bench(c, env, "Authorization")
        rq = req["request_id"]
        body, files = bench_result(req)
        uid = uuid.uuid4().hex
        wrong = {"Authorization": "Bearer wrong-key-0123456789abcdef"}
        callers = [({"Authorization": ""}, 401), (wrong, 401), (VIEW, 403), (AGENT, 403), (bearer(other_key), 404)]
        for headers, status in callers:
            c.app.state.translucent.auth.throttle.reset()          # wrong keys are rate-limited (tests/test_auth.py)
            r = c.put(f"{BASE}/uploads/{uid}/{CELL.name}", content=b"t,I\n0,1\n", headers=headers)
            assert r.status_code == status, ("upload", headers, r.text)
            r = c.post(f"{BASE}/campaigns", headers=headers, json={
                "title": "t", "objective": records_objective(), "records_upload_id": uuid.uuid4().hex})
            assert r.status_code == status, ("open", headers, r.text)
            r = c.post(f"{BASE}/campaigns/{cid}/requests/{rq}/approve", headers=headers,
                       json={"content_hash": req["content_hash"]})
            assert r.status_code == status, ("approve", headers, r.text)
            r = c.post(f"{BASE}/campaigns/{cid}/requests/{rq}/results", headers=headers, json=body)
            assert r.status_code == status, ("result", headers, r.text)
        r = c.put(f"{BASE}/uploads/{uid}/{CELL.name}", content=b"t,I\n0,1\n", headers=AGENT)
        assert "cannot upload files" in r.text
        # an engineer may upload and approve, but not open a paid planner: that is an administrator's
        r, _ = open_records(c, env, "Engineer's paid planner", None, headers=TEAM)
        assert r.status_code == 403 and "administrator" in r.text, r.text
        assert not (env.data / "state" / LID / "dataset-uploads" / uid).exists()
        assert ok(c.get(f"{BASE}/campaigns/{cid}/requests/{rq}"))["state"] == "awaiting_approval"
    with env.ledger() as L:
        recs = chain.records(L, cid)
        assert not [r for r in recs if r.type in ("approval", "result", "result_rejected")]


# ------------------------------------------------------------------ the web form's free planners on a records campaign

# regression test: a defect this file found, now fixed
def test_the_default_free_planner_on_a_records_campaign_is_refused_or_says_why(env):
    with env.client() as c:
        r, _ = open_records(c, env, "Records with the free planner", None, headers=TEAM,
                            planner={"kind": "domain_baseline", "profile": None, "client": None},
                            budget={"model_usd": 0, "experiments": 8, "experiment_usd": 0})
        if r.status_code == 422:
            return
        assert r.status_code == 201, r.text
        cid = r.json()["campaign"]["campaign_id"]
        time.sleep(1.5)
        summary = ok(c.get(f"{BASE}/campaigns"))["items"][0]
        assert turns(env, cid) or summary["next_action"] == "planner_stopped", (summary, turns(env, cid))

