import numpy as np
import pytest


def examples():
    from translucent.intervention_models import compute_features
    from translucent.systems.nist_heat import load_dataset
    return [dict(id=t['trial_key'],condition={'format':t['format'],'soc':t['soc']},
                 features=compute_features(t['t'],t['y'],t['t_heater_off'],
                                           {'format':t['format'],'soc':t['soc'],'delay_after_vent_s':t['delay_after_vent_s'],
                                            't_vent':t['t_vent']}),y=int(t['outcome']!='rescued'))
            for t in load_dataset() if t['t'] and t['delay_after_vent_s'] is not None]


def test_features_ignore_every_post_off_sample():
    from translucent.intervention_models import compute_features
    t=list(range(201));y=[20+.2*x for x in t]
    m=dict(format='21700',soc=75,delay_after_vent_s=10,t_vent=90)
    f=compute_features(t,y,100,m)
    y[101:]=[10000]*100
    assert compute_features(t,y,100,m)==f
    assert f['slope_30']==pytest.approx(.2)
    assert f['rise_60']==pytest.approx(12)
    assert f['time_since_vent']==10
    assert all(np.isfinite(v) for v in f.values())


def test_reference_models_reproduce_validation_loco():
    from translucent.intervention_models import PolicySpec, fit, predict, split_folds
    rows=examples()
    specs=[(PolicySpec(family='threshold',features=['delay_after_vent_s'],grouping='condition'),6,11,.277),
           (PolicySpec(family='threshold',features=['level']),3,12,.229),
           (PolicySpec(family='logistic',features=['format','level','slope_30','rise_60']),2,5,.138)]
    for spec,unsafe,missed,brier in specs:
        predictions={}
        for _,train,test in split_folds(rows,'loco'):
            model=fit(spec,train)
            for x in test:predictions[x['id']]=predict(model,x)
        ps=np.array([predictions[x['id']][0] for x in rows]);hs=np.array([predictions[x['id']][1] for x in rows]);ys=np.array([x['y'] for x in rows])
        assert int(((hs==0)&(ys==1)).sum())==unsafe
        assert int(((hs==1)&(ys==0)).sum())==missed
        assert float(np.mean((ps-ys)**2))==pytest.approx(brier,abs=.0005)


def test_nested_search_and_bootstrap_use_training_only():
    from translucent.intervention_models import PolicySpec, fit, bootstrap_predict, search
    rows=examples()[:25]
    candidates=[PolicySpec(family='threshold',features=[f],grouping='format') for f in ['level','slope_10','slope_30','slope_60','rise_60']]
    chosen,audit=search(candidates,rows)
    assert chosen in candidates
    assert len(audit)==len(candidates)
    assert all(set(a['train_ids']).isdisjoint(a['test_ids']) for a in audit[0]['folds'])
    p,h,interval=bootstrap_predict(fit(chosen,rows),rows,rows[0],replicates=16)
    assert 0<=interval[0]<=p<=interval[1]<=1


def test_training_constraint_and_thermal_forecast():
    from translucent.intervention_models import PolicySpec, fit, predict, thermal_forecast
    rows=examples()
    m=fit(PolicySpec(family='threshold',features=['level'],constraint='zero_unsafe'),rows)
    assert all(predict(m,x)[1]==1 for x in rows if x['y'])
    f=thermal_forecast(list(range(101)),[20+.2*i for i in range(101)])
    assert f['simulated'] is True
    assert f['model']=='lumped Arrhenius'
    assert len(f['temperature_C'])==601
    with pytest.raises(ValueError):PolicySpec(family='threshold',features=['post_off_peak'])
    with pytest.raises(ValueError,match='zero_unsafe'):
        PolicySpec(family='logistic',features=['level'],constraint='zero_unsafe')


def test_muse_without_recording_is_not_run(tmp_path):
    from translucent.intervention_models import muse_replay
    assert muse_replay({'level':170},tmp_path)['status']=='not_run'
    from translucent.intervention_models import scripted_specs
    assert set(scripted_specs())=={'agent_threshold','agent_logistic'}


def test_fitting_requires_revealed_registered_training_data(tmp_path):
    from translucent.ledger import Ledger
    from translucent.intervention import create,register_fold,propose_policy,fit_policy,commit_decision,reveal
    from translucent.systems.nist_heat import ingest
    from translucent.intervention_models import PolicySpec
    L=Ledger(tmp_path/'ledger');c=create(L);store=tmp_path/'seals'
    trials=ingest(L,c.id,store,scope='fold',selected={15,16,22})
    fold=register_fold(L,c.id,'fold','loco',[trials[0].id,trials[1].id],[trials[2].id])
    p=propose_policy(L,c.id,'level',PolicySpec(family='threshold',features=['level']))
    with pytest.raises(ValueError,match='revealed'):fit_policy(L,c.id,p.id,fold.id)
    for t in trials[:2]:
        commit_decision(L,c.id,t.id,'training','abstain',.5,[0,1],scored=False)
        reveal(L,c.id,t.id,store)
    fitted=fit_policy(L,c.id,p.id,fold.id)
    assert fitted.data['train_ids']==fold.data['train_ids']
    assert len(fitted.data['reveal_ids'])==2


def test_observation_models_fit_only_the_selected_training_window():
    from translucent.intervention_models import PolicySpec,fit,predict
    from translucent.intervention_actions import decide,DEFAULT_COSTS
    rows=[]
    for i in range(12):
        rows.append(dict(id=f'Exp{i}',condition={'format':'18650','soc':50},y=i%2,
                         features={'level':100},post_features_by_window={'30':{'post_slope':float(i%2)},'60':{'post_slope':float(1-i%2)}},
                         watch_early_by_window={'30':False,'60':True}))
    model=fit(PolicySpec(family='threshold',features=['post_slope'],observation_s=30),rows)
    assert model['watch_risk']==0
    p,h=predict(model,rows[0])
    decision=decide(p,[p,p],{'mode':'cost'},DEFAULT_COSTS,world_model=model)
    assert decision['action']=='observe' and decision['observe_s']==30
    altered=[{**r,'post_features_by_window':{**r['post_features_by_window'],'60':{'post_slope':99999.}}} for r in rows]
    assert fit(PolicySpec(family='threshold',features=['post_slope'],observation_s=30),altered)==model


def test_protocol_registers_owner_amendment_before_observation(tmp_path):
    from translucent.intervention import create,protocol
    from translucent.ledger import Ledger
    L=Ledger(tmp_path/'ledger');c=create(L,heater_off_lag_s=7,unsafe_ratio=20)
    p=protocol(L,c.id)
    assert p.seq==1 and p.data['primary_metric']=='mean_decision_cost'
    assert p.data['costs']['unsafe']==200 and p.data['costs']['watch_per_second']==.05
    assert p.data['heater_off_lag_s']==7 and p.data['amendment']==3
