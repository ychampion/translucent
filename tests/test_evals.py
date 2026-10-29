"""Eval harness: every fault case runs (offline) and returns a well-formed result; the ablation scaffold runs end to
end with a fake model; the baseline computes what it says. Asserts shape, not that every fault is caught."""
import numpy as np
import pytest

from evals import baseline, faults
from evals import run as evals_run
from evals.ablation import run_ablation

needs_data = pytest.mark.skipif(faults.available() is not None, reason=f"eval data missing: {faults.available()}")


@needs_data
def test_every_fault_case_runs_and_is_well_formed(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSLUCENT_SAM", "strict")
    res = evals_run.run(tmp_path / "run", ablation=False, log=lambda *a: None)
    rows = res["cases"]
    assert len(rows) == len(faults.CASES) >= 15
    assert len({r["id"] for r in rows}) == len(rows)
    errors = {r["id"]: r["error"] for r in rows if r["error"]}
    assert not errors, errors
    classes = {r["fault_class"] for r in rows}
    assert {"spatial calibration", "time base", "swapped video", "junk masks", "fabricated measurement",
            "missing corroboration", "retracted evidence", "critic overreach"} <= classes
    for r in rows:
        t, b = r["translucent"], r["baseline"]
        assert set(t) == {"caught", "how", "detail", "evidence_ids"} and isinstance(t["caught"], bool)
        assert (t["how"] is not None) == t["caught"]
        assert set(b) == {"caught", "how", "output"} and isinstance(b["caught"], bool)
        assert r["via"] in ("tool", "record")
    tot = res["totals"]
    assert tot["cases"] == len(rows) and tot["errors"] == 0
    assert tot["translucent_caught"] == sum(r["translucent"]["caught"] for r in rows)
    assert len(res["clean"]) == 3 and all({"case", "truth", "translucent", "baseline"} <= set(c) for c in res["clean"])
    report = tmp_path / "REPORT.md"
    evals_run.write_report(res, report)
    text = report.read_text(encoding="utf-8")
    assert f"Translucent catches {tot['translucent_caught']} of {tot['cases']}" in text and "## Requests" in text


@needs_data
def test_network_is_blocked_during_run():
    import socket
    saved = socket.create_connection
    with evals_run.no_network():
        with pytest.raises(OSError, match="network disabled"):
            socket.create_connection(("example.com", 80))
    assert socket.create_connection is saved


@needs_data
def test_ablation_fake_mode_end_to_end(tmp_path):
    with evals_run.no_network():
        res = run_ablation(tmp_path, n=1, mode="fake", log=lambda *a: None)
    assert res["campaign1_lessons"], "campaign 1 should record a lesson"
    on, off = res["arms"]["on"][0], res["arms"]["off"][0]
    assert on["status"] == off["status"] == "finished"
    assert on["used_campaign1_lesson"] and on["lessons_loaded"] >= 1
    assert not off["used_campaign1_lesson"] and off["lessons_loaded"] == 0
    for m in (on, off):
        assert m["steps"] > 0 and m["cost_usd"] > 0 and m["earned"] >= 1


def test_baseline_velocity_and_failure_modes():
    boxes = {f: {"1": [100.0 - 2 * f, 0, 100.0, 5, 0.9]} for f in range(20)}   # 2 px per frame
    v = baseline.growth_velocity(boxes, px_per_mm=200.0, fps=25)["1"]
    assert np.isclose(v["velocity_um_s"], 2 * 25 / 200 * 1000) and v["n_frames"] == 20
    assert baseline.run(baseline.growth_velocity, boxes, None, 25)["caught"]          # no scale: it crashes
    assert not baseline.run(baseline.growth_velocity, boxes, 999.0, 25)["caught"]     # wrong scale: it does not
    assert baseline.short_time([0, 1, 2], [3.0, 0.01, 0.0]) == 1.0
