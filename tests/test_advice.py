"""What-next advice, the untraced-number check on the final answer, sign-offs bound to their evidence, secrets scrubbed
at intake, and idempotent people's writes."""
import json
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from translucent import advice, analysis, experiments, server, tools
from translucent.contracts import (Campaign, Claim, Lesson, Measurement, Predicate, Review, Run, Source,
                                   Uncertainty)
from translucent.ledger import Ledger

SECRET = "sk-" + "a1B2c3D4e5F6g7H8i9J0"
TEAM = {"Authorization": "Bearer team-secret"}


@pytest.fixture
def world(tmp_path):
    L = Ledger(tmp_path / "ledger")
    path = tmp_path / "tiny.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 1, (16, 16))
    for _ in range(2):
        writer.write(np.zeros((16, 16, 3), dtype=np.uint8))
    writer.release()
    vid = L.put_file(path, kind="video", media_type="video/mp4", source=Source(uri="experiment:fixture")).id
    L.add(Campaign(id="C", objective="x", title="x", inputs=[vid], budget_usd=0, created_at=time.time()))
    L.emit("C", "campaign.started", "runtime", "Start")
    n = iter(range(100))

    def measured(quantity, value, unit, tool="measure_growth", u=1.0, calibration_ids=()):
        i = next(n)
        L.add(Run(id=f"r{i}", campaign_id="C", step_id="s1", tool=tool, idempotency_key=f"k{i}", input_ids=[vid],
                  measurement_ids=[f"m{i}"], status="ok", started_at=time.time(), ended_at=time.time()))
        return L.add(Measurement(id=f"m{i}", run_id=f"r{i}", quantity=quantity, value=value, unit=unit,
                                 artifact_ids=[vid], calibration_ids=list(calibration_ids),
                                 uncertainty=Uncertainty(value=u, kind="bound", method="x"))).id

    def voltage(value=89.55):   # a short time read from the cell's log, on the recorded clock
        clock = measured("time_base", 1.0, "s", tool="lookup_calibration", u=0.0)
        return measured("voltage_collapse_time", value, "s", tool="read_cell_voltage", u=0.5, calibration_ids=[clock])

    def claim(mid, params, statement="A finding.", supersedes=None, type="value_within"):
        cid = f"k{next(n)}"
        L.add(Claim(id=cid, campaign_id="C", author="worker", statement=statement, supersedes=supersedes,
                    created_at=time.time(), predicate=Predicate(type=type, params=params,
                                                                measurement_ids=mid if isinstance(mid, list) else [mid])))
        from translucent import gate
        gate.review(L, cid)
        return cid
    yield SimpleNamespace(L=L, video=vid, measured=measured, claim=claim, voltage=voltage, root=tmp_path / "ledger",
                          state=tmp_path / "api")
    L.db.close()


def rules(L):
    return [(a["rule"], a["refs"]) for a in advice.next_steps(L, "C")]


def test_next_steps_name_what_is_missing(world):
    w = world
    speed = w.measured("growth_velocity", 60.0, "um/s")
    first = w.claim(speed, {"lo": 50, "hi": 70, "unit": "um/s"})
    second = w.claim(speed, {"lo": 55, "hi": 65, "unit": "um/s"})
    got = rules(w.L)
    assert ("missing calibration", [first]) in got and ("missing calibration", [second]) in got
    assert ("same check failed twice", [first, second]) in got
    volts = w.voltage(89.6)
    supported = w.claim(volts, {"lo": 80, "hi": 100, "unit": "s"}, statement="It shorts at about 90 s.")
    assert w.L.claim_status(supported) == "supported"
    assert ("one method only", [supported]) in rules(w.L)
    w.L.add(Review(id="rv_h", claim_id=supported, reviewer="human", verdict="refuted", author="Ana",
                   reason="The log shows two drops.", created_at=time.time()))
    assert ("challenge unanswered", [supported, "rv_h"]) in rules(w.L)
    w.claim(volts, {"lo": 85, "hi": 95, "unit": "s"}, supersedes=supported)
    assert not [r for r in rules(w.L) if r[0] == "challenge unanswered"]
    w.L.add(Lesson(id="lesson_x", text="Read the log first.", scope="method:timing", evidence_ids=[volts],
                   learned_in="C", created_at=time.time()))
    w.L.emit("C", "memory.loaded", "runtime", "Remembering 1 lesson", refs=["lesson_x"])
    assert ("lesson unused", ["lesson_x"]) in rules(w.L)


def test_numbers_in_the_answer_must_trace_to_supported_findings(world):
    w = world
    volts = w.voltage()
    w.claim(volts, {"lo": 80, "hi": 100, "unit": "s"})
    w.L.emit("C", "campaign.finished", "runtime", "Done", data={
        "answer": "The cell shorted at about 90 s (between 80 and 100 s), at 89.6 s by the log; it grew 2 mm."})
    out = advice.untraced_numbers(w.L, "C")
    assert out["numbers"] == 4
    assert [u["text"] for u in out["untraced"]] == ["2 mm"]


def test_signoff_is_bound_to_the_evidence_it_saw(world):
    w = world
    volts = w.voltage()
    k = w.claim(volts, {"lo": 80, "hi": 100, "unit": "s"})
    w.L.add(Review(id="rv_ok", claim_id=k, reviewer="human", verdict="supported", author="ana",
                   reason="Agreed.", created_at=time.time()))
    (s,) = advice.signoffs(w.L, k)
    assert s["current"] and s["text"] == "Ana reviewed this exact evidence"
    run = w.L.get("measurement", volts).run_id
    w.L.invalidate_run(run, "wrong log")
    (s,) = advice.signoffs(w.L, k)
    assert not s["current"] and s["evidence_digest"] != s["current_digest"]
    assert s["text"] == f"Evidence changed since ana reviewed it (run {run} invalidated)"


def test_secrets_are_scrubbed_from_agent_code_before_anything_is_recorded(world, monkeypatch):
    w = world
    seen = {}

    def sandbox(code, files, timeout_s):
        seen["code"] = code
        return SimpleNamespace(status="ok", outputs={"x.json": json.dumps({"value": 1.0, "uncertainty": {
            "value": 0.1, "kind": "bound", "method": "x"}}).encode()}, reason="", sandbox_id="sb", terminated=True)
    monkeypatch.setattr(analysis, "run_untrusted", sandbox)
    speed = w.measured("growth_velocity", 60.0, "um/s")
    res = tools.call(tools.Context(w.L, "C"), "run_analysis_code", {
        "code": f"API_KEY = '{SECRET}'\n# compute", "inputs": [speed], "outputs": [{"name": "x", "unit": "um/s", "label": "X"}]})
    run = w.L.get("run", res.run_id)
    stored = [w.L.blob_path(i).read_text(encoding="utf-8") for i in run.input_ids
              if (a := w.L.get("artifact", i)) and a.kind == "code"]
    assert SECRET not in json.dumps(run.params) and stored and SECRET not in stored[0]
    assert SECRET not in json.dumps([e.model_dump(mode="json") for e in w.L.events("C")])
    assert SECRET not in seen["code"]                                       # the code that ran is the code recorded


def test_manifest_text_is_scrubbed():
    m = experiments.scrubbed({"id": "lab-1", "title": "Cell", "notes": f"token={SECRET}", "video": "a.mp4",
                              "scale": {"um_per_px": 1, "source": f"bearer {SECRET}"}})
    assert SECRET not in json.dumps(m) and m["video"] == "a.mp4"


@pytest.fixture
def client(world, monkeypatch):
    monkeypatch.setenv("TRANSLUCENT_TEAM_TOKEN", "team-secret")
    with TestClient(server.create_app(world.root, world.state, poll_interval=0.01)) as c:
        yield c


def test_people_text_is_scrubbed_and_writes_are_idempotent(world, client):
    w = world
    body = {"author": "Ana", "text": f"my key is {SECRET}"}
    key = {**TEAM, "Idempotency-Key": "note-1"}
    first = client.post("/api/campaigns/C/notes", json=body, headers=key)
    again = client.post("/api/campaigns/C/notes", json=body, headers=key)
    assert first.status_code == again.status_code == 201 and first.json() == again.json()
    notes = [e for e in w.L.events("C") if e.type == "human.note"]
    assert len(notes) == 1 and SECRET not in notes[0].data["text"] and "[secret removed]" in notes[0].data["text"]
    assert client.post("/api/campaigns/C/notes", json={**body, "text": "other"}, headers=key).status_code == 409
    assert client.post("/api/campaigns/C/notes", json=body, headers={**TEAM, "Idempotency-Key": "bad key!"}
                       ).status_code == 400
    assert client.post("/api/campaigns/C/notes", json=body, headers=TEAM).status_code == 201   # no key: a new note
    volts = w.voltage()
    k = w.claim(volts, {"lo": 80, "hi": 100, "unit": "s"})
    review = {"author": "Ben", "claim_id": k, "verdict": "challenge", "reason": f"see {SECRET}"}
    rk = {**TEAM, "Idempotency-Key": "review-1"}
    assert client.post("/api/campaigns/C/reviews", json=review, headers=rk).json() == \
        client.post("/api/campaigns/C/reviews", json=review, headers=rk).json()
    humans = [r for r in w.L.list("review", ref=k) if r.reviewer == "human"]
    assert len(humans) == 1 and SECRET not in humans[0].reason
    signed = client.get(f"/api/findings/{k}/evidence").json()["signoffs"]
    assert signed[0]["review_id"] == humans[0].id and signed[0]["current"]
    nxt = client.get("/api/campaigns/C/next").json()
    assert ("challenge unanswered", [k, humans[0].id]) in [(a["rule"], a["refs"]) for a in nxt["advice"]]
    assert nxt["answer"] == {"answer": None, "numbers": 0, "untraced": []}
    assert client.get("/api/campaigns/nope/next").status_code == 404
