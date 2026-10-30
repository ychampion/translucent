"""Executable fits are independent of native linear-algebra CPU dispatch."""

import json
import math
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest


def test_closed_form_features_and_fixed_newton_fit_do_not_call_lapack(monkeypatch):
    from translucent.intervention_models import (
        compute_features,
        fit,
        predict,
    )
    from translucent.intervention_numeric import linear_fit, quadratic_coefficient
    from translucent.intervention_physics import _state

    def forbidden(*args, **kwargs):
        pytest.fail("An executable intervention fit called a BLAS/LAPACK fitter")

    monkeypatch.setattr(np, "polyfit", forbidden)
    monkeypatch.setattr(np.linalg, "solve", forbidden)
    monkeypatch.setattr(np.linalg, "lstsq", forbidden)
    times = list(range(101))
    values = [20 + 0.2 * t + 0.003 * t * t for t in times]
    features = compute_features(
        times,
        values,
        80,
        dict(format="18650", soc=50, delay_after_vent_s=20, t_vent=60),
    )
    assert features["level"] == values[80]
    assert linear_fit(times, [2 + 3 * t for t in times]) == pytest.approx((2, 3))
    assert quadratic_coefficient(times, values) == pytest.approx(0.003, abs=1e-14)
    rows = [
        dict(
            id=f"Exp{i}",
            condition=dict(format="18650", soc=50),
            features=dict(level=float(i), soc=float((i * 7) % 11)),
            y=int(i % 3 != 0),
        )
        for i in range(12)
    ]
    model = fit(
        dict(family="logistic", features=["level", "soc"], regularization=0.75),
        rows,
        seed=17,
    )
    assert model["numerics"]["bootstrap_generator"] == "PCG64"
    assert model["numerics"]["bootstrap_seed"] == 17
    assert model["numerics"]["logistic_solver"] == "fixed-16-IRLS-Cholesky"
    assert 0 < predict(model, rows[0])[0] < 1
    state = _state(values, dict(E=60000, A=0.1, q=250, g=0.003))
    assert all(math.isfinite(value) for value in state.values())


KERNEL_PROGRAM = r"""
import json
from translucent.intervention_models import compute_features,fit,bootstrap_predict,thermal_forecast
from translucent.intervention_actions import post_features
from translucent.intervention_physics import _state
rows=[]
times=list(range(101))
for i in range(24):
    values=[20+i*.2+(.15+i*.002)*t+.0003*t*t+((i*7+t)%11)*.001 for t in times]
    meta=dict(format='21700' if i%2 else '18650',soc=25*(1+i%4),delay_after_vent_s=30,t_vent=30)
    features=compute_features(times,values,60,meta)
    rows.append(dict(id=f'Exp{i}',condition={k:meta[k] for k in ('format','soc')},features=features,y=int((i*7)%13>5)))
model=fit(dict(family='logistic',features=['format','level','slope_30','rise_60']),rows,seed=17)
trace=dict(t=times,temperature_C=values)
result=dict(model=model,features=rows[0]['features'],
    bootstrap=bootstrap_predict(model,rows,rows[0],replicates=8),
    post=post_features(trace,60,30),
    thermal=thermal_forecast(times,values,horizon=20),
    state=_state(values,dict(E=60000,A=.1,q=250,g=.003)))
print(json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False))
"""


def test_fit_bytes_match_between_blas_kernels_and_thread_counts():
    root = Path(__file__).resolve().parents[1]
    outputs = []
    for core, threads, disabled in (
        ("Haswell", "1", "X86_V3,X86_V4"),
        ("Prescott", "4", ""),
    ):
        environment = dict(
            os.environ, OPENBLAS_CORETYPE=core, OPENBLAS_NUM_THREADS=threads
        )
        environment["NPY_DISABLE_CPU_FEATURES"] = disabled
        completed = subprocess.run(
            [sys.executable, "-c", KERNEL_PROGRAM],
            cwd=root,
            env=environment,
            capture_output=True,
            check=True,
            timeout=60,
        )
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]
    value = json.loads(outputs[0])
    assert value["model"]["numerics"]["bootstrap_seed"] == 17
    assert value["thermal"]["simulated"] is True


def test_pure_fit_and_forecast_caches_do_not_share_mutable_results():
    from translucent.intervention_models import fit, thermal_forecast

    rows = [
        dict(
            id=f"Exp{i}",
            condition=dict(format="18650", soc=50),
            features=dict(level=float(i)),
            y=i % 2,
        )
        for i in range(6)
    ]
    spec = dict(family="logistic", features=["level"])
    original = fit(spec, rows)
    expected = json.loads(json.dumps(original))
    original["models"]["pooled"]["w"][0] = 12345
    assert fit(spec, rows) == expected
    times = list(range(20))
    values = [20 + 0.2 * t for t in times]
    original = thermal_forecast(times, values, horizon=5)
    expected = json.loads(json.dumps(original))
    original["temperature_C"][0] = 12345
    original["parameters"]["a"] = 12345
    assert thermal_forecast(times, values, horizon=5) == expected
