"""Condition-block robustness checks leave the registered trial test intact."""

import itertools
import math

import pytest

from translucent.intervention_bench import holm, paired_signflip
from translucent.intervention_comparison import clustered_signflip


def test_condition_signflip_uses_whole_blocks_and_matches_exact_enumeration():
    differences = [-100, -10, 5, 5, -20, 5]
    conditions = [("18650", 100)] * 2 + [("18650", 75)] * 2 + [("21700", 50)] * 2
    block_sums = [-110, 10, -15]
    observed = abs(sum(block_sums))
    sums = [
        abs(sum(sign * value for sign, value in zip(signs, block_sums)))
        for signs in itertools.product((-1, 1), repeat=len(block_sums))
    ]
    expected = sum(value >= observed for value in sums) / len(sums)

    result = clustered_signflip(differences, conditions)

    assert result["p"] == expected == 0.5
    assert result["n_blocks"] == result["n_discordant_blocks"] == 3
    assert result["assignments"] == 8
    assert result["method"] == "exact"
    assert result["monte_carlo_se"] == 0
    assert result["descriptive"] is result["post_hoc"] is True
    assert result["p"] != paired_signflip(differences)["p"]


def test_eight_condition_floor_and_registered_family_holm_are_descriptive():
    differences = [-1] * 50
    conditions = [f"condition-{index % 8}" for index in range(len(differences))]
    result = clustered_signflip(differences, conditions)

    assert result["n_blocks"] == result["n_discordant_blocks"] == 8
    assert result["assignments"] == 256
    assert result["p"] == 2 / 256
    assert (
        holm({("agent", "baseline"): result["p"]}, 18)[("agent", "baseline")]
        == 0.140625
    )
    assert paired_signflip(differences)["p"] < result["p"]


def test_condition_sums_are_stable_under_row_order_and_zero_blocks():
    differences = [1e16, 1, -1e16, 2, -2, 0]
    conditions = ["a", "a", "a", "b", "b", "c"]
    result = clustered_signflip(differences, conditions)

    assert result == clustered_signflip(differences[::-1], conditions[::-1])
    assert result["n_blocks"] == 3
    assert result["n_discordant_blocks"] == result["discordant"] == 1
    assert result["per_block_differences"] == [
        {"condition": "a", "difference": 1.0},
        {"condition": "b", "difference": 0.0},
        {"condition": "c", "difference": 0.0},
    ]
    assert result["p"] == 1
    assert clustered_signflip([], [])["p"] == 1


def test_clustered_receipt_accepts_json_conditions_and_seeded_resampling():
    differences = [-1] * 21
    conditions = [["format", soc] for soc in range(21)]
    result = clustered_signflip(differences, conditions, seed=42, resamples=500)

    assert result == clustered_signflip(differences, conditions, seed=42, resamples=500)
    assert result["method"] == "seeded Monte Carlo"
    assert result["assignments"] == 500
    assert result["seed"] == 42
    assert 0 < result["p"] <= 1
    assert result["monte_carlo_se"] > 0
    assert isinstance(result["per_block_differences"][0]["condition"], list)


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf])
def test_condition_signflip_rejects_invalid_costs(invalid):
    with pytest.raises(ValueError, match="Costs must be finite"):
        clustered_signflip([invalid], ["condition"])


def test_condition_signflip_rejects_unaligned_trials():
    with pytest.raises(ValueError, match="one condition block"):
        clustered_signflip([1, 2], ["condition"])
