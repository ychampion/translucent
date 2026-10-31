"""Units for imported records and loop measurements: an explicit registry, no new dependency.

A unit is a product of registered symbols, written with `*`, `/` and integer powers (`m^2`, `s^-1`), read left to
right: `J/kg*K` is J*K/kg. `1` is dimensionless, so `1/h` is a C-rate.

Temperatures are the one trap. `degC` is an absolute temperature: it converts to `K` by adding 273.15, and only as
a whole value, so it may not appear inside a compound unit. A temperature difference is `delta_degC` (same size as
a kelvin, no offset), and operators that produce differences (`delta`, `slope`) declare it, so a difference is never
shifted by 273.15. Unknown units and conversions between dimensions are refused with a plain ValueError.
"""
from __future__ import annotations

import math
import numbers
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Literal

# dimension exponents over (m, kg, s, A, K)
_LENGTH, _MASS, _TIME, _CURRENT, _TEMP = ((1, 0, 0, 0, 0), (0, 1, 0, 0, 0), (0, 0, 1, 0, 0), (0, 0, 0, 1, 0),
                                          (0, 0, 0, 0, 1))
_NONE = (0, 0, 0, 0, 0)


def _dim(*terms: tuple[tuple[int, ...], int]) -> tuple[int, ...]:
    out = [0] * 5
    for base, power in terms:
        out = [o + b * power for o, b in zip(out, base)]
    return tuple(out)


_VOLT = _dim((_MASS, 1), (_LENGTH, 2), (_TIME, -3), (_CURRENT, -1))
_WATT = _dim((_MASS, 1), (_LENGTH, 2), (_TIME, -3))
_JOULE = _dim((_MASS, 1), (_LENGTH, 2), (_TIME, -2))
_OHM = _dim((_MASS, 1), (_LENGTH, 2), (_TIME, -3), (_CURRENT, -2))
_PASCAL = _dim((_MASS, 1), (_LENGTH, -1), (_TIME, -2))
_NEWTON = _dim((_MASS, 1), (_LENGTH, 1), (_TIME, -2))
_CHARGE = _dim((_CURRENT, 1), (_TIME, 1))

Kind = Literal["linear", "absolute", "delta"]

# symbol -> (dimension, scale to SI, offset to SI, kind). Only degC has an offset.
_SYMBOLS: dict[str, tuple[tuple[int, ...], Fraction, Fraction, Kind]] = {
    "1": (_NONE, Fraction(1), Fraction(0), "linear"),
    "%": (_NONE, Fraction(1, 100), Fraction(0), "linear"),
    "s": (_TIME, Fraction(1), Fraction(0), "linear"),
    "ms": (_TIME, Fraction(1, 1000), Fraction(0), "linear"),
    "min": (_TIME, Fraction(60), Fraction(0), "linear"),
    "h": (_TIME, Fraction(3600), Fraction(0), "linear"),
    "V": (_VOLT, Fraction(1), Fraction(0), "linear"),
    "mV": (_VOLT, Fraction(1, 1000), Fraction(0), "linear"),
    "A": (_CURRENT, Fraction(1), Fraction(0), "linear"),
    "mA": (_CURRENT, Fraction(1, 1000), Fraction(0), "linear"),
    "Ah": (_CHARGE, Fraction(3600), Fraction(0), "linear"),
    "mAh": (_CHARGE, Fraction(36, 10), Fraction(0), "linear"),
    "W": (_WATT, Fraction(1), Fraction(0), "linear"),
    "mW": (_WATT, Fraction(1, 1000), Fraction(0), "linear"),
    "J": (_JOULE, Fraction(1), Fraction(0), "linear"),
    "Wh": (_JOULE, Fraction(3600), Fraction(0), "linear"),
    "K": (_TEMP, Fraction(1), Fraction(0), "linear"),
    "degC": (_TEMP, Fraction(1), Fraction(27315, 100), "absolute"),
    "delta_degC": (_TEMP, Fraction(1), Fraction(0), "delta"),
    "Ohm": (_OHM, Fraction(1), Fraction(0), "linear"),
    "mOhm": (_OHM, Fraction(1, 1000), Fraction(0), "linear"),
    "m": (_LENGTH, Fraction(1), Fraction(0), "linear"),
    "mm": (_LENGTH, Fraction(1, 1000), Fraction(0), "linear"),
    "um": (_LENGTH, Fraction(1, 10**6), Fraction(0), "linear"),
    "nm": (_LENGTH, Fraction(1, 10**9), Fraction(0), "linear"),
    "kg": (_MASS, Fraction(1), Fraction(0), "linear"),
    "g": (_MASS, Fraction(1, 1000), Fraction(0), "linear"),
    "Pa": (_PASCAL, Fraction(1), Fraction(0), "linear"),
    "kPa": (_PASCAL, Fraction(1000), Fraction(0), "linear"),
    "MPa": (_PASCAL, Fraction(10**6), Fraction(0), "linear"),
    "N": (_NEWTON, Fraction(1), Fraction(0), "linear"),
}

_FACTOR = re.compile(r"([A-Za-z_%1]+)(?:\^(-?[0-9]+))?")


@dataclass(frozen=True)
class Unit:
    text: str
    dimension: tuple[int, ...]       # exponents over (m, kg, s, A, K)
    scale: Fraction                  # SI value = value * scale + offset
    offset: Fraction = Fraction(0)
    kind: Kind = "linear"            # absolute: degC; delta: delta_degC; linear: everything else


def parse(text: str) -> Unit:
    """The unit named by `text`, or ValueError naming what is wrong with it."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("a unit is required (use 1 for a dimensionless value)")
    text = text.strip()
    if text in _SYMBOLS:
        dim, scale, offset, kind = _SYMBOLS[text]
        return Unit(text, dim, scale, offset, kind)
    parts = re.split(r"([*/])", text.replace(" ", ""))
    dim, scale, op = _NONE, Fraction(1), "*"
    for i, part in enumerate(parts):
        if i % 2:
            op = part
            continue
        m = _FACTOR.fullmatch(part)
        if not m or m.group(1) not in _SYMBOLS:
            raise ValueError(f"unknown unit {part or '(empty)'!r} in {text!r}")
        symbol, power = m.group(1), int(m.group(2) or 1)
        sdim, sscale, _, skind = _SYMBOLS[symbol]
        if skind == "absolute":
            raise ValueError(f"{text}: {symbol} is an absolute temperature and cannot be part of a compound unit; "
                             f"write delta_{symbol} for a temperature difference")
        power = power if op == "*" else -power
        dim, scale = _dim((dim, 1), (sdim, power)), scale * sscale ** power
    return Unit(text, dim, scale)


def same_dimension(a: str, b: str) -> bool:
    return parse(a).dimension == parse(b).dimension


def factor(src: str, dst: str) -> tuple[Fraction, Fraction]:
    """(multiplier, addend) with dst = value * multiplier + addend, after the dimension and temperature checks."""
    s, d = parse(src), parse(dst)
    if s.dimension != d.dimension:
        raise ValueError(f"cannot convert {src} to {dst}: they measure different things")
    if {s.kind, d.kind} == {"absolute", "delta"}:
        raise ValueError(f"cannot convert {src} to {dst}: one is an absolute temperature, the other a difference")
    if "absolute" in (s.kind, d.kind) and "delta" not in (s.kind, d.kind):
        return s.scale / d.scale, (s.offset - d.offset) / d.scale
    return s.scale / d.scale, Fraction(0)


def _apply(v: float, mul: Fraction, add: Fraction) -> float:
    if not isinstance(v, numbers.Real) or isinstance(v, bool) or not math.isfinite(v):
        raise ValueError(f"cannot convert {v!r}: not a finite number")
    # one division or multiplication where possible, so mV -> V is v / 1000, not v * 0.001
    if mul.denominator == 1:
        out = v * mul.numerator
    elif mul.numerator == 1:
        out = v / mul.denominator
    else:
        out = v * mul.numerator / mul.denominator
    return float(out + float(add)) if add else float(out)


def convert(values: float | Iterable[float], src: str, dst: str) -> float | list[float]:
    """Convert one value or a sequence from `src` to `dst`. degC <-> K shifts by 273.15 (absolute values only)."""
    mul, add = factor(src, dst)
    if isinstance(values, (str, bytes)) or not hasattr(values, "__iter__"):
        return _apply(values, mul, add)
    return [_apply(v, mul, add) for v in values]


def difference(unit: str) -> str:
    """The unit of a difference of two values in `unit`: degC becomes delta_degC, others are unchanged."""
    u = parse(unit)
    return f"delta_{u.text}" if u.kind == "absolute" else u.text


def _compound(a: str, op: str, b: str) -> str:
    out = b if a == "1" and op == "*" else a if b == "1" else f"{a}{op}{b}"
    parse(out)
    return out


def derive(op: str, y_unit: str, t_unit: str = "s") -> str:
    """The output unit of an ops/1 operator applied to a series in `y_unit` over time in `t_unit`."""
    parse(y_unit)
    if parse(t_unit).dimension != _TIME:
        raise ValueError(f"{t_unit} is not a unit of time")
    if op in ("final", "initial", "max", "min", "mean", "value_at"):
        return y_unit
    if op in ("time_to_threshold", "duration_where"):
        return t_unit
    if op == "delta":
        return difference(y_unit)
    if op == "slope":
        return _compound(difference(y_unit), "/", t_unit)
    if op == "integral":
        if parse(y_unit).kind == "absolute":
            raise ValueError(f"the integral of an absolute {y_unit} has no fixed unit conversion; "
                             f"integrate a difference (delta_{y_unit}) instead")
        return _compound(y_unit, "*", t_unit)
    raise ValueError(f"unknown operator {op!r}")
