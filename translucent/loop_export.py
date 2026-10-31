"""A recorded loop campaign: one self-contained `<name>.loop.json` (design §2.14).

    {"loop_export_version": 1,
     "view": LoopView with source.kind "recorded",
     "receipt": {"campaign_id", "ledger_id", "ledger": EmbeddedEvidenceFile, "blobs": {artifact id: ...}}}

The receipt embeds a SQLite ledger holding only this campaign's evidence, in its original order: the Campaign record,
its loop records, its runs and their measurements, the datasets its objective names, every artifact they reach
(transform parents included) and all of the campaign's events; plus those artifacts' bytes. `source.sha256` is the
digest of the receipt (canonical JSON), so the view names the evidence it was computed from.

`verify_export` (reached through `translucent verify <file>.loop.json`) restores the receipt into a scratch folder,
runs the full ledger verify on it (loop checks included), then recomputes the view from it with the file's own
source and requires it to equal the stored view: one edited byte in the view is a problem.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from translucent import chain, loop_view
from translucent.contracts import EmbeddedEvidenceFile
from translucent.intervention_export import MAX_TOTAL_BYTES, _pack, _unpack
from translucent.ledger import Ledger
from translucent.loop import current_objective

EXPORT_VERSION = 1


def evidence_ids(L: Ledger, cid: str) -> tuple[set[str], set[str]]:
    """(record ids, artifact ids) of everything the campaign's view and verify read."""
    obj = current_objective(L, cid)
    records = chain.records(L, cid)
    runs = L.list("run", campaign_id=cid)
    measurements = [m for run in runs for m in L.list("measurement", ref=run.id)]
    datasets = [d for did in obj.datasets if (d := L.get("dataset", did)) is not None]
    found: set[str] = set()

    def walk(value):
        if isinstance(value, str):
            if value.startswith("sha256:") and L.kind_of(value) == "artifact":
                found.add(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)

    for rec in [*records, *runs, *measurements, *datasets]:
        walk(rec.model_dump(mode="json"))
    pending = list(found)
    while pending:
        art = L.get("artifact", pending.pop())
        for parent in art.transform.parent_ids if art and art.transform else []:
            if parent not in found and L.kind_of(parent) == "artifact":
                found.add(parent)
                pending.append(parent)
    ids = {cid} | {r.id for r in records} | {r.id for r in runs} | {m.id for m in measurements} | \
        {d.id for d in datasets} | found
    return ids, found


def export(L: Ledger, cid: str, path: Path | str, *, ledger: str | None = None) -> Path:
    """Write the campaign as `<path>` (a .json file) or `<path>/<cid>.loop.json`; returns the file written."""
    from translucent.verify import copy_snapshot

    path = Path(path)
    if path.suffix.lower() != ".json":
        path = path / f"{cid}.loop.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger = ledger or L.root.name
    own_transaction = not L.db.in_transaction
    if own_transaction:                     # one read transaction: the view and the receipt see the same records
        L.db.execute("BEGIN")
    try:
        ids, artifacts = evidence_ids(L, cid)
        with tempfile.TemporaryDirectory(prefix="translucent-loop-export-") as scratch:
            db = Path(scratch) / "ledger.sqlite"
            copy_snapshot(L, db, cid, ids)
            # zlib's usual level 6: level 9 (Python gzip's default) costs far more CPU for a few percent smaller
            receipt = {"campaign_id": cid, "ledger_id": ledger, "ledger": _pack(db.read_bytes(), 6).model_dump(),
                       "blobs": {aid: _pack(L.blob_path(aid).read_bytes(), 6).model_dump()
                                 for aid in sorted(artifacts)}}
        if receipt["ledger"]["bytes"] + sum(b["bytes"] for b in receipt["blobs"].values()) > MAX_TOTAL_BYTES:
            raise ValueError("Embedded receipt exceeds total size limit")
        source = {"kind": "recorded", "file": path.name, "sha256": chain.digest(receipt),
                  "exported_at": time.time(), "verified": None}
        out = {"loop_export_version": EXPORT_VERSION, "view": loop_view.view(L, cid, source, ledger=ledger),
               "receipt": receipt}
    finally:
        if own_transaction:
            L.db.execute("ROLLBACK")
    path.write_text(json.dumps(out, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    return path


def verify_export(path: Path | str, result, data: dict | None = None):
    """Restore the receipt, verify its ledger, and recompute the view (ignoring source) from it."""
    from translucent.verify import _ledger, read_ledger

    data = json.loads(Path(path).read_text(encoding="utf-8")) if data is None else data
    if data.get("loop_export_version") != EXPORT_VERSION:
        raise ValueError(f"Loop export version {data.get('loop_export_version')!r} is not one this verify reads")
    receipt, view = data["receipt"], data["view"]
    cid = receipt["campaign_id"]
    if not isinstance(view, dict) or (view.get("campaign") or {}).get("id") != cid:
        raise ValueError("The export's view and receipt name different campaigns")
    ledger_file = EmbeddedEvidenceFile.model_validate(receipt["ledger"])
    blobs = {aid: EmbeddedEvidenceFile.model_validate(f) for aid, f in receipt["blobs"].items()}
    if ledger_file.bytes + sum(b.bytes for b in blobs.values()) > MAX_TOTAL_BYTES:
        raise ValueError("Embedded receipt exceeds total size limit")
    with tempfile.TemporaryDirectory(prefix="translucent-loop-verify-") as scratch:
        root = Path(scratch)
        (root / "ledger.sqlite").write_bytes(_unpack(ledger_file))
        for aid, file in blobs.items():
            hexd = aid.removeprefix("sha256:")
            if aid != "sha256:" + hexd or len(hexd) != 64 or any(c not in "0123456789abcdef" for c in hexd) or \
                    file.sha256 != hexd:
                raise ValueError("Malformed embedded artifact address")
            target = root / "blobs" / hexd[:2] / hexd
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_unpack(file))
        _ledger(root, result, cid)
        source = view.get("source") or {}
        if source.get("kind") != "recorded":
            result.problems.append("The exported view is not marked as a recorded campaign")
        if source.get("sha256") != chain.digest(receipt):
            result.problems.append("The exported view names a different receipt than the one embedded")
        with read_ledger(root) as L:
            expected = loop_view.view(L, cid, source, ledger=receipt.get("ledger_id"))
        if json.loads(json.dumps(expected)) != view:
            result.problems.append("Loop export view differs from the recorded evidence")
    return result
