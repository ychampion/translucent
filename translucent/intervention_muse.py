"""Recorded, tool-free model judgments of the factual heater-off task.

The model receives only the declared cell metadata and available sensor samples.
It does not receive training labels, outcome tables, artifacts, or planner tools.
Every returned probability remains a simulated forecast until measured feedback.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy

from translucent.intervention import (
    canonical,
    commit_decision,
    fit_policy,
    get,
    pre_off_trace,
    protocol,
    record,
    records,
    reveal,
)
from translucent.intervention_actions import observe
from translucent.intervention_bench import score
from translucent.intervention_errors import (
    ControllerError,
    InvalidModelResponse,
    RecordingError,
)
from translucent.intervention_runtime import InterventionTools


MAX_TRACE_ROWS = 128


def numeric_table(trace, *, after=None):
    """Downsample by sample position, retaining both available endpoints."""
    pairs = [
        [float(t), float(v)]
        for t, v in zip(trace["t"], trace["temperature_C"])
        if after is None or t > after
    ]
    if len(pairs) > MAX_TRACE_ROWS:
        indices = sorted(
            {
                round(i * (len(pairs) - 1) / (MAX_TRACE_ROWS - 1))
                for i in range(MAX_TRACE_ROWS)
            }
        )
        pairs = [pairs[i] for i in indices]
    return {"columns": ["recorded_time_s", "surface_temperature_C"], "rows": pairs}


def _system_prompt(config):
    return (
        "The heater has just been cut on one recorded battery experiment. "
        "Judge whether stopping heat is enough or escalation is needed. "
        "Predict the recorded probability of ignition after this heater cut; "
        "the effect of suppression or evacuation was not recorded. "
        "Use only the metadata and numeric thermocouple table provided. "
        "The costs are "
        + json.dumps(config["costs"], sort_keys=True)
        + ". An unsafe stand-down and ignition while watching incur the respective "
        "unsafe/watch_ignition cost. Escalation costs unnecessary_escalation only "
        "when the recorded trial was rescued; escalation on an ignition costs zero. "
        "Watching costs watch_per_second for elapsed seconds. "
        "Available watch durations in seconds are "
        + json.dumps(config["observe_options"])
        + ". You may choose one watch, after which a final stand_down or escalate "
        "is required. Ignition during that watch is already unsafe regardless of "
        "the final decision. No tools are available. Return one JSON object only: "
        '{"action":"stand_down|escalate|observe", "observe_s":0, '
        '"p_ignite":0.5,"interval":[0.0,1.0]}. '
        "For observe set observe_s to 30 or 60; otherwise use 0. "
        "The interval is your uncertainty about ignition probability. "
        "The probability is a forecast, not a measured outcome."
    )


def trial_messages(L, cid, trial_id):
    """Build the complete first-call input without opening the sealed outcome."""
    t = get(L, cid, trial_id, "trial")
    # Explicit declared metadata only: reconstruction and quality flags can
    # depend on outcomes, so neither the first nor the paid-window prompt gets them.
    metadata = {
        key: t.data[key]
        for key in ("condition", "chemistry", "capacity_mAh", "delay_after_vent_s")
    }
    metadata.update(
        t_heater_on=t.data["t_heater_on"],
        t_vent=t.data["t_vent"],
        t_heater_off=t.data["t_heater_off"],
    )
    return [
        {"role": "system", "content": _system_prompt(protocol(L, cid).data)},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "metadata": metadata,
                    "observed": numeric_table(pre_off_trace(L, cid, trial_id)),
                    "decision_point": "recorded heater-off",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    ]


def terminal_messages(L, cid, initial, first_response, paid):
    """Add exactly the paid post-off samples to the same no-tool conversation."""
    from translucent.intervention_chat import assistant_message

    t = get(L, cid, initial.data["trial_id"], "trial")
    return trial_messages(L, cid, t.id) + [
        assistant_message(first_response),
        {
            "role": "user",
            "content": json.dumps(
                {
                    "paid_observe_s": initial.data["observe_s"],
                    "available_until": paid.data["available_until"],
                    "observed": numeric_table(
                        paid.data["trace"], after=t.data["t_heater_off"]
                    ),
                    "remaining_actions": ["stand_down", "escalate"],
                    "instruction": (
                        "Return the final decision and updated p_ignite in the same JSON format. "
                        "No further watching is available."
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    ]


def parse_prediction(message, *, terminal=False):
    """Accept one finite probability and one allowed action, failing closed."""
    if not isinstance(message, dict) or message.get("refusal"):
        raise InvalidModelResponse("Muse-only response refused the task")
    if message.get("tool_calls"):
        raise InvalidModelResponse("Muse-only cannot call tools")
    content = message.get("content")
    if not isinstance(content, str):
        raise InvalidModelResponse("Muse-only response must contain JSON text")
    try:
        value = json.loads(content)
    except ValueError as error:
        raise InvalidModelResponse(
            "Muse-only response must contain valid JSON"
        ) from error
    if not isinstance(value, dict) or set(value) - {
        "action",
        "decision",
        "observe_s",
        "p_ignite",
        "interval",
        "reason",
    }:
        raise InvalidModelResponse("Muse-only prediction has unknown fields")
    if (
        "action" in value
        and "decision" in value
        and value["action"] != value["decision"]
    ):
        raise InvalidModelResponse("Muse-only returned conflicting actions")
    action = value.get("action", value.get("decision"))
    seconds = value.get("observe_s", 0)
    if isinstance(action, str):
        match = re.fullmatch(r"observe\((30|60)\)", action)
        if match:
            if seconds not in (0, int(match.group(1))):
                raise InvalidModelResponse(
                    "Muse-only returned conflicting watch durations"
                )
            action, seconds = "observe", int(match.group(1))
    allowed = (
        {"stand_down", "escalate"}
        if terminal
        else {"stand_down", "escalate", "observe"}
    )
    if not isinstance(action, str) or action not in allowed or type(seconds) is not int:
        raise InvalidModelResponse("Muse-only action is not available")
    if seconds not in ((30, 60) if action == "observe" else (0,)):
        raise InvalidModelResponse("Muse-only watch duration is not available")
    p = value.get("p_ignite")
    if "interval" not in value:
        raise InvalidModelResponse("Muse-only prediction is missing required interval")
    interval = value["interval"]
    if (
        type(p) not in (int, float)
        or not 0 <= p <= 1
        or not math.isfinite(p)
        or not isinstance(interval, list)
        or len(interval) != 2
        or any(
            type(x) not in (int, float) or not 0 <= x <= 1 or not math.isfinite(x)
            for x in interval
        )
        or not 0 <= interval[0] <= p <= interval[1] <= 1
    ):
        raise InvalidModelResponse("Muse-only probability or interval is invalid")
    return {
        "decision": action,
        "observe_s": seconds,
        "p_ignite": p,
        "interval": interval,
    }


def _receipt(
    L,
    cid,
    session,
    trial_id,
    policy_id,
    fold_id,
    messages,
    response,
    *,
    parent=None,
    attempts=(),
):
    """Bind a judgment to the actual durable request, never a synthetic request."""
    meta = getattr(session, "last_record", {}) or {}
    if (
        not attempts
        or attempts[-1] != meta
        or not isinstance(meta.get("request"), dict)
    ):
        raise ControllerError(
            "Muse-only response has no durable model request recording"
        )
    request = deepcopy(meta["request"])
    if request.get("messages") != messages:
        raise ControllerError(
            "Muse-only request differs from the permitted trace and metadata"
        )
    if meta.get("response") != response or not meta.get("digest"):
        raise ControllerError(
            "Muse-only response differs from its durable model request recording"
        )
    prediction = parse_prediction(response, terminal=parent is not None)
    return record(
        L,
        cid,
        "planner",
        {
            "planner": "muse_only",
            "receipt_version": 2,
            "trial_id": trial_id,
            "policy_id": policy_id,
            "fold_id": fold_id,
            "stage": "terminal" if parent else "initial",
            "parent_decision_id": parent,
            "request": request,
            "request_sha256": hashlib.sha256(canonical(request)).hexdigest(),
            "raw_assistant": deepcopy(response),
            "response_sha256": hashlib.sha256(canonical(response)).hexdigest(),
            "prediction": prediction,
            "model": request["model"],
            "returned_model": meta.get("model", request["model"]),
            "recording_digest": meta.get("digest"),
            "attempt_recording_digests": [entry["digest"] for entry in attempts],
            "usage": deepcopy(meta.get("usage", {})),
            "cost_usd": math.fsum(entry["cost_usd"] for entry in attempts),
            "simulated": True,
        },
    )


def validate_muse_decision(L, cid, d, *, before_seq=None):
    """Recompute the committed model judgment and its allowed observation input.

    A recording is prediction evidence only. The salted reveal remains the sole
    measured outcome, and an unsupported counterfactual cannot enter this check.
    """
    if not d.get("model_receipt_id"):
        raise ValueError("Muse-only decision requires model_receipt_id")
    receipt = get(L, cid, d["model_receipt_id"], "planner")
    r = receipt.data
    policy = get(L, cid, d["policy_id"], "policy")
    if policy.data["kind"] != "baseline" or policy.data["spec"]["family"] != "muse":
        raise ValueError("Model receipt is reserved for the Muse-only comparator")
    if before_seq is not None and receipt.seq >= before_seq:
        raise ValueError("Muse-only prediction must precede its decision")
    if r.get("planner") != "muse_only" or any(
        r.get(key) != d.get(key)
        for key in ("trial_id", "policy_id", "fold_id", "parent_decision_id")
    ):
        raise ValueError("Muse-only receipt is outside this decision scope")
    terminal = d.get("parent_decision_id") is not None
    if (
        r.get("stage") != ("terminal" if terminal else "initial")
        or r.get("simulated") is not True
    ):
        raise ValueError("Muse-only receipt stage or forecast label differs")
    if hashlib.sha256(canonical(r["request"])).hexdigest() != r["request_sha256"]:
        raise ValueError("Muse-only request digest differs")
    if (
        hashlib.sha256(canonical(r["raw_assistant"])).hexdigest()
        != r["response_sha256"]
    ):
        raise ValueError("Muse-only response digest differs")
    if r["request"].get("tools") or r["request"].get("tool_choice"):
        raise ValueError("Muse-only model request may not include tools")
    if r["request"].get("model") != r.get("model"):
        raise ValueError("Muse-only recorded model differs")
    digest = r.get("recording_digest")
    if not isinstance(digest, str) or not digest:
        raise ValueError("Muse-only receipt requires a model request recording digest")
    attempts = r.get("attempt_recording_digests", [digest])
    if (
        not isinstance(attempts, list)
        or not attempts
        or attempts[-1] != digest
        or len(set(attempts)) != len(attempts)
    ):
        raise ValueError("Muse-only receipt has invalid model request attempts")
    sources = []
    for attempt in attempts:
        matching = [
            entry
            for entry in records(L, cid, "planner")
            if entry.seq < receipt.seq
            and entry.data.get("phase") == "model_request"
            and entry.data.get("digest") == attempt
        ]
        if len(matching) != 1:
            raise ValueError(
                "Muse-only receipt needs exactly one earlier model_request "
                "for each recording digest"
            )
        source = matching[0].data
        actual_digest = hashlib.sha256(
            canonical({k: v for k, v in source.items() if k not in {"phase", "digest"}})
        ).hexdigest()
        if actual_digest != attempt:
            raise ValueError("Muse-only model_request recording digest differs")
        if source.get("request") != r["request"]:
            raise ValueError("Muse-only receipt request differs from its model_request")
        sources.append(source)
    source = sources[-1]
    if source.get("error"):
        raise ValueError("Muse-only decision cannot use a failed model_request")
    finish_reason = source.get("finish_reason") or r["raw_assistant"].get(
        "finish_reason"
    )
    if finish_reason in {"length", "content_filter"}:
        raise ValueError(
            f"Muse-only decision cannot use an unfinished model_request ({finish_reason})"
        )
    if (
        source.get("response") != r["raw_assistant"]
        or source.get("request_sha256") != r["request_sha256"]
        or source.get("model") != r.get("returned_model")
        or source.get("usage") != r.get("usage")
    ):
        raise ValueError(
            "Muse-only receipt response or metadata differs from its model_request"
        )
    if r.get("cost_usd") != math.fsum(source["cost_usd"] for source in sources):
        raise ValueError(
            "Muse-only receipt cost differs from its model_request attempts"
        )
    expected = trial_messages(L, cid, d["trial_id"])
    if terminal:
        initial = get(L, cid, d["parent_decision_id"], "decision")
        first = get(L, cid, initial.data["model_receipt_id"], "planner")
        paid = [
            o
            for o in records(L, cid, "observation")
            if o.data["decision_id"] == initial.id and o.seq < receipt.seq
        ]
        if len(paid) != 1:
            raise ValueError(
                "Muse-only terminal receipt needs its preceding paid window"
            )
        expected = terminal_messages(
            L, cid, initial, first.data["raw_assistant"], paid[0]
        )
    if r["request"].get("messages") != expected:
        raise ValueError(
            "Muse-only request differs from the permitted trace and metadata"
        )
    parsed = parse_prediction(r["raw_assistant"], terminal=terminal)
    if parsed != r["prediction"] or any(
        d[key] != parsed[key] for key in ("decision", "p_ignite", "interval")
    ):
        raise ValueError(
            "Muse-only committed prediction differs from its recorded response"
        )
    if d["observe_s"] != (
        get(L, cid, d["parent_decision_id"], "decision").data["observe_s"]
        if terminal
        else parsed["observe_s"]
    ):
        raise ValueError("Muse-only committed watch differs from its recorded response")


def run_muse_only(L, campaign, fold, sealed_store, session):
    """Score one fixed no-tool comparator through every registered fold."""
    from translucent.intervention_chat import StopCampaign
    from translucent.recordings import NoRecording

    cid = campaign.id
    policy = None
    status, reason = "finished", "All held-out trials were committed and revealed"
    receipt_cursor = 0
    call_attempts = []

    def chat(messages):
        """Retain every attempt, including failures preceding a clean stop."""
        nonlocal receipt_cursor, call_attempts
        call_attempts = []
        try:
            return session.chat(messages)
        finally:
            attempts = getattr(session, "records", [])
            call_attempts = attempts[receipt_cursor:]
            for entry in call_attempts:
                record(L, cid, "planner", dict(phase="model_request", **entry))
            receipt_cursor = len(attempts)

    try:
        service = InterventionTools(L, cid, fold.id, sealed_store)
        policies = [
            p
            for p in records(L, cid, "policy")
            if p.data["name"] == "muse_only" and p.data["spec"]["family"] == "muse"
        ]
        if len(policies) != 1:
            raise ControllerError(
                "Campaign must register exactly one frozen Muse-only baseline"
            )
        policy = policies[0]
        while True:
            fold = service.fold
            revealed = {r.data["trial_id"] for r in records(L, cid, "reveal")}
            for tid in fold.data["train_ids"]:
                if tid not in revealed:
                    commit_decision(
                        L,
                        cid,
                        tid,
                        "training",
                        "abstain",
                        0.5,
                        [0, 1],
                        scored=False,
                        split=fold.data["split"],
                    )
                    reveal(L, cid, tid, sealed_store)
            fitted = fit_policy(L, cid, policy.id, fold.id)
            for tid in fold.data["test_ids"]:
                messages = trial_messages(L, cid, tid)
                response = chat(messages)
                receipt = _receipt(
                    L,
                    cid,
                    session,
                    tid,
                    policy.id,
                    fold.id,
                    messages,
                    response,
                    attempts=call_attempts,
                )
                value = receipt.data["prediction"]
                initial = commit_decision(
                    L,
                    cid,
                    tid,
                    policy.id,
                    **value,
                    split=fold.data["split"],
                    fit_id=fitted.id,
                    fold_id=fold.id,
                    model_receipt_id=receipt.id,
                )
                if value["decision"] == "observe":
                    paid = observe(L, cid, tid, policy.id, value["observe_s"])
                    messages = terminal_messages(L, cid, initial, response, paid)
                    response = chat(messages)
                    receipt = _receipt(
                        L,
                        cid,
                        session,
                        tid,
                        policy.id,
                        fold.id,
                        messages,
                        response,
                        parent=initial.id,
                        attempts=call_attempts,
                    )
                    value = deepcopy(receipt.data["prediction"])
                    value["observe_s"] = initial.data["observe_s"]
                    commit_decision(
                        L,
                        cid,
                        tid,
                        policy.id,
                        **value,
                        split=fold.data["split"],
                        fit_id=fitted.id,
                        fold_id=fold.id,
                        model_receipt_id=receipt.id,
                        parent_decision_id=initial.id,
                    )
                service._execute_baselines(tid)
                reveal(L, cid, tid, sealed_store)
            if service._advance()["done"]:
                break
    except StopCampaign as exc:
        status, reason = exc.status, exc.reason
    except InvalidModelResponse as exc:
        status, reason = "invalid_response", str(exc)
    except RecordingError as exc:
        status, reason = "recording_error", str(exc)
    except NoRecording as exc:
        status, reason = "replay_error", str(exc)
    except Exception as exc:
        status, reason = "controller_error", str(exc)
    decisions, revealed, incomplete = [], set(), []
    scores = []

    def record_stop():
        """Make a stop visible to score recomputation before any score is written."""
        record(
            L,
            cid,
            "planner",
            {
                "planner": "muse_only",
                "phase": "stopped",
                "status": status,
                "reason": reason,
                "incomplete_decision_ids": incomplete,
                "tool_calls": 0,
                "spent_usd": session.spent_usd,
                "recording_count": len(getattr(session, "records", [])),
            },
        )

    try:
        decisions = [d for d in records(L, cid, "decision") if d.data["scored"]]
        revealed = {r.data["trial_id"] for r in records(L, cid, "reveal")}
        incomplete = [d.id for d in decisions if d.data["trial_id"] not in revealed]
        record_stop()
        if policy is not None and not incomplete:
            scores = [
                score(L, cid, policy.id, split)
                for split in protocol(L, cid).data["splits"]
            ]
            for value in scores:
                record(L, cid, "score", value)
        L.emit(
            cid,
            "campaign.finished",
            "intervention",
            "Muse-only campaign ended",
            data={"status": status, "reason": reason, "spent_usd": session.spent_usd},
        )
    except Exception as exc:
        status, reason = "controller_error", str(exc)
        try:
            record_stop()
        except Exception:
            # Storage itself may be unavailable; the runner still returns the
            # controller status for the separately persisted replay summary.
            pass
    return {
        "campaign_id": cid,
        "policy_id": policy.id if policy else None,
        "status": status,
        "reason": reason,
        "scores": scores,
        "spent_usd": session.spent_usd,
        "tool_calls": 0,
        "recording_count": len(getattr(session, "records", [])),
        "incomplete_decision_ids": incomplete,
        "completed_trials": len(
            {
                d.data["trial_id"]
                for d in decisions
                if not d.data.get("parent_decision_id")
                and d.data["trial_id"] in revealed
            }
        ),
        "claim_available": status == "finished"
        and not incomplete
        and bool(scores)
        and all(s["n"] >= protocol(L, cid).data["min_trials"] for s in scores),
    }
