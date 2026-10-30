"""Real chat-shaped fake providers exercise planning, metering and exact replay."""

import json

import httpx
from openai import APIConnectionError, PermissionDeniedError
from openai.types.chat import ChatCompletion
import pytest

from translucent.intervention_chat import (
    ChatSession,
    Settings,
    StopCampaign,
    worst_case_cost,
)


def reply(message=None):
    return ChatCompletion.model_validate(
        dict(
            id="fake-call",
            object="chat.completion",
            created=0,
            model="muse-wonder-1.0",
            choices=[
                dict(
                    index=0,
                    message=message or dict(role="assistant", content="ready"),
                    finish_reason="stop",
                )
            ],
            usage=dict(prompt_tokens=100, completion_tokens=20, total_tokens=120),
        )
    )


class Responses:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def chat(self, **request):
        self.calls.append(request)
        value = next(self.responses)
        if isinstance(value, BaseException):
            raise value
        return value


def test_chat_records_transient_retry_and_replays_without_client(tmp_path, monkeypatch):
    import translucent.intervention_chat as chat

    client = Responses(
        APIConnectionError(request=httpx.Request("POST", "https://provider.invalid")),
        reply(),
    )
    settings = Settings(budget_usd=1, retry_base_s=0)
    session = ChatSession(settings, tmp_path, client=client)
    messages = [dict(role="user", content="Use recorded measurements only")]
    result = session.chat(messages)
    assert len(client.calls) == len(session.records) == 2
    assert session.records[0]["error"]["transient"]
    assert session.records[0]["cost_usd"] == worst_case_cost(settings, messages)
    assert session.records[1]["usage"]["total_tokens"] == 120
    assert session.records[1]["request_sha256"]
    assert (
        session.records[1]["raw_response"]["choices"][0]["message"]["content"]
        == "ready"
    )
    monkeypatch.setattr(
        chat, "_client", lambda *a: pytest.fail("replay constructed a provider")
    )
    replay = ChatSession(settings, tmp_path, replay=True)
    assert replay.chat(messages) == result
    assert replay.spent_usd == session.spent_usd
    assert replay.records == session.records
    replay.assert_consumed()


@pytest.mark.parametrize("kind", ["assistant", "service"])
def test_refusal_is_recorded_without_retry_and_replayed(tmp_path, kind):
    if kind == "assistant":
        response = reply(
            dict(role="assistant", content=None, refusal="Cannot answer this request")
        )
    else:
        response = PermissionDeniedError(
            "Request refused",
            response=httpx.Response(
                403, request=httpx.Request("POST", "https://provider.invalid")
            ),
            body=None,
        )
    client = Responses(response)
    settings = Settings(budget_usd=1, retry_base_s=0)
    live = ChatSession(settings, tmp_path, client=client)
    with pytest.raises(StopCampaign) as stopped:
        live.chat([dict(role="user", content="recorded experiment")])
    assert stopped.value.status == ("refused" if kind == "assistant" else "provider_rejected")
    if kind == "service":
        assert "HTTP 403" in stopped.value.reason
    assert len(client.calls) == 1
    replay = ChatSession(settings, tmp_path, replay=True)
    with pytest.raises(StopCampaign) as replayed:
        replay.chat([dict(role="user", content="recorded experiment")])
    assert replayed.value.status == stopped.value.status
    assert replayed.value.reason == stopped.value.reason
    assert replay.spent_usd == live.spent_usd


def test_campaign_cap_reserves_before_constructing_provider(tmp_path, monkeypatch):
    import translucent.intervention_chat as chat

    monkeypatch.setattr(
        chat, "_client", lambda *a: pytest.fail("provider constructed beyond budget")
    )
    session = ChatSession(Settings(budget_usd=0.000001), tmp_path)
    with pytest.raises(StopCampaign) as stopped:
        session.chat([dict(role="user", content="task")])
    assert stopped.value.status == "budget"
    assert not session.records


def test_chat_replay_rejects_changed_request_and_tampered_tape(tmp_path):
    from translucent.recordings import NoRecording

    settings = Settings()
    messages = [dict(role="user", content="first")]
    ChatSession(settings, tmp_path, client=Responses(reply())).chat(messages)
    with pytest.raises(NoRecording, match="request"):
        ChatSession(settings, tmp_path, replay=True).chat(
            [dict(role="user", content="other")]
        )
    path = tmp_path / "chat" / "000001.json"
    entry = json.loads(path.read_text())
    entry["response"]["content"] = "altered"
    path.write_text(json.dumps(entry))
    with pytest.raises(NoRecording, match="digest"):
        ChatSession(settings, tmp_path, replay=True).chat(messages)


class ToolPlanner:
    """Only tool responses and frozen context drive this generic constant strategy."""

    def __init__(self, repair=False):
        self.program = self._program()
        self.started = False
        self.count = 0
        self.requests = []
        self.repair = repair

    def chat(self, **request):
        self.requests.append(request)
        assert request["tool_choice"] == "auto"
        if self.repair:
            self.repair = False
            return reply(dict(role="assistant", content="<|tool_call|>list_trials{}"))
        if not self.started:
            self.started = True
            call = next(self.program)
        else:
            result = json.loads(request["messages"][-1]["content"])
            assert "error" not in result, result
            call = self.program.send(result)
        self.count += 1
        return reply(
            dict(
                role="assistant",
                content=None,
                tool_calls=[
                    dict(
                        id=f"call-{self.count}",
                        type="function",
                        function=dict(name=call[0], arguments=json.dumps(call[1])),
                    )
                ],
            )
        )

    def _program(self):
        for mechanism in ("surface", "self_heating", "soc"):
            yield (
                "record_hypothesis",
                dict(
                    text=f"Competing {mechanism} mechanism",
                    mechanism=mechanism,
                    policies=[],
                ),
            )
        p = yield (
            "propose_policy",
            dict(
                name="generic constant probability",
                spec=dict(family="constant", probability=1),
            ),
        )
        pid = p["id"]
        submitted = False
        while True:
            yield "prepare_training", {}
            yield "evaluate_policy", dict(policy_id=pid)
            if not submitted:
                yield "submit_policy", dict(policy_id=pid)
                submitted = True
            yield "execute_fold", {}
            advance = yield "advance", {}
            if advance.get("done"):
                break
        score = yield "score", dict(policy_id=pid)
        claim = yield (
            "propose_claim",
            dict(
                predicate=dict(
                    type="policy_performance",
                    policy_id=pid,
                    split=score["split"],
                    subset="all",
                    n=score["n"],
                    unsafe=score["unsafe"],
                )
            ),
        )
        assert claim["verdict"] == "supported", claim
        yield "write_procedure", dict(policy_id=pid, claim_ids=[claim["claim_id"]])


def test_live_provider_tools_finish_all_folds_and_repair_chat_template(tmp_path):
    from translucent.intervention import records
    from translucent.intervention_live import run_live
    from translucent.intervention_runtime import open_intervention
    from translucent.ledger import Ledger
    from translucent.verify import verify

    ledger = Ledger(tmp_path / "ledger")
    campaign, fold, sealed = open_intervention(
        ledger, tmp_path / "sealed", selected={15, 22, 65}, min_trials=3
    )
    client = ToolPlanner(repair=True)
    session = ChatSession(
        Settings(budget_usd=10), tmp_path / "recordings", client=client
    )
    result = run_live(ledger, campaign, fold, sealed, session)
    assert result["status"] == "finished", result
    assert result["procedure"]["claim_ids"]
    evaluation_folds = [
        f for f in records(ledger, campaign.id, "fold")
        if f.data["scope"] != "baseline_registration"
    ]
    assert len(evaluation_folds) == 3
    agent_ids = {
        p.id for p in records(ledger, campaign.id, "policy")
        if p.data["kind"] == "agent"
    }
    assert (
        len([
            d for d in records(ledger, campaign.id, "decision")
            if d.data["scored"] and d.data["policy_id"] in agent_ids
        ])
        == 3
    )
    assert any(
        "plain text" in m.get("content", "")
        for request in client.requests
        for m in request["messages"]
    )
    assert (
        len({h.data["mechanism"] for h in records(ledger, campaign.id, "hypothesis")})
        == 3
    )
    contexts = [json.loads(request["messages"][1]["content"]) for request in client.requests]
    later_folds = [context for context in contexts if context["registration_frozen"]]
    assert later_folds
    for context in later_folds:
        assert context["submitted_agent_strategies"]
        assert "Registration is frozen" in context["instruction"]
        assert "prepare_training, then execute_fold" in context["instruction"]
        assert context["frozen_comparators"]
    assert verify(ledger.root).ok
    ledger.db.close()


def test_live_campaign_replay_reproduces_exact_committed_bodies(tmp_path, monkeypatch):
    import translucent.intervention_chat as chat
    from translucent.intervention_session import run_intervention
    from translucent.verify import verify

    class OnceTransient:
        def __init__(self):
            self.failed = False
            self.planner = ToolPlanner(repair=True)

        def chat(self, **request):
            if not self.failed:
                self.failed = True
                raise APIConnectionError(
                    request=httpx.Request("POST", "https://provider.invalid")
                )
            return self.planner.chat(**request)

    settings = Settings(budget_usd=10, retry_base_s=0)
    store = tmp_path / "recordings"
    selected = {15, 22, 65}
    live = run_intervention(
        tmp_path / "live",
        settings,
        recordings=store,
        client=OnceTransient(),
        selected=selected,
        min_trials=3,
    )
    assert live["status"] == "finished", live
    monkeypatch.setattr(
        chat, "_client", lambda *a: pytest.fail("replay opened a provider")
    )
    replay = run_intervention(
        tmp_path / "replay", settings, replay=store, selected=selected, min_trials=3
    )
    assert replay["status"] == "finished", replay
    assert replay["decisions_identical"] is True
    assert replay["committed_decisions"] == live["committed_decisions"]
    assert replay["spent_usd"] == live["spent_usd"]
    assert verify(tmp_path / "replay").ok


@pytest.mark.parametrize("kind", ["budget", "step_cap", "refused"])
def test_live_driver_records_clean_stop_reason(tmp_path, kind):
    from translucent.intervention import records
    from translucent.intervention_live import run_live
    from translucent.intervention_runtime import open_intervention
    from translucent.ledger import Ledger

    ledger = Ledger(tmp_path / "ledger")
    campaign, fold, sealed = open_intervention(
        ledger, tmp_path / "sealed", selected={15, 22}, min_trials=1
    )
    response = (
        reply(dict(role="assistant", refusal="Refused"))
        if kind == "refused"
        else reply(
            dict(
                role="assistant",
                tool_calls=[
                    dict(
                        id="one",
                        type="function",
                        function=dict(name="list_trials", arguments="{}"),
                    )
                ],
            )
        )
    )
    settings = Settings(budget_usd=0.000001 if kind == "budget" else 1)
    session = ChatSession(settings, tmp_path / "recordings", client=Responses(response))
    result = run_live(ledger, campaign, fold, sealed, session, step_cap=1)
    assert result["status"] == kind
    stop = records(ledger, campaign.id, "planner")[-1]
    assert stop.data["phase"] == "stop" and stop.data["reason"]
    assert ledger.events(campaign.id)[-1].data["status"] == kind
    ledger.db.close()


def test_default_cap_executes_exactly_120_tools_without_guessing_an_action(tmp_path):
    from translucent.intervention import records
    from translucent.intervention_live import run_live
    from translucent.intervention_runtime import open_intervention
    from translucent.ledger import Ledger

    class Listing:
        def __init__(self):
            self.calls = 0

        def chat(self, **request):
            self.calls += 1
            return reply(
                dict(
                    role="assistant",
                    tool_calls=[
                        dict(
                            id=f"call-{self.calls}",
                            type="function",
                            function=dict(name="list_trials", arguments="{}"),
                        )
                    ],
                )
            )

    ledger = Ledger(tmp_path / "ledger")
    campaign, fold, sealed = open_intervention(
        ledger, tmp_path / "sealed", selected={15, 22}, min_trials=1
    )
    client = Listing()
    session = ChatSession(
        Settings(budget_usd=10), tmp_path / "recordings", client=client
    )
    result = run_live(ledger, campaign, fold, sealed, session)
    assert result["status"] == "step_cap"
    assert result["tool_calls"] == client.calls == 120
    assert not records(ledger, campaign.id, "decision")
    assert (
        len(
            [
                r
                for r in records(ledger, campaign.id, "planner")
                if r.data["phase"] == "tool_call"
            ]
        )
        == 120
    )
    ledger.db.close()
