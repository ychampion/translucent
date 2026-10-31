"""System adapter boundary: protocol checks, registry and NIST golden replay."""

import hashlib
import time
from pathlib import Path

import pytest
from support.analytic_system import AnalyticSystem

from translucent import systems
from translucent.intervention_runtime import (
    InterventionCampaigner,
    ScriptedPlanner,
    open_intervention,
)
from translucent.intervention_session import ReplayIdentity, decision_bytes
from translucent.ledger import Ledger
from translucent.systems.base import (
    Cost,
    NotExecutable,
    ResultTable,
    SealedTrials,
    SystemAdapter,
)


def check_protocol(adapter):
    """Checks every slice relies on, whatever the system's domain."""
    assert isinstance(adapter, SystemAdapter)
    spec = adapter.spec
    assert spec.id and spec.version and spec.quantities and spec.actions
    names = {q.name for q in spec.quantities}
    assert len(names) == len(spec.quantities)
    digest = adapter.code_digest()
    assert len(digest) == 64 and digest == adapter.code_digest()
    for action in spec.actions:
        assert action.executor in ("computational", "human", "retrospective")
        assert set(action.produces) <= names
        params = {
            p.name: p.choices[0] if p.choices else (p.lo if p.lo is not None else 0)
            for p in action.parameters
        }
        assert adapter.check_params(action.name, params) == []
        assert adapter.check_params(action.name, {**params, "unknown": 1})
        for p in action.parameters:
            bad = "x" if not p.choices else object()
            assert adapter.check_params(action.name, {**params, p.name: bad})
        config = adapter.configuration(action.name, params)
        assert config == adapter.configuration(action.name, dict(params))
        assert isinstance(adapter.estimate_cost(action.name, params), Cost)
    assert adapter.check_params("no-such-action", {})
    policy = adapter.baseline_policy()
    if policy is not None:
        first = policy.propose([], 3)
        assert first == policy.propose([], 3) and len(first) <= 3
        for action, params in first:
            assert adapter.check_params(action, params) == []
    assert adapter.evidence_problems(None, "c") == []


def test_production_registry_lists_the_built_in_systems():
    assert [s.id for s in systems.available()] == [
        "nist-heat-interrupt",
        "pybamm-fastcharge",
    ]
    with pytest.raises(KeyError, match="Unknown system analytic-cell"):
        systems.get("analytic-cell")


def test_nist_adapter_satisfies_protocol_and_delegates(tmp_path):
    from translucent.systems import nist_heat

    nist = systems.get("nist-heat-interrupt")
    assert isinstance(nist, nist_heat.NistHeatAdapter)
    assert isinstance(nist, SealedTrials)
    check_protocol(nist)
    assert {a.executor for a in nist.spec.actions} == {"retrospective"}
    assert nist.metadata is nist_heat.SYSTEM
    assert nist.condition_grid() is nist_heat.VENT_MEANS
    assert nist.load_trials() == nist_heat.load_dataset()
    assert nist.load_trials(heater_off_lag_s=7) == nist_heat.load_dataset(
        heater_off_lag_s=7
    )
    assert all(t["delay_after_vent_s"] is None for t in nist.continuous_traces())
    assert nist.check_params("observe", {"observe_s": 45})
    assert nist.check_params("observe", {"observe_s": 30}) == []
    assert nist.check_result(
        "escalate", {}, ResultTable(scalars={"outcome": ("melted", "")})
    )
    with pytest.raises(NotExecutable):
        nist.execute("escalate", {}, tmp_path)
    assert nist.baseline_policy().propose([], 2) == [("escalate", {})] * 2


def test_analytic_adapter_satisfies_protocol_and_registers_in_tests(
    tmp_path, monkeypatch
):
    toy = AnalyticSystem()
    check_protocol(toy)
    monkeypatch.setattr(systems, "_ADAPTERS", dict(systems._ADAPTERS))
    systems.register(toy)
    assert systems.get("analytic-cell") is toy
    assert {s.id for s in systems.available()} == {
        "nist-heat-interrupt",
        "pybamm-fastcharge",
        "analytic-cell",
    }
    with pytest.raises(TypeError):
        systems.register(object())


def test_analytic_adapter_is_deterministic_fast_and_checked(tmp_path):
    toy = AnalyticSystem()
    params = {"current_A": 4.0, "ambient_C": 25.0}
    start = time.perf_counter()
    out = toy.execute("simulate_charge", params, tmp_path / "a")
    assert time.perf_counter() - start < 0.05
    again = toy.execute("simulate_charge", params, tmp_path / "b")
    assert out.table == again.table and out.executed == params
    assert set(out.table.series) | set(out.table.scalars) == set(
        toy.spec.action("simulate_charge").produces
    )
    assert out.files["solver.json"].exists()
    assert toy.check_result("simulate_charge", params, out.table) == []
    bad = ResultTable(
        time_unit="s",
        t=(0.0, 10.0),
        series=out.table.series,
        scalars={"max_temperature": (0.0, "degC"), "charge_time_80": (1e6, "s")},
    )
    assert len(toy.check_result("simulate_charge", params, bad)) == 2
    assert toy.check_params("simulate_charge", {"current_A": 50.0, "ambient_C": 25.0})
    with pytest.raises(ValueError):
        toy.execute("simulate_charge", {"current_A": 0.0, "ambient_C": 25.0}, tmp_path)
    assert toy.baseline_policy().propose(
        [{"action": "simulate_charge", "params": {"current_A": 1.0}}], 2
    ) == [
        ("simulate_charge", {"current_A": 2.0, "ambient_C": 25.0}),
        ("simulate_charge", {"current_A": 4.0, "ambient_C": 25.0}),
    ]


# Recorded on the commit before the adapter boundary existed (abb2006). The
# planner, seed, epoch and trial set are fixed, so every id, salt, timestamp
# and committed byte must reproduce through the registry.
GOLDEN_SELECTED = {15, 16, 22, 65, 66, 67}
GOLDEN_SEED = "11" * 32
GOLDEN_EPOCH = 1_790_000_000.0
GOLDEN_SHA256 = "9f366c3352c569bdde3711931cf61857ddc812d203c99ba86a1112ad6856e332"


def golden_digest(root: Path) -> str:
    ledger = Ledger(root / "ledger")
    try:
        ledger.intervention_identity = ReplayIdentity(GOLDEN_SEED, GOLDEN_EPOCH)
        campaign, fold, store = open_intervention(
            ledger, root / "sealed", selected=GOLDEN_SELECTED, min_trials=1
        )
        InterventionCampaigner(ledger, campaign, fold, store, ScriptedPlanner()).run()
        h = hashlib.sha256()
        for body in decision_bytes(ledger, campaign.id):
            h.update(body.encode())
        for (body,) in ledger.db.execute(
            "SELECT body FROM record WHERE kind='intervention' AND campaign_id=? "
            "ORDER BY rowid",
            (campaign.id,),
        ):
            h.update(body.encode())
        return h.hexdigest()
    finally:
        ledger.db.close()


def test_nist_golden_replay_is_byte_identical(tmp_path):
    assert golden_digest(tmp_path) == GOLDEN_SHA256
