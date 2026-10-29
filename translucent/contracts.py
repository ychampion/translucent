"""Frozen contracts (v1, 2026-10-28). Shared by the ledger, tools, agents, API, replay bundles and the web UI.

Changing a field here is a contract change: bump CONTRACT_VERSION, update docs/CONTRACTS.md and the fixtures.

The ledger is insert-only. Status changes are Events (and, for claims, a new Review); nothing is updated in place.
Every Event carries a plain-language `title` written for a non-specialist viewer; technical detail goes in
`detail` and `data`, which the UI shows only on demand.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------- evidence

ArtifactKind = Literal["video", "frame", "masks", "overlay", "series", "table", "code", "plot", "text", "report"]


class Source(_Frozen):
    uri: str                       # file path, URL or DOI the bytes came from
    citation: str | None = None    # human citation, e.g. "Reisecker et al., Nat. Commun. 14, 2432 (2023), Movie 4"
    licence: str | None = None     # e.g. "CC BY 4.0"


class Transform(_Frozen):
    op: str                        # e.g. "faststart_remux", "dedupe_frames", "sam_track", "decode_masks"
    parent_ids: list[str]
    params: dict[str, Any] = {}


class Artifact(_Frozen):
    id: str                        # "sha256:<hex>" of the stored bytes (content-addressed)
    kind: ArtifactKind
    media_type: str                # MIME type
    bytes: int
    shape: list[int] | None = None           # e.g. [frames, height, width]
    axes: list[str] | None = None            # e.g. ["frame", "y", "x"]
    source: Source | None = None             # set for imported artifacts
    transform: Transform | None = None       # set for derived artifacts
    label: str | None = None                 # short plain-language name for the UI
    created_at: float


RunStatus = Literal["ok", "tool_failure", "invalid_result"]


class Run(_Frozen):
    """One tool invocation. A tool_failure never counts as scientific evidence either way."""
    id: str
    campaign_id: str
    step_id: str
    tool: str                                # registered tool name
    idempotency_key: str                     # hash of tool + inputs + params; same key = same run
    input_ids: list[str]                     # artifact and measurement ids
    output_ids: list[str] = []
    measurement_ids: list[str] | None = None  # measurements this run produced (None: records made outside tools.call)
    params: dict[str, Any] = {}
    code_sha: str | None = None              # for sandboxed analysis code
    prompt_sha: str | None = None            # for model calls
    provider: str | None = None              # "meta", "fireworks", "modal", "local"
    model: str | None = None
    provider_request_id: str | None = None
    cost_usd: float = 0.0
    status: RunStatus
    error: str | None = None
    started_at: float
    ended_at: float


UncertaintyKind = Literal["std", "ci95", "bound", "resolution", "none"]


class Uncertainty(_Frozen):
    value: float | None
    kind: UncertaintyKind
    method: str                              # how it was obtained, e.g. "±1 captured image at 6.9 s"


class Measurement(_Frozen):
    """A number produced by trusted code (never by a language model)."""
    id: str
    run_id: str
    quantity: str                            # registered quantity name, e.g. "growth_velocity"
    value: float | list[float]
    unit: str                                # SI-style, e.g. "um/s", "s", "px/mm", "V"
    uncertainty: Uncertainty
    subject: str | None = None               # what was measured, e.g. "dendrite 1", "particle 3"
    frame_range: tuple[int, int] | None = None   # source frames (inclusive), native indices
    time_range: tuple[float, float] | None = None  # physical seconds, from a recorded time base
    calibration_ids: list[str] = []          # measurements this depends on (scale, time base)
    artifact_ids: list[str] = []             # masks, frames, series it was computed from
    valid_domain: str | None = None          # stated limits, e.g. "single straight dendrite, frames 60-109"
    label: str | None = None                 # plain language for the UI, e.g. "Growth speed"


PredicateType = Literal[
    "value_within",          # measurement value within [lo, hi] (units checked)
    "values_agree",          # two measurements agree within combined uncertainty
    "events_coincide",       # two time measurements coincide within tolerance
    "trend",                 # series is increasing / decreasing / steady, by a stated test
    "exists",                # an observed object/event exists in cited frames
]


class Predicate(_Frozen):
    type: PredicateType
    params: dict[str, Any]                   # typed per predicate; validated by the claim gate
    measurement_ids: list[str]


ClaimStatus = Literal["proposed", "supported", "refuted", "inconclusive", "unsupported", "stale"]


class Claim(_Frozen):
    id: str
    campaign_id: str
    author: str                              # agent role that proposed it
    predicate: Predicate
    statement: str                           # one plain sentence, rendered from the predicate
    supersedes: str | None = None
    created_at: float


class Check(_Frozen):
    name: str                                # e.g. "ancestry", "units", "time_base_recorded", "voltage_corroboration"
    passed: bool
    detail: str


class Review(_Frozen):
    """Gate reviews decide status; critic and human reviews are recorded but never change it."""
    id: str
    claim_id: str
    reviewer: Literal["gate", "critic", "human"]
    verdict: Literal["supported", "refuted", "inconclusive", "unsupported"]
    checks: list[Check] = []
    reason: str                              # plain language
    artifact_ids: list[str] = []             # e.g. overlay image the critic looked at
    model: str | None = None
    author: str | None = None                # the person's name, for a human review ("supported" = signed off)
    created_at: float


class Lesson(_Frozen):
    """Campaign memory: a procedure or fact learned from evidence, reusable by later campaigns."""
    id: str
    text: str                                # plain language, e.g. "These movies repeat each image; remove repeats before timing growth"
    scope: str                               # e.g. "source:reisecker2023", "modality:optical-operando", "tool:sam-3.1"
    evidence_ids: list[str]                  # runs / measurements / claims that taught it
    learned_in: str                          # campaign id
    created_at: float


# ---------------------------------------------------------------- campaign + event stream

class Campaign(_Frozen):
    id: str
    objective: str                           # the research question, plain language
    title: str                               # short, for the UI
    inputs: list[str]                        # artifact ids of the recorded experiments
    memory: Literal["on", "off"] = "on"
    budget_usd: float
    created_at: float


EventType = Literal[
    "campaign.started", "campaign.finished",
    "memory.loaded", "memory.learned",
    "step.started", "step.finished",         # agent reasoning steps (plan, choose test, reflect)
    "tool.started", "tool.finished",         # tool runs (SAM, measure, sandbox code, critic)
    "artifact.created", "measurement.recorded",
    "claim.proposed", "claim.reviewed",
    "cost.updated",
    "human.note", "question.asked", "question.answered", "question.expired",   # people in the loop
    "campaign.control", "campaign.paused", "campaign.resumed", "campaign.retry",
]


class Event(_Frozen):
    seq: int                                 # strictly increasing per campaign; SSE id
    campaign_id: str
    t: float                                 # wall-clock seconds since the epoch
    type: EventType
    actor: str                               # "orchestrator", "worker", "critic", "gate", "runtime", tool name
    title: str = Field(max_length=90)        # plain language for the viewer, no jargon
    detail: str | None = None                # one or two sentences, shown on expand
    refs: list[str] = []                     # ids of artifacts / measurements / claims / runs / lessons
    data: dict[str, Any] = {}                # typed payload; for *.reviewed includes the full Review


# ---------------------------------------------------------------- replay bundle (what the web UI loads)

class Track(_Frozen):
    """Per-frame geometry for the video overlay. Coordinates are source-video pixels."""
    subject: str                             # e.g. "dendrite 1"
    frames: list[int]
    mask_paths: list[str]                    # SVG path data per frame (meta-sam-parser decode_mask_to_svg_path),
                                             # in mask-local pixels: translate by mask_origins[i]
    mask_origins: list[tuple[float, float]]  # top-left (x, y) of each mask in source-video pixels
    points: list[tuple[float, float] | None] = []   # e.g. tip position per frame


class Series(_Frozen):
    """A time series shown alongside the video, e.g. cell voltage."""
    name: str                                # plain label, e.g. "Cell voltage"
    unit: str
    t: list[float]                           # physical seconds
    y: list[float]
    frame_of_t: list[int] | None = None      # mapping to video frames when known


class ReplayBundle(_Frozen):
    contract_version: str = CONTRACT_VERSION
    campaign: Campaign
    video: str                               # relative path to an MP4 inside the bundle
    video_size: tuple[int, int]              # width, height
    fps: float                               # playback fps of the MP4
    frame_time_s: list[float] | None = None  # physical time of each playback frame, when known
    tracks: list[Track] = []
    series: list[Series] = []
    events: list[Event]
    artifacts: list[Artifact] = []
    measurements: list[Measurement] = []
    claims: list[Claim] = []
    reviews: list[Review] = []
    lessons: list[Lesson] = []
