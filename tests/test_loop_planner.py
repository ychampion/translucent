"""S7 planner: turns rebuilt from the ledger, the tools generated from the capability table, recorded receipts and
stops, exact tape replay, a constraint change that changes the next experiment, cross-campaign evidence refused, the
turn trigger, and the cost dry-run.

The recorded tape in tests/data/loop_tapes/toy-amendment was made by a real ChatSession against ScriptedChatClient (a
scripted fake client, never a paid model). A change to the prompt, the context, the tool schemas or the toy system
changes the bytes the model is sent, and the replay refuses the tape; re-record it from the checkout's root with
    PYTHONPATH=. uv run --no-sync python tests/test_loop_planner.py record
"""
import json
import math
import re
import shutil
import sys
from pathlib import Path

import pytest
from support.loop_systems import objective, register

from translucent import chain, exec_child, loop_runner
from translucent.executors import ingest_output
from translucent.intervention_chat import Settings
from translucent.ledger import Ledger
from translucent.locks import ExclusiveFileLock
from translucent.loop import PENDING, RUNNER, current_objective, request_states
from translucent.loop_planner import (CLOSER, PROMPT, DomainBaselinePlanner, ScriptedChatClient, ScriptedLoopPlanner,
                                      _shown, baseline_history, main, offered_tools, planner_context, planner_tick,
                                      run_turn, tool_schemas, turn_due, turn_lock_path, turn_session)
from translucent.loop_runner import LoopRunner
from translucent.loop_service import CAPABILITIES, Actor, LoopService, allowed, planner_tools

ROOT = Path(__file__).resolve().parents[1]
TAPES = Path(__file__).resolve().parent / "data" / "loop_tapes"
ANA = Actor("person", "Ana", "http")
AMBIENT = 40.0


@pytest.fixture(autouse=True)
def adapter(monkeypatch):
    return register(monkeypatch)


@pytest.fixture
def L(tmp_path):
    led = Ledger(tmp_path / "ledger")
    yield led
    led.db.close()


def _inline(monkeypatch):
    """Run computational requests in this process, hashing the output alone, and record a fixed worker: two runs of
    one campaign in different folders then record identical bytes."""
    def run(L, cid, req, adapter, *, worker, workdir, timeout_s):
        out = adapter.execute(req.action, dict(req.data.params), workdir)
        body = exec_child.encode_output(out, workdir)
        body.pop("files")
        return ingest_output(L, cid, req, out, adapter=adapter, basis="computational", submitted_by=RUNNER,
                             attempt=req.attempt, payload_sha256=chain.digest(body), run_params={"worker": worker})
    monkeypatch.setattr(loop_runner, "run_computational", run)
    monkeypatch.setattr(LoopRunner, "worker", property(lambda self: {"host": "test", "pid": 0,
                                                                     "process": self.process}))


@pytest.fixture
def inline(monkeypatch):
    _inline(monkeypatch)


def open_loop(L, kind="scripted", key=None, **kw):
    obj = objective(planner={"kind": kind, "profile": "meta" if kind.startswith("muse") else None, "client": None},
                    **kw)
    return LoopService(L, ANA, idempotency_key=key).open_campaign(obj, "Planner")["campaign_id"]


def approve_all(L, cid):
    for st in request_states(L, cid).values():
        if st.state == "awaiting_approval":
            LoopService(L, ANA).approve(cid, st.request_id, st.content_hash)


def execute(L, cid):
    runner = LoopRunner(L.root, process="cli", workers=1)
    n = runner.tick(cid)
    assert not runner.problems, runner.problems
    return n


def receipts(L, cid, phase=None):
    return [r for r in chain.records(L, cid) if r.type == "planner" and (phase is None or r.data["phase"] == phase)]


def finished(L, cid):
    return [r.data["payload"]["status"] for r in receipts(L, cid, "turn_finished")]


def sequence(L, cid):
    return [[r.id, r.type, r.actor, r.data] for r in chain.records(L, cid)]


# ------------------------------------------------------------------ the toy rule: evidence and constraints decide

def _measured(req, quantity):
    return next(m["value"] for m in req["result"]["measurements"] if m["quantity"] == quantity)


def _proposal(current, k, limit, why):
    t80, peak = 0.8 * 5.0 * 3600 / current, AMBIENT + k * current ** 2
    return ("propose_experiment", {
        "action": "simulate_charge", "params": {"current_A": current, "ambient_C": AMBIENT},
        "hypothesis": "Joule heating (I^2 R) sets the peak temperature; the charge time falls as 1/I.",
        "prediction": {"charge_time_80": {"value": round(t80, 3), "lo": round(0.95 * t80, 3),
                                          "hi": round(1.05 * t80, 3), "unit": "s"},
                       "max_temperature": {"value": round(peak, 3), "lo": round(peak - 1, 3),
                                           "hi": round(peak + 1, 3), "unit": "degC"}},
        "rationale": f"{why} The largest current on the 0.5 A grid predicted under {limit:g} degC is {current:g} A.",
        "expected_effect": "Under the limit, this current is the candidate; over it, the heating coefficient is "
                           "underestimated and the next current is lower."})


def toy_rule(ctx, messages):
    """A scripted planner that reads only its context: probe at 2 A, fit the heating coefficient k from the latest
    result, then propose the largest current predicted to stay under the current temperature limit; conclude once
    that current has run and passes. A new result or a new limit changes the next proposal."""
    if any(r["state"] in PENDING for r in ctx["requests"]):
        return [("wait", {"reason": "an experiment is pending"})]
    limit = next(c["limit"] for c in ctx["objective"]["constraints"] if c["quantity"] == "max_temperature")
    done = [r for r in ctx["requests"] if r.get("result")]
    if not done:
        return [_proposal(2.0, 0.04, limit, "A low-current probe measures the heating coefficient.")]
    last = done[-1]
    current = last["params"]["current_A"]["value"]
    k = (_measured(last, "max_temperature") - AMBIENT) / current ** 2
    best = min(10.0, math.floor(math.sqrt(max(limit - AMBIENT - 0.5, 0.0) / k) * 2) / 2)
    if best < 0.5:
        return [("conclude", {"text": "No current on the grid stays under the limit.",
                              "unresolved_reasons": ["no feasible current"]})]
    if any(r["params"]["current_A"]["value"] == best and all(c["ok"] for c in r["comparison"]["constraints"]) for r in done):
        return [("conclude", {"text": f"{best:g} A at {AMBIENT:g} degC is the fastest charge predicted and measured "
                                      f"under {limit:g} degC ({last['result']['result_id']})."})]
    return [_proposal(best, round(k, 6), limit, f"The latest result gives k = {k:.5f} K/A^2.")]


# ------------------------------------------------------------------ 1. a scripted campaign end to end

def test_a_scripted_campaign_runs_end_to_end_and_concludes(L):
    cid = open_loop(L)
    planner = ScriptedLoopPlanner(toy_rule)
    for _ in range(6):
        planner_tick(L.root, lambda *_: planner, cid)
        if chain.concluded(L, cid):
            break
        approve_all(L, cid)
        execute(L, cid)                       # the real runner: a child process per request
    done = chain.concluded(L, cid)
    assert done is not None and done.actor == "agent:scripted"
    assert done.data["outcome"] == "unresolved"            # no comparator in this slice: computed, not written
    assert finished(L, cid) == ["proposed", "proposed", "concluded"]
    currents = [st.data.params["current_A"] for st in request_states(L, cid).values()]
    assert currents == [2.0, 10.0]
    assert all(st.state == "completed" for st in request_states(L, cid).values())
    assert chain.problems(L, cid) == []
    assert turn_due(L, cid) == []
    # the hypothesis travels with the sealed request
    assert request_states(L, cid)[next(iter(request_states(L, cid)))].data.rationale.startswith("Hypothesis: ")


# ------------------------------------------------------------------ 2. exact tape replay

def tape_scenario(L, session_for, *, key="tape-toy"):
    """The recorded campaign: probe, best current, the engineer lowers the limit, the planner moves, concludes."""
    cid = open_loop(L, kind="muse", key=key, budget={"model_usd": 5.0, "experiments": 8, "experiment_usd": 0.0})
    results = []
    for step in range(6):
        if step == 2:
            LoopService(L, ANA).amend(cid, "The pack's thermal margin needs 3 K more headroom.",
                                      constraints=[{"quantity": "max_temperature", "op": "<=", "limit": 42.0,
                                                    "unit": "degC", "label": "Cell stays at or below 42 degC"}])
        results += planner_tick(L.root, session_for, cid)
        if chain.concluded(L, cid):
            break
        approve_all(L, cid)
        execute(L, cid)
    return cid, results


def _sessions(store, *, replay, client_planner=None):
    def session_for(L, cid, obj):
        n = len(receipts(L, cid, "turn_started")) + 1
        session = turn_session(L.root, cid, n, Settings(profile="meta", budget_usd=5.0), replay=replay,
                               client=None if replay else ScriptedChatClient(client_planner), store=store)
        session_for.made.append(session)
        return session
    session_for.made = []
    return session_for


def record_tape(dest: Path, ledger_root: Path):
    """Record tests/data/loop_tapes/toy-amendment: chat tapes per turn and the records they produced."""
    with pytest.MonkeyPatch.context() as mp:
        register(mp)
        _inline(mp)
        if dest.exists():
            shutil.rmtree(dest)
        L = Ledger(ledger_root)
        try:
            cid, results = tape_scenario(L, _sessions(dest, replay=False,
                                                      client_planner=ScriptedLoopPlanner(toy_rule)))
            (dest / "records.json").write_bytes(chain.canonical(sequence(L, cid)))
            return cid, results
        finally:
            L.db.close()


def test_replaying_the_recorded_tape_reproduces_the_records(L, inline):
    expected = json.loads((TAPES / "toy-amendment" / "records.json").read_text(encoding="utf-8"))
    sessions = _sessions(TAPES / "toy-amendment", replay=True)
    cid, results = tape_scenario(L, sessions)
    assert [r["status"] for r in results] == ["proposed", "proposed", "proposed", "concluded"]
    assert sequence(L, cid) == expected
    model = receipts(L, cid, "model_request")
    assert model and all(r.data["payload"]["model"] == ScriptedChatClient.MODEL for r in model)
    assert {r.actor for r in model} == {"agent:muse-wonder-1.0"}
    assert len(sessions.made) == len(list((TAPES / "toy-amendment").glob("turn-*"))) == 4
    for session in sessions.made:                                       # every recorded call was replayed
        session.assert_consumed()


def test_recording_then_replaying_in_another_folder_gives_identical_records(tmp_path, inline):
    store = tmp_path / "tape"
    first = Ledger(tmp_path / "a")
    second = Ledger(tmp_path / "b")
    try:
        cid1, _ = tape_scenario(first, _sessions(store, replay=False, client_planner=ScriptedLoopPlanner(toy_rule)))
        cid2, _ = tape_scenario(second, _sessions(store, replay=True))
        assert cid1 == cid2
        assert sequence(first, cid1) == sequence(second, cid2)
        costs = [r.data["payload"]["cost_usd"] for r in receipts(second, cid2, "model_request")]
        assert costs and all(c > 0 for c in costs)            # metered from the recorded call's own tokens
    finally:
        first.db.close()
        second.db.close()


def test_a_replay_that_sends_different_bytes_stops_without_a_provider(L, inline, tmp_path):
    store = tmp_path / "tape"
    other = Ledger(tmp_path / "other")
    try:
        tape_scenario(other, _sessions(store, replay=False, client_planner=ScriptedLoopPlanner(toy_rule)))
    finally:
        other.db.close()
    cid = open_loop(L, kind="muse", key="a-different-campaign",
                    budget={"model_usd": 5.0, "experiments": 8, "experiment_usd": 0.0})
    result = run_turn(L, cid, turn_session(L.root, cid, 1, Settings(profile="meta", budget_usd=5.0), replay=True,
                                           store=store))
    assert result["status"] == "replay_error"
    assert finished(L, cid) == ["replay_error"]


# ------------------------------------------------------------------ 3. a constraint change changes the next proposal

def test_an_amendment_changes_the_next_proposal_and_the_context_shows_it(tmp_path, inline):
    proposals, contexts = {}, {}
    for branch in ("amended", "unchanged"):
        L = Ledger(tmp_path / branch)
        try:
            cid = open_loop(L, key="same-prefix")
            planner = ScriptedLoopPlanner(toy_rule)
            run_turn(L, cid, planner)                          # probe at 2 A
            approve_all(L, cid)
            execute(L, cid)
            if branch == "amended":
                LoopService(L, ANA).amend(cid, "3 K more headroom for the pack",
                                          constraints=[{"quantity": "max_temperature", "op": "<=", "limit": 42.0,
                                                        "unit": "degC", "label": "At or below 42 degC"}])
            result = run_turn(L, cid, planner)
            assert result["status"] == "proposed"
            st = request_states(L, cid)[result["proposed"][0]]
            proposals[branch] = (st.data.params["current_A"], st.data.prediction["max_temperature"],
                                 st.data.objective_version)
            contexts[branch] = json.loads(planner.seen[-1][1]["content"])
        finally:
            L.db.close()
    assert proposals["unchanged"][0] == 10.0 and proposals["amended"][0] == 6.0      # the next proposal differs
    assert proposals["amended"][1]["value"] <= 42.0 and proposals["amended"][2] == 2  # predicted under the new limit
    ctx = contexts["amended"]
    limit = next(c for c in ctx["objective"]["constraints"] if c["quantity"] == "max_temperature")
    assert ctx["objective"]["version"] == 2 and limit["limit"] == 42.0 and limit["since_version"] == 2
    assert ctx["objective"]["amendments"][0]["changes"] == ["max_temperature 45 -> 42 degC"]
    assert any(t["kind"] == "amendment" for t in ctx["turn"]["new_since_last_turn"])
    # the earlier 2 A result is re-checked against version 2, and the unchanged branch never saw a version 2
    assert contexts["unchanged"]["objective"]["version"] == 1


# ------------------------------------------------------------------ 4. the planner's tools

def test_the_planner_tools_come_from_the_capability_table_and_exclude_people_and_system(L):
    tools = offered_tools()
    agent = set(planner_tools())
    people_only = allowed("person") - allowed("agent")
    system_only = allowed("system") - allowed("agent")
    assert people_only >= {"approve", "reject", "revise", "submit_result", "amend", "note"}
    for name, capability in tools.items():
        assert capability is None or (capability in agent and CAPABILITIES[capability].actors >= {"agent"})
        assert capability not in people_only | system_only
    assert set(tools) == {"get_view", "read_series", "get_request", "define_measurement", "propose_experiment",
                          "cancel_request", "conclude", "ask_person", "wait"}
    cid = open_loop(L)
    names = {s["function"]["name"] for s in tool_schemas(current_objective(L, cid))}
    assert names == set(tools)
    assert not names & {"approve", "approve_request", "reject", "revise", "submit_result", "amend", "note"}


def test_the_planners_question_to_a_person_is_scrubbed(L):
    """ask_person writes the model's own words into an event: a secret it echoes is removed first."""
    from types import SimpleNamespace
    from translucent.loop_planner import _Turn
    secret = "sk-" + "Zq8" * 10
    cid = open_loop(L)
    turn = _Turn(L, cid, SimpleNamespace(model="muse-test", records=[]), step_cap=4, full_requests=4, lessons=[],
                 triggers=[])
    seq = turn.dispatch("ask_person", {"question": f"Is {secret} the bench key?"})["seq"]
    ev = next(e for e in L.events(cid) if e.seq == seq)
    assert ev.type == "question.asked" and ev.detail == "Is [secret removed] the bench key?"
    assert secret not in ev.model_dump_json()


def test_a_model_calling_a_person_tool_is_refused_and_nothing_is_approved(L):
    cid = open_loop(L)
    planner = ScriptedLoopPlanner([
        [_proposal(2.0, 0.04, 45.0, "Probe.")],
    ])
    run_turn(L, cid, planner)
    st = next(iter(request_states(L, cid).values()))
    sneaky = ScriptedLoopPlanner([
        [("approve", {"request_id": st.request_id, "content_hash": st.content_hash}),
         ("submit_result", {"request_id": st.request_id}),
         ("amend", {"reason": "loosen", "constraints": []})],
        [("wait", {})]])
    LoopService(L, ANA).note(cid, "Please carry on.")
    result = run_turn(L, cid, sneaky)
    assert result["status"] == "waiting"
    errors = [r.data["payload"]["result"]["error"] for r in receipts(L, cid, "tool_result")
              if "error" in r.data["payload"]["result"]]
    assert len(errors) == 3 and all("is not a planner tool" in e for e in errors)
    assert request_states(L, cid)[st.request_id].state == "awaiting_approval"
    assert not [r for r in chain.records(L, cid) if r.type in ("approval", "amendment", "result")]


# ------------------------------------------------------------------ 5. stops are recorded

@pytest.mark.parametrize("replies,status", [
    (lambda ctx, m: [("get_view", {})], "step_cap"),
    (["Let me think about the heat.", "Still thinking.", "The answer is 4 A."], "stalled"),
    (["<|python_tag|>propose_experiment(current_A=4)"] * 3, "garbled"),
    ([{"content": None, "tool_calls": [{"id": "c1", "type": "function",
                                        "function": {"name": "propose_experiment", "arguments": "{current_A: 4"}}]}] * 3,
     "garbled"),
    ([[("teleport", {})]] * 3, "garbled"),
])
def test_stops_are_recorded_for_step_cap_stall_and_garbled_calls(L, replies, status):
    cid = open_loop(L)
    result = run_turn(L, cid, ScriptedLoopPlanner(replies), step_cap=3)
    assert result["status"] == status
    assert finished(L, cid) == [status]
    end = receipts(L, cid, "turn_finished")[-1].data["payload"]
    assert end["reason"] and end["turn"] == 1
    assert not request_states(L, cid)


def test_stops_are_recorded_for_the_session_and_the_campaign_model_budget(L):
    # the session's own cap: the first call's worst case already exceeds it, so no call is made
    cid = open_loop(L, kind="muse", key="session-cap",
                    budget={"model_usd": 5.0, "experiments": 8, "experiment_usd": 0.0})
    client = ScriptedChatClient(ScriptedLoopPlanner(toy_rule))
    session = turn_session(L.root, cid, 1, Settings(profile="meta", budget_usd=0.01), client=client)
    assert run_turn(L, cid, session)["status"] == "budget"
    assert finished(L, cid) == ["budget"] and not receipts(L, cid, "model_request")
    # the campaign's model budget across turns, below the session's cap
    cid2 = open_loop(L, kind="muse", key="campaign-cap",
                     budget={"model_usd": 0.01, "experiments": 8, "experiment_usd": 0.0})
    session = turn_session(L.root, cid2, 1, Settings(profile="meta", budget_usd=5.0), client=client)
    result = run_turn(L, cid2, session)
    assert result["status"] == "budget" and "model budget" in result["reason"]
    assert finished(L, cid2) == ["budget"]
    # a stopped turn is not re-run until something new happens
    assert turn_due(L, cid2) == []
    # and the view says the planner stopped on its budget, rather than that it will propose when its turn comes
    stopped = LoopService(L, ANA).view(cid2)["next_action"]
    assert stopped["kind"] == "planner_stopped" and stopped["status"] == "budget"
    assert stopped["title"] == result["reason"]


# ------------------------------------------------------------------ 6. evidence from another campaign is refused

def test_a_proposal_citing_a_measurement_from_another_campaign_is_refused(L, inline):
    a = open_loop(L, key="campaign-a")
    run_turn(L, a, ScriptedLoopPlanner([[_proposal(2.0, 0.04, 45.0, "Probe.")]]))
    approve_all(L, a)
    execute(L, a)
    run_turn(L, a, ScriptedLoopPlanner([[("define_measurement", {"name": "peak", "spec": {
        "op": "max", "channel": "temperature", "unit": "degC"}})], [("wait", {})]]))
    peak = next(r for r in receipts(L, a, "tool_result") if r.data["payload"]["tool"] == "define_measurement")
    mid = peak.data["payload"]["result"]["measurements"][0]["id"]

    b = open_loop(L, key="campaign-b")
    cite = dict(_proposal(4.0, 0.04, 45.0, "Uses a peak.")[1], evidence_ids=[mid])
    result = run_turn(L, b, ScriptedLoopPlanner([[("propose_experiment", cite)], [("wait", {})]]))
    assert result["status"] == "waiting" and result["proposed"] == []
    refusal = [r.data["payload"]["result"] for r in receipts(L, b, "tool_result")][0]
    assert "not a record of this campaign" in refusal["error"]
    assert not request_states(L, b)
    # the same citation inside its own campaign is accepted
    LoopService(L, ANA).note(a, "Go on.")
    result = run_turn(L, a, ScriptedLoopPlanner([[("propose_experiment", cite)]]))
    assert result["status"] == "proposed"
    assert request_states(L, a)[result["proposed"][0]].data.evidence_ids == [mid]


# ------------------------------------------------------------------ 7. the turn trigger

def test_a_turn_starts_automatically_after_a_result_and_not_before(L):
    cid = open_loop(L)
    planner = ScriptedLoopPlanner(toy_rule)
    tick = lambda: planner_tick(L.root, lambda *_: planner, cid)      # noqa: E731
    assert [t["kind"] for t in turn_due(L, cid)] == ["objective"]
    assert [r["status"] for r in tick()] == ["proposed"]
    assert turn_due(L, cid) == [] and tick() == []                      # its own proposal is not news
    approve_all(L, cid)
    assert turn_due(L, cid) == [] and tick() == []                      # an approval is not a result
    execute(L, cid)                                                     # the real runner records the result
    due = turn_due(L, cid)
    assert [t["kind"] for t in due] == ["result"]
    turn = tick()
    assert [r["status"] for r in turn] == ["proposed"] and turn[0]["turn"] == 2
    started = receipts(L, cid, "turn_started")[-1].data["payload"]
    assert [t["kind"] for t in started["triggers"]] == ["result"]
    assert started["context_seq"] >= due[0]["seq"]


def test_notes_rejections_and_amendments_start_a_turn_but_its_own_cancellation_does_not(L):
    cid = open_loop(L)
    run_turn(L, cid, ScriptedLoopPlanner([[_proposal(2.0, 0.04, 45.0, "Probe.")]]))
    st = next(iter(request_states(L, cid).values()))
    run_turn(L, cid, ScriptedLoopPlanner([[("cancel_request", {"request_id": st.request_id,
                                                               "reason": "rethinking"})]]), only_if_due=False)
    assert turn_due(L, cid) == []
    LoopService(L, ANA).note(cid, "Try the ambient at 40 degC.")
    assert [t["kind"] for t in turn_due(L, cid)] == ["human.note"]
    run_turn(L, cid, ScriptedLoopPlanner([[_proposal(3.0, 0.04, 45.0, "Probe.")]]))
    st = next(s for s in request_states(L, cid).values() if s.state == "awaiting_approval")
    LoopService(L, ANA).reject(cid, st.request_id, st.content_hash, "too slow")
    assert [t["kind"] for t in turn_due(L, cid)] == ["rejection"]


def test_an_interrupted_turn_is_closed_and_run_again(L):
    cid = open_loop(L)
    chain.append(L, cid, "planner", {"phase": "turn_started", "payload": {"turn": 1, "context_seq": 1}},
                 "agent:scripted", chain.natural_id("pln", cid, 1, 0))
    holder = ExclusiveFileLock(turn_lock_path(L.root, cid))
    assert holder.try_acquire()
    try:
        assert turn_due(L, cid) == []                                   # a live process holds the turn
        assert run_turn(L, cid, ScriptedLoopPlanner([]))["status"] == "busy"
        assert LoopService(L, ANA).view(cid)["next_action"]["kind"] == "planner_thinking"
    finally:
        holder.release()
    assert [t["kind"] for t in turn_due(L, cid)] == ["objective"]       # its process is gone: due again
    results = planner_tick(L.root, lambda *_: ScriptedLoopPlanner(toy_rule), cid)
    assert [r["status"] for r in results] == ["proposed"] and results[0]["turn"] == 2
    ends = receipts(L, cid, "turn_finished")
    assert [(r.actor, r.data["payload"]["turn"], r.data["payload"]["status"]) for r in ends] == \
        [(CLOSER, 1, "interrupted"), ("agent:scripted", 2, "proposed")]


# ------------------------------------------------------------------ variants: no feedback, domain baseline, muse

def test_the_no_feedback_arm_proposes_the_budget_in_one_turn_and_never_sees_a_result(L, inline):
    # a model planner needs a model budget to open; the scripted planner spends none of it
    cid = open_loop(L, kind="muse_no_feedback", budget={"model_usd": 1.0, "experiments": 3, "experiment_usd": 0.0})
    planner = ScriptedLoopPlanner([[_proposal(2.0, 0.04, 45.0, "One.")],
                                   [_proposal(6.0, 0.04, 45.0, "Two."), _proposal(10.0, 0.04, 45.0, "Three.")]])
    result = run_turn(L, cid, planner)
    assert result["status"] == "proposed" and len(result["proposed"]) == 3
    assert "Propose all 3 experiments now" in planner.seen[0][0]["content"]
    approve_all(L, cid)
    execute(L, cid)
    assert turn_due(L, cid) == []                                       # results never start a turn
    LoopService(L, ANA).amend(cid, "tighter", constraints=[{"quantity": "max_temperature", "op": "<=",
                                                            "limit": 42.0, "unit": "degC", "label": "42"}])
    assert turn_due(L, cid) == [] and planner_tick(L.root, lambda *_: planner, cid) == []
    ctx = planner_context(L, cid, no_results=True)
    assert all("result" not in r and r["state"] == "not shown" for r in ctx["requests"])
    assert ctx["incumbent"] is None


def test_the_domain_baseline_runs_the_adapters_policy_with_no_model(L, inline):
    cid = open_loop(L, kind="domain_baseline")
    for _ in range(4):
        planner_tick(L.root, lambda L_, c, o: DomainBaselinePlanner(L_, c), cid)
        if chain.concluded(L, cid):
            break
        approve_all(L, cid)
        execute(L, cid)
    assert chain.concluded(L, cid).actor == "agent:baseline-current-sweep"
    assert sorted(st.data.params["current_A"] for st in request_states(L, cid).values()) == [1.0, 2.0, 4.0, 6.0,
                                                                                             8.0]
    assert finished(L, cid) == ["proposed", "concluded"]
    assert all(st.data.prediction == {} for st in request_states(L, cid).values())


def test_baseline_history_carries_results_and_constraint_changes(L, inline):
    cid = open_loop(L, kind="domain_baseline")
    run_turn(L, cid, DomainBaselinePlanner(L, cid))
    approve_all(L, cid)
    execute(L, cid)
    LoopService(L, ANA).amend(cid, "tighter", constraints=[{"quantity": "max_temperature", "op": "<=",
                                                            "limit": 42.0, "unit": "degC", "label": "42"}])
    history = baseline_history(L, cid)
    assert history[0] == {"constraints": {"max_temperature": 45.0}, "objective_version": 1}    # as registered
    assert [set(h) for h in history[1:6]] == [{"action", "params", "state", "result"}] * 5
    assert history[1]["result"]["max_temperature"] == pytest.approx(25.0 + 0.04 * (1 - math.exp(-90)), abs=1e-6)
    assert history[-1] == {"constraints": {"max_temperature": 42.0}, "objective_version": 2}


def test_proposals_must_carry_a_hypothesis_and_predict_the_target_and_constraints(L):
    cid = open_loop(L)
    bare = dict(_proposal(4.0, 0.04, 45.0, "x")[1])
    bare["prediction"] = {"charge_time_80": bare["prediction"]["charge_time_80"]}
    no_hypothesis = dict(_proposal(4.0, 0.04, 45.0, "x")[1], hypothesis=" ")
    result = run_turn(L, cid, ScriptedLoopPlanner([[("propose_experiment", bare),
                                                    ("propose_experiment", no_hypothesis)], [("wait", {})]]))
    assert result["proposed"] == []
    errors = [r.data["payload"]["result"]["error"] for r in receipts(L, cid, "tool_result")
              if "error" in r.data["payload"]["result"]]
    assert "predict max_temperature with value, lo and hi" in errors[0]
    assert "state the hypothesis" in errors[1]


def test_a_muse_turn_meters_records_and_names_the_model(L, tmp_path):
    cid = open_loop(L, kind="muse", budget={"model_usd": 2.0, "experiments": 8, "experiment_usd": 0.0})
    session = turn_session(L.root, cid, 1, Settings(profile="meta", budget_usd=2.0),
                           client=ScriptedChatClient(ScriptedLoopPlanner(toy_rule)))
    result = run_turn(L, cid, session)
    assert result["status"] == "proposed" and result["model"] == "muse-wonder-1.0"
    st = next(iter(request_states(L, cid).values()))
    assert st.proposed_by == "agent:muse-wonder-1.0"
    model = receipts(L, cid, "model_request")
    assert len(model) == 1 and model[0].data["payload"]["digest"] == session.records[0]["digest"]
    assert "request" not in model[0].data["payload"]                    # the tape holds the bytes sent
    assert (L.root / "planner" / cid / "turn-1" / "chat" / "000001.json").is_file()
    sent = session.records[0]["request"]["messages"]
    assert sent[0]["content"] == PROMPT
    assert result["spent_usd"] == pytest.approx(model[0].data["payload"]["cost_usd"])
    spend = LoopService(L, ANA).view(cid)["spend"]                      # the LoopView shows the planner's spend
    assert spend["model_usd"] == pytest.approx(result["spent_usd"], abs=1e-6) and spend["model_budget_usd"] == 2.0


# ------------------------------------------------------------------ the context

def test_the_context_is_capped_and_holds_what_the_planner_needs(L, inline):
    cid = open_loop(L)
    agent = Actor("agent", "scripted", "planner")
    ids = []
    for current in (1.0, 2.0, 3.0, 4.0):
        ids.append(LoopService(L, agent).propose(cid, "simulate_charge", {"current_A": current, "ambient_C": 25.0},
                                                 rationale="r", prediction={}, expected_effect="e")["request_id"])
    for rq in ids[:2]:
        LoopService(L, agent).cancel(cid, rq, "older")
    LoopService(L, ANA).note(cid, "Keep the ambient at 25 degC.")
    approve_all(L, cid)
    execute(L, cid)
    ctx = planner_context(L, cid, full_requests=2)
    old, new = ctx["requests"][:2], ctx["requests"][2:]
    assert all(set(r) == {"request_id", "state", "action"} for r in old)
    assert [r["state"] for r in old] == ["cancelled", "cancelled"]
    assert all(r["comparison"]["constraints"][0]["ok"] is True for r in new)
    assert ctx["incumbent"]["request_id"] == ids[3]                     # fastest that passes
    assert ctx["budget"]["experiments_used"] == 2 and ctx["budget"]["experiments_remaining"] == 6
    assert ctx["notes"][0]["text"] == "Keep the ambient at 25 degC."
    action = next(a for a in ctx["system"]["actions"] if a["name"] == "simulate_charge")
    assert action["parameters"][0] == {"name": "current_A", "unit": "A", "lo": 0.5, "hi": 10.0,
                                       "label": "Charge current"}
    assert ctx["system"]["solver"]["validity"] == "test system only"
    text = json.dumps(ctx)
    assert not re.search(r'"(t|created_at|recorded_at|generated_at|as_of_seq)"\s*:', text)   # no wall-clock times
    assert planner_context(L, cid, full_requests=2) == ctx


def test_a_crossing_not_reached_is_shown_in_words_never_as_a_number():
    value = {"value": None, "unit": "s", "h": 0.0, "not_reached_within": [0.0, 1440.0]}
    check = {"quantity": "t_45", "value": None, "limit": 600.0, "ok": None, "not_reached_within": [0.0, 1440.0]}
    shown = _shown({"values": {"t_45": value}, "constraints": [check], "plain": {"value": 3.0, "unit": "s"}})
    assert shown["values"]["t_45"] == {"unit": "s", "value": "not reached within [0, 1440] s"}
    assert shown["constraints"][0] == {"quantity": "t_45", "limit": 600.0, "ok": None,
                                       "value": "not reached within [0, 1440] s"}
    assert shown["plain"] == {"value": 3.0, "unit": "s"}


def test_after_an_amendment_the_context_shows_earlier_results_rechecked_on_the_new_version(L, inline):
    cid = open_loop(L)
    run_turn(L, cid, ScriptedLoopPlanner([[_proposal(10.0, 0.04, 45.0, "Fast.")]]))
    approve_all(L, cid)
    execute(L, cid)
    before = planner_context(L, cid)["requests"][0]["comparison"]
    LoopService(L, ANA).amend(cid, "headroom", constraints=[{"quantity": "max_temperature", "op": "<=",
                                                             "limit": 42.0, "unit": "degC", "label": "42"}])
    ctx = planner_context(L, cid)
    after = ctx["requests"][0]["comparison"]
    assert (before["objective_version"], before["constraints"][0]["ok"]) == (1, True)
    assert (after["objective_version"], after["constraints"][0]["limit"], after["constraints"][0]["ok"]) == \
        (2, 42.0, False)
    assert ctx["incumbent"] is None                                     # the 44 degC run no longer passes


# ------------------------------------------------------------------ 8. dry-run cost estimate, no provider

def test_the_dry_run_prints_a_cost_estimate_for_a_muse_fastcharge_campaign_without_a_provider(monkeypatch, capsys):
    from translucent import providers, runtime

    def refuse(*a, **k):
        raise AssertionError("dry-run must not construct a provider client")
    monkeypatch.setattr(runtime, "_client", refuse)
    monkeypatch.setattr(providers.MeteredMeta, "__init__", refuse)
    monkeypatch.setattr(providers.MeteredFireworks, "__init__", refuse)
    assert main(["dry-run", "--profile", "meta"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["system"] == "pybamm-fastcharge" and out["kind"] == "muse" and out["model"] == "muse-wonder-1.0"
    assert out["dry_run"] is True and out["experiments"] == 8
    assert 0 < out["first_call_reservation_usd"] < out["typical_estimate_usd"] < out["ceiling_estimate_usd"]
    assert out["first_call_input_chars"] > len(PROMPT)


def test_the_paid_smoke_refuses_without_live(capsys):
    with pytest.raises(SystemExit):
        main(["smoke", "--ledger", "unused", "--profile", "dev"])


def test_the_planner_module_never_updates_or_deletes():
    text = (ROOT / "translucent" / "loop_planner.py").read_text(encoding="utf-8")
    assert not re.search(r"\b(UPDATE|DELETE)\s", text)


if __name__ == "__main__" and sys.argv[1:] == ["record"]:
    import tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    with tempfile.TemporaryDirectory() as tmp:
        cid, results = record_tape(TAPES / "toy-amendment", Path(tmp) / "ledger")
        print(json.dumps({"campaign_id": cid, "turns": results}, indent=2))
