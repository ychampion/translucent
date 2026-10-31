"""Claim gate: deterministic, trusted code that decides a claim's status. No model is consulted.

Verdicts
- unsupported: the evidence cannot carry the claim (missing/failed/stale ancestry, wrong or unknown units, no recorded
  scale or time base behind a physical number, a required corroboration missing, a malformed finding). Says nothing
  about the science.
- supported / refuted / inconclusive: the predicate recomputed from the cited measurements with their stated
  uncertainty. A tool failure or a malformed finding can never produce "refuted"; a test too loose to tell anything
  apart is "inconclusive", never "supported".
The agent writes the finding's sentence; the gate's checks state the numbers it actually tested.
"""
from __future__ import annotations

import math
import time
import uuid

import numpy as np

from translucent.contracts import Check, Claim, Review
from translucent.ledger import Ledger

LENGTH_UNITS = {"um", "mm", "nm", "m"}
TIME_UNITS = {"s", "min", "h"}
PLAIN_UNITS = {"px", "px/mm", "V", "mV", "1", "%", "count"}  # need no scale or clock
TIME_QUANTITIES = {"elapsed_time", "time_base"}

# A claim that cites the first quantity must also cite the second (trusted campaign rules, not agent-editable).
REQUIRED_CORROBORATION = {"short_time": "voltage_collapse_time"}
# Scale and clock count only when they come from the experiment's own records, not from any run that names them.
TRUSTED_RECORDS = {"lookup_calibration", "read_cell_voltage"}
# A test whose allowed window is wider than this share of the values compared can't tell anything apart.
LOOSEST = 0.5


def _half_width(m) -> float:
    """Uncertainty as a half-width comparable across kinds (std -> 2 sigma)."""
    u = m.uncertainty
    if u.value is None or u.kind == "none":
        return 0.0
    return 2 * u.value if u.kind == "std" else u.value


def _needs(unit: str) -> set[str] | None:
    """Which calibrations a physical unit depends on; None for a unit the gate does not know (fails closed)."""
    if unit in PLAIN_UNITS:
        return set()
    num, slash, den = unit.partition("/")
    known = LENGTH_UNITS | TIME_UNITS
    if num not in known or (slash and den not in known):
        return None
    need = set()
    if num in LENGTH_UNITS or den in LENGTH_UNITS:
        need.add("spatial_scale")
    if num in TIME_UNITS or den in TIME_UNITS:
        need.add("time_base")
    return need


def _trusted(ledger: Ledger, m) -> bool:
    """A non-derived measurement produced by a tool that reads the experiment's own records."""
    return not m.quantity.startswith("derived:") and ledger.get("run", m.run_id).tool in TRUSTED_RECORDS


def _calibrations(ledger: Ledger, m, _seen=None) -> list:
    """Every calibration a measurement depends on, transitively, that came from the experiment's records."""
    _seen = _seen if _seen is not None else set()
    out = []
    for cid in m.calibration_ids:
        if cid in _seen:
            continue
        _seen.add(cid)
        c = ledger.get("measurement", cid)
        if c.quantity not in ("spatial_scale", "time_base") or _trusted(ledger, c):
            out.append(c)
        out += _calibrations(ledger, c, _seen)
    return out


def _conflict(ledger: Ledger, c) -> str | None:
    """Another trusted, current scale for the same recording that disagrees with this one beyond both uncertainties."""
    if c.quantity != "spatial_scale" or not isinstance(c.value, (int, float)):
        return None
    mine = ledger.recordings(c.id)
    for x in ledger.list("measurement"):
        if x.id == c.id or x.quantity != "spatial_scale" or not isinstance(x.value, (int, float)):
            continue
        if not _trusted(ledger, x) or ledger.stale_reason(x.id) or not ledger.recordings(x.id) & mine:
            continue
        if abs(x.value - c.value) > _half_width(x) + _half_width(c):
            return f"records disagree on the image scale ({c.value:.4g} vs {x.value:.4g} {c.unit})"
    return None


def _provenance(ledger: Ledger, claim: Claim) -> list[Check]:
    ms = [ledger.get("measurement", i) for i in claim.predicate.measurement_ids]
    checks = []
    for m in ms:
        run = ledger.get("run", m.run_id)
        stale = ledger.stale_reason(m.id)
        checks.append(Check(name="evidence is sound", passed=run.status == "ok" and not stale,
                            detail=f"{m.label or m.quantity}: " + (stale or f"from a successful {run.tool} run")))
        own = ledger.recordings(m.id)
        checks.append(Check(name="traced to a recording", passed=bool(own),
                            detail=f"{m.label or m.quantity}: " + ("from the recorded experiment" if own else
                                                                   "can't tell which recording it came from")))
        if m.quantity.startswith("derived:"):
            checks.append(Check(name="computed by analysis code", passed=True,
                                detail=f"{m.label or m.quantity}: computed by sandboxed code {run.code_sha or '?'}"))
        need = _needs(m.unit)
        if need is None:
            checks.append(Check(name="scale and timing recorded", passed=False,
                                detail=f"{m.label or m.quantity}: unit {m.unit!r} not recognised"))
            continue
        need -= {m.quantity} if m.quantity in ("spatial_scale", "time_base") else set()
        cals = _calibrations(ledger, m)
        missing = need - {c.quantity for c in cals}
        foreign = [c for c in cals if c.quantity in need and not own & ledger.recordings(c.id)]
        conflict = next((w for c in cals if c.quantity in need and (w := _conflict(ledger, c))), None)
        detail = (f"no recorded {', '.join(sorted(missing))}" if missing else
                  f"{foreign[0].label or foreign[0].quantity} belongs to another recording" if foreign else
                  conflict or "calibration on record")
        checks.append(Check(name="scale and timing recorded", passed=not (missing or foreign or conflict),
                            detail=f"{m.label or m.quantity} in {m.unit}: {detail}"))
    for q, partner in REQUIRED_CORROBORATION.items():
        if any(m.quantity.removeprefix("derived:") == q for m in ms):
            # the partner must be the instrument's own record, not a value computed by agent code
            checks.append(Check(name="independently corroborated",
                                passed=any(m.quantity == partner and _trusted(ledger, m) for m in ms),
                                detail=f"{q} must be checked against the recorded {partner}"))
    return checks


def _units_check(ms, unit) -> Check:
    bad = [m for m in ms if m.unit != unit]
    return Check(name="units match", passed=not bad,
                 detail="all in " + unit if not bad else f"expected {unit}, got {', '.join(m.unit for m in bad)}")


def _interval_verdict(lo_v, hi_v, lo, hi):
    if lo <= lo_v and hi_v <= hi:
        return "supported"
    if hi_v < lo or lo_v > hi:
        return "refuted"
    return "inconclusive"


# predicate parameters come from the agent: checked for shape before any arithmetic
_PARAMS = {"value_within": ({"lo": float, "hi": float, "unit": str}, {}),
           "values_agree": ({}, {"tol": float, "unit": str}),
           "events_coincide": ({}, {"tol": float, "unit": str}),
           "trend": ({"direction": str}, {"steady_tolerance": float})}
_COUNTS = {"value_within": 1, "values_agree": 2, "events_coincide": 2, "trend": 2}
_SCALAR = {"value_within", "values_agree", "events_coincide"}


def _number(v) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(float(v))
    except OverflowError:
        return False


def _well_formed(ledger: Ledger, claim: Claim) -> Check:
    p = claim.predicate
    if p.type not in _PARAMS:  # "exists" waits for a trusted presence measurement; nothing produces one yet
        return Check(name="finding is well-formed", passed=False, detail=f"{p.type} findings are not supported yet")
    required, optional = _PARAMS[p.type]
    prm = p.params
    problems = [f"missing {k}" for k in required if prm.get(k) is None]
    for k, want in {**required, **optional}.items():
        v = prm.get(k)
        if v is not None and not (isinstance(v, str) if want is str else _number(v)):
            problems.append(f"{k} must be {'text' if want is str else 'a number'}, got {v!r}")
    if not problems:
        if p.type == "value_within" and prm["lo"] > prm["hi"]:
            problems.append("lo is above hi")
        for k in ("tol", "steady_tolerance"):
            if prm.get(k) is not None and prm[k] < 0:
                problems.append(f"{k} is negative")
        if p.type == "trend" and prm["direction"] not in ("increasing", "decreasing", "steady"):
            problems.append(f"direction must be increasing, decreasing or steady, got {prm['direction']!r}")
    ids = p.measurement_ids
    if len(ids) != _COUNTS[p.type]:
        problems.append(f"{p.type} takes {_COUNTS[p.type]} measurement(s), got {len(ids)}")
    elif len(set(ids)) != len(ids):
        problems.append("the same measurement is cited twice")
    else:
        ms = [ledger.get("measurement", i) for i in ids]
        if p.type in _SCALAR and any(not _number(m.value) for m in ms):
            problems.append(f"{p.type} compares single numbers")
        if p.type == "trend" and any(not isinstance(m.value, list) or not all(_number(v) for v in m.value)
                                     for m in ms):
            problems.append("trend needs two series of numbers")
    return Check(name="finding is well-formed", passed=not problems, detail="; ".join(problems) or "ok")


def _too_loose(window: float, *values: float) -> bool:
    return window > LOOSEST * max(abs(v) for v in values)


def _evaluate(ledger: Ledger, claim: Claim) -> tuple[str, list[Check], str]:
    p = claim.predicate
    ms = [ledger.get("measurement", i) for i in p.measurement_ids]
    prm = p.params
    if p.type == "value_within":
        (m,) = ms
        u = _units_check(ms, prm["unit"])
        if not u.passed:
            return "unsupported", [u], u.detail
        h = _half_width(m)
        lo, hi = prm["lo"], prm["hi"]
        detail = f"{m.value:.4g} ± {h:.2g} {m.unit} vs [{lo:.4g}, {hi:.4g}]"
        # a window around zero may be as wide as the measurement's own spread; elsewhere a 10 ± 10 h value placed in
        # [0, 20] h says nothing
        if hi - lo > max(LOOSEST * max(abs(lo), abs(hi)), 4 * h if lo < 0 < hi else 0):
            return "inconclusive", [u, Check(name="range is specific", passed=False,
                                             detail=detail + ": range too wide to say anything")], ""
        v = _interval_verdict(m.value - h, m.value + h, lo, hi)
        return v, [u, Check(name="value in range", passed=v == "supported", detail=detail)], ""
    if p.type in ("values_agree", "events_coincide"):
        a, b = ms
        if p.type == "events_coincide" and not ledger.recordings(a.id) & ledger.recordings(b.id):
            c = Check(name="same experiment", passed=False, detail="the two times come from different recordings")
            return "unsupported", [c], c.detail
        unit = prm.get("unit") or ("s" if p.type == "events_coincide" else a.unit)
        u = _units_check(ms, unit)
        if not u.passed:
            return "unsupported", [u], u.detail
        comb = math.hypot(_half_width(a), _half_width(b))
        tol = prm.get("tol") or 0.0
        if comb == 0 and tol == 0:
            c = Check(name="uncertainty stated", passed=False, detail="neither value states an uncertainty")
            return "unsupported", [u, c], c.detail
        diff = abs(a.value - b.value)
        name = "times coincide" if p.type == "events_coincide" else "values agree"
        detail = f"differ by {diff:.4g} {unit}; allowed {tol + comb:.3g}"
        if _too_loose(tol + comb, a.value, b.value):
            return "inconclusive", [u, Check(name=name, passed=False,
                                             detail=detail + ": allowance too wide to tell them apart")], ""
        v = "supported" if diff <= tol + comb else "refuted" if diff > tol + 3 * max(comb, 1e-12) else "inconclusive"
        return v, [u, Check(name=name, passed=v == "supported", detail=detail)], ""
    if p.type == "trend":
        y, x = ms
        pair = Check(name="series belong together", passed=True, detail=f"{y.label or y.quantity} over time")
        if x.unit not in TIME_UNITS or not (x.quantity in TIME_QUANTITIES or x.quantity.startswith("derived:")):
            pair = Check(name="series belong together", passed=False, detail="the second series must be the time axis")
        elif not (y.run_id == x.run_id and y.subject == x.subject) and \
                not ledger.recordings(x.id) & ledger.recordings(y.id):
            pair = Check(name="series belong together", passed=False,
                         detail="the values and times are from different objects or recordings")
        yv, xv = np.asarray(y.value, float), np.asarray(x.value, float)
        if pair.passed and (yv.shape != xv.shape or len(yv) < 4):
            pair = Check(name="enough points", passed=False, detail=f"{len(yv)} points; need 4+ aligned")
        if not pair.passed or np.ptp(xv) == 0:
            pair = pair if not pair.passed else Check(name="enough points", passed=False, detail="all at one time")
            return "unsupported", [pair], pair.detail
        slope, intercept = np.polyfit(xv, yv, 1)
        resid = yv - (slope * xv + intercept)
        se = math.sqrt(resid @ resid / (len(xv) - 2) / ((xv - xv.mean()) @ (xv - xv.mean())))
        lo, hi = slope - 2 * se, slope + 2 * se
        tolerance = prm.get("steady_tolerance") or 0.0
        direction = prm["direction"]
        if direction == "increasing":
            v = "supported" if lo > 0 else "refuted" if hi < 0 else "inconclusive"
        elif direction == "decreasing":
            v = "supported" if hi < 0 else "refuted" if lo > 0 else "inconclusive"
        else:  # steady
            v = "supported" if -tolerance <= lo and hi <= tolerance else \
                "refuted" if (lo > tolerance or hi < -tolerance) else "inconclusive"
        return v, [pair, Check(name=f"trend is {direction}", passed=v == "supported",
                               detail=f"slope {slope:.4g} {y.unit} per {x.unit} (95% range {lo:.3g} to {hi:.3g}), "
                                      f"{len(xv)} points")], ""
    raise ValueError(f"unknown predicate {p.type}")


def review(ledger: Ledger, claim_id: str) -> Review:
    """Run the gate on a recorded claim and record its Review. Every claim gets a review, even a malformed one."""
    claim = ledger.get("claim", claim_id)
    try:
        shape = _well_formed(ledger, claim)
        checks = [shape] + (_provenance(ledger, claim) if shape.passed else [])
        if not all(c.passed for c in checks):
            verdict, more, why = "unsupported", [], next(c.detail for c in checks if not c.passed)
        else:
            verdict, more, why = _evaluate(ledger, claim)
        checks += more
    except Exception as e:  # noqa: BLE001 - agent input the checks above missed: refused, never left unreviewed
        why = f"malformed finding ({type(e).__name__})"
        verdict, checks = "unsupported", [Check(name="finding is well-formed", passed=False, detail=why)]
    reason = {"supported": "The evidence supports it.",
              "refuted": "The measurements contradict it.",
              "inconclusive": "The measurements can't settle it yet.",
              "unsupported": f"Not enough evidence: {why}"}[verdict]
    rv = Review(id=f"rev_{uuid.uuid4().hex[:12]}", claim_id=claim_id, reviewer="gate", verdict=verdict,
                checks=checks, reason=reason, created_at=time.time())
    ledger.add(rv)
    return rv
