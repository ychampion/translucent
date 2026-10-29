"""Calibrate Lu et al. 2023 Supplementary Movie 1 pixels against the 50 um scale bar of Fig. 2.

Registers movie frames (SIFT + RANSAC similarity) to the Fig. 2 optical strips (timesteps 1, 2, 3, 4, 6),
then converts the Fig. 2 raster scale bar (50 um) into movie px/um.

Usage:
  uv run --with opencv-python-headless --with numpy python -I lu2023_calibrate_from_fig2.py <Fig2_HTML.png> <movie1.mp4> <out.csv>
"""
import csv
import sys

import cv2
import numpy as np

# Fig. 2 (2000-px-class publisher PNG, 1930 x 1164): panel crops (x0, y0, x1, y1) and scale bar search box.
PANELS = {"ts1_charge_0h": (10, 565, 550, 755), "ts2_charge_4h": (10, 770, 550, 955),
          "ts3_charge_8h": (10, 965, 550, 1160), "ts4_charge_14h": (780, 55, 1340, 250),
          "ts6_relax_3h": (780, 315, 1340, 505)}
BAR_BOX = (440, 510, 600, 540)
BAR_UM = 50.0


def main(fig, movie, out):
    g = cv2.cvtColor(cv2.imread(fig), cv2.COLOR_BGR2GRAY)
    x0, y0, x1, y1 = BAR_BOX
    sub = g[y0:y1, x0:x1] < 60
    r = int(np.argmax(sub.sum(1)))
    cols = np.where(sub[r])[0]
    bar = cols.max() - cols.min() + 1
    print(f"scale bar: {bar} px = {BAR_UM} um in Fig. 2 PNG")
    sift, cl = cv2.SIFT_create(), cv2.createCLAHE(2.0, (8, 8))
    pk = {}
    for k, (a, b, c, d) in PANELS.items():
        crop = cv2.resize(g[b:d, a:c], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        pk[k] = sift.detectAndCompute(cl.apply(crop), None)
    cap = cv2.VideoCapture(movie)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    rows = []
    for frac in (0.02, 0.2, 0.35, 0.5):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * frac))
        ok, fr = cap.read()
        kp, des = sift.detectAndCompute(cl.apply(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)), None)
        for k, (kp2, des2) in pk.items():
            good = [a for a, b in (m for m in cv2.BFMatcher().knnMatch(des, des2, k=2) if len(m) == 2)
                    if a.distance < 0.75 * b.distance]
            if len(good) < 6:
                continue
            M, inl = cv2.estimateAffinePartial2D(np.float32([kp[m.queryIdx].pt for m in good]),
                                                 np.float32([kp2[m.trainIdx].pt for m in good]),
                                                 method=cv2.RANSAC, ransacReprojThreshold=3.0)
            s = float(np.hypot(M[0, 0], M[1, 0]))
            px_um = (bar * 2 / BAR_UM) / s
            rows.append({"movie_frame": int(n * frac), "fig2_panel": k, "inliers": int(inl.sum()),
                         "rotation_deg": round(float(np.degrees(np.arctan2(M[1, 0], M[0, 0]))), 2),
                         "movie_px_per_um": round(px_um, 3), "frame_width_um": round(fr.shape[1] / px_um, 1)})
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    v = [r["movie_px_per_um"] for r in rows if r["inliers"] >= 100]
    print(f"{len(v)} registrations with >=100 inliers: px/um median {np.median(v):.3f}, range {min(v):.3f}-{max(v):.3f}; "
          f"frame width {1280/np.median(v):.0f} um")


if __name__ == "__main__":
    main(*sys.argv[1:4])
