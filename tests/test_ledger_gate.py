import sqlite3
import time

import numpy as np
import pytest

from translucent import gate
from translucent.contracts import Artifact, Campaign, Claim, Measurement, Predicate, Run, Source, Uncertainty
from translucent.ledger import AdmissionError, Ledger

T = time.time()
VID = "sha256:" + "f" * 64


@pytest.fixture
def L(tmp_path):
    led = Ledger(tmp_path / "ledger")
    led.add(Campaign(id="c1", objective="How fast?", title="Dendrite", inputs=[], budget_usd=1.0, created_at=T))
    led.add(Artifact(id=VID, kind="video", media_type="video/mp4", bytes=1, created_at=T,
                     source=Source(uri="experiment:test")))  # every tool-path measurement traces to a recording
    return led


def run(L, rid, status="ok", inputs=(), tool="measure"):
    return L.add(Run(id=rid, campaign_id="c1", step_id="s", tool=tool, idempotency_key=rid,
                     input_ids=list(inputs), status=status, started_at=T, ended_at=T))


def meas(L, mid, quantity, value, unit, u=0.0, kind="ci95", cal=(), run_id="r1", arts=None):
    return L.add(Measurement(id=mid, run_id=run_id, quantity=quantity, value=value, unit=unit,
                             uncertainty=Uncertainty(value=u, kind=kind, method="test"), calibration_ids=list(cal),
                             artifact_ids=[VID] if arts is None else list(arts)))


def claim(L, cid, ptype, mids, **params):
    return L.add(Claim(id=cid, campaign_id="c1", author="worker", statement="s", created_at=T,
                       predicate=Predicate(type=ptype, params=params, measurement_ids=mids)))


def calibrated(L):
    run(L, "r1")
    run(L, "rc", tool="lookup_calibration")  # scale and clock count only from the experiment's records
    meas(L, "scale", "spatial_scale", 184.9, "px/mm", 2.0, run_id="rc")
    meas(L, "tb", "time_base", 6.9, "s", 0.1, run_id="rc")


def test_insert_only(L):
    with pytest.raises(sqlite3.DatabaseError, match="insert-only"):
        L.db.execute("UPDATE record SET body='x'")
    with pytest.raises(sqlite3.DatabaseError, match="insert-only"):
        L.db.execute("DELETE FROM record")
    L.emit("c1", "campaign.started", "runtime", "Started")
    with pytest.raises(sqlite3.DatabaseError, match="insert-only"):
        L.db.execute("DELETE FROM event")


def test_admission_refuses_bad_evidence(L):
    run(L, "bad", status="tool_failure")
    with pytest.raises(AdmissionError, match="status tool_failure"):
        meas(L, "m", "growth_velocity", 1.0, "um/s", run_id="bad")
    run(L, "r1")
    with pytest.raises(AdmissionError, match="calibration"):
        meas(L, "m", "growth_velocity", 1.0, "um/s", cal=["nope"])
    with pytest.raises(AdmissionError, match="claim evidence"):
        claim(L, "k", "value_within", ["nope"], lo=0, hi=1, unit="um/s")
    with pytest.raises(AdmissionError, match="run input"):
        run(L, "r2", inputs=["sha256:missing"])


def test_blob_dedupe_and_hash(L, tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"hello")
    a = L.put_file(f, kind="text", media_type="text/plain")
    b = L.put_file(f, kind="text", media_type="text/plain")
    assert a.id == b.id and L.blob_path(a.id).read_bytes() == b"hello"


def test_velocity_without_time_base_is_unsupported(L):
    calibrated(L)
    meas(L, "v", "growth_velocity", 17.8, "um/s", 1.0, cal=["scale"])  # no time base
    claim(L, "k", "value_within", ["v"], lo=10, hi=30, unit="um/s")
    rv = gate.review(L, "k")
    assert rv.verdict == "unsupported" and "time_base" in rv.reason


def test_value_within_verdicts(L):
    calibrated(L)
    meas(L, "v", "growth_velocity", 17.8, "um/s", 1.0, cal=["scale", "tb"])
    claim(L, "in", "value_within", ["v"], lo=15, hi=20, unit="um/s")
    claim(L, "out", "value_within", ["v"], lo=40, hi=50, unit="um/s")
    claim(L, "edge", "value_within", ["v"], lo=18, hi=25, unit="um/s")
    claim(L, "units", "value_within", ["v"], lo=15, hi=20, unit="mm/s")
    assert [gate.review(L, c).verdict for c in ("in", "out", "edge", "units")] == \
        ["supported", "refuted", "inconclusive", "unsupported"]
    assert L.claim_status("in") == "supported"


def test_invalidated_calibration_makes_claim_stale(L):
    run(L, "rcal", tool="lookup_calibration")
    meas(L, "scale", "spatial_scale", 184.9, "px/mm", 2.0, run_id="rcal")
    run(L, "r1")
    run(L, "rtb", tool="lookup_calibration")
    meas(L, "tb", "time_base", 6.9, "s", 0.1, run_id="rtb")
    meas(L, "v", "growth_velocity", 17.8, "um/s", 1.0, cal=["scale", "tb"])
    claim(L, "k", "value_within", ["v"], lo=15, hi=20, unit="um/s")
    assert gate.review(L, "k").verdict == "supported"
    L.invalidate_run("rcal", "scale bar misread")
    assert L.claim_status("k") == "stale"
    assert gate.review(L, "k").verdict == "unsupported"


def test_events_coincide_and_required_corroboration(L):
    calibrated(L)
    meas(L, "short", "short_time", 400.0, "s", 7.0, cal=["tb"])
    run(L, "rv", tool="read_cell_voltage")  # the corroboration must be the instrument's own record
    meas(L, "volt", "voltage_collapse_time", 405.0, "s", 7.0, cal=["tb"], run_id="rv")
    meas(L, "far", "voltage_collapse_time", 900.0, "s", 7.0, cal=["tb"], run_id="rv")
    claim(L, "alone", "value_within", ["short"], lo=0, hi=1000, unit="s")
    claim(L, "match", "events_coincide", ["short", "volt"], tol=0.0)
    claim(L, "miss", "events_coincide", ["short", "far"], tol=0.0)
    assert gate.review(L, "alone").verdict == "unsupported"  # short time must be checked against the voltage
    assert gate.review(L, "match").verdict == "supported"
    assert gate.review(L, "miss").verdict == "refuted"


def test_trend(L):
    calibrated(L)
    t = np.arange(10, dtype=float) * 6.9
    meas(L, "t", "elapsed_time", t.tolist(), "s", 0.1, cal=["tb"])
    meas(L, "acc", "tip_speed", (5 + 0.3 * t).tolist(), "um/s", 0.5, cal=["scale", "tb"])
    meas(L, "flat", "tip_speed", (5 + 0.001 * np.sin(t)).tolist(), "um/s", 0.5, cal=["scale", "tb"])
    claim(L, "up", "trend", ["acc", "t"], direction="increasing")
    claim(L, "steady", "trend", ["flat", "t"], direction="steady", steady_tolerance=0.01)
    claim(L, "flat_up", "trend", ["flat", "t"], direction="increasing")
    assert gate.review(L, "up").verdict == "supported"
    assert gate.review(L, "steady").verdict == "supported"
    assert gate.review(L, "flat_up").verdict in ("inconclusive", "refuted")


def test_critic_never_decides(L):
    calibrated(L)
    meas(L, "v", "growth_velocity", 17.8, "um/s", 1.0, cal=["scale", "tb"])
    claim(L, "k", "value_within", ["v"], lo=40, hi=50, unit="um/s")
    from translucent.contracts import Review
    L.add(Review(id="crit", claim_id="k", reviewer="critic", verdict="supported", reason="looks fine", created_at=T))
    assert L.claim_status("k") == "proposed"
    gate.review(L, "k")
    assert L.claim_status("k") == "refuted"


def test_event_seq(L):
    seqs = [L.emit("c1", "step.started", "orchestrator", f"Step {i}").seq for i in range(5)]
    assert seqs == [1, 2, 3, 4, 5]
    assert [e.seq for e in L.events("c1", after_seq=3)] == [4, 5]


def test_malformed_predicate_is_refused_not_crashed(L):
    """Predicate parameters come from the agent; a string where a number belongs gets a verdict, not an exception."""
    calibrated(L)
    meas(L, "len", "filament_length", [1.0, 2.0, 3.0, 4.0], "um", cal=["scale"])
    meas(L, "t", "elapsed_time", [0.0, 7.0, 14.0, 21.0], "s", cal=["tb"])
    claim(L, "k", "trend", ["len", "t"], direction="steady", steady_tolerance="0.5")
    rv = gate.review(L, "k")
    assert rv.verdict == "unsupported" and "steady_tolerance must be a number" in rv.reason
    claim(L, "k2", "value_within", ["len"], lo=0, unit="um")
    assert "missing hi" in gate.review(L, "k2").reason


def test_artifact_from_failed_run_cannot_back_a_measurement(L):
    from translucent.contracts import Artifact
    L.add(Artifact(id="sha256:" + "a" * 64, kind="table", media_type="application/json", bytes=2, created_at=T))
    L.add(Run(id="seg", campaign_id="c1", step_id="s", tool="segment_images", idempotency_key="seg",
              input_ids=[], output_ids=["sha256:" + "a" * 64], status="invalid_result", started_at=T, ended_at=T))
    run(L, "r1")
    with pytest.raises(AdmissionError, match="did not succeed"):
        L.add(Measurement(id="m", run_id="r1", quantity="filament_length", value=1.0, unit="um",
                          uncertainty=Uncertainty(value=0, kind="none", method="t"), artifact_ids=["sha256:" + "a" * 64]))


def test_derived_measurement_goes_stale_with_its_input(L):
    """A sandbox analysis that read a measurement inherits its invalidation through the run's inputs."""
    run(L, "r1")
    meas(L, "v", "growth_velocity", 1.0, "um/s", 0.1)
    run(L, "an", inputs=["v"])
    meas(L, "d", "derived:rate", 2.0, "um/s", run_id="an")
    assert L.stale_reason("d") is None
    L.invalidate_run("r1", "wrong calibration")
    assert "wrong calibration" in L.stale_reason("d")
