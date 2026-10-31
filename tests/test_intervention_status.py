"""Controller failures and replay completion are never blamed on the planner."""

import json
import sqlite3

import httpx
from openai import APIStatusError
from openai.types.chat import ChatCompletion
import pytest

from translucent.intervention_chat import ChatSession, Settings, StopCampaign
from translucent.intervention_errors import RecordingError, ToolRejected
from translucent.ledger import AdmissionError


def completion(*, tool=None, arguments="{}", finish_reason="stop", reasoning=0):
    message = dict(role="assistant", content="continue")
    if tool:
        message = dict(
            role="assistant",
            content=None,
            tool_calls=[
                dict(
                    id="call-1",
                    type="function",
                    function=dict(name=tool, arguments=arguments),
                )
            ],
        )
    return ChatCompletion.model_validate(
        dict(
            id="status-test",
            object="chat.completion",
            created=0,
            model="muse-wonder-1.0",
            choices=[dict(index=0, message=message, finish_reason=finish_reason)],
            usage=dict(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                completion_tokens_details=dict(reasoning_tokens=reasoning),
            ),
        )
    )


class Client:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def chat(self, **request):
        self.calls.append(request)
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


@pytest.mark.parametrize("http_code", [400, 401, 403, 404])
def test_http_configuration_rejection_is_not_a_model_refusal(tmp_path, http_code):
    error = APIStatusError(
        "configuration rejected",
        response=httpx.Response(
            http_code, request=httpx.Request("POST", "https://provider.invalid")
        ),
        body=None,
    )
    client = Client(error)
    session = ChatSession(Settings(retry_base_s=0), tmp_path, client=client)
    with pytest.raises(StopCampaign) as stopped:
        session.chat([dict(role="user", content="recorded data")])
    assert stopped.value.status == "provider_rejected"
    assert f"HTTP {http_code}" in stopped.value.reason
    assert len(client.calls) == 1
    assert session.records[0]["error"]["status_code"] == http_code


def test_length_stop_records_finish_reason_reasoning_and_replays(tmp_path):
    session = ChatSession(
        Settings(),
        tmp_path,
        client=Client(completion(finish_reason="length", reasoning=17)),
    )
    messages = [dict(role="user", content="recorded data")]
    with pytest.raises(StopCampaign) as error:
        session.chat(messages)
    assert error.value.status == "length_limit"
    assert "reasoning_tokens=17" in error.value.reason
    entry = session.records[0]
    assert entry["finish_reason"] == entry["response"]["finish_reason"] == "length"
    assert entry["reasoning_tokens"] == entry["response"]["reasoning_tokens"] == 17
    with pytest.raises(StopCampaign) as replayed:
        ChatSession(Settings(), tmp_path, replay=True).chat(messages)
    assert replayed.value.status == "length_limit"
    assert replayed.value.reason == error.value.reason


def test_a_cut_off_reply_is_asked_again_with_more_room_and_replays(tmp_path):
    client = Client(completion(finish_reason="length", reasoning=3900), completion(tool="finish"))
    settings = Settings(max_tokens=4000, length_max_tokens=10000, retry_base_s=0)
    messages = [dict(role="user", content="recorded data")]
    reply = ChatSession(settings, tmp_path, client=client).chat(messages)
    assert reply["tool_calls"][0]["function"]["name"] == "finish"
    assert [c["max_tokens"] for c in client.calls] == [4000, 8000]
    assert ChatSession(settings, tmp_path, replay=True).chat(messages) == reply


def test_the_room_for_a_cut_off_reply_stops_at_its_ceiling(tmp_path):
    client = Client(*[completion(finish_reason="length", reasoning=9000)] * 3)
    session = ChatSession(Settings(max_tokens=4000, length_max_tokens=10000, retry_base_s=0), tmp_path, client=client)
    with pytest.raises(StopCampaign) as error:
        session.chat([dict(role="user", content="recorded data")])
    assert error.value.status == "length_limit"
    assert [c["max_tokens"] for c in client.calls] == [4000, 8000, 10000]


def test_paid_recording_write_failure_keeps_cost_and_no_partial_tape(
    tmp_path, monkeypatch
):
    import translucent.intervention_chat as chat

    def fail_replace(*args):
        raise OSError("disk full")

    session = ChatSession(Settings(), tmp_path, client=Client(completion()))
    monkeypatch.setattr(chat.os, "replace", fail_replace)
    with pytest.raises(RecordingError, match="disk full"):
        session.chat([dict(role="user", content="recorded data")])
    assert session.spent_usd > 0
    assert not session.records
    assert not list((tmp_path / "chat").iterdir())


def test_paid_unserializable_response_keeps_its_cost(tmp_path, monkeypatch):
    import translucent.intervention_chat as chat

    original = chat._plain

    def corrupt(value):
        if isinstance(value, ChatCompletion):
            return dict(invalid=float("nan"))
        return original(value)

    session = ChatSession(Settings(), tmp_path, client=Client(completion()))
    monkeypatch.setattr(chat, "_plain", corrupt)
    with pytest.raises(RecordingError, match="serialize"):
        session.chat([dict(role="user", content="recorded data")])
    assert session.spent_usd > 0
    assert not session.records


def test_live_recording_failure_stops_with_charged_cost_and_partial_scores(
    tmp_path, monkeypatch
):
    import translucent.intervention_chat as chat
    from translucent.intervention import records
    from translucent.intervention_live import run_live
    from translucent.intervention_runtime import open_intervention
    from translucent.ledger import Ledger

    def fail_write(*args):
        raise RecordingError("disk full")

    ledger = Ledger(tmp_path / "ledger")
    try:
        campaign, fold, sealed = open_intervention(
            ledger, tmp_path / "sealed", selected={15, 22}, min_trials=1
        )
        session = ChatSession(
            Settings(), tmp_path / "recordings", client=Client(completion())
        )
        monkeypatch.setattr(chat, "atomic_recording", fail_write)
        result = run_live(ledger, campaign, fold, sealed, session)
        assert result["status"] == "recording_error", result
        assert result["spent_usd"] > 0
        stops = [
            r
            for r in records(ledger, campaign.id, "planner")
            if r.data.get("phase") == "stop"
        ]
        assert stops[0].data["spent_usd"] == result["spent_usd"]
        scores = records(ledger, campaign.id, "score")
        assert scores
        assert all(
            s.data["partial"] and s.data["campaign_status"] == "recording_error"
            for s in scores
        )
    finally:
        ledger.db.close()


@pytest.mark.parametrize(
    "failure",
    [
        ValueError("corrupt sealed JSON"),
        OSError("missing sealed file"),
        sqlite3.Error("database error"),
        AdmissionError("Reveal does not match seal"),
        AdmissionError("Intervention hash chain is broken"),
    ],
)
def test_live_controller_error_stops_without_sending_model_tool_error(
    tmp_path, monkeypatch, failure
):
    from translucent.intervention import records
    from translucent.intervention_live import run_live
    from translucent.intervention_runtime import InterventionTools, open_intervention
    from translucent.ledger import Ledger

    def broken(*args):
        raise failure

    ledger = Ledger(tmp_path / "ledger")
    try:
        campaign, fold, sealed = open_intervention(
            ledger, tmp_path / "sealed", selected={15, 22}, min_trials=1
        )
        client = Client(completion(tool="list_trials"))
        session = ChatSession(Settings(), tmp_path / "recordings", client=client)
        monkeypatch.setattr(InterventionTools, "call", broken)
        result = run_live(ledger, campaign, fold, sealed, session)
        assert result["status"] == "controller_error", result
        assert str(failure) in result["reason"]
        planner = records(ledger, campaign.id, "planner")
        assert planner[-1].data["phase"] == "stop"
        assert planner[-1].data["status"] == "controller_error"
        assert not any(p.data.get("phase") == "tool_result" for p in planner)
        assert len(client.calls) == 1
    finally:
        ledger.db.close()


def test_live_startup_storage_failure_is_a_controller_stop(tmp_path, monkeypatch):
    import translucent.intervention_live as live
    from translucent.intervention_runtime import open_intervention
    from translucent.ledger import Ledger

    ledger = Ledger(tmp_path / "ledger")
    try:
        campaign, fold, sealed = open_intervention(
            ledger, tmp_path / "sealed", selected={15, 22}, min_trials=1
        )

        def failed_read(*args):
            raise sqlite3.Error("Cannot read campaign protocol")

        monkeypatch.setattr(live, "protocol", failed_read)
        session = ChatSession(Settings(), tmp_path / "recordings")
        result = live.run_live(ledger, campaign, fold, sealed, session)
        assert result["status"] == "controller_error"
        assert "Cannot read campaign protocol" in result["reason"]
        assert result["tool_calls"] == 0
    finally:
        ledger.db.close()


def test_model_tool_rejection_is_returned_but_recording_error_stops(
    tmp_path, monkeypatch
):
    from translucent.intervention import records
    from translucent.intervention_live import run_live
    from translucent.intervention_runtime import InterventionTools, open_intervention
    from translucent.ledger import Ledger

    ledger = Ledger(tmp_path / "ledger")
    try:
        campaign, fold, sealed = open_intervention(
            ledger, tmp_path / "sealed", selected={15, 22}, min_trials=1
        )

        def reject(*args):
            raise ToolRejected("unknown trial")

        monkeypatch.setattr(InterventionTools, "call", reject)
        session = ChatSession(
            Settings(),
            tmp_path / "recordings",
            client=Client(completion(tool="list_trials")),
        )
        result = run_live(ledger, campaign, fold, sealed, session, step_cap=1)
        assert result["status"] == "step_cap"
        tool_results = [
            r.data
            for r in records(ledger, campaign.id, "planner")
            if r.data.get("phase") == "tool_result"
        ]
        assert tool_results[0]["result"]["error"] == "ToolRejected: unknown trial"
    finally:
        ledger.db.close()


def test_run_completion_receipt_and_truncated_tape_rejection(tmp_path):
    from translucent.intervention_session import run_intervention
    from translucent.recordings import NoRecording

    settings = Settings(budget_usd=10)
    options = dict(selected={15, 22}, min_trials=1)
    store = tmp_path / "recordings"
    live = run_intervention(
        tmp_path / "live",
        settings,
        recordings=store,
        client=Client(completion(), completion(), completion()),
        **options,
    )
    receipt = json.loads((store / "run.json").read_text())
    assert receipt == dict(
        version=1,
        status="stalled",
        reason=live["reason"],
        tool_calls=0,
        spent_usd=live["spent_usd"],
        recording_count=3,
    )
    replay = run_intervention(tmp_path / "replay", settings, replay=store, **options)
    assert replay["decisions_identical"]
    assert replay["status"] == live["status"]
    (store / "chat" / "000003.json").unlink()
    with pytest.raises(NoRecording, match="No exact"):
        run_intervention(tmp_path / "truncated", settings, replay=store, **options)


def test_replay_requires_the_recorded_run_status_and_completion_receipt(tmp_path):
    from translucent.intervention_session import run_intervention
    from translucent.recordings import NoRecording

    settings = Settings(budget_usd=0.000001)
    options = dict(selected={15, 22}, min_trials=1)
    store = tmp_path / "recordings"
    run_intervention(tmp_path / "live", settings, recordings=store, **options)
    path = store / "run.json"
    receipt = json.loads(path.read_text())
    receipt["recording_count"] += 1
    path.write_text(json.dumps(receipt))
    with pytest.raises(NoRecording, match="status, reason, counts or spend"):
        run_intervention(tmp_path / "changed", settings, replay=store, **options)
    path.unlink()
    with pytest.raises(NoRecording, match="required run.json"):
        run_intervention(tmp_path / "missing", settings, replay=store, **options)


@pytest.mark.parametrize(
    "failure",
    [
        OSError("budget storage unavailable"),
        sqlite3.OperationalError("budget storage unavailable"),
    ],
)
def test_paid_balance_storage_failure_keeps_charge_and_controller_category(
    tmp_path, monkeypatch, failure
):
    from translucent.intervention_errors import ControllerError

    session = ChatSession(Settings(), tmp_path, client=Client(completion()))
    calls = 0

    def balance():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise failure
        return 0.0

    monkeypatch.setattr(session, "_balance", balance)
    with pytest.raises(ControllerError, match="budget storage unavailable"):
        session.chat([dict(role="user", content="recorded data")])
    assert session.spent_usd > 0
    assert not session.records
    assert not list((tmp_path / "chat").glob("*.json"))


def test_parallel_campaign_spend_is_not_charged_to_this_call(tmp_path, monkeypatch):
    # Another campaign on the same provider ledger spends $1 during this call.
    session = ChatSession(Settings(), tmp_path, client=Client(completion()))
    balances = iter([0.0, 1.0])
    monkeypatch.setattr(session, "_balance", lambda: next(balances))
    session.chat([dict(role="user", content="recorded data")])
    # 100 input and 20 output tokens at $4 and $20 per million.
    assert session.records[0]["cost_usd"] == pytest.approx(100 * 4e-6 + 20 * 20e-6)
    assert session.spent_usd == pytest.approx(100 * 4e-6 + 20 * 20e-6)
