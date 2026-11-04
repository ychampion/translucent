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
    from translucent.records import imported
    # the objective's datasets and those the campaign imported from its uploads (an investigation from records):
    # verify re-checks every record test against its dataset, so both travel with the export
    dids = [*obj.datasets, *(d for d in imported(L, cid, records) if d not in obj.datasets)]
    datasets = [d for did in dids if (d := L.get("dataset", did)) is not None]
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
            legacy = current_objective(L, cid).evaluator in LEGACY_EVALUATORS
        expected = json.loads(json.dumps(expected))
        # Exported before milestone 2: its view has none of the added fields. Only a loop-compare/1 or /2 campaign can
        # have been; a /3 export always carries its assessment and findings, so removing them is a difference.
        if legacy and "assessment" not in view:
            expected = without_m2_fields(expected)
        expected = without_later_empty_fields(expected, view)
        if expected != view:
            result.problems.append("Loop export view differs from the recorded evidence")
    return result


# Optional view fields milestone 2 added (loop_view). An export written before them is compared without them; every
# field it does hold is still recomputed from its receipt and compared exactly.
LEGACY_EVALUATORS = ("loop-compare/1", "loop-compare/2")     # the evaluators that existed before milestone 2
M2_OBJECTIVE_FIELDS = ("kind", "summary", "statement_version")
M2_REQUEST_FIELDS = ("role", "revision")


def without_m2_fields(view: dict) -> dict:
    out = {k: v for k, v in view.items() if k != "assessment"}
    if isinstance(out.get("objective"), dict):
        out["objective"] = {k: v for k, v in out["objective"].items() if k not in M2_OBJECTIVE_FIELDS}
    out["requests"] = [{k: v for k, v in r.items() if k not in M2_REQUEST_FIELDS} if isinstance(r, dict) else r
                       for r in out.get("requests", [])]
    if isinstance(out.get("conclusion"), dict):
        out["conclusion"] = {k: v for k, v in out["conclusion"].items() if k != "findings"}
    return out


def without_later_empty_fields(expected: dict, stored: dict) -> dict:
    """The learning summary's `model_gap` and each imported record's `import_notes` were added to the view after
    exports already existed. An export written before them lacks the key; the recomputed view is compared without it
    only where the stored view lacks it and the value recomputed from the receipt is empty (None, []), so no model
    gap and no import note can be dropped from an export without verify noticing."""
    def empty(value) -> bool:
        return value is None or value == []

    out = dict(expected)
    learning, kept = out.get("learning"), stored.get("learning")
    if isinstance(learning, dict) and isinstance(kept, dict) and "model_gap" not in kept and \
            empty(learning.get("model_gap", [])):
        out["learning"] = {k: v for k, v in learning.items() if k != "model_gap"}
    inv, kept = out.get("investigation"), stored.get("investigation")
    if isinstance(inv, dict) and isinstance(kept, dict) and isinstance(inv.get("instrument_facts"), list) and \
            isinstance(kept.get("instrument_facts"), list) and len(inv["instrument_facts"]) == len(kept["instrument_facts"]):
        out["investigation"] = dict(inv, instrument_facts=[
            {k: v for k, v in f.items() if k != "import_notes"}
            if isinstance(f, dict) and isinstance(g, dict) and "import_notes" not in g and empty(f.get("import_notes", []))
            else f
            for f, g in zip(inv["instrument_facts"], kept["instrument_facts"])])
    return out
