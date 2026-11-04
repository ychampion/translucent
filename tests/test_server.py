import hashlib
import json
from pathlib import Path
import threading
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from support.keys import add_key

from translucent import cli, providers, replay, runtime, server, tools
from translucent.contracts import Campaign, Source
from translucent.ledger import Ledger


@pytest.fixture(autouse=True)
def no_live_models(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Paid model calls are forbidden in API/CLI tests")
    monkeypatch.setattr(runtime, "_client", denied)
    monkeypatch.delenv("TRANSLUCENT_LIVE", raising=False)
    monkeypatch.delenv("TRANSLUCENT_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("TRANSLUCENT_CORS_ORIGINS", raising=False)


@pytest.fixture
def store(tmp_path):
    root, state = tmp_path / "ledger", tmp_path / "api"
    L = Ledger(root)
    path = tmp_path / "tiny.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 1, (16, 16))
    assert writer.isOpened()
    writer.write(np.zeros((16, 16, 3), dtype=np.uint8))
    writer.release()
    video = L.put_file(path, kind="video", media_type="video/mp4", source=Source(uri="experiment:fixture"))
    L.add(Campaign(id="seed", objective="Test the boundary", title="Fixture", inputs=[video.id],
                   budget_usd=1, created_at=0))
    add_key(state, "Ana", "engineer", READER_KEY)                  # reads are private: every client needs a key
    yield L, root, state, video
    L.db.close()


def finished(L, cid="seed"):
    L.emit(cid, "campaign.started", "runtime", "Start")
    L.emit(cid, "step.started", "runtime", "Work")
    L.emit(cid, "campaign.finished", "runtime", "Done")


READER_KEY = "test-engineer-0123456789abcdef"
READER = {"Authorization": f"Bearer {READER_KEY}"}


def Reader(app, **kw):
    """A test client that sends an engineer's key unless a request names another (or none)."""
    return TestClient(app, headers=READER, **kw)


def make_app(store, factory=None):
    return server.create_app(store[1], store[2], campaigner_factory=factory, poll_interval=0.01)


def sse_data(response):
    ids, data = [], []
    for line in response.text.splitlines():
        if line.startswith("id: "):
            ids.append(int(line[4:]))
        elif line.startswith("data: "):
            data.append(json.loads(line[6:]))
    return ids, data


def enable(monkeypatch):
    monkeypatch.setenv("TRANSLUCENT_LIVE", "1")
    monkeypatch.setenv("TRANSLUCENT_ADMIN_TOKEN", "test-administrator-0123-32chars-minimum")
    for name in ("FIREWORKS_API_KEY", "MODEL_API_KEY"):  # placeholders: the fake campaigners never call a provider
        monkeypatch.setenv(name, "test-key")
    return {"Authorization": "Bearer test-administrator-0123-32chars-minimum"}


def request_body(**changes):
    return {"objective": "Find the evidence", "experiment_id": "fixture", "profile": "dev", "budget": 0.25,
            **changes}


def wait_job(client, cid, status):
    deadline = time.monotonic() + 30  # the box is memory-starved; this checks behaviour, not speed
    while time.monotonic() < deadline:
        response = client.get(f"/api/campaigns/{cid}")
        job = response.json()["job"]
        if job["status"] == status:
            return job
        time.sleep(0.01)
    pytest.fail(f"job did not become {status}: {job}")


class FakeCampaigner:
    def __init__(self, ledger, campaign, settings):
        self.L, self.c, self.s = ledger, campaign, settings

    def run(self):
        assert "run_analysis_code" in tools.REGISTRY
        finished(self.L, self.c.id)
        return {"status": "finished", "profile": self.s.profile}


def test_campaign_listing_detail_and_unknown(store):
    with Reader(make_app(store)) as client:
        assert [c["id"] for c in client.get("/api/campaigns").json()] == ["seed"]
        assert client.get("/api/campaigns/seed").json()["objective"] == "Test the boundary"
        for suffix in ("", "/events", "/bundle"):
            assert client.get("/api/campaigns/unknown" + suffix).status_code == 404


def test_sse_ordering_resume_and_reopen_from_disk(store):
    L, root, state, _ = store
    finished(L)
    with Reader(make_app(store)) as client:
        response = client.get("/api/campaigns/seed/events")
        assert response.headers["content-type"].startswith("text/event-stream")
        ids, data = sse_data(response)
        assert ids == [1, 2, 3] and [e["seq"] for e in data] == ids
        assert data[-1]["type"] == "campaign.finished"
        assert sse_data(client.get("/api/campaigns/seed/events", headers={"Last-Event-ID": "1"}))[0] == [2, 3]
        assert sse_data(client.get("/api/campaigns/seed/events", headers={"Last-Event-ID": "3"}))[0] == []
    with Reader(server.create_app(root, state)) as client:
        assert sse_data(client.get("/api/campaigns/seed/events", headers={"Last-Event-ID": "2"}))[0] == [3]


def test_sse_polls_events_written_by_another_connection(store):
    L, root, _, _ = store
    L.emit("seed", "campaign.started", "runtime", "Start")
    def append():
        time.sleep(0.1)
        other = Ledger(root)
        try:
            other.emit("seed", "step.started", "runtime", "External writer")
            other.emit("seed", "campaign.finished", "runtime", "Done")
        finally:
            other.db.close()
    with Reader(make_app(store)) as client:
        writer = threading.Thread(target=append)
        writer.start()
        try:
            ids, data = sse_data(client.get("/api/campaigns/seed/events"))
        finally:
            writer.join()
        assert ids == [1, 2, 3] and data[1]["title"] == "External writer"


@pytest.mark.parametrize("cursor", ["-1", "abc", "1.5", "9" * 21, str(2**63)])
def test_sse_malformed_resume_refused(store, cursor):
    with Reader(make_app(store)) as client:
        assert client.get("/api/campaigns/seed/events", headers={"Last-Event-ID": cursor}).status_code == 400


@pytest.mark.parametrize("live,configured,header,status", [(False, True, "Bearer test-administrator-0123-32chars-minimum", 403),
    (True, True, None, 401), (True, True, "Bearer wrong", 401), (True, True, "Basic test-administrator-0123-32chars-minimum", 401),
    (True, False, "Bearer ", 401), (True, False, "Bearer test-administrator-0123-32chars-minimum", 401),
    (True, True, f"Bearer {READER_KEY}", 403)])
def test_post_refuses_disabled_missing_wrong_and_unconfigured_token(store, monkeypatch, live, configured, header,
                                                                    status):
    """Live off refuses even an administrator; no key, a wrong one or an unconfigured one is "sign in" (401); an
    engineer's key may read but not spend (403)."""
    monkeypatch.setenv("TRANSLUCENT_LIVE", "1" if live else "0")
    if configured:
        monkeypatch.setenv("TRANSLUCENT_ADMIN_TOKEN", "test-administrator-0123-32chars-minimum")
    with TestClient(make_app(store, FakeCampaigner)) as client:
        assert client.post("/api/campaigns", json=request_body(),
                           headers={"Authorization": header} if header else {}).status_code == status
        assert len(client.get("/api/campaigns", headers=READER).json()) == 1
        assert list((store[2] / "jobs").glob("*.json")) == []


def test_valid_post_uses_campaigner_and_persists_job(store, monkeypatch):
    headers = enable(monkeypatch)
    with Reader(make_app(store, FakeCampaigner)) as client:
        response = client.post("/api/campaigns", json=request_body(profile="meta"), headers=headers)
        assert response.status_code == 202
        cid = response.json()["id"]
        job = wait_job(client, cid, "finished")
        assert job["result"] == {"status": "finished", "profile": "meta"}
        assert client.get(f"/api/campaigns/{cid}").json()["budget_usd"] == 0.25
        assert sse_data(client.get(f"/api/campaigns/{cid}/events"))[0] == [1, 2, 3]
        saved = json.loads(next((store[2] / "jobs").glob("*.json")).read_text())
        assert saved["campaign_id"] == cid and saved["status"] == "finished"


def test_single_live_campaign_and_slot_released(store, monkeypatch):
    headers, entered, release = enable(monkeypatch), threading.Event(), threading.Event()
    class Blocking(FakeCampaigner):
        def run(self):
            entered.set()
            assert release.wait(5)
            return super().run()
    with Reader(make_app(store, Blocking)) as client:
        try:
            first = client.post("/api/campaigns", json=request_body(), headers=headers)
            assert first.status_code == 202 and entered.wait(5)
            assert client.post("/api/campaigns", json=request_body(), headers=headers).status_code == 409
        finally:
            release.set()
        wait_job(client, first.json()["id"], "finished")
        assert client.post("/api/campaigns", json=request_body(), headers=headers).status_code == 202


def test_worker_failure_is_durable_and_ends_sse(store, monkeypatch, caplog):
    headers = enable(monkeypatch)
    class Broken(FakeCampaigner):
        def run(self):
            self.L.emit(self.c.id, "campaign.started", "runtime", "Start")
            raise RuntimeError("fixture failure")
    with Reader(make_app(store, Broken)) as client:
        cid = client.post("/api/campaigns", json=request_body(), headers=headers).json()["id"]
        job = wait_job(client, cid, "failed")
        assert "fixture failure" in job["error"]
        assert sse_data(client.get(f"/api/campaigns/{cid}/events"))[0] == [1]
    # the operator sees it in the server log too, with the campaign and the error
    assert [r for r in caplog.records if r.levelname == "ERROR" and r.exc_info and
            f"Campaign {cid} failed: RuntimeError: fixture failure" in r.getMessage()]


def test_live_needs_the_profiles_provider_keys(store, monkeypatch, tmp_path):
    """Without a provider key the server is not live: a campaign is refused up front, not failed in the background."""
    headers = enable(monkeypatch)
    monkeypatch.setattr(providers, "ROOT", tmp_path)  # no .env
    monkeypatch.delenv("MODEL_API_KEY")
    monkeypatch.delenv("META_API_KEY", raising=False)
    monkeypatch.setenv("FIREWORKS_API_KEY", "")  # set but empty: missing, as providers.Metered reads it
    with Reader(make_app(store, FakeCampaigner)) as client:
        assert client.get("/api/session").json()["live"] is False
        refused = client.post("/api/campaigns", json=request_body(), headers=headers)
        assert refused.status_code == 503 and "no FIREWORKS_API_KEY for the dev profile" in refused.json()["detail"]
        assert len(client.get("/api/campaigns").json()) == 1 and list((store[2] / "jobs").glob("*.json")) == []
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        assert client.get("/api/session").json()["live"] is True
        refused = client.post("/api/campaigns", json=request_body(profile="meta"), headers=headers)
        assert refused.status_code == 503 and "MODEL_API_KEY" in refused.json()["detail"]
        assert client.post("/api/campaigns", json=request_body(), headers=headers).status_code == 202


def write_job(store, status):
    directory = store[2] / "jobs"
    directory.mkdir(parents=True, exist_ok=True)
    job = server.Job(campaign_id="seed", status=status, profile="dev", created_at=0, updated_at=0)
    (directory / (hashlib.sha256(b"seed").hexdigest() + ".json")).write_text(job.model_dump_json())


def test_running_job_interrupted_on_restart(store):
    write_job(store, "running")
    with Reader(make_app(store, FakeCampaigner)) as client:
        job = client.get("/api/campaigns/seed").json()["job"]
        assert job["status"] == "interrupted"
        assert sse_data(client.get("/api/campaigns/seed/events"))[0] == []
    with Reader(make_app(store, FakeCampaigner)) as client:
        assert client.get("/api/campaigns/seed").json()["job"]["status"] == "interrupted"


def test_queued_job_stays_off_by_default_then_resumes_when_enabled(store, monkeypatch):
    write_job(store, "queued")
    with Reader(make_app(store, FakeCampaigner)) as client:
        assert client.get("/api/campaigns/seed").json()["job"]["status"] == "queued"
        assert store[0].events("seed") == []
    enable(monkeypatch)
    with Reader(make_app(store, FakeCampaigner)) as client:
        wait_job(client, "seed", "finished")


def test_exclusive_state_owner(store):
    with Reader(make_app(store)):
        with pytest.raises(RuntimeError, match="another server"):
            with Reader(make_app(store)):
                pass


@pytest.mark.parametrize("rid", ["sha256:" + "0" * 64, "sha256:abc", "sha256:" + "A" * 64,
    "garbage", "..%2F..%2F.env", "%2e%2e%5c.env", "sha256:%2Fetc%2Fpasswd", "%00", "C:%5cWindows%5cwin.ini"])
def test_artifact_unknown_and_malformed_ids_never_reach_blob_paths(store, monkeypatch, rid):
    original = Ledger.blob_path
    def denied(ledger, aid):
        if aid != store[3].id:
            pytest.fail("malformed or unknown id reached filesystem path construction")
        return original(ledger, aid)
    monkeypatch.setattr(Ledger, "blob_path", denied)
    with Reader(make_app(store)) as client:
        assert client.get("/api/artifacts/" + rid).status_code == 404


def test_artifact_media_bytes_and_missing_blob(store):
    L = store[0]
    artifact = L.put_bytes(b"hello", kind="text", media_type="text/plain")
    with Reader(make_app(store)) as client:
        response = client.get("/api/artifacts/" + artifact.id)
        assert response.content == b"hello" and response.headers["content-type"].startswith("text/plain")
        L.blob_path(artifact.id).unlink()
        assert client.get("/api/artifacts/" + artifact.id).status_code == 404


def test_bundle_uses_replay_disk_cache_and_invalidates_on_new_events(store, monkeypatch):
    calls, original = [], replay.export
    def export(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(replay, "export", export)
    finished(store[0])
    with Reader(make_app(store)) as client:
        response = client.get("/api/campaigns/seed/bundle")
        assert response.status_code == 200
        body = response.json()
        assert body["campaign"]["id"] == "seed" and len(body["events"]) == 3
        assert client.get(body["video"]).content == store[0].blob_path(store[3].id).read_bytes()
        assert client.get("/api/campaigns/seed/bundle").json() == body and len(calls) == 1
    with Reader(make_app(store)) as client:
        assert client.get("/api/campaigns/seed/bundle").status_code == 200 and len(calls) == 1
        store[0].emit("seed", "step.started", "runtime", "Additional recorded event")
        assert len(client.get("/api/campaigns/seed/bundle").json()["events"]) == 4 and len(calls) == 2
    assert list((store[2] / "bundles").rglob("bundle.json"))
    assert not (store[1] / "work").exists()


def test_old_export_revision_is_rebuilt_with_unchanged_ledger_counts(store, monkeypatch):
    ledger, _, state, video = store
    finished(ledger)
    out = state / "bundles" / server._key("seed")
    out.mkdir(parents=True)
    old = server.ReplayBundle(campaign=ledger.get("campaign", "seed"), video="old-cached.mp4",
                              video_size=(16, 16), fps=1, events=ledger.events("seed"))
    (out / "bundle.json").write_text(old.model_dump_json(), encoding="utf-8")
    counts = [ledger.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in ("record", "event", "invalidation")]
    (out / "source.json").write_text(json.dumps([2] + counts), encoding="utf-8")
    calls, original = [], replay.export
    def export(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)
    monkeypatch.setattr(replay, "export", export)
    app = make_app(store)
    with Reader(app) as client:
        assert app.state.export_ready.wait(30)
        assert app.state.export_errors == {}
        assert client.get("/api/campaigns/seed/bundle").json()["video"] == "/api/artifacts/" + video.id
        assert calls == ["seed"]
    assert json.loads((out / "source.json").read_text())[1:] == counts


def test_budget_and_explicit_cors_allowlist(store, monkeypatch):
    monkeypatch.setenv("TRANSLUCENT_CORS_ORIGINS", "https://viewer.example, http://localhost:3000")
    monkeypatch.setattr(server.budget, "balances", lambda: {"fixture": {"remaining_usd": 1}})
    with Reader(make_app(store)) as client:
        response = client.get("/api/budget", headers={"Origin": "https://viewer.example"})
        assert response.json() == {"fixture": {"remaining_usd": 1}}
        assert response.headers["access-control-allow-origin"] == "https://viewer.example"
        assert "access-control-allow-origin" not in client.get("/api/budget", headers={"Origin": "https://bad.example"}).headers


@pytest.mark.parametrize("change", [{"experiment_id": "../.env"}, {"experiment_id": "unknown"},
    {"profile": "unknown"}, {"budget": -1}, {"budget": 0}, {"objective": ""}])
def test_post_invalid_input_or_unknown_experiment(store, monkeypatch, change):
    headers = enable(monkeypatch)
    with Reader(make_app(store, FakeCampaigner)) as client:
        assert client.post("/api/campaigns", json=request_body(**change), headers=headers).status_code in {404, 422}
        assert len(client.get("/api/campaigns").json()) == 1


@pytest.mark.parametrize("budget,expected", [("1e999", "budget: Input should be a finite number"),
    ("100000", "budget: $100000 is more than the $25 cap"), ("200", "budget: $200 is more than the $170 cap")])
def test_campaign_budget_is_finite_and_within_the_provider_caps(store, monkeypatch, budget, expected):
    headers = {**enable(monkeypatch), "Content-Type": "application/json"}
    body = json.dumps(request_body(profile="meta" if budget == "200" else "dev")).replace("0.25", budget)
    with Reader(make_app(store, FakeCampaigner)) as client:
        response = client.post("/api/campaigns", content=body, headers=headers)
        assert response.status_code == 422 and response.json()["detail"].startswith(expected)
        assert len(client.get("/api/campaigns").json()) == 1
        assert client.post("/api/campaigns", content=body.replace(budget, "25"), headers=headers).status_code == 202


def test_cli_budget_export_serve_and_run_with_fake_campaigner(store, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(server.budget, "balances", lambda: {"fixture": {"remaining_usd": 1}})
    assert cli.main(["budget"]) == 0
    assert json.loads(capsys.readouterr().out)["fixture"]["remaining_usd"] == 1
    finished(store[0])
    out = tmp_path / "export"
    assert cli.main(["export", "seed", str(out), "--ledger", str(store[1])]) == 0
    assert json.loads((out / "bundle.json").read_text())["campaign"]["id"] == "seed"
    import uvicorn
    captured = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.append(kw))
    assert cli.main(["serve", "--ledger", str(store[1]), "--state", str(store[2]), "--port", "8123"]) == 0
    # a stop waits a bounded time for open update streams (an open campaign page never closes its own)
    assert captured == [{"host": "127.0.0.1", "port": 8123, "timeout_graceful_shutdown": cli.STREAM_CLOSE_S}]
    monkeypatch.setattr(tools, "import_video", lambda *args, **kwargs: store[3])
    monkeypatch.setattr(runtime, "Campaigner", FakeCampaigner)
    assert cli.main(["run", "--ledger", str(store[1]), "--budget", "0.1", "--steps", "1"]) == 0
    assert len(store[0].list("campaign")) == 2


def test_reading_commands_refuse_a_missing_ledger(store, monkeypatch, capsys, tmp_path):
    """A mistyped --ledger is an error, not a new empty ledger. Only commands that write records create one."""
    import uvicorn
    served = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.append(kw))
    typo, state = tmp_path / "ledgr", str(tmp_path / "api-state")
    for argv in (["experiments", "--ledger", str(typo)], ["export", "seed", str(tmp_path / "out"), "--ledger", str(typo)],
                 ["resume", "seed", "--ledger", str(typo)], ["serve", "--ledger", str(typo), "--state", state],
                 ["mcp", "--ledger", str(typo)], ["verify", str(typo)]):
        with pytest.raises(SystemExit) as stop:
            cli.main(argv)
        assert stop.value.code == 2 and "ledger" in capsys.readouterr().err, argv
        assert not typo.exists() and not served, argv
    assert cli.main(["serve", "--ledger", str(typo), "--state", state, "--create-ledger"]) == 0
    assert (typo / "ledger.sqlite").is_file() and served and "Created an empty ledger" in capsys.readouterr().err
    assert cli.main(["experiments", "--ledger", str(typo)]) == 0


def test_run_on_a_missing_movie_says_how_to_fetch_it(store, capsys):
    with pytest.raises(SystemExit) as stop:  # there is no Movie 9; a fresh clone has none of them
        cli.main(["run", "--ledger", str(store[1]), "--movie", "9", "--budget", "0.1", "--steps", "1"])
    assert stop.value.code == 2 and "scripts/fetch_data.py reisecker2023/movie9" in capsys.readouterr().err


def test_export_cache_is_prepared_without_a_bundle_request(store, monkeypatch):
    finished(store[0])
    app = make_app(store)
    with Reader(app) as client:
        assert app.state.export_ready.wait(30)
        assert app.state.export_errors == {}
        assert list((store[2] / "bundles").glob("*/bundle.json"))
        def denied(*args, **kwargs):
            pytest.fail("The first request should use the prepared export")
        monkeypatch.setattr(replay, "export", denied)
        assert client.get("/api/campaigns/seed/bundle").status_code == 200


def test_failed_gate_checks_have_plain_explanations_in_bundle_and_events(store):
    from translucent.contracts import Check, Claim, Predicate, Review
    ledger = store[0]
    claim = ledger.add(Claim(id="finding", campaign_id="seed", author="fixture", statement="Growth is known.",
                            predicate=Predicate(type="exists", params={}, measurement_ids=[]), created_at=0))
    review = ledger.add(Review(id="review", claim_id=claim.id, reviewer="gate", verdict="unsupported",
                              reason="Original recorded reason", created_at=0,
                              checks=[Check(name="scale and timing recorded", passed=False,
                                            detail="growth_velocity in um/s: no recorded time_base")]))
    ledger.emit("seed", "claim.reviewed", "gate", "Not enough evidence", data={"review": review.model_dump()})
    finished(ledger)
    with Reader(make_app(store)) as client:
        body = client.get("/api/campaigns/seed/bundle").json()
        got = body["reviews"][0]
        assert got["reason"] == review.reason
        assert got["checks"][0]["detail"] == review.checks[0].detail
        assert "recorded clock" in got["failed_checks"][0]["explanation"]
        assert got["checks"][0]["explanation"] == got["failed_checks"][0]["explanation"]
        _, events = sse_data(client.get("/api/campaigns/seed/events"))
        assert events[0]["data"]["review"]["failed_checks"] == got["failed_checks"]


@pytest.mark.parametrize("name,detail,expected", [
    ("evidence is sound", "playback_time: run was invalidated", "later withdrawn"),
    ("evidence is sound", "spatial_scale: run was invalidated", "later withdrawn"),
    ("scale and timing recorded", "spatial_scale belongs to another recording", "same recorded experiment"),
    ("same experiment", "playback_time comes from different recordings", "same recorded experiment"),
    ("units match", "playback_time is measured in seconds", "units that cannot be compared"),
])
def test_check_explanation_names_the_failed_check(name, detail, expected):
    assert expected in server.explain_check({"name": name, "detail": detail, "passed": False})


def test_replay_does_not_decode_outlines_that_will_be_rejected(monkeypatch):
    from types import SimpleNamespace as N
    mask = N(kind="mask", frame=N(frame_index=0), object_id="1", mask=object(), bounds=N(left=0, top=0))
    parser = N(push=lambda body: [], finish=lambda outcome: N(result=N(records=[mask])))
    monkeypatch.setattr(replay, "video_segmentation_format", lambda: N(create_parser=lambda: parser))
    def denied(*args):
        pytest.fail("An outline rejected by the measurement must not be decoded")
    monkeypatch.setattr(replay, "decode_mask_to_svg_path", denied)
    assert replay._svg_masks("recorded response", None, set()) == {}


@pytest.mark.parametrize("frame", [None, 12])
def test_replay_filters_objects_before_parser_mask_validation(monkeypatch, frame):
    from types import SimpleNamespace as N
    bodies = []
    record = N(kind="mask", frame=N(frame_index=7), object_id="2", mask=object(), bounds=N(left=3, top=4))
    parser = N(push=lambda body: bodies.append(body) or [], finish=lambda outcome: N(result=N(records=[record])))
    monkeypatch.setattr(replay, "video_segmentation_format", lambda: N(create_parser=lambda: parser))
    monkeypatch.setattr(replay, "decode_mask_to_svg_path", lambda mask: "M0 0L1 1Z")
    one = "1<|box;x1=0|><|mask;data=1,1,!first|>"
    two = "2<|box;x1=3|><|mask;data=1,1,!second|>"
    f = 7 if frame is None else frame
    result = replay._svg_masks("<7f>" + one + "," + two + "\n", frame, {(f, "2")})
    assert set(result[f]) == {"2"}
    assert "!first" not in bodies[0] and "!second" in bodies[0]
    assert bodies[0].startswith("<7f>" if frame is None else "<0f>")


def test_replay_filter_preserves_real_mask_geometry():
    from meta_sam_parser._mask_codec import _encode_raster
    payload = _encode_raster([0, 1, 1, 1], 2, 2)
    token = "<|box;x1=0;y1=0;x2=1;y2=1;w=2;h=2|><|mask;x=0;y=0;data=2,2," + payload + "|>"
    text = "<0f>1" + token + ",2" + token + "\n"
    all_masks = replay._svg_masks(text, None)
    selected = replay._svg_masks(text, None, {(0, "2")})
    assert selected == {0: {"2": all_masks[0]["2"]}}


def test_api_reuses_existing_svg_cache_without_decoding_or_rewriting_source(store, monkeypatch):
    from translucent.contracts import Run
    ledger, _, state, video = store
    masks = ledger.put_json({"0": "<0f>1<|box;x1=0;y1=0;x2=3;y2=3;c=1|>"}, kind="masks")
    ledger.add(Run(id="sam", campaign_id="seed", step_id="s", tool="segment_images", idempotency_key="sam",
                   input_ids=[video.id], output_ids=[masks.id], params={"video_id": video.id}, status="ok",
                   started_at=0, ended_at=0))
    cache = ledger.root / "work" / f"svg_{masks.id.removeprefix('sha256:')[:16]}.json"
    cache.parent.mkdir()
    cache.write_text(json.dumps({"0": {"1": ["M0 0L3 3Z", [0, 0]]}}))
    before = {p.relative_to(cache.parent): p.read_bytes() for p in cache.parent.rglob("*") if p.is_file()}
    def denied(*args, **kwargs):
        pytest.fail("The existing geometry must be reused")
    monkeypatch.setattr(replay, "_svg_masks", denied)
    with Reader(make_app(store)) as client:
        body = client.get("/api/campaigns/seed/bundle").json()
        assert body["tracks"][0]["mask_paths"] == ["M0 0L3 3Z"]
        assert list((state / "bundles").glob("*/bundle.json"))
    assert before == {p.relative_to(cache.parent): p.read_bytes() for p in cache.parent.rglob("*") if p.is_file()}


def test_a_campaign_without_a_profile_runs_on_the_servers(store, monkeypatch):
    headers = enable(monkeypatch)
    monkeypatch.setenv("TRANSLUCENT_PROFILE", "meta")
    body = request_body()
    del body["profile"]
    with Reader(make_app(store, FakeCampaigner)) as client:
        response = client.post("/api/campaigns", json=body, headers=headers)
        assert response.status_code == 202
        assert wait_job(client, response.json()["id"], "finished")["result"]["profile"] == "meta"
