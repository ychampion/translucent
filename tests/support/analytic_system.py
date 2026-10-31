"""An analytic computational system for tests: a lumped cell charged at constant current.

Two parameters (charge current, ambient temperature), three quantities (a
temperature series, its maximum and the time to reach 80 % charge). Closed-form,
deterministic, and well under 50 ms, so slices can test the loop without a real
solver or domain.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from translucent.systems.base import (
    ActionSpec,
    Cost,
    ExecutionOutput,
    Parameter,
    Quantity,
    ResultTable,
    SystemSpec,
    check_params,
    file_digest,
)

CAPACITY_AH = 5.0
RESISTANCE_OHM = 0.02
HEAT_CAPACITY_J_K = 80.0
CONDUCTANCE_W_K = 0.5
STEP_S = 10.0

SPEC = SystemSpec(
    id="analytic-cell",
    version="1",
    title="Analytic lumped cell, constant-current charge",
    description="Closed-form Joule heating and charge time; a test system with no real solver.",
    quantities=(
        Quantity("temperature", "degC", kind="series", label="Cell temperature"),
        Quantity("max_temperature", "degC", label="Peak temperature", tolerance=1e-9),
        Quantity("charge_time_80", "s", label="Time to 80 % charge", tolerance=1e-9),
    ),
    actions=(
        ActionSpec(
            name="simulate_charge",
            label="Simulate a constant-current charge",
            executor="computational",
            parameters=(
                Parameter("current_A", "A", lo=0.5, hi=10.0, label="Charge current"),
                Parameter("ambient_C", "degC", lo=-10.0, hi=45.0, label="Ambient"),
            ),
            produces=("temperature", "max_temperature", "charge_time_80"),
            deterministic=True,
            destructive=False,
            max_wall_s=5.0,
        ),
    ),
    solver={
        "name": "closed-form",
        "version": "1",
        "model": "lumped RC thermal, ohmic heating",
        "validity": "test system only",
    },
)


def simulate(
    current_A: float, ambient_C: float
) -> tuple[list[float], list[float], float]:
    """Temperature rises toward ambient + I^2 R / G with time constant C / G."""
    charge_time = 0.8 * CAPACITY_AH * 3600 / current_A
    rise = current_A**2 * RESISTANCE_OHM / CONDUCTANCE_W_K
    tau = HEAT_CAPACITY_J_K / CONDUCTANCE_W_K
    n = math.ceil(charge_time / STEP_S)
    t = [min(i * STEP_S, charge_time) for i in range(n + 1)]
    y = [ambient_C + rise * (1 - math.exp(-x / tau)) for x in t]
    return t, y, charge_time


class SweepBaseline:
    """Deterministic one-factor sweep of the current at 25 degC."""

    name = "current-sweep"
    grid = (1.0, 2.0, 4.0, 6.0, 8.0)

    def propose(self, history, remaining):
        done = {
            h["params"]["current_A"]
            for h in history
            if h.get("action") == "simulate_charge"
        }
        return [
            ("simulate_charge", {"current_A": c, "ambient_C": 25.0})
            for c in self.grid
            if c not in done
        ][: max(remaining, 0)]


class AnalyticSystem:
    spec = SPEC

    def code_digest(self) -> str:
        return file_digest(Path(__file__))

    def check_params(self, action: str, params: dict[str, Any]) -> list[str]:
        return check_params(self.spec, action, params)

    def configuration(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": action,
            **{
                p.name: float(params[p.name])
                for p in self.spec.action(action).parameters
            },
        }

    def estimate_cost(self, action: str, params: dict[str, Any]) -> Cost:
        return Cost(wall_s=0.01)

    def execute(
        self, action: str, params: dict[str, Any], workdir: Path
    ) -> ExecutionOutput:
        problems = self.check_params(action, params)
        if problems:
            raise ValueError("; ".join(problems))
        t, y, charge_time = simulate(params["current_A"], params["ambient_C"])
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        log = workdir / "solver.json"
        log.write_text(json.dumps({"params": params, "points": len(t)}))
        return ExecutionOutput(
            table=ResultTable(
                time_unit="s",
                t=tuple(t),
                series={"temperature": ("degC", tuple(y))},
                scalars={
                    "max_temperature": (max(y), "degC"),
                    "charge_time_80": (charge_time, "s"),
                },
            ),
            files={"solver.json": log},
            solver=dict(self.spec.solver, step_s=str(STEP_S)),
            executed={k: float(params[k]) for k in ("current_A", "ambient_C")},
        )

    def check_result(
        self, action: str, params: dict[str, Any], table: ResultTable
    ) -> list[str]:
        problems = []
        series = table.series.get("temperature")
        peak = table.scalars.get("max_temperature")
        charge = table.scalars.get("charge_time_80")
        if series and peak and abs(max(series[1]) - peak[0]) > 1e-9:
            problems.append("max_temperature differs from the temperature series")
        if charge and table.t and table.t[-1] < charge[0]:
            problems.append("time does not cover the charge")
        return problems

    def procedure(self, action: str, params: dict[str, Any]) -> str | None:
        return None

    def baseline_policy(self) -> SweepBaseline:
        return SweepBaseline()

    def evidence_problems(self, ledger, campaign_id: str) -> list[str]:
        return []
