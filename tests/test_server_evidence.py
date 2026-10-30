"""Read-only evidence endpoints: campaign graph, a finding's evidence. Offline only."""
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from translucent import gate, runtime, server
from translucent.contracts import Campaign, Claim, Measurement, Predicate, Run, Source, Uncertainty
from translucent.ledger import Ledger


@pytest.fixture(autouse=True)
def no_live_models(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Paid model calls are forbidden in API tests")
    monkeypatch.setattr(runtime, "_client", denied)
    for var in ("TRANSLUCENT_LIVE", "TRANSLUCENT_ADMIN_TOKEN", "TRANSLUCENT_TEAM_TOKEN", "TRANSLUCENT_CORS_ORIGINS"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def store(tmp_path):
    root, state = tmp_path / "ledger", tmp_path / "api"
    L = Ledger(root)
    path = tmp_path / "tiny.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 1, (16, 16))
    writer.write(np.zeros((16, 16, 3), dtype=np.uint8))
    writer.release()
    video = L.put_file(path, kind="video", media_type="video/mp4", label="Fixture movie",
                       source=Source(uri="experiment:fixture", citation="Fixture et al.", licence="CC BY 4.0"))
    L.add(Campaign(id="seed", objective="Test the boundary", title="Fixture", inputs=[video.id],
                   budget_usd=1, created_at=0))
    L.add(Run(id="r", campaign_id="seed", step_id="s", tool="read_cell_voltage", idempotency_key="r",
              input_ids=[video.id], status="ok", measurement_ids=["m"], started_at=0, ended_at=0))
    L.add(Measurement(id="m", run_id="r", quantity="voltage", value=2, unit="V", artifact_ids=[video.id],
                      uncertainty=Uncertainty(value=0.01, kind="bound", method="fixture")))
    L.add(Claim(id="k", campaign_id="seed", author="fixture", statement="The voltage is near 2 V.",
                predicate=Predicate(type="value_within", params={"lo": 1.9, "hi": 2.1, "unit": "V"},
                                    measurement_ids=["m"]), created_at=time.time()))
    gate.review(L, "k")
    yield L, root, state, video
    L.db.close()


def client(store):
    return TestClient(server.create_app(store[1], store[2], poll_interval=0.01))


def test_campaign_graph(store):
    with client(store) as c:
        g = c.get("/api/campaigns/seed/graph").json()
        assert c.get("/api/campaigns/nope/graph").status_code == 404
    kinds = {n["id"]: n["kind"] for n in g["nodes"]}
    assert kinds["k"] == "finding" and kinds["m"] == "measurement" and kinds["r"] == "run"
    assert kinds[store[3].id] == "recording"
    assert {"from": "k", "to": "m", "kind": "cites"} in g["edges"]
    assert {"from": "m", "to": store[3].id, "kind": "computed_from"} in g["edges"]
    finding = next(n for n in g["nodes"] if n["id"] == "k")
    assert finding["status"] == "supported"


def test_finding_evidence(store):
    with client(store) as c:
        ev = c.get("/api/findings/k/evidence").json()
        assert c.get("/api/findings/missing/evidence").status_code == 404
    assert ev["status"] == "supported" and ev["problems"] == []
    assert [m["id"] for m in ev["measurements"]] == ["m"] and [r["id"] for r in ev["runs"]] == ["r"]
