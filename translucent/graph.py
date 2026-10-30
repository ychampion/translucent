"""A campaign's evidence as one navigable graph: question -> findings -> measurements -> runs -> files -> recording.

A pure function over records the ledger already holds; nothing is stored. `build` takes the same lists a replay
bundle carries, so the web app can compute the identical graph from a bundle.

Node kinds: question, finding, review, measurement, run, artifact, recording, lesson, note.
Edge kinds (from -> to):
  answers        finding -> question
  cites          finding -> measurement
  replaces       finding -> the finding it supersedes
  forked_from    finding in a what-if branch -> the finding it re-examines
  reviewed_by    finding -> review
  shows          review -> the image a second opinion looked at
  produced_by    measurement -> run
  calibrated_by  measurement -> scale or clock measurement
  computed_from  measurement -> artifact
  read           run -> input (artifact or measurement)
  output_of      artifact -> the run that wrote it
  derived_from   artifact -> its transform parent
  taught         lesson -> its evidence
"""
from __future__ import annotations

from translucent.ledger import Ledger


def _value(m) -> str:
    if isinstance(m.value, list):
        return f"{len(m.value)} values ({m.unit})"
    u = m.uncertainty.value
    return f"{m.value:.4g}" + (f" ± {u:.2g}" if u is not None else "") + f" {m.unit}"


def build(campaign, claims, reviews, measurements, runs, artifacts, lessons=(), events=(), forks=(),
          status=None, intervention=None) -> dict:
    """Nodes and edges from records. `status(claim)` gives a finding's current status (default: latest gate
    verdict, else proposed)."""
    nodes, edges, seen = {}, [], set()

    def node(nid, kind, label, **data):
        nodes.setdefault(nid, {"id": nid, "kind": kind, "label": label, **data})

    def edge(a, b, kind):
        if (a, b, kind) not in seen:
            seen.add((a, b, kind))
            edges.append({"from": a, "to": b, "kind": kind})

    question = f"question:{campaign.id}"
    node(question, "question", campaign.objective, title=campaign.title)
    by_claim = {}
    for r in reviews:
        by_claim.setdefault(r.claim_id, []).append(r)
    for k in claims:
        gate = [r for r in by_claim.get(k.id, []) if r.reviewer == "gate"]
        st = status(k) if status else (gate[-1].verdict if gate else "proposed")
        node(k.id, "finding", k.statement, status=st, campaign_id=k.campaign_id, predicate=k.predicate.type)
        if k.campaign_id == campaign.id:
            edge(k.id, question, "answers")
        for m in k.predicate.measurement_ids:
            edge(k.id, m, "cites")
        if k.supersedes:
            edge(k.id, k.supersedes, "replaces")
        for r in by_claim.get(k.id, []):
            node(r.id, "review", f"{r.reviewer}: {r.verdict}", reviewer=r.reviewer, verdict=r.verdict,
                 author=r.author, model=r.model)
            edge(k.id, r.id, "reviewed_by")
            for a in r.artifact_ids:
                edge(r.id, a, "shows")
    for f in forks:
        if f.claim_id:
            for k in claims:
                if k.campaign_id == f.campaign_id and k.id != f.claim_id:
                    edge(k.id, f.claim_id, "forked_from")
    for m in measurements:
        node(m.id, "measurement", m.label or m.quantity, quantity=m.quantity, value=_value(m), unit=m.unit,
             subject=m.subject)
        edge(m.id, m.run_id, "produced_by")
        for c in m.calibration_ids:
            edge(m.id, c, "calibrated_by")
        for a in m.artifact_ids:
            edge(m.id, a, "computed_from")
    for r in runs:
        node(r.id, "run", r.tool, status=r.status, model=r.model, provider=r.provider, cost_usd=r.cost_usd,
             code_sha=r.code_sha, campaign_id=r.campaign_id)
        for i in r.input_ids:
            edge(r.id, i, "read")
        for o in r.output_ids:
            edge(o, r.id, "output_of")
    for a in artifacts:
        node(a.id, "recording" if a.kind == "video" else "artifact", a.label or a.kind, artifact_kind=a.kind,
             media_type=a.media_type, citation=a.source.citation if a.source else None)
        for p in a.transform.parent_ids if a.transform else []:
            edge(a.id, p, "derived_from")
    for x in lessons:
        node(x.id, "lesson", x.text, scope=x.scope, learned_in=x.learned_in)
        for e in x.evidence_ids:
            edge(x.id, e, "taught")
    for ev in events:
        if ev.type == "human.note":
            node(f"note:{ev.campaign_id}:{ev.seq}", "note", ev.data.get("text") or ev.detail or "",
                 author=ev.data.get("author"))
    if intervention is not None:
        data=intervention.model_dump() if hasattr(intervention,'model_dump') else intervention
        for t in data['trials']:
            node(t['id'],'trial',t['trial_key'],condition=t['condition'],sealed_hash=t['sealed_hash'])
            edge(t['id'],t['series_artifact'],'computed_from')
            if t['revealed_seq'] is not None:
                rid='reveal:'+t['id']
                node(rid,'outcome',t['outcome'],seq=t['revealed_seq'])
                edge(rid,t['id'],'reveals')
        for p in data['policies']:
            node(p['id'],'policy',p['name'],family=p['family'],simulated=p['simulated'])
        for d in data['decisions']:
            node(d['id'],'decision',d['decision'],p_ignite=d['p_ignite'],seq=d['committed_seq'],observe_s=d['observe_s'])
            edge(d['id'],d['policy_id'],'uses_policy');edge(d['id'],d['trial_id'],'observes')
            if d['revealed_seq'] is not None:edge(d['id'],'reveal:'+d['trial_id'],'committed_before')
        for h in data['hypotheses']:
            node(h['id'],'hypothesis',h['text'])
            for p in h['policies']:edge(h['id'],p,'tested_by')
        for s in data['scores']:
            node(s['id'],'score',f'{s["unsafe"]} unsafe / {s["n"]} trials ({s["split"]}, {s["subset"]})')
            edge(s['id'],s['policy_id'],'scores')
            for d in s['decision_ids']:edge(s['id'],d,'computed_from')
        for c in data['claims']:
            node(c['id'],'finding',c['predicate']['type'],predicate=c['predicate'])
            edge(c['id'],question,'answers')
            for pid in (c['predicate'].get('policy_id'),c['predicate'].get('a'),c['predicate'].get('b')):
                if pid:edge(c['id'],pid,'cites')
        for r in data['reviews']:
            node(r['id'],'review',f'gate: {r["verdict"]}',verdict=r['verdict'],reason=r['reason'])
            edge(r['claim_id'],r['id'],'reviewed_by')
    # an edge to a record outside this graph (e.g. a lesson citing another campaign's run) is dropped
    edges = [e for e in edges if e["from"] in nodes and e["to"] in nodes]
    return {"campaign_id": campaign.id, "nodes": list(nodes.values()), "edges": edges}


def campaign_graph(L: Ledger, campaign_id: str) -> dict:
    """The graph of everything a campaign's bundle carries (see translucent.replay.closure)."""
    from translucent.replay import closure, extra
    c = L.get("campaign", campaign_id)
    if c is None:
        raise ValueError(f"Campaign not found: {campaign_id}")
    from translucent.intervention import records as intervention_records
    if intervention_records(L,campaign_id,'protocol'):
        from translucent.intervention_export import bundle
        b=bundle(L,campaign_id)
        return build(c,[],[],[],[],b.artifacts,events=b.events,intervention=b.intervention)
    claims = L.list("claim", campaign_id=campaign_id)
    events = L.events(campaign_id)
    reviews = [r for k in claims for r in L.list("review", ref=k.id)]
    ids = closure(L, campaign_id, claims, events, reviews)
    ids["run"] |= {r.id for r in L.list("run", campaign_id=campaign_id)}   # failed attempts too
    for rid in list(ids["run"]):
        run = L.get("run", rid)
        ids["artifact"] |= {i for i in run.input_ids + run.output_ids if L.kind_of(i) == "artifact"}
    ids["artifact"] |= set(c.inputs)
    forks = extra(L, campaign_id)["forks"]
    shown = {k.id for k in claims}   # a branch also shows the finding it re-examines
    for f in forks:
        if f.campaign_id == campaign_id and f.claim_id and f.claim_id not in shown:
            claims = claims + [L.get("claim", f.claim_id)]
            reviews = reviews + L.list("review", ref=f.claim_id)
            shown.add(f.claim_id)
    lessons = [x for x in L.list("lesson") if x.learned_in == campaign_id or x.id in ids["lesson"]]
    return build(c, claims, reviews,
                 [m for m in L.list("measurement") if m.id in ids["measurement"]],
                 [r for r in L.list("run") if r.id in ids["run"]],
                 [a for a in L.list("artifact") if a.id in ids["artifact"]],
                 lessons, events, forks,
                 status=lambda k: L.claim_status(k.id))
