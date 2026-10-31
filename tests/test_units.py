import math

import pytest

from translucent import units


@pytest.mark.parametrize("value, src, dst, want", [
    (1500, "mV", "V", 1.5),
    (0.25, "V", "mV", 250.0),
    (2, "min", "s", 120.0),
    (1.5, "h", "s", 5400.0),
    (250, "ms", "s", 0.25),
    (800, "mA", "A", 0.8),
    (2500, "mAh", "Ah", 2.5),
    (1, "Wh", "J", 3600.0),
    (12, "mOhm", "Ohm", 0.012),
    (3, "um", "nm", 3000.0),
    (500, "g", "kg", 0.5),
    (0.2, "MPa", "kPa", 200.0),
    (50, "%", "1", 0.5),
    (2, "1/h", "1/s", 2 / 3600),
    (1, "1/h", "h^-1", 1.0),
    (3, "mV/s", "V/min", 0.18),
    (2, "A*h", "Ah", 2.0),
    (1, "kg*m/s^2", "N", 1.0),
    (1, "V*A", "W", 1.0),
])
def test_conversion_table(value, src, dst, want):
    assert units.convert(value, src, dst) == pytest.approx(want, rel=1e-12)
    assert units.same_dimension(src, dst)


def test_decimal_scales_divide_instead_of_multiplying_by_a_rounded_factor():
    assert units.convert(1500, "mV", "V") == 1.5
    assert units.convert([0.3, 0.7], "V", "V") == [0.3, 0.7]
    assert units.convert(1, "mV", "V") == 1 / 1000


def test_sequences_convert_elementwise():
    assert units.convert([1, 2.5], "min", "s") == [60.0, 150.0]
    assert units.convert((0.0, 100.0), "degC", "K") == [273.15, 373.15]


def test_degc_to_kelvin_is_affine_for_absolute_values():
    assert units.convert(25, "degC", "K") == pytest.approx(298.15)
    assert units.convert(298.15, "K", "degC") == pytest.approx(25.0)
    assert units.convert(-40, "degC", "degC") == -40.0


def test_temperature_differences_are_never_shifted():
    assert units.convert(3, "delta_degC", "K") == 3.0
    assert units.convert(3, "K", "delta_degC") == 3.0
    with pytest.raises(ValueError, match="absolute temperature, the other a difference"):
        units.convert(3, "degC", "delta_degC")
    with pytest.raises(ValueError, match="absolute temperature, the other a difference"):
        units.convert(3, "delta_degC", "degC")


def test_absolute_degc_is_refused_inside_compound_units():
    with pytest.raises(ValueError, match="write delta_degC"):
        units.parse("degC/s")
    assert units.convert(0.5, "delta_degC/s", "delta_degC/min") == pytest.approx(30.0)


@pytest.mark.parametrize("bad", ["degF", "furlong", "", "  ", "V/", "*V", "m^x", "Volt"])
def test_unknown_units_are_refused(bad):
    with pytest.raises(ValueError):
        units.parse(bad)
    with pytest.raises(ValueError):
        units.convert(1, bad, "V")


@pytest.mark.parametrize("src, dst", [("V", "s"), ("mA", "Ah"), ("1/h", "1"), ("degC", "s"), ("Pa", "N"),
                                      ("%", "s"), ("W", "Wh")])
def test_cross_dimension_conversions_are_refused(src, dst):
    assert not units.same_dimension(src, dst)
    with pytest.raises(ValueError, match="measure different things"):
        units.convert(1, src, dst)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, "1.0", None, True])
def test_non_finite_or_non_numeric_values_are_refused(value):
    with pytest.raises(ValueError):
        units.convert(value, "V", "mV")


@pytest.mark.parametrize("op, y, t, want", [
    ("final", "V", "s", "V"), ("max", "degC", "s", "degC"), ("mean", "mA", "s", "mA"),
    ("value_at", "degC", "s", "degC"), ("initial", "%", "s", "%"),
    ("time_to_threshold", "V", "s", "s"), ("duration_where", "degC", "min", "min"),
    ("delta", "degC", "s", "delta_degC"), ("delta", "V", "s", "V"),
    ("slope", "degC", "s", "delta_degC/s"), ("slope", "V", "min", "V/min"),
    ("integral", "A", "h", "A*h"), ("integral", "1", "s", "s"),
])
def test_operator_output_units(op, y, t, want):
    assert units.derive(op, y, t) == want
    units.parse(want)


def test_derive_refusals():
    with pytest.raises(ValueError, match="integrate a difference"):
        units.derive("integral", "degC", "s")
    with pytest.raises(ValueError, match="not a unit of time"):
        units.derive("slope", "V", "m")
    with pytest.raises(ValueError, match="unknown operator"):
        units.derive("median", "V", "s")
    with pytest.raises(ValueError):
        units.derive("max", "degF", "s")


def test_derived_integral_converts_to_the_registry_unit():
    assert units.convert(2, units.derive("integral", "A", "h"), "Ah") == 2.0
    assert units.convert(1, units.derive("integral", "mA", "s"), "mAh") == pytest.approx(1 / 3600)
