"""Offline MCP writes use the recorded tool runner and the same deterministic gate."""
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys

import pytest

from translucent import cli, mcp_server, tools
from translucent.ledger import Ledger
from translucent.mcp_server import Protocol
from translucent.runtime import new_campaign
from translucent.server import _StateLock, explain_check
from translucent.verify import read_ledger, verify

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = "reisecker2023/movie4"
CLIENT = {"name": "pytest-agent", "version": "1.2.3"}
READ = {"list_campaigns", "list_findings", "get_evidence", "verify", "list_branches"}
WRITE = {"list_experiments", "open_campaign", "list_tools", "run_tool", "propose_finding",
         "record_lesson", "finish_campaign"}


def request(method, **params):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def initialize(protocol, client=CLIENT):
    reply = protocol.handle(request("initialize", protocolVersion="2025-06-18", capabilities={}, clientInfo=client))
    assert "result" in reply, reply
    protocol.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return reply


def invoke(protocol, name, **arguments):
    reply = protocol.handle(request("tools/call", name=name, arguments=arguments))
    assert "result" in reply, reply
    result = reply["result"]
    assert not result["isError"], result
    return json.loads(result["content"][0]["text"])


def refused(protocol, name, reason, **arguments):
    reply = protocol.handle(request("tools/call", name=name, arguments=arguments))
    assert reply["result"]["isError"], reply
    text = reply["result"]["content"][0]["text"]
    assert reason in text, text
    return text


def opened(protocol):
    return invoke(protocol, "open_campaign", objective="When does the battery short?", experiment=EXPERIMENT,
                  budget_usd=0)["id"]


def run(protocol, cid, name, **arguments):
    return invoke(protocol, "run_tool", campaign_id=cid, tool=name, arguments=arguments)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TRANSLUCENT_SAM", "strict")
    def deny(*args, **kwargs):
        raise AssertionError("MCP tests must not use the network")
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)


@pytest.fixture(scope="module")
def imported(tmp_path_factory):
    root = tmp_path_factory.mktemp("mcp-import")
    ledger = Ledger(root)
    try:
        video = tools.import_video(ledger, ROOT / "data/raw/reisecker2023/movie4.mp4", label="Movie 4",
                                   citation="Reisecker et al. 2023, Supplementary Movie 4", licence="CC BY 4.0",
                                   experiment=EXPERIMENT)
        return root, video.id
    finally:
        ledger.db.close()


@pytest.fixture
def ledger(tmp_path, imported):
    root = tmp_path / "ledger"
    shutil.copytree(imported[0], root)
    return root, imported[1]


@pytest.fixture
def protocol(ledger, tmp_path):
    instance = Protocol(ledger[0], write=True, state_dir=tmp_path / "api")
    initialize(instance)
    try:
        yield instance
    finally:
        instance.close()


def test_discovery_annotations_and_client_identity(ledger, protocol):
    reader = Protocol(ledger[0])
    initialize(reader)
    catalog = reader.handle(request("tools/list"))["result"]["tools"]
    assert {t["name"] for t in catalog} == READ
    assert all(t["annotations"]["readOnlyHint"] for t in catalog)
    for name in WRITE:
        assert reader.handle(request("tools/call", name=name))["error"]["code"] == -32602
    catalog = protocol.handle(request("tools/list"))["result"]["tools"]
    assert {t["name"] for t in catalog} == READ | WRITE
    for tool in catalog:
        read_only = tool["name"] in READ | {"list_tools", "list_experiments"}
        assert tool["annotations"]["readOnlyHint"] == read_only
        assert tool["annotations"]["idempotentHint"] == read_only
        assert tool["inputSchema"]["additionalProperties"] is False
    assert invoke(protocol, "list_experiments") == [{"experiment": EXPERIMENT, "video_id": ledger[1],
             "label": "Movie 4", "citation": "Reisecker et al. 2023, Supplementary Movie 4"}]
    cid = opened(protocol)
    catalog = invoke(protocol, "list_tools", campaign_id=cid)
    assert {t["name"] for t in catalog} == set(tools.REGISTRY) - mcp_server.EXCLUDED
    assert not {"propose_finding", "record_lesson", "run_analysis_code"} & {t["name"] for t in catalog}
    for tool in catalog:
        original = tools.REGISTRY[tool["name"]]["schema"]["function"]["parameters"]
        assert tool["inputSchema"] == {**original, "additionalProperties": False}
    with read_ledger(ledger[0]) as store:
        event = store.events(cid)[0]
        assert event.type == "campaign.started" and event.actor == "agent:pytest-agent"
        assert event.data == {"via": "mcp", "clientInfo": CLIENT}
        assert store.get("campaign", cid).budget_usd == 0


def test_timing_finding_refused_corrected_finished_and_verified(ledger, protocol, capsys):
    cid, video = opened(protocol), ledger[1]
    scale = run(protocol, cid, "lookup_calibration", video_id=video, quantity="spatial_scale")
    voltage = run(protocol, cid, "read_cell_voltage", video_id=video)
    mid = voltage["data"]["events"][0]
    bad = invoke(protocol, "propose_finding", campaign_id=cid, type="value_within", measurement_ids=[mid],
                 params={"lo": 4, "hi": 6, "unit": "s"}, statement="The battery shorts after 4-6 seconds.")
    assert bad["verdict"] == "refuted" and bad["failed_checks"]
    for check in bad["failed_checks"]:
        assert check["explanation"] == explain_check(check)
    clock = run(protocol, cid, "lookup_calibration", video_id=video, quantity="time_base")
    good = invoke(protocol, "propose_finding", campaign_id=cid, type="value_within", measurement_ids=[mid],
                  params={"lo": 80, "hi": 100, "unit": "s"}, statement="The battery shorts after 80-100 seconds.",
                  supersedes=bad["claim_id"])
    assert good["verdict"] == "supported" and good["failed_checks"] == []
    lesson = invoke(protocol, "record_lesson", campaign_id=cid,
                    text="Time the short from the voltage log and recorded clock, not playback frames.",
                    scope=EXPERIMENT, evidence_ids=[good["claim_id"], mid])
    assert lesson["status"] == "ok"
    result = invoke(protocol, "finish_campaign", campaign_id=cid, answer="The first short is at 80-100 s.",
                    not_proven="Filament growth was not measured.")
    assert result["status"] == "finished"
    for name, arguments in [
        ("run_tool", {"tool": "read_cell_voltage", "arguments": {"video_id": video}}),
        ("propose_finding", {"type": "value_within", "measurement_ids": [mid],
                              "params": {"lo": 80, "hi": 100, "unit": "s"}, "statement": "Again"}),
        ("record_lesson", {"text": "Again", "scope": EXPERIMENT, "evidence_ids": [mid]}),
        ("finish_campaign", {"answer": "Again", "not_proven": ""}),
    ]:
        refused(protocol, name, "finished", campaign_id=cid, **arguments)
    assert invoke(protocol, "list_tools", campaign_id=cid)
    with read_ledger(ledger[0]) as store:
        assert store.get("claim", good["claim_id"]).supersedes == bad["claim_id"]
        runs = store.list("run", campaign_id=cid)
        assert [r.step_id for r in runs] == [f"mcp{n}" for n in range(1, len(runs) + 1)]
        assert all(r.status == "ok" for r in runs)
        for measurement in store.list("measurement"):
            producer = store.get("run", measurement.run_id)
            assert producer.campaign_id == cid and measurement.id in producer.measurement_ids
        assert all(r.reviewer == "gate" for r in store.list("review"))
        end = store.events(cid)[-1]
        assert end.type == "campaign.finished" and end.actor == "agent:pytest-agent"
        assert end.data == {k: result[k] for k in ("status", "answer", "not_proven")}
    assert invoke(protocol, "verify", campaign_or_ledger=cid)["ok"]
    assert cli.main(["verify", str(ledger[0])]) == 0
    cli_tail = capsys.readouterr().out.strip()
    print("\n".join([
        "Agent: Open Movie 4 to time the first short, with a zero-dollar budget.",
        "Translucent: Campaign opened; client pytest-agent 1.2.3 recorded.",
        "Agent: Read the image scale and cell voltage.",
        "Translucent: " + voltage["summary"],
        "Agent: Propose that the first short falls in 4-6 seconds, using the measured event.",
        "Translucent: " + bad["verdict"] + ". " + bad["failed_checks"][0]["explanation"],
        "Agent: Read the recorded clock and replace that finding with an 80-100 second window.",
        "Translucent: " + clock["summary"] + " Verdict: " + good["verdict"] + ".",
        "Agent: Save the lesson and finish; growth was not measured.",
        "Translucent: Further writes refused. " + cli_tail,
    ]))
    assert scale["outputs"] and clock["outputs"]


def test_forged_and_foreign_evidence_is_refused(ledger, protocol):
    cid, other = opened(protocol), opened(protocol)
    voltage = run(protocol, other, "read_cell_voltage", video_id=ledger[1])
    mid = voltage["data"]["events"][0]
    claim = invoke(protocol, "propose_finding", campaign_id=other, type="value_within", measurement_ids=[mid],
                   params={"lo": 80, "hi": 100, "unit": "s"}, statement="First short near 90 s.")
    for evidence, reason in [("m_forged", "not a recorded measurement"), (mid, "another campaign")]:
        refused(protocol, "propose_finding", reason, campaign_id=cid, type="value_within",
                measurement_ids=[evidence], params={"lo": 80, "hi": 100, "unit": "s"}, statement="Forged")
    local = run(protocol, cid, "read_cell_voltage", video_id=ledger[1])["data"]["events"][0]
    refused(protocol, "propose_finding", "another campaign", campaign_id=cid, type="value_within",
            measurement_ids=[local], params={"lo": 80, "hi": 100, "unit": "s"}, statement="Borrowed",
            supersedes=claim["claim_id"])
    refused(protocol, "record_lesson", "another campaign", campaign_id=cid,
            text="Borrowed", scope=EXPERIMENT, evidence_ids=[mid])
    refused(protocol, "run_tool", "another campaign", campaign_id=cid, tool="measure_growth",
            arguments={"video_id": ledger[1], "boxes_id": ledger[1], "scale_id": mid, "grows_from": "left"})
    refused(protocol, "run_tool", "not a recorded artifact", campaign_id=cid, tool="read_cell_voltage",
            arguments={"video_id": "../../outside"})
    with read_ledger(ledger[0]) as store:
        assert store.list("claim", campaign_id=cid) == []
    assert verify(ledger[0]).ok


def test_session_ownership_and_explicit_adoption(ledger, protocol, tmp_path):
    owned = opened(protocol)
    store = Ledger(ledger[0])
    try:
        external = new_campaign(store, "Runtime campaign", "Elsewhere", [ledger[1]], budget_usd=0).id
    finally:
        store.db.close()
    refused(protocol, "run_tool", "did not open", campaign_id=external, tool="read_cell_voltage",
            arguments={"video_id": ledger[1]})
    protocol.close()
    later = Protocol(ledger[0], write=True, state_dir=tmp_path / "api")
    initialize(later)
    try:
        refused(later, "finish_campaign", "did not open", campaign_id=owned, answer="", not_proven="")
    finally:
        later.close()
    adopted = Protocol(ledger[0], write=True, adopt=True, state_dir=tmp_path / "api")
    initialize(adopted)
    try:
        assert run(adopted, external, "read_cell_voltage", video_id=ledger[1])["status"] == "ok"
        invoke(adopted, "finish_campaign", campaign_id=owned, answer="Done", not_proven="")
        refused(adopted, "finish_campaign", "finished", campaign_id=owned, answer="Again", not_proven="")
    finally:
        adopted.close()


@pytest.mark.parametrize("name,arguments,reason", [
    ("open_campaign", {"objective": "x", "experiment": EXPERIMENT, "budget_usd": 0, "verdict": "supported"}, "extra fields"),
    ("open_campaign", {"objective": "x", "experiment": EXPERIMENT, "budget_usd": True}, "must be number"),
    ("open_campaign", {"objective": "x", "experiment": EXPERIMENT, "budget_usd": float("nan")}, "finite"),
    ("open_campaign", {"objective": "x", "experiment": EXPERIMENT, "budget_usd": -1}, "bounds"),
    ("run_tool", {"tool": "read_cell_voltage", "arguments": {"video_id": "v", "value": 42}}, "extra fields"),
    ("run_tool", {"tool": "track_objects", "arguments": {"video_id": "v", "frame": 0, "points": [[1, True]]}}, "number"),
    ("run_tool", {"tool": "track_objects", "arguments": {"video_id": "v", "frame": 0, "points": [[1]]}}, "items"),
    ("run_tool", {"tool": "inspect_video", "arguments": {"video_id": "v", "frame": True}}, "integer"),
    ("run_tool", {"tool": "lookup_calibration", "arguments": {"video_id": "v", "quantity": "forged"}}, "one of"),
    ("propose_finding", {"type": "exists", "measurement_ids": [], "params": {}, "statement": "x", "review": {}}, "extra fields"),
    ("record_lesson", {"text": "x", "scope": "x", "evidence_ids": [4]}, "string"),
    ("finish_campaign", {"answer": "x", "not_proven": "", "status": "supported"}, "extra fields"),
])
def test_schema_validation(protocol, name, arguments, reason):
    cid = opened(protocol)
    if name != "open_campaign":
        arguments = {"campaign_id": cid, **arguments}
    refused(protocol, name, reason, **arguments)


@pytest.mark.parametrize("tool", ["propose_finding", "record_lesson", "run_analysis_code", "write_measurement", "review"])
def test_run_tool_has_no_gate_or_sandbox_bypass(protocol, tool):
    refused(protocol, "run_tool", "not available", campaign_id=opened(protocol), tool=tool, arguments={})


def test_write_requires_client_name_and_version(ledger, tmp_path):
    instance = Protocol(ledger[0], write=True, state_dir=tmp_path / "api")
    try:
        for client in ({}, {"name": "x"}, {"name": " ", "version": "1"}, {"name": "x", "version": 1}):
            reply = instance.handle(request("initialize", protocolVersion="2025-06-18", capabilities={}, clientInfo=client))
            assert reply["error"]["code"] == -32602
        initialize(instance)
    finally:
        instance.close()


def test_one_connection_per_write_and_closed_after_success_or_refusal(protocol, monkeypatch):
    opened_ledgers = []
    def connect(root):
        store = Ledger(root)
        opened_ledgers.append(store)
        return store
    monkeypatch.setattr(mcp_server, "Ledger", connect)
    cid = opened(protocol)
    assert len(opened_ledgers) == 1
    refused(protocol, "run_tool", "not available", campaign_id=cid, tool="write_measurement", arguments={})
    assert len(opened_ledgers) == 2
    invoke(protocol, "finish_campaign", campaign_id=cid, answer="Done", not_proven="Everything")
    assert len(opened_ledgers) == 3
    for store in opened_ledgers:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            store.db.execute("SELECT 1")


def test_images_are_paths_and_cached_measurements_keep_output_ids(ledger, protocol):
    cid = opened(protocol)
    frame = run(protocol, cid, "inspect_video", video_id=ledger[1], frame=0)
    assert frame["images"] and all(Path(p).is_file() for p in frame["images"])
    assert len(json.dumps(frame)) < 200_000 and "base64" not in json.dumps(frame)
    first = run(protocol, cid, "lookup_calibration", video_id=ledger[1], quantity="time_base")
    cached = run(protocol, cid, "lookup_calibration", video_id=ledger[1], quantity="time_base")
    assert first["run_id"] == cached["run_id"]
    assert first["outputs"] == cached["outputs"] == [first["data"]["measurement_id"]]


@pytest.mark.parametrize("mode", [None, "strict", "nearest"])
def test_sam_respects_replay_mode(ledger, protocol, monkeypatch, mode):
    if mode is None:
        monkeypatch.delenv("TRANSLUCENT_SAM")
    else:
        monkeypatch.setenv("TRANSLUCENT_SAM", mode)
    cid = opened(protocol)
    reply = protocol.handle(request("tools/call", name="run_tool", arguments={"campaign_id": cid,
        "tool": "segment_images", "arguments": {"video_id": ledger[1], "points": [[1, 1]], "max_images": 1}}))
    result = json.loads(reply["result"]["content"][0]["text"])
    assert reply["result"]["isError"]
    if mode == "nearest":
        assert result["status"] == "invalid_result" and "cannot be used as evidence" in result["summary"]
    else:
        assert result["status"] == "tool_failure" and "NoRecording" in result["summary"]
    assert os.environ.get("TRANSLUCENT_SAM") == mode
    with read_ledger(ledger[0]) as store:
        assert store.get("run", result["run_id"]).cost_usd == 0
        assert store.list("measurement") == []


def test_api_state_lock_and_other_mcp_writer_refuse_start(ledger, tmp_path):
    state = tmp_path / "api"
    state.mkdir()
    lock = _StateLock(state / "server.lock")
    try:
        with pytest.raises(ValueError, match="another server"):
            Protocol(ledger[0], write=True, state_dir=state)
        process = subprocess.run([sys.executable, "-m", "translucent.mcp_server", "--ledger", str(ledger[0]),
                                  "--write", "--state", str(state)], input="", text=True,
                                 capture_output=True, timeout=30)
        assert process.returncode != 0 and "another server" in process.stderr and not process.stdout
        reader = Protocol(ledger[0])
        initialize(reader)
        assert invoke(reader, "list_campaigns") == []
    finally:
        lock.close()
    writer = Protocol(ledger[0], write=True, state_dir=state)
    try:
        with pytest.raises(ValueError, match="another server"):
            Protocol(ledger[0], write=True, state_dir=tmp_path / "other-api")
        with pytest.raises(RuntimeError, match="another server"):
            _StateLock(state / "server.lock")
    finally:
        writer.close()
    replacement = Protocol(ledger[0], write=True, state_dir=state)
    replacement.close()
    assert writer.handle(request("ping"))["error"]["message"] == "Server is closed"


def test_stdio_write_smoke(ledger, tmp_path):
    messages = [request("initialize", protocolVersion="2025-06-18", capabilities={}, clientInfo=CLIENT),
                {"jsonrpc": "2.0", "method": "notifications/initialized"}, request("tools/list"),
                request("tools/call", name="open_campaign", arguments={"objective": "Time the short",
                        "experiment": EXPERIMENT, "budget_usd": 0}),
                request("tools/call", name="verify", arguments={"campaign_or_ledger": "ledger"})]
    process = subprocess.run([sys.executable, "-m", "translucent.mcp_server", "--ledger", str(ledger[0]),
                              "--write", "--state", str(tmp_path / "api")],
                             input="".join(json.dumps(m) + "\n" for m in messages), text=True, encoding="utf-8",
                             capture_output=True, timeout=30, env={**os.environ, "TRANSLUCENT_SAM": "strict"})
    assert process.returncode == 0, process.stderr
    replies = [json.loads(line)["result"] for line in process.stdout.splitlines()]
    assert len(replies) == 4
    assert {t["name"] for t in replies[1]["tools"]} == READ | WRITE
    cid = json.loads(replies[2]["content"][0]["text"])["id"]
    assert json.loads(replies[3]["content"][0]["text"])["ok"]
    with read_ledger(ledger[0]) as store:
        assert store.events(cid)[0].data["clientInfo"] == CLIENT
    replacement = Protocol(ledger[0], write=True, state_dir=tmp_path / "api")
    replacement.close()
