"""Digitise Reisecker et al., Nat. Commun. 14, 2432 (2023), Fig. 3e and 3f from the article PDF.

Panels 3e/3f are embedded as vector graphics in the publisher PDF (page 4), so markers, axis ticks and
fit lines are read as exact path coordinates instead of from pixels. Tick labels are outlined glyphs
(not text), so the tick -> value mapping below was read by eye from the rendered figure and is
asserted against the tick count.

Usage:
  uv run --with pymupdf python -I digitise_fig3.py <article.pdf> <out_dir>
Outputs: reisecker2023_fig3ef_points.csv, reisecker2023_fig3ef_fitlines.csv, fig3ef_overlay.png
"""
import csv
import math
import sys
from pathlib import Path

import pymupdf

PAGE = 3  # 0-based page index of Fig. 3 in the article PDF

# Panel configuration. Coordinates are PDF points on the page.
PANELS = {
    "3e": {
        "clip": pymupdf.Rect(200, 55, 285, 140),
        "sample": "SC",
        "x_ticks": [0, 1, 2, 3, 4, 5],          # major x ticks, left to right (labels 0..5)
        "y_ticks": [0, 10, 20, 30, 40, 50],     # major y ticks, bottom to top (labels 0..50)
        "legend": pymupdf.Rect(248, 118, 285, 135),  # legend markers "SC DC" / "SC PP"
        "series": {"square": "SC DC", "circle": "SC PP"},
        "lines": {"solid": "SC DC", "dashed": "SC PP"},
        "stated_slope": {"SC DC": 8.4, "SC PP": 10.5},
    },
    "3f": {
        "clip": pymupdf.Rect(200, 158, 285, 242),
        "sample": "HP",
        "x_ticks": [0, 1, 2, 3],                 # labelled major ticks 0..3 (minor ticks at .5 ignored)
        "y_ticks": [0, 5, 10, 20, 30],           # labels 0, 5, 10, 20, 30 (linear axis, 5 is an extra tick)
        "legend": pymupdf.Rect(248, 219, 285, 236),
        "series": {"square": "HP DC", "circle": "HP PP"},
        "lines": {"solid": "HP DC", "dashed": "HP PP"},
        "stated_slope": {"HP DC": 13.7, "HP PP": 1.1},
    },
}
AXIS_W = 0.481  # stroke width of axes, ticks, markers and fit lines; arrows use 0.241


def linfit(px, val):
    n = len(px)
    mx, my = sum(px) / n, sum(val) / n
    sxx = sum((a - mx) ** 2 for a in px)
    b = sum((a - mx) * (c - my) for a, c in zip(px, val)) / sxx
    a = my - b * mx
    res = [c - (a + b * p) for p, c in zip(px, val)]
    return a, b, max(abs(r) for r in res)


def ols(xs, ys, through_origin=False):
    if through_origin:
        b = sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs)
        return 0.0, b
    a, b, _ = linfit(xs, ys)
    return a, b


def segs(g):
    return [it for it in g["items"]]


def main(pdf_path, out_dir):
    out_dir = Path(out_dir)
    doc = pymupdf.open(pdf_path)
    page = doc[PAGE]
    drawings = page.get_drawings()
    points, fitlines, overlay = [], [], []

    for panel, cfg in PANELS.items():
        clip = cfg["clip"]
        inside = [g for g in drawings if clip.contains(g["rect"]) and g["type"] == "s"]
        xt, yt, markers, lines = [], [], [], []
        for g in inside:
            w = g.get("width") or 0
            items = g["items"]
            if len(items) == 1 and items[0][0] == "l" and abs(w - AXIS_W) < 1e-3:
                p1, p2 = items[0][1], items[0][2]
                dx, dy = abs(p2.x - p1.x), abs(p2.y - p1.y)
                L = math.hypot(dx, dy)
                if dx < 1e-3 and 2.0 < L < 3.0:     # major x tick (vertical, ~2.59 pt)
                    xt.append(p1.x)
                elif dy < 1e-3 and 2.0 < L < 3.0:   # major y tick (horizontal, ~2.59 pt)
                    yt.append(p1.y)
                elif dx > 5 and dy > 5:             # oblique line = fit line
                    lines.append(g)
            elif len(items) == 1 and items[0][0] == "re":
                r = items[0][1]
                markers.append(("square", (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2, r.width))
            elif len(items) == 4 and all(it[0] == "c" for it in items):
                r = g["rect"]
                markers.append(("circle", (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2, r.width - (w or 0)))
        xt, yt = sorted(set(round(v, 3) for v in xt)), sorted(set(round(v, 3) for v in yt), reverse=True)
        assert len(xt) == len(cfg["x_ticks"]), (panel, xt)
        assert len(yt) == len(cfg["y_ticks"]), (panel, yt)
        ax, bx, rx = linfit(xt, cfg["x_ticks"])
        ay, by, ry = linfit(yt, cfg["y_ticks"])
        X = lambda px: ax + bx * px
        Y = lambda py: ay + by * py
        print(f"[{panel}] x-axis: {1/bx:.3f} pt/unit, max tick residual {rx:.4f} units; "
              f"y-axis: {-1/by:.3f} pt/unit, max tick residual {ry:.4f} units")

        data = [m for m in markers if not cfg["legend"].contains(pymupdf.Point(m[1], m[2]))]
        for shape, cx, cy, size in sorted(data, key=lambda m: (m[0], m[1])):
            series = cfg["series"][shape]
            points.append({
                "figure_panel": panel, "sample_type": cfg["sample"], "series": series,
                "marker": shape, "ccd_eff_mA_cm2": round(X(cx), 3), "growth_rate_um_s": round(Y(cy), 2),
                "x_unc_mA_cm2": round(rx + 0.01 * abs(bx) + 0.005, 3),
                "y_unc_um_s": round(ry + 0.01 * abs(by) + 0.05, 2),
                "marker_halfwidth_x_mA_cm2": round(size / 2 * abs(bx), 3),
                "marker_halfwidth_y_um_s": round(size / 2 * abs(by), 2),
                "pdf_x_pt": round(cx, 3), "pdf_y_pt": round(cy, 3),
                "provenance": "digitised (vector path centre from publisher PDF)",
            })
            overlay.append((cx, cy))

        for g in lines:
            p1, p2 = g["items"][0][1], g["items"][0][2]
            kind = "dashed" if g.get("dashes") and g["dashes"].strip() not in ("[] 0", "") else "solid"
            series = cfg["lines"][kind]
            x1, y1, x2, y2 = X(p1.x), Y(p1.y), X(p2.x), Y(p2.y)
            slope = (y2 - y1) / (x2 - x1)
            fitlines.append({
                "figure_panel": panel, "series": series, "line_style": kind,
                "x1": round(x1, 3), "y1": round(y1, 2), "x2": round(x2, 3), "y2": round(y2, 2),
                "slope_um_s_per_mA_cm2": round(slope, 2),
                "intercept_um_s": round(y1 - slope * x1, 2),
                "stated_slope": cfg["stated_slope"][series],
            })

        for series in sorted(set(cfg["series"].values())):
            pts = [(p["ccd_eff_mA_cm2"], p["growth_rate_um_s"]) for p in points
                   if p["figure_panel"] == panel and p["series"] == series]
            a, b = ols([p[0] for p in pts], [p[1] for p in pts])
            _, b0 = ols([p[0] for p in pts], [p[1] for p in pts], through_origin=True)
            print(f"  {series}: n={len(pts)} OLS slope {b:.2f} (intercept {a:.2f}); "
                  f"through-origin slope {b0:.2f}; stated {cfg['stated_slope'][series]}")

    with open(out_dir / "reisecker2023_fig3ef_points.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(points[0].keys()))
        w.writeheader(); w.writerows(points)
    with open(out_dir / "reisecker2023_fig3ef_fitlines.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(fitlines[0].keys()))
        w.writeheader(); w.writerows(fitlines)
    for fl in fitlines:
        print("  fit line", fl)

    # Visual check: render panels e/f and mark each digitised centre with a red cross.
    for cx, cy in overlay:
        page.draw_line((cx - 2.5, cy), (cx + 2.5, cy), color=(1, 0, 0), width=0.3)
        page.draw_line((cx, cy - 2.5), (cx, cy + 2.5), color=(1, 0, 0), width=0.3)
    page.get_pixmap(dpi=600, clip=pymupdf.Rect(185, 48, 290, 262)).save(out_dir / "reisecker2023_fig3ef_overlay.png")
    print(f"wrote {len(points)} points, {len(fitlines)} fit lines")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
