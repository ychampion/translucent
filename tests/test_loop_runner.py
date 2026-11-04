"""S4a runner: claims under the file lock across real processes, recovery after the runner and its child are
killed mid-execution, and an MCP-style inline tick racing an API-style runner thread on one ledger."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from support.loop_systems import HOLD_ENV, objective, register

from translucent import chain
from translucent.ledger import Ledger
from translucent.loop import request_states
from translucent.loop_contracts import LoopView
from translucent.loop_runner import LoopRunner
from translucent.loop_service import Actor, LoopService
from translucent.loop_view import view

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
ANA = Actor("person", "Ana", "http")
MUSE = Actor("agent", "muse-test", "planner")

RUNNER = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from support.loop_systems import register
register()
from translucent.loop_runner import LoopRunner
root, cid, go = sys.argv[2], sys.argv[3], Path(sys.argv[4])
while not go.exists():
    time.sleep(0.005)
runner = LoopRunner(root, process="cli", workers=1)
n = runner.tick(cid)
print(json.dumps({"advanced": n, "problems": runner.problems}), flush=True)
"""


@pytest.fixture(autouse=True)
def adapter(monkeypatch):
    return register(monkeypatch)


@pytest.fixture
def L(tmp_path):
    led = Ledger(tmp_path / "ledger")
    yield led
    led.db.close()


@pytest.fixture
def hold(tmp_path, monkeypatch):
    """While this file exists, hold_charge runs wait; the variable reaches runner processes and their children."""
    path = tmp_path / "hold"
    path.touch()
    monkeypatch.setenv(HOLD_ENV, str(path))
    yield path
    path.unlink(missing_ok=True)


def queued(L, action="hold_charge", current=2.0, cid=None):
    cid = cid or LoopService(L, ANA).open_campaign(objective(), "Runner")["campaign_id"]
    req = LoopService(L, MUSE).propose(cid, action, {"current_A": current, "ambient_C": 25.0}, rationale="r",
                                       prediction={}, expected_effect="e")
    LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])
    return cid, req["request_id"]


def spawn_runner(L, cid, go):
    return subprocess.Popen([sys.executable, "-c", RUNNER, str(TESTS), str(L.root), cid, str(go)], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def wait_for(predicate, timeout=60.0, what="condition"):
    deadline = time.monotonic() + timeout
    while not (value := predicate()):
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.05)
    return value


def executions(L, cid, rq):
    return [(r.data["phase"], r.data["attempt"]) for r in chain.records(L, cid)
            if r.type == "execution" and r.data["request_id"] == rq]


def kill(proc_or_pid):
    if isinstance(proc_or_pid, subprocess.Popen):
        proc_or_pid.kill()
        proc_or_pid.wait(timeout=30)
    else:
        try:
            os.kill(proc_or_pid, signal.SIGTERM)
        except OSError:
            pass


# ------------------------------------------------------------------ 4. two runner processes, one request

def test_two_runner_processes_produce_one_execution_and_one_result(L, tmp_path, hold):
    cid, rq = queued(L)
    go = tmp_path / "go"
    procs = [spawn_runner(L, cid, go) for _ in range(2)]
    try:
        time.sleep(1.0)                    # both import and open the ledger, then tick together
        go.touch()
        workdir = L.root / "work" / "exec" / rq / "1"
        wait_for(lambda: (workdir / "started.txt").exists(), what="the child to start")
        # while one runner holds the claim, the request is running and the other runner has nothing to take
        assert LoopService(L, ANA).request(cid, rq)["state"] == "running"
        assert LoopService(L, ANA).view(cid)["next_action"]["kind"] == "running"
        idle = wait_for(lambda: next((p for p in procs if p.poll() is not None), None), what="the idle runner")
        out = json.loads(idle.communicate(timeout=30)[0])
        assert out == {"advanced": 0, "problems": []}
        hold.unlink()
        busy = next(p for p in procs if p is not idle)
        stdout, stderr = busy.communicate(timeout=120)
        assert json.loads(stdout) == {"advanced": 1, "problems": []}, stderr
    finally:
        for p in procs:
            if p.poll() is None:
                kill(p)
    assert executions(L, cid, rq) == [("started", 1)]
    assert [r.type for r in chain.records(L, cid)].count("result") == 1
    assert request_states(L, cid)[rq].state == "completed" and chain.problems(L, cid) == []


# ------------------------------------------------------------------ 5. killed mid-execution, then restarted

@pytest.mark.parametrize("action, ending", [("hold_charge", "completed"), ("hold_charge_nd", "failed")])
def test_a_run_killed_mid_execution_is_recovered_on_restart(L, tmp_path, hold, action, ending):
    cid, rq = queued(L, action=action)
    go = tmp_path / "go"
    go.touch()
    runner = spawn_runner(L, cid, go)
    child_pid = None
    try:
        marker = L.root / "work" / "exec" / rq / "1" / "started.txt"
        wait_for(lambda: marker.exists() and marker.read_text(), what="the child to start")
        child_pid = int(marker.read_text())
        assert request_states(L, cid)[rq].state == "running"
        kill(runner)                       # the runner first, so it never sees its child die and records nothing
        kill(child_pid)
    finally:
        if runner.poll() is None:
            kill(runner)
        if child_pid:
            kill(child_pid)
    hold.unlink()
    assert request_states(L, cid)[rq].state == "running"         # what a crash leaves behind

    restarted = LoopRunner(L.root, process="api", workers=1)
    # Windows releases a killed process's lock asynchronously; until then the request still looks claimed
    wait_for(lambda: restarted.tick(cid) == 1, timeout=30, what="the dead runner's lock to be released")
    assert restarted.problems == []
    st = request_states(L, cid)[rq]
    assert st.state == ending
    if ending == "completed":
        assert executions(L, cid, rq) == [("started", 1), ("abandoned", 1), ("started", 2)]
        assert st.result.data["attempt"] == 2
    else:
        assert executions(L, cid, rq) == [("started", 1), ("abandoned", 1)]
        assert st.failure.data["attempt"] == 1 and "not deterministic" in st.failure.data["error"]
        assert st.failure.data["retryable"] is False
    assert restarted.tick(cid) == 0 and chain.problems(L, cid) == []


def test_a_deterministic_action_abandoned_three_times_fails(L, monkeypatch):
    """Without real kills: a runner that dies after `started` leaves the request running with its lock free."""
    cid, rq = queued(L, action="simulate_charge")
    runner = LoopRunner(L.root, process="cli", workers=1)
    monkeypatch.setattr("translucent.loop_runner.run_computational", lambda *a, **k: None)   # dies, records nothing
    for n in (1, 2, 3):
        assert runner.tick(cid) == 1                       # claims; the run "dies" (records nothing)
        assert request_states(L, cid)[rq].state == "running"
    assert runner.tick(cid) == 1
    st = request_states(L, cid)[rq]
    assert st.state == "failed" and "last of 3 attempts" in st.failure.data["error"]
    assert executions(L, cid, rq) == [("started", 1), ("abandoned", 1), ("started", 2), ("abandoned", 2),
                                      ("started", 3), ("abandoned", 3)]
    assert chain.problems(L, cid) == []
    v = view(L, cid, {"kind": "live", "api": "", "ledger": "lab"})       # the person sees why it failed
    TypeAdapter(LoopView).validate_python(v, strict=False)
    shown = next(r for r in v["requests"] if r["request_id"] == rq)
    assert shown["failure"]["attempt"] == 3 and "last of 3 attempts" in shown["failure"]["reason"]
    assert all("failure" not in r for r in v["requests"] if r["request_id"] != rq)


# ------------------------------------------------------------------ 12. inline tick and a runner thread together

def test_an_inline_tick_and_a_runner_thread_never_duplicate_work(L):
    cid, first = queued(L, action="simulate_charge", current=1.0)
    ids = [first] + [queued(L, action="simulate_charge", current=c, cid=cid)[1] for c in (2.0, 3.0, 4.0, 5.0)]
    api = LoopRunner(L.root, process="api", workers=2)
    mcp = LoopRunner(L.root, process="mcp", workers=1)
    stop = threading.Event()

    def api_loop():
        while not stop.is_set():
            api.tick()
            time.sleep(0.01)

    thread = threading.Thread(target=api_loop, daemon=True)
    thread.start()
    try:
        def all_done():
            mcp.tick(cid, limit=1)                         # what MCP's wait_for_request does, inline
            return all(s.state == "completed" for s in request_states(L, cid).values())
        wait_for(all_done, timeout=300, what="every request to complete")
    finally:
        stop.set()
        thread.join(timeout=120)
    assert api.problems == [] and mcp.problems == []
    for rq in ids:
        assert executions(L, cid, rq) == [("started", 1)]
    recs = chain.records(L, cid)
    assert sorted(r.data["request_id"] for r in recs if r.type == "result") == sorted(ids)
    assert {r.data["worker"]["process"] for r in recs if r.type == "execution"} <= {"api", "mcp"}
    assert chain.problems(L, cid) == []


def test_the_export_command_writes_a_loop_campaign_that_verify_accepts(L, tmp_path, capsys):
    from translucent import cli
    cid, _ = queued(L)
    out = tmp_path / "campaign.loop.json"
    L.db.close()
    assert cli.main(["export", "--ledger", str(L.root), cid, str(out)]) == 0
    assert "loop campaign" in capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8"))["view"]["campaign"]["id"] == cid
    assert cli.main(["verify", str(out)]) == 0
