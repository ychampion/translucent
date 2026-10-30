import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from translucent import gate, graph, replay, tools
from translucent.contracts import Campaign, Claim, Measurement, Predicate, ReplayBundle, Run, Source, Uncertainty
from translucent.ledger import Ledger
from translucent.runtime import Campaigner
from translucent.verify import evidence, read_ledger, verify

ROOT = Path(__file__).resolve().parents[1]
FOLLOW = {"cites", "produced_by", "calibrated_by", "computed_from", "read", "derived_from", "output_of"}


def reachable(g, start):
    nodes = {n["id"]: n for n in g["nodes"]}
    out, todo = set(), [start]
    while todo:
        x = todo.pop()
        for e in g["edges"]:
            if e["from"] != x or e["kind"] not in FOLLOW or e["to"] in out:
                continue
            if e["kind"] == "output_of" and nodes[e["to"]]["status"] != "ok":
                continue  # evidence never follows a file back to a failed run
            out.add(e["to"])
            todo.append(e["to"])
    return out


@pytest.mark.parametrize("name", ["fw-chain8", "fw-chain6"])
def test_graph_reaches_exactly_the_evidence_of_every_finding(name):
    with read_ledger(ROOT / "runs" / name) as L:
        for c in L.list("campaign"):
            g = graph.campaign_graph(L, c.id)
            kinds = {n["id"]: n["kind"] for n in g["nodes"]}
            for k in L.list("claim", campaign_id=c.id):
                ev = evidence(L, k.id)
                want = {x["id"] for key in ("measurements", "runs", "artifacts") for x in ev[key]}
                assert reachable(g, k.id) == want
                assert kinds[k.id] == "finding"
                assert {"from": k.id, "to": f"question:{c.id}", "kind": "answers"} in g["edges"]


@pytest.fixture
def recorded(tmp_path):
    ledger = Ledger(tmp_path / "ledger")
    video = tmp_path / "tiny.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 1, (16, 16))
    writer.write(np.zeros((16, 16, 3), dtype=np.uint8))
    writer.release()
    art = ledger.put_file(video, kind="video", media_type="video/mp4", source=Source(uri="experiment:fixture"))
    ledger.add(Campaign(id="c", objective="Check the voltage", title="Fixture", inputs=[art.id],
                        budget_usd=0, created_at=time.time()))
    ledger.add(Run(id="r", campaign_id="c", step_id="s", tool="read_cell_voltage", idempotency_key="r",
                   input_ids=[art.id], status="ok", measurement_ids=["m"], started_at=0, ended_at=0))
    ledger.add(Measurement(id="m", run_id="r", quantity="voltage", value=2, unit="V", artifact_ids=[art.id],
                           uncertainty=Uncertainty(value=0.01, kind="bound", method="fixture")))
    ledger.emit("c", "measurement.recorded", "read_cell_voltage", "Voltage", refs=["m"],
                data={"measurement": ledger.get("measurement", "m").model_dump(mode="json")})
    ledger.emit("c", "tool.finished", "read_cell_voltage", "Read voltage", refs=["r", "m"], data={"status": "ok"})
    ledger.add(Claim(id="k", campaign_id="c", author="fixture", statement="The voltage is near 2 V.",
                     predicate=Predicate(type="value_within", params={"lo": 1.9, "hi": 2.1, "unit": "V"},
                                         measurement_ids=["m"]), created_at=time.time()))
    review = gate.review(ledger, "k")
    ledger.emit("c", "claim.reviewed", "gate", "Finding earned", refs=["k", review.id],
                data={"review": review.model_dump(mode="json")})
    yield ledger, art
    ledger.db.close()


def test_failed_tool_keeps_a_sanitised_summary_and_the_summary_backs_nothing(recorded, monkeypatch):
    ledger, art = recorded
    ctx = tools.Context(ledger, "c")
    here = str(Path(tools.__file__).resolve())
    elsewhere = "C:" + chr(92) + "Users" + chr(92) + "someone" + chr(92) + "lab" + chr(92) + "voltage.csv"
    secret = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"

    def broken(ctx, run_id, rec, video_id):
        raise RuntimeError(f"cannot read {elsewhere} via {here} with key {secret} " + "x" * 5000)
    monkeypatch.setitem(tools.REGISTRY, "broken_tool", {"fn": broken, "title": "Broken", "schema": {}})
    res = tools.call(ctx, "broken_tool", {"video_id": art.id})
    run = ledger.get("run", res.run_id)
    assert run.status == "tool_failure" and len(run.output_ids) == 1
    trace = ledger.get("artifact", run.output_ids[0])
    stored = ledger.blob_path(trace.id).read_text(encoding="utf-8")
    assert trace.label == "Error trace" and trace.kind == "text"
    assert stored.startswith("RuntimeError: cannot read …/voltage.csv via translucent/tools.py with key ")
    assert len(stored) <= 2048 and secret not in stored and "Users" not in stored and "Traceback" not in stored
    assert secret not in (run.error or "") and "Users" not in (run.error or "")
    assert "Traceback" in (ledger.root / "work" / f"{run.id}.trace.txt").read_text(encoding="utf-8")  # private copy
    assert ledger.unsound_source(trace.id)
    assert verify(ledger.root).ok


def test_critic_review_keeps_the_image_it_looked_at(recorded, tmp_path):
    ledger, art = recorded
    camp = Campaigner(ledger, ledger.get("campaign", "c"))
    camp._chat = lambda role, messages, tools_=None: SimpleNamespace(content="SUPPORTED\nThe trace is flat at 2 V.")
    image = tmp_path / "shown.png"
    cv2.imwrite(str(image), np.full((8, 8, 3), 200, np.uint8))
    camp.critique("k", image)
    critic = [r for r in ledger.list("review", ref="k") if r.reviewer == "critic"][0]
    shown = ledger.get("artifact", critic.artifact_ids[0])
    assert shown.id == "sha256:" + hashlib.sha256(image.read_bytes()).hexdigest() and shown.kind == "plot"
    assert ledger.claim_status("k") == "supported"
    assert verify(ledger.root).ok


def test_setup_snapshot_recorded_at_start_and_noted_by_verify(recorded, capsys):
    ledger, art = recorded
    ledger.add(Campaign(id="c2", objective="Check again", title="Again", inputs=[art.id], budget_usd=0,
                        created_at=time.time()))
    camp = Campaigner(ledger, ledger.get("campaign", "c2"))
    camp._loop = lambda messages: {"status": "finished"}
    camp.run()
    started = next(e for e in ledger.events("c2") if e.type == "campaign.started")
    setup = json.loads(ledger.blob_path(started.data["setup_id"]).read_text(encoding="utf-8"))
    assert started.refs == [started.data["setup_id"]]
    assert set(setup["code"]["files"]) >= {"gate.py", "tools.py"} and setup["models"]["planner"]
    assert setup["recordings"][0]["video_id"] == art.id
    result = verify(ledger.root)
    assert result.ok and result.summary() == "1 findings re-checked, 1 match; 2 files intact"
    assert result.notes == [f"Re-checked with the gate code the findings were recorded with "
                            f"({setup['code']['files']['gate.py'][:12]})"]


def test_export_carries_runs_and_the_evidence_closure(recorded, tmp_path):
    ledger, art = recorded
    # a second campaign whose finding cites the first campaign's measurement (as a what-if branch does)
    ledger.add(Campaign(id="b", objective="Branch", title="Branch", inputs=[art.id], budget_usd=0,
                        created_at=time.time()))
    ledger.emit("b", "campaign.started", "runtime", "Branch")
    ledger.add(Claim(id="kb", campaign_id="b", author="fixture", statement="Still near 2 V.",
                     predicate=Predicate(type="value_within", params={"lo": 1.9, "hi": 2.1, "unit": "V"},
                                         measurement_ids=["m"]), created_at=time.time()))
    review = gate.review(ledger, "kb")
    ledger.emit("b", "claim.reviewed", "gate", "Finding earned", refs=["kb", review.id],
                data={"review": review.model_dump(mode="json")})
    bundle = replay.export(ledger, "b", tmp_path / "branch")
    assert [m.id for m in bundle.measurements] == ["m"] and [r.id for r in bundle.runs] == ["r"]
    assert ReplayBundle.model_validate_json((tmp_path / "branch" / "bundle.json").read_text()).runs == bundle.runs
    result = verify(tmp_path / "branch")
    assert result.ok, result.problems
    g = graph.campaign_graph(ledger, "b")
    assert {"from": "m", "to": "r", "kind": "produced_by"} in g["edges"]


def test_tampered_bundle_run_is_a_problem(recorded, tmp_path):
    ledger, _ = recorded
    out = tmp_path / "bundle"
    replay.export(ledger, "c", out)
    body = json.loads((out / "bundle.json").read_text())
    body["runs"][0]["tool"] = "lookup_calibration"
    (out / "bundle.json").write_text(json.dumps(body))
    assert "Bundle run records differ from the evidence snapshot" in verify(out).problems


def test_old_bundle_without_runs_still_loads():
    body = json.loads((ROOT / "web/public/fixtures/m4-team/bundle.json").read_text(encoding="utf-8"))
    bundle = ReplayBundle.model_validate(body)
    assert bundle.contract_version == "1" and bundle.runs == []
