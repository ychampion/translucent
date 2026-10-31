"""The system adapter boundary: the only place domain knowledge lives.

Core code asks an adapter for quantities, actions, parameter bounds, costs, the
executor kind, execution, result checks and a deterministic baseline policy. It
never branches on a system id.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

ExecutorKind = Literal["computational", "human", "retrospective"]


@dataclass(frozen=True)
class Quantity:
    name: str
    unit: str  # canonical unit
    kind: Literal["scalar", "series", "category"] = "scalar"
    label: str = ""
    categories: tuple[str, ...] = ()
    tolerance: float = 0.0  # computational: solver bound


@dataclass(frozen=True)
class Parameter:
    name: str
    unit: str
    lo: float | None = None
    hi: float | None = None
    choices: tuple[float | str, ...] = ()
    integer: bool = False
    tolerance: float = 0.0  # allowed |executed - requested| in a returned result
    label: str = ""


@dataclass(frozen=True)
class Cost:
    usd: float = 0.0
    wall_s: float = 0.0
    person_minutes: float = 0.0
    specimens: int = 0
    other: tuple[tuple[str, float], ...] = ()  # domain cost units


@dataclass(frozen=True)
class ActionSpec:
    name: str
    label: str
    executor: ExecutorKind
    parameters: tuple[Parameter, ...]
    produces: tuple[str, ...]  # Quantity names a result must contain
    deterministic: bool = False  # safe to re-execute after a crash
    destructive: bool = True  # consumes its specimen
    procedure: str | None = (
        None  # human: instruction template with {param} placeholders
    )
    max_wall_s: float = 600.0


@dataclass(frozen=True)
class SystemSpec:
    id: str
    version: str  # adapter version, recorded in the objective
    title: str
    description: str
    quantities: tuple[Quantity, ...]
    actions: tuple[ActionSpec, ...]
    citation: str | None = None
    licence: str | None = None
    solver: dict[str, str] | None = None  # name, version, model, validity
    vision: bool = False

    def action(self, name: str) -> ActionSpec:
        for a in self.actions:
            if a.name == name:
                return a
        raise KeyError(f"Unknown action {name}")


@dataclass(frozen=True)
class ResultTable:
    time_unit: str | None = None  # None for scalar-only results
    t: tuple[float, ...] = ()
    series: dict[str, tuple[str, tuple[float, ...]]] = field(default_factory=dict)
    scalars: dict[str, tuple[float | str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionOutput:
    table: ResultTable
    files: dict[str, Path]  # raw outputs kept as artifacts
    solver: dict[str, str]  # exact name, version and settings used
    executed: dict[str, float | str]  # configuration actually run


class NotExecutable(RuntimeError):
    """The action's results come from people or a sealed store, not from code."""


class BaselinePolicy(Protocol):
    """Deterministic domain baseline with the same budget as the agent."""

    name: str

    def propose(
        self, history: list[dict], remaining: int
    ) -> list[tuple[str, dict[str, Any]]]: ...


@runtime_checkable
class SystemAdapter(Protocol):
    spec: SystemSpec

    def code_digest(self) -> str: ...
    def check_params(self, action: str, params: dict[str, Any]) -> list[str]: ...
    def configuration(self, action: str, params: dict[str, Any]) -> dict[str, Any]: ...
    def estimate_cost(self, action: str, params: dict[str, Any]) -> Cost: ...
    def execute(
        self, action: str, params: dict[str, Any], workdir: Path
    ) -> ExecutionOutput: ...
    def check_result(
        self, action: str, params: dict[str, Any], table: ResultTable
    ) -> list[str]: ...
    def procedure(self, action: str, params: dict[str, Any]) -> str | None: ...
    def baseline_policy(self) -> BaselinePolicy | None: ...
    def evidence_problems(self, ledger, campaign_id: str) -> list[str]: ...


@runtime_checkable
class SealedTrials(Protocol):
    """Retrospective extension used by the intervention engine (NIST only)."""

    metadata: dict[str, str]

    def load_trials(self, heater_off_lag_s: int = 10) -> list[dict]: ...
    def ingest(self, ledger, cid, sealed_store, **kw) -> list: ...
    def outcome_evidence_problems(
        self, ledger, trial, revealed, records
    ) -> list[str]: ...
    def corroboration_rows(self, rows, real_experiments=None) -> dict: ...
    def register_closing_evidence(self, ledger, cid): ...
    def condition_grid(self) -> dict[tuple[str, int], float]: ...
    def continuous_traces(self) -> list[dict]: ...


def check_params(spec: SystemSpec, action: str, params: dict[str, Any]) -> list[str]:
    """Shared bounds, choices and integer checks against an action's parameters."""
    try:
        a = spec.action(action)
    except KeyError as exc:
        return [str(exc.args[0])]
    known = {p.name: p for p in a.parameters}
    problems = [f"Unknown parameter {k}" for k in params if k not in known]
    for p in a.parameters:
        if p.name not in params:
            problems.append(f"Missing parameter {p.name}")
            continue
        v = params[p.name]
        if p.choices:
            if v not in p.choices:
                problems.append(f"{p.name} must be one of {list(p.choices)}")
            continue
        if (
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
        ):
            problems.append(f"{p.name} must be a number")
            continue
        if p.integer and v != int(v):
            problems.append(f"{p.name} must be an integer")
        if p.lo is not None and v < p.lo or p.hi is not None and v > p.hi:
            problems.append(f"{p.name} must be in [{p.lo}, {p.hi}] {p.unit}")
    return problems


def file_digest(*paths: Path) -> str:
    """sha256 over adapter source files, in the order given.

    Line endings are normalised so CRLF and LF checkouts of one commit agree.
    """
    h = hashlib.sha256()
    for path in paths:
        h.update(Path(path).read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()
