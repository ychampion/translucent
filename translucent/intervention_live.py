"""Live intervention planning through the same scoped, executable tools as replay.

The model can propose and screen strategies. Only the trusted controller can
measure traces, fit a strategy, commit its action, reveal an outcome and review
a claim. Each held-out fold starts with fresh model history.
"""

from __future__ import annotations

import json

from translucent.intervention import canonical, protocol, record, records
from translucent.intervention_chat import StopCampaign, assistant_message
from translucent.intervention_errors import RecordingError, ToolRejected
from translucent.intervention_models import FEATURES, POST_FEATURES, PolicySpec
from translucent.intervention_runtime import InterventionTools, tool_schemas
from translucent.recordings import NoRecording
from translucent.runtime import TEMPLATE


def system_prompt(config):
    """Describe the registered task without selecting a feature or policy."""
    return (
        "You plan a research campaign on recorded NIST battery experiments. "
        "At the recorded decision point the heater has just been cut. Decide "
        "whether stopping the heat is enough, or escalation is needed. You may "
        "stand down, escalate, abstain (charged as escalation), or observe one "
        "registered 30 or 60 second window and then stand down or escalate. "
        "Ignition during the watch is unsafe even if your final action is escalation. "
        "Every second watched has the registered cost. All forecasts are simulated; "
        "only recorded revealed outcomes are measured evidence. Do not claim an "
        "outcome under another heater-off time or efficacy of suppression.\n\n"
        "The protocol below fixes costs, primary/secondary endpoints, splits, "
        "minimum trial count and held-out cap K. K counts submitted agent "
        "strategies only; frozen baselines are comparators. Begin by recording "
        "at least three competing mechanistic hypotheses with distinct mechanism "
        "labels before proposing any policy. Link hypotheses to the policies that "
        "test them. Propose generic executable learning strategies; do not choose "
        "a strategy from a held-out result. For LOCO/LOFO register every strategy "
        "before any outcome is revealed, and reuse those frozen strategies in all "
        "folds. Call prepare_training after registration to commit non-scored "
        "training abstentions and reveal only the training subset. Evaluate and "
        "screen on revealed training folds; only screening survivors may be "
        "submitted, within K. Each new fold requires a fresh fit.\n\n"
        "Use predict for an executable world-model probability and interval, "
        "then commit_decision with exactly its declared action and probability. "
        "Commit before reveal. An observe decision must be committed before "
        "reading its paid window and must receive a final decision before reveal. "
        "Alternatively execute_fold runs the submitted executable strategies "
        "through the same primitive commits, observations and reveals; it cannot "
        "choose or change a policy. Never access another fold or an unpaid "
        "post-off sample. After completing a fold, use advance to move to the "
        "next registered fold. Prequential updates happen only after a reveal "
        "and require the recorded safety/calibration compatibility check.\n\n"
        "When all folds are complete, obtain scores, propose claims and respect "
        "the deterministic gate's verdicts. Write a final operating procedure "
        "only through write_procedure, citing supported claim ids and limits. "
        "An unsupported claim must never become an operating instruction. "
        "You may propose simulated next experiments that discriminate the "
        "recorded hypotheses. Use tool calls; text that looks like a tool call "
        "does not execute. There is a per-fold tool-call cap.\n\n"
        "Registered protocol: "
        + canonical(config).decode()
        + "\nGeneric executable policy schema: "
        + canonical(PolicySpec.model_json_schema()).decode()
        + "\nAvailable pre-off features (alphabetical, no preference): "
        + ", ".join(sorted(FEATURES + ("physics_prior",)))
        + "\nPaid-window features (require observation_s): "
        + ", ".join(sorted(POST_FEATURES))
    )


def provider_tools():
    """Convert the scoped tool catalogue to chat-completions function schemas."""
    return [
        dict(
            type="function",
            function=dict(
                name=t["name"],
                description=t["description"],
                parameters=t["inputSchema"],
            ),
        )
        for t in tool_schemas()
    ]


def _context(service):
    L, cid = service.L, service.cid
    submitted_ids = {s.data["policy_id"] for s in records(L, cid, "submission")}
    frozen = bool(records(L, cid, "reveal")) and (
        service.fold.data["split"] != "prequential"
        or any(d.data.get("scored") for d in records(L, cid, "decision"))
    )
    return dict(
        fold_id=service.fold_id,
        split=service.fold.data["split"],
        training_n=len(service.fold.data["train_ids"]),
        held_out_n=len(service.fold.data["test_ids"]),
        frozen_policies=[
            dict(id=p.id, **p.data)
            for p in records(L, cid, "policy")
            if p.data["kind"] == "agent"
        ],
        registration_frozen=frozen,
        submitted_agent_strategies=[
            dict(id=p.id, name=p.data["name"], spec=p.data["spec"])
            for p in records(L, cid, "policy")
            if p.data["kind"] == "agent" and p.id in submitted_ids
        ],
        frozen_comparators=[
            dict(id=p.id, name=p.data["name"])
            for p in records(L, cid, "policy")
            if p.data["kind"] == "baseline"
        ],
        hypotheses=[
            dict(id=h.id, text=h.data["text"], mechanism=h.data["mechanism"])
            for h in records(L, cid, "hypothesis")
        ],
        instruction=(
            "Registration is frozen. Do not re-record hypotheses or re-propose "
            "strategies. Reuse the submitted agent strategies above. This is a "
            "fresh fold: call prepare_training, then execute_fold, or fit and "
            "predict/commit the submitted strategies. Frozen comparators are "
            "read-only and cannot be submitted or changed."
            if frozen
            else "List this fold's trials and continue using only this scope. "
            "Frozen comparators are read-only and cannot be submitted or changed."
        ),
    )


def run_live(L, campaign, fold, sealed_store, session, step_cap=120):
    """Let a provider drive scoped tools; record every stop without inventing actions."""
    if step_cap <= 0:
        raise ValueError("The per-fold tool-call cap must be positive")
    try:
        return _run_live(L, campaign, fold, sealed_store, session, step_cap)
    except Exception as error:
        # Startup and finalization can fail before the scoped loop can record a
        # stop. A damaged ledger may also prevent recording the failure itself.
        reason = f"{type(error).__name__}: {error}"[:1000]
        try:
            record(
                L,
                campaign.id,
                "planner",
                dict(
                    phase="stop",
                    status="controller_error",
                    reason=reason,
                    fold_id=fold.id,
                    tool_calls=0,
                    spent_usd=session.spent_usd,
                    recording_count=len(session.records),
                ),
            )
            L.emit(
                campaign.id,
                "campaign.finished",
                "intervention",
                "Intervention controller stopped",
                data=dict(
                    status="controller_error",
                    reason=reason,
                    spent_usd=session.spent_usd,
                ),
            )
        except Exception:
            pass
        return dict(
            campaign_id=campaign.id,
            status="controller_error",
            reason=reason,
            spent_usd=session.spent_usd,
            tool_calls=0,
            procedure=None,
        )


def _run_live(L, campaign, fold, sealed_store, session, step_cap):
    """Execute one controller-owned planning loop inside the failure boundary."""
    service = InterventionTools(L, campaign.id, fold.id, sealed_store)
    config = protocol(L, campaign.id).data
    schemas = provider_tools()

    def fresh_messages():
        return [
            dict(role="system", content=system_prompt(config)),
            dict(role="user", content=canonical(_context(service)).decode()),
        ]

    messages = fresh_messages()
    steps = 0
    idle = 0
    total_calls = 0
    receipt_cursor = 0
    status, reason = "incomplete", "Planner did not finish a supported procedure"

    def save_requests():
        nonlocal receipt_cursor
        for entry in session.records[receipt_cursor:]:
            record(L, campaign.id, "planner", dict(phase="model_request", **entry))
        receipt_cursor = len(session.records)

    try:
        while True:
            if steps >= step_cap:
                status, reason = (
                    "step_cap",
                    f"Per-fold cap of {step_cap} tool calls reached",
                )
                break
            try:
                response = session.chat(messages, schemas)
            finally:
                save_requests()
            calls = response.get("tool_calls") or []
            messages.append(assistant_message(response))
            if not calls:
                idle += 1
                if idle >= 3:
                    status, reason = (
                        "stalled",
                        "Three model replies contained no executable tool call",
                    )
                    break
                garbled = bool(TEMPLATE.search(response.get("content") or ""))
                messages.append(
                    dict(
                        role="user",
                        content=(
                            "Your tool call arrived as plain text and did not run. "
                            "Call the tool again using the tool-call format."
                            if garbled
                            else "Continue by calling a tool. Finish only through "
                            "a supported write_procedure."
                        ),
                    )
                )
                continue
            idle = 0
            reset_history = False
            finished = False
            for call in calls:
                if steps >= step_cap:
                    status, reason = (
                        "step_cap",
                        f"Per-fold cap of {step_cap} tool calls reached",
                    )
                    finished = True
                    break
                steps += 1
                total_calls += 1
                old_fold = service.fold_id
                function = call.get("function") or {}
                name = function.get("name", "")
                arguments = function.get("arguments") or "{}"
                try:
                    arguments = (
                        json.loads(arguments)
                        if isinstance(arguments, str)
                        else arguments
                    )
                    if not isinstance(arguments, dict):
                        raise ToolRejected("Tool arguments must be a JSON object")
                except (json.JSONDecodeError, ToolRejected) as error:
                    result = dict(error=f"{type(error).__name__}: {error}"[:2000])
                else:
                    record(
                        L,
                        campaign.id,
                        "planner",
                        dict(
                            phase="tool_call",
                            fold_id=old_fold,
                            call=dict(tool=name, arguments=arguments),
                        ),
                    )
                    try:
                        if name == "propose_policy":
                            mechanisms = {
                                h.data["mechanism"]
                                for h in records(L, campaign.id, "hypothesis")
                            }
                            if len(mechanisms) < 3:
                                raise ToolRejected(
                                    "Record three competing mechanistic hypotheses "
                                    "before proposing policies"
                                )
                        result = service.call(name, arguments)
                    except ToolRejected as error:
                        result = dict(error=f"{type(error).__name__}: {error}"[:2000])
                if name != "write_procedure" or "error" in result:
                    record(
                        L,
                        campaign.id,
                        "planner",
                        dict(
                            phase="tool_result",
                            fold_id=old_fold,
                            tool=name,
                            result=result,
                        ),
                    )
                messages.append(
                    dict(
                        role="tool",
                        tool_call_id=call.get("id", ""),
                        content=canonical(result).decode(),
                    )
                )
                if service.fold_id != old_fold:
                    # A new held-out scope never sees the preceding fold's labels.
                    reset_history = True
                    steps = 0
                if name == "write_procedure" and "error" not in result:
                    status, reason = (
                        "finished",
                        "Supported operating procedure recorded",
                    )
                    finished = True
                    break
            if finished:
                break
            if reset_history:
                messages = fresh_messages()
    except StopCampaign as error:
        status, reason = error.status, error.reason
    except NoRecording as error:
        status, reason = "replay_error", str(error)
    except RecordingError as error:
        status, reason = "recording_error", str(error)
    except Exception as error:
        status, reason = "controller_error", f"{type(error).__name__}: {error}"[:1000]
    if status != "finished":
        try:
            record(
                L,
                campaign.id,
                "planner",
                dict(
                    phase="stop",
                    status=status,
                    reason=reason,
                    fold_id=service.fold_id,
                    tool_calls=total_calls,
                    spent_usd=session.spent_usd,
                    recording_count=len(session.records),
                ),
            )
            from translucent.intervention_bench import score

            for submission in records(L, campaign.id, "submission"):
                measured = score(
                    L,
                    campaign.id,
                    submission.data["policy_id"],
                    service.fold.data["split"],
                )
                record(L, campaign.id, "score", measured)
            L.emit(
                campaign.id,
                "campaign.finished",
                "intervention",
                "Intervention planner stopped",
                data=dict(status=status, reason=reason, spent_usd=session.spent_usd),
            )
        except Exception as error:
            status, reason = (
                "controller_error",
                f"{reason}; cannot record controller stop: {type(error).__name__}: {error}"[
                    :2000
                ],
            )
    try:
        procedure = next((r.data for r in records(L, campaign.id, "procedure")), None)
    except Exception as error:
        status, reason = ("controller_error", f"{type(error).__name__}: {error}"[:1000])
        procedure = None
    return dict(
        campaign_id=campaign.id,
        status=status,
        reason=reason,
        spent_usd=session.spent_usd,
        tool_calls=total_calls,
        procedure=procedure,
    )
