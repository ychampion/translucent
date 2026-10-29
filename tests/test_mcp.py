import json
import io
import os
import subprocess
import sys
import time

import pytest

from translucent import gate
from translucent.contracts import Campaign, Claim, Measurement, Predicate, Run, Source, Uncertainty
from translucent.ledger import Ledger
from translucent.mcp_server import Protocol, call


@pytest.fixture
def ledger(tmp_path):
    store = Ledger(tmp_path / "ledger")
    art = store.put_bytes(b"recording", kind="video", media_type="video/mp4", source=Source(uri="fixture:recording"))
    store.add(Campaign(id="c", objective="Check voltage", title="Fixture", inputs=[art.id],
                       budget_usd=0, created_at=time.time()))
    store.add(Run(id="r", campaign_id="c", step_id="s", tool="read_cell_voltage", idempotency_key="r",
                  input_ids=[art.id], status="ok", started_at=0, ended_at=0))
    store.add(Measurement(id="m", run_id="r", quantity="voltage", value=2, unit="V", artifact_ids=[art.id],
                          uncertainty=Uncertainty(value=.01, kind="bound", method="fixture")))
    store.add(Claim(id="k", campaign_id="c", author="fixture", statement="Near 2 V.", created_at=time.time(),
                    predicate=Predicate(type="value_within", params={"lo": 1.9, "hi": 2.1, "unit": "V"},
                                        measurement_ids=["m"])))
    gate.review(store, "k")
    store.emit("c", "campaign.started", "runtime", "Started")
    store.emit("c", "campaign.finished", "runtime", "Done")
    store.db.close()
    return store.root


def request(rid, method, **params):
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}


def test_stdio_initialize_discovery_and_tool_calls(ledger):
    before = {p.relative_to(ledger): p.read_bytes() for p in ledger.rglob("*") if p.is_file()}
    messages = [request(1, "initialize", protocolVersion="2025-06-18", capabilities={},
                        clientInfo={"name": "pytest", "version": "1"}),
                {"jsonrpc": "2.0", "method": "notifications/initialized"}, request(2, "tools/list"),
                request(3, "tools/call", name="list_campaigns", arguments={}),
                request(4, "tools/call", name="get_evidence", arguments={"claim_id": "k"}),
                request(5, "tools/call", name="list_findings", arguments={"campaign_id": "c"}),
                request(6, "tools/call", name="verify", arguments={"campaign_or_ledger": "c"})]
    process = subprocess.run([sys.executable, "-m", "translucent.mcp_server", "--ledger", str(ledger)],
                             input="".join(json.dumps(m) + "\n" for m in messages), text=True, encoding="utf-8",
                             capture_output=True, timeout=30,
                             env={**os.environ, "TRANSLUCENT_SAM": "strict"})
    assert process.returncode == 0, process.stderr
    replies = [json.loads(line) for line in process.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5, 6]
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    catalog = replies[1]["result"]["tools"]
    assert {t["name"] for t in catalog} == {"list_campaigns", "list_findings", "get_evidence", "verify"}
    assert all(t["annotations"]["readOnlyHint"] for t in catalog)
    values = [json.loads(r["result"]["content"][0]["text"]) for r in replies[2:]]
    assert values[0][0]["id"] == "c"
    assert values[1]["measurements"][0]["id"] == "m"
    assert values[1]["runs"][0]["status"] == "ok" and values[1]["artifacts"]
    assert values[1]["gate_checks"]
    assert values[2][0]["status"] == "supported"
    assert values[3]["ok"] and values[3]["matched"] == 1
    assert before == {p.relative_to(ledger): p.read_bytes() for p in ledger.rglob("*") if p.is_file()}


def test_protocol_errors_and_no_arbitrary_files(ledger):
    protocol = Protocol(ledger)
    assert protocol.handle(request(1, "tools/list"))["error"]["code"] == -32600
    assert protocol.handle(request(2, "initialize", protocolVersion="future", capabilities={}, clientInfo={}))[
        "result"]["protocolVersion"] == "2025-06-18"
    assert protocol.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert protocol.handle(request(3, "unknown"))["error"]["code"] == -32601
    assert protocol.handle(request(4, "tools/call", name="delete", arguments={}))["error"]["code"] == -32602
    for name, arguments in [("verify", {"campaign_or_ledger": str(ledger.parent)}),
                             ("get_evidence", {"claim_id": "missing"}),
                             ("get_evidence", {"claim_id": "k", "path": ".env"})]:
        reply = protocol.handle(request(5, "tools/call", name=name, arguments=arguments))
        assert reply["result"]["isError"]


def test_verify_mismatch_is_tool_error(ledger):
    path = next(p for p in (ledger / "blobs").rglob("*") if p.is_file())
    path.write_bytes(b"changed")
    assert call(ledger, "verify", {"campaign_or_ledger": "ledger"})["ok"] is False


def test_stdio_deep_json_does_not_end_the_session(ledger, monkeypatch, capsys):
    from translucent.mcp_server import main
    nested = "[" * 10000 + "0" + "]" * 10000
    monkeypatch.setattr(sys, "stdin", io.StringIO(nested + "\n" + json.dumps(request(7, "ping")) + "\n"))
    assert main(["--ledger", str(ledger)]) == 0
    replies = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1] == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_read_tools_page_findings_and_evidence_values(ledger):
    store = Ledger(ledger)
    try:
        for cid in ("k1", "k2"):
            store.add(store.get("claim", "k").model_copy(update={"id": cid}))
        series = store.add(Measurement(id="series", run_id="r", quantity="voltage", value=list(range(6)),
                                       unit="V", calibration_ids=["m"],
                                       uncertainty=Uncertainty(value=.01, kind="bound", method="fixture")))
        store.add(store.get("claim", "k").model_copy(update={"id": "series-claim", "predicate":
                  Predicate(type="trend", params={"direction": "increasing"}, measurement_ids=[series.id])}))
    finally:
        store.db.close()
    first = call(ledger, "list_findings", {"campaign_id": "c", "limit": 1})
    second = call(ledger, "list_findings", {"campaign_id": "c", "limit": 1, "cursor": "1"})
    assert [row["id"] for row in first] == ["k"]
    assert [row["id"] for row in second] == ["k1"]
    chain = call(ledger, "get_evidence", {"claim_id": "series-claim", "limit": 1, "sample_limit": 2})
    assert chain["measurements"][0]["value"] == [0, 1]
    assert chain["measurements"][0]["value_count"] == 6
    assert chain["measurements"][0]["value_next_cursor"] == "2"
    assert chain["next_cursor"] == "1"
    later = call(ledger, "get_evidence", {"claim_id": "series-claim", "limit": 1,
                                         "sample_limit": 2, "sample_cursor": "2"})
    assert later["measurements"][0]["value"] == [2, 3]
    assert later["measurements"][0]["value_next_cursor"] == "4"
    last = call(ledger, "get_evidence", {"claim_id": "series-claim", "limit": 1, "cursor": "1"})
    assert last["measurements"][0]["id"] == "m" and last["next_cursor"] is None


@pytest.mark.parametrize("arguments", [{"limit": True}, {"limit": 0}, {"limit": 201}, {"limit": "2"},
                                       {"cursor": "-1"}, {"cursor": "1.5"}, {"cursor": "9" * 30}])
def test_invalid_page_arguments_are_refused(ledger, arguments):
    with pytest.raises(ValueError):
        call(ledger, "list_findings", arguments)


def test_finding_review_summary_contains_latest_reviews_and_marks_truncation(ledger):
    store = Ledger(ledger)
    try:
        first = store.list("review", ref="k")[0]
        for index in range(55):
            store.add(first.model_copy(update={"id": f"review-{index}", "created_at": index,
                                               "verdict": "refuted" if index == 54 else "supported"}))
    finally:
        store.db.close()
    finding = call(ledger, "list_findings", {"campaign_id": "c"})[0]
    assert finding["status"] == "refuted"
    assert len(finding["reviews"]) == 50
    assert finding["reviews"][-1]["id"] == "review-54"
    assert finding["review_count"] == 56 and finding["reviews_truncated"] is True
