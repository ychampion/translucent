"""Recorded physical systems and trusted ingestion.

Adapters register explicitly; there are no entry-point plugins. Built-in
adapters load on first lookup, so importing the registry stays cheap.
"""

from __future__ import annotations

from translucent.systems.base import SystemAdapter, SystemSpec

_ADAPTERS: dict[str, SystemAdapter] = {}
_BUILTINS_LOADED = False


def _builtins() -> None:
    global _BUILTINS_LOADED
    if not _BUILTINS_LOADED:
        from translucent.systems.lgm50_fastcharge import FastChargeAdapter
        from translucent.systems.nist_heat import NistHeatAdapter

        _ADAPTERS.setdefault("nist-heat-interrupt", NistHeatAdapter())
        _ADAPTERS.setdefault("pybamm-fastcharge", FastChargeAdapter())
        _BUILTINS_LOADED = True


def register(adapter: SystemAdapter) -> None:
    if not isinstance(adapter, SystemAdapter):
        raise TypeError("System adapters must implement SystemAdapter")
    _builtins()
    _ADAPTERS[adapter.spec.id] = adapter


def get(system_id: str) -> SystemAdapter:
    _builtins()
    try:
        return _ADAPTERS[system_id]
    except KeyError:
        raise KeyError(f"Unknown system {system_id}") from None


def available() -> list[SystemSpec]:
    _builtins()
    return [a.spec for a in _ADAPTERS.values()]
