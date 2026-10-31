"""Pre-registered metrics, competing methods, replication, and declaring a winner only on independent evidence."""
import json
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from translucent import analysis, instruments, protocol, replay, rerun, runtime, server, tools
from translucent.contracts import Campaign, Metric, Predicate, Protocol, Source
from translucent.ledger import AdmissionError, Ledger
from translucent.verify import verify

WINDOW = {"lo": 1400, "hi": 1600, "unit": "um"}
LENGTH = Metric(id="length", name="Filament length", quantities=["derived:*"], unit="um",
                predicate=Predicate(type="value_within", params=WINDOW, measurement_ids=[]))


@pytest.fixture(autouse=True)
def records(monkeypatch):
    monkeypatch.setattr(instruments, "spatial_scale", lambda exp: {
        "px_per_mm": 200.0, "uncertainty": 2.0, "method": "scale bar", "source": "fixture records"})
    monkeypatch.setattr(instruments, "frame_times", lambda exp: None)
    monkeypatch.setattr(instruments, "voltage", lambda exp: None)
    monkeypatch.setattr(instruments, "frame_time_uncertainty", lambda exp: None)

    def sandbox(code, files, timeout_s):  # computes from its inputs, like real analysis code
        index = json.loads(files["inputs.json"])
        vals = {rid: json.loads(files[f]) for rid, f in index.items() if f.endswith(".json")}
        scale = next(v for v in vals.values() if v["unit"] == "px/mm")
        px = next(v for v in vals.values() if v["unit"] == "px")
        out = {"value": 1000 * px["value"] / scale["value"],
               "uncertainty": {"value": 1000 * px["value"] / scale["value"] ** 2 * scale["uncertainty"]["value"] + 0.5,
                               "kind": "bound", "method": "scale bound"}}
        return SimpleNamespace(status="ok", outputs={"length.json": json.dumps(out).encode()}, reason="",
                               sandbox_id="sb-fake", terminated=True)
    monkeypatch.setattr(analysis, "run_untrusted", sandbox)
    monkeypatch.setattr(runtime, "_client", lambda *a: (_ for _ in ()).throw(AssertionError("no model calls")))

    def outline(ctx, run_id, rec, video_id, px=300.0, boxes_id=None):   # a deterministic local measuring step
        rec["inputs"] += [video_id] + ([boxes_id] if boxes_id else [])
        mid = tools._measure(rec, run_id, "filament_extent_px", float(px), "px", 1, "resolution", "±1 px",
                             label="Filament extent", artifact_ids=[video_id])
        return tools.ToolResult(run_id, "ok", f"{px} px", data={"measurement_id": mid})
    monkeypatch.setitem(tools.REGISTRY, "fixture_outline", {"fn": outline, "title": "Outlining", "schema": {
        "type": "function", "function": {"name": "fixture_outline", "description": "test", "parameters": {
            "type": "object", "required": ["video_id"], "properties": {"video_id": {"type": "string"},
                                                                      "px": {"type": "number"},
                                                                      "boxes_id": {"type": "string"}}}}}})

    def segment(ctx, run_id, rec, video_id):   # writes the same file every time it runs, like replayed outlines
        rec["inputs"].append(video_id)
        rec["outputs"].append(ctx.ledger.put_json({"boxes": [[1, 2, 3, 4]]}, kind="report", label="Outlines").id)
        return tools.ToolResult(run_id, "ok", "outlined", data={"boxes_id": rec["outputs"][-1]})
    monkeypatch.setitem(tools.REGISTRY, "fixture_segment", {"fn": segment, "title": "Outlining", "schema": {
        "type": "function", "function": {"name": "fixture_segment", "description": "test", "parameters": {
            "type": "object", "required": ["video_id"], "properties": {"video_id": {"type": "string"}}}}}})
    monkeypatch.setattr(rerun, "RERUNNABLE", rerun.RERUNNABLE | {"fixture_outline", "fixture_segment"})


def video(L, tmp_path):
    path = tmp_path / "tiny.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 1, (16, 16))
    for _ in range(3):
        writer.write(np.zeros((16, 16, 3), dtype=np.uint8))
    writer.release()
    return L.put_file(path, kind="video", media_type="video/mp4", label="Fixture movie",
                      source=Source(uri="experiment:fixture", citation="Fixture et al.")).id


def length_by(ctx, scale, px, code):
    res = tools.call(ctx, "run_analysis_code", {"code": code, "inputs": [scale, px],
                                                "outputs": [{"name": "length", "unit": "um", "label": "Length"}]})
    assert res.status == "ok", res.summary
    return res.data["measurements"]["length"]


def attempt(ctx, mid, params=WINDOW, metric="length", type="value_within"):
    res = tools.call(ctx, "propose_finding", {"type": type, "measurement_ids": mid if isinstance(mid, list) else [mid],
                                              "params": params, "statement": "The filament is about 1.5 mm long.",
                                              "metric_id": metric})
    return res.data["claim_id"]


@pytest.fixture
def camp(tmp_path):
    L = Ledger(tmp_path / "ledger")
    vid = video(L, tmp_path)
    L.add(Campaign(id="P", objective="How long is the filament?", title="Methods", inputs=[vid], budget_usd=0,
                   created_at=time.time()))
    pre = L.add(Protocol(id="protocol_1", campaign_id="P", author="Ana", metrics=[LENGTH], created_at=time.time()))
    L.emit("P", "campaign.started", "runtime", "Methods", refs=[pre.id], data={"protocol_id": pre.id})
    ctx = tools.Context(L, "P")
    scale = tools.call(ctx, "lookup_calibration", {"video_id": vid, "quantity": "spatial_scale"}).data["measurement_id"]
    px = tools.call(ctx, "fixture_outline", {"video_id": vid}).data["measurement_id"]
    a = length_by(ctx, scale, px, "# method A: length from the outline and the scale bar")
    b = length_by(ctx, scale, px, "# method B: the same, written differently")
    yield SimpleNamespace(L=L, ctx=ctx, video=vid, scale=scale, px=px, a=a, b=b, root=tmp_path / "ledger",
                          claim_a=attempt(ctx, a), claim_b=attempt(ctx, b))
    L.db.close()


def verified(c, claim_id, measurement_id):
    rep = rerun.replicate(c.L, claim_id)
    assert rep["status"] == "done", rep["reason"]
    again = rep["measurements"][measurement_id]
    res = tools.call(c.ctx, "propose_finding", {"type": "values_agree", "measurement_ids": [measurement_id, again],
                                                "params": {}, "statement": "Re-derived, the length agrees."})
    assert res.data["verdict"] == "supported"
    return res.data["claim_id"], rep


def declare(c, winner, verification=None):
    args = {"metric_id": "length", "winner": winner} | ({"verification": verification} if verification else {})
    res = tools.call(c.ctx, "declare_winner", args)
    return c.L.get("decision", res.data["decision_id"]), res


def failed(d):
    return {k.name for k in d.checks if not k.passed}


def test_protocol_is_fixed_before_the_start_and_verify_flags_a_forged_order(camp, tmp_path):
    c = camp
    with pytest.raises(AdmissionError, match="fixed before the campaign starts"):
        c.L.add(Protocol(id="protocol_2", campaign_id="P", author="Ana", metrics=[LENGTH], created_at=time.time()))
    assert verify(c.root).ok, verify(c.root).problems
    L = Ledger(tmp_path / "forged")
    vid = video(L, tmp_path)
    L.add(Campaign(id="F", objective="x", title="x", inputs=[vid], budget_usd=0, created_at=time.time()))
    L.emit("F", "campaign.started", "runtime", "x")
    late = Protocol(id="protocol_late", campaign_id="F", author="Ana", metrics=[LENGTH], created_at=time.time())
    L.db.execute("INSERT INTO record VALUES (?,?,?,?,?,?)",
                 (late.id, "protocol", "F", None, late.model_dump_json(), time.time()))
    assert "Protocol protocol_late was not recorded before campaign F started" in verify(tmp_path / "forged").problems
    L.db.close()


def test_metric_grammar_refuses_anything_the_gate_cannot_apply_as_written():
    bad = [Metric(id="m", name="x", quantities=["growth"], unit="um/s",
                  predicate=Predicate(type="value_within", params={"lo": 1, "hi": 2, "unit": "um/s", "note": "x"},
                                      measurement_ids=[])),
           Metric(id="m", name="x", quantities=["growth"], unit="um/s",
                  predicate=Predicate(type="value_within", params={"lo": "about 50", "hi": 2, "unit": "um/s"},
                                      measurement_ids=[])),
           Metric(id="m", name="x", quantities=["growth"], unit="um/s",
                  predicate=Predicate(type="values_agree", params={}, measurement_ids=[])),
           Metric(id="m", name="x", quantities=["growth speed!"], unit="um/s",
                  predicate=Predicate(type="value_within", params={"lo": 1, "hi": 2, "unit": "um/s"},
                                      measurement_ids=[])),
           Metric(id="m", name="x", quantities=["growth"], unit="um/s",
                  predicate=Predicate(type="value_within", params={"lo": 3, "hi": 2, "unit": "um/s"},
                                      measurement_ids=[])),
           Metric(id="m", name="x", quantities=["growth"], unit="um/s",
                  predicate=Predicate(type="value_within", params={"lo": 1, "hi": 2, "unit": "mm/s"},
                                      measurement_ids=[]))]
    for m in bad:
        assert protocol.metric_problems(m), m
    assert protocol.metric_problems(LENGTH) == []
    with pytest.raises(ValueError):
        Metric(id="m", name="x", quantities=["g"], unit="s", min_methods=2, needs_verification=True,
               predicate=Predicate(type="value_within", params={"lo": float("nan"), "hi": 2, "unit": "s"},
                                   measurement_ids=[]), extra=1)


def test_off_protocol_window_never_wins(camp):
    c = camp
    fitted = attempt(c.ctx, c.a, params={"lo": 1450, "hi": 1550, "unit": "um"})
    assert c.L.claim_status(fitted) == "supported"                       # the gate verdict is unchanged
    fits, why = protocol.on_protocol(c.L, c.L.get("claim", fitted))
    assert not fits and "off protocol" in why
    check, _ = verified(c, fitted, c.a)
    d, res = declare(c, fitted, check)
    assert d.outcome == "insufficient_evidence" and "winner on protocol" in failed(d)
    assert "No winner yet" in res.summary


def test_decide_refuses_one_method_a_shared_run_and_disagreeing_methods(camp):
    c = camp
    # the two methods share the outline run: comparing them is not an independent verification
    shared = tools.call(c.ctx, "propose_finding", {"type": "values_agree", "measurement_ids": [c.a, c.b],
                                                   "params": {}, "statement": "A and B agree."}).data["claim_id"]
    d, _ = declare(c, c.claim_a, shared)
    assert d.outcome == "insufficient_evidence" and failed(d) == {"verified by an independent run"}
    assert "shares" in next(k.detail for k in d.checks if k.name == "verified by an independent run")
    # a third method on a different outline disagrees
    px2 = tools.call(c.ctx, "fixture_outline", {"video_id": c.video, "px": 400}).data["measurement_id"]
    far = length_by(c.ctx, c.scale, px2, "# method C: a different outline")
    assert c.L.claim_status(attempt(c.ctx, far)) == "refuted"
    check, _ = verified(c, c.claim_a, c.a)
    d, _ = declare(c, c.claim_a, check)
    assert d.outcome == "insufficient_evidence" and failed(d) == {"methods agree"}


def test_one_method_is_not_enough(tmp_path):
    L = Ledger(tmp_path / "ledger")
    vid = video(L, tmp_path)
    L.add(Campaign(id="P", objective="x", title="x", inputs=[vid], budget_usd=0, created_at=time.time()))
    L.add(Protocol(id="protocol_1", campaign_id="P", author="Ana", metrics=[LENGTH], created_at=time.time()))
    L.emit("P", "campaign.started", "runtime", "x", refs=["protocol_1"])
    ctx = tools.Context(L, "P")
    scale = tools.call(ctx, "lookup_calibration", {"video_id": vid, "quantity": "spatial_scale"}).data["measurement_id"]
    px = tools.call(ctx, "fixture_outline", {"video_id": vid}).data["measurement_id"]
    a = length_by(ctx, scale, px, "# method A")
    c = SimpleNamespace(L=L, ctx=ctx)
    claim = attempt(ctx, a)
    check, _ = verified(c, claim, a)
    d, _ = declare(c, claim, check)
    assert d.outcome == "insufficient_evidence" and failed(d) == {"methods attempted"}
    L.db.close()


def test_winner_after_an_independent_replication_rechecks_offline(camp, tmp_path):
    c = camp
    check, rep = verified(c, c.claim_a, c.a)
    assert len(rep["runs"]) == 2                                          # the outline and the analysis, not the scale
    assert all(c.L.get("run", r).step_id == rep["step"] for r in rep["runs"])
    d, res = declare(c, c.claim_a, check)
    assert d.outcome == "winner", d.checks
    assert d.replication_run_ids == rep["runs"] and set(d.attempt_claim_ids) == {c.claim_a, c.claim_b}
    assert "Winner declared" in res.summary
    result = verify(c.root)
    assert result.ok, result.problems
    assert result.summary().endswith(", 1 decisions re-checked")
    bundle = replay.export(c.L, "P", tmp_path / "bundle", evidence_only=True)
    assert bundle.protocol.id == "protocol_1" and [x.id for x in bundle.decisions] == [d.id]
    offline = verify(tmp_path / "bundle")
    assert offline.ok, offline.problems
    assert offline.decisions == 1
    board = protocol.board(c.L, "P")
    rows = board["metrics"][0]["attempts"]
    assert board["pre_registered"] and {r["claim_id"] for r in rows} == {c.claim_a, c.claim_b}
    assert all(r["on_protocol"] for r in rows) and board["metrics"][0]["decisions"][0]["outcome"] == "winner"


def test_a_forged_decision_fails_the_recheck(camp):
    c = camp
    d, _ = declare(c, c.claim_a)                                          # no verification: no winner
    assert d.outcome == "insufficient_evidence"
    forged = d.model_copy(update={"id": "decision_forged", "outcome": "winner"})
    c.L.db.execute("INSERT INTO record VALUES (?,?,?,?,?,?)",
                   (forged.id, "decision", "P", None, forged.model_dump_json(), time.time()))
    assert any(p.startswith("Decision decision_forged: recorded winner, re-check gives insufficient_evidence")
               for p in verify(c.root).problems)


def test_tie_when_two_methods_are_each_verified(camp):
    c = camp
    check_a, _ = verified(c, c.claim_a, c.a)
    verified(c, c.claim_b, c.b)
    d, _ = declare(c, c.claim_a, check_a)
    assert d.outcome == "tie", d.checks


def test_a_failed_replication_blocks_a_winner(camp, monkeypatch):
    c = camp
    check, _ = verified(c, c.claim_a, c.a)
    monkeypatch.setattr(analysis, "run_untrusted", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sandbox down")))
    second = rerun.replicate(c.L, c.claim_a)
    assert second["status"] == "failed" and second["step"].endswith(":2")
    d, _ = declare(c, c.claim_a, check)
    assert d.outcome == "insufficient_evidence" and failed(d) == {"every replication succeeded"}
    assert set(second["runs"]) <= set(d.replication_run_ids)


def test_ledgers_without_protocols_are_unchanged(camp):
    assert protocol.board(camp.L, "missing") == {"protocol": None, "metrics": []}
    text = protocol.prompt(camp.L, "P")
    assert "length: Filament length" in text and "declare_winner" in text and protocol.prompt(camp.L, "x") == ""
    assert "decisions" not in verify(camp.root).summary()            # no decisions yet: the summary is as before


METRIC = {"id": "length", "name": "Filament length", "quantities": ["derived:*"], "unit": "um",
          "test": {"type": "value_within", "params": WINDOW}}


def test_post_campaign_records_the_protocol_before_the_start(tmp_path, monkeypatch):
    L = Ledger(tmp_path / "ledger")
    video(L, tmp_path)
    L.db.close()
    monkeypatch.setenv("TRANSLUCENT_LIVE", "1")
    monkeypatch.setenv("TRANSLUCENT_ADMIN_TOKEN", "admin")
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")  # a placeholder: the fake campaigner never calls a provider
    headers = {"Authorization": "Bearer admin"}
    seen = {}

    class Fake:
        def __init__(self, ledger, campaign, settings):
            self.L, self.c = ledger, campaign

        def run(self):
            seen["protocol"] = protocol.protocol_of(self.L, self.c.id)
            pre = seen["protocol"]
            self.L.emit(self.c.id, "campaign.started", "runtime", "Start", refs=[pre.id])
            self.L.emit(self.c.id, "campaign.finished", "runtime", "Done")
            return {"status": "finished"}

    body = {"objective": "How long is it?", "experiment_id": "fixture", "profile": "dev", "budget": 0.25,
            "protocol": {"author": "Ana", "metrics": [METRIC]}}
    with TestClient(server.create_app(tmp_path / "ledger", tmp_path / "api", campaigner_factory=Fake,
                                      poll_interval=0.01)) as client:
        for bad in ({**METRIC, "test": {"type": "value_within", "params": {**WINDOW, "lo": "x"}}},
                    {**METRIC, "test": {"type": "value_within", "params": {**WINDOW, "extra": 1}}},
                    {**METRIC, "test": {"type": "exists", "params": {}}}):
            r = client.post("/api/campaigns", json={**body, "protocol": {"author": "Ana", "metrics": [bad]}},
                            headers=headers)
            assert r.status_code == 422, r.text
        assert client.get("/api/campaigns").json() == []
        made = client.post("/api/campaigns", json=body, headers=headers)
        assert made.status_code == 202, made.text
        cid = made.json()["id"]
        deadline = time.time() + 30
        while client.get(f"/api/campaigns/{cid}").json()["job"]["status"] != "finished":
            assert time.time() < deadline
            time.sleep(0.05)
        board = client.get(f"/api/campaigns/{cid}/protocol").json()
        assert board["pre_registered"] and board["metrics"][0]["metric"]["id"] == "length"
        assert seen["protocol"].metrics[0].predicate.params == WINDOW
        assert client.get("/api/campaigns/nope/protocol").status_code == 404
    assert verify(tmp_path / "ledger").ok


def test_a_replica_that_remakes_the_same_file_is_still_independent(camp):
    """Re-executing a step writes a byte-identical, so same-id, file: the original chain must not adopt the replica's
    run as a maker of the file it read long before."""
    c = camp
    c.ctx.step_id = "s5"                                                 # outlined in one step, measured in the next
    boxes = tools.call(c.ctx, "fixture_segment", {"video_id": c.video}).data["boxes_id"]
    c.ctx.step_id = "s6"
    px = tools.call(c.ctx, "fixture_outline", {"video_id": c.video, "boxes_id": boxes, "px": 299}).data["measurement_id"]
    a2 = length_by(c.ctx, c.scale, px, "# method A2: from the segmented outline")
    claim = attempt(c.ctx, a2)
    check, rep = verified(c, claim, a2)
    assert len(rep["runs"]) == 3
    assert c.L.get("run", rep["runs"][0]).output_ids == [boxes]          # the same file, made again
    d, _ = declare(c, claim, check)
    assert "verified by an independent run" not in failed(d), [k.detail for k in d.checks]
