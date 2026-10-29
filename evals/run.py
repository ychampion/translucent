"""One command: fault-injection suite + plain-pipeline baseline + clean cases (+ a fake-mode ablation smoke run).

    uv run python -m evals.run

Offline only: TRANSLUCENT_SAM=strict (recorded SAM), sockets disabled for the whole run. Writes
evals/results/<timestamp>.json and overwrites evals/REPORT.md.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ["TRANSLUCENT_SAM"] = "strict"

import json  # noqa: E402
import shutil  # noqa: E402
import socket  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
REPORT = HERE / "REPORT.md"
LEDGER_ONLY = {"fabricated measurement", "retracted evidence", "critic overreach"}  # no plain-pipeline analogue


@contextmanager
def no_network():
    """Any socket use during the run raises (proves the suite is offline)."""
    def refuse(*a, **k):
        raise OSError("network disabled during evals")
    saved = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)
    socket.socket.connect = socket.socket.connect_ex = refuse
    socket.create_connection = socket.getaddrinfo = refuse
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo = saved


def totals(rows) -> dict:
    ran = [r for r in rows if not r["error"]]
    t = {"cases": len(rows), "errors": len(rows) - len(ran),
         "translucent_caught": sum(r["translucent"]["caught"] for r in ran),
         "baseline_caught": sum(r["baseline"]["caught"] for r in ran)}
    data = [r for r in ran if r["fault_class"] not in LEDGER_ONLY]
    t["data_path"] = {"cases": len(data), "translucent_caught": sum(r["translucent"]["caught"] for r in data),
                      "baseline_caught": sum(r["baseline"]["caught"] for r in data)}
    by = {}
    for r in ran:
        b = by.setdefault(r["fault_class"], {"cases": 0, "translucent_caught": 0, "baseline_caught": 0})
        b["cases"] += 1
        b["translucent_caught"] += r["translucent"]["caught"]
        b["baseline_caught"] += r["baseline"]["caught"]
    t["by_class"] = by
    return t


def run(tmp_root: Path | None = None, ablation: bool = True, only=None, log=print) -> dict:
    from evals import faults
    why = faults.available()
    if why:
        raise SystemExit(f"cannot run the evals here: {why}")
    tmp_root = Path(tmp_root or tempfile.mkdtemp(prefix="translucent-evals-"))
    t0 = time.time()
    with no_network():
        rows = faults.run_all(tmp_root / "faults", only=only, log=log)
        clean = faults.clean(tmp_root / "clean")
        abl = None
        if ablation:
            from evals.ablation import run_ablation
            abl = run_ablation(tmp_root / "ablation", n=1, mode="fake", log=lambda *a: None)
    return {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(time.time() - t0, 1),
            "sam_mode": os.environ["TRANSLUCENT_SAM"], "totals": totals(rows), "cases": rows, "clean": clean,
            "ablation_fake_smoke": abl and {"summary": abl["summary"], "campaign1": abl["campaign1"]}}


# ------------------------------------------------------------------ report

WHY = {  # one line per known weak spot; printed only while the case is not caught
    "scale_si_bar": "Two different spatial_scale records for the same video coexist; nothing compares them, and the "
                    "gate only checks that *a* scale is on record, so the 1.57x-wrong one earns a finding.",
    "scale_other_experiment": "measure_growth and the gate never check that the scale measurement belongs to the "
                              "video being measured (scale.artifact_ids is Movie 1, boxes are Movie 4).",
    "time_base_mislabelled": "The gate trusts the quantity name 'time_base'; it does not check that the clock came "
                             "from a trusted records tool (lookup_calibration / read_cell_voltage).",
    "voltage_other_movie": "events_coincide never checks that both measurements come from the same experiment; "
                           "Movie 4's voltage corroborates a Movie 1 event.",
    "clock_other_movie": "measure_growth applies Movie 4's clock to Movie 1 frames (no experiment check on "
                         "time_base_id); the result even lands near the paper's 45 um/s.",
    "experiment_mislabelled": "Records are keyed by the experiment id given at import; no content check ties the "
                              "file to its records (Movie 1 gets Movie 4's scale and clock).",
    "junk_point_masks_m4": "measure_growth drops flagged frames but still measures objects whose remaining frames "
                           "are junk (flagged share per measured object: #2 62%, #4 47%, #5 90%); the naive claims are earned.",
    "box_merged_filaments": "A confident, thin-enough box spanning two filaments passes every _flag rule; nothing "
                            "detects the merge.",
    "meas_borrowed_run": "Admission checks only that the cited run exists and is ok, not that the run produced the "
                         "measurement (runs do not list their measurement outputs).",
    "short_as_derived": "REQUIRED_CORROBORATION matches the exact quantity 'short_time'; the sandbox names it "
                        "'derived:short_time', so the voltage cross-check is skipped.",
    "retract_masks": "stale_reason follows the measurement's run and calibrations only, not the artifacts (masks) "
                     "it was computed from; invalidating the SAM run leaves the finding earned.",
}

REQUESTS = """Requests 1-9 from the first run (2026-10-28) are now in core: calibrations must belong to the measured recording
(tool and gate), corroboration must come from the same recording, scale and clock count only from the experiment's
records tools, conflicting scales on record refuse the finding, the `derived:` prefix no longer skips corroboration,
runs list the measurements they produced, staleness follows artifact lineage, objects whose outlines are often the
wrong shape are not measured, and import ties each experiment's records to its exact video bytes.

Open:
- **Merged masks** (`box_merged_filaments`): no cheap rule yet; candidates are an across-axis jump against the
  object's own early frames, or overlap checks between prompted objects.
- **Data:** record lane-box `segment_images` outputs for Movie 4 when Meta resumes, so the velocity cases can use
  real clean masks instead of the scripted filament."""


def _cell(s: str) -> str:
    return str(s).replace("|", "/").replace("\n", " ")


def write_report(res: dict, path: Path = REPORT, results_path: Path | None = None):
    t = res["totals"]
    rows = res["cases"]
    dp = t["data_path"]
    L = [f"# Translucent evals — fault injection vs a plain pipeline",
         "",
         f"Generated {res['generated_at']} by `uv run python -m evals.run` ({res['seconds']} s, offline, "
         f"TRANSLUCENT_SAM={res['sam_mode']}, sockets disabled)."
         + (f" Raw results: `{results_path.relative_to(HERE.parent).as_posix()}`." if results_path else ""),
         "",
         f"**Translucent catches {t['translucent_caught']} of {t['cases'] - t['errors']} injected faults; "
         f"the plain pipeline catches {t['baseline_caught']}.**"
         + (f" ({t['errors']} case(s) errored and are excluded.)" if t["errors"] else ""),
         "",
         f"On the {dp['cases']} data-path faults (calibration, time base, video identity, masks, corroboration) that a "
         f"plain script also faces: Translucent {dp['translucent_caught']}/{dp['cases']}, baseline "
         f"{dp['baseline_caught']}/{dp['cases']}. The other {t['cases'] - t['errors'] - dp['cases']} (fabricated "
         f"records, retraction, critic) concern the evidence ledger itself; a plain script has no analogue, so it "
         f"cannot catch them by construction.",
         "",
         "How to read it: each case builds a fresh ledger, plants one fault, and runs the real tools "
         "(`tools.call`) or, where the fault is in a record itself, inserts that record (`via=record`). Then the "
         "*naive claim* is proposed: exactly what the faulty evidence shows (value ± its uncertainty, padded). So only "
         "a check that notices the fault can stop it, never a number that happens to disagree. *Caught* = the faulty "
         "evidence did not become an earned finding. The baseline (`evals/baseline.py`) catches a fault only if it "
         "crashes or outputs a non-finite number.",
         "",
         "Where Movie 4 needs clean filament masks, a scripted SAM output (one thin filament growing 1 px per recorded "
         "second, ±1.5 px jitter) goes through the real `track_objects` code, because only junk point-prompt masks are "
         "recorded for Movie 4. Movie 1 uses its real recorded SAM track; the junk-mask case uses the 49 real Movie 4 "
         "recordings.",
         "",
         "| # | Fault class | Variant | via | Translucent | How | Baseline |",
         "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        if r["error"]:
            L.append(f"| {i} | {r['fault_class']} | {_cell(r['variant'])} | {r['via']} | ERROR | "
                     f"{_cell(r['error'][:80])} | — |")
            continue
        tr, b = r["translucent"], r["baseline"]
        L.append(f"| {i} | {r['fault_class']} | {_cell(r['variant'])} | {r['via']} | "
                 f"{'**caught**' if tr['caught'] else 'NOT caught'} | {tr['how'] or '—'} | "
                 f"{'caught (' + _cell(b['how'][:40]) + ')' if b['caught'] else 'not caught'} |")
    L += ["", f"Totals: Translucent **{t['translucent_caught']}/{t['cases'] - t['errors']}**, baseline "
          f"**{t['baseline_caught']}/{t['cases'] - t['errors']}**.", "",
          "| Fault class | Cases | Translucent | Baseline |", "|---|---|---|---|"]
    for k, v in t["by_class"].items():
        L.append(f"| {k} | {v['cases']} | {v['translucent_caught']} | {v['baseline_caught']} |")
    L += ["", "## Clean cases (no fault)", "", "| Case | Truth | Translucent | Baseline |", "|---|---|---|---|"]
    for c in res["clean"]:
        L.append(f"| {_cell(c['case'])} | {_cell(c['truth'])} | {_cell(c['translucent'])} | {_cell(c['baseline'])} |")
    L += ["", "The clean path works: on Movie 4's real clock and scale Translucent recovers the injected speed and earns "
          "it, while the per-playback-frame script is off by ~18x (each image is 6.9 s of real time but 0.4 s of playback). On Movie 1 both produce "
          "the same number, but Translucent refuses to earn it because no clock is on record.", "",
          "## Not caught / weak spots", ""]
    missed = [r for r in rows if not r["error"] and not r["translucent"]["caught"]]
    for r in missed:
        L.append(f"- **{r['id']}** ({r['fault_class']}: {r['variant']}): "
                 f"{WHY.get(r['id'], 'not caught; see detail.')} Observed: {_cell(r['translucent']['detail'][:220])}")
    for r in rows:
        if r["error"]:
            L.append(f"- **{r['id']}** errored: `{_cell(r['error'][:200])}` (harness problem, not a verdict).")
    L += ["- Without a recorded clock, `measure_growth` does not remove repeated images (Movie 1: 50 playback "
          "frames = 5 captured images), so the playback speed it reports to the agent is per frame, not per image. "
          "The gate still refuses it.",
          "- The critic can't override the gate (3/3), but in the runtime it is only consulted after the gate says "
          "supported. So a fault the gate misses (above) reaches the critic with nothing behind it.",
          "", "## Requests", "", REQUESTS]
    abl = res.get("ablation_fake_smoke")
    if abl:
        s = abl["summary"]
        L += ["## Memory ablation (scaffold)", "",
              "`uv run python -m evals.ablation` (fake model, offline) or `--real --profile dev --n 3` (paid; real result in evals/MEMORY.md). "
              "Fake-mode smoke result, which proves only that the plumbing works (the scripted agent skips steps when "
              f"it sees a lesson): memory on {s['on']['steps']} steps / lesson used {s['on']['used_campaign1_lesson']}; "
              f"memory off {s['off']['steps']} steps / lesson used {s['off']['used_campaign1_lesson']}.", ""]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    tmp = Path(tempfile.mkdtemp(prefix="translucent-evals-"))
    try:
        res = run(tmp, only=[a for a in argv if not a.startswith("-")] or None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    write_report(res, REPORT, out)
    t = res["totals"]
    print(f"\nTranslucent caught {t['translucent_caught']}/{t['cases'] - t['errors']}, baseline "
          f"{t['baseline_caught']}/{t['cases'] - t['errors']} ({t['errors']} errors). Wrote {out} and {REPORT}")
    return 1 if t["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
