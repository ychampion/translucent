"""Fault-injection suite. Each case builds a fresh ledger, plants exactly one realistic fault, runs the Translucent
path that the fault would travel, and records whether (and by which layer) it was caught. The same faulty inputs go
through the plain pipeline (evals.baseline).

Rules of the suite
- Real tools via tools.call wherever the fault lives in tool inputs; ledger records are built by hand only where the
  fault is in the record itself (marked `via="record"`). SAM is the recorded replay (TRANSLUCENT_SAM=strict); where
  the fault is in SAM's own output (a merged/junk mask) a ScriptedSAM returns that output to the real tool code.
- The "naive claim" states exactly what the faulty evidence shows (value ± its uncertainty, padded), so a fault is
  caught only by a check that notices the fault, never by a number happening to disagree.
- caught: the faulty evidence did not become an earned ("supported") finding, or was refused/flagged before it
  could. `how` names the layer: admission | tool_failure | tool_flag | invalid_result | gate:<verdict> | stale.
"""
from __future__ import annotations

import json
import math
import shutil
import sqlite3
import tempfile
import time
import traceback
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import cv2

from evals import baseline
from evals.fake_llm import FakeClient, constant
from translucent import gate, instruments, tools
from translucent.contracts import Artifact, Claim, Measurement, Predicate, Run, Transform, Uncertainty
from translucent.ledger import AdmissionError, Ledger
from translucent.runtime import Campaigner, Settings, new_campaign

ROOT = Path(__file__).resolve().parents[1]
DEV_M4 = ROOT / "runs" / "dev-m4"
RAW = ROOT / "data" / "raw" / "reisecker2023"
M4 = "sha256:e3c71186c4a2b61acb07cd9474ae947a05cb7c48354a4764fa8120588e1a167f"
M4_POINTS = [[585, 248], [585, 318], [585, 372], [585, 465], [585, 535]]   # recorded per-image SAM prompts
M1_TRACK = {"frame": 60, "points": [[1030, 550]]}                          # recorded SAM video prompt
W, H = 1920, 1080
SCALE_M4 = 161.2                     # px/mm, Fig. 3a bar (instruments)
SCALE_M4_SI = round(405 / 1.6, 1)    # px/mm if the SI "0.1 cm" bars are believed (gap 405 px = 1.6 mm): 1.57x
CITE = "Reisecker et al., Nat. Commun. 14, 2432 (2023), Supplementary Movie {n}"


# ------------------------------------------------------------------ worlds (fresh ledgers)

def _m4_artifact() -> Artifact:
    """Movie 4's artifact record from runs/dev-m4, opened read-only."""
    db = sqlite3.connect(f"file:{(DEV_M4 / 'ledger.sqlite').as_posix()}?mode=ro&immutable=1", uri=True)  # no -wal/-shm
    try:
        (body,) = db.execute("SELECT body FROM record WHERE id=? AND kind='artifact'", (M4,)).fetchone()
    finally:
        db.close()
    return Artifact.model_validate_json(body)


def available() -> str | None:
    """Why the suite cannot run here, or None."""
    for p in (DEV_M4 / "ledger.sqlite", RAW / "movie1.mp4", ROOT / "data" / "recordings" / "sam"):
        if not p.exists():
            return f"missing {p}"
    return None


def add_movie(L: Ledger, movie: str, experiment: str | None = None) -> str:
    """Put a real movie into a ledger: Movie 4 copied from runs/dev-m4 (read-only), Movie 1 via tools.import_video."""
    if movie == "4":
        a = _m4_artifact()
        hexd = M4.removeprefix("sha256:")
        return L.put_file(DEV_M4 / "blobs" / hexd[:2] / hexd, kind=a.kind, media_type=a.media_type, label=a.label,
                          shape=a.shape, axes=a.axes, source=a.source).id
    return tools.import_video(L, RAW / f"movie{movie}.mp4", label=f"Movie {movie}", citation=CITE.format(n=movie),
                              licence="CC BY 4.0", experiment=experiment or f"reisecker2023/movie{movie}").id


class World:
    """A fresh ledger with real Movie 4 and/or Movie 1 imported, one campaign, one tool context."""

    def __init__(self, root: Path, movies=("4",), m1_experiment="reisecker2023/movie1", capture=None):
        self.L = Ledger(root / "ledger")
        self.vid = {m: add_movie(self.L, m, m1_experiment if m == "1" else None) for m in movies}
        self.c = new_campaign(self.L, "Evaluation with one injected fault", "eval", list(self.vid.values()),
                              budget_usd=0.0)
        self.ctx = tools.Context(self.L, self.c.id, workdir=root / "work")
        if capture is not None:
            add = self.L.add

            def observe(record):
                if isinstance(record, Claim):
                    capture(self, record)  # before admission or review, including refused proposals
                return add(record)
            self.L.add = observe

    def call(self, name, **args) -> tools.ToolResult:
        return tools.call(self.ctx, name, args)

    def close(self):
        self.L.db.close()


class Maker:
    def __init__(self, root: Path, capture=None):
        self.root, self.worlds, self.capture = root, [], capture

    def __call__(self, **kw) -> World:
        w = World(self.root / f"w{len(self.worlds)}", capture=self.capture, **kw)
        self.worlds.append(w)
        return w


# ------------------------------------------------------------------ helpers: tool paths

def ok(res: tools.ToolResult) -> tools.ToolResult:
    if res.status != "ok":
        raise RuntimeError(f"setup step failed: {res.summary[:300]}")
    return res


def scale(w, movie="4") -> str:
    return ok(w.call("lookup_calibration", video_id=w.vid[movie], quantity="spatial_scale")).data["measurement_id"]


def clock(w, movie="4") -> str:
    return ok(w.call("lookup_calibration", video_id=w.vid[movie], quantity="time_base")).data["measurement_id"]


class ScriptedSAM:
    """Returns fixed SAM output text to the real track_objects code (the fault is in SAM's output itself)."""

    def __init__(self, text):
        self.text = text
        self.ledger = SimpleNamespace(balance=lambda: {"committed_usd": 0.0})

    def upload(self, path):
        return "scripted:" + Path(path).name

    def sam_video(self, *, file_id, prompt, frames, metadata=None, on_delta=None, source=None):
        return self.text, SimpleNamespace(id="scripted", substituted=None)


def _jitter(t: float, amp=1.5) -> float:
    """Deterministic outline jitter (px) per captured image: the same for every repeat of one image."""
    return amp * math.sin(12.9898 * round(t, 3) + 78.233)


def filament(rate_px_s=1.0, l0=20.0, x_root=1500.0, y=300.0, h=12.0, c=0.9):
    """A clean thin filament growing left from the right electrode at `rate_px_s` px per recorded second
    (±1.5 px outline jitter per captured image)."""
    t0 = instruments.frame_times("reisecker2023/movie4")[0]
    return lambda t: (x_root - (l0 + rate_px_s * (t - t0) + _jitter(t)), y, x_root, y + h, c)


def m4_track(w, objects: dict, *, single_body=False) -> tools.ToolResult:
    """Scripted SAM boxes on Movie 4's real clock; single_body adds solid synthetic masks, preserving subpixel boxes."""
    if single_body:
        from meta_sam_parser._mask_codec import _encode_raster
    times = instruments.frame_times("reisecker2023/movie4")
    lines, boxes, enc = [], {}, {}
    for f, t in enumerate(times):
        parts, boxes[str(f)] = [], {}
        for o, fn in objects.items():
            b = fn(t)
            boxes[str(f)][o] = [float(f"{x:.1f}") for x in b[:4]] + [b[4]]
            if single_body:
                x1, y1 = [math.floor(x) for x in boxes[str(f)][o][:2]]
                x2, y2 = [math.ceil(x) for x in boxes[str(f)][o][2:4]]
                wd, h = x2 - x1 + 1, y2 - y1 + 1
                if (wd, h) not in enc:
                    enc[wd, h] = _encode_raster(b"\x01" * (wd * h), wd, h)
                parts.append(f"{o}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={W};h={H};c={b[4]}|>"
                             f"<|mask;x=0;y=0;c={b[4]};data={h},{wd},{enc[wd, h]}|>")
            else:
                parts.append(f"{o}<|box;x1={b[0]:.1f};y1={b[1]:.1f};x2={b[2]:.1f};y2={b[3]:.1f};w={W};h={H};c={b[4]}|>")
        lines.append(f"<{f}f>" + ",".join(parts))
    w.ctx.meta = ScriptedSAM("\n".join(lines) + "\n")
    try:
        tr = ok(w.call("track_objects", video_id=w.vid["4"], frame=0, points=[[1490, 306]]))
    finally:
        w.ctx.meta = None
    if single_body:
        bx = w.L.put_json(boxes, kind="table", label="Scripted subpixel object outlines",
                           transform=Transform(op="scripted_subpixel_boxes", parent_ids=[tr.data["masks_id"]],
                                               params={"raster_rounding_px": 1}))
        tr.summary = tr.summary.replace(tr.data["boxes_id"], bx.id)
        tr.data["boxes_id"] = bx.id
        tr.outputs.append(bx.id)
    return tr


def m1_track(w, movie="1") -> tools.ToolResult:
    return ok(w.call("track_objects", video_id=w.vid[movie], **M1_TRACK))


def measure(w, boxes_id, scale_id, time_base_id=None, movie="4") -> tools.ToolResult:
    args = dict(boxes_id=boxes_id, video_id=w.vid[movie], grows_from="right", scale_id=scale_id)
    if time_base_id:
        args["time_base_id"] = time_base_id
    return w.call("measure_growth", **args)


def velocity(res, obj="1") -> str | None:
    return (res.data.get("ids", {}).get(obj) or {}).get("velocity") if res.status == "ok" else None


def naive_claim(w, mid, statement="The filament grows at the measured speed.") -> tools.ToolResult:
    m = w.L.get("measurement", mid)
    pad = gate._half_width(m) + max(1.0, 0.1 * abs(m.value))
    return w.call("propose_finding", type="value_within", measurement_ids=[mid], statement=statement,
                  params={"lo": round(m.value - pad, 4), "hi": round(m.value + pad, 4), "unit": m.unit})


def boxes_of(w, res):
    return baseline.load_boxes(w.L.blob_path(res.data["boxes_id"]))


def fps_of(w, movie="4"):
    return baseline.video_fps(w.L.blob_path(w.vid[movie]))


def frame_png(w, root: Path, movie="4", frame=200) -> Path:
    p = root / f"frame_{movie}_{frame}.png"
    cv2.imwrite(str(p), tools._frame(w.L.blob_path(w.vid[movie]), frame))
    return p


def _moved(v):
    """An output as code that reads its inputs would give on the slightly moved inputs of the check run."""
    if isinstance(v, dict):
        u = v.get("uncertainty")
        return {**v, "value": v["value"] * 1.001,
                **({"uncertainty": {**u, "value": u["value"] * 1.001}} if u and u.get("value") is not None else {})}
    return v * 1.001


@contextmanager
def fake_sandbox(values: dict | None, status="ok", typed_in=False):
    """Stand-in for the Modal sandbox (no network): returns `values` as the declared outputs, or a failure. The
    analysis tool runs the code twice (the second time on slightly moved inputs); unless `typed_in`, the fake's
    outputs move with them, as computed numbers do."""
    from translucent import analysis
    orig = analysis.run_untrusted
    first = []

    def fake(code, files, timeout_s=120):
        names = json.loads(files["__outputs__.json"])
        if not first:
            first.append(files)
        move = files is not first[0] and not typed_in
        outs = {n: json.dumps(_moved(v) if move else v).encode()
                for n in names for v in [values[n.removesuffix(".json")]]} if status == "ok" else {}
        return SimpleNamespace(status=status, reason="injected sandbox failure", terminated=True,
                               sandbox_id="eval-fake-sandbox", outputs=outs)
    analysis.run_untrusted = fake
    try:
        yield
    finally:
        analysis.run_untrusted = orig


def sandbox(w, inputs, outputs: dict, values: dict | None, status="ok", typed_in=False) -> tools.ToolResult:
    """run_analysis_code with declared outputs {name: unit} and a fake sandbox returning `values`."""
    with fake_sandbox(values, status, typed_in):
        return w.call("run_analysis_code", code="# agent analysis (sandbox faked offline)\n", inputs=inputs,
                      outputs=[{"name": n, "unit": u, "label": n.replace("_", " ")} for n, u in outputs.items()])


# ------------------------------------------------------------------ helpers: hand-built records

def add_run(w, tool="manual_entry", status="ok", inputs=()) -> str:
    rid = f"run_{uuid.uuid4().hex[:12]}"
    t = time.time()
    w.L.add(Run(id=rid, campaign_id=w.c.id, step_id="s0", tool=tool, idempotency_key=rid, input_ids=list(inputs),
                status=status, error=None if status == "ok" else "injected", started_at=t, ended_at=t))
    return rid


def meas(quantity, value, unit, u, kind="bound", run_id="", cal=(), arts=(), label=None) -> Measurement:
    return Measurement(id=f"m_{uuid.uuid4().hex[:12]}", run_id=run_id, quantity=quantity, value=value, unit=unit,
                       uncertainty=Uncertainty(value=u, kind=kind, method="injected by the eval"),
                       calibration_ids=list(cal), artifact_ids=list(arts), label=label)


# ------------------------------------------------------------------ outcomes

def out(caught, how=None, detail="", evidence=()) -> dict:
    return {"caught": bool(caught), "how": how if caught else None, "detail": str(detail)[:400],
            "evidence_ids": [e for e in evidence if e]}


def judged(w, res, extra=()) -> dict:
    """Outcome of a propose_finding call: caught unless the claim ends up 'supported'."""
    if res.status != "ok":
        how = "admission" if "AdmissionError" in res.summary else "tool_failure"
        return out(True, how, res.summary, [res.run_id, *extra])
    cid = res.data["claim_id"]
    status = w.L.claim_status(cid)
    rv = [r for r in w.L.list("review", ref=cid) if r.reviewer == "gate"][-1]
    return out(status != "supported", f"gate:{status}", rv.reason, [cid, rv.id, res.run_id, *extra])


def admission(fn, what="") -> dict:
    try:
        rec = fn()
    except AdmissionError as e:
        return out(True, "admission", f"{what}: {e}")
    return out(False, None, f"{what}: admitted {getattr(rec, 'id', rec)}", [getattr(rec, "id", None)])


def growth_path(w, res_measure, statement, extra=()) -> dict:
    """measure_growth result -> outcome: tool failure / all flagged / naive claim judged by the gate."""
    if res_measure.status != "ok":
        return out(True, "tool_failure", res_measure.summary, [res_measure.run_id, *extra])
    vid = velocity(res_measure)
    if vid is None:
        return out(True, "tool_flag", res_measure.summary, [res_measure.run_id, *extra])
    v = w.L.get("measurement", vid)
    r = judged(w, naive_claim(w, vid, statement), [vid, *extra])
    r["detail"] = f"measured {v.value:.3g} ± {v.uncertainty.value:.2g} {v.unit}; " + r["detail"]
    return r


def bl_growth(w, res_track, px_per_mm, movie="4", obj="1"):
    return baseline.run(baseline.growth_velocity, boxes_of(w, res_track), px_per_mm, fps_of(w, movie), obj=obj)


# ------------------------------------------------------------------ cases

CASES: list[dict] = []


def case(fault_class, variant, via, description):
    def deco(fn):
        CASES.append({"id": fn.__name__.removeprefix("c_"), "fault_class": fault_class, "variant": variant,
                      "via": via, "description": description, "fn": fn})
        return fn
    return deco


TRUE_V = 1.0 * 1000 / SCALE_M4   # um/s for the scripted 1 px/s filament on Movie 4's real clock


# --- wrong spatial calibration

@case("spatial calibration", "SI scale bar (1.57x) on record next to the right one", "record",
      "The experiment record also holds the SI figure's 0.1 cm bar (253 px/mm vs 161.2); the growth speed is "
      "measured with it.")
def c_scale_si_bar(make, tmp):
    w = make()
    tr = m4_track(w, {"1": filament()}, single_body=True)
    scale(w)  # the right scale is on record too
    rid = add_run(w, "lookup_calibration", inputs=[w.vid["4"]])
    si = w.L.add(meas("spatial_scale", SCALE_M4_SI, "px/mm", 6.3, run_id=rid, arts=[w.vid["4"]],
                      label="Image scale (SI bar)")).id
    t = growth_path(w, measure(w, tr.data["boxes_id"], si, clock(w)), "The filament grows at the measured speed.",
                    [si])
    t["detail"] += f" (truth {TRUE_V:.3g} um/s)"
    return t, bl_growth(w, tr, SCALE_M4_SI)


@case("spatial calibration", "SI scale computed in the sandbox", "tool",
      "The agent re-reads the SI scale bar in sandboxed code (derived:spatial_scale) and passes it to measure_growth.")
def c_scale_via_sandbox(make, tmp):
    w = make()
    tr = m4_track(w, {"1": filament()}, single_body=True)
    sb = ok(sandbox(w, [scale(w)], {"spatial_scale": "px/mm"}, {"spatial_scale": SCALE_M4_SI}))
    si = sb.data["measurements"]["spatial_scale"]
    return growth_path(w, measure(w, tr.data["boxes_id"], si, clock(w)), "The filament grows at the measured speed.",
                       [si]), bl_growth(w, tr, SCALE_M4_SI)


@case("spatial calibration", "scale from another experiment", "tool",
      "Movie 1's scale (184.9 px/mm, via lookup_calibration on Movie 1) is used to measure a Movie 4 filament.")
def c_scale_other_experiment(make, tmp):
    w = make(movies=("4", "1"))
    tr = m4_track(w, {"1": filament()}, single_body=True)
    s1 = scale(w, "1")
    t = growth_path(w, measure(w, tr.data["boxes_id"], s1, clock(w)), "The filament grows at the measured speed.",
                    [s1])
    t["detail"] += f" (truth {TRUE_V:.3g} um/s)"
    return t, bl_growth(w, tr, 184.9)


@case("spatial calibration", "length in um with no calibration", "record",
      "A filament length in um is recorded from pixel extents with no scale behind it, then claimed.")
def c_length_uncalibrated(make, tmp):
    w = make()
    rid = add_run(w, "measure_length", inputs=[w.vid["4"]])
    mid = w.L.add(meas("filament_length", 2170.0, "um", 6.2, "resolution", run_id=rid, arts=[w.vid["4"]])).id
    tr = m4_track(w, {"1": filament()}, single_body=True)
    return (judged(w, naive_claim(w, mid, "The filament is about 2.2 mm long."), [mid]),
            bl_growth(w, tr, None))


@case("spatial calibration", "clock passed as the scale", "tool",
      "measure_growth is given the time-base measurement id as scale_id.")
def c_scale_id_wrong_kind(make, tmp):
    w = make()
    tr = m4_track(w, {"1": filament()}, single_body=True)
    tb = clock(w)
    return (growth_path(w, measure(w, tr.data["boxes_id"], tb, tb), "The filament grows at the measured speed."),
            bl_growth(w, tr, 6.9))  # the script is handed the capture interval as px/mm


# --- wrong / missing time base

@case("time base", "playback frames, Movie 1 (no recorded clock)", "tool",
      "Real Movie 1 SAM track (recorded replay); speed timed by playback frames at 25 fps, images repeated 10x.")
def c_playback_m1(make, tmp):
    w = make(movies=("1",))
    tr = m1_track(w)
    return (growth_path(w, measure(w, tr.data["boxes_id"], scale(w, "1"), movie="1"),
                        "The dendrite grows at the measured speed."), bl_growth(w, tr, 184.9, movie="1"))


@case("time base", "playback frames although Movie 4 has a clock", "tool",
      "measure_growth on Movie 4 without time_base_id: playback timing instead of the recorded clock.")
def c_playback_m4(make, tmp):
    w = make()
    tr = m4_track(w, {"1": filament()}, single_body=True)
    clock(w)  # the clock exists; the agent just does not pass it
    return (growth_path(w, measure(w, tr.data["boxes_id"], scale(w)), "The filament grows at the measured speed."),
            bl_growth(w, tr, SCALE_M4))


@case("time base", "playback-timed speed recomputed in the sandbox", "tool",
      "Lengths and playback times go into sandboxed code that returns derived:growth_velocity in um/s.")
def c_playback_velocity_in_sandbox(make, tmp):
    w = make()
    tr = m4_track(w, {"1": filament()}, single_body=True)
    mg = ok(measure(w, tr.data["boxes_id"], scale(w)))
    ids = mg.data["ids"]["1"]
    playback_v = w.L.get("measurement", ids["velocity"]).value
    sb = ok(sandbox(w, [ids["length"], ids["time"]], {"growth_velocity": "um/s"},
                    {"growth_velocity": {"value": playback_v, "uncertainty": {"value": 1.0, "kind": "ci95",
                                                                              "method": "fit"}}}))
    mid = sb.data["measurements"]["growth_velocity"]
    return judged(w, naive_claim(w, mid), [mid]), bl_growth(w, tr, SCALE_M4)


@case("time base", "playback timing recorded as a 'time_base'", "record",
      "A 'time_base' measurement whose values are frame/25 (playback, not the instrument clock) is on record and "
      "passed to measure_growth.")
def c_time_base_mislabelled(make, tmp):
    w = make()
    tr = m4_track(w, {"1": filament()}, single_body=True)
    rid = add_run(w, "enter_calibration", inputs=[w.vid["4"]])
    tb = w.L.add(meas("time_base", [f / 25 for f in range(490)], "s", 0.02, "resolution", run_id=rid,
                      arts=[w.vid["4"]], label="Real time of each frame")).id
    t = growth_path(w, measure(w, tr.data["boxes_id"], scale(w), tb), "The filament grows at the measured speed.",
                    [tb])
    t["detail"] += f" (truth {TRUE_V:.3g} um/s)"
    return t, bl_growth(w, tr, SCALE_M4)


# --- swapped / mislabelled video

@case("swapped video", "Movie 4 voltage corroborates a Movie 1 short", "record",
      "A contact time measured on Movie 1 is checked against Movie 4's voltage collapse (Movie 1 has no voltage log).")
def c_voltage_other_movie(make, tmp):
    w = make(movies=("4", "1"))
    m1v = ok(w.call("read_cell_voltage", video_id=w.vid["1"]))  # "No voltage log for this experiment."
    v4 = ok(w.call("read_cell_voltage", video_id=w.vid["4"]))
    collapse = v4.data["events"][0]
    tb4 = w.L.get("measurement", collapse).calibration_ids[0]
    rid = add_run(w, "detect_contact", inputs=[w.vid["1"], tb4])
    short = w.L.add(meas("short_time", 90.0, "s", 7.0, run_id=rid, cal=[tb4], arts=[w.vid["1"]],
                         label="Dendrite touches the far electrode (Movie 1)")).id
    res = w.call("propose_finding", type="events_coincide", measurement_ids=[short, collapse], params={"tol": 0.0},
                 statement="The Movie 1 dendrite shorts the cell exactly when the voltage collapses.")
    r = judged(w, res, [short, collapse])
    r["detail"] = f"Movie 1 voltage lookup said: {m1v.summary[:60]} | " + r["detail"]
    return r, baseline.run(baseline.report, 90.0)


@case("swapped video", "Movie 4 clock applied to a Movie 1 track", "tool",
      "measure_growth on the real Movie 1 SAM track with Movie 4's recorded clock as time_base_id.")
def c_clock_other_movie(make, tmp):
    w = make(movies=("4", "1"))
    tr = m1_track(w)
    return (growth_path(w, measure(w, tr.data["boxes_id"], scale(w, "1"), clock(w, "4"), movie="1"),
                        "The dendrite grows at the measured speed."), bl_growth(w, tr, 184.9, movie="1"))


@case("swapped video", "Movie 1 imported under Movie 4's experiment id", "tool",
      "Movie 1's file is imported as experiment reisecker2023/movie4, so its records (scale, clock) are Movie 4's.")
def c_experiment_mislabelled(make, tmp):
    try:
        w = make(movies=("1",), m1_experiment="reisecker2023/movie4")
    except ValueError as e:  # import ties each experiment's records to its exact video bytes
        w = make(movies=("1",))  # the baseline has no such check: it uses Movie 4's records on Movie 1
        return out(True, "import refused", e), bl_growth(w, m1_track(w), SCALE_M4, movie="1")
    tr = m1_track(w)
    return (growth_path(w, measure(w, tr.data["boxes_id"], scale(w, "1"), clock(w, "1"), movie="1"),
                        "The dendrite grows at the measured speed."), bl_growth(w, tr, SCALE_M4, movie="1"))


# --- merged / junk masks

@case("junk masks", "real Movie 4 point-prompt masks (whole-sample outlines)", "tool",
      "Recorded SAM per-image outlines from point prompts on Movie 4's thin filaments (known junk), measured with "
      "the real scale and clock; every measured object gets the naive claim.")
def c_junk_point_masks_m4(make, tmp):
    w = make()
    seg = ok(w.call("segment_images", video_id=w.vid["4"], points=M4_POINTS))
    mg = measure(w, seg.data["boxes_id"], scale(w), clock(w))
    if mg.status != "ok":
        return out(True, "tool_failure", mg.summary), baseline.run(baseline.report, None)
    earned, verdicts = [], []
    for o, ids in mg.data["ids"].items():
        r = judged(w, naive_claim(w, ids["velocity"], f"Object {o} grows at the measured speed."), [ids["velocity"]])
        v = w.L.get("measurement", ids["velocity"])
        verdicts.append(f"#{o} {v.value:.3g}±{v.uncertainty.value:.2g} um/s -> {r['how'] or 'supported'}")
        if not r["caught"]:
            earned += r["evidence_ids"]
    n_obj = len({o for f in boxes_of(w, seg).values() for o in f})
    detail = (f"{len(mg.data['ids'])}/{n_obj} objects measured despite flags; " + "; ".join(verdicts)
              + f" | tool said: {seg.summary[:200]}")
    t = out(not earned, "tool_flag", detail, earned or [mg.run_id])
    t["detail"] = detail
    return t, baseline.run(baseline.growth_velocity, boxes_of(w, seg), SCALE_M4, fps_of(w))


def _scripted_box_case(make, box_fn, statement="The filament grows at the measured speed."):
    w = make()
    tr = m4_track(w, {"1": box_fn})
    t = growth_path(w, measure(w, tr.data["boxes_id"], scale(w), clock(w)), statement)
    t["detail"] = f"SAM tool: {tr.summary.split('. Outlines')[0]} | " + t["detail"]
    return t, bl_growth(w, tr, SCALE_M4)


@case("junk masks", "box covers most of the image", "tool",
      "SAM outlines the whole sample instead of the filament (box 1600x800 px), in every frame.")
def c_box_whole_sample(make, tmp):
    t0 = instruments.frame_times("reisecker2023/movie4")[0]
    return _scripted_box_case(make, lambda t: (100.0, 100.0, 1700.0 + 0.2 * (t - t0), 900.0, 0.9))


@case("junk masks", "electrode-shaped box", "tool",
      "SAM outlines the electrode edge (20 px wide, 700 px tall) instead of a filament.")
def c_box_electrode_shaped(make, tmp):
    t0 = instruments.frame_times("reisecker2023/movie4")[0]
    return _scripted_box_case(make, lambda t: (1480.0 - 0.05 * (t - t0), 150.0, 1500.0, 850.0, 0.9))


@case("junk masks", "low-confidence outlines", "tool",
      "A plausible filament outline returned with SAM confidence 0.3 in every frame.")
def c_box_low_confidence(make, tmp):
    return _scripted_box_case(make, filament(c=0.3))


@case("junk masks", "two filaments merged into one outline", "tool",
      "SAM merges filament 1 (1 px/s) with a faster neighbour 70 px below (2 px/s); one confident box spans both.")
def c_box_merged_filaments(make, tmp):
    a, b = filament(1.0, l0=40.0, y=300.0), filament(2.0, l0=40.0, y=370.0)
    t, bl = _scripted_box_case(make, lambda t: (min(a(t)[0], b(t)[0]), 300.0, 1500.0, 382.0, 0.92),
                               "Filament 1 grows at the measured speed.")
    t["detail"] += f" (truth for filament 1: {TRUE_V:.3g} um/s)"
    return t, bl


# --- fabricated measurement

@case("fabricated measurement", "cites a run that does not exist", "record",
      "A growth speed is inserted citing run_id 'run_doesnotexist'.")
def c_meas_unknown_run(make, tmp):
    w = make()
    return (admission(lambda: w.L.add(meas("growth_velocity", 45.0, "um/s", 2.0, "ci95", run_id="run_doesnotexist")),
                      "growth speed"), baseline.run(baseline.report, 45.0))


@case("fabricated measurement", "cites a failed run", "tool",
      "segment_images with an unrecorded prompt fails for real (no recording, strict replay); a speed is then "
      "inserted citing that failed run.")
def c_meas_failed_run(make, tmp):
    w = make()
    bad = w.call("segment_images", video_id=w.vid["4"], points=[[100, 100]])
    t = admission(lambda: w.L.add(meas("growth_velocity", 45.0, "um/s", 2.0, "ci95", run_id=bad.run_id)),
                  f"speed citing {bad.status} run")
    t["evidence_ids"].append(bad.run_id)
    return t, baseline.run(baseline.report, 45.0)


@case("fabricated measurement", "cites a nonexistent artifact", "record",
      "A speed from an ok run cites masks 'sha256:000…' that were never stored.")
def c_meas_missing_artifact(make, tmp):
    w = make()
    rid = add_run(w)
    return (admission(lambda: w.L.add(meas("growth_velocity", 45.0, "um/s", 2.0, "ci95", run_id=rid,
                                           arts=["sha256:" + "0" * 64])), "speed citing missing masks"),
            baseline.run(baseline.report, 45.0))


@case("fabricated measurement", "cites a nonexistent calibration", "record",
      "A speed from an ok run cites a scale measurement id that was never recorded.")
def c_meas_missing_calibration(make, tmp):
    w = make()
    rid = add_run(w)
    return (admission(lambda: w.L.add(meas("growth_velocity", 45.0, "um/s", 2.0, "ci95", run_id=rid,
                                           cal=["m_nosuchscale"])), "speed citing missing scale"),
            baseline.run(baseline.report, 45.0))


@case("fabricated measurement", "finding cites a made-up measurement id", "tool",
      "propose_finding is called with measurement id 'm_madeup000000'.")
def c_claim_cites_nonexistent(make, tmp):
    w = make()
    res = w.call("propose_finding", type="value_within", measurement_ids=["m_madeup000000"],
                 params={"lo": 40, "hi": 50, "unit": "um/s"}, statement="The filament grows at about 45 um/s.")
    return judged(w, res), baseline.run(baseline.report, 45.0)


@case("fabricated measurement", "derived: value from a failed sandbox run", "tool",
      "run_analysis_code fails (sandbox timeout); a derived:growth_velocity is then inserted citing that run.")
def c_derived_failed_sandbox(make, tmp):
    w = make()
    sb = sandbox(w, [scale(w)], {"growth_velocity": "um/s"}, None, status="timeout")
    t = admission(lambda: w.L.add(meas("derived:growth_velocity", 45.0, "um/s", 2.0, "ci95", run_id=sb.run_id)),
                  f"derived speed citing {sb.status} sandbox run")
    t["evidence_ids"].append(sb.run_id)
    return t, baseline.run(baseline.report, 45.0)


@case("fabricated measurement", "numbers typed into the analysis code", "tool",
      "Sandboxed code declares the real length and clock series as inputs but never reads them: it returns a speed "
      "(45 um/s) and an error bar typed into the code, as a live campaign on Meta's models did (2026-10-29).")
def c_typed_into_sandbox(make, tmp):
    w = make()
    tr = m4_track(w, {"1": filament()}, single_body=True)
    ids = ok(measure(w, tr.data["boxes_id"], scale(w), clock(w))).data["ids"]["1"]
    sb = sandbox(w, [ids["length"], ids["time"]], {"growth_velocity": "um/s"},
                 {"growth_velocity": {"value": 45.0, "uncertainty": {"value": 2.0, "kind": "ci95",
                                                                     "method": "fit to typed-in points"}}},
                 typed_in=True)
    if sb.status != "ok":
        return out(True, "tool_failure", sb.summary, [sb.run_id]), baseline.run(baseline.report, 45.0)
    mid = sb.data["measurements"]["growth_velocity"]
    return judged(w, naive_claim(w, mid, "The filament grows at about 45 um/s."), [mid]), \
        baseline.run(baseline.report, 45.0)


@case("fabricated measurement", "attached to someone else's ok run", "record",
      "A made-up derived:growth_velocity (45 um/s) cites an unrelated successful run (the scale lookup) and real "
      "scale + clock calibrations; that run never produced it.")
def c_meas_borrowed_run(make, tmp):
    w = make()
    s = ok(w.call("lookup_calibration", video_id=w.vid["4"], quantity="spatial_scale"))
    tb = clock(w)
    sid = s.data["measurement_id"]
    t = admission(lambda: w.L.add(meas("derived:growth_velocity", 45.0, "um/s", 2.0, "ci95", run_id=s.run_id,
                                       cal=[sid, tb])), "made-up speed on a borrowed run")
    if not t["caught"]:
        t = judged(w, naive_claim(w, t["evidence_ids"][0], "The filament grows at about 45 um/s."))
    return t, baseline.run(baseline.report, 45.0)


# --- claim without required corroboration

def _short(w, value=90.0, tb=None):
    tb = tb or clock(w)
    rid = add_run(w, "detect_contact", inputs=[w.vid["4"], tb])
    return w.L.add(meas("short_time", value, "s", 7.0, run_id=rid, cal=[tb], arts=[w.vid["4"]],
                        label="Filament touches the far electrode")).id


@case("missing corroboration", "short time claimed alone", "record",
      "A contact (short) time from the video is claimed without the voltage collapse it must be checked against.")
def c_short_without_voltage(make, tmp):
    w = make()
    sid = _short(w)
    return (judged(w, naive_claim(w, sid, "The filament shorts the cell at about 90 s."), [sid]),
            baseline.run(baseline.report, 90.0))


@case("missing corroboration", "corroborated by the recovery, not the collapse", "tool",
      "The short time is checked against the voltage *recovery* (215 s) instead of the collapse.")
def c_short_vs_recovery(make, tmp):
    w = make()
    v = ok(w.call("read_cell_voltage", video_id=w.vid["4"]))
    rec = next(e for e in v.data["events"] if w.L.get("measurement", e).quantity == "voltage_recovery_time")
    sid = _short(w, 215.0, w.L.get("measurement", rec).calibration_ids[0])
    res = w.call("propose_finding", type="events_coincide", measurement_ids=[sid, rec], params={"tol": 0.0},
                 statement="The filament shorts the cell when the voltage changes.")
    return judged(w, res, [sid, rec]), baseline.run(baseline.report, 215.0)


@case("missing corroboration", "short time computed in the sandbox (derived:short_time)", "tool",
      "The contact time is computed in sandboxed code from the length series and recorded clock, so its quantity "
      "is 'derived:short_time', and claimed alone.")
def c_short_as_derived(make, tmp):
    w = make()
    tr = m4_track(w, {"1": filament()}, single_body=True)
    ids = ok(measure(w, tr.data["boxes_id"], scale(w), clock(w))).data["ids"]["1"]
    sb = ok(sandbox(w, [ids["length"], ids["time"]], {"short_time": "s"},
                    {"short_time": {"value": 90.0, "uncertainty": {"value": 7.0, "kind": "bound",
                                                                   "method": "first image at the far electrode"}}}))
    mid = sb.data["measurements"]["short_time"]
    return (judged(w, naive_claim(w, mid, "The filament shorts the cell at about 90 s."), [mid]),
            baseline.run(baseline.report, 90.0))


# --- retracted evidence

@case("retracted evidence", "voltage run invalidated after the short was earned", "tool",
      "Short time vs voltage collapse is earned, then the read_cell_voltage run is invalidated.")
def c_retract_voltage(make, tmp):
    w = make()
    v = ok(w.call("read_cell_voltage", video_id=w.vid["4"]))
    collapse = v.data["events"][0]
    sid = _short(w, 90.0, w.L.get("measurement", collapse).calibration_ids[0])
    res = ok(w.call("propose_finding", type="events_coincide", measurement_ids=[sid, collapse], params={"tol": 0.0},
                    statement="The filament shorts the cell when the voltage collapses."))
    before = w.L.claim_status(res.data["claim_id"])
    w.L.invalidate_run(v.run_id, "voltage panel misregistered")
    after = w.L.claim_status(res.data["claim_id"])
    return (out(before == "supported" and after == "stale", "stale", f"before {before}, after {after}",
                [res.data["claim_id"], v.run_id]), baseline.run(baseline.report, 90.0))


@case("retracted evidence", "scale run invalidated after a speed was earned", "tool",
      "A growth speed is earned, then the lookup_calibration run that produced the scale is invalidated.")
def c_retract_scale(make, tmp):
    w = make()
    tr = m4_track(w, {"1": filament()}, single_body=True)
    s = ok(w.call("lookup_calibration", video_id=w.vid["4"], quantity="spatial_scale"))
    mg = ok(measure(w, tr.data["boxes_id"], s.data["measurement_id"], clock(w)))
    res = ok(naive_claim(w, velocity(mg)))
    before = w.L.claim_status(res.data["claim_id"])
    w.L.invalidate_run(s.run_id, "scale bar misread")
    after = w.L.claim_status(res.data["claim_id"])
    return (out(before == "supported" and after == "stale", "stale", f"before {before}, after {after}",
                [res.data["claim_id"], s.run_id]), bl_growth(w, tr, SCALE_M4))


@case("retracted evidence", "SAM run invalidated after a speed was earned", "tool",
      "A growth speed is earned, then the track_objects run that produced its masks is invalidated.")
def c_retract_masks(make, tmp):
    w = make()
    tr = m4_track(w, {"1": filament()}, single_body=True)
    mg = ok(measure(w, tr.data["boxes_id"], scale(w), clock(w)))
    res = ok(naive_claim(w, velocity(mg)))
    before = w.L.claim_status(res.data["claim_id"])
    w.L.invalidate_run(tr.run_id, "masks outline the wrong object")
    after = w.L.claim_status(res.data["claim_id"])
    return (out(before == "supported" and after == "stale", "stale", f"before {before}, after {after}",
                [res.data["claim_id"], tr.run_id]), bl_growth(w, tr, SCALE_M4))


# --- critic says "supported" for a wrong claim

def _critic(w, cid, image):
    camp = Campaigner(w.L, w.c, Settings(profile="dev"))
    camp.clients = {"fireworks": FakeClient(constant("SUPPORTED\nThe outline clearly shows the claimed growth."))}
    camp.critique(cid, image)
    return [r for r in w.L.list("review", ref=cid) if r.reviewer == "critic"]


@case("critic overreach", "critic supports a claim the gate refutes", "tool",
      "Clean Movie 4 speed (~6.2 um/s) claimed to be the paper's 40-50 um/s; the critic (fake model) says SUPPORTED.")
def c_critic_wrong_claim(make, tmp):
    w = make()
    tr = m4_track(w, {"1": filament()}, single_body=True)
    vid = velocity(ok(measure(w, tr.data["boxes_id"], scale(w), clock(w))))
    res = ok(w.call("propose_finding", type="value_within", measurement_ids=[vid],
                    params={"lo": 40, "hi": 50, "unit": "um/s"}, statement="The filament grows at 40-50 um/s."))
    crit = _critic(w, res.data["claim_id"], frame_png(w, tmp))
    st = w.L.claim_status(res.data["claim_id"])
    return (out(bool(crit) and st != "supported", f"gate:{st}", f"critic said {crit[-1].verdict if crit else 'n/a'}; "
                f"status {st}", [res.data["claim_id"]] + [c.id for c in crit]),
            baseline.run(baseline.report, "supported"))


@case("critic overreach", "critic supports a claim the gate finds unsupported", "tool",
      "Movie 1 playback-timed speed (no recorded clock) is claimed; the critic says SUPPORTED.")
def c_critic_unsupported_claim(make, tmp):
    w = make(movies=("1",))
    tr = m1_track(w)
    vid = velocity(ok(measure(w, tr.data["boxes_id"], scale(w, "1"), movie="1")))
    res = ok(naive_claim(w, vid, "The dendrite grows at the measured speed."))
    crit = _critic(w, res.data["claim_id"], frame_png(w, tmp, "1", 80))
    st = w.L.claim_status(res.data["claim_id"])
    return (out(bool(crit) and st != "supported", f"gate:{st}", f"critic said {crit[-1].verdict if crit else 'n/a'}; "
                f"status {st}", [res.data["claim_id"]]), baseline.run(baseline.report, "supported"))


@case("critic overreach", "critic review on a claim the gate never saw", "record",
      "A claim is inserted without a gate review and the critic says SUPPORTED: does the claim count as earned?")
def c_critic_before_gate(make, tmp):
    w = make()
    v = ok(w.call("read_cell_voltage", video_id=w.vid["4"]))
    cid = f"claim_{uuid.uuid4().hex[:10]}"
    w.L.add(Claim(id=cid, campaign_id=w.c.id, author="worker", statement="The cell shorts at 40-50 s.",
                  created_at=time.time(), predicate=Predicate(type="value_within", measurement_ids=[v.data["events"][0]],
                                                              params={"lo": 40, "hi": 50, "unit": "s"})))
    crit = _critic(w, cid, frame_png(w, tmp))
    st = w.L.claim_status(cid)
    return (out(bool(crit) and st != "supported", f"status:{st}", f"critic said {crit[-1].verdict if crit else 'n/a'}; "
                f"status {st}", [cid]), baseline.run(baseline.report, "supported"))


# --- merged masks, with SAM masks

def m4_track_masked(w, objects: dict) -> tools.ToolResult:
    """track_objects on Movie 4 with scripted SAM output that carries real encoded masks: id -> f(t) -> (x1, y1, mask),
    the mask box-local (rows across, columns along). Encoded once per captured image."""
    from meta_sam_parser._mask_codec import _encode_raster
    times = instruments.frame_times("reisecker2023/movie4")
    enc, lines = {}, []
    for f, t in enumerate(times):
        parts = []
        for o, fn in objects.items():
            k = (o, round(t, 3))
            if k not in enc:
                x1, y1, m = fn(t)
                h, wd = m.shape
                enc[k] = (f"{o}<|box;x1={x1};y1={y1};x2={x1 + wd - 1};y2={y1 + h - 1};w={W};h={H};c=0.92|>"
                          f"<|mask;x=0;y=0;c=0.92;data={h},{wd},{_encode_raster(bytes(m.ravel()), wd, h)}|>")
            parts.append(enc[k])
        lines.append(f"<{f}f>" + ",".join(parts))
    w.ctx.meta = ScriptedSAM("\n".join(lines) + "\n")
    try:
        return ok(w.call("track_objects", video_id=w.vid["4"], frame=0, points=[[1490, 306]]))
    finally:
        w.ctx.meta = None


def bars(*fils, x_root=1500, y=300):
    """Box-local mask of filaments growing left from x_root: each (rate px/s, l0 px, width px, gap above px)."""
    import numpy as np
    t0 = instruments.frame_times("reisecker2023/movie4")[0]

    def fn(t):
        ls = [int(round(l0 + r * (t - t0) + _jitter(t))) for r, l0, _, _ in fils]
        m = np.zeros((sum(wd + g for _, _, wd, g in fils), max(ls)), np.uint8)
        row = 0
        for (_, _, wd, g), ln in zip(fils, ls):
            row += g
            m[row:row + wd, m.shape[1] - ln:] = 1
            row += wd
        return x_root - m.shape[1] + 1, y, m
    return fn


@case("junk masks", "merged outline, 3 px gap (box narrow enough to pass as one filament)", "tool",
      "SAM merges filament 1 (0.3 px/s, 10 px wide) with a faster neighbour (0.6 px/s) only 3 px away; the box is "
      "23 px across, so only the mask shows two bodies.")
def c_mask_merged_small_gap(make, tmp):
    w = make()
    tr = m4_track_masked(w, {"1": bars((0.3, 20, 10, 0), (0.6, 20, 10, 3))})
    t = growth_path(w, measure(w, tr.data["boxes_id"], scale(w), clock(w)), "Filament 1 grows at the measured speed.")
    t["detail"] = f"SAM tool: {tr.summary.split('. Outlines')[0]} | " + t["detail"] + \
        f" (truth for filament 1: {0.3 * TRUE_V:.3g} um/s)"
    return t, bl_growth(w, tr, SCALE_M4)


@case("junk masks", "single wide filament must not be flagged as merged; claim overstates its speed 2x", "tool",
      "One solid 50 px wide filament (1 px/s, real mask) and a claim of twice its speed: caught only if the outline "
      "is measured (not thrown out as two merged filaments) and the gate refutes the claim.")
def c_mask_wide_single(make, tmp):
    w = make()
    tr = m4_track_masked(w, {"1": bars((1.0, 20, 50, 0))})
    mg = measure(w, tr.data["boxes_id"], scale(w), clock(w))
    vid = velocity(mg)
    if vid is None:
        t = out(False, None, f"wide single filament not measured: {mg.summary[:300]}", [mg.run_id])
    else:
        v = w.L.get("measurement", vid)
        res = w.call("propose_finding", type="value_within", measurement_ids=[vid], statement="It grows twice as fast.",
                     params={"lo": round(1.8 * TRUE_V, 3), "hi": round(2.2 * TRUE_V, 3), "unit": "um/s"})
        st = w.L.claim_status(res.data["claim_id"]) if res.status == "ok" else res.status
        t = out(abs(v.value - TRUE_V) < 0.05 * TRUE_V and st == "refuted", f"gate:{st}",
                f"measured {v.value:.3g} ± {v.uncertainty.value:.2g} um/s (truth {TRUE_V:.3g}); 2x claim -> {st}",
                [vid, mg.run_id])
    return t, bl_growth(w, tr, SCALE_M4)


# ------------------------------------------------------------------ clean cases

def clean(tmp: Path, capture=None) -> list[dict]:
    """Both pipelines on fault-free inputs, with the numbers each reports."""
    make = Maker(tmp, capture)
    rows = []
    try:
        w = make()
        tr = m4_track(w, {"1": filament()}, single_body=True)
        vid = velocity(ok(measure(w, tr.data["boxes_id"], scale(w), clock(w))))
        v = w.L.get("measurement", vid)
        res = ok(w.call("propose_finding", type="value_within", measurement_ids=[vid], statement="speed within 10%",
                        params={"lo": round(0.9 * TRUE_V, 3), "hi": round(1.1 * TRUE_V, 3), "unit": "um/s"}))
        b = bl_growth(w, tr, SCALE_M4)["output"]["1"]
        rows.append({"case": "Movie 4, scripted clean filament (1 px per recorded s) on the real clock + scale",
                     "truth": f"{TRUE_V:.3f} um/s",
                     "translucent": f"{v.value:.3f} ± {v.uncertainty.value:.3f} um/s (ci95); claim 'within 10% of "
                                    f"truth' -> {res.data['verdict']}",
                     "baseline": f"{b['velocity_um_s']:.2f} ± {b['ci95']:.2f} um/s (per playback frame, "
                                 f"{b['velocity_um_s'] / TRUE_V:.1f}x truth)"})

        w = make(movies=("1",))
        tr = m1_track(w)
        mg = ok(measure(w, tr.data["boxes_id"], scale(w, "1"), movie="1"))
        v = w.L.get("measurement", velocity(mg))
        res = ok(naive_claim(w, v.id, "The dendrite grows at the measured speed."))
        b = bl_growth(w, tr, 184.9, movie="1")["output"]["1"]
        n_img = len(w.L.get("measurement", mg.data["ids"]["1"]["time"]).value)
        rows.append({"case": "Movie 1, real recorded SAM track (frame 60 point), no recorded clock",
                     "truth": "unknown (no clock on record; paper gives 2-7 s per image)",
                     "translucent": f"{v.value:.2f} ± {v.uncertainty.value:.2f} um per playback-s over {n_img} "
                                    f"playback frames (repeats not removed without a clock); claim -> "
                                    f"{res.data['verdict']} (no recorded clock)",
                     "baseline": f"{b['velocity_um_s']:.2f} ± {b['ci95']:.2f} um/s reported as real speed "
                                 f"({b['n_frames']} playback frames)"})

        w = make()
        v = ok(w.call("read_cell_voltage", video_id=w.vid["4"]))
        c = w.L.get("measurement", v.data["events"][0])
        res = ok(w.call("propose_finding", type="value_within", measurement_ids=[c.id], statement="short at 80-100 s",
                        params={"lo": 80, "hi": 100, "unit": "s"}))
        series = instruments.voltage("reisecker2023/movie4")
        bs = baseline.short_time(series["t"], series["v"], series["short_threshold_v"])
        rows.append({"case": "Movie 4 voltage log: first short",
                     "truth": "90 ± 3 s (paper's voltage data)",
                     "translucent": f"{c.value:.2f} ± {c.uncertainty.value:.2f} s; claim 80-100 s -> "
                                    f"{res.data['verdict']}",
                     "baseline": f"{bs:.1f} s (first logged sample below {series['short_threshold_v']} V, no uncertainty)"})
    finally:
        for w in make.worlds:
            w.close()
    return rows


# These faults carry mask pixels, so a pair is distinguishable from one wide filament.
def _qc_outline_track(w, gap=2, ratio=1.5, angle=0.0, single_width=None):
    import numpy as np
    from meta_sam_parser._mask_codec import _encode_raster
    lines = []
    for i, f in enumerate((0, 100, 200, 300, 400)):
        length = 80 + 40 * i
        if single_width is not None:
            mask = np.ones((single_width, length), np.uint8)
        elif not angle:
            width = 6
            mask = np.zeros((2 * width + gap, length), np.uint8)
            mask[:width, -round(length / ratio):] = 1
            mask[width + gap:] = 1
            mask[:, -2:] = 1  # a thin join at the electrode still leaves two long bodies
        else:
            mask = np.zeros((220, length), np.uint8)
            for base, extent, slope in ((90, round(length / ratio), -angle), (110, length, angle)):
                for x in range(length - extent, length):
                    y = round(base + slope * (length - 1 - x))
                    mask[y:y + 6, x] = 1
            mask[90:116, -2:] = 1
            ys, xs = np.nonzero(mask)
            mask = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        h, width = mask.shape
        payload = _encode_raster(bytes(mask.ravel()), width, h)
        lines.append(f"<{f}f>1<|box;x1={1501 - width};y1=300;x2=1500;y2={299 + h};w={W};h={H};c=0.95|>"
                     f"<|mask;x=0;y=0;c=0.95;data={h},{width},{payload}|>")
    w.ctx.meta = ScriptedSAM("\n".join(lines) + "\n")
    try:
        return ok(w.call("track_objects", video_id=w.vid["4"], frame=0, points=[[1490, 302]]))
    finally:
        w.ctx.meta = None


@case("junk masks", "two outlines separated by only two pixels, joined at the electrode", "tool",
      "One confident SAM mask joins two narrow filaments at the electrode. The two-pixel gap is much smaller "
      "than the original box-only fault; the neighbour grows 1.5 times as fast.")
def c_mask_merged_connected_small_gap(make, tmp):
    w = make()
    tr = _qc_outline_track(w, gap=2, ratio=1.5)
    return (growth_path(w, measure(w, tr.data["boxes_id"], scale(w), clock(w)),
                        "Filament 1 grows at the measured speed."), bl_growth(w, tr, SCALE_M4))


@case("junk masks", "two tilted outlines with different fronts and a threefold speed ratio", "tool",
      "One confident SAM mask covers two filaments diverging from neighbouring roots, joined at the electrode. "
      "The leading outline grows three times as fast as the target filament.")
def c_mask_merged_diverging_fronts(make, tmp):
    w = make()
    tr = _qc_outline_track(w, ratio=3.0, angle=0.15)
    return (growth_path(w, measure(w, tr.data["boxes_id"], scale(w), clock(w)),
                        "Filament 1 grows at the measured speed."), bl_growth(w, tr, SCALE_M4))


# ------------------------------------------------------------------ runner

def run_case(c: dict, tmp_root: Path, capture=None) -> dict:
    d = Path(tempfile.mkdtemp(prefix=c["id"][:20] + "_", dir=tmp_root))
    make = Maker(d, capture)
    t0 = time.time()
    row = {k: c[k] for k in ("id", "fault_class", "variant", "via", "description")}
    try:
        t, b = c["fn"](make, d)
        row.update(translucent=t, baseline=b, error=None)
    except Exception as e:  # noqa: BLE001 - a broken case is reported, never counted as caught
        row.update(translucent=None, baseline=None, error=f"{type(e).__name__}: {e}",
                   traceback=traceback.format_exc()[-2000:])
    finally:
        for w in make.worlds:
            if capture is not None:
                capture(w, None)  # raw invalidations made after the proposal
            w.close()
        shutil.rmtree(d, ignore_errors=True)
    row["seconds"] = round(time.time() - t0, 2)
    return row


def run_all(tmp_root: Path | None = None, only: list[str] | None = None, log=print) -> list[dict]:
    tmp_root = Path(tmp_root or tempfile.mkdtemp(prefix="translucent-evals-"))
    tmp_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for c in CASES:
        if only and c["id"] not in only:
            continue
        r = run_case(c, tmp_root)
        t = r["translucent"] or {}
        log(f"{r['id']:32s} {'ERROR ' + r['error'] if r['error'] else ('caught ' + str(t['how']) if t['caught'] else 'NOT caught')}"
            f"  [{r['seconds']}s]")
        rows.append(r)
    return rows
