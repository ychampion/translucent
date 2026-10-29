"""Regression tests from a review of the checker: wrong claims that the gate supported,
honest evidence it refused, and agent input that crashed it."""
import time

import pytest

from translucent import gate
from translucent.contracts import Artifact, Campaign, Claim, Measurement, Predicate, Run, Source, Uncertainty
from translucent.ledger import Ledger

T = time.time()
VID = "sha256:" + "a" * 64


@pytest.fixture
def L(tmp_path):
    led = Ledger(tmp_path / "ledger")
    led.add(Campaign(id="c1", objective="?", title="t", inputs=[], budget_usd=1.0, created_at=T))
    led.add(Artifact(id=VID, kind="video", media_type="video/mp4", bytes=1, created_at=T,
                     source=Source(uri="experiment:x")))
    return led


def run(L, rid, tool="measure", inputs=(), code_sha=None):
    return L.add(Run(id=rid, campaign_id="c1", step_id="s", tool=tool, idempotency_key=rid, input_ids=list(inputs),
                     status="ok", started_at=T, ended_at=T, code_sha=code_sha))


def meas(L, mid, q, value, unit, u=0.0, cal=(), run_id="r1", arts=(VID,), subject=None):
    return L.add(Measurement(id=mid, run_id=run_id, quantity=q, value=value, unit=unit, subject=subject,
                             uncertainty=Uncertainty(value=u, kind="ci95", method="t"), calibration_ids=list(cal),
                             artifact_ids=list(arts)))


def claim(L, cid, ptype, mids, **params):
    L.add(Claim(id=cid, campaign_id="c1", author="a", statement="s", created_at=T,
                predicate=Predicate(type=ptype, params=params, measurement_ids=mids)))
    return gate.review(L, cid)


@pytest.fixture
def W(L):
    """Recorded scale + clock + a calibrated speed and the voltage record, all from one recording."""
    run(L, "rec", tool="lookup_calibration")
    meas(L, "scale", "spatial_scale", 161.2, "px/mm", 4.0, run_id="rec")
    meas(L, "clock", "time_base", [0.0, 7.0, 14.0, 21.0], "s", 0.1, run_id="rec")
    run(L, "volt", tool="read_cell_voltage")
    meas(L, "collapse", "voltage_collapse_time", 89.55, "s", 3.45, cal=["clock"], run_id="volt")
    run(L, "r1", inputs=[VID, "scale", "clock"])
    meas(L, "v", "growth_velocity", 6.2, "um/s", 0.02, cal=["scale", "clock"])
    meas(L, "v2", "growth_velocity", 6.25, "um/s", 0.05, cal=["scale", "clock"])
    meas(L, "len", "filament_length", [1.0, 2.0, 3.0, 4.0], "um", cal=["scale"], subject="o1")
    meas(L, "t", "elapsed_time", [0.0, 7.0, 14.0, 21.0], "s", cal=["clock"], subject="o1")
    return L


def test_loose_tests_are_inconclusive_not_supported(W):
    assert claim(W, "k1", "values_agree", ["v", "v2"], tol=1e9).verdict == "inconclusive"
    assert claim(W, "k2", "value_within", ["v"], lo=-1e300, hi=1e300, unit="um/s").verdict == "inconclusive"
    assert claim(W, "k3", "value_within", ["v"], lo=6.0, hi=6.4, unit="um/s").verdict == "supported"
    assert claim(W, "k4", "value_within", ["v"], lo=40, hi=50, unit="um/s").verdict == "refuted"
    # a paid Lu campaign (2026-10-28) put a 10.2 ± 10.2 h plateau in [0, 20.42] h and had it supported
    meas(W, "plateau", "elapsed_time", 10.21, "h", 10.21, cal=["clock"], run_id="volt")
    assert claim(W, "k5", "value_within", ["plateau"], lo=0.0, hi=20.42, unit="h").verdict == "inconclusive"
    meas(W, "near0", "cell_voltage", 0.003, "V", 0.05, cal=["clock"], run_id="volt")
    assert claim(W, "k6", "value_within", ["near0"], lo=-0.1, hi=0.1, unit="V").verdict == "supported"


def test_derived_value_is_not_the_voltage_corroboration(W):
    run(W, "an", tool="run_analysis_code", inputs=["len", "t"], code_sha="c" * 64)
    meas(W, "st", "derived:short_time", 90.0, "s", 2.0, cal=["clock"], run_id="an")
    meas(W, "fake", "derived:voltage_collapse_time", 91.0, "s", 2.0, cal=["clock"], run_id="an")
    rv = claim(W, "k", "events_coincide", ["st", "fake"])
    assert rv.verdict == "unsupported" and "recorded voltage_collapse_time" in rv.reason
    assert claim(W, "k2", "events_coincide", ["st", "collapse"]).verdict == "supported"


def test_shape_rules(W):
    assert claim(W, "e", "exists", ["scale"]).verdict == "unsupported"
    assert "cited twice" in claim(W, "d", "values_agree", ["v", "v"]).reason
    meas(W, "len2", "filament_length", [2.0, 3.0, 4.0, 5.0], "um", cal=["scale"], subject="o2")
    assert "time axis" in claim(W, "tr", "trend", ["len", "len2"], direction="increasing").reason
    assert claim(W, "tr2", "trend", ["len", "t"], direction="increasing").verdict == "supported"
    # a live Meta campaign (2026-10-29) cited a length series in a range check; the reason says how to fix it
    rv = claim(W, "sc", "value_within", ["len"], lo=0, hi=9, unit="um")
    assert rv.verdict == "unsupported" and "'len' is not one" in rv.reason and "run_analysis_code" in rv.reason


def test_unknown_units_and_untraceable_values_fail_closed(W):
    run(W, "an0", tool="run_analysis_code", code_sha="c" * 64)  # no inputs at all
    meas(W, "x", "derived:speed", 45.0, "um*s^-1", 1.0, run_id="an0", arts=())
    rv = claim(W, "k", "value_within", ["x"], lo=40, hi=50, unit="um*s^-1")
    assert rv.verdict == "unsupported"
    assert {c.name for c in rv.checks if not c.passed} >= {"traced to a recording", "scale and timing recorded"}


def test_agent_input_never_crashes_or_refutes(W):
    for k, (pt, ids, prm) in enumerate([("value_within", ["len"], {"lo": 0, "hi": 9, "unit": "um"}),
                                        ("trend", ["v", "v2"], {"direction": "increasing"}),
                                        ("value_within", ["v"], {"lo": None, "hi": 9, "unit": "um/s"}),
                                        ("values_agree", ["v", "v2"], {"tol": None}),
                                        ("value_within", ["v"], {"lo": 10 ** 400, "hi": 1, "unit": "um/s"}),
                                        ("value_within", ["v"], {"lo": 7, "hi": 6, "unit": "um/s"}),
                                        ("values_agree", ["v", "v2"], {"tol": -5}),
                                        ("trend", ["len", "t"], {"direction": None})]):
        rv = claim(W, f"m{k}", pt, ids, **prm)
        assert rv.verdict in ("unsupported", "supported", "inconclusive"), (pt, prm, rv.verdict)
        assert W.claim_status(f"m{k}") != "proposed"
    assert claim(W, "lohi", "value_within", ["v"], lo=7, hi=6, unit="um/s").verdict == "unsupported"


def test_invalidated_wrong_scale_stops_blocking(W):
    run(W, "si", tool="lookup_calibration")
    meas(W, "wrong", "spatial_scale", 253.1, "px/mm", 4.0, run_id="si")
    assert "disagree" in claim(W, "k", "value_within", ["v"], lo=6.0, hi=6.4, unit="um/s").reason
    W.invalidate_run("si", "SI scale bar conflicts with the main figure")
    assert claim(W, "k2", "value_within", ["v"], lo=6.0, hi=6.4, unit="um/s").verdict == "supported"


def test_analysis_reading_a_scale_directly_is_calibrated(W):
    run(W, "an", tool="run_analysis_code", inputs=["scale", "len"], code_sha="c" * 64)
    meas(W, "dl", "derived:length", 3.0, "um", 0.1, cal=["scale"], run_id="an")  # what analysis.py now records
    assert claim(W, "k", "value_within", ["dl"], lo=2.8, hi=3.2, unit="um").verdict == "supported"

