"""Figures for paper/translucent.tex.

fig_loop.pdf  the observe -> hypothesise -> forecast -> commit -> reveal -> update loop (no data)
fig_nist.pdf  (a) heater-off delay after vent and recorded outcome for the 51 interrupted NIST trials;
              (b) surface temperature after heater-off for the interrupted 21700 trials that cooled by at least
                  2 C after heater-off and then ignited, against the rescued 21700 trials

Inputs (read as data only):
  --inventory  the 77-row trial inventory CSV from the data validation (nist_heat_interrupt_inventory.csv)
  --sheet      the dataset's thermocouple sheet exported to CSV (00_Sheet1.csv, from
               "Li-ion Battery Safe Intervention Time Temperature Data.xlsx", DOI 10.18434/mds2-4027)

Run:  uv run --with matplotlib --with numpy python -I paper/make_figures.py \
          --inventory <inventory.csv> --sheet <00_Sheet1.csv> --out paper/figures
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

RESCUED = "#0072B2"   # blue
IGNITED = "#D55E00"   # vermillion
INK, MUTED, GRID = "#222222", "#666666", "#dddddd"

plt.rcParams.update({"font.family": "serif", "font.size": 8, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.linewidth": 0.6,
                     "pdf.fonttype": 42, "savefig.bbox": "tight"})


def load_inventory(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_sheet(path: Path) -> dict[str, list[tuple[float, float]]]:
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    head, out = rows[0], {}
    for j, name in enumerate(head[1:], 1):
        series = []
        for r in rows[2:]:
            try:
                series.append((float(r[0]), float(r[j])))
            except (ValueError, IndexError):
                continue
        out[name] = series
    return out


def num(x: str) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def outcome(row: dict) -> str:
    c = row["outcome_class"]
    return "rescued" if c.startswith("no ignition") else "delayed" if c.startswith("delayed") else "continued"


def fig_nist(inv: list[dict], sheet: dict, out: Path):
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(7.0, 2.9), gridspec_kw={"width_ratios": [1.05, 1]})

    # (a) delay after vent per condition
    conds = [(f, s) for f in ("18650", "21700") for s in (100, 75, 50, 25)]
    style = {"rescued": dict(marker="o", facecolors="white", edgecolors=RESCUED, label="no ignition (rescued)"),
             "delayed": dict(marker="^", facecolors=IGNITED, edgecolors=IGNITED, label="delayed ignition"),
             "continued": dict(marker="s", facecolors=IGNITED, edgecolors=IGNITED, label="ignition, rise continued")}
    for kind, st in style.items():
        xs, ys = [], []
        for row in inv:
            if row["series"] == "continuous" or outcome(row) != kind:
                continue
            d = num(row["interruption_delay_after_vent_s"])
            y = conds.index((row["cell_format"], int(float(row["soc_pct"]))))
            if d is None:
                continue
            same = sum(1 for (xx, yy) in zip(xs, ys) if abs(xx - d) < 1 and yy == y)
            xs.append(d)
            ys.append(y + 0.16 * same - 0.08 * (kind == "rescued"))
        ax.scatter(xs, ys, s=22, linewidths=1.0, zorder=3, **st)
    ax.set_yticks(range(len(conds)), [f"{f} {s}%" for f, s in conds])
    ax.invert_yaxis()
    ax.set_xlabel("heater-off delay after vent (s)")
    ax.set_xlim(0, 305)
    ax.grid(axis="x", color=GRID, linewidth=0.5)
    ax.axhline(3.5, color=MUTED, linewidth=0.5)
    ax.set_title("(a) recorded action and outcome, 51 interrupted trials", fontsize=8, loc="left", color=INK)
    ax.legend(loc="lower right", fontsize=6.5, frameon=False, handletextpad=0.3)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)

    # (b) 21700 surface temperature after heater-off: rescued vs cooled-then-ignited
    shown = {"rescued": 0, "ignited": 0}
    for row in inv:
        if row["series"] == "continuous" or row["cell_format"] != "21700" or row["trial_id"] not in sheet:
            continue
        t_off = num(row["heater_off_s"])
        if t_off is None:
            continue
        rescued = outcome(row) == "rescued"
        end = num(row["ignition_time_s"])
        if rescued:
            m = re.search(r"re-heat from t=(\d+)", row["render_safe_reheat_in_trace"])
            end = float(m.group(1)) if m else None
        series = sheet[row["trial_id"]]
        if not rescued:  # keep the ignited trials that cooled by >= 2 C after the post-off peak (first 40 s)
            after = [(t, v) for t, v in series if t_off <= t < end]
            early = [(t, v) for t, v in after if t <= t_off + 40]
            if not early:
                continue
            t_pk, v_pk = max(early, key=lambda tv: tv[1])
            if v_pk - min(v for t, v in after if t >= t_pk) < 2.0:
                continue
        pts = [(t - t_off, v) for t, v in series if t >= t_off - 30 and (end is None or t < end)]
        if not pts:
            continue
        key = "rescued" if rescued else "ignited"
        bx.plot(*zip(*pts), color=RESCUED if rescued else IGNITED, linewidth=0.9, alpha=0.9,
                label=("rescued (shown to render-safe re-heat)" if rescued else "cooled, then ignited (x)")
                if not shown[key] else None, zorder=2 if rescued else 3)
        shown[key] += 1
        if not rescued:
            bx.plot(end - t_off, pts[-1][1], marker="x", color=IGNITED, markersize=4, zorder=4)
    bx.axvline(0, color=MUTED, linewidth=0.6, linestyle="--")
    bx.text(3, 151, "heater off", fontsize=6.5, color=MUTED)
    bx.set_xlim(-30, 420)
    bx.set_ylim(150, 190)
    bx.set_xlabel("time since reconstructed heater-off (s)")
    bx.set_ylabel("surface temperature ($^\\circ$C)")
    bx.grid(color=GRID, linewidth=0.5)
    bx.set_title(f"(b) 21700: {shown['ignited']} cooled then ignited, {shown['rescued']} rescued", fontsize=8,
                 loc="left", color=INK)
    bx.legend(loc="upper right", fontsize=6.5, frameon=False)
    for s in ("top", "right"):
        bx.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(out / "fig_nist.pdf")
    plt.close(fig)


def fig_loop(out: Path):
    fig, ax = plt.subplots(figsize=(7.0, 1.9))
    ax.set_xlim(0, 100)
    ax.set_ylim(-3, 27)
    ax.axis("off")
    boxes = [
        (0.5, "Observe", "Muse Spark\nreads frames, tables;\nSAM 3.1 outlines"),
        (17.2, "Hypothesise", "Muse Wonder\nstates competing\nmechanisms"),
        (33.9, "Forecast", "world models give\nP(ignite | action),\nlabelled simulated"),
        (50.6, "Commit", "decision and p\nrecorded before\nthe reveal"),
        (67.3, "Reveal, score", "sealed outcome\nopened, scored\nagainst baselines"),
        (84.0, "Update", "after feedback,\nonly through\nthe update gate"),
    ]
    w, h, y = 15.0, 16, 8
    for x, title, body in boxes:
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.2,rounding_size=1.2",
                                    facecolor="white", edgecolor=INK, linewidth=0.7))
        ax.text(x + w / 2, y + h - 3.0, title, ha="center", va="center", fontsize=7.5, fontweight="bold",
                color=INK)
        ax.text(x + w / 2, y + h / 2 - 2.2, body, ha="center", va="center", fontsize=6.2, color=INK,
                linespacing=1.3)
    for i in range(len(boxes) - 1):
        ax.add_patch(FancyArrowPatch((boxes[i][0] + w + 0.3, y + h / 2), (boxes[i + 1][0] - 0.3, y + h / 2),
                                     arrowstyle="-|>", mutation_scale=7, color=INK, linewidth=0.7))
    ax.add_patch(FancyArrowPatch((boxes[-1][0] + w / 2, y - 0.4), (boxes[0][0] + w / 2, y - 0.4),
                                 connectionstyle="arc3,rad=-0.06", arrowstyle="-|>", mutation_scale=7,
                                 color=MUTED, linewidth=0.7))
    ax.text(50, -2.6, "insert-only ledger: every record content-addressed and sequenced; a deterministic gate "
                      "decides claims; translucent verify re-checks offline", ha="center", fontsize=6.2, color=MUTED)
    fig.savefig(out / "fig_loop.pdf")
    plt.close(fig)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--inventory", type=Path, required=True)
    p.add_argument("--sheet", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path(__file__).parent / "figures")
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    fig_loop(a.out)
    fig_nist(load_inventory(a.inventory), load_sheet(a.sheet), a.out)


if __name__ == "__main__":
    main()
