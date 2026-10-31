"""A campaign is charged its own model calls, not the spend of other campaigns sharing the provider."""
from types import SimpleNamespace

import pytest

from translucent.contracts import Campaign
from translucent.ledger import Ledger
from translucent.providers import Metered
from translucent.runtime import Campaigner, Settings


class SharedBalance:
    """A provider budget another campaign spends from while this one's call is in flight."""

    def __init__(self):
        self.committed = 0.0

    def balance(self):
        return {"committed_usd": self.committed}


class Busy(Metered):
    provider = "test"

    def __init__(self, shared):
        self.ledger = shared

    def chat(self, *, model, messages, max_tokens, **kw):
        self.ledger.committed += 0.50          # the other campaign's call, settled meanwhile
        self.last_usd = 0.01                   # this call's own charge
        self.ledger.committed += 0.01
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))])


def test_a_call_is_charged_its_own_cost_while_another_campaign_spends(tmp_path):
    L = Ledger(tmp_path / "l")
    c = Campaign(id="C", objective="o", title="t", inputs=[], budget_usd=1.0, created_at=0.0)
    L.add(c)
    camp = object.__new__(Campaigner)
    camp.L, camp.c, camp.s, camp.spent = L, c, Settings(), 0.0
    camp.roles = {"planner": ("test", "m")}
    camp.clients = {"test": Busy(SharedBalance())}
    camp._chat("planner", [{"role": "user", "content": "hi"}])
    camp._chat("planner", [{"role": "user", "content": "again"}])
    assert camp.spent == pytest.approx(0.02)
    charged = [e.data["usd"] for e in L.events("C") if e.type == "cost.updated"]
    assert charged == [0.01, 0.01]
