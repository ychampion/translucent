"""loop-compare/1, /2 and /3: the deterministic comparator of a loop campaign and the conclusion's computed outcome
(design §2.7).

Each objective names its evaluator, and a comparison is recomputed with the evaluator its objective names, so the
rules of a released version never change; a change ships as a new version beside it. loop-compare/2 is /1 plus one
rule (5 below), for the case the product exists for: a failing baseline. loop-compare/3 (rules 6-10, after /1 and /2)
is what a new objective names: it answers feasibility, speed and the objective separately, because /2's rule 5 let a
feasible candidate count as "better" than a failing baseline even when it was slower.

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

loop-compare/3. Rules 1 (same basis), 2 (a constraint within its uncertainty) and the arithmetic of 3 stand; rule 5
does not exist here. Each comparison answers three questions, each recorded with its own answer:
6. Feasibility. The baseline (its same-basis values, when there are any) and the candidate each "meets" every
   constraint of the objective version, "fails" one, or is "unknown" (a straddled limit, a missing value, no
   baseline). A candidate missing a target or constraint quantity is not_comparable. A minimised target never
   reached within its window (a threshold crossing after it) "fails": the run never got there (`never_reached`; the
   reference search reads such a run as too slow).
7. Speed, only against a reference that meets the same constraints, on the candidate's basis: the baseline when it
   meets them; otherwise the system reference search's answer for the version whose constraints are in force
   (translucent.loop_reference: the fastest single-step protocol meeting them, found by bisection on the solver,
   computational only); otherwise none. A failing baseline is never the reference. The speed is compared only for a
   candidate that meets the constraints: d = reference - candidate (minimize; mirrored), H = hypot(h_r, h_c),
   m = max(margin, 0) (faster means faster; a /3 objective refuses a negative margin at registration and amendment);
   "faster" if d - H > m, "not_faster" otherwise (faster false if d + H <= m, null within the
   uncertainty); "not_compared", with the reason, when there is no reference, it is still running, the baseline it
   would be has not finished, or the candidate does not meet the constraints. The reference names the objective
   version whose constraints it was judged under; an amendment that changes the constraints starts a new search, and
   no speed is compared across constraint versions.
8. Objective. kind hard_target (the target's limit is the hard target, never relaxed except by a person's
   amendment): "met" when the candidate meets every constraint and the limit holds within its uncertainty, "not_met"
   when it fails a constraint or misses the limit, "unknown" otherwise. kind fastest_feasible: "open", always: there
   is no proven optimum.
9. Verdict, for the lists that show one: not_comparable (rule 6), violates_constraints (fails), inconclusive (a
   constraint undecided; or meets them with the speed undecided or not compared), candidate_better (meets them and
   faster than the reference), not_better (meets them, not faster). A feasibility recovery alone is never better.
10. Outcome, from each candidate's latest comparison on the current version, the first group that is not empty:
   objective_met (rule 8 met), faster_than_reference (rule 7 faster), feasible_only (meets every constraint),
   no_feasible_result (none meets them, one fails them); else unresolved. Within the group, measured evidence first,
   then the best target value, then the earliest; computational evidence adds "_in_simulation". The conclusion
   records `findings` {feasibility, speed, objective} for that comparison, recomputed at admission and by verify.

`findings` also describes /1 and /2 campaigns (for the view's assessment; their conclusions record none): the best
candidate that meets the constraints, feasibility "recovered" when the baseline fails them, and speed compared only
when the baseline itself meets the constraints (a /1 or /2 campaign ran no reference search).
"""
from __future__ import annotations

import json
import math
import re

from translucent import chain, loop_reference, operators, units
from translucent.contracts import Uncertainty
from translucent.ledger import AdmissionError, Ledger
from translucent.loop import (PENDING, REGISTRATION, SYSTEM_RUNS, RequestState, _baseline_record, apply_operator,
                              baseline_request_id, current_objective, request_states, withdraw_superseded)
from translucent.loop_contracts import (ComparisonData, ConclusionData, Constraint, LoopRecord, ObjectiveData,
                                        OperatorSpec)

EVALUATORS = ("loop-compare/1", "loop-compare/2", "loop-compare/3")
EVALUATOR = "loop-compare/3"      # what a new objective names
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
            if did not in obj.datasets and cid is not None and getattr(obj, "uploads", None):
                from translucent import records       # a dataset this investigation imported from its uploads
                ok = did in records.imported(L, cid)
            else:
                ok = did in obj.datasets
            ds = L.get("dataset", did) if ok else None
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


def _basis_problem(L: Ledger, obj: ObjectiveData, result: LoopRecord, states: dict[str, RequestState]) -> str | None:
    from translucent import systems
    sid = obj.system["id"]
    try:
        adapter = systems.get(sid)
    except KeyError:
        return None              # a system no longer registered here: its results carry no forecasts to withhold
    hook = getattr(adapter, "forecast_basis_problem", None)
    if hook is None:
        return None
    st = (states or {}).get(result.data.get("request_id"))
    if st is None:               # never compare a result whose request cannot be checked: fail closed
        raise ValueError(f"result {result.id} has no request state to check its basis against")
    return hook(L, result.campaign_id, states, st)


def result_values(L: Ledger, obj: ObjectiveData, result: LoopRecord, states: dict[str, RequestState]) -> dict:
    """quantity -> value (in the objective's unit) of one result, or {"missing": reason}. A result the system says
    has no basis for comparison (its adapter's forecast_basis_problem, e.g. a design under a model that was never
    shown adequate on data it did not fit) has every value missing with that reason."""
    defs, out = {q.name: q for q in obj.quantities}, {}
    basis_problem = _basis_problem(L, obj, result, states)
    for name in compared_quantities(obj):
        q, what = defs.get(name), f"result {result.id}"
        if basis_problem:
            out[name] = {"missing": f"{what} is {basis_problem}"}
        elif q is None:
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


# ------------------------------------------------------------------ loop-compare/3 (pure)

def _usable(v: dict | None, quantity: str, unit: str) -> dict:
    """The value, or {"missing": why} when it is absent or cannot be read in `unit`."""
    if v is None:
        return {"missing": f"{quantity} was not measured"}
    if "missing" not in v:
        try:
            _interval(v, unit)
        except ValueError as e:
            return {"missing": str(e)}
    return v


def feasibility_of(checks: list[dict]) -> str:
    """"fails" if a constraint check is violated, "meets" if every one holds, else "unknown"."""
    oks = [c["ok"] for c in checks]
    return "fails" if False in oks else "meets" if all(ok is True for ok in oks) else "unknown"


def never_reached(direction: str, v: dict | None) -> bool:
    """A minimised target whose threshold crossing does not happen within its window: the run never got there (it is
    slower than its window). Under loop-compare/3 such a run is not feasible, in a comparison and in the reference
    search (which reads it as too slow). A maximised target not reached is a lower bound above the window, not a
    failure."""
    return direction == "minimize" and isinstance(v, dict) and "missing" not in v and v.get("value") is None \
        and "not_reached_within" in v


def side_feasibility(obj: ObjectiveData, values: dict) -> str:
    """loop-compare/3 feasibility of one result's (or the baseline's) values under the objective's constraints:
    "fails" when a constraint is violated or the minimised target is never reached, "meets" when every constraint
    holds, else "unknown"."""
    checks = [check_constraint(c, _usable(values.get(c.quantity), c.quantity, c.unit)) for c in obj.constraints]
    t = obj.target
    return "fails" if never_reached(t.direction, _usable(values.get(t.quantity), t.quantity, t.unit)) else \
        feasibility_of(checks)


def objective_kind(obj: ObjectiveData) -> str:
    return loop_reference.effective_kind(obj)


def compare3(obj: ObjectiveData, basis: str, baseline: dict | None, candidate: dict, reference: dict | None, *,
             reference_reason: str = "") -> dict:
    """loop-compare/3 (rules 6-9) for one candidate: the ComparisonData payload as a dict. `reference` is
    {"label", "source", "objective_version", "request_id", "values"} (a reference that meets the constraints), or
    None with `reference_reason` saying why there is none."""
    t, reasons, comparable = obj.target, [], True
    cv, bv = candidate["values"], (baseline or {}).get("values", {})
    checks = []
    for c in obj.constraints:
        v = _usable(cv.get(c.quantity), c.quantity, c.unit)
        if "missing" in v:
            comparable = False
            reasons.append(f"the candidate lacks {c.quantity} ({v['missing']})")
        elif why := constraint_reason(c, v):
            reasons.append(why)
        checks.append(check_constraint(c, v))
    c_v = _usable(cv.get(t.quantity), t.quantity, t.unit)
    if "missing" in c_v:
        comparable = False
        reasons.append(f"the candidate lacks {t.quantity} ({c_v['missing']})")
    unreached = comparable and never_reached(t.direction, c_v)
    if unreached:
        reasons.append(f"{t.quantity} is {_fmt(c_v, t.unit)}: a run that never reaches its target within its window "
                       "is not feasible")
    feas_c = "fails" if unreached else feasibility_of(checks) if comparable else "unknown"
    b_checks = [check_constraint(c, _usable(bv.get(c.quantity), c.quantity, c.unit)) for c in obj.constraints] \
        if baseline is not None else []
    feas_b = side_feasibility(obj, bv) if baseline is not None else "unknown"

    target = {"quantity": t.quantity, "unit": t.unit, "direction": t.direction, "margin": t.margin,
              "limit": t.limit, "limit_ok": None, "baseline": None, "candidate": None, "reference": None,
              "against": None, "delta": None, "h": None, "better": None}
    if "missing" not in c_v:
        c_lo, c_hi = _interval(c_v, t.unit)
        if c_v["value"] is not None:
            target["candidate"] = _converted(c_v["value"], c_v["h"], c_v["unit"], t.unit)["value"]
        if t.limit is not None:
            if t.direction == "minimize":
                target["limit_ok"] = True if c_hi <= t.limit else False if c_lo > t.limit else None
            else:
                target["limit_ok"] = True if c_lo >= t.limit else False if c_hi < t.limit else None
    b_t = _usable(bv.get(t.quantity), t.quantity, t.unit)
    if baseline is not None and "missing" not in b_t and b_t["value"] is not None:
        target["baseline"] = _converted(b_t["value"], b_t["h"], b_t["unit"], t.unit)["value"]

    ref_public, faster, status, why = None, None, "not_compared", reference_reason
    r_v = _usable((reference or {}).get("values", {}).get(t.quantity), t.quantity, t.unit)
    if reference is not None:
        ref_public = {"label": reference["label"], "source": reference["source"],
                      "value": None if "missing" in r_v or r_v["value"] is None else
                      _converted(r_v["value"], r_v["h"], r_v["unit"], t.unit)["value"],
                      "unit": t.unit, "objective_version": reference["objective_version"],
                      "request_id": reference.get("request_id")}
    if unreached:
        why = "the candidate never reaches its target within its window, so its speed is not compared"
    elif feas_c == "fails":
        why = "the candidate does not meet the constraints, so its speed is not compared"
    elif feas_c == "unknown":
        why = ("the candidate lacks a compared quantity" if not comparable else
               "a constraint is undecided within its uncertainty") + ", so its speed is not compared"
    elif reference is None:
        pass
    elif "missing" in r_v:
        why = f"the reference lacks {t.quantity} ({r_v['missing']})"
    else:
        what = f"{t.quantity} {_fmt(c_v, t.unit)} against {_fmt(r_v, t.unit)} for {reference['label']}"
        if c_v["value"] is not None and r_v["value"] is not None:
            r = _converted(r_v["value"], r_v["h"], r_v["unit"], t.unit)
            c = _converted(c_v["value"], c_v["h"], c_v["unit"], t.unit)
            d = r["value"] - c["value"] if t.direction == "minimize" else c["value"] - r["value"]
            H = math.hypot(r["h"], c["h"])
            d_lo, d_hi = d - H, d + H
            target |= {"delta": d, "h": H}
        else:
            r_lo, r_hi = _interval(r_v, t.unit)
            c_lo, c_hi = _interval(c_v, t.unit)
            d_lo, d_hi = (r_lo - c_hi, r_hi - c_lo) if t.direction == "minimize" else (c_lo - r_hi, c_hi - r_lo)
        m = max(t.margin, 0.0)             # "faster" means faster: a negative margin never allows a slowdown here
        faster = True if d_lo > m else False if d_hi <= m else None
        status = "faster" if faster else "not_faster"
        target |= {"reference": ref_public["value"], "against": reference["source"], "better": faster}
        why = (f"{what}: faster by more than the {m:g} {t.unit} margin" if faster else
               f"{what}: not faster by more than the {m:g} {t.unit} margin" if faster is False else
               f"{what}: the difference is within its uncertainty of the {m:g} {t.unit} margin"
               if math.isfinite(d_lo) and math.isfinite(d_hi) else
               f"{what}: a crossing outside its window leaves the difference open")
    speed = {"reference": ref_public, "status": status, "faster": faster, "delta": target["delta"],
             "reason": why}

    kind = objective_kind(obj)
    if kind == "hard_target":
        lim = target["limit_ok"]
        o_status = "not_met" if feas_c == "fails" or lim is False else \
            "met" if feas_c == "meets" and lim is True else "unknown"
        if lim is False:
            reasons.append(f"{t.quantity} {_fmt(c_v, t.unit)} misses the hard target of {t.limit:g} {t.unit}")
        elif lim is None and "missing" not in c_v:
            reasons.append(f"{t.quantity} {_fmt(c_v, t.unit)} straddles the hard target of {t.limit:g} {t.unit}")
    else:
        o_status = "open"
    objective = {"kind": kind, "status": o_status, "target_limit": t.limit}

    if not comparable:
        verdict = "not_comparable"
    elif feas_c == "fails":
        verdict = "violates_constraints"
    elif feas_c == "unknown":
        verdict = "inconclusive"
    else:
        verdict = "candidate_better" if faster else "not_better" if faster is False else "inconclusive"
        if status != "faster":
            reasons.append(f"speed not shown faster: {why}" if status == "not_faster" else f"speed not compared: {why}")
    if verdict == "candidate_better":
        reasons = [r for r in reasons if "hard target" in r]
    return {"evaluator": obj.evaluator, "objective_version": obj.version, "basis": basis,
            "baseline": {**baseline, "constraints": b_checks} if baseline is not None else
            {"basis": None, "refs": [], "results": [], "values": {}, "constraints": []},
            "candidate": candidate, "target": target, "constraints": checks, "verdict": verdict,
            "reasons": list(dict.fromkeys(reasons)),
            "feasibility": {"baseline": feas_b, "candidate": feas_c}, "speed": speed, "objective": objective}


# ------------------------------------------------------------------ the speed reference (loop-compare/3)

def _baseline_pending(cid: str, obj: ObjectiveData, states: dict[str, RequestState]) -> bool:
    runs = [states.get(baseline_request_id(cid, ref)) for ref in obj.baseline]
    return any(st is not None and st.proposed_by == REGISTRATION and st.state in PENDING for st in runs)


def speed_reference(L: Ledger, cid: str, obj: ObjectiveData, states: dict[str, RequestState], basis: str,
                    baseline: dict | None, records: list[LoopRecord]) -> tuple[dict | None, str]:
    """(the reference for speed on `basis`, or None; why there is none): the baseline when it meets every constraint,
    else the reference search's answer for the constraints in force (computational only)."""
    if baseline is not None:
        feas = side_feasibility(obj, baseline["values"])
        if feas == "meets":
            rid = baseline_request_id(cid, baseline["refs"][0]) if basis == "computational" and \
                len(baseline["refs"]) == 1 else None
            return {"label": "the baseline", "source": "baseline", "objective_version": obj.version,
                    "request_id": rid, "values": baseline["values"]}, ""
        t = obj.target
        lead = ("the baseline never reaches its target within its window, so it is no reference for speed"
                if never_reached(t.direction, _usable(baseline["values"].get(t.quantity), t.quantity, t.unit)) else
                "the baseline fails the constraints, so it is no reference for speed" if feas == "fails" else
                "the baseline does not meet the constraints within its uncertainty, so it is no reference for speed")
    elif basis == "computational" and _baseline_pending(cid, obj, states):
        return None, "the computational baseline has not finished on the solver yet"
    else:
        lead = f"there is no {basis} baseline that meets the constraints"
    if basis != "computational":
        return None, f"{lead}, and the reference search runs on the solver only"
    s = loop_reference.state(L, cid, obj, states, records)
    if s is None:
        return None, f"{lead}, and this objective runs no reference search"
    v = s["objective_version"]
    if s["status"] in ("due", "running", "waiting_for_baseline"):
        return None, f"{lead}; the reference search for objective version {v} has not finished"
    if s["status"] == "stopped":
        return None, f"{lead}; the reference search for objective version {v} was stopped by a cancellation"
    if s["status"] == "stopped_at_cap":
        return None, (f"{lead}; the run cap of {s['runs_cap']} runs was reached before the search finished, with no "
                      f"run meeting the constraints of objective version {v} "
                      f"({loop_reference.bracket_words(s)})")
    if s["status"] == "not_needed":        # the baseline meets them (handled above); kept for a caller's own baseline
        return None, f"{lead}; the reference search for objective version {v} did not run: the baseline meets them"
    if s["found"] is None:
        return None, (f"{lead}; the reference search for objective version {v} found no single-step protocol "
                      "that meets the constraints")
    return {"label": f"{s['label']} (best found by the reference search for objective version {v})",
            "source": "reference_search", "objective_version": v, "request_id": s["found"]["request_id"],
            "values": s["found"]["values"]}, ""


# ------------------------------------------------------------------ comparisons of a campaign

def candidates(states: dict[str, RequestState]) -> list[RequestState]:
    """Completed requests compared with the baseline, in result order; the registration re-runs are the baseline."""
    done = [s for s in states.values() if s.result is not None and s.proposed_by not in SYSTEM_RUNS]
    return sorted(done, key=lambda s: s.result.seq)


def comparison(L: Ledger, cid: str, obj: ObjectiveData, states: dict[str, RequestState], st: RequestState,
               records: list[LoopRecord] | None = None) -> dict:
    """The canonical comparison payload for one completed request, from the ledger as it stands."""
    basis = st.result.data["basis"]
    baseline, why = baseline_side(L, cid, obj, states, basis)
    cand = {"request_id": st.request_id, "result_id": st.result.id,
            "values": result_values(L, obj, st.result, states)}
    if obj.evaluator == "loop-compare/3":
        recs = chain.records(L, cid) if records is None else records
        reference, ref_why = speed_reference(L, cid, obj, states, basis, baseline, recs)
        return chain.payload("comparison", compare3(obj, basis, baseline, cand, reference, reference_reason=ref_why))
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
    missing or out of date (a new result, an amendment, a baseline re-run or the reference search finishing). First,
    withdraw the proposals an amendment superseded (loop.withdraw_superseded), and under loop-compare/3 queue the
    reference search's next step if one is due (its own savepoint: a step that
    cannot be queued now is left to the runner's next tick). Idempotent; one transaction."""
    out = []
    with L.transaction():
        if chain.concluded(L, cid):
            return out
        withdraw_superseded(L, cid)      # an amendment supersedes proposals still awaiting approval
        try:
            loop_reference.queue(L, cid)
        except AdmissionError:
            pass
        recs = chain.records(L, cid)
        obj, states = current_objective(L, cid, recs), request_states(L, cid, recs)
        latest, counts = _latest(recs, obj.version)
        for st in candidates(states):
            rid = st.result.id
            fresh = comparison(L, cid, obj, states, st, recs)
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


def select_best3(comparisons: list[LoopRecord]) -> tuple[str, LoopRecord | None]:
    """loop-compare/3 rule 10: (outcome, the comparison it rests on)."""
    groups = (("objective_met", lambda d: d["objective"]["status"] == "met"),
              ("faster_than_reference", lambda d: d["speed"]["status"] == "faster"),
              ("feasible_only", lambda d: d["feasibility"]["candidate"] == "meets"),
              ("no_feasible_result", lambda d: d["feasibility"]["candidate"] == "fails"))
    for name, test in groups:
        group = [c for c in comparisons if test(c.data)]
        if group:
            measured = [c for c in group if c.data["basis"] != "computational"]
            return name + ("" if measured else "_in_simulation"), min(measured or group, key=_rank)
    return "unresolved", None


def _current(L: Ledger, cid: str, recs: list[LoopRecord], *, check: bool = True):
    """(objective, states, each candidate's latest comparison on the current version, problems); with check=False the
    comparisons are not recomputed and problems is empty."""
    obj, states = current_objective(L, cid, recs), request_states(L, cid, recs)
    latest, _ = _latest(recs, obj.version)
    problems, comps = [], []
    for st in candidates(states):
        rec = latest.get(st.result.id)
        if check and (rec is None or rec.data != comparison(L, cid, obj, states, st, recs)):
            problems.append(f"result {st.result.id} has no up-to-date comparison on objective version {obj.version}")
        if rec is not None:
            comps.append(rec)
    return obj, states, comps, problems


def outcome(L: Ledger, cid: str, records: list[LoopRecord] | None = None) -> tuple[str, str | None, list[str]]:
    """(outcome, comparison id, problems): problems name completed candidates whose latest comparison on the current
    objective version is missing or no longer what its evaluator computes."""
    recs = chain.records(L, cid) if records is None else records
    obj, _, comps, problems = _current(L, cid, recs)
    best_outcome, best = select_best3(comps) if obj.evaluator == "loop-compare/3" else select_best(comps)
    return best_outcome, best.id if best else None, problems


# ------------------------------------------------------------------ findings: feasibility, speed, objective

def facets(obj: ObjectiveData, d: dict) -> dict:
    """{"feasibility", "speed", "objective"} of one comparison payload: /3 records them; for /1 and /2 they are
    derived (speed only against a baseline that itself meets the constraints)."""
    if d["evaluator"] == "loop-compare/3":
        return {"feasibility": d["feasibility"], "speed": d["speed"], "objective": d["objective"]}
    t = d["target"]
    unreached = lambda side: never_reached(t.get("direction", "minimize"),       # noqa: E731
                                           (side.get("values") or {}).get(t["quantity"]))
    feas_c = "fails" if d["verdict"] != "not_comparable" and unreached(d["candidate"]) else \
        feasibility_of(d["constraints"])
    if d["baseline"].get("basis") is None:
        b_checks, feas_b = [], "unknown"
    else:
        b_checks = d["baseline"].get("constraints") or \
            [check_constraint(c, _usable(d["baseline"]["values"].get(c.quantity), c.quantity, c.unit))
             for c in obj.constraints]
        feas_b = "fails" if unreached(d["baseline"]) else feasibility_of(b_checks)
    speed = {"reference": None, "status": "not_compared", "faster": None, "delta": None, "reason": ""}
    if feas_b != "meets":
        speed["reason"] = ("the baseline fails the constraints, so it is no reference for speed" if feas_b == "fails"
                           else "no baseline on this basis meets the constraints") + \
            f", and a {d['evaluator']} campaign runs no reference search: speed is not compared"
    elif feas_c != "meets":
        speed["reason"] = "the candidate does not meet the constraints, so its speed is not compared"
    else:
        speed |= {"reference": {"label": "the baseline", "source": "baseline", "value": t["baseline"],
                                "unit": t["unit"], "objective_version": d["objective_version"], "request_id": None},
                  "status": "faster" if t["better"] else "not_faster", "faster": t["better"], "delta": t["delta"],
                  "reason": "faster than the baseline, which meets the constraints" if t["better"] else
                  "not shown faster than the baseline, which meets the constraints"}
    lim = t.get("limit_ok")
    status = "open" if t.get("limit") is None else \
        "not_met" if feas_c == "fails" or lim is False else "met" if feas_c == "meets" and lim is True else "unknown"
    return {"feasibility": {"baseline": feas_b, "candidate": feas_c}, "speed": speed,
            "objective": {"kind": "legacy", "status": status, "target_limit": t.get("limit")}}


def assess(L: Ledger, cid: str, records: list[LoopRecord] | None = None) -> dict:
    """What the findings rest on: {"best": the comparison record they describe or None, "basis", "findings",
    "compared": how many candidates have a comparison on the current version}.

    Under /3 the best is the comparison the outcome rests on; under /1 and /2 the best candidate that meets every
    constraint (measured first, then the best target value, then the earliest), else the one select_best names. When
    results were compared but none is decided (each not comparable, or straddling a limit), the best is the first of
    them by the same order, and its feasibility is "unknown"; "none" is only for no comparison at all. Reads the
    recorded comparisons (whether they are up to date is outcome()'s check, which a conclusion must pass)."""
    recs = chain.records(L, cid) if records is None else records
    obj, states, comps, _ = _current(L, cid, recs, check=False)
    if obj.evaluator == "loop-compare/3":
        _, best = select_best3(comps)
    else:
        feasible = [c for c in comps if facets(obj, c.data)["feasibility"]["candidate"] == "meets"]
        best = min(feasible, key=_rank) if feasible else select_best(comps)[1]
    if best is None and comps:
        best = min(comps, key=_rank)
    basis = best.data["basis"] if best is not None else (comps[0].data["basis"] if comps else
                                                         "computational" if obj.baseline_simulation else "physical")
    if best is not None:
        f = facets(obj, best.data)
        feas, speed, objective = f["feasibility"], dict(f["speed"]), dict(f["objective"])
        t = best.data["target"]
        speed["best"] = {"request_id": best.data["candidate"]["request_id"], "value": t["candidate"],
                         "unit": t["unit"]} if feas["candidate"] == "meets" and t["candidate"] is not None else None
    else:
        baseline, _why = baseline_side(L, cid, obj, states, basis)
        feas = {"baseline": side_feasibility(obj, baseline["values"]) if baseline is not None else "unknown",
                "candidate": "none"}
        reference, why = (speed_reference(L, cid, obj, states, basis, baseline, recs)
                          if obj.evaluator == "loop-compare/3" else (None, ""))
        ref_public = None
        if reference is not None:
            v = _usable(reference["values"].get(obj.target.quantity), obj.target.quantity, obj.target.unit)
            ref_public = {"label": reference["label"], "source": reference["source"],
                          "value": None if "missing" in v or v["value"] is None else
                          _converted(v["value"], v["h"], v["unit"], obj.target.unit)["value"],
                          "unit": obj.target.unit, "objective_version": reference["objective_version"],
                          "request_id": reference.get("request_id")}
        speed = {"reference": ref_public, "status": "not_compared", "faster": None, "delta": None, "best": None,
                 "reason": "no candidate result yet" + (f"; {why}" if why else "")}
        kind = objective_kind(obj)
        objective = {"kind": kind, "status": "open" if kind == "fastest_feasible" or
                     (kind == "legacy" and obj.target.limit is None) else "unknown",
                     "target_limit": obj.target.limit}
    if objective["kind"] == "hard_target" and best is not None:
        statuses = [c.data["objective"]["status"] for c in comps]
        objective["status"] = "met" if "met" in statuses else "unknown" if "unknown" in statuses else "not_met"
    findings = {
        "feasibility": {"baseline": feas["baseline"], "best": feas["candidate"],
                        "recovered": feas["baseline"] == "fails" and feas["candidate"] == "meets",
                        "best_request_id": best.data["candidate"]["request_id"] if best is not None else None},
        "speed": {"reference": speed["reference"], "best": speed["best"], "delta": speed["delta"],
                  "faster": speed["faster"], "status": speed["status"], "reason": speed["reason"]},
        "objective": objective}
    return {"best": best, "basis": basis, "findings": findings, "compared": len(comps)}


def findings(L: Ledger, cid: str, records: list[LoopRecord] | None = None) -> dict:
    """The conclusion's findings: {"feasibility", "speed", "objective"} (see `assess`)."""
    return assess(L, cid, records)["findings"]


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
