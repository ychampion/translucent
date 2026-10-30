"""Descriptive variants must remain reproducible and outside primary claims."""

import pytest

from translucent.intervention_sensitivity import COST_RULE_BASELINES, cost_rule_rows


def test_cost_rule_uses_the_baselines_own_probabilities_without_a_watch():
    from translucent.intervention_actions import DEFAULT_COSTS

    rows = [
        dict(
            key=str(i),
            p=p,
            y=y,
            action="stand_down",
            unsafe=y,
            missed=0,
            abstain=0,
            correct=not y,
            observe_s=0,
            watch_time_s=0,
            ignited_during_watch=False,
            decision_id=f"decision{i}",
        )
        for i, (p, y) in enumerate([(0.0, 1), (0.05, 0), (0.1, 1), (0.25, 0)])
    ]
    changed = cost_rule_rows(rows, DEFAULT_COSTS)
    assert [r["p"] for r in changed] == [0, 0.05, 0.1, 0.25]
    assert [r["action"] for r in changed] == [
        "stand_down",
        "stand_down",
        "escalate",
        "escalate",
    ]
    assert sum(r["unsafe"] for r in changed) == 1
    assert sum(r["missed"] for r in changed) == 1
    assert not any(r["observe_s"] for r in changed)
    assert [r["decision_id"] for r in changed] == [r["decision_id"] for r in rows]
    assert rows[2]["action"] == "stand_down"  # Original commitments are untouched.


@pytest.fixture
def sensitivity_campaign(tmp_path):
    from translucent.intervention import (
        commit_decision,
        create,
        fit_policy,
        propose_policy,
        register_fold,
        reveal,
    )
    from translucent.intervention_bench import screen_policy, submit_policy
    from translucent.intervention_models import PolicySpec
    from translucent.ledger import Ledger
    from translucent.systems.nist_heat import ingest

    ledger = Ledger(tmp_path / "ledger")
    campaign = create(ledger, min_trials=1, splits=["loco"])
    agent = propose_policy(
        ledger, campaign.id, "agent", PolicySpec(family="constant"), kind="agent"
    )
    baselines = [
        propose_policy(
            ledger,
            campaign.id,
            name,
            PolicySpec(family="constant", probability=0.2),
            kind="baseline",
        )
        for name in COST_RULE_BASELINES
    ]
    store = tmp_path / "sealed"
    trials = ingest(ledger, campaign.id, store, scope="test", selected={15, 16, 28, 35})
    fold = register_fold(
        ledger, campaign.id, "test", "loco", [], [t.id for t in trials]
    )
    fits = {
        p.id: fit_policy(ledger, campaign.id, p.id, fold.id)
        for p in [agent, *baselines]
    }
    screen_policy(ledger, campaign.id, agent.id, fold.id, fits[agent.id])
    for policy in [agent, *baselines]:
        submit_policy(ledger, campaign.id, policy.id)
    for trial in trials:
        for policy in [agent, *baselines]:
            p = 1.0 if policy.id == agent.id else 0.2
            commit_decision(
                ledger,
                campaign.id,
                trial.id,
                policy.id,
                "escalate" if policy.id == agent.id else "stand_down",
                p,
                [p, p],
                fit_id=fits[policy.id].id,
            )
        reveal(ledger, campaign.id, trial.id, store)
    yield ledger, campaign, agent, baselines
    ledger.db.close()


def test_all_six_cost_rule_variants_and_sweep_verify_without_primary_family_change(
    sensitivity_campaign,
):
    from translucent.intervention import records
    from translucent.intervention_bench import comparison
    from translucent.intervention_sensitivity import write_sensitivities
    from translucent.verify import verify

    ledger, campaign, agent, baselines = sensitivity_campaign
    before = comparison(ledger, campaign.id, agent.id, baselines[0].id, "loco")
    registration = len(records(ledger, campaign.id, "submission"))
    result = write_sensitivities(ledger, campaign.id)
    assert len(result["cost_rule_scores"]) == len(COST_RULE_BASELINES)
    assert all(
        s["unsafe"] == 0 and s["mean_cost"] == 5 for s in result["cost_rule_scores"]
    )
    assert all(
        c["p"] == 1 and c["p_holm"] is None for c in result["cost_rule_comparisons"]
    )
    assert [s["threshold_c"] for s in result["threshold_sweep"]] == list(
        range(140, 201, 5)
    )
    for group in (
        "cost_rule_scores",
        "cost_rule_comparisons",
        "threshold_sweep",
        "clustered_comparisons",
    ):
        assert all(
            r["post_hoc"] and r["descriptive"] and r["outside_registered_holm_family"]
            for r in result[group]
        )
    assert len(records(ledger, campaign.id, "submission")) == registration
    assert comparison(ledger, campaign.id, agent.id, baselines[0].id, "loco") == before
    checked = verify(ledger.root)
    assert checked.ok, checked.problems


@pytest.mark.parametrize(
    "group,field,value",
    [
        ("cost_rule_scores", "mean_cost", 0),
        ("cost_rule_comparisons", "p", 0.0001),
        ("clustered_comparisons", "p_holm", 0.0001),
        ("threshold_sweep", "unsafe", 999),
        ("cost_rule_scores", "outside_registered_holm_family", False),
    ],
)
def test_verify_recomputes_post_hoc_scores_and_tests(
    sensitivity_campaign,
    group,
    field,
    value,
):
    from translucent.intervention import record
    from translucent.intervention_sensitivity import sensitivities
    from translucent.verify import verify

    ledger, campaign, _, _ = sensitivity_campaign
    forged = {**sensitivities(ledger, campaign.id)[group][0], field: value}
    record(ledger, campaign.id, "comparison" if "a" in forged else "score", forged)
    checked = verify(ledger.root)
    assert not checked.ok
    assert any("differs from" in problem for problem in checked.problems)


def test_benchmark_output_directory_and_utf8_are_explicit(tmp_path, monkeypatch):
    from translucent import intervention_bench as bench

    def fake_variant(job):
        return {"id": "test", "variant": "test"}

    monkeypatch.setattr(bench, "_benchmark_variant", fake_variant)
    monkeypatch.setattr(bench, "markdown_report", lambda _: "7–10 s; 170 °C\n")
    messages = []
    target = tmp_path / "isolated-output"
    target.mkdir()
    (target / "INTERVENE.md").write_text("previous report", encoding="utf-8")
    _, path = bench.benchmark(
        target, inferred=True, lag_s=10, unsafe_ratio=10, progress=messages.append
    )
    assert path.parent == target / "results"
    assert (target / "INTERVENE.md").read_bytes() == "7–10 s; 170 °C\n".encode("utf-8")
    assert any(str(target.resolve()) in m for m in messages)
    assert any("Replacing existing report:" in m for m in messages)
