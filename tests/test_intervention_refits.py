"""Adversarial receipts must be checked independently of admission."""

from copy import deepcopy

import pytest

from translucent.intervention import (
    commit_decision,
    create,
    example,
    fit_policy,
    propose_policy,
    record,
    register_fold,
    reveal,
)
from translucent.intervention_bench import screen_policy, submit_policy
from translucent.intervention_fit_verification import check_refit
from translucent.intervention_models import PolicySpec, fit
from translucent.ledger import Ledger
from translucent.systems.nist_heat import ingest
from translucent.verify import verify


def _unchecked_record(monkeypatch, ledger, campaign_id, kind, data):
    """Forge a correctly hashed record without relying on admission to catch it."""
    with monkeypatch.context() as patch:
        patch.setattr("translucent.intervention.admit", lambda *_: None)
        return record(ledger, campaign_id, kind, data)


def _training_campaign(tmp_path, *, family="threshold"):
    ledger = Ledger(tmp_path / "ledger")
    campaign = create(ledger, splits=["loco"], min_trials=1)
    policy = propose_policy(
        ledger,
        campaign.id,
        "registered strategy",
        PolicySpec(family=family, features=["level"]),
        kind="agent",
    )
    store = tmp_path / "sealed"
    trials = ingest(
        ledger, campaign.id, store, scope="outer", selected={15, 16, 28, 35}
    )
    train = [trial for trial in trials if trial.data["condition"]["soc"] == 100]
    test = [trial for trial in trials if trial not in train]
    fold = register_fold(
        ledger,
        campaign.id,
        "outer",
        "loco",
        [trial.id for trial in train],
        [trial.id for trial in test],
    )
    for trial in train:
        commit_decision(
            ledger,
            campaign.id,
            trial.id,
            "training",
            "abstain",
            0.5,
            [0, 1],
            scored=False,
        )
        reveal(ledger, campaign.id, trial.id, store)
    fitted = fit_policy(ledger, campaign.id, policy.id, fold.id)
    return ledger, campaign, policy, fold, fitted, test


def test_repro_fit_catches_model_trained_on_hidden_held_out_labels(tmp_path):
    import hashlib
    import json

    ledger, campaign, policy, fold, fitted, test = _training_campaign(tmp_path)
    rows = [
        example(ledger, campaign.id, trial_id, training=True)
        for trial_id in fold.data["train_ids"]
    ]
    # This models an untrusted writer reading the sealed store. Its provenance
    # still claims the original training partition, exactly as review repro_fit.
    for trial in test:
        path = (
            tmp_path
            / "sealed"
            / (hashlib.sha256(trial.id.encode()).hexdigest() + ".json")
        )
        outcome = json.loads(path.read_text())["outcome"]
        rows.append(
            {
                **example(ledger, campaign.id, trial.id),
                "y": int(outcome != "rescued"),
            }
        )
    forged = fit(policy.data["spec"], rows)
    assert forged != fitted.data["model"]
    record(
        ledger,
        campaign.id,
        "fit",
        {**fitted.data, "model": forged},
    )
    result = verify(ledger.root)
    assert not result.ok
    assert any("Stored fit differs from executable refit" in p for p in result.problems)


@pytest.mark.parametrize(
    "field",
    ["survived", "candidate_models_proposed", "candidate_models_screened_out"],
)
def test_verify_recomputes_screen_counts_and_survival(tmp_path, field):
    ledger, campaign, policy, fold, fitted, _ = _training_campaign(tmp_path)
    screened = screen_policy(ledger, campaign.id, policy.id, fold.id, fitted)
    forged = deepcopy(screened.data)
    forged[field] = not forged[field] if field == "survived" else forged[field] + 1
    record(ledger, campaign.id, "screen", forged)
    result = verify(ledger.root)
    assert not result.ok
    assert any("screen counts or survival differ" in p for p in result.problems)


def test_verify_requires_a_surviving_screen_for_submission(tmp_path, monkeypatch):
    ledger, campaign, policy, _, _, _ = _training_campaign(tmp_path)
    _unchecked_record(
        monkeypatch, ledger, campaign.id, "submission", {"policy_id": policy.id}
    )
    result = verify(ledger.root)
    assert not result.ok
    assert any("no preceding surviving screen" in p for p in result.problems)


def test_verify_rejects_decision_split_different_from_fold(tmp_path, monkeypatch):
    ledger, campaign, policy, fold, fitted, test = _training_campaign(
        tmp_path, family="constant"
    )
    screen_policy(ledger, campaign.id, policy.id, fold.id, fitted)
    submit_policy(ledger, campaign.id, policy.id)
    with monkeypatch.context() as patch:
        patch.setattr("translucent.intervention.admit", lambda *_: None)
        commit_decision(
            ledger,
            campaign.id,
            test[0].id,
            policy.id,
            "escalate",
            1.0,
            [1, 1],
            fit_id=fitted.id,
            split="lofo",
        )
    result = verify(ledger.root)
    assert not result.ok
    assert any("Decision split differs" in p for p in result.problems)


def test_verify_rejects_nonprequential_fold_registered_after_training_reveal(
    tmp_path, monkeypatch
):
    ledger = Ledger(tmp_path / "ledger")
    campaign = create(ledger, splits=["loco"])
    store = tmp_path / "sealed"
    trials = ingest(ledger, campaign.id, store, scope="late", selected={15, 16})
    train, test = trials
    commit_decision(
        ledger,
        campaign.id,
        train.id,
        "training",
        "abstain",
        0.5,
        [0, 1],
        scored=False,
    )
    reveal(ledger, campaign.id, train.id, store)
    with monkeypatch.context() as patch:
        patch.setattr("translucent.intervention.admit", lambda *_: None)
        register_fold(ledger, campaign.id, "late", "loco", [train.id], [test.id])
    result = verify(ledger.root)
    assert not result.ok
    assert any(
        "registered after a training or held-out outcome reveal" in p
        for p in result.problems
    )


@pytest.mark.parametrize(
    "field", ["fit_id", "fold_id", "split", "scored", "trial_id", "policy_id"]
)
def test_verify_terminal_decision_inherits_parent_fields(tmp_path, monkeypatch, field):
    from translucent.intervention_actions import observe

    ledger = Ledger(tmp_path / "ledger")
    campaign = create(ledger)
    trials = ingest(
        ledger, campaign.id, tmp_path / "sealed", scope="watch", selected={15, 16}
    )
    initial = commit_decision(
        ledger,
        campaign.id,
        trials[0].id,
        "training",
        "observe",
        0.5,
        [0, 1],
        scored=False,
        observe_s=30,
    )
    observe(ledger, campaign.id, trials[0].id, "training", 30)
    data = {
        **initial.data,
        "decision": "escalate",
        "parent_decision_id": initial.id,
    }
    data[field] = {
        "fit_id": "another-fit",
        "fold_id": "another-fold",
        "split": "lofo",
        "scored": True,
        "trial_id": trials[1].id,
        "policy_id": "another-policy",
    }[field]
    _unchecked_record(monkeypatch, ledger, campaign.id, "decision", data)
    result = verify(ledger.root)
    assert not result.ok
    assert any(f"parent's {field}" in p for p in result.problems)


def test_refit_only_float_leaves_have_registered_absolute_tolerance():
    model = {"weights": [0.25], "count": 2, "rule": "threshold"}
    check_refit(
        model,
        {**model, "weights": [0.25 + 1e-12]},
        config={"numerical_version": 2},
        atol=1e-9,
    )
    for forged in (
        {**model, "weights": [0.25 + 1e-6]},
        {**model, "count": 2.0},
        {**model, "rule": "logistic"},
        {**model, "numerics": {}},
    ):
        with pytest.raises(ValueError, match="Stored fit differs"):
            check_refit(model, forged, config={"numerical_version": 2}, atol=1e-9)


def test_refit_cache_is_bounded_to_one_verification_snapshot(tmp_path, monkeypatch):
    from translucent.intervention import protocol
    from translucent.intervention_fit_verification import recompute_fit
    from translucent.intervention_verify_cache import verification_cache
    import translucent.intervention_models as models

    ledger, campaign, policy, fold, _, _ = _training_campaign(
        tmp_path, family="constant"
    )
    config = protocol(ledger, campaign.id).data
    calls = []
    original = models.fit

    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(models, "fit", counted)
    with verification_cache(ledger):
        one = recompute_fit(ledger, campaign.id, policy, fold, config)
        two = recompute_fit(ledger, campaign.id, policy, fold, config)
        assert one is two
        assert len(calls) == 1
        recompute_fit(ledger, campaign.id, policy, fold, {**config, "seed": 1})
        assert len(calls) == 2
    with verification_cache(ledger):
        recompute_fit(ledger, campaign.id, policy, fold, config)
        assert len(calls) == 3


@pytest.mark.parametrize("field", ["trial_id", "policy_id", "source_artifact"])
def test_verify_observation_retains_its_parent_and_source(tmp_path, monkeypatch, field):
    from translucent.intervention_actions import observe

    ledger = Ledger(tmp_path / "ledger")
    campaign = create(ledger)
    trials = ingest(
        ledger, campaign.id, tmp_path / "sealed", scope="watch", selected={15, 16}
    )
    commit_decision(
        ledger,
        campaign.id,
        trials[0].id,
        "training",
        "observe",
        0.5,
        [0, 1],
        scored=False,
        observe_s=30,
    )
    paid = observe(ledger, campaign.id, trials[0].id, "training", 30)
    changed = {
        "trial_id": trials[1].id,
        "policy_id": "another-policy",
        "source_artifact": trials[1].data["series_artifact"],
    }[field]
    _unchecked_record(
        monkeypatch,
        ledger,
        campaign.id,
        "observation",
        {**paid.data, field: changed},
    )
    result = verify(ledger.root)
    assert not result.ok
    reason = (
        "Observation source differs"
        if field == "source_artifact"
        else "Observation belongs to a different trial or policy"
    )
    assert any(reason in problem for problem in result.problems)
