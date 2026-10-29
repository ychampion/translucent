"""Read the experiment time base of Reisecker 2023 Supplementary Movie 4 from its on-screen voltage plot.

The right half of every Movie 4 frame is a voltage-vs-time plot (x axis "Time (h)", 41.32-41.46 h) with a
black marker at the current time. This script finds the axes' tick marks (pixel calibration), then the
marker centroid in every frame, and writes frame -> experiment time (h, s) and voltage (V).

Tick values were read by eye from the frame (x labels 41.32 ... 41.46 every 0.02 h at the long ticks;
y labels 0, -5, -10, -15 V at the long ticks) and are asserted by count.

Usage:
  uv run --with opencv-python-headless --with numpy python -I movie4_timebase.py <movie4.mp4> <out.csv>
"""
import csv
import sys

import cv2
import numpy as np

X_TICK_VALUES_H = [41.32, 41.34, 41.36, 41.38, 41.40, 41.42, 41.44, 41.46]
Y_TICK_VALUES_V = [0, -5, -10, -15]
PLOT = (1030, 150, 1830, 835)  # x0, y0, x1, y1 of plot interior (px)


def groups(idx):
    out, cur = [], [idx[0]]
    for c in idx[1:]:
        if c - cur[-1] <= 1:
            cur.append(c)
        else:
            out.append(cur); cur = [c]
    out.append(cur)
    return out


def calibrate(gray):
    # x axis: horizontal dark line near y=836; long ticks hang ~10 px below it.
    dark = gray < 140
    axis_y = 150 + int(np.argmax(dark[150:900, 1000:1850].sum(1)))
    below = dark[axis_y + 2:axis_y + 13, 1030:1830]
    xg = [g for g in groups(list(np.where(below.sum(0) >= 1)[0] + 1030))]
    xmaj = [float(np.mean(g)) for g in xg if below[:, int(np.mean(g)) - 1030].sum() >= 8]
    assert len(xmaj) == len(X_TICK_VALUES_H), xmaj
    # y axis: vertical dark line near x=1027; long ticks extend ~10 px to its left.
    axis_x = 1000 + int(np.argmax(dark[150:900, 1000:1100].sum(0)))
    left = dark[150:840, axis_x - 12:axis_x - 1]
    yg = groups(list(np.where(left.sum(1) >= 1)[0] + 150))
    ymaj = [float(np.mean(g)) for g in yg if left[int(np.mean(g)) - 150, :].sum() >= 8]
    assert len(ymaj) == len(Y_TICK_VALUES_V), ymaj
    bx, ax = np.polyfit(xmaj, X_TICK_VALUES_H, 1)
    by, ay = np.polyfit(ymaj, Y_TICK_VALUES_V, 1)
    rx = np.max(np.abs(np.polyval([bx, ax], xmaj) - X_TICK_VALUES_H))
    ry = np.max(np.abs(np.polyval([by, ay], ymaj) - Y_TICK_VALUES_V))
    return (bx, ax, rx, xmaj), (by, ay, ry, ymaj)


def marker(gray):
    x0, y0, x1, y1 = PLOT
    roi = (gray[y0:y1, x0:x1] < 70).astype(np.uint8)
    roi = cv2.morphologyEx(roi, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(roi)
    if n < 2:
        return None
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return cent[k][0] + x0, cent[k][1] + y0, int(stats[k, cv2.CC_STAT_AREA])


def main(movie, out):
    cap = cv2.VideoCapture(movie)
    fps = cap.get(cv2.CAP_PROP_FPS)
    rows, cal = [], None
    i = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        if cal is None:
            cal = calibrate(g)
            (bx, ax, rx, xm), (by, ay, ry, ym) = cal
            print(f"x: {1/bx:.1f} px/h ({bx*3600:.3f} s/px), tick residual {rx*3600:.2f} s; "
                  f"y: {1/abs(by):.2f} px/V, tick residual {ry:.3f} V")
        m = marker(g)
        if m:
            (bx, ax, _, _), (by, ay, _, _) = cal
            t = bx * m[0] + ax
            rows.append({"frame": i, "playback_s": round(i / fps, 3), "marker_px_x": round(m[0], 2),
                         "marker_px_y": round(m[1], 2), "t_h": round(t, 6), "t_s_rel_41p32h": round((t - 41.32) * 3600, 1),
                         "voltage_V": round(by * m[1] + ay, 3), "marker_area_px": m[2]})
        i += 1
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    ts = [r["t_h"] for r in rows]
    uniq = sorted(set(round(t, 5) for t in ts))
    d = np.diff(uniq) * 3600
    print(f"{i} frames, marker found in {len(rows)}; t from {ts[0]:.4f} h to {ts[-1]:.4f} h "
          f"({(ts[-1]-ts[0])*3600:.0f} s); {len(uniq)} distinct marker times; "
          f"step median {np.median(d):.1f} s (min {d.min():.1f}, max {d.max():.1f})")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
