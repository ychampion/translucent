"""Planner policies and immutable comparators share recorded held-out outcomes."""

import socket

import pytest

from translucent.intervention import protocol, records
from translucent.intervention_bench import comparison, score
from translucent.intervention_errors import ToolRejected
from translucent.intervention_models import PolicySpec, frozen_specs
from translucent.intervention_runtime import (
    InterventionCampaigner,
    InterventionTools,
    ScriptedPlanner,
    open_intervention,
)
from translucent.ledger import Ledger
from translucent.verify import verify


def test_scripted_campaign_scores_frozen_comparators_in_agent_holm_family(
    tmp_path, monkeypatch
):
    import translucent.intervention_runtime as runtime

    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: pytest.fail("provider network called"),
    )
    monkeypatch.setattr(
        runtime,
        "scripted_specs",
        lambda: {
            "constant prior": PolicySpec(family="constant"),
            "generic level": PolicySpec(family="threshold", features=["level"]),
        },
    )
    ledger = Ledger(tmp_path / "ledger")
    selected = {15, 16, 28, 35}
    campaign, fold, store = open_intervention(
        ledger, tmp_path / "sealed", selected=selected, min_trials=len(selected)
    )
    baseline_by_name = {
        row.data["name"]: row
        for row in records(ledger, campaign.id, "policy")
        if row.data["kind"] == "baseline"
    }
    assert set(baseline_by_name) == set(frozen_specs())
    assert len(baseline_by_name) == 9
    submitted_before = records(ledger, campaign.id, "submission")
    assert {s.data["policy_id"] for s in submitted_before} == {
        p.id for p in baseline_by_name.values()
    }
    screens = records(ledger, campaign.id, "screen")
    assert all(
        any(s.data["policy_id"] == p.id and s.data["survived"] for s in screens)
        for p in baseline_by_name.values()
    )
    # Baseline registration uses no outcomes and consumes no agent K slots.
    assert not records(ledger, campaign.id, "reveal")
    assert not any(
        p.data["kind"] == "agent" for p in records(ledger, campaign.id, "policy")
    )
    cap = protocol(ledger, campaign.id).data["heldout_cap_K"]
    frozen = {p.id: p.model_dump() for p in baseline_by_name.values()}
    tools = InterventionTools(ledger, campaign.id, fold.id, store)
    baseline_id = baseline_by_name["always_escalate"].id
    for tool, arguments in [
        ("evaluate_policy", {"policy_id": baseline_id}),
        ("submit_policy", {"policy_id": baseline_id}),
        (
            "commit_decision",
            {
                "policy_id": baseline_id,
                "trial_id": fold.data["test_ids"][0],
                "decision": "escalate",
                "p_ignite": 1,
                "interval": [1, 1],
            },
        ),
        (
            "observe",
            {
                "policy_id": baseline_id,
                "trial_id": fold.data["test_ids"][0],
                "observe_s": 30,
            },
        ),
    ]:
        with pytest.raises(ToolRejected, match="Frozen baseline"):
            tools.call(tool, arguments)
    with pytest.raises(ToolRejected):
        tools.call(
            "propose_policy",
            {
                "name": "replacement",
                "spec": {"family": "constant"},
                "kind": "baseline",
            },
        )
    assert {
        p.id: p.model_dump() for p in records(ledger, campaign.id, "policy")
    } == frozen
    assert records(ledger, campaign.id, "submission") == submitted_before

    result = InterventionCampaigner(
        ledger, campaign, fold, store, ScriptedPlanner()
    ).run()
    assert result["procedure"]["claim_ids"]
    agents = [
        p for p in records(ledger, campaign.id, "policy") if p.data["kind"] == "agent"
    ]
    assert len(agents) == 2 < cap
    assert len(records(ledger, campaign.id, "submission")) == 9 + len(agents)
    assert protocol(ledger, campaign.id).data["heldout_cap_K"] == cap
    assert {
        p.id: p.model_dump()
        for p in records(ledger, campaign.id, "policy")
        if p.data["kind"] == "baseline"
    } == frozen
    reveals = {
        row.data["trial_id"]: row for row in records(ledger, campaign.id, "reveal")
    }
    scored_decisions = [
        row for row in records(ledger, campaign.id, "decision") if row.data["scored"]
    ]
    for name, policy in baseline_by_name.items():
        measured = score(ledger, campaign.id, policy.id, "loco")
        if name == "muse_only":
            assert measured["n"] == 0
            assert measured["status"] == "not_run"
            continue
        assert measured["n"] == len(selected), name
        own_decisions = [
            row for row in scored_decisions if row.data["policy_id"] == policy.id
        ]
        assert own_decisions
        assert all(row.seq < reveals[row.data["trial_id"]].seq for row in own_decisions)
    for agent in agents:
        paired = comparison(ledger, campaign.id, agent.id, baseline_id, "loco")
        assert paired["n"] == len(selected)
        assert paired["inferential"] is True
        assert paired["candidates_submitted"] == len(agents)
        assert paired["baselines_submitted"] == 9
        assert paired["family_size"] == len(agents) * 9
        assert 0 <= paired["p"] <= paired["p_holm"] <= 1
    descriptive = comparison(
        ledger,
        campaign.id,
        baseline_id,
        baseline_by_name["fixed_temperature"].id,
        "loco",
    )
    assert descriptive["n"] == len(selected)
    assert descriptive["inferential"] is False
    assert descriptive["p"] is None and descriptive["p_holm"] is None
    assert descriptive["method"] == "descriptive baseline comparison"
    checked = verify(ledger.root)
    assert checked.ok, checked.problems
