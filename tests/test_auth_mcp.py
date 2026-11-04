"""M2 authorization over MCP: a client's self-declared name is not an identity. Agent tools act as the agent the
operator named at launch (--agent, default local-agent); the client's name is kept only as an unverified label;
person tools stay behind --operator, and an operator may lower a budget but never raise one."""
import json

import pytest
from support.loop_systems import PREDICTION, objective, register

from translucent import chain, mcp_server
from translucent.ledger import Ledger
from translucent.mcp_server import Protocol
from translucent.workspace import init_ledger


@pytest.fixture(autouse=True)
def adapter(monkeypatch):
    for name in ("TRANSLUCENT_LIVE", "TRANSLUCENT_ADMIN_TOKEN", "TRANSLUCENT_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    return register(monkeypatch)


@pytest.fixture
def ledger(tmp_path):
    return init_ledger(tmp_path / "data", "lab", title="Battery lab")


def session(protocol, client):
    reply = protocol.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": client}})
    assert "result" in reply, reply
    protocol.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return protocol


def call(protocol, tool, **arguments):
    reply = protocol.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": tool, "arguments": arguments}})
    result = reply["result"]
    text = result["content"][0]["text"]
    if result["isError"]:
        raise ValueError(text)
    return json.loads(text)


def toy():
    return {k: v for k, v in objective().items() if k not in ("planner", "registered_by")}


def propose(protocol, cid, current=2.0):
    return call(protocol, "propose_experiment", campaign_id=cid, action="simulate_charge",
                params={"current_A": current, "ambient_C": 25.0}, rationale="r", prediction=PREDICTION,
                expected_effect="e")


def test_a_client_naming_itself_a_person_acts_as_the_launch_agent(ledger):
    proto = session(Protocol(ledger, write=True), {"name": "Ana", "version": "1"})      # claims to be Ana
    assert proto.agent == "local-agent"
    assert not {"approve_request", "amend_objective"} & {t["name"] for t in proto.tools}  # no --operator: no person
    opened = call(proto, "open_loop_campaign", title="t", objective=toy())
    cid = opened["campaign_id"]
    req = propose(proto, cid)
    assert req["proposed_by"] == "agent:local-agent"
    L = Ledger(ledger)
    try:
        recs = chain.records(L, cid)
        objective_data = recs[0].data
        assert recs[0].actor == "agent:local-agent" and objective_data["registered_by"] == "local-agent"
        assert objective_data["planner"]["client"]["name"] == "Ana"             # a label, never an actor
        assert not any("Ana" in r.actor for r in recs)
    finally:
        L.db.close()


def test_the_operator_names_the_agent_at_launch(ledger):
    proto = session(Protocol(ledger, write=True, agent="lab-bot", operator="Ana"), {"name": "Root", "version": "1"})
    cid = call(proto, "open_loop_campaign", title="t", objective=toy())["campaign_id"]
    req = propose(proto, cid)
    assert req["proposed_by"] == "agent:lab-bot"
    approved = call(proto, "approve_request", campaign_id=cid, request_id=req["request_id"],
                    content_hash=req["content_hash"])
    assert approved["approved_by"] == "person:Ana"
    # an operator acts as an engineer: lowering a budget is theirs, raising one is an administrator's
    with pytest.raises(ValueError, match="needs an administrator key"):
        call(proto, "amend_objective", campaign_id=cid, reason="more", budget={"experiments": 20})
    lowered = call(proto, "amend_objective", campaign_id=cid, reason="fewer", budget={"experiments": 3})
    assert lowered["amendment"]["data"]["budget"] == {"experiments": 3}
    for bad in ("", " ", "x" * 81, "a\nb"):
        with pytest.raises(ValueError, match="--agent"):
            Protocol(ledger, write=True, agent=bad)


def test_the_agent_flag_reaches_the_server(ledger, monkeypatch):
    seen = {}

    class Recorder(Protocol):
        def __init__(self, *a, **kw):
            seen.update(kw)
            super().__init__(*a, **kw)

        def close(self):
            super().close()

    monkeypatch.setattr(mcp_server, "Protocol", Recorder)
    monkeypatch.setattr("sys.stdin", iter([]))
    assert mcp_server.main(["--ledger", str(ledger), "--write", "--agent", "lab-bot"]) == 0
    assert seen["agent"] == "lab-bot"
    assert mcp_server.main(["--ledger", str(ledger)]) == 0
    assert seen["agent"] == "local-agent"
