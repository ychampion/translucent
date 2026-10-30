import pytest


def scored_campaign(tmp_path,n=4,cap=4):
    from translucent.intervention import create,propose_policy,register_fold,commit_decision,reveal,record
    from translucent.intervention_bench import submit_policy
    from translucent.intervention_models import PolicySpec
    from translucent.systems.nist_heat import ingest
    from translucent.ledger import Ledger
    L=Ledger(tmp_path/'ledger');c=create(L,min_trials=n,heldout_cap=cap);store=tmp_path/'seals'
    a=propose_policy(L,c.id,'a',PolicySpec(family='constant'),kind='agent')
    b=propose_policy(L,c.id,'b',PolicySpec(family='constant',probability=0),kind='baseline')
    trials=ingest(L,c.id,store,scope='test',selected={15,16,28,35})
    fold=register_fold(L,c.id,'test','loco',[],[t.id for t in trials])
    from translucent.intervention import fit_policy
    fa=fit_policy(L,c.id,a.id,fold.id);fb=fit_policy(L,c.id,b.id,fold.id)
    from translucent.intervention_bench import screen_policy
    screen_policy(L,c.id,a.id,fold.id,fa)
    submit_policy(L,c.id,a.id);submit_policy(L,c.id,b.id)
    for t in trials:
        commit_decision(L,c.id,t.id,a.id,'escalate',1.,[1,1],fit_id=fa.id)
        commit_decision(L,c.id,t.id,b.id,'stand_down',0.,[0,0],fit_id=fb.id)
        reveal(L,c.id,t.id,store)
    return L,c,a,b,record


def test_gate_recomputes_counts_and_primary_comparison(tmp_path):
    from translucent import gate
    from translucent.intervention_bench import score,comparison,propose_claim
    L,c,a,b,_=scored_campaign(tmp_path)
    s=score(L,c.id,a.id,'loco')
    assert (s['n'],s['unsafe'],s['missed'],s['accuracy'])==(4,0,2,.5)
    assert s['accuracy_interval']==pytest.approx([.150038989,.849961011])
    claim=propose_claim(L,c.id,dict(type='policy_performance',policy_id=a.id,split='loco',subset='all',n=4,unsafe=0))
    assert gate.review(L,claim.id).data['verdict']=='supported'
    wrong=propose_claim(L,c.id,dict(type='policy_performance',policy_id=b.id,split='loco',subset='all',n=4,unsafe=0))
    assert gate.review(L,wrong.id).data['verdict']=='refuted'
    pair=comparison(L,c.id,a.id,b.id,'loco')
    assert pair['candidates_submitted']==1 and pair['family_size']==1
    assert pair['a_only_correct']==2 and pair['b_only_correct']==0
    assert pair['p']==.5 and pair['p_holm']==.5


def test_gate_refuses_unobserved_simulated_and_too_small(tmp_path):
    from translucent import gate
    from translucent.intervention_bench import propose_claim
    L,c,a,b,record=scored_campaign(tmp_path,n=5)
    for extra,reason in [({},'minimum'),({'action':'earlier_heater_off'},'no recorded outcome'),({'simulated':True},'simulated')]:
        claim=propose_claim(L,c.id,dict(type='policy_performance',policy_id=a.id,split='loco',subset='all',n=4,unsafe=0,**extra))
        review=gate.review(L,claim.id)
        assert review.data['verdict']=='unsupported'
        assert reason in review.data['reason']


def test_cap_counts_ledger_candidate_submissions_and_cannot_be_changed(tmp_path):
    from translucent.intervention import create,propose_policy,register_fold,fit_policy
    from translucent.intervention_bench import submit_policy,screen_policy
    from translucent.intervention_models import PolicySpec
    from translucent.ledger import Ledger
    L=Ledger(tmp_path/'ledger');c=create(L,heldout_cap=1)
    a=propose_policy(L,c.id,'a',PolicySpec(family='constant'),kind='agent');b=propose_policy(L,c.id,'b',PolicySpec(family='constant'),kind='agent')
    fold=register_fold(L,c.id,'cap','loco',[],[])
    for p in (a,b):screen_policy(L,c.id,p.id,fold.id,fit_policy(L,c.id,p.id,fold.id))
    submit_policy(L,c.id,a.id)
    with pytest.raises(ValueError,match='cap'):submit_policy(L,c.id,b.id)


def test_offline_verify_recomputes_score_and_gate(tmp_path):
    from translucent import gate
    from translucent.intervention_bench import score,propose_claim
    from translucent.verify import verify
    L,c,a,b,record=scored_campaign(tmp_path)
    record(L,c.id,'score',score(L,c.id,a.id,'loco'))
    claim=propose_claim(L,c.id,dict(type='policy_performance',policy_id=a.id,split='loco',subset='all',n=4,unsafe=0))
    gate.review(L,claim.id)
    assert verify(L.root).ok


def test_submission_family_closes_before_outcomes_can_select_new_candidates(tmp_path):
    from translucent.intervention import propose_policy
    from translucent.intervention_models import PolicySpec
    from translucent.intervention_bench import submit_policy
    L,c,a,b,_=scored_campaign(tmp_path)
    later=propose_policy(L,c.id,'later',PolicySpec(family='constant'),kind='baseline')
    with pytest.raises(ValueError,match='submissions close'):submit_policy(L,c.id,later.id)


def test_old_cli_output_including_gate_notes_stays_byte_for_byte():
    import json,subprocess,sys
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    golden=json.loads((root/'tests/data/verify-golden.json').read_text())
    for name in ('m4-fork-1','m4-fork-2','m4-fork-3','m4-methods'):
        path='web/public/fixtures/'+name
        expected=(golden[path]['summary']+'\n'+
                  'Re-checked with the gate code the findings were recorded with (4245d5a3599b)\n')
        checked=subprocess.run([sys.executable,'-m','translucent.cli','verify',path],cwd=root,capture_output=True,text=True,check=True)
        assert checked.stdout==expected
