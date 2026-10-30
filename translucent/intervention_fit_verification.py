"""Rebuild executable fits and training-only screens from ledger provenance.

The stored model is evidence to check, never a substitute for fitting the
registered specification on its recorded, revealed training partition. Caches
exist only inside an immutable verification snapshot and retain no verdicts.
"""

from __future__ import annotations

from copy import deepcopy
import math


def training_rows(ledger, campaign_id, policy, fold):
    """Use exactly the feature, observation and physics inputs used by fit_policy."""
    from translucent.intervention import example

    spec = policy.data["spec"]
    return [
        example(
            ledger,
            campaign_id,
            trial_id,
            training=True,
            post_s=spec.get("observation_s", 0),
            physics="physics_prior" in str(spec),
        )
        for trial_id in fold.data["train_ids"]
    ]


def recompute_fit(ledger, campaign_id, policy, fold, config):
    """Fit once per full specification and registered training partition."""
    from translucent.intervention import canonical
    from translucent.intervention_models import fit

    cache = getattr(ledger, "_intervention_verification_cache", None)
    key = canonical(
        dict(
            spec=policy.data["spec"],
            train_ids=fold.data["train_ids"],
            costs=config["costs"],
            seed=config["seed"],
        )
    )
    if cache is not None and key in cache.fits:
        return cache.fits[key]
    model = fit(
        policy.data["spec"],
        training_rows(ledger, campaign_id, policy, fold),
        costs=config["costs"],
        seed=config["seed"],
    )
    if cache is not None:
        cache.fits[key] = model
    return model


def screen_data(ledger, campaign_id, policy, fold, model, config):
    """Re-run the registered training-only candidate screening rule."""
    from translucent.intervention import canonical
    from translucent.intervention_models import fit, predict, split_folds

    cache = getattr(ledger, "_intervention_verification_cache", None)
    key = canonical(
        dict(
            policy_id=policy.id,
            fold_id=fold.id,
            model=model,
            costs=config["costs"],
            funnel=config["funnel"],
            seed=config["seed"],
            frozen=bool(config.get("require_baseline_screens")),
        )
    )
    if cache is not None and key in cache.screens:
        return deepcopy(cache.screens[key])
    training = training_rows(ledger, campaign_id, policy, fold)
    audit = model.get("selection", [])
    if policy.data["kind"] == "baseline" and config.get("require_baseline_screens"):
        survived = True
        rejected = 0
        proposed = 0
        detail = "Frozen baseline comparator: registered without candidate selection"
    else:
        proposed = len(audit) or 1
        if audit:
            survived = any(not item["screened_out"] for item in audit)
            rejected = sum(item["screened_out"] for item in audit)
            detail = (
                "Nested candidate models screened on inner LOCO; selected learning "
                "strategy remains the held-out submission unit"
            )
        else:
            unsafe = 0
            for _, train, test in split_folds(training, "loco"):
                inner = fit(policy.data["spec"], train, costs=config["costs"])
                unsafe += sum(
                    predict(inner, row)[1] == 0 and row["y"] == 1 for row in test
                )
            survived = (
                unsafe <= len(training) * config["funnel"]["max_inner_unsafe_fraction"]
            )
            rejected = int(not survived)
            detail = f"Training-only LOCO unsafe={unsafe}, n={len(training)}"
        if not training:
            survived = True
            detail = (
                "No feedback yet: registered cold-start escalation branch; "
                "no fitted candidate selected"
            )
    result = dict(
        policy_id=policy.id,
        fold_id=fold.id,
        train_ids=fold.data["train_ids"],
        reveal_ids=[row["reveal_id"] for row in training],
        survived=survived,
        reason=detail,
        candidate_models_proposed=proposed,
        candidate_models_screened_out=rejected,
    )
    if cache is not None:
        cache.screens[key] = deepcopy(result)
    return result


def check_refit(recorded, recomputed, *, config, atol):
    """Require the same model structure, allowing bounded float leaves only."""
    # Numerical metadata was added in v2. It cannot be retroactively required
    # of earlier receipts, and cannot be omitted from a v2-or-later receipt.
    if config.get("numerical_version", 1) < 2:
        recorded = _without_numerics(recorded)
        recomputed = _without_numerics(recomputed)
    _check_equal(recorded, recomputed, atol=atol, path="model")


def _without_numerics(value):
    if isinstance(value, dict):
        return {k: _without_numerics(v) for k, v in value.items() if k != "numerics"}
    if isinstance(value, list):
        return [_without_numerics(v) for v in value]
    return value


def _check_equal(recorded, recomputed, *, atol, path):
    if type(recorded) is not type(recomputed):
        raise ValueError(
            f"Stored fit differs from executable refit at {path}: type differs"
        )
    if isinstance(recorded, dict):
        if recorded.keys() != recomputed.keys():
            raise ValueError(
                f"Stored fit differs from executable refit at {path}: fields differ"
            )
        for key in recorded:
            _check_equal(
                recorded[key], recomputed[key], atol=atol, path=f"{path}.{key}"
            )
    elif isinstance(recorded, list):
        if len(recorded) != len(recomputed):
            raise ValueError(
                f"Stored fit differs from executable refit at {path}: length differs"
            )
        for index, (saved, fresh) in enumerate(zip(recorded, recomputed)):
            _check_equal(saved, fresh, atol=atol, path=f"{path}[{index}]")
    elif isinstance(recorded, float):
        if not math.isfinite(recorded) or not math.isfinite(recomputed):
            raise ValueError(
                f"Stored fit differs from executable refit at {path}: non-finite value"
            )
        delta = abs(recorded - recomputed)
        if delta > atol:
            raise ValueError(
                f"Stored fit differs from executable refit at {path}: recorded "
                f"{recorded:.17g}, recomputed {recomputed:.17g}; absolute "
                f"difference {delta:.17g} exceeds protocol tolerance {atol:.17g}"
            )
    elif recorded != recomputed:
        raise ValueError(
            f"Stored fit differs from executable refit at {path}: recorded "
            f"{recorded!r}, recomputed {recomputed!r}"
        )
