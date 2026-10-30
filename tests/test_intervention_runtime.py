import json
import socket

import pytest


def test_scripted_planner_uses_training_tools_and_gate(tmp_path,monkeypatch):
    from translucent.runtime import InterventionCampaigner
    from translucent.intervention_runtime import ScriptedPlanner,open_intervention
    from translucent.ledger import Ledger
    from translucent.verify import verify
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('network called'))
    L=Ledger(tmp_path/'ledger')
    c,fold,store=open_intervention(L,tmp_path/'sealed',selected={15,16,22,28,35,65,66,67},min_trials=1)
    result=InterventionCampaigner(L,c,fold,store,ScriptedPlanner()).run()
    assert result['procedure']['claim_ids']
    assert result['hypotheses'] and len(result['policies'])>=2
    assert all(p['simulated'] is True for p in result['policies'])
    assert all(x['simulated'] and x['status']=='proposed' and len(x['per_hypothesis'])==3 for x in result['next_experiments'])
    assert all('400' in x['schedule'] and '41 W' in x['schedule'] for x in result['next_experiments'])
    assert verify(L.root).ok


def test_tool_scope_and_post_off_boundary(tmp_path):
    from translucent.intervention_runtime import InterventionTools,open_intervention
    from translucent.systems.nist_heat import ingest
    from translucent.ledger import Ledger
    L=Ledger(tmp_path/'ledger');c,fold,store=open_intervention(L,tmp_path/'sealed',selected={15,16,28,35},min_trials=1)
    tools=InterventionTools(L,c.id,fold.id,store)
    listed=tools.call('list_trials',{})
    assert all('outcome' not in t and 't_ignition' not in t for t in listed)
    t=listed[0];trace=tools.call('get_trace',{'trial_id':t['id']})
    assert max(trace['t'])<=t['t_heater_off']
    forecast=tools.call('forecast_actions',{'trial_id':t['id'],'heater_off_after_vent_s':5})
    assert forecast['simulated'] is True and forecast['receipt_id']
    assert {'stop_now','observe_30','observe_60','heater_off_after_vent'}<=set(forecast['actions'])
    other=ingest(L,c.id,store,scope='other',selected={81})[0]
    with pytest.raises(ValueError,match='scope'):tools.call('get_trace',{'trial_id':other.id})
    with pytest.raises(ValueError):tools.call('compute_features',{'trial_id':t['id'],'observe_s':60})
    with pytest.raises(ValueError):tools.call('get_trace',{'trial_id':t['id'],'path':'/etc/passwd'})


def test_recorded_planner_requires_exact_request(tmp_path):
    from translucent.intervention_runtime import RecordedPlanner,save_planner_recording
    from translucent.recordings import NoRecording
    messages=[{'role':'user','content':'use only revealed training data'}]
    response={'tool':'list_trials','arguments':{}}
    save_planner_recording(tmp_path,messages,[],response)
    planner=RecordedPlanner(tmp_path)
    assert planner.next(messages,[])==response
    with pytest.raises(NoRecording):planner.next([{'role':'user','content':'different'}],[])


def test_mcp_intervention_tools_require_write_and_own_the_scope(tmp_path):
    from translucent.ledger import Ledger
    from translucent.mcp_server import Protocol
    L=Ledger(tmp_path/'ledger');L.db.close()
    p=Protocol(tmp_path/'ledger',write=True,state_dir=tmp_path/'api')
    def request(name,args):return p.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}})['result']
    try:
        p.handle({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'intervention-test','version':'1'}}})
        p.handle({'jsonrpc':'2.0','method':'notifications/initialized'})
        opened=request('open_campaign',dict(experiment='nist:loco',objective='Judge recorded ignition',budget_usd=0))
        assert not opened['isError']
        cid=json.loads(opened['content'][0]['text'])['id']
        catalog=json.loads(request('list_tools',{'campaign_id':cid})['content'][0]['text'])
        assert {'list_trials','observe','predict','submit_policy','write_procedure'}<={t['name'] for t in catalog}
        result=request('run_tool',dict(campaign_id=cid,tool='list_trials',arguments={}))
        trials=json.loads(result['content'][0]['text'])
        assert len(trials)==50 and all('outcome' not in t for t in trials)
        bad=request('run_tool',dict(campaign_id=cid,tool='get_trace',arguments={'trial_id':trials[0]['id'],'path':'/etc/passwd'}))
        assert bad['isError']
        refused=request('finish_campaign',dict(campaign_id=cid,answer='unsupported',not_proven=''))
        assert refused['isError']
    finally:p.close()


def test_prequential_tools_advance_only_after_feedback_and_check_updates(tmp_path):
    from translucent.intervention import records
    from translucent.intervention_runtime import InterventionTools,open_intervention
    from translucent.ledger import Ledger
    from translucent.verify import verify
    L=Ledger(tmp_path/'ledger')
    c,fold,store=open_intervention(L,tmp_path/'sealed',split='prequential',selected={15,16,22},min_trials=1)
    service=InterventionTools(L,c.id,fold.id,store)
    with pytest.raises(ValueError,match='reveal'):service.call('advance',{})
    p=service.call('propose_policy',dict(name='generic level',spec=dict(family='logistic',features=['level'])))
    service.call('evaluate_policy',{'policy_id':p['id']});service.call('submit_policy',{'policy_id':p['id']})
    for _ in range(3):
        trial=next(t for t in service.call('list_trials',{}) if t['role']=='held_out')
        pred=service.call('predict',dict(policy_id=p['id'],trial_id=trial['id']))
        with pytest.raises(ValueError,match='executable world-model'):
            service.call('commit_decision',dict(policy_id=p['id'],trial_id=trial['id'],decision='escalate',p_ignite=.12345,interval=[0,1]))
        service.call('commit_decision',dict(policy_id=p['id'],trial_id=trial['id'],decision=pred['decision_policy']['action'],**{k:pred['world_model'][k] for k in ('p_ignite','interval')}))
        service.call('reveal',{'trial_id':trial['id']})
        advanced=service.call('advance',{})
        if advanced['done']:break
        service.call('evaluate_policy',{'policy_id':p['id']})
    assert len([r for r in records(L,c.id,'update') if r.data['policy_id']==p['id']])==2
    assert verify(L.root).ok


def test_scripted_prequential_loop_reaches_final_gate_after_updates(tmp_path):
    from translucent.intervention import records
    from translucent.intervention_runtime import InterventionCampaigner,open_intervention,ScriptedPlanner
    from translucent.ledger import Ledger
    from translucent.verify import verify
    L=Ledger(tmp_path/'ledger')
    c,fold,store=open_intervention(L,tmp_path/'sealed',split='prequential',selected={15,16,22},min_trials=1)
    result=InterventionCampaigner(L,c,fold,store,ScriptedPlanner()).run()
    assert result['procedure']['claim_ids']
    agent_ids={p.id for p in records(L,c.id,'policy') if p.data['kind']=='agent'}
    assert len([r for r in records(L,c.id,'update') if r.data['policy_id'] in agent_ids])==4
    assert len(records(L,c.id,'reveal'))==3
    assert verify(L.root).ok
