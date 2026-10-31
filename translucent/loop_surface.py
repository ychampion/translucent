"""What the HTTP routes (translucent.api_v1) and the MCP tools (translucent.mcp_server) share for loop campaigns
(design §2.9): the budget rules a campaign is opened under, idempotency keys, and the replay of a keyed write that
the loop service cannot recognise by its record id alone.

Both surfaces are generated from `loop_service.CAPABILITIES`: every capability an agent or a person may call has an
HTTP route named `loop.<capability>` and an MCP tool; system capabilities (the runner's, the comparator's) have
neither. tests/test_mcp_loop.py checks that parity.
"""
from __future__ import annotations

import math
import re

from translucent import chain
from translucent.loop_service import CAPABILITIES

IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
FREE_PLANNERS = frozenset({"external", "scripted", "domain_baseline"})   # spend nothing: the team token is enough


def surface_capabilities() -> dict[str, frozenset[str]]:
    """Capabilities a surface offers, with who may call them: everything but the system's own."""
    return {name: cap.actors - {"system"} for name, cap in CAPABILITIES.items() if cap.actors - {"system"}}


def budget_cap(profile: str) -> float:
    """The most a campaign on this profile may spend: the caps of the providers it calls (translucent.budget)."""
    from translucent import budget
    from translucent.runtime import PROFILES
    return sum(budget.CAPS_USD[provider] for provider in {p for p, _ in PROFILES[profile].values()})


def budget_problem(usd, profile: str, what: str = "budget") -> str | None:
    """Why a model budget is refused (not a finite number of dollars, negative, above the profile's caps), or None."""
    if isinstance(usd, bool) or not isinstance(usd, (int, float)) or not math.isfinite(usd) or usd < 0:
        return f"{what} must be a finite number of dollars, 0 or more"
    cap = budget_cap(profile)
    if usd > cap:
        return f"{what}: ${usd:g} is more than the ${cap:g} cap on the {profile} profile's providers"
    return None


def objective_budget_problems(objective: dict, profile: str) -> list[str]:
    """A loop objective's budget under the same rules as a video campaign's: model spend capped by the providers,
    experiment money and wall time finite and non-negative. The experiment count is checked at registration."""
    budget = objective.get("budget")
    if not isinstance(budget, dict):
        return ["budget states model_usd and experiments"]
    out = [p for p in [budget_problem(budget.get("model_usd", 0.0), profile, "budget.model_usd")] if p]
    for key in ("experiment_usd", "max_wall_s"):
        v = budget.get(key)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0):
            out.append(f"budget.{key} must be a finite number, 0 or more")
    return out


def check_key(key: str | None) -> str | None:
    if key is not None and not IDEMPOTENCY_KEY.fullmatch(key):
        raise ValueError("an idempotency key is 1-128 letters, digits or _.:-")
    return key


def replay(service, op: str, cid: str, **body) -> dict | None:
    """The answer to a keyed propose, revise or amend that already happened, or None. The service derives these
    records' bodies from the current state (the objective's version, the next request version), so after anything
    changed its own replay check would see a different body and refuse the retry; the key's record id finds the
    original instead. A different call under the key is a ChainConflict."""
    if not service.key:
        return None
    kind = service.actor.kind
    if op == "propose":
        rec = service.L.get("loop", chain.key_id("req", cid, "request", kind, service.key))
        if rec is None:
            return None
        if rec.campaign_id != cid or rec.data.get("action") != body["action"] or                 _numbers(rec.data.get("params", {})) != _numbers(body["params"]):
            raise chain.ChainConflict("this idempotency key was used for a different request")
        return service.request(cid, rec.data["request_id"])
    if op == "revise":
        rec = service.L.get("loop", chain.key_id("req", cid, "request", kind, service.key))
        if rec is None:
            return None
        params = rec.data.get("params", {})
        if rec.campaign_id != cid or rec.data.get("request_id") != body["request_id"] or \
                any(params.get(k) != v for k, v in body["params"].items()):
            raise chain.ChainConflict("this idempotency key was used for a different revision")
        return service.request(cid, body["request_id"])
    rec = service.L.get("loop", chain.key_id("amd", cid, "amendment", kind, service.key))
    if rec is None:
        return None
    sent = chain.payload("amendment", {"version": rec.data.get("version"), "author": service.actor.name,
                                       "reason": body["reason"], "target": body.get("target"),
                                       "constraints": body.get("constraints")})
    if rec.campaign_id != cid or sent != rec.data:
        raise chain.ChainConflict("this idempotency key was used for a different amendment")
    compared = [r for r in chain.records(service.L, cid)          # recorded with the amendment, under its version
                if r.type == "comparison" and r.data.get("objective_version") == rec.data["version"]]
    return {"amendment": rec.model_dump(mode="json"), "comparisons": [r.model_dump(mode="json") for r in compared]}


def _numbers(params: dict) -> dict:
    return {k: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v for k, v in params.items()}


def view(service, cid: str, ledger: str, api: str = "") -> dict:
    """The campaign's LoopView, its live source naming the ledger (and the API) it is served from."""
    return service.view(cid, api=api, ledger=ledger)


def campaigns(service, ledger: str, **page) -> dict:
    return service.campaigns(ledger=ledger, **page)
