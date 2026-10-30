"""The no-tool comparator uses only paid observations and sealed real feedback."""

import hashlib
import json
import sqlite3
from copy import deepcopy

import pytest

from translucent.intervention import canonical, get, records
from translucent.intervention_bench import score
from translucent.intervention_muse import parse_prediction, run_muse_only
from translucent.intervention_runtime import open_intervention
from translucent.ledger import Ledger
from translucent.verify import verify


class FakeSession:
    model = "fake-muse"
    spent_usd = 0.0
    last_record = None

    def __init__(self, responses, inspect=None):
        self.responses = iter(responses)
        self.requests = []
        self.inspect = inspect
        self.records = []
        self.last_record = None
        self.spent_usd = 0.0

    def chat(self, messages, tools=None):
        assert tools is None
        self.requests.append(deepcopy(messages))
        if self.inspect:
            self.inspect(messages)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        prediction = {"interval": [0, 1], **response}
        response = {"role": "assistant", "content": json.dumps(prediction)}
        request = {"model": self.model, "messages": deepcopy(messages)}
        entry = {
            "kind": "chat",
            "request": request,
            "request_sha256": hashlib.sha256(canonical(request)).hexdigest(),
            "provider": "fake",
            "model": self.model,
            "response": deepcopy(response),
            "usage": {},
            "cost_usd": 0.0,
        }
        entry["digest"] = hashlib.sha256(canonical(entry)).hexdigest()
        self.records.append(entry)
        self.last_record = entry
        return response


def opened(tmp_path, *, split="loco", selected=None):
    ledger = Ledger(tmp_path / "ledger")
    campaign, fold, store = open_intervention(
        ledger,
        tmp_path / "sealed",
        split=split,
        selected=selected or {15, 16, 22, 28, 35, 65, 66, 67},
        min_trials=1,
    )
    return ledger, campaign, fold, store


def test_fake_muse_replaces_the_unrun_comparator_score(tmp_path):
    L, campaign, fold, store = opened(tmp_path)
    session = FakeSession([{"action": "escalate", "p_ignite": 0.8}] * 8)
    result = run_muse_only(L, campaign, fold, store, session)
    assert result["status"] == "finished", result
    assert result["completed_trials"] == 8
    assert result["scores"][0]["status"] == "scored"
    assert result["scores"][0]["n"] == 8
    policy = get(L, campaign.id, result["policy_id"], "policy")
    assert policy.data["kind"] == "baseline"
    assert policy.data["name"] == "muse_only"
    assert len(session.requests) == 8
    for messages in session.requests:
        assert len(messages) == 2
        payload = json.loads(messages[1]["content"])
        assert set(payload["metadata"]) >= {
            "condition",
            "chemistry",
            "capacity_mAh",
            "delay_after_vent_s",
        }
        samples = payload["observed"]["rows"]
        assert len(samples) <= 128
        assert max(t for t, _ in samples) <= payload["metadata"]["t_heater_off"]
        assert "outcome" not in payload
    assert verify(L.root).ok
    L.db.close()


def test_muse_paid_window_is_committed_before_second_call_and_ignition_is_unsafe(
    tmp_path,
):
    L, campaign, fold, store = opened(tmp_path, selected={28})

    def inspect(messages):
        if len(messages) == 4:
            assert len(records(L, campaign.id, "decision")) == 1
            assert not records(L, campaign.id, "reveal")
            assert len(records(L, campaign.id, "observation")) == 1
            window = json.loads(messages[-1]["content"])
            assert window["paid_observe_s"] == 60
            trial = get(L, campaign.id, fold.data["test_ids"][0], "trial")
            assert all(
                trial.data["t_heater_off"] < t <= trial.data["t_heater_off"] + 60
                for t, _ in window["observed"]["rows"]
            )

    session = FakeSession(
        [
            {"action": "observe(60)", "p_ignite": 0.6},
            {"action": "stand_down", "p_ignite": 0.1},
        ],
        inspect=inspect,
    )
    result = run_muse_only(L, campaign, fold, store, session)
    assert result["status"] == "finished", result
    s = result["scores"][0]
    assert s["n"] == 1
    assert s["unsafe"] == 1
    assert s["ignited_during_watch"] == 1
    assert s["brier"] == pytest.approx((0.6 - 1) ** 2)
    assert 0 < s["watch_time_s"] < 60
    decisions = records(L, campaign.id, "decision")
    reveal = records(L, campaign.id, "reveal")[0]
    assert decisions[0].seq < decisions[1].seq < reveal.seq
    assert verify(L.root).ok
    L.db.close()


def test_fixed_muse_model_runs_prequential_without_learning_from_labels(tmp_path):
    L, campaign, fold, store = opened(
        tmp_path, split="prequential", selected={15, 16, 22}
    )
    session = FakeSession([{"action": "escalate", "p_ignite": 0.5}] * 3)
    result = run_muse_only(L, campaign, fold, store, session)
    assert result["status"] == "finished", result
    assert result["scores"][0]["n"] == 3
    assert not [
        r
        for r in records(L, campaign.id, "update")
        if r.data["policy_id"] == result["policy_id"]
    ]
    assert all(len(m) == 2 for m in session.requests)
    assert verify(L.root).ok
    L.db.close()


@pytest.mark.parametrize(
    "response",
    [
        {"action": "observe", "observe_s": 31, "p_ignite": 0.5},
        {"action": "stand_down", "p_ignite": float("nan")},
        {"action": "stand_down", "p_ignite": 0.5, "interval": [0.6, 0.9]},
        {"action": "escalate", "observe_s": 30, "p_ignite": 0.5},
        {"action": "stand_down", "decision": "escalate", "p_ignite": 0.5},
        {"action": "abstain", "p_ignite": 0.5},
        {"action": ["escalate"], "p_ignite": 0.5},
        {"action": "escalate", "p_ignite": 10**1000},
    ],
)
def test_invalid_muse_predictions_fail_closed(response):
    with pytest.raises(ValueError):
        parse_prediction({"content": json.dumps(response)})


def test_muse_prediction_requires_an_explicit_interval():
    from translucent.intervention_errors import InvalidModelResponse

    with pytest.raises(InvalidModelResponse, match="missing required interval"):
        parse_prediction({"content": '{"action":"escalate","p_ignite":0.5}'})


@pytest.mark.parametrize("with_interval", [False, True])
def test_legacy_muse_cache_cannot_supply_unreceipted_benchmark_decisions(
    tmp_path, with_interval
):
    from translucent.intervention_models import muse_replay
    from translucent.recordings import key

    summary = {"level": 170}
    prompt = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    prediction = {"p_ignite": 0.5}
    if with_interval:
        prediction["interval"] = [0, 1]
    path = tmp_path / (key("muse-only", digest, prompt, {}) + ".json")
    path.write_text(
        json.dumps(
            {
                "prompt": prompt,
                "media_sha": digest,
                "kind": "muse-only",
                "text": json.dumps(prediction),
            }
        )
    )
    reason = "bound model request" if with_interval else "missing required interval"
    with pytest.raises(ValueError, match=reason):
        muse_replay(summary, tmp_path)


@pytest.mark.parametrize(
    "error,status",
    [
        (OSError("sealed store is unreadable"), "controller_error"),
        (ValueError("Outcome seal mismatch"), "controller_error"),
        (KeyError("corrupt controller record"), "controller_error"),
        (sqlite3.OperationalError("database is corrupt"), "controller_error"),
    ],
)
def test_muse_controller_failures_do_not_blame_the_model(tmp_path, error, status):
    L, campaign, fold, store = opened(tmp_path, selected={15})
    result = run_muse_only(L, campaign, fold, store, FakeSession([error]))
    assert result["status"] == status
    assert result["scores"][0]["partial"] is True
    assert result["scores"][0]["campaign_status"] == status
    L.db.close()


def test_muse_invalid_model_schema_is_reported_as_invalid_response(tmp_path):
    L, campaign, fold, store = opened(tmp_path, selected={15})
    result = run_muse_only(
        L,
        campaign,
        fold,
        store,
        FakeSession([{"action": ["escalate"], "p_ignite": 0.5}]),
    )
    assert result["status"] == "invalid_response"
    assert "action is not available" in result["reason"]
    L.db.close()


def test_muse_missing_recording_is_a_replay_error(tmp_path):
    from translucent.recordings import NoRecording

    L, campaign, fold, store = opened(tmp_path, selected={15})
    result = run_muse_only(
        L, campaign, fold, store, FakeSession([NoRecording("truncated tape")])
    )
    assert result["status"] == "replay_error"
    L.db.close()


def test_muse_recording_failure_keeps_the_paid_cost(tmp_path):
    from translucent.intervention_errors import RecordingError

    L, campaign, fold, store = opened(tmp_path, selected={15})
    session = FakeSession([RecordingError("disk is full")])
    session.spent_usd = 0.25
    result = run_muse_only(L, campaign, fold, store, session)
    assert result["status"] == "recording_error"
    assert result["spent_usd"] == 0.25
    assert not records(L, campaign.id, "decision")
    L.db.close()


def test_muse_scoring_failure_records_the_controller_stop(tmp_path, monkeypatch):
    def broken_score(*_):
        raise KeyError("broken score record")

    L, campaign, fold, store = opened(tmp_path, selected={15})
    monkeypatch.setattr("translucent.intervention_muse.score", broken_score)
    result = run_muse_only(
        L,
        campaign,
        fold,
        store,
        FakeSession([{"action": "escalate", "p_ignite": 0.8}]),
    )
    assert result["status"] == "controller_error"
    stops = [
        r
        for r in records(L, campaign.id, "planner")
        if r.data.get("phase") == "stopped"
    ]
    assert stops[-1].data["status"] == "controller_error"
    L.db.close()


def test_muse_never_fabricates_an_unrecorded_request(tmp_path):
    class UnrecordedSession(FakeSession):
        def chat(self, messages, tools=None):
            return {
                "role": "assistant",
                "content": '{"action":"escalate","p_ignite":0.8,"interval":[0,1]}',
            }

    L, campaign, fold, store = opened(tmp_path, selected={15})
    result = run_muse_only(L, campaign, fold, store, UnrecordedSession([]))
    assert result["status"] == "controller_error"
    assert "no durable model request" in result["reason"]
    assert not records(L, campaign.id, "decision")
    L.db.close()


def test_repro_muse_verifier_rejects_a_decision_without_a_model_receipt(tmp_path):
    from test_intervention_seal_versions import _rehash

    L, campaign, fold, store = opened(tmp_path, selected={15})
    result = run_muse_only(
        L, campaign, fold, store, FakeSession([{"action": "escalate", "p_ignite": 0.8}])
    )
    decision = next(
        d
        for d in records(L, campaign.id, "decision")
        if d.data["policy_id"] == result["policy_id"]
    )
    _rehash(L, campaign.id, {decision.id: {"model_receipt_id": None}})
    verified = verify(L.root)
    assert not verified.ok
    assert any("requires model_receipt_id" in p for p in verified.problems), (
        verified.problems
    )
    L.db.close()


@pytest.mark.parametrize("mutation", ["missing", "response", "request"])
def test_verify_binds_muse_receipt_to_the_actual_model_request(tmp_path, mutation):
    from test_intervention_seal_versions import _rehash

    L, campaign, fold, store = opened(tmp_path, selected={15})
    result = run_muse_only(
        L, campaign, fold, store, FakeSession([{"action": "escalate", "p_ignite": 0.8}])
    )
    assert result["status"] == "finished", result
    source = next(
        r
        for r in records(L, campaign.id, "planner")
        if r.data.get("phase") == "model_request"
    )
    receipt = next(
        r
        for r in records(L, campaign.id, "planner")
        if r.data.get("planner") == "muse_only" and "raw_assistant" in r.data
    )
    source_data = deepcopy(source.data)
    if mutation == "missing":
        source_data["phase"] = "unrelated"
        changes = {source.id: source_data}
    else:
        if mutation == "response":
            source_data["response"]["content"] = (
                '{"action":"escalate","p_ignite":0.9,"interval":[0,1]}'
            )
        else:
            source_data["request"]["messages"][-1]["content"] = "unpaid information"
        source_data["digest"] = hashlib.sha256(
            canonical(
                {k: v for k, v in source_data.items() if k not in {"phase", "digest"}}
            )
        ).hexdigest()
        changes = {
            source.id: source_data,
            receipt.id: {
                "recording_digest": source_data["digest"],
                "attempt_recording_digests": [source_data["digest"]],
            },
        }
    _rehash(L, campaign.id, changes)
    verified = verify(L.root)
    assert not verified.ok
    assert any("model_request" in p for p in verified.problems), verified.problems
    L.db.close()


def test_verify_rejects_ambiguous_duplicate_model_request_receipts(
    tmp_path, monkeypatch
):
    from translucent.intervention import record

    L, campaign, fold, store = opened(tmp_path, selected={15})
    result = run_muse_only(
        L, campaign, fold, store, FakeSession([{"action": "escalate", "p_ignite": 0.8}])
    )
    source = next(
        r
        for r in records(L, campaign.id, "planner")
        if r.data.get("phase") == "model_request"
    )
    receipt = next(
        r
        for r in records(L, campaign.id, "planner")
        if r.data.get("planner") == "muse_only" and "raw_assistant" in r.data
    )
    decision = next(
        r
        for r in records(L, campaign.id, "decision")
        if r.data["policy_id"] == result["policy_id"]
    )
    with monkeypatch.context() as patch:
        patch.setattr("translucent.intervention.admit", lambda *_: None)
        record(L, campaign.id, "planner", deepcopy(source.data))
        forged = record(L, campaign.id, "planner", deepcopy(receipt.data))
        record(
            L,
            campaign.id,
            "decision",
            {
                **decision.data,
                "model_receipt_id": forged.id,
            },
        )
    verified = verify(L.root)
    assert not verified.ok
    assert any("exactly one earlier model_request" in p for p in verified.problems)
    L.db.close()


def test_budget_during_watch_is_explicitly_incomplete_without_fabricated_terminal(
    tmp_path,
):
    from translucent.intervention_chat import StopCampaign

    L, campaign, fold, store = opened(tmp_path, selected={28})
    session = FakeSession(
        [
            {"action": "observe", "observe_s": 30, "p_ignite": 0.5},
            StopCampaign("budget", "Run budget cannot reserve another request"),
        ]
    )
    result = run_muse_only(L, campaign, fold, store, session)
    assert result["status"] == "budget"
    assert result["incomplete_decision_ids"]
    assert not result["claim_available"]
    assert result["completed_trials"] == 0
    assert not result["scores"]
    assert len(records(L, campaign.id, "decision")) == 1
    assert not records(L, campaign.id, "reveal")
    assert not verify(L.root).ok
    L.db.close()


def test_receipt_cannot_borrow_an_unpaid_sample_or_change_the_raw_probability(tmp_path):
    from translucent.intervention_muse import validate_muse_decision

    L, campaign, fold, store = opened(tmp_path, selected={15})
    result = run_muse_only(
        L, campaign, fold, store, FakeSession([{"action": "escalate", "p_ignite": 0.8}])
    )
    d = records(L, campaign.id, "decision")[0]
    changed = {**d.data, "p_ignite": 0.9}
    with pytest.raises(ValueError, match="differs"):
        validate_muse_decision(L, campaign.id, changed, before_seq=d.seq)
    assert score(L, campaign.id, result["policy_id"], "loco")["n"] == 1
    L.db.close()


def test_request_receipt_with_an_unpaid_sample_cannot_commit(tmp_path):
    L, campaign, fold, store = opened(tmp_path, selected={15})

    class WrongRequest(FakeSession):
        def chat(self, messages, tools=None):
            response = super().chat(messages, tools)
            altered = deepcopy(messages)
            payload = json.loads(altered[1]["content"])
            payload["observed"]["rows"].append([99999, 700])
            altered[1]["content"] = json.dumps(payload)
            entry = self.records[-1]
            entry["request"]["messages"] = altered
            entry["request_sha256"] = hashlib.sha256(
                canonical(entry["request"])
            ).hexdigest()
            entry["digest"] = hashlib.sha256(
                canonical({k: v for k, v in entry.items() if k != "digest"})
            ).hexdigest()
            return response

    result = run_muse_only(
        L,
        campaign,
        fold,
        store,
        WrongRequest([{"action": "escalate", "p_ignite": 0.8}]),
    )
    assert result["status"] == "controller_error"
    assert "permitted trace" in result["reason"]
    assert not records(L, campaign.id, "decision")
    assert result["scores"][0]["status"] == "not_run"
    L.db.close()


def test_budget_stop_counts_preceding_completed_trials_without_scoring_partial_episode(
    tmp_path,
):
    from translucent.intervention_chat import StopCampaign

    L, campaign, fold, store = opened(tmp_path, split="prequential", selected={15, 16})
    result = run_muse_only(
        L,
        campaign,
        fold,
        store,
        FakeSession(
            [
                {"action": "escalate", "p_ignite": 0.8},
                {"action": "observe", "observe_s": 30, "p_ignite": 0.5},
                StopCampaign("budget", "No request reservation remains"),
            ]
        ),
    )
    assert result["status"] == "budget"
    assert result["completed_trials"] == 1
    assert len(records(L, campaign.id, "reveal")) == 1
    assert result["incomplete_decision_ids"]
    assert not result["claim_available"]
    assert not result["scores"]
    L.db.close()


def test_muse_provider_recording_replays_identical_decision_bytes_without_keys(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from translucent.intervention_chat import Settings
    from translucent.intervention_session import run_intervention

    class Provider:
        calls = 0

        def chat(self, **request):
            assert "tools" not in request
            self.calls += 1
            prediction = (
                {
                    "action": "observe",
                    "observe_s": 30,
                    "p_ignite": 0.6,
                    "interval": [0, 1],
                }
                if self.calls == 1
                else {"action": "escalate", "p_ignite": 0.9, "interval": [0, 1]}
            )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            role="assistant",
                            content=json.dumps(prediction),
                            tool_calls=None,
                        )
                    )
                ],
                model=request["model"],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
            )

    settings = Settings(profile="meta", budget_usd=10, retry_base_s=0)
    provider = Provider()
    recordings = tmp_path / "recordings"
    live = run_intervention(
        tmp_path / "live",
        settings,
        muse_only=True,
        recordings=recordings,
        client=provider,
        selected={28},
        min_trials=1,
    )
    assert live["status"] == "finished", live
    assert live["scores"][0]["n"] == 1
    assert provider.calls == 2
    monkeypatch.setattr(
        "translucent.intervention_chat._client",
        lambda *_: pytest.fail("replay opened a provider"),
    )
    replay = run_intervention(
        tmp_path / "replay",
        settings,
        muse_only=True,
        replay=recordings,
        selected={28},
        min_trials=1,
    )
    assert replay["decisions_identical"]
    assert replay["scores"] == live["scores"]
    assert replay["spent_usd"] == live["spent_usd"]
    assert verify(tmp_path / "replay").ok


@pytest.mark.parametrize("failure", ["transient", "refusal", "budget"])
def test_muse_ledger_retains_every_model_attempt_and_its_cost(tmp_path, failure):
    import httpx
    from openai import APIConnectionError
    from openai.types.chat import ChatCompletion

    from translucent.budget import BudgetExceeded
    from translucent.intervention_chat import ChatSession, Settings

    reply = ChatCompletion.model_validate(
        {
            "id": "fake-call",
            "object": "chat.completion",
            "created": 0,
            "model": "muse-wonder-1.0",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {"action": "escalate", "p_ignite": 0.7, "interval": [0, 1]}
                        ),
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        }
    )
    if failure == "transient":
        values = [
            APIConnectionError(
                request=httpx.Request("POST", "https://provider.invalid")
            ),
            reply,
        ]
        expected_status, expected_attempts = "finished", 2
    elif failure == "refusal":
        reply.choices[0].message.content = None
        reply.choices[0].message.refusal = "Cannot answer this request"
        values = [reply]
        expected_status, expected_attempts = "refused", 1
    else:
        values = [BudgetExceeded("Provider spending reservation rejected")]
        expected_status, expected_attempts = "budget", 1

    class Provider:
        def chat(self, **request):
            value = values.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

    L, campaign, fold, store = opened(tmp_path, selected={15})
    session = ChatSession(
        Settings(budget_usd=1, retry_base_s=0),
        tmp_path / "recordings",
        client=Provider(),
    )
    result = run_muse_only(L, campaign, fold, store, session)
    assert result["status"] == expected_status
    attempts = [
        r.data
        for r in records(L, campaign.id, "planner")
        if r.data.get("phase") == "model_request"
    ]
    assert len(attempts) == len(session.records) == expected_attempts
    assert [
        {k: v for k, v in a.items() if k != "phase"} for a in attempts
    ] == session.records
    assert sum(a["cost_usd"] for a in attempts) == pytest.approx(result["spent_usd"])
    assert all(a["request_sha256"] and a["digest"] and a["model"] for a in attempts)
    if failure == "transient":
        assert attempts[0]["error"]["transient"]
        assert attempts[0]["cost_usd"] > 0
        receipt = next(
            r
            for r in records(L, campaign.id, "planner")
            if r.data.get("planner") == "muse_only" and "raw_assistant" in r.data
        )
        assert receipt.data["cost_usd"] == sum(a["cost_usd"] for a in attempts)
        assert receipt.data["attempt_recording_digests"] == [
            a["digest"] for a in attempts
        ]
    assert verify(L.root).ok
    L.db.close()
