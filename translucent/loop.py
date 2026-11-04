"""Loop campaigns: opening one, the request lifecycle folded from insert-only records, and per-type admission.

A request's state is never stored. `request_states` folds the campaign's loop records in seq order:

  request v1                  -> awaiting_approval ("proposed" is the call itself; it is never recorded)
  request v(n+1), supersedes  -> the old version superseded, the new one awaiting_approval
  approval                    -> queued (computational) or awaiting_result (human)
  rejection / cancellation    -> rejected / cancelled
  execution(started, n)       -> running
  execution(abandoned, n)     -> queued when the action is deterministic and n < 3; otherwise a failure follows
                                 in the same transaction
  result / failure            -> completed / failed
  result_rejected             -> no change: the refused upload is listed and a corrected one may follow

Every transition is checked by the admission hooks below, inside chain.append's BEGIN IMMEDIATE, against the state
re-derived in that transaction, so check-then-act is atomic across processes (design §2.5, §2.10). chain.HOOK_MODULES
names this module, so a process that writes loop records without importing it still runs every check. The hooks
are also what keeps people's decisions with people: an agent actor can never approve, reject, amend the objective,
submit a physical result or record execution.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
import secrets
import time
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from translucent import chain, operators, systems, units
from translucent.contracts import Campaign, Uncertainty
from translucent.ledger import AdmissionError, Ledger
from translucent.locks import ExclusiveFileLock
from translucent.loop_contracts import (AmendmentData, ApprovalData, CancellationData, ConclusionData,
                                        DatasetRecord, ExecutionData, FailureData, LoopRecord, MeasurementData,
                                        ObjectiveData, OperatorSpec, RejectionData, RequestData, ResultData,
                                        ResultRejectedData)
from translucent.systems.base import SystemAdapter, file_digest

MAX_ATTEMPTS = 3
PENDING = frozenset({"awaiting_approval", "queued", "running", "awaiting_result"})
REQUEST_ID = re.compile(r"^rq_[0-9a-f]{20}$")
REGISTRATION = "policy:registration"
REFERENCE = "policy:reference"       # the reference search's runs (translucent.loop_reference), loop-compare/3
SYSTEM_RUNS = frozenset({REGISTRATION, REFERENCE})    # requests written by policy: never budgeted, never candidates
RUNNER = "system:runner"
AMENDED = "system:objective"         # withdraws proposals an amendment left waiting under an older objective version
RESULTS = "system:results"           # records a person's refused upload
_REQUEST_TYPES = frozenset({"request", "approval", "rejection", "cancellation", "execution", "result",
                            "result_rejected", "failure"})


class NotAllowed(PermissionError):
    """This actor may not do this (HTTP 403)."""


class NotFound(LookupError):
    """No such campaign, request or record (HTTP 404)."""


class StaleRequest(AdmissionError):
    """An approval or rejection names a version that is no longer the latest (HTTP 409)."""


class DuplicateRequest(AdmissionError):
    """A pending request already has this configuration; the service returns it instead."""

    def __init__(self, request_id: str):
        super().__init__(f"request {request_id} with the same configuration is still pending")
        self.request_id = request_id


def actor_kind(actor: str) -> str:
    return actor.split(":", 1)[0]


def actor_name(actor: str) -> str:
    return actor.split(":", 1)[1] if ":" in actor else actor


def _authored_by(author: str, actor: str) -> bool:
    return author in (actor, actor_name(actor))


# ------------------------------------------------------------------ the objective

def campaign_ids(L: Ledger) -> list[str]:
    """Loop campaigns of the ledger, oldest first."""
    rows = L.db.execute("SELECT campaign_id FROM record WHERE kind='loop' AND json_extract(body,'$.type')='objective' "
                        "ORDER BY inserted_at, rowid")
    return [cid for (cid,) in rows]


def current_objective(L: Ledger, cid: str, records: list[LoopRecord] | None = None) -> ObjectiveData:
    """The objective with every amendment applied; `version` is the latest amendment's."""
    obj = None
    for rec in chain.records(L, cid) if records is None else records:
        if rec.type == "objective":
            obj = ObjectiveData.model_validate(rec.data)
        elif rec.type == "amendment" and obj is not None:
            a = AmendmentData.model_validate(rec.data)
            update = {"version": a.version}
            if a.target is not None:
                update["target"] = a.target
            if a.constraints is not None:
                update["constraints"] = a.constraints
            if a.budget is not None:          # M2 (AUTH): a budget amendment, merged over the budget
                update["budget"] = {**obj.budget, **a.budget}
            obj = obj.model_copy(update=update)
    if obj is None:
        raise NotFound(f"{cid} is not a loop campaign")
    return obj


def adapter_for(obj: ObjectiveData) -> SystemAdapter:
    return systems.get(obj.system["id"])


def system_snapshot(adapter: SystemAdapter) -> dict:
    """What the objective freezes about the system: id, version, code digest and the whole spec as JSON."""
    spec = json.loads(json.dumps(dataclasses.asdict(adapter.spec)))
    if spec.get("objective_template") is None:      # milestone 2's field: a system without one freezes as before
        spec.pop("objective_template", None)
    return {"id": adapter.spec.id, "version": adapter.spec.version, "code_digest": adapter.code_digest(),
            "spec": spec}


def registered_executor_digest(obj: ObjectiveData) -> str:
    """The executor digest of the code the objective froze (executors.executor_digest of that adapter): a request is
    run on exactly that code, or not at all."""
    return chain.digest({"adapter": obj.system.get("code_digest"),
                         "solver": (obj.system.get("spec") or {}).get("solver") or {}})


def _spec_action(obj_or_spec: ObjectiveData | dict, name: str) -> dict | None:
    spec = obj_or_spec.system.get("spec") if isinstance(obj_or_spec, ObjectiveData) else obj_or_spec
    return next((a for a in (spec or {}).get("actions", []) if a.get("name") == name), None)


def _spec_quantity(obj: ObjectiveData, name: str) -> dict | None:
    return next((q for q in obj.system.get("spec", {}).get("quantities", []) if q.get("name") == name), None)


def objective_problems(L: Ledger, obj: ObjectiveData, adapter: SystemAdapter) -> list[str]:
    """Registration checks: every quantity the target and constraints use is defined, units agree with the
    system, datasets and baseline records exist, and the budget is stated."""
    out = []
    defined = {q.name: q for q in obj.quantities}
    for q in obj.quantities:
        sq = next((x for x in adapter.spec.quantities if x.name == q.name), None)
        try:
            if q.source == "result":
                if sq is None or sq.kind != "scalar":
                    out.append(f"quantity {q.name}: the system produces no scalar of that name")
                elif not units.same_dimension(q.unit, sq.unit):
                    out.append(f"quantity {q.name}: {q.unit} does not measure the same thing as {sq.unit}")
            elif q.operator is None:
                out.append(f"quantity {q.name}: an operator quantity needs its operator")
            elif (ch := next((x for x in adapter.spec.quantities if x.name == q.operator.channel), None)) is None \
                    or ch.kind != "series":
                out.append(f"quantity {q.name}: {q.operator.channel} is not a series of the system")
            elif q.operator.unit != (derived := units.derive(q.operator.op, ch.unit)) or q.unit != derived:
                out.append(f"quantity {q.name}: {q.operator.op} of {ch.unit} is in {derived}")
        except ValueError as e:
            out.append(f"quantity {q.name}: {e}")
    for what, quantity, unit in [("target", obj.target.quantity, obj.target.unit)] + \
            [("constraint", c.quantity, c.unit) for c in obj.constraints]:
        if quantity not in defined:
            out.append(f"{what} {quantity} is not defined in the objective's quantities")
            continue
        try:
            if not units.same_dimension(unit, defined[quantity].unit):
                out.append(f"{what} {quantity}: {unit} does not measure the same thing as {defined[quantity].unit}")
        except ValueError as e:
            out.append(f"{what} {quantity}: {e}")
    for did in obj.datasets:
        if L.get("dataset", did) is None:
            out.append(f"dataset {did} is not imported in this ledger")
    for ref in obj.baseline:
        if _baseline_record(L, obj, ref) is None:
            out.append(f"baseline {ref} is not a baseline record of the objective's datasets")
    n = obj.budget.get("experiments")
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        out.append("budget.experiments must be a whole number of experiments, 0 or more")
    if not obj.registered_by.strip():
        out.append("registered_by names the person who registers the objective")
    from translucent.records import MAX_DOC_CHARS
    doc = getattr(obj, "documentation", None)
    if isinstance(doc, str) and len(doc) > MAX_DOC_CHARS:
        out.append(f"documentation is {len(doc)} characters; the planner reads at most {MAX_DOC_CHARS}: shorten it "
                   "or upload it as a file")
    # a campaign its planner could never plan is refused here, rather than left running with nothing to say why
    kind = obj.planner.get("kind")
    if kind == "domain_baseline" and adapter.baseline_policy() is None:
        out.append(f"{adapter.spec.id} has no baseline policy for the domain baseline planner to run; choose an "
                   "agent planner")
    if kind in ("muse", "muse_no_feedback"):
        usd = obj.budget.get("model_usd")
        if isinstance(usd, bool) or not isinstance(usd, (int, float)) or not usd > 0:
            out.append("a model planner needs a model budget (budget.model_usd) above 0")
    return out


def _baseline_record(L: Ledger, obj: ObjectiveData, ref: str):
    did, _, rid = ref.partition("/")
    ds = L.get("dataset", did) if did in obj.datasets else None
    rec = next((r for r in ds.records if r.id == rid), None) if isinstance(ds, DatasetRecord) else None
    return rec if rec is not None and rec.role == "baseline" else None


def _new_campaign_id(key: str | None) -> str:
    return "loop_" + (chain.digest(["campaign", key])[:16] if key else secrets.token_hex(8))


def open_campaign(L: Ledger, objective: ObjectiveData | dict, title: str, *, actor: str, memory: str = "on",
                  key: str | None = None) -> tuple[str, LoopRecord, bool]:
    """Register a loop campaign: its Campaign record and the objective as the chain's first record, one
    transaction. `objective.system` may name only the id; the adapter's version, code digest and spec are frozen
    into it. With `baseline_simulation`, each baseline configuration is also requested on the solver and approved
    under the registration rule, so computational candidates meet a computational baseline. Opening again with the
    same idempotency key and objective returns the existing campaign; the flag says whether this call created it."""
    data = objective.model_dump(mode="json") if isinstance(objective, ObjectiveData) else dict(objective)
    system = data.get("system")
    sid = system if isinstance(system, str) else (system or {}).get("id")
    try:
        adapter = systems.get(sid)
    except KeyError as e:
        raise AdmissionError(str(e.args[0])) from None
    snapshot = system_snapshot(adapter)
    if isinstance(system, dict):
        for k in ("version", "code_digest"):
            if system.get(k) not in (None, snapshot[k]):
                raise AdmissionError(f"the objective names {sid} {k} {system[k]}; the registered adapter has "
                                     f"{snapshot[k]}")
    data["system"] = snapshot
    if data.get("evaluator", "loop-compare/3") == "loop-compare/3" and data.get("kind") is None:
        target = data.get("target")             # the objective records its kind: a target limit is a hard target
        data["kind"] = "hard_target" if isinstance(target, dict) and target.get("limit") is not None \
            else "fastest_feasible"
    obj = chain.PAYLOADS["objective"].model_validate(chain.payload("objective", data))
    if problems := objective_problems(L, obj, adapter):
        raise AdmissionError("; ".join(problems))
    cid = _new_campaign_id(key)
    with L.transaction():
        if L.get("campaign", cid) is None:
            L.add(Campaign(id=cid, objective=obj.statement, title=title, inputs=[], memory=memory,
                           budget_usd=float(obj.budget.get("model_usd", 0.0)), created_at=time.time()))
        rec, created = chain.write(L, cid, "objective", obj, actor, chain.natural_id("obj", cid))
        if obj.baseline_simulation:
            for ref in obj.baseline:
                _register_baseline_run(L, cid, obj, adapter, ref)
        if created:                             # loop-compare/3: the reference search's first step, pre-approved
            from translucent import loop_reference
            loop_reference.queue(L, cid)
    return cid, rec, created


def baseline_request_id(cid: str, ref: str) -> str:
    """The request id of a baseline record's registration re-run on the solver."""
    return "rq_" + chain.digest([cid, "baseline", ref])[:20]


def _register_baseline_run(L: Ledger, cid: str, obj: ObjectiveData, adapter: SystemAdapter, ref: str):
    record = _baseline_record(L, obj, ref)
    config = dict(record.configuration)
    for action in adapter.spec.actions:
        names = {p.name for p in action.parameters}
        if action.executor != "computational" or not names <= set(config):
            continue
        try:
            params = {p.name: (units.convert(config[p.name], record.configuration_units[p.name], p.unit)
                               if p.name in record.configuration_units and not isinstance(config[p.name], str)
                               else config[p.name]) for p in action.parameters}
        except ValueError as e:
            raise AdmissionError(f"baseline {ref}: {e}") from None
        if adapter.check_params(action.name, params):
            continue
        req = build_request(adapter, obj, action.name, params,
                            request_id=baseline_request_id(cid, ref),
                            rationale=f"Re-run the baseline configuration of {ref} on the solver, so computational "
                                      "candidates are compared with a computational baseline.",
                            prediction={}, expected_effect="Gives the computational baseline; no decision depends "
                                                           "on it alone.")
        rec = chain.append(L, cid, "request", req, REGISTRATION, chain.natural_id("req", cid, req.request_id, 1))
        chain.append(L, cid, "approval", ApprovalData(request_id=req.request_id, record_id=rec.id,
                                                      content_hash=req.content_hash, author=obj.registered_by,
                                                      note=f"Pre-approved at registration ({ref})",
                                                      rule="registration"),
                     REGISTRATION, chain.natural_id("apr", req.request_id, req.content_hash))
        return
    raise AdmissionError(f"baseline {ref}: no computational action of {adapter.spec.id} runs its configuration")


# ------------------------------------------------------------------ building a request version

def build_request(adapter: SystemAdapter, obj: ObjectiveData, action: str, params: dict, *, request_id: str,
                  rationale: str, prediction: dict, expected_effect: str, evidence_ids=(), specimen=None,
                  version: int = 1, supersedes: str | None = None, due_by: float | None = None) -> RequestData:
    """A complete request version: units, configuration hash, cost and procedure come from the adapter, never
    from the proposer, and the content hash covers everything the approver will see."""
    try:
        spec = adapter.spec.action(action)
    except KeyError as e:
        raise AdmissionError(str(e.args[0])) from None
    if problems := adapter.check_params(action, params):
        raise AdmissionError("; ".join(problems))
    body = dict(request_id=request_id, version=version, supersedes=supersedes, action=action,
                executor=spec.executor, params=params, units={p.name: p.unit for p in spec.parameters},
                configuration_hash=chain.digest(adapter.configuration(action, params)), specimen=specimen,
                rationale=rationale, prediction=prediction, expected_effect=expected_effect,
                cost=json.loads(json.dumps(dataclasses.asdict(adapter.estimate_cost(action, params)))),
                evidence_ids=list(evidence_ids), objective_version=obj.version,
                procedure=adapter.procedure(action, params) if spec.executor == "human" else None, due_by=due_by)
    return RequestData.model_validate(chain.payload("request", body | {"content_hash": chain.content_hash(body)}))


def bind_params(L, cid: str, obj: ObjectiveData, adapter: SystemAdapter, action: str, params: dict) -> dict:
    """The proposer's parameters plus those the system derives from the ledger and the objective in force (a model
    version's definition, the lab tests a fit uses, the objective's limits). Admission derives them again."""
    bind = getattr(adapter, "bind", None)
    if bind is None:
        return params
    try:
        return bind(L, cid, obj, request_states(L, cid), action, params)
    except ValueError as e:
        raise AdmissionError(str(e)) from None


def request_problems(adapter: SystemAdapter, obj: ObjectiveData, req: RequestData) -> list[str]:
    """Everything about one request version that the adapter and the objective decide."""
    try:
        spec = adapter.spec.action(req.action)
    except KeyError as e:
        return [str(e.args[0])]
    out = []
    if req.executor != spec.executor:
        out.append(f"{req.action} runs on the {spec.executor} executor, not {req.executor}")
    if spec.executor == "retrospective":
        out.append(f"{req.action} is retrospective: its results come from a sealed store, not a request")
    if problems := adapter.check_params(req.action, req.params):
        return out + problems
    if req.units != {p.name: p.unit for p in spec.parameters}:
        out.append("parameter units must be the system's: " + ", ".join(f"{p.name} in {p.unit}"
                                                                         for p in spec.parameters))
    if req.configuration_hash != chain.digest(adapter.configuration(req.action, req.params)):
        out.append("the configuration hash does not match the parameters")
    if req.cost != json.loads(json.dumps(dataclasses.asdict(adapter.estimate_cost(req.action, req.params)))):
        out.append("the cost must be the system's estimate")
    expected = adapter.procedure(req.action, req.params) if spec.executor == "human" else None
    if req.procedure != expected:
        out.append("the procedure must be the system's, rendered for these parameters")
    if req.objective_version != obj.version:
        out.append(f"the request names objective version {req.objective_version}; the current one is {obj.version}")
    if req.specimen is not None and (len(req.specimen) != 1 or not {"id", "batch"} & set(req.specimen) or
                                     not all(isinstance(v, str) and v for v in req.specimen.values())):
        out.append('specimen is {"id": ...} or {"batch": ...}')
    quantities = {q.name: q.unit for q in obj.quantities} | {q.name: q.unit for q in adapter.spec.quantities}
    for q, p in req.prediction.items():
        if q not in quantities:
            out.append(f"prediction {q}: not a quantity of the objective or the system")
            continue
        try:
            if not units.same_dimension(p.get("unit", ""), quantities[q]):
                out.append(f"prediction {q}: {p.get('unit')} does not measure the same thing as {quantities[q]}")
        except ValueError as e:
            out.append(f"prediction {q}: {e}")
        nums = [p.get(k) for k in ("lo", "value", "hi") if p.get(k) is not None]
        if p.get("value") is None or \
                any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in nums) or nums != sorted(nums):
            out.append(f"prediction {q}: give a number as value, with lo <= value <= hi when an interval is given")
        if set(p) - {"value", "lo", "hi", "unit"}:
            out.append(f"prediction {q}: only value, lo, hi and unit")
    if req.content_hash != chain.content_hash(req):
        out.append("the content hash does not match the request")
    return out


# ------------------------------------------------------------------ request state, folded

@dataclass
class RequestState:
    request_id: str
    state: str
    versions: list[LoopRecord]
    approval: LoopRecord | None = None
    closed_by: LoopRecord | None = None       # the rejection or cancellation
    executions: list[LoopRecord] = field(default_factory=list)
    result: LoopRecord | None = None
    failure: LoopRecord | None = None
    refused: list[LoopRecord] = field(default_factory=list)   # result_rejected: uploads refused, with reasons
    timeline: list[dict] = field(default_factory=list)

    @property
    def record(self) -> LoopRecord:
        return self.versions[-1]

    @cached_property
    def _latest(self) -> RequestData:
        return RequestData.model_validate(self.record.data)

    @property
    def data(self) -> RequestData:
        if self._latest.content_hash != self.record.data["content_hash"]:   # a version arrived after the cache
            del self._latest
        return self._latest

    version = property(lambda self: self.data.version)
    content_hash = property(lambda self: self.data.content_hash)
    action = property(lambda self: self.data.action)
    executor = property(lambda self: self.data.executor)
    proposed_by = property(lambda self: self.versions[0].actor)
    approved_hash = property(lambda self: self.approval.data["content_hash"] if self.approval else None)
    approved_by = property(lambda self: self.approval.actor if self.approval else None)
    attempt = property(lambda self: sum(e.data["phase"] == "started" for e in self.executions))

    def version_named(self, content_hash: str) -> int | None:
        return next((r.data["version"] for r in self.versions if r.data["content_hash"] == content_hash), None)


def request_states(L: Ledger, cid: str, records: list[LoopRecord] | None = None) -> dict[str, RequestState]:
    """Every request of the campaign, keyed by request_id, at its latest version's state."""
    out: dict[str, RequestState] = {}
    spec: dict = {}

    def move(st: RequestState, state: str, rec: LoopRecord):
        st.state = state
        st.timeline.append({"state": state, "seq": rec.seq, "t": rec.created_at, "actor": rec.actor})

    for rec in chain.records(L, cid) if records is None else records:
        if rec.type == "objective":
            spec = rec.data["system"].get("spec") or {}
        if rec.type not in _REQUEST_TYPES:
            continue
        rq = rec.data["request_id"]
        st = out.get(rq)
        if rec.type == "request":
            if st is None:
                st = out[rq] = RequestState(rq, "awaiting_approval", [rec])
            else:
                move(st, "superseded", rec)
                st.versions.append(rec)
            move(st, "awaiting_approval", rec)
            continue
        if st is None:                     # admission refuses these; an unrelated record is ignored by the fold
            continue
        if rec.type == "approval":
            st.approval = rec
            move(st, "queued" if st.executor == "computational" else "awaiting_result", rec)
        elif rec.type in ("rejection", "cancellation"):
            st.closed_by = rec
            move(st, "rejected" if rec.type == "rejection" else "cancelled", rec)
        elif rec.type == "execution":
            st.executions.append(rec)
            if rec.data["phase"] == "started":
                move(st, "running", rec)
            elif (_spec_action(spec, st.action) or {}).get("deterministic") and rec.data["attempt"] < MAX_ATTEMPTS:
                move(st, "queued", rec)
        elif rec.type == "result":
            st.result = rec
            move(st, "completed", rec)
        elif rec.type == "failure":
            st.failure = rec
            move(st, "failed", rec)
        elif rec.type == "result_rejected":
            st.refused.append(rec)
    return out


def withdrawn_reason(version: int) -> str:
    return f"objective amended to v{version}"


def _terms(target: dict, constraints: list) -> tuple:
    """What an objective version asks: its target and constraints without their labels."""
    t = (target["quantity"], target["unit"], target["direction"], float(target.get("margin", 0.0)),
         None if target.get("limit") is None else float(target["limit"]))
    return t, sorted((c["quantity"], c["op"], float(c["limit"]), c["unit"]) for c in constraints)


def terms_version(records: list[LoopRecord], version: int) -> int:
    """The objective version, at or before `version`, of the last amendment that changed the target or the
    constraints (a budget or a label alone changes neither); 1 when none did."""
    since, target, constraints = 1, None, None
    for rec in records:
        if rec.type == "objective":
            target, constraints = rec.data["target"], rec.data["constraints"]
        elif rec.type == "amendment" and target is not None and rec.data["version"] <= version:
            new_t = rec.data.get("target") or target
            new_c = rec.data["constraints"] if rec.data.get("constraints") is not None else constraints
            if _terms(new_t, new_c) != _terms(target, constraints):
                since = rec.data["version"]
            target, constraints = new_t, new_c
    return since


def withdraw_superseded(L: Ledger, cid: str) -> list[LoopRecord]:
    """Cancel, as the system (AMENDED), every request still awaiting approval whose current version was proposed
    before the last amendment that changed the target or the constraints: that amendment supersedes it. It no longer
    counts against the budget and the planner is called again (a cancellation starts a turn). An amendment of the
    budget or of a label alone withdraws nothing; approved, queued and running requests are untouched. Idempotent;
    one savepoint."""
    out = []
    with L.transaction():
        recs = chain.records(L, cid)
        obj = current_objective(L, cid, recs)
        since = terms_version(recs, obj.version)
        for st in request_states(L, cid, recs).values():
            if st.state == "awaiting_approval" and st.data.objective_version < since:
                out.append(chain.append(L, cid, "cancellation",
                                        CancellationData(request_id=st.request_id, author=AMENDED,
                                                         reason=withdrawn_reason(since)),
                                        AMENDED, chain.natural_id("cnl", st.request_id)))
    return out


def experiments_used(states: dict[str, RequestState]) -> int:
    """Requests that count against budget.experiments: every one the agent or a person proposed, except those
    rejected or cancelled. Registration baseline re-runs and reference-search runs do not count."""
    return sum(s.proposed_by not in SYSTEM_RUNS and s.state not in ("rejected", "cancelled") for s in states.values())


# ------------------------------------------------------------------ claims (design §2.6)

def claim_path(root: Path | str, request_id: str) -> Path:
    return Path(root) / "locks" / f"req-{hashlib.sha256(request_id.encode()).hexdigest()[:16]}.lock"


def claim_held(root: Path | str, request_id: str) -> bool:
    """True while some process (this one included) holds the request's claim lock."""
    probe = ExclusiveFileLock(claim_path(root, request_id))
    if probe.try_acquire():
        probe.release()
        return False
    return True


# ------------------------------------------------------------------ the conclusion's outcome

def computed_outcome(L: Ledger, cid: str, records: list[LoopRecord] | None = None) -> tuple[str, str | None]:
    """(outcome, comparison_id): loop_compare.select_best over each candidate's latest comparison on the current
    objective version. The agent never writes the outcome."""
    from translucent import loop_compare
    outcome, comparison, _ = loop_compare.outcome(L, cid, records)
    return outcome, comparison


def computed_findings(L: Ledger, cid: str, records: list[LoopRecord] | None = None) -> dict | None:
    """The conclusion's findings under loop-compare/3 ({"feasibility", "speed", "objective"}, computed like the
    outcome); None under /1 and /2, whose conclusions record none."""
    from translucent import loop_compare
    recs = chain.records(L, cid) if records is None else records
    if current_objective(L, cid, recs).evaluator != "loop-compare/3":
        return None
    return loop_compare.findings(L, cid, recs)


# ------------------------------------------------------------------ admission

@dataclass
class _Ctx:
    records: list[LoopRecord]
    objective: ObjectiveData
    states: dict[str, RequestState]

    def state(self, rq: str) -> RequestState:
        st = self.states.get(rq)
        if st is None:
            raise AdmissionError(f"request {rq} is not a request of this campaign")
        return st


def _ctx(L: Ledger, cid: str) -> _Ctx:
    recs = chain.records(L, cid)
    return _Ctx(recs, current_objective(L, cid, recs), request_states(L, cid, recs))


def _refuse(why: str):
    raise AdmissionError(why)


@chain.admission("objective")
def _admit_objective(L: Ledger, rec: LoopRecord, obj: ObjectiveData):
    kind = actor_kind(rec.actor)
    if kind not in ("person", "agent"):
        _refuse("an objective is registered by a person, or by an agent's client for an external planner")
    if obj.version != 1:
        _refuse("a campaign's objective starts at version 1; later versions are amendments")
    if kind == "agent":
        if obj.auto_approve or obj.baseline_simulation:
            _refuse("an agent cannot pre-approve experiments: auto_approve and baseline_simulation are a person's")
        if obj.planner.get("kind") != "external":
            _refuse("an agent registers only campaigns it plans itself (planner kind external)")
    if problems := kind_problems(obj.evaluator, obj.kind, obj.target.limit) + \
            margin_problems(obj.evaluator, obj.target.margin):
        _refuse("; ".join(problems))


def margin_problems(evaluator: str, margin: float) -> list[str]:
    """loop-compare/3 calls a result faster only when it is faster: its target margin is 0 or more. (/1 and /2 keep
    their rule, where a negative margin allowed that much slowdown.)"""
    if evaluator == "loop-compare/3" and margin < 0:
        return [f"the target's margin cannot be negative under loop-compare/3 (it is {margin:g}): a result is called "
                "faster only when it is faster than the reference by more than the margin"]
    return []


def kind_problems(evaluator: str, kind: str | None, target_limit: float | None) -> list[str]:
    """An objective's kind agrees with its evaluator and target: only loop-compare/3 names one; a hard target is the
    target's limit, and an objective asking for the fastest protocol has none."""
    if evaluator != "loop-compare/3":
        return [f"{evaluator} objectives name no kind; loop-compare/3 does"] if kind is not None else []
    if kind == "hard_target" and target_limit is None:
        return ["a hard_target objective states its target as the target's limit"]
    if kind == "fastest_feasible" and target_limit is not None:
        return ["a fastest_feasible objective has no target limit; set kind hard_target to require one"]
    return []


@chain.admission("amendment")
def _admit_amendment(L: Ledger, rec: LoopRecord, a: AmendmentData):
    if actor_kind(rec.actor) != "person" or not _authored_by(a.author, rec.actor):
        _refuse("only a person amends the objective, under their own name")
    obj = current_objective(L, rec.campaign_id)
    if a.version != obj.version + 1:
        _refuse(f"the next objective version is {obj.version + 1}")
    if a.target is None and a.constraints is None and a.budget is None:
        _refuse("an amendment changes the target, the constraints, the budget or more than one")
    if a.budget is not None and (problems := budget_amendment_problems(a.budget)):
        _refuse("; ".join(problems))
    defined = {q.name: q.unit for q in obj.quantities}
    for quantity, unit in ([(a.target.quantity, a.target.unit)] if a.target else []) + \
            [(c.quantity, c.unit) for c in a.constraints or []]:
        if quantity not in defined:
            _refuse(f"{quantity} is not defined in the objective's quantities")
        try:
            if not units.same_dimension(unit, defined[quantity]):
                _refuse(f"{quantity}: {unit} does not measure the same thing as {defined[quantity]}")
        except ValueError as e:
            _refuse(f"{quantity}: {e}")
    if a.target is not None and obj.evaluator == "loop-compare/3":      # the kind stays; a hard target changes
        kind = obj.kind or ("hard_target" if obj.target.limit is not None else "fastest_feasible")   # only here
        if problems := kind_problems(obj.evaluator, kind, a.target.limit):
            _refuse("; ".join(problems) + " (an amendment keeps the objective's kind)")
        if problems := margin_problems(obj.evaluator, a.target.margin):
            _refuse("; ".join(problems))


BUDGET_KEYS =("model_usd", "experiments", "experiment_usd", "max_wall_s", "lab_tests")


def budget_amendment_problems(budget) -> list[str]:
    """M2 (AUTH): why a budget amendment is refused: it names budget keys only, experiments a whole number, the
    others finite numbers, all 0 or more. Who may raise a budget is checked before (translucent.loop_service)."""
    if not isinstance(budget, dict) or not budget:
        return ["a budget amendment names one or more of " + ", ".join(BUDGET_KEYS)]
    out = [f"budget.{k} is not a budget field ({', '.join(BUDGET_KEYS)})" for k in budget if k not in BUDGET_KEYS]
    for k, v in budget.items():
        if k == "experiments":
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                out.append("budget.experiments must be a whole number of experiments, 0 or more")
        elif k in BUDGET_KEYS and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                                   or v < 0):
            out.append(f"budget.{k} must be a finite number, 0 or more")
    return out


def measurement_problems(obj: ObjectiveData, m: MeasurementData) -> list[str]:
    channel = _spec_quantity(obj, m.spec.channel)
    if channel is None or channel.get("kind") != "series":
        return [f"{m.spec.channel} is not a series quantity of {obj.system['id']}"]
    try:
        derived = units.derive(m.spec.op, channel["unit"])
    except ValueError as e:
        return [f"{m.name}: {e}"]
    return [f"{m.name}: {m.spec.op} of {channel['unit']} is in {derived}, not {m.spec.unit}"] \
        if m.spec.unit != derived else []


@chain.admission("measurement")
def _admit_measurement(L: Ledger, rec: LoopRecord, m: MeasurementData):
    if actor_kind(rec.actor) not in ("agent", "person") or m.defined_by != rec.actor:
        _refuse("a measurement is defined by an agent or a person, as itself")
    if problems := measurement_problems(current_objective(L, rec.campaign_id), m):
        _refuse("; ".join(problems))


@chain.admission("request")
def _admit_request(L: Ledger, rec: LoopRecord, req: RequestData):
    kind, cid = actor_kind(rec.actor), rec.campaign_id
    if kind not in ("agent", "person") and rec.actor not in SYSTEM_RUNS:
        _refuse("experiments are requested by an agent or a person")
    ctx = _ctx(L, cid)
    if rec.actor == REGISTRATION and not ctx.objective.baseline_simulation:
        _refuse("registration requests exist only for an objective with baseline_simulation")
    if rec.actor == REFERENCE:
        from translucent import loop_reference
        s = loop_reference.state(L, cid, ctx.objective, ctx.states, ctx.records)
        if s is None:
            _refuse("reference-search runs exist only for a loop-compare/3 fastest_feasible objective with "
                    "baseline_simulation, on a system that defines a reference search")
        nxt = s["next"] or {}
        if s["status"] != "due" or req.version != 1 or req.request_id != nxt.get("request_id") or \
                req.action != s["spec"]["action"] or dict(req.params) != nxt.get("params"):
            _refuse(f"request {req.request_id} is not the reference search's next step (the search is "
                    f"{s['status']}" + (f"; step {nxt['step']} is {nxt['request_id']}" if nxt else "") + ")")
    if not REQUEST_ID.match(req.request_id):
        _refuse("request_id is rq_ followed by 20 hex digits")
    st = ctx.states.get(req.request_id)
    if req.version == 1:
        if st is not None:
            _refuse(f"request {req.request_id} exists; a revision supersedes its latest version")
        if req.supersedes is not None:
            _refuse("version 1 supersedes nothing")
    else:
        if st is None:
            _refuse(f"request {req.request_id} is not a request of this campaign")
        if req.action != st.action:
            _refuse(f"a revision keeps the action: {req.request_id} is {st.action}")
        if req.version != st.version + 1 or req.supersedes != st.record.id:
            raise StaleRequest(f"a revision of {req.request_id} is version {st.version + 1}, superseding "
                               f"{st.record.id}")
        if st.state != "awaiting_approval":
            _refuse(f"request {req.request_id} is {st.state}; only a request awaiting approval is revised")
        if kind == "agent" and rec.actor != st.proposed_by:
            _refuse("an agent revises only its own requests")
        if rec.actor in SYSTEM_RUNS or st.proposed_by in SYSTEM_RUNS:
            _refuse("registration and reference-search requests are not revised")
    try:
        adapter = adapter_for(ctx.objective)
    except KeyError as e:
        _refuse(f"{e.args[0]}: this process cannot check the request")
    if problems := request_problems(adapter, ctx.objective, req):
        _refuse("; ".join(problems))
    only = ctx.objective.planner.get("actions")
    if kind == "agent" and isinstance(only, list) and req.action not in only:
        _refuse(f"{req.action} is not among the actions this campaign's registering person allowed its agent")
    if (bound := getattr(adapter, "admission_problems", None)) is not None and \
            (problems := bound(L, cid, ctx.objective, req, ctx.states)):
        _refuse("; ".join(problems))
    for eid in req.evidence_ids:
        if not _evidence_of(L, cid, ctx.objective, eid, ctx.states):
            _refuse(f"evidence {eid} is not a record of this campaign")
    if req.version == 1 and rec.actor not in SYSTEM_RUNS:
        limit = ctx.objective.budget.get("experiments")
        if limit is not None and experiments_used(ctx.states) >= limit:
            _refuse(f"the experiment budget is spent: {experiments_used(ctx.states)} of {limit} experiments are "
                    f"requested or done")
        usd = ctx.objective.budget.get("experiment_usd")
        spent = sum(float(s.data.cost.get("usd", 0.0)) for s in ctx.states.values()
                    if s.proposed_by not in SYSTEM_RUNS and s.state not in ("rejected", "cancelled"))
        if usd is not None and req.cost.get("usd", 0.0) > 0 and spent + req.cost["usd"] > usd:
            _refuse(f"the experiment budget is spent: ${spent:.2f} of ${usd:.2f} committed")
    dup = next((s for s in ctx.states.values() if s.request_id != req.request_id and s.state in PENDING and
                s.data.configuration_hash == req.configuration_hash), None)
    if dup is not None:
        raise DuplicateRequest(dup.request_id)


def _evidence_of(L: Ledger, cid: str, obj: ObjectiveData, eid: str, states=None) -> bool:
    kind = L.kind_of(eid)
    if kind == "loop":
        return L.get("loop", eid).campaign_id == cid
    if kind == "run":
        return L.get("run", eid).campaign_id == cid
    if kind == "measurement":
        run = L.get("run", L.get("measurement", eid).run_id)
        return run is not None and run.campaign_id == cid
    if eid in obj.datasets or eid in obj.baseline or eid in (states or {}):    # states: this campaign's requests
        return True
    # a dataset the campaign imported, or one of its records as the planner sees them ("dataset/record")
    from translucent.records import campaign_records, imported
    return eid in imported(L, cid) or any(ref == eid for ref, _ in campaign_records(L, obj, cid))


def _latest_version(st: RequestState, record_id: str, content_hash: str):
    if record_id != st.record.id or content_hash != st.content_hash:
        raise StaleRequest(f"This request changed after you read it; review version {st.version}")


@chain.admission("approval")
def _admit_approval(L: Ledger, rec: LoopRecord, a: ApprovalData):
    kind = actor_kind(rec.actor)
    if kind == "agent":
        _refuse("an agent cannot approve experiments; a person does")
    ctx = _ctx(L, rec.campaign_id)
    st = ctx.state(a.request_id)
    if st.state != "awaiting_approval":
        _refuse(f"request {a.request_id} is {st.state}, not awaiting approval")
    _latest_version(st, a.record_id, a.content_hash)
    if kind == "person":
        if a.rule is not None or not _authored_by(a.author, rec.actor):
            _refuse("a person approves under their own name, with no rule")
    elif rec.actor == REGISTRATION:
        if a.rule != "registration" or st.proposed_by != REGISTRATION:
            _refuse("the registration rule approves only the baseline re-runs written at registration")
    elif rec.actor == REFERENCE:
        if a.rule != "reference" or st.proposed_by != REFERENCE or a.author != ctx.objective.registered_by:
            _refuse("the reference rule approves only the reference search's own runs, under the registering "
                    "person's pre-approval")
    elif (m := re.fullmatch(r"policy:auto/([1-9][0-9]*)", rec.actor)) and a.rule == f"auto/{m.group(1)}":
        n, obj = int(m.group(1)), ctx.objective
        if n > len(obj.auto_approve) or actor_kind(ctx.records[0].actor) != "person":
            _refuse(f"auto-approval rule {n} is not a rule of the objective")
        rule = obj.auto_approve[n - 1]
        used = sum(s.approval is not None and s.approval.actor == rec.actor for s in ctx.states.values())
        if st.executor != rule.executor or float(st.data.cost.get("usd", 0.0)) > rule.max_usd or \
                float(st.data.cost.get("wall_s", 0.0)) > rule.max_wall_s or used >= rule.max_count:
            _refuse(f"request {a.request_id} is outside auto-approval rule {n}")
    else:
        _refuse("experiments are approved by a person or by a rule of the objective")


@chain.admission("rejection")
def _admit_rejection(L: Ledger, rec: LoopRecord, r: RejectionData):
    if actor_kind(rec.actor) != "person" or not _authored_by(r.author, rec.actor):
        _refuse("only a person rejects an experiment, under their own name")
    st = _ctx(L, rec.campaign_id).state(r.request_id)
    if st.state != "awaiting_approval":
        _refuse(f"request {r.request_id} is {st.state}, not awaiting approval")
    _latest_version(st, r.record_id, r.content_hash)


@chain.admission("cancellation")
def _admit_cancellation(L: Ledger, rec: LoopRecord, c: CancellationData):
    kind = actor_kind(rec.actor)
    if rec.actor == AMENDED:             # the system withdraws a proposal an amendment superseded, and nothing else
        ctx = _ctx(L, rec.campaign_id)
        st = ctx.state(c.request_id)
        since = terms_version(ctx.records, ctx.objective.version)
        if c.author != AMENDED or st.state != "awaiting_approval" or st.data.objective_version >= since or \
                c.reason != withdrawn_reason(since):
            _refuse(f"{AMENDED} withdraws only a request awaiting approval that was proposed under an older "
                    f"objective version than the last amendment that changed the target or the constraints "
                    f"(v{since}), with the reason \"{withdrawn_reason(since)}\"")
        return
    if kind not in ("person", "agent") or not _authored_by(c.author, rec.actor):
        _refuse("a request is cancelled by a person, or by the agent that proposed it, under their own name")
    st = _ctx(L, rec.campaign_id).state(c.request_id)
    if kind == "agent":
        if rec.actor != st.proposed_by:
            _refuse("an agent cancels only its own requests")
        if st.state != "awaiting_approval":
            _refuse(f"request {c.request_id} is {st.state}; an agent cancels only before approval")
    elif st.state not in ("awaiting_approval", "queued", "awaiting_result"):
        _refuse(f"request {c.request_id} is {st.state} and can no longer be cancelled")


@chain.admission("execution")
def _admit_execution(L: Ledger, rec: LoopRecord, x: ExecutionData):
    if rec.actor != RUNNER:
        _refuse("execution is recorded by the runner")
    ctx = _ctx(L, rec.campaign_id)
    st = ctx.state(x.request_id)
    if st.executor != "computational":
        _refuse(f"request {x.request_id} is not run by code")
    if x.phase == "started" and x.executor_digest != registered_executor_digest(ctx.objective):
        _refuse(f"the installed {ctx.objective.system['id']} code or solver differs from what the campaign "
                "registered; restore it, or open a new campaign on the new code")
    if x.content_hash != st.approved_hash:
        _refuse("execution must name the approved content hash")
    if not claim_held(L.root, x.request_id):
        _refuse(f"the runner records execution of {x.request_id} only while holding its claim lock")
    if x.phase == "started":
        if st.state != "queued":
            _refuse(f"request {x.request_id} is {st.state}, not queued")
        if x.attempt != st.attempt + 1 or x.attempt > MAX_ATTEMPTS:
            _refuse(f"the next attempt is {st.attempt + 1}, at most {MAX_ATTEMPTS}")
    elif st.state != "running" or x.attempt != st.attempt or st.executions[-1].data["phase"] != "started":
        _refuse(f"attempt {x.attempt} of {x.request_id} is not running")


@chain.admission("failure")
def _admit_failure(L: Ledger, rec: LoopRecord, f: FailureData):
    if rec.actor != RUNNER:
        _refuse("failures are recorded by the runner")
    st = _ctx(L, rec.campaign_id).state(f.request_id)
    if st.state != "running" or f.attempt != st.attempt:
        _refuse(f"attempt {f.attempt} of {f.request_id} is not running")
    if not claim_held(L.root, f.request_id):
        _refuse(f"the runner records a failure of {f.request_id} only while holding its claim lock")


def consumed_by(L: Ledger, specimen_id: str, request_id: str) -> LoopRecord | None:
    """A result of another request, in any campaign of the ledger, that consumed this specimen."""
    rows = L.db.execute("SELECT body FROM record WHERE kind='loop' AND json_extract(body,'$.type')='result' "
                        "AND json_extract(body,'$.data.executed.specimen_id')=?", (specimen_id,))
    for (body,) in rows:
        rec = LoopRecord.model_validate_json(body)
        if rec.data["request_id"] != request_id and rec.data["executed"].get("destructive"):
            return rec
    return None


@chain.admission("result")
def _admit_result(L: Ledger, rec: LoopRecord, r: ResultData):
    ctx = _ctx(L, rec.campaign_id)
    st = ctx.state(r.request_id)
    if r.content_hash != st.approved_hash:
        _refuse(f"the result names {r.content_hash[:12]}; the approved version is {st.approved_hash or 'none'}")
    if st.executor == "computational":
        if rec.actor != RUNNER or r.basis != "computational" or r.submitted_by != RUNNER:
            _refuse("results of computational requests are recorded by the runner")
        if st.state != "running" or r.attempt != st.attempt or st.executions[-1].data["phase"] != "started":
            _refuse(f"attempt {r.attempt} of {r.request_id} is not running")
        if not claim_held(L.root, r.request_id):
            _refuse(f"the runner records the result of {r.request_id} only while holding its claim lock")
    elif st.executor == "human":
        if actor_kind(rec.actor) != "person" or r.basis != "physical" or r.attempt is not None or \
                r.submitted_by != rec.actor:
            _refuse("results of physical tests are submitted by a person, under their own name")
        if st.state != "awaiting_result":
            _refuse(f"request {r.request_id} is {st.state}, not awaiting a result")
        # checked again here, inside the write, so two uploads naming one destroyed specimen cannot both land
        if r.executed.get("destructive") and (used := consumed_by(L, r.executed.get("specimen_id"), r.request_id)):
            _refuse(f"specimen {r.executed['specimen_id']} was consumed by destructive test "
                    f"{used.data['request_id']} ({used.id})")
    else:
        _refuse(f"request {r.request_id} has no executor that returns results")
    run = L.get("run", r.run_id)
    if run is None or run.campaign_id != rec.campaign_id or run.status != "ok":
        _refuse(f"result run {r.run_id} is not a successful run of this campaign")
    for q, mid in r.measurements.items():
        m = L.get("measurement", mid)
        if m is None or m.run_id != r.run_id or m.quantity != q:
            _refuse(f"measurement {mid} is not the run's {q}")
    for q, v in r.categories.items():
        spec = _spec_quantity(ctx.objective, q) or {}
        if spec.get("kind") != "category" or v not in spec.get("categories", []):
            _refuse(f"{q} = {v!r} is not a declared category value of the system")
    L._require(list(r.series.values()) + list(r.files.values()), ("artifact",), "result output")
    produces = set((_spec_action(ctx.objective, st.action) or {}).get("produces", ()))
    if missing := produces - set(r.measurements) - set(r.series) - set(r.categories):
        _refuse(f"the result lacks {', '.join(sorted(missing))}")


@chain.admission("result_rejected")
def _admit_result_rejected(L: Ledger, rec: LoopRecord, r: ResultRejectedData):
    if rec.actor != RESULTS:
        _refuse("refused results are recorded by the result intake")
    _ctx(L, rec.campaign_id).state(r.request_id)
    if not r.reasons:
        _refuse("a refused result states why")


@chain.admission("comparison")
def _admit_comparison(L: Ledger, rec: LoopRecord, c):
    if actor_kind(rec.actor) != "system":
        _refuse("comparisons are computed by the comparator, never written by an agent or a person")


@chain.admission("conclusion")
def _admit_conclusion(L: Ledger, rec: LoopRecord, c: ConclusionData):
    if actor_kind(rec.actor) not in ("agent", "person"):
        _refuse("a campaign is concluded by its agent or a person")
    # a queued or running computation would change the outcome, and once concluded it could never be recorded
    if busy := sorted(s.request_id for s in request_states(L, rec.campaign_id).values()
                      if s.state in ("queued", "running")):
        _refuse(f"{', '.join(busy)} {'is' if len(busy) == 1 else 'are'} queued or running; wait for the result, "
                "or cancel a queued request, before concluding")
    outcome, comparison = computed_outcome(L, rec.campaign_id)
    if (c.outcome, c.comparison_id) != (outcome, comparison):
        _refuse(f"the outcome is computed from the comparisons: {outcome} ({comparison or 'no comparison'})")
    if c.findings != computed_findings(L, rec.campaign_id):
        _refuse("the findings are computed from the comparisons (loop-compare/3 records them; /1 and /2 none)")


@chain.admission("planner")
def _admit_planner(L: Ledger, rec: LoopRecord, p):
    if actor_kind(rec.actor) not in ("agent", "system"):
        _refuse("planner receipts are written by the planner")


# ------------------------------------------------------------------ measurement operators (ops/1)

OPS_DIGEST = file_digest(Path(operators.__file__))


def apply_operator(spec: OperatorSpec, t: list[float], y: list[float], y_unit: str,
                   h: Uncertainty) -> tuple[float, Uncertainty]:
    """(value, uncertainty) of an ops/1 operator over one series. Uncertainty rules: a value read from the series
    (final, initial, max, min, value_at, mean) keeps the input's; a difference of two readings (delta) is sqrt(2)
    times it; a threshold crossing is half the sample interval around it; slope, integral and duration_where are
    not propagated and say so."""
    out = operators.evaluate(spec, t, y, y_unit)
    mul = float(units.factor(out["computed_unit"], spec.unit)[0])
    if spec.op == "time_to_threshold":
        unc = Uncertainty(value=abs(out["half_interval_s"] * mul), kind="resolution",
                          method="half the sample interval at the crossing")
    elif spec.op in ("final", "initial", "max", "min", "value_at", "mean") and h.value is not None:
        unc = Uncertainty(value=abs(h.value * mul), kind=h.kind, method=f"{h.method}; carried by ops/1 {spec.op}")
    elif spec.op == "delta" and h.value is not None:
        unc = Uncertainty(value=abs(h.value * mul) * math.sqrt(2), kind=h.kind,
                          method=f"{h.method}; sqrt(2) times it for a difference of two readings")
    else:
        unc = Uncertainty(value=None, kind="none", method=f"not propagated by ops/1 {spec.op}")
    return out["value"], unc
