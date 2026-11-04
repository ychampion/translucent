"""The loop service: one table of capabilities, and the one implementation every surface calls (design §2.9).

`CAPABILITIES` names each capability with the actor kinds that may call it. HTTP routes and MCP tools (S5) and the
planner's tool list are generated from it, so a capability an agent may not use is absent from the planner's tools,
and `LoopService` refuses it again at the call (NotAllowed, HTTP 403). The ledger's admission hooks
(translucent.loop) refuse it a third time, for anything that writes loop records without this service.

Writes accept an idempotency key (HTTP Idempotency-Key, MCP idempotency_key): the record id becomes
`<prefix>_` + sha256([cid, type, actor kind, key])[:24], so the same call under the same key returns the original
record, and a different body under it is a ChainConflict (HTTP 409). Without a key, ids are natural (design §2.5).

Reads: `view` is the LoopView (translucent.loop_view) and `campaigns` lists CampaignSummary items. Comparisons are
recorded by the comparator (translucent.loop_compare) with every result, after an amendment and before a conclusion.
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple

from translucent import chain, datasets, loop_compare, loop_view, systems
from translucent.scrub import scrub
from translucent.contracts import Measurement, Run, Uncertainty
from translucent.executors import (HumanUpload, ResultInvalid, human_uncertainties, ingest_output,
                                   parse_human_result, upload_sha256)
from translucent.ledger import AdmissionError, Ledger
from translucent.loop import (OPS_DIGEST, PENDING, RESULTS, NotAllowed, NotFound, RequestState, actor_kind,
                              adapter_for, bind_params, build_request, campaign_ids, computed_findings, computed_outcome,
                              current_objective,
                              measurement_problems, open_campaign, request_states, StaleRequest)
from translucent.loop_contracts import (AmendmentData, ApprovalData, CancellationData, ConclusionData, Constraint,
                                        MeasurementData, ObjectiveData, OperatorSpec, RejectionData)


def _labels_scrubbed(constraints):
    """Constraints with their free-text labels scrubbed (dicts or Constraint models; anything else is left for
    validation to refuse)."""
    if not isinstance(constraints, (list, tuple)):
        return constraints
    return [c.model_copy(update={"label": scrub(c.label)}) if isinstance(c, Constraint) else
            {**c, "label": scrub(c["label"])} if isinstance(c, dict) and isinstance(c.get("label"), str) else c
            for c in constraints]


class Actor(NamedTuple):
    kind: Literal["agent", "person", "system"]
    name: str
    via: Literal["http", "mcp", "cli", "planner", "runner"] = "cli"

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.name}"


@dataclass(frozen=True)
class Capability:
    actors: frozenset[str]
    write: bool
    summary: str


_ALL = frozenset({"agent", "person", "system"})
_AGENT_PERSON = frozenset({"agent", "person"})
_PERSON = frozenset({"person"})
_SYSTEM = frozenset({"system"})

CAPABILITIES: dict[str, Capability] = {
    # read, everyone
    "systems": Capability(_ALL, False, "The registered systems: quantities, actions, parameter bounds, costs"),
    "datasets": Capability(_ALL, False, "Imported datasets of the ledger"),
    "dataset": Capability(_ALL, False, "One dataset's records (no raw values)"),
    "campaigns": Capability(_ALL, False, "Loop campaigns of the ledger, with status and next action"),
    "view": Capability(_ALL, False, "One campaign: objective, requests, next action, conclusion"),
    "series": Capability(_ALL, False, "A result's or baseline's series, windowed and downsampled"),
    "request": Capability(_ALL, False, "One request with its content hash, state, timeline and result"),
    # agent and person
    "import_dataset": Capability(_AGENT_PERSON, True, "Import a dataset (import v2)"),
    "open_campaign": Capability(_AGENT_PERSON, True,
                                "Register a campaign's objective; an agent only for its own external planner, "
                                "with no pre-approvals"),
    "define_measurement": Capability(_AGENT_PERSON, True,
                                     "Define a derived measurement (ops/1) and apply it to baselines and results"),
    "propose": Capability(_AGENT_PERSON, True,
                          "Request an experiment: action, parameters, rationale, sealed prediction"),
    "cancel": Capability(_AGENT_PERSON, True,
                         "Cancel a request; an agent only its own, and only before approval"),
    "conclude": Capability(_AGENT_PERSON, True, "Conclude the campaign; the outcome is computed, not written"),
    # person only
    "approve": Capability(_PERSON, True, "Approve the exact request version a person read (its content hash)"),
    "reject": Capability(_PERSON, True, "Reject a request version, with the reason"),
    "revise": Capability(_PERSON, True, "Correct a request's parameters as a new version; optionally approve it"),
    "submit_result": Capability(_PERSON, True,
                                "Return a physical test's result against the approved hash; checked, or refused "
                                "and recorded"),
    "amend": Capability(_PERSON, True, "Amend the objective's target or constraints as a new version"),
    "note": Capability(_PERSON, True, "Add a note to the campaign's event stream"),
    # system only: the runner (translucent.loop_runner), executors and the comparator (S4b); never on a surface
    "claim": Capability(_SYSTEM, True, "Take a queued computational request under its file lock"),
    "record_execution": Capability(_SYSTEM, True, "Record an execution start or an abandoned attempt"),
    "record_result": Capability(_SYSTEM, True, "Record a validated solver result"),
    "record_failure": Capability(_SYSTEM, True, "Record a failed attempt"),
    "auto_approve": Capability(_SYSTEM, True, "Approve under a rule the registering person wrote"),
    "record_comparison": Capability(_SYSTEM, True, "Record a loop-compare result"),
}


def allowed(kind: str) -> frozenset[str]:
    return frozenset(name for name, cap in CAPABILITIES.items() if kind in cap.actors)


def planner_tools() -> list[str]:
    """The capabilities a planner (an agent) is offered: never approve, reject, revise, submit results or amend."""
    return sorted(allowed("agent"))


def budget_raises(current: dict, new) -> list[str]:
    """The budget fields an amendment raises. A field the objective leaves unset is unlimited, except model_usd (no
    model budget is $0), so setting an unset field is a raise only for model_usd."""
    if not isinstance(new, dict):
        return []
    out = []
    for k, v in new.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue        # not a number: admission refuses it
        old = current.get(k, 0.0 if k == "model_usd" else None)
        if old is not None and (isinstance(old, bool) or not isinstance(old, (int, float)) or v > old):
            out.append(k)
    return out


class ResultRefused(AdmissionError):
    """A returned result was refused; the refusal is recorded as `record` (HTTP 422 with the reasons)."""

    def __init__(self, reasons: list[str], record):
        super().__init__("; ".join(reasons))
        self.reasons, self.record = list(reasons), record


def _spec_json(spec) -> dict:
    return json.loads(json.dumps(dataclasses.asdict(spec)))


def request_view(L: Ledger, st: RequestState) -> dict:
    d = st.data
    result = None
    if st.result is not None:
        r = st.result.data
        ms = [L.get("measurement", mid) for mid in r["measurements"].values()]
        result = {"result_id": st.result.id, "basis": r["basis"], "run_id": r["run_id"],
                  "measurements": [{"id": m.id, "quantity": m.quantity, "value": m.value, "unit": m.unit,
                                    "h": m.uncertainty.value} for m in ms if m is not None],
                  "categories": dict(r.get("categories", {})),
                  "series": [{"quantity": q, "artifact_id": a} for q, a in r["series"].items()],
                  "executed": r["executed"]}
    return {"request_id": st.request_id, "version": d.version, "record_id": st.record.id, "state": st.state,
            "action": d.action, "executor": d.executor,
            "params": {k: {"value": v, "unit": d.units.get(k, "")} for k, v in d.params.items()},
            "proposed_by": st.proposed_by, "approved_by": st.approved_by, "content_hash": d.content_hash,
            "approved_hash": st.approved_hash, "rationale": d.rationale, "prediction": d.prediction,
            "expected_effect": d.expected_effect, "cost": d.cost, "procedure": d.procedure, "specimen": d.specimen,
            "due_by": d.due_by, "attempts": st.attempt, "timeline": list(st.timeline), "result": result,
            "failure": dict(st.failure.data) if st.failure else None,
            "rejections": [{"seq": r.seq, "reasons": r.data["reasons"]} for r in st.refused]}


class LoopService:
    def __init__(self, L: Ledger, actor: Actor, *, idempotency_key: str | None = None,
                 import_root: Path | str | None = None, may_raise_budget: bool = False):
        if actor.kind not in ("agent", "person", "system") or not actor.name.strip():
            raise NotAllowed("an actor is an agent, a person or the system, with a name")
        self.L, self.actor, self.key = L, actor, idempotency_key
        # an amendment may raise a model or experiment budget only for an administrator (translucent.auth)
        self.may_raise_budget = may_raise_budget
        # The only folder whose files a caller may name for import. A caller's path is never trusted beyond it.
        self.import_root = Path(import_root).resolve() if import_root is not None else None

    # ------------------------------------------------------------ helpers

    def _allow(self, capability: str):
        cap = CAPABILITIES[capability]
        if self.actor.kind not in cap.actors:
            who = " or ".join(sorted(cap.actors))
            raise NotAllowed(f"{capability.replace('_', ' ')} is for {who} actors, not an {self.actor.kind}"
                             if self.actor.kind == "agent" else
                             f"{capability.replace('_', ' ')} is for {who} actors, not a {self.actor.kind}")

    def _rid(self, prefix: str, cid: str, type: str, natural: str) -> str:
        return chain.key_id(prefix, cid, type, self.actor.kind, self.key) if self.key else natural

    def _state(self, cid: str, request_id: str) -> RequestState:
        st = request_states(self.L, cid).get(request_id)
        if st is None:
            current_objective(self.L, cid)      # NotFound for an unknown campaign, first
            raise NotFound(f"{request_id} is not a request of {cid}")
        return st

    def _name_check(self, author: str):
        if author not in (self.actor.name, self.actor.label):
            raise NotAllowed(f"{self.actor.label} cannot act as {author}")

    # ------------------------------------------------------------ read

    def systems(self) -> list[dict]:
        self._allow("systems")
        return [_spec_json(s) for s in systems.available()]

    def datasets(self) -> list[dict]:
        self._allow("datasets")
        return [{"id": d.id, "title": d.title, "author": d.author, "system": d.system, "records": len(d.records),
                 "manifest_version": d.manifest_version, "created_at": d.created_at} for d in self.L.list("dataset")]

    def dataset(self, did: str) -> dict:
        self._allow("dataset")
        d = self.L.get("dataset", did)
        if d is None:
            raise NotFound(f"dataset {did} is not imported in this ledger")
        return d.model_dump(mode="json")

    def campaigns(self, *, cursor: int = 0, limit: int = 50, ledger: str | None = None) -> dict:
        """CampaignSummary items of the ledger's loop campaigns, oldest first; `ledger` is the ledger's id."""
        self._allow("campaigns")
        ids = campaign_ids(self.L)
        items = [loop_view.summary(self.L, cid, ledger or self.L.root.name) for cid in ids[cursor:cursor + limit]]
        return {"items": items, "next_cursor": cursor + limit if cursor + limit < len(ids) else None}

    def request(self, cid: str, request_id: str) -> dict:
        self._allow("request")
        return request_view(self.L, self._state(cid, request_id))

    def view(self, cid: str, *, api: str = "", ledger: str | None = None) -> dict:
        """The campaign's LoopView with a live source (`api` and `ledger` name where it is served)."""
        self._allow("view")
        return loop_view.view(self.L, cid, {"kind": "live", "api": api, "ledger": ledger or self.L.root.name})

    def series(self, cid: str, ref: str, quantity: str, *, window: tuple[float, float] | None = None,
               max_points: int = 2000) -> dict:
        self._allow("series")
        found = self._series(cid, current_objective(self.L, cid), ref, quantity)
        if found is None:
            raise NotFound(f"{ref} has no series {quantity}")
        body = found[1]
        pts = [(t, y) for t, y in zip(body["t"], body["y"]) if window is None or window[0] <= t <= window[1]]
        step = max(1, math.ceil(len(pts) / max(1, max_points)))
        kept = pts[::step]
        return {"t": [p[0] for p in kept], "y": [p[1] for p in kept], "unit": body["unit"], "t_unit": "s",
                "n": len(pts), "downsampled": step > 1}

    def _series(self, cid: str, obj: ObjectiveData, ref: str, quantity: str) -> tuple[str, dict] | None:
        """(artifact id, {"t", "y", "unit", "uncertainty"}) for a request/result ref or a "dataset/record" ref,
        values in the system's canonical unit (loop_compare.series, which verify reads too)."""
        return loop_compare.series(self.L, obj, ref, quantity, cid=cid)

    # ------------------------------------------------------------ agent and person

    def import_dataset(self, manifest: dict, files: dict[str, str] | str | None = None) -> dict:
        """Import v2: a manifest (a dict, never a path) and its files, named relative to the import root (a folder,
        or file name -> path). Same manifest and files: the stored dataset; a different body under a recorded id:
        DatasetConflict."""
        self._allow("import_dataset")
        if not isinstance(manifest, dict):
            raise NotAllowed("send the manifest itself, not a path to one")
        if files is None:
            files = "."
        if not isinstance(files, dict):     # a folder: confine each file the manifest names, not just the folder
            files = {name: Path(files) / name for name in datasets.file_names(manifest)}
        located = {name: self._confined(p) for name, p in files.items()}
        return datasets.import_dataset(self.L, manifest, located).model_dump(mode="json")

    def _confined(self, path: str | Path) -> Path:
        """`path` inside the import root, or NotAllowed. Relative paths are read from the root; a symlink anywhere
        on the way, or a path that resolves outside the root, is refused."""
        if self.import_root is None:
            raise NotAllowed("this server has no import folder; upload the files instead")
        refused = NotAllowed(f"{path}: not a file in the import folder")   # one answer: no probing outside it
        p = Path(path)
        p = Path(os.path.normpath(p if p.is_absolute() else self.import_root / p))
        if not p.is_relative_to(self.import_root):      # by the text alone, before touching the filesystem
            raise refused
        probe = p
        while probe != self.import_root:
            if probe.is_symlink():
                raise NotAllowed(f"{path}: links are not followed for import")
            probe = probe.parent
        try:
            resolved = p.resolve(strict=True)
        except (OSError, RuntimeError):
            raise refused from None
        if not resolved.is_relative_to(self.import_root) or not resolved.is_file():
            raise refused
        return resolved

    def open_campaign(self, objective: ObjectiveData | dict, title: str, *, memory: str = "on") -> dict:
        """Register the objective. A person registers under their own name; an agent's client may register only a
        campaign it plans itself (planner kind external) with no auto-approval and no baseline pre-approval."""
        self._allow("open_campaign")
        data = objective.model_dump(mode="json") if isinstance(objective, ObjectiveData) else dict(objective)
        if self.actor.kind == "person":
            self._name_check(data.get("registered_by", ""))
        # free text enters the insert-only ledger scrubbed (ids, units and quantities are not free text)
        if isinstance(data.get("statement"), str):
            data["statement"] = scrub(data["statement"])
        if "constraints" in data:
            data["constraints"] = _labels_scrubbed(data["constraints"])
        planner = data.get("planner")
        if isinstance(planner, dict) and isinstance(planner.get("client"), dict):    # a client names itself
            data["planner"] = {**planner, "client": {k: scrub(v) if isinstance(v, str) else v
                                                     for k, v in planner["client"].items()}}
        title = scrub(title) if isinstance(title, str) else title
        cid, rec, created = open_campaign(self.L, data, title, actor=self.actor.label, memory=memory, key=self.key)
        return {"campaign_id": cid, "objective": rec.model_dump(mode="json"), "created": created}

    def define_measurement(self, cid: str, name: str, spec: OperatorSpec | dict) -> dict:
        """Record a derived measurement and apply it, in one transaction, to every baseline record and completed
        result that has the channel: one operator Run and one `derived:<name>` Measurement per target. A threshold
        its window never reaches is an answer, not an error: that target's Run records `not_reached_within` (record
        seconds) and no Measurement."""
        self._allow("define_measurement")
        obj = current_objective(self.L, cid)
        spec = spec if isinstance(spec, OperatorSpec) else OperatorSpec.model_validate(spec)
        data = MeasurementData(name=name, spec=spec, defined_by=self.actor.label)
        if problems := measurement_problems(obj, data):       # before any operator work; admission checks again
            raise AdmissionError("; ".join(problems))
        targets = []
        for ref in list(obj.baseline) + [s.request_id for s in request_states(self.L, cid).values() if s.result]:
            found = self._series(cid, obj, ref, spec.channel)
            if found is None:
                continue
            try:
                targets.append((ref, found[0], loop_compare.measure(spec, found[1])))
            except (NotImplementedError, ValueError) as e:
                raise AdmissionError(f"{name}: {e}") from None
        rid = self._rid("msr", cid, "measurement", chain.natural_id("msr", cid, name))
        ids, missed = [], []
        with self.L.transaction():
            rec, created = chain.write(self.L, cid, "measurement", data, self.actor.label, rid)
            for ref, aid, m in targets:
                run_id = chain.natural_id("run", rec.id, ref)
                mid = chain.natural_id("m", run_id, name)
                reached = m["value"] is not None
                if reached:
                    ids.append(mid)
                else:
                    missed.append({"subject": ref, "not_reached_within": m["not_reached_within"]})
                if not created:
                    continue
                self.L.add(Run(id=run_id, campaign_id=cid, step_id=rec.id, tool=f"operator:{spec.op}",
                               idempotency_key=chain.digest([rec.id, ref, aid]), input_ids=[aid],
                               measurement_ids=[mid] if reached else [],
                               params=spec.model_dump(mode="json") | {"target": ref} |
                               ({} if reached else {"not_reached_within": m["not_reached_within"]}),
                               code_sha=OPS_DIGEST, provider="local", status="ok", started_at=rec.created_at,
                               ended_at=rec.created_at))
                if not reached:
                    continue
                meas = Measurement(id=mid, run_id=run_id, quantity=f"derived:{name}", value=m["value"],
                                   unit=spec.unit, uncertainty=Uncertainty.model_validate(m["uncertainty"]),
                                   subject=ref, artifact_ids=[aid], label=name)
                self.L.add(meas)
                self.L.emit(cid, "measurement.recorded", self.actor.label, name, refs=[mid],
                            data={"value": meas.value, "unit": meas.unit, "measurement": meas.model_dump(mode="json")})
        return {"record": rec.model_dump(mode="json"),
                "measurements": [m.model_dump(mode="json") for i in ids if (m := self.L.get("measurement", i))],
                "not_reached": missed}

    def propose(self, cid: str, action: str, params: dict, *, rationale: str, prediction: dict,
                expected_effect: str, evidence_ids=(), specimen: dict | None = None,
                due_by: float | None = None) -> dict:
        """Seal a request: parameters, rationale and prediction, with units, cost and procedure from the adapter.
        A pending request with the same configuration is returned instead of a second one. A rule the
        registering person wrote may approve it at once."""
        self._allow("propose")
        obj = current_objective(self.L, cid)
        adapter = adapter_for(obj)
        params = bind_params(self.L, cid, obj, adapter, action, dict(params))
        rq = "rq_" + (chain.digest([cid, "request", self.actor.kind, self.key])[:20] if self.key
                      else secrets.token_hex(10))
        # free text is scrubbed before it is sealed: the ledger is insert-only, so a pasted key would stay forever
        req = build_request(adapter, obj, action, params, request_id=rq, rationale=scrub(rationale),
                            prediction=prediction, expected_effect=scrub(expected_effect), evidence_ids=evidence_ids, specimen=specimen,
                            due_by=due_by)
        with self.L.transaction():
            states = request_states(self.L, cid)
            dup = next((s for s in states.values() if s.state in PENDING and
                        s.data.configuration_hash == req.configuration_hash), None)
            if dup is not None:
                return request_view(self.L, dup)
            rec = chain.append(self.L, cid, "request", req, self.actor.label,
                               self._rid("req", cid, "request", chain.natural_id("req", cid, rq, 1)))
            self._auto_approve(cid, obj, rec.data["request_id"])
        return request_view(self.L, self._state(cid, rec.data["request_id"]))

    def _auto_approve(self, cid: str, obj: ObjectiveData, rq: str):
        recs = chain.records(self.L, cid)
        if actor_kind(recs[0].actor) != "person":
            return
        states = request_states(self.L, cid, recs)
        st = states[rq]
        if st.state != "awaiting_approval" or st.executor != "computational":
            return
        for n, rule in enumerate(obj.auto_approve, start=1):
            used = sum(s.approved_by == f"policy:auto/{n}" for s in states.values())
            if rule.executor == st.executor and float(st.data.cost.get("usd", 0.0)) <= rule.max_usd and \
                    float(st.data.cost.get("wall_s", 0.0)) <= rule.max_wall_s and used < rule.max_count:
                chain.append(self.L, cid, "approval",
                             ApprovalData(request_id=rq, record_id=st.record.id, content_hash=st.content_hash,
                                          author=obj.registered_by, note=f"Approved by rule {n} of the objective",
                                          rule=f"auto/{n}"),
                             f"policy:auto/{n}", chain.natural_id("apr", rq, st.content_hash))
                return

    def cancel(self, cid: str, request_id: str, reason: str) -> dict:
        self._allow("cancel")
        st = self._state(cid, request_id)
        try:     # the same cancellation again is the same record; another one of a cancelled request is refused
            chain.append(self.L, cid, "cancellation",
                         CancellationData(request_id=request_id, author=self.actor.name, reason=scrub(reason)),
                         self.actor.label, self._rid("cnl", cid, "cancellation", chain.natural_id("cnl", request_id)))
        except chain.ChainConflict:
            if st.state != "cancelled":
                raise
            raise StaleRequest(f"{request_id} is already cancelled (an amendment withdraws the proposals still "
                               "waiting under the older objective)") from None
        return self.request(cid, request_id)

    def conclude(self, cid: str, text: str, unresolved_reasons=()) -> dict:
        """Conclude with the outcome computed from the comparisons; the caller writes only the text. Comparisons
        still owed are recorded first, in the same transaction."""
        self._allow("conclude")
        with self.L.transaction():
            loop_compare.record_comparisons(self.L, cid)
            outcome, comparison = computed_outcome(self.L, cid)
            rec = chain.append(self.L, cid, "conclusion",
                               ConclusionData(outcome=outcome, comparison_id=comparison, text=scrub(text),
                                              unresolved_reasons=[scrub(r) if isinstance(r, str) else r
                                                                  for r in unresolved_reasons],
                                              author=self.actor.label,
                                              findings=computed_findings(self.L, cid)),     # M2 SEMANTICS: /3
                               self.actor.label, self._rid("con", cid, "conclusion", chain.natural_id("con", cid)))
        return rec.model_dump(mode="json")

    # ------------------------------------------------------------ person only

    def approve(self, cid: str, request_id: str, content_hash: str, note: str = "") -> dict:
        """Approve the version whose hash the person read. A hash of an older version is refused with
        StaleRequest ("This request changed after you read it; review version N")."""
        self._allow("approve")
        st = self._state(cid, request_id)
        record_id = next((r.id for r in st.versions if r.data["content_hash"] == content_hash), st.record.id)
        chain.append(self.L, cid, "approval",
                     ApprovalData(request_id=request_id, record_id=record_id, content_hash=content_hash,
                                  author=self.actor.name, note=scrub(note)),
                     self.actor.label,
                     self._rid("apr", cid, "approval", chain.natural_id("apr", request_id, content_hash)))
        return self.request(cid, request_id)

    def reject(self, cid: str, request_id: str, content_hash: str, reason: str) -> dict:
        self._allow("reject")
        st = self._state(cid, request_id)
        record_id = next((r.id for r in st.versions if r.data["content_hash"] == content_hash), st.record.id)
        chain.append(self.L, cid, "rejection",
                     RejectionData(request_id=request_id, record_id=record_id, content_hash=content_hash,
                                   author=self.actor.name, reason=scrub(reason)),
                     self.actor.label,
                     self._rid("rej", cid, "rejection", chain.natural_id("rej", request_id, content_hash)))
        return self.request(cid, request_id)

    def revise(self, cid: str, request_id: str, params: dict, reason: str, *, approve: bool = False) -> dict:
        """An engineer's correction: the given parameters replace the latest version's, as version n + 1 by this
        person. The agent's prediction was for other parameters, so the new version carries none; the reason is
        appended to the rationale. With approve=True the new version is approved in the same transaction."""
        reason = scrub(reason)
        self._allow("revise")
        st = self._state(cid, request_id)
        obj = current_objective(self.L, cid)
        d = st.data
        adapter = adapter_for(obj)
        bound = set((obj.system.get("spec", {}).get("objective_template") or {}).get("bound_parameters", {})
                    .get(d.action, ()))
        mine = bind_params(self.L, cid, obj, adapter, d.action,
                           {k: v for k, v in {**d.params, **params}.items() if k not in bound})
        req = build_request(adapter, obj, d.action, mine, request_id=request_id,
                            rationale=f"{d.rationale}\n\nRevised by {self.actor.name}: {reason}", prediction={},
                            expected_effect=d.expected_effect, evidence_ids=d.evidence_ids, specimen=d.specimen,
                            version=d.version + 1, supersedes=st.record.id, due_by=d.due_by)
        with self.L.transaction():
            rec = chain.append(self.L, cid, "request", req, self.actor.label,
                               self._rid("req", cid, "request",
                                         chain.natural_id("req", cid, request_id, req.version)))
            if approve:
                chain.append(self.L, cid, "approval",
                             ApprovalData(request_id=request_id, record_id=rec.id, content_hash=req.content_hash,
                                          author=self.actor.name, note=f"Approved with the revision: {reason}"),
                             self.actor.label,
                             self._rid("apr", cid, "approval", chain.natural_id("apr", request_id, req.content_hash)))
        return self.request(cid, request_id)

    def submit_result(self, cid: str, upload: HumanUpload | dict, files: dict[str, Path]) -> dict:
        """A person returns a physical test's result. Accepted: the request completes. Refused (not approved,
        cancelled, wrong version, wrong unit or configuration, reused specimen, already completed...): a
        `result_rejected` record holds the reasons and ResultRefused is raised; the request is unchanged. An
        identical re-upload of the accepted result returns it without a new record."""
        self._allow("submit_result")
        upload = upload if isinstance(upload, HumanUpload) else HumanUpload.model_validate(upload)
        self._name_check(upload.author)
        files = {n: Path(p) for n, p in files.items()}
        st = self._state(cid, upload.request_id)
        adapter = adapter_for(current_objective(self.L, cid))
        sha = upload_sha256(upload, files)
        reasons = self._intake_reasons(st, sha)
        if reasons is None:
            return request_view(self.L, st)
        if not reasons:
            parsed = parse_human_result(st, adapter, upload, files)
            if isinstance(parsed, list):
                reasons = parsed
            else:
                try:
                    ingest_output(self.L, cid, st, parsed, adapter=adapter, basis="physical",
                                  submitted_by=self.actor.label, attempt=None, payload_sha256=sha,
                                  specimen_id=upload.specimen_id, uncertainties=human_uncertainties(upload))
                    return self.request(cid, upload.request_id)
                except ResultInvalid as e:
                    reasons = e.reasons
                except AdmissionError as e:     # the state moved while the files were stored
                    reasons = self._intake_reasons(self._state(cid, upload.request_id), sha)
                    if reasons is None:
                        return self.request(cid, upload.request_id)
                    reasons = reasons or [str(e)]
        raise ResultRefused(reasons, self._refuse_result(cid, upload, sha, reasons))

    def _intake_reasons(self, st: RequestState, sha: str) -> list[str] | None:
        """Why the request cannot take a result now ([] when it can; None for an identical re-upload)."""
        if st.executor != "human":
            return [f"request {st.request_id} is {st.executor}; its result does not come from an upload"]
        if st.state == "completed":
            run = self.L.get("run", st.result.data["run_id"])
            if run is not None and run.params.get("payload_sha256") == sha:
                return None
            return [f"already completed by {st.result.id}; record a new request to repeat the test"]
        if st.state == "awaiting_approval":
            return ["the request is not approved"]
        if st.state in ("cancelled", "rejected"):
            return [f"the request was {st.state} at seq {st.closed_by.seq}"]
        if st.state != "awaiting_result":
            return [f"the request is {st.state}"]
        return []

    def _refuse_result(self, cid: str, upload: HumanUpload, sha: str, reasons: list[str]):
        rid = chain.natural_id("rrj", upload.request_id, sha)
        data = {"request_id": upload.request_id, "claimed_hash": upload.content_hash, "payload_sha256": sha,
                "reasons": reasons, "submitted_by": self.actor.label}
        try:
            return chain.append(self.L, cid, "result_rejected", data, RESULTS, rid)
        except chain.ChainConflict:            # this exact upload was refused before, for reasons then current
            return self.L.get("loop", rid)

    def amend(self, cid: str, reason: str, *, target=None, constraints=None, budget=None) -> dict:
        """A new objective version with a changed target, constraints and/or budget; every completed result is
        compared again under it, in the same transaction. Lowering a budget is any person's; raising one needs
        may_raise_budget (an administrator), and a model budget stays within the provider caps."""
        self._allow("amend")
        obj = current_objective(self.L, cid)
        if budget is not None:
            if (raised := budget_raises(obj.budget, budget)) and not self.may_raise_budget:
                raise NotAllowed(f"Raising a budget ({', '.join(raised)}) needs an administrator key; an engineer "
                                 "may lower one")
            if isinstance(budget, dict) and "model_usd" in budget:
                from translucent.loop_surface import budget_problem
                from translucent.runtime import PROFILES
                profile = obj.planner.get("profile")
                if problem := budget_problem(budget["model_usd"], profile if profile in PROFILES else "meta",
                                             "budget.model_usd"):
                    raise AdmissionError(problem)
        with self.L.transaction():
            rec = chain.append(self.L, cid, "amendment",
                               AmendmentData(version=obj.version + 1, author=self.actor.name, reason=scrub(reason),
                                             target=target, constraints=_labels_scrubbed(constraints),
                                             budget=budget),
                               self.actor.label,
                               self._rid("amd", cid, "amendment", chain.natural_id("amd", cid, obj.version + 1)))
            comparisons = loop_compare.record_comparisons(self.L, cid)
        return {"amendment": rec.model_dump(mode="json"),
                "comparisons": [c.model_dump(mode="json") for c in comparisons]}

    def note(self, cid: str, text: str) -> dict:
        self._allow("note")
        current_objective(self.L, cid)
        text = scrub(text)
        return self.L.emit(cid, "human.note", self.actor.label, text, detail=text).model_dump(mode="json")

    # ------------------------------------------------------------ system only

    def record_comparisons(self, cid: str) -> list[dict]:
        """Record the comparisons still owed (loop_compare.record_comparisons); results and amendments already do."""
        self._allow("record_comparison")
        current_objective(self.L, cid)
        return [c.model_dump(mode="json") for c in loop_compare.record_comparisons(self.L, cid)]
