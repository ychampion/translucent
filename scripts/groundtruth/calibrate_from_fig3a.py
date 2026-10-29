"""Transfer the 1 mm scale bars of Reisecker et al. 2023 Fig. 3a onto the supplementary movies.

Fig. 3a (straight / cone / diffuse / multiple initiation) shows optical stills with a vector 1 mm scale
bar each. If a movie shows the same sample, a similarity transform (rotation + uniform scale + shift)
registered with SIFT + RANSAC maps movie pixels onto the figure raster, and the scale bar gives
movie pixels per mm. A match is accepted only with enough RANSAC inliers; otherwise it is reported
as "no match" (the movie may show a different sample).

Usage:
  uv run --with pymupdf --with opencv-python-headless --with numpy python -I calibrate_from_fig3a.py \
      <article.pdf> <movie_dir> <out_dir>
"""
import csv
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pymupdf

PAGE = 3
IMG_XREF = 280                         # raster holding Fig. 3a-d photos
IMG_BBOX = (48.2, 49.7, 184.8, 259.5)  # where that raster is placed on the page (pt)
# Fig. 3a sub-panels (pt, page coords) and their vector 1 mm scale bars (x0, x1 in pt), from get_drawings().
SUBPANELS = {
    "straight": {"rect": (48.2, 49.7, 100.4, 102.5), "bar_pt": (85.80, 98.77)},
    "cone": {"rect": (48.2, 102.5, 100.4, 155.0), "bar_pt": (83.79, 98.76)},
    "diffuse": {"rect": (48.2, 155.0, 100.4, 207.0), "bar_pt": (82.91, 98.77)},
    "multiple_initiation": {"rect": (48.2, 207.0, 100.4, 259.5), "bar_pt": (84.70, 98.77)},
    "3b_crack": {"rect": (100.9, 49.7, 184.8, 102.5), "bar_pt": (102.83, 122.99)},
}
MOVIE_CROP = {4: (0, 0, 905, 1080)}  # Movie 4: left part only (right part is the voltage plot)
MIN_INLIERS = 25


def load_strip(pdf):
    doc = pymupdf.open(pdf)
    info = doc.extract_image(IMG_XREF)
    img = cv2.imdecode(np.frombuffer(info["image"], np.uint8), cv2.IMREAD_GRAYSCALE)
    h, w = img.shape
    sx = w / (IMG_BBOX[2] - IMG_BBOX[0])
    sy = h / (IMG_BBOX[3] - IMG_BBOX[1])
    return img, sx, sy


def movie_frame(path, frac):
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(n * frac) - 1))
    ok, fr = cap.read()
    cap.release()
    return cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY) if ok else None


def main(pdf, movie_dir, out_dir):
    strip, sx, sy = load_strip(pdf)
    print(f"strip {strip.shape[1]}x{strip.shape[0]} px, {sx:.4f} px/pt (x), {sy:.4f} px/pt (y)")
    sift = cv2.SIFT_create()
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    panels = {}
    for name, cfg in SUBPANELS.items():
        x0, y0, x1, y1 = cfg["rect"]
        crop = strip[int((y0 - IMG_BBOX[1]) * sy):int((y1 - IMG_BBOX[1]) * sy),
                     int((x0 - IMG_BBOX[0]) * sx):int((x1 - IMG_BBOX[0]) * sx)]
        crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)  # upsample 2x for SIFT
        kp, des = sift.detectAndCompute(clahe.apply(crop), None)
        bar_px = (cfg["bar_pt"][1] - cfg["bar_pt"][0]) * sx * 2  # in upsampled crop pixels
        panels[name] = (kp, des, bar_px)

    rows = []
    bf = cv2.BFMatcher(cv2.NORM_L2)
    for m in range(1, 6):
        path = Path(movie_dir) / f"movie{m}.mp4"
        if not path.exists():
            continue
        for frac in (0.05, 0.5, 1.0):
            fr = movie_frame(path, frac)
            if fr is None:
                continue
            if m in MOVIE_CROP:
                a, b, c, d = MOVIE_CROP[m]
                fr = fr[b:d, a:c]
            scale_down = 0.5  # movie frames are 1920+ px wide; halve for SIFT speed
            frs = cv2.resize(fr, None, fx=scale_down, fy=scale_down, interpolation=cv2.INTER_AREA)
            kp_m, des_m = sift.detectAndCompute(clahe.apply(frs), None)
            for name, (kp_p, des_p, bar_px) in panels.items():
                knn = bf.knnMatch(des_m, des_p, k=2)
                good = [a for a, b in (p for p in knn if len(p) == 2) if a.distance < 0.75 * b.distance]
                if len(good) < 8:
                    continue
                src = np.float32([kp_m[g.queryIdx].pt for g in good])
                dst = np.float32([kp_p[g.trainIdx].pt for g in good])
                M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
                if M is None:
                    continue
                n_in = int(inl.sum())
                s = float(np.hypot(M[0, 0], M[1, 0]))  # panel px per (downscaled) movie px
                rot = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
                px_per_mm_movie = bar_px / s * (1 / scale_down) if s > 0 else float("nan")
                rows.append({"movie": m, "frame_frac": frac, "panel": name, "good_matches": len(good),
                             "inliers": n_in, "rotation_deg": round(rot, 2), "scale_panel_per_movie": round(s, 5),
                             "movie_px_per_mm": round(px_per_mm_movie, 1),
                             "accepted": n_in >= MIN_INLIERS})
    out = Path(out_dir)
    with open(out / "reisecker2023_fig3a_registration.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    for r in rows:
        if r["accepted"]:
            print(r)
    # Summary per movie over accepted matches
    summary = {}
    for m in range(1, 6):
        acc = [r for r in rows if r["movie"] == m and r["accepted"]]
        if acc:
            best_panel = max(set(r["panel"] for r in acc), key=lambda p: sum(r["inliers"] for r in acc if r["panel"] == p))
            vals = [r["movie_px_per_mm"] for r in acc if r["panel"] == best_panel]
            summary[m] = {"panel": best_panel, "movie_px_per_mm_values": vals,
                          "median": float(np.median(vals)), "n": len(vals)}
        else:
            summary[m] = {"panel": None}
    print(json.dumps(summary, indent=1))
    (out / "reisecker2023_fig3a_registration_summary.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:4])
