"""Numerical recomputation contracts; categorical evidence remains exact.

Float64 linear algebra can differ in its last bits between platforms. The
protocol admits only a small absolute probability tolerance. It never admits a
different action, observation duration, outcome, or compatibility acceptance.
"""

from __future__ import annotations

import math
from collections import defaultdict

PROBABILITY_ATOL = 1e-9


def clustered_signflip(differences, conditions, *, seed=0, resamples=100000):
    """Describe cost sensitivity when a condition is the randomization unit.

    Aligned differences are agent cost minus comparator cost for each trial.
    Sum those signed differences within each condition, then flip the signs of
    whole conditions with the registered paired-test implementation. This is a
    post-hoc robustness check; it does not replace the registered per-trial test.
    Zero-sum blocks count in ``n_blocks`` but add no randomization information.
    """
    from .intervention_bench import paired_signflip

    differences = list(differences)
    conditions = list(conditions)
    if len(differences) != len(conditions):
        raise ValueError("Each cost difference must have one condition block")

    grouped = defaultdict(list)
    for difference, condition in zip(differences, conditions):
        value = float(difference)
        if not math.isfinite(value):
            raise ValueError("Costs must be finite")
        # Formats/SOCs are tuples in the ledger; allow their JSON list form too.
        key = tuple(condition) if isinstance(condition, list) else condition
        grouped[key].append(value)

    blocks = [
        (condition, math.fsum(grouped[condition]))
        for condition in sorted(grouped, key=repr)
    ]
    result = paired_signflip(
        [difference for _, difference in blocks], seed=seed, resamples=resamples
    )
    return {
        **result,
        "n_blocks": len(blocks),
        "n_discordant_blocks": result["discordant"],
        "per_block_differences": [
            {
                "condition": list(condition)
                if isinstance(condition, tuple)
                else condition,
                "difference": difference,
            }
            for condition, difference in blocks
        ],
        "statistic": "absolute sum of condition-block cost differences",
        "descriptive": True,
        "post_hoc": True,
    }


def probability_tolerance(config):
    """Read the registered bound, with the same bound for older NIST receipts."""
    value = config.get("probability_atol", PROBABILITY_ATOL)
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not 0 <= value <= PROBABILITY_ATOL
    ):
        raise ValueError(
            "Protocol probability_atol must be finite and between 0 and 1e-9"
        )
    return value


def check_probability(
    recorded, recomputed, *, atol, trial, label="Committed probability"
):
    """Allow absolute float noise without a relative or decision tolerance."""
    if any(
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not 0 <= value <= 1
        for value in (recorded, recomputed)
    ):
        raise ValueError(f"{trial}: {label} is not a finite probability")
    delta = abs(recorded - recomputed)
    if delta > atol:
        raise ValueError(
            f"{trial}: {label} differs from its executable world model: "
            f"committed {recorded:.17g}, recomputed {recomputed:.17g}, "
            f"absolute difference {delta:.17g} exceeds protocol tolerance {atol:.17g}"
        )


def check_action(recorded, recomputed, *, recorded_p, recomputed_p, trial):
    """Even a sub-tolerance probability difference cannot cross an action boundary."""
    if recorded != recomputed:
        raise ValueError(
            f"{trial}: Committed action differs from its declared decision rule "
            f"(decision threshold/action mismatch): committed {recorded} "
            f"at p={recorded_p:.17g}, recomputed {recomputed} at p={recomputed_p:.17g}"
        )


def check_history(recorded, recomputed, *, atol, trials, side):
    """Compare aligned compatibility predictions with only their probabilities tolerant."""
    if len(recorded) != len(recomputed) or len(recorded) != len(trials):
        raise ValueError("Compatibility prediction history length differs")
    for saved, actual, trial in zip(recorded, recomputed, trials):
        if saved.keys() != actual.keys():
            raise ValueError(f"{trial}: Compatibility prediction fields differ")
        check_action(
            saved["action"],
            actual["action"],
            recorded_p=saved["p"],
            recomputed_p=actual["p"],
            trial=trial,
        )
        for key in saved.keys() - {"p", "action"}:
            if type(saved[key]) is not type(actual[key]) or saved[key] != actual[key]:
                raise ValueError(
                    f"{trial}: Compatibility {side} {key} differs from executable models"
                )
        check_probability(
            saved["p"],
            actual["p"],
            atol=atol,
            trial=trial,
            label=f"Compatibility {side} probability",
        )
