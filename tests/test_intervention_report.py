"""Scientific conclusions distinguish registered claims from descriptive checks."""

from copy import deepcopy

import pytest

from test_intervention_claim_validation import _report_input
from translucent.intervention_bench import markdown_report


def _sensitivity_report():
    result = _report_input(verdict="supported")
    campaign = result["campaigns"][0]
    campaign["variant"] = "with_inferred-lag10-ratio10"
    campaign["sensitivities"] = {
        "cost_rule_scores": [
            {
                "policy_id": "baseline",
                "split": "loco",
                "n": 7,
                "unsafe": 0,
                "missed": 1,
                "mean_cost": 1.0,
            }
        ],
        "cost_rule_comparisons": [
            {
                "a": "agent",
                "b": "baseline",
                "split": "loco",
                "a_mean_cost": 1.0,
                "b_mean_cost": 1.0,
                "p": 1.0,
            }
        ],
        "clustered_comparisons": [
            {
                "a": "agent",
                "b": "baseline",
                "split": "loco",
                "p": 0.125,
                "p_holm": 0.25,
                "n_blocks": 4,
            }
        ],
        "threshold_sweep": [
            {
                "threshold_c": 140,
                "split": "loco",
                "n": 7,
                "unsafe": 0,
                "missed": 1,
                "mean_cost": 1.0,
            }
        ],
        "physics_probabilities": [],
    }
    campaign["data_quality"] = {
        "inferred_count": 3,
        "inferred_ignition_delays_s": [2, 19, 40],
    }
    campaign["references"] = [
        {
            "name": "passive_never_stop",
            "n": 6,
            "ignited": 4,
            "ignition_interval": [0.3, 0.9],
            "traces": [{"series_artifact": "artifact"}, {"series_artifact": None}],
        }
    ]
    return result


def test_only_registered_primary_gate_wins_receive_yes():
    result = _sensitivity_report()
    primary = result["campaigns"][0]
    for split in ("lofo", "prequential"):
        comparison = deepcopy(primary["comparisons"][0])
        comparison["split"] = split
        primary["comparisons"].append(comparison)
        claim = deepcopy(primary["claims"][0])
        claim["id"] = split
        claim["predicate"]["split"] = split
        primary["claims"].append(claim)
        primary["reviews"].append({"claim_id": split, "verdict": "supported"})
    sensitivity = deepcopy(primary)
    sensitivity["subset"] = "without_inferred"
    sensitivity["variant"] = "without_inferred-lag10-ratio10"
    result["campaigns"].append(sensitivity)
    text = markdown_report(result)
    comparisons = [
        line
        for line in text.splitlines()
        if line.startswith("| ") and "| 0.001 | 0.001 | 2 |" in line
    ]
    assert len(comparisons) == 6
    assert sum("| yes |" in line for line in comparisons) == 1
    assert all("| descriptive |" in line for line in comparisons)
    assert "| yes | descriptive |" in comparisons[0]


def test_post_hoc_tables_disclose_cost_rule_and_clustering_without_claims():
    text = markdown_report(_sensitivity_report())
    assert (
        "## post-hoc sensitivity (added after the primary results were known)" in text
    )
    assert "outside the registered Holm family" in text
    assert "without refitting or adding observation" in text
    assert "| 0.125 | 0.25 | 4 |" in text
    assert "Condition-clustered p values flip the sign" in text
    post_hoc = text.split("## post-hoc sensitivity", 1)[1]
    assert "| yes |" not in post_hoc
    assert "illustrative, unsourced" in post_hoc
    assert "No threshold is selected from this sweep" in post_hoc
    assert "| 140 | 7 | 0 | 1 | 1.000 | descriptive |" in post_hoc


def test_report_counts_come_from_receipts_and_cost_assumption_beside_each_table():
    text = markdown_report(_sensitivity_report())
    assert "4/6 recorded ignitions" in text
    assert "1 traces" in text
    assert "all 3 inferred heater-off decision points" in text
    assert "2–40 s" in text
    assert "26/26" not in text
    assert "n=50" not in text
    assert "Two candidates and nine" not in text
    cost_headers = [
        i
        for i, line in enumerate(text.splitlines())
        if line.startswith("| Data") and "cost" in line.lower()
    ]
    lines = text.splitlines()
    assert len(cost_headers) == 5
    assert all(
        "escalating on a trial that ignites costs 0" in "\n".join(lines[i - 3 : i])
        and "escalation efficacy was not observed" in "\n".join(lines[i - 3 : i])
        for i in cost_headers
    )


def test_report_discloses_amendment_timing_selection_and_output_paths():
    text = markdown_report(_sensitivity_report())
    assert "02:12:58 +05:30" in text
    assert "02:22:19 +05:30" in text
    assert "02:50:31 +05:30" in text
    assert "21:21:02 UTC, 31 seconds after amendment 3" in text
    assert "git cannot establish" in text
    assert "without_inferred subset selects on outcome" in text
    assert "Missing conditions use a default delay of -1 s" in text
    assert "--out /path/to/output" in text
    assert (
        "Ledger paths in result JSON exist after rerunning; they are not committed"
        in text
    )


@pytest.mark.parametrize("one_max,two_max", [(0.0, 0.25), (0.0, 0.125)])
def test_physics_probability_disclosure_uses_measured_forecasts(one_max, two_max):
    result = _sensitivity_report()
    campaign = result["campaigns"][0]
    campaign["policy_ids"].update(physics_only="one", two_node_physics="two")
    campaign["sensitivities"]["physics_probabilities"] = [
        {"policy_id": "one", "split": "loco", "min_p": 0.0, "max_p": one_max},
        {"policy_id": "two", "split": "loco", "min_p": 0.0, "max_p": two_max},
    ]
    text = markdown_report(result)
    assert "physics_only gives p=0 everywhere" in text
    assert f"two_node_physics never exceeds {two_max:g}" in text
