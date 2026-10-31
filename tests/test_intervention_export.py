import json
from pathlib import Path

import pytest


@pytest.fixture
def campaign(tmp_path):
    from translucent.intervention_runtime import open_intervention,InterventionCampaigner,ScriptedPlanner
    from translucent.ledger import Ledger
    L=Ledger(tmp_path/'ledger')
    c,fold,store=open_intervention(L,tmp_path/'sealed',selected={15,16,22,28,35,65,66,67},min_trials=1)
    InterventionCampaigner(L,c,fold,store,ScriptedPlanner()).run()
    return L,c


def test_self_contained_bundle_verifies_and_detects_tampering(campaign,tmp_path):
    from translucent.intervention_export import export
    from translucent.contracts import ReplayBundle
    from translucent.verify import verify
    L,c=campaign
    target=tmp_path/'nist-bench.json'
    bundle=export(L,c.id,target)
    assert bundle.contract_version=='2' and bundle.intervention.procedure
    assert ReplayBundle.model_validate_json(target.read_text()).intervention.receipt
    checked=verify(target)
    assert checked.ok,checked.problems
    assert checked.decisions>0 and checked.findings>=2
    body=json.loads(target.read_text());body['intervention']['decisions'][0]['p_ignite']=.12345
    changed=tmp_path/'changed.json';changed.write_text(json.dumps(body))
    bad=verify(changed)
    assert not bad.ok and any('projection' in p for p in bad.problems)
    body=json.loads(target.read_text());key=next(iter(body['intervention']['receipt']['blobs']))
    body['intervention']['receipt']['blobs'][key]['data']='AAAA'
    changed.write_text(json.dumps(body))
    assert not verify(changed).ok


def test_export_reuses_graph_and_campaign_diff(campaign,tmp_path):
    from translucent.replay import export
    from translucent.graph import campaign_graph
    from translucent.diff import View,diff_campaigns
    L,c=campaign
    bundle=export(L,c.id,tmp_path/'bundle',include_verification=False)
    graph=campaign_graph(L,c.id)
    assert any(n['kind']=='policy' for n in graph['nodes'])
    assert any(e['kind']=='committed_before' for e in graph['edges'])
    d=diff_campaigns(View.from_ledger(L,c.id),View.from_bundle(bundle))
    assert d['intervention']['score_changes']==[]
    assert d['intervention']['a_decisions']==d['intervention']['b_decisions']>0


def test_server_intervention_reads_are_projected_and_read_only(campaign,tmp_path,monkeypatch):
    monkeypatch.setenv('TRANSLUCENT_TEAM_TOKEN','team-secret')
    from fastapi.testclient import TestClient
    from translucent.server import create_app
    L,c=campaign
    before=L.db.execute('SELECT COUNT(*) FROM record').fetchone()[0]
    with TestClient(create_app(L.root,tmp_path/'api')) as client:
        data=client.get(f'/api/campaigns/{c.id}/intervention')
        assert data.status_code==200 and 'receipt' not in data.json()
        assert client.get(f'/api/campaigns/{c.id}/intervention/updates').json()==[]
        assert client.get(f'/api/campaigns/{c.id}/bundle').json()['intervention']['procedure']
        assert client.get(f'/api/campaigns/{c.id}/graph').status_code==200
        # no write route: a token holder gets 405; without a token, every write is refused before routing
        team={'Authorization':'Bearer team-secret'}
        assert client.post(f'/api/campaigns/{c.id}/intervention/updates',json={},headers=team).status_code==405
        assert client.post(f'/api/campaigns/{c.id}/intervention/updates',json={}).status_code==403
    assert L.db.execute('SELECT COUNT(*) FROM record').fetchone()[0]==before
