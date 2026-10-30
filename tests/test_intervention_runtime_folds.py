"""A planner's final claim covers every registered held-out condition or format."""
import pytest


@pytest.mark.parametrize('split, expected_folds', [('loco', 6), ('lofo', 2)])
def test_scripted_runtime_advances_all_folds_with_fresh_seals(tmp_path, monkeypatch, split, expected_folds):
    import translucent.intervention_runtime as runtime
    from translucent.intervention import records
    from translucent.intervention_models import PolicySpec
    from translucent.ledger import Ledger
    from translucent.verify import verify

    monkeypatch.setattr(runtime, 'scripted_specs', lambda: {
        'constant prior': PolicySpec(family='constant'),
        'generic level': PolicySpec(family='threshold', features=['level'])
    })
    ledger = Ledger(tmp_path / 'ledger')
    selected = {15, 16, 22, 28, 35, 65, 66, 67}
    campaign, initial_fold, store = runtime.open_intervention(
        ledger, tmp_path / 'sealed', split=split, selected=selected, min_trials=len(selected)
    )
    tools = runtime.InterventionTools(ledger, campaign.id, initial_fold.id, store)
    with pytest.raises(ValueError, match='reveal'):
        tools.call('advance', {})
    result = runtime.InterventionCampaigner(ledger, campaign, initial_fold, store, runtime.ScriptedPlanner()).run()
    assert result['procedure']['claim_ids']
    first_reveal = min(r.seq for r in records(ledger, campaign.id, 'reveal'))
    assert all(p.seq < first_reveal for p in records(ledger, campaign.id, 'policy'))
    assert all(h.seq < first_reveal for h in records(ledger, campaign.id, 'hypothesis'))
    all_folds = records(ledger, campaign.id, 'fold')
    folds = [f for f in all_folds if f.data['scope'] != 'baseline_registration']
    assert len(folds) == expected_folds
    trials = records(ledger, campaign.id, 'trial')
    assert len({t.data['scope'] for t in trials}) == expected_folds
    assert len({t.data['sealed_hash'] for t in trials}) == len(trials)
    trial_by_id = {t.id: t for t in trials}
    held_out = [trial_by_id[i].data['exp'] for fold in folds for i in fold.data['test_ids']]
    assert sorted(held_out) == sorted(selected)
    for review in records(ledger, campaign.id, 'review'):
        if review.data['verdict'] == 'supported':
            assert review.data['computed']['n'] == len(selected)
    # Each fit names only its own freshly sealed fold's revealed training records.
    for fitted in records(ledger, campaign.id, 'fit'):
        own = next(f for f in all_folds if f.id == fitted.data['fold_id'])
        assert fitted.data['train_ids'] == own.data['train_ids']
        assert all(trial_by_id[i].data['scope'] == own.data['scope'] for i in fitted.data['train_ids'])
    assert verify(ledger.root).ok


def test_planner_cannot_replace_frozen_action_with_an_undeclared_abstention(tmp_path):
    from translucent.intervention import propose_policy,commit_decision,reveal
    from translucent.intervention_bench import submit_policy
    from translucent.intervention_models import PolicySpec
    from translucent.intervention_runtime import InterventionTools,open_intervention
    from translucent.ledger import Ledger
    ledger=Ledger(tmp_path/'ledger')
    campaign,fold,store=open_intervention(ledger,tmp_path/'sealed',selected={15,16,28,35},min_trials=1)
    policy=propose_policy(ledger,campaign.id,'fixed escalation',PolicySpec(family='constant'),kind='agent')
    tools=InterventionTools(ledger,campaign.id,fold.id,store)
    for trial_id in fold.data['train_ids']:
        commit_decision(ledger,campaign.id,trial_id,'training','abstain',.5,[0,1],scored=False)
        reveal(ledger,campaign.id,trial_id,store)
    tools.call('evaluate_policy',{'policy_id':policy.id});submit_policy(ledger,campaign.id,policy.id)
    trial_id=fold.data['test_ids'][0]
    prediction=tools.call('predict',{'policy_id':policy.id,'trial_id':trial_id})['world_model']
    with pytest.raises(ValueError,match='declared decision rule'):
        tools.call('commit_decision',dict(policy_id=policy.id,trial_id=trial_id,decision='abstain',
                   p_ignite=prediction['p_ignite'],interval=prediction['interval']))


@pytest.mark.parametrize('split', ['loco', 'lofo'])
def test_policy_registration_precedes_any_cross_validation_feedback(tmp_path, split):
    from translucent.intervention import commit_decision, reveal
    from translucent.intervention_runtime import InterventionTools, open_intervention
    from translucent.ledger import Ledger

    ledger = Ledger(tmp_path / 'ledger')
    campaign, fold, store = open_intervention(ledger, tmp_path / 'sealed', split=split, selected={15, 16, 28, 35, 65, 66}, min_trials=1)
    tools = InterventionTools(ledger, campaign.id, fold.id, store)
    first = tools.call('propose_policy', {'name': 'registered generic level', 'spec': {'family': 'threshold', 'features': ['level']}})
    assert first['id']
    trial_id = fold.data['train_ids'][0]
    commit_decision(ledger, campaign.id, trial_id, 'training', 'abstain', .5, [0, 1], scored=False)
    reveal(ledger, campaign.id, trial_id, store)
    with pytest.raises(ValueError, match='before.*reveal'):
        tools.call('propose_policy', {'name': 'late generic level', 'spec': {'family': 'threshold', 'features': ['level']}})
