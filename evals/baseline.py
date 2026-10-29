"""Plain pipeline: what a typical analysis script does with the same inputs. No agent, no ledger checks, no gate.

Takes SAM's per-frame boxes, converts px -> um with whatever scale it is handed, times every playback frame at the
file's fps (no repeat removal, no recorded clock), fits a line and reports the number. It "catches" a fault only if
it raises or its output is not a finite number (`run`).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np

GROWS = {"right": 0, "left": 0, "bottom": 1, "top": 1}


def load_boxes(path: Path | str) -> dict[int, dict[str, list[float]]]:
    """SAM boxes table (frame -> object -> [x1, y1, x2, y2, conf]) as written by the SAM tools."""
    return {int(k): v for k, v in json.loads(Path(path).read_text()).items()}


def video_fps(path: Path | str) -> float:
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return fps


def growth_velocity(boxes: dict, px_per_mm, fps: float, grows_from="right", obj: str | None = None) -> dict:
    """Per-object growth speed in um/s from a linear fit of box extent vs playback time (frame / fps)."""
    axis = GROWS[grows_from]
    objs = [obj] if obj else sorted({o for f in boxes.values() for o in f}, key=int)
    out = {}
    for o in objs:
        pts = [(f / fps, (b[o][2] - b[o][0]) if axis == 0 else (b[o][3] - b[o][1]))
               for f, b in sorted(boxes.items()) if o in b]
        t = np.array([p[0] for p in pts])
        length_um = np.array([p[1] for p in pts]) / px_per_mm * 1000
        (slope, icpt), cov = np.polyfit(t, length_um, 1, cov=True)
        out[o] = {"velocity_um_s": float(slope), "ci95": float(2 * math.sqrt(cov[0, 0])), "n_frames": len(t)}
    if not out:
        raise ValueError("no objects")
    return out


def short_time(t, v, threshold=0.05) -> float:
    """First logged time at which |V| drops below the threshold."""
    t, v = np.asarray(t, float), np.asarray(v, float)
    idx = np.flatnonzero(np.abs(v) < threshold)
    if not len(idx):
        raise ValueError("no short in log")
    return float(t[idx[0]])


def report(value):
    """A number handed over by an upstream step (a record, a model, a critic): reported as is."""
    return value


def _finite(x) -> bool:
    if isinstance(x, dict):
        return all(_finite(v) for v in x.values())
    if isinstance(x, (list, tuple)):
        return all(_finite(v) for v in x)
    if isinstance(x, (int, float)):
        return math.isfinite(x)
    return x is not None


def run(fn, *args, **kw) -> dict:
    """Run a baseline step: caught = it raised, or produced a non-finite / empty number."""
    try:
        out = fn(*args, **kw)
    except Exception as e:  # noqa: BLE001 - the baseline's only defence is crashing
        return {"caught": True, "how": f"error: {type(e).__name__}: {e}"[:200], "output": None}
    if not _finite(out):
        return {"caught": True, "how": "flagged: non-finite output", "output": out}
    return {"caught": False, "how": None, "output": out}
