import json
import os

import pytest

from translucent.ledger import Ledger
from translucent.verify import verify


def setup_campaign(tmp_path):
    from translucent.intervention import create, commit_decision, reveal
    from translucent.systems.nist_heat import ingest
    ledger = Ledger(tmp_path / 'ledger')
    campaign = create(ledger)
    store = tmp_path / 'sealed'
    trials = ingest(ledger, campaign.id, store, scope='test')
    return ledger, campaign, store, trials, commit_decision, reveal


def test_reconstruction_and_sealed_ingest(tmp_path):
    from translucent.systems.nist_heat import load_dataset
    trials = load_dataset()
    assert len(trials) == 77
    usable = [t for t in trials if t['delay_after_vent_s'] is not None and t['t']]
    assert len(usable) == 50
    assert {t['exp'] for t in usable if 'heater_off_inferred' in t['flags']} == {28,35,38,43,46}
    assert {t['exp'] for t in usable if 'censored' in t['flags']} == {67,73}
    assert 'suspect_soc' in next(t for t in trials if t['exp'] == 33)['flags']
    assert sum(t['truncated_at'] is not None for t in usable) == 12
    assert next(t for t in trials if t['exp'] == 81)['t_heater_off'] == 626
    for t in usable:
        if t['truncated_at'] is not None:
            assert t['t'][-1] == t['truncated_at']
        assert t['event_sources']['t_heater_off']
    ledger, c, store, records, _, _ = setup_campaign(tmp_path)
    assert len(records) == 50
    if os.name != "nt":  # Windows has no POSIX permission bits
        assert store.stat().st_mode & 0o077 == 0
    bodies = [json.loads(b) for (b,) in ledger.db.execute("SELECT body FROM record WHERE kind='intervention'")]
    assert all('outcome' not in r['data'] for r in bodies)
    assert verify(ledger.root).ok


def test_reveal_requires_commit_and_matches_seal(tmp_path):
    ledger, c, store, trials, commit, reveal = setup_campaign(tmp_path)
    trial = trials[0]
    with pytest.raises(ValueError, match='commit'):
        reveal(ledger, c.id, trial.id, store)
    decision = commit(ledger, c.id, trial.id, 'training', 'escalate', 1.0, [1,1], scored=False)
    opened = reveal(ledger, c.id, trial.id, store)
    assert decision.seq < opened.seq
    assert verify(ledger.root).ok
    ledger.db.execute('DROP TRIGGER record_no_update')
    bad = opened.model_dump()
    bad['data']['outcome'] = 'ignited' if opened.data['outcome'] == 'rescued' else 'rescued'
    ledger.db.execute('UPDATE record SET body=? WHERE id=?', (json.dumps(bad), opened.id))
    result = verify(ledger.root)
    assert not result.ok
    assert any('seal' in p.lower() for p in result.problems)


def test_verifier_refuses_reveal_before_commit(tmp_path):
    ledger, c, store, trials, commit, reveal = setup_campaign(tmp_path)
    trial = trials[0]
    d = commit(ledger, c.id, trial.id, 'training', 'abstain', .5, [0,1], scored=False)
    r = reveal(ledger, c.id, trial.id, store)
    ledger.db.execute('DROP TRIGGER record_no_update')
    bad = d.model_dump(); bad['seq'] = r.seq + 1
    ledger.db.execute('UPDATE record SET body=? WHERE id=?', (json.dumps(bad), d.id))
    result = verify(ledger.root)
    assert not result.ok
    assert any('before' in p for p in result.problems)
