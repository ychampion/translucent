"""Registered timing sensitivity and sealed, independent label corroboration."""
import hashlib
import json

import pytest


def test_lag_sensitivity_moves_visible_breaks_and_records_provenance():
    from translucent.systems.nist_heat import load_dataset
    default={r['exp']:r for r in load_dataset()}
    shifted={r['exp']:r for r in load_dataset(heater_off_lag_s=7)}
    assert default[81]['t_heater_off']==626
    assert shifted[81]['t_heater_off']==629
    for exp,row in default.items():
        if row['t_heater_off'] is None:continue
        other=shifted[exp]
        expected=0 if 'heater_off_inferred' in row['flags'] else 3
        assert other['t_heater_off']-row['t_heater_off']==expected
        assert other['t_vent']-row['t_vent']==expected
        assert other['heater_off_lag_s']==7
        if expected:assert 'minus 7 s lag' in other['event_sources']['t_heater_off']
        assert (other['truncated_at'] is None)==(row['truncated_at'] is None)


@pytest.mark.parametrize('lag',[-1,float('nan'),float('inf'),61])
def test_invalid_lag_is_refused(lag):
    from translucent.systems.nist_heat import reconstruct
    with pytest.raises(ValueError,match='heater_off_lag_s'):
        reconstruct([],[],'18650',100,10,'ignited',heater_off_lag_s=lag)


def test_corroboration_uses_binary_trace_and_never_invents_missing_trace():
    from translucent.systems.nist_heat import visual_corroboration
    rows=visual_corroboration()
    assert len(rows)==77
    assert rows[81]==dict(image='delayed_ignition',vector='delayed_ignition',trace='ignited',agree=True)
    # The red/black shape inconsistency does not change the binary ignition.
    assert rows[61]==dict(image='ignited',vector='ignited',trace='ignited',agree=True)
    for exp in (3,17):
        assert rows[exp]['trace']=='unavailable'
        assert rows[exp]['agree'] is False


def test_corroboration_stays_sealed_until_decision_then_reveals_with_hashes(tmp_path):
    from translucent.intervention import create,commit_decision,reveal,records
    from translucent.ledger import Ledger
    from translucent.systems.nist_heat import DATA,ingest,register_visual_evidence
    ledger=Ledger(tmp_path/'ledger');campaign=create(ledger);store=tmp_path/'sealed'
    trial=ingest(ledger,campaign.id,store,selected={81})[0]
    assert 'corroboration' not in trial.data
    assert all('corroboration' not in r.data for r in records(ledger,campaign.id))
    # No bulk plaintext outcome reading is imported into the ledger blob store.
    labels_hash='sha256:'+hashlib.sha256((DATA/'visual/out/task1_labels.csv').read_bytes()).hexdigest()
    assert ledger.get('artifact',labels_hash) is None
    with pytest.raises(ValueError,match='every campaign trial'):
        register_visual_evidence(ledger,campaign.id)
    commit_decision(ledger,campaign.id,trial.id,'training','escalate',1,[1,1],scored=False)
    opened=reveal(ledger,campaign.id,trial.id,store)
    assert opened.data['corroboration']['agree'] is True
    assert opened.seq>trial.seq
    for source in opened.data['corroboration_sources']:
        assert hashlib.sha256((DATA/source['path']).read_bytes()).hexdigest()==source['sha256']
    reference=register_visual_evidence(ledger,campaign.id)
    assert reference.seq>opened.seq
    assert reference.data['registered_after_reveals'] is True
    assert len(reference.data['files'])==171
    assert ledger.get('artifact',labels_hash) is not None
    assert register_visual_evidence(ledger,campaign.id).id==reference.id
    for item in reference.data['files']:
        assert ledger.get('artifact',item['artifact_id']) is not None
    with pytest.raises(ValueError,match='label audit is closed'):
        ingest(ledger,campaign.id,tmp_path/'later',selected={82})
    with pytest.raises(ValueError,match='registered protocol'):
        ingest(ledger,campaign.id,tmp_path/'other',selected={81},heater_off_lag_s=7)


def test_ingest_uses_registered_lag_and_artifact_provenance(tmp_path):
    from translucent.intervention import create
    from translucent.ledger import Ledger
    from translucent.systems.nist_heat import ingest
    ledger=Ledger(tmp_path/'ledger');campaign=create(ledger,heater_off_lag_s=7)
    trial=ingest(ledger,campaign.id,tmp_path/'sealed',selected={81})[0]
    assert trial.data['t_heater_off']==629
    assert trial.data['heater_off_lag_s']==7
    assert ledger.get('artifact',trial.data['series_artifact']).transform.params['heater_off_lag_s']==7


def test_visual_copies_have_only_expected_text_files_and_match_manifest():
    from translucent.systems.nist_heat import DATA
    root=DATA/'visual'
    manifest=json.loads((root/'MANIFEST.json').read_text())
    assert len(manifest)==170
    for entry in manifest:
        path=root/entry['path']
        assert path.suffix in ('.csv','.json')
        assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256']
    assert not any(p.suffix.lower() in ('.jpg','.jpeg','.png','.webp','.gif','.mp4') for p in root.rglob('*'))
