"""The loop campaign hash chain: canonical JSON, digests, deterministic ids, append and admission.

Every loop record is appended by `append` inside one `Ledger.transaction()` (BEGIN IMMEDIATE), which:
  * looks the id up first: the same record under the same id is returned unchanged (idempotent replay), a different
    one is refused with ChainConflict, so the SQLite primary key de-duplicates across processes;
  * links to the digest of the campaign's latest loop record and takes the next event seq of the campaign;
  * runs admission (chain rules, then the hooks registered for the record's type) with state re-derived inside the
    transaction, so check-then-act is atomic across processes;
  * inserts the record and its `loop.recorded` event at the same seq and the same clamped time.

The chain covers loop records only. Other events of the campaign take seq numbers between them; each loop record's
event is at its own seq with refs == [id] and data.record == the record.
"""
from __future__ import annotations

import hashlib
import importlib
import json
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from translucent.contracts import Event
from translucent.ledger import AdmissionError, Ledger
from translucent.loop_contracts import PAYLOADS, LoopRecord, RequestData

# Plain-language event titles, one per record type.
TITLES = {
    "objective": "Objective registered", "amendment": "Objective amended",
    "measurement": "New measurement defined", "request": "Experiment requested",
    "approval": "Experiment approved", "rejection": "Experiment rejected", "cancellation": "Experiment cancelled",
    "execution": "Experiment execution updated", "result": "Experiment result recorded",
    "result_rejected": "Experiment result refused", "failure": "Experiment failed",
    "comparison": "Result compared with the baseline", "conclusion": "Campaign concluded",
    "planner": "Planner step recorded",
}

# Record types still accepted after the conclusion: the receipts of the planner turn that wrote it.
AFTER_CONCLUSION = frozenset({"planner"})


class ChainConflict(AdmissionError):
    """A different record already has this id (HTTP 409): same key, different body."""


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def record_digest(rec: LoopRecord) -> str:
    return digest(rec.model_dump(mode="json", exclude={"digest"}))


def content_hash(data: dict | RequestData) -> str:
    """A request's hash: everything the approver saw, i.e. the payload without its content_hash. Taken over the
    RequestData JSON form (defaults filled in), the form the record stores, so the raw dict a caller built and the
    stored record give the same hash."""
    if not isinstance(data, RequestData):
        data = RequestData.model_validate({**data, "content_hash": ""})
    body = data.model_dump(mode="json")
    body.pop("content_hash")
    return digest(body)


def natural_id(prefix: str, *parts) -> str:
    """Deterministic record id, e.g. natural_id("apr", request_id, content_hash)."""
    return f"{prefix}_{digest(list(parts))[:24]}"


def key_id(prefix: str, cid: str, type: str, actor_kind: str, key: str) -> str:
    """Record id for a write carrying a client idempotency key (HTTP Idempotency-Key, MCP idempotency_key)."""
    return natural_id(prefix, cid, type, actor_kind, key)


def payload(type: str, data: dict | BaseModel) -> dict:
    """`data` validated against the payload model for `type`, in the JSON form the record stores."""
    model = PAYLOADS.get(type)
    if model is None:
        raise AdmissionError(f"unknown loop record type {type!r}")
    try:
        validated = data if isinstance(data, model) else model.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, x['loc'])) or type}: {x['msg']}" for x in e.errors())
        raise AdmissionError(f"{type} payload: {problems}") from None
    try:  # free-form dicts accept NaN and inf, and JSON mode would silently write them as null
        canonical(validated.model_dump())
    except ValueError:
        raise AdmissionError(f"{type} payload: numbers must be finite") from None
    return validated.model_dump(mode="json")


# ------------------------------------------------------------------ per-type admission hooks

Hook = Callable[[Ledger, LoopRecord, BaseModel], None]
_HOOKS: dict[str, list[Hook]] = {}

# Modules that register hooks when imported. Admission imports them first, so a process that writes loop records
# without having imported the lifecycle module still runs every check.
HOOK_MODULES: tuple[str, ...] = ("translucent.loop", "translucent.loop_compare")


def admission(*types: str):
    """Decorator: run `fn(L, rec, payload_model)` when a loop record of one of `types` is admitted. Raise
    AdmissionError to refuse. Hooks run inside the append transaction, after the chain checks."""
    for t in types:
        if t not in PAYLOADS:
            raise ValueError(f"unknown loop record type {t!r}")

    def register(fn: Hook) -> Hook:
        for t in types:
            hooks = _HOOKS.setdefault(t, [])
            name = (fn.__module__, fn.__qualname__)
            hooks[:] = [h for h in hooks if (h.__module__, h.__qualname__) != name] + [fn]  # re-import replaces
        return fn
    return register


def hooks(type: str) -> list[Hook]:
    for module in HOOK_MODULES:
        importlib.import_module(module)
    return list(_HOOKS.get(type, ()))


# ------------------------------------------------------------------ reading the chain

def records(L: Ledger, cid: str) -> list[LoopRecord]:
    rows = L.db.execute("SELECT body FROM record WHERE kind='loop' AND campaign_id=? "
                        "ORDER BY json_extract(body,'$.seq')", (cid,))
    return [LoopRecord.model_validate_json(b) for (b,) in rows]


def latest(L: Ledger, cid: str) -> LoopRecord | None:
    row = L.db.execute("SELECT body FROM record WHERE kind='loop' AND campaign_id=? "
                       "ORDER BY json_extract(body,'$.seq') DESC LIMIT 1", (cid,)).fetchone()
    return LoopRecord.model_validate_json(row[0]) if row else None


def concluded(L: Ledger, cid: str) -> LoopRecord | None:
    row = L.db.execute("SELECT body FROM record WHERE kind='loop' AND campaign_id=? "
                       "AND json_extract(body,'$.type')='conclusion' LIMIT 1", (cid,)).fetchone()
    return LoopRecord.model_validate_json(row[0]) if row else None


def problems(L: Ledger, cid: str) -> list[str]:
    """Offline chain check for one campaign: digests, links, payloads, and each record's mirror event."""
    out, previous = [], ""
    for rec in records(L, cid):
        if rec.digest != record_digest(rec):
            out.append(f"Loop record {rec.id} does not match its digest")
        if rec.previous_hash != previous:
            out.append(f"Loop record {rec.id} does not link to the record before it")
        try:
            if payload(rec.type, rec.data) != rec.data:
                out.append(f"Loop record {rec.id} is not in its payload's canonical form")
        except AdmissionError as e:
            out.append(f"Loop record {rec.id}: {e}")
        row = L.db.execute("SELECT body FROM event WHERE campaign_id=? AND seq=?", (cid, rec.seq)).fetchone()
        ev = Event.model_validate_json(row[0]) if row else None
        if ev is None or ev.type != "loop.recorded" or ev.refs != [rec.id] or \
                ev.data.get("record") != rec.model_dump(mode="json") or ev.t != rec.created_at:
            out.append(f"Loop record {rec.id} differs from its event at seq {rec.seq}")
        previous = rec.digest
    return out


# ------------------------------------------------------------------ admission and append

def admit(L: Ledger, rec: LoopRecord):
    """Chain admission, called by Ledger.add for every loop record (inside chain.append's transaction)."""
    if not L.in_transaction():
        raise AdmissionError(f"loop record {rec.id}: loop records are written with chain.append")
    L._require([rec.campaign_id], ("campaign",), "loop campaign")
    data = payload(rec.type, rec.data)
    if data != rec.data:
        raise AdmissionError(f"loop record {rec.id}: data is not in the {rec.type} payload's canonical form")
    previous = latest(L, rec.campaign_id)
    if rec.digest != record_digest(rec) or rec.previous_hash != (previous.digest if previous else ""):
        raise AdmissionError(f"loop record {rec.id}: the hash chain differs")
    (last,) = L.db.execute("SELECT COALESCE(MAX(seq), 0) FROM event WHERE campaign_id=?",
                           (rec.campaign_id,)).fetchone()
    if rec.seq != last + 1:
        raise AdmissionError(f"loop record {rec.id}: seq {rec.seq} is not the campaign's next event ({last + 1})")
    if (previous is None) != (rec.type == "objective"):
        raise AdmissionError(f"loop record {rec.id}: " + ("the objective is registered once, first" if previous
                                                           else "a loop campaign starts with its objective"))
    if rec.type not in AFTER_CONCLUSION and (done := concluded(L, rec.campaign_id)):
        raise AdmissionError(f"loop record {rec.id}: the campaign concluded at seq {done.seq}")
    model = PAYLOADS[rec.type].model_validate(data)
    for hook in hooks(rec.type):
        hook(L, rec, model)


def write(L: Ledger, cid: str, type: str, data: dict | BaseModel, actor: str, rid: str) -> tuple[LoopRecord, bool]:
    """Append one loop record; (record, True) when inserted, (existing record, False) on an identical replay."""
    body = payload(type, data)
    with L.transaction():
        row = L.db.execute("SELECT kind, body FROM record WHERE id=?", (rid,)).fetchone()
        if row is not None:
            existing = LoopRecord.model_validate_json(row[1]) if row[0] == "loop" else None
            if existing is None or (existing.campaign_id, existing.type, existing.actor, existing.data) != \
                    (cid, type, actor, body):
                raise ChainConflict(f"{rid} already records something else; use a new id or key")
            return existing, False
        previous = latest(L, cid)
        seq, t = L.next_event(cid)
        rec = LoopRecord(id=rid, campaign_id=cid, type=type, seq=seq, actor=actor, data=body, created_at=t,
                         previous_hash=previous.digest if previous else "", digest="")
        rec = rec.model_copy(update={"digest": record_digest(rec)})
        L.add(rec)
        L.write_event(Event(seq=seq, campaign_id=cid, t=t, type="loop.recorded", actor=actor, title=TITLES[type],
                            refs=[rid], data={"record": rec.model_dump(mode="json")}))
    return rec, True


def append(L: Ledger, cid: str, type: str, data: dict | BaseModel, actor: str, rid: str) -> LoopRecord:
    """Append one loop record, or return the identical record already stored under `rid`."""
    return write(L, cid, type, data, actor, rid)[0]


def typed(rec: LoopRecord) -> Any:
    """The record's payload as its model (RequestData, ApprovalData, ...)."""
    return PAYLOADS[rec.type].model_validate(rec.data)
