"""Command and replay boundaries: estimates never construct a paid client."""

import json
import os
import pytest


@pytest.mark.parametrize(
    "split,folds,calls", [("loco", 8, 960), ("prequential", 50, 6000)]
)
def test_cli_dry_run_uses_registered_prices_without_files_or_keys(
    tmp_path, monkeypatch, capsys, split, folds, calls
):
    from translucent.cli import main
    from translucent import intervention_chat

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        intervention_chat, "_client", lambda *a: pytest.fail("provider constructed")
    )
    assert (
        main(
            [
                "intervene",
                "nist",
                "--profile",
                "meta",
                "--budget",
                "25",
                "--split",
                split,
                "--dry-run",
            ]
        )
        == 0
    )
    value = json.loads(capsys.readouterr().out)
    assert value["folds"] == folds
    assert value["planned_calls"] == calls
    assert value["model"] == "muse-wonder-1.0"
    assert value["prices_usd_per_million"] == {"input": 4.0, "output": 20.0}
    assert value["uncached_estimate_usd"] == pytest.approx(
        calls * (8000 * 4e-6 + 6000 * 20e-6)
    )
    assert not list(tmp_path.iterdir())


def test_muse_dry_run_counts_paid_second_calls_and_dev_profile(capsys):
    from translucent.cli import main

    assert (
        main(
            [
                "intervene",
                "nist",
                "--muse-only",
                "--profile",
                "dev",
                "--budget",
                "10",
                "--dry-run",
            ]
        )
        == 0
    )
    value = json.loads(capsys.readouterr().out)
    assert value["planned_calls"] == 50
    assert value["calls_with_all_watch_windows"] == 100
    assert value["provider"] == "fireworks"
    assert value["model"] == "accounts/fireworks/models/muse-spark-open"


@pytest.mark.parametrize("budget", ["0", "-1", "nan", "inf"])
def test_cli_rejects_invalid_budget_before_creating_state(
    tmp_path, monkeypatch, budget
):
    from translucent.cli import main

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as error:
        main(["intervene", "nist", "--budget", budget, "--dry-run"])
    assert error.value.code == 2
    assert not list(tmp_path.iterdir())


def test_budget_stop_replays_and_manifest_settings_are_fixed(tmp_path, monkeypatch):
    from translucent import intervention_chat
    from translucent.intervention_chat import Settings
    from translucent.intervention_session import run_intervention
    from translucent.verify import verify

    monkeypatch.setattr(
        intervention_chat, "_client", lambda *a: pytest.fail("provider constructed")
    )
    settings = Settings(budget_usd=0.000001)
    source = tmp_path / "recordings"
    options = dict(selected={15, 22}, min_trials=1)
    live = run_intervention(tmp_path / "live", settings, recordings=source, **options)
    replay = run_intervention(tmp_path / "replay", settings, replay=source, **options)
    assert live["status"] == replay["status"] == "budget"
    assert replay["decisions_identical"] is True
    assert replay["committed_decisions"] == 0
    assert verify(tmp_path / "replay").ok
    if os.name != "nt":  # Windows has no POSIX permission bits
        assert source.stat().st_mode & 0o777 == 0o700
        assert (source / "manifest.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="settings differ"):
        run_intervention(
            tmp_path / "wrong", Settings(budget_usd=1), replay=source, **options
        )
    assert not (tmp_path / "wrong").exists()
    with pytest.raises(ValueError, match="fresh ledger"):
        run_intervention(tmp_path / "replay", settings, replay=source, **options)


def test_agent_cannot_use_muse_family_to_bypass_executable_predictions(tmp_path):
    from translucent.intervention import propose_policy
    from translucent.intervention_runtime import open_intervention
    from translucent.ledger import Ledger

    ledger = Ledger(tmp_path / "ledger")
    try:
        campaign, _, _ = open_intervention(
            ledger, tmp_path / "sealed", selected={15, 22}, min_trials=1
        )
        with pytest.raises(ValueError, match="reserved for a frozen baseline"):
            propose_policy(ledger, campaign.id, "opaque agent", {"family": "muse"})
    finally:
        ledger.db.close()
