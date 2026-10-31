"""loop-compare/1 and /2: the deterministic comparator of a loop campaign and the conclusion's computed outcome
(design §2.7).

Each objective names its evaluator, and a comparison is recomputed with the evaluator its objective names, so the
rules of a released version never change; a change ships as a new version beside it. loop-compare/2 is /1 plus one
rule (5 below), for the case the product exists for: a failing baseline.

1. Same basis only. A result is compared with the baseline on its own basis: a physical (bench) result with the
   objective's baseline dataset records, a computational result with the registration re-runs of those records on
   the solver (`baseline_simulation`). Mixed bases, a missing baseline or a missing quantity are `not_comparable`.
   Several baseline records give their mean, with h the larger of their spread (half the range) and the largest of
   their half-widths.
2. Constraints, on the candidate. For "<=": ok if v + h <= limit, violated if v - h > limit, otherwise unknown;
   ">=" mirrored. h is a half-width: gate._half_width's rule (2 sigma for a std, the value for other kinds, 0 when
   none is stated), re-implemented here so a gate change cannot move loop verdicts.
3. Target. d = baseline - candidate (minimize) or candidate - baseline (maximize), H = hypot(h_b, h_c). Better if
   d - H > margin and the absolute limit holds; not better if d + H <= margin or the limit fails; else inconclusive.
   A negative margin allows that much slowdown. The limit holds for minimize if c + h <= limit (mirrored).
4. Verdict: not_comparable, else violates_constraints if any constraint is violated, else inconclusive if any part is
   unknown, else candidate_better or not_better.
5. (/2 only) Feasibility first. When the baseline violates a constraint and the candidate meets every constraint, the
   candidate is better whatever the target's difference says: a protocol that breaks a limit is not an option to keep.
   The target's own absolute limit, when it has one, must still hold: missed, the target decides; straddled, the
   verdict is inconclusive. The target
   difference is still recorded (a slower candidate shows its cost), with the baseline's own constraint checks under
   baseline["constraints"] and target["decided_by"] = "feasibility".

Values come from the result's Measurements (quantities with source "result") or from ops/1 applied to its series
(source "operator"), so verify recomputes them from the stored blobs. A threshold crossing that does not happen in
its window is a legitimate answer, not an error: {"value": None, "not_reached_within": [t0, t1]} (record seconds),
read as a lower bound (the crossing, if any, is after t1).

The outcome: `select_best` prefers measured (physical) evidence, then computational; then the best target value; then
the earliest. A measured candidate_better is `improved`; a computational one only `improved_in_simulation`; decisive
comparisons none better give `not_improved`; otherwise `unresolved`.
"""
from __future__ import annotations

import json
import math
import re

from translucent import chain, operators, units
from translucent.contracts import Uncertainty
from translucent.ledger import AdmissionError, Ledger
from translucent.loop import (PENDING, REGISTRATION, RequestState, _baseline_record, apply_operator,
                              baseline_request_id, current_objective, request_states)
from translucent.loop_contracts import (ComparisonData, ConclusionData, Constraint, LoopRecord, ObjectiveData,
                                        OperatorSpec)

EVALUATORS = ("loop-compare/1", "loop-compare/2")
EVALUATOR = "loop-compare/2"      # what a new objective names
ACTOR = "system:comparator"
BASIS_RANK = {"physical": 0, "retrospective": 1, "computational": 2}
_COMPARISON_ID = re.compile(r"cmp_[0-9a-f]{24}")


def half_width(u: Uncertainty | dict | None) -> float:
    """An uncertainty as a half-width (std -> 2 sigma); 0 when none is stated. gate._half_width's rule."""
    if u is None:
        return 0.0
    value, kind = (u.value, u.kind) if isinstance(u, Uncertainty) else (u.get("value"), u.get("kind"))
    if value is None or kind == "none":
        return 0.0
    return 2 * float(value) if kind == "std" else float(value)


# ------------------------------------------------------------------ series and ops/1 values

def series(L: Ledger, obj: ObjectiveData, ref: str, quantity: str,
           states: dict[str, RequestState] | None = None, cid: str | None = None) -> tuple[str, dict] | None:
    """(artifact id, {"t", "y", "unit", "uncertainty"}) of a request/result ref or a "dataset/record" ref, values in
    the system's canonical unit. One function for the service, the comparator and verify, so all three read the same
    numbers."""
    if "/" in ref:
        record = _baseline_record(L, obj, ref) if ref in obj.baseline else None
        if record is None:
            did, _, rid = ref.partition("/")
            ds = L.get("dataset", did) if did in obj.datasets else None
            record = next((r for r in ds.records if r.id == rid), None) if ds else None
        ch = next((c for c in record.channels if c.quantity == quantity), None) if record else None
        if ch is None:
            return None
        body = json.loads(L.blob_path(ch.series_id).read_text(encoding="utf-8"))
        canonical = next((q["unit"] for q in obj.system["spec"]["quantities"] if q["name"] == quantity), ch.unit)
        unc = ch.uncertainty.model_dump(mode="json")
        if unc.get("value") is not None:      # a spread scales with the unit; an offset (degC to K) does not
            unc["value"] = abs(unc["value"] * float(units.factor(ch.unit, canonical)[0]))
        return ch.series_id, {"t": body["t"], "y": units.convert(body["y"], ch.unit, canonical),
                              "unit": canonical, "uncertainty": unc}
    if states is None:
        states = request_states(L, cid)
    st = next((s for s in states.values() if s.result is not None and ref in (s.request_id, s.result.id)), None)
    aid = st.result.data["series"].get(quantity) if st else None
    if aid is None:
        return None
    return aid, json.loads(L.blob_path(aid).read_text(encoding="utf-8"))


def not_reached(spec: OperatorSpec, t: list[float], y: list[float], y_unit: str) -> list[float] | None:
    """[t0, t1] (record seconds) when a time_to_threshold has a valid window in which y never crosses the threshold;
    None otherwise (it crosses, or the series or spec is refused for another reason)."""
    if spec.op != "time_to_threshold" or spec.threshold is None or spec.direction is None:
        return None
    try:
        operators.check(spec, y_unit)
    except ValueError:
        return None
    if len(t) != len(y) or not t or not all(math.isfinite(v) for v in (*t, *y)) or \
            any(b < a for a, b in zip(t, t[1:])):
        return None
    lo, hi = spec.window if spec.window is not None else (t[0], t[-1])
    kept = [(a, b) for a, b in zip(t, y) if lo <= a <= hi]
    if len(kept) < 2 or kept[-1][0] == kept[0][0]:
        return None
    past = (lambda v: v >= spec.threshold) if spec.direction == "rising" else (lambda v: v <= spec.threshold)
    return None if any(past(b) for _, b in kept) else [kept[0][0], kept[-1][0]]


def measure(spec: OperatorSpec, body: dict) -> dict:
    """ops/1 over one series body: {"value", "unit", "uncertainty"}, or {"value": None, "unit",
    "not_reached_within": [t0, t1]} for a crossing the window never reaches. Other refusals raise ValueError."""
    h = body.get("uncertainty") or {"value": None, "kind": "none", "method": "not stated"}
    try:
        value, unc = apply_operator(spec, body["t"], body["y"], body["unit"], Uncertainty.model_validate(h))
    except ValueError:
        window = not_reached(spec, body["t"], body["y"], body["unit"])
        if window is None:
            raise
        return {"value": None, "unit": spec.unit, "not_reached_within": window}
    return {"value": value, "unit": spec.unit, "uncertainty": unc.model_dump(mode="json")}


# ------------------------------------------------------------------ the values compared

def _converted(value: float, h: float, src: str, dst: str) -> dict:
    mul = float(units.factor(src, dst)[0])
    return {"value": units.convert(value, src, dst), "unit": dst, "h": abs(h * mul)}


def _operator_value(spec: OperatorSpec, found: tuple[str, dict] | None, what: str) -> dict:
    if found is None:
        return {"missing": f"{what} has no {spec.channel} series"}
    try:
        m = measure(spec, found[1])
    except (ValueError, NotImplementedError) as e:
        return {"missing": f"{what}: {e}"}
    if m["value"] is None:
        return {"value": None, "unit": m["unit"], "h": 0.0, "not_reached_within": m["not_reached_within"]}
    return {"value": m["value"], "unit": m["unit"], "h": half_width(m["uncertainty"])}


def compared_quantities(obj: ObjectiveData) -> list[str]:
    return list(dict.fromkeys([obj.target.quantity] + [c.quantity for c in obj.constraints]))


def result_values(L: Ledger, obj: ObjectiveData, result: LoopRecord, states: dict[str, RequestState]) -> dict:
    """quantity -> value (in the objective's unit) of one result, or {"missing": reason}."""
    defs, out = {q.name: q for q in obj.quantities}, {}
    for name in compared_quantities(obj):
        q, what = defs.get(name), f"result {result.id}"
        if q is None:
            out[name] = {"missing": f"{name} is not a quantity of the objective"}
        elif q.source == "operator":
            out[name] = _operator_value(q.operator, series(L, obj, result.id, q.operator.channel, states), what)
        else:
            mid = result.data["measurements"].get(name)
            m = L.get("measurement", mid) if mid else None
            if m is None or not isinstance(m.value, float):
                out[name] = {"missing": f"{what} has no {name} measurement"}
                continue
            try:
                out[name] = _converted(m.value, half_width(m.uncertainty), m.unit, q.unit)
            except ValueError as e:
                out[name] = {"missing": f"{what}: {name}: {e}"}
    return out


def record_values(L: Ledger, obj: ObjectiveData, ref: str) -> dict:
    """quantity -> value of one baseline dataset record: its scalars, or ops/1 over its channels."""
    defs, record, out = {q.name: q for q in obj.quantities}, _baseline_record(L, obj, ref), {}
    for name in compared_quantities(obj):
        q = defs.get(name)
        if q is None:
            out[name] = {"missing": f"{name} is not a quantity of the objective"}
        elif q.source == "operator":
            out[name] = _operator_value(q.operator, series(L, obj, ref, q.operator.channel), f"baseline {ref}")
        else:
            s = next((s for s in (record.scalars if record else []) if s.quantity == name), None)
            if s is None or isinstance(s.value, str):
                out[name] = {"missing": f"baseline {ref} has no numeric {name}"}
            else:
                try:
                    out[name] = _converted(float(s.value), half_width(s.uncertainty), s.unit, q.unit)
                except ValueError as e:
                    out[name] = {"missing": f"baseline {ref}: {name}: {e}"}
    return out


def aggregate(values: list[dict]) -> dict:
    """Several baseline records' values of one quantity: the mean, h = max(half the range, largest half-width)."""
    if missing := [v["missing"] for v in values if "missing" in v]:
        return {"missing": missing[0]}
    reached = [v for v in values if v["value"] is not None]
    if not reached:     # none crosses: the weakest bound holds for all
        end = min(v["not_reached_within"][1] for v in values)
        start = min(v["not_reached_within"][0] for v in values)
        return {"value": None, "unit": values[0]["unit"], "h": 0.0, "not_reached_within": [start, end]}
    if len(reached) != len(values):
        return {"missing": "the baseline records disagree: some reach the threshold within their window, some do not"}
    if len(values) == 1:
        return dict(values[0])
    xs = [v["value"] for v in values]
    return {"value": math.fsum(xs) / len(xs), "unit": values[0]["unit"],
            "h": max((max(xs) - min(xs)) / 2, max(v["h"] for v in values))}


def baseline_side(L: Ledger, cid: str, obj: ObjectiveData, states: dict[str, RequestState],
                  basis: str) -> tuple[dict | None, str | None]:
    """({"basis", "refs", "results", "values"}, None) for the baseline on `basis`, or (None, why there is none)."""
    if basis == "physical":
        if not obj.baseline:
            return None, "no measured baseline: the objective names no baseline records"
        per = [record_values(L, obj, ref) for ref in obj.baseline]
        return {"basis": "physical", "refs": list(obj.baseline), "results": [],
                "values": {q: aggregate([p[q] for p in per]) for q in compared_quantities(obj)}}, None
    if basis != "computational":
        return None, f"no {basis} baseline"
    runs = [(ref, states.get(baseline_request_id(cid, ref))) for ref in obj.baseline]
    runs = [(ref, st) for ref, st in runs if st is not None and st.proposed_by == REGISTRATION]
    if not runs:
        return None, ("simulated candidate vs measured baseline" if obj.baseline else
                      "no computational baseline: the objective names no baseline to re-run on the solver")
    if any(st.state in PENDING for _, st in runs):
        return None, "the computational baseline has not finished on the solver yet"
    done = [(ref, st) for ref, st in runs if st.result is not None]
    if not done:
        return None, "the computational baseline failed on the solver"
    per = [result_values(L, obj, st.result, states) for _, st in done]
    return {"basis": "computational", "refs": [ref for ref, _ in done], "results": [st.result.id for _, st in done],
            "values": {q: aggregate([p[q] for p in per]) for q in compared_quantities(obj)}}, None


# ------------------------------------------------------------------ the comparator (pure)

def _interval(v: dict, unit: str) -> tuple[float, float]:
    """(lo, hi) of a value in `unit`: value -/+ h, or (end of its window, inf) for a crossing not reached."""
    if v["value"] is None:
        return units.convert(v["not_reached_within"][1], "s", unit), math.inf
    c = _converted(v["value"], v["h"], v["unit"], unit)
    return c["value"] - c["h"], c["value"] + c["h"]


def _fmt(v: dict, unit: str) -> str:
    if v["value"] is None:
        return f"not reached by {units.convert(v['not_reached_within'][1], 's', unit):g} {unit}"
    c = _converted(v["value"], v["h"], v["unit"], unit)
    return f"{c['value']:g} ± {c['h']:g} {unit}" if c["h"] else f"{c['value']:g} {unit}"


def check_constraint(c: Constraint, v: dict) -> dict:
    """One value against one constraint: {"quantity", "op", "limit", "unit", "value", "h", "ok"}, value and h in
    the constraint's unit; ok None when the uncertainty straddles the limit or the value is missing."""
    check = {"quantity": c.quantity, "op": c.op, "limit": c.limit, "unit": c.unit, "value": None, "h": None,
             "ok": None}
    if "missing" in v:
        return check
    lo, hi = _interval(v, c.unit)
    if v["value"] is not None:
        conv = _converted(v["value"], v["h"], v["unit"], c.unit)
        check |= {"value": conv["value"], "h": conv["h"]}
    else:
        check["not_reached_within"] = v["not_reached_within"]
    if c.op == "<=":
        check["ok"] = True if hi <= c.limit else False if lo > c.limit else None
    else:
        check["ok"] = True if lo >= c.limit else False if hi < c.limit else None
    return check


def constraint_reason(c: Constraint, v: dict) -> str | None:
    """A plain sentence when a value violates or straddles a constraint; None when it holds."""
    ok = check_constraint(c, v)["ok"]
    if ok is False:
        return f"{c.label}: {c.quantity} is {_fmt(v, c.unit)}, outside {c.op} {c.limit:g} {c.unit}"
    if ok is None:
        return f"{c.label}: {c.quantity} {_fmt(v, c.unit)} straddles the {c.limit:g} {c.unit} limit"
    return None


def compare(obj: ObjectiveData, basis: str, baseline: dict | None, candidate: dict, *,
            baseline_reason: str | None = None) -> dict:
    """The objective's evaluator (loop-compare/1 or /2) for one candidate: the ComparisonData payload as a dict.
    `baseline` and `candidate` carry
    "values": quantity -> {"value", "unit", "h"} | {"value": None, "not_reached_within", ...} | {"missing": why}."""
    reasons, comparable = [], baseline is not None
    if baseline is None:
        reasons.append(baseline_reason or "no baseline on the same basis")
    cv, bv = candidate["values"], (baseline or {}).get("values", {})

    def usable(v: dict | None, quantity: str, unit: str) -> dict:
        """The value, or {"missing": why} when it is absent or cannot be read in `unit`."""
        if v is None:
            return {"missing": f"{quantity} was not measured"}
        if "missing" not in v:
            try:
                _interval(v, unit)
            except ValueError as e:
                return {"missing": str(e)}
        return v

    checks = []
    for c in obj.constraints:
        v = usable(cv.get(c.quantity), c.quantity, c.unit)
        if "missing" in v:
            comparable = False
            reasons.append(f"the candidate lacks {c.quantity} ({v['missing']})")
        elif why := constraint_reason(c, v):
            reasons.append(why)
        checks.append(check_constraint(c, v))

    t = obj.target
    target = {"quantity": t.quantity, "unit": t.unit, "direction": t.direction, "baseline": None, "candidate": None,
              "delta": None, "h": None, "margin": t.margin, "limit": t.limit, "limit_ok": None, "better": None}
    c_v = usable(cv.get(t.quantity), t.quantity, t.unit)
    b_v = usable(bv.get(t.quantity), t.quantity, t.unit)
    if "missing" in c_v:
        comparable = False
        reasons.append(f"the candidate lacks {t.quantity} ({c_v['missing']})")
    if baseline is not None and "missing" in b_v:
        comparable = False
        reasons.append(f"the baseline lacks {t.quantity} ({b_v['missing']})")
    if "missing" not in c_v:
        c_lo, c_hi = _interval(c_v, t.unit)
        if c_v["value"] is not None:
            target["candidate"] = _converted(c_v["value"], c_v["h"], c_v["unit"], t.unit)["value"]
        if t.limit is not None:
            if t.direction == "minimize":
                target["limit_ok"] = True if c_hi <= t.limit else False if c_lo > t.limit else None
            else:
                target["limit_ok"] = True if c_lo >= t.limit else False if c_hi < t.limit else None
    if comparable:
        if b_v["value"] is not None:
            target["baseline"] = _converted(b_v["value"], b_v["h"], b_v["unit"], t.unit)["value"]
        if c_v["value"] is not None and b_v["value"] is not None:
            b = _converted(b_v["value"], b_v["h"], b_v["unit"], t.unit)
            c = _converted(c_v["value"], c_v["h"], c_v["unit"], t.unit)
            d = b["value"] - c["value"] if t.direction == "minimize" else c["value"] - b["value"]
            H = math.hypot(b["h"], c["h"])
            d_lo, d_hi = d - H, d + H
            target |= {"delta": d, "h": H}
        else:                              # a bound on one side: interval arithmetic, never better by assumption
            b_lo, b_hi = _interval(b_v, t.unit)
            d_lo, d_hi = (b_lo - c_hi, b_hi - c_lo) if t.direction == "minimize" else (c_lo - b_hi, c_hi - b_lo)
        lim = target["limit_ok"]
        if d_hi <= t.margin or lim is False:
            target["better"] = False
        elif d_lo > t.margin and (t.limit is None or lim is True):
            target["better"] = True
        what = f"{t.quantity} {_fmt(c_v, t.unit)} against the baseline's {_fmt(b_v, t.unit)}"
        if lim is False:
            reasons.append(f"{what}: it misses the absolute limit of {t.limit:g} {t.unit}")
        elif lim is None and t.limit is not None:
            reasons.append(f"{what}: it straddles the absolute limit of {t.limit:g} {t.unit}")
        if target["better"] is False and lim is not False:
            reasons.append(f"{what}: not better by more than the {t.margin:g} {t.unit} margin")
        elif target["better"] is None and not (d_lo > t.margin):
            reasons.append(f"{what}: the difference is within its uncertainty of the {t.margin:g} {t.unit} margin"
                           if math.isfinite(d_lo) and math.isfinite(d_hi) else
                           f"{what}: a crossing outside its window leaves the difference open")

    feasibility_win = feasibility_open = False
    if obj.evaluator != "loop-compare/1" and baseline is not None:      # rule 5
        b_checks = [check_constraint(c, usable(bv.get(c.quantity), c.quantity, c.unit)) for c in obj.constraints]
        baseline = {**baseline, "constraints": b_checks}
        feasible = comparable and any(ch["ok"] is False for ch in b_checks) and             all(ch["ok"] is True for ch in checks)
        feasibility_win = feasible and (t.limit is None or target["limit_ok"] is True)
        feasibility_open = feasible and t.limit is not None and target["limit_ok"] is None
        if feasibility_win:
            target["decided_by"] = "feasibility"

    if not comparable:
        verdict = "not_comparable"
    elif any(ch["ok"] is False for ch in checks):
        verdict = "violates_constraints"
    elif feasibility_win:
        verdict = "candidate_better"
    elif feasibility_open or any(ch["ok"] is None for ch in checks) or target["better"] is None:
        verdict = "inconclusive"
    else:
        verdict = "candidate_better" if target["better"] else "not_better"
    if verdict == "candidate_better":
        reasons = []
    return {"evaluator": obj.evaluator, "objective_version": obj.version, "basis": basis,
            "baseline": baseline or {"basis": None, "refs": [], "results": [], "values": {}},
            "candidate": candidate, "target": target, "constraints": checks, "verdict": verdict,
            "reasons": list(dict.fromkeys(reasons))}


# ------------------------------------------------------------------ comparisons of a campaign

def candidates(states: dict[str, RequestState]) -> list[RequestState]:
    """Completed requests compared with the baseline, in result order; the registration re-runs are the baseline."""
    done = [s for s in states.values() if s.result is not None and s.proposed_by != REGISTRATION]
    return sorted(done, key=lambda s: s.result.seq)


def comparison(L: Ledger, cid: str, obj: ObjectiveData, states: dict[str, RequestState], st: RequestState) -> dict:
    """The canonical comparison payload for one completed request, from the ledger as it stands."""
    basis = st.result.data["basis"]
    baseline, why = baseline_side(L, cid, obj, states, basis)
    cand = {"request_id": st.request_id, "result_id": st.result.id,
            "values": result_values(L, obj, st.result, states)}
    return chain.payload("comparison", compare(obj, basis, baseline, cand, baseline_reason=why))


def _latest(records: list[LoopRecord], version: int) -> tuple[dict[str, LoopRecord], dict[str, int]]:
    latest, counts = {}, {}
    for r in records:
        if r.type == "comparison":
            rid = r.data["candidate"]["result_id"]
            counts[rid] = counts.get(rid, 0) + 1
            if r.data["objective_version"] == version:
                latest[rid] = r
    return latest, counts


def record_comparisons(L: Ledger, cid: str) -> list[LoopRecord]:
    """Record a comparison for every completed candidate whose latest comparison on the current objective version is
    missing or out of date (a new result, an amendment, a baseline re-run finishing). Idempotent; one transaction."""
    out = []
    with L.transaction():
        if chain.concluded(L, cid):
            return out
        recs = chain.records(L, cid)
        obj, states = current_objective(L, cid, recs), request_states(L, cid, recs)
        latest, counts = _latest(recs, obj.version)
        for st in candidates(states):
            rid = st.result.id
            fresh = comparison(L, cid, obj, states, st)
            if rid in latest and latest[rid].data == fresh:
                continue
            out.append(chain.append(L, cid, "comparison", fresh, ACTOR,
                                    chain.natural_id("cmp", rid, counts.get(rid, 0))))
    return out


def _rank(rec: LoopRecord):
    t = rec.data["target"]
    value = t.get("candidate")
    key = math.inf if value is None else (value if t.get("direction", "minimize") == "minimize" else -value)
    return BASIS_RANK.get(rec.data["basis"], 9), key, rec.seq


def select_best(comparisons: list[LoopRecord]) -> tuple[str, LoopRecord | None]:
    """(outcome, the comparison it rests on) from each candidate's latest comparison on the current version."""
    better = [c for c in comparisons if c.data["verdict"] == "candidate_better"]
    measured = [c for c in better if c.data["basis"] != "computational"]
    if measured:
        return "improved", min(measured, key=_rank)
    if better:
        return "improved_in_simulation", min(better, key=_rank)
    decisive = [c for c in comparisons if c.data["verdict"] in ("not_better", "violates_constraints")]
    if decisive:
        return "not_improved", min(decisive, key=_rank)
    return "unresolved", None


def outcome(L: Ledger, cid: str, records: list[LoopRecord] | None = None) -> tuple[str, str | None, list[str]]:
    """(outcome, comparison id, problems): problems name completed candidates whose latest comparison on the current
    objective version is missing or no longer what its evaluator computes."""
    recs = chain.records(L, cid) if records is None else records
    obj, states = current_objective(L, cid, recs), request_states(L, cid, recs)
    latest, _ = _latest(recs, obj.version)
    problems, comps = [], []
    for st in candidates(states):
        rec = latest.get(st.result.id)
        if rec is None or rec.data != comparison(L, cid, obj, states, st):
            problems.append(f"result {st.result.id} has no up-to-date comparison on objective version {obj.version}")
        if rec is not None:
            comps.append(rec)
    best_outcome, best = select_best(comps)
    return best_outcome, best.id if best else None, problems


# ------------------------------------------------------------------ admission (runs in every writing process)

@chain.admission("comparison")
def _admit_comparison_recomputed(L: Ledger, rec: LoopRecord, c: ComparisonData):
    if rec.actor != ACTOR:
        raise AdmissionError(f"comparisons are recorded by {ACTOR}")
    recs = chain.records(L, rec.campaign_id)
    obj, states = current_objective(L, rec.campaign_id, recs), request_states(L, rec.campaign_id, recs)
    if c.evaluator != obj.evaluator:
        raise AdmissionError(f"the objective names {obj.evaluator}; comparison {rec.id} names {c.evaluator}")
    st = next((s for s in candidates(states) if s.result.id == c.candidate.get("result_id")), None)
    if st is None:
        raise AdmissionError(f"comparison {rec.id} names no completed candidate result")
    fresh = comparison(L, rec.campaign_id, obj, states, st)
    if fresh != rec.data:
        raise AdmissionError(f"comparison {rec.id} is not what {obj.evaluator} computes: recorded {c.verdict}, "
                             f"recomputed {fresh['verdict']}")


@chain.admission("conclusion")
def _admit_conclusion_evidence(L: Ledger, rec: LoopRecord, c: ConclusionData):
    best_outcome, best, problems = outcome(L, rec.campaign_id)
    if problems:
        raise AdmissionError("; ".join(problems) + "; record the comparisons first")
    if named := sorted(set(_COMPARISON_ID.findall(c.text)) - {best}):
        raise AdmissionError(f"the text names {', '.join(named)} as the result; the computed result is "
                             f"{best or 'no comparison'} ({best_outcome})")
