"""LoopView (view_version 1): the one JSON a live campaign and a recorded export share (design §2.12).

`view(L, cid, source)` is computed from the ledger alone: the loop records, their runs, measurements and series,
the campaign's events and the objective's frozen system spec. Nothing in it depends on the adapters this process
has installed or on the clock, except `source`, so verify recomputes a recorded view byte for byte (ignoring
`source`) from the export's embedded ledger. `source.kind` says whether this is a live campaign or a recorded
replay; the web app renders it as a persistent banner.

`summary(L, cid, ledger)` is the CampaignSummary the campaign list shows.

Milestone 2 added optional fields, computed for every campaign (loop-compare/1 and /2 included): objective.kind,
objective.summary (one line from the current version's target and constraints; objective.statement stays the text
as registered, at objective.statement_version), requests[].role, `assessment` (feasibility, speed against a reference
that meets the same constraints, the objective's status, solver against model uncertainty, a headline and lines),
and conclusion.findings for a loop-compare/3 conclusion. Fields that existed before are computed exactly as before,
so an export written earlier still verifies (loop_export compares a pre-M2 view without the added fields). The view
never words a feasibility recovery as an improvement or as faster.
"""
from __future__ import annotations

import json
import time

from translucent import chain, units
from translucent.ledger import Ledger
from translucent.loop import (REFERENCE, REGISTRATION, RequestState, actor_kind, actor_name, adapter_for,
                              current_objective, experiments_used, request_states)
from translucent.loop_compare import (assess, baseline_side, check_constraint, constraint_reason, half_width,
                                      never_reached, objective_kind)
from translucent.loop_contracts import AmendmentData, LoopRecord, ObjectiveData, RequestData

VIEW_VERSION = 1
_APPROVAL_ACTIONS = ["approve", "reject", "revise", "cancel"]
_VERDICT_WORDS = {"candidate_better": "better than the baseline", "not_better": "not better than the baseline",
                  "violates_constraints": "outside a constraint", "inconclusive": "inconclusive within the uncertainty",
                  "not_comparable": "not comparable with the baseline"}
_BASIS_WORDS = {"computational": "In simulation", "physical": "On the bench", "retrospective": "In the records"}


# ------------------------------------------------------------------ small reads

def events_cursor(L: Ledger, cid: str) -> int:
    return L.db.execute("SELECT COALESCE(MAX(seq), 0) FROM event WHERE campaign_id=?", (cid,)).fetchone()[0]


def _last_event_t(L: Ledger, cid: str) -> float | None:
    row = L.db.execute("SELECT body FROM event WHERE campaign_id=? ORDER BY seq DESC LIMIT 1", (cid,)).fetchone()
    return json.loads(row[0])["t"] if row else None


def model_spend(L: Ledger, cid: str) -> float:
    """Model spending recorded on the campaign's cost.updated events, as the legacy campaigns sum it."""
    rows = L.db.execute("SELECT body FROM event WHERE campaign_id=? AND body LIKE '%cost.updated%'", (cid,))
    events = [json.loads(b) for (b,) in rows]
    return round(sum(float((e.get("data") or {}).get("usd") or 0) for e in events if e["type"] == "cost.updated"), 6)


def _spec(obj: ObjectiveData) -> dict:
    return obj.system.get("spec") or {}


def quantity_label(obj: ObjectiveData, name: str) -> str:
    q = next((q for q in _spec(obj).get("quantities", []) if q.get("name") == name), None)
    return (q or {}).get("label") or name


TITLE_TEXT_MAX = 40          # a longer text parameter (a model definition, a commitment) is named, not printed


def request_title(obj: ObjectiveData, d: RequestData) -> str:
    """The action's label with its parameters, from the frozen spec (not the installed adapter)."""
    action = next((a for a in _spec(obj).get("actions", []) if a.get("name") == d.action), None)
    label = (action or {}).get("label") or d.action
    values = ", ".join(f"{k} {v:g} {d.units.get(k, '')}".strip() if isinstance(v, (int, float)) else
                       f"{k} {v}" if len(str(v)) <= TITLE_TEXT_MAX else f"{k} (recorded with the request)"
                       for k, v in d.params.items())
    return f"{label}: {values}" if values else label


# ------------------------------------------------------------------ status and next action

def status(states: dict[str, RequestState], concluded: bool) -> str:
    if concluded:
        return "concluded"
    live = {s.state for s in states.values()}
    if "awaiting_approval" in live:
        return "waiting_for_approval"
    if "awaiting_result" in live:
        return "waiting_for_result"
    return "running"


def next_action(obj: ObjectiveData, states: dict[str, RequestState], records: list[LoopRecord],
                concluded: bool) -> dict:
    """What the campaign waits for: a person's approval, a run, a person's result, the planner, a person's decision
    after the planner stopped on its model budget, or nothing."""
    if concluded:
        return {"kind": "none"}
    by_recency = sorted(states.values(), key=lambda s: s.timeline[-1]["seq"], reverse=True)
    for kind, wanted in (("awaiting_approval", ("awaiting_approval",)), ("running", ("queued", "running")),
                         ("awaiting_result", ("awaiting_result",))):
        st = next((s for s in by_recency if s.state in wanted), None)
        if st is None:
            continue
        d = st.data
        out = {"kind": kind, "request_id": st.request_id, "version": d.version, "title": request_title(obj, d)}
        if kind == "awaiting_approval":
            return out | {"content_hash": d.content_hash, "rationale": d.rationale,
                          "expected_effect": d.expected_effect, "prediction": d.prediction, "cost": d.cost,
                          "executor": d.executor, "actions": list(_APPROVAL_ACTIONS)}
        actions = ["cancel"] if st.state == "queued" else [] if kind == "running" else ["submit_result", "cancel"]
        return out | {"executor": d.executor, "actions": actions}
    planner = next((r for r in reversed(records) if r.type == "planner"), None)
    if planner is not None and planner.data["phase"] != "turn_finished":
        return {"kind": "planner_thinking",
                "title": planner.data["payload"].get("title") or "The planner is choosing the next step"}
    if planner is not None and planner.data["payload"].get("status") == "budget":
        # every later turn starts the same way until a person raises the budget or concludes, so say so
        return {"kind": "planner_stopped", "status": "budget",
                "title": planner.data["payload"].get("reason") or "The campaign's model budget is spent"}
    return {"kind": "none"}


# ------------------------------------------------------------------ parts of the view

def _value(v: dict) -> dict:
    out = {"value": v["value"], "unit": v["unit"], "h": v["h"]}
    return out | ({"not_reached_within": list(v["not_reached_within"])} if v["value"] is None else {})


def _check(check: dict) -> dict:
    out = {"quantity": check["quantity"], "value": check["value"], "limit": check["limit"], "ok": check["ok"]}
    return out | ({"not_reached_within": check["not_reached_within"]} if "not_reached_within" in check else {})


def _amendments(records: list[LoopRecord]) -> list[dict]:
    out, obj = [], None
    for rec in records:
        if rec.type == "objective":
            obj = rec.data
            continue
        if rec.type != "amendment" or obj is None:
            continue
        a, changes = AmendmentData.model_validate(rec.data), []
        if a.target is not None:
            new = a.target.model_dump(mode="json")
            changes += [f"target {k} {obj['target'].get(k)} -> {v}" for k, v in new.items()
                        if obj["target"].get(k) != v]
            obj = obj | {"target": new}
        if a.constraints is not None:
            old = {c["quantity"]: c for c in obj["constraints"]}
            new = {c.quantity: c.model_dump(mode="json") for c in a.constraints}
            for q in dict.fromkeys(list(old) + list(new)):
                o, n = old.get(q), new.get(q)
                if o == n:
                    continue
                if o is None:
                    changes.append(f"{q} {n['op']} {n['limit']:g} {n['unit']} added")
                elif n is None:
                    changes.append(f"{q} {o['op']} {o['limit']:g} {o['unit']} removed")
                elif (o["op"], o["unit"]) == (n["op"], n["unit"]):
                    changes.append(f"{q} {o['limit']:g} -> {n['limit']:g} {n['unit']}")
                else:
                    changes.append(f"{q} {o['op']} {o['limit']:g} {o['unit']} -> {n['op']} {n['limit']:g} {n['unit']}")
            obj = obj | {"constraints": list(new.values())}
        out.append({"version": a.version, "seq": rec.seq, "author": a.author, "reason": a.reason, "changes": changes})
    return out


def _baselines(L: Ledger, cid: str, obj: ObjectiveData, states: dict[str, RequestState]) -> list[dict]:
    out = []
    for basis in ("physical", "computational"):
        side, _ = baseline_side(L, cid, obj, states, basis)
        if side is None:
            continue
        values = {q: v for q, v in side["values"].items() if "missing" not in v}
        checks = [(c, values.get(c.quantity)) for c in obj.constraints]
        out.append({"basis": basis, "refs": list(side["refs"]), "values": {q: _value(v) for q, v in values.items()},
                    "constraints": [_check(check_constraint(c, v)) for c, v in checks if v is not None],
                    "fails": [why for c, v in checks
                              if v is not None and check_constraint(c, v)["ok"] is False
                              and (why := constraint_reason(c, v))]})
    return out


def _derived(L: Ledger, cid: str) -> dict[str, list]:
    """subject -> derived Measurements (define_measurement), in run order."""
    out: dict[str, list] = {}
    for run in L.list("run", campaign_id=cid):
        if run.tool.startswith("operator:"):
            for mid in run.measurement_ids or []:
                m = L.get("measurement", mid)
                if m is not None:
                    out.setdefault(m.subject, []).append(m)
    return out


def _measurement(m) -> dict | None:
    if not isinstance(m.value, float):
        return None
    return {"id": m.id, "quantity": m.quantity, "value": m.value, "unit": m.unit, "h": half_width(m.uncertainty)}


def _prediction_error(prediction: dict, measured: dict[str, tuple[float, str]]) -> dict:
    out = {}
    for q, p in prediction.items():
        if q not in measured or p.get("value") is None:
            continue
        value, unit = measured[q]
        try:
            predicted = units.convert(p["value"], p["unit"], unit)
            lo = units.convert(p["lo"], p["unit"], unit) if p.get("lo") is not None else None
            hi = units.convert(p["hi"], p["unit"], unit) if p.get("hi") is not None else None
        except (ValueError, KeyError):
            continue
        out[q] = {"predicted": predicted, "measured": value,
                  "inside": lo <= value <= hi if lo is not None and hi is not None else None}
    return out


def _request(L: Ledger, obj: ObjectiveData, st: RequestState, ledger: str, derived: dict[str, list],
             compared: dict[str, LoopRecord]) -> dict:
    d = st.data
    result = None
    if st.result is not None:
        r = st.result.data
        ms = [m for mid in r["measurements"].values() if (m := L.get("measurement", mid)) is not None]
        ms += derived.get(st.request_id, [])
        measurements = [v for m in ms if (v := _measurement(m)) is not None]
        measured = {m["quantity"]: (m["value"], m["unit"]) for m in measurements}
        if (cmp := compared.get(st.result.id)) is not None:     # objective quantities computed by ops/1
            for q, v in cmp.data["candidate"]["values"].items():
                if v.get("value") is not None:
                    measured.setdefault(q, (v["value"], v["unit"]))
        series = []
        for q, aid in r["series"].items():
            body = json.loads(L.blob_path(aid).read_text(encoding="utf-8"))
            series.append({"quantity": q, "unit": body["unit"], "url": f"/api/v1/ledgers/{ledger}/artifacts/{aid}",
                           "n": len(body["t"])})
        result = {"result_id": st.result.id, "basis": r["basis"], "measurements": measurements,
                  "categories": dict(r.get("categories", {})), "series": series,
                  "prediction_error": _prediction_error(d.prediction, measured)}
    return {"request_id": st.request_id, "version": d.version, "state": st.state, "action": d.action,
            "executor": d.executor, "params": {k: {"value": v, "unit": d.units.get(k, "")} for k, v in d.params.items()},
            "proposed_by": st.proposed_by, "approved_by": st.approved_by, "content_hash": d.content_hash,
            "rationale": d.rationale, "prediction": d.prediction, "timeline": [dict(t) for t in st.timeline],
            "result": result, "rejections": [{"seq": x.seq, "reasons": list(x.data["reasons"])} for x in st.refused],
            **({"failure": _failure(st.failure.data)} if st.failure is not None else {}),
            "role": role(st), **({"revision": revision(st)} if d.version > 1 else {})}


def revision(st: RequestState) -> dict:
    """Who wrote the current version of a revised request; proposed_by keeps the original proposer."""
    by = st.record.actor
    return {"by": by, "version": st.data.version, "words": f"revised by {actor_name(by)} (v{st.data.version})"}


def role(st: RequestState) -> str:
    """"baseline" (a registration re-run), "reference" (a reference-search run) or "experiment"."""
    return {REGISTRATION: "baseline", REFERENCE: "reference"}.get(st.proposed_by, "experiment")


def _failure(d: dict) -> dict:
    lines = [x.strip() for x in d["error"].splitlines() if x.strip()]
    return {"attempt": d["attempt"], "reason": lines[-1] if lines else "no reason was recorded",
            "retryable": d["retryable"]}


def _comparison(rec: LoopRecord) -> dict:
    d, t = rec.data, rec.data["target"]
    out = {"id": rec.id, "request_id": d["candidate"]["request_id"], "objective_version": d["objective_version"],
           "basis": d["basis"],
           "target": {"baseline": t["baseline"], "candidate": t["candidate"], "delta": t["delta"],
                      "better": t["better"], **({"decided_by": t["decided_by"]} if t.get("decided_by") else {})},
           "constraints": [_check(c) for c in d["constraints"]], "verdict": d["verdict"], "reasons": list(d["reasons"])}
    if d["evaluator"] == "loop-compare/3":      # delta and better are against the speed reference, never a failing
        out["target"] |= {"reference": t["reference"], "against": t["against"]}          # baseline
        out |= {"feasibility": dict(d["feasibility"]), "speed": dict(d["speed"]), "objective": dict(d["objective"])}
    return out


def _unreached(d: dict) -> bool:
    """A comparison whose candidate never reaches its minimised target within its window (loop_compare.never_reached)."""
    t = d["target"]
    return d["verdict"] != "not_comparable" and \
        never_reached(t.get("direction", "minimize"), d["candidate"].get("values", {}).get(t["quantity"]))


def _sentence3(obj: ObjectiveData, rec: LoopRecord, title: str) -> str:
    """loop-compare/3: what ran, the target against the speed reference (never a failing baseline), the
    constraints, then feasibility, speed and the hard target, each in its own words."""
    d, t = rec.data, rec.data["target"]
    s, feas = d["speed"], d["feasibility"]["candidate"]
    parts = []
    if t["candidate"] is not None:
        parts.append(f"{quantity_label(obj, t['quantity'])} {t['candidate']:g} {t['unit']}" +
                     (f" against {t['reference']:g} {t['unit']} for the reference" if t.get("reference") is not None
                      else ""))
    parts += [f"{c['quantity']} {c['value']:g} {c['unit']} (limit {c['op']} {c['limit']:g})"
              for c in d["constraints"] if c["value"] is not None]
    ref = (s.get("reference") or {}).get("label", "the reference")
    if d["verdict"] == "not_comparable":
        words = "not comparable" + (f" ({d['reasons'][0]})" if d["reasons"] else "")
    elif feas == "fails" and _unreached(d):
        words = "never reaches its target within its window, so it is not feasible"
    elif feas == "fails":
        words = "outside a constraint"
    elif feas == "unknown":
        words = "a constraint is undecided within its uncertainty"
    elif s["status"] == "faster":
        words = f"meets every constraint; {s['delta']:g} {t['unit']} faster than {ref}" if s["delta"] is not None \
            else f"meets every constraint; faster than {ref}"
    elif s["status"] == "not_faster":
        words = f"meets every constraint; not shown faster than {ref}"
    else:
        words = f"meets every constraint; speed not compared ({s['reason']})"
    if d["objective"]["kind"] == "hard_target":
        words += {"met": f"; meets the hard target of {t['limit']:g} {t['unit']}",
                  "not_met": f"; does not meet the hard target of {t['limit']:g} {t['unit']}",
                  "unknown": f"; the hard target of {t['limit']:g} {t['unit']} is undecided"}[d["objective"]["status"]]
    lead = _BASIS_WORDS.get(d["basis"])
    head = f"{lead}, {title}" if lead else title
    return f"{head}: {'; '.join(parts)}; {words}." if parts else f"{head}: {words}."


def sentence(obj: ObjectiveData, rec: LoopRecord, title: str) -> str:
    """One plain sentence for the current result: what ran, the target against the baseline, the constraints."""
    if rec.data["evaluator"] == "loop-compare/3":
        return _sentence3(obj, rec, title)
    d, t = rec.data, rec.data["target"]
    parts = []
    if t["candidate"] is not None:
        parts.append(f"{quantity_label(obj, t['quantity'])} {t['candidate']:g} {t['unit']}" +
                     (f" against {t['baseline']:g} {t['unit']} for the baseline" if t["baseline"] is not None else ""))
    parts += [f"{c['quantity']} {c['value']:g} {c['unit']} (limit {c['op']} {c['limit']:g})"
              for c in d["constraints"] if c["value"] is not None]
    words = _VERDICT_WORDS[d["verdict"]]
    if t.get("decided_by") == "feasibility":          # loop-compare/2: it meets limits the baseline breaks
        labels = {c.quantity: c.label or c.quantity for c in obj.constraints}
        broken = [labels.get(c["quantity"], c["quantity"]) for c in d["baseline"].get("constraints", [])
                  if c["ok"] is False]
        words = f"better than the baseline, which fails {' and '.join(broken)}"
        if t["delta"] is not None and t["delta"] < 0:
            words += f"; meeting the limits costs {-t['delta']:g} {t['unit']}"
    if d["verdict"] == "not_comparable" and d["reasons"]:
        words += f" ({d['reasons'][0]})"
    lead = _BASIS_WORDS.get(d["basis"])
    head = f"{lead}, {title}" if lead else title
    return f"{head}: {'; '.join(parts)}; {words}." if parts else f"{head}: {words}."


# ------------------------------------------------------------------ objective summary and assessment (M2)

def _lower(text: str) -> str:
    return text[:1].lower() + text[1:] if text[1:2].islower() or len(text) < 2 else text


def objective_summary(obj: ObjectiveData) -> str:
    """One line from the current version's target and constraints, in the system's wording when its frozen spec
    gives one, e.g. "Fastest 10-80 % charge with anode potential >= +25 mV and peak temperature <= 45 degC". Each
    constraint is said from its quantity, op, limit and unit, never from its label: a label is free text that an
    amendment of the limit may leave stale."""
    words = (_spec(obj).get("objective_template") or {}).get("summary") or {}
    terms = words.get("terms") or {}
    t, kind = obj.target, objective_kind(obj)
    if kind in ("fastest_feasible", "hard_target") and words.get(kind):
        head = words[kind].format(limit=t.limit, unit=t.unit)
    else:
        verb = "Minimise" if t.direction == "minimize" else "Maximise"
        head = f"{verb} {_lower(quantity_label(obj, t.quantity))}"
        if t.limit is not None:
            head += f" to {'at most' if t.direction == 'minimize' else 'at least'} {t.limit:g} {t.unit}"
    parts = []
    for c in obj.constraints:
        term = terms.get(c.quantity)
        if term:
            parts.append(f"{term['name']} {c.op} {c.limit:+g} {c.unit}" if term.get("signed") else
                         f"{term['name']} {c.op} {c.limit:g} {c.unit}")
        else:
            parts.append(f"{_lower(quantity_label(obj, c.quantity))} {c.op} {c.limit:g} {c.unit}")
    return head + (" with " + " and ".join(parts) if parts else "")


_IN = {"computational": "in simulation", "physical": "on the bench", "retrospective": "in the records"}


def _num(v: float | None, unit: str) -> str:
    return "an unknown value" if v is None else f"{v:g} {unit}"


def _uncertainty(L: Ledger, obj: ObjectiveData, states: dict[str, RequestState], basis: str,
                 best: LoopRecord | None) -> dict:
    """Solver tolerance (numerical: the tolerances the solver ran with, as its result recorded them, and the bounds
    the system declares on its outputs) apart from model uncertainty (the system's own statement, frozen at
    registration; never a number made up here)."""
    spec = _spec(obj)
    template = spec.get("objective_template") or {}
    if basis != "computational":
        return {"solver": None, "model": "Measured: each value carries the uncertainty stated by the person who "
                                         "returned it; no model is involved."}
    st = states.get(best.data["candidate"]["request_id"]) if best is not None else None
    results = [st.result] if st is not None and st.result is not None else \
        [s.result for s in states.values() if s.result is not None and s.result.data["basis"] == "computational"]
    ran = (results[0].data.get("executed") or {}).get("solver") or {} if results else {}

    def number(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None
    compared = list(dict.fromkeys([obj.target.quantity] + [c.quantity for c in obj.constraints]))
    bounds = [f"{q['name']} ±{q['tolerance']:g} {q['unit']}" for name in compared
              for q in spec.get("quantities", []) if q.get("name") == name and q.get("tolerance")]
    note = ("Numerical error of the solver, not the model's error against a cell. " +
            (f"Bounds the system declares on its outputs: {', '.join(bounds)}." if bounds else
             "The system declares no bound on its outputs.") +
            ("" if ran.get("rtol") is not None else " The solver's tolerances were not recorded with the result."))
    model = template.get("model_uncertainty")
    if not model:
        validity = (spec.get("solver") or {}).get("validity")
        model = ("Not quantified: the system stated no model error against measurements when this campaign was "
                 "registered" + (f"; its stated validity: {validity}" if validity else "") +
                 ". Confirm on a physical cell.")
    return {"solver": {"rtol": number(ran.get("rtol")), "atol": number(ran.get("atol")), "note": note},
            "model": model}


def _headline(obj: ObjectiveData, states: dict[str, RequestState], a: dict) -> str:
    """The assessment's one sentence, from loop_compare.assess's answer. A feasibility recovery is never called an
    improvement or faster; results that were compared but none decided are said so, with why."""
    f, best = a["findings"], a["best"]
    fe, sp, ob = f["feasibility"], f["speed"], f["objective"]
    where, unit = _IN.get(a["basis"], ""), obj.target.unit
    st = states.get(best.data["candidate"]["request_id"]) if best is not None else None
    title = request_title(obj, st.data) if st is not None else None
    ref = sp["reference"]
    best_value = (sp["best"] or {}).get("value") if sp["best"] else \
        (best.data["target"]["candidate"] if best is not None else None)
    if best is None:
        return "No result has been compared yet."
    if ob["kind"] == "hard_target" and ob["status"] == "met":
        return (f"Objective met {where}: {title} takes {_num(best_value, unit)}, within the hard target of "
                f"{ob['target_limit']:g} {unit}, and meets every constraint.")
    if sp["status"] == "faster":
        return (f"Faster than the reference {where}: {title} takes {_num(best_value, unit)} against "
                f"{_num(ref['value'], unit)} for {ref['label']}, and meets every constraint.")
    if fe["best"] == "meets":
        return (f"{'Feasibility recovered' if fe['recovered'] else 'Feasible'} {where}: {title} meets every "
                f"constraint{' the baseline breaks' if fe['recovered'] else ''}; " +
                ("speed not compared." if sp["status"] == "not_compared" else
                 f"not shown faster than {ref['label']}."))
    if fe["best"] == "fails" and _unreached(best.data):
        return f"No result is feasible {where}: {title} never reaches its target within its window."
    if fe["best"] == "fails":
        return f"No result meets the constraints {where}."
    n = a["compared"]                    # compared, none decided: not comparable, or within a limit's uncertainty
    why = best.data["reasons"][0] if best.data["reasons"] else \
        "none is shown to meet or to break the constraints within its uncertainty"
    return f"{n} result{'' if n == 1 else 's'}, none decided {where}: {why.rstrip('.')}."


def assessment(L: Ledger, cid: str, obj: ObjectiveData, states: dict[str, RequestState],
               records: list[LoopRecord]) -> dict:
    """Feasibility, speed and the objective as loop-compare/3 separates them (loop_compare.assess), for every
    campaign, with the uncertainty and plain words. A feasibility recovery is never called an improvement or faster."""
    a = assess(L, cid, records)
    f, best, basis = a["findings"], a["best"], a["basis"]
    fe, sp, ob = f["feasibility"], f["speed"], f["objective"]
    unit = obj.target.unit
    st = states.get(best.data["candidate"]["request_id"]) if best is not None else None
    title = request_title(obj, st.data) if st is not None else None
    ref = sp["reference"]
    headline = _headline(obj, states, a)

    lines = []
    v = f"objective version {obj.version}"
    lines.append({"meets": f"The baseline meets the constraints of {v}.",
                  "fails": f"The baseline fails the constraints of {v}.",
                  "unknown": f"The baseline's feasibility under {v} is not known."}[fe["baseline"]])
    if fe["recovered"]:
        lines.append(f"Feasibility recovered: {title} meets the constraints the baseline breaks. That is a "
                     "feasibility result, not a speed result.")
        b_value = best.data["target"].get("baseline") if best is not None else None
        if b_value is not None:
            lines.append(f"The failing baseline takes {_num(b_value, unit)}; it is not a speed reference, because "
                         "it breaks the constraints.")
    if ref is not None:
        lines.append(f"Speed reference: {ref['label']}, {_num(ref['value'], unit)} (constraints of objective "
                     f"version {ref['objective_version']}).")
    lines.append(f"Speed: {sp['reason']}." if sp["reason"] else f"Speed: {sp['status'].replace('_', ' ')}.")
    if ob["kind"] == "hard_target":
        lines.append(f"Hard target: {obj.target.quantity} at most {ob['target_limit']:g} {unit}, "
                     f"{ob['status'].replace('_', ' ')}. Only an engineer's amendment changes it.")
    elif ob["kind"] == "fastest_feasible":
        lines.append("Objective open: the fastest protocol meeting the constraints is asked for; no result is "
                     "proven to be the fastest possible.")
    else:
        lines.append(f"Assessed by the loop-compare/3 rules; this campaign's own comparisons and conclusion keep the "
                     f"words its {obj.evaluator} rules recorded.")
    unc = _uncertainty(L, obj, states, basis, best)
    if unc["solver"] is not None:
        lines.append("Solver: " + unc["solver"]["note"])
    lines.append("Model: " + unc["model"])
    return {"basis": basis, "feasibility": fe, "speed": sp, "objective": ob, "uncertainty": unc,
            "headline": headline, "lines": lines}


# ------------------------------------------------------------------ the view

def view(L: Ledger, cid: str, source: dict, *, ledger: str | None = None) -> dict:
    """The LoopView of one campaign. `source` is {"kind": "live", "api", "ledger"} (as_of_seq and generated_at are
    filled in) or a complete recorded source; `ledger` names the ledger in artifact URLs (default: source's)."""
    recs = chain.records(L, cid)
    obj, states = current_objective(L, cid, recs), request_states(L, cid, recs)
    campaign = L.get("campaign", cid)
    ledger = ledger or source.get("ledger") or L.root.name
    cursor = events_cursor(L, cid)
    src = dict(source)
    if src.get("kind") == "live":
        src = {"kind": "live", "api": src.get("api", ""), "ledger": src.get("ledger") or ledger, "as_of_seq": cursor,
               "generated_at": src.get("generated_at") or time.time()}

    comparisons = [r for r in recs if r.type == "comparison"]
    compared = {}                                     # result id -> its latest comparison
    for r in comparisons:
        compared[r.data["candidate"]["result_id"]] = r
    done = next((r for r in recs if r.type == "conclusion"), None)
    current = None
    if done is not None and done.data["comparison_id"]:
        current = next((r for r in comparisons if r.id == done.data["comparison_id"]), None)
    elif done is None:
        current = next((r for r in reversed(comparisons) if r.data["objective_version"] == obj.version), None)
    current_result = None
    if current is not None:
        st = states.get(current.data["candidate"]["request_id"])
        title = request_title(obj, st.data) if st else current.data["candidate"]["request_id"]
        current_result = {"comparison_id": current.id, "request_id": current.data["candidate"]["request_id"],
                          "verdict": current.data["verdict"], "basis": current.data["basis"],
                          "sentence": sentence(obj, current, title)}

    spec, solver = _spec(obj), _spec(obj).get("solver")
    models = obj.planner.get("models")
    if not isinstance(models, dict):
        agent = next((r.actor for r in recs if actor_kind(r.actor) == "agent"), None)
        models = {"planner": agent.split(":", 1)[1] if agent else None, "vision": None}
    target = {"quantity": obj.target.quantity, "unit": obj.target.unit, "direction": obj.target.direction,
              "margin": obj.target.margin, "label": quantity_label(obj, obj.target.quantity)}
    if obj.target.limit is not None:
        target["limit"] = obj.target.limit
    derived = _derived(L, cid)
    return {
        "view_version": VIEW_VERSION,
        "source": src,
        "campaign": {"id": cid, "title": campaign.title, "created_at": campaign.created_at,
                     "status": status(states, done is not None), "memory": campaign.memory,
                     "system": {"id": obj.system["id"], "version": obj.system["version"],
                                "title": spec.get("title") or obj.system["id"],
                                "solver": {"name": str(solver.get("name", "")), "version": str(solver.get("version", "")),
                                           "validity": str(solver.get("validity", ""))} if solver else None},
                     "planner": {"kind": str(obj.planner.get("kind", "")), "models": dict(models)}},
        "objective": {"version": obj.version, "statement": obj.statement, "target": target,
                      "constraints": [c.model_dump(mode="json") for c in obj.constraints],
                      "amendments": _amendments(recs),
                      "kind": objective_kind(obj), "summary": objective_summary(obj),
                      "statement_version": 1},           # the statement is registered once; amendments never change it
        "assessment": assessment(L, cid, obj, states, recs),
        "baseline": _baselines(L, cid, obj, states),
        "current_result": current_result,
        "next_action": next_action(obj, states, recs, done is not None),
        "requests": [_request(L, obj, s, ledger, derived, compared) for s in states.values()],
        "comparisons": [_comparison(r) for r in comparisons],
        "measurements_defined": [{"name": r.data["name"], "op": r.data["spec"]["op"], "unit": r.data["spec"]["unit"],
                                  "defined_by": r.data["defined_by"]} for r in recs if r.type == "measurement"],
        "spend": {"model_usd": model_spend(L, cid), "model_budget_usd": float(obj.budget.get("model_usd", 0.0)),
                  "experiments_used": experiments_used(states),
                  "experiments_budget": obj.budget.get("experiments", 0)},
        "conclusion": {"outcome": done.data["outcome"], "comparison_id": done.data["comparison_id"],
                       "text": done.data["text"], "unresolved_reasons": list(done.data["unresolved_reasons"]),
                       **({"findings": done.data["findings"]} if done.data.get("findings") is not None else {})}
        if done else None,
        "events_cursor": cursor,
        **_learning(L, cid, obj, states),
    }


def _learning(L: Ledger, cid: str, obj: ObjectiveData, states: dict) -> dict:
    """{"learning": ...} when the campaign's system keeps model versions (adapter.campaign_summary), else {}."""
    try:
        summary = getattr(adapter_for(obj), "campaign_summary", None)
    except KeyError:
        return {}
    if summary is None:
        return {}
    out = {"learning": summary(L, cid, states)}
    if getattr(obj, "uploads", None):     # an investigation from the engineer's records: what the planner sees of it
        from translucent import records
        out["investigation"] = records.investigation(L, cid, obj, states=states)
    return out


def summary_fields(L: Ledger, cid: str, records: list[LoopRecord] | None = None) -> dict:
    """The CampaignSummary fields the list takes from the loop semantics: "objective", the current version's one-line
    summary (objective_summary, cut to 140 characters; the registered statement stays in the view); and, once the
    campaign is concluded, "assessment_headline" (the view's assessment headline) and "findings" (basis,
    feasibility, speed status and objective status), so a list never shows a feasibility recovery as "improved"."""
    recs = chain.records(L, cid) if records is None else records
    obj = current_objective(L, cid, recs)
    out = {"objective": objective_summary(obj)[:140]}
    if any(r.type == "conclusion" for r in recs):
        a = assess(L, cid, recs)
        f = a["findings"]
        out |= {"assessment_headline": _headline(obj, request_states(L, cid, recs), a),
                "findings": {"basis": a["basis"],
                             "feasibility": {k: f["feasibility"][k] for k in ("baseline", "best", "recovered")},
                             "speed": {"status": f["speed"]["status"]},
                             "objective": {k: f["objective"][k] for k in ("kind", "status")}}}
    return out


def summary(L: Ledger, cid: str, ledger: str) -> dict:
    """The CampaignSummary of one loop campaign, for the campaign list."""
    recs = chain.records(L, cid)
    obj, states = current_objective(L, cid, recs), request_states(L, cid, recs)
    campaign = L.get("campaign", cid)
    done = next((r for r in recs if r.type == "conclusion"), None)
    return {"id": cid, "ledger": ledger, "kind": "loop", "title": campaign.title,
            "status": status(states, done is not None), "created_at": campaign.created_at,
            "updated_at": _last_event_t(L, cid) or campaign.created_at,
            "next_action": next_action(obj, states, recs, done is not None)["kind"],
            "outcome": done.data["outcome"] if done else None, "spend_usd": model_spend(L, cid),
            **summary_fields(L, cid, recs)}
