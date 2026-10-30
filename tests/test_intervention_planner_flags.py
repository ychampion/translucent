"""Planner views exclude quality annotations derived from the later outcome."""

import json
from types import SimpleNamespace

from translucent.intervention import records
from translucent.intervention_live import _context
from translucent.intervention_muse import terminal_messages, trial_messages
from translucent.intervention_runtime import InterventionTools, open_intervention
from translucent.ledger import Ledger


HIDDEN_FIELDS = {
    "flags",
    "event_sources",
    "corroboration",
    "truncated_at",
    "t_ignition",
    "outcome",
}


def test_planner_views_hide_outcome_correlated_flags_but_keep_ledger_flags(tmp_path):
    ledger = Ledger(tmp_path / "ledger")
    try:
        campaign, fold, store = open_intervention(
            ledger,
            tmp_path / "sealed",
            selected={28, 33, 67},
            min_trials=1,
        )
        tools = InterventionTools(ledger, campaign.id, fold.id, store)
        trial_records = records(ledger, campaign.id, "trial")
        assert any("heater_off_inferred" in t.data["flags"] for t in trial_records)
        assert any("suspect_soc" in t.data["flags"] for t in trial_records)

        listed = tools.call("list_trials", {})
        visible = [listed, _context(tools)]
        for trial in listed:
            assert HIDDEN_FIELDS.isdisjoint(trial)
            for name in ("get_trace", "compute_features"):
                view = tools.call(name, {"trial_id": trial["id"]})
                assert HIDDEN_FIELDS.isdisjoint(view)
                visible.append(view)

        encoded = json.dumps(visible)
        assert "heater_off_inferred" not in encoded
        assert "suspect_soc" not in encoded
        assert all("flags" in t.data for t in trial_records)
    finally:
        ledger.db.close()


def test_muse_initial_and_paid_prompts_hide_all_quality_annotations(tmp_path):
    ledger = Ledger(tmp_path / "ledger")
    try:
        campaign, fold, _ = open_intervention(
            ledger, tmp_path / "sealed", selected={28}, min_trials=1
        )
        trial = next(
            t for t in records(ledger, campaign.id, "trial") if t.data["exp"] == 28
        )
        assert "heater_off_inferred" in trial.data["flags"]
        first = trial_messages(ledger, campaign.id, trial.id)
        initial = SimpleNamespace(data={"trial_id": trial.id, "observe_s": 30})
        paid = SimpleNamespace(
            data={
                "available_until": trial.data["t_heater_off"] + 30,
                "trace": {
                    "t": [trial.data["t_heater_off"] + 1],
                    "temperature_C": [175.0],
                },
            }
        )
        final = terminal_messages(
            ledger,
            campaign.id,
            initial,
            {"role": "assistant", "content": '{"action":"observe"}'},
            paid,
        )
        for messages in (first, final):
            encoded = json.dumps(messages)
            assert "heater_off_inferred" not in encoded
            assert "suspect_soc" not in encoded
            for message in messages:
                if message["role"] == "user":
                    payload = json.loads(message["content"])
                    assert HIDDEN_FIELDS.isdisjoint(payload)
                    assert HIDDEN_FIELDS.isdisjoint(payload.get("metadata", {}))
        assert final[-1]["content"] != first[-1]["content"]
    finally:
        ledger.db.close()
