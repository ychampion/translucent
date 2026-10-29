"""Does the visual second opinion (critic) catch wrong findings? Seeded statements with known answers, scored.

    python -m evals.critic_eval                       offline: scripted critic (harness check) + always-agree critic
    python -m evals.critic_eval --build               rebuild evals/critic_cases/*.png and manifest.json from real data
    python -m evals.critic_eval --real --repeats 3 --budget-usd 2
                                                      the real critic (Muse Spark Open on Fireworks, metered, hard cap);
                                                      prints a worst-case cost first and refuses if it exceeds the budget
The number that matters is the false-agree rate: how often the critic says SUPPORTED to a finding that is wrong or
cannot be read from the image.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from contextlib import nullcontext
from pathlib import Path

from translucent.runtime import PROFILES, Settings, critic_messages, critic_verdict

CASES = Path(__file__).resolve().parent / "critic_cases"
VERDICTS = ("supported", "refuted", "inconclusive")
M4 = "reisecker2023/movie4"
RAW = Path(__file__).resolve().parents[1] / "data" / "raw" / "reisecker2023"

# Movie 4 voltage events on the recorded clock (s after 41.32 h), from data/groundtruth/reisecker2023.json
# movie4_voltage_events: first short 86-93 s, re-plating onset (voltage leaves 0 V) 211-218 s, second short 345-358 s.
SEEDS = [
    # id, image spec, statement, expected, why
    ("short_ok", ("plot", [90]), "The cell shorts at about 90 s: the voltage falls to 0 V and stays there.",
     "supported", "Voltage reaches ~0 V between 86 and 93 s and stays flat until ~211 s."),
    ("recovery_ok", ("plot", [215]), "At about 215 s the voltage leaves 0 V and falls below -1 V.",
     "supported", "Voltage leaves 0 V between 211 and 218 s (re-plating onset) and keeps falling."),
    ("short2_ok", ("plot", [351]), "A second short occurs at about 351 s: the voltage jumps from about -13 V back to 0 V.",
     "supported", "Voltage jumps from -13.3 V to ~0.3 V between 345 and 358 s."),
    ("short_window_ok", ("plot", [90, 215]), "The voltage stays near 0 V from about 90 s to about 215 s.",
     "supported", "The flat 0 V stretch runs from ~90 s to ~211 s."),
    ("short_late", ("plot", [180]), "The cell shorts at about 180 s: the voltage falls to 0 V there.",
     "refuted", "+90 s off: at 180 s the voltage has already been ~0 V for 90 s; nothing happens at the mark."),
    ("short_late2", ("plot", [160]), "The cell shorts at about 160 s.",
     "refuted", "+70 s off: the drop to 0 V happened at ~90 s; the mark sits on a flat 0 V line."),
    ("recovery_late", ("plot", [320]), "At about 320 s the voltage leaves 0 V.",
     "refuted", "+105 s off: the voltage left 0 V at ~215 s; at 320 s it is near -11 V."),
    ("short2_early", ("plot", [260]), "A second short occurs at about 260 s: the voltage returns to 0 V.",
     "refuted", "-91 s off: at 260 s the voltage is around -5 V and falling; the return to 0 V is at ~351 s."),
    ("short2_late", ("plot", [400]), "A second short occurs at about 400 s.",
     "refuted", "+49..+55 s after the event and on a flat 0 V line; nothing changes at the mark."),
    ("direction_215", ("plot", [215]), "At about 215 s the cell voltage rises sharply above 0 V.",
     "refuted", "Wrong direction: at 215 s the voltage falls from 0 V to negative values."),
    ("direction_351", ("plot", [351]), "At about 351 s the voltage falls from 0 V to about -13 V.",
     "refuted", "Wrong direction: at 351 s the voltage rises from -13 V to 0 V."),
    ("plot_not_visible", ("plot", []), "The cell temperature stayed at 21 °C throughout the recording.",
     "inconclusive", "The image shows only voltage; temperature is not in it."),
    ("m1_span_ok", ("frame", 1, 109), "A single straight dark filament crosses the whole gap from one electrode to the other.",
     "supported", "Movie 1 last frame: the dendrite runs diagonally from the right electrode to the left one (short)."),
    ("m1_span_small", ("frame", 1, 109), "The dark filament has crossed only about a tenth of the gap between the electrodes.",
     "refuted", "Wrong magnitude: in the last frame it spans the full gap."),
    ("m1_span_early", ("frame", 1, 0), "A single straight dark filament crosses the whole gap from one electrode to the other.",
     "refuted", "Movie 1 first frame (intact condition): no filament spans the gap yet."),
    ("frame_speed_not_visible", ("frame", 1, 109), "The filament grows at about 10 um/s.",
     "inconclusive", "One frame has no clock: a speed cannot be read from it."),
]


def build(root: Path = CASES) -> list[dict]:
    """Render every case image from the recorded data and write manifest.json."""
    import cv2
    from translucent import instruments, tools
    from translucent.runtime import _voltage_plot
    root.mkdir(parents=True, exist_ok=True)
    series = instruments.voltage(M4)
    cases = []
    for cid, spec, statement, expected, why in SEEDS:
        out = root / (f"m4_voltage_{'_'.join(map(str, spec[1])) or 'nomarks'}.png" if spec[0] == "plot"
                      else f"movie{spec[1]}_frame{spec[2]}.png")  # cases that look at the same image share it
        if spec[0] == "plot":
            _voltage_plot(series, spec[1], out)
            source = f"{M4} voltage log (data/groundtruth/reisecker2023_movie4_timebase.csv), marks {spec[1]}"
        else:
            cv2.imwrite(str(out), tools._frame(RAW / f"movie{spec[1]}.mp4", spec[2]))
            source = f"reisecker2023/movie{spec[1]} frame {spec[2]}"
        cases.append({"id": cid, "statement": statement, "image": out.name, "expected": expected, "why": why,
                      "source": source})
    (root / "manifest.json").write_text(json.dumps({"cases": cases}, indent=1, ensure_ascii=False), encoding="utf-8")
    return cases


def load_cases(root: Path = CASES) -> list[dict]:
    cases = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["cases"]
    seen = set()
    for c in cases:
        missing = {"id", "statement", "image", "expected", "why"} - c.keys()
        if missing:
            raise ValueError(f"case {c.get('id')} is missing {sorted(missing)}")
        if c["expected"] not in VERDICTS:
            raise ValueError(f"case {c['id']}: expected must be one of {VERDICTS}, got {c['expected']!r}")
        if c["id"] in seen:
            raise ValueError(f"duplicate case id {c['id']}")
        if not (root / c["image"]).is_file():
            raise ValueError(f"case {c['id']}: image {c['image']} not found in {root}")
        seen.add(c["id"])
        c["path"] = root / c["image"]
    return cases


# ------------------------------------------------------------------ offline critics

_FORMATS = {"supported": ["SUPPORTED\nThe marked time matches the change in the image.",
                          "**Supported** - the image shows it.", "Verdict: SUPPORTED. It is visible."],
            "refuted": ["REFUTED\nThe image shows something else at the mark.", "**REFUTED**: the change is elsewhere.",
                        "Verdict: refuted - not what the image shows."],
            "inconclusive": ["INCONCLUSIVE\nThe image does not show this.", "**Inconclusive.** Not in the image.",
                             "Verdict: INCONCLUSIVE - cannot be read from this image."]}


def scripted(cases):
    """Answers each case's expected verdict in assorted well-formed formats: checks the harness and parser, not a model."""
    n = {"i": 0}

    def policy(messages, tools_):  # run() asks the cases in manifest order
        exp = cases[n["i"] % len(cases)]["expected"]
        n["i"] += 1
        return _FORMATS[exp][n["i"] % 3], []
    return policy


def always_agree(cases):
    return lambda messages, tools_: ("SUPPORTED\nThe image clearly shows it.", [])


OFFLINE = {"scripted": scripted, "agree": always_agree}


# ------------------------------------------------------------------ cost, run, score

def estimate_usd(cases, repeats: int, settings: Settings = Settings(), max_retries: int | None = None) -> float:
    """Worst case including SDK retry attempts; each attempt uses Metered.chat's reservation formula."""
    from openai import DEFAULT_MAX_RETRIES
    from translucent.budget import SAFETY
    from translucent.providers import IMAGE_TOKENS_WORST, TOKEN_PRICES
    max_retries = DEFAULT_MAX_RETRIES if max_retries is None else max_retries
    provider, model = PROFILES["dev"]["critic"]
    p_in, _, p_out = TOKEN_PRICES[provider][model]
    total = 0.0
    for c in cases:
        text = json.dumps(critic_messages(c["statement"], c["path"]))
        images = text.count("image_url")
        in_tokens = len(text.split("base64,")[0]) // 2 + images * IMAGE_TOKENS_WORST
        total += (in_tokens * p_in + settings.max_tokens * p_out) * SAFETY
    return total * repeats * (1 + max_retries)


def run(cases, client, repeats: int = 1, settings: Settings = Settings(), log=print,
        budget_usd: float | None = None, journal: Path | None = None, label: str = "real") -> list[dict]:
    if repeats < 1:
        raise ValueError("repeats must be positive")
    if budget_usd is not None and (not math.isfinite(budget_usd) or budget_usd <= 0):
        raise ValueError("budget must be a finite, positive amount")
    provider, model = PROFILES["dev"]["critic"]
    kw = {"reasoning_effort": settings.reasoning_effort} if settings.reasoning_effort and provider == "fireworks" else {}
    sdk = getattr(client, "_client", None)
    if sdk is not None:
        client._client = sdk.with_options(max_retries=0)  # a timed-out attempt must keep its own reservation
    start = client.ledger.balance()["committed_usd"]
    if journal is not None:
        journal.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with (journal.open("a", encoding="utf-8") if journal is not None else nullcontext()) as out:
        for r in range(repeats):
            for c in cases:
                before = client.ledger.balance()["committed_usd"]
                worst = estimate_usd([c], 1, settings, max_retries=0) if budget_usd is not None else 0.0
                if budget_usd is not None and before - start + worst > budget_usd:
                    log(f"stopped: next worst-case ${worst:.4f} would exceed this run's ${budget_usd:.4f} budget")
                    return rows
                try:
                    msg = client.chat(model=model, messages=critic_messages(c["statement"], c["path"]),
                                      max_tokens=settings.max_tokens, **kw).choices[0].message
                    verdict, reason, raw = *critic_verdict(msg.content), msg.content
                except Exception as e:  # noqa: BLE001 - one failed call is recorded as unclear; a budget stop ends the run
                    from translucent.budget import BudgetExceeded
                    if isinstance(e, BudgetExceeded):
                        log(f"stopped: {e}")
                        return rows
                    verdict, reason, raw = "error", f"{type(e).__name__}: {e}"[:300], None
                row = {"case": c["id"], "repeat": r, "expected": c["expected"], "verdict": verdict,
                       "reason": reason[:300], "raw": raw, "usd": client.ledger.balance()["committed_usd"] - before}
                rows.append(row)
                if out is not None:
                    out.write(json.dumps({"critic": label, **row}) + "\n")
                    out.flush()
                    os.fsync(out.fileno())
                log(f"  {c['id']:<24} r{r} expected {c['expected']:<12} critic {verdict}")
    return rows


def score(rows) -> dict:
    """agreement: verdict == expected. false_agree: SUPPORTED on a finding that is wrong or not readable from the image.
    unclear: inconclusive or failed calls, over all runs. missed_correct: correct findings the critic did not support."""
    def rate(sel, pred):
        sel = list(sel)
        return round(sum(map(pred, sel)) / len(sel), 3) if sel else None
    wrong = [r for r in rows if r["expected"] != "supported"]
    right = [r for r in rows if r["expected"] == "supported"]
    usd = [r["usd"] for r in rows]
    return {"runs": len(rows), "agreement": rate(rows, lambda r: r["verdict"] == r["expected"]),
            "false_agree": rate(wrong, lambda r: r["verdict"] == "supported"), "wrong_findings": len(wrong),
            "unclear": rate(rows, lambda r: r["verdict"] in ("inconclusive", "error")),
            "errors": sum(r["verdict"] == "error" for r in rows),
            "missed_correct": rate(right, lambda r: r["verdict"] != "supported"),
            "usd_total": round(sum(usd), 4), "usd_per_run": round(sum(usd) / len(usd), 5) if usd else None}


def _print(name, s):
    pct = lambda x: "n/a" if x is None else f"{100 * x:.0f}%"  # noqa: E731
    print(f"{name}: {s['runs']} runs | agreement {pct(s['agreement'])} | false-agree on wrong findings "
          f"{pct(s['false_agree'])} (of {s['wrong_findings']}) | unclear {pct(s['unclear'])} | missed correct "
          f"{pct(s['missed_correct'])} | cost ${s['usd_total']:.4f} (${s['usd_per_run'] or 0:.5f}/run)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", action="store_true", help="rebuild the case images and manifest from real data")
    ap.add_argument("--real", action="store_true", help="call the real critic model (paid, metered)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--budget-usd", type=float, default=None, help="cap this run's spend, reserving the next call's worst case")
    ap.add_argument("--critic", choices=sorted(OFFLINE), action="append", help="offline critic(s) (default: both)")
    ap.add_argument("--out", type=Path, default=None, help="write JSON results here and append runs to the adjacent .jsonl")
    a = ap.parse_args(argv)
    if a.repeats < 1:
        print("refusing to start: --repeats must be positive")
        return 2
    if a.build:
        print(f"built {len(build())} cases in {CASES}")
    cases = load_cases()
    results = {}
    if a.real:
        if a.budget_usd is not None and (not math.isfinite(a.budget_usd) or a.budget_usd <= 0):
            print("refusing to start: --budget-usd must be a finite, positive amount")
            return 2
        est = estimate_usd(cases, a.repeats)
        print(f"{len(cases)} cases x {a.repeats} repeats = {len(cases) * a.repeats} critic calls; worst-case cost "
              f"${est:.2f} (including SDK retries; automatic retries are disabled for this eval)")
        if a.budget_usd is None or est > a.budget_usd:
            print(f"refusing to start: worst-case ${est:.2f} exceeds --budget-usd {a.budget_usd}" if a.budget_usd is not None
                  else "refusing to start: pass --budget-usd")
            return 2
        from translucent.providers import MeteredFireworks
        a.out = a.out or CASES / "results" / f"{time.strftime('%Y%m%dT%H%M%S')}.json"
        rows = run(cases, MeteredFireworks(), a.repeats, budget_usd=a.budget_usd, journal=a.out.with_suffix(".jsonl"))
        results["real"] = {"rows": rows, "score": score(rows)}
        _print("real critic", results["real"]["score"])
    else:
        from evals.fake_llm import FakeClient
        for name in a.critic or ["scripted", "agree"]:
            rows = run(cases, FakeClient(OFFLINE[name](cases)), a.repeats, log=lambda *_: None,
                       journal=a.out.with_suffix(".jsonl") if a.out else None, label=name)
            results[name] = {"rows": rows, "score": score(rows)}
            _print(f"offline {name}", results[name]["score"])
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
        print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
