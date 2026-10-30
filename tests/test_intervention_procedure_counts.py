"""Procedure limits count the actual campaign's unique interrupted trials."""

from translucent.intervention import records
from translucent.intervention_models import PolicySpec
from translucent.intervention_runtime import (
    InterventionCampaigner,
    ScriptedPlanner,
    open_intervention,
)
from translucent.ledger import Ledger


def test_procedure_count_excludes_unselected_inferred_trials_and_repeated_scopes(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "translucent.intervention_runtime.scripted_specs",
        lambda: {"constant prior": PolicySpec(family="constant")},
    )
    selected = {15, 16, 22, 65, 66, 67}
    ledger = Ledger(tmp_path / "ledger")
    try:
        campaign, fold, store = open_intervention(
            ledger,
            tmp_path / "sealed",
            selected=selected,
            min_trials=1,
        )
        result = InterventionCampaigner(
            ledger, campaign, fold, store, ScriptedPlanner()
        ).run()
        trials = records(ledger, campaign.id, "trial")
        assert not any("heater_off_inferred" in t.data["flags"] for t in trials)
        assert len(trials) > len(selected)
        assert len({t.data["trial_key"] for t in trials}) == len(selected)
        assert (
            f"{len(selected)} interrupted traces in one NIST study; "
            "a case study, not a validated fire-safety procedure"
        ) in result["procedure"]["limits"]
    finally:
        ledger.db.close()
