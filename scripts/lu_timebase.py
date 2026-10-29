"""Digitize the recorded Lu Movie 1 inset, without assuming a playback cadence.

The four-pixel bound covers raster reading, line
width and the manually read tick positions. Clipped endpoints are bounded by
the end of cycle 1 in the source log. No image is kept in memory after its row.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/groundtruth/lu2023"
X0, X25, PIXEL_BOUND = 879.5, 1265.0, 4.0


def main():
    path = ROOT / "data/raw/lu2023/movie1.mp4"
    with path.open("rb") as fh:
        sha = hashlib.file_digest(fh, "sha256").hexdigest()
    if sha != "708efdce5f66643a0a2edcdaaec9cd80d44a3aaf3dbe5df3918cb3e79e50c7c4":
        raise ValueError("not the recorded Lu movie")
    end = json.loads((OUT / "lu2023_fig2_segments_derived.json").read_text())["cycle1_discharge"]["t_end_s"]
    seconds_per_px = 25 * 3600 / (X25 - X0)
    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_N_THREADS, 1)
    rows = []
    while True:
        ok, image = cap.read()
        if not ok:
            break
        roi = image[584:898, 879:1280].astype(np.int16)
        b, g, r = (roi[:, :, i] for i in range(3))
        masks = [(r > 110) & (r - g > 65) & (r - b > 65),
                 (g > 110) & (g - r > 65) & (g - b > 65),
                 (b > 110) & (b - r > 65) & (b - g > 65)]
        pixels = [np.flatnonzero(m.sum(axis=0) >= 2) for m in masks]
        phases = [i for i, p in enumerate(pixels) if len(p)]
        if phases:
            phase = max(phases)
            x = int(pixels[phase][-1]) + 879
            lo = max(0.0, (x - PIXEL_BOUND - X0) * seconds_per_px)
            hi = min(end, (x + PIXEL_BOUND - X0) * seconds_per_px)
            if x >= 1276:
                hi = end
            name = ("charge", "relax", "discharge")[phase]
        else:
            x, lo, hi, name = None, 0.0, PIXEL_BOUND * seconds_per_px, "no visible trace"
        rows.append({"frame": len(rows), "endpoint_x": x, "phase": name, "lower_s": lo, "upper_s": hi})
    cap.release()
    if len(rows) != 1261:
        raise ValueError(f"expected 1261 frames, found {len(rows)}")
    # The plot is drawn progressively: chronology tightens overlapping raster bounds.
    lower = np.maximum.accumulate([r["lower_s"] for r in rows])
    upper = np.minimum.accumulate([r["upper_s"] for r in rows][::-1])[::-1]
    if np.any(lower > upper):
        raise ValueError("inset bounds cannot be reconciled with a progressive clock")
    for row, lo, hi in zip(rows, lower, upper):
        row.update(lower_s=round(float(lo), 3), upper_s=round(float(hi), 3),
                   t_s=round(float((lo + hi) / 2), 3))
    with (OUT / "lu2023_movie1_timebase.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["frame", "endpoint_x", "phase", "lower_s", "upper_s", "t_s"])
        writer.writeheader()
        writer.writerows(rows)
    meta = {"video_sha256": sha, "frames": len(rows), "x_0h": X0, "x_25h": X25,
            "pixel_bound": PIXEL_BOUND, "uncertainty_s": round(float(max((upper - lower) / 2)), 3),
            "source": "Lu et al. 2023, Movie 1 recorded voltage inset; Fig. 2 Source Data cycle-1 bounds",
            "method": "progressive coloured trace endpoint; 0 and 25 h ticks at x=879.5 and 1265; "
                      "+/-4 pixels; blank start bounded by first resolvable trace; clipped right edge bounded by "
                      "cycle-1 discharge end; chronological interval intersection; midpoint per frame",
            "stated_acquisition_interval_s": 60,
            "limitations": "Coarse inset digitization, not a 60 s/frame clock. No cycle-2 images. "
                           "Voltage event times use the full 2 s source log, independently of this frame map."}
    (OUT / "lu2023_movie1_timebase.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2))
    for index in (0, 25, 252, 441, 630, 900, 1260):
        print(rows[index])


if __name__ == "__main__":
    main()
