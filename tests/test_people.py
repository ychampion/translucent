"""People in the loop (runtime side): notes, questions, pause/stop, provider retries, resume. Offline only."""
import threading
import time
from types import SimpleNamespace

import cv2
import httpx
import numpy as np
import openai
import pytest

from evals.fake_llm import FakeClient
from translucent import gate, runtime
from translucent.budget import BudgetExceeded
from translucent.contracts import Claim, Measurement, Predicate, Review, Run, Source, Uncertainty
from translucent.ledger import Ledger
from translucent.runtime import NOBODY, Campaigner, Settings, new_campaign
from translucent.verify import verify

REQ = httpx.Request("POST", "https://provider.invalid/v1/chat/completions")


@pytest.fixture(autouse=True)
def no_live_models(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Paid model calls are forbidden in these tests")
    monkeypatch.setattr(runtime, "_client", denied)


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "ledger"
    L = Ledger(root)
    path = tmp_path / "tiny.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 1, (16, 16))
    assert writer.isOpened()
    writer.write(np.zeros((16, 16, 3), dtype=np.uint8))
    writer.release()
    video = L.put_file(path, kind="video", media_type="video/mp4", label="Fixture movie",
                       source=Source(uri="experiment:fixture", citation="Fixture et al."))
    w = SimpleNamespace(L=L, root=root, vid=video.id, others=[])
    w.campaign = lambda budget=1.0: new_campaign(L, "Does the voltage hold?", "People", [video.id], memory="off",
                                                 budget_usd=budget)

    def person():  # another connection, as the API thread writes
        other = Ledger(root)
        w.others.append(other)
        return other
    w.person = person
    yield w
    for other in w.others:
        other.db.close()
    L.db.close()


def campaigner(w, c, policy, resume=False, **settings):
    camp = Campaigner(w.L, c, Settings(profile="dev", max_steps=8, poll_s=0.01, retry_base_s=0, **settings),
                      resume=resume)
    fake = FakeClient(policy, usd_per_call=0.01)
    camp.clients = {"fireworks": fake, "meta": fake}
    return camp, fake


def evidence(L, cid, vid):
    """One recorded voltage, a finding the gate supports and one it refuses."""
    L.add(Run(id=f"run_{cid}", campaign_id=cid, step_id="s1", tool="read_cell_voltage", idempotency_key=cid,
              input_ids=[vid], status="ok", measurement_ids=[f"m_{cid}"], started_at=0, ended_at=0))
    L.add(Measurement(id=f"m_{cid}", run_id=f"run_{cid}", quantity="voltage", value=2, unit="V", artifact_ids=[vid],
                      uncertainty=Uncertainty(value=0.01, kind="bound", method="fixture"), label="Cell voltage"))
    claims = [Claim(id=f"k1_{cid}", campaign_id=cid, author="orchestrator", statement="The voltage is near 2 V.",
                    predicate=Predicate(type="value_within", params={"lo": 1.9, "hi": 2.1, "unit": "V"},
                                        measurement_ids=[f"m_{cid}"]), created_at=time.time()),
              Claim(id=f"k2_{cid}", campaign_id=cid, author="orchestrator", statement="The voltage holds for 2 s.",
                    predicate=Predicate(type="value_within", params={"lo": 1, "hi": 3, "unit": "s"},
                                        measurement_ids=[f"m_{cid}"]), created_at=time.time())]
    return [gate.review(L, L.add(k).id) for k in claims]


def finish(text="done"):
    return "Done.", [("finish", {"answer": text, "not_proven": ""})]


def events(w, cid, kind):
    return [e for e in w.L.events(cid) if e.type == kind]


def tool_outputs(messages):
    return [m["content"] for m in messages if m["role"] == "tool"]


# ------------------------------------------------------------------ the gate alone decides

def test_human_challenge_never_changes_status_and_verify_still_passes(world):
    c = world.campaign()
    good, bad = evidence(world.L, c.id, world.vid)
    assert good.verdict == "supported" and bad.verdict != "supported"
    rv = world.L.add(Review(id="rev_human", claim_id=good.claim_id, reviewer="human", verdict="refuted",
                            reason="The scale bar looks wrong to me.", author="Ana", created_at=time.time()))
    world.L.emit(c.id, "claim.reviewed", "person:Ana", "Ana challenged a finding", refs=[good.claim_id, rv.id],
                 data={"review": rv.model_dump()})
    world.L.add(Review(id="rev_human2", claim_id=bad.claim_id, reviewer="human", verdict="supported",
                       reason="Looks right.", author="Ben", created_at=time.time()))
    assert world.L.claim_status(good.claim_id) == "supported"
    assert world.L.claim_status(bad.claim_id) == bad.verdict
    result = verify(world.root)
    assert result.ok, result.problems
    assert result.findings == 2 and result.matched == 2


# ------------------------------------------------------------------ inbox

def test_note_reaches_the_agent_before_its_next_step(world):
    c, seen = world.campaign(), []
    def agent(messages, tools_):
        users = [m["content"] for m in messages if m["role"] == "user"]
        seen.append(users)
        if len(seen) == 1:
            world.person().emit(c.id, "human.note", "person:Ana", "Ana left a note",
                                data={"text": "Use the recorded scale bar, not the stated field of view.", "author": "Ana"})
            return "Looking at the movie.", [("ask_person", {"question": "Which cell is this?"})]
        note = next(u for u in users if u.startswith("Note from Ana"))
        return finish(f"Acted on: {note}")
    camp, _ = campaigner(world, c, agent)
    result = camp.run()
    assert result["status"] == "finished"
    assert not any("Note from" in u for u in seen[0])
    assert "Note from Ana, a person on the team: Use the recorded scale bar, not the stated field of view." in seen[1]
    assert "Use the recorded scale bar" in result["answer"]
    assert "ask_person" in {t["function"]["name"] for t in runtime.EXTRA_TOOLS}
    assert "ask_person" in runtime.ORCHESTRATOR and "never ask permission" in runtime.ORCHESTRATOR


def test_challenge_and_sign_off_reach_the_agent_in_contract_wording(world):
    c, seen = world.campaign(), []
    good, _ = evidence(world.L, c.id, world.vid)
    def agent(messages, tools_):
        seen.append([m["content"] for m in messages if m["role"] == "user"])
        if len(seen) == 1:
            other = world.person()
            for who, verdict, reason in (("Ana", "refuted", "The scale bar was read from the wrong frame."),
                                         ("Ben", "supported", "Matches our notebook.")):
                rv = other.add(Review(id=f"rev_{who}", claim_id=good.claim_id, reviewer="human", verdict=verdict,
                                      reason=reason, author=who, created_at=time.time()))
                other.emit(c.id, "claim.reviewed", f"person:{who}", f"{who} reviewed a finding",
                           data={"review": rv.model_dump()})
            return "Waiting.", [("ask_person", {"question": "Anything else?"})]
        return finish()
    camp, _ = campaigner(world, c, agent)
    camp.run()
    assert (f"Ana challenged finding {good.claim_id}: The scale bar was read from the wrong frame. Re-examine it: if "
            "the evidence holds, say why in one sentence; if not, propose a corrected finding with supersedes.") in seen[1]
    assert any(u.startswith(f"Ben, a person on the team, signed off on finding {good.claim_id}") for u in seen[1])
    assert world.L.claim_status(good.claim_id) == "supported"


# ------------------------------------------------------------------ ask_person

def answer_when_asked(w, cid, text):
    def go():
        other = Ledger(w.root)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                asked = [e for e in other.events(cid) if e.type == "question.asked"]
                if asked:
                    other.emit(cid, "question.answered", "person:Ana", f"Ana answered: {text}",
                               data={"question_id": asked[-1].data["question_id"], "text": text, "author": "Ana"})
                    return
                time.sleep(0.01)
        finally:
            other.db.close()
    thread = threading.Thread(target=go)
    thread.start()
    return thread


def test_ask_person_gets_an_answer_written_by_another_thread_mid_wait(world):
    c, outs, users = world.campaign(), [], []
    def agent(messages, tools_):
        outs.append(tool_outputs(messages))
        users.append([m["content"] for m in messages if m["role"] == "user"])
        if len(outs) == 1:
            return "Asking the team.", [("ask_person", {"question": "How long is the scale bar?",
                                                        "options": ["10 um", "20 um"]})]
        return finish()
    camp, fake = campaigner(world, c, agent, ask_wait_s=20)
    thread = answer_when_asked(world, c.id, "10 um, from the lab notebook")
    t0 = time.monotonic()
    try:
        assert camp.run()["status"] == "finished"
    finally:
        thread.join()
    assert time.monotonic() - t0 < 15
    assert outs[1][-1] == "Ana, a person on the team, answered: 10 um, from the lab notebook"
    asked = events(world, c.id, "question.asked")[0]
    assert asked.actor == "orchestrator" and asked.title == "Asked the team: How long is the scale bar?"
    assert asked.data["options"] == ["10 um", "20 um"]
    assert events(world, c.id, "question.answered") and not events(world, c.id, "question.expired")
    assert not any("earlier question" in u for u in users[1])  # delivered once, as the tool result
    assert len(fake.calls) == 2  # waiting spent nothing


@pytest.mark.parametrize("wait", [0, 0.05])
def test_ask_person_expires_when_nobody_answers(world, wait):
    c, outs = world.campaign(), []
    def agent(messages, tools_):
        outs.append(tool_outputs(messages))
        return finish() if len(outs) > 1 else ("Asking.", [("ask_person", {"question": "Which cell?"})])
    camp, _ = campaigner(world, c, agent, ask_wait_s=wait)
    t0 = time.monotonic()
    camp.run()
    assert outs[1][-1] == NOBODY and NOBODY.startswith("Nobody is available")
    asked, expired = events(world, c.id, "question.asked")[0], events(world, c.id, "question.expired")
    assert [e.data for e in expired] == [{"question_id": asked.data["question_id"]}]
    assert time.monotonic() - t0 < 10


# ------------------------------------------------------------------ pause, resume, stop

def test_pause_then_resume_then_stop(world):
    c, statuses, steps = world.campaign(), [], []
    def resume_after_pause():
        other = Ledger(world.root)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not [e for e in other.events(c.id) if e.type == "campaign.paused"]:
                time.sleep(0.01)
            other.emit(c.id, "campaign.control", "person:Ana", "Ana asked to resume the campaign",
                       data={"action": "resume", "author": "Ana"})
        finally:
            other.db.close()
    thread = threading.Thread(target=resume_after_pause)
    def agent(messages, tools_):
        steps.append(1)
        action = "pause" if len(steps) == 1 else "stop"
        world.person().emit(c.id, "campaign.control", "person:Ana", f"Ana asked to {action} the campaign",
                            data={"action": action, "author": "Ana"})
        if action == "pause":
            thread.start()
        return "Working.", [("ask_person", {"question": f"Step {len(steps)}?"})]
    camp, fake = campaigner(world, c, agent, on_status=statuses.append)
    try:
        result = camp.run()
    finally:
        thread.join()
    assert result["status"] == "stopped" and result["by"] == "Ana"
    assert len(fake.calls) == 2 and statuses == ["paused", "running"]
    titles = [e.title for e in world.L.events(c.id) if e.actor == "runtime"]
    assert "Paused by Ana" in titles and "Resumed by Ana" in titles
    done = events(world, c.id, "campaign.finished")[-1]
    assert done.title == "Stopped by Ana" and done.data["status"] == "stopped"
    assert events(world, c.id, "campaign.paused")[0].data == {"by": "Ana"}


def test_pause_past_the_limit_stops_the_campaign(world):
    c = world.campaign()
    def agent(messages, tools_):
        world.person().emit(c.id, "campaign.control", "person:Ana", "Ana asked to pause the campaign",
                            data={"action": "pause", "author": "Ana"})
        return "Working.", [("ask_person", {"question": "Anyone?"})]
    camp, fake = campaigner(world, c, agent, pause_limit_s=0.05)
    result = camp.run()
    assert result["status"] == "stopped" and result["by"] is None and len(fake.calls) == 1
    assert result["reason"] == "pause_limit"
    assert events(world, c.id, "campaign.finished")[-1].title == "Campaign stopped (paused for too long)"


def test_host_shutdown_ends_a_pause_and_a_wait_at_once(world):
    c, outs = world.campaign(), []
    def agent(messages, tools_):
        outs.append(tool_outputs(messages))
        if len(outs) == 1:
            return "Asking.", [("ask_person", {"question": "Anyone?"})]
        world.person().emit(c.id, "campaign.control", "person:Ana", "Ana asked to pause the campaign",
                            data={"action": "pause", "author": "Ana"})
        return "Working.", [("ask_person", {"question": "Still there?"})]
    camp, _ = campaigner(world, c, agent, ask_wait_s=600, pause_limit_s=600, shutting_down=lambda: True)
    t0 = time.monotonic()
    result = camp.run()
    assert time.monotonic() - t0 < 10 and outs[1][-1] == NOBODY
    assert result["status"] == "stopped" and result["reason"] == "shutdown"
    assert events(world, c.id, "campaign.finished")[-1].title == "Campaign stopped (the server shut down)"


# ------------------------------------------------------------------ provider retries

def status_error(code):
    cls = openai.RateLimitError if code == 429 else openai.InternalServerError if code >= 500 else openai.APIStatusError
    return cls("provider says no", response=httpx.Response(code, request=REQ), body=None)


def test_transient_errors_are_retried_and_every_attempt_is_metered(world):
    c = world.campaign()
    failures = [openai.APIConnectionError(request=REQ), openai.APITimeoutError(request=REQ)]
    def agent(messages, tools_):
        if failures:
            raise failures.pop(0)
        return finish()
    camp, fake = campaigner(world, c, agent)
    assert camp.run()["status"] == "finished"
    retries = events(world, c.id, "campaign.retry")
    assert [e.data["attempt"] for e in retries] == [1, 2]
    assert all(e.data["role"] == "orchestrator" and e.data["wait_s"] == 0 for e in retries)
    assert "APIConnectionError" in retries[0].data["error"] and "APITimeoutError" in retries[1].data["error"]
    assert fake.spent == pytest.approx(0.03) and camp.spent == pytest.approx(0.03)
    assert sum(e.data["usd"] for e in events(world, c.id, "cost.updated")) == pytest.approx(0.03)
    assert runtime._transient(status_error(429)) and runtime._transient(status_error(503))
    assert not runtime._transient(status_error(400)) and not runtime._transient(ValueError("x"))


def test_exhausted_retries_end_the_campaign_as_provider_error(world):
    c, attempts = world.campaign(), []
    def agent(messages, tools_):
        attempts.append(1)
        raise status_error(502)
    camp, fake = campaigner(world, c, agent)
    result = camp.run()
    assert result["status"] == "provider_error" and len(attempts) == 4
    assert [e.data["attempt"] for e in events(world, c.id, "campaign.retry")] == [1, 2, 3]
    done = events(world, c.id, "campaign.finished")[-1]
    assert done.title == "Campaign stopped (the model service kept failing)"
    assert "failed 4 times in a row" in done.detail and "can be resumed" in done.detail
    assert camp.spent == pytest.approx(0.04)


def test_a_budget_refusal_is_never_retried(world):
    c, attempts = world.campaign(), []
    def agent(messages, tools_):
        attempts.append(1)
        raise BudgetExceeded("cap reached")
    camp, _ = campaigner(world, c, agent)
    with pytest.raises(BudgetExceeded):
        camp.run()
    assert len(attempts) == 1 and not events(world, c.id, "campaign.retry")


@pytest.mark.parametrize("code", [400, 401, 404])
def test_a_refused_request_ends_the_campaign_plainly_without_retry(world, code):
    c, attempts = world.campaign(), []
    def agent(messages, tools_):
        attempts.append(1)
        raise status_error(code)
    camp, _ = campaigner(world, c, agent)
    result = camp.run()
    assert result["status"] == "provider_error" and result["refused"] and len(attempts) == 1
    assert not events(world, c.id, "campaign.retry")
    done = events(world, c.id, "campaign.finished")[-1]
    assert done.title == "Campaign stopped (the model service refused the request)" and "can be resumed" in done.detail


def test_tool_schemas_use_only_json_schema_regex():
    """Meta's Model API validates tool schemas and refuses Python-only anchors such as backslash-Z (a 400 on every call)."""
    import json
    from translucent import tools
    text = json.dumps([t["schema"] for t in tools.REGISTRY.values()] + runtime.EXTRA_TOOLS)
    assert "\\Z" not in text and "\\A" not in text and "(?P<" not in text


# ------------------------------------------------------------------ resume

@pytest.mark.parametrize("previous", ["interrupted", "stopped", "stalled"])
def test_resume_rebuilds_context_from_the_ledger(world, previous):
    c = world.campaign()
    world.L.emit(c.id, "campaign.started", "runtime", "People")
    good, bad = evidence(world.L, c.id, world.vid)
    world.L.emit(c.id, "human.note", "person:Ana", "Ana left a note", data={"text": "Cell 2 is the shorted one.",
                                                                            "author": "Ana"})
    world.L.emit(c.id, "question.asked", "orchestrator", "Asked the team: Which electrode is the anode?",
                 data={"question_id": "q_1", "question": "Which electrode is the anode?", "options": None})
    world.L.emit(c.id, "question.answered", "person:Ben", "Ben answered: the left one",
                 data={"question_id": "q_1", "text": "The left one.", "author": "Ben"})
    world.L.add(Review(id="rev_h", claim_id=good.claim_id, reviewer="human", verdict="refuted",
                       reason="Wrong frame.", author="Ana", created_at=time.time()))
    if previous != "interrupted":
        world.L.emit(c.id, "campaign.finished", "runtime", "Stopped", data={"status": previous})
    seen = []
    def agent(messages, tools_):
        seen.append(messages)
        return finish()
    camp, _ = campaigner(world, c, agent, resume=True)
    assert camp.run()["status"] == "finished"
    context = seen[0][1]["content"]
    assert seen[0][0]["role"] == "system" and seen[0][1]["role"] == "user"
    assert f"(it was {previous})" in context
    assert f"m_{c.id}: Cell voltage = 2 V ± 0.01" in context
    assert good.claim_id in context and bad.claim_id in context and bad.reason in context
    assert "Gate verdict: supported" in context and "Ana challenged it: Wrong frame." in context
    assert "Ana: Cell 2 is the shorted one." in context
    assert "Which electrode is the anode? -> Ben answered: The left one." in context
    resumed = events(world, c.id, "campaign.resumed")[-1]
    assert resumed.data["previous"] == previous and resumed.title == "Campaign picked up where it left off"
    assert resumed.data["profile"] == "dev" and resumed.data["models"]["planner"] == runtime.SPARK_OPEN
    assert events(world, c.id, "campaign.finished")[-1].data["status"] == "finished"
    # what was in the ledger before resuming is in the rebuilt context, not repeated as fresh inbox messages
    assert not any(m["content"].startswith("Note from") for m in seen[0] if m["role"] == "user")


def test_resume_remembers_tool_work_that_left_no_measurement(world):
    """Frames looked at, the worker's points and outline ids survive a resume, so the agent does not redo them."""
    c = world.campaign()
    world.L.emit(c.id, "campaign.started", "runtime", "People")
    world.L.emit(c.id, "tool.finished", "inspect_video", "Looking at the experiment", data={
        "status": "ok", "summary": "1920x1080 px. Image shows frame 230, region x 0-1920, y 0-1080, gridlines every 100 px."})
    world.L.emit(c.id, "step.finished", "worker", "Found 2 places to track", detail="Tips on the right edge.",
                 data={"points": [[300, 180], [300, 290]], "frame": 230})
    long = "Outlined 49 captured images: " + "#1: 49 frames flagged; " * 40 + "Outlines id sha256:abc123."
    world.L.emit(c.id, "tool.finished", "segment_images", "Outlining", data={"status": "ok", "summary": long})
    seen = []
    def agent(messages, tools_):
        seen.append(messages)
        return finish()
    camp, _ = campaigner(world, c, agent, resume=True)
    assert camp.run()["status"] == "finished"
    context = seen[0][1]["content"]
    assert "Frames already looked at (1;" in context and "230, region x 0-1920, y 0-1080" in context
    assert "worker on frame 230: Tips on the right edge. Points [[300, 180], [300, 290]]" in context
    assert "Outlines id sha256:abc123." in context and len(long) > 420
