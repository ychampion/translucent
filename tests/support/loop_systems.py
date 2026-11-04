"""Loop test systems on top of the analytic cell (support.analytic_system), for the S4a lifecycle tests.

`LoopCell` adds actions the lifecycle needs and the analytic cell lacks:
  hold_charge      deterministic; writes started.txt (its pid) in the work folder, then waits while the file named
                   by TRANSLUCENT_TEST_HOLD exists, so a test can kill a run mid-execution
  hold_charge_nd   the same, not deterministic: an abandoned run is not re-run
  mislabelled_charge  returns its temperature series in volts, so the solver's output is refused
  stuck_charge     sleeps past its 0.5 s wall-time limit
  cell_test        a human, destructive physical test: requested, then returned by a person; never executed
`LabelledCell` is a separate system whose one action also returns a category (run_status): "ok", or an undeclared
"garbled" at 9 A or more, so the category check has something to refuse.
The child process imports this module by name (support.loop_systems) and registers LoopCell itself.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from support.analytic_system import SPEC, AnalyticSystem

from translucent import systems
from translucent.systems.base import ActionSpec, NotExecutable, Parameter, Quantity, SystemSpec, file_digest

HOLD_ENV = "TRANSLUCENT_TEST_HOLD"
_PARAMS = SPEC.action("simulate_charge").parameters
_PRODUCES = ("temperature", "max_temperature", "charge_time_80")

LOOP_SPEC = SystemSpec(
    id="loop-test-cell", version="1", title="Analytic cell with lifecycle test actions",
    description="The analytic lumped cell plus slow, non-deterministic, mislabelled and human actions.",
    quantities=SPEC.quantities,
    actions=SPEC.actions + (
        ActionSpec("hold_charge", "Simulate a charge that waits for the test", "computational", _PARAMS, _PRODUCES,
                   deterministic=True, destructive=False, max_wall_s=120.0),
        ActionSpec("hold_charge_nd", "Simulate a non-repeatable charge", "computational", _PARAMS, _PRODUCES,
                   deterministic=False, destructive=False, max_wall_s=120.0),
        ActionSpec("mislabelled_charge", "Simulate a charge with a unit bug", "computational", _PARAMS, _PRODUCES,
                   deterministic=True, destructive=False, max_wall_s=30.0),
        ActionSpec("stuck_charge", "Simulate a charge that hangs", "computational", _PARAMS, _PRODUCES,
                   deterministic=True, destructive=False, max_wall_s=0.5),
        ActionSpec("cell_test", "Charge a real cell on the bench", "human",
                   (Parameter("current_A", "A", lo=0.5, hi=10.0, tolerance=0.05, label="Charge current"),
                    Parameter("ambient_C", "degC", lo=-10.0, hi=45.0, tolerance=1.0, label="Chamber")),
                   _PRODUCES, deterministic=False, destructive=True,
                   procedure="Charge the cell at {current_A} A from 0 % to 80 % in a {ambient_C} degC chamber; "
                             "log the surface temperature every 10 s."),
    ),
    solver=SPEC.solver,
)


class LoopCell(AnalyticSystem):
    spec = LOOP_SPEC

    def code_digest(self) -> str:
        return file_digest(Path(__file__), Path(__file__).with_name("analytic_system.py"))

    def execute(self, action, params, workdir):
        workdir = Path(workdir)
        if action == "cell_test":
            raise NotExecutable("cell_test is run by a person")
        if action in ("hold_charge", "hold_charge_nd"):
            workdir.mkdir(parents=True, exist_ok=True)
            (workdir / "started.txt").write_text(str(os.getpid()))
            hold = os.environ.get(HOLD_ENV)
            while hold and Path(hold).exists():
                time.sleep(0.05)
        if action == "stuck_charge":
            time.sleep(5)
        out = super().execute("simulate_charge", params, workdir)
        if action == "mislabelled_charge":
            table = out.table
            series = {q: ("V", v) for q, (_, v) in table.series.items()}
            out = type(out)(table=type(table)(time_unit=table.time_unit, t=table.t, series=series,
                                              scalars=table.scalars),
                            files=out.files, solver=out.solver, executed=out.executed)
        return out

    def procedure(self, action, params):
        spec = self.spec.action(action)
        return spec.procedure.format(**params) if spec.procedure else None


LABELLED_SPEC = SystemSpec(
    id="loop-test-labelled", version="1", title="Analytic cell that labels its runs",
    description="The analytic lumped cell; each run also states a category.",
    quantities=SPEC.quantities + (
        Quantity("run_status", "", kind="category", categories=("ok", "degraded"), label="Run status"),),
    actions=(ActionSpec("labelled_charge", "Simulate a charge and label the run", "computational", _PARAMS,
                        _PRODUCES + ("run_status",), deterministic=True, destructive=False, max_wall_s=30.0),),
    solver=SPEC.solver,
)


class LabelledCell(AnalyticSystem):
    spec = LABELLED_SPEC

    def code_digest(self) -> str:
        return file_digest(Path(__file__), Path(__file__).with_name("analytic_system.py"))

    def execute(self, action, params, workdir):
        out = AnalyticSystem.execute(self, action, params, workdir)
        out.table.scalars["run_status"] = ("garbled" if params["current_A"] >= 9.0 else "ok", "")
        return out


def register(monkeypatch=None, cls=None):
    """Register LoopCell (or `cls`); with pytest's monkeypatch, only for the test (the registry is restored
    afterwards)."""
    if monkeypatch is not None:
        systems.available()                       # load the built-ins first, so the restored registry keeps them
        monkeypatch.setattr(systems, "_ADAPTERS", dict(systems._ADAPTERS))
    adapter = (cls or LoopCell)()
    systems.register(adapter)
    return adapter


def objective(**kw) -> dict:
    """A loop objective for LoopCell, without the system snapshot (open_campaign fills it in)."""
    return {"statement": "Charge to 80 % faster while the cell stays at or below 45 degC.",
            "system": {"id": LOOP_SPEC.id}, "datasets": [], "baseline": [],
            "target": {"quantity": "charge_time_80", "unit": "s", "direction": "minimize"},
            "constraints": [{"quantity": "max_temperature", "op": "<=", "limit": 45.0, "unit": "degC",
                             "label": "Cell stays at or below 45 degC"}],
            "quantities": [{"name": "charge_time_80", "unit": "s", "source": "result"},
                           {"name": "max_temperature", "unit": "degC", "source": "result"}],
            "budget": {"model_usd": 0.0, "experiments": 8, "experiment_usd": 0.0},
            "planner": {"kind": "external", "profile": None, "client": None}, "registered_by": "Ana",
            "evaluator": "loop-compare/2"} | kw    # milestone 2 made /3 the default; these tests keep /2


PREDICTION = {"charge_time_80": {"value": 3000.0, "lo": 2500.0, "hi": 3500.0, "unit": "s"}}
