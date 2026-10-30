"""Probability noise never relaxes a committed action or measured outcome."""

import pytest


def checked_campaign(
    tmp_path,
    *,
    probability=0.4,
    perturbation=0,
    action="stand_down",
    cost_rule=False,
    narrow_interval=False,
):
    from translucent.intervention import (
        commit_decision,
        create,
        fit_policy,
        propose_policy,
        register_fold,
        reveal,
    )
    from translucent.intervention_bench import submit_policy
    from translucent.ledger import Ledger
    from translucent.systems.nist_heat import ingest
    from translucent.verify import verify

    ledger = Ledger(tmp_path / "ledger")
    try:
        campaign = create(ledger, min_trials=1)
        policy = propose_policy(
            ledger,
            campaign.id,
            "constant check",
            dict(family="constant", probability=probability),
            kind="baseline",
            decision_rule={"mode": "cost"} if cost_rule else None,
        )
        submit_policy(ledger, campaign.id, policy.id)
        store = tmp_path / "sealed"
        trial = ingest(ledger, campaign.id, store, scope="check", selected={22})[0]
        fold = register_fold(ledger, campaign.id, "check", "loco", [], [trial.id])
        fitted = fit_policy(ledger, campaign.id, policy.id, fold.id)
        assert fitted.data["bootstrap_generator"] == "PCG64"
        assert fitted.data["bootstrap_seed"] == 0
        commit_decision(
            ledger,
            campaign.id,
            trial.id,
            policy.id,
            action,
            probability + perturbation,
            [probability + perturbation] * 2 if narrow_interval else [0, 1],
            fit_id=fitted.id,
        )
        reveal(ledger, campaign.id, trial.id, store)
        return verify(ledger.root)
    finally:
        ledger.db.close()


def test_probability_noise_of_one_trillionth_passes(tmp_path):
    assert checked_campaign(tmp_path, perturbation=1e-12).ok


def test_probability_noise_at_a_committed_interval_endpoint_passes(tmp_path):
    assert checked_campaign(
        tmp_path,
        perturbation=1e-12,
        action="escalate",
        cost_rule=True,
        narrow_interval=True,
    ).ok


def test_probability_change_of_one_millionth_fails_with_trial_and_values(tmp_path):
    result = checked_campaign(tmp_path, perturbation=1e-6)
    assert not result.ok
    assert any(
        "Exp22" in problem
        and "committed" in problem
        and "recomputed" in problem
        and "exceeds protocol tolerance" in problem
        for problem in result.problems
    )


def test_flipped_action_fails_even_at_identical_probability(tmp_path):
    result = checked_campaign(tmp_path, action="escalate")
    assert not result.ok
    assert any(
        "Exp22" in p and "threshold/action mismatch" in p for p in result.problems
    )


def test_crossing_threshold_inside_probability_tolerance_still_fails(tmp_path):
    result = checked_campaign(
        tmp_path, probability=0.5 - 5e-13, perturbation=1e-12, action="escalate"
    )
    assert not result.ok
    assert any(
        "Exp22" in p
        and "threshold/action mismatch" in p
        and "committed escalate at p=" in p
        and "recomputed stand_down at p=" in p
        for p in result.problems
    )


def test_cost_action_boundary_stays_exact_at_a_narrow_interval(tmp_path):
    result = checked_campaign(
        tmp_path,
        probability=1 / 11 - 5e-13,
        perturbation=1e-12,
        action="escalate",
        cost_rule=True,
        narrow_interval=True,
    )
    assert not result.ok
    assert any(
        "Exp22" in p
        and "threshold/action mismatch" in p
        and "committed escalate at p=" in p
        and "recomputed stand_down at p=" in p
        for p in result.problems
    )


def test_compatibility_probabilities_are_tolerant_but_categories_are_exact():
    from translucent.intervention_comparison import check_history

    actual = dict(p=0.4, action="stand_down", y=0, unsafe=False)
    check_history(
        [{**actual, "p": 0.4 + 1e-12}],
        [actual],
        atol=1e-9,
        trials=["Exp22"],
        side="candidate",
    )
    for field, value in (
        ("p", 0.4 + 1e-6),
        ("action", "escalate"),
        ("y", 1),
        ("unsafe", True),
        ("observe_s", 30),
    ):
        with pytest.raises(ValueError):
            check_history(
                [{**actual, field: value}],
                [actual],
                atol=1e-9,
                trials=["Exp22"],
                side="candidate",
            )


def test_protocol_cannot_expand_the_registered_tolerance(tmp_path):
    from translucent.intervention import create, protocol
    from translucent.intervention_comparison import probability_tolerance
    from translucent.ledger import Ledger

    ledger = Ledger(tmp_path / "ledger")
    try:
        c = create(ledger)
        config = protocol(ledger, c.id).data
        assert config["probability_atol"] == 1e-9
        assert config["numerical_version"] == 2
        assert config["bootstrap_generator"] == "PCG64"
        with pytest.raises(ValueError, match="between 0 and 1e-9"):
            probability_tolerance({**config, "probability_atol": 1e-6})
    finally:
        ledger.db.close()
