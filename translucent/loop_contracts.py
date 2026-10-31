"""Frozen contracts for import v2 datasets and loop campaigns (milestone 2026-11). Shared by the ledger, the loop
service, executors, HTTP, MCP and the web app.

Two new ledger record kinds: `dataset` (ledger-scoped, campaign_id NULL; DatasetRecord) and `loop` (one hash-chained
record per step of a loop campaign; LoopRecord). A loop record's `data` is validated against the payload model for
its `type` (PAYLOADS) when it is admitted, and stored in that model's JSON form, so a digest has one byte form.

These are additions: CONTRACT_VERSION and every existing record, event and bundle keep their meaning. LoopView and
CampaignSummary at the end are the web data contract (TypedDicts, documentation that tests validate the sample
against), not ledger records.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, with_config
from typing_extensions import NotRequired, TypedDict

from translucent.contracts import Uncertainty


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


# Same values as translucent.systems.base.ExecutorKind (S1); kept here so contracts import nothing from systems.
ExecutorKind = Literal["computational", "human", "retrospective"]


# ---------------------------------------------------------------- import v2: the manifest a team writes

class ChannelSpec(_Frozen):
    quantity: str                                 # adapter quantity, or "lab:<name>" for anything else
    unit: str
    uncertainty: Uncertainty | None = None
    label: str | None = None


class TableSpec(_Frozen):
    file: str                                     # flat filename (experiments._filename rules)
    format: Literal["csv", "tsv", "json"] = "csv"
    delimiter: str | None = None
    header_row: int = Field(0, ge=0, le=200)      # preamble lines before the header (EC-Lab, Maccor)
    skip_rows: int = Field(0, ge=0, le=10)        # unit rows after the header
    decimal: Literal[".", ","] = "."
    time_column: str
    columns: dict[str, ChannelSpec]               # unlisted source columns are ignored and named in the record


class TimeBase(_Frozen):
    unit: str = "s"
    origin: str                                   # plain words: what t = 0 means
    offset_s: float = 0.0                         # record time = column time (converted to s) - offset_s
    source: str
    uncertainty_s: float = Field(0.0, ge=0)


class MediaSpec(_Frozen):
    file: str
    kind: Literal["image", "video"]
    t_s: float | None = None                      # image time on the record clock
    clock: dict | None = None                     # video: the v1 Clock shape (timestamps_csv | interval_s)
    needs_vision: bool = False
    label: str | None = None


class ScalarSpec(_Frozen):
    quantity: str
    value: float | str
    unit: str
    source: str
    uncertainty: Uncertainty | None = None


class Specimen(_Frozen):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
    batch: str | None = None
    properties: dict[str, float | str] = {}


class Provenance(_Frozen):
    source: str
    instrument: str | None = None
    operator: str | None = None
    acquired_at: str | None = None
    notes: str | None = None


class RecordSpec(_Frozen):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")
    role: Literal["baseline", "history", "reference"] = "history"
    specimen: Specimen
    configuration: dict[str, float | str] = {}
    configuration_units: dict[str, str] = {}
    conditions: dict[str, float | str] = {}
    conditions_units: dict[str, str] = {}
    time: TimeBase | None = None                  # required when tables are present
    tables: list[TableSpec] = []
    scalars: list[ScalarSpec] = []
    media: list[MediaSpec] = []
    provenance: Provenance


class ManifestV2(_Frozen):
    manifest_version: Literal[2]
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    title: str
    author: str
    citation: str
    licence: str
    system: str | None = None
    records: list[RecordSpec] = Field(min_length=1, max_length=500)
    notes: str | None = None


# ---------------------------------------------------------------- import v2: what the ledger stores

class StoredChannel(_Frozen):
    column: str
    quantity: str
    unit: str
    uncertainty: Uncertainty
    series_id: str                                # artifact: {"t":[..],"t_unit":"s","y":[..],"unit":..}
    n: int
    t_range: tuple[float, float]


class StoredRecord(_Frozen):
    id: str
    role: str
    specimen: Specimen
    configuration: dict[str, float | str]
    configuration_units: dict[str, str]
    configuration_hash: str                       # sha256(canonical({"system": id|None, "configuration": normalized}))
    conditions: dict[str, float | str]
    conditions_units: dict[str, str]
    time: TimeBase | None
    channels: list[StoredChannel]
    scalars: list[ScalarSpec]
    media: list[dict]                             # {"artifact_id","kind","t_s","needs_vision","label"}
    provenance: Provenance
    files: dict[str, str]                         # source filename -> artifact id
    ignored_columns: list[str] = []


class DatasetRecord(_Frozen):
    """Ledger record kind `dataset`. Its id is the manifest id, so the primary key makes it unique per ledger."""
    id: str
    manifest_version: int                         # 2, or 1 when upgraded
    manifest_id: str                              # artifact id of the scrubbed canonical manifest JSON
    title: str
    author: str
    citation: str
    licence: str
    system: str | None
    records: list[StoredRecord]
    upgraded_from: Literal["v1"] | None = None
    created_at: float


# ---------------------------------------------------------------- loop campaigns: the chained record

LoopType = Literal["objective", "amendment", "measurement", "request", "approval", "rejection", "cancellation",
                   "execution", "result", "result_rejected", "failure", "comparison", "conclusion", "planner"]


class LoopRecord(_Frozen):
    """Ledger record kind `loop`. Appended only by translucent.chain.append, mirrored by a `loop.recorded` event at
    the same seq. Request states are derived by folding these records; nothing is updated."""
    id: str
    campaign_id: str
    type: LoopType
    seq: int = Field(ge=1)                        # equals its mirror event's seq
    actor: str                                    # "agent:<model|client>", "person:<name>", "system:runner",
                                                  # "policy:auto/<n>", "policy:registration"
    data: dict[str, Any]                          # PAYLOADS[type] in JSON form, checked at admission
    created_at: float                             # the mirror event's t
    previous_hash: str                            # digest of the campaign's previous loop record; "" for the first
    digest: str                                   # sha256(canonical(model_dump(exclude={"digest"})))


# ---------------------------------------------------------------- objective, amendments, measurements

class Target(_Frozen):
    quantity: str
    unit: str
    direction: Literal["minimize", "maximize"]
    margin: float = 0.0                           # improvement that counts; negative = slowdown allowed
    limit: float | None = None                    # optional absolute requirement on the candidate


class Constraint(_Frozen):
    quantity: str
    op: Literal["<=", ">="]
    limit: float
    unit: str
    label: str


class OperatorSpec(_Frozen):
    """ops/1: deterministic, recomputed by verify."""
    op: Literal["final", "initial", "max", "min", "mean", "value_at", "time_to_threshold", "delta", "slope",
                "integral", "duration_where"]
    channel: str                                  # series quantity, e.g. "temperature"
    window: tuple[float, float] | None = None     # record seconds
    threshold: float | None = None
    direction: Literal["rising", "falling"] | None = None
    at_s: float | None = None
    unit: str                                     # declared output unit; must equal units.derive(...)


class QuantityDef(_Frozen):
    name: str
    unit: str
    source: Literal["result", "operator"]         # adapter-produced scalar, or computed from a series
    operator: OperatorSpec | None = None


class AutoRule(_Frozen):
    """A pre-approval written by the registering person."""
    executor: Literal["computational"]
    max_usd: float = 0.0
    max_wall_s: float = 600.0
    max_count: int = 0                            # 0 = no auto-approval


class ObjectiveData(_Frozen):
    version: int = 1
    statement: str
    system: dict                                  # {"id","version","code_digest","spec": SystemSpec as JSON}
    datasets: list[str]
    baseline: list[str]                           # "dataset_id/record_id", role baseline
    target: Target
    constraints: list[Constraint]
    quantities: list[QuantityDef]                 # every target/constraint quantity is defined here
    evaluator: Literal["loop-compare/1", "loop-compare/2"] = "loop-compare/2"   # /1 stays for objectives naming it
    operators: Literal["ops/1"] = "ops/1"
    budget: dict                                  # {"model_usd": float, "experiments": int, "experiment_usd": float}
    auto_approve: list[AutoRule] = []
    baseline_simulation: bool = False             # pre-approves re-running each baseline configuration on the solver
    planner: dict                                 # {"kind": "muse"|"muse_no_feedback"|"domain_baseline"|"external"
                                                  #   |"scripted", "profile": "meta"|"dev"|None, "client": {...}|None}
    registered_by: str                            # person name


class AmendmentData(_Frozen):
    """People only; never a planner tool."""
    version: int
    author: str
    reason: str
    target: Target | None = None
    constraints: list[Constraint] | None = None


class MeasurementData(_Frozen):
    """A derived measurement the planner defined (define_measurement). Its values are ordinary Measurements."""
    name: str
    spec: OperatorSpec
    defined_by: str


# ---------------------------------------------------------------- the request lifecycle

class RequestData(_Frozen):
    request_id: str                               # stable across versions: "rq_<20 hex>"
    version: int
    supersedes: str | None                        # loop record id of the previous version
    action: str
    executor: ExecutorKind
    params: dict[str, float | str]
    units: dict[str, str]
    configuration_hash: str                       # sha256(canonical(adapter.configuration(action, params)))
    specimen: dict | None = None                  # human tests: {"id"} or {"batch"}
    rationale: str                                # why this experiment, plain language
    prediction: dict[str, dict]                   # quantity -> {"value","lo","hi","unit"}; sealed before execution
    expected_effect: str                          # what decision changes depending on the result
    cost: dict                                    # adapter.estimate_cost(...) as JSON
    evidence_ids: list[str] = []                  # measurements, results, comparisons it relies on
    objective_version: int
    procedure: str | None = None                  # human: adapter.procedure(...) rendered
    due_by: float | None = None
    content_hash: str                             # chain.content_hash(this payload): everything the approver saw


class ApprovalData(_Frozen):
    request_id: str
    record_id: str                                # the request version's loop record id
    content_hash: str
    author: str
    note: str = ""
    rule: str | None = None                       # "auto/<n>" or "registration" for a policy approval


class RejectionData(_Frozen):
    request_id: str
    record_id: str
    content_hash: str
    author: str
    reason: str


class CancellationData(_Frozen):
    request_id: str
    author: str
    reason: str


class ExecutionData(_Frozen):
    request_id: str
    content_hash: str
    attempt: int
    phase: Literal["started", "abandoned"]
    worker: dict                                  # {"host","pid","process": "api"|"mcp"|"cli"}
    executor_digest: str                          # adapter.code_digest() + solver version


class ResultData(_Frozen):
    request_id: str
    content_hash: str
    attempt: int | None
    basis: Literal["computational", "physical", "retrospective"]
    run_id: str
    measurements: dict[str, str]                  # quantity -> Measurement id
    categories: dict[str, str] = {}               # category quantity -> value, e.g. {"validity": "extrapolation"}
    series: dict[str, str]                        # quantity -> normalized series artifact id
    files: dict[str, str]                         # raw files -> artifact ids
    executed: dict                                # {"configuration","specimen_id","solver"}
    submitted_by: str


class ResultRejectedData(_Frozen):
    request_id: str
    claimed_hash: str | None
    payload_sha256: str
    reasons: list[str]
    submitted_by: str


class FailureData(_Frozen):
    request_id: str
    attempt: int
    error: str
    retryable: bool


Verdict = Literal["candidate_better", "not_better", "violates_constraints", "inconclusive", "not_comparable"]
Outcome = Literal["improved", "improved_in_simulation", "not_improved", "unresolved"]


class ComparisonData(_Frozen):
    evaluator: str
    objective_version: int
    basis: str
    baseline: dict                                # {"refs": [...], "values": {q: {value, unit, h}}}
    candidate: dict                               # {"request_id", "result_id", "values": {...}}
    target: dict                                  # {"baseline","candidate","delta","h","margin","better"}
    constraints: list[dict]                       # {"quantity","op","limit","value","h","ok": bool|None}
    verdict: Verdict
    reasons: list[str]


class ConclusionData(_Frozen):
    outcome: Outcome
    comparison_id: str | None
    text: str
    unresolved_reasons: list[str]
    author: str


class PlannerData(_Frozen):
    phase: Literal["turn_started", "model_request", "tool_call", "tool_result", "turn_finished"]
    payload: dict                                 # mirrors intervention_live receipts


PAYLOADS: dict[str, type[_Frozen]] = {
    "objective": ObjectiveData, "amendment": AmendmentData, "measurement": MeasurementData,
    "request": RequestData, "approval": ApprovalData, "rejection": RejectionData,
    "cancellation": CancellationData, "execution": ExecutionData, "result": ResultData,
    "result_rejected": ResultRejectedData, "failure": FailureData, "comparison": ComparisonData,
    "conclusion": ConclusionData, "planner": PlannerData,
}

RequestStateName = Literal["proposed", "awaiting_approval", "queued", "running", "awaiting_result", "completed",
                           "failed", "cancelled", "rejected", "superseded"]


# ---------------------------------------------------------------- web data contract (documentation types)
# LoopView is computed by loop_view.view(L, cid, source); a live view and a recorded export share it.
# tests/data/loop_view_sample.json is a complete example and is validated against these types.

_STRICT = ConfigDict(extra="forbid")
CampaignStatus = Literal["running", "waiting_for_approval", "waiting_for_result", "concluded", "stopped", "finished"]
NextActionKind = Literal["awaiting_approval", "running", "awaiting_result", "planner_thinking", "none"]


@with_config(_STRICT)
class CampaignSummary(TypedDict):
    id: str
    ledger: str
    kind: Literal["loop", "video", "intervention"]
    title: str
    status: CampaignStatus
    created_at: float
    updated_at: float
    objective: str                                # first 140 characters of the statement
    next_action: NextActionKind
    outcome: Outcome | None
    spend_usd: float


@with_config(_STRICT)
class LiveSource(TypedDict):
    kind: Literal["live"]
    api: str
    ledger: str
    as_of_seq: int
    generated_at: float


@with_config(_STRICT)
class VerifyRun(TypedDict):
    command: str
    summary: str
    ok: bool
    ran_on: str


@with_config(_STRICT)
class RecordedSource(TypedDict):
    kind: Literal["recorded"]
    file: str
    sha256: str
    exported_at: float
    verified: VerifyRun | None


@with_config(_STRICT)
class ViewSolver(TypedDict):
    name: str
    version: str
    validity: str


@with_config(_STRICT)
class ViewSystem(TypedDict):
    id: str
    version: str
    title: str
    solver: ViewSolver | None


@with_config(_STRICT)
class ViewPlanner(TypedDict):
    kind: str
    models: dict[str, str | None]                 # {"planner": ..., "vision": ... | None}


@with_config(_STRICT)
class ViewCampaign(TypedDict):
    id: str
    title: str
    created_at: float
    status: CampaignStatus
    memory: Literal["on", "off"]
    system: ViewSystem
    planner: ViewPlanner


@with_config(_STRICT)
class ViewTarget(TypedDict):
    quantity: str
    unit: str
    direction: Literal["minimize", "maximize"]
    margin: float
    label: str
    limit: NotRequired[float]                     # the target's absolute requirement, when it has one


@with_config(_STRICT)
class ViewConstraint(TypedDict):
    quantity: str
    op: Literal["<=", ">="]
    limit: float
    unit: str
    label: str


@with_config(_STRICT)
class ViewAmendment(TypedDict):
    version: int
    seq: int
    author: str
    reason: str
    changes: list[str]


@with_config(_STRICT)
class ViewObjective(TypedDict):
    version: int
    statement: str
    target: ViewTarget
    constraints: list[ViewConstraint]
    amendments: list[ViewAmendment]


@with_config(_STRICT)
class ViewValue(TypedDict):
    value: float | None                           # None: a threshold crossing not reached in its window
    unit: str
    h: float                                      # half-width of the uncertainty
    not_reached_within: NotRequired[list[float]]  # [t0, t1] record seconds: the crossing, if any, is after t1


@with_config(_STRICT)
class ViewConstraintCheck(TypedDict):
    quantity: str
    value: float | None                           # None: missing, or a crossing not reached in its window
    limit: float
    ok: bool | None                               # None: the uncertainty straddles the limit
    not_reached_within: NotRequired[list[float]]


@with_config(_STRICT)
class ViewBaseline(TypedDict):
    basis: Literal["physical", "computational", "retrospective"]
    refs: list[str]                               # "dataset_id/record_id"
    values: dict[str, ViewValue]
    constraints: list[ViewConstraintCheck]
    fails: list[str]                              # plain sentences, one per violated constraint


@with_config(_STRICT)
class CurrentResult(TypedDict):
    comparison_id: str
    request_id: str
    verdict: Verdict
    basis: str
    sentence: str


@with_config(_STRICT)
class AwaitingApproval(TypedDict):
    kind: Literal["awaiting_approval"]
    request_id: str
    version: int
    content_hash: str
    title: str
    rationale: str
    expected_effect: str
    prediction: dict[str, dict]
    cost: dict
    executor: ExecutorKind
    actions: list[str]                            # e.g. ["approve", "reject", "revise", "cancel"]


@with_config(_STRICT)
class RequestInFlight(TypedDict):
    kind: Literal["running", "awaiting_result"]
    request_id: str
    version: int
    title: str
    executor: ExecutorKind
    actions: list[str]                            # e.g. ["cancel"], or ["submit_result", "cancel"] for a human test


@with_config(_STRICT)
class PlannerThinking(TypedDict):
    kind: Literal["planner_thinking"]
    title: str


@with_config(_STRICT)
class NothingNext(TypedDict):
    kind: Literal["none"]


@with_config(_STRICT)
class ViewParam(TypedDict):
    value: float | str
    unit: str


@with_config(_STRICT)
class TimelineEntry(TypedDict):
    state: RequestStateName
    seq: int
    t: float
    actor: str


@with_config(_STRICT)
class ViewMeasurement(TypedDict):
    id: str
    quantity: str
    value: float
    unit: str
    h: float


@with_config(_STRICT)
class ViewSeries(TypedDict):
    quantity: str
    unit: str
    url: str                                      # /api/v1/ledgers/<lid>/artifacts/sha256:...
    n: int


@with_config(_STRICT)
class PredictionError(TypedDict):
    predicted: float
    measured: float
    inside: bool | None                           # None when the prediction gave no interval


@with_config(_STRICT)
class ViewResult(TypedDict):
    result_id: str
    basis: Literal["computational", "physical", "retrospective"]
    measurements: list[ViewMeasurement]
    categories: dict[str, str]                  # category quantity -> value, e.g. validity
    series: list[ViewSeries]
    prediction_error: dict[str, PredictionError]


@with_config(_STRICT)
class ResultRejection(TypedDict):
    seq: int
    reasons: list[str]


@with_config(_STRICT)
class ViewRequest(TypedDict):
    request_id: str
    version: int
    state: RequestStateName
    action: str
    executor: ExecutorKind
    params: dict[str, ViewParam]
    proposed_by: str
    approved_by: str | None                       # "person:<name>", "policy:auto/<n>", "policy:registration"
    content_hash: str
    rationale: str
    prediction: dict[str, dict]
    timeline: list[TimelineEntry]
    result: ViewResult | None
    rejections: list[ResultRejection]             # result_rejected records: uploads refused, with reasons


@with_config(_STRICT)
class ComparisonTarget(TypedDict):
    baseline: float | None                        # None: no baseline on the candidate's basis, or not reached
    candidate: float | None
    delta: float | None                           # improvement (positive is better); None unless both are values
    better: bool | None
    decided_by: NotRequired[Literal["feasibility"]]   # loop-compare/2 rule 5: it meets limits the baseline breaks


@with_config(_STRICT)
class ViewComparison(TypedDict):
    id: str
    request_id: str
    objective_version: int
    basis: str
    target: ComparisonTarget
    constraints: list[ViewConstraintCheck]
    verdict: Verdict
    reasons: list[str]


@with_config(_STRICT)
class MeasurementDefined(TypedDict):
    name: str
    op: str
    unit: str
    defined_by: str


@with_config(_STRICT)
class Spend(TypedDict):
    model_usd: float
    model_budget_usd: float
    experiments_used: int
    experiments_budget: int


@with_config(_STRICT)
class ViewConclusion(TypedDict):
    outcome: Outcome
    comparison_id: str | None
    text: str
    unresolved_reasons: list[str]


@with_config(_STRICT)
class LoopView(TypedDict):
    view_version: Literal[1]
    source: LiveSource | RecordedSource
    campaign: ViewCampaign
    objective: ViewObjective
    baseline: list[ViewBaseline]
    current_result: CurrentResult | None
    next_action: AwaitingApproval | RequestInFlight | PlannerThinking | NothingNext
    requests: list[ViewRequest]
    comparisons: list[ViewComparison]
    measurements_defined: list[MeasurementDefined]
    spend: Spend
    conclusion: ViewConclusion | None
    events_cursor: int
