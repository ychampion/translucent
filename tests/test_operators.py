"""ops/1 operators: exact values on hand-computed series, derived units, refusals, and a brute-force reference."""
import math
import random

import numpy as np
import pytest

from translucent import operators
from translucent.loop_contracts import OperatorSpec

T = [0.0, 10.0, 20.0, 30.0, 40.0]
Y = [1.0, 3.0, 2.0, 6.0, 4.0]


def ev(op, t=T, y=Y, y_unit="V", unit=None, **kw):
    spec = OperatorSpec(op=op, channel="voltage", unit=unit or operators.derive_unit(op, y_unit), **kw)
    return operators.evaluate(spec, t, y, y_unit)


@pytest.mark.parametrize("op, kw, want", [
    ("final", {}, 4.0),
    ("initial", {}, 1.0),
    ("max", {}, 6.0),
    ("min", {}, 1.0),
    # trapezoid 20 + 25 + 40 + 50 = 135 over 40 s
    ("mean", {}, 135 / 40),
    ("integral", {}, 135.0),
    ("delta", {}, 3.0),
    ("value_at", {"at_s": 25.0}, 4.0),
    ("value_at", {"at_s": 30.0}, 6.0),
    # first rise through 2.5: between (0, 1) and (10, 3) -> 0 + 1.5 * 10 / 2
    ("time_to_threshold", {"threshold": 2.5, "direction": "rising"}, 7.5),
    # rises through 5 between (20, 2) and (30, 6): 20 + 3 * 10 / 4
    ("time_to_threshold", {"threshold": 5.0, "direction": "rising", "window": (15.0, 40.0)}, 27.5),
    # least squares: t mean 20, y mean 3.2; sum (t - 20)(y - 3.2) = 44 + 2 + 0 + 28 + 16 = 90, sum (t - 20)^2 = 1000
    ("slope", {}, 0.09),
    ("max", {"window": (5.0, 25.0)}, 3.0),
    # at or above 2.5: (0,1)-(10,3) above from 7.5 -> 2.5 s; (10,3)-(20,2) above until 15 -> 5 s;
    # (20,2)-(30,6) above from 21.25 -> 8.75 s; (30,6)-(40,4) all 10 s. Total 26.25 s
    ("duration_where", {"threshold": 2.5, "direction": "rising"}, 26.25),
    ("duration_where", {"threshold": 2.5, "direction": "falling"}, 40 - 26.25),
])
def test_hand_computed_values_are_exact(op, kw, want):
    assert ev(op, **kw)["value"] == want


def test_threshold_crossing_reports_its_time_resolution():
    out = ev("time_to_threshold", threshold=2.5, direction="rising")
    assert out["half_interval_s"] == 5.0 and out["unit"] == "s" and out["samples"] == 5


@pytest.mark.parametrize("op, y_unit, want", [
    ("final", "degC", "degC"), ("delta", "degC", "delta_degC"), ("slope", "degC", "delta_degC/s"),
    ("integral", "A", "A*s"), ("time_to_threshold", "V", "s"), ("duration_where", "mV", "s"), ("slope", "mV", "mV/s"),
])
def test_derived_units(op, y_unit, want):
    assert operators.derive_unit(op, y_unit) == want


def test_results_convert_to_the_declared_unit():
    # 2 A for 1800 s is 1 Ah; a slope of 1 degC per second is 60 K per minute; 7.5 s is 0.125 min
    assert ev("integral", [0.0, 1800.0], [2.0, 2.0], "A", unit="Ah")["value"] == 1.0
    out = ev("slope", [0.0, 1.0, 2.0], [25.0, 26.0, 27.0], "degC", unit="K/min")
    assert out["value"] == 60.0 and out["computed_unit"] == "delta_degC/s" and out["output_conversion"] == ["60", "0"]
    assert ev("delta", [0.0, 1.0], [25.0, 27.5], "degC", unit="K")["value"] == 2.5
    assert ev("time_to_threshold", threshold=2.5, direction="rising", unit="min")["value"] == 0.125


def test_the_input_unit_is_converted_first_and_thresholds_read_in_it():
    spec = OperatorSpec(op="time_to_threshold", channel="voltage", threshold=2.5, direction="rising", unit="s")
    out = operators.evaluate(spec, T, [1000.0, 3000.0, 2000.0, 6000.0, 4000.0], "mV", input_unit="V")
    assert out["value"] == 7.5 and out["input_conversion"] == ["1/1000", "0"]
    spec = OperatorSpec(op="max", channel="temperature", unit="K")
    assert operators.evaluate(spec, [0.0, 1.0], [25.0, 26.85], "degC")["value"] == 26.85 + 273.15


@pytest.mark.parametrize("op, y_unit, unit, kw, words", [
    ("integral", "degC", "degC*s", {}, "absolute"),
    ("delta", "degC", "degC", {}, "absolute temperature"),
    ("time_to_threshold", "V", "V", {"threshold": 1.0, "direction": "rising"}, "different things"),
    ("time_to_threshold", "V", "s", {"threshold": 1.0}, "needs direction"),
    ("value_at", "V", "V", {}, "needs at_s"),
    ("max", "V", "V", {"threshold": 1.0}, "does not use threshold"),
    ("value_at", "V", "V", {"at_s": 1.0, "window": (0.0, 2.0)}, "does not use window"),
    ("max", "V", "V", {"window": (5.0, 1.0)}, "starts after it ends"),
    ("final", "furlong", "furlong", {}, "unknown unit"),
])
def test_specs_that_do_not_fit_are_refused(op, y_unit, unit, kw, words):
    spec = OperatorSpec(op=op, channel="x", unit=unit, **kw)
    with pytest.raises(ValueError, match=words):
        operators.evaluate(spec, T, Y, y_unit)


@pytest.mark.parametrize("op, kw, words", [
    ("time_to_threshold", {"threshold": 0.5, "direction": "rising"}, "already at or above"),
    ("time_to_threshold", {"threshold": 9.0, "direction": "rising"}, "never rises"),
    ("time_to_threshold", {"threshold": 0.5, "direction": "falling"}, "never falls"),
    ("value_at", {"at_s": 41.0}, "outside"),
    ("max", {"window": (41.0, 50.0)}, "has 0"),
    ("slope", {"window": (10.0, 10.0)}, "at least 2 samples"),
])
def test_values_that_do_not_exist_are_refused(op, kw, words):
    with pytest.raises(ValueError, match=words):
        ev(op, **kw)


def test_repeated_times_are_a_step_not_an_error():
    # a cycler logs a step change twice at t = 10
    t, y = [0.0, 10.0, 10.0, 20.0], [1.0, 1.0, 3.0, 3.0]
    assert ev("value_at", t, y, at_s=10.0)["value"] == 3.0
    assert ev("integral", t, y)["value"] == 40.0
    assert ev("time_to_threshold", t, y, threshold=2.0, direction="rising")["value"] == 10.0
    assert ev("duration_where", t, y, threshold=2.0, direction="rising")["value"] == 10.0
    with pytest.raises(ValueError, match="needs time to pass"):
        ev("mean", [5.0, 5.0], [1.0, 2.0])
    with pytest.raises(ValueError, match="go back"):
        ev("final", [0.0, 2.0, 1.0], [1.0, 1.0, 1.0])


def _reference(op, t, y, thr):
    """Brute force on a dense resampling of the piecewise-linear series."""
    if op == "integral":
        return float(np.trapezoid(y, t))
    if op == "slope":
        return float(np.polyfit(t, y, 1)[0])
    dense = np.linspace(t[0], t[-1], 400_001)
    v = np.interp(dense, t, y)
    if op == "duration_where":
        return float((v >= thr).mean() * (t[-1] - t[0]))
    above = np.flatnonzero(v >= thr)
    return float(dense[above[0]])


@pytest.mark.parametrize("seed", range(25))
def test_matches_a_brute_force_reference_on_random_series(seed):
    rng = random.Random(seed)
    n = rng.randint(3, 40)
    t = sorted(rng.uniform(0, 100) for _ in range(n))
    t[0] -= 1  # strictly increasing so np.interp is defined
    y = [rng.uniform(-5, 5) for _ in range(n)]
    span = t[-1] - t[0]
    assert ev("integral", t, y)["value"] == pytest.approx(_reference("integral", t, y, 0), abs=1e-9)
    assert ev("slope", t, y)["value"] == pytest.approx(_reference("slope", t, y, 0), rel=1e-9, abs=1e-12)
    thr = rng.uniform(-3, 3)
    got = ev("duration_where", t, y, threshold=thr, direction="rising")["value"]
    assert got == pytest.approx(_reference("duration_where", t, y, thr), abs=span * 1e-4)
    if y[0] < thr and max(y) >= thr:
        got = ev("time_to_threshold", t, y, threshold=thr, direction="rising")["value"]
        assert got == pytest.approx(_reference("time_to_threshold", t, y, thr), abs=span * 1e-5)
    # the same input gives the same bits (verify recomputes these)
    assert ev("mean", t, y)["value"] == ev("mean", list(t), list(y))["value"]


def test_every_operator_is_defined_for_the_contract():
    ops = set(OperatorSpec.model_fields["op"].annotation.__args__)
    for op in ops:
        kw = {"at_s": 5.0} if op == "value_at" else \
            {"threshold": 2.5, "direction": "rising"} if op in ("time_to_threshold", "duration_where") else {}
        assert math.isfinite(ev(op, **kw)["value"])
