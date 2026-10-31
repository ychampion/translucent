"""Learning capture and export for loop campaigns (design §2.13).

Nothing new is stored: every episode is derived from the campaign's hash-chained loop records (requests and their
versions, the engineer's approvals, rejections, revisions and cancellations, results, failures, comparisons,
amendments), the planner's receipts (the turn that proposed a request and the digest of the context it saw), the
runs and measurements a result names, and lessons.

`episodes(L, campaign_ids)` yields one episode per request version, plus one per amendment (`"kind": "amendment"`).
`export(L, scope=..., out=...)` writes them as JSONL: a header line, then one episode per line. Free text goes through
`scrub`; raw series are omitted unless `include_series`; the held-out split (by campaign or by time, seeded) is fixed
and recorded in the header before anything is scored. Only campaigns of the ledger the scope names are exported.

`lessons(L, cid, objective)` is the planner's memory hook (loop_planner.planner_context's `lessons`): the lessons a
loop campaign loads, by memory.select, which relates a `system:<id>` lesson to every loop campaign on that system.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Annotated, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from translucent import chain, memory, units
from translucent.ledger import Ledger
from translucent.loop import actor_kind, current_objective, request_states
from translucent.loop import campaign_ids as loop_campaign_ids
from translucent.loop_compare import half_width, select_best
from translucent.loop_contracts import LoopRecord, ObjectiveData
from translucent.loop_view import _amendments
from translucent.scrub import scrub, scrub_strings

SCHEMA = "translucent.learning/1"


# ------------------------------------------------------------------ the schema (every line is validated on export)

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class EpisodeSystem(_Strict):
    id: str
    version: str


class EpisodeContext(_Strict):
    objective_version: int
    objective_digest: str                         # chain.digest of the objective at that version
    seen_record_ids: list[str]                    # loop records (not planner receipts) the proposing turn's context held
    context_sha256: str | None                    # the turn_started receipt's digest; None when a person proposed
    turn: int | None


class Proposal(_Strict):
    by: str
    version: int
    supersedes: str | None
    action: str
    executor: str
    params: dict[str, float | str]
    units: dict[str, str]
    rationale: str
    expected_effect: str
    prediction: dict[str, dict]
    cost: dict
    evidence_ids: list[str]


class Revision(_Strict):
    record_id: str                                # the next version's loop record
    version: int
    params_diff: dict[str, tuple[float | str | None, float | str | None]]   # name -> [before, after]


class Engineer(_Strict):
    decision: Literal["approved", "auto_approved", "rejected", "revised", "superseded", "cancelled", "pending"]
    by: str | None
    reason: str | None
    revision: Revision | None


class OutcomeValue(_Strict):
    value: float | None
    unit: str
    h: float


class Outcome(_Strict):
    state: str                                    # the request state, or "superseded" for an older version
    basis: str | None
    result_id: str | None
    values: dict[str, OutcomeValue]
    categories: dict[str, str]
    series: dict[str, dict] | None                # None: omitted (the default)
    failure: dict | None                          # {"error", "retryable"}
    refused_uploads: list[list[str]]


class PredictionCheck(_Strict):
    predicted: float
    lo: float | None
    hi: float | None
    measured: float
    unit: str
    abs_err: float
    inside: bool | None                           # None when the prediction gave no interval


class ComparisonSummary(_Strict):
    id: str
    verdict: str
    objective_version: int
    basis: str
    reasons: list[str]


class Validation(_Strict):
    prediction: dict[str, PredictionCheck]
    comparison: ComparisonSummary | None          # the result's latest comparison


class Labels(_Strict):
    changed_best: bool | None                     # its first comparison made it the campaign's best
    violated_constraint: bool | None
    unnecessary: None = None                      # not derived yet


class RequestEpisode(_Strict):
    kind: Literal["request"]
    episode_id: str                               # the request version's loop record id
    campaign_id: str
    split: Literal["train", "heldout"]
    t: float
    request_id: str
    system: EpisodeSystem
    context: EpisodeContext
    proposal: Proposal
    engineer: Engineer
    outcome: Outcome
    validation: Validation
    labels: Labels


class ObjectiveTerms(_Strict):
    target: dict
    constraints: list[dict]


class AmendmentEpisode(_Strict):
    kind: Literal["amendment"]
    episode_id: str                               # the amendment's loop record id
    campaign_id: str
    split: Literal["train", "heldout"]
    t: float
    system: EpisodeSystem
    by: str
    version: int
    reason: str
    changes: list[str]
    before: ObjectiveTerms
    after: ObjectiveTerms


Episode = TypeAdapter(Annotated[RequestEpisode | AmendmentEpisode, Field(discriminator="kind")])


class Excluded(_Strict):
    campaign_id: str
    reason: str


class Split(_Strict):
    rule: Literal["campaign", "time"]
    seed: int
    heldout_fraction: float
    heldout_campaigns: list[str]                  # rule "campaign"
    heldout_from: float | None                    # rule "time": episodes at or after this time are held out


class Redaction(_Strict):
    free_text: Literal["scrubbed"]
    series: Literal["omitted", "included"]


class ExportReceipt(_Strict):
    campaign_id: str
    chain_head_sha256: str                        # digest of the campaign's latest loop record: pins the whole chain
    records: int


class ExportLesson(_Strict):
    id: str
    text: str
    scope: str
    learned_in: str
    evidence_ids: list[str]


class Header(_Strict):
    schema_: Literal["translucent.learning/1"] = Field(alias="schema")
    customer_scope: str
    ledger_id: str
    campaign_ids: list[str]
    excluded: list[Excluded]
    exported_at: float
    redaction: Redaction
    split: Split
    receipts: list[ExportReceipt]
    lessons: list[ExportLesson]
    counts: dict[str, int]

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ------------------------------------------------------------------ deriving episodes

def _text(s: str | None) -> str | None:
    return scrub(s) if s else s


def _revision_reason(before: str, after: str) -> str | None:
    """LoopService.revise appends "\n\nRevised by <name>: <reason>" to the rationale it copies."""
    if after.startswith(before):
        tail = after[len(before):].lstrip("\n")
        if tail.startswith("Revised by ") and ": " in tail:
            return tail.split(": ", 1)[1]
    return None


def _params_diff(old: dict, new: dict) -> dict:
    return {k: (old.get(k), new.get(k)) for k in dict.fromkeys([*old, *new]) if old.get(k) != new.get(k)}


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _prediction_checks(prediction: dict, measured: dict[str, tuple[float, str]]) -> dict:
    out = {}
    for q, p in prediction.items():
        if q not in measured or not _number(p.get("value")):
            continue
        value, unit = measured[q]
        try:
            predicted = units.convert(p["value"], p["unit"], unit)
            lo = units.convert(p["lo"], p["unit"], unit) if _number(p.get("lo")) else None
            hi = units.convert(p["hi"], p["unit"], unit) if _number(p.get("hi")) else None
        except (ValueError, KeyError):
            continue
        out[q] = {"predicted": predicted, "lo": lo, "hi": hi, "measured": value, "unit": unit,
                  "abs_err": abs(predicted - value),
                  "inside": lo <= value <= hi if lo is not None and hi is not None else None}
    return out


def _objective_digests(L: Ledger, cid: str, recs: list[LoopRecord]) -> dict[int, str]:
    """objective version -> digest of the objective as it stood at that version."""
    out = {}
    for i, rec in enumerate(recs):
        if rec.type in ("objective", "amendment"):
            obj = current_objective(L, cid, recs[:i + 1])
            out[obj.version] = chain.digest(obj.model_dump(mode="json"))
    return out


def _proposing_turns(recs: list[LoopRecord]) -> dict[str, dict]:
    """request id -> {"turn", "context_sha256", "context_seq"} of the planner turn whose proposal created it."""
    started, out = {}, {}
    for r in recs:
        if r.type != "planner":
            continue
        p = r.data["payload"]
        if r.data["phase"] == "turn_started":
            started[p.get("turn")] = p
        elif r.data["phase"] == "tool_result" and p.get("tool") == "propose_experiment":
            res = p.get("result") or {}
            rq = res.get("request_id")
            if rq and "error" not in res and not res.get("duplicate_of_pending") and rq not in out:
                turn = started.get(p.get("turn")) or {}
                out[rq] = {"turn": p.get("turn"), "context_sha256": turn.get("context_sha256"),
                           "context_seq": turn.get("context_seq")}
    return out


def _decisions(recs: list[LoopRecord]) -> dict[str, LoopRecord]:
    """request version record id -> the approval, rejection or cancellation that decided it."""
    latest: dict[str, str] = {}
    out: dict[str, LoopRecord] = {}
    for r in recs:
        if r.type == "request":
            latest[r.data["request_id"]] = r.id
        elif r.type in ("approval", "rejection"):
            out.setdefault(r.data["record_id"], r)
        elif r.type == "cancellation" and (vid := latest.get(r.data["request_id"])):
            out.setdefault(vid, r)
    return out


def _best_changes(recs: list[LoopRecord]) -> dict[str, bool]:
    """result id -> whether its first comparison made it select_best's comparison."""
    by_version: dict[int, dict[str, LoopRecord]] = {}
    out: dict[str, bool] = {}
    for r in recs:
        if r.type != "comparison":
            continue
        version, result_id = r.data["objective_version"], r.data["candidate"]["result_id"]
        latest = by_version.setdefault(version, {})
        _, before = select_best(list(latest.values()))
        latest[result_id] = r
        _, after = select_best(list(latest.values()))
        if result_id not in out:
            out[result_id] = after is not None and after.id == r.id and (before is None or before.id != r.id)
    return out


def _outcome(L: Ledger, st, comparison: LoopRecord | None, include_series: bool) -> dict:
    out = {"state": st.state, "basis": None, "result_id": None, "values": {}, "categories": {}, "series": None,
           "failure": None, "refused_uploads": [[scrub(x) for x in r.data["reasons"]] for r in st.refused]}
    if st.failure is not None:
        out["failure"] = {"error": scrub(st.failure.data["error"]), "retryable": st.failure.data["retryable"]}
    if st.result is None:
        return out
    r = st.result.data
    values = {}
    for q, mid in r["measurements"].items():
        m = L.get("measurement", mid)
        if m is not None and _number(m.value):
            values[q] = {"value": float(m.value), "unit": m.unit, "h": half_width(m.uncertainty)}
    if comparison is not None:                    # objective quantities computed by ops/1
        for q, v in comparison.data["candidate"]["values"].items():
            if q not in values and "missing" not in v:
                values[q] = {"value": v.get("value"), "unit": v["unit"], "h": float(v.get("h") or 0.0)}
    out |= {"basis": r["basis"], "result_id": st.result.id, "values": values, "categories": dict(r["categories"])}
    if include_series:
        series = {}
        for q, aid in r["series"].items():
            body = json.loads(L.blob_path(aid).read_text(encoding="utf-8"))
            series[q] = {"artifact_id": aid, "t": body["t"], "y": body["y"], "unit": body["unit"]}
        out["series"] = series
    return out


def _campaign_episodes(L: Ledger, cid: str, *, include_series: bool = False) -> list[dict]:
    recs = chain.records(L, cid)
    first = ObjectiveData.model_validate(recs[0].data)
    system = {"id": first.system["id"], "version": str(first.system["version"])}
    states = request_states(L, cid, recs)
    digests = _objective_digests(L, cid, recs)
    turns = _proposing_turns(recs)
    decisions = _decisions(recs)
    best = _best_changes(recs)
    latest_cmp: dict[str, LoopRecord] = {}
    for r in recs:
        if r.type == "comparison":
            latest_cmp[r.data["candidate"]["result_id"]] = r
    out: list[dict] = []
    for r in recs:
        if r.type == "amendment":
            prior = current_objective(L, cid, recs[:recs.index(r)])
            after = current_objective(L, cid, recs[:recs.index(r) + 1])
            change = next(a for a in _amendments(recs) if a["seq"] == r.seq)
            out.append({"kind": "amendment", "episode_id": r.id, "campaign_id": cid, "t": r.created_at,
                        "system": system, "by": r.actor, "version": r.data["version"],
                        "reason": scrub(r.data["reason"]), "changes": change["changes"],
                        "before": {"target": prior.target.model_dump(mode="json"),
                                   "constraints": [c.model_dump(mode="json") for c in prior.constraints]},
                        "after": {"target": after.target.model_dump(mode="json"),
                                  "constraints": [c.model_dump(mode="json") for c in after.constraints]}})
            continue
        if r.type != "request":
            continue
        d = r.data
        st = states[d["request_id"]]
        i = next(k for k, v in enumerate(st.versions) if v.id == r.id)
        nxt = st.versions[i + 1] if i + 1 < len(st.versions) else None
        turn = turns.get(d["request_id"]) if d["version"] == 1 and actor_kind(r.actor) == "agent" else None
        seen = [x.id for x in recs if turn and turn["context_seq"] is not None and x.seq <= turn["context_seq"]
                and x.type != "planner"]
        context = {"objective_version": d["objective_version"], "objective_digest": digests[d["objective_version"]],
                   "seen_record_ids": seen, "context_sha256": turn["context_sha256"] if turn else None,
                   "turn": turn["turn"] if turn else None}
        proposal = {"by": r.actor, "version": d["version"], "supersedes": d["supersedes"], "action": d["action"],
                    "executor": d["executor"], "params": dict(d["params"]), "units": dict(d["units"]),
                    "rationale": scrub(d["rationale"]), "expected_effect": scrub(d["expected_effect"]),
                    "prediction": d["prediction"], "cost": d["cost"], "evidence_ids": list(d["evidence_ids"])}
        engineer = {"decision": "pending", "by": None, "reason": None, "revision": None}
        if nxt is not None:
            revision = {"record_id": nxt.id, "version": nxt.data["version"],
                        "params_diff": _params_diff(d["params"], nxt.data["params"])}
            if actor_kind(nxt.actor) == "person":
                engineer = {"decision": "revised", "by": nxt.actor,
                            "reason": _text(_revision_reason(d["rationale"], nxt.data["rationale"])),
                            "revision": revision}
            else:
                engineer = {"decision": "superseded", "by": nxt.actor, "reason": None, "revision": revision}
        elif (dec := decisions.get(r.id)) is not None:
            if dec.type == "approval":
                engineer = {"decision": "approved" if actor_kind(dec.actor) == "person" else "auto_approved",
                            "by": dec.actor, "reason": _text(dec.data.get("note")) or None, "revision": None}
            else:
                engineer = {"decision": "rejected" if dec.type == "rejection" else "cancelled", "by": dec.actor,
                            "reason": scrub(dec.data["reason"]), "revision": None}
        cmp = latest_cmp.get(st.result.id) if nxt is None and st.result is not None else None
        if nxt is None:
            outcome = _outcome(L, st, cmp, include_series)
        else:
            outcome = {"state": "superseded", "basis": None, "result_id": None, "values": {}, "categories": {},
                       "series": None, "failure": None, "refused_uploads": []}
        measured = {q: (v["value"], v["unit"]) for q, v in outcome["values"].items() if v["value"] is not None}
        validation = {"prediction": _prediction_checks(d["prediction"], measured),
                      "comparison": {"id": cmp.id, "verdict": cmp.data["verdict"],
                                     "objective_version": cmp.data["objective_version"], "basis": cmp.data["basis"],
                                     "reasons": [scrub(x) for x in cmp.data["reasons"]]} if cmp else None}
        labels = {"changed_best": best.get(st.result.id) if cmp else None,
                  "violated_constraint": any(c["ok"] is False for c in cmp.data["constraints"]) if cmp else None,
                  "unnecessary": None}
        out.append({"kind": "request", "episode_id": r.id, "campaign_id": cid, "t": r.created_at,
                    "request_id": d["request_id"], "system": system, "context": context, "proposal": proposal,
                    "engineer": engineer, "outcome": outcome, "validation": validation, "labels": labels})
    return out


def episodes(L: Ledger, campaign_ids: list[str], *, include_series: bool = False) -> Iterator[dict]:
    """One episode per request version and one per amendment, campaign by campaign in record order. Each lacks only
    its `split`, which export assigns. Campaigns that are not loop campaigns of this ledger yield nothing."""
    own = set(loop_campaign_ids(L))
    for cid in campaign_ids:
        if cid in own:
            yield from _campaign_episodes(L, cid, include_series=include_series)


# ------------------------------------------------------------------ the held-out split

def _rank(seed: int, key: str) -> str:
    return hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()


def heldout_campaigns(campaign_ids: list[str], fraction: float, seed: int) -> list[str]:
    """The campaigns held out: the round(fraction * n) with the lowest seeded hash, at least one and never all of them
    when there are two or more and fraction > 0. Independent of the order the ids are given in."""
    ids = sorted(set(campaign_ids))
    k = round(fraction * len(ids))
    if len(ids) >= 2 and fraction > 0:
        k = min(max(k, 1), len(ids) - 1)
    elif len(ids) < 2:
        k = 0
    return sorted(sorted(ids, key=lambda c: _rank(seed, c))[:k])


def heldout_from(times: list[float], fraction: float) -> float | None:
    """The time from which episodes are held out: the latest round(fraction * n) episodes; None when none are."""
    ts = sorted(times)
    k = round(fraction * len(ts))
    if fraction > 0 and len(ts) >= 2:
        k = min(max(k, 1), len(ts) - 1)
    elif len(ts) < 2:
        k = 0
    return ts[len(ts) - k] if k else None


# ------------------------------------------------------------------ the export

def export(L: Ledger, *, scope: str, campaign_ids: list[str] | None = None, out: Path | str,
           include_series: bool = False, heldout: Literal["campaign", "time"] = "campaign",
           heldout_fraction: float = 0.2, seed: int = 0, ledger_id: str | None = None) -> dict:
    """Write the ledger's loop campaigns as learning episodes (JSONL: header, then episodes) and return
    {"path", "header", "episodes"}. `scope` is "ledger:<id>" and must name this ledger (default id: its folder
    name); `campaign_ids` defaults to every loop campaign of the ledger, and ids that are not are excluded and
    listed in the header."""
    ledger_id = ledger_id or L.root.name
    if scope != f"ledger:{ledger_id}":
        raise ValueError(f"scope {scope!r} does not name this ledger (ledger:{ledger_id}); export one ledger at a time")
    if heldout not in ("campaign", "time"):
        raise ValueError("heldout is 'campaign' or 'time'")
    if not (isinstance(heldout_fraction, (int, float)) and 0.0 <= heldout_fraction < 1.0):
        raise ValueError("heldout_fraction is at least 0 and below 1")
    own = loop_campaign_ids(L)
    asked = own if campaign_ids is None else list(dict.fromkeys(campaign_ids))
    kept = [c for c in asked if c in set(own)]
    excluded = [{"campaign_id": c, "reason": f"not a loop campaign of ledger {ledger_id}"}
                for c in asked if c not in set(own)]

    own_transaction = not L.db.in_transaction
    if own_transaction:                           # one read transaction: header and episodes see the same records
        L.db.execute("BEGIN")
    try:
        found = list(episodes(L, kept, include_series=include_series))
        receipts = []
        for cid in kept:
            head = chain.latest(L, cid)
            n = L.db.execute("SELECT COUNT(*) FROM record WHERE kind='loop' AND campaign_id=?", (cid,)).fetchone()[0]
            receipts.append({"campaign_id": cid, "chain_head_sha256": head.digest, "records": n})
        lessons = [{"id": x.id, "text": scrub(x.text), "scope": x.scope, "learned_in": x.learned_in,
                    "evidence_ids": list(x.evidence_ids)} for x in L.list("lesson") if x.learned_in in set(kept)]
    finally:
        if own_transaction:
            L.db.execute("ROLLBACK")

    held_campaigns = heldout_campaigns(kept, heldout_fraction, seed) if heldout == "campaign" else []
    cutoff = heldout_from([e["t"] for e in found], heldout_fraction) if heldout == "time" else None
    for e in found:
        held = e["campaign_id"] in held_campaigns if heldout == "campaign" else \
            cutoff is not None and e["t"] >= cutoff
        e["split"] = "heldout" if held else "train"

    header = Header.model_validate({
        "schema": SCHEMA, "customer_scope": scope, "ledger_id": ledger_id, "campaign_ids": kept,
        "excluded": excluded, "exported_at": time.time(),
        "redaction": {"free_text": "scrubbed", "series": "included" if include_series else "omitted"},
        "split": {"rule": heldout, "seed": seed, "heldout_fraction": float(heldout_fraction),
                  "heldout_campaigns": held_campaigns, "heldout_from": cutoff},
        "receipts": receipts, "lessons": lessons,
        "counts": {"request_versions": sum(e["kind"] == "request" for e in found),
                   "amendments": sum(e["kind"] == "amendment" for e in found)},
    }).model_dump(mode="json", by_alias=True)
    lines = [json.dumps(header, sort_keys=True, allow_nan=False)]
    for e in found:
        e = Episode.dump_python(Episode.validate_python(e), mode="json")
        lines.append(json.dumps(scrub_strings(e), sort_keys=True, allow_nan=False))
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"path": str(path), "header": header, "episodes": len(found)}


def read(path: Path | str) -> tuple[dict, list[dict]]:
    """(header, episodes) of an export, each line validated against the schema."""
    lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    header = Header.model_validate_json(lines[0]).model_dump(mode="json", by_alias=True)
    return header, [Episode.dump_python(Episode.validate_json(ln), mode="json") for ln in lines[1:]]


# ------------------------------------------------------------------ memory for loop campaigns

def lessons(L: Ledger, cid: str, objective: ObjectiveData | None = None) -> list[dict] | None:
    """The planner_context `lessons` hook: the lessons this loop campaign loads (memory.select; a `system:<id>`
    lesson concerns every loop campaign on that system), or None when there are none or the campaign's memory is
    off, so the context of a campaign without lessons is unchanged."""
    campaign = L.get("campaign", cid)
    if campaign is None or campaign.memory == "off":
        return None
    picked = memory.select(L, campaign)
    if not picked:
        return None
    return [{"id": d["lesson"].id, "text": d["lesson"].text, "scope": d["lesson"].scope, "reason": d["reason"],
             "confidence": d["confidence"], "evidence_ids": list(d["lesson"].evidence_ids)} for d in picked]
