import pytest


def test_cost_policy_is_separate_from_world_model():
    from translucent.intervention_actions import choose_action,DEFAULT_COSTS
    assert choose_action(.5,[.2,.8],DEFAULT_COSTS)['action']=='observe'
    assert choose_action(0,[0,.001],DEFAULT_COSTS)['action']=='stand_down'
    r=choose_action(.5,[.2,.8],DEFAULT_COSTS,options=['stand_down','observe_30'])
    assert r['action']=='observe' and r['observe_s']==30
    assert r['expected_costs']['observe_30']==1.5
    assert DEFAULT_COSTS['unsafe']==100 and DEFAULT_COSTS['unnecessary_escalation']==10
    # A fitted pre-off model has no post-off update: watching cannot help it.
    from translucent.intervention_actions import decide
    assert decide(.5,[.2,.8],{'mode':'cost'},DEFAULT_COSTS,world_model={'spec':{'observation_s':0}})['action']=='escalate'


def test_observation_requires_prior_payment_and_hides_later_samples(tmp_path):
    from translucent.intervention import create,propose_policy,register_fold,fit_policy,commit_decision,reveal
    from translucent.intervention_bench import submit_policy,score
    from translucent.intervention_actions import observe,observation_features
    from translucent.intervention_models import PolicySpec
    from translucent.systems.nist_heat import ingest
    from translucent.ledger import Ledger
    L=Ledger(tmp_path/'ledger');c=create(L,min_trials=1);store=tmp_path/'seals'
    p=propose_policy(L,c.id,'watch',PolicySpec(family='constant'),kind='baseline',decision_rule={'mode':'always_observe','observe_s':30})
    submit_policy(L,c.id,p.id)
    t=ingest(L,c.id,store,scope='watch',selected={28})[0]
    fold=register_fold(L,c.id,'watch','loco',[],[t.id]);f=fit_policy(L,c.id,p.id,fold.id)
    with pytest.raises(ValueError):observe(L,c.id,t.id,p.id,30)
    d=commit_decision(L,c.id,t.id,p.id,'observe',1,[.8,1],fit_id=f.id,observe_s=30)
    obs=observe(L,c.id,t.id,p.id,30)
    assert obs.seq>d.seq
    assert max(obs.data['trace']['t'])<=t.data['t_heater_off']+30
    features=observation_features(L,c.id,t.id,p.id,30)
    assert 'post_slope' in features
    with pytest.raises(ValueError):observation_features(L,c.id,t.id,p.id,60)
    with pytest.raises(ValueError,match='terminal watch'):reveal(L,c.id,t.id,store)
    commit_decision(L,c.id,t.id,p.id,'escalate',1,[1,1],fit_id=f.id,parent_decision_id=d.id,observe_s=30)
    reveal(L,c.id,t.id,store)
    s=score(L,c.id,p.id,'loco')
    assert s['unsafe']==1
    assert s['watch_time_s']>0 and s['watch_time_s']<=30


def test_two_node_solver_is_action_conditioned_and_simulated():
    from translucent.intervention_physics import continuous_prior,two_node_forecast
    from translucent.systems.nist_heat import load_dataset
    t=next(t for t in load_dataset() if t['exp']==81)
    n=int(t['t_heater_off'])+1
    prior=continuous_prior('21700',75)
    assert prior['source_trials']==['Exp52','Exp53','Exp54']
    assert prior['simulated'] is True
    forecast=two_node_forecast(t['t'][:n],t['y'][:n],prior,vent=t['t_vent'],delay=5)
    assert {'stop_now','observe_30','observe_60','heater_off_after_vent'}<=set(forecast['actions'])
    for a in forecast['actions'].values():
        assert a['simulated'] is True and 0<=a['p_ignite']<=1
        assert 0<=a['interval'][0]<=a['p_ignite']<=a['interval'][1]<=1
    assert forecast['hidden_state']['name']=='core_temperature_C'


def test_incompatible_world_and_rule_updates_are_rejected():
    from translucent.intervention_actions import compatibility
    old=[{'p':.9,'action':'escalate','y':1},{'p':.1,'action':'stand_down','y':0}]
    new=[{'p':.1,'action':'stand_down','y':1},{'p':.1,'action':'stand_down','y':0}]
    verdict=compatibility(old,new,tolerance=.01)
    assert not verdict['accepted'] and 'unsafe' in verdict['reason']
    new=[{'p':.5,'action':'escalate','y':1},{'p':.5,'action':'stand_down','y':0}]
    verdict=compatibility(old,new,tolerance=.01)
    assert not verdict['accepted'] and 'calibration' in verdict['reason']


def test_abstention_is_a_fixed_reproducible_rule_not_a_planner_override():
    from translucent.intervention_actions import decide,DEFAULT_COSTS,DecisionRule
    rule={'mode':'cost','abstain_band':[.4,.6]}
    assert decide(.5,[.2,.8],rule,DEFAULT_COSTS)['action']=='abstain'
    assert decide(.9,[.8,1],rule,DEFAULT_COSTS)['action']=='escalate'
    assert decide(.5,[.2,.8],rule,DEFAULT_COSTS,terminal=True)['action']=='escalate'
    with pytest.raises(ValueError):DecisionRule(abstain_band=[.7,.2])
