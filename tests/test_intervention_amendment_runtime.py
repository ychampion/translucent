"""Measured risk and paid observations remain visible under the cost protocol."""

def test_scripted_planner_finishes_a_paid_watch_before_reveal(tmp_path, monkeypatch):
    import translucent.intervention_runtime as runtime
    from translucent.intervention import records
    from translucent.intervention_models import PolicySpec
    from translucent.ledger import Ledger
    from translucent.verify import verify

    monkeypatch.setattr(runtime, 'scripted_specs', lambda: {
        'watch generic slope': PolicySpec(family='threshold', features=['post_slope'], observation_s=30)
    })

    class WatchingPlanner(runtime.ScriptedPlanner):
        def next(self, messages, tools):
            response = super().next(messages, tools)
            if response.get('tool') == 'propose_policy':
                response['arguments']['decision_rule'] = {'mode': 'always_observe', 'observe_s': 30}
            return response

    ledger = Ledger(tmp_path / 'ledger')
    campaign, fold, store = runtime.open_intervention(
        ledger, tmp_path / 'sealed', selected={15, 16, 22, 28, 35, 65, 66, 67}, min_trials=1
    )
    result = runtime.InterventionCampaigner(ledger, campaign, fold, store, WatchingPlanner()).run()
    decisions = records(ledger, campaign.id, 'decision')
    watches = [d for d in decisions if d.data['decision'] == 'observe']
    assert watches
    for watch in watches:
        terminal = next(d for d in decisions if d.data.get('parent_decision_id') == watch.id)
        feedback = next(r for r in records(ledger, campaign.id, 'reveal') if r.data['trial_id'] == watch.data['trial_id'])
        assert watch.seq < terminal.seq < feedback.seq
        assert terminal.data['decision'] in ('stand_down', 'escalate')
    assert result['procedure']['claim_ids']
    assert verify(ledger.root).ok


def test_supported_procedure_reports_observed_risk_instead_of_requiring_zero_errors(tmp_path):
    from translucent import gate
    from translucent.intervention import create, propose_policy, register_fold, fit_policy, commit_decision, reveal
    from translucent.intervention_bench import submit_policy, propose_claim
    from translucent.intervention_models import PolicySpec
    from translucent.intervention_runtime import write_procedure
    from translucent.ledger import Ledger
    from translucent.systems.nist_heat import ingest

    ledger = Ledger(tmp_path / 'ledger')
    campaign = create(ledger, min_trials=1)
    policy = propose_policy(ledger, campaign.id, 'recorded stand-down example', PolicySpec(family='constant', probability=0), kind='baseline')
    submit_policy(ledger, campaign.id, policy.id)
    trials = ingest(ledger, campaign.id, tmp_path / 'sealed', scope='default', selected={15, 16, 28, 35})
    fold = register_fold(ledger, campaign.id, 'default', 'loco', [], [t.id for t in trials])
    fitted = fit_policy(ledger, campaign.id, policy.id, fold.id)
    for trial in trials:
        commit_decision(ledger, campaign.id, trial.id, policy.id, 'stand_down', 0, [0, 0], fit_id=fitted.id)
        reveal(ledger, campaign.id, trial.id, tmp_path / 'sealed')
    claim = propose_claim(ledger, campaign.id, dict(type='policy_performance', policy_id=policy.id, split='loco', subset='all', n=4, unsafe=2))
    assert gate.review(ledger, claim.id).data['verdict'] == 'supported'
    procedure = write_procedure(ledger, campaign.id, policy.id, [claim.id])
    assert '2 unsafe' in procedure['text']
    assert 'mean decision cost' in procedure['text']
    assert procedure['rule']['world_model'] == policy.data['world_model']
    assert any('not a validated' in limit for limit in procedure['limits'])


def test_export_keeps_corroborating_labels_sealed_until_reveal(tmp_path):
    from translucent.intervention import create, commit_decision, reveal
    from translucent.intervention_export import projection
    from translucent.ledger import Ledger
    from translucent.systems.nist_heat import ingest

    ledger = Ledger(tmp_path / 'ledger')
    campaign = create(ledger, heater_off_lag_s=7)
    trial = ingest(ledger, campaign.id, tmp_path / 'sealed', selected={15})[0]
    before = projection(ledger, campaign.id)
    assert before.trials[0]['corroboration'] is None
    assert before.trials[0]['outcome'] is None
    assert '7 s' in before.system['decision_point']
    commit_decision(ledger, campaign.id, trial.id, 'training', 'abstain', .5, [0, 1], scored=False)
    feedback = reveal(ledger, campaign.id, trial.id, tmp_path / 'sealed')
    after = projection(ledger, campaign.id)
    assert after.trials[0]['corroboration'] == feedback.data['corroboration']
    assert after.trials[0]['corroboration']['agree'] is True
    assert {'image', 'vector', 'trace', 'agree'} <= after.trials[0]['corroboration'].keys()
