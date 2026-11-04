"""The system reference search of a loop-compare/3 campaign: the fastest single-step protocol meeting the current
constraints, found on the solver by bisection, so a speed claim has a reference that meets the same constraints
when the baseline does not.

The system's adapter states the domain parts in its objective template (`reference_search`, frozen into the objective
with the system spec): the action, the one parameter searched and the action parameters it sets, its lattice
(lo, hi, step), which direction is faster, the fixed parameters, the run cap and the tolerance. This module only
bisects: lattice indices lo = -1 and hi = N stand for "below the lattice" and "above it"; each step runs the middle
point and keeps the half whose edge it decides (meets every constraint: lo moves up; never reaches a minimised target
within its window, which loop-compare/3 calls not feasible: too slow, so lo moves up without an answer; fails, is
undecided within its uncertainty, or the run failed: hi moves down), until hi - lo is within the tolerance
("found" or "none_feasible") or the run cap is reached ("found" with the best run so far, or "stopped_at_cap"). It
assumes that meeting the constraints gets harder as the protocol gets faster; the answer is a protocol that was run
and met them, called "best found by the reference search", never an optimum.

When it runs: for an objective under loop-compare/3 of kind fastest_feasible whose registering person pre-approved
baseline_simulation, and whose system defines a reference search, once the computational baseline has finished and
does not meet the constraints in force (a baseline that meets them is the speed reference: "not_needed"). Its first
step is queued when the baseline's result is recorded, each next step when the previous result is recorded
(loop_compare.record_comparisons, which the result, an amendment and a conclusion all call), and by every runner
tick, so a restart resumes it. A search serves the objective version
whose constraints it judged (`constraints_version`): an amendment that changes the constraints starts a new search;
one that changes only the target or the budget keeps the reference.

Each step is a request and an approval by `policy:reference` (loop.REFERENCE), approved under the registration's
pre-approval (rule "reference"), with a deterministic request id. Admission recomputes the search and accepts only its
next step, so a reference run cannot be forged or misplaced; reference runs never count against the experiment budget
and are never candidates.
"""
from __future__ import annotations

from translucent import chain
from translucent.ledger import AdmissionError, Ledger
from translucent.loop import (PENDING, REFERENCE, DuplicateRequest, RequestState, adapter_for, build_request,
                              current_objective, request_states)
from translucent.loop_contracts import ApprovalData, LoopRecord, ObjectiveData

EVALUATOR = "loop-compare/3"


def effective_kind(obj: ObjectiveData) -> str:
    """"legacy" under loop-compare/1 and /2; otherwise the objective's kind, or the one its target implies."""
    if obj.evaluator != EVALUATOR:
        return "legacy"
    return obj.kind or ("hard_target" if obj.target.limit is not None else "fastest_feasible")


def search_spec(obj: ObjectiveData) -> dict | None:
    """The reference search the system declared when the objective was registered (frozen spec), or None."""
    template = (obj.system.get("spec") or {}).get("objective_template") or {}
    rs = template.get("reference_search")
    return rs if isinstance(rs, dict) and rs.get("action") and isinstance(rs.get("parameter"), dict) else None


def applies(obj: ObjectiveData) -> bool:
    return effective_kind(obj) == "fastest_feasible" and obj.baseline_simulation and search_spec(obj) is not None


def _constraint_terms(constraints) -> list:
    return sorted((c["quantity"], c["op"], float(c["limit"]), c["unit"]) for c in constraints)


def constraints_version(records: list[LoopRecord], version: int) -> int:
    """The objective version, at or before `version`, whose constraints are those in force at `version`: the last
    one that changed them (a label alone is no change)."""
    since, terms = 1, None
    for rec in records:
        if rec.type == "objective":
            terms = _constraint_terms(rec.data["constraints"])
        elif rec.type == "amendment" and rec.data["version"] <= version and rec.data.get("constraints") is not None:
            new = _constraint_terms(rec.data["constraints"])
            if new != terms:
                since, terms = rec.data["version"], new
    return since


def lattice(rs: dict) -> list[float]:
    p = rs["parameter"]
    n = int(round((float(p["hi"]) - float(p["lo"])) / float(p["step"]))) + 1
    values = [round(float(p["lo"]) + i * float(p["step"]), 6) for i in range(n)]
    return values if p.get("faster", "higher") == "higher" else values[::-1]     # index order: slower to faster


def params_for(rs: dict, value: float) -> dict:
    return {**{k: v for k, v in (rs.get("fixed") or {}).items()}, **{name: value for name in rs["parameter"]["sets"]}}


def request_id(cid: str, version: int, step: int) -> str:
    return "rq_" + chain.digest([cid, "reference", version, step])[:20]


def label(rs: dict, value: float) -> str:
    p = rs["parameter"]
    text = rs.get("label") or rs["action"]
    return f"{text} at {value:g} {p.get('unit_label', p.get('unit', ''))}".strip()


def _judge(L: Ledger, obj: ObjectiveData, st: RequestState, states: dict[str, RequestState]) -> tuple[str, dict]:
    """(how one step's run stands under the objective as it stands, the result's values): "meets" (every constraint
    holds and the target has a value), "too_slow" (no constraint is violated, but the minimised target is never
    reached within its window: loop-compare/3 calls that not feasible, and a faster protocol is needed), or "fails"
    (a constraint is violated or undecided, a value is missing). "meets" is exactly loop_compare.side_feasibility's
    "meets", with the target present."""
    from translucent.loop_compare import _usable, check_constraint, feasibility_of, never_reached, result_values
    values = result_values(L, obj, st.result, states)
    t = obj.target
    tv = _usable(values.get(t.quantity), t.quantity, t.unit)
    checks = feasibility_of([check_constraint(c, _usable(values.get(c.quantity), c.quantity, c.unit))
                             for c in obj.constraints])
    if checks == "fails":
        return "fails", values
    if never_reached(t.direction, tv):
        return "too_slow", values
    return ("meets" if checks == "meets" and "missing" not in tv else "fails"), values


def _baseline_meets(L: Ledger, cid: str, obj: ObjectiveData, states: dict[str, RequestState]) -> bool | None:
    """Whether the computational baseline meets the objective's constraints (loop_compare.side_feasibility): None
    while it is still running on the solver; False when there is none, it failed, or it does not meet them."""
    from translucent.loop_compare import _baseline_pending, baseline_side, side_feasibility
    if _baseline_pending(cid, obj, states):
        return None
    baseline, _ = baseline_side(L, cid, obj, states, "computational")
    return baseline is not None and side_feasibility(obj, baseline["values"]) == "meets"


def bracket_words(s: dict) -> str:
    """Where the search stands, in words: "between 1 C and 2.5 C", "below 2.5 C", "above 1 C" or "nothing run"."""
    unit = s["spec"]["parameter"].get("unit_label", s["spec"]["parameter"].get("unit", ""))
    lo, hi = s["bracket"]
    fmt = lambda x: f"{x:g} {unit}".strip()       # noqa: E731
    if lo is not None and hi is not None:
        return f"the answer lies between {fmt(lo)} and {fmt(hi)}"
    if hi is not None:
        return f"every step run failed them; the answer, if any, lies below {fmt(hi)}"
    if lo is not None:
        return f"the answer lies above {fmt(lo)}"
    return "no step was run"


def state(L: Ledger, cid: str, obj: ObjectiveData, states: dict[str, RequestState],
          records: list[LoopRecord]) -> dict | None:
    """The search serving the objective's current constraints, folded from its runs; None when none applies.

    {"objective_version": the constraints version, "status": "waiting_for_baseline" | "not_needed" | "due" |
     "running" | "found" | "none_feasible" | "stopped_at_cap" | "stopped", "steps": [{"step", "request_id", "value",
     "meets", "result"}], "next": {"step", "request_id", "params", "value"} | None, "found": {"request_id",
     "result_id", "value", "values"} | None, "bracket": [lo, hi] values or None at an open end, "lo_meets": whether
     the lower edge is a run that met them (else one too slow to reach the target), "runs_cap", "label"}

    The search waits for the computational baseline and does not run when the baseline meets the constraints in force
    ("not_needed"): the baseline is then the speed reference. "stopped_at_cap": the run cap was reached before the
    bracket closed and no run met the constraints."""
    rs = search_spec(obj)
    if rs is None or not applies(obj):
        return None
    cv = constraints_version(records, obj.version)
    values = lattice(rs)
    cap = int(rs.get("max_runs", 8))
    gap = max(1, int(float(rs.get("tolerance", rs["parameter"]["step"])) / float(rs["parameter"]["step"]) + 1e-9))
    lo, hi, k = -1, len(values), 0
    steps, found, nxt, status = [], None, None, None
    if obj.baseline and (meets := _baseline_meets(L, cid, obj, states)) is not False:
        status = "waiting_for_baseline" if meets is None else "not_needed"
    while status is None:
        if hi - lo <= gap:
            status = "found" if found is not None else "none_feasible"
            break
        if k >= cap:
            status = "found" if found is not None else "stopped_at_cap"
            break
        mid = (lo + hi) // 2
        rid = request_id(cid, cv, k + 1)
        st = states.get(rid)
        if st is None:
            nxt = {"step": k + 1, "request_id": rid, "params": params_for(rs, values[mid]), "value": values[mid]}
            status = "due"
        elif st.state in PENDING:
            status = "running"
        elif st.state in ("cancelled", "rejected"):
            status = "stopped"
        else:
            res, got = _judge(L, obj, st, states) if st.result is not None else ("fails", {})
            steps.append({"step": k + 1, "request_id": rid, "value": values[mid], "meets": res == "meets",
                          "result": res})
            if res == "meets":
                lo, found = mid, {"request_id": rid, "result_id": st.result.id, "value": values[mid], "values": got}
            elif res == "too_slow":      # never reached the target: the answer is faster than this
                lo = mid
            else:
                hi = mid
            k += 1
    return {"objective_version": cv, "status": status, "steps": steps, "next": nxt, "found": found,
            "bracket": [values[lo] if lo >= 0 else None, values[hi] if hi < len(values) else None],
            "lo_meets": found is not None and lo >= 0 and found["value"] == values[lo],
            "runs_cap": cap, "label": label(rs, found["value"]) if found else None, "spec": rs}


def _due(L: Ledger, cid: str) -> bool:
    """Read-only: does the campaign's search have a step to queue? (Spares a runner tick the write lock.)"""
    if chain.concluded(L, cid):
        return False
    recs = chain.records(L, cid)
    obj = current_objective(L, cid, recs)
    if not applies(obj):
        return False
    s = state(L, cid, obj, request_states(L, cid, recs), recs)
    return s is not None and s["status"] == "due"


def queue(L: Ledger, cid: str) -> LoopRecord | None:
    """Queue the search's next step (request and approval, one savepoint) when one is due; None otherwise. Safe to
    call from any process at any time: ids are deterministic and admission accepts only the next step."""
    if not L.db.in_transaction and not _due(L, cid):
        return None
    try:
        with L.transaction():
            if chain.concluded(L, cid):
                return None
            recs = chain.records(L, cid)
            obj = current_objective(L, cid, recs)
            if not applies(obj):
                return None
            states = request_states(L, cid, recs)
            s = state(L, cid, obj, states, recs)
            if s is None or s["status"] != "due":
                return None
            rs, nxt = s["spec"], s["next"]
            try:
                adapter = adapter_for(obj)
            except KeyError:
                return None
            lo, hi = s["bracket"]
            unit = rs["parameter"].get("unit_label", rs["parameter"].get("unit", ""))
            known = ([f"{lo:g} {unit} {'met them' if s['lo_meets'] else 'never reached the target'}"]
                     if lo is not None else []) + \
                ([f"{hi:g} {unit} did not"] if hi is not None else [])
            req = build_request(
                adapter, obj, rs["action"], nxt["params"], request_id=nxt["request_id"],
                rationale=(f"Reference search for objective version {s['objective_version']}, step {nxt['step']} of "
                           f"at most {s['runs_cap']}: {label(rs, nxt['value'])}. A bisection" +
                           (f" (so far {' and '.join(known)})" if known else "") +
                           " for the fastest single-step protocol that meets this version's constraints, so speed is "
                           "compared only with a reference that meets them. A system run: not counted against the "
                           "experiment budget, never a candidate."),
                prediction={}, expected_effect="Narrows the reference search; no decision depends on one step.")
            rec = chain.append(L, cid, "request", req, REFERENCE, chain.natural_id("req", cid, req.request_id, 1))
            chain.append(L, cid, "approval",
                         ApprovalData(request_id=req.request_id, record_id=rec.id, content_hash=req.content_hash,
                                      author=obj.registered_by, rule="reference",
                                      note=f"Pre-approved at registration (baseline_simulation): reference search, "
                                           f"step {nxt['step']}"),
                         REFERENCE, chain.natural_id("apr", req.request_id, req.content_hash))
            return rec
    except DuplicateRequest:
        return None          # a pending request has this configuration; the step is queued once it is done
