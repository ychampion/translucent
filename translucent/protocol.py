"""Pre-registered metrics, competing methods, and declaring a winner only after an independent re-derivation.

A Protocol is recorded before the campaign starts and fixes, per metric, which quantities count, the unit and the test
(predicate type and params). A finding that names a metric is "on protocol" only if its test is exactly the metric's;
otherwise it is still checked by the gate as usual but can never win. A method is the (tool, code) that produced the
cited measurement. `decide` passes only with enough distinct methods, a supported on-protocol winner, a supported
verification finding that re-derives the winner's value from independent runs, every replication of the winner
successful, and no supported on-protocol attempt by another method disagreeing. Nothing here changes a gate verdict.
"""
from __future__ import annotations

import math
import re

from translucent.contracts import Check, Claim, Decision, Metric, Protocol
from translucent.gate import TRUSTED_RECORDS, _half_width

# the strict grammar of a metric's test: the gate's own predicate types, typed params, nothing else
PARAMS = {"value_within": ({"lo", "hi", "unit"}, set()),
          "events_coincide": ({"tol"}, {"unit"}),
          "trend": ({"direction"}, {"steady_tolerance"})}
QUANTITY = re.compile(r"^[a-z][a-z0-9_]*(:([a-z][a-z0-9_]*|\*))?$")
REPLICATE = "replicate:"


def _number(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def metric_problems(m: Metric) -> list[str]:
    """Why a metric is not a test the gate can apply exactly as written (empty: it is)."""
    p, out = m.predicate, []
    if p.type not in PARAMS:
        return [f"{m.id}: the test must be one of {', '.join(PARAMS)}, not {p.type}"]
    required, optional = PARAMS[p.type]
    keys = set(p.params)
    out += [f"{m.id}: missing {k}" for k in sorted(required - keys)]
    out += [f"{m.id}: {k} is not a parameter of {p.type}" for k in sorted(keys - required - optional)]
    for k, v in p.params.items():
        text = k in ("unit", "direction")
        if (text and not isinstance(v, str)) or (not text and not _number(v)):
            out.append(f"{m.id}: {k} must be {'text' if text else 'a finite number'}")
    if not out:
        if p.type == "value_within" and p.params["lo"] > p.params["hi"]:
            out.append(f"{m.id}: lo is above hi")
        if p.params.get("unit", m.unit) != m.unit:
            out.append(f"{m.id}: the test's unit {p.params['unit']} differs from the metric's unit {m.unit}")
        if any(p.params.get(k, 0) < 0 for k in ("tol", "steady_tolerance")):
            out.append(f"{m.id}: a tolerance cannot be negative")
        if p.type == "trend" and p.params["direction"] not in ("increasing", "decreasing", "steady"):
            out.append(f"{m.id}: direction must be increasing, decreasing or steady")
    if p.measurement_ids:
        out.append(f"{m.id}: a metric names no measurements; findings cite them")
    if not m.quantities or any(not QUANTITY.fullmatch(q) for q in m.quantities):
        out.append(f"{m.id}: quantities are names like growth_velocity or derived:*")
    if m.min_methods < 1:
        out.append(f"{m.id}: at least one method")
    return out


def protocol_problems(p: Protocol) -> list[str]:
    out = [] if p.metrics else ["a protocol fixes at least one metric"]
    if len({m.id for m in p.metrics}) != len(p.metrics):
        out.append("each metric id once")
    for m in p.metrics:
        out += metric_problems(m)
    return out


def protocol_of(L, campaign_id: str) -> Protocol | None:
    return next(iter(L.list("protocol", campaign_id=campaign_id)), None)


def metric_of(L, campaign_id: str, metric_id: str | None) -> Metric | None:
    p = protocol_of(L, campaign_id)
    return next((m for m in p.metrics if m.id == metric_id), None) if p and metric_id else None


def accepts(metric: Metric, quantity: str) -> bool:
    return any(quantity == q or (q.endswith(":*") and quantity.startswith(q[:-1])) for q in metric.quantities)


def pre_registered(L, campaign_id: str) -> str | None:
    """Why the campaign's protocol does not provably precede its start (None: it does, or there is no protocol)."""
    row = L.db.execute("SELECT rowid, inserted_at, id FROM record WHERE kind='protocol' AND campaign_id=? "
                       "ORDER BY rowid LIMIT 1", (campaign_id,)).fetchone()
    if row is None:
        return None
    rowid, at, pid = row
    started = next((e for e in L.events(campaign_id) if e.type == "campaign.started"), None)
    if started is None:
        return None
    earlier = L.db.execute("SELECT COUNT(*) FROM record WHERE campaign_id=? AND kind NOT IN ('campaign', 'protocol') "
                           "AND rowid<?", (campaign_id, rowid)).fetchone()[0]
    if pid not in started.refs or at > started.t or earlier:
        return f"Protocol {pid} was not recorded before campaign {campaign_id} started"
    return None


def on_protocol(L, claim: Claim) -> tuple[bool, str]:
    """Whether a finding tests its metric exactly as pre-registered."""
    if claim.metric_id is None:
        return False, "names no metric"
    metric = metric_of(L, claim.campaign_id, claim.metric_id)
    if metric is None:
        return False, f"no pre-registered metric {claim.metric_id}"
    if claim.predicate.type != metric.predicate.type or claim.predicate.params != metric.predicate.params:
        return False, "off protocol (test or range chosen after seeing the data)"
    ms = [L.get("measurement", i) for i in claim.predicate.measurement_ids]
    if not any(accepts(metric, m.quantity) for m in ms):
        return False, f"off protocol (cites none of {', '.join(metric.quantities)})"
    return True, "on protocol"


def measurement(L, claim: Claim, metric: Metric | None):
    """The measurement a finding offers for its metric: the first cited one of an accepted quantity."""
    ms = [L.get("measurement", i) for i in claim.predicate.measurement_ids]
    return next((m for m in ms if metric and accepts(metric, m.quantity)), ms[0] if ms else None)


def method(L, claim: Claim, metric: Metric | None) -> tuple[str, str | None]:
    m = measurement(L, claim, metric)
    run = L.get("run", m.run_id)
    return run.tool, run.code_sha


def describe_method(m: tuple[str, str | None]) -> str:
    return m[0] + (f" (code {m[1][:12]})" if m[1] else "")


def ancestry(L, measurement_id: str) -> set[str]:
    """The runs a measurement's value passed through, other than the trusted record lookups. A file made by several
    runs (content-addressed, so a re-execution makes the same one) belongs to the maker in the consuming run's own
    step when there is one, else to the makers recorded before the consuming run."""
    order = dict(L.db.execute("SELECT id, rowid FROM record WHERE kind='run'"))
    makers: dict[str, list] = {}
    for r in L.list("run"):
        for a in r.output_ids:
            makers.setdefault(a, []).append(r)
    runs, seen, todo = set(), set(), [("measurement", measurement_id, None)]
    while todo:
        kind, rid, consumer = todo.pop()
        if (kind, rid, consumer and consumer.id) in seen:
            continue
        seen.add((kind, rid, consumer and consumer.id))
        if kind == "measurement":
            m = L.get("measurement", rid)
            todo.append(("run", m.run_id, None))
            todo += [("measurement", c, None) for c in m.calibration_ids]
        elif kind == "run":
            r = L.get("run", rid)
            if r.tool not in TRUSTED_RECORDS:
                runs.add(r.id)
            todo += [(L.kind_of(i), i, r) for i in r.input_ids if L.kind_of(i) in ("artifact", "measurement")]
        else:
            ok = [r for r in makers.get(rid, []) if r.status == "ok"]
            own = [r for r in ok if consumer and r.step_id == consumer.step_id]
            before = [r for r in ok if consumer and order[r.id] < order[consumer.id]]
            todo += [("run", r.id, None) for r in (own or before or ok)]
    return runs


def independent(L, a_ids: list[str], b_ids: list[str]) -> tuple[bool, str]:
    a = set().union(*(ancestry(L, i) for i in a_ids)) if a_ids else set()
    b = set().union(*(ancestry(L, i) for i in b_ids)) if b_ids else set()
    shared = sorted(a & b)
    if not b:
        return False, "nothing was re-derived"
    if shared:
        return False, f"shares {len(shared)} run(s) with the winner ({', '.join(shared[:3])})"
    return True, f"re-derived through {len(b)} separate run(s); only the experiment's records are shared"


def attempts(L, campaign_id: str, metric_id: str) -> list[Claim]:
    return [k for k in L.list("claim", campaign_id=campaign_id) if k.metric_id == metric_id]


def replications(L, campaign_id: str, claim_id: str) -> list:
    """Every run executed to replicate a finding, failed ones included."""
    return [r for r in L.list("run", campaign_id=campaign_id) if r.step_id.startswith(f"{REPLICATE}{claim_id}:")]


def _verified(L, claim: Claim, metric: Metric, verification: Claim | None) -> Check:
    if verification is None:
        return Check(name="verified by an independent run", passed=False, detail="no verification finding named")
    if L.claim_status(verification.id) != "supported":
        return Check(name="verified by an independent run", passed=False,
                     detail=f"verification finding {verification.id} is {L.claim_status(verification.id)}")
    mine = measurement(L, claim, metric)
    ids = verification.predicate.measurement_ids
    if verification.predicate.type == "values_agree" and mine.id in ids:
        other = [i for i in ids if i != mine.id]
    elif verification.metric_id == metric.id and on_protocol(L, verification)[0]:
        other = ids
    else:
        return Check(name="verified by an independent run", passed=False,
                     detail="the verification must compare the winner's value with another, or pass the same metric")
    ok, detail = independent(L, [mine.id], other)
    return Check(name="verified by an independent run", passed=ok, detail=detail)


def _agree(L, a: Claim, b: Claim, metric: Metric) -> bool:
    ma, mb = measurement(L, a, metric), measurement(L, b, metric)
    if metric.predicate.type == "value_within" and _number(ma.value) and _number(mb.value):
        return abs(ma.value - mb.value) <= _half_width(ma) + _half_width(mb)
    return L.claim_status(b.id) != "refuted"


def decide(L, campaign_id: str, metric_id: str, winner_id: str | None, verification_id: str | None
           ) -> tuple[str, list[Check]]:
    """The outcome of declaring `winner_id` the best method for a metric, with the checks behind it."""
    metric = metric_of(L, campaign_id, metric_id)
    if metric is None:
        return "insufficient_evidence", [Check(name="metric pre-registered", passed=False,
                                               detail=f"no pre-registered metric {metric_id}")]
    why = pre_registered(L, campaign_id)
    checks = [Check(name="metric pre-registered", passed=why is None, detail=why or f"{metric.name}, fixed before the start")]
    tried = attempts(L, campaign_id, metric_id)
    valid = [k for k in tried if on_protocol(L, k)[0]]
    methods = list(dict.fromkeys(method(L, k, metric) for k in valid))
    checks.append(Check(name="methods attempted", passed=len(methods) >= metric.min_methods,
                        detail=f"{len(methods)} distinct method(s) on protocol, {metric.min_methods} needed: "
                               + (", ".join(describe_method(m) for m in methods) or "none")))
    winner = next((k for k in tried if k.id == winner_id), None)
    if winner is None:
        checks.append(Check(name="winner on protocol", passed=False,
                            detail=f"{winner_id} is not an attempt at this metric in this campaign"))
        return "insufficient_evidence", checks
    status, (fits, fit_detail) = L.claim_status(winner.id), on_protocol(L, winner)
    checks.append(Check(name="winner on protocol", passed=fits and status == "supported",
                        detail=f"{fit_detail}; the checker says {status}"))
    if metric.needs_verification:
        verification = L.get("claim", verification_id) if verification_id else None
        if verification is not None and verification.campaign_id != campaign_id:
            verification = None
        checks.append(_verified(L, winner, metric, verification))
    reps = replications(L, campaign_id, winner.id)
    failed = [r for r in reps if r.status != "ok"]
    checks.append(Check(name="every replication succeeded", passed=not failed,
                        detail=(f"{len(failed)} of {len(reps)} replication run(s) failed ({failed[0].tool})" if failed
                                else f"{len(reps)} replication run(s), all succeeded" if reps
                                else "no replication runs")))
    mine = method(L, winner, metric)
    rivals = [k for k in valid if method(L, k, metric) != mine and L.claim_status(k.id) in ("supported", "refuted")]
    against = [k for k in rivals if not _agree(L, winner, k, metric)]
    checks.append(Check(name="methods agree", passed=not against,
                        detail=(f"{describe_method(method(L, against[0], metric))} disagrees ({against[0].id}): "
                                "methods disagree, no winner") if against
                        else f"{len(rivals)} other method(s) checked" if rivals else "no other method was checked"))
    if not all(c.passed for c in checks):
        return "insufficient_evidence", checks
    tied = [k for k in rivals if L.claim_status(k.id) == "supported" and metric.needs_verification and any(
        _verified(L, k, metric, v).passed for v in L.list("claim", campaign_id=campaign_id) if v.id != k.id)]
    if tied:
        return "tie", checks
    for d in L.list("decision", campaign_id=campaign_id):
        other = metric_of(L, campaign_id, d.metric_id)
        if d.metric_id != metric_id and d.outcome in ("winner", "tie") and other and d.winner_claim_id:
            best = method(L, L.get("claim", d.winner_claim_id), other)
            if best != mine:
                return "tradeoff", checks
    return "winner", checks


def board(L, campaign_id: str) -> dict:
    """Every metric with its attempts (method, value, verdict, on protocol or not) and decisions."""
    p = protocol_of(L, campaign_id)
    if p is None:
        return {"protocol": None, "metrics": []}
    decisions = L.list("decision", campaign_id=campaign_id)
    out = []
    for metric in p.metrics:
        rows = []
        for k in attempts(L, campaign_id, metric.id):
            m = measurement(L, k, metric)
            fits, why = on_protocol(L, k)
            rows.append({"claim_id": k.id, "statement": k.statement, "status": L.claim_status(k.id),
                         "method": describe_method(method(L, k, metric)), "measurement_id": m.id if m else None,
                         "value": m.value if m else None, "unit": m.unit if m else None,
                         "uncertainty": _half_width(m) if m else None, "on_protocol": fits, "protocol_note": why,
                         "replications": len(replications(L, campaign_id, k.id))})
        out.append({"metric": metric.model_dump(mode="json"), "attempts": rows,
                    "decisions": [d.model_dump(mode="json") for d in decisions if d.metric_id == metric.id]})
    return {"protocol": p.model_dump(mode="json"), "pre_registered": pre_registered(L, campaign_id) is None,
            "metrics": out}


def prompt(L, campaign_id: str) -> str:
    """The system-prompt lines naming the pre-registered metrics."""
    p = protocol_of(L, campaign_id)
    if p is None:
        return ""
    lines = []
    for m in p.metrics:
        lines.append(f"- {m.id}: {m.name}. Test {m.predicate.type} {m.predicate.params} on "
                     f"{' or '.join(m.quantities)} in {m.unit}; at least {m.min_methods} methods"
                     + ("; a winner needs an independent verification" if m.needs_verification else ""))
    return ("\nPre-registered metrics (fixed by " + p.author + " before the start; give propose_finding the metric_id "
            "and exactly this test, or the finding is off protocol and can never win):\n" + "\n".join(lines)
            + "\nAttempt each metric with at least the stated number of different methods. Before declare_winner, "
              "re-derive the winner's value through separate runs (replicate it, or measure it another way) and "
              "propose a values_agree finding between the two.")


def record(L, campaign_id: str, metric_id: str, winner_id: str | None, verification_id: str | None, now: float
           ) -> Decision:
    import uuid
    outcome, checks = decide(L, campaign_id, metric_id, winner_id, verification_id)
    winner = L.get("claim", winner_id) if winner_id else None
    return L.add(Decision(id=f"decision_{uuid.uuid4().hex[:10]}", campaign_id=campaign_id, metric_id=metric_id,
                          outcome=outcome, winner_claim_id=winner.id if winner else None,
                          verification_claim_id=verification_id if verification_id and L.get("claim", verification_id)
                          else None,
                          attempt_claim_ids=[k.id for k in attempts(L, campaign_id, metric_id)],
                          replication_run_ids=[r.id for r in replications(L, campaign_id, winner.id)] if winner else [],
                          checks=checks, created_at=now))
