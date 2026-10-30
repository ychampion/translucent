"""What changed between two runs, two findings or two campaigns. Deterministic: no model is consulted.

Works on a ledger or on exported replay bundles (`View.from_bundle`), so the web app can compute the same diff from
two bundles. Bundles exported before runs were bundled get their runs from the tool events.
Each diff returns structured changes plus plain sentences for a non-specialist.
"""
from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path

from translucent.contracts import ReplayBundle


@dataclass
class View:
    """The records one campaign's bundle carries, from a ledger or from an exported bundle."""
    campaign: object
    events: list
    claims: list
    reviews: dict                              # claim id -> reviews, in order
    measurements: dict
    runs: dict                                 # run id -> plain dict (tool, params, status, ...)
    artifacts: dict
    lessons: list = field(default_factory=list)
    blob_root: Path | None = None
    status: dict = field(default_factory=dict)  # claim id -> current status, when known beyond the gate verdict
    intervention: object | None = None

    @classmethod
    def from_ledger(cls, L, campaign_id):
        from translucent.replay import closure
        c = L.get("campaign", campaign_id)
        if c is None:
            raise ValueError(f"Campaign not found: {campaign_id}")
        from translucent.intervention import records as intervention_records
        if intervention_records(L,campaign_id,'protocol'):
            from translucent.intervention_export import bundle
            return cls.from_bundle(bundle(L,campaign_id))
        claims = L.list("claim", campaign_id=campaign_id)
        events = L.events(campaign_id)
        reviews = {k.id: L.list("review", ref=k.id) for k in claims}
        ids = closure(L, campaign_id, claims, events, [r for rs in reviews.values() for r in rs])
        ids["run"] |= {r.id for r in L.list("run", campaign_id=campaign_id)}
        runs = {r.id: _run(r.model_dump(mode="json")) for r in L.list("run") if r.id in ids["run"]}
        for r in runs.values():
            ids["artifact"] |= set(r["input_ids"])
        return cls(c, events, claims, reviews,
                   {m.id: m for m in L.list("measurement") if m.id in ids["measurement"]}, runs,
                   {a.id: a for a in L.list("artifact") if a.id in ids["artifact"]},
                   [x for x in L.list("lesson") if x.learned_in == campaign_id], L.blobs,
                   {k.id: L.claim_status(k.id) for k in claims})

    @classmethod
    def from_bundle(cls, path_or_bundle):
        root = None
        if isinstance(path_or_bundle, ReplayBundle):
            b = path_or_bundle
        else:
            p = Path(path_or_bundle)
            p = p / "bundle.json" if p.is_dir() else p
            b, root = ReplayBundle.model_validate_json(p.read_text(encoding="utf-8")), p.parent / "verification" / "blobs"
        reviews = {}
        for r in b.reviews:
            reviews.setdefault(r.claim_id, []).append(r)
        runs = {r.id: _run(r.model_dump(mode="json")) for r in b.runs} if b.runs else _runs_from_events(b.events)
        return cls(b.campaign, b.events, b.claims, reviews, {m.id: m for m in b.measurements}, runs,
                   {a.id: a for a in b.artifacts}, list(b.lessons), root if root and root.is_dir() else None,
                   intervention=b.intervention)

    def verdict(self, claim_id):
        if claim_id in self.status:
            return self.status[claim_id]
        gate = [r for r in self.reviews.get(claim_id, []) if r.reviewer == "gate"]
        return gate[-1].verdict if gate else "proposed"

    def gate_checks(self, claim_id):
        gate = [r for r in self.reviews.get(claim_id, []) if r.reviewer == "gate"]
        return gate[-1].checks if gate else []

    def code(self, run) -> str | None:
        if isinstance(run["params"].get("code"), str):
            return run["params"]["code"]
        for i in run["input_ids"]:
            a = self.artifacts.get(i)
            if a is not None and a.kind == "code" and self.blob_root is not None:
                path = self.blob_root / i.removeprefix("sha256:")[:2] / i.removeprefix("sha256:")
                if path.is_file():
                    return path.read_text(encoding="utf-8", errors="replace")
        return None

    def tools(self) -> list[str]:
        return [e.actor for e in self.events if e.type == "tool.started"]

    def started(self) -> dict:
        return next((e.data for e in self.events if e.type == "campaign.started"), {})

    def cost(self) -> float:
        return round(sum(float(e.data.get("usd") or 0) for e in self.events if e.type == "cost.updated"), 6)

    def lessons_loaded(self) -> list[str]:
        return list(dict.fromkeys(r for e in self.events if e.type == "memory.loaded" for r in e.refs))


def _run(d: dict) -> dict:
    keys = ("id", "tool", "params", "status", "error", "model", "provider", "cost_usd", "code_sha", "input_ids",
            "output_ids", "campaign_id")
    return {k: d.get(k) for k in keys} | {"input_ids": d.get("input_ids") or [], "params": d.get("params") or {}}


def _runs_from_events(events) -> dict:
    """Runs as the tool events describe them (bundles exported before runs were bundled)."""
    runs = {}
    for e in events:
        if e.type == "tool.started" and e.refs:
            args = e.data.get("args") or {}
            runs[e.refs[0]] = _run({"id": e.refs[0], "tool": e.actor, "params": args, "status": None,
                                    "campaign_id": e.campaign_id,
                                    "input_ids": [v for v in args.values() if isinstance(v, str)]})
        elif e.type == "tool.finished" and e.refs and e.refs[0] in runs:
            r = runs[e.refs[0]]
            r["status"] = e.data.get("status")
            r["cost_usd"] = e.data.get("cost_usd") or 0.0
            r["output_ids"] = e.refs[1:]
    return runs


# ------------------------------------------------------------------ formatting

def _num(v) -> str:
    return f"{v:.4g}" if isinstance(v, (int, float)) else str(v)


def _value(m) -> str:
    if isinstance(m.value, list):
        return f"{len(m.value)} values in {m.unit}"
    u = m.uncertainty.value
    return f"{_num(m.value)}" + (f" ± {u:.2g}" if u is not None else "") + f" {m.unit}"


def _name(m) -> str:
    label = m.label or m.quantity
    return label + (f" ({m.subject})" if m.subject and m.subject not in label else "")


def _describe(view: View, value):
    """A parameter value with ids replaced by what they stand for."""
    if isinstance(value, str) and value in view.measurements:
        m = view.measurements[value]
        return f"{_name(m)} = {_value(m)}"
    if isinstance(value, str) and value in view.artifacts:
        a = view.artifacts[value]
        return f"{a.label or a.kind} ({value[7:19]})"
    if isinstance(value, list):
        return [_describe(view, v) for v in value]
    return value


# ------------------------------------------------------------------ runs

def diff_runs(va: View, a: str, vb: View, b: str) -> dict:
    ra, rb = va.runs[a], vb.runs[b]
    changes, sentences = [], []
    if ra["tool"] != rb["tool"]:
        changes.append({"field": "tool", "a": ra["tool"], "b": rb["tool"]})
        sentences.append(f"Different tools: {ra['tool']} vs {rb['tool']}.")
    else:
        sentences.append(f"Both ran {ra['tool']}.")
    for key in sorted(set(ra["params"]) | set(rb["params"])):
        if key == "code":
            continue
        x, y = ra["params"].get(key), rb["params"].get(key)
        if x == y:
            if isinstance(x, str) and (x in va.artifacts or x in va.measurements):
                sentences.append(f"Same {key}: {_describe(va, x)}.")
            continue
        dx, dy = _describe(va, x), _describe(vb, y)
        changes.append({"field": f"params.{key}", "a": x, "b": y, "a_shown": dx, "b_shown": dy})
        sentences.append(f"{key}: {dx if x is not None else 'not given'} vs {dy if y is not None else 'not given'}.")
    for side, view, run in (("a", va, ra), ("b", vb, rb)):
        run["_cals"] = {view.measurements[i].quantity: view.measurements[i] for i in run["input_ids"]
                        if i in view.measurements and view.measurements[i].quantity in ("spatial_scale", "time_base")}
    for q in sorted(set(ra["_cals"]) | set(rb["_cals"])):
        x, y = ra["_cals"].get(q), rb["_cals"].get(q)
        if x is not None and y is not None and x.id == y.id:
            continue
        what = {"spatial_scale": "Scale", "time_base": "Clock"}[q]
        shown = lambda m: "none" if m is None else _value(m) + (f" ({m.valid_domain})" if m.valid_domain else "")
        changes.append({"field": f"calibration.{q}", "a": x.id if x else None, "b": y.id if y else None})
        sentences.append(f"{what}: {shown(x)} vs {shown(y)}.")
    code_a, code_b = va.code(ra), vb.code(rb)
    if (code_a or code_b) and code_a != code_b:
        lines = list(difflib.unified_diff((code_a or "").splitlines(), (code_b or "").splitlines(), "A", "B",
                                          lineterm="", n=2))
        changes.append({"field": "code", "a": ra.get("code_sha"), "b": rb.get("code_sha"), "diff": "\n".join(lines)})
        sentences.append(f"The analysis code differs ({sum(1 for x in lines if x[:1] in '+-') - 2} lines changed).")
    elif code_a:
        sentences.append("Same analysis code.")
    for key, label in (("model", "Model"), ("provider", "Provider"), ("status", "Outcome"), ("error", "Error")):
        if ra.get(key) != rb.get(key):
            changes.append({"field": key, "a": ra.get(key), "b": rb.get(key)})
            sentences.append(f"{label}: {ra.get(key) or 'none'} vs {rb.get(key) or 'none'}.")
    for r in (ra, rb):
        r.pop("_cals", None)
    return {"kind": "run", "a": a, "b": b, "changes": changes, "sentences": sentences}


# ------------------------------------------------------------------ findings

def _match_measurements(ma: list, mb: list) -> list[tuple]:
    pairs, left = [], list(mb)
    def key(m):  # a derived number is matched by its unit: each analysis names its outputs its own way
        return ("derived" if m.quantity.startswith("derived:") else m.quantity, m.unit)
    for x in ma:
        y = next((m for m in left if key(m) == key(x) and m.subject == x.subject), None) or \
            next((m for m in left if key(m) == key(x)), None)
        if y is not None:
            left.remove(y)
        pairs.append((x, y))
    return pairs + [(None, y) for y in left]


def _checks_by_name(checks) -> dict:
    out, seen = {}, {}
    for c in checks:
        n = seen[c.name] = seen.get(c.name, 0) + 1
        out[(c.name, n)] = c
    return out


def diff_claims(va: View, a: str, vb: View, b: str) -> dict:
    ka = next(k for k in va.claims if k.id == a) if any(k.id == a for k in va.claims) else None
    kb = next(k for k in vb.claims if k.id == b) if any(k.id == b for k in vb.claims) else None
    if ka is None or kb is None:
        raise ValueError("Finding not found")
    changes, sentences = [], []
    pa, pb = ka.predicate, kb.predicate
    if pa.type != pb.type:
        changes.append({"field": "type", "a": pa.type, "b": pb.type})
        sentences.append(f"Different tests: {pa.type} vs {pb.type}.")
    if pa.params != pb.params:
        changes.append({"field": "params", "a": pa.params, "b": pb.params})
        if "lo" in pa.params and "lo" in pb.params:
            sentences.append(f"Accepted range: [{_num(pa.params['lo'])}, {_num(pa.params['hi'])}] vs "
                             f"[{_num(pb.params['lo'])}, {_num(pb.params['hi'])}] {pb.params.get('unit', '')}.".replace(" .", "."))
        else:
            sentences.append(f"Test settings: {json.dumps(pa.params, sort_keys=True)} vs "
                             f"{json.dumps(pb.params, sort_keys=True)}.")
    ms_a = [va.measurements[i] for i in pa.measurement_ids if i in va.measurements]
    ms_b = [vb.measurements[i] for i in pb.measurement_ids if i in vb.measurements]
    for x, y in _match_measurements(ms_a, ms_b):
        if x is not None and y is not None and x.id == y.id:
            sentences.append(f"Same measurement: {_name(x)} = {_value(x)}.")
            continue
        if x is not None and y is not None:
            changes.append({"field": "measurement", "a": x.id, "b": y.id, "a_value": _value(x), "b_value": _value(y)})
            same = _value(x) == _value(y)
            sentences.append(f"{_name(y)}: {_value(y)} in both." if same else f"{_name(y)}: {_value(x)} -> {_value(y)}.")
        else:
            m = x or y
            changes.append({"field": "measurement", "a": x.id if x else None, "b": y.id if y else None})
            sentences.append(f"Only {'A' if x else 'B'} cites {_name(m)} = {_value(m)}.")
    vda, vdb = va.verdict(a), vb.verdict(b)
    if vda != vdb:
        changes.append({"field": "verdict", "a": vda, "b": vdb})
    ca, cb = _checks_by_name(va.gate_checks(a)), _checks_by_name(vb.gate_checks(b))
    flipped = []
    for key in [k for k in ca if k in cb]:
        if ca[key].passed != cb[key].passed:
            flipped.append({"check": key[0], "a_passed": ca[key].passed, "b_passed": cb[key].passed,
                            "a_detail": ca[key].detail, "b_detail": cb[key].detail})
    if flipped:
        changes.append({"field": "checks", "flipped": flipped})
    if vda == vdb:
        sentences.append(f"Verdict: {vda} in both.")
    else:  # why, in the gate's own words: the first failed check of the side that was not supported
        failed = [(side, c) for side, checks, v in (("A", ca, vda), ("B", cb, vdb)) if v != "supported"
                  for c in checks.values() if not c.passed]
        why = f" ({failed[0][0]}: {failed[0][1].name}: {failed[0][1].detail})." if failed else "."
        sentences.append(f"Verdict: {vda} -> {vdb}{why}")
    return {"kind": "finding", "a": a, "b": b, "changes": changes, "sentences": sentences}


# ------------------------------------------------------------------ campaigns

def _key(view: View, k) -> tuple:
    qs = []
    for i in k.predicate.measurement_ids:
        m = view.measurements.get(i)
        qs.append("?" if m is None else f"derived [{m.unit}]" if m.quantity.startswith("derived:") else m.quantity)
    return (k.predicate.type, tuple(sorted(qs)))


def _centre(k) -> float | None:
    p = k.predicate.params
    if isinstance(p.get("lo"), (int, float)) and isinstance(p.get("hi"), (int, float)):
        return (p["lo"] + p["hi"]) / 2
    return None


def _close(x, y) -> bool:
    """Two accepted ranges that could be about the same quantity: same sign, centres within half their size."""
    cx, cy = _centre(x), _centre(y)
    if cx is None or cy is None:
        return True
    return cx * cy >= 0 and abs(cx - cy) <= 0.5 * max(abs(cx), abs(cy))


def pair_findings(va: View, vb: View) -> tuple[list[tuple], list, list]:
    """Pairs findings that test the same kind of thing (test type and cited quantities, a derived number by its
    unit), nearest accepted range first; a finding's latest version wins a tie over the one it replaced."""
    replaced_a = {k.supersedes for k in va.claims if k.supersedes}
    replaced_b = {k.supersedes for k in vb.claims if k.supersedes}
    order_a = {k.id: n for n, k in enumerate(va.claims)}
    order_b = {k.id: n for n, k in enumerate(vb.claims)}
    candidates = []
    for x in va.claims:
        for y in vb.claims:
            if _key(va, x) != _key(vb, y) or not _close(x, y):
                continue
            cx, cy = _centre(x), _centre(y)
            distance = abs(cx - cy) if cx is not None and cy is not None else 0.0
            candidates.append((distance, (x.id in replaced_a) + (y.id in replaced_b), order_a[x.id], order_b[y.id],
                               x, y))
    pairs, used_a, used_b = [], set(), set()
    for *_, x, y in sorted(candidates, key=lambda c: c[:4]):
        if x.id not in used_a and y.id not in used_b:
            used_a.add(x.id)
            used_b.add(y.id)
            pairs.append((x, y))
    pairs.sort(key=lambda p: order_a[p[0].id])
    return (pairs, [k for k in va.claims if k.id not in used_a], [k for k in vb.claims if k.id not in used_b])


def _first_divergence(ta: list[str], tb: list[str]) -> dict:
    n = next((i for i, (x, y) in enumerate(zip(ta, tb)) if x != y), min(len(ta), len(tb)))
    return {"same_first_calls": n, "a_next": ta[n] if n < len(ta) else None, "b_next": tb[n] if n < len(tb) else None}


def diff_campaigns(va: View, vb: View) -> dict:
    if va.intervention is not None or vb.intervention is not None:
        return _intervention_diff(va,vb)
    sa, sb = va.started(), vb.started()
    models_a, models_b = sa.get("models") or {}, sb.get("models") or {}
    ta, tb = va.tools(), vb.tools()
    pairs, only_a, only_b = pair_findings(va, vb)
    sentences = []
    for role in ("planner", "pointer", "second_opinion"):
        x, y = models_a.get(role), models_b.get(role)
        if x != y:
            sentences.append(f"{role.replace('_', ' ').capitalize()}: {x or 'not recorded'} vs {y or 'not recorded'}.")
    split = _first_divergence(ta, tb)
    if split["a_next"] is None and split["b_next"] is None:
        sentences.append(f"Both made the same {len(ta)} tool calls.")
    else:
        sentences.append(f"Both began with the same {split['same_first_calls']} tool calls; then A called "
                         f"{split['a_next'] or 'nothing more'} and B called {split['b_next'] or 'nothing more'}.")
    sentences.append(f"Tool calls: {len(ta)} vs {len(tb)}. Model cost: ${va.cost():.2f} vs ${vb.cost():.2f}.")
    la, lb = va.lessons_loaded(), vb.lessons_loaded()
    if la or lb:
        sentences.append(f"Lessons loaded at the start: {len(la)} vs {len(lb)}.")
    count = lambda v: sum(1 for k in v.claims if v.verdict(k.id) == "supported")
    sentences.append(f"Findings: {len(va.claims)} proposed, {count(va)} supported vs {len(vb.claims)} proposed, "
                     f"{count(vb)} supported.")
    findings = []
    for x, y in pairs:
        d = diff_claims(va, x.id, vb, y.id)
        findings.append({"a": x.id, "b": y.id, "a_statement": x.statement, "b_statement": y.statement,
                         "a_verdict": va.verdict(x.id), "b_verdict": vb.verdict(y.id), "sentences": d["sentences"],
                         "changes": d["changes"]})
    return {"kind": "campaign", "a": va.campaign.id, "b": vb.campaign.id,
            "models": {"a": models_a, "b": models_b},
            "tool_calls": {"a": len(ta), "b": len(tb), **split},
            "cost_usd": {"a": va.cost(), "b": vb.cost()},
            "lessons_loaded": {"a": la, "b": lb},
            "findings": findings,
            "only_a": [{"id": k.id, "statement": k.statement, "verdict": va.verdict(k.id)} for k in only_a],
            "only_b": [{"id": k.id, "statement": k.statement, "verdict": vb.verdict(k.id)} for k in only_b],
            "sentences": sentences}


def _intervention_diff(va,vb):
    def data(v):return v.intervention.model_dump() if v.intervention is not None else {'policies':[],'scores':[],'decisions':[]}
    a,b=data(va),data(vb)
    def scores(d):
        names={p['id']:p['name'] for p in d['policies']}
        return {(names[s['policy_id']],s['split'],s['subset']):{k:s.get(k) for k in
            ('n','unsafe','missed','abstain','brier','logloss','accuracy','accuracy_interval','watch_time_s','total_cost')} for s in d['scores']}
    aa,bb=scores(a),scores(b);changes=[]
    for key in sorted(aa.keys()|bb.keys()):
        if aa.get(key)!=bb.get(key):changes.append(dict(policy=key[0],split=key[1],subset=key[2],a=aa.get(key),b=bb.get(key)))
    return dict(kind='campaign',a=va.campaign.id,b=vb.campaign.id,
        intervention=dict(score_changes=changes,a_decisions=len(a['decisions']),b_decisions=len(b['decisions'])),
        sentences=[f'{len(changes)} intervention policy/split/subset scores differ; {len(a["decisions"])} vs {len(b["decisions"])} committed episodes.'])


def compare(L, a: str, b: str) -> dict:
    """Diff two campaigns, two findings or two runs by id, from one ledger."""
    kind_a, kind_b = L.kind_of(a), L.kind_of(b)
    if kind_a is None or kind_b is None:
        raise LookupError("Not found: " + (a if kind_a is None else b))
    if kind_a != kind_b or kind_a not in ("campaign", "claim", "run"):
        raise ValueError("Compare two campaigns, two findings or two runs")
    if kind_a == "campaign":
        return diff_campaigns(View.from_ledger(L, a), View.from_ledger(L, b))
    rec_a, rec_b = L.get(kind_a, a), L.get(kind_b, b)
    va, vb = View.from_ledger(L, rec_a.campaign_id), View.from_ledger(L, rec_b.campaign_id)
    return diff_claims(va, a, vb, b) if kind_a == "claim" else diff_runs(va, a, vb, b)
