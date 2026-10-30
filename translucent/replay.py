"""Export a campaign from the ledger as a ReplayBundle (what the web UI loads). Never re-calls a model or SAM.

Usage: uv run python -m translucent.replay <ledger_dir> <campaign_id> <out_dir>
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from bisect import bisect_left
from pathlib import Path

from meta_sam_parser import CompletedOutcome, decode_mask_to_svg_path, video_segmentation_format

from translucent import instruments
from translucent.contracts import ExperimentRecord, ReplayBundle, Series, Track
from translucent.ledger import Ledger
from translucent.tools import _boxes, _experiment, _flag, _video_meta


def _svg_masks(text: str, frame: int | None, allowed=None) -> dict[int, dict[str, tuple[str, tuple[float, float]]]]:
    """SVG path + origin per frame and object (slow pure-Python decode; called once per export)."""
    if allowed is not None:
        selected = []
        for line in text.splitlines():
            header = re.match(r"<([0-9]+)f(?:;[^>]*)?>", line)
            if header is None:
                continue
            f = frame if frame is not None else int(header[1])
            if not any(key[0] == f for key in allowed):
                continue
            body = line[header.end():]
            records = list(re.finditer(r",?([0-9]+)((?:<\|[A-Za-z][A-Za-z0-9_.-]*(?:;[^|]*)?\|>)+)", body))
            if "".join(r[0] for r in records) != body:
                selected.append(line)
                continue
            kept = [r[1] + r[2] for r in records if (f, r[1]) in allowed]
            if kept:
                selected.append(header[0] + ",".join(kept))
        text = "\n".join(selected) + "\n"
        if not selected:
            return {}
    parser = video_segmentation_format().create_parser()
    body = text if frame is None else "".join(
        ("<0f>" + ln[ln.index(">") + 1:] if ln.startswith("<") else ln) for ln in text.splitlines(keepends=True))
    list(parser.push(body))
    out: dict[int, dict] = {}
    for r in parser.finish(CompletedOutcome()).result.records:
        if r.kind != "mask":
            continue
        f = frame if frame is not None else r.frame.frame_index
        if allowed is not None and (f, r.object_id) not in allowed:
            continue
        out.setdefault(f, {})[r.object_id] = (decode_mask_to_svg_path(r.mask), (r.bounds.left, r.bounds.top))
    return out


def _tracks(L: Ledger, campaign_id: str) -> list[Track]:
    """Outlines from the campaign's last successful SAM run (a what-if branch shows its parent's)."""
    runs = [r for r in L.list("run", campaign_id=campaign_id)
            if r.tool in ("segment_images", "track_objects") and r.status == "ok"]
    parent = next((f.parent_campaign_id for f in L.list("fork", campaign_id=campaign_id)), None)
    if not runs and parent:
        return _tracks(L, parent)
    if not runs:
        return []
    run = runs[-1]
    masks = next(L.get("artifact", i) for i in run.output_ids if (L.get("artifact", i) or None) and
                 L.get("artifact", i).kind == "masks")
    raw = json.loads(L.blob_path(masks.id).read_text(encoding="utf-8"))
    boxes = {}
    for f, text in (raw.items() if "video" not in raw else [(None, raw["video"])]):
        boxes.update(_boxes(text, frame=int(f) if f is not None else None))
    meta = _video_meta(L.blob_path(run.params["video_id"]))
    grow = [r for r in L.list("run", campaign_id=campaign_id) if r.tool == "measure_growth" and r.status == "ok"]
    axis = 0 if not grow or grow[-1].params.get("grows_from") in ("left", "right") else 1
    credible = {(f, o) for f, objs_ in boxes.items() for o, b in objs_.items()
                if not _flag(b, meta["width"], meta["height"], axis)}
    cache = L.root / "work" / f"svg_v2_{axis}_{masks.id.removeprefix('sha256:')[:16]}.json"
    prior = getattr(L, "svg_cache_root", L.root / "work") / f"svg_{masks.id.removeprefix('sha256:')[:16]}.json"
    saved = cache if cache.exists() else prior
    if saved.exists():
        per_frame = {int(k): v for k, v in json.loads(saved.read_text(encoding="utf-8")).items()}
    else:
        per_frame = {}
        if "video" in raw:
            per_frame = _svg_masks(raw["video"], None, credible)
        else:
            for f, text in raw.items():
                per_frame.update(_svg_masks(text, int(f), credible))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({str(k): v for k, v in per_frame.items()}), encoding="utf-8")
    objs = sorted({o for v in per_frame.values() for o in v}, key=int)
    tracks = []
    for o in objs:
        fs = sorted(f for f in per_frame if o in per_frame[f] and (f, o) in credible)
        if not fs:
            continue
        tracks.append(Track(subject=f"filament {o}", frames=fs,
                            mask_paths=[per_frame[f][o][0] for f in fs],
                            mask_origins=[tuple(per_frame[f][o][1]) for f in fs],
                            points=[(boxes[f][o][0], (boxes[f][o][1] + boxes[f][o][3]) / 2)
                                    if f in boxes and o in boxes[f] else None for f in fs]))
    return tracks


def closure(L: Ledger, campaign_id: str, claims=None, events=None, reviews=None) -> dict[str, set[str]]:
    """Ids of the records a campaign's bundle carries: everything its events cite, the full evidence behind every
    finding (which may sit in another campaign, e.g. a branch citing its parent's outlines), the runs that made
    any of it, and the files those runs read or wrote."""
    from translucent.verify import evidence
    claims = L.list("claim", campaign_id=campaign_id) if claims is None else claims
    events = L.events(campaign_id) if events is None else events
    reviews = [r for k in claims for r in L.list("review", ref=k.id)] if reviews is None else reviews
    refs = {r for e in events for r in e.refs}
    ids = {k: set() for k in ("artifact", "measurement", "run", "lesson")}
    for r in refs:
        kind = L.kind_of(r)
        if kind in ids:
            ids[kind].add(r)
    forked = [f.claim_id for f in L.list("fork", campaign_id=campaign_id) if f.claim_id]
    for k in list(claims) + [L.get("claim", c) for c in forked]:   # a branch also carries the finding it re-examines
        ev = evidence(L, k.id)
        for kind, key in (("measurement", "measurements"), ("run", "runs"), ("artifact", "artifacts")):
            ids[kind] |= {x["id"] for x in ev[key]}
    ids["artifact"] |= {a for r in reviews for a in r.artifact_ids}
    for mid in list(ids["measurement"]):
        m = L.get("measurement", mid)
        ids["run"].add(m.run_id)
        ids["artifact"] |= set(m.artifact_ids)
    for rid in list(ids["run"]):
        run = L.get("run", rid)
        ids["artifact"] |= {i for i in run.input_ids + run.output_ids if L.kind_of(i) == "artifact"}
    return ids


def extra(L: Ledger, campaign_id: str) -> dict:
    """Bundle fields added after v1: the campaign's own fork record (when it is a what-if branch), forks made from
    it, its pre-registered protocol and its decisions."""
    return {"forks": [f for f in L.list("fork") if campaign_id in (f.campaign_id, f.parent_campaign_id)],
            "protocol": next(iter(L.list("protocol", campaign_id=campaign_id)), None),
            "decisions": L.list("decision", campaign_id=campaign_id)}


def evidence_records(L: Ledger, campaign_id: str) -> set[str]:
    """Every record id a campaign's evidence needs, for a receipt that holds only this campaign: its own records,
    the evidence closure (wherever recorded), the campaigns those records belong to, and every file in the lineage."""
    ids = closure(L, campaign_id)
    out = {campaign_id} | ids["lesson"]
    for kind in ("run", "claim", "fork", "protocol", "decision"):
        out |= {r.id for r in L.list(kind, campaign_id=campaign_id)}
    claims = list(L.list("claim", campaign_id=campaign_id))
    claims += [L.get("claim", f.claim_id) for f in L.list("fork", campaign_id=campaign_id) if f.claim_id]
    for f in extra(L, campaign_id)["forks"]:
        out |= {f.id, f.parent_campaign_id, f.campaign_id}
    out |= {k.id for k in claims}
    out |= {r.id for k in claims for r in L.list("review", ref=k.id)}
    todo = list(ids["measurement"] | ids["run"] | ids["artifact"] | set(L.get("campaign", campaign_id).inputs))
    todo += [m.id for r in L.list("run", campaign_id=campaign_id) for m in L.list("measurement", ref=r.id)]
    todo += [a for k in claims for r in L.list("review", ref=k.id) for a in r.artifact_ids]
    while todo:
        rid = todo.pop()
        if rid in out:
            continue
        kind = L.kind_of(rid)
        if kind is None:
            continue
        out.add(rid)
        rec = L.get(kind, rid)
        if kind == "measurement":
            todo += [rec.run_id] + rec.calibration_ids + rec.artifact_ids
        elif kind == "run":
            out.add(rec.campaign_id)
            todo += rec.input_ids + rec.output_ids
        elif kind == "artifact" and rec.transform:
            todo += rec.transform.parent_ids
    for rid in list(out):
        if L.kind_of(rid) == "claim":
            out.add(L.get("claim", rid).campaign_id)
    return out


def _record(experiment: str) -> ExperimentRecord | None:
    try:
        return ExperimentRecord.model_validate(instruments.record(experiment))
    except (ValueError, OSError, KeyError):   # records missing or changed since registration: say nothing
        return None


def export(L: Ledger, campaign_id: str, out: Path, *, include_verification=True,
           evidence_only=False) -> ReplayBundle:
    """Write bundle.json, movie.mp4 and (by default) the verification receipt. evidence_only: the receipt's ledger
    holds only what this campaign's evidence needs instead of the whole ledger."""
    from translucent.intervention import records as intervention_records
    if intervention_records(L,campaign_id,'protocol'):
        from translucent.intervention_export import export as export_intervention
        return export_intervention(L,campaign_id,out,include_verification=include_verification)
    c = L.get("campaign", campaign_id)
    out.mkdir(parents=True, exist_ok=True)
    video_id = c.inputs[0]
    shutil.copy(L.blob_path(video_id), out / "movie.mp4")
    meta = _video_meta(L.blob_path(video_id))
    exp = _experiment(L, video_id)
    frame_times = instruments.frame_times(exp)
    series = []
    v = instruments.voltage(exp)
    if v:
        if v.get("clock") == "log":
            name = "Cell voltage"
            bound = instruments.frame_time_uncertainty(exp)
            if bound is not None:
                name += f" (movie time is approximate, +/-{bound:g} s)"
            if frame_times and len(frame_times) == meta["frames"]:
                indices = []
                for t in frame_times:
                    j = bisect_left(v["t"], t)
                    indices.append(min((max(0, j - 1), min(j, len(v["t"]) - 1)),
                                       key=lambda i: abs(v["t"][i] - t)))
                series.append(Series(name=name, unit="V", t=[v["t"][i] for i in indices],
                                     y=[v["v"][i] for i in indices], frame_of_t=list(range(len(indices)))))
            else:
                step = max(1, (len(v["t"]) + 1999) // 2000)
                indices = sorted(set(range(0, len(v["t"]), step)) | {len(v["t"]) - 1})
                series.append(Series(name=name, unit="V", t=[v["t"][i] for i in indices],
                                     y=[v["v"][i] for i in indices], frame_of_t=None))
        else:
            series.append(Series(name="Cell voltage", unit="V", t=v["t"], y=v["v"], frame_of_t=list(range(len(v["t"])))))
    claims = L.list("claim", campaign_id=campaign_id)
    events = L.events(campaign_id)
    reviews = [r for k in claims for r in L.list("review", ref=k.id)]
    ids = closure(L, campaign_id, claims, events, reviews)
    bundle = ReplayBundle(
        campaign=c, video="movie.mp4", video_size=(meta["width"], meta["height"]), fps=meta["fps"],
        frame_time_s=frame_times, tracks=_tracks(L, campaign_id), series=series, events=events,
        artifacts=[a for a in L.list("artifact") if a.id in ids["artifact"] or a.id == video_id],
        measurements=[m for m in L.list("measurement") if m.id in ids["measurement"]],
        claims=claims, reviews=reviews,
        lessons=[x for x in L.list("lesson") if x.learned_in == campaign_id or x.id in ids["lesson"]],
        runs=[r for r in L.list("run") if r.id in ids["run"]], experiment=_record(exp), **extra(L, campaign_id))
    (out / "bundle.json").write_text(bundle.model_dump_json(), encoding="utf-8")
    if include_verification:
        from translucent.verify import export_evidence
        export_evidence(L, out, evidence_records(L, campaign_id) if evidence_only else None)
    return bundle


if __name__ == "__main__":
    b = export(Ledger(Path(sys.argv[1])), sys.argv[2], Path(sys.argv[3]))
    print(f"{len(b.events)} events, {len(b.tracks)} tracks, {len(b.claims)} findings -> {sys.argv[3]}")
