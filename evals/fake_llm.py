"""Offline stand-in for the chat clients Campaigner uses (MeteredFireworks / MeteredMeta). No network.

A policy maps (messages, tools) -> (content, [(tool_name, args), ...]). Cost is a fixed fake amount per call so the
budget/cost plumbing is exercised.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from types import SimpleNamespace


@dataclass
class _Function:
    name: str
    arguments: str


@dataclass
class _ToolCall:
    id: str
    function: _Function
    type: str = "function"

    def model_dump(self):
        return {"id": self.id, "type": self.type,
                "function": {"name": self.function.name, "arguments": self.function.arguments}}


class FakeClient:
    def __init__(self, policy, usd_per_call: float = 0.0):
        self.policy, self.usd_per_call, self.spent, self.calls = policy, usd_per_call, 0.0, []
        self.ledger = SimpleNamespace(balance=lambda: {"committed_usd": self.spent})

    def chat(self, *, model, messages, max_tokens=None, **kw):
        self.spent += self.usd_per_call
        content, calls = self.policy(messages, kw.get("tools"))
        self.calls.append({"model": model, "n_messages": len(messages), "tool_calls": [c[0] for c in calls]})
        tc = [_ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", function=_Function(n, json.dumps(a))) for n, a in calls]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tc or None))])


def constant(text: str):
    """Policy that always answers `text` with no tool calls (e.g. a critic that always says SUPPORTED)."""
    return lambda messages, tools: (text, [])
