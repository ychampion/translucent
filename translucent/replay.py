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
from translucent.contracts import ReplayBundle, Series, Track
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
    """Outlines from the campaign's last successful SAM run."""
    runs = [r for r in L.list("run", campaign_id=campaign_id)
            if r.tool in ("segment_images", "track_objects") and r.status == "ok"]
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


def export(L: Ledger, campaign_id: str, out: Path, *, include_verification=True) -> ReplayBundle:
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
    refs = {r for e in events for r in e.refs}
    bundle = ReplayBundle(
        campaign=c, video="movie.mp4", video_size=(meta["width"], meta["height"]), fps=meta["fps"],
        frame_time_s=frame_times, tracks=_tracks(L, campaign_id), series=series, events=events,
        artifacts=[a for a in L.list("artifact") if a.id in refs or a.id == video_id],
        measurements=[m for m in L.list("measurement") if m.id in refs],
        claims=claims, reviews=[r for k in claims for r in L.list("review", ref=k.id)],
        lessons=[x for x in L.list("lesson") if x.learned_in == campaign_id or x.id in refs])
    (out / "bundle.json").write_text(bundle.model_dump_json(), encoding="utf-8")
    if include_verification:
        from translucent.verify import export_evidence
        export_evidence(L, out)
    return bundle


if __name__ == "__main__":
    b = export(Ledger(Path(sys.argv[1])), sys.argv[2], Path(sys.argv[3]))
    print(f"{len(b.events)} events, {len(b.tracks)} tracks, {len(b.claims)} findings -> {sys.argv[3]}")
