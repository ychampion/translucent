"""/api/v1: one server, many ledgers. Every route below /api/v1/ledgers/{lid} reaches its ledger through
`get_ledger`, which answers 404 for an id with no ledger and creates nothing.

Every route needs a credential (translucent.auth; server.AuthGate checks it before a body is read), and a ledger
outside the credential's scope is a 404 like an unknown one. Who acts is the credential: an engineer, administrator
or viewer is the person it names, a planner key the agent it names. Bodies keep `author` / `agent` as optional
fields for older clients; if present they must be the credential's name and kind (else 403 "This key acts as ...").
What a role may do is translucent.auth.ROLE_CAPABILITIES: a viewer reads; a planner proposes, defines measurements
and cancels its own requests; an engineer does a person's work; an administrator may also open a paid-planner
campaign (with live opt-in and the provider keys) and raise a budget by amendment.

Loop campaigns (design §2.9): every capability of translucent.loop_service.CAPABILITIES that an agent or a person may
call is a route named `loop.<capability>`; the runner's and the comparator's are on no route. Each write goes through
LoopService, so HTTP refuses what the service refuses: NotAllowed 403, NotFound 404, a stale version, a key reused
for something else or a dataset id clash 409, a refused result 422 with its reasons, anything else the ledger
refuses 422 with the plain reason; a write to a concluded campaign is a 409. A write's Idempotency-Key becomes part
of the record ids it writes, so a retry after a restart, even one that lost the API's state, finds the original
records; the API also keeps the first response, as for every people's write (a note, an event rather than a record,
relies on that alone).
"""
from __future__ import annotations

from contextlib import contextmanager
import shutil
import threading
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from translucent import chain, datasets, records
from translucent.contracts import Campaign
from translucent.executors import HumanUpload
from translucent.workspace import UnknownLedger

# translucent.server imports this module to include the router, so nothing here imports it at import time
router = APIRouter(prefix="/api/v1")

LIFECYCLE = ("campaign.started", "campaign.resumed", "campaign.paused", "campaign.finished")
SERIES_READS = threading.BoundedSemaphore(2)   # a series read parses its whole blob (up to 2M points); reads are open
BUSY_WAIT_S = 10.0     # an open read waits this long for a series slot or a busy export, then gets 503


def principal(request: Request):
    """The request's credential (translucent.auth.Credential), set by server.AuthGate."""
    from translucent.server import _credential
    return _credential(request)


def get_ledger(lid: str, request: Request):
    """The dependency every /ledgers/{lid} route takes: the served ledger, a server.LedgerHost (open it with
    `host.ledger()`). A ledger outside the credential's scope is not found, as an unknown one is."""
    if not principal(request).reads(lid):
        raise HTTPException(404, "Ledger not found")
    try:
        return request.app.state.translucent.host(lid)
    except UnknownLedger:
        raise HTTPException(404, "Ledger not found") from None


def may(capability: str):
    """A route dependency: the credential's role may do this (translucent.auth.ROLE_CAPABILITIES), else 403."""
    def check(request: Request):
        cred = principal(request)
        cred.require(capability)
        return cred
    return Depends(check)


# ------------------------------------------------------------ browser sessions (sign in once per session)

class SignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=512)


def _session_body(cred, expires_at: float | None) -> dict:
    return {**cred.public(), "expires_at": expires_at}


@router.post("/session")
def sign_in(body: SignIn, request: Request, response: Response):
    """Trade a person's key for a session cookie (HttpOnly, SameSite=Strict, Path=/api, 12 hours; Secure over
    https). A planner key cannot open a browser session. Failed keys are rate-limited per client address: past the
    limit a wrong key is a 429, while a key that matches always signs in."""
    from translucent import auth
    from translucent.server import TOO_MANY
    gate = request.app.state.translucent.auth
    addr = request.client.host if request.client else "unknown"
    key = body.key.strip()
    cred = gate.by_key(key)
    if cred is None:
        if gate.throttle.fail(addr):
            raise HTTPException(429, TOO_MANY, headers={"Retry-After": "60"})
        raise HTTPException(401, auth.SIGN_IN)
    if cred.kind != "person":
        raise HTTPException(403, "A planner key cannot open a browser session; an agent sends its key with each "
                                 "request")
    token, expires = gate.open_session(cred, key)
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_TTL_S, path="/api", httponly=True,
                        samesite="strict", secure=request.url.scheme == "https")
    return _session_body(cred, expires)


@router.get("/session")
def session(request: Request):
    """Who this browser session (or bearer key) is, or 401. server.AuthGate checks the key or cookie, with the same
    failed-key throttle as every other route."""
    from translucent.server import _principal
    principal_ = _principal(request)
    return _session_body(principal_.credential, principal_.expires_at)


@router.post("/session/end", status_code=204)
def sign_out(request: Request):
    """End this browser session: the stored session and the cookie."""
    from translucent import auth
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        request.app.state.translucent.auth.sessions.end(token)
    out = Response(status_code=204)
    out.delete_cookie(auth.SESSION_COOKIE, path="/api", httponly=True, samesite="strict",
                      secure=request.url.scheme == "https")
    return out


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
    if turn is not None and turn.data.get("phase") != "turn_finished":
        return "running", "planner_thinking", None
    stopped = turn is not None and (turn.data.get("payload") or {}).get("status") == "budget"
    return "running", "planner_stopped" if stopped else "none", None


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
    objective, extra = campaign.objective, {}
    if loop:
        kind = "loop"
        status, next_action, outcome = _loop_status(L, cid, loop)
        if loop[0].type == "objective":    # the current version's summary, and once concluded its three answers
            from translucent.loop_view import summary_fields
            extra = summary_fields(L, cid, loop)
            objective = extra.pop("objective") or objective
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
            "spend_usd": round(spend, 6), **extra}


# ------------------------------------------------------------ ledgers and campaigns

@router.get("/ledgers")
def ledgers(request: Request):
    """Every ledger this server serves that the credential may read: [{id, title, campaigns, datasets}]."""
    server, cred = request.app.state.translucent, principal(request)
    out = []
    for lid in server.workspace.ids():
        if not cred.reads(lid):
            continue
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
            dependencies=[may("upload")])
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


@router.post("/ledgers/{lid}/datasets", status_code=201, dependencies=[may("import_dataset")],
             name="loop.import_dataset")
def import_dataset(body: DatasetImport, request: Request, host=Depends(get_ledger)):
    """Import a team's dataset (a v2 manifest, or a v1 experiment manifest) into this ledger; returns its
    DatasetRecord. The same dataset again returns the stored one; a different one under a used id is a 409; a refused
    file or manifest is a 422 with the plain reason; a path the server may not read is a 403. Dataset ids are per
    ledger."""
    from translucent.loop import NotAllowed
    from translucent.loop_service import LoopService
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
                    # reached without links. The root is this ledger's own folder when laid out per ledger.
                    service = LoopService(L, principal(request).actor(), import_root=server.import_root_for(host.id))
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
    """A person's action, as the credential's person; `author`, if given, must be that name."""
    author: Name | None = None

    def actor(self, cred):
        return cred.acting_as(author=self.author)


class _PersonOrAgent(_Body):
    """An action open to people and agents, as the credential's person or agent; `author` / `agent`, if given, must
    be the credential's name and kind."""
    author: Name | None = None
    agent: Name | None = None

    def actor(self, cred):
        return cred.acting_as(author=self.author, agent=self.agent)


class OpenLoop(_PersonOrAgent):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    objective: dict          # ObjectiveData without version or system snapshot; registered_by defaults to the caller
    memory: Literal["on", "off"] = "on"
    # the engineer's raw records (PUT .../uploads/{records_upload_id}/{name}), read by the planner, never pre-imported
    records_upload_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


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
    budget: dict | None = None      # model_usd, experiments, experiment_usd, max_wall_s; raising one needs an admin


class Note(_Person):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ResultUpload(HumanUpload):
    """A person's returned result: the HumanUpload JSON, and the upload (PUT .../uploads/{upload_id}/{name}) that
    holds its files; no upload when the result is scalars only. The uploader and author is the credential's person
    (`author`, if given, must be that name)."""
    author: str | None = None
    upload_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    def actor(self, cred):
        return cred.acting_as(author=self.author)


def _idempotency_key(request: Request) -> str | None:
    from translucent.loop_surface import check_key
    try:
        return check_key(request.headers.get("Idempotency-Key"))
    except ValueError:
        raise HTTPException(400, "Idempotency-Key must be 1-128 letters, digits or _.:-") from None


@contextmanager
def _loop(host, actor, key: str | None = None, cid: str | None = None, *, may_raise_budget: bool = False):
    """A LoopService on the served ledger (`actor` None: a reader), its refusals said as HTTP statuses (see the
    module docstring)."""
    from translucent.ledger import AdmissionError
    from translucent.loop import NotAllowed, NotFound, StaleRequest
    from translucent.loop_service import Actor, LoopService, ResultRefused
    from translucent.server import _plain
    with host.ledger() as L:
        try:
            yield LoopService(L, actor or Actor("person", "reader", "http"), idempotency_key=key,
                              import_root=host.server.import_root_for(host.id), may_raise_budget=may_raise_budget)
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


# ------------------------------------------------------------ planners: who may open which, and whether it can run

DEFAULT_MODEL_BUDGET_USD = 1.5


def paid_planner_refusal(cred, profile: str) -> tuple[int, str] | None:
    """Why this credential cannot open a paid-planner campaign on this server now, as (HTTP status, reason), or None.
    A planner that is not ready is refused, never replaced by another planner or model."""
    from translucent.server import _live_enabled
    if not cred.may("paid"):
        return 403, f"your role is {cred.role}, not admin: a paid planner needs an administrator key"
    if not _live_enabled():
        return 403, "live mode is off on this server (TRANSLUCENT_LIVE=1 turns it on)"
    if missing := _planner_missing_keys(profile):
        return 503, f"this server is missing {' and '.join(missing)} for the {profile} profile"
    if spent := [p["provider"] for p in _provider_spend(profile) if p["remaining_usd"] <= 0]:
        return 503, f"the {profile} profile's {' and '.join(spent)} provider has spent its cap"
    return None


def _planner_providers(profile: str) -> list[str]:
    """The providers a loop campaign's paid planner calls: only the profile's orchestrator (translucent.loop_planner
    runs its turns through intervention_chat, which calls PROFILES[profile]["orchestrator"]); the profile's other
    roles belong to the microscopy runtime."""
    from translucent.runtime import PROFILES
    return [PROFILES[profile]["orchestrator"][0]]


def _planner_missing_keys(profile: str) -> list[str]:
    """The server's missing provider keys for this profile, kept to those of the providers the planner calls."""
    from translucent import providers, server
    needed = {providers.KEY_VARS[p][0] for p in _planner_providers(profile)}
    return [k for k in server._missing_keys(profile) if k in needed]


def _provider_spend(profile: str) -> list[dict]:
    """Each provider the planner calls with its own cap, committed spend and remainder (translucent.budget). Each
    cap is a separate hard limit, so they are reported apart, never summed."""
    from translucent import budget
    out = []
    for provider in _planner_providers(profile):
        cap, spent = budget.CAPS_USD[provider], round(budget.Ledger(provider).balance()["committed_usd"], 6)
        out.append({"provider": provider, "cap_usd": cap, "spent_usd": spent,
                    "remaining_usd": round(max(0.0, cap - spent), 6)})
    return out


def _free_refusal(cred) -> str | None:
    if not cred.may("open_campaign"):
        return f"your role is {cred.role}: it cannot open a campaign"
    return None


@router.get("/ledgers/{lid}/planners")
def planners(request: Request, host=Depends(get_ledger)):
    """The planners a campaign may be opened with, and whether each is ready for this credential on this server
    now (with the reason when it is not). The paid one is the one a muse campaign opened without a profile runs on
    (the server's TRANSLUCENT_PROFILE): it names the exact model the server will call, and each provider's spend
    against its own cap (translucent.budget)."""
    from translucent.runtime import PROFILES
    from translucent.server import PLANNER, _profile
    cred, profile = principal(request), _profile()
    out = []
    for pid, kind, label in (("bo", "domain_baseline", "Bayesian optimisation (no model)"),
                             ("external", "external", "Your agent over MCP or HTTP (planner key)")):
        reason = _free_refusal(cred)
        out.append({"id": pid, "kind": kind, "label": label, "paid": False, "ready": reason is None,
                    "reason": reason})
    refusal = paid_planner_refusal(cred, profile)
    providers = _provider_spend(profile)
    main = providers[0]   # the orchestrator's provider, where most of a campaign's model spend lands
    out.append({"id": "muse", "kind": "muse", "label": PLANNER[profile],
                "model": PROFILES[profile]["orchestrator"][1], "profile": profile, "paid": True,
                "ready": refusal is None, "reason": refusal[1] if refusal else None, "cap_usd": main["cap_usd"],
                "spent_usd": main["spent_usd"], "remaining_usd": main["remaining_usd"], "providers": providers,
                "default_budget_usd": DEFAULT_MODEL_BUDGET_USD})
    return out


@router.post("/ledgers/{lid}/campaigns", status_code=201, name="loop.open_campaign")
def open_loop_campaign(body: OpenLoop, request: Request, host=Depends(get_ledger)):
    """Register a loop campaign: {campaign, view}. A planner that spends nothing (external, scripted,
    domain_baseline) needs an engineer's or administrator's key; a paid one (muse...) needs an administrator's key,
    live opt-in and the provider keys of its profile (the server's unless the objective names one; stored with it, so
    it runs on it). A planner that is not ready is refused with the reason (GET .../planners), never replaced. The
    model budget is held to the provider caps, as a video campaign's is."""
    from translucent.loop_surface import FREE_PLANNERS, objective_budget_problems, view
    from translucent.server import PLANNER, _profile
    cred = principal(request)
    cred.require("open_campaign")
    actor = body.actor(cred)
    objective = dict(body.objective)
    planner = objective.get("planner") if isinstance(objective.get("planner"), dict) else {}
    paid = isinstance(objective.get("planner"), dict) and planner.get("kind") not in FREE_PLANNERS
    profile = planner.get("profile") if planner.get("profile") in PLANNER else _profile()
    if paid:
        objective["planner"] = {**planner, "profile": profile}   # it runs on the profile its budget was checked on
        if refusal := paid_planner_refusal(cred, profile):
            raise HTTPException(refusal[0], f"The {PLANNER[profile]} planner is not ready: {refusal[1]}")
    if problems := objective_budget_problems(objective, profile):
        raise HTTPException(422, "; ".join(problems))
    objective.setdefault("registered_by", actor.name)
    key = _idempotency_key(request)
    if objective.get("uploads") is not None and body.records_upload_id is None:
        raise HTTPException(422, "uploads are the files sent with records_upload_id, not listed in the objective")

    def act():
        files, folder = None, None
        if body.records_upload_id is not None:
            folder = host.dataset_uploads_dir / body.records_upload_id    # the server's own staging folder
            if not folder.is_dir():
                raise HTTPException(404, "Upload the records first")
            files = {p.name: p.read_bytes() for p in sorted(folder.iterdir())
                     if p.is_file() and not p.name.endswith(".part")}
            try:
                objective["uploads"] = records.describe(files)    # the digests every read is checked against
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
        with _loop(host, actor, key) as service:
            opened = service.open_campaign(objective, body.title, memory=body.memory)
            if files is not None:
                records.stage_uploads(service.L.root, opened["campaign_id"], files, expected=objective["uploads"])
            opened.pop("created")     # HTTP has no ownership: a keyed replay answers as the first call did
            out = {"campaign": opened, "view": view(service, opened["campaign_id"], host.id)}
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)
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


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/measurements", dependencies=[may("define_measurement")],
             name="loop.define_measurement")
def define_measurement(campaign_id: str, body: DefineMeasurement, request: Request, host=Depends(get_ledger)):
    """Define a derived measurement (ops/1) and apply it to the baselines and results: {record, measurements}."""
    key = _idempotency_key(request)

    def act():
        with _loop(host, body.actor(principal(request)), key, campaign_id) as service:
            return service.define_measurement(campaign_id, body.name, body.spec)
    return host.once(request, body, 200, act)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/requests", status_code=201, dependencies=[may("propose")],
             name="loop.propose")
def propose(campaign_id: str, body: Propose, request: Request, host=Depends(get_ledger)):
    """Request an experiment: the sealed request with its content_hash, awaiting a person's approval (or approved at
    once by a rule the registering person wrote). A pending request with the same configuration is returned."""
    from translucent.loop_surface import replay
    key = _idempotency_key(request)

    def act():
        with _loop(host, body.actor(principal(request)), key, campaign_id) as service:
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


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/requests/{request_id}/approve", dependencies=[may("approve")],
             name="loop.approve")
def approve(campaign_id: str, request_id: str, body: Approve, request: Request, host=Depends(get_ledger)):
    """Approve the version whose content_hash the person read; a hash of an older version is a 409 naming the
    version to review."""
    key = _idempotency_key(request)

    def act():
        with _loop(host, body.actor(principal(request)), key, campaign_id) as service:
            out = service.approve(campaign_id, request_id, body.content_hash, body.note)
        _wake_runner(request)
        return out
    return host.once(request, body, 200, act)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/requests/{request_id}/reject", dependencies=[may("reject")],
             name="loop.reject")
def reject(campaign_id: str, request_id: str, body: Reject, request: Request, host=Depends(get_ledger)):
    key = _idempotency_key(request)

    def act():
        with _loop(host, body.actor(principal(request)), key, campaign_id) as service:
            return service.reject(campaign_id, request_id, body.content_hash, body.reason)
    return host.once(request, body, 200, act)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/requests/{request_id}/revise", dependencies=[may("revise")],
             name="loop.revise")
def revise(campaign_id: str, request_id: str, body: Revise, request: Request, host=Depends(get_ledger)):
    """A person's correction of the parameters, as the next version; with approve, approved in the same write."""
    from translucent.loop_surface import replay
    key = _idempotency_key(request)

    def act():
        with _loop(host, body.actor(principal(request)), key, campaign_id) as service:
            out = replay(service, "revise", campaign_id, request_id=request_id, params=body.params) or \
                service.revise(campaign_id, request_id, body.params, body.reason, approve=body.approve)
        if body.approve:
            _wake_runner(request)
        return out
    return host.once(request, body, 200, act)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/requests/{request_id}/cancel", dependencies=[may("cancel")],
             name="loop.cancel")
def cancel(campaign_id: str, request_id: str, body: Cancel, request: Request, host=Depends(get_ledger)):
    """Cancel a request: a person any pending one, an agent only its own before approval."""
    key = _idempotency_key(request)

    def act():
        with _loop(host, body.actor(principal(request)), key, campaign_id) as service:
            return service.cancel(campaign_id, request_id, body.reason)
    return host.once(request, body, 200, act)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/requests/{request_id}/results",
             dependencies=[may("submit_result")], name="loop.submit_result")
def submit_result(campaign_id: str, request_id: str, body: ResultUpload, request: Request,
                  host=Depends(get_ledger)):
    """A person returns a physical test's result against the approved content hash. It is checked as the executor
    checks a solver's output; a refused result is recorded and answered 422 with its reasons. Upload the files
    first (PUT .../uploads/{upload_id}/{name}); they are removed once the result is accepted. An identical
    re-upload of the accepted result returns it without a new record."""
    _idempotency_key(request)
    if body.request_id != request_id:
        raise HTTPException(422, f"the result names request {body.request_id}, not {request_id}")
    actor = body.actor(principal(request))      # the uploader is the result's author
    upload = HumanUpload.model_validate(body.model_dump(exclude={"upload_id"}) | {"author": actor.name})

    def act():      # after the reply cache: a keyed retry finds its answer although the files are gone
        folder, files = None, {}
        if body.upload_id is not None:
            folder = host.dataset_uploads_dir / body.upload_id     # the server's own staging folder
            if not folder.is_dir():
                raise HTTPException(404, "Upload the result's files first")
            files = {p.name: p for p in folder.iterdir() if p.is_file() and not p.name.endswith(".part")}
        with _loop(host, actor, None, campaign_id) as service:
            out = service.submit_result(campaign_id, upload, files)
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)
        return out
    return host.once(request, body, 200, act)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/amendments", dependencies=[may("amend")], name="loop.amend")
def amend(campaign_id: str, body: Amend, request: Request, host=Depends(get_ledger)):
    """A person amends the objective's target, constraints and/or budget as its next version: {amendment,
    comparisons}. Lowering a budget is an engineer's; raising one (model_usd, experiments, experiment_usd, max_wall_s)
    needs an administrator's key."""
    from translucent.loop_surface import replay
    key = _idempotency_key(request)
    cred = principal(request)

    def act():
        with _loop(host, body.actor(cred), key, campaign_id, may_raise_budget=cred.may("raise_budget")) as service:
            return replay(service, "amend", campaign_id, reason=body.reason, target=body.target,
                          constraints=body.constraints, budget=body.budget) or \
                service.amend(campaign_id, body.reason, target=body.target, constraints=body.constraints,
                              budget=body.budget)
    return host.once(request, body, 200, act)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/notes", dependencies=[may("note")], name="loop.note")
def note(campaign_id: str, body: Note, request: Request, host=Depends(get_ledger)):
    """A person's note in the campaign's event stream."""
    _idempotency_key(request)

    def act():
        with _loop(host, body.actor(principal(request)), None, campaign_id) as service:
            if done := chain.concluded(service.L, campaign_id):
                raise HTTPException(409, f"Campaign concluded at seq {done.seq}; further writes are refused")
            return service.note(campaign_id, body.text)
    return host.once(request, body, 200, act)


@router.post("/ledgers/{lid}/campaigns/{campaign_id}/conclusion", dependencies=[may("conclude")],
             name="loop.conclude")
def conclude(campaign_id: str, body: Conclude, request: Request, host=Depends(get_ledger)):
    """Conclude the campaign. The outcome is computed from the recorded comparisons; the caller writes only the
    text. Every later write to the campaign is a 409."""
    key = _idempotency_key(request)

    def act():
        with _loop(host, body.actor(principal(request)), key, campaign_id) as service:
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
