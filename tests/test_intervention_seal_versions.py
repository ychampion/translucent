"""Outcome commitments and recorded traces defend the reveal boundary."""

import hashlib
import json
import zipfile

import pytest

from translucent.intervention import (
    RuleRejected,
    admit,
    canonical,
    commit_decision,
    create,
    example,
    propose_policy,
    record,
    records,
    reveal,
    seal,
    _digest,
)
from translucent.intervention_actions import observe
from translucent.intervention_errors import ToolRejected
from translucent.ledger import AdmissionError, Ledger
from translucent.systems.nist_heat import (
    ignition_time,
    ingest,
    read_workbook,
    visual_corroboration,
)
from translucent.verify import verify


def _sealed_path(store, trial_id):
    return store / (hashlib.sha256(trial_id.encode()).hexdigest() + ".json")


def _rehash(ledger, cid, changes):
    """Forge an unsigned receipt consistently, beyond admission checks."""
    ledger.db.execute("DROP TRIGGER record_no_update")
    ledger.db.execute("DROP TRIGGER event_no_update")
    previous = ""
    for row in records(ledger, cid):
        update = changes.get(row.id)
        if update:
            row = row.model_copy(update={"data": {**row.data, **update}})
        row = row.model_copy(update={"previous_hash": previous})
        row = row.model_copy(update={"digest": _digest(row)})
        previous = row.digest
        ledger.db.execute(
            "UPDATE record SET body=? WHERE id=?", (row.model_dump_json(), row.id)
        )
        event = next(e for e in ledger.events(cid) if e.seq == row.seq)
        event = event.model_copy(
            update={"data": {"record": row.model_dump(mode="json")}}
        )
        ledger.db.execute(
            "UPDATE event SET body=? WHERE campaign_id=? AND seq=?",
            (event.model_dump_json(), cid, row.seq),
        )


def _generic(tmp_path, *, ignition=198):
    ledger = Ledger(tmp_path / "ledger")
    campaign = create(ledger, min_trials=1)
    t = list(range(261))
    y = [100.0 if x < 200 else 200.0 for x in t]
    source = ledger.put_json({"t": t, "temperature_C": y}, kind="series")
    payload = {
        "outcome": "ignited",
        "salt": "legacy-salt",
        "t_ignition": ignition,
        "flags": [],
        "truncated_at": None,
        "event_sources": {},
        "simulated": False,
    }
    trial = record(
        ledger,
        campaign.id,
        "trial",
        {
            "scope": "s",
            "trial_key": "Exp1",
            "exp": 1,
            "condition": {"format": "18650", "soc": 100},
            "delay_after_vent_s": 5,
            "series_artifact": source.id,
            "t_heater_on": 0,
            "t_vent": 175,
            "t_heater_off": 180,
            "flags": [],
            "event_sources": {},
            "sealed_hash": seal(payload["outcome"], payload["salt"]),
        },
    )
    return ledger, campaign, trial, payload


@pytest.mark.parametrize(
    "field,value",
    [
        ("t_ignition", 99999),
        ("flags", ["censored"]),
        ("truncated_at", 99999),
        ("event_sources", {"t_ignition": "edited"}),
    ],
)
def test_v2_seal_rejects_every_outcome_annotation_at_reveal(tmp_path, field, value):
    ledger = Ledger(tmp_path / "ledger")
    campaign = create(ledger)
    store = tmp_path / "sealed"
    trial = ingest(ledger, campaign.id, store, selected={28})[0]
    assert trial.data["seal_version"] == 2
    commit_decision(
        ledger, campaign.id, trial.id, "training", "escalate", 1, [1, 1], scored=False
    )
    path = _sealed_path(store, trial.id)
    payload = json.loads(path.read_text())
    payload[field] = value
    path.write_text(json.dumps(payload))
    with pytest.raises(AdmissionError, match="seal"):
        reveal(ledger, campaign.id, trial.id, store)


def test_v2_seal_commits_canonical_payload_and_verifier_checks_it(tmp_path):
    ledger = Ledger(tmp_path / "ledger")
    campaign = create(ledger)
    store = tmp_path / "sealed"
    trial = ingest(ledger, campaign.id, store, selected={28})[0]
    commit_decision(
        ledger, campaign.id, trial.id, "training", "escalate", 1, [1, 1], scored=False
    )
    opened = reveal(ledger, campaign.id, trial.id, store)
    fields = {
        k: opened.data[k]
        for k in ("outcome", "t_ignition", "flags", "truncated_at", "event_sources")
    }
    expected = hashlib.sha256(
        canonical(fields) + opened.data["salt"].encode()
    ).hexdigest()
    assert expected == trial.data["sealed_hash"]
    assert verify(ledger.root).ok
    _rehash(
        ledger, campaign.id, {opened.id: {"flags": opened.data["flags"] + ["censored"]}}
    )
    result = verify(ledger.root)
    assert not result.ok
    assert any("seal" in p for p in result.problems)
    assert any(
        "flags differs from source-trace reconstruction" in p for p in result.problems
    )


def test_legacy_v1_seal_stays_verifiable(tmp_path):
    ledger, campaign, trial, payload = _generic(tmp_path)
    commit_decision(
        ledger, campaign.id, trial.id, "training", "escalate", 1, [1, 1], scored=False
    )
    record(ledger, campaign.id, "reveal", {"trial_id": trial.id, **payload})
    assert verify(ledger.root).ok


def test_legacy_nist_payload_annotations_are_reconstructed(tmp_path):
    ledger = Ledger(tmp_path / "ledger")
    campaign = create(ledger)
    store = tmp_path / "sealed"
    trial = ingest(ledger, campaign.id, store, selected={28})[0]
    commit_decision(
        ledger, campaign.id, trial.id, "training", "escalate", 1, [1, 1], scored=False
    )
    opened = reveal(ledger, campaign.id, trial.id, store)
    _rehash(
        ledger,
        campaign.id,
        {
            trial.id: {
                "seal_version": 1,
                "sealed_hash": seal(opened.data["outcome"], opened.data["salt"]),
            },
            opened.id: {"flags": opened.data["flags"] + ["censored"]},
        },
    )
    result = verify(ledger.root)
    assert not result.ok
    assert any(
        "flags differs from source-trace reconstruction" in p for p in result.problems
    )
    assert not any("does not match seal" in p for p in result.problems)


def test_paid_watch_cannot_hide_earlier_recorded_ignition(tmp_path):
    ledger, campaign, trial, payload = _generic(tmp_path, ignition=250)
    first = commit_decision(
        ledger,
        campaign.id,
        trial.id,
        "training",
        "observe",
        1,
        [1, 1],
        scored=False,
        observe_s=60,
    )
    observation = observe(ledger, campaign.id, trial.id, "training", 60)
    assert (
        ignition_time(
            observation.data["trace"]["t"], observation.data["trace"]["temperature_C"]
        )
        == 198
    )
    commit_decision(
        ledger,
        campaign.id,
        trial.id,
        "training",
        "escalate",
        1,
        [1, 1],
        scored=False,
        observe_s=60,
        parent_decision_id=first.id,
    )
    record(ledger, campaign.id, "reveal", {"trial_id": trial.id, **payload})
    result = verify(ledger.root)
    assert not result.ok
    assert any("paid observation contains ignition jump" in p for p in result.problems)


def test_ignited_training_watch_with_unknown_time_is_rejected(tmp_path):
    ledger, campaign, trial, payload = _generic(tmp_path, ignition=None)
    commit_decision(
        ledger, campaign.id, trial.id, "training", "escalate", 1, [1, 1], scored=False
    )
    record(ledger, campaign.id, "reveal", {"trial_id": trial.id, **payload})
    with pytest.raises(ValueError, match="watch safety is unknown"):
        example(ledger, campaign.id, trial.id, training=True, post_s=30)


def test_paid_watch_reveal_with_unknown_ignition_time_is_controller_failure(tmp_path):
    ledger, campaign, trial, payload = _generic(tmp_path, ignition=None)
    first = commit_decision(
        ledger,
        campaign.id,
        trial.id,
        "training",
        "observe",
        1,
        [1, 1],
        scored=False,
        observe_s=30,
    )
    observe(ledger, campaign.id, trial.id, "training", 30)
    commit_decision(
        ledger,
        campaign.id,
        trial.id,
        "training",
        "escalate",
        1,
        [1, 1],
        scored=False,
        observe_s=30,
        parent_decision_id=first.id,
    )
    with pytest.raises(AdmissionError, match="watch safety is unknown") as missing:
        record(ledger, campaign.id, "reveal", {"trial_id": trial.id, **payload})
    assert not isinstance(missing.value, ToolRejected)


def test_muse_decision_requires_receipt_even_when_unscored(tmp_path):
    ledger, campaign, trial, _ = _generic(tmp_path)
    policy = propose_policy(
        ledger, campaign.id, "Muse comparator", {"family": "muse"}, kind="baseline"
    )
    with pytest.raises(AdmissionError, match="requires a model receipt"):
        commit_decision(
            ledger,
            campaign.id,
            trial.id,
            policy.id,
            "escalate",
            1,
            [1, 1],
            scored=False,
        )


def test_nonnumeric_workbook_cell_is_not_silently_dropped(tmp_path):
    workbook = tmp_path / "bad.xlsx"
    xml = """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
    <row r="1"><c r="A1" t="inlineStr"><is><t>Time</t></is></c><c r="B1" t="inlineStr"><is><t>Exp1</t></is></c></row>
    <row r="2"/><row r="3"><c r="A3"><v>0</v></c><c r="B3" t="inlineStr"><is><t>broken</t></is></c></row>
    </sheetData></worksheet>"""
    with zipfile.ZipFile(workbook, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", xml)
    with pytest.raises(ValueError, match="Exp1 at worksheet row 3"):
        read_workbook(workbook)


def test_missing_corroborating_labels_fail_ingestion_explicitly(tmp_path):
    with pytest.raises(FileNotFoundError, match="Required corroborating label reading"):
        visual_corroboration(tmp_path)


def test_model_preconditions_are_distinct_from_integrity_failures(tmp_path):
    ledger, campaign, trial, payload = _generic(tmp_path)
    with pytest.raises(RuleRejected, match="commit") as rejected:
        record(ledger, campaign.id, "reveal", {"trial_id": trial.id, **payload})
    assert isinstance(rejected.value, ToolRejected)
    assert isinstance(rejected.value, AdmissionError)
    commit_decision(
        ledger, campaign.id, trial.id, "training", "escalate", 1, [1, 1], scored=False
    )
    with pytest.raises(RuleRejected, match="Duplicate"):
        commit_decision(
            ledger,
            campaign.id,
            trial.id,
            "training",
            "escalate",
            1,
            [1, 1],
            scored=False,
        )
    with pytest.raises(AdmissionError, match="seal") as broken:
        record(
            ledger,
            campaign.id,
            "reveal",
            {"trial_id": trial.id, **payload, "outcome": "rescued"},
        )
    assert not isinstance(broken.value, ToolRejected)
    with pytest.raises(AdmissionError, match="hash chain") as chain:
        admit(ledger, trial.model_copy(update={"digest": "broken"}))
    assert not isinstance(chain.value, ToolRejected)


def test_corrupt_sealed_store_is_not_a_model_rejection(tmp_path):
    ledger, campaign, trial, _ = _generic(tmp_path)
    commit_decision(
        ledger, campaign.id, trial.id, "training", "escalate", 1, [1, 1], scored=False
    )
    store = tmp_path / "sealed"
    store.mkdir()
    with pytest.raises(OSError):
        reveal(ledger, campaign.id, trial.id, store)
    _sealed_path(store, trial.id).write_text("not JSON")
    with pytest.raises(json.JSONDecodeError) as malformed:
        reveal(ledger, campaign.id, trial.id, store)
    assert not isinstance(malformed.value, ToolRejected)
