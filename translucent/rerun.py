"""What-if branches: a person changes one assumption behind a finding, and only the work that depends on it runs again.

A fork is a new campaign (the branch) plus a Fork record naming the parent's finding and the overrides. The parent's
records are never touched: the branch cites the parent's clean evidence directly and records its own versions of
everything the override reaches. The rerun is deterministic and makes no model calls; when a run that depends on the
override needs a model or SAM, it stops and says the branch needs the agent.
"""
from __future__ import annotations

import math
import time
import uuid

from translucent import recordings, tools
from translucent.contracts import Campaign, Fork, Override
from translucent.verify import evidence

RERUNNABLE = {"lookup_calibration", "read_cell_voltage", "measure_growth", "run_analysis_code"}
REPLAYABLE = {"segment_images", "track_objects"}   # in a replication, SAM answers are replayed from their recording
REMOTE = {"run_analysis_code"}          # runs in a remote sandbox: a rerun that reaches it is a queued, authorized job
MAX_INLINE_STEPS = 12                   # a rerun longer than this is queued too
MAX_FORKS_PER_CLAIM = 20
UNITS = {"time_base_interval": {"s"}, "voltage_offset_s": {"s"}, "short_threshold_v": {"V"},
         "spatial_scale": {"px/mm", "um/px"}}
POSITIVE = {"spatial_scale", "time_base_interval", "short_threshold_v"}
VOLTAGE = {"short_threshold_v", "voltage_offset_s"}
WORDS = {"spatial_scale": "the image scale", "time_base_interval": "the time between images",
         "voltage_offset_s": "the voltage clock offset", "short_threshold_v": "the short-circuit threshold",
         "finding_window": "the accepted range"}


def describe(o: Override) -> str:
    value = "–".join(f"{v:g}" for v in o.value) if isinstance(o.value, list) else f"{o.value:g}"
    return f"{WORDS[o.quantity]} is {value} {o.unit}".rstrip()


def _used(L, claim, ev) -> dict[str, list]:
    """For each override quantity, what in the finding's evidence it would replace (empty: the finding never used it)."""
    ms = [L.get("measurement", m["id"]) for m in ev["measurements"]]
    runs = {r["id"]: r for r in ev["runs"]}
    return {
        "spatial_scale": [m for m in ms if m.quantity == "spatial_scale"],
        "time_base_interval": [m for m in ms if m.quantity == "time_base"
                               and runs.get(m.run_id, {}).get("tool") == "lookup_calibration"],
        "short_threshold_v": [m for m in ms if runs.get(m.run_id, {}).get("tool") == "read_cell_voltage"],
        "voltage_offset_s": [m for m in ms if runs.get(m.run_id, {}).get("tool") == "read_cell_voltage"],
        "finding_window": [claim] if {"lo", "hi", "tol"} & set(claim.predicate.params) else [],
    }


def unused_overrides(L, fork: Fork) -> list[str]:
    """Override quantities the re-examined finding never depended on."""
    if fork.claim_id is None:
        return []
    claim = L.get("claim", fork.claim_id)
    used = _used(L, claim, evidence(L, claim.id))
    return [o.quantity for o in fork.overrides if not used[o.quantity]]


def _check_value(o: Override, claim) -> None:
    values = o.value if isinstance(o.value, list) else [o.value]
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values + [o.uncertainty]):
        raise ValueError(f"{WORDS[o.quantity]} needs finite numbers")
    if o.uncertainty < 0:
        raise ValueError(f"the uncertainty of {WORDS[o.quantity]} cannot be negative")
    if o.quantity == "finding_window":
        unit = claim.predicate.params.get("unit")
        if unit is not None and o.unit != unit:
            raise ValueError(f"the accepted range is in the finding's unit, {unit}, not {o.unit}")
        ok = len(values) == 2 and values[0] <= values[1] if isinstance(o.value, list) else values[0] >= 0
        if not ok:
            raise ValueError("an accepted range is [lo, hi] with lo <= hi, or one tolerance of at least 0")
        return
    if isinstance(o.value, list):
        raise ValueError(f"{WORDS[o.quantity]} is one number")
    if o.unit not in UNITS[o.quantity]:
        raise ValueError(f"{WORDS[o.quantity]} is given in {' or '.join(sorted(UNITS[o.quantity]))}, not {o.unit}")
    if o.quantity in POSITIVE and not o.value > 0:
        raise ValueError(f"{WORDS[o.quantity]} must be greater than 0")


def check(L, parent_id: str, claim_id: str | None, overrides: list) -> tuple:
    """Validate a what-if without recording anything. Returns (parent campaign, finding, overrides)."""
    parent = L.get("campaign", parent_id)
    if parent is None:
        raise LookupError("Campaign not found")
    overrides = [o if isinstance(o, Override) else Override.model_validate(o) for o in overrides]
    if not overrides:
        raise ValueError("a what-if changes at least one assumption")
    if len({o.quantity for o in overrides}) != len(overrides):
        raise ValueError("each assumption can be changed once per what-if")
    if claim_id is None:
        raise ValueError("a what-if re-examines one finding")
    claim = L.get("claim", claim_id)
    if claim is None or claim.campaign_id != parent_id:
        raise LookupError("Finding not found in this campaign")
    for o in overrides:
        _check_value(o, claim)
    unused = [o.quantity for o in overrides if not _used(L, claim, evidence(L, claim.id))[o.quantity]]
    if unused:
        raise ValueError(f"this finding does not depend on {', '.join(WORDS[q] for q in unused)}")
    return parent, claim, overrides


def create_fork(L, parent_id: str, *, author: str, reason: str, overrides: list, claim_id: str | None) -> Fork:
    """Record the branch campaign and the fork, and annotate both event streams. Changes nothing in the parent."""
    parent, claim, overrides = check(L, parent_id, claim_id, overrides)
    what = "; ".join(describe(o) for o in overrides)
    branch = Campaign(id=f"cmp_{uuid.uuid4().hex[:10]}", objective=f"What if {what}? Re-examine: {claim.statement}",
                      title=f"What if {what}"[:90], inputs=parent.inputs, memory=parent.memory, budget_usd=0.0,
                      created_at=time.time())
    L.add(branch)
    fork = L.add(Fork(id=f"fork_{uuid.uuid4().hex[:10]}", campaign_id=branch.id, parent_campaign_id=parent_id,
                      claim_id=claim_id, author=author, reason=reason, overrides=overrides, created_at=time.time()))
    for cid in (branch.id, parent_id):
        L.emit(cid, "fork.created", "person:" + author, f"{author} asked: what if {what}?", detail=reason,
               refs=[fork.id, claim_id, branch.id], data={"fork": fork.model_dump(mode="json")})
    return fork


def _map(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_map(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: _map(v, mapping) for k, v in value.items()}
    return value


def _pair(old: list, new: list) -> dict[str, str]:
    """Old measurement id -> its counterpart from the re-executed run: same quantity, subject and label; the nearest
    value when several match (e.g. two shorts)."""
    out, left = {}, list(new)
    for m in old:
        same = [n for n in left if (n.quantity, n.subject, n.label) == (m.quantity, m.subject, m.label)]
        if not same:
            continue
        if isinstance(m.value, (int, float)) and all(isinstance(n.value, (int, float)) for n in same):
            best = min(same, key=lambda n: abs(n.value - m.value))
        else:
            best = same[0]
        left.remove(best)
        out[m.id] = best.id
    return out


def declare(L, fork: Fork) -> dict[str, str]:
    """Record the fork's scale and clock overrides as declared calibrations in the branch (for an agent to cite).
    Returns override quantity -> measurement id."""
    ctx = tools.Context(L, fork.campaign_id, step_id="what-if", assumptions=frozenset({fork.id}))
    out = {}
    video = L.get("campaign", fork.campaign_id).inputs[0]
    for o in fork.overrides:
        if o.quantity in ("spatial_scale", "time_base_interval"):
            q = "spatial_scale" if o.quantity == "spatial_scale" else "time_base"
            res = tools.call(ctx, "lookup_calibration", {"video_id": video, "quantity": q, "assumption": fork.id})
            if res.status == "ok" and res.data.get("measurement_id"):
                out[o.quantity] = res.data["measurement_id"]
    return out


def steps(L, claim, overrides) -> tuple[list, list, set]:
    """The finding's runs in order, the ones the overrides reach (re-executed by a rerun), and the seed run ids."""
    ev = evidence(L, claim.id)
    used = _used(L, claim, ev)
    runs = sorted((L.get("run", r["id"]) for r in ev["runs"]), key=lambda r: (r.started_at, r.id))
    seeds = set()
    for o in overrides:
        if o.quantity != "finding_window":
            seeds |= {m.run_id for m in used[o.quantity]}
    dirty, dirty_ms = [], set()
    for r in runs:
        if r.id in seeds or any(i in dirty_ms for i in r.input_ids):
            dirty.append(r)
            dirty_ms |= {m.id for m in L.list("measurement", ref=r.id)}
    return runs, dirty, seeds


def needs_job(L, claim, overrides) -> str | None:
    """Why a rerun of this what-if cannot run inside a request (None: it is short and strictly local)."""
    _, dirty, _ = steps(L, claim, overrides)
    remote = sorted({r.tool for r in dirty} & REMOTE)
    if remote:
        return f"it re-runs {', '.join(remote)} in the remote sandbox"
    if len(dirty) > MAX_INLINE_STEPS:
        return f"it re-runs {len(dirty)} steps"
    return None


def rerun(L, fork_id: str) -> dict:
    """Re-execute only the runs the fork's overrides reach, then re-propose the finding in the branch.

    Returns {"status": "done" | "needs_agent" | "failed", "reason", "claim_id", "verdict", "reran", "cited"}."""
    fork = L.get("fork", fork_id)
    if fork is None:
        raise LookupError("What-if not found")
    claim = L.get("claim", fork.claim_id)
    ev = evidence(L, claim.id)
    overrides = {o.quantity: o for o in fork.overrides}
    runs, dirty, seeds = steps(L, claim, fork.overrides)
    ctx = tools.Context(L, fork.campaign_id, step_id="what-if", assumptions=frozenset({fork.id}))
    from translucent.runtime import setup_snapshot
    setup = L.put_json(setup_snapshot(L, L.get("campaign", fork.campaign_id), {"profile": "rerun", "models": {}}),
                       kind="report", label="Setup")
    L.emit(fork.campaign_id, "campaign.started", "runtime", "Re-checking the finding under the changed assumption",
           detail=f"Re-running {len(dirty)} of {len(runs)} steps behind the finding; the rest is cited unchanged.",
           refs=[fork.id, setup.id], data={"mode": "rerun", "fork_id": fork.id, "setup_id": setup.id})
    result = {"status": "done", "reason": None, "claim_id": None, "verdict": None, "reran": [],
              "cited": sorted(r.id for r in runs if r not in dirty)}

    def stop(status, reason):
        result.update(status=status, reason=reason)
        L.emit(fork.campaign_id, "campaign.finished", "runtime",
               "What-if needs the agent" if status == "needs_agent" else "What-if could not be re-checked",
               detail=reason, data=result)
        return result

    blocked = next((r for r in dirty if r.tool not in RERUNNABLE), None)
    if blocked is not None:
        return stop("needs_agent", f"{blocked.tool} would have to run again under the changed assumption, and it "
                                   "needs a model or SAM; this branch needs the agent.")
    mapping = {}
    for r in dirty:
        args = _map(dict(r.params), mapping)
        if isinstance(args.get("code"), str):  # analysis code often names its inputs by id: the same code, new ids
            for old, new in mapping.items():
                args["code"] = args["code"].replace(old, new)
        if r.id in seeds:
            args["assumption"] = fork.id
        res = tools.call(ctx, r.tool, args)
        result["reran"].append(res.run_id)
        if res.status != "ok":
            return stop("failed", f"{r.tool} failed in the branch: {res.summary[:300]}")
        old = L.list("measurement", ref=r.id)
        new = L.list("measurement", ref=res.run_id)
        pairs = _pair(old, new)
        lost = [m for m in old if m.id not in pairs and m.id in {x["id"] for x in ev["measurements"]}]
        if lost:
            first = res.summary.splitlines()[0] if res.summary else ""
            return stop("failed", f"Under the changed assumption {r.tool} no longer produces "
                                  f"{lost[0].label or lost[0].quantity}: {first[:300]}")
        mapping.update(pairs)
    params = dict(claim.predicate.params)
    if "finding_window" in overrides:
        w = overrides["finding_window"].value
        if isinstance(w, list):
            params.update(lo=float(w[0]), hi=float(w[1]))
        else:
            params["tol"] = float(w)
    res = tools.call(ctx, "propose_finding", {"type": claim.predicate.type, "params": params,
                                              "measurement_ids": _map(claim.predicate.measurement_ids, mapping),
                                              "statement": claim.statement})
    result.update(claim_id=res.data.get("claim_id"), verdict=res.data.get("verdict"))
    L.emit(fork.campaign_id, "campaign.finished", "runtime", "What-if re-checked",
           detail=f"Re-ran {len(result['reran'])} steps; the checker says {result['verdict']}.", data=result)
    return result


def replicate(L, claim_id: str, *, campaign_id: str | None = None) -> dict:
    """Re-execute every step behind a finding, except the experiment's own record lookups, as new runs: the same tools
    and arguments in their original order, SAM answers replayed from their recording (never live), analysis code run
    again in the sandbox. Every run is recorded under step "replicate:<claim>:<n>", failed ones included.

    Returns {"status": "done" | "failed" | "needs_agent", "reason", "step", "runs", "measurements": {old: new}}."""
    from translucent.gate import TRUSTED_RECORDS
    claim = L.get("claim", claim_id)
    if claim is None:
        raise LookupError("Finding not found")
    campaign_id = campaign_id or claim.campaign_id
    ev = evidence(L, claim.id)
    runs = sorted((L.get("run", r["id"]) for r in ev["runs"]), key=lambda r: (r.started_at, r.id))
    todo = [r for r in runs if r.tool not in TRUSTED_RECORDS]
    prefix = f"replicate:{claim_id}:"
    n = len({r.step_id for r in L.list("run", campaign_id=campaign_id) if r.step_id.startswith(prefix)}) + 1
    out = {"status": "done", "reason": None, "step": f"{prefix}{n}", "runs": [], "measurements": {}}
    blocked = next((r for r in todo if r.tool not in RERUNNABLE | REPLAYABLE), None)
    if blocked is not None:
        return {**out, "status": "needs_agent", "reason": f"{blocked.tool} cannot be re-executed without the agent"}
    ctx = tools.Context(L, campaign_id, step_id=out["step"], meta=recordings.RecordedSAM("strict"), fresh=True)
    mapping = {}
    for r in todo:
        args = _map(dict(r.params), mapping)
        if isinstance(args.get("code"), str):
            for old, new in mapping.items():
                args["code"] = args["code"].replace(old, new)
        res = tools.call(ctx, r.tool, args)
        if res.run_id not in out["runs"]:
            out["runs"].append(res.run_id)
        if res.status != "ok":
            return {**out, "status": "failed", "reason": f"{r.tool} failed when replicated: {res.summary[:300]}"}
        new_run = L.get("run", res.run_id)
        mapping.update({a: b for a, b in zip(r.output_ids, new_run.output_ids) if a != b})
        pairs = _pair(L.list("measurement", ref=r.id), L.list("measurement", ref=res.run_id))
        mapping.update(pairs)
        out["measurements"].update(pairs)
    return out


def branches(L, claim_id: str) -> dict:
    """A finding's alternatives: the chain of findings that replace or are replaced by it, what-if branches made from
    it, and findings in other campaigns that test the same quantity."""
    claim = L.get("claim", claim_id)
    if claim is None:
        raise LookupError("Finding not found")
    same = lambda k: {"id": k.id, "campaign_id": k.campaign_id, "statement": k.statement,
                      "status": L.claim_status(k.id)}
    chain, seen = [], {claim.id}
    k = claim
    while k.supersedes and k.supersedes not in seen:
        seen.add(k.supersedes)
        k = L.get("claim", k.supersedes)
        chain.append({**same(k), "relation": "replaced"})
    others = L.list("claim", campaign_id=claim.campaign_id)
    for k in others:
        if k.supersedes == claim.id:
            chain.append({**same(k), "relation": "replaces it"})
    forks = []
    for f in L.list("fork", ref=claim_id):
        out = [same(k) for k in L.list("claim", campaign_id=f.campaign_id)]
        state = next((e.data for e in reversed(L.events(f.campaign_id)) if e.type == "campaign.finished"), {})
        forks.append({"fork": f.model_dump(mode="json"), "branch_campaign_id": f.campaign_id, "findings": out,
                      "status": state.get("status", "running"), "reason": state.get("reason")})

    def quantities(k):
        qs = []
        for i in k.predicate.measurement_ids:
            m = L.get("measurement", i)
            qs.append(f"derived [{m.unit}]" if m.quantity.startswith("derived:") else m.quantity)
        return (k.predicate.type, tuple(sorted(qs)))
    mine = quantities(claim)
    branch_ids = {f.campaign_id for f in L.list("fork")}
    elsewhere = [same(k) for k in L.list("claim") if k.campaign_id != claim.campaign_id
                 and k.campaign_id not in branch_ids and quantities(k) == mine]
    return {"claim_id": claim_id, "chain": chain, "forks": forks, "same_quantity": elsewhere}
