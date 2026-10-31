"""Two-node core/surface solver. All hidden states and action forecasts are simulated.

No core temperature was measured. Continuous-trial ignition times constrain an
ensemble of Arrhenius rates, not a uniquely identified mechanism. The observed
surface trace conditions the core-state filter; it does not validate that state.
"""

from __future__ import annotations

from functools import lru_cache
import math
from statistics import median

from translucent.intervention_numeric import linear_fit, mean


def _core(surface, *, E, A, q, g, initial_offset=0.0):
    core = float(surface[0]) + initial_offset
    x = 0.0
    for measured in surface[1:]:
        rate = (
            A
            * math.exp(max(-700.0, -E / (8.314 * max(150.0, core + 273.15))))
            * (1 - x)
        )
        dx = min(1 - x, max(0.0, rate))
        x += dx
        core += g * (float(measured) - core) + q * dx
        core = min(1800.0, max(-100.0, core))
    return core, x


@lru_cache(maxsize=8)
def continuous_prior(fmt, soc):
    """Kinetic priors fitted only from the same condition's continuous heating traces."""
    from translucent import systems

    nist = systems.get("nist-heat-interrupt")
    load_dataset = nist.load_trials

    source = [
        t
        for t in load_dataset()
        if t["format"] == fmt
        and t["soc"] == soc
        and t["delay_after_vent_s"] is None
        and t["t"]
    ]
    ensemble = []
    for E in (60000.0, 90000.0):
        for q in (250.0, 600.0):
            for g in (0.003, 0.015):
                fitted = []
                for trial in source:
                    stop = int(trial["t_ignition"] or len(trial["y"]) - 1)
                    surface = trial["y"][: stop + 1 : 2]
                    # 2 s subsampling: doubled couplings/rates, temperature units unchanged.
                    lo, hi = -5.0, 35.0
                    for _ in range(24):
                        mid = (lo + hi) / 2
                        temperature, _ = _core(
                            surface, E=E, A=math.exp(mid) * 2, q=q, g=g * 2
                        )
                        if temperature >= 300:
                            hi = mid
                        else:
                            lo = mid
                    fitted.append((lo + hi) / 2)
                A = math.exp(median(fitted))
                ensemble.append(dict(E=E, A=A, q=q, g=g, surface_coupling=0.004))
    return dict(
        simulated=True,
        condition=dict(format=fmt, soc=soc),
        source_trials=[t["trial_key"] for t in source],
        source="NIST continuous-heating thermocouple traces; trace-derived first ignition; same condition",
        ensemble=ensemble,
        limit="Core state unmeasured; kinetics are non-identifiable and condition specific",
    )


def register_priors(L, cid):
    """Bind every kinetic parameter to immutable continuous-trial artifacts."""
    from translucent.contracts import Source
    from translucent.intervention import record, records
    from translucent import systems

    nist = systems.get("nist-heat-interrupt")
    load_dataset, SYSTEM = nist.load_trials, nist.metadata

    if records(L, cid, "physics_prior"):
        return
    continuous = [
        t for t in load_dataset() if t["delay_after_vent_s"] is None and t["t"]
    ]
    sources = {}
    for t in continuous:
        artifact = L.put_json(
            dict(t=t["t"], temperature_C=t["y"]),
            kind="series",
            source=Source(
                uri="doi:" + SYSTEM["doi"],
                citation=SYSTEM["citation"],
                licence=SYSTEM["licence"],
            ),
        )
        sources[t["trial_key"]] = dict(
            trial_key=t["trial_key"],
            series_artifact=artifact.id,
            t_ignition=t["t_ignition"],
            event_sources=t["event_sources"],
        )
    for fmt in ("18650", "21700"):
        for soc in (25, 50, 75, 100):
            prior = continuous_prior(fmt, soc)
            record(
                L,
                cid,
                "physics_prior",
                {
                    **prior,
                    "sources": [sources[i] for i in prior["source_trials"]],
                    "registration": "Continuous-heating reference observations available before interrupted-trial scoring",
                    "fitter": "two-node prior v1; E/q/g grid and log-A bisection to 300 C core at trace-derived ignition",
                },
            )


def _state(surface, params):
    core, x = _core(surface, E=params["E"], A=params["A"], q=params["q"], g=params["g"])
    y = [float(value) for value in surface]
    ambient = y[0]
    slope = [b - a for a, b in zip(y, y[1:])]
    # Heater absorption and surface cooling fitted on the pre-off measurements only.
    a, b = (
        linear_fit([-(value - ambient) for value in y[:-1]], slope)
        if slope
        else (0.0, 0.0)
    )
    return dict(
        core=core,
        surface=float(y[-1]),
        x=x,
        ambient=ambient,
        a=max(0.01, min(2.0, a)),
        b=max(1e-6, min(0.02, b)),
    )


def _simulate(state, params, *, horizon=600, heater_for=0):
    s = dict(state)
    times = []
    surface = [s["surface"]]
    core = [s["core"]]
    for tick in range(horizon):
        rate = (
            params["A"]
            * math.exp(
                max(-700.0, -params["E"] / (8.314 * max(150.0, s["core"] + 273.15)))
            )
            * (1 - s["x"])
        )
        dx = min(1 - s["x"], max(0.0, rate))
        s["x"] += dx
        difference = s["core"] - s["surface"]
        s["core"] += -params["g"] * difference + params["q"] * dx
        s["surface"] += (
            (s["a"] if tick < heater_for else 0)
            - s["b"] * (s["surface"] - s["ambient"])
            + params["surface_coupling"] * difference
        )
        s["core"] = min(1800.0, max(-100.0, s["core"]))
        s["surface"] = min(1800.0, max(-100.0, s["surface"]))
        surface.append(s["surface"])
        core.append(s["core"])
        if (s["core"] >= 300 or s["surface"] >= 300) and not times:
            times.append(tick + 1)
    return dict(
        ignited=bool(times),
        ignition_after_s=times[0] if times else None,
        surface=surface,
        core=core,
    )


def _summary(forecasts, watch=0):
    from translucent.intervention_bench import wilson

    successes = sum(f["ignited"] for f in forecasts)
    p = successes / len(forecasts)
    return dict(
        simulated=True,
        p_ignite=p,
        interval=wilson(successes, len(forecasts)),
        p_ignite_during_watch=sum(
            f["ignition_after_s"] is not None and f["ignition_after_s"] <= watch
            for f in forecasts
        )
        / len(forecasts),
        interval_method="Wilson spread of a nonidentified kinetic ensemble, not a calibrated confidence guarantee",
    )


@lru_cache(maxsize=512)
def _forecast_cached(t, y, fmt, soc, vent, delay):
    prior = continuous_prior(fmt, soc)
    return two_node_forecast(list(t), list(y), prior, vent=vent, delay=delay)


def cached_forecast(trace, condition, vent=None, delay=None):
    return _forecast_cached(
        tuple(trace["t"]),
        tuple(trace["temperature_C"]),
        condition["format"],
        condition["soc"],
        vent,
        delay,
    )


def two_node_forecast(t, y, prior, *, vent=None, delay=None):
    if not y:
        raise ValueError("A pre-off trace is required")
    stop = []
    states = []
    for params in prior["ensemble"]:
        state = _state(y, params)
        states.append(state)
        stop.append(_simulate(state, params))
    actions = {
        "stop_now": _summary(stop),
        "observe_30": _summary(stop, 30),
        "observe_60": _summary(stop, 60),
    }
    if delay is not None:
        if vent is None or delay < 0:
            raise ValueError(
                "A proposed heater-off delay needs a vent time and nonnegative delay"
            )
        pairs = [v for time, v in zip(t, y) if time <= vent]
        if len(pairs) < 3:
            raise ValueError("Need a trace up to vent to simulate a new delay")
        forecasts = [
            _simulate(
                _state(pairs, p), p, horizon=int(delay) + 600, heater_for=int(delay)
            )
            for p in prior["ensemble"]
        ]
        actions["heater_off_after_vent"] = {
            **_summary(forecasts),
            "heater_off_after_vent_s": delay,
            "surface_temperature_at_off_C": mean(
                [f["surface"][int(delay)] for f in forecasts]
            ),
            "surface_temperature_at_off_interval_C": [
                min(f["surface"][int(delay)] for f in forecasts),
                max(f["surface"][int(delay)] for f in forecasts),
            ],
            "limit": "Unobserved alternative action; proposed experiment only",
        }
    return dict(
        simulated=True,
        model="two-node core/surface Arrhenius",
        actions=actions,
        hidden_state=dict(
            name="core_temperature_C",
            simulated=True,
            interval=[min(s["core"] for s in states), max(s["core"] for s in states)],
        ),
        prior_sources=prior["source_trials"],
        limit=prior["limit"],
    )
