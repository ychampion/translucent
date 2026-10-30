import itertools

import pytest

from translucent.intervention_bench import decision_cost,holm,paired_signflip


def test_paired_cost_signflip_is_exact_with_unequal_costs_and_zero_pairs():
    delta=[-100.,-100.,10.,10.,0.]
    values=[sum(s*d for s,d in zip(signs,delta[:4])) for signs in itertools.product((-1,1),repeat=4)]
    expected=sum(abs(v)>=abs(sum(delta)) for v in values)/len(values)
    got=paired_signflip(delta)
    assert got['p']==expected==.5
    assert got['discordant']==4 and got['assignments']==16 and got['method']=='exact'
    assert paired_signflip([-d for d in delta])['p']==got['p']
    assert paired_signflip([0.,0.])['p']==1.
    assert paired_signflip([-1.]*6)['p']==.03125
    assert paired_signflip([-1.]*12)['p']*99<.05
    assert paired_signflip([-1.]*11)['p']*99>.05
    assert paired_signflip([-1.]*10)['p']*18<.05
    assert paired_signflip([-1.]*9)['p']*18>.05


def test_large_paired_cost_test_uses_registered_seed_and_never_reports_zero():
    a=paired_signflip([-1.]*21,seed=8)
    b=paired_signflip([-1.]*21,seed=8)
    assert a==b and a['assignments']==100000 and a['method']=='seeded Monte Carlo'
    assert 0<a['p']<=.05 and a['monte_carlo_se']>0


def test_abstention_is_an_escalation_charge_without_double_counting():
    costs=dict(unsafe=100,watch_ignition=100,unnecessary_escalation=10,watch_per_second=.05,abstain=10)
    base=dict(unsafe=0,watch_time_s=0,abstain=0,missed=0)
    assert decision_cost({**base,'missed':1},costs)==10
    assert decision_cost({**base,'abstain':1,'missed':1},costs)==10
    assert decision_cost({**base,'watch_time_s':30},costs)==1.5
    assert decision_cost({**base,'watch_time_s':12,'unsafe':1,'ignited_during_watch':True},costs)==100.6


def test_registered_comparison_family_counts_submissions_times_baselines(tmp_path):
    from test_intervention_gate import scored_campaign
    from translucent.intervention_bench import comparison,score
    L,c,a,b,_=scored_campaign(tmp_path)
    pair=comparison(L,c.id,a.id,b.id,'loco')
    assert pair['metric']=='mean_decision_cost'
    assert pair['candidates_submitted']==1 and pair['baselines_submitted']==1 and pair['family_size']==1
    assert pair['a_mean_cost']==5 and pair['b_mean_cost']==50
    assert pair['mean_cost_difference']==-45
    assert pair['unsafe_mcnemar_p']==.5 and pair['p_holm']==.5
    s=score(L,c.id,a.id,'loco')
    assert s['mean_cost']==5 and s['total_cost']==20
    assert len(s['per_trial_costs'])==4
    assert s['mean_cost_interval'][0]<=5<=s['mean_cost_interval'][1]
    assert holm({('a','b'):.001,('c','b'):.1},4)[('a','b')]==.004


def test_gate_can_support_lower_cost_when_unsafe_counts_are_identical(tmp_path):
    from translucent.intervention import create,propose_policy,register_fold,fit_policy,commit_decision,reveal,record
    from translucent.intervention_bench import submit_policy,comparison,propose_claim
    from translucent.intervention_models import PolicySpec
    from translucent.systems.nist_heat import ingest,load_dataset
    from translucent.ledger import Ledger
    from translucent import gate
    from translucent.verify import verify
    L=Ledger(tmp_path/'ledger');c=create(L,min_trials=10);store=tmp_path/'seals'
    a=propose_policy(L,c.id,'rescue classifier',PolicySpec(family='constant',probability=0),kind='agent')
    b=propose_policy(L,c.id,'always escalate',PolicySpec(family='constant'),kind='baseline')
    selected={t['exp'] for t in load_dataset() if t['outcome']=='rescued' and t['t']}
    trials=ingest(L,c.id,store,scope='test',selected=set(sorted(selected)[:10]))
    fold=register_fold(L,c.id,'test','loco',[],[t.id for t in trials])
    fa=fit_policy(L,c.id,a.id,fold.id);fb=fit_policy(L,c.id,b.id,fold.id)
    from translucent.intervention_bench import screen_policy
    screen_policy(L,c.id,a.id,fold.id,fa)
    for policy in (a,b):submit_policy(L,c.id,policy.id)
    for trial in trials:
        commit_decision(L,c.id,trial.id,a.id,'stand_down',0.,[0,0],fit_id=fa.id)
        commit_decision(L,c.id,trial.id,b.id,'escalate',1.,[1,1],fit_id=fb.id)
        reveal(L,c.id,trial.id,store)
    pair=comparison(L,c.id,a.id,b.id,'loco')
    assert pair['a_unsafe']==pair['b_unsafe']==0 and pair['unsafe_mcnemar_p']==1
    assert pair['mean_cost_difference']==-10 and pair['p_holm']<.05
    assert pair['secondary_two_part']
    record(L,c.id,'comparison',pair)
    claim=propose_claim(L,c.id,dict(type='policy_beats',a=a.id,b=b.id,split='loco',subset='all'))
    review=gate.review(L,claim.id)
    assert review.data['verdict']=='supported' and 'Mean decision cost' in review.data['reason']
    assert verify(L.root).ok


def test_offline_verify_rejects_longer_watch_than_executable_rule(tmp_path):
    from translucent.intervention import create,propose_policy,register_fold,fit_policy,commit_decision,reveal
    from translucent.intervention_bench import submit_policy
    from translucent.intervention_models import PolicySpec
    from translucent.intervention_actions import observe
    from translucent.systems.nist_heat import ingest
    from translucent.ledger import Ledger
    from translucent.verify import verify
    L=Ledger(tmp_path/'ledger');c=create(L,min_trials=1);store=tmp_path/'seals'
    policy=propose_policy(L,c.id,'fixed 30 s watch',PolicySpec(family='constant'),kind='baseline',
                          decision_rule={'mode':'always_observe','observe_s':30})
    submit_policy(L,c.id,policy.id)
    trial=ingest(L,c.id,store,scope='watch',selected={28})[0]
    fold=register_fold(L,c.id,'watch','loco',[],[trial.id]);fitted=fit_policy(L,c.id,policy.id,fold.id)
    initial=commit_decision(L,c.id,trial.id,policy.id,'observe',1,[1,1],fit_id=fitted.id,observe_s=60)
    observe(L,c.id,trial.id,policy.id,60)
    commit_decision(L,c.id,trial.id,policy.id,'escalate',1,[1,1],fit_id=fitted.id,parent_decision_id=initial.id,observe_s=60)
    reveal(L,c.id,trial.id,store)
    checked=verify(L.root)
    assert not checked.ok and any('watch duration' in problem for problem in checked.problems)


def _prequential_constant_state(tmp_path):
    from translucent.intervention import create,propose_policy,register_fold,fit_policy,commit_decision,reveal
    from translucent.intervention_bench import submit_policy
    from translucent.intervention_models import PolicySpec
    from translucent.systems.nist_heat import ingest
    from translucent.ledger import Ledger
    L=Ledger(tmp_path/'ledger');c=create(L,min_trials=1,splits=['prequential']);store=tmp_path/'seals'
    p=propose_policy(L,c.id,'constant world model',PolicySpec(family='constant'),kind='baseline')
    submit_policy(L,c.id,p.id)
    trials=ingest(L,c.id,store,scope='history',selected={15,16,22})
    fold=register_fold(L,c.id,'history','prequential',[],[trials[0].id]);cold=fit_policy(L,c.id,p.id,fold.id)
    commit_decision(L,c.id,trials[0].id,p.id,'escalate',1,[1,1],split='prequential',fit_id=cold.id,fold_id=fold.id)
    reveal(L,c.id,trials[0].id,store)
    return L,c,p,trials,cold,store


def test_offline_verify_requires_compatibility_before_prequential_refit(tmp_path,monkeypatch):
    from translucent import intervention
    from translucent.intervention import register_fold,fit_policy,commit_decision,reveal
    from translucent.verify import verify
    L,c,p,trials,cold,store=_prequential_constant_state(tmp_path)
    fold=register_fold(L,c.id,'history','prequential',[trials[0].id],[trials[1].id])
    candidate=fit_policy(L,c.id,p.id,fold.id)
    # Build a correctly hashed receipt through the low-level writer while bypassing
    # admission. Offline verification must defend this boundary independently.
    with monkeypatch.context() as bypass:
        bypass.setattr(intervention,'admit',lambda *args:None)
        commit_decision(L,c.id,trials[1].id,p.id,'escalate',1,[1,1],split='prequential',fit_id=candidate.id,fold_id=fold.id)
    reveal(L,c.id,trials[1].id,store)
    checked=verify(L.root)
    assert not checked.ok
    assert any('no compatibility update' in problem for problem in checked.problems)


def test_offline_verify_rejects_subset_compatibility_history(tmp_path,monkeypatch):
    from translucent import intervention
    from translucent.intervention import register_fold,fit_policy,commit_decision,reveal,example,record,protocol,records
    from translucent.intervention_actions import checked_update,history_predictions,compatibility
    from translucent.verify import verify
    L,c,p,trials,cold,store=_prequential_constant_state(tmp_path)
    second=register_fold(L,c.id,'history','prequential',[trials[0].id],[trials[1].id])
    prior=fit_policy(L,c.id,p.id,second.id)
    checked_update(L,c.id,p.id,prior,cold,fold_id=second.id)
    commit_decision(L,c.id,trials[1].id,p.id,'escalate',1,[1,1],split='prequential',fit_id=prior.id,fold_id=second.id)
    reveal(L,c.id,trials[1].id,store)
    third=register_fold(L,c.id,'history','prequential',[t.id for t in trials[:2]],[trials[2].id])
    candidate=fit_policy(L,c.id,p.id,third.id)
    subset=[example(L,c.id,trials[1].id,training=True)]
    costs=protocol(L,c.id).data['costs'];rule=p.data['decision_rule']
    old=history_predictions(prior.data['model'],rule,subset,costs)
    new=history_predictions(candidate.data['model'],rule,subset,costs)
    verdict=compatibility(old,new,tolerance=.01)
    latest=records(L,c.id,'reveal')[-1]
    with monkeypatch.context() as bypass:
        bypass.setattr(intervention,'admit',lambda *args:None)
        record(L,c.id,'update',dict(policy_id=p.id,fold_id=third.id,after_trial=trials[1].id,after_reveal_id=latest.id,
               candidate_fit_id=candidate.id,previous_fit_id=prior.id,applied_fit_id=candidate.id,
               world_model_updated=True,decision_policy_updated=False,decision_rule_revalidated=True,
               previous_rule=rule,candidate_rule=rule,evaluated_trial_ids=[trials[1].id],
               old_predictions=old,new_predictions=new,**verdict))
    checked=verify(L.root)
    assert not checked.ok
    assert any('all registered training history' in problem for problem in checked.problems)


def test_offline_verify_rejects_using_a_60_second_model_after_30_seconds(tmp_path,monkeypatch):
    from translucent import intervention
    from translucent.intervention import create,propose_policy,register_fold,fit_policy,commit_decision,reveal,example
    from translucent.intervention_bench import submit_policy
    from translucent.intervention_models import PolicySpec,predict
    from translucent.intervention_actions import observe,observation_features
    from translucent.systems.nist_heat import ingest
    from translucent.ledger import Ledger
    from translucent.verify import verify
    L=Ledger(tmp_path/'ledger');c=create(L,min_trials=1);store=tmp_path/'seals'
    policy=propose_policy(L,c.id,'60 s world forced to watch 30 s',
             PolicySpec(family='threshold',features=['post_slope'],observation_s=60),kind='baseline',
             decision_rule={'mode':'always_observe','observe_s':30})
    submit_policy(L,c.id,policy.id)
    trials=ingest(L,c.id,store,scope='watch',selected={15,16,22});target=trials[0];training=trials[1:]
    fold=register_fold(L,c.id,'watch','loco',[t.id for t in training],[target.id])
    for t in training:
        commit_decision(L,c.id,t.id,'training','abstain',.5,[0,1],scored=False)
        reveal(L,c.id,t.id,store)
    fitted=fit_policy(L,c.id,policy.id,fold.id);row=example(L,c.id,target.id)
    p,h=predict(fitted.data['model'],row)
    with monkeypatch.context() as bypass:
        bypass.setattr(intervention,'admit',lambda *args:None)
        initial=commit_decision(L,c.id,target.id,policy.id,'observe',p,[p,p],fit_id=fitted.id,observe_s=30)
    observe(L,c.id,target.id,policy.id,30)
    extra=observation_features(L,c.id,target.id,policy.id,30)
    p,h=predict(fitted.data['model'],{**row,'observed':True,'features':{**row['features'],**extra}})
    with monkeypatch.context() as bypass:
        bypass.setattr(intervention,'admit',lambda *args:None)
        commit_decision(L,c.id,target.id,policy.id,'escalate' if h else 'stand_down',p,[p,p],
                        fit_id=fitted.id,parent_decision_id=initial.id,observe_s=30)
    reveal(L,c.id,target.id,store)
    checked=verify(L.root)
    assert not checked.ok and any('trained world-model window' in problem for problem in checked.problems)


def test_gate_refuses_selective_trial_skips_even_above_minimum(tmp_path):
    from translucent.intervention import create,propose_policy,register_fold,fit_policy,commit_decision,reveal
    from translucent.intervention_bench import submit_policy,score,propose_claim
    from translucent.intervention_models import PolicySpec
    from translucent.systems.nist_heat import ingest,load_dataset
    from translucent.ledger import Ledger
    from translucent import gate
    L=Ledger(tmp_path/'ledger');c=create(L,min_trials=20);store=tmp_path/'seals'
    policies=[propose_policy(L,c.id,name,PolicySpec(family='constant',probability=p),kind=kind)
              for name,p,kind in [('escalate',1,'agent'),('stand down',0,'baseline')]]
    eligible=[t['exp'] for t in load_dataset() if t['t'] and t['delay_after_vent_s'] is not None]
    trials=ingest(L,c.id,store,scope='all registered',selected=set(eligible[:21]))
    fold=register_fold(L,c.id,'all registered','loco',[],[t.id for t in trials])
    fits=[fit_policy(L,c.id,p.id,fold.id) for p in policies]
    from translucent.intervention_bench import screen_policy
    screen_policy(L,c.id,policies[0].id,fold.id,fits[0])
    for p in policies:submit_policy(L,c.id,p.id)
    for trial in trials[:-1]:
        for policy,fitted,prob,action in zip(policies,fits,[1,0],['escalate','stand_down']):
            commit_decision(L,c.id,trial.id,policy.id,action,prob,[prob,prob],fit_id=fitted.id)
        reveal(L,c.id,trial.id,store)
    partial=score(L,c.id,policies[0].id,'loco')
    assert partial['n']==20 and partial['status']=='scored'  # Progress remains inspectable.
    predicates=[dict(type='policy_performance',policy_id=policies[0].id,n=20,unsafe=0),
                dict(type='policy_beats',a=policies[0].id,b=policies[1].id),
                dict(type='policy_no_more_unsafe_fewer_missed',a=policies[0].id,b=policies[1].id)]
    for predicate in predicates:
        claim=propose_claim(L,c.id,dict(split='loco',subset='all',**predicate))
        review=gate.review(L,claim.id)
        assert review.data['verdict']=='unsupported'
        assert '20 of 21 registered physical trials' in review.data['reason']


def test_offline_verify_rejects_undeclared_abstention(tmp_path,monkeypatch):
    from translucent import intervention
    from translucent.intervention import create,propose_policy,register_fold,fit_policy,commit_decision,reveal
    from translucent.intervention_bench import submit_policy
    from translucent.intervention_models import PolicySpec
    from translucent.systems.nist_heat import ingest
    from translucent.ledger import Ledger
    from translucent.verify import verify
    L=Ledger(tmp_path/'ledger');c=create(L,min_trials=1);store=tmp_path/'seals'
    p=propose_policy(L,c.id,'constant escalate',PolicySpec(family='constant'),kind='baseline')
    submit_policy(L,c.id,p.id)
    trial=ingest(L,c.id,store,scope='abstain',selected={28})[0]
    fold=register_fold(L,c.id,'abstain','loco',[],[trial.id]);fitted=fit_policy(L,c.id,p.id,fold.id)
    with monkeypatch.context() as bypass:
        bypass.setattr(intervention,'admit',lambda *args:None)
        commit_decision(L,c.id,trial.id,p.id,'abstain',1,[1,1],fit_id=fitted.id)
    reveal(L,c.id,trial.id,store)
    checked=verify(L.root)
    assert not checked.ok and any('declared decision rule' in problem for problem in checked.problems)


def test_baseline_registrations_do_not_consume_sixteen_candidate_slots(tmp_path):
    from translucent.intervention import create,propose_policy,register_fold,fit_policy,protocol
    from translucent.intervention_bench import submit_policy,screen_policy,funnel_counts
    from translucent.intervention_models import PolicySpec
    from translucent.ledger import Ledger
    L=Ledger(tmp_path/'ledger');c=create(L,heldout_cap=16)
    spec=PolicySpec(family='constant')
    for i in range(9):
        baseline=propose_policy(L,c.id,f'baseline {i}',spec,kind='baseline')
        submit_policy(L,c.id,baseline.id)
    fold=register_fold(L,c.id,'empty registered training','loco',[],[])
    candidates=[propose_policy(L,c.id,f'candidate {i}',spec,kind='agent') for i in range(17)]
    for p in candidates:
        screen_policy(L,c.id,p.id,fold.id,fit_policy(L,c.id,p.id,fold.id))
    for p in candidates[:16]:submit_policy(L,c.id,p.id)
    with pytest.raises(ValueError,match='candidate submission cap'):submit_policy(L,c.id,candidates[16].id)
    f=funnel_counts(L,c.id)
    assert f['candidates_submitted']==16 and f['baselines_registered']==9
    assert f['proposed']==17 and f['submitted']==16
    # Registering a fixed comparator remains legal even when candidate slots are full.
    extra=propose_policy(L,c.id,'extra fixed comparator',spec,kind='baseline')
    submit_policy(L,c.id,extra.id)
    assert funnel_counts(L,c.id)['baselines_registered']==10
    config=protocol(L,c.id).data
    assert config['heldout_cap_K']==16 and config['amendment']==3
    assert config['amendment_registration']['decision_date']=='2026-10-30'


def test_two_screened_candidates_times_nine_comparators_is_family_eighteen(tmp_path):
    from translucent.intervention import create,propose_policy,register_fold,fit_policy,commit_decision,reveal
    from translucent.intervention_bench import submit_policy,screen_policy,funnel_counts,comparison,propose_claim
    from translucent.intervention_models import PolicySpec
    from translucent.systems.nist_heat import ingest
    from translucent.ledger import Ledger
    from translucent import gate
    L=Ledger(tmp_path/'ledger');c=create(L,min_trials=1);store=tmp_path/'seals'
    spec=PolicySpec(family='constant')
    baselines=[propose_policy(L,c.id,f'baseline {i}',spec,kind='baseline') for i in range(9)]
    candidates=[propose_policy(L,c.id,f'candidate {i}',spec,kind='agent') for i in range(2)]
    trial=ingest(L,c.id,store,scope='paired',selected={28})[0]
    fold=register_fold(L,c.id,'paired','loco',[],[trial.id])
    fitted={p.id:fit_policy(L,c.id,p.id,fold.id) for p in baselines+candidates}
    for p in candidates:screen_policy(L,c.id,p.id,fold.id,fitted[p.id])
    for p in baselines+candidates:submit_policy(L,c.id,p.id)
    for p in baselines+candidates:
        commit_decision(L,c.id,trial.id,p.id,'escalate',1,[1,1],fit_id=fitted[p.id].id)
    reveal(L,c.id,trial.id,store)
    pair=comparison(L,c.id,candidates[0].id,baselines[0].id,'loco')
    assert pair['inferential'] and pair['candidates_submitted']==2
    assert pair['baselines_submitted']==9 and pair['family_size']==18
    counts=funnel_counts(L,c.id)
    assert counts['candidates_submitted']==2 and counts['baselines_registered']==9
    descriptive=comparison(L,c.id,baselines[0].id,baselines[1].id,'loco')
    assert not descriptive['inferential']
    assert descriptive['p'] is None and descriptive['p_holm'] is None
    assert descriptive['mean_cost_difference']==0 and descriptive['assignments']==0
    claim=propose_claim(L,c.id,dict(type='policy_beats',a=baselines[0].id,b=baselines[1].id,split='loco',subset='all'))
    reviewed=gate.review(L,claim.id)
    assert reviewed.data['verdict']=='unsupported' and 'descriptive comparators' in reviewed.data['reason']
