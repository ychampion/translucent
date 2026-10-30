"""Self-contained intervention projection and receipt, within ReplayBundle v2.

The compact event projection names each original record's digest. The compressed
SQLite receipt retains the complete record and event bytes and every source blob.
It contains no private sealed-store file. Unrevealed outcomes stay absent.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path
import tempfile
import zlib

from translucent.contracts import (
    EmbeddedEvidenceFile,
    InterventionBundle,
    InterventionReceipt,
    ReplayBundle,
)
from translucent.intervention import get, protocol, records

MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024


def artifact_ids(L, cid):
    """Closure of content-addressed references, including transform parents."""
    found = set()

    def walk(value):
        if (
            isinstance(value, str)
            and value.startswith("sha256:")
            and L.kind_of(value) == "artifact"
        ):
            found.add(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    for r in records(L, cid):
        walk(r.data)
    pending = list(found)
    while pending:
        a = L.get("artifact", pending.pop())
        for parent in a.transform.parent_ids if a.transform else []:
            if parent not in found:
                found.add(parent)
                pending.append(parent)
    return found


def projection(L, cid):
    from translucent.intervention_bench import funnel_counts, observations
    from translucent.systems.nist_heat import SYSTEM

    rs = records(L, cid)
    if not rs:
        raise ValueError("Campaign is not an intervention campaign")
    p = protocol(L, cid)
    bytype = {}
    for r in rs:
        bytype.setdefault(r.type, []).append(r)
    revealed = {r.data["trial_id"]: r for r in bytype.get("reveal", [])}
    trials = []
    for t in bytype.get("trial", []):
        r = revealed.get(t.id)
        d = t.data
        feedback = r.data if r else {}
        trials.append(
            {
                **d,
                "id": t.id,
                "t_ignition": feedback.get("t_ignition"),
                "truncated_at": feedback.get("truncated_at"),
                "outcome": feedback.get("outcome"),
                "revealed_seq": r.seq if r else None,
                "corroboration": feedback.get("corroboration"),
                "corroboration_sources": feedback.get("corroboration_sources", []),
                "corroboration_source": feedback.get("corroboration_source"),
                "flags": sorted(set(d["flags"]) | set(feedback.get("flags", []))),
                "event_sources": {
                    **d["event_sources"],
                    **feedback.get("event_sources", {}),
                },
            }
        )
    policies = [
        dict(id=r.id, **r.data, proposed_seq=r.seq) for r in bytype.get("policy", [])
    ]
    decisions = []
    for policy in bytype.get("policy", []):
        for split in p.data["splits"]:
            # In-progress exports carry committed but unrevealed actions too.
            complete = all(
                d.data["trial_id"] in revealed
                for d in bytype.get("decision", [])
                if d.data["scored"]
                and d.data["policy_id"] == policy.id
                and d.data["split"] == split
            )
            if complete:
                for row in observations(L, cid, policy.id, split):
                    initial = get(L, cid, row["decision_id"], "decision")
                    terminal = get(L, cid, row["final_decision_id"], "decision")
                    decisions.append(
                        dict(
                            id=initial.id,
                            trial_id=initial.data["trial_id"],
                            policy_id=policy.id,
                            split=split,
                            p_ignite=row["p"],
                            interval=initial.data["interval"]
                            if row["ignited_during_watch"]
                            else terminal.data["interval"],
                            decision=row["action"],
                            initial_action=initial.data["decision"],
                            observe_s=row["observe_s"],
                            watch_time_s=row["watch_time_s"],
                            ignited_during_watch=row["ignited_during_watch"],
                            committed_seq=row["committed_seq"],
                            terminal_committed_seq=row["final_committed_seq"],
                            revealed_seq=row["revealed_seq"],
                            correct=row["correct"],
                            unsafe=row["unsafe"],
                            fit_id=initial.data["fit_id"],
                            final_decision_id=terminal.id,
                        )
                    )
                    decisions[-1]["simulated"] = initial.data["simulated"]
                    if initial.data.get("model_receipt_id"):
                        decisions[-1]["model_receipt_id"] = initial.data[
                            "model_receipt_id"
                        ]
                        decisions[-1]["terminal_model_receipt_id"] = terminal.data[
                            "model_receipt_id"
                        ]
            else:
                for d in bytype.get("decision", []):
                    if (
                        d.data["scored"]
                        and d.data["policy_id"] == policy.id
                        and d.data["split"] == split
                    ):
                        decisions.append(
                            dict(
                                id=d.id,
                                **d.data,
                                committed_seq=d.seq,
                                revealed_seq=revealed[d.data["trial_id"]].seq
                                if d.data["trial_id"] in revealed
                                else None,
                                correct=None,
                            )
                        )

    def items(kind):
        return [dict(id=r.id, **r.data, seq=r.seq) for r in bytype.get(kind, [])]

    system = {
        **SYSTEM,
        "decision_point": f"Heater cut reconstructed with {p.data.get('heater_off_lag_s', 10):g} s thermocouple lag; optional paid 30/60 s recorded watch",
    }
    return InterventionBundle(
        system=system,
        protocol={
            **p.data,
            "registered_seq": p.seq,
            "funnel": {**p.data["funnel"], **funnel_counts(L, cid)},
        },
        trials=trials,
        policies=policies,
        decisions=decisions,
        hypotheses=items("hypothesis"),
        scores=items("score"),
        comparisons=items("comparison"),
        updates=items("update"),
        folds=items("fold"),
        claims=items("claim"),
        reviews=items("review"),
        references=items("reference"),
        physics_priors=items("physics_prior"),
        procedure=bytype["procedure"][-1].data if bytype.get("procedure") else None,
        next_experiments=items("next_experiment"),
    )


def bundle(L, cid):
    """A deterministic view; never calls a model or re-fits an outcome predictor."""
    c = L.get("campaign", cid)
    if c is None:
        raise ValueError("Campaign not found")
    events = []
    for event in L.events(cid):
        if event.type == "intervention.recorded":
            r = event.data["record"]
            event = event.model_copy(
                update={
                    "data": {
                        "record_id": r["id"],
                        "record_type": r["type"],
                        "digest": r["digest"],
                    }
                }
            )
        events.append(event)
    from translucent.replay import extra

    ids = artifact_ids(L, cid)
    return ReplayBundle(
        campaign=c,
        video="",
        video_size=(0, 0),
        fps=0,
        events=events,
        artifacts=[a for a in L.list("artifact") if a.id in ids],
        intervention=projection(L, cid),
        **extra(L, cid),
    )


def _pack(data):
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Evidence file exceeds the embedded receipt limit")
    return EmbeddedEvidenceFile(
        sha256=hashlib.sha256(data).hexdigest(),
        bytes=len(data),
        data=base64.b64encode(gzip.compress(data, mtime=0)).decode("ascii"),
    )


def _unpack(file):
    if not 0 <= file.bytes <= MAX_FILE_BYTES:
        raise ValueError("Evidence file size exceeds the embedded receipt limit")
    try:
        packed = base64.b64decode(file.data, validate=True)
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        data = decoder.decompress(packed, file.bytes + 1)
        if not decoder.eof or decoder.unused_data or len(data) != file.bytes:
            raise ValueError("Embedded evidence size differs")
    except (ValueError, zlib.error) as exc:
        raise ValueError("Malformed compressed embedded evidence") from exc
    if hashlib.sha256(data).hexdigest() != file.sha256:
        raise ValueError("Embedded evidence hash differs")
    return data


def export(L, cid, path, *, include_verification=True):
    """Write one JSON file or OUT/bundle.json, using the existing receipt copier."""
    from translucent.verify import copy_snapshot

    path = Path(path)
    if path.suffix.lower() != ".json":
        path = path / "bundle.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # One read transaction binds the visible projection to the receipt's records.
    own_transaction = not L.db.in_transaction
    if own_transaction:
        L.db.execute("BEGIN")
    try:
        result = bundle(L, cid)
        if include_verification:
            ids = {cid} | artifact_ids(L, cid) | {r.id for r in records(L, cid)}
            ids |= {r.id for r in L.list("protocol", campaign_id=cid)}
            with tempfile.TemporaryDirectory(
                prefix="translucent-intervention-export-"
            ) as scratch:
                db = Path(scratch) / "ledger.sqlite"
                copy_snapshot(L, db, cid, ids)
                receipt = InterventionReceipt(
                    campaign_id=cid,
                    ledger=_pack(db.read_bytes()),
                    blobs={
                        a.id: _pack(L.blob_path(a.id).read_bytes())
                        for a in result.artifacts
                    },
                )
            if (
                receipt.ledger.bytes + sum(b.bytes for b in receipt.blobs.values())
                > MAX_TOTAL_BYTES
            ):
                raise ValueError("Embedded receipt exceeds total size limit")
            result = result.model_copy(
                update={
                    "intervention": result.intervention.model_copy(
                        update={"receipt": receipt}
                    )
                }
            )
        path.write_text(result.model_dump_json(), encoding="utf-8")
        return result
    finally:
        if own_transaction:
            L.db.execute("ROLLBACK")


def verify_bundle(path, result):
    """Restore fixed, content-addressed paths only; compare every projected field."""
    from translucent.verify import _ledger, read_ledger
    from translucent.intervention_verify_cache import verification_cache

    candidate = ReplayBundle.model_validate_json(Path(path).read_text(encoding="utf-8"))
    payload = candidate.intervention
    receipt = payload.receipt if payload else None
    if receipt is None:
        raise ValueError("Intervention bundle lacks an embedded verification receipt")
    if receipt.campaign_id != candidate.campaign.id:
        raise ValueError("Embedded receipt campaign differs")
    if (
        receipt.ledger.bytes + sum(b.bytes for b in receipt.blobs.values())
        > MAX_TOTAL_BYTES
    ):
        raise ValueError("Embedded receipt exceeds total size limit")
    with tempfile.TemporaryDirectory(
        prefix="translucent-intervention-verify-"
    ) as scratch:
        root = Path(scratch)
        (root / "ledger.sqlite").write_bytes(_unpack(receipt.ledger))
        for aid, file in receipt.blobs.items():
            hexd = aid.removeprefix("sha256:")
            if (
                aid != "sha256:" + hexd
                or len(hexd) != 64
                or any(c not in "0123456789abcdef" for c in hexd)
                or file.sha256 != hexd
            ):
                raise ValueError("Malformed embedded artifact address")
            target = root / "blobs" / hexd[:2] / hexd
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_unpack(file))
        _ledger(root, result, candidate.campaign.id)
        with read_ledger(root) as L:
            with verification_cache(L):
                expected = bundle(L, candidate.campaign.id)
            comparable = candidate.model_copy(
                update={"intervention": payload.model_copy(update={"receipt": None})}
            )
            if comparable != expected:
                result.problems.append(
                    "Intervention bundle projection differs from the recorded evidence"
                )
    return result
