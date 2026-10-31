"""Scoped planner tools, exact replay and a scripted intervention research loop."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from translucent.intervention import (
    canonical,
    commit_decision,
    create,
    example,
    fit_policy,
    get,
    pre_off_trace,
    protocol,
    propose_policy,
    record,
    records,
    register_fold,
    reveal,
)
from translucent.intervention_models import (
    PolicySpec,
    bootstrap_predict,
    fit,
    predict,
    scripted_specs,
)
from translucent.intervention_gate import install as _install_gate_extension
from translucent.intervention_errors import ToolRejected

_install_gate_extension()


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TrialArgs(Args):
    trial_id: str


class TraceArgs(TrialArgs):
    observe_s: Literal[0, 30, 60] = 0
    policy_id: str | None = None


class PolicyArgs(Args):
    name: str = Field(min_length=1, max_length=200)
    spec: dict
    decision_rule: dict | None = None


class PolicyId(Args):
    policy_id: str


class PredictionArgs(PolicyId):
    trial_id: str
    observe_s: Literal[0, 30, 60] = 0


class CommitArgs(PredictionArgs):
    decision: Literal["stand_down", "escalate", "abstain", "observe"]
    p_ignite: float = Field(ge=0, le=1)
    interval: list[float] = Field(min_length=2, max_length=2)
    parent_decision_id: str | None = None


class HypothesisArgs(Args):
    text: str = Field(min_length=1, max_length=4000)
    policies: list[str]
    mechanism: Literal["surface", "self_heating", "soc"]


class ClaimArgs(Args):
    predicate: dict


class NextArgs(Args):
    limit: int = Field(default=3, ge=1, le=10)


class ForecastArgs(TrialArgs):
    heater_off_after_vent_s: float | None = Field(default=None, ge=0, le=600)


class ProcedureArgs(PolicyId):
    claim_ids: list[str]
    limits: list[str] = []


SCHEMAS = {
    "list_trials": Args,
    "get_trace": TraceArgs,
    "compute_features": TraceArgs,
    "propose_policy": PolicyArgs,
    "evaluate_policy": PolicyId,
    "submit_policy": PolicyId,
    "predict": PredictionArgs,
    "commit_decision": CommitArgs,
    "reveal": TrialArgs,
    "observe": PredictionArgs,
    "record_hypothesis": HypothesisArgs,
    "propose_claim": ClaimArgs,
    "score": PolicyId,
    "next_experiments": NextArgs,
    "write_procedure": ProcedureArgs,
    "advance": Args,
    "forecast_actions": ForecastArgs,
    "prepare_training": Args,
    "execute_fold": Args,
}
READ_TOOLS = {"list_trials", "get_trace", "compute_features", "predict", "score"}


def tool_schemas():
    return [
        dict(
            name=name,
            description={
                "list_trials": "List this fold only, with metadata and no sealed outcomes.",
                "get_trace": "Read pre-off samples, or a previously paid observation window.",
                "compute_features": "Compute generic features within the available observation window.",
                "evaluate_policy": "Fit and screen using revealed training data only; no held-out labels.",
                "propose_policy": "Register a generic learning strategy before any LOCO/LOFO outcome reveal; fitting happens later on revealed training data.",
                "submit_policy": "Submit a screened learning strategy; counts toward the registered cap.",
                "predict": "Get a world-model probability, bootstrap interval and a separate cost-based action.",
                "forecast_actions": "Simulate core/surface dynamics for stop, watch 30/60 s and an optional alternative delay; never outcome evidence.",
                "advance": "After revealing this held-out fold, advance to the next condition/format with fresh seals, or the next prequential trial.",
                "write_procedure": "Produce an operating procedure from supported policy-performance claims only.",
                "prepare_training": "After recording three hypotheses and registering candidate strategies, commit unscored training abstentions and reveal this fold's training labels in one batch. Never releases held-out labels.",
                "execute_fold": "Execute every submitted policy's frozen executable predictions and actions on this held-out fold, including paid windows, then reveal each trial. Returns scores; no policy changes or alternative actions are allowed.",
                "propose_claim": "Ask the gate to review a predicate. Required fields: policy_performance: type, policy_id, split, n, unsafe; policy_beats: type, a, b, split; policy_no_more_unsafe_fewer_missed: type, a, b, split. For comparisons a is the agent policy id and b the frozen baseline id (aliases policy_id and baseline_id accepted). Optional subset defaults to all; only recorded actions and real outcomes are evidence.",
            }.get(name, name.replace("_", " ")),
            inputSchema=cls.model_json_schema(),
            read_only=name in READ_TOOLS,
        )
        for name, cls in SCHEMAS.items()
    ]


def open_intervention(
    L,
    sealed_store,
    *,
    split="loco",
    condition=None,
    selected=None,
    min_trials=20,
    budget_usd=0,
):
    from translucent import systems

    nist = systems.get("nist-heat-interrupt")
    ingest = nist.ingest

    c = create(L, splits=[split], min_trials=min_trials, budget_usd=budget_usd)
    from translucent.intervention_physics import register_priors

    register_priors(L, c.id)
    trials = ingest(L, c.id, sealed_store, scope="planner", selected=selected)
    if not trials:
        raise ValueError("No eligible trials")
    if split == "prequential":
        train = []
        test = [min(trials, key=lambda t: t.data["exp"])]
    elif split == "lofo":
        fmt = (condition or {}).get("format", "21700")
        test = [t for t in trials if t.data["condition"]["format"] == fmt]
        train = [t for t in trials if t not in test]
    else:
        cond = condition or trials[0].data["condition"]
        test = [t for t in trials if t.data["condition"] == cond]
        train = [t for t in trials if t not in test]
    register_frozen_baselines(L, c.id)
    fold = register_fold(
        L, c.id, "planner", split, [t.id for t in train], [t.id for t in test]
    )
    return c, fold, Path(sealed_store)


def register_frozen_baselines(L, cid):
    """Register the benchmark's comparators without exposing candidate controls."""
    from translucent.intervention_bench import submit_policy
    from translucent.intervention_models import frozen_specs

    existing = {
        p.data["name"]: p
        for p in records(L, cid, "policy")
        if p.data["kind"] == "baseline"
    }
    for name, spec in frozen_specs().items():
        if name in existing:
            continue
        rule = (
            {"mode": "always_observe", "observe_s": 30}
            if name == "always_observe_30"
            else None
        )
        p = propose_policy(L, cid, name, spec, kind="baseline", decision_rule=rule)
        submit_policy(L, cid, p.id)


class InterventionTools:
    """No tool accepts a filesystem path, arbitrary training ids, or another fold's id."""

    def __init__(self, L, cid, fold_id, sealed_store):
        self.L, self.cid, self.fold_id, self._sealed_store = (
            L,
            cid,
            fold_id,
            Path(sealed_store),
        )
        self.fitted = {}

    @property
    def fold(self):
        return get(self.L, self.cid, self.fold_id, "fold")

    def _trial(self, tid):
        if tid not in set(self.fold.data["train_ids"] + self.fold.data["test_ids"]):
            raise ToolRejected("Trial is outside this planner scope")
        return get(self.L, self.cid, tid, "trial")

    def _policy(self, pid, *, agent_only=False):
        policies = {p.id: p for p in records(self.L, self.cid, "policy")}
        if pid not in policies:
            raise ToolRejected("Unknown policy id in this campaign")
        policy = policies[pid]
        if agent_only and policy.data["kind"] != "agent":
            raise ToolRejected(
                "Frozen baseline comparators cannot be submitted or changed by the planner"
            )
        return policy

    def _fit(self, pid):
        if pid in self.fitted:
            return self.fitted[pid]
        if self.fold.data["split"] == "prequential":
            updates = [
                r
                for r in records(self.L, self.cid, "update")
                if r.data["policy_id"] == pid
            ]
            if updates:
                applied = get(
                    self.L, self.cid, updates[-1].data["applied_fit_id"], "fit"
                )
                candidate = get(
                    self.L, self.cid, updates[-1].data["candidate_fit_id"], "fit"
                )
                if candidate.data["fold_id"] == self.fold_id:
                    self.fitted[pid] = applied
                    return applied
        found = [
            r
            for r in records(self.L, self.cid, "fit")
            if r.data["policy_id"] == pid and r.data["fold_id"] == self.fold_id
        ]
        if not found:
            raise ToolRejected("Evaluate this policy on revealed training data first")
        self.fitted[pid] = found[-1]
        return found[-1]

    def call(self, name, arguments):
        if name not in SCHEMAS:
            raise ToolRejected("Unknown intervention tool")
        try:
            a = SCHEMAS[name].model_validate(arguments).model_dump()
        except ValidationError as error:
            raise ToolRejected(str(error)) from error
        L, cid = self.L, self.cid
        if name not in READ_TOOLS and any(
            e.type == "campaign.finished" for e in L.events(cid)
        ):
            raise ToolRejected("Campaign is finished")
        if "trial_id" in a:
            self._trial(a["trial_id"])
        if a.get("policy_id") and a["policy_id"] != "training":
            self._policy(
                a["policy_id"],
                agent_only=name
                in ("evaluate_policy", "submit_policy", "commit_decision", "observe"),
            )
        if name == "list_trials":
            allowed = set(self.fold.data["train_ids"] + self.fold.data["test_ids"])
            # Keep planner metadata on an allowlist. Reconstruction and quality
            # flags can depend on the later trace and must stay in the ledger.
            return [
                dict(
                    id=t.id,
                    trial_key=t.data["trial_key"],
                    condition=t.data["condition"],
                    chemistry=t.data["chemistry"],
                    capacity_mAh=t.data["capacity_mAh"],
                    delay_after_vent_s=t.data["delay_after_vent_s"],
                    t_vent=t.data["t_vent"],
                    t_heater_off=t.data["t_heater_off"],
                    split=self.fold.data["split"],
                    role="training"
                    if t.id in self.fold.data["train_ids"]
                    else "held_out",
                )
                for t in records(L, cid, "trial")
                if t.id in allowed
            ]
        if name in ("get_trace", "compute_features"):
            if a["observe_s"]:
                from translucent.intervention_actions import observation_features

                if not a["policy_id"]:
                    raise ToolRejected("A paid observation needs its policy id")
                extra = observation_features(
                    L, cid, a["trial_id"], a["policy_id"], a["observe_s"]
                )
                if name == "compute_features":
                    return extra
                return next(
                    o.data["trace"]
                    for o in records(L, cid, "observation")
                    if o.data["trial_id"] == a["trial_id"]
                    and o.data["policy_id"] == a["policy_id"]
                    and o.data["observe_s"] == a["observe_s"]
                )
            return (
                pre_off_trace(L, cid, a["trial_id"])
                if name == "get_trace"
                else example(L, cid, a["trial_id"])["features"]
            )
        if name == "propose_policy":
            if self.fold.data["split"] in ("loco", "lofo") and records(
                L, cid, "reveal"
            ):
                raise ToolRejected(
                    "Register LOCO/LOFO learning strategies before any outcome is revealed"
                )
            from translucent.intervention_actions import DecisionRule

            try:
                PolicySpec.model_validate(a["spec"])
                if a["decision_rule"] is not None:
                    DecisionRule.model_validate(a["decision_rule"])
            except ValidationError as error:
                raise ToolRejected(str(error)) from error
            if a["spec"].get("family") == "muse":
                raise ToolRejected(
                    "Muse-only is a frozen comparator and requires provider receipts"
                )
            return propose_policy(
                L, cid, a["name"], a["spec"], decision_rule=a["decision_rule"]
            ).model_dump(mode="json")
        if name == "prepare_training":
            mechanisms = {
                r.data.get("mechanism") for r in records(L, cid, "hypothesis")
            }
            if len(mechanisms) < 3 or not any(
                p.data["kind"] == "agent" for p in records(L, cid, "policy")
            ):
                raise ToolRejected(
                    "Record three hypotheses and candidate strategies first"
                )
            revealed = {r.data["trial_id"] for r in records(L, cid, "reveal")}
            feedback = []
            for tid in self.fold.data["train_ids"]:
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
                        split=self.fold.data["split"],
                    )
                    reveal(L, cid, tid, self._sealed_store)
                row = next(
                    r for r in records(L, cid, "reveal") if r.data["trial_id"] == tid
                )
                feedback.append(dict(trial_id=tid, outcome=row.data["outcome"]))
            return dict(training_n=len(feedback), feedback=feedback)
        if name == "execute_fold":
            return self._execute_fold()
        if name == "forecast_actions":
            from translucent.intervention_physics import cached_forecast

            t = self._trial(a["trial_id"])
            trace = pre_off_trace(L, cid, t.id)
            forecast = cached_forecast(
                trace,
                t.data["condition"],
                vent=t.data["t_vent"],
                delay=a["heater_off_after_vent_s"],
            )
            receipt = record(
                L,
                cid,
                "planner",
                dict(
                    planner="trusted two-node solver",
                    call={"tool": name, "arguments": a},
                    trial_id=t.id,
                    source_artifact=t.data["series_artifact"],
                    forecast=forecast,
                    simulated=True,
                ),
            )
            return dict(receipt_id=receipt.id, **forecast)
        if name == "record_hypothesis":
            for pid in a["policies"]:
                self._policy(pid, agent_only=True)
            world = {
                "surface": PolicySpec(family="logistic", features=["format", "level"]),
                "self_heating": PolicySpec(family="two_node"),
                "soc": PolicySpec(
                    family="logistic", features=["format", "soc", "delay_after_vent_s"]
                ),
            }[a["mechanism"]]
            return record(
                L, cid, "hypothesis", {**a, "world_model": world.model_dump()}
            ).model_dump(mode="json")
        if name == "evaluate_policy":
            from translucent.intervention_bench import screen_policy

            revealed = {r.data["trial_id"] for r in records(L, cid, "reveal")}
            if not set(self.fold.data["train_ids"]) <= revealed:
                raise ToolRejected("Call prepare_training before fitting this fold")

            previous = [
                r
                for r in records(L, cid, "fit")
                if r.data["policy_id"] == a["policy_id"]
            ]
            updates = [
                r
                for r in records(L, cid, "update")
                if r.data["policy_id"] == a["policy_id"]
            ]
            old = (
                get(L, cid, updates[-1].data["applied_fit_id"], "fit")
                if updates
                else previous[-1]
                if previous
                else None
            )
            f = fit_policy(L, cid, a["policy_id"], self.fold_id)
            s = screen_policy(L, cid, a["policy_id"], self.fold_id, f)
            candidate = f
            if self.fold.data["split"] == "prequential":
                from translucent.intervention_actions import checked_update

                f = checked_update(L, cid, a["policy_id"], f, old, fold_id=self.fold_id)
            self.fitted[a["policy_id"]] = f
            return dict(
                fit_id=f.id,
                screen_id=s.id,
                survived=s.data["survived"],
                reason=s.data["reason"],
                candidate_fit_id=candidate.id,
                update_applied=f.id == candidate.id,
                selected_world_model=f.data["model"]["spec"],
                training_n=len(f.data["train_ids"]),
            )
        if name == "advance":
            return self._advance()
        if name == "submit_policy":
            from translucent.intervention_bench import submit_policy

            return submit_policy(L, cid, a["policy_id"]).model_dump(mode="json")
        if name == "predict":
            return self._predict(**a)
        if name == "commit_decision":
            training = a["trial_id"] in self.fold.data["train_ids"]
            f = None if training else self._fit(a["policy_id"])
            if not training:
                paid = a["observe_s"] if a["parent_decision_id"] else 0
                expected = self._predict(a["policy_id"], a["trial_id"], paid)
                world = expected["world_model"]
                rule = expected["decision_policy"]
                if (
                    a["p_ignite"] != world["p_ignite"]
                    or a["interval"] != world["interval"]
                ):
                    raise ToolRejected(
                        "Commit the executable world-model prediction; other policies cannot lend paid observations"
                    )
                if (a["decision"], a["observe_s"]) != (
                    rule["action"],
                    paid or rule["observe_s"],
                ):
                    raise ToolRejected("Commit the declared decision rule action")
            return commit_decision(
                L,
                cid,
                a["trial_id"],
                a["policy_id"],
                a["decision"],
                a["p_ignite"],
                a["interval"],
                scored=not training,
                split=self.fold.data["split"],
                fit_id=f.id if f else None,
                fold_id=self.fold_id,
                observe_s=a["observe_s"],
                parent_decision_id=a["parent_decision_id"],
            ).model_dump(mode="json")
        if name == "observe":
            from translucent.intervention_actions import observe

            return observe(
                L, cid, a["trial_id"], a["policy_id"], a["observe_s"]
            ).model_dump(mode="json")
        if name == "reveal":
            self._execute_baselines(a["trial_id"])
            return reveal(L, cid, a["trial_id"], self._sealed_store).model_dump(
                mode="json"
            )
        if name == "score":
            from translucent.intervention_bench import score

            return score(L, cid, a["policy_id"], self.fold.data["split"])
        if name == "propose_claim":
            from translucent.intervention_bench import propose_claim
            from translucent import gate

            claim = propose_claim(L, cid, a["predicate"])
            r = gate.review(L, claim.id)
            return dict(r.data)
        if name == "next_experiments":
            return propose_experiments(
                L, cid, self.fold.data["train_ids"], limit=a["limit"]
            )
        if name == "write_procedure":
            return write_procedure(L, cid, **a)
        raise ToolRejected("Unknown intervention action")

    def _ensure_baseline_fit(self, policy_id):
        """Fit a frozen comparator on this fold's revealed training rows only."""
        if policy_id in self.fitted:
            return self.fitted[policy_id]
        previous = [
            r
            for r in records(self.L, self.cid, "update")
            if r.data["policy_id"] == policy_id
        ]
        prior_fits = [
            r
            for r in records(self.L, self.cid, "fit")
            if r.data["policy_id"] == policy_id
            and get(self.L, self.cid, r.data["fold_id"], "fold").data["scope"]
            != "baseline_registration"
        ]
        old = (
            get(self.L, self.cid, previous[-1].data["applied_fit_id"], "fit")
            if previous
            else prior_fits[-1]
            if prior_fits
            else None
        )
        fitted = fit_policy(self.L, self.cid, policy_id, self.fold_id)
        if self.fold.data["split"] == "prequential":
            from translucent.intervention_actions import checked_update

            fitted = checked_update(
                self.L, self.cid, policy_id, fitted, old, fold_id=self.fold_id
            )
        self.fitted[policy_id] = fitted
        return fitted

    def _execute_policy_trial(self, policy_id, trial_id):
        """Execute one immutable specification, preserving paid observation receipts."""
        from translucent.intervention_actions import observe

        L, cid = self.L, self.cid
        existing = [
            d
            for d in records(L, cid, "decision")
            if d.data["trial_id"] == trial_id
            and d.data["policy_id"] == policy_id
            and d.data["scored"]
        ]
        initial = next(
            (d for d in existing if not d.data.get("parent_decision_id")), None
        )
        if initial is None:
            prediction = self._predict(policy_id, trial_id)
            world, action = prediction["world_model"], prediction["decision_policy"]
            initial = commit_decision(
                L,
                cid,
                trial_id,
                policy_id,
                action["action"],
                world["p_ignite"],
                world["interval"],
                fit_id=self._fit(policy_id).id,
                fold_id=self.fold_id,
                split=self.fold.data["split"],
                observe_s=action["observe_s"],
            )
        if initial.data["decision"] != "observe" or any(
            d.data.get("parent_decision_id") == initial.id for d in existing
        ):
            return
        window = initial.data["observe_s"]
        paid = any(
            o.data["decision_id"] == initial.id for o in records(L, cid, "observation")
        )
        if not paid:
            observe(L, cid, trial_id, policy_id, window)
        prediction = self._predict(policy_id, trial_id, window)
        commit_decision(
            L,
            cid,
            trial_id,
            policy_id,
            prediction["decision_policy"]["action"],
            prediction["world_model"]["p_ignite"],
            prediction["world_model"]["interval"],
            fit_id=initial.data["fit_id"],
            fold_id=initial.data["fold_id"],
            split=initial.data["split"],
            observe_s=window,
            parent_decision_id=initial.id,
        )

    def _execute_baselines(self, trial_id):
        """Commit all available frozen comparators before releasing a held-out label."""
        if trial_id not in self.fold.data["test_ids"]:
            return
        for submission in records(self.L, self.cid, "submission"):
            policy = get(self.L, self.cid, submission.data["policy_id"], "policy")
            if (
                policy.data["kind"] != "baseline"
                or policy.data["spec"]["family"] == "muse"
            ):
                continue
            self._ensure_baseline_fit(policy.id)
            self._execute_policy_trial(policy.id, trial_id)

    def _execute_fold(self):
        """Execute submitted candidates and frozen comparators before each reveal."""
        L, cid = self.L, self.cid
        policy_ids = [
            r.data["policy_id"]
            for r in records(L, cid, "submission")
            if get(L, cid, r.data["policy_id"], "policy").data["kind"] == "agent"
        ]
        if not policy_ids:
            raise ToolRejected("Screen and submit at least one executable policy first")
        for pid in policy_ids:
            if not any(
                f.data["policy_id"] == pid and f.data["fold_id"] == self.fold_id
                for f in records(L, cid, "fit")
            ):
                self.call("evaluate_policy", {"policy_id": pid})
            self._fit(pid)
        revealed = {r.data["trial_id"] for r in records(L, cid, "reveal")}
        completed = 0
        for tid in self.fold.data["test_ids"]:
            if tid in revealed:
                continue
            for pid in policy_ids:
                self._execute_policy_trial(pid, tid)
            self._execute_baselines(tid)
            reveal(L, cid, tid, self._sealed_store)
            completed += 1
        from translucent.intervention_bench import score

        all_ids = [r.data["policy_id"] for r in records(L, cid, "submission")]
        return dict(
            completed_trials=completed,
            scores=[score(L, cid, pid, self.fold.data["split"]) for pid in all_ids],
        )

    def _advance(self):
        L, cid = self.L, self.cid
        current = self.fold
        split = current.data["split"]
        revealed = {r.data["trial_id"] for r in records(L, cid, "reveal")}
        if not set(current.data["test_ids"]) <= revealed:
            raise ToolRejected(
                "Commit and reveal the current held-out fold before advancing"
            )
        trials = [
            get(L, cid, i, "trial")
            for i in current.data["train_ids"] + current.data["test_ids"]
        ]
        if split == "prequential":
            # The shared prequential scope also contains trials not in the current prefix.
            trials = [
                r
                for r in records(L, cid, "trial")
                if r.data["scope"] == current.data["scope"]
            ]
            pending = sorted(
                (t for t in trials if t.id not in revealed), key=lambda t: t.data["exp"]
            )
            if not pending:
                return dict(done=True)
            test = [pending[0]]
            train = sorted(
                (t for t in trials if t.data["exp"] < test[0].data["exp"]),
                key=lambda t: t.data["exp"],
            )
            if any(t.id not in revealed for t in train):
                raise ToolRejected("Prequential history is incomplete")
            scope = current.data["scope"]
        else:

            def key(t):
                c = t.data["condition"]
                return (c["format"], c["soc"]) if split == "loco" else (c["format"],)

            folds = [f for f in records(L, cid, "fold") if f.data["split"] == split]
            completed = {
                key(get(L, cid, f.data["test_ids"][0], "trial"))
                for f in folds
                if f.data["test_ids"]
            }
            pending = sorted({key(t) for t in trials} - completed)
            if not pending:
                return dict(done=True)
            from translucent import systems

            nist = systems.get("nist-heat-interrupt")
            ingest = nist.ingest

            scope = f"planner:{split}:{len(folds)}"
            # Registration fixes the trial universe. New scopes get independent salts;
            # fitting and executable commitments cannot reuse another fold's labels.
            fresh = ingest(
                L,
                cid,
                self._sealed_store,
                scope=scope,
                selected={t.data["exp"] for t in trials},
            )
            test = [t for t in fresh if key(t) == pending[0]]
            train = [t for t in fresh if key(t) != pending[0]]
        f = register_fold(
            L, cid, scope, split, [t.id for t in train], [t.id for t in test]
        )
        self.fold_id = f.id
        self.fitted.clear()
        return dict(
            done=False,
            fold_id=f.id,
            trial_id=test[0].id,
            training_n=len(train),
            held_out_n=len(test),
        )

    def _predict(self, policy_id, trial_id, observe_s=0):
        from translucent.intervention_actions import decide, observation_features

        p = get(self.L, self.cid, policy_id, "policy")
        f = self._fit(policy_id)
        s = p.data["spec"]
        row = example(self.L, self.cid, trial_id, physics="physics_prior" in str(s))
        if observe_s:
            required = f.data["model"]["spec"].get("observation_s", 0)
            if required and observe_s != required:
                raise ToolRejected(
                    "Observation duration differs from the fitted world model"
                )
            extra = observation_features(
                self.L, self.cid, trial_id, policy_id, observe_s
            )
            row = {**row, "features": {**row["features"], **extra}, "observed": True}
        training = [
            example(
                self.L,
                self.cid,
                i,
                training=True,
                post_s=s.get("observation_s", 0),
                physics="physics_prior" in str(s),
            )
            for i in f.data["train_ids"]
        ]
        probability, h, interval = bootstrap_predict(f.data["model"], training, row)
        decision = decide(
            probability,
            interval,
            p.data["decision_rule"],
            protocol(self.L, self.cid).data["costs"],
            hard_prediction=h,
            terminal=bool(observe_s),
            world_model=f.data["model"],
        )
        return dict(
            world_model=dict(
                p_ignite=probability, interval=interval, simulated=p.data["simulated"]
            ),
            decision_policy=decision,
        )


def _entropy(p):
    return -sum(v * np.log2(v) for v in (p, 1 - p) if v > 0)


def register_hypotheses(L, cid, policies):
    specs = {
        "surface": PolicySpec(family="logistic", features=["format", "level"]),
        "self_heating": PolicySpec(family="two_node"),
        "soc": PolicySpec(
            family="logistic", features=["format", "soc", "delay_after_vent_s"]
        ),
    }
    texts = {
        "surface": "Surface temperature tracks internal state",
        "self_heating": "Self-heating can continue after external heating stops",
        "soc": "Stored energy through SOC controls residual ignition risk",
    }
    return [
        record(
            L,
            cid,
            "hypothesis",
            dict(
                mechanism=k,
                text=texts[k],
                policies=policies,
                world_model=s.model_dump(),
            ),
        )
        for k, s in specs.items()
    ]


def propose_experiments(L, cid, training_ids, *, limit=3):
    """Expected information about the hypothesis identity, using equal prior weights."""
    from translucent import systems

    nist = systems.get("nist-heat-interrupt")
    load_dataset, VENT_MEANS = nist.load_trials, nist.condition_grid()
    from translucent.intervention_physics import continuous_prior, two_node_forecast

    hypotheses = records(L, cid, "hypothesis")
    bymechanism = {h.data["mechanism"]: h for h in hypotheses}
    if set(bymechanism) != {"surface", "self_heating", "soc"}:
        raise ValueError("Record three competing mechanistic hypotheses first")
    rows = [example(L, cid, i, training=True) for i in training_ids]
    surface = fit(PolicySpec(family="logistic", features=["format", "level"]), rows)
    soc_model = fit(
        PolicySpec(family="logistic", features=["format", "soc", "delay_after_vent_s"]),
        rows,
    )
    proposals = []
    for fmt in ("18650", "21700"):
        for soc in (25, 50, 75, 100):
            trial = next(
                t
                for t in load_dataset()
                if t["format"] == fmt
                and t["soc"] == soc
                and t["delay_after_vent_s"] is None
                and t["t"]
            )
            vent = VENT_MEANS[(fmt, soc)]
            n = int(vent) + 1
            for delay in (
                (60, 120, 180, 240, 300)
                if fmt == "18650"
                else (0, 5, 10, 20, 40, 80, 120)
            ):
                forecast = two_node_forecast(
                    trial["t"][:n],
                    trial["y"][:n],
                    continuous_prior(fmt, soc),
                    vent=vent,
                    delay=delay,
                )
                physics = forecast["actions"]["heater_off_after_vent"]["p_ignite"]
                temp = forecast["actions"]["heater_off_after_vent"][
                    "surface_temperature_at_off_C"
                ]
                row = dict(
                    condition=dict(format=fmt, soc=soc),
                    features={
                        "format": float(fmt == "21700"),
                        "level": temp,
                        "soc": soc,
                        "delay_after_vent_s": delay,
                    },
                )
                ps = {
                    "surface": predict(surface, row)[0],
                    "self_heating": physics,
                    "soc": predict(soc_model, row)[0],
                }
                mean = float(np.mean(list(ps.values())))
                information = float(
                    _entropy(mean) - np.mean([_entropy(p) for p in ps.values()])
                )
                proposals.append(
                    dict(
                        condition=row["condition"],
                        heater_off_after_vent_s=delay,
                        p_ignite=mean,
                        interval=[min(ps.values()), max(ps.values())],
                        disagreement_bits=information,
                        per_hypothesis=[
                            dict(hypothesis_id=bymechanism[k].id, p_ignite=v)
                            for k, v in ps.items()
                        ],
                        why="Expected reduction in uncertainty about three equally weighted hypotheses (Bernoulli Jensen-Shannon divergence)",
                        status="proposed",
                        simulated=True,
                        schedule=f"For human review: heater on at 41 W; detect vent; heater off at vent + {delay} s; watch for at least 400 s; re-heat to render safe only after that observation period.",
                        limits=[
                            "Alternative delay is unobserved",
                            "Core state and proposed surface trace are simulated",
                            "Schedule is proposed, never executed",
                        ],
                    )
                )
    proposals.sort(
        key=lambda x: (
            -x["disagreement_bits"],
            x["condition"]["format"],
            x["condition"]["soc"],
            x["heater_off_after_vent_s"],
        )
    )
    design = record(
        L,
        cid,
        "planner",
        dict(
            planner="trusted mechanism discrimination",
            call={"tool": "next_experiments", "arguments": {"limit": limit}},
            training_ids=training_ids,
            reveal_ids=[r["reveal_id"] for r in rows],
            hypothesis_models={"surface": surface, "soc": soc_model},
            physics_prior_ids=[r.id for r in records(L, cid, "physics_prior")],
            candidates=proposals,
            ranking="descending Bernoulli Jensen-Shannon divergence; equal hypothesis weights",
            simulated=True,
        ),
    )
    proposals = [{**p, "design_id": design.id} for p in proposals]
    return [
        dict(id=r.id, **r.data)
        for r in [record(L, cid, "next_experiment", p) for p in proposals[:limit]]
    ]


def write_procedure(L, cid, policy_id, claim_ids, limits=None):
    p = get(L, cid, policy_id, "policy")
    supported = []
    for claim_id in claim_ids:
        claim = get(L, cid, claim_id, "claim")
        reviews = [
            r for r in records(L, cid, "review") if r.data["claim_id"] == claim_id
        ]
        predicate = claim.data["predicate"]
        if not reviews or reviews[-1].data["verdict"] != "supported":
            raise ValueError("Procedure may cite only supported claims")
        if (
            predicate.get("policy_id") == policy_id
            and predicate["type"] == "policy_performance"
        ):
            supported.append(reviews[-1].data["computed"])
    if not supported:
        raise ValueError(
            "Procedure needs supported performance evidence for its policy"
        )
    from translucent import systems

    nist = systems.get("nist-heat-interrupt")
    register_visual_evidence = nist.register_closing_evidence

    register_visual_evidence(L, cid)
    config = protocol(L, cid).data
    lag = config.get("heater_off_lag_s", 10)
    trial_count = len({t.data["trial_key"] for t in records(L, cid, "trial")})
    fixed_limits = [
        "Factual recorded heater-off only; suppression efficacy and alternative stop times were not observed",
        f"{trial_count} interrupted traces in one NIST study; a case study, not a validated fire-safety procedure",
        f"One surface thermocouple; internal state unmeasured; event reconstruction uses a {lag:g} s lag",
        "Censored rescues and inferred heater-off times; no indefinite stability claim",
        "LOFO is a known transfer failure; no packs, other cell makes, powers or abuse modes",
        "A supported performance count describes observed errors; it does not prove a safe stand-down guarantee",
    ]
    evidence = "; ".join(
        f"{s['unsafe']} unsafe, {s['missed']} missed rescues over {s['n']} held-out trials ({s['split']}); "
        f"mean decision cost {s['mean_cost']:.6g}"
        for s in supported
    )
    rule = p.data["decision_rule"]
    instructions = (
        f"At recorded heater-off, apply the declared {p.data['name']} world model using only available observations. "
        "Apply its registered decision rule and action costs. If it selects observation, commit the 30 or 60 s "
        "watch, use only the paid recorded samples, then commit stand-down or escalation before revealing the outcome. "
        "Escalation is a risk classification; its physical efficacy was not measured. "
    )
    data = dict(
        text=instructions + "Recorded evidence: " + evidence + ".",
        rule={
            "at": "recorded heater-off",
            "policy_id": policy_id,
            "world_model": p.data["world_model"],
            "decision_rule": rule,
            "costs": config["costs"],
            "observe_options": config["observe_options"],
        },
        claim_ids=claim_ids,
        limits=fixed_limits + (limits or []),
    )
    record(L, cid, "procedure", data)
    L.emit(
        cid,
        "campaign.finished",
        "intervention",
        "Intervention operating procedure recorded",
        data={"status": "finished", "answer": data["text"], "claim_ids": claim_ids},
    )
    return data


def save_planner_recording(store, messages, tools, response):
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    request = dict(messages=messages, tools=tools)
    sha = hashlib.sha256(canonical(request)).hexdigest()
    path = store / (sha + ".json")
    path.write_bytes(canonical(dict(request=request, response=response)))
    return path


class RecordedPlanner:
    def __init__(self, store):
        self.store = Path(store)

    def next(self, messages, tools):
        from translucent.recordings import NoRecording

        request = dict(messages=messages, tools=tools)
        sha = hashlib.sha256(canonical(request)).hexdigest()
        path = self.store / (sha + ".json")
        if not path.is_file():
            raise NoRecording(
                "No exact intervention planner recording; no provider called"
            )
        r = json.loads(path.read_text())
        if r["request"] != request:
            raise ValueError("Planner recording input mismatch")
        return r["response"]


class RecordingPlanner:
    """Wrap an injected MeteredMeta-compatible client; the provider owns budget reservations."""

    def __init__(self, client, model, store):
        self.client, self.model, self.store = client, model, Path(store)

    def next(self, messages, tools):
        functions = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["inputSchema"],
                },
            }
            for t in tools
        ]
        reply = self.client.chat(
            model=self.model,
            messages=messages,
            max_tokens=6000,
            tools=functions,
            tool_choice="auto",
        )
        message = reply.choices[0].message
        if message.tool_calls:
            call = message.tool_calls[0].function
            response = dict(tool=call.name, arguments=json.loads(call.arguments))
        else:
            response = {"done": True}
        save_planner_recording(self.store, messages, tools, response)
        return response


class ScriptedPlanner:
    """A generator with no ledger, dataset loader, labels or privileged controller access."""

    def __init__(self):
        self.program = self._program()
        self.started = False

    def next(self, messages, tools):
        try:
            if not self.started:
                self.started = True
                return next(self.program)
            result = json.loads(messages[-1]["content"])
            return self.program.send(result)
        except StopIteration:
            return {"done": True}

    def _program(self):
        trials = yield {"tool": "list_trials", "arguments": {}}
        first = trials[0]["id"]
        yield {"tool": "get_trace", "arguments": {"trial_id": first}}
        yield {"tool": "compute_features", "arguments": {"trial_id": first}}
        yield {"tool": "forecast_actions", "arguments": {"trial_id": first}}
        policies = []
        for name, spec in scripted_specs().items():
            p = yield {
                "tool": "propose_policy",
                "arguments": {"name": name, "spec": spec.model_dump()},
            }
            policies.append(p["id"])
        for mechanism, text in [
            ("surface", "Surface temperature tracks internal state"),
            ("self_heating", "Self-heating can continue after external heating stops"),
            ("soc", "Stored energy through SOC controls ignition"),
        ]:
            yield {
                "tool": "record_hypothesis",
                "arguments": dict(text=text, mechanism=mechanism, policies=policies),
            }
        for t in trials:
            if t["role"] != "training":
                continue
            yield {
                "tool": "commit_decision",
                "arguments": dict(
                    trial_id=t["id"],
                    policy_id="training",
                    decision="abstain",
                    p_ignite=0.5,
                    interval=[0, 1],
                ),
            }
            yield {"tool": "reveal", "arguments": {"trial_id": t["id"]}}
        for pid in policies:
            yield {"tool": "evaluate_policy", "arguments": {"policy_id": pid}}
            yield {"tool": "submit_policy", "arguments": {"policy_id": pid}}
        prequential = trials[0]["split"] == "prequential"
        while True:
            for t in trials:
                if t["role"] != "held_out":
                    continue
                for pid in policies:
                    prediction = yield {
                        "tool": "predict",
                        "arguments": dict(trial_id=t["id"], policy_id=pid),
                    }
                    world = prediction["world_model"]
                    action = prediction["decision_policy"]
                    committed = yield {
                        "tool": "commit_decision",
                        "arguments": dict(
                            trial_id=t["id"],
                            policy_id=pid,
                            decision=action["action"],
                            p_ignite=world["p_ignite"],
                            interval=world["interval"],
                            observe_s=action["observe_s"],
                        ),
                    }
                    if action["action"] == "observe":
                        seconds = action["observe_s"]
                        yield {
                            "tool": "observe",
                            "arguments": dict(
                                trial_id=t["id"], policy_id=pid, observe_s=seconds
                            ),
                        }
                        final = yield {
                            "tool": "predict",
                            "arguments": dict(
                                trial_id=t["id"], policy_id=pid, observe_s=seconds
                            ),
                        }
                        world = final["world_model"]
                        terminal = final["decision_policy"]
                        yield {
                            "tool": "commit_decision",
                            "arguments": dict(
                                trial_id=t["id"],
                                policy_id=pid,
                                decision=terminal["action"],
                                p_ignite=world["p_ignite"],
                                interval=world["interval"],
                                observe_s=seconds,
                                parent_decision_id=committed["id"],
                            ),
                        }
                yield {"tool": "reveal", "arguments": {"trial_id": t["id"]}}
            advanced = yield {"tool": "advance", "arguments": {}}
            if advanced["done"]:
                break
            trials = yield {"tool": "list_trials", "arguments": {}}
            if not prequential:
                for t in trials:
                    if t["role"] != "training":
                        continue
                    yield {
                        "tool": "commit_decision",
                        "arguments": dict(
                            trial_id=t["id"],
                            policy_id="training",
                            decision="abstain",
                            p_ignite=0.5,
                            interval=[0, 1],
                        ),
                    }
                    yield {"tool": "reveal", "arguments": {"trial_id": t["id"]}}
            for pid in policies:
                yield {"tool": "evaluate_policy", "arguments": {"policy_id": pid}}
        claims = []
        for pid in policies:
            s = yield {"tool": "score", "arguments": {"policy_id": pid}}
            claim = yield {
                "tool": "propose_claim",
                "arguments": {
                    "predicate": dict(
                        type="policy_performance",
                        policy_id=pid,
                        split=s["split"],
                        subset="all",
                        n=s["n"],
                        unsafe=s["unsafe"],
                    )
                },
            }
            if claim["verdict"] == "supported":
                claims.append((pid, claim["claim_id"]))
        yield {"tool": "next_experiments", "arguments": {"limit": 3}}
        if claims:
            pid, claim_id = claims[0]
            yield {
                "tool": "write_procedure",
                "arguments": dict(policy_id=pid, claim_ids=[claim_id]),
            }


class InterventionCampaigner:
    def __init__(self, L, campaign, fold, sealed_store, planner=None):
        self.L, self.c = L, campaign
        self.service = InterventionTools(L, campaign.id, fold.id, sealed_store)
        self.planner = planner or ScriptedPlanner()

    def run(self, max_steps=4000):
        messages = [
            {
                "role": "system",
                "content": self.c.objective
                + " Use only this scope. Compare mechanistic hypotheses, screen on revealed training data, commit before reveal, and cite only supported gate claims.",
            }
        ]
        schemas = tool_schemas()
        for _ in range(max_steps):
            response = self.planner.next(messages, schemas)
            if response.get("done"):
                break
            record(
                self.L,
                self.c.id,
                "planner",
                dict(
                    context_sha256=hashlib.sha256(canonical(messages)).hexdigest(),
                    call=response,
                ),
            )
            result = self.service.call(response["tool"], response.get("arguments", {}))
            messages.extend(
                [
                    {"role": "assistant", "content": json.dumps(response)},
                    {"role": "user", "content": json.dumps(result)},
                ]
            )
            if response["tool"] == "write_procedure":
                break
        return dict(
            procedure=next(
                (r.data for r in records(self.L, self.c.id, "procedure")), None
            ),
            hypotheses=[r.data for r in records(self.L, self.c.id, "hypothesis")],
            policies=[r.data for r in records(self.L, self.c.id, "policy")],
            next_experiments=[
                r.data for r in records(self.L, self.c.id, "next_experiment")
            ],
        )
