"""Campaign tools. Each call is a recorded Run with events; numbers come from trusted code, never from a model.

Tools return a ToolResult whose `summary` is what the agent reads (short, factual) and whose `images` are
attached to the agent's next turn when the model can see.
"""
from __future__ import annotations

import base64
import hashlib
import re
import json
from collections import Counter
import subprocess
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from meta_sam_parser import CompletedOutcome, decode_mask_to_raster, decode_mask_to_svg_path, video_segmentation_format

from translucent import gate, instruments, recordings
from translucent.contracts import Claim, Lesson, Measurement, Predicate, Run, Source, Transform, Uncertainty
from translucent.ledger import Ledger, sha256_file


@dataclass
class ToolResult:
    run_id: str
    status: str
    summary: str
    outputs: list[str] = field(default_factory=list)
    images: list[Path] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Context:
    ledger: Ledger
    campaign_id: str
    step_id: str = "s0"
    meta: Any = None          # translucent.providers.MeteredMeta, created lazily
    workdir: Path | None = None

    def scratch(self) -> Path:
        d = self.workdir or (self.ledger.root / "work")
        d.mkdir(parents=True, exist_ok=True)
        return d


REGISTRY: dict[str, dict] = {}


def tool(name: str, description: str, parameters: dict, title: str):
    """Register a tool with its JSON schema (for function calling) and a plain-language title for the viewer."""
    def deco(fn: Callable):
        REGISTRY[name] = {"fn": fn, "title": title,
                          "schema": {"type": "function", "function": {
                              "name": name, "description": description,
                              "parameters": {"type": "object", **parameters}}}}
        return fn
    return deco


UNCACHED = {"propose_finding", "record_lesson"}  # a verdict is re-decided every time: evidence may have changed


def call(ctx: Context, name: str, args: dict) -> ToolResult:
    """Run a tool as a recorded Run. Exceptions become tool_failure runs (never evidence)."""
    spec = REGISTRY.get(name)
    if spec is None:
        return ToolResult(run_id="", status="tool_failure", summary=f"No tool named {name}.")
    # models sometimes drop the "sha256:" of a content address; a bare 64-hex string can only mean that artifact
    args = {k: "sha256:" + v if isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) else v for k, v in args.items()}
    key = hashlib.sha256(json.dumps([name, args], sort_keys=True).encode()).hexdigest()
    # idempotency: identical successful call in this ledger is reused, never re-run (SAM is non-deterministic)
    for r in ctx.ledger.list("run", campaign_id=ctx.campaign_id) if name not in UNCACHED else []:
        if r.idempotency_key == key and r.status == "ok" and                 not any(ctx.ledger.stale_reason(i) for i in r.output_ids if ctx.ledger.kind_of(i) == "measurement"):
            prior = ctx.ledger.events(ctx.campaign_id)
            cached = next((e for e in reversed(prior) if e.type == "tool.finished" and r.id in e.refs), None)
            if cached:
                frames = [ctx.ledger.blob_path(i) for i in r.output_ids
                          if (a := ctx.ledger.get("artifact", i)) and a.kind == "frame"]
                return ToolResult(run_id=r.id, status="ok", summary="(same call as before) " + cached.data.get("summary", ""),
                                  outputs=r.output_ids, images=frames, data=cached.data.get("result", {}))
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    ctx.ledger.emit(ctx.campaign_id, "tool.started", name, spec["title"], refs=[run_id], data={"args": args})
    t0 = time.time()
    rec = {"inputs": [], "outputs": [], "measurements": [], "cost": 0.0, "provider": "local", "model": None,
           "request_id": None}
    try:
        res: ToolResult = spec["fn"](ctx, run_id, rec, **args)
        status, error = res.status, None
    except Exception as e:  # noqa: BLE001 - every failure is recorded, typed, and shown to the agent
        status, error = "tool_failure", f"{type(e).__name__}: {e}"
        res = ToolResult(run_id=run_id, status=status, summary=f"The tool failed: {error}")
        (ctx.scratch() / f"{run_id}.trace.txt").write_text(traceback.format_exc(), encoding="utf-8")
    if status == "ok":  # numbers taken from outputs of failed or substituted runs are not evidence
        pending = [m.id for m in rec["measurements"]]  # a run may calibrate its own outputs (e.g. playback time)
        bad = next((w for m in rec["measurements"] if (w := ctx.ledger.admission_problem(m, pending))), None)
        if bad:
            status = "invalid_result"
            res = ToolResult(run_id=run_id, status=status, summary=f"Not measured: {bad}. Outline the objects again "
                                                                    "with a call that succeeds.")
    run = Run(id=run_id, campaign_id=ctx.campaign_id, step_id=ctx.step_id, tool=name, idempotency_key=key,
              input_ids=[i for i in rec["inputs"] if ctx.ledger.kind_of(i)], output_ids=rec["outputs"],
              measurement_ids=[m.id for m in rec["measurements"]] if status == "ok" else [],
              params=args, provider=rec["provider"], model=rec["model"], provider_request_id=rec["request_id"],
              code_sha=rec.get("code_sha"), cost_usd=rec["cost"], status=status, error=error, started_at=t0, ended_at=time.time())
    ctx.ledger.add(run)
    # measurements are admitted only after their run exists and is ok
    ids = []
    if status == "ok":
        for m in rec["measurements"]:
            ctx.ledger.add(m)
            ids.append(m.id)
            ctx.ledger.emit(ctx.campaign_id, "measurement.recorded", name, m.label or m.quantity, refs=[m.id],
                            data={"value": m.value if not isinstance(m.value, list) else f"{len(m.value)} values",
                                  "unit": m.unit, "measurement": m.model_dump(mode="json")})
    res.run_id = run_id
    res.outputs = rec["outputs"] + ids
    ctx.ledger.emit(ctx.campaign_id, "tool.finished", name, spec["title"] + (" — failed" if status != "ok" else ""),
                    detail=res.summary[:400], refs=[run_id] + res.outputs,
                    data={"status": status, "summary": res.summary, "cost_usd": rec["cost"], "result": res.data})
    return res


def _measure(rec, run_id, quantity, value, unit, u, ukind, method, **kw) -> str:
    mid = f"m_{uuid.uuid4().hex[:12]}"
    rec["measurements"].append(Measurement(id=mid, run_id=run_id, quantity=quantity, value=value, unit=unit,
                                           uncertainty=Uncertainty(value=u, kind=ukind, method=method), **kw))
    return mid


# ------------------------------------------------------------------ video

def _video_meta(path: Path) -> dict:
    cap = cv2.VideoCapture(str(path))
    meta = {"frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), "fps": cap.get(cv2.CAP_PROP_FPS),
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}
    cap.release()
    return meta


def _frame(path: Path, index: int):
    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, f = cap.read()
    cap.release()
    if not ok:
        raise ValueError(f"frame {index} not readable")
    return f


def import_video(ledger: Ledger, path: Path, *, label: str, citation: str, licence: str, experiment: str):
    """Import a recorded experiment (faststart remux for streaming upload). Not an agent tool."""
    instruments.check_recording(experiment, sha256_file(path))
    meta = _video_meta(path)
    out = ledger.root / "work" / f"{path.stem}_faststart.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    # local MP4/MOV only: a playlist or concat file posing as a video could make ffmpeg read other files or URLs
    subprocess.run([recordings.ffmpeg(), "-v", "error", "-y", "-protocol_whitelist", "file", "-f", "mov", "-i", str(path),
                    "-c", "copy", "-movflags", "+faststart", str(out)], check=True)
    existing = ledger.get("artifact", "sha256:" + sha256_file(out))
    if existing and (not existing.source or existing.source.uri != f"experiment:{experiment}"):
        raise ValueError("this video already belongs to another experiment in this ledger; use its existing id")
    return ledger.put_file(out, kind="video", media_type="video/mp4", label=label,
                           shape=[meta["frames"], meta["height"], meta["width"]], axes=["frame", "y", "x"],
                           source=Source(uri=f"experiment:{experiment}", citation=citation, licence=licence))


def _experiment(ledger: Ledger, video_id: str) -> str:
    art = ledger.get("artifact", video_id)
    if art is None or art.kind != "video":
        raise ValueError(f"{video_id} is not a recorded video")
    return art.source.uri.removeprefix("experiment:")


@tool("inspect_video",
      "Look at a recorded experiment: size, frame count, whether frames repeat, and a gridded frame image with "
      "pixel coordinates so you can point at objects. Use region [x0,y0,x1,y1] to zoom.",
      {"required": ["video_id", "frame"], "properties": {
          "video_id": {"type": "string"}, "frame": {"type": "integer"},
          "region": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4}}},
      "Looking at the experiment")
def inspect_video(ctx, run_id, rec, video_id, frame, region=None):
    rec["inputs"].append(video_id)
    path = ctx.ledger.blob_path(video_id)
    meta = _video_meta(path)
    img = _frame(path, frame)
    x0, y0, x1, y1 = region or (0, 0, meta["width"], meta["height"])
    x0, x1 = sorted((min(max(int(x0), 0), meta["width"]), min(max(int(x1), 0), meta["width"])))
    y0, y1 = sorted((min(max(int(y0), 0), meta["height"]), min(max(int(y1), 0), meta["height"])))
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise ValueError(f"region is outside the {meta['width']}x{meta['height']} image or too small")
    crop = img[y0:y1, x0:x1]
    scale = min(3.0, 1024 / max(crop.shape[:2]))  # upscale small zooms so a model can read them
    view = cv2.resize(crop, None, fx=scale, fy=scale)
    # the grid is drawn after resizing, so its labels (source-video pixels) stay legible at any zoom
    step = 50 if max(x1 - x0, y1 - y0) <= 1000 else 100
    h, w = view.shape[:2]

    def label(text, org):
        cv2.putText(view, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(view, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1, cv2.LINE_AA)
    for x in range((x0 // step + 1) * step, x1, step):
        major, px = x % (step * 2) == 0, round((x - x0) * scale)
        cv2.line(view, (px, 0), (px, h), (0, 255, 255) if major else (0, 150, 150), 1)
        if major:
            label(str(x), (px + 4, 22))
    for y in range((y0 // step + 1) * step, y1, step):
        major, py = y % (step * 2) == 0, round((y - y0) * scale)
        cv2.line(view, (0, py), (w, py), (0, 255, 255) if major else (0, 150, 150), 1)
        if major:
            label(str(y), (4, py - 6))
    out = ctx.scratch() / f"{run_id}_f{frame}.png"
    cv2.imwrite(str(out), view)
    art = ctx.ledger.put_file(out, kind="frame", media_type="image/png", label=f"Frame {frame}",
                              transform=Transform(op="grid_frame", parent_ids=[video_id],
                                                  params={"frame": frame, "region": [x0, y0, x1, y1]}))
    rec["outputs"].append(art.id)
    exp = _experiment(ctx.ledger, video_id)
    times = instruments.frame_times(exp)
    bound = instruments.frame_time_uncertainty(exp)
    repeats, reliable = _repeat_factor(path)
    repeat_note = (f"Each captured image is shown {repeats:.0f} times (playback repeats frames). " if reliable else
                   "Could not tell reliably whether playback repeats frames; check consecutive frames before timing. ")
    clock = (f"The movie clock is coarse, with a bound +/-{bound:.0f} s; it cannot establish a growth speed. "
             if bound is not None else "The experiment has a recorded clock for every frame. " if times is not None else
             "No recorded clock for this experiment: playback timing is not real time. ")
    source = instruments.time_base_source(exp)
    if source and source.startswith("declared by "):
        clock = f"The experiment has a recorded clock: {source}. "
        repeats, reliable = len(times) / len(set(times)), True
        repeat_note = f"The declared clock has {len(set(times))} capture times for {len(times)} source frames. "
        if bound is not None:
            clock += f"Elapsed-time uncertainty bound +/-{bound:g} s. "
    where = instruments.layout(exp)
    summary = (f"{meta['width']}x{meta['height']} px, {meta['frames']} frames at {meta['fps']:.0f} fps playback. "
               + (f"Frame layout (experiment records): {where} " if where else "")
               + repeat_note
               + clock
               + f"Image shows frame {frame}, region x {x0}-{x1}, y {y0}-{y1}, gridlines every {step} px, drawn "
               f"at {scale:.2f}x. Grid labels are source-video pixels: give boxes, points and regions in those, "
               "never in the shown image's own pixels.")
    return ToolResult(run_id, "ok", summary, images=[out],
                      data={"repeats": repeats if reliable else None, **meta})


def _new_image_flags(path: Path) -> np.ndarray:
    """True where a playback frame shows a new captured image. Threshold = the widest gap in the upper two-thirds
    of frame-to-frame differences (repeats differ only by compression flicker; a fixed threshold counts flicker)."""
    cap, prev, diffs = cv2.VideoCapture(str(path)), None, []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(f, None, fx=0.25, fy=0.25), cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            diffs.append(float(np.abs(g - prev).mean()))
        prev = g
    cap.release()
    d = np.array(diffs)
    s = np.sort(d)
    lo = len(s) // 3
    if len(s) - lo < 2:
        return d > 0
    k = int(np.argmax(np.diff(s[lo:]))) + lo
    return d > (s[k] + s[k + 1]) / 2


def _repeat_factor(path: Path) -> tuple[float, bool]:
    """(frames per captured image, reliable?). Reliable only when new images arrive in regular runs."""
    new = _new_image_flags(path)
    runs = np.diff(np.flatnonzero(new))
    reliable = int(new.sum()) >= 5 and len(runs) > 0 and runs.max() <= 1.2 * runs.min()
    return (len(new) + 1) / (int(new.sum()) + 1), reliable


# ------------------------------------------------------------------ calibration (from the experiment's records)

@tool("lookup_calibration",
      "Ask the experiment's records for a calibration: 'spatial_scale' (pixels per mm, from a scale bar) or "
      "'time_base' (recorded frame-time estimates; check the uncertainty bound). Returns nothing if none is on record.",
      {"required": ["video_id", "quantity"], "properties": {
          "video_id": {"type": "string"}, "quantity": {"type": "string", "enum": ["spatial_scale", "time_base"]}}},
      "Checking the experiment's records")
def lookup_calibration(ctx, run_id, rec, video_id, quantity):
    rec["inputs"].append(video_id)
    exp = _experiment(ctx.ledger, video_id)
    if quantity == "spatial_scale":
        cal = instruments.spatial_scale(exp)
        if cal is None:
            return ToolResult(run_id, "ok", "No scale on record for this experiment.")
        mid = _measure(rec, run_id, "spatial_scale", cal["px_per_mm"], "px/mm", cal["uncertainty"], "bound",
                       cal["method"], label="Image scale", valid_domain=cal["source"], artifact_ids=[video_id])
        return ToolResult(run_id, "ok", f"Scale: {cal['px_per_mm']} ± {cal['uncertainty']} px per mm "
                                        f"({cal['source']}). Measurement id {mid}.", data={"measurement_id": mid})
    times = instruments.frame_times(exp)
    if times is None:
        why = instruments.time_base_source(exp)
        return ToolResult(run_id, "ok", "No recorded clock for this experiment. Playback frame rate is not real "
                                        "time, so speeds in um/s cannot be established from this video alone."
                          + (f" Records: {why}." if why else ""))
    distinct = sorted(set(times))
    bound = instruments.frame_time_uncertainty(exp)
    if bound is not None and exp != instruments.LU:
        source = instruments.time_base_source(exp)
        mid = _measure(rec, run_id, "time_base", times, "s", bound, "bound", source,
                       label="Real time of each frame", valid_domain=source, artifact_ids=[video_id])
        return ToolResult(run_id, "ok", f"Recorded clock for {len(times)} source frames, spanning "
                          f"{times[-1] - times[0]:g} s; elapsed-time bound +/-{bound:g} s. {source}. "
                          f"Measurement id {mid}.", data={"measurement_id": mid})
    if bound is not None:
        source = instruments.time_base_source(exp)
        mid = _measure(rec, run_id, "time_base", times, "s", bound, "bound", source,
                       label="Coarse movie clock", valid_domain=source, artifact_ids=[video_id])
        return ToolResult(run_id, "ok", f"Recorded voltage inset digitized for {len(times)} movie frames; "
                          f"conservative bound +/-{bound:.0f} s (about {bound / 60:.0f} min). Methods states "
                          "60 s per acquired image; the exported movie does not verify that cadence. "
                          f"The voltage log has its own 2 s clock. Measurement id {mid}.", data={"measurement_id": mid})
    step = float(np.median(np.diff(distinct)))
    source = instruments.time_base_source(exp)
    method = "instrument clock per frame; ± half the capture interval"
    if source and source.startswith("declared by "):
        method = source + "; ± half the median capture interval"
    mid = _measure(rec, run_id, "time_base", times, "s", step / 2, "resolution",
                   method, label="Real time of each frame", valid_domain=source, artifact_ids=[video_id])
    return ToolResult(run_id, "ok", f"Recorded clock found: {len(distinct)} distinct capture times, one image every "
                                    f"{step:.1f} s, spanning {distinct[-1] - distinct[0]:.0f} s. Measurement id {mid}.",
                      data={"measurement_id": mid})


# ------------------------------------------------------------------ SAM 3.1 tracking

def _decode(text: str) -> dict[int, dict[str, dict]]:
    parser = video_segmentation_format().create_parser()
    list(parser.push(text))
    result = parser.finish(CompletedOutcome()).result
    frames: dict[int, dict[str, dict]] = {}
    for r in result.records:
        if r.kind != "mask":
            continue
        b = r.bounds
        w, h = int(round(b.right - b.left)), int(round(b.bottom - b.top))
        raster = np.frombuffer(decode_mask_to_raster(r.mask), dtype=np.uint8).reshape(h, w)
        frames.setdefault(r.frame.frame_index, {})[r.object_id] = {
            "raster": raster, "origin": (b.left, b.top), "confidence": r.confidence,
            "svg": decode_mask_to_svg_path(r.mask)}
    return frames


def _sam(ctx):
    """SAM client for this campaign. TRANSLUCENT_SAM: live | record (live + store) | strict (replay, default) |
    nearest (replay closest recording; development only, substitution is reported)."""
    if ctx.meta is None:
        import os
        mode = os.environ.get("TRANSLUCENT_SAM", "strict")
        if mode in ("live", "record"):
            from translucent.providers import MeteredMeta
            ctx.meta = MeteredMeta() if mode == "live" else recordings.RecordingSAM(MeteredMeta())
        else:
            ctx.meta = recordings.RecordedSAM(mode)
    return ctx.meta


def _substitution_note(final) -> str:
    sub = getattr(final, "substituted", None)
    return (f" NOTE: development replay: no recording for your exact prompt, so this is the recorded output for {sub}."
            " It cannot be used as evidence." if sub else "")


BOX = re.compile(r"(?:^|,|>)(\d+)<\|box;([^|]*)\|>")


def _boxes(text: str, frame: int | None = None) -> dict[int, dict[str, list[float]]]:
    """Per-frame boxes [x1, y1, x2, y2, confidence] from SAM output text. Cheap: masks are not decoded."""
    out: dict[int, dict[str, list[float]]] = {}
    for ln in text.splitlines():
        m = re.match(r"<(\d+)f>", ln)
        f = frame if frame is not None else (int(m.group(1)) if m else None)
        if f is None:
            continue
        for oid, body in BOX.findall(ln):
            kv = dict(p.split("=", 1) for p in body.split(";"))
            out.setdefault(f, {})[oid] = [float(kv["x1"]), float(kv["y1"]), float(kv["x2"]), float(kv["y2"]),
                                          float(kv.get("c", "nan"))]
    return out


def _flag(box, W, H, axis, um_per_px=None) -> str | None:
    """Why a box is not a credible growing object, or None. um_per_px: set when the outline has no mask to look
    inside, so a box wider than one filament can't be shown to hold one body (translucent.qc)."""
    x1, y1, x2, y2, c = box
    w, h = x2 - x1, y2 - y1
    if w * h > 0.10 * W * H:
        return "covers a large part of the image (probably the whole sample, not the object)"
    along, across = (w, h) if axis == 0 else (h, w)
    if across > 30 and across > 3 * along:
        return "shaped like the electrode edge, not a filament"
    if along < 2:
        return "too small to measure"
    from translucent import qc
    if um_per_px and (why := qc.too_wide(box, axis, um_per_px, (W, H))):
        return why
    if c == c and c < 0.5:  # last: a faint outline of the right shape is an absent object, not a wrong one
        return f"low confidence {c:.2f}"
    return None


def _store_boxes(ctx, rec, boxes, raw_texts, parent, params, op):
    raw = ctx.ledger.put_json(raw_texts, kind="masks", label="SAM 3.1 masks",
                              transform=Transform(op=op, parent_ids=[parent], params=params))
    bx = ctx.ledger.put_json({str(k): v for k, v in sorted(boxes.items())}, kind="table", label="Object outlines",
                             transform=Transform(op="extract_boxes", parent_ids=[raw.id], params={}))
    rec["outputs"] += [raw.id, bx.id]
    return raw.id, bx.id


def _box_summary(boxes, W, H) -> str:
    objs = sorted({o for f in boxes.values() for o in f}, key=int)
    parts = []
    for o in objs:
        seen = [(f, b[o]) for f, b in sorted(boxes.items()) if o in b]
        bad = [x for x in (_flag(b, W, H, 0) for _, b in seen) if x]
        parts.append(f"#{o}: {len(seen)} frames ({seen[0][0]}-{seen[-1][0]})"
                     + (f", {len(bad)} flagged: {max(set(bad), key=bad.count)}" if bad else ""))
    return "; ".join(parts) if parts else "no objects found"


@tool("track_objects",
      "Track up to 16 objects through the video with SAM 3.1 from one frame forward. Point at each object on a "
      "frame where it is clearly visible (pixel coordinates from inspect_video). Works when objects move little "
      "between captured images; for fast change use segment_images. Naming objects in words does not work on "
      "microscopy; always point.",
      {"required": ["video_id", "frame", "points"], "properties": {
          "video_id": {"type": "string"}, "frame": {"type": "integer"},
          "points": {"type": "array", "maxItems": 16, "items": {"type": "array", "items": {"type": "number"},
                                                                 "minItems": 2, "maxItems": 2}}}},
      "Tracking with SAM 3.1")
def track_objects(ctx, run_id, rec, video_id, frame, points):
    rec["inputs"].append(video_id)
    sam = _sam(ctx)
    path = ctx.ledger.blob_path(video_id)
    meta = _video_meta(path)
    W, H = meta["width"], meta["height"]
    prompt = f"<{frame}f>" + ",".join(f"{i + 1}<|point;x={int(x)};y={int(y)};w={W};h={H}|>"
                                      for i, (x, y) in enumerate(points))
    before = sam.ledger.balance()["committed_usd"]
    text, final = sam.sam_video(file_id=_meta_file_id(ctx, video_id, path), prompt=prompt,
                                     frames=meta["frames"],
                                     metadata={"propagation_direction": "forward", "include_confidence": "true"},
                                     source=recordings.stream_id(path))
    rec.update(provider="meta", model="sam-3.1", request_id=getattr(final, "id", None),
               cost=round(sam.ledger.balance()["committed_usd"] - before, 6))
    boxes = _boxes(text)
    masks_id, boxes_id = _store_boxes(ctx, rec, boxes, {"video": text}, video_id,
                                      {"frame": frame, "points": points}, "sam_track")
    if not boxes:
        return ToolResult(run_id, "invalid_result", "SAM returned nothing. Point closer to the object's centre "
                                                    "or pick a frame where it is clearer.")
    status = "invalid_result" if getattr(final, "substituted", None) else "ok"  # another prompt's output: not evidence
    return ToolResult(run_id, status, f"Tracked from frame {frame}: {_box_summary(boxes, W, H)}. "
                                      f"Outlines id {boxes_id}." + _substitution_note(final),
                      data={"boxes_id": boxes_id, "masks_id": masks_id,
                            "substituted": getattr(final, "substituted", None)})


def _capture_frames(path: Path, times: list[float] | None) -> list[int]:
    """One playback frame per captured image (the first frame showing it)."""
    if times is not None:
        seen, out = set(), []
        for i, t in enumerate(times):
            if t not in seen:
                seen.add(t)
                out.append(i)
        return out
    return [0] + [k + 1 for k, v in enumerate(_new_image_flags(path)) if v]


@tool("segment_images",
      "Outline objects with SAM 3.1 on every captured image separately, using the same prompts each time. "
      "Use when objects change a lot between images. Prefer boxes: a loose box ('lane') around where each object "
      "is or will be, e.g. a thin horizontal band spanning the gap for each filament; SAM outlines only the object "
      "inside it. Points on thin faint objects often outline the whole sample instead. Costs $0.0025 per image.",
      {"required": ["video_id"], "properties": {
          "video_id": {"type": "string"},
          "boxes": {"type": "array", "maxItems": 16, "items": {"type": "array", "items": {"type": "number"},
                                                                "minItems": 4, "maxItems": 4}},
          "points": {"type": "array", "maxItems": 16, "items": {"type": "array", "items": {"type": "number"},
                                                                 "minItems": 2, "maxItems": 2}},
          "max_images": {"type": "integer", "description": "cap on images (default 60)"}}},
      "Outlining each image with SAM 3.1")
def segment_images(ctx, run_id, rec, video_id, boxes=None, points=None, max_images=60):
    rec["inputs"].append(video_id)
    sam = _sam(ctx)
    path = ctx.ledger.blob_path(video_id)
    meta = _video_meta(path)
    W, H = meta["width"], meta["height"]
    frames = _capture_frames(path, instruments.frame_times(_experiment(ctx.ledger, video_id)))
    if len(frames) > max_images:
        idx = np.linspace(0, len(frames) - 1, max_images).round().astype(int)
        frames = [frames[i] for i in sorted(set(idx))]
    if boxes:
        prompt = "<0f>" + ",".join(f"{i + 1}<|box;x1={int(a)};y1={int(b)};x2={int(c)};y2={int(d)};w={W};h={H}|>"
                                   for i, (a, b, c, d) in enumerate(boxes))
    elif points:
        prompt = "<0f>" + ",".join(f"{i + 1}<|point;x={int(x)};y={int(y)};w={W};h={H}|>" for i, (x, y) in enumerate(points))
    else:
        raise ValueError("give boxes or points")
    before = sam.ledger.balance()["committed_usd"]
    found, texts, notes = {}, {}, set()
    for f in frames:
        ok, jpg = cv2.imencode(".jpg", _frame(path, f), [cv2.IMWRITE_JPEG_QUALITY, 95])
        url = "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode()
        text, final = sam.sam_image(image_url=url, prompt=prompt, metadata={"include_confidence": "true"},
                                    source=f"{recordings.stream_id(path)}#{f}")
        if getattr(final, "substituted", None):
            notes.add(final.substituted)
        texts[str(f)] = text
        found.update(_boxes(text, frame=f))
    rec.update(provider="meta", model="sam-3.1", cost=round(sam.ledger.balance()["committed_usd"] - before, 6))
    masks_id, boxes_id = _store_boxes(ctx, rec, found, texts, video_id,
                                      {"boxes": boxes, "points": points, "frames": frames}, "sam_segment_images")
    return ToolResult(run_id, "invalid_result" if notes else "ok",  # replayed other prompts: shown, never evidence
                      f"Outlined {len(frames)} captured images (frames {frames[0]}-{frames[-1]}): "
                                    f"{_box_summary(found, W, H)}. Outlines id {boxes_id}."
                                    + (f" NOTE: development replay of recordings for other prompts {sorted(notes)}; cannot be used as evidence."
                         if notes else ""),
                      data={"boxes_id": boxes_id, "masks_id": masks_id, "frames": frames})


def _meta_file_id(ctx, video_id, path) -> str:
    if not hasattr(ctx.meta, "_client") and not hasattr(ctx.meta, "inner"):
        return ctx.meta.upload(path)  # replay: content hash, nothing uploaded
    cache = ctx.ledger.root / "meta_files.json"
    known = json.loads(cache.read_text()) if cache.exists() else {}
    if video_id not in known:
        known[video_id] = ctx.meta.upload(path)
        cache.write_text(json.dumps(known, indent=1), encoding="utf-8")
    if hasattr(ctx.meta, "remember"):  # recording client keys stored responses by video content
        ctx.meta.remember(known[video_id], path)
    return known[video_id]


def decode_masks(text: str, frames: set[int] | None = None) -> dict[int, dict[str, dict]]:
    """Full mask decode for display (slow: ~5 s per frame in pure Python). Only the frames asked for."""
    keep = []
    for ln in text.splitlines(keepends=True):
        m = re.match(r"<(\d+)f>", ln)
        if frames is None or (m and int(m.group(1)) in frames):
            keep.append(ln)
    return _decode("".join(keep))


# ------------------------------------------------------------------ measurement

GROWS = {"right": 0, "left": 0, "bottom": 1, "top": 1}


@tool("measure_growth",
      "Measure each outlined object's length along its growth direction at every captured image, and its growth "
      "speed. Uses the recorded clock if you pass its measurement id, else playback timing (not real time). "
      "Needs the image scale measurement id and readable masks for the merged-outline check. Coarse clocks are "
      "refused. Outlines flagged as not credible are left out.",
      {"required": ["boxes_id", "video_id", "grows_from", "scale_id"], "properties": {
          "boxes_id": {"type": "string"}, "video_id": {"type": "string"},
          "grows_from": {"type": "string", "enum": list(GROWS)}, "scale_id": {"type": "string"},
          "time_base_id": {"type": "string"}, "objects": {"type": "array", "items": {"type": "string"}}}},
      "Measuring growth")
def measure_growth(ctx, run_id, rec, boxes_id, video_id, grows_from, scale_id, time_base_id=None, objects=None):
    L = ctx.ledger
    rec["inputs"] += [boxes_id, video_id, scale_id] + ([time_base_id] if time_base_id else [])
    scale = L.get("measurement", scale_id)
    if scale is None or scale.quantity != "spatial_scale":
        raise ValueError(f"{scale_id} is not a recorded image scale")
    if scale.artifact_ids and video_id not in scale.artifact_ids:
        raise ValueError(f"{scale_id} is the image scale of another recording")
    declared_scale = (scale.valid_domain or "").startswith("declared by ")
    scale_u = scale.uncertainty.value or 0
    if declared_scale and scale_u >= scale.value:
        return ToolResult(run_id, "ok", "Not measured: the declared scale bound reaches zero pixels per mm; "
                          "a tighter calibration is needed for a bounded length or speed.", data={"ids": {}})
    scale_fraction = scale_u / (scale.value - scale_u) if declared_scale else 0
    boxes = {int(k): v for k, v in json.loads(L.blob_path(boxes_id).read_text()).items()}
    meta = _video_meta(L.blob_path(video_id))
    W, H = meta["width"], meta["height"]
    if time_base_id:
        tb = L.get("measurement", time_base_id)
        if tb is None or tb.quantity != "time_base":
            raise ValueError(f"{time_base_id} is not a recorded clock")
        t_of = np.asarray(tb.value, float)
        if t_of.ndim != 1 or len(t_of) != meta["frames"] or "not a frame clock" in tb.uncertainty.method:
            raise ValueError(f"{time_base_id} is not a clock for this video's frames ({t_of.size} readings for "
                             f"{meta['frames']} frames)")
        if tb.artifact_ids and video_id not in tb.artifact_ids:
            raise ValueError(f"{time_base_id} is the clock of another recording")
        if tb.uncertainty.kind == "bound" and tb.uncertainty.value:
            return ToolResult(run_id, "ok", f"Not measured: clock too coarse for a speed (bound "
                              f"+/-{tb.uncertainty.value:.0f} s). Images sharing a coarse reading are not repeats; "
                              "a resolved clock for each captured image is needed.", data={"ids": {}})
        timing_cal, timing_note = [time_base_id], "recorded clock"
    else:
        t_of = np.arange(meta["frames"]) / meta["fps"]
        tid = _measure(rec, run_id, "playback_time", t_of.tolist(), "s", 1 / meta["fps"], "resolution",
                       "frame index / playback fps (not real time)", label="Playback time (not real time)",
                       artifact_ids=[video_id])
        timing_cal, timing_note = [tid], "playback timing (not real time)"
    axis = GROWS[grows_from]
    objs = [str(o) for o in objects] if objects else sorted({o for f in boxes.values() for o in f}, key=int)
    from translucent import qc
    lines, ids = [], {}
    art = L.get("artifact", boxes_id)  # the masks SAM returned with these boxes: look inside for two bodies
    try:
        raw = json.loads(L.blob_path(art.transform.parent_ids[0]).read_text()) if art and art.transform else {}
        mask_lines = qc.frame_lines(raw) if isinstance(raw, dict) else {}
    except Exception as e:
        return ToolResult(run_id, "ok", f"Not measured: merge check could not read the stored outlines ({e}). "
                          "Outline the objects again.", data={"ids": {}})
    for o in objs:
        frames = [f for f in sorted(boxes) if o in boxes[f]]
        credible = [f for f in frames if not _flag(boxes[f][o], W, H, axis)]
        if not frames:
            lines.append(f"#{o}: no outline has this number; use the numbers the outlining result lists.")
            continue
        if not credible:  # say why, or the agent goes hunting for a naming or reading problem that isn't there
            whys = Counter(_flag(boxes[f][o], W, H, axis).split(" 0.")[0] for f in frames)
            conf = [boxes[f][o][4] for f in frames if boxes[f][o][4] == boxes[f][o][4]]
            lines.append(f"#{o}: none of its {len(frames)} outlines is credible ("
                         + ", ".join(f"{n} {why}" for why, n in whys.most_common(2))
                         + (f"; best confidence {max(conf):.2f}" if conf else "")
                         + "); not measured. Outline it again with a tighter box along this one object.")
            continue
        try:
            merged, n_masks = qc.merged_track(mask_lines, o, credible, axis, grows_from, 1000 / scale.value)
        except Exception as e:
            lines.append(f"#{o}: merge check could not read the outlines ({e}); not measured. Outline it again.")
            continue
        if merged:
            lines.append(f"#{o}: {merged}; not measured. Outline each filament with its own box.")
            continue
        if not credible or n_masks != len(credible):
            lines.append(f"#{o}: merge check needs readable mask pixels for every credible outline "
                         f"({n_masks} of {len(credible)} checked); not measured. Outline it again.")
            continue
        # Coarse clocks were refused above; repeated readings here belong to resolved captured images.
        by_t: dict[float, list[float]] = {}
        flagged = misshapen = 0
        last_why = ""
        for fi in sorted(boxes):
            b = boxes[fi].get(o)
            if b is None or fi >= len(t_of):
                continue
            um_px = None if _flag(b, W, H, axis) or qc.has_mask(mask_lines.get(fi, ""), o) else 1000 / scale.value
            if why := _flag(b, W, H, axis, um_px):
                flagged += 1
                misshapen += not why.startswith("low confidence")  # absent objects come back faint; that's fine
                last_why = why if not why.startswith("low confidence") else last_why
                continue
            by_t.setdefault(round(float(t_of[fi]), 3), []).append(b[2] - b[0] if axis == 0 else b[3] - b[1])
        if len(by_t) < 3:
            lines.append(f"#{o}: only {len(by_t)} credible captured images ({flagged} flagged"
                         f"{', e.g. ' + last_why if last_why else ''}); not measured")
            continue
        seen = sum(len(v) for v in by_t.values()) + flagged
        if misshapen > 0.25 * seen:  # an outline that is often the whole sample or the electrode is not this object
            lines.append(f"#{o}: {misshapen} of {seen} outlines are the wrong shape (e.g. {last_why}); "
                         "not measured. Outline it again, e.g. with a loose box around its lane.")
            continue
        t = np.array(sorted(by_t))
        length_um = np.array([np.median(by_t[k]) for k in t]) / scale.value * 1000
        slope, icpt = np.polyfit(t, length_um, 1)
        resid = length_um - (slope * t + icpt)
        se = float(np.sqrt(resid @ resid / max(1, len(t) - 2) / ((t - t.mean()) @ (t - t.mean()))))
        cal = [scale_id] + timing_cal
        length_u = 1000 / scale.value + (1000 / scale.value + float(np.max(np.abs(length_um)))) * scale_fraction
        length_method = "outline extent along growth axis, ±1 px"
        velocity_u, velocity_kind = 2 * se, "ci95"
        velocity_method = f"linear fit of length vs time over {len(t)} captured images ({timing_note})"
        if declared_scale:
            length_method += "; includes the declared scale bound; " + scale.valid_domain
            velocity_u += (abs(float(slope)) + velocity_u) * scale_fraction
            velocity_method += "; twice the fit standard error plus declared scale sensitivity; " + scale.valid_domain
        lid = _measure(rec, run_id, "filament_length", length_um.tolist(), "um", length_u,
                       "bound" if declared_scale else "resolution", length_method,
                       subject=f"object {o}", calibration_ids=[scale_id],
                       artifact_ids=[boxes_id], label=f"Length of object {o}")
        tid = _measure(rec, run_id, "elapsed_time", t.tolist(), "s", float(np.median(np.diff(t))) / 2, "resolution",
                       timing_note, subject=f"object {o}", calibration_ids=timing_cal, artifact_ids=[boxes_id],
                       label=f"Time of each image, object {o}")
        vid = _measure(rec, run_id, "growth_velocity", float(slope), "um/s", velocity_u, velocity_kind,
                       velocity_method,
                       subject=f"object {o}", calibration_ids=cal, artifact_ids=[boxes_id],
                       time_range=(float(t[0]), float(t[-1])), label=f"Growth speed, object {o}")
        ids[o] = {"length": lid, "time": tid, "velocity": vid}
        series = ", ".join(f"{tt:.0f}s:{ll:.0f}" for tt, ll in zip(t, length_um))
        lines.append(f"#{o}: {len(t)} images ({flagged} flagged), length um by time [{series}]; overall linear speed "
                     f"{slope:.2f} ± {velocity_u:.2f} um/s [{timing_note}] (velocity {vid}, length {lid}, time {tid})")
    return ToolResult(run_id, "ok", "\n".join(lines), data={"ids": ids})


# ------------------------------------------------------------------ the cell's own electrical record

def _voltage_samples(ctx, rec, run_id, video_id, series, source_id):
    """An unsegmented log establishes sampled values, not the physical meaning of voltage changes."""
    t, v = np.asarray(series["t"]), np.asarray(series["v"])
    window = series.get("movie_time_range_s")
    if window:
        keep = (t >= window[0]) & (t <= window[1])
        t, v = t[keep], v[keep]
    if len(t) < 2:
        return ToolResult(run_id, "ok", "The voltage log has fewer than two samples in the recorded movie's "
                          "time window. Check the declared offset; no timing was measured.",
                          data={"events": [], "source_id": source_id})
    parents = [video_id, source_id]
    note = series["method"]
    clock_note = series.get("time_base_source")
    dt = float(np.max(np.diff(t)))
    u = dt / 2 + series.get("time_uncertainty_s", 0)
    cals = []
    if clock_note:
        tid = _measure(rec, run_id, "time_base", t.tolist(), "s", u, "bound",
                       clock_note + "; " + note + "; half the largest log sample interval plus clock bound; "
                       "voltage log samples, not a frame clock",
                       label="Voltage sample clock", valid_domain=clock_note + "; " + note,
                       artifact_ids=parents, time_range=(float(t[0]), float(t[-1])))
        cals = [tid]
    vid = _measure(rec, run_id, "cell_voltage", v.tolist(), "V", None, "none",
                   note + "; voltage accuracy not stated", label="Cell voltage", valid_domain=note,
                   calibration_ids=cals, artifact_ids=parents)
    minima = np.flatnonzero(v == v.min())
    lo, hi = float(t[minima[0]]), float(t[minima[-1]])
    mid = _measure(rec, run_id, "elapsed_time", (lo + hi) / 2, "s", (hi - lo) / 2 + u, "bound",
                   "time of the lowest recorded voltage sample (ties span all minima); half the largest sample "
                   "interval plus clock bound; not a physical onset; voltage accuracy not stated; " + note,
                   label="Time of lowest recorded voltage", valid_domain=note,
                   calibration_ids=cals, artifact_ids=parents, time_range=(lo, hi))
    return ToolResult(run_id, "ok", f"Voltage log: {len(t)} samples from {t[0]:g} to {t[-1]:g} s. "
                      f"Lowest recorded voltage {float(v.min()):g} V at {lo:g}-{hi:g} s; timing measurement {mid}. "
                      + (clock_note + ". " if clock_note else
                         "No recorded clock for the video; timing findings cannot be supported. ")
                      + note + ". No short-circuit or plating threshold was declared.",
                      data={"events": [mid], "voltage_id": vid, "time_base_id": cals[0] if cals else None,
                            "source_id": source_id, "full_samples": len(series["t"]), "returned_samples": len(t)})


def _voltage_log(ctx, rec, run_id, video_id, series, every_s=60.0):
    """A voltage log with its own clock (not per frame): markers from the full series, a 1-per-minute copy stored."""
    t, v = np.asarray(series["t"]), np.asarray(series["v"])
    src = series["source"]
    source = ctx.ledger.put_file(src["path"], kind="series", media_type="text/csv", label=src["label"],
                                 source=Source(uri=src["uri"], citation=src.get("citation"), licence=src.get("licence")))
    rec["outputs"].append(source.id)
    if not series.get("segments"):
        return _voltage_samples(ctx, rec, run_id, video_id, series, source.id)
    parents = [source.id]
    keep = np.flatnonzero(np.diff(np.floor(t / every_s), prepend=-1) > 0)
    tid = _measure(rec, run_id, "time_base", t[keep].tolist(), "s", series["dt"] / 2, "resolution",
                   f"potentiostat clock of the voltage log, one sample per {series['dt']:g} s (stored one per "
                   f"{every_s:g} s)", label="Voltage log clock", artifact_ids=parents)
    movie_window = series.get("movie_time_range_s")
    movie_tid = None
    if movie_window:
        covered = keep[(t[keep] >= movie_window[0]) & (t[keep] <= movie_window[1])]
        movie_tid = _measure(rec, run_id, "time_base", t[covered].tolist(), "s", series["dt"] / 2, "resolution",
                             "voltage log clock restricted to the recorded movie's time window; not a frame clock",
                             label="Voltage clock within movie window", artifact_ids=[video_id, source.id],
                             time_range=tuple(movie_window))
    vid = _measure(rec, run_id, "cell_voltage", v[keep].tolist(), "V", None, "none",
                   series["method"] + "; voltage accuracy not stated", label="Cell voltage",
                   calibration_ids=[tid], artifact_ids=parents)
    events, lines = [], []
    for s in series["segments"]:
        lines.append(f"cycle {s['cycle']} {s['mode'].replace('_', ' ')} at {s['current_mA_cm2']:g} mA/cm2: "
                     f"{(s['t_start_s'] - t[0]) / 3600:.2f}-{(s['t_end_s'] - t[0]) / 3600:.2f} h")
    label = {"first_below_0V": "below 0 V", "plateau_reached": "plateau reached", "voltage_minimum": "lowest voltage"}
    marks = []
    for m in instruments.plating_markers(t, v, series["segments"]):
        window = m["time_window_s"]
        in_movie = movie_window and movie_window[0] <= window[0] and window[1] <= movie_window[1]
        mid = _measure(rec, run_id, "elapsed_time", m["t_in_charge_h"], "h", m["uncertainty_h"],
                       "bound", m["definition"] + "; " + m["uncertainty_method"] +
                       "; time from the start of that charge; not independent visual proof of plating",
                       calibration_ids=[movie_tid if in_movie else tid],
                       artifact_ids=[video_id, source.id] if in_movie else parents, subject=m["subject"],
                       time_range=tuple(window), valid_domain=("Electrical log during this movie's time window" if in_movie
                                     else "Electrical log outside this movie's coverage; no movie images"),
                       label=f"{m['subject']}: {label[m['kind']]}")
        events.append(mid)
        marks.append(f"{m['subject']}: {m['definition']} after {m['t_in_charge_h']:.2f} h "
                     f"+/-{m['uncertainty_h']:.2f} h assuming voltage error within +/-10 mV (accuracy not stated; "
                     f"{m['voltage_V'] * 1000:.1f} mV logged) — {mid}")
    return ToolResult(run_id, "ok", f"Voltage log over {(t[-1] - t[0]) / 3600:.1f} h ({len(t)} samples, one every "
                      f"{series['dt']:g} s; voltage series {vid} and clock {tid} keep one per minute). Protocol: "
                      + "; ".join(lines) + ". Plating markers (time into each charge): " + "; ".join(marks)
                      + ". " + series.get("notes", "") + " The full source log "
                      "is retained for computation; the returned series keeps one sample per minute.",
                      data={"events": events, "voltage_id": vid, "time_base_id": tid, "source_id": source.id,
                            "full_samples": len(t), "returned_samples": len(keep)})


@tool("read_cell_voltage",
      "Read the cell's voltage log for this experiment and find when the cell shorts (voltage collapses to ~0 V) "
      "and when it recovers; for a charging log, when the voltage drops below 0 V (lithium plating).",
      {"required": ["video_id"], "properties": {"video_id": {"type": "string"}}},
      "Reading the battery's voltage")
def read_cell_voltage(ctx, run_id, rec, video_id):
    rec["inputs"].append(video_id)
    exp = _experiment(ctx.ledger, video_id)
    series = instruments.voltage(exp)
    if series is None:
        return ToolResult(run_id, "ok", "No voltage log for this experiment.")
    if series.get("clock") == "log":
        return _voltage_log(ctx, rec, run_id, video_id, series)
    t, v = np.asarray(series["t"]), np.asarray(series["v"])
    tid = _measure(rec, run_id, "time_base", instruments.frame_times(exp), "s", series["dt"] / 2, "resolution",
                   "instrument clock per frame", label="Real time of each frame", artifact_ids=[video_id])
    vid = _measure(rec, run_id, "cell_voltage", v.tolist(), "V", 0.05, "resolution", series["method"],
                   label="Cell voltage", calibration_ids=[tid], artifact_ids=[video_id])
    shorted = np.abs(v) < series["short_threshold_v"]
    events, lines = [], []
    edges = np.flatnonzero(np.diff(shorted.astype(int)))
    for e in edges:
        kind = "voltage_collapse_time" if shorted[e + 1] else "voltage_recovery_time"
        when = float((t[e] + t[e + 1]) / 2)
        mid = _measure(rec, run_id, kind, when, "s", float(t[e + 1] - t[e]) / 2, "bound",
                       f"|V| crosses {series['short_threshold_v']} V between captures", calibration_ids=[tid],
                       artifact_ids=[video_id],
                       label="Battery shorts" if kind == "voltage_collapse_time" else "Battery recovers")
        events.append(mid)
        lines.append(f"{'short starts' if 'collapse' in kind else 'short ends'} at {when:.0f} s "
                     f"(± {(t[e + 1] - t[e]) / 2:.0f} s) — {mid}")
    return ToolResult(run_id, "ok", f"Voltage log over {t[0]:.0f}-{t[-1]:.0f} s. " +
                      ("; ".join(lines) if lines else "No short detected.") + f" Voltage series {vid}.",
                      data={"events": events, "voltage_id": vid})


# ------------------------------------------------------------------ findings

@tool("propose_finding",
      "Propose a finding for checking. It is accepted only if the cited measurements support it under trusted "
      "checks (units, recorded scale and clock, sound evidence, required cross-checks). Types: value_within "
      "{lo,hi,unit}; values_agree {tol?}; events_coincide {tol}; trend {direction: increasing|decreasing|steady, "
      "steady_tolerance?} with [series, time]; exists.",
      {"required": ["type", "measurement_ids", "params", "statement"], "properties": {
          "type": {"type": "string", "enum": ["value_within", "values_agree", "events_coincide", "trend", "exists"]},
          "measurement_ids": {"type": "array", "items": {"type": "string"}},
          "params": {"type": "object"},
          "statement": {"type": "string", "description": "One plain sentence a non-specialist understands."},
          "supersedes": {"type": "string"}}},
      "Checking a finding")
def propose_finding(ctx, run_id, rec, type, measurement_ids, params, statement, supersedes=None):
    rec["inputs"] += measurement_ids
    cid = f"claim_{uuid.uuid4().hex[:10]}"
    claim = Claim(id=cid, campaign_id=ctx.campaign_id, author="worker", statement=statement, supersedes=supersedes,
                  predicate=Predicate(type=type, params=params, measurement_ids=measurement_ids),
                  created_at=time.time())
    ctx.ledger.add(claim)
    ctx.ledger.emit(ctx.campaign_id, "claim.proposed", "worker", statement, refs=[cid],
                    data={"claim": claim.model_dump(mode="json")})
    rv = gate.review(ctx.ledger, cid)
    title = {"supported": "Finding earned", "refuted": "Finding contradicted by the evidence",
             "inconclusive": "Not proven yet", "unsupported": "Finding refused"}[rv.verdict]
    ctx.ledger.emit(ctx.campaign_id, "claim.reviewed", "gate", title, detail=rv.reason, refs=[cid, rv.id],
                    data={"review": rv.model_dump()})
    checks = "; ".join(f"{'pass' if c.passed else 'FAIL'} {c.name}: {c.detail}" for c in rv.checks)
    # a hint for the planner only: the gate's own wording stays fixed so recorded verdicts re-check byte for byte
    hint = (" Give the finding's unit as the measurements' unit." if any(c.name == "units match" and not c.passed
                                                                       for c in rv.checks) else "")
    return ToolResult(run_id, "ok", f"{title} ({rv.verdict}). {rv.reason} Checks: {checks}.{hint} Finding id {cid}.",
                      data={"claim_id": cid, "verdict": rv.verdict})


@tool("record_lesson",
      "Save a lesson for future campaigns: a procedure or pitfall learned here, with the evidence that taught it.",
      {"required": ["text", "scope", "evidence_ids"], "properties": {
          "text": {"type": "string", "description": "One plain sentence a non-specialist understands, about the "
                   "experiment or the method, not tool or model names, recordings or replays. People read it."},
          "scope": {"type": "string"},
          "evidence_ids": {"type": "array", "items": {"type": "string"}}}},
      "Remembering a lesson")
def record_lesson(ctx, run_id, rec, text, scope, evidence_ids):
    lid = f"lesson_{uuid.uuid4().hex[:10]}"
    ctx.ledger.add(Lesson(id=lid, text=text, scope=scope, evidence_ids=evidence_ids, learned_in=ctx.campaign_id,
                          created_at=time.time()))
    ctx.ledger.emit(ctx.campaign_id, "memory.learned", "orchestrator", text[:90], refs=[lid])
    return ToolResult(run_id, "ok", f"Lesson saved ({lid}).")


def image_data_url(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()
