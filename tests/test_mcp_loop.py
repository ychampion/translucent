"""S5: loop campaigns over MCP. An external agent's stdio client completes the built-in planner's workflow on the toy
cell (import, open, measure, propose, approval, wait, view, conclude, verify) with a person approving over HTTP from
another process, or the local operator approving over MCP; one API server and two MCP processes share a ledger;
every capability a person or agent may call is on both surfaces and the system's on neither; ownership, --adopt,
--import-root and the budget caps hold. The subprocesses register the toy adapter themselves (BOOT)."""
import json
import os
import queue
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from support.loop_systems import PREDICTION, objective, register

from translucent import api_v1, chain
from translucent.ledger import Ledger
from translucent.loop import request_states
from translucent.loop_service import CAPABILITIES
from translucent.mcp_server import LOOP_BY_TOOL, LOOP_TOOLS, WAIT, Protocol
from translucent.verify import verify
from translucent.workspace import init_ledger

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
DATA = TESTS / "data" / "import_v2"
TEAM = {"Authorization": "Bearer team-secret"}
CLIENT = {"name": "external-agent", "version": "0.1"}
BOOT = f"""import sys
sys.path.insert(0, {str(TESTS)!r})
from support.loop_systems import register
register()
from translucent.mcp_server import main
raise SystemExit(main(sys.argv[1:]))
"""
HTTP_BOOT = f"""import sys
sys.path.insert(0, {str(TESTS)!r})
from support.loop_systems import register
register()
import uvicorn
from translucent.server import create_app
uvicorn.run(create_app(data_root=sys.argv[1]), host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
"""


@pytest.fixture(autouse=True)
def adapter(monkeypatch):
    for name in ("TRANSLUCENT_LIVE", "TRANSLUCENT_ADMIN_TOKEN", "TRANSLUCENT_DATA_ROOT", "TRANSLUCENT_IMPORT_ROOT",
                 "TRANSLUCENT_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRANSLUCENT_TEAM_TOKEN", "team-secret")
    return register(monkeypatch)


@pytest.fixture
def lab(tmp_path):
    """A data root with ledger "lab", and an import folder holding the import v2 fixture under bench/."""
    data = tmp_path / "data"
    ledger = init_ledger(data, "lab", title="Battery lab")
    imports = tmp_path / "imports"
    shutil.copytree(DATA, imports / "bench", ignore=shutil.ignore_patterns("v1"))
    return data, ledger, imports


class McpError(RuntimeError):
    pass


class Mcp:
    """An MCP server in its own process, spoken to over stdio JSON-RPC as an external agent's client would."""

    def __init__(self, ledger, *flags, log: Path, client=CLIENT):
        self.log = log.open("w", encoding="utf-8")
        self.process = subprocess.Popen([sys.executable, "-c", BOOT, "--ledger", str(ledger), *flags], cwd=ROOT,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True,
                                        encoding="utf-8", env=os.environ.copy())
        self.lines, self.next_id = queue.Queue(), 0
        threading.Thread(target=self._read, daemon=True).start()
        reply = self.rpc("initialize", protocolVersion="2025-06-18", capabilities={}, clientInfo=client)
        assert "result" in reply, reply
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _read(self):
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def rpc(self, method, **params):
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
        line = self.lines.get(timeout=180)
        assert line is not None, f"the MCP server exited: {self.log.name}"
        reply = json.loads(line)
        assert reply["id"] == self.next_id
        return reply

    def tools(self) -> set[str]:
        return {t["name"] for t in self.rpc("tools/list")["result"]["tools"]}

    def call(self, tool, /, **arguments):
        reply = self.rpc("tools/call", name=tool, arguments=arguments)
        if "error" in reply:
            raise McpError(reply["error"]["message"])
        text = reply["result"]["content"][0]["text"]
        if reply["result"]["isError"]:
            raise McpError(text)
        return json.loads(text)

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(10)
        self.log.close()


class Api:
    """`translucent serve --data-root` in its own process (with its loop runner thread)."""

    def __init__(self, data, log: Path):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.log = log.open("w", encoding="utf-8")
        self.process = subprocess.Popen([sys.executable, "-c", HTTP_BOOT, str(data), str(port)], cwd=ROOT,
                                        stdout=self.log, stderr=subprocess.STDOUT, env=os.environ.copy())
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{port}/api/v1/ledgers/lab", headers=TEAM, timeout=60)
        deadline = time.monotonic() + 90
        while True:
            try:
                if self.http.get("/systems").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            assert self.process.poll() is None and time.monotonic() < deadline, f"API did not start: {log}"
            time.sleep(0.2)

    def close(self):
        self.http.close()
        self.process.terminate()
        self.process.wait(30)
        self.log.close()


@pytest.fixture
def started(tmp_path):
    """Start MCP and API processes; every one is stopped at the end of the test, whatever happened."""
    processes = []

    def start(kind, *args, **kw):
        n = len(processes)
        process = (Mcp if kind == "mcp" else Api)(*args, log=tmp_path / f"{kind}-{n}.log", **kw)
        processes.append(process)
        return process
    yield start
    for process in reversed(processes):
        process.close()


def manifest():
    return json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))


def journey_objective(dataset_id):
    return {k: v for k, v in objective(datasets=[dataset_id], baseline=[f"{dataset_id}/cell07-cc"]).items()
            if k not in ("planner", "registered_by")}


def wait_until_settled(mcp, cid, rq):
    for _ in range(10):
        state = mcp.call("wait_for_request", campaign_id=cid, request_id=rq, timeout_s=30)
        if state["settled"]:
            return state
    raise AssertionError(f"{rq} never settled: {state}")


# ------------------------------------------------------------------ 1. the external agent's journey

@pytest.mark.parametrize("approver", ["http", "operator"])
def test_external_agent_journey(lab, started, approver):
    data, ledger, imports = lab
    flags = ["--write", "--import-root", str(imports)] + (["--operator", "Ana"] if approver == "operator" else [])
    api = started("api", data) if approver == "http" else None
    agent = started("mcp", ledger, *flags)
    person_tools = {"approve_request", "reject_request", "revise_request", "submit_result", "amend_objective",
                    "add_note"}
    assert bool(person_tools & agent.tools()) is (approver == "operator")

    assert "loop-test-cell" in {s["id"] for s in agent.call("list_systems")}
    dataset = agent.call("import_dataset", manifest=manifest(), files="bench")
    assert dataset["id"] == "fastcharge-screen-2026q3" and agent.call("list_datasets")[0]["id"] == dataset["id"]

    opened = agent.call("open_loop_campaign", title="Faster charge", objective=journey_objective(dataset["id"]))
    cid = opened["campaign_id"]
    assert opened["view"]["campaign"]["planner"]["kind"] == "external"
    peak = agent.call("define_measurement", campaign_id=cid, name="peak_temperature",
                      spec={"op": "max", "channel": "temperature", "unit": "degC"})
    assert peak["record"]["actor"] == "agent:external-agent" and len(peak["measurements"]) == 1   # the baseline

    req = agent.call("propose_experiment", campaign_id=cid, action="simulate_charge",
                     params={"current_A": 2.0, "ambient_C": 25.0}, rationale="Twice the baseline current.",
                     prediction=PREDICTION, expected_effect="Shorter charge, still under 45 degC.")
    assert req["state"] == "awaiting_approval" and req["proposed_by"] == "agent:external-agent"
    waiting = agent.call("wait_for_request", campaign_id=cid, request_id=req["request_id"], timeout_s=0.5)
    assert waiting["state"] == "awaiting_approval" and not waiting["settled"]     # nothing runs before a person
    if approver == "http":
        with pytest.raises(McpError, match="Unknown tool"):
            agent.call("approve_request", campaign_id=cid, request_id=req["request_id"],
                       content_hash=req["content_hash"])
        r = api.http.post(f"/campaigns/{cid}/requests/{req['request_id']}/approve",
                          json={"author": "Ana", "content_hash": req["content_hash"], "note": "Go"})
        assert r.status_code == 200, r.text
    else:
        agent.call("approve_request", campaign_id=cid, request_id=req["request_id"],
                   content_hash=req["content_hash"], note="Go")

    done = wait_until_settled(agent, cid, req["request_id"])
    assert done["state"] == "completed" and done["approved_by"] == "person:Ana" and done["attempts"] == 1
    view = agent.call("get_campaign_view", campaign_id=cid)
    assert view["requests"][-1]["state"] == "completed"
    assert view["source"]["kind"] == "live" and view["source"]["ledger"] == "lab"
    compared = view["comparisons"]               # recorded with the result by the comparator
    assert [c["request_id"] for c in compared] == [req["request_id"]], compared
    concluded = agent.call("conclude_campaign", campaign_id=cid, text="One simulated step; not yet better.")
    assert concluded["type"] == "conclusion" and concluded["data"]["outcome"]
    with pytest.raises(McpError, match="concluded"):
        agent.call("propose_experiment", campaign_id=cid, action="simulate_charge",
                   params={"current_A": 3.0, "ambient_C": 25.0}, rationale="More.", prediction=PREDICTION,
                   expected_effect="Faster.")
    report = agent.call("verify", campaign_or_ledger=cid)      # ok: false would be a tool error
    assert report["ok"] and report["requests"] >= 1, report

    L = Ledger(ledger)
    try:
        assert chain.problems(L, cid) == []
        records = chain.records(L, cid)
        approval = next(r for r in records if r.type == "approval")
        assert approval.actor == "person:Ana" and approval.data["author"] == "Ana"     # never the client's name
        assert records[0].data["registered_by"] == ("Ana" if approver == "operator" else "external-agent")
        workers = {r.data["worker"]["process"] for r in records if r.type == "execution"}
        assert workers <= {"api", "mcp"} and len([r for r in records if r.type == "execution"]) == 1
    finally:
        L.db.close()


# ------------------------------------------------------------------ 2. one API server and two MCP processes

def test_api_and_two_mcp_processes_share_one_ledger(lab, started):
    data, ledger, _ = lab
    api = started("api", data)
    first = started("mcp", ledger, "--write", client={"name": "agent-one", "version": "1"})
    second = started("mcp", ledger, "--write", "--adopt", client={"name": "agent-two", "version": "1"})
    cid = first.call("open_loop_campaign", title="Shared campaign",
                     objective={**journey_objective("x"), "datasets": [], "baseline": [],
                                "budget": {"model_usd": 0, "experiments": 1000, "experiment_usd": 0}})["campaign_id"]
    errors, stop = [], threading.Event()

    def agent(mcp, seed):
        rnd = random.Random(seed)
        for i in range(30):
            current = round(0.6 + rnd.random() * 9, 6)
            try:
                req = mcp.call("propose_experiment", campaign_id=cid, action="simulate_charge",
                               params={"current_A": current, "ambient_C": 25.0}, rationale=f"Try {current} A.",
                               prediction=PREDICTION, expected_effect="Learn the slope.")
                if i % 7 == 3:
                    mcp.call("cancel_request", campaign_id=cid, request_id=req["request_id"], reason="Changed plan")
                elif i % 3 == 0:
                    mcp.call("wait_for_request", campaign_id=cid, request_id=req["request_id"], timeout_s=1)
                if i % 10 == 5:
                    mcp.call("define_measurement", campaign_id=cid, name=f"peak_{seed}_{i}",
                             spec={"op": "max", "channel": "temperature", "unit": "degC"})
            except McpError as e:      # a person or the other agent moved first: refused, never corrupted
                if "is approved" not in str(e) and "not awaiting approval" not in str(e) and \
                        "only before approval" not in str(e):
                    errors.append(f"{mcp.process.pid}: {e}")

    def person():
        n = 0
        while not stop.is_set():
            view = api.http.get(f"/campaigns/{cid}/view").json()
            for r in view["requests"]:
                if r["state"] != "awaiting_approval":
                    continue
                n += 1
                action, body = ("reject", {"reason": "Not this one"}) if n % 5 == 0 else ("approve", {})
                resp = api.http.post(f"/campaigns/{cid}/requests/{r['request_id']}/{action}",
                                     json={"author": "Ana", "content_hash": r["content_hash"], **body})
                if resp.status_code not in (200, 409, 422):
                    errors.append(f"{action}: {resp.status_code} {resp.text}")
            time.sleep(0.05)

    people = threading.Thread(target=person)
    agents = [threading.Thread(target=agent, args=(m, s)) for m, s in ((first, 1), (second, 2))]
    people.start()
    for t in agents:
        t.start()
    for t in agents:
        t.join(400)
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:          # the API's runner (and the agents' waits) drain the queue
        states = [r["state"] for r in api.http.get(f"/campaigns/{cid}/view").json()["requests"]]
        if not {"awaiting_approval", "queued", "running"} & set(states):
            break
        time.sleep(0.5)
    stop.set()
    people.join(30)
    assert errors == []

    L = Ledger(ledger)
    try:
        records = chain.records(L, cid)
        types = [r.type for r in records]
        assert len(records) - 1 >= 200, types                     # transitions after the objective
        assert {"request", "approval", "rejection", "cancellation", "execution", "result", "measurement"} <= set(types)
        assert chain.problems(L, cid) == []
        states = request_states(L, cid, records)
        assert not {s.state for s in states.values()} & {"awaiting_approval", "queued", "running"}
        for st in states.values():                                   # one execution and one result per approval
            starts = [r for r in records if r.type == "execution" and r.data["request_id"] == st.request_id]
            results = [r for r in records if r.type == "result" and r.data["request_id"] == st.request_id]
            assert len(starts) == (1 if st.state == "completed" else 0) and len(results) == len(starts)
        workers = {r.data["worker"]["pid"] for r in records if r.type == "execution"}
        assert len(workers) >= 1
        actors = {r.actor for r in records}
        assert {"agent:agent-one", "agent:agent-two", "person:Ana", "system:runner"} <= actors
    finally:
        L.db.close()
    report = verify(ledger, campaign_id=cid)
    assert report.ok and report.requests >= 60, report.problems


# ------------------------------------------------------------------ 3. capability parity

def test_every_capability_is_on_both_surfaces_and_the_systems_on_neither(lab):
    _, ledger, imports = lab
    routes = {r.name.removeprefix("loop."): r for r in api_v1.router.routes if r.name.startswith("loop.")}
    full = Protocol(ledger, write=True, operator="Ana", import_root=imports)
    tools = {t["name"] for t in full.tools}
    for name, cap in CAPABILITIES.items():
        if cap.actors <= {"system"}:
            assert name not in routes and name not in LOOP_TOOLS, name
            assert not any(n == name or n.endswith("_" + name) for n in tools), name
            continue
        assert name in routes, f"{name} has no HTTP route"
        assert LOOP_TOOLS[name][0] in tools, f"{name} has no MCP tool"
        methods = routes[name].methods
        assert methods == ({"POST"} if cap.write else {"GET"}), (name, methods)
        tool = next(t for t in full.tools if t["name"] == LOOP_TOOLS[name][0])
        assert tool["annotations"]["readOnlyHint"] is (not cap.write)
    assert set(LOOP_BY_TOOL.values()) == {n for n, c in CAPABILITIES.items() if c.actors - {"system"}}
    # who gets which tools: reads everyone, agent writes with --write, person tools only with --operator
    reader, writer = Protocol(ledger), Protocol(ledger, write=True)
    person = {LOOP_TOOLS[n][0] for n, c in CAPABILITIES.items() if c.actors == {"person"}}
    agent = {LOOP_TOOLS[n][0] for n, c in CAPABILITIES.items() if c.write and "agent" in c.actors} | {WAIT["name"]}
    read = {LOOP_TOOLS[n][0] for n, c in CAPABILITIES.items() if not c.write and c.actors - {"system"}}
    assert read <= {t["name"] for t in reader.tools} and not (person | agent) & {t["name"] for t in reader.tools}
    assert (read | agent) <= {t["name"] for t in writer.tools} and not person & {t["name"] for t in writer.tools}
    assert person <= tools
    with pytest.raises(ValueError, match="--operator needs --write"):
        Protocol(ledger, operator="Ana")


# ------------------------------------------------------------------ 4. ownership, adopt, finished, refusals (in process)

def initialize(protocol, client=CLIENT):
    reply = protocol.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": client}})
    assert "result" in reply, reply
    protocol.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return protocol


def invoke(protocol, tool, /, **arguments):
    reply = protocol.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": tool, "arguments": arguments}})
    if "error" in reply:
        raise McpError(reply["error"]["message"])
    text = reply["result"]["content"][0]["text"]
    if reply["result"]["isError"]:
        raise McpError(text)
    return json.loads(text)


def toy_objective(**kw):
    return {k: v for k, v in objective(**kw).items() if k not in ("planner", "registered_by")}


def proposal(protocol, cid, current=2.0, **kw):
    return invoke(protocol, "propose_experiment", campaign_id=cid, action="simulate_charge",
                  params={"current_A": current, "ambient_C": 25.0}, rationale="Faster.", prediction=PREDICTION,
                  expected_effect="Shorter charge.", **kw)


def test_ownership_adopt_and_concluded_campaigns(lab):
    _, ledger, _ = lab
    owner = initialize(Protocol(ledger, write=True))
    cid = invoke(owner, "open_loop_campaign", title="Mine", objective=toy_objective())["campaign_id"]
    other = initialize(Protocol(ledger, write=True), {"name": "other-agent", "version": "1"})
    with pytest.raises(McpError, match="did not open that campaign.*--adopt"):
        proposal(other, cid)
    assert invoke(other, "get_campaign_view", campaign_id=cid)["campaign"]["id"] == cid     # reading is open
    reader = initialize(Protocol(ledger))                       # a read-only session reads a snapshot
    assert invoke(reader, "get_campaign_view", campaign_id=cid)["source"]["ledger"] == "lab"
    assert invoke(reader, "list_loop_campaigns")["items"][0]["id"] == cid
    adopter = initialize(Protocol(ledger, write=True, adopt=True), {"name": "other-agent", "version": "1"})
    req = proposal(adopter, cid)
    assert req["proposed_by"] == "agent:other-agent"
    with pytest.raises(McpError, match="an agent cancels only its own requests"):
        invoke(owner, "cancel_request", campaign_id=cid, request_id=req["request_id"], reason="mine now")
    with pytest.raises(McpError, match="not a loop campaign"):
        proposal(owner, "loop_doesnotexist0000")
    invoke(owner, "conclude_campaign", campaign_id=cid, text="Stopping here.")
    with pytest.raises(McpError, match="Campaign concluded at seq"):
        proposal(owner, cid, current=3.0)
    with pytest.raises(McpError, match="Campaign concluded at seq"):
        invoke(owner, "define_measurement", campaign_id=cid, name="peak",
               spec={"op": "max", "channel": "temperature", "unit": "degC"})


def test_replaying_another_sessions_open_key_does_not_take_its_campaign(lab):
    """The campaign id comes from the key alone and the objective is readable: a second session (same client name,
    no --adopt) replaying the key gets no ownership, so it cannot conclude the first session's campaign."""
    _, ledger, _ = lab
    first = initialize(Protocol(ledger, write=True))
    cid = invoke(first, "open_loop_campaign", title="A's", objective=toy_objective(),
                 idempotency_key="open-1")["campaign_id"]
    assert invoke(first, "open_loop_campaign", title="A's", objective=toy_objective(),
                  idempotency_key="open-1")["campaign_id"] == cid          # its own retry still answers
    second = initialize(Protocol(ledger, write=True))
    with pytest.raises(McpError, match="Another session opened this campaign.*--adopt"):
        invoke(second, "open_loop_campaign", title="A's", objective=toy_objective(), idempotency_key="open-1")
    assert cid not in second.owned
    with pytest.raises(McpError, match="did not open that campaign"):
        invoke(second, "conclude_campaign", campaign_id=cid, text="hijack")
    adopter = initialize(Protocol(ledger, write=True, adopt=True))
    assert invoke(adopter, "open_loop_campaign", title="A's", objective=toy_objective(),
                  idempotency_key="open-1")["campaign_id"] == cid          # --adopt is the way to write to it
    invoke(first, "conclude_campaign", campaign_id=cid, text="Mine to conclude.")


def test_operator_name_not_client_name_and_keys_replay(lab):
    _, ledger, _ = lab
    session = initialize(Protocol(ledger, write=True, operator="Ana"), {"name": "Ben", "version": "1"})
    cid = invoke(session, "open_loop_campaign", title="Keyed", objective=toy_objective(),
                 idempotency_key="open-1")["campaign_id"]
    assert invoke(session, "open_loop_campaign", title="Keyed", objective=toy_objective(),
                  idempotency_key="open-1")["campaign_id"] == cid
    req = proposal(session, cid, idempotency_key="p-1")
    assert proposal(session, cid, idempotency_key="p-1")["request_id"] == req["request_id"]
    revised = invoke(session, "revise_request", campaign_id=cid, request_id=req["request_id"],
                     params={"current_A": 1.5}, reason="Rated 1.5 A", idempotency_key="rv-1")
    again = invoke(session, "revise_request", campaign_id=cid, request_id=req["request_id"],
                   params={"current_A": 1.5}, reason="Rated 1.5 A", idempotency_key="rv-1")
    assert again["version"] == revised["version"] == 2
    with pytest.raises(McpError, match="different revision"):
        invoke(session, "revise_request", campaign_id=cid, request_id=req["request_id"],
               params={"current_A": 1.2}, reason="Rated 1.2 A", idempotency_key="rv-1")
    ok = invoke(session, "approve_request", campaign_id=cid, request_id=req["request_id"],
                content_hash=revised["content_hash"], idempotency_key="a-1")
    assert ok["approved_by"] == "person:Ana" and ok["proposed_by"] == "agent:Ben"
    assert invoke(session, "approve_request", campaign_id=cid, request_id=req["request_id"],
                  content_hash=revised["content_hash"], idempotency_key="a-1")["state"] == ok["state"]
    amended = invoke(session, "amend_objective", campaign_id=cid, reason="Cooler cell",
                     constraints=[{"quantity": "max_temperature", "op": "<=", "limit": 40.0, "unit": "degC",
                                   "label": "At or below 40 degC"}], idempotency_key="am-1")
    replayed = invoke(session, "amend_objective", campaign_id=cid, reason="Cooler cell",
                      constraints=[{"quantity": "max_temperature", "op": "<=", "limit": 40.0, "unit": "degC",
                                    "label": "At or below 40 degC"}], idempotency_key="am-1")
    assert replayed["amendment"]["id"] == amended["amendment"]["id"]
    assert amended["amendment"]["actor"] == "person:Ana" and amended["amendment"]["data"]["author"] == "Ana"
    note = invoke(session, "add_note", campaign_id=cid, text="Bench is free on Monday")
    assert note["actor"] == "person:Ana"
    L = Ledger(ledger)
    try:
        types = [r.type for r in chain.records(L, cid)]
        assert types == ["objective", "request", "request", "approval", "amendment"]
        assert chain.records(L, cid)[0].data["registered_by"] == "Ana"
    finally:
        L.db.close()


def test_import_is_confined_to_the_import_root(lab, tmp_path):
    _, ledger, imports = lab
    outside = tmp_path / "outside"
    shutil.copytree(DATA, outside, ignore=shutil.ignore_patterns("v1"))
    session = initialize(Protocol(ledger, write=True, import_root=imports))
    for files in (str(outside), "../outside", {"eclab-cell07.mpt": str(outside / "eclab-cell07.mpt")}):
        with pytest.raises(McpError, match="not a file in the import folder"):
            invoke(session, "import_dataset", manifest=manifest(), files=files)
    with pytest.raises(McpError, match="manifest must be object"):
        invoke(session, "import_dataset", manifest=str(imports / "bench" / "manifest.json"), files="bench")
    no_root = initialize(Protocol(ledger, write=True))
    with pytest.raises(McpError, match="no import folder"):
        invoke(no_root, "import_dataset", manifest=manifest(), files="bench")
    assert invoke(session, "import_dataset", manifest=manifest(), files="bench")["id"] == "fastcharge-screen-2026q3"
    # a person's result files are confined the same way
    operator = initialize(Protocol(ledger, write=True, operator="Ana", import_root=imports))
    cid = invoke(operator, "open_loop_campaign", title="Bench", objective=toy_objective())["campaign_id"]
    with pytest.raises(McpError, match="not a file in the import folder"):
        invoke(operator, "submit_result", campaign_id=cid,
               upload={"request_id": "rq_" + "0" * 20, "content_hash": "0" * 64, "executed_configuration": {}},
               files={"run.csv": str(outside / "cell08-mv.csv")})


@pytest.mark.parametrize("profile, cap", [("dev", 25.0), ("meta", 170.0)])
def test_mcp_budgets_are_held_to_the_provider_caps(lab, monkeypatch, profile, cap):
    _, ledger, _ = lab
    monkeypatch.setenv("TRANSLUCENT_PROFILE", profile)
    session = initialize(Protocol(ledger, write=True))
    for usd in (cap + 1, 1e6):
        with pytest.raises(McpError, match=re.escape(f"more than the ${cap:g} cap")):
            invoke(session, "open_campaign", objective="Short?", experiment="nist:loco", budget_usd=usd)
        with pytest.raises(McpError, match=re.escape(f"budget.model_usd: ${usd:g} is more than the ${cap:g} cap")):
            invoke(session, "open_loop_campaign", title="Too rich",
                   objective=toy_objective(budget={"model_usd": usd, "experiments": 4}))
    with pytest.raises(McpError, match="finite number within the advertised bounds"):
        invoke(session, "open_campaign", objective="Short?", experiment="nist:loco", budget_usd=-1)
    for bad in (-1, float("inf")):
        with pytest.raises(McpError, match="budget.model_usd must be a finite number"):
            invoke(session, "open_loop_campaign", title="Bad",
                   objective=toy_objective(budget={"model_usd": bad, "experiments": 4}))
    with pytest.raises(McpError, match="budget.experiment_usd must be a finite number"):
        invoke(session, "open_loop_campaign", title="Bad",
               objective=toy_objective(budget={"model_usd": 0, "experiments": 4, "experiment_usd": -5}))
    opened = invoke(session, "open_loop_campaign", title="At the cap",
                    objective=toy_objective(budget={"model_usd": cap, "experiments": 4}))
    assert opened["campaign_id"].startswith("loop_")
