import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from translucent import diff, gate, server
from translucent.contracts import Campaign, Claim, Measurement, Predicate, Run, Source, Uncertainty
from translucent.ledger import Ledger
from translucent.verify import read_ledger

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "web" / "public" / "fixtures"


def test_bundle_diff_matches_the_recorded_expectation():
    """The web app's diff port is checked against this same file."""
    got = diff.diff_campaigns(diff.View.from_bundle(FIXTURES / "m4-meta"), diff.View.from_bundle(FIXTURES / "m4-team"))
    want = json.loads((ROOT / "tests" / "data" / "diff-m4-meta-vs-m4-team.json").read_text(encoding="utf-8"))
    assert json.loads(json.dumps(got)) == want
    assert "Both began with the same 7 tool calls; then A called inspect_video and B called segment_images." \
        in got["sentences"]
    short = next(f for f in got["findings"] if f["a"] == "claim_1c7db9617f")
    assert short["b"] == "claim_633e60443c" and "Verdict: supported in both." in short["sentences"]


def test_campaigns_in_two_ledgers():
    with read_ledger(ROOT / "runs" / "fw-chain6") as A, read_ledger(ROOT / "runs" / "fw-chain8") as B:
        va, vb = diff.View.from_ledger(A, "cmp_d6e28c20b1"), diff.View.from_ledger(B, "cmp_92c5ca76f7")
        d = diff.diff_campaigns(va, vb)
        assert d["a"] == "cmp_d6e28c20b1" and d["b"] == "cmp_92c5ca76f7"
        assert len(d["findings"]) + len(d["only_a"]) == 6 and len(d["findings"]) + len(d["only_b"]) == 11
        assert all(f["sentences"][-1].startswith("Verdict: ") for f in d["findings"])
        assert d == diff.diff_campaigns(diff.View.from_ledger(A, "cmp_d6e28c20b1"),
                                        diff.View.from_ledger(B, "cmp_92c5ca76f7"))   # deterministic
        ra = next(r for r in va.runs.values() if r["tool"] == "read_cell_voltage")
        rb = next(r for r in vb.runs.values() if r["tool"] == "read_cell_voltage")
        rd = diff.diff_runs(va, ra["id"], vb, rb["id"])
        assert rd["sentences"][0] == "Both ran read_cell_voltage."


@pytest.fixture
def ledger(tmp_path):
    L = Ledger(tmp_path / "ledger")
    video = L.put_bytes(b"not really a video", kind="video", media_type="video/mp4",
                        source=Source(uri="experiment:fixture"))
    for cid in ("a", "b"):
        L.add(Campaign(id=cid, objective="How fast?", title=cid, inputs=[video.id], budget_usd=0, created_at=0))
    for cid, scale, speed, declared in (("a", 1210.0, 63.7, None), ("b", 1110.0, 69.4, "declared by Ben from a what-if")):
        L.add(Run(id=f"cal_{cid}", campaign_id=cid, step_id="s1", tool="lookup_calibration", idempotency_key=cid,
                  input_ids=[video.id], params={"video_id": video.id, "quantity": "spatial_scale"},
                  measurement_ids=[f"scale_{cid}"], status="ok", started_at=0, ended_at=0))
        L.add(Measurement(id=f"scale_{cid}", run_id=f"cal_{cid}", quantity="spatial_scale", value=scale,
                          unit="px/mm", label="Image scale", valid_domain=declared, artifact_ids=[video.id],
                          uncertainty=Uncertainty(value=10, kind="bound", method="fixture")))
        L.add(Run(id=f"grow_{cid}", campaign_id=cid, step_id="s2", tool="measure_growth", idempotency_key=cid + "g",
                  input_ids=[video.id, f"scale_{cid}"], params={"video_id": video.id, "scale_id": f"scale_{cid}"},
                  measurement_ids=[f"v_{cid}"], status="ok", started_at=0, ended_at=0))
        L.add(Measurement(id=f"v_{cid}", run_id=f"grow_{cid}", quantity="growth_velocity", value=speed, unit="um/s",
                          label="Growth speed", subject="object 1", artifact_ids=[video.id],
                          uncertainty=Uncertainty(value=2.0, kind="ci95", method="fixture")))
        L.add(Claim(id=f"k_{cid}", campaign_id=cid, author="worker", statement="Grows at 55-66 um/s.",
                    predicate=Predicate(type="value_within", params={"lo": 55, "hi": 66, "unit": "um/s"},
                                        measurement_ids=[f"v_{cid}"]), created_at=time.time()))
        gate.review(L, f"k_{cid}")
    yield L
    L.db.close()


def test_finding_and_run_diffs_in_plain_words(ledger):
    d = diff.compare(ledger, "k_a", "k_b")
    assert d["kind"] == "finding"
    assert "Growth speed (object 1): 63.7 ± 2 um/s -> 69.4 ± 2 um/s." in d["sentences"]
    assert d["sentences"][-1].startswith("Verdict: unsupported -> unsupported") or \
        d["sentences"][-1].startswith("Verdict: ")
    r = diff.compare(ledger, "grow_a", "grow_b")
    assert "Scale: 1210 ± 10 px/mm vs 1110 ± 10 px/mm (declared by Ben from a what-if)." in r["sentences"]
    assert any(c["field"] == "params.scale_id" for c in r["changes"])
    with pytest.raises(ValueError):
        diff.compare(ledger, "k_a", "grow_b")
    with pytest.raises(LookupError):
        diff.compare(ledger, "k_a", "missing")


def test_compare_endpoint(ledger, tmp_path):
    with TestClient(server.create_app(ledger.root, tmp_path / "api", poll_interval=0.01)) as c:
        assert c.get("/api/compare", params={"a": "a", "b": "b"}).json()["kind"] == "campaign"
        assert c.get("/api/compare", params={"a": "k_a", "b": "k_b"}).json()["kind"] == "finding"
        assert c.get("/api/compare", params={"a": "k_a", "b": "grow_b"}).status_code == 400
        assert c.get("/api/compare", params={"a": "k_a", "b": "nope"}).status_code == 404
