"""ops/1: the measurement operators a loop campaign applies to a recorded or computed series. Pure Python and
deterministic, so verify recomputes every value bit for bit: sums use math.fsum and nothing depends on the platform.

A series is (t, y): t in record seconds, never decreasing (a time may repeat, e.g. a cycler logging a step change
twice at one instant); y in its source unit. `evaluate` converts y to the input unit first (the adapter's canonical
unit for the quantity, or the series' own unit), so a threshold is read in that unit, then applies the operator and
converts the result to the declared unit. Both conversions are returned with the value.

Definitions (a window [lo, hi] keeps the samples with lo <= t <= hi; nothing is interpolated at its edges):
  final, initial    y of the last / first sample (the last row of a repeated time is the later one)
  max, min          largest / smallest y
  mean              time-weighted mean: the trapezoid integral divided by the time spanned (cycler logs change their
                    sampling rate with the step, so an unweighted mean would follow the logging rate)
  value_at          y at at_s, linear between the samples either side; at a repeated time, the last row's value
  time_to_threshold record time at which y first crosses `threshold` in `direction`, linear between the two samples
                    either side; refused when the series already starts past the threshold
  delta             last y minus first y, as a difference (degC gives delta_degC)
  slope             least-squares slope of y against t, as a difference per unit time
  integral          trapezoid integral of y over t (the integral of an absolute degC is refused by units.derive)
  duration_where    total time with y at or above the threshold ("rising") or at or below it ("falling"), the
                    series read as linear between samples
"""
from __future__ import annotations

import math

from translucent import units
from translucent.loop_contracts import OperatorSpec

VERSION = "ops/1"

_VALUE = ("final", "initial", "max", "min", "mean", "value_at")
# op -> (parameters it requires, parameters it accepts besides those)
_PARAMS = {
    "final": ((), ("window",)), "initial": ((), ("window",)), "max": ((), ("window",)), "min": ((), ("window",)),
    "mean": ((), ("window",)), "delta": ((), ("window",)), "slope": ((), ("window",)),
    "integral": ((), ("window",)), "value_at": (("at_s",), ()),
    "time_to_threshold": (("threshold", "direction"), ("window",)),
    "duration_where": (("threshold", "direction"), ("window",)),
}
# fewest samples each operator needs (mean, slope, integral and the time operators also need time to pass)
_MIN_SAMPLES = {"final": 1, "initial": 1, "max": 1, "min": 1, "value_at": 1}


def derive_unit(op: str, y_unit: str, t_unit: str = "s") -> str:
    """The unit an operator's result has, computed from a series in `y_unit` over time in `t_unit`."""
    return units.derive(op, y_unit, t_unit)


def check(spec: OperatorSpec, input_unit: str) -> str:
    """The unit the operator computes in for a series in `input_unit`; ValueError when the spec's parameters do not
    fit its operator or its declared unit cannot be reached from that unit."""
    required, optional = _PARAMS[spec.op]
    given = [p for p in ("window", "threshold", "direction", "at_s") if getattr(spec, p) is not None]
    if missing := [p for p in required if p not in given]:
        raise ValueError(f"{spec.op} needs {' and '.join(missing)}")
    if extra := [p for p in given if p not in required + optional]:
        raise ValueError(f"{spec.op} does not use {' or '.join(extra)}; remove it")
    if spec.window is not None and spec.window[0] > spec.window[1]:
        raise ValueError(f"window {list(spec.window)} s starts after it ends")
    natural = derive_unit(spec.op, input_unit)
    try:
        units.factor(natural, spec.unit)
    except ValueError as e:
        raise ValueError(f"{spec.op} of {spec.channel} in {input_unit} gives {natural}, and {e}") from None
    return natural


def evaluate(spec: OperatorSpec, t: list[float], y: list[float], y_unit: str, *,
             input_unit: str | None = None) -> dict:
    """Apply `spec` to the series (t, y in y_unit). Returns {"value", "unit", ...} with the conversions made, the
    samples used and, for a threshold crossing, half the sample interval around it (its time resolution)."""
    input_unit = input_unit or y_unit
    natural = check(spec, input_unit)
    if len(t) != len(y) or not t:
        raise ValueError(f"the {spec.channel} series has {len(t)} times and {len(y)} values")
    if not all(math.isfinite(v) for v in (*t, *y)):
        raise ValueError(f"the {spec.channel} series has a value that is not a finite number")
    if any(b < a for a, b in zip(t, t[1:])):
        raise ValueError(f"the {spec.channel} series has times that go back")
    y = units.convert(y, y_unit, input_unit) if input_unit != y_unit else list(y)
    lo, hi = spec.window if spec.window is not None else (t[0], t[-1])
    first = next((i for i, x in enumerate(t) if x >= lo), len(t))
    last = next((i for i in range(len(t) - 1, -1, -1) if t[i] <= hi), -1)
    ts, ys = t[first:last + 1], y[first:last + 1]
    need = _MIN_SAMPLES.get(spec.op, 2)
    if len(ts) < need:
        raise ValueError(f"{spec.op} needs at least {need} samples; the window [{lo:g}, {hi:g}] s has {len(ts)} "
                         f"(the {spec.channel} series covers [{t[0]:g}, {t[-1]:g}] s)")
    if spec.op in ("mean", "slope", "integral", "time_to_threshold", "duration_where") and ts[-1] == ts[0]:
        raise ValueError(f"{spec.op} needs time to pass; every sample in the window is at {ts[0]:g} s")
    out: dict = {}
    value = _OPS[spec.op](spec, ts, ys, out)
    mul, add = units.factor(natural, spec.unit)
    result = units.convert(value, natural, spec.unit) if (mul, add) != (1, 0) else float(value)
    return {"op": spec.op, "operators": VERSION, "value": result, "unit": spec.unit, "computed_unit": natural,
            "input_unit": input_unit, "input_conversion": _factor(y_unit, input_unit),
            "output_conversion": [str(mul), str(add)], "samples": len(ts), "t_range": [ts[0], ts[-1]], **out}


def _factor(src: str, dst: str) -> list[str]:
    mul, add = units.factor(src, dst)
    return [str(mul), str(add)]


def _integral(t: list[float], y: list[float]) -> float:
    return math.fsum((t[i + 1] - t[i]) * (y[i] + y[i + 1]) / 2 for i in range(len(t) - 1))


def _value_at(spec, t, y, out):
    x = spec.at_s
    if not t[0] <= x <= t[-1]:
        raise ValueError(f"value_at {x:g} s is outside the {spec.channel} series, which covers [{t[0]:g}, {t[-1]:g}] s")
    exact = [i for i, v in enumerate(t) if v == x]
    if exact:
        return y[exact[-1]]
    i = next(i for i, v in enumerate(t) if v > x)
    return y[i - 1] + (x - t[i - 1]) * (y[i] - y[i - 1]) / (t[i] - t[i - 1])


def _time_to_threshold(spec, t, y, out):
    thr, rising = spec.threshold, spec.direction == "rising"
    past = (lambda v: v >= thr) if rising else (lambda v: v <= thr)
    if past(y[0]):
        raise ValueError(f"the {spec.channel} series is already {'at or above' if rising else 'at or below'} "
                         f"{thr:g} at {t[0]:g} s, so it never {'rises' if rising else 'falls'} to it in this window")
    for i in range(1, len(t)):
        if past(y[i]):
            out["half_interval_s"] = (t[i] - t[i - 1]) / 2
            return t[i - 1] + (thr - y[i - 1]) * (t[i] - t[i - 1]) / (y[i] - y[i - 1])
    raise ValueError(f"the {spec.channel} series never {'rises' if rising else 'falls'} to {thr:g} "
                     f"between {t[0]:g} and {t[-1]:g} s")


def _slope(spec, t, y, out):
    n = len(t)
    tm, ym = math.fsum(t) / n, math.fsum(y) / n
    return math.fsum((a - tm) * (b - ym) for a, b in zip(t, y)) / math.fsum((a - tm) ** 2 for a in t)


def _duration_where(spec, t, y, out):
    # "rising": time at or above the threshold; "falling": at or below. a >= 0 marks a sample inside.
    sign = 1 if spec.direction == "rising" else -1
    parts = []
    for i in range(len(t) - 1):
        a, b, dt = sign * (y[i] - spec.threshold), sign * (y[i + 1] - spec.threshold), t[i + 1] - t[i]
        if a >= 0 and b >= 0:
            parts.append(dt)
        elif a >= 0 > b:
            parts.append(dt * (a / (a - b)))
        elif b >= 0 > a:
            parts.append(dt * (b / (b - a)))
    return math.fsum(parts)


_OPS = {
    "final": lambda s, t, y, o: y[-1],
    "initial": lambda s, t, y, o: y[0],
    "max": lambda s, t, y, o: max(y),
    "min": lambda s, t, y, o: min(y),
    "mean": lambda s, t, y, o: _integral(t, y) / (t[-1] - t[0]),
    "value_at": _value_at,
    "time_to_threshold": _time_to_threshold,
    "delta": lambda s, t, y, o: y[-1] - y[0],
    "slope": _slope,
    "integral": lambda s, t, y, o: _integral(t, y),
    "duration_where": _duration_where,
}
assert set(_OPS) == set(_PARAMS) == set(OperatorSpec.model_fields["op"].annotation.__args__)
