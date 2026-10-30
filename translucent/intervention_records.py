"""New append-only records; existing movie contracts retain their semantics."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class InterventionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    id: str
    campaign_id: str
    type: Literal[
        "protocol",
        "trial",
        "fold",
        "reference",
        "policy",
        "hypothesis",
        "submission",
        "fit",
        "decision",
        "reveal",
        "score",
        "comparison",
        "claim",
        "review",
        "procedure",
        "next_experiment",
        "planner",
        "observation",
        "update",
        "screen",
        "physics_prior",
    ]
    seq: int = Field(ge=1)
    data: dict[str, Any]
    previous_hash: str
    digest: str
