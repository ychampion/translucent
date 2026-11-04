"""What-if branches: forks, the hidden assumption argument, the scoped scale-conflict rule, deterministic rerun."""
import json
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from translucent import analysis, gate, instruments, mcp_server, providers, replay, rerun, runtime, server, tools
from translucent.contracts import Campaign, Claim, Fork, Measurement, Override, Predicate, Run, Source, Uncertainty
from translucent.ledger import AdmissionError, Ledger
from translucent.verify import verify

CODE = "# reads the scale and the pixel length from /work/inputs"


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


@pytest.fixture
def parent(tmp_path):
    L = Ledger(tmp_path / "ledger")
    path = tmp_path / "tiny.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 1, (16, 16))
    for _ in range(3):
        writer.write(np.zeros((16, 16, 3), dtype=np.uint8))
    writer.release()
    video = L.put_file(path, kind="video", media_type="video/mp4", label="Fixture movie",
                       source=Source(uri="experiment:fixture", citation="Fixture et al."))
    L.add(Campaign(id="P", objective="How long is the filament?", title="Parent", inputs=[video.id], budget_usd=0,
                   created_at=time.time()))
    L.emit("P", "campaign.started", "runtime", "Parent")
    ctx = tools.Context(L, "P")
    scale = tools.call(ctx, "lookup_calibration", {"video_id": video.id, "quantity": "spatial_scale"})
    scale_id = scale.data["measurement_id"]
    L.add(Run(id="px_run", campaign_id="P", step_id="s2", tool="fixture_outline", idempotency_key="px",
              input_ids=[video.id], measurement_ids=["px_len"], status="ok", started_at=time.time(),
              ended_at=time.time()))
    L.add(Measurement(id="px_len", run_id="px_run", quantity="filament_extent_px", value=300.0, unit="px",
                      label="Filament extent", artifact_ids=[video.id],
                      uncertainty=Uncertainty(value=1, kind="resolution", method="±1 px")))
    res = tools.call(ctx, "run_analysis_code", {"code": CODE, "inputs": [scale_id, "px_len"],
                                                "outputs": [{"name": "length", "unit": "um", "label": "Length"}]})
    length = res.data["measurements"]["length"]
    found = tools.call(ctx, "propose_finding", {"type": "value_within", "measurement_ids": [length],
                                                "params": {"lo": 1400, "hi": 1600, "unit": "um"},
                                                "statement": "The filament is about 1.5 mm long."})
    assert found.data["verdict"] == "supported"
    yield SimpleNamespace(L=L, video=video.id, scale=scale_id, length=length, claim=found.data["claim_id"],
                          root=tmp_path / "ledger")
    L.db.close()


def what_if(p, value, author="Ben", uncertainty=1.0, quantity="spatial_scale", unit="px/mm"):
    return rerun.create_fork(p.L, "P", author=author, reason="the scale bar may be misread",
                             overrides=[{"quantity": quantity, "value": value, "uncertainty": uncertainty,
                                         "unit": unit}], claim_id=p.claim)


def test_rerun_redoes_only_dirty_runs_and_leaves_the_parent_untouched(parent):
    p = parent
    before_events = [e for e in p.L.events("P")]
    status_before = p.L.claim_status(p.claim)
    fork = what_if(p, 150.0)
    out = rerun.rerun(p.L, fork.id)
    assert out["status"] == "done" and len(out["reran"]) == 2           # the scale lookup and the analysis
    assert out["cited"] == ["px_run"]                                   # the outline is cited, not recomputed
    branch = p.L.get("claim", out["claim_id"])
    assert branch.campaign_id == fork.campaign_id and branch.supersedes is None
    m = p.L.get("measurement", branch.predicate.measurement_ids[0])
    assert m.value == pytest.approx(2000.0)                             # 300 px at 150 px/mm
    assert out["verdict"] == "refuted"
    review = [r for r in p.L.list("review", ref=branch.id) if r.reviewer == "gate"][-1]
    assert any("declared by Ben in a what-if branch" in c.detail for c in review.checks)
    # the parent: same status, same events plus one annotation
    after = p.L.events("P")
    assert p.L.claim_status(p.claim) == status_before
    assert after[:len(before_events)] == before_events
    assert [e.type for e in after[len(before_events):]] == ["fork.created"]
    assert verify(p.root).ok, verify(p.root).problems


def test_same_fork_twice_gives_equal_values(parent):
    values = []
    for _ in range(2):
        out = rerun.rerun(parent.L, what_if(parent, 190.0).id)
        claim = parent.L.get("claim", out["claim_id"])
        values.append(parent.L.get("measurement", claim.predicate.measurement_ids[0]).value)
    assert values[0] == values[1] == pytest.approx(1000 * 300 / 190)


def test_assumption_refused_from_the_planner_mcp_and_other_campaigns(parent):
    p = parent
    fork = what_if(p, 150.0)
    args = {"video_id": p.video, "quantity": "spatial_scale", "assumption": fork.id}
    planner = tools.call(tools.Context(p.L, fork.campaign_id), "lookup_calibration", args)
    assert planner.status == "tool_failure" and "person's what-if" in planner.summary
    elsewhere = tools.call(tools.Context(p.L, "P", assumptions=frozenset({fork.id})), "lookup_calibration", args)
    assert elsewhere.status == "tool_failure"
    with pytest.raises(ValueError, match="extra fields"):
        mcp_server._arguments(mcp_server._schema("lookup_calibration"), args)
    assert "assumption" not in json.dumps(tools.REGISTRY["lookup_calibration"]["schema"])
    assert "assumption" not in json.dumps(tools.REGISTRY["read_cell_voltage"]["schema"])


def test_branch_scale_never_conflicts_across_campaigns_but_does_within_one(parent):
    p = parent
    fork = what_if(p, 150.0)
    out = rerun.rerun(p.L, fork.id)
    scale_check = lambda cid: next(c for c in [r for r in p.L.list("review", ref=cid) if r.reviewer == "gate"][-1].checks
                                   if c.name == "scale and timing recorded")
    assert scale_check(out["claim_id"]).passed                          # the parent's recorded scale is not a conflict
    again = tools.call(tools.Context(p.L, "P"), "propose_finding", {
        "type": "value_within", "measurement_ids": [p.length], "params": {"lo": 1400, "hi": 1600, "unit": "um"},
        "statement": "Still about 1.5 mm."})
    assert again.data["verdict"] == "supported"                         # nor is the branch's scale, in the parent
    # inside the branch, the recorded scale beside the declared one is a real disagreement
    tools.call(tools.Context(p.L, fork.campaign_id), "lookup_calibration",
               {"video_id": p.video, "quantity": "spatial_scale"})
    inside = tools.call(tools.Context(p.L, fork.campaign_id), "propose_finding", {
        "type": "value_within", "measurement_ids": p.L.get("claim", out["claim_id"]).predicate.measurement_ids,
        "params": {"lo": 1900, "hi": 2100, "unit": "um"}, "statement": "About 2 mm under the what-if."})
    assert inside.data["verdict"] == "unsupported"
    assert "records disagree on the image scale" in scale_check(inside.data["claim_id"]).detail


def test_branch_that_needs_sam_stops_with_a_plain_reason(parent):
    p = parent
    p.L.add(Run(id="sam", campaign_id="P", step_id="s9", tool="track_objects", idempotency_key="sam",
                input_ids=[p.video, p.scale], measurement_ids=["tracked"], status="ok", started_at=time.time(),
                ended_at=time.time()))
    p.L.add(Measurement(id="tracked", run_id="sam", quantity="filament_extent_px", value=10.0, unit="px",
                        artifact_ids=[p.video], uncertainty=Uncertainty(value=1, kind="resolution", method="px")))
    claim = p.L.add(Claim(id="k_sam", campaign_id="P", author="worker", statement="Ten pixels.", created_at=time.time(),
                          predicate=Predicate(type="value_within", params={"lo": 9, "hi": 11, "unit": "px"},
                                              measurement_ids=["tracked"])))
    gate.review(p.L, claim.id)
    fork = rerun.create_fork(p.L, "P", author="Ana", reason="scale", claim_id="k_sam",
                             overrides=[{"quantity": "spatial_scale", "value": 150, "unit": "px/mm"}])
    out = rerun.rerun(p.L, fork.id)
    assert out["status"] == "needs_agent" and "track_objects" in out["reason"] and "needs the agent" in out["reason"]
    assert out["claim_id"] is None


def test_fork_validation(parent):
    p = parent
    with pytest.raises(ValueError, match="does not depend on the short-circuit threshold"):
        what_if(p, 0.2, quantity="short_threshold_v", unit="V")
    with pytest.raises(LookupError):
        rerun.create_fork(p.L, "P", author="Ben", reason="x", claim_id="missing",
                          overrides=[{"quantity": "spatial_scale", "value": 150, "unit": "px/mm"}])
    with pytest.raises(AdmissionError):
        p.L.add(Fork(id="fork_self", campaign_id="P", parent_campaign_id="P", claim_id=p.claim, author="Ben",
                     reason="x", overrides=[Override(quantity="spatial_scale", value=1, unit="px/mm")],
                     created_at=0))


def test_finding_window_what_if_changes_only_the_range(parent):
    p = parent
    fork = rerun.create_fork(p.L, "P", author="Ana", reason="the paper's range", claim_id=p.claim,
                             overrides=[{"quantity": "finding_window", "value": [1450, 1550], "unit": "um"}])
    out = rerun.rerun(p.L, fork.id)
    claim = p.L.get("claim", out["claim_id"])
    assert out["reran"] == [] and claim.predicate.measurement_ids == [p.length]
    assert claim.predicate.params == {"lo": 1450.0, "hi": 1550.0, "unit": "um"}


def test_verify_flags_forged_what_ifs(parent):
    p = parent
    fork = what_if(p, 150.0)
    rerun.rerun(p.L, fork.id)
    assert verify(p.root).ok
    p.L.add(Run(id="forged", campaign_id="P", step_id="s", tool="lookup_calibration", idempotency_key="forged",
                input_ids=[p.video], params={"assumption": fork.id}, status="ok", started_at=0, ended_at=0))
    p.L.add(Fork(id="fork_unused", campaign_id=fork.campaign_id, parent_campaign_id="P", claim_id=p.claim,
                 author="Ben", reason="x", created_at=0,
                 overrides=[Override(quantity="short_threshold_v", value=0.2, unit="V")]))
    problems = verify(p.root).problems
    assert "Branch measurement claims a what-if it was not created under (run forged)" in problems
    assert "Fork fork_unused overrides a quantity its finding never used" in problems


def test_exported_branch_verifies_offline_with_only_its_evidence(parent, tmp_path):
    p = parent
    fork = what_if(p, 150.0)
    out = rerun.rerun(p.L, fork.id)
    bundle = replay.export(p.L, fork.campaign_id, tmp_path / "branch", evidence_only=True)
    assert [f.id for f in bundle.forks] == [fork.id]
    assert {"px_len", p.length} <= {m.id for m in bundle.measurements}   # the parent's outline and original result
    assert out["claim_id"] in {k.id for k in bundle.claims}
    result = verify(tmp_path / "branch")
    assert result.ok, result.problems
    assert result.summary() == "1 findings re-checked, 1 match; " + result.summary().split("; ")[1]
    from translucent.graph import campaign_graph
    g = campaign_graph(p.L, fork.campaign_id)
    assert {"from": out["claim_id"], "to": p.claim, "kind": "forked_from"} in g["edges"]


def test_branches_lists_forks_and_alternatives(parent):
    p = parent
    rerun.rerun(p.L, what_if(p, 150.0).id)
    b = rerun.branches(p.L, p.claim)
    assert len(b["forks"]) == 1 and b["forks"][0]["status"] == "done"
    assert b["forks"][0]["findings"][0]["status"] == "refuted"
    assert mcp_server.call(p.root, "list_branches", {"claim_id": p.claim})["forks"][0]["status"] == "done"


def test_override_values_are_finite_positive_and_in_the_right_unit(parent):
    p = parent
    for value in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            Override(quantity="spatial_scale", value=value, unit="px/mm")
    with pytest.raises(ValueError):
        Override(quantity="spatial_scale", value=150, unit="px/mm", sneaky=1)
    bad = [({"value": 150, "uncertainty": -1}, "cannot be negative"),
           ({"value": 0}, "greater than 0"),
           ({"value": -150}, "greater than 0"),
           ({"value": 150, "unit": "mm/px"}, "px/mm or um/px"),
           ({"value": [150, 160]}, "one number")]
    for change, message in bad:
        o = {"quantity": "spatial_scale", "value": 150, "unit": "px/mm", **change}
        with pytest.raises(ValueError, match=message):
            rerun.create_fork(p.L, "P", author="Ben", reason="x", claim_id=p.claim, overrides=[o])
    nan = Override.model_construct(quantity="spatial_scale", value=float("nan"), uncertainty=0.0, unit="px/mm",
                                   replaces=None)
    with pytest.raises(ValueError, match="finite"):
        rerun.create_fork(p.L, "P", author="Ben", reason="x", claim_id=p.claim, overrides=[nan])
    for window, message in (([1600, 1400], "lo <= hi"), (-5, "at least 0")):
        with pytest.raises(ValueError, match=message):
            rerun.create_fork(p.L, "P", author="Ben", reason="x", claim_id=p.claim,
                              overrides=[{"quantity": "finding_window", "value": window, "unit": "um"}])
    with pytest.raises(ValueError, match="finding's unit"):
        rerun.create_fork(p.L, "P", author="Ben", reason="x", claim_id=p.claim,
                          overrides=[{"quantity": "finding_window", "value": [1, 2], "unit": "mm"}])
    assert p.L.list("fork") == []


def test_declared_scale_counts_only_for_findings_of_its_own_branch(parent):
    p = parent
    out = rerun.rerun(p.L, what_if(p, 150.0).id)
    branch_length = p.L.get("claim", out["claim_id"]).predicate.measurement_ids
    # a parent finding that cites the branch's result leans on a scale someone declared, not one on record
    leaning = tools.call(tools.Context(p.L, "P"), "propose_finding", {
        "type": "value_within", "measurement_ids": branch_length, "params": {"lo": 1900, "hi": 2100, "unit": "um"},
        "statement": "About 2 mm."})
    assert leaning.data["verdict"] != "supported"
    review = [r for r in p.L.list("review", ref=leaning.data["claim_id"]) if r.reviewer == "gate"][-1]
    scale = next(c for c in review.checks if c.name == "scale and timing recorded")
    assert not scale.passed and "no recorded spatial_scale" in scale.detail
    assert verify(p.root).ok, verify(p.root).problems


TEAM = {"Authorization": "Bearer team-secret-0123456789ab-32chars-minimum"}
ADMIN = {"Authorization": "Bearer admin-secret-0123456789-32chars-minimum"}


@pytest.fixture
def no_providers(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a local what-if called a provider or the sandbox")
    monkeypatch.setattr(providers.Metered, "__init__", refuse)
    monkeypatch.setattr(analysis, "run_untrusted", refuse)


def test_local_rerun_runs_in_the_request_without_providers(parent, tmp_path, monkeypatch, no_providers):
    p = parent
    monkeypatch.setenv("TRANSLUCENT_TEAM_TOKEN", "team-secret-0123456789ab-32chars-minimum")
    monkeypatch.setenv("TRANSLUCENT_TEAM_NAME", "Ana")             # the team key is Ana's
    for var in ("TRANSLUCENT_LIVE", "TRANSLUCENT_ADMIN_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    scale = tools.call(tools.Context(p.L, "P"), "propose_finding", {
        "type": "value_within", "measurement_ids": [p.scale], "params": {"lo": 190, "hi": 210, "unit": "px/mm"},
        "statement": "The scale bar reads about 200 px per mm."})
    body = {"author": "Ana", "claim_id": scale.data["claim_id"], "reason": "a longer scale bar",
            "overrides": [{"quantity": "spatial_scale", "value": 150, "uncertainty": 1, "unit": "px/mm"}]}
    with TestClient(server.create_app(p.root, tmp_path / "api", poll_interval=0.01), headers=TEAM) as c:
        made = c.post("/api/campaigns/P/forks", json=body, headers=TEAM)
        assert made.status_code == 201, made.text
        assert made.json()["status"] == "done" and made.json()["job"] is None
        assert made.json()["findings"][0]["status"] == "refuted"
        monkeypatch.setattr(rerun, "MAX_FORKS_PER_CLAIM", 1)
        assert c.post("/api/campaigns/P/forks", json=body, headers=TEAM).status_code == 429
    assert len(p.L.list("fork")) == 1


def test_rerun_reaching_the_sandbox_is_an_authorized_queued_job(parent, tmp_path, monkeypatch):
    p = parent
    monkeypatch.setenv("TRANSLUCENT_TEAM_TOKEN", "team-secret-0123456789ab-32chars-minimum")
    monkeypatch.setenv("TRANSLUCENT_ADMIN_TOKEN", "admin-secret-0123456789-32chars-minimum")
    for var in ("TRANSLUCENT_TEAM_NAME", "TRANSLUCENT_ADMIN_NAME"):  # Ben holds both: an engineer's and an admin's key
        monkeypatch.setenv(var, "Ben")
    monkeypatch.delenv("TRANSLUCENT_LIVE", raising=False)
    calls = []
    sandbox = analysis.run_untrusted
    monkeypatch.setattr(analysis, "run_untrusted", lambda *a, **k: calls.append(1) or sandbox(*a, **k))
    body = {"author": "Ben", "claim_id": p.claim, "reason": "the scale bar may be misread",
            "overrides": [{"quantity": "spatial_scale", "value": 150, "uncertainty": 1, "unit": "px/mm"}]}
    with TestClient(server.create_app(p.root, tmp_path / "api", poll_interval=0.01)) as c:
        assert c.post("/api/campaigns/P/forks", json=body).status_code == 401                 # no key: sign in
        assert c.post("/api/campaigns/P/forks", json=body, headers=TEAM).status_code == 403   # team token only
        assert c.post("/api/campaigns/P/forks", json=body, headers=ADMIN).status_code == 403  # live not opted in
        monkeypatch.setenv("TRANSLUCENT_LIVE", "1")
        assert c.post("/api/campaigns/P/forks", json=body, headers=ADMIN).status_code == 422  # no budget
        assert c.post("/api/campaigns/P/forks", json={**body, "budget": 1e6}, headers=ADMIN).status_code == 422
        assert p.L.list("fork") == [] and calls == []
        made = c.post("/api/campaigns/P/forks", json={**body, "budget": 1}, headers=ADMIN)
        assert made.status_code == 201, made.text
        view = made.json()
        assert view["status"] == "queued" and view["job"]["rerun"] == view["fork"]["id"]
        branch = view["branch"]["id"]
        deadline = time.time() + 30
        while c.get(f"/api/campaigns/{branch}", headers=TEAM).json()["job"]["status"] not in ("finished", "failed"):
            assert time.time() < deadline
            time.sleep(0.05)
        assert c.get(f"/api/campaigns/{branch}", headers=TEAM).json()["job"]["status"] == "finished"
        assert calls                                                     # the sandbox ran, in the job
        listed = c.get("/api/campaigns/P/forks", headers=TEAM).json()
        assert [x["fork"]["id"] for x in listed] == [view["fork"]["id"]]
        assert listed[0]["status"] == "done" and listed[0]["findings"][0]["status"] == "refuted"
        assert c.post("/api/campaigns/P/forks", json={**body, "mode": "agent", "budget": 1},
                      headers=TEAM).status_code == 403
        assert c.post("/api/campaigns/P/forks", json={**body, "claim_id": "nope"}, headers=TEAM).status_code == 404
        bad = {**body, "overrides": [{"quantity": "short_threshold_v", "value": 0.2, "unit": "V"}]}
        assert c.post("/api/campaigns/P/forks", json=bad, headers=TEAM).status_code == 422
        nan = {**body, "overrides": [{"quantity": "spatial_scale", "value": "NaN", "unit": "px/mm"}]}
        assert c.post("/api/campaigns/P/forks", json=nan, headers=TEAM).status_code == 422
        assert c.get(f"/api/findings/{p.claim}/branches", headers=TEAM).json()["forks"][0][
            "branch_campaign_id"] == branch
