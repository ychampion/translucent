"""World-model probabilities and decision rules have separate contracts and histories."""

from __future__ import annotations

import json
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_COSTS = {
    "unsafe": 100.0,
    "watch_ignition": 100.0,
    "watch_per_second": 0.05,
    "unnecessary_escalation": 10.0,
    "abstain": 10.0,
}


class DecisionRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    mode: Literal["cost", "classification", "always_observe"] = "cost"
    threshold: float = Field(default=0.5, ge=0, le=1)
    observe_s: Literal[0, 30, 60] = 0
    abstain_band: tuple[float, float] | None = None

    @model_validator(mode="after")
    def valid_band(self):
        if (
            self.abstain_band is not None
            and not 0 <= self.abstain_band[0] <= self.abstain_band[1] <= 1
        ):
            raise ValueError(
                "Abstention requires a declared probability band within [0,1]"
            )
        return self


def choose_action(p, interval, costs, *, options=None, watch_risk=None):
    """Bayes action with explicit costs. Forecasts used for VOI remain simulated.

    The observation estimate is an explicitly optimistic information bound.
    Training-fold CV measures the actual continuation errors before submission.
    """
    if not 0 <= interval[0] <= p <= interval[1] <= 1:
        raise ValueError("Invalid world-model probability")
    options = options or ["stand_down", "escalate", "observe_30", "observe_60"]
    expected = {
        "stand_down": p * costs["unsafe"],
        "escalate": (1 - p) * costs["unnecessary_escalation"],
    }
    for w in (30, 60):
        risk = (watch_risk or {}).get(w, 0.0)
        expected[f"observe_{w}"] = w * costs["watch_per_second"] + risk * costs.get(
            "watch_ignition", costs["unsafe"]
        )
    allowed = {k: v for k, v in expected.items() if k in options}
    if not allowed:
        raise ValueError("No available actions")
    selected = min(allowed, key=lambda k: (allowed[k], k))
    return dict(
        action="observe" if selected.startswith("observe_") else selected,
        observe_s=int(selected.split("_")[-1])
        if selected.startswith("observe_")
        else 0,
        expected_costs=allowed,
        method="minimum expected cost; optimistic perfect-information lower bound for watching",
    )


def decide(
    p, interval, rule, costs, *, hard_prediction=None, terminal=False, world_model=None
):
    rule = DecisionRule.model_validate(rule)
    if (
        not terminal
        and rule.abstain_band is not None
        and rule.abstain_band[0] <= p <= rule.abstain_band[1]
    ):
        return dict(action="abstain", observe_s=0)
    if rule.mode == "always_observe" and not terminal:
        return {"action": "observe", "observe_s": rule.observe_s or 30}
    if rule.mode == "cost":
        options = ["stand_down", "escalate"]
        risk = {}
        if not terminal and world_model and "pre_model" in world_model:
            w = world_model["spec"]["observation_s"]
            options.append(f"observe_{w}")
            risk[w] = p * world_model.get("watch_risk", 0.0)
        return choose_action(p, interval, costs, options=options, watch_risk=risk)
    h = hard_prediction if hard_prediction is not None else p >= rule.threshold
    return dict(action="escalate" if h else "stand_down", observe_s=0)


def observe(L, cid, trial_id, policy_id, seconds):
    from translucent.intervention import records, get, record

    trial = get(L, cid, trial_id, "trial")
    initial = next(
        (
            d
            for d in records(L, cid, "decision")
            if d.data["trial_id"] == trial_id
            and d.data["policy_id"] == policy_id
            and d.data["decision"] == "observe"
        ),
        None,
    )
    if initial is None or initial.data["observe_s"] != seconds:
        raise ValueError("Commit and pay for this observation first")
    full = json.loads(L.blob_path(trial.data["series_artifact"]).read_text())
    end = trial.data["t_heater_off"] + seconds
    pairs = [(x, y) for x, y in zip(full["t"], full["temperature_C"]) if x <= end]
    return record(
        L,
        cid,
        "observation",
        dict(
            decision_id=initial.id,
            trial_id=trial_id,
            policy_id=policy_id,
            observe_s=seconds,
            trace=dict(t=[x for x, y in pairs], temperature_C=[y for x, y in pairs]),
            source_artifact=trial.data["series_artifact"],
            available_until=min(end, pairs[-1][0]),
        ),
    )


def observation_features(L, cid, trial_id, policy_id, seconds):
    from translucent.intervention import records, get

    cache = getattr(L, "_intervention_verification_cache", None)
    key = (cid, trial_id, policy_id, seconds)
    if cache is not None and key in cache.observation_features:
        return cache.observation_features[key]
    o = next(
        (
            r
            for r in records(L, cid, "observation")
            if r.data["trial_id"] == trial_id
            and r.data["policy_id"] == policy_id
            and r.data["observe_s"] == seconds
        ),
        None,
    )
    if o is None:
        raise ValueError("No paid observation of this duration")
    off = get(L, cid, trial_id, "trial").data["t_heater_off"]
    features = post_features(o.data["trace"], off, seconds)
    if cache is not None:
        cache.observation_features[key] = features
    return features


def post_features(trace, off, seconds):
    from translucent.intervention_numeric import (
        interpolate,
        linear_fit,
        quadratic_coefficient,
    )

    t = np.asarray(trace["t"])
    y = np.asarray(trace["temperature_C"])
    mask = (t > off) & (t <= off + seconds)
    if mask.sum() < 3:
        raise ValueError("Observation does not contain enough post-off samples")
    ts = t[mask]
    ys = y[mask]
    # No sample outside the paid window enters any feature.
    return dict(
        post_level=float(ys[-1]),
        post_slope=linear_fit(ts - ts[0], ys)[1],
        post_rise=float(ys[-1] - interpolate(off, t, y)),
        post_curvature=quadratic_coefficient(ts - ts[0], ys),
        watch_elapsed=float(ts[-1] - off),
    )


def compatibility(old, new, *, tolerance):
    if len(old) != len(new) or not old:
        raise ValueError("Compatibility requires the same revealed trials")

    def unsafe(x):
        return x.get("unsafe", x["y"] == 1 and x["action"] == "stand_down")

    added = sum(not unsafe(a) and unsafe(b) for a, b in zip(old, new))
    old_brier = sum((a["p"] - a["y"]) ** 2 for a in old) / len(old)
    new_brier = sum((a["p"] - a["y"]) ** 2 for a in new) / len(new)

    def calibration(rows):
        bins = {}
        for row in rows:
            bins.setdefault(min(4, int(row["p"] * 5)), []).append(row)
        return sum(
            abs(sum(r["p"] - r["y"] for r in group)) for group in bins.values()
        ) / len(rows)

    old_calibration, new_calibration = calibration(old), calibration(new)
    accepted = (
        added == 0
        and new_brier <= old_brier + tolerance
        and new_calibration <= old_calibration + tolerance
    )
    reason = (
        f"rejected: adds {added} unsafe errors on revealed trials"
        if added
        else f"rejected: calibration guard ECE {old_calibration:.6g} -> {new_calibration:.6g}, Brier {old_brier:.6g} -> {new_brier:.6g}; tolerance {tolerance}"
        if not accepted
        else f"accepted: no added unsafe errors; ECE {old_calibration:.6g} -> {new_calibration:.6g}, Brier {old_brier:.6g} -> {new_brier:.6g} within tolerance {tolerance}"
    )
    return dict(
        accepted=accepted,
        reason=reason,
        added_unsafe=added,
        old_brier=old_brier,
        new_brier=new_brier,
        old_calibration=old_calibration,
        new_calibration=new_calibration,
        tolerance=tolerance,
    )


def history_predictions(model, rule, rows, costs):
    from translucent.intervention_models import predict

    results = []
    for row in rows:
        p, h = predict(model, row)
        a = decide(p, [p, p], rule, costs, hard_prediction=h, world_model=model)
        waiting_unsafe = False
        if a["action"] == "observe":
            from translucent.intervention_models import window_example

            watched = window_example(row, a["observe_s"])
            waiting_unsafe = bool(watched.get("watch_early"))
            if not waiting_unsafe and watched.get("post_features"):
                p, h = predict(
                    model,
                    {
                        **row,
                        "features": {**row["features"], **watched["post_features"]},
                        "observed": True,
                    },
                )
            a = decide(p, [p, p], rule, costs, hard_prediction=h, terminal=True)
        results.append(
            dict(
                p=p,
                y=row["y"],
                action=a["action"],
                unsafe=waiting_unsafe
                or (a["action"] == "stand_down" and row["y"] == 1),
            )
        )
    return results


def checked_update(L, cid, policy_id, candidate_fit, previous_fit, *, fold_id):
    from translucent.intervention import example, get, protocol, record

    p = get(L, cid, policy_id, "policy")
    fold = get(L, cid, fold_id, "fold")
    config = protocol(L, cid).data
    if not fold.data["train_ids"] or previous_fit is None:
        return candidate_fit
    spec = p.data["spec"]
    rows = [
        example(
            L,
            cid,
            i,
            training=True,
            post_s=spec.get("observation_s", 0),
            physics="physics_prior" in str(spec),
        )
        for i in fold.data["train_ids"]
    ]
    old = history_predictions(
        previous_fit.data["model"], p.data["decision_rule"], rows, config["costs"]
    )
    new = history_predictions(
        candidate_fit.data["model"], p.data["decision_rule"], rows, config["costs"]
    )
    verdict = compatibility(old, new, tolerance=config["calibration_tolerance"])
    applied = candidate_fit if verdict["accepted"] else previous_fit
    last = max(
        (get(L, cid, r["reveal_id"], "reveal") for r in rows), key=lambda r: r.seq
    )
    record(
        L,
        cid,
        "update",
        dict(
            policy_id=policy_id,
            fold_id=fold_id,
            after_trial=last.data["trial_id"],
            after_reveal_id=last.id,
            candidate_fit_id=candidate_fit.id,
            previous_fit_id=previous_fit.id,
            applied_fit_id=applied.id,
            world_model_updated=verdict["accepted"],
            decision_policy_updated=False,
            decision_rule_revalidated=True,
            previous_rule=p.data["decision_rule"],
            candidate_rule=p.data["decision_rule"],
            evaluated_trial_ids=fold.data["train_ids"],
            old_predictions=old,
            new_predictions=new,
            **verdict,
        ),
    )
    return applied
