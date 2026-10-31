"""The fast-charge journey on the real solver, over HTTP, through a server restart.

A team imports the LG M50 records with their failing 1.5C baseline; the baseline is re-run on the solver at
registration; an agent proposes a four-step protocol; a person approves it; the server stops before running it and a
new server runs it; the comparison says the candidate is better in simulation; the agent concludes; the ledger and
the campaign's export verify.

Executed, not mocked: it runs PyBaMM twice (about 20 s). Opt in with TRANSLUCENT_SOLVER_TESTS=1, with the solver
group installed and the records fetched (scripts/fetch_lgm50_data.py). No model is called.
"""
import json
import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from translucent import runtime, server
from translucent.loop_planner import fastcharge_objective
from translucent.verify import verify
from translucent.workspace import init_ledger

ROOT = Path(__file__).resolve().parents[1]
RECORDS = ROOT / "data" / "raw" / "chen2020-lgm50"
MANIFEST = ROOT / "data" / "chen2020-lgm50" / "manifest.json"
TEAM = {"Authorization": "Bearer team-secret"}
BASE = "/api/v1/ledgers/lab"

pytestmark = pytest.mark.skipif(
    os.environ.get("TRANSLUCENT_SOLVER_TESTS") != "1" or not RECORDS.is_dir(),
    reason="executed journey: set TRANSLUCENT_SOLVER_TESTS=1 and fetch the LG M50 records")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("The journey calls no model")
    monkeypatch.setattr(runtime, "_client", denied)
    for name in ("TRANSLUCENT_LIVE", "TRANSLUCENT_ADMIN_TOKEN", "TRANSLUCENT_IMPORT_ROOT", "TRANSLUCENT_DATA_ROOT",
                 "TRANSLUCENT_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRANSLUCENT_TEAM_TOKEN", "team-secret")


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def wait_for(c, cid, request_id, states, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        req = ok(c.get(f"{BASE}/campaigns/{cid}/requests/{request_id}"))
        if req["state"] in states:
            return req
        time.sleep(0.25)
    raise AssertionError(f"{request_id} is still {req['state']}")


def test_import_approve_restart_execute_compare_conclude_verify(tmp_path):
    data, imports = tmp_path / "data", tmp_path / "imports"
    init_ledger(data, "lab", title="Cell lab")
    (imports / "lgm50").mkdir(parents=True)
    for f in RECORDS.iterdir():
        shutil.copy2(f, imports / "lgm50" / f.name)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def app():
        return server.create_app(data_root=data, import_root=imports, poll_interval=0.01, loop_workers=1)

    with TestClient(app()) as c:
        dataset = ok(c.post(f"{BASE}/datasets", headers=TEAM, json={"manifest": manifest, "path": "lgm50"}), 201)
        objective = fastcharge_objective(kind="external", profile=None, model_usd=0.0)
        objective.pop("registered_by")
        objective |= {"datasets": [dataset["id"]], "baseline": [f"{dataset['id']}/failing-baseline-1p5c"],
                      "baseline_simulation": True}
        opened = ok(c.post(f"{BASE}/campaigns", headers=TEAM,
                           json={"author": "Ana", "title": "Faster LG M50 charge", "objective": objective}), 201)
        cid = opened["campaign"]["campaign_id"]

        view = ok(c.get(f"{BASE}/campaigns/{cid}/view"))
        baseline_run = next(r for r in view["requests"] if r["proposed_by"] == "policy:registration")
        baseline = wait_for(c, cid, baseline_run["request_id"], ("completed", "failed"))
        assert baseline["state"] == "completed", baseline["failure"]
        measured = {m["quantity"]: m["value"] for m in baseline["result"]["measurements"]}
        assert measured["max_temperature"] > 45.0 and measured["min_anode_potential"] < 10.0   # it fails both limits

        req = ok(c.post(f"{BASE}/campaigns/{cid}/requests", headers=TEAM, json={
            "agent": "external-agent", "action": "simulate_protocol",
            "params": {"c_rate_1": 1.5, "c_rate_2": 1.5, "c_rate_3": 1.05, "c_rate_4": 1.1, "ambient": 25.0},
            "rationale": "Keep the low-SOC windows fast and cut the last two, where heat has accumulated.",
            "prediction": {"charge_time_80": {"value": 34.0, "lo": 32.0, "hi": 37.0, "unit": "min"},
                           "min_anode_potential": {"value": 12.0, "lo": 10.0, "hi": 16.0, "unit": "mV"},
                           "max_temperature": {"value": 44.0, "lo": 42.0, "hi": 45.5, "unit": "degC"}},
            "expected_effect": "If both limits hold, this replaces the failing protocol."}), 201)
        assert req["state"] == "awaiting_approval"
        approved = ok(c.post(f"{BASE}/campaigns/{cid}/requests/{req['request_id']}/approve", headers=TEAM,
                             json={"author": "Ana", "content_hash": req["content_hash"]}))
        assert approved["approved_by"] == "person:Ana"
    # The first server stopped. Whether its runner had started the request or not, a new server finishes it once.

    with TestClient(app()) as c:
        done = wait_for(c, cid, req["request_id"], ("completed", "failed"))
        assert done["state"] == "completed", done["failure"]
        assert done["result"]["categories"]["validity"] == "within_range"
        view = ok(c.get(f"{BASE}/campaigns/{cid}/view"))
        attempts = [r for r in view["requests"] if r["request_id"] == req["request_id"]]
        assert len(attempts) == 1

        concluded = ok(c.post(f"{BASE}/campaigns/{cid}/conclusion", headers=TEAM, json={
            "agent": "external-agent", "text": "The four-step protocol meets both limits in simulation."}))
        view = ok(c.get(f"{BASE}/campaigns/{cid}/view"))
        assert view["conclusion"]["outcome"] == "improved_in_simulation", (concluded, view["conclusion"])
        exported = c.get(f"{BASE}/campaigns/{cid}/export")
        assert exported.status_code == 200
        (tmp_path / "campaign.loop.json").write_bytes(exported.content)

    result = verify(data / "ledgers" / "lab")
    assert result.ok, result.problems
    assert verify(tmp_path / "campaign.loop.json").ok
