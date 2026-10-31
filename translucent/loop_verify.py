"""Offline verify of loop campaigns (design §2.14). verify._ledger calls `check` on its scratch snapshot, after the
intervention checks; ledgers without loop records are untouched, so their summaries stay byte-identical.

Per campaign:
  * the hash chain: digests, links, canonical payloads, each record's mirror event (chain.problems), and records
    stored in the order they were chained;
  * the objective first and once; nothing but planner receipts after the conclusion;
  * every record replayed through the same admission hooks that admitted it, against the ledger as it stood when it
    was written: the records are inserted again, in the order they were written, into an in-memory ledger, and each
    loop record's hooks run just before it goes in. This re-checks every transition of the request lifecycle,
    approval and result hashes against the bound version, the people-only decisions, each comparison against
    the objective's evaluator and the conclusion's outcome against select_best. Locks are never evidence, so verify
    holds the claim locks itself in its scratch folder while replaying runner records;
  * each result re-checked from its stored blobs: units and finite, ordered series; the executed configuration within
    the approved version's tolerances; for a solver result, its output.json is the one the run hashed, and the
    recorded measurements and series are that output normalized again, bit for bit;
  * each ops/1 value recomputed from its series and compared bit for bit (a "not reached" answer included);
  * the system adapter's own evidence checks, when the adapter is installed; otherwise a note says they were skipped.

Verify never re-runs a solver.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from types import SimpleNamespace

from pydantic import ValidationError

from translucent import chain, systems, units
from translucent.executors import configuration_problems
from translucent.ledger import SCHEMA, AdmissionError, Ledger
from translucent.locks import ExclusiveFileLock
from translucent.loop import OPS_DIGEST, RequestState, claim_path, current_objective, request_states
from translucent.loop_compare import measure, series
from translucent.loop_contracts import LoopRecord, ObjectiveData, OperatorSpec

_RUNNER_TYPES = frozenset({"execution", "result", "failure"})


def campaign_ids(L: Ledger, campaign_id: str | None = None) -> list[str]:
    """Every campaign with loop records (an objective or not), in the order they were written."""
    rows = L.db.execute("SELECT campaign_id FROM record WHERE kind='loop' ORDER BY rowid")
    return [cid for cid in dict.fromkeys(c for (c,) in rows) if campaign_id is None or cid == campaign_id]


_ERRORS = (ValueError, KeyError, TypeError, AttributeError, LookupError, ValidationError)


def check(L: Ledger, result, campaign_id: str | None = None):
    checked = {}                                      # campaign id -> its system is installed here
    for cid in campaign_ids(L, campaign_id):
        try:
            if (installed := _chain(L, result, cid)) is not None:
                checked[cid] = installed
        except _ERRORS as e:
            result.problems.append(f"Loop campaign {cid} could not be re-checked: {type(e).__name__}: {e}")
    if not checked:
        return
    _replay(L, result, checked)
    for cid in checked:
        try:
            _evidence(L, result, cid)
        except _ERRORS as e:
            result.problems.append(f"Loop campaign {cid} could not be re-checked: {type(e).__name__}: {e}")


def _chain(L: Ledger, result, cid: str) -> bool | None:
    """The chain and record order of one campaign. Returns whether the code it registered is installed; None when
    the records are not a campaign (no objective first)."""
    problems = result.problems
    problems += chain.problems(L, cid)
    recs = chain.records(L, cid)
    if recs[0].type != "objective" or any(r.type == "objective" for r in recs[1:]):
        problems.append(f"Loop campaign {cid} does not start with its one objective")
        return None
    rows = L.db.execute("SELECT id FROM record WHERE kind='loop' AND campaign_id=? ORDER BY rowid", (cid,))
    if [rid for (rid,) in rows] != [r.id for r in recs]:
        problems.append(f"Loop records of {cid} are not stored in the order they were chained")
    done = next((r for r in recs if r.type == "conclusion"), None)
    if done is not None and any(r.seq > done.seq and r.type not in chain.AFTER_CONCLUSION for r in recs):
        problems.append(f"Loop campaign {cid} has records after its conclusion at seq {done.seq}")
    system = current_objective(L, cid, recs).system
    try:
        adapter = systems.get(system.get("id"))
    except KeyError:
        return False
    return adapter.code_digest() == system.get("code_digest")     # other code would judge old requests by new rules


def _evidence(L: Ledger, result, cid: str):
    """Results from their blobs, ops/1 values, and the system adapter's own checks."""
    problems = result.problems
    recs = chain.records(L, cid)
    obj = current_objective(L, cid, recs)
    sid = obj.system.get("id")
    try:
        adapter = systems.get(sid)
    except KeyError:
        adapter = None
    states = request_states(L, cid, recs)
    result.requests += len(states)
    for st in states.values():
        if st.result is not None:
            problems += [f"Result {st.result.id}: {p}" for p in _result_problems(L, obj, st)]
    problems += _operator_problems(L, result, cid, obj, states)
    if adapter is None:
        result.notes.append(f"System {sid} is not installed; domain checks of {cid} skipped")
    elif adapter.code_digest() != obj.system.get("code_digest"):
        result.notes.append(f"System {sid} installed here differs from the code {cid} registered; "
                            "domain checks skipped")
    else:
        problems += [f"Loop campaign {cid}: {p}" for p in adapter.evidence_problems(L, cid)]


# ------------------------------------------------------------------ admission, replayed

def _replay(L: Ledger, result, campaigns: dict[str, bool]):
    """Insert the ledger's records again, in the order they were written, into an in-memory ledger; just before each
    loop record of `campaigns` goes in, run its admission hooks there, so each sees the ledger as it stood."""
    R = Ledger.__new__(Ledger)
    R.root, R.blobs = L.root, L.blobs
    R.db = sqlite3.connect(":memory:", isolation_level=None)
    skipped = dict.fromkeys(campaigns, 0)
    try:
        R.db.executescript(SCHEMA)
        marks = ",".join("?" * len(campaigns))
        (last,) = L.db.execute(f"SELECT COALESCE(MAX(rowid), 0) FROM record WHERE kind='loop' AND campaign_id IN "
                               f"({marks})", list(campaigns)).fetchone()
        rows = L.db.execute("SELECT id, kind, campaign_id, ref, body, inserted_at FROM record WHERE rowid<=? "
                            "ORDER BY rowid", (last,))
        for row in rows:
            if row[1] == "loop" and row[2] in campaigns:
                skipped[row[2]] += _admit_again(R, result, LoopRecord.model_validate_json(row[4]), campaigns[row[2]])
            R.db.execute("INSERT INTO record VALUES (?,?,?,?,?,?)", row)
    finally:
        R.db.close()
    for cid, n in skipped.items():
        if n:
            result.notes.append(f"{n} requests of {cid} were checked without their system's parameter rules "
                                "(the code the campaign registered is not installed)")


def _admit_again(R: Ledger, result, rec: LoopRecord, adapter_installed: bool) -> int:
    """Run the record's admission hooks against R; 1 when a request's adapter checks had to be skipped."""
    try:
        model = chain.typed(rec)
    except ValidationError:
        return 0                                       # chain.problems names it
    hooks, skipped = chain.hooks(rec.type), 0
    if rec.type == "request" and not adapter_installed:
        hooks, skipped = [], 1                         # its parameters are the adapter's to check
        if model.content_hash != chain.content_hash(model):
            result.problems.append(f"Loop record {rec.id}: the content hash does not match the request")
    lock = ExclusiveFileLock(claim_path(R.root, rec.data["request_id"])) \
        if rec.type in _RUNNER_TYPES and isinstance(rec.data.get("request_id"), str) else None
    if lock is not None:
        lock.try_acquire()
    try:
        for hook in hooks:
            hook(R, rec, model)
    except AdmissionError as e:
        result.problems.append(f"Loop record {rec.id} ({rec.type}, seq {rec.seq}) was not admissible when written: "
                               f"{e}")
    except _ERRORS as e:
        result.problems.append(f"Loop record {rec.id} ({rec.type}, seq {rec.seq}) could not be re-checked: "
                               f"{type(e).__name__}: {e}")
    finally:
        if lock is not None:
            lock.release()
    return skipped


# ------------------------------------------------------------------ results, from their blobs

def _blob_json(L: Ledger, aid: str):
    return json.loads(L.blob_path(aid).read_text(encoding="utf-8"))


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _result_problems(L: Ledger, obj: ObjectiveData, st: RequestState) -> list[str]:
    spec = obj.system.get("spec") or {}
    canonical = {q["name"]: q["unit"] for q in spec.get("quantities", [])}
    action = next((a for a in spec.get("actions", []) if a.get("name") == st.action), None)
    r, out = st.result.data, []
    run = L.get("run", r["run_id"])
    if run is None or action is None:
        return ["its run or its action is not recorded"]
    ms = {q: L.get("measurement", mid) for q, mid in r["measurements"].items()}
    for q, m in ms.items():
        if m is None:
            out.append(f"measurement of {q} is missing")
        elif m.unit != canonical.get(q) or not _finite(m.value):
            out.append(f"{q} is {m.value!r} {m.unit}; results are finite numbers in the system's unit "
                       f"{canonical.get(q)}")
    named = {q["name"]: q.get("categories") or [] for q in spec.get("quantities", []) if q.get("kind") == "category"}
    for q, v in r.get("categories", {}).items():
        if v not in named.get(q, []):
            out.append(f"{q} is {v!r}; the system names {', '.join(named.get(q, [])) or 'no such category'}")
    bodies = {}
    for q, aid in r["series"].items():
        try:
            body = bodies[q] = _blob_json(L, aid)
            t, y = body["t"], body["y"]
            if body["unit"] != canonical.get(q) or len(t) != len(y) or not all(map(_finite, t + y)) or \
                    any(b < a for a, b in zip(t, t[1:])):
                out.append(f"the {q} series is not finite and time-ordered in {canonical.get(q)}")
        except (OSError, ValueError, KeyError, TypeError) as e:
            out.append(f"the {q} series cannot be read ({type(e).__name__})")
    version = next((v for v in st.versions if v.data["content_hash"] == r["content_hash"]), None)
    if version is not None:
        params = SimpleNamespace(parameters=[SimpleNamespace(**p) for p in action.get("parameters", [])])
        out += configuration_problems(params, dict(version.data["params"]), dict(r["executed"]["configuration"]))
    if r["basis"] == "computational":
        out += _solver_output_problems(L, r, run, canonical, ms, bodies, set(named))
    return out


def _solver_output_problems(L: Ledger, r: dict, run, canonical: dict, ms: dict, bodies: dict,
                            category_names: set[str]) -> list[str]:
    """The solver's answer (output.json, whose sha256 the run recorded) normalized again: the same series and
    values as recorded, bit for bit."""
    aid = r["files"].get("output.json")
    if aid is None:
        return ["the solver's output.json is not recorded"]
    raw = L.blob_path(aid).read_bytes()
    if hashlib.sha256(raw).hexdigest() != run.params.get("payload_sha256"):
        return ["the stored output.json is not the solver output the run hashed"]
    try:
        table = json.loads(raw)["output"]["table"]
        out = []
        if table.get("series"):
            t = units.convert(list(table["t"]), table["time_unit"], "s")
            for q, (unit, values) in table["series"].items():
                body = bodies.get(q)
                if body is None or body["t"] != t or body["y"] != units.convert(list(values), unit, canonical[q]):
                    out.append(f"the {q} series differs from the solver's output")
        for q, (value, unit) in table.get("scalars", {}).items():
            if q in category_names:
                if r.get("categories", {}).get(q) != value:
                    out.append(f"{q} differs from the solver's output")
                continue
            m = ms.get(q)
            if m is None or m.value != units.convert(value, unit, canonical[q]):
                out.append(f"{q} differs from the solver's output")
        return out
    except (ValueError, KeyError, TypeError) as e:
        return [f"the solver's output cannot be normalized again ({type(e).__name__}: {e})"]


# ------------------------------------------------------------------ ops/1 values

def _operator_problems(L: Ledger, result, cid: str, obj: ObjectiveData, states: dict[str, RequestState]) -> list[str]:
    out, other_code = [], False
    defined = {r.id: r for r in chain.records(L, cid) if r.type == "measurement"}
    for run in L.list("run", campaign_id=cid):
        if not run.tool.startswith("operator:"):
            continue
        what = f"Operator run {run.id}"
        params = dict(run.params)
        ref, window = params.pop("target", None), params.pop("not_reached_within", None)
        try:
            spec = OperatorSpec.model_validate(params)
        except ValidationError:
            out.append(f"{what} has no valid ops/1 spec")
            continue
        definition = defined.get(run.step_id)
        if definition is None or definition.data["spec"] != spec.model_dump(mode="json") or \
                run.tool != f"operator:{spec.op}":
            out.append(f"{what} is not an application of a measurement defined in {cid}")
            continue
        other_code |= run.code_sha != OPS_DIGEST
        found = series(L, obj, ref, spec.channel, states) if isinstance(ref, str) else None
        if found is None or run.input_ids != [found[0]]:
            out.append(f"{what}: its series is not {ref}'s {spec.channel}")
            continue
        try:
            m = measure(spec, found[1])
        except (ValueError, NotImplementedError) as e:
            out.append(f"{what}: ops/1 refuses its series now ({e})")
            continue
        if m["value"] is None:
            if run.measurement_ids or window != m["not_reached_within"]:
                out.append(f"{what}: ops/1 gives not reached within {m['not_reached_within']} s; the run records "
                           f"otherwise")
            continue
        meas = L.get("measurement", run.measurement_ids[0]) if run.measurement_ids and len(run.measurement_ids) == 1 \
            else None
        if window is not None or meas is None:
            out.append(f"{what}: ops/1 gives {m['value']!r} {m['unit']}; the run records no value")
        elif meas.value != m["value"] or meas.unit != m["unit"] or \
                meas.uncertainty.model_dump(mode="json") != m["uncertainty"] or meas.subject != ref or \
                meas.quantity != f"derived:{definition.data['name']}":
            out.append(f"Measurement {meas.id} ({meas.quantity} of {ref}) differs from ops/1 recomputed from its "
                       f"series: recorded {meas.value!r} {meas.unit}, recomputed {m['value']!r} {m['unit']}")
    if other_code:
        result.notes.append(f"Some ops/1 values of {cid} were recorded by other operator code than this "
                            "verify's; recomputed with this one")
    return out
