"""Verification caches freeze one snapshot and never carry state to another run."""

import json
import sqlite3

import pytest


def campaign(tmp_path):
    from translucent.intervention import create
    from translucent.ledger import Ledger
    from translucent.systems.nist_heat import ingest

    ledger = Ledger(tmp_path / "ledger")
    c = create(ledger)
    trials = ingest(ledger, c.id, tmp_path / "sealed", selected={15, 22, 28})
    return ledger, c, trials


def test_snapshot_cache_reuses_features_but_refuses_writes_and_unrevealed_feedback(
    tmp_path, monkeypatch
):
    from translucent.intervention import example, records
    from translucent import intervention_models
    from translucent.intervention_verify_cache import verification_cache

    ledger, c, trials = campaign(tmp_path)
    original = intervention_models.compute_features
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(intervention_models, "compute_features", counted)
    try:
        with verification_cache(ledger) as cache:
            first = example(ledger, c.id, trials[0].id)
            assert example(ledger, c.id, trials[0].id) == first
            assert len(calls) == 1
            assert cache.examples
            selected = records(ledger, c.id, "trial")
            selected.clear()
            assert len(records(ledger, c.id, "trial")) == 3
            with pytest.raises(ValueError, match="not been revealed"):
                example(ledger, c.id, trials[0].id, training=True)
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                ledger.db.execute("CREATE TABLE forbidden_during_verification (value)")
        assert not hasattr(ledger, "_intervention_verification_cache")
        assert ledger.db.execute("PRAGMA query_only").fetchone()[0] == 0
        example(ledger, c.id, trials[0].id)
        assert len(calls) == 3
    finally:
        ledger.db.close()


def test_fresh_verification_detects_tampering_after_a_successful_cached_run(tmp_path):
    from translucent.verify import verify

    ledger, _, trials = campaign(tmp_path)
    try:
        assert verify(ledger.root).ok
        ledger.db.execute("DROP TRIGGER record_no_update")
        changed = trials[0].model_dump()
        changed["data"]["t_heater_off"] += 1
        ledger.db.execute(
            "UPDATE record SET body=? WHERE id=?",
            (json.dumps(changed), trials[0].id),
        )
        checked = verify(ledger.root)
        assert not checked.ok
        assert any("hash chain" in value for value in checked.problems)
    finally:
        ledger.db.close()


def test_cache_drops_revealed_examples_between_read_snapshots(tmp_path):
    from translucent.intervention import commit_decision, example, reveal
    from translucent.intervention_verify_cache import verification_cache

    ledger, c, trials = campaign(tmp_path)
    try:
        with verification_cache(ledger):
            example(ledger, c.id, trials[0].id)
            with pytest.raises(ValueError, match="not been revealed"):
                example(ledger, c.id, trials[0].id, training=True)
        commit_decision(
            ledger, c.id, trials[0].id, "training", "abstain", 0.5, [0, 1], scored=False
        )
        opened = reveal(ledger, c.id, trials[0].id, tmp_path / "sealed")
        with verification_cache(ledger):
            trained = example(ledger, c.id, trials[0].id, training=True)
            assert trained["reveal_id"] == opened.id
            assert trained["y"] == int(opened.data["outcome"] != "rescued")
    finally:
        ledger.db.close()


def test_cache_preserves_each_original_query_order_when_insertion_clock_differs(
    tmp_path,
):
    from translucent.intervention import records
    from translucent.intervention_verify_cache import verification_cache

    ledger, c, trials = campaign(tmp_path)
    try:
        ledger.db.execute("DROP TRIGGER record_no_update")
        ledger.db.execute(
            "UPDATE record SET inserted_at=0 WHERE id=?", (trials[-1].id,)
        )
        all_ids = [r.id for r in records(ledger, c.id)]
        trial_ids = [r.id for r in records(ledger, c.id, "trial")]
        joint_ids = [
            rid
            for (rid,) in ledger.db.execute(
                "SELECT id FROM record WHERE kind='intervention' AND campaign_id=? "
                "AND json_extract(body,'$.type') IN ('protocol','trial') ORDER BY rowid",
                (c.id,),
            )
        ]
        with verification_cache(ledger) as cache:
            assert [r.id for r in records(ledger, c.id)] == all_ids
            assert [r.id for r in records(ledger, c.id, "trial")] == trial_ids
            assert [
                r.id for r in cache.select(c.id, ("protocol", "trial"))
            ] == joint_ids
    finally:
        ledger.db.close()
