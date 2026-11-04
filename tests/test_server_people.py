"""People in the loop over HTTP: auth, validation, 409s, summaries, resume, paused jobs. Offline only."""
import hashlib
import json
import threading
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from evals.fake_llm import FakeClient
from support.keys import add_key
from translucent import cli, runtime, server, tools
from translucent.contracts import Campaign, Check, Claim, Predicate, Review, Source
from translucent.ledger import Ledger

TEAM = {"Authorization": "Bearer team-secret-0123456789ab-32chars-minimum"}              # Ana's key (the env team token, named Ana)
BEN_KEY = "ben-0123456789abcdefghij"
BEN = {"Authorization": f"Bearer {BEN_KEY}"}
ADMIN = {"Authorization": "Bearer test-administrator-0123-32chars-minimum"}


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
    assert writer.isOpened()
    writer.write(np.zeros((16, 16, 3), dtype=np.uint8))
    writer.release()
    video = L.put_file(path, kind="video", media_type="video/mp4", label="Fixture movie",
                       source=Source(uri="experiment:fixture", citation="Fixture et al.", licence="CC BY 4.0"))
    L.add(Campaign(id="seed", objective="Test the boundary", title="Fixture", inputs=[video.id],
                   budget_usd=1, created_at=0))
    yield L, root, state, video
    L.db.close()


def make_app(store, factory=None):
    return server.create_app(store[1], store[2], campaigner_factory=factory, poll_interval=0.01)


def Reader(app, **kw):
    """A test client that reads with Ana's key unless a request names another: reads are private."""
    return TestClient(app, headers=TEAM, **kw)


@pytest.fixture
def team(monkeypatch, store):
    """Two people: Ana holds the env team token, Ben a stored key. Each acts only as themself."""
    monkeypatch.setenv("TRANSLUCENT_TEAM_TOKEN", "team-secret-0123456789ab-32chars-minimum")
    monkeypatch.setenv("TRANSLUCENT_TEAM_NAME", "Ana")
    add_key(store[2], "Ben", "engineer", BEN_KEY)


def live(monkeypatch):
    monkeypatch.setenv("TRANSLUCENT_LIVE", "1")
    monkeypatch.setenv("TRANSLUCENT_ADMIN_TOKEN", "test-administrator-0123-32chars-minimum")
    for name in ("FIREWORKS_API_KEY", "MODEL_API_KEY"):  # placeholders: the fake campaigners never call a provider
        monkeypatch.setenv(name, "test-key")


def wait_job(client, cid, status):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"/api/campaigns/{cid}").json()["job"]
        if job and job["status"] == status:
            return job
        time.sleep(0.01)
    pytest.fail(f"job did not become {status}: {job}")


def add_claim(L, cid="seed", kid="finding"):
    claim = L.add(Claim(id=kid, campaign_id=cid, author="fixture", statement="Growth is known.",
                        predicate=Predicate(type="exists", params={}, measurement_ids=[]), created_at=0))
    review = L.add(Review(id=f"gate_{kid}", claim_id=claim.id, reviewer="gate", verdict="unsupported",
                          reason="Not enough evidence", created_at=0,
                          checks=[Check(name="finding is well-formed", passed=False, detail="no measurements")]))
    L.emit(cid, "claim.reviewed", "gate", "Not enough evidence", data={"review": review.model_dump()})
    return claim


class Fake:
    made = []

    def __init__(self, ledger, campaign, settings):
        self.L, self.c, self.s = ledger, campaign, settings
        Fake.made.append(self)

    def run(self):
        return self._go("run", "campaign.started")

    def resume(self):
        return self._go("resume", "campaign.resumed")

    def _go(self, mode, kind):
        self.L.emit(self.c.id, kind, "runtime", "Go")
        self.L.emit(self.c.id, "campaign.finished", "runtime", "Done", data={"status": "finished"})
        return {"status": "finished", "mode": mode, "budget": self.c.budget_usd, "ask_wait_s": self.s.ask_wait_s}


def test_session_flags_never_show_secrets(store, monkeypatch):
    with TestClient(make_app(store)) as client:
        assert client.get("/api/session").json() == {"live": False, "team": False, "model": "Muse Spark Open"}
        monkeypatch.setenv("TRANSLUCENT_TEAM_TOKEN", "team-secret-0123456789ab-32chars-minimum")
        assert client.get("/api/session").json() == {"live": False, "team": True, "model": "Muse Spark Open"}
        live(monkeypatch)
        monkeypatch.setenv("TRANSLUCENT_PROFILE", "meta")
        body = client.get("/api/session").json()
        assert body == {"live": True, "team": True, "model": "Muse Wonder"} and "secret" not in json.dumps(body)


def test_experiments_list(store, team):
    with Reader(make_app(store)) as client:
        assert client.get("/api/experiments").json() == [{"experiment": "fixture", "video_id": store[3].id,
            "label": "Fixture movie", "citation": "Fixture et al.", "licence": "CC BY 4.0"}]


@pytest.mark.parametrize("headers,status,author", [({}, 401, None), ({"Authorization": "Bearer wrong"}, 401, None),
    ({"Authorization": "team-secret-0123456789ab-32chars-minimum"}, 401, None), (TEAM, 201, "Ana"), (BEN, 201, "Ben"),
    (ADMIN, 201, "administrator")])
def test_people_actions_need_the_team_or_admin_token_but_not_live(store, monkeypatch, team, headers, status, author):
    """No key or a malformed one is "sign in"; a person's key writes, live or not, as the person the key names."""
    monkeypatch.setenv("TRANSLUCENT_ADMIN_TOKEN", "test-administrator-0123-32chars-minimum")  # live stays off
    store[0].emit("seed", "campaign.started", "runtime", "Start")
    with TestClient(make_app(store)) as client:
        r = client.post("/api/campaigns/seed/notes", json={"text": "Check frame 40"}, headers=headers)
        assert r.status_code == status
    notes = [e for e in store[0].events("seed") if e.type == "human.note"]
    assert [(e.actor, e.data["author"]) for e in notes] == ([(f"person:{author}", author)] if author else [])


def test_no_configured_token_refuses_people(store):
    store[0].emit("seed", "campaign.started", "runtime", "Start")
    with TestClient(make_app(store)) as client:
        assert client.post("/api/campaigns/seed/notes", json={"text": "x", "author": "Ana"},
                           headers={"Authorization": "Bearer "}).status_code == 401
        assert client.get("/api/campaigns/seed").status_code == 401
    assert [e.type for e in store[0].events("seed")] == ["campaign.started"]


def test_a_key_acts_only_as_its_person(store, monkeypatch, team):
    """The author field may only repeat the key's person: Ana's key cannot sign as Ben, nor Ben's as Ana."""
    monkeypatch.setenv("TRANSLUCENT_ADMIN_TOKEN", "test-administrator-0123-32chars-minimum")
    store[0].emit("seed", "campaign.started", "runtime", "Start")
    with TestClient(make_app(store)) as client:
        for headers, author in ((TEAM, "Ben"), (BEN, "Ana"), (ADMIN, "Ana")):
            r = client.post("/api/campaigns/seed/notes", json={"text": "x", "author": author}, headers=headers)
            assert r.status_code == 403 and "This key acts as" in r.json()["detail"], r.text
            r = client.post("/api/campaigns/seed/control", json={"action": "pause", "author": author}, headers=headers)
            assert r.status_code == 403
    assert [e.type for e in store[0].events("seed")] == ["campaign.started"]


def test_team_token_cannot_start_or_resume(store, monkeypatch, team):
    live(monkeypatch)
    store[0].emit("seed", "campaign.started", "runtime", "Start")
    with Reader(make_app(store, Fake)) as client:
        assert client.post("/api/campaigns", json={"objective": "x", "experiment_id": "fixture", "budget": 0.1},
                           headers=TEAM).status_code == 403
        assert client.post("/api/campaigns/seed/resume", json={"budget": 0.1}, headers=TEAM).status_code == 403
    assert len(store[0].list("campaign")) == 1


def test_note_event_and_409_unless_running_or_paused(store, team):
    L = store[0]
    with Reader(make_app(store)) as client:
        post = lambda: client.post("/api/campaigns/seed/notes", json={"text": "  Use the scale bar  ", "author": "Ana"},
                                   headers=TEAM)
        assert post().status_code == 409  # never started
        L.emit("seed", "campaign.started", "runtime", "Start")
        r = post()
        assert r.status_code == 201
        ev = r.json()["event"]
        assert ev["type"] == "human.note" and ev["actor"] == "person:Ana" and ev["title"] == "Ana left a note"
        assert ev["data"] == {"text": "Use the scale bar", "author": "Ana"}
        L.emit("seed", "campaign.paused", "runtime", "Paused by Ana", data={"by": "Ana"})
        assert post().status_code == 201
        L.emit("seed", "campaign.finished", "runtime", "Done", data={"status": "finished"})
        assert post().status_code == 409
        assert client.post("/api/campaigns/unknown/notes", json={"text": "x", "author": "Ana"},
                           headers=TEAM).status_code == 404


@pytest.mark.parametrize("path,body", [
    ("notes", {"text": "", "author": "Ana"}), ("notes", {"text": "   ", "author": "Ana"}),
    ("notes", {"text": "x" * 2001, "author": "Ana"}), ("notes", {"text": "x", "author": "A" * 81}),
    ("notes", {"text": "x", "author": "Ana\nAdmin"}), ("notes", {"text": "x", "author": ""}),
    ("notes", {"text": "x", "author": "Ana", "extra": 1}), ("notes", {"author": "Ana"}),
    ("reviews", {"claim_id": "finding", "verdict": "challenge", "reason": "", "author": "Ana"}),
    ("reviews", {"claim_id": "finding", "verdict": "supported", "reason": "x", "author": "Ana"}),
    ("control", {"action": "restart", "author": "Ana"}),
    ("answers", {"question_id": "q", "text": "", "author": "Ana"}),
])
def test_people_bodies_are_validated(store, team, path, body):
    store[0].emit("seed", "campaign.started", "runtime", "Start")
    with Reader(make_app(store)) as client:
        assert client.post(f"/api/campaigns/seed/{path}", json=body, headers=TEAM).status_code == 422
    assert [e.type for e in store[0].events("seed")] == ["campaign.started"]


def test_answers_only_for_open_questions_and_people_summary(store, team):
    L = store[0]
    L.emit("seed", "campaign.started", "runtime", "Start")
    for qid in ("q_open", "q_gone"):
        L.emit("seed", "question.asked", "orchestrator", "Asked the team: How long is the bar?",
               data={"question_id": qid, "question": "How long is the bar?", "options": ["10 um", "20 um"]})
    L.emit("seed", "question.expired", "runtime", "No answer", data={"question_id": "q_gone"})
    with Reader(make_app(store)) as client:
        answer = lambda qid: client.post("/api/campaigns/seed/answers", headers=BEN,
                                         json={"question_id": qid, "text": "10 um", "author": "Ben"})
        r = answer("q_open")
        assert r.status_code == 201 and r.json()["event"]["type"] == "question.answered"
        assert r.json()["event"]["actor"] == "person:Ben"
        assert answer("q_open").status_code == 409
        assert answer("q_gone").status_code == 409
        assert answer("q_unknown").status_code == 404
        client.post("/api/campaigns/seed/notes", json={"text": "Frame 40 is blurry", "author": "Ana"}, headers=TEAM)
        people = client.get("/api/campaigns/seed/people").json()
        q = {x["question_id"]: x for x in people["questions"]}
        assert q["q_open"]["status"] == "answered" and q["q_open"]["answer"]["text"] == "10 um"
        assert q["q_open"]["answer"]["author"] == "Ben" and q["q_open"]["options"] == ["10 um", "20 um"]
        assert q["q_gone"]["status"] == "expired" and q["q_gone"]["answer"] is None
        assert [(n["author"], n["text"]) for n in people["notes"]] == [("Ana", "Frame 40 is blurry")]
        assert people["reviews"] == []
        assert client.get("/api/campaigns/unknown/people").status_code == 404


def test_human_reviews_after_the_campaign_ended_never_decide(store, team):
    L = store[0]
    add_claim(L)
    L.add(Campaign(id="other", objective="o", title="o", inputs=[store[3].id], budget_usd=1, created_at=0))
    other = add_claim(L, "other", "elsewhere")
    L.emit("seed", "campaign.finished", "runtime", "Done", data={"status": "finished"})
    with Reader(make_app(store)) as client:
        review = lambda headers=TEAM, **b: client.post("/api/campaigns/seed/reviews", headers=headers,
                                                       json={"claim_id": "finding", "author": "Ana", **b})
        r = review(verdict="challenge", reason="The scale bar was read from the wrong frame.")
        assert r.status_code == 201
        got = r.json()["review"]
        assert got["reviewer"] == "human" and got["verdict"] == "refuted" and got["author"] == "Ana"
        signed = review(BEN, verdict="agree", author="Ben").json()["review"]
        assert signed["verdict"] == "supported" and signed["reason"] == "Ben agrees with this finding."
        assert L.claim_status("finding") == "unsupported"
        assert client.post("/api/campaigns/seed/reviews", headers=TEAM, json={"claim_id": other.id, "author": "Ana",
                           "verdict": "agree"}).status_code == 404
        assert client.post("/api/campaigns/seed/reviews", headers=TEAM, json={"claim_id": "nope", "author": "Ana",
                           "verdict": "agree"}).status_code == 404
        body = client.get("/api/campaigns/seed/bundle").json()
        humans = [x for x in body["reviews"] if x["reviewer"] == "human"]
        assert [x["author"] for x in humans] == ["Ana", "Ben"] and all(x["failed_checks"] == [] for x in humans)
        gate = next(x for x in body["reviews"] if x["reviewer"] == "gate")
        assert gate["failed_checks"] and gate["failed_checks"][0]["explanation"]
        reviewed = [e for e in body["events"] if e["type"] == "claim.reviewed" and e["actor"].startswith("person:")]
        assert [e["title"] for e in reviewed] == ["Ana challenged a finding", "Ben agreed with a finding"]
        stream = client.get("/api/campaigns/seed/events").text
        sse = [json.loads(line[6:]) for line in stream.splitlines() if line.startswith("data: ")]
        assert [e["data"]["review"]["reviewer"] for e in sse if e["type"] == "claim.reviewed"] == ["gate", "human", "human"]
        assert [x["id"] for x in client.get("/api/campaigns/seed/people").json()["reviews"]] == [got["id"], signed["id"]]


def test_control_events_and_409(store, team):
    L = store[0]
    with Reader(make_app(store)) as client:
        control = lambda action: client.post("/api/campaigns/seed/control", headers=TEAM,
                                             json={"action": action, "author": "Ana"})
        assert control("pause").status_code == 409
        L.emit("seed", "campaign.started", "runtime", "Start")
        r = control("pause")
        assert r.status_code == 202
        assert r.json()["event"]["data"] == {"action": "pause", "author": "Ana"}
        assert r.json()["event"]["title"] == "Ana asked to pause the campaign"
        assert control("stop").status_code == 202


def test_resume_endpoint_rules_and_worker_settings(store, monkeypatch, team):
    live(monkeypatch)
    L, Fake.made = store[0], []
    with Reader(make_app(store, Fake)) as client:
        resume = lambda **b: client.post("/api/campaigns/seed/resume", json={"budget": 0.5, **b}, headers=ADMIN)
        assert resume().status_code == 409  # never started
        L.emit("seed", "campaign.started", "runtime", "Start")  # then the process died: interrupted
        assert resume(extra=1).status_code == 422 and resume(budget=0).status_code == 422
        assert "more than the $25 cap" in resume(budget=1e6).json()["detail"]  # dev: Fireworks only
        r = resume(profile="meta")
        assert r.status_code == 202 and r.json()["job"]["resume"] is True and r.json()["campaign"]["id"] == "seed"
        job = wait_job(client, "seed", "finished")
        assert job["result"] == {"status": "finished", "mode": "resume", "budget": 0.5, "ask_wait_s": 900}
        assert job["profile"] == "meta" and L.get("campaign", "seed").budget_usd == 1
        assert resume().status_code == 409  # finished cleanly
        assert client.post("/api/campaigns/unknown/resume", json={"budget": 0.5}, headers=ADMIN).status_code == 404


def test_resume_after_stop_and_one_campaign_at_a_time(store, monkeypatch, team):
    live(monkeypatch)
    L, entered, release = store[0], threading.Event(), threading.Event()
    L.emit("seed", "campaign.started", "runtime", "Start")
    L.emit("seed", "campaign.finished", "runtime", "Stopped by Ana", data={"status": "stopped"})

    class Blocking(Fake):
        def run(self):
            entered.set()
            assert release.wait(10)
            return super().run()
    with Reader(make_app(store, Blocking)) as client:
        try:
            first = client.post("/api/campaigns", json={"objective": "x", "experiment_id": "fixture", "budget": 0.1},
                                headers=ADMIN)
            assert first.status_code == 202 and entered.wait(10)
            assert client.post("/api/campaigns/seed/resume", json={"budget": 0.5}, headers=ADMIN).status_code == 409
        finally:
            release.set()
        wait_job(client, first.json()["id"], "finished")
        assert client.post("/api/campaigns/seed/resume", json={"budget": 0.5}, headers=ADMIN).status_code == 202
        assert wait_job(client, "seed", "finished")["result"]["mode"] == "resume"


def test_job_shows_paused_and_paused_blocks_new_campaigns(store, monkeypatch, team):
    live(monkeypatch)
    paused, release = threading.Event(), threading.Event()

    class Pausing(Fake):
        def run(self):
            self.L.emit(self.c.id, "campaign.started", "runtime", "Go")
            self.s.on_status("paused")
            paused.set()
            assert release.wait(10)
            self.s.on_status("running")
            return super().run()
    with Reader(make_app(store, Pausing)) as client:
        try:
            cid = client.post("/api/campaigns", json={"objective": "x", "experiment_id": "fixture", "budget": 0.1},
                              headers=ADMIN).json()["id"]
            assert paused.wait(10)
            wait_job(client, cid, "paused")
            assert client.post(f"/api/campaigns/{cid}/notes", json={"text": "While paused", "author": "Ana"},
                               headers=TEAM).status_code == 201
            assert client.post("/api/campaigns", json={"objective": "y", "experiment_id": "fixture", "budget": 0.1},
                               headers=ADMIN).status_code == 409
        finally:
            release.set()
        wait_job(client, cid, "finished")


def test_paused_job_is_interrupted_on_restart(store, team):
    directory = store[2] / "jobs"
    directory.mkdir(parents=True)
    job = server.Job(campaign_id="seed", status="paused", profile="dev", created_at=0, updated_at=0)
    (directory / (hashlib.sha256(b"seed").hexdigest() + ".json")).write_text(job.model_dump_json())
    with Reader(make_app(store)) as client:
        assert client.get("/api/campaigns/seed").json()["job"]["status"] == "interrupted"


def test_api_answer_reaches_a_waiting_real_campaigner(store, monkeypatch, team):
    """End to end: the real runtime asks, a person answers over HTTP mid-wait, the agent gets the answer."""
    live(monkeypatch)
    outputs = []

    def agent(messages, tools_):
        outputs.append([m["content"] for m in messages if m["role"] == "tool"])
        if len(outputs) == 1:
            return "Asking.", [("ask_person", {"question": "Which cell shorted?", "options": ["1", "2"]})]
        return "Done.", [("finish", {"answer": "done", "not_proven": ""})]

    def factory(L, c, settings):
        assert settings.ask_wait_s == 900
        settings.poll_s, settings.ask_wait_s = 0.02, 30  # a failing test must not hold shutdown for 15 minutes
        camp = runtime.Campaigner(L, c, settings)
        fake = FakeClient(agent)
        camp.clients = {"fireworks": fake, "meta": fake}
        return camp
    with Reader(make_app(store, factory)) as client:
        cid = client.post("/api/campaigns", json={"objective": "x", "experiment_id": "fixture", "budget": 0.1},
                          headers=ADMIN).json()["id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not client.get(f"/api/campaigns/{cid}/people").json()["questions"]:
            time.sleep(0.02)
        q = client.get(f"/api/campaigns/{cid}/people").json()["questions"][0]
        assert q["status"] == "open" and q["options"] == ["1", "2"]
        assert client.post(f"/api/campaigns/{cid}/answers", headers=TEAM,
                           json={"question_id": q["question_id"], "text": "Cell 2", "author": "Ana"}).status_code == 201
        job = wait_job(client, cid, "finished")
        assert job["result"]["status"] == "finished"
    assert outputs[1][-1] == "Ana, a person on the team, answered: Cell 2"


def test_cli_run_ask_wait_and_resume(store, monkeypatch, capsys):
    seen = []

    class Recording:
        def __init__(self, ledger, campaign, settings, resume=False):
            seen.append((campaign.budget_usd, settings.ask_wait_s, settings.profile, resume))

        def run(self):
            return {"status": "finished"}
    monkeypatch.setattr(runtime, "Campaigner", Recording)
    monkeypatch.setattr(tools, "import_video", lambda *a, **k: store[3])
    assert cli.main(["run", "--ledger", str(store[1]), "--budget", "0.1", "--ask-wait", "30"]) == 0
    assert seen[-1][1] == 30
    store[0].emit("seed", "campaign.started", "runtime", "Start")
    assert cli.main(["resume", "seed", "--ledger", str(store[1]), "--budget", "0.2", "--profile", "meta"]) == 0
    assert seen[-1] == (0.2, 0, "meta", True)
    store[0].emit("seed", "campaign.finished", "runtime", "Done", data={"status": "finished"})
    with pytest.raises(SystemExit):
        cli.main(["resume", "seed", "--ledger", str(store[1])])
    with pytest.raises(SystemExit):
        cli.main(["resume", "unknown", "--ledger", str(store[1])])
    with pytest.raises(SystemExit):
        cli.main(["run", "--ledger", str(store[1]), "--ask-wait", "-1"])


def test_a_job_file_is_never_read_while_a_worker_replaces_it(store):
    """Windows refuses to open a file another thread is replacing (PermissionError, seen once in
    test_resume_after_stop_and_one_campaign_at_a_time): reads and writes of job files share one lock."""
    import threading
    with TestClient(make_app(store)) as client:
        host = client.app.state.translucent.host("default")
        job = host.save(server.Job(campaign_id="seed", status="queued", profile="dev", created_at=0, updated_at=0))
        errors, done = [], threading.Event()

        def writer():
            try:
                for i in range(400):
                    host.save(job, status="running" if i % 2 else "paused")
            except Exception as e:      # noqa: BLE001 - the test reports any failure of the writer
                errors.append(e)
            finally:
                done.set()
        t = threading.Thread(target=writer)
        t.start()
        try:
            while not done.is_set():
                assert host.load_job("seed").campaign_id == "seed"
                assert [j.campaign_id for j in host.jobs()] == ["seed"]
        finally:
            t.join()
        assert not errors, errors
