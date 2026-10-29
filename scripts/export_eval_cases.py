"""Export the eval cases the Proof screen's "Try to fool it" panel shows, from a real eval run.

Usage: uv run python scripts/export_eval_cases.py [results.json] [--out web/public/fixtures/evals.json]

Default input: the latest evals/results/<timestamp>.json with at least 37 cases. Every number written comes from the
results file: the plain pipeline's output, the true value where the case states one, and the run's totals. The plain
words (button label, the quantity, the one-sentence reason) are written here, one entry per chosen case id, because
the results speak in tool and ledger terms. A case is only exported if Translucent caught it and the plain pipeline
did not; anything else stops the export, so the panel can never show a contrast the run did not produce.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "evals" / "results"
OUT = ROOT / "web" / "public" / "fixtures" / "evals.json"
MIN_CASES = 37

# id -> what the panel says. label: the button; quantity: what the plain pipeline reported; reason: why Translucent
# would not report it, in one plain sentence.
CHOSEN: dict[str, dict[str, str]] = {
    "time_base_mislabelled": {
        "label": "Time it by video playback",
        "quantity": "Growth speed",
        "reason": "The video plays far faster than the experiment ran, so only the lab's own clock can time it.",
    },
    "scale_other_experiment": {
        "label": "Take the scale from another experiment",
        "quantity": "Growth speed",
        "reason": "That scale was measured on a different recording, so the distances are wrong.",
    },
    "voltage_other_movie": {
        "label": "Use another battery's voltage",
        "quantity": "Battery shorted at",
        "reason": "That voltage was logged on a different battery, so it can't confirm this one shorted.",
    },
    "box_whole_sample": {
        "label": "Outline the whole sample",
        "quantity": "Growth speed",
        "reason": "The outline covers the whole sample, not the filament, so there is nothing real to time.",
    },
    "mask_merged_small_gap": {
        "label": "Blur two filaments into one",
        "quantity": "Growth speed",
        "reason": "The outline holds two filaments side by side, so their growth gets mixed together.",
    },
    "claim_cites_nonexistent": {
        "label": "Cite a number that doesn't exist",
        "quantity": "Growth speed",
        "reason": "No measurement on record backs that number, so it can't be reported.",
    },
}

# The plain pipeline reports bare numbers; these are the units its outputs are in (evals/faults.py).
UNITS = {"velocity_um_s": "µm/s"}
SCALAR_UNIT = {"Growth speed": "µm/s", "Battery shorted at": "s"}
TRUTH = re.compile(r"\(truth(?: for filament 1)?:? ([0-9.]+) ([^)\s]+)\)")


def _unit(u: str) -> str:
    return u.replace("um", "µm")


def _sig(x: float) -> float:
    """What the screen shows: one decimal from 10 up (112.8), else three significant figures (1.86)."""
    return round(x, 1) if abs(x) >= 10 else float(f"{x:.3g}")


def latest(results: Path, min_cases: int) -> Path:
    for p in sorted(results.glob("2*.json"), reverse=True):
        if len(json.loads(p.read_text(encoding="utf-8")).get("cases", [])) >= min_cases:
            return p
    sys.exit(f"no results file in {results} with at least {min_cases} cases")


def plain_output(case: dict, quantity: str) -> dict:
    out = case["baseline"]["output"]
    if isinstance(out, dict):  # {"1": {"velocity_um_s": ..., ...}}: object 1 is the filament the case is about
        obj = out["1"]
        key = next(k for k in obj if k in UNITS)
        return {"value": _sig(obj[key]), "unit": UNITS[key]}
    if isinstance(out, (int, float)):
        return {"value": _sig(out), "unit": SCALAR_UNIT[quantity]}
    sys.exit(f"{case['id']}: baseline output {out!r} is not a number")


def export(path: Path) -> dict:
    run = json.loads(path.read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in run["cases"]}
    cases = []
    for cid, words in CHOSEN.items():
        c = by_id.get(cid)
        if c is None:
            sys.exit(f"{cid}: not in {path.name}")
        if not c["translucent"]["caught"] or c["baseline"]["caught"] or c.get("error"):
            sys.exit(f"{cid}: in {path.name} it is not 'Translucent caught, plain pipeline did not'")
        row = {"id": cid, **words, "plain": plain_output(c, words["quantity"])}
        m = TRUTH.search(c["translucent"].get("detail") or "")
        if m:
            row["truth"] = {"value": float(m.group(1)), "unit": _unit(m.group(2))}
        cases.append(row)
    t = run["totals"]
    return {
        "source": {"file": f"evals/results/{path.name}", "generated_at": run["generated_at"]},
        "totals": {"planted": t["cases"] - t.get("errors", 0), "caught": t["translucent_caught"],
                   "plain_caught": t["baseline_caught"]},
        "cases": cases,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("results", nargs="?", type=Path, help="eval results JSON (default: latest with >= 37 cases)")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    src = a.results or latest(RESULTS, MIN_CASES)
    data = export(src)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    t = data["totals"]
    print(f"{src.name}: {len(data['cases'])} cases, caught {t['caught']}/{t['planted']}, "
          f"plain {t['plain_caught']} -> {a.out}")


if __name__ == "__main__":
    main()
