"""Deterministic reading aids derived from the ledger on request; none of them changes a verdict or a record.

- `next_steps`: what is missing, as advice for people and the agent ("run lookup_calibration", "change method, not
  window", "compare with a second method", "a challenge is unanswered", "a loaded lesson was never used").
- `untraced_numbers`: numbers with a unit in the agent's final answer that match no supported finding's value or range.
- `signoffs`: for each person's sign-off or challenge, whether the finding's evidence is still what they looked at.
"""
from __future__ import annotations

import hashlib
import json
import re

from translucent.gate import _half_width

UNITS = {"um/s": "um/s", "µm/s": "um/s", "μm/s": "um/s", "mm/s": "mm/s", "um": "um", "µm": "um", "μm": "um",
         "mm": "mm", "nm": "nm", "s": "s", "min": "min", "h": "h", "V": "V", "mV": "mV", "px/mm": "px/mm", "px": "px",
         "%": "%", "seconds": "s", "second": "s", "hours": "h", "hour": "h", "minutes": "min", "volts": "V"}
_NUMBER = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)\s*(" + "|".join(sorted((re.escape(u) for u in UNITS), key=len,
                                                                            reverse=True)) + r")(?![\w/])")


def _gate_reviews(L, claim_id):
    return [r for r in L.list("review", ref=claim_id) if r.reviewer == "gate"]


def next_steps(L, campaign_id: str) -> list[dict]:
    """Advice, never a verdict: each item {"rule", "text", "refs"}."""
    out = []
    claims = L.list("claim", campaign_id=campaign_id)
    superseded = {k.supersedes for k in claims if k.supersedes}
    open_claims = [k for k in claims if k.id not in superseded]
    for k in open_claims:
        latest = (_gate_reviews(L, k.id) or [None])[-1]
        if latest is None or latest.verdict == "supported":
            continue
        for c in latest.checks:
            if c.name == "scale and timing recorded" and not c.passed and "no recorded" in c.detail:
                missing = "time_base" if "time_base" in c.detail else "spatial_scale"
                out.append({"rule": "missing calibration", "refs": [k.id],
                            "text": f"\"{k.statement}\" was refused for a missing {missing.replace('_', ' ')}: run "
                                    f"lookup_calibration for {missing}, or ask a person for it."})
                break
    failed = {}
    for k in claims:
        latest = (_gate_reviews(L, k.id) or [None])[-1]
        if latest is None or latest.verdict == "supported":
            continue
        ms = [L.get("measurement", i) for i in k.predicate.measurement_ids]
        quantity = tuple(sorted(m.quantity for m in ms))
        for c in latest.checks:
            if not c.passed:
                failed.setdefault((quantity, c.name), []).append(k.id)
    for (quantity, check), ids in failed.items():
        if len(ids) >= 2:
            out.append({"rule": "same check failed twice", "refs": ids,
                        "text": f"{len(ids)} attempts at {', '.join(quantity)} failed \"{check}\" the same way: change "
                                "the method, not the window."})
    agreeing = {i for k in claims if k.predicate.type == "values_agree" for i in k.predicate.measurement_ids}
    for k in open_claims:
        if L.claim_status(k.id) == "supported" and k.predicate.type == "value_within" and \
                not set(k.predicate.measurement_ids) & agreeing:
            out.append({"rule": "one method only", "refs": [k.id],
                        "text": f"\"{k.statement}\" rests on one method: compare its value with a second method or "
                                "a replication (values_agree)."})
    for k in claims:
        reviews = L.list("review", ref=k.id)
        for i, r in enumerate(reviews):
            if r.reviewer == "human" and r.verdict == "refuted":
                answered = k.id in superseded or any(x.reviewer == "gate" for x in reviews[i + 1:])
                if not answered:
                    out.append({"rule": "challenge unanswered", "refs": [k.id, r.id],
                                "text": f"{r.author or 'A person'} challenged \"{k.statement}\": {r.reason} No new "
                                        "check or replacement finding answers it yet."})
    events = L.events(campaign_id)
    loaded = [(e.seq, ref) for e in events if e.type == "memory.loaded" for ref in e.refs]
    later = " ".join(json.dumps(e.model_dump(mode="json")) for e in events if e.type != "memory.loaded")
    for _, lid in loaded:
        lesson = L.get("lesson", lid)
        if lesson is not None and lid not in later:
            out.append({"rule": "lesson unused", "refs": [lid],
                        "text": f"The lesson \"{lesson.text}\" was loaded but nothing in this campaign cites it."})
    return out


def _close(value: float, shown: str, target: float) -> bool:
    """Whether a number as written is the target rounded to the digits written, or within 1% of it."""
    decimals = len(shown.split(".")[1]) if "." in shown else 0
    return abs(value - target) <= max(0.5 * 10 ** -decimals + 1e-9, abs(target) * 0.01)


def untraced_numbers(L, campaign_id: str) -> dict:
    """Numbers with a unit in the final answer that no supported finding of this campaign carries (its measured
    value or the range it was checked against). A summary-level check, kept out of the gate and verify."""
    finished = next((e for e in reversed(L.events(campaign_id)) if e.type == "campaign.finished"), None)
    answer = (finished.data.get("answer") if finished else None) or ""
    targets = []
    for k in L.list("claim", campaign_id=campaign_id):
        if L.claim_status(k.id) != "supported":
            continue
        for i in k.predicate.measurement_ids:
            m = L.get("measurement", i)
            if isinstance(m.value, (int, float)) and not isinstance(m.value, bool):
                h = _half_width(m)
                targets += [(UNITS.get(m.unit, m.unit), v) for v in (m.value, h, m.value - h, m.value + h)]
        unit = k.predicate.params.get("unit")
        for key in ("lo", "hi", "tol"):
            v = k.predicate.params.get(key)
            if isinstance(v, (int, float)) and unit:
                targets.append((UNITS.get(unit, unit), float(v)))
    numbers, untraced = [], []
    for m in _NUMBER.finditer(answer):
        shown, unit = m.group(1), UNITS[m.group(2)]
        value = float(shown)
        numbers.append({"text": m.group(0), "value": value, "unit": unit})
        if not any(u == unit and _close(value, shown, t) for u, t in targets):
            untraced.append({"text": m.group(0), "value": value, "unit": unit})
    return {"answer": answer or None, "numbers": len(numbers), "untraced": untraced}


def _digest(L, claim, ev: dict, at: float) -> str:
    """A hash of the finding and the evidence it was proposed on, with the runs invalidated by time `at`."""
    created = L.db.execute("SELECT inserted_at FROM record WHERE id=?", (claim.id,)).fetchone()[0]
    rows = []
    for kind, key in (("measurement", "measurements"), ("run", "runs"), ("artifact", "artifacts")):
        for x in ev[key]:
            row = L.db.execute("SELECT body, inserted_at FROM record WHERE id=?", (x["id"],)).fetchone()
            if row and row[1] <= created:
                rows.append([kind, x["id"], hashlib.sha256(row[0].encode()).hexdigest()])
    runs = {x["id"] for x in ev["runs"]}
    gone = sorted(r for r, t in L.db.execute("SELECT run_id, at FROM invalidation") if r in runs and t <= at)
    body = json.dumps({"claim": claim.model_dump(mode="json"), "records": sorted(rows), "invalidated": gone},
                      sort_keys=True)
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def signoffs(L, claim_id: str) -> list[dict]:
    """Each person's review of a finding, with the evidence digest when they reviewed it and now."""
    from translucent.verify import evidence
    claim = L.get("claim", claim_id)
    ev = evidence(L, claim_id)
    runs = {x["id"] for x in ev["runs"]}
    invalidations = list(L.db.execute("SELECT run_id, at FROM invalidation"))
    now = _digest(L, claim, ev, float("inf"))
    out = []
    for r in L.list("review", ref=claim_id):
        if r.reviewer != "human":
            continue
        at = L.db.execute("SELECT inserted_at FROM record WHERE id=?", (r.id,)).fetchone()[0]
        then = _digest(L, claim, ev, at)
        changed = sorted(rid for rid, t in invalidations if t > at and rid in runs)
        who = r.author or "a person"
        out.append({"review_id": r.id, "author": r.author, "verdict": r.verdict, "evidence_digest": then,
                    "current_digest": now, "current": then == now,
                    "text": (f"{who[0].upper() + who[1:]} reviewed this exact evidence" if then == now else
                             f"Evidence changed since {who} reviewed it"
                             + (f" (run {', '.join(changed)} invalidated)" if changed else ""))})
    return out
