"""Gate/report failures must preserve the distinction between rules and bugs."""

from types import SimpleNamespace

import pytest

from translucent import intervention_bench as bench
from translucent.intervention import (
    commit_decision,
    create,
    fit_policy,
    propose_policy,
    register_fold,
    reveal,
)
from translucent.intervention_models import PolicySpec
from translucent.ledger import Ledger
from translucent.systems.nist_heat import ingest
from tests.test_intervention_gate import scored_campaign


@pytest.mark.parametrize(
    "kind,predicate,required",
    [
        (
            "policy_performance",
            {"type": "policy_performance", "policy_id": "p", "n": 50, "unsafe": 0},
            "type, policy_id, split, n, unsafe",
        ),
        (
            "policy_beats",
            {"type": "policy_beats", "a": "p", "b": "b"},
            "type, a, b, split",
        ),
        (
            "policy_no_more_unsafe_fewer_missed",
            {"type": "policy_no_more_unsafe_fewer_missed", "a": "p", "b": "b"},
            "type, a, b, split",
        ),
    ],
)
def test_malformed_claim_has_human_readable_required_fields(kind, predicate, required):
    from translucent.intervention_runtime import tool_schemas

    claim = SimpleNamespace(
        id="claim", campaign_id="campaign", data={"predicate": predicate}
    )
    result = bench.evaluate_claim(None, claim)
    assert result["verdict"] == "unsupported"
    assert result["reason"] == (
        f"{kind} claim is missing field 'split' (required: {required})"
    )
    description = next(
        tool["description"]
        for tool in tool_schemas()
        if tool["name"] == "propose_claim"
    )
    assert f"{kind}: {required}" in description


@pytest.mark.parametrize(
    "predicate,fragment",
    [
        ({}, "supported 'type'"),
        (
            {
                "type": "policy_performance",
                "policy_id": "p",
                "split": "loco",
                "unsafe": 0,
            },
            "missing field 'n'",
        ),
    ],
)
def test_gate_explains_missing_type_and_count(predicate, fragment):
    claim = SimpleNamespace(
        id="claim", campaign_id="campaign", data={"predicate": predicate}
    )
    result = bench.evaluate_claim(None, claim)
    assert result["verdict"] == "unsupported"
    assert fragment in result["reason"]


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("controller failed"),
        KeyError("broken field"),
        ValueError("numerics failed"),
    ],
)
def test_evaluate_claim_propagates_unexpected_controller_failure(
    tmp_path, monkeypatch, failure
):
    ledger, campaign, agent, _, _ = scored_campaign(tmp_path)
    claim = bench.propose_claim(
        ledger,
        campaign.id,
        {
            "type": "policy_performance",
            "policy_id": agent.id,
            "split": "loco",
            "n": 4,
            "unsafe": 0,
        },
    )

    def broken(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(bench, "require_complete_coverage", broken)
    with pytest.raises(type(failure), match=str(failure)):
        bench.evaluate_claim(ledger, claim)


def test_evaluate_claim_reports_deliberate_rule_rejection(tmp_path, monkeypatch):
    ledger, campaign, agent, _, _ = scored_campaign(tmp_path)
    claim = bench.propose_claim(
        ledger,
        campaign.id,
        {
            "type": "policy_performance",
            "policy_id": agent.id,
            "split": "loco",
            "n": 4,
            "unsafe": 0,
        },
    )

    def reject(*_args, **_kwargs):
        raise bench.ClaimRuleViolation("Registered minimum coverage is incomplete")

    monkeypatch.setattr(bench, "require_complete_coverage", reject)
    result = bench.evaluate_claim(ledger, claim)
    assert result["verdict"] == "unsupported"
    assert result["reason"] == "Registered minimum coverage is incomplete"


def _partially_paired_campaign(tmp_path):
    ledger = Ledger(tmp_path / "ledger")
    campaign = create(ledger, splits=["loco"], min_trials=1)
    policies = [
        propose_policy(
            ledger,
            campaign.id,
            name,
            PolicySpec(family="constant"),
            kind=kind,
        )
        for name, kind in (
            ("scored", "agent"),
            ("unrun", "agent"),
            ("baseline", "baseline"),
        )
    ]
    store = tmp_path / "sealed"
    trials = ingest(ledger, campaign.id, store, scope="heldout", selected={15, 28})
    fold = register_fold(
        ledger, campaign.id, "heldout", "loco", [], [trial.id for trial in trials]
    )
    fitted = {}
    for policy in policies:
        fitted[policy.id] = fit_policy(ledger, campaign.id, policy.id, fold.id)
        bench.screen_policy(ledger, campaign.id, policy.id, fold.id, fitted[policy.id])
        bench.submit_policy(ledger, campaign.id, policy.id)
    for trial in trials:
        for policy in (policies[0], policies[2]):
            commit_decision(
                ledger,
                campaign.id,
                trial.id,
                policy.id,
                "escalate",
                1.0,
                [1, 1],
                fit_id=fitted[policy.id].id,
            )
        reveal(ledger, campaign.id, trial.id, store)
    return ledger, campaign, policies


def test_incomplete_candidate_retains_a_conservative_correction_family(tmp_path):
    ledger, campaign, (agent, _, baseline) = _partially_paired_campaign(tmp_path)
    result = bench.comparison(ledger, campaign.id, agent.id, baseline.id, "loco")
    assert result["candidates_submitted"] == 2
    assert result["family_size"] == 2
    assert result["p"] == result["p_holm"] == 1.0


@pytest.mark.parametrize(
    "failure", [ValueError("broken cost arithmetic"), RuntimeError("controller bug")]
)
def test_comparison_does_not_turn_arithmetic_errors_into_p_one(
    tmp_path, monkeypatch, failure
):
    ledger, campaign, (agent, _, baseline) = _partially_paired_campaign(tmp_path)
    original = bench._cost_pair

    def broken_if_unrun(a, b, *args, **kwargs):
        if not a:
            raise failure
        return original(a, b, *args, **kwargs)

    monkeypatch.setattr(bench, "_cost_pair", broken_if_unrun)
    with pytest.raises(type(failure), match=str(failure)):
        bench.comparison(ledger, campaign.id, agent.id, baseline.id, "loco")


def _report_input(*, verdict="inconclusive", claim_type="policy_beats", muse_n=7):
    pair = {
        "a": "agent",
        "b": "baseline",
        "split": "loco",
        "mean_cost_difference": -1,
        "a_mean_cost": 1,
        "b_mean_cost": 2,
        "p": 0.001,
        "p_holm": 0.001,
        "family_size": 2,
        "secondary_two_part": False,
        "unsafe_mcnemar_p": 1,
    }
    metric = dict(
        policy_id="muse",
        subset="all",
        split="loco",
        n=muse_n,
        mean_cost=1,
        mean_cost_interval=[0, 2],
        unsafe=0,
        unsafe_interval=[0, 1],
        missed=1,
        missed_interval=[0, 1],
        abstain=0,
        watch_time_s=0,
        brier=0.1,
        brier_interval=[0, 0.2],
        logloss=0.1,
        logloss_interval=[0, 0.2],
        accuracy=0.9,
        accuracy_interval=[0.8, 1],
    )
    campaign = {
        "policy_ids": {
            "agent_threshold": "agent",
            "agent_logistic": "other-agent",
            "always_escalate": "baseline",
            "muse_only": "muse",
        },
        "variant": "primary",
        "scores": [metric],
        "comparisons": [pair],
        "funnel": {
            "proposed": 2,
            "screened_out": 0,
            "submitted": 2,
            "candidate_models_proposed": 2,
            "candidate_models_screened_out": 0,
        },
        "protocol": {"heldout_cap_K": 16},
        "updates": {"accepted": 0, "rejected": 0},
        "subset": "with_inferred",
        "heater_off_lag_s": 10,
        "unsafe_ratio": 10,
        "claims": [
            {
                "id": "claim",
                "predicate": {
                    "type": claim_type,
                    "a": "agent",
                    "b": "baseline",
                    "split": "loco",
                },
            }
        ],
        "reviews": [{"claim_id": "claim", "verdict": verdict}],
    }
    return {"campaigns": [campaign]}


@pytest.mark.parametrize(
    "verdict,claim_type,won",
    [
        ("inconclusive", "policy_beats", False),
        ("supported", "policy_performance", False),
        ("supported", "policy_beats", True),
    ],
)
def test_report_wins_follow_supported_primary_gate_reviews(verdict, claim_type, won):
    result = _report_input(verdict=verdict, claim_type=claim_type)
    text = bench.markdown_report(result)
    comparison_line = next(
        line
        for line in text.splitlines()
        if line.startswith("| primary | loco | agent_threshold | always_escalate |")
    )
    assert f"| {'yes' if won else 'descriptive'} | descriptive |" in comparison_line
    summary = next(
        line for line in text.splitlines() if line.startswith("agent_threshold:")
    )
    expected = "always_escalate" if won else "no scored baseline"
    assert f"supported lower mean cost than {expected};" in summary


def test_report_muse_status_uses_recorded_sample_count():
    text = bench.markdown_report(_report_input(muse_n=7))
    assert "Muse-only has 7 recorded held-out predictions." in text
    assert "Muse-only has 0 recorded" not in text
    assert "Muse-only is not run" not in text


def test_intervention_review_boundary_reports_its_own_mismatch(tmp_path, monkeypatch):
    """A review mismatch fails even without another verifier's arithmetic check."""
    from translucent.intervention import record
    from translucent.verify import Result, _interventions

    ledger, campaign, agent, _, _ = scored_campaign(tmp_path)
    claim = bench.propose_claim(
        ledger,
        campaign.id,
        {
            "type": "policy_performance",
            "policy_id": agent.id,
            "split": "loco",
            "n": 4,
            "unsafe": 0,
        },
    )
    record(
        ledger,
        campaign.id,
        "review",
        dict(claim_id=claim.id, verdict="unsupported", reason="forged", computed=None),
    )
    monkeypatch.setattr("translucent.intervention.problems", lambda *_: [])
    checked = Result()
    _interventions(ledger, checked, campaign.id)
    assert not checked.ok
    assert checked.findings == 1 and checked.matched == 0
    assert any(
        "gate verdict that differs from recomputation" in p for p in checked.problems
    )
