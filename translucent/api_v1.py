"""/api/v1: one server, many ledgers. Every route below /api/v1/ledgers/{lid} reaches its ledger through
`get_ledger`, which answers 404 for an id with no ledger and creates nothing.

Reads need no token (today's read policy). Writes need what the matching legacy write needs: the team token for
people's work (uploads, dataset imports), the administrator token and live opt-in for a paid run. The tokens are
the server's, so they are valid on every ledger it serves (one server per customer until identity lands).

Loop campaigns (design §2.9): every capability of translucent.loop_service.CAPABILITIES that an agent or a person may
call is a route named `loop.<capability>`; the runner's and the comparator's are on no route. Each write goes through
LoopService, so HTTP refuses what the service refuses: NotAllowed 403, NotFound 404, a stale version, a key reused
for something else or a dataset id clash 409, a refused result 422 with its reasons, anything else the ledger
refuses 422 with the plain reason; a write to a concluded campaign is a 409. A write's Idempotency-Key becomes part
of the record ids it writes, so a retry after a restart, even one that lost the API's state, finds the original
records; the API also keeps the first response, as for every people's write (a note, an event rather than a record,
relies on that alone). People act under the `author` they name, with
the team token; an agent's own client may call the agent capabilities with the team token and `agent` instead.
"""
from __future__ import annotations

from contextlib import contextmanager
import shutil
import threading
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from translucent import chain, datasets
from translucent.contracts import Campaign
from translucent.executors import HumanUpload
from translucent.workspace import UnknownLedger

# translucent.server imports this module to include the router, so nothing here imports it at import time
router = APIRouter(prefix="/api/v1")

LIFECYCLE = ("campaign.started", "campaign.resumed", "campaign.paused", "campaign.finished")
SERIES_READS = threading.BoundedSemaphore(2)   # a series read parses its whole blob (up to 2M points); reads are open
BUSY_WAIT_S = 10.0     # an open read waits this long for a series slot or a busy export, then gets 503


def get_ledger(lid: str, request: Request):
    """The dependency every /ledgers/{lid} route takes: the served ledger, a server.LedgerHost (open it with
    `host.ledger()`)."""
    try:
        return request.app.state.translucent.host(lid)
    except UnknownLedger:
        raise HTTPException(404, "Ledger not found") from None


def team(request: Request):
    """The team (or administrator) token, as every legacy people's write requires."""
    from translucent.server import _authorize_person
    _authorize_person(request)


# ------------------------------------------------------------ campaign summaries (web data contract, CampaignSummary)

def _loop_status(L, cid, records) -> tuple[str, str, str | None]:
    """(status, next_action, outcome) of a loop campaign: its conclusion, else its requests' folded states, else
    whether a planner turn is open."""
    from translucent.loop import request_states
    conclusion = next((r for r in reversed(records) if r.type == "conclusion"), None)
    if conclusion is not None:
        return "concluded", "none", conclusion.data.get("outcome")
    pending = {s.state for s in request_states(L, cid, records).values()}
    if "awaiting_approval" in pending:
        return "waiting_for_approval", "awaiting_approval", None
    if "awaiting_result" in pending:
        return "waiting_for_result", "awaiting_result", None
    if pending & {"queued", "running"}:
        return "running", "running", None
    turn = next((r for r in reversed(records) if r.type == "planner"), None)
    thinking = turn is not None and turn.data.get("phase") != "turn_finished"
    return "running", "planner_thinking" if thinking else "none", None


def _legacy_status(last, job) -> tuple[str, str]:
    """(status, next_action) of a video or intervention campaign from its worker job and its last lifecycle event.
    A paused campaign is shown as stopped: nothing runs until a person resumes it."""
    if job is not None and job.status in ("queued", "running"):
        return "running", "running"
    if job is not None and job.status == "paused":
        return "stopped", "none"
    kind, status = last or (None, None)
    if kind == "campaign.finished":
        return ("finished" if status in (None, "finished") else "stopped"), "none"
    if kind == "campaign.paused" or job is not None or kind is None:
        return "stopped", "none"      # paused, a failed or interrupted job, or never started
    return "running", "running"       # started by another process (the CLI) and not finished


def campaign_summary(L, lid: str, campaign, job) -> dict:
    db, cid = L.db, campaign.id
    t_last, spend = db.execute(
        "SELECT MAX(json_extract(body,'$.t')), TOTAL(CASE WHEN json_extract(body,'$.type')='cost.updated' "
        "THEN json_extract(body,'$.data.usd') END) FROM event WHERE campaign_id=?", (cid,)).fetchone()
    last = db.execute(
        "SELECT json_extract(body,'$.type'), json_extract(body,'$.data.status'), "
        "json_extract(body,'$.data.spent_usd') "
        f"FROM event WHERE campaign_id=? AND json_extract(body,'$.type') IN ({','.join('?' * len(LIFECYCLE))}) "
        "ORDER BY seq DESC LIMIT 1", (cid, *LIFECYCLE)).fetchone()
    loop = chain.records(L, cid)
    objective = campaign.objective
    if loop:
        kind = "loop"
        status, next_action, outcome = _loop_status(L, cid, loop)
        if loop[0].type == "objective":
            objective = loop[0].data.get("statement") or objective
    else:
        intervention = db.execute("SELECT 1 FROM record WHERE kind='intervention' AND campaign_id=? AND "
                                  "json_extract(body,'$.type')='protocol' LIMIT 1", (cid,)).fetchone()
        kind = "intervention" if intervention else "video"
        status, next_action = _legacy_status(last[:2] if last else None, job)
        outcome = None
    if last is not None and isinstance(last[2], (int, float)):   # intervention runs report spend when they finish
        spend = max(spend, float(last[2]))
    return {"id": cid, "ledger": lid, "kind": kind, "title": campaign.title, "status": status,
            "created_at": campaign.created_at, "updated_at": max(campaign.created_at, t_last or 0.0),
            "objective": objective[:140], "next_action": next_action, "outcome": outcome,
            "spend_usd": round(spend, 6)}


# ------------------------------------------------------------ ledgers and campaigns

@router.get("/ledgers")
def ledgers(request: Request):
    """Every ledger this server serves: [{id, title, campaigns, datasets}]."""
    server = request.app.state.translucent
    out = []
    for lid in server.workspace.ids():
        try:
            with server.workspace.open(lid) as L:
                count = lambda kind: L.db.execute("SELECT COUNT(*) FROM record WHERE kind=?", (kind,)).fetchone()[0]
                out.append({"id": lid, "title": server.workspace.title(lid), "campaigns": count("campaign"),
                            "datasets": count("dataset")})
        except UnknownLedger:   # removed while listing
            continue
    return out


@router.get("/ledgers/{lid}/campaigns", name="loop.campaigns")
def campaigns(host=Depends(get_ledger), cursor: int = Query(0, ge=0),
              limit: int = Query(50, ge=1, le=50)):     # each item folds its campaign's chain
    """Campaigns of every kind (loop, video, intervention), newest first: {items: [CampaignSummary], next_cursor}.
    Pass next_cursor back as cursor for the next page; it is null on the last one. New campaigns never shift a page."""
    with host.ledger() as L:
        rows = L.db.execute("SELECT rowid, body FROM record WHERE kind='campaign' AND (?=0 OR rowid<?) "
                            "ORDER BY rowid DESC LIMIT ?", (cursor, cursor, limit + 1)).fetchall()
        items = [campaign_summary(L, host.id, c, host.load_job(c.id))
                 for c in (Campaign.model_validate_json(body) for _, body in rows[:limit])]
    return {"items": items, "next_cursor": rows[limit - 1][0] if len(rows) > limit else None}


@router.get("/ledgers/{lid}/campaigns/{campaign_id}/events")
def events(campaign_id: str, request: Request, host=Depends(get_ledger),
           last_event_id: str | None = Header(None)):
    """The campaign's events as server-sent events; Last-Event-ID resumes after that seq (seqs are per campaign)."""
    return host.events_response(campaign_id, request, last_event_id)


@router.get("/ledgers/{lid}/artifacts/{artifact_id:path}")
def artifact(artifact_id: str, host=Depends(get_ledger)):
    return host.artifact_response(artifact_id)


# ------------------------------------------------------------ datasets

@router.put("/ledgers/{lid}/uploads/{upload_id}/{filename}", status_code=201,
            dependencies=[Depends(team)])
async def upload(upload_id: str, filename: str, request: Request, host=Depends(get_ledger)):
    """One file of a dataset, streamed to this ledger's upload space; import it with POST .../datasets."""
    return await request.app.state.translucent.receive_upload(
        host.dataset_uploads_dir, upload_id, filename, request, files=datasets.MAX_FILES,
        too_many=f"One dataset import takes at most {datasets.MAX_FILES} files")


class DatasetImport(BaseModel):
    """A manifest (the JSON itself, never a path to one) and where its files are: an upload made with PUT
    .../uploads/{upload_id}/{name}, or a folder under the server's import root (path, relative to it)."""
    model_config = ConfigDict(extra="forbid")
    manifest: dict
    upload_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    path: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def _one_source(self):
        if (self.upload_id is None) == (self.path is None):
            raise ValueError("give upload_id or path")
        return self


@router.post("/ledgers/{lid}/datasets", status_code=201, dependencies=[Depends(team)], name="loop.import_dataset")
def import_dataset(body: DatasetImport, request: Request, host=Depends(get_ledger)):
    """Import a team's dataset (a v2 manifest, or a v1 experiment manifest) into this ledger; returns its
    DatasetRecord. The same dataset again returns the stored one; a different one under a used id is a 409; a refused
    file or manifest is a 422 with the plain reason; a path the server may not read is a 403. Dataset ids are per
    ledger."""
    from translucent.loop import NotAllowed
    from translucent.loop_service import Actor, LoopService
    from translucent.server import _plain
    server = request.app.state.translucent

    def act():
        with host.ledger() as L:
            try:
                if body.upload_id is not None:
                    folder = host.dataset_uploads_dir / body.upload_id    # the server's own staging folder
                    if not folder.is_dir():
                        raise HTTPException(404, "Upload the dataset's files first")
                    record = datasets.import_dataset(L, body.manifest, folder).model_dump(mode="json")
                else:
                    # LoopService's rule: no import root, no read; every named file must be a file under the root,
                    # reached without links
                    service = LoopService(L, Actor("person", "team", "http"), import_root=server.import_root)
                    record = service.import_dataset(body.manifest, body.path)
            except NotAllowed as e:
                raise HTTPException(403, str(e)) from e
            except datasets.DatasetConflict as e:
                raise HTTPException(409, str(e)) from e
            except ValueError as e:   # includes pydantic's ValidationError and AdmissionError: said plainly
                raise HTTPException(422, _plain(e)) from e   # uploaded files stay (until they expire) to retry
            except RuntimeError as e:
                if "another dataset import" in str(e):
                    raise HTTPException(409, str(e)) from e
                raise
        if body.upload_id is not None:
            shutil.rmtree(folder, ignore_errors=True)
        return record
    return host.once(request, body, 201, act)


@router.get("/ledgers/{lid}/datasets", name="loop.datasets")
def list_datasets(host=Depends(get_ledger)):
    """The ledger's imported datasets (summaries; no raw values)."""
    with _loop(host, None) as service:
        return service.datasets()


@router.get("/ledgers/{lid}/datasets/{dataset_id}", name="loop.dataset")
def get_dataset(dataset_id: str, host=Depends(get_ledger)):
    """One DatasetRecord: its records, channels and artifact ids (series are artifacts; read them separately)."""
    with _loop(host, None) as service:
        return service.dataset(dataset_id)


# ------------------------------------------------------------ loop campaigns (design §2.9)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80,
                                        pattern=r"^[^\x00-\x1f\x7f]+$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]
Hash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class _Person(_Body):
    """A person's action: the team token, under the name they give."""
    author: Name

    def actor(self):
        from translucent.loop_service import Actor
        return Actor("person", self.author, "http")


class _PersonOrAgent(_Body):
    """An action open to people and agents: `author` for a person, `agent` for an agent's client (its name)."""
    author: Name | None = None
    agent: Name | None = None

    @model_validator(mode="after")
    def _one(self):
        if (self.author is None) == (self.agent is None):
            raise ValueError("give author (a person) or agent (an agent's client), not both")
        return self

    def actor(self):
        from translucent.loop_service import Actor
        return Actor("person", self.author, "http") if self.author else Actor("agent", self.agent, "http")


class OpenLoop(_PersonOrAgent):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    objective: dict          # ObjectiveData without version or system snapshot; registered_by defaults to the caller
    memory: Literal["on", "off"] = "on"


class DefineMeasurement(_PersonOrAgent):
    name: Name
    spec: dict               # OperatorSpec (ops/1)


class Propose(_PersonOrAgent):
    action: Name
    params: dict
    rationale: Text
    prediction: dict
    expected_effect: Text
    evidence_ids: list[Annotated[str, StringConstraints(min_length=1, max_length=200)]] = Field(default=[],
                                                                                                max_length=50)
    specimen: dict | None = None
    due_by: float | None = None


class Approve(_Person):
    content_hash: Hash
    note: Annotated[str, StringConstraints(max_length=2000)] = ""


class Reject(_Person):
    content_hash: Hash
    reason: Text


class Revise(_Person):
    params: dict
    reason: Text
    approve: bool = False


class Cancel(_PersonOrAgent):
    reason: Text


class Conclude(_PersonOrAgent):
    text: Text
    unresolved_reasons: list[Text] = Field(default=[], max_length=50)


class Amend(_Person):
    reason: Text
    target: dict | None = None
    constraints: list[dict] | None = Field(default=None, max_length=50)


class Note(_Person):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ResultUpload(HumanUpload):
    """A person's returned result: the HumanUpload JSON, and the upload (PUT .../uploads/{upload_id}/{name}) that
    holds its files; no upload when the result is scalars only. The uploader is the person named as its author."""
    upload_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    def actor(self):
        from translucent.loop_service import Actor
        return Actor("person", self.author, "http")


def _idempotency_key(request: Request) -> str | None:
    from translucent.loop_surface import check_key
    try:
        return check_key(request.headers.get("Idempotency-Key"))
    except ValueError:
        raise HTTPException(400, "Idempotency-Key must be 1-128 letters, digits or _.:-") from None


@contextmanager
def _loop(host, actor, key: str | None = None, cid: str | None = None):
    """A LoopService on the served ledger (`actor` None: a reader), its refusals said as HTTP statuses (see the
    module docstring)."""
    from translucent.ledger import AdmissionError
    from translucent.loop import NotAllowed, NotFound, StaleRequest
    from translucent.loop_service import Actor, LoopService, ResultRefused
    from translucent.server import _plain
    with host.ledger() as L:
        try:
            yield LoopService(L, actor or Actor("person", "reader", "http"), idempotency_key=key,
                              import_root=host.server.import_root)
        except HTTPException:
            raise
        except NotAllowed as e:
            raise HTTPException(403, str(e)) from e
        except NotFound as e:
            raise HTTPException(404, str(e)) from e
        except ResultRefused as e:
            raise HTTPException(422, {"message": "The result was refused; the reasons are recorded",
                                      "result_rejected": {"record_id": getattr(e.record, "id", None),
                                                          "reasons": e.reasons}}) from e
        except AdmissionError as e:     # a stale version or a reused key (409), or another refusal (422)
            done = chain.concluded(L, cid) if cid else None
            if done is not None:
                raise HTTPException(409, f"Campaign concluded at seq {done.seq}; further writes are refused") from e
            raise HTTPException(409 if isinstance(e, (StaleRequest, chain.ChainConflict)) else 422, _plain(e)) from e
        except datasets.DatasetConflict as e:
            raise HTTPException(409, str(e)) from e
        except KeyError as e:     # a system this server has not registered
            raise HTTPException(422, str(e.args[0]) if e.args else "unknown system") from e
        except ValueError as e:   # pydantic's ValidationError included: said plainly
            raise HTTPException(422, _plain(e)) from e


def _wake_runner(request: Request):
    """Approved computational work: the API's runner looks now instead of at its next tick."""
    request.app.state.translucent.loop_wake.set()


@router.get("/ledgers/{lid}/systems", name="loop.systems")
def list_systems(host=Depends(get_ledger)):
    """The registered systems (SystemSpec as JSON): quantities, actions, parameter bounds, costs."""
    with _loop(host, None) as service:
        return service.systems()


@router.post("/ledgers/{lid}/campaigns", status_code=201, dependencies=[Depends(team)], name="loop.open_campaign")
def open_loop_campaign(body: OpenLoop, request: Request, host=Depends(get_ledger)):
    """Register a loop campaign: {campaign, view}. A planner that spends nothing (external, scripted,
    domain_baseline) needs the team token; a paid one (muse...) also needs the administrator token and live opt-in.
    The model budget is held to the provider caps, as a video campaign's is."""
    from translucent.loop_surface import FREE_PLANNERS, objective_budget_problems, view
    from translucent.server import PLANNER, _authorize, _profile
    objective = dict(body.objective)
    planner = objective.get("planner") if isinstance(objective.get("planner"), dict) else {}
    if planner.get("kind") not in FREE_PLANNERS:
        _authorize(request)
    profile = planner.get("profile") if planner.get("profile") in PLANNER else _profile()
    if planner.get("kind") not in FREE_PLANNERS and isinstance(objective.get("planner"), dict):
        objective["planner"] = {**planner, "profile": profile}   # it runs on the profile its budget was checked on
    if problems := objective_budget_problems(objective, profile):
        raise HTTPException(422, "; ".join(problems))
    objective.setdefault("registered_by", body.author or body.agent)
    key = _idempotency_key(request)

    def act():
        with _loop(host, body.actor(), key) as service:
            opened = service.open_campaign(objective, body.title, memory=body.memory)
            opened.pop("created")     # HTTP has no ownership: a keyed replay answers as the first call did
            out = {"campaign": opened, "view": view(service, opened["campaign_id"], host.id)}
        _wake_runner(request)     # a baseline_simulation objective pre-approves its baseline runs
        return out
    return host.once(request, body, 201, act)


@router.get("/ledgers/{lid}/campaigns/{campaign_id}/view", name="loop.view")
def campaign_view(campaign_id: str, request: Request, host=Depends(get_ledger)):
    """The loop campaign's LoopView (translucent.loop_view): objective, baseline, requests, comparisons, next action
    and conclusion, with a live source naming this server and ledger."""
    from translucent.loop_surface import view
    with _loop(host, None) as service:
        return view(service, campaign_id, host.id, api=str(request.base_url).rstrip("/"))


@router.get("/ledgers/{lid}/campaigns/{campaign_id}/series", name="loop.series")
def campaign_series(campaign_id: str, ref: str = Query(min_length=1, max_length=300),
                    quantity: str = Query(min_length=1, max_length=80), max_points: int = Query(2000, ge=2, le=20000),
                    t0: float | None = None, t1: float | None = None, host=Depends(get_ledger)):
    """A result's (ref: request or result id) or baseline record's (ref: dataset/record) series in the system's
    unit, between t0 and t1 seconds when given, downsampled to at most max_points."""
    window = None
    if t0 is not None or t1 is not None:
        window = (t0 if t0 is not None else float("-inf"), t1 if t1 is not None else float("inf"))
    if not SERIES_READS.acquire(timeout=BUSY_WAIT_S):        # open reads must not pile up the server's threads
        raise HTTPException(503, "Other series are being read; try again shortly", headers={"Retry-After": "2"})
    try:
        with _loop(host, None) as service:
            return service.series(campaign_id, ref, quantity, window=window, max_points=max_points)
    finally:
        SERIES_READS.release()


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/measurements", dependencies=[Depends(team)],
             name="loop.define_measurement")
def define_measurement(campaign_id: str, body: DefineMeasurement, request: Request, host=Depends(get_ledger)):
    """Define a derived measurement (ops/1) and apply it to the baselines and results: {record, measurements}."""
    key = _idempotency_key(request)

    def act():
        with _loop(host, body.actor(), key, campaign_id) as service:
            return service.define_measurement(campaign_id, body.name, body.spec)
    return host.once(request, body, 200, act)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/requests", status_code=201, dependencies=[Depends(team)],
             name="loop.propose")
def propose(campaign_id: str, body: Propose, request: Request, host=Depends(get_ledger)):
    """Request an experiment: the sealed request with its content_hash, awaiting a person's approval (or approved at
    once by a rule the registering person wrote). A pending request with the same configuration is returned."""
    from translucent.loop_surface import replay
    key = _idempotency_key(request)

    def act():
        with _loop(host, body.actor(), key, campaign_id) as service:
            out = replay(service, "propose", campaign_id, action=body.action, params=body.params) or                 service.propose(campaign_id, body.action, body.params, rationale=body.rationale,
                                prediction=body.prediction, expected_effect=body.expected_effect,
                                evidence_ids=tuple(body.evidence_ids), specimen=body.specimen, due_by=body.due_by)
        if out["state"] == "queued":
            _wake_runner(request)
        return out
    return host.once(request, body, 201, act)


@router.get("/ledgers/{lid}/campaigns/{campaign_id}/requests/{request_id}", name="loop.request")
def get_request(campaign_id: str, request_id: str, host=Depends(get_ledger)):
    """One request: its versions' content hash, state, timeline and result."""
    with _loop(host, None) as service:
        return service.request(campaign_id, request_id)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/requests/{request_id}/approve", dependencies=[Depends(team)],
             name="loop.approve")
def approve(campaign_id: str, request_id: str, body: Approve, request: Request, host=Depends(get_ledger)):
    """Approve the version whose content_hash the person read; a hash of an older version is a 409 naming the
    version to review."""
    key = _idempotency_key(request)

    def act():
        with _loop(host, body.actor(), key, campaign_id) as service:
            out = service.approve(campaign_id, request_id, body.content_hash, body.note)
        _wake_runner(request)
        return out
    return host.once(request, body, 200, act)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/requests/{request_id}/reject", dependencies=[Depends(team)],
             name="loop.reject")
def reject(campaign_id: str, request_id: str, body: Reject, request: Request, host=Depends(get_ledger)):
    key = _idempotency_key(request)

    def act():
        with _loop(host, body.actor(), key, campaign_id) as service:
            return service.reject(campaign_id, request_id, body.content_hash, body.reason)
    return host.once(request, body, 200, act)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/requests/{request_id}/revise", dependencies=[Depends(team)],
             name="loop.revise")
def revise(campaign_id: str, request_id: str, body: Revise, request: Request, host=Depends(get_ledger)):
    """A person's correction of the parameters, as the next version; with approve, approved in the same write."""
    from translucent.loop_surface import replay
    key = _idempotency_key(request)

    def act():
        with _loop(host, body.actor(), key, campaign_id) as service:
            out = replay(service, "revise", campaign_id, request_id=request_id, params=body.params) or \
                service.revise(campaign_id, request_id, body.params, body.reason, approve=body.approve)
        if body.approve:
            _wake_runner(request)
        return out
    return host.once(request, body, 200, act)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/requests/{request_id}/cancel", dependencies=[Depends(team)],
             name="loop.cancel")
def cancel(campaign_id: str, request_id: str, body: Cancel, request: Request, host=Depends(get_ledger)):
    """Cancel a request: a person any pending one, an agent only its own before approval."""
    key = _idempotency_key(request)

    def act():
        with _loop(host, body.actor(), key, campaign_id) as service:
            return service.cancel(campaign_id, request_id, body.reason)
    return host.once(request, body, 200, act)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/requests/{request_id}/results", dependencies=[Depends(team)],
             name="loop.submit_result")
def submit_result(campaign_id: str, request_id: str, body: ResultUpload, request: Request,
                  host=Depends(get_ledger)):
    """A person returns a physical test's result against the approved content hash. It is checked as the executor
    checks a solver's output; a refused result is recorded and answered 422 with its reasons. Upload the files
    first (PUT .../uploads/{upload_id}/{name}); they are removed once the result is accepted. An identical
    re-upload of the accepted result returns it without a new record."""
    _idempotency_key(request)
    if body.request_id != request_id:
        raise HTTPException(422, f"the result names request {body.request_id}, not {request_id}")
    upload = HumanUpload.model_validate(body.model_dump(exclude={"upload_id"}))

    def act():      # after the reply cache: a keyed retry finds its answer although the files are gone
        folder, files = None, {}
        if body.upload_id is not None:
            folder = host.dataset_uploads_dir / body.upload_id     # the server's own staging folder
            if not folder.is_dir():
                raise HTTPException(404, "Upload the result's files first")
            files = {p.name: p for p in folder.iterdir() if p.is_file() and not p.name.endswith(".part")}
        with _loop(host, body.actor(), None, campaign_id) as service:
            out = service.submit_result(campaign_id, upload, files)
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)
        return out
    return host.once(request, body, 200, act)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/amendments", dependencies=[Depends(team)], name="loop.amend")
def amend(campaign_id: str, body: Amend, request: Request, host=Depends(get_ledger)):
    """A person amends the objective's target and/or constraints as its next version: {amendment, comparisons}."""
    from translucent.loop_surface import replay
    key = _idempotency_key(request)

    def act():
        with _loop(host, body.actor(), key, campaign_id) as service:
            return replay(service, "amend", campaign_id, reason=body.reason, target=body.target,
                          constraints=body.constraints) or \
                service.amend(campaign_id, body.reason, target=body.target, constraints=body.constraints)
    return host.once(request, body, 200, act)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/notes", dependencies=[Depends(team)], name="loop.note")
def note(campaign_id: str, body: Note, request: Request, host=Depends(get_ledger)):
    """A person's note in the campaign's event stream."""
    _idempotency_key(request)

    def act():
        with _loop(host, body.actor(), None, campaign_id) as service:
            if done := chain.concluded(service.L, campaign_id):
                raise HTTPException(409, f"Campaign concluded at seq {done.seq}; further writes are refused")
            return service.note(campaign_id, body.text)
    return host.once(request, body, 200, act)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/conclusion", dependencies=[Depends(team)],
             name="loop.conclude")
def conclude(campaign_id: str, body: Conclude, request: Request, host=Depends(get_ledger)):
    """Conclude the campaign. The outcome is computed from the recorded comparisons; the caller writes only the
    text. Every later write to the campaign is a 409."""
    key = _idempotency_key(request)

    def act():
        with _loop(host, body.actor(), key, campaign_id) as service:
            return service.conclude(campaign_id, body.text, body.unresolved_reasons)
    return host.once(request, body, 200, act)


@router.get("/ledgers/{lid}/campaigns/{campaign_id}/export")
def export(campaign_id: str, host=Depends(get_ledger)):
    """The campaign's loop export (translucent.loop_export): its recorded LoopView and the embedded receipt that
    `translucent verify <file>` re-checks, recomputing the view. Made once per ledger revision (server.LedgerHost)."""
    from translucent.loop import current_objective
    with _loop(host, None, cid=campaign_id) as service:
        current_objective(service.L, campaign_id)   # NotFound (404) for a campaign that is not a loop one
        handle = host.loop_export(campaign_id)

    def chunks():
        with handle:
            while chunk := handle.read(2**20):
                yield chunk
    return StreamingResponse(chunks(), media_type="application/json",
                             headers={"Content-Disposition": f'attachment; filename="{campaign_id}.loop.json"'})
