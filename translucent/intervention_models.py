"""Executable, declarative policies. No provider calls and no outcome-file access.

Every fit receives only explicit training examples. The controller supplies those
examples from already-revealed records and records their ids before scoring.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from translucent.intervention_numeric import (
    NUMERICS_VERSION,
    dot,
    interpolate,
    linear_fit,
    logistic_fit,
    mean,
    quantile,
    sigmoid,
)

WINDOWS = (10, 20, 30, 60, 120)
FEATURES = (
    "level",
    "format",
    "soc",
    "delay_after_vent_s",
    "time_since_vent",
    "rise_since_vent",
    "time_heated",
    *[f"{name}_{w}" for name in ("slope", "rise", "curvature") for w in WINDOWS],
)
POST_FEATURES = ("post_level", "post_slope", "post_rise", "post_curvature")


class PolicySpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    family: Literal[
        "constant",
        "threshold",
        "logistic",
        "thermal",
        "two_node",
        "muse",
        "search",
        "table3",
    ]
    features: list[str] = []
    grouping: Literal["pooled", "format", "condition"] = "pooled"
    constraint: Literal["none", "zero_unsafe"] = "none"
    threshold: float | None = None
    probability: float = Field(default=1, ge=0, le=1)
    regularization: float = Field(default=1, gt=0, le=100)
    candidates: list["PolicySpec"] = Field(default=[], max_length=100)
    observation_s: Literal[0, 30, 60] = 0

    @model_validator(mode="after")
    def valid(self):
        if set(self.features) - set(FEATURES + POST_FEATURES + ("physics_prior",)):
            raise ValueError("Only declared features are allowed")
        if set(self.features) & set(POST_FEATURES) and not self.observation_s:
            raise ValueError("Post-off features require a paid observation window")
        if self.family == "threshold" and len(self.features) != 1:
            raise ValueError("A threshold needs one feature")
        if self.family == "logistic" and not self.features:
            raise ValueError("Logistic regression needs features")
        if self.family == "search" and (
            not self.candidates or any(c.family == "search" for c in self.candidates)
        ):
            raise ValueError("Search needs nonrecursive candidate specs")
        if self.constraint == "zero_unsafe" and self.family != "threshold":
            raise ValueError(
                "zero_unsafe is implemented for threshold policies; constrain individual threshold candidates inside search"
            )
        return self


def compute_features(t, y, heater_off, metadata):
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    keep = t <= heater_off
    t, y = t[keep], y[keep]
    if len(t) < 3 or not np.all(np.diff(t) > 0) or not np.isfinite(y).all():
        raise ValueError("Need a finite pre-off trace")
    level = float(y[-1])
    vent = metadata["t_vent"]

    def slope(a, b):
        seg = (t >= a) & (t <= b)
        return linear_fit(t[seg] - t[seg][0], y[seg])[1] if seg.sum() > 2 else 0.0

    f = dict(
        level=level,
        format=float(metadata["format"] == "21700"),
        soc=float(metadata["soc"]),
        delay_after_vent_s=float(metadata["delay_after_vent_s"]),
        time_since_vent=float(heater_off - vent),
        rise_since_vent=level - interpolate(vent, t, y),
        time_heated=float(t[-1] - t[0]),
    )
    for w in WINDOWS:
        s = slope(heater_off - w, heater_off)
        f[f"slope_{w}"] = s
        f[f"rise_{w}"] = level - interpolate(heater_off - w, t, y)
        f[f"curvature_{w}"] = s - slope(heater_off - 2 * w, heater_off - w)
    return f


def split_folds(rows, split):
    if split == "prequential":
        ordered = sorted(rows, key=lambda r: int(r["id"].split("Exp")[-1]))
        return [(f"exp-{x['id']}", ordered[:i], [x]) for i, x in enumerate(ordered)]
    if split not in ("loco", "lofo"):
        raise ValueError("No random trial splits are supported")

    def key(r):
        c = r["condition"]
        return f"{c['format']}-{c['soc']}" if split == "loco" else c["format"]

    return [
        (k, [r for r in rows if key(r) != k], [r for r in rows if key(r) == k])
        for k in sorted({key(r) for r in rows})
    ]


def _stump(x, y, safe=False, fixed=None):
    xs = np.unique(x)
    thresholds = (
        [fixed] if fixed is not None else [-np.inf, *((xs[:-1] + xs[1:]) / 2), np.inf]
    )
    best = None
    for threshold in thresholds:
        for direction in (1, -1) if fixed is None else (1,):
            h = x > threshold if direction == 1 else x < threshold
            unsafe = int(((~h) & (y == 1)).sum())
            acc = float((h == y).mean())
            if safe and unsafe:
                continue
            key = (acc, -unsafe)
            if best is None or key > best[0]:
                best = (key, threshold, direction, h)
    if best is None:
        raise ValueError("No threshold satisfies the training constraint")
    _, thr, direction, h = best
    return dict(
        threshold=float(thr) if np.isfinite(thr) else None,
        edge="low" if thr == -np.inf else "high" if thr == np.inf else None,
        direction=direction,
        p1=float((y[h].sum() + 1) / (h.sum() + 2)),
        p0=float((y[~h].sum() + 1) / ((~h).sum() + 2)),
    )


def _logit(X, y, lam):
    return logistic_fit(X, y, lam)


def _fit_one(spec, rows):
    if spec.family == "constant":
        return dict(constant=spec.probability)
    if not rows:
        return dict(constant=1.0, cold_start=True)
    y = np.array([r["y"] for r in rows])
    if spec.family in ("thermal", "two_node", "muse", "table3"):
        return {}
    if spec.family == "threshold":
        return _stump(
            np.array([r["features"][spec.features[0]] for r in rows]),
            y,
            spec.constraint == "zero_unsafe",
            spec.threshold,
        )
    if len(set(y)) == 1:
        return dict(constant=float((y.sum() + 1) / (len(y) + 2)))
    X = np.array([[r["features"][k] for k in spec.features] for r in rows])
    return _logit(X, y, spec.regularization)


def window_example(row, seconds):
    """Select precisely the revealed training window, never a longer window."""
    if not seconds:
        return row
    if "post_features_by_window" not in row:
        return row
    return {
        **row,
        "post_features": row["post_features_by_window"].get(str(seconds)),
        "watch_early": row.get("watch_early_by_window", {}).get(str(seconds), False),
    }


def fit(spec, rows, *, costs=None, seed=0):
    spec = PolicySpec.model_validate(spec)
    if spec.family == "search":
        chosen, audit = search(spec.candidates, rows, costs=costs, seed=seed)
        model = fit(chosen, rows, costs=costs, seed=seed)
        model["selection"] = audit
        model["search_spec"] = spec.model_dump()
        return model
    pre_model = None
    if spec.observation_s and set(spec.features) & set(POST_FEATURES):
        pre_model = fit(
            PolicySpec(family="threshold", features=["level"], grouping="format"),
            rows,
            seed=seed,
        )
        rows = [window_example(r, spec.observation_s) for r in rows]
        # Estimate the observed early-ignition fraction among ignitions, then
        # condition it on the current world's P(ignition) in the decision rule.
        ignitions = sum(r["y"] for r in rows)
        risk = (
            sum(bool(r.get("watch_early")) for r in rows) / ignitions
            if ignitions
            else 0.0
        )
        rows = [
            {**r, "features": {**r["features"], **r["post_features"]}}
            for r in rows
            if r.get("post_features") and not r.get("watch_early")
        ]
    models = {"pooled": _fit_one(spec, rows)}
    if spec.grouping in ("format", "condition"):
        for fmt in sorted({r["condition"]["format"] for r in rows}):
            models[fmt] = _fit_one(
                spec, [r for r in rows if r["condition"]["format"] == fmt]
            )
    if spec.grouping == "condition":
        for fmt, soc in sorted(
            {(r["condition"]["format"], r["condition"]["soc"]) for r in rows}
        ):
            models[f"{fmt}:{soc}"] = _fit_one(
                spec, [r for r in rows if r["condition"] == dict(format=fmt, soc=soc)]
            )
    result = dict(
        spec=spec.model_dump(),
        models=models,
        numerics=dict(
            version=NUMERICS_VERSION,
            reduction="math.fsum",
            logistic_solver="fixed-16-IRLS-Cholesky",
            bootstrap_generator="PCG64",
            bootstrap_seed=int(seed),
        ),
    )
    if pre_model is not None:
        result.update(
            pre_model=pre_model,
            watch_risk=risk,
            watch_risk_method="P(ignition so far) times revealed early-ignition fraction among ignitions; continuation evaluated in inner CV",
        )
    return result


# Published lookup is a retrospective reference; it used this same study's outcomes.
TABLE3 = {
    ("18650", 100): 193,
    ("18650", 75): 268,
    ("18650", 50): 265,
    ("18650", 25): 235,
    ("21700", 50): 40,
    ("21700", 25): 89,
}


def predict(model, row):
    if "pre_model" in model and not row.get("observed"):
        return predict(model["pre_model"], row)
    s = PolicySpec.model_validate(model["spec"])
    fmt = row["condition"]["format"]
    soc = row["condition"]["soc"]
    key = (
        f"{fmt}:{soc}"
        if s.grouping == "condition"
        else fmt
        if s.grouping == "format"
        else "pooled"
    )
    m = model["models"].get(key, model["models"].get(fmt, model["models"]["pooled"]))
    if s.family == "table3":
        threshold = TABLE3.get((fmt, soc), -1)
        h = int(row["features"]["delay_after_vent_s"] > threshold)
        return float(h), h
    if s.family == "thermal":
        forecast = thermal_forecast(row["trace"]["t"], row["trace"]["temperature_C"])
        return forecast["p_ignite"], int(forecast["p_ignite"] >= 0.5)
    if s.family == "two_node":
        from translucent.intervention_physics import cached_forecast

        forecast = cached_forecast(row["trace"], row["condition"])["actions"][
            "stop_now"
        ]
        return forecast["p_ignite"], int(forecast["p_ignite"] >= 0.5)
    if "constant" in m:
        return m["constant"], int(m["constant"] >= 0.5)
    if s.family == "threshold":
        thr = (
            m["threshold"]
            if m["threshold"] is not None
            else -np.inf
            if m["edge"] == "low"
            else np.inf
        )
        h = int(
            row["features"][s.features[0]] > thr
            if m["direction"] == 1
            else row["features"][s.features[0]] < thr
        )
        return m["p1"] if h else m["p0"], h
    values = [float(row["features"][key]) for key in s.features]
    design = [1.0] + [
        (value - center) / scale
        for value, center, scale in zip(values, m["mu"], m["sd"])
    ]
    p = sigmoid(dot(design, m["w"]))
    return p, int(p >= 0.5)


def search(candidates, rows, *, costs=None, seed=0):
    """Inner LOCO only; selection sees no outer test feature or outcome."""
    if not rows:
        return candidates[0], []
    from translucent.intervention_actions import DEFAULT_COSTS, decide

    costs = costs or DEFAULT_COSTS
    audit = []
    folds = split_folds(rows, "loco")
    for index, candidate in enumerate(candidates):
        unsafe = missed = 0
        loss = []
        decision_costs = []
        watch_time = 0.0
        fold_audit = []
        for name, train, test in folds:
            model = fit(candidate, train, costs=costs, seed=seed)
            for row in test:
                p, h = predict(model, row)
                y = row["y"]
                action = decide(p, [p, p], {"mode": "cost"}, costs, world_model=model)
                early = False
                w = action["observe_s"]
                if action["action"] == "observe":
                    watched = window_example(row, w)
                    early = bool(watched.get("watch_early"))
                    if not early and watched.get("post_features"):
                        p, h = predict(
                            model,
                            {
                                **row,
                                "features": {
                                    **row["features"],
                                    **watched["post_features"],
                                },
                                "observed": True,
                            },
                        )
                    action = decide(p, [p, p], {"mode": "cost"}, costs, terminal=True)
                u = int(early or (action["action"] == "stand_down" and y == 1))
                m = int(action["action"] == "escalate" and y == 0)
                spent = row.get("watch_time_by_window", {}).get(str(w), w)
                unsafe += u
                missed += m
                loss.append((p - y) ** 2)
                watch_time += spent
                decision_costs.append(
                    u * costs["unsafe"]
                    + m * costs["unnecessary_escalation"]
                    + spent * costs["watch_per_second"]
                )
            fold_audit.append(
                dict(
                    name=name,
                    train_ids=[r["id"] for r in train],
                    test_ids=[r["id"] for r in test],
                )
            )
        audit.append(
            dict(
                spec=candidate.model_dump(),
                unsafe=unsafe,
                missed=missed,
                brier=mean(loss),
                mean_cost=mean(decision_costs),
                watch_time_s=watch_time,
                screened_out=unsafe > len(rows) / 2,
                folds=fold_audit if index == 0 else [],
                folds_as_candidate=0,
            )
        )
    idx = min(
        range(len(audit)),
        key=lambda i: (
            audit[i]["mean_cost"],
            audit[i]["unsafe"],
            audit[i]["missed"],
            audit[i]["brier"],
            i,
        ),
    )
    return candidates[idx], audit


@lru_cache(maxsize=128)
def _bootstrap_models(spec_json, training_json, replicates, seed):
    spec = PolicySpec.model_validate_json(spec_json)
    train = json.loads(training_json)
    rng = np.random.Generator(np.random.PCG64(seed))
    blocks = {}
    for r in train:
        blocks.setdefault((r["condition"]["format"], r["condition"]["soc"]), []).append(
            r
        )
    groups = list(blocks)
    models = []
    for _ in range(replicates):
        sample = [
            r
            for i in rng.integers(0, len(groups), len(groups), dtype=np.int64)
            for r in blocks[groups[int(i)]]
        ]
        models.append(fit(spec, sample, seed=seed))
    return tuple(models)


def bootstrap_predict(model, train, row, *, replicates=64, seed=None):
    p, h = predict(model, row)
    if model["spec"]["family"] in ("thermal", "two_node"):
        return p, h, [0.0, 1.0]
    if not train or model["spec"]["family"] in ("constant", "table3"):
        return p, h, [0.0, 1.0] if not train else [p, p]
    # Reuse identical training resamples across a fold's test trials. No held-out
    # observation or outcome enters the cache key or a bootstrap fit.
    compact = [{k: v for k, v in r.items() if k != "trace"} for r in train]
    if seed is None:
        seed = model.get("numerics", {}).get("bootstrap_seed", 0)
    models = _bootstrap_models(
        json.dumps(model["spec"], sort_keys=True),
        json.dumps(compact, sort_keys=True),
        replicates,
        seed,
    )
    ps = [predict(fitted, row)[0] for fitted in models]
    lo, hi = quantile(ps, 0.025), quantile(ps, 0.975)
    return p, h, [min(float(lo), p), max(float(hi), p)]


def thermal_forecast(t, y, horizon=600):
    """Fit pre-off heat balance; profile weak Arrhenius kinetics, then turn heater off.

    dT/dt = a*heater - b*(T-Tambient) + q*dx/dt;
    dx/dt = A*exp(-E/(R*T))*(1-x). A surface trace cannot identify core kinetics.
    The regularized fit deliberately prefers weak reaction when equally plausible.
    """
    return deepcopy(
        _thermal_forecast_cached(
            tuple(float(value) for value in t),
            tuple(float(value) for value in y),
            horizon,
        )
    )


@lru_cache(maxsize=512)
def _thermal_forecast_cached(t, y, horizon):
    """Cache only explicit trace inputs; never share a mutable forecast publicly."""
    if not t or len(t) != len(y):
        raise ValueError("A finite, aligned pre-off trace is required")
    ambient = y[0]
    elapsed = [value - t[0] for value in t]
    best = None
    low, high = math.log(1e-6), math.log(0.003)
    grid = [math.exp(low + index * (high - low) / 47) for index in range(48)]
    grid[0], grid[-1] = 1e-6, 0.003
    above_ambient = [value - ambient for value in y]
    for b in grid:
        basis = [-math.expm1(-b * value) / b for value in elapsed]
        norm = dot(basis, basis)
        a = max(0.0, dot(basis, above_ambient) / norm) if norm else 0.0
        base = [ambient + a * value for value in basis]
        residual = [value - fitted for value, fitted in zip(y, base)]
        for E in (40000.0, 60000.0, 80000.0):
            rate = [
                math.exp(max(-700.0, min(0.0, -E / (8.314 * (value + 273.15)))))
                for value in y
            ]
            A = 1e-5 / max(max(rate), 1e-30)
            cumulative = 0.0
            progress = []
            for value in rate:
                cumulative += value * A
                progress.append(1.0 - math.exp(-cumulative))
            q = max(
                0.0, min(200.0, dot(progress, residual) / (dot(progress, progress) + 1))
            )
            fitted = [value + q * x for value, x in zip(base, progress)]
            rmse = math.sqrt(
                mean([(value - real) ** 2 for value, real in zip(fitted, y)])
            )
            key = rmse + 0.01 * q
            if best is None or key < best[0]:
                best = (key, a, b, E, A, q, progress[-1], rmse)
    _, a, b, E, A, q, x, rmse = best
    temperature = float(y[-1])
    forecast = [temperature]
    for _ in range(horizon):
        dx = min(
            1 - x,
            A
            * math.exp(max(-700.0, min(0.0, -E / (8.314 * (temperature + 273.15)))))
            * (1 - x),
        )
        temperature += -b * (temperature - ambient) + q * dx
        x += dx
        forecast.append(float(temperature))
    return dict(
        simulated=True,
        model="lumped Arrhenius",
        p_ignite=float(max(forecast) >= 300),
        interval=[0.0, 1.0],
        temperature_C=forecast,
        parameters=dict(a=a, b=b, E=E, A=A, q=q),
        pre_off_rmse_C=rmse,
        limit="Unidentified core state; surface fit cannot prove rescue",
    )


def muse_replay(summary, store=None):
    """Keep the offline stub until complete, ledger-bound chat receipts exist.

    The older summary cache contains no complete provider request or response.
    Its values cannot support a committed Muse decision, even when its key is
    correct; use the intervention chat replay runner for those recordings.
    """
    from translucent.budget import ROOT
    from translucent.recordings import key

    prompt = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    path = Path(store or ROOT / "data/recordings/intervention") / (
        key("muse-only", digest, prompt, {}) + ".json"
    )
    if not path.is_file():
        return dict(
            status="not_run", reason="No exact Muse-only recording; no provider called"
        )
    r = json.loads(path.read_text())
    value = json.loads(r["text"])
    if (
        r.get("prompt") != prompt
        or r.get("media_sha") != digest
        or r.get("kind") != "muse-only"
    ):
        raise ValueError("Recording input mismatch")
    p = value["p_ignite"]
    if type(p) not in (int, float) or not np.isfinite(p) or not 0 <= p <= 1:
        raise ValueError("Invalid recorded probability")
    if "interval" not in value:
        raise ValueError("Recorded Muse-only prediction is missing required interval")
    interval = value["interval"]
    if (
        not isinstance(interval, list)
        or len(interval) != 2
        or any(type(x) not in (int, float) or not math.isfinite(x) for x in interval)
        or not 0 <= interval[0] <= p <= interval[1] <= 1
    ):
        raise ValueError("Invalid recorded interval")
    raise ValueError(
        "Loose Muse-only recordings lack a bound model request and receipt; "
        "use translucent intervene nist --muse-only --replay instead"
    )


def frozen_specs():
    return {
        "always_escalate": PolicySpec(family="constant"),
        "paper_table3": PolicySpec(
            family="table3", features=["delay_after_vent_s"], grouping="condition"
        ),
        "delay_lookup_refit": PolicySpec(
            family="threshold", features=["delay_after_vent_s"], grouping="condition"
        ),
        "fixed_temperature": PolicySpec(
            family="threshold", features=["level"], threshold=170.0
        ),
        "temperature_refit": PolicySpec(family="threshold", features=["level"]),
        "physics_only": PolicySpec(family="thermal"),
        "two_node_physics": PolicySpec(family="two_node"),
        "always_observe_30": PolicySpec(
            family="threshold",
            features=["post_slope"],
            grouping="format",
            observation_s=30,
        ),
        "muse_only": PolicySpec(family="muse"),
    }


def scripted_specs():
    # All windows are generated by a generic library, never selected from held-out results.
    thresholds = [
        PolicySpec(
            family="threshold",
            features=[feature],
            grouping=group,
            constraint="zero_unsafe",
        )
        for group in ("pooled", "format")
        for feature in FEATURES
        if feature not in ("format",)
    ]
    logits = [
        PolicySpec(family="logistic", features=keys)
        for keys in [
            ["format", "soc", "delay_after_vent_s"],
            ["format", "level"],
            ["format", "level", "physics_prior"],
            *[
                [
                    "format",
                    "level",
                    f"slope_{w}",
                    f"rise_{next((v for v in WINDOWS if v >= 2 * w), WINDOWS[-1])}",
                ]
                for w in WINDOWS
            ],
        ]
    ]
    post_thresholds = [
        PolicySpec(
            family="threshold", features=[feature], grouping=group, observation_s=w
        )
        for w in (30, 60)
        for group in ("pooled", "format")
        for feature in POST_FEATURES
    ]
    post_logits = [
        PolicySpec(
            family="logistic",
            features=["format", "level", *POST_FEATURES],
            observation_s=w,
        )
        for w in (30, 60)
    ]
    constants = [PolicySpec(family="constant", probability=p) for p in (0, 0.5, 1)]
    return {
        "agent_threshold": PolicySpec(
            family="search",
            candidates=thresholds + post_thresholds + constants,
            observation_s=60,
        ),
        "agent_logistic": PolicySpec(
            family="search",
            candidates=logits + post_logits + constants,
            observation_s=60,
        ),
    }
