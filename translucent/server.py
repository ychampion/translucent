"""Disk-backed campaign API. Live POSTs require explicit opt-in and a bearer token.

Run one process per state directory (enforced by an OS file lock). Each request
and worker uses its own SQLite connection. SSE re-reads the ledger on every
poll, so reconnecting clients and new server instances need no event cache.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import time
import uuid
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from translucent import analysis, budget, experiments, providers, replay  # analysis registers the sandbox tool
from translucent import api_v1, chain
from translucent.scrub import scrub
from translucent.contracts import Override, ReplayBundle, Review
from translucent.ledger import Ledger  # noqa: F401 - re-exported
from translucent.locks import ExclusiveFileLock, campaign_lock
from translucent.runtime import (Campaigner, Settings, campaign_state, explain_check,  # noqa: F401 - re-exported
                                 new_campaign, PROFILES)
from translucent.intervention_gate import install as _install_intervention_gate
from translucent.workspace import DEFAULT, UnknownLedger, Workspace, device_name

_install_intervention_gate()

DEFAULT_LEDGER = Path("state/ledger")
DEFAULT_STATE = Path("state/api")
ARTIFACT_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
TERMINAL = {"finished", "failed", "interrupted"}
UPLOAD_ID = re.compile(r"[0-9a-f]{32}\Z")
UPLOAD_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
UPLOAD_LIMIT, UPLOAD_TOTAL = 500 * 2**20, 4 * 2**30   # per file, all pending uploads
UPLOAD_FILES, UPLOAD_TTL_S = 3, 24 * 3600
ACTIVE = {"queued", "running", "paused"}
# artifact types a browser shows inline without running anything (svg is an image that runs scripts: not here)
INLINE_TYPES = {"text/csv", "text/tab-separated-values", "text/plain", "application/json", "application/x-ndjson",
                "application/vnd.apache.parquet", "image/png", "image/jpeg", "image/gif", "image/webp", "image/tiff"}
PLANNER_TICK_S = 2.0    # how often the planner thread looks for campaigns with something new
PLANNER_JOIN_S = 10.0   # at shutdown, how long to wait for a planner turn to finish
LOOP_TICK_S = 1.0       # how often the loop runner looks for approved computational requests (an approval wakes it)


def explain_review(review):
    if review.get("reviewer") == "human":  # a person's sign-off or challenge has no gate checks to explain
        review["failed_checks"] = []
        return review
    review["failed_checks"] = [{**c, "explanation": explain_check(c)} for c in review.get("checks", [])
                               if not c["passed"]]
    for check in review.get("checks", []):
        if not check["passed"]:
            check["explanation"] = explain_check(check)
    return review


class MetricTest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    type: Literal["value_within", "events_coincide", "trend"]
    params: dict[str, float | str]


class MetricRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    name: str = Field(min_length=1, max_length=200)
    quantities: list[str] = Field(min_length=1, max_length=10)
    unit: str = Field(min_length=1, max_length=20)
    test: MetricTest
    min_methods: int = Field(default=2, ge=1, le=10)
    needs_verification: bool = True


class ProtocolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    author: str = Field(min_length=1, max_length=80, pattern=r"^[^\x00-\x1f\x7f]+$")
    metrics: list[MetricRequest] = Field(min_length=1, max_length=10)


class CampaignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    objective: str = Field(min_length=1, max_length=10000)
    experiment_id: str = Field(min_length=1, max_length=256)
    profile: Literal["dev", "meta"] | None = None   # default: the server's (TRANSLUCENT_PROFILE)
    budget: float = Field(gt=0)
    protocol: ProtocolRequest | None = None          # metrics fixed before the agent starts


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")
    campaign_id: str
    status: Literal["queued", "running", "paused", "finished", "failed", "interrupted"]
    profile: Literal["dev", "meta"]
    created_at: float
    updated_at: float
    result: dict | None = None
    error: str | None = None
    resume: bool = False            # the worker continues the campaign from the ledger instead of starting it
    budget: float | None = None     # budget for a resumed run (the campaign record keeps its original one)
    rerun: str | None = None        # a what-if rerun that reaches the remote sandbox: the fork id to re-run


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80,
                                        pattern=r"^[^\x00-\x1f\x7f]+$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class _PersonAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    author: Name


class ExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    manifest: dict


def _plain(error: ValueError) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(f"{'.'.join(str(k).replace('_', ' ') for k in p['loc']) or 'manifest'}: "
                         f"{p['msg'].removeprefix('Value error, ')}" for p in error.errors(include_url=False))
    return str(error)


class NoteRequest(_PersonAction):
    text: Text


class AnswerRequest(_PersonAction):
    question_id: str = Field(min_length=1, max_length=64)
    text: Text


class ReviewRequest(_PersonAction):
    claim_id: str = Field(min_length=1, max_length=128)
    verdict: Literal["agree", "challenge"]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""

    @model_validator(mode="after")
    def _challenge_has_reason(self):
        if self.verdict == "challenge" and not self.reason:
            raise ValueError("a challenge needs a reason")
        return self


class ControlRequest(_PersonAction):
    action: Literal["pause", "resume", "stop"]


class ForkRequest(_PersonAction):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    claim_id: str = Field(min_length=1, max_length=128)
    reason: Text
    overrides: list[Override] = Field(min_length=1, max_length=5)
    mode: Literal["rerun", "agent"] = "rerun"
    budget: float | None = Field(default=None, gt=0)          # agent mode: the paid run's budget
    profile: Literal["dev", "meta"] | None = None


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    budget: float = Field(gt=0)
    profile: Literal["dev", "meta"] | None = None   # default: the campaign's last job profile, else "dev"


def _key(campaign_id):
    # Neither campaign ids nor experiment strings are used as filesystem paths.
    return hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()


def _atomic_json(path, obj):
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(obj, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(100):  # Windows refuses to replace a file a reader has open: wait for it to close
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 99:
                    raise
                time.sleep(0.01)
    finally:
        temp.unlink(missing_ok=True)


class _StateLock(ExclusiveFileLock):
    """Kernel-owned lock releases on process death; stale files need no deletion."""
    def __init__(self, path):
        super().__init__(path)
        if not self.try_acquire():
            raise RuntimeError("another server owns this state directory")

    def close(self):
        self.release()


class _ExportLedger:
    """Replay's SVG scratch cache belongs in API state, not the source ledger."""
    def __init__(self, original, root):
        self.original, self.root = original, root
        self.svg_cache_root = original.root / "work"

    def __getattr__(self, name):
        return getattr(self.original, name)


# The planner people see named for each profile (translucent.runtime.PROFILES)
PLANNER = {"dev": "Muse Spark Open", "meta": "Muse Wonder"}


def _within_caps(usd: float, profile: str):
    """A campaign cannot spend more than the caps of the providers its profile calls (translucent.budget.CAPS_USD).
    Loop and MCP campaigns are held to the same caps (translucent.loop_surface)."""
    from translucent.loop_surface import budget_cap
    cap = budget_cap(profile)
    if usd > cap:
        raise HTTPException(422, f"budget: ${usd:g} is more than the ${cap:g} cap on the {profile} profile's providers")


def _missing_keys(profile: str) -> list[str]:
    return providers.missing_keys({p for p, _ in PROFILES[profile].values()})


def _require_keys(profile: str):
    """Refuse a paid run up front when the server lacks a key its profile needs; the worker would only fail later."""
    if missing := _missing_keys(profile):
        raise HTTPException(503, f"This server has no {' or '.join(missing)} for the {profile} profile; "
                                 "add it to the server's environment or .env and restart")


def _profile() -> str:
    """The profile new campaigns run on: TRANSLUCENT_PROFILE (dev or meta), dev when unset or unknown."""
    p = os.environ.get("TRANSLUCENT_PROFILE", "dev")
    return p if p in PLANNER else "dev"


def _live_enabled():
    return os.environ.get("TRANSLUCENT_LIVE") == "1" and bool(os.environ.get("TRANSLUCENT_ADMIN_TOKEN"))


def _authorize(request: Request):
    token = os.environ.get("TRANSLUCENT_ADMIN_TOKEN", "")
    supplied = request.headers.get("Authorization", "").encode("utf-8")
    matched = secrets.compare_digest(supplied, ("Bearer " + token).encode("utf-8"))
    if not _live_enabled() or not matched:
        raise HTTPException(403, "Live campaigns require opt-in and an administrator token")


def _team_enabled():
    return bool(os.environ.get("TRANSLUCENT_TEAM_TOKEN") or os.environ.get("TRANSLUCENT_ADMIN_TOKEN"))


def _server_token(supplied: bytes) -> bool:
    """The Authorization header carries the team or the administrator token."""
    tokens = [t for t in (os.environ.get("TRANSLUCENT_TEAM_TOKEN"), os.environ.get("TRANSLUCENT_ADMIN_TOKEN")) if t]
    return any([secrets.compare_digest(supplied, ("Bearer " + t).encode("utf-8")) for t in tokens])


def _authorize_person(request: Request):
    """People's actions spend nothing: the team or administrator token is enough, live opt-in is not needed."""
    if not _server_token(request.headers.get("Authorization", "").encode("utf-8")):
        raise HTTPException(403, "People's actions require the team token")


JSON_LIMIT = 2**20               # a write's JSON body: an objective, a proposal, a result's JSON (files go by PUT)
MANIFEST_LIMIT = 16 * 2**20      # a manifest of up to 500 records (a real record is about 3.5 kB)
_UPLOAD_ROUTE = re.compile(r"/api/(?:v1/ledgers/[^/]+/)?uploads/[^/]+/[^/]+")
_MANIFEST_ROUTE = re.compile(r"/api/(?:experiments|v1/ledgers/[^/]+/datasets)")


class WriteGate:
    """Every write under /api needs a server token before its body is read (FastAPI parses a body before it runs
    a route's dependencies), and a body larger than its route takes is a 413: by Content-Length, then as it
    streams. The routes still check their own token (an administrator's, for a paid run)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS") or \
                not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        if not _server_token(headers.get(b"authorization", b"")):
            return await JSONResponse({"detail": "Writes require the team token"}, 403)(scope, receive, send)
        path = scope["path"]
        limit = UPLOAD_LIMIT if _UPLOAD_ROUTE.fullmatch(path) else \
            MANIFEST_LIMIT if _MANIFEST_ROUTE.fullmatch(path) else JSON_LIMIT
        too_large = f"The request body can be up to {limit // 2**20} MB"
        length = headers.get(b"content-length", b"")
        if length and (not length.isdigit() or int(length) > limit):
            return await JSONResponse({"detail": too_large}, 413)(scope, receive, send)
        seen = 0

        async def counted():
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > limit:   # FastAPI re-raises an HTTPException met while reading the body
                raise HTTPException(413, too_large)
            return message
        return await self.app(scope, counted, send)


def get_campaign(L, cid):
    campaign = L.get("campaign", cid)
    if campaign is None:
        raise HTTPException(404, "Campaign not found")
    return campaign


def exportable(L, campaign) -> bool:
    """A replay bundle needs a recorded video or an intervention protocol; a loop campaign has neither."""
    from translucent.intervention import records as intervention_records
    return bool(campaign.inputs) or bool(intervention_records(L, campaign.id, "protocol"))


def _questions(L, cid):
    out = {}
    for ev in L.events(cid):
        d, q = ev.data, out.get(ev.data.get("question_id"))
        if ev.type == "question.asked":
            out[d.get("question_id")] = {"question_id": d.get("question_id"), "question": d.get("question"),
                                         "options": d.get("options"), "status": "open", "answer": None,
                                         "asked_at": ev.t}
        elif ev.type == "question.answered" and q is not None and q["answer"] is None:
            q["status"], q["answer"] = "answered", {"text": d.get("text"), "author": d.get("author"), "t": ev.t}
        elif ev.type == "question.expired" and q is not None and q["status"] == "open":
            q["status"] = "expired"
    return out


class Server:
    """What one API process shares across the ledgers it serves: the workspace, the campaign factory, shutdown
    signals and the upload space. `app.state.translucent` holds it; api_v1 reaches ledgers through `host`."""

    def __init__(self, app, workspace: Workspace, factory, poll_interval, import_root, loop_workers=2):
        self.app, self.workspace, self.factory, self.poll_interval = app, workspace, factory, poll_interval
        self.import_root = Path(import_root) if import_root else None
        self.stopping, self.warm_stop = threading.Event(), threading.Event()   # stopping: workers end their waits
        self.loop_workers, self.loop_wake, self.loop_reported = loop_workers, threading.Event(), set()
        self.upload_mutex, self.in_flight = threading.Lock(), set()
        self.hosts: dict[str, LedgerHost] = {}
        self.hosts_mutex = threading.Lock()

    def host(self, lid) -> "LedgerHost":
        """The served ledger with this id; UnknownLedger (a 404) when there is none, with nothing created."""
        with self.hosts_mutex:
            if lid not in self.hosts:
                self.workspace.path(lid)      # UnknownLedger before any folder is made
                self.hosts[lid] = LedgerHost(self, lid)
            return self.hosts[lid]

    def upload_roots(self):
        return [root for h in list(self.hosts.values()) for root in (h.uploads_dir, h.dataset_uploads_dir)
                if root.is_dir()]

    async def receive_upload(self, root: Path, upload_id: str, filename: str, request: Request, *, files: int,
                             too_many: str):
        """One file streamed to root/<upload_id>/<filename>. Limits are per file, per upload and for all pending
        uploads on this server (every ledger's)."""
        if not UPLOAD_ID.fullmatch(upload_id) or not UPLOAD_NAME.fullmatch(filename) or \
                filename in {"manifest.json", "record.json", "record.sha256"}:
            raise HTTPException(400, "Use a plain file name (letters, digits, dot, dash, underscore)")
        if device_name(filename):
            raise HTTPException(422, f"{filename} is a device name on Windows, not a file name; rename the file")
        folder = root / upload_id
        root.mkdir(parents=True, exist_ok=True)
        for old in root.iterdir():  # unregistered uploads expire after a day
            if old.is_dir() and old != folder and time.time() - old.stat().st_mtime > UPLOAD_TTL_S:
                shutil.rmtree(old, ignore_errors=True)
        # check and reserve under one lock: each upload in flight holds a full-size slot until it ends
        slot = (str(folder), filename, uuid.uuid4().hex)
        with self.upload_mutex:
            done = [p for r in self.upload_roots() for p in r.rglob("*")
                    if p.is_file() and not p.name.endswith(".part")]
            mine = {p.name for p in done if p.parent == folder} | {s[1] for s in self.in_flight if s[0] == slot[0]}
            if filename not in mine and len(mine) >= files:
                raise HTTPException(409, too_many)
            if sum(p.stat().st_size for p in done) + (len(self.in_flight) + 1) * UPLOAD_LIMIT > UPLOAD_TOTAL:
                raise HTTPException(507, "Upload space is full; try again later")
            self.in_flight.add(slot)
        folder.mkdir(exist_ok=True)
        part, size = folder / (filename + "." + slot[2] + ".part"), 0
        try:
            with part.open("wb") as out:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > UPLOAD_LIMIT:
                        raise HTTPException(413, "Files can be up to 500 MB")
                    out.write(chunk)
            os.replace(part, folder / filename)
        finally:
            part.unlink(missing_ok=True)
            with self.upload_mutex:
                self.in_flight.discard(slot)
        return {"name": filename, "bytes": size}

    def run_loop_requests(self):
        """The loop runner thread: run approved computational requests of every ledger, and recover runs a crash
        abandoned (translucent.loop_runner). MCP sessions tick the same requests inline; the claim lock and chain
        admission keep each request to one execution, whichever process gets it."""
        from translucent.loop_runner import LoopRunner
        log = logging.getLogger(__name__)
        while not self.stopping.is_set():
            for lid in self.workspace.ids():
                if self.stopping.is_set():
                    break
                try:
                    runner = LoopRunner(self.workspace.path(lid), process="api", workers=self.loop_workers)
                    runner.tick()
                    problems = runner.problems
                except Exception as exc:  # noqa: BLE001 - one ledger's trouble must not stop the others
                    problems = [f"{type(exc).__name__}: {exc}"]
                for problem in problems:
                    first = problem.splitlines()[0]
                    if (lid, first) not in self.loop_reported and len(self.loop_reported) < 1000:
                        self.loop_reported.add((lid, first))   # once each: a missing system stays missing
                        log.warning("Loop runner, ledger %s: %s", lid, problem)
            self.loop_wake.wait(LOOP_TICK_S)
            self.loop_wake.clear()

    def run_planner_turns(self):
        """The planner thread: after a result, an amendment or a new campaign, run each loop campaign's planner turn
        (translucent.loop_planner.planner_tick). The domain baseline always runs; a model planner only when live
        execution is on, metered and held to its campaign's model budget. A turn lock keeps each campaign to one
        turn at a time across processes."""
        from translucent.loop_planner import default_session_for, planner_tick
        log = logging.getLogger(__name__)
        while not self.stopping.is_set():
            for lid in self.workspace.ids():
                if self.stopping.is_set():
                    break
                try:
                    for turn in planner_tick(self.workspace.path(lid), default_session_for(live=_live_enabled())):
                        if turn.get("status") not in ("proposed", "waiting", "concluded", "busy"):
                            log.info("Planner turn, ledger %s, campaign %s: %s %s", lid, turn.get("campaign_id"),
                                     turn.get("status"), turn.get("reason", ""))
                        self.loop_wake.set()            # a proposal an auto-approval rule accepted can run now
                except Exception as exc:  # noqa: BLE001 - one ledger's trouble must not stop the others
                    first = f"{type(exc).__name__}: {exc}".splitlines()[0]
                    if (lid, first) not in self.loop_reported and len(self.loop_reported) < 1000:
                        self.loop_reported.add((lid, first))
                        log.warning("Planner, ledger %s: %s", lid, first)
            self.stopping.wait(PLANNER_TICK_S)

    def prewarm(self):
        try:
            for lid in self.workspace.ids():
                if self.warm_stop.is_set():
                    break
                self.host(lid).prewarm()
        finally:
            self.app.state.export_ready.set()


class LedgerHost:
    """One ledger this process serves, with its own state folder: jobs (one legacy paid job at a time per ledger),
    uploads, idempotency replies and the export cache. Each request and worker opens its own connection."""

    def __init__(self, server: Server, lid: str):
        self.server, self.id = server, lid
        self.state = server.workspace.state_dir(lid)
        self.jobs_dir, self.cache_dir = self.state / "jobs", self.state / "bundles"
        self.uploads_dir, self.dataset_uploads_dir = self.state / "uploads", self.state / "dataset-uploads"
        self.replies_dir = self.state / "idempotency"
        self.mutex, self.export_mutex, self.people_mutex = threading.Lock(), threading.Lock(), threading.Lock()
        self.register_mutex, self.replies_mutex = threading.Lock(), threading.Lock()
        self.loop_export_mutex = threading.Lock()
        self.worker = None
        # the default ledger keeps its legacy artifact links; others link through /api/v1
        self.artifacts = "/api/artifacts/" if lid == DEFAULT else f"/api/v1/ledgers/{lid}/artifacts/"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self.server.workspace.path(self.id)

    @contextmanager
    def ledger(self):
        with self.server.workspace.open(self.id) as L:
            yield L

    # ------------------------------------------------------------ jobs

    def job_path(self, cid):
        return self.jobs_dir / (_key(cid) + ".json")

    def load_job(self, cid):
        path = self.job_path(cid)
        if not path.exists():
            return None
        job = Job.model_validate_json(path.read_text(encoding="utf-8"))
        if job.campaign_id != cid:
            raise ValueError("job campaign identity mismatch")
        return job

    def jobs(self):
        result = []
        for path in sorted(self.jobs_dir.glob("*.json")):
            job = Job.model_validate_json(path.read_text(encoding="utf-8"))
            if path != self.job_path(job.campaign_id):
                raise ValueError("job filename identity mismatch")
            result.append(job)
        return result

    def save(self, job, **changes):
        job = Job.model_validate({**job.model_dump(), **changes, "updated_at": time.time()})
        _atomic_json(self.job_path(job.campaign_id), job.model_dump())
        return job

    def require_free_slot(self):
        """Call holding self.mutex: one legacy paid job at a time in this ledger."""
        if any(job.status in ACTIVE for job in self.jobs()):
            raise HTTPException(409, "A live campaign is already queued, running or paused")

    def execute(self, cid):
        with self.mutex:
            job = self.load_job(cid)
            job = self.save(job, status="running")

        def on_status(status):  # the runtime pauses and resumes between steps
            with self.mutex:
                current = self.load_job(cid)
                if current is not None and current.status in ("running", "paused"):
                    self.save(current, status=status)
        lock = campaign_lock(self.path, cid)
        try:
            # an MCP --adopt writer takes this lock for each call: wait out a call in progress, then hold it
            deadline = time.monotonic() + 60
            while not lock.try_acquire():
                if time.monotonic() > deadline:
                    raise RuntimeError("an MCP session kept writing to this campaign; stop it, then resume")
                time.sleep(0.1)
            with self.ledger() as L:
                campaign = L.get("campaign", cid)
                if campaign is None:
                    raise ValueError("queued campaign missing from ledger")
                if job.rerun is not None:
                    from translucent.rerun import rerun
                    result = rerun(L, job.rerun)
                else:
                    if job.budget is not None:
                        campaign = campaign.model_copy(update={"budget_usd": job.budget})
                    camp = self.server.factory(L, campaign, Settings(
                        profile=job.profile, ask_wait_s=900, on_status=on_status,
                        shutting_down=self.server.stopping.is_set))
                    result = camp.resume() if job.resume else camp.run()
            with self.mutex:
                self.save(job, status="finished", result=result)
        except Exception as exc:
            where = "" if self.id == DEFAULT else f" in ledger {self.id}"
            logging.getLogger(__name__).error("Campaign %s%s failed: %s: %s", cid, where, type(exc).__name__, exc,
                                              exc_info=exc)
            with self.mutex:
                self.save(job, status="failed", error=f"{type(exc).__name__}: {exc}"[:1000])
        finally:
            lock.release()

    def start(self, cid):
        self.worker = threading.Thread(target=self.execute, args=(cid,), name=f"translucent-campaign-{self.id}",
                                       daemon=True)
        self.worker.start()

    def launch(self, job):
        """Save a queued job and start its worker (call holding self.mutex)."""
        self.save(job)
        try:
            self.start(job.campaign_id)
        except Exception as exc:
            self.save(job, status="failed", error=f"Worker startup failed: {type(exc).__name__}")
            raise HTTPException(503, "Campaign worker unavailable") from exc

    def recover(self):
        """At startup: a job the last server left running was interrupted; a queued one starts when live is on."""
        with self.mutex:
            for job in self.jobs():
                if job.status in ("running", "paused"):
                    self.save(job, status="interrupted", error="Server stopped while campaign was running")
            queued = [job for job in self.jobs() if job.status == "queued"]
            # A queued job was previously authorized. Do not start it while
            # live execution is disabled or its administrator is unconfigured.
            if queued and _live_enabled():
                self.start(queued[0].campaign_id)

    def queue_video_campaign(self, body: CampaignRequest):
        """POST /api/campaigns on this ledger: a paid agent run on a recorded experiment (authorized by the caller)."""
        _within_caps(body.budget, body.profile or _profile())
        _require_keys(body.profile or _profile())
        with self.mutex:
            self.require_free_slot()
            with self.ledger() as L:
                video = next((a for a in L.list("artifact") if a.kind == "video" and (
                    a.id == body.experiment_id or (a.source is not None and
                    a.source.uri == "experiment:" + body.experiment_id))), None)
                if video is None:
                    raise HTTPException(404, "Recorded experiment not found")
                pre = None
                if body.protocol is not None:
                    from translucent.contracts import Metric, Predicate, Protocol
                    from translucent.protocol import protocol_problems
                    pre = Protocol(id=f"protocol_{uuid.uuid4().hex[:10]}", campaign_id="", author=body.protocol.author,
                                   created_at=time.time(), metrics=[
                                       Metric(id=m.id or f"metric_{n}", name=m.name, quantities=m.quantities,
                                              unit=m.unit, min_methods=m.min_methods,
                                              needs_verification=m.needs_verification,
                                              predicate=Predicate(type=m.test.type, params=m.test.params,
                                                                  measurement_ids=[]))
                                       for n, m in enumerate(body.protocol.metrics, 1)])
                    if problems := protocol_problems(pre):
                        raise HTTPException(422, "; ".join(problems))
                c = new_campaign(L, body.objective, body.objective[:90], [video.id], budget_usd=body.budget)
                if pre is not None:   # fixed before the campaign is queued, so before it starts
                    L.add(pre.model_copy(update={"campaign_id": c.id}))
            job = Job(campaign_id=c.id, status="queued", profile=body.profile or _profile(),
                      created_at=time.time(), updated_at=time.time())
            self.launch(job)
            return {**c.model_dump(), "job": job.model_dump()}

    # ------------------------------------------------------------ reading

    def view(self, campaign):
        job = self.load_job(campaign.id)
        return {**campaign.model_dump(), "job": job.model_dump() if job else None}

    def state_of(self, L, cid):
        """'queued', 'running', 'paused', 'ended' or 'new'. A worker job, when there is one, outranks the event log."""
        job = self.load_job(cid)
        if job is not None and job.status in TERMINAL:
            return "ended"
        if job is not None:
            return job.status
        return campaign_state(L, cid)[0]

    def require_active(self, L, cid):
        if self.state_of(L, cid) not in ("running", "paused"):
            raise HTTPException(409, "The campaign is not running")

    def events_response(self, campaign_id: str, request: Request, last_event_id: str | None):
        with self.ledger() as L:
            get_campaign(L, campaign_id)
        if last_event_id is not None and not re.fullmatch(r"[0-9]{1,20}", last_event_id):
            raise HTTPException(400, "Last-Event-ID must be a nonnegative sequence number")
        after = int(last_event_id or 0)
        if after > 2**63 - 1:
            raise HTTPException(400, "Last-Event-ID exceeds the ledger sequence range")

        def poll(cursor):
            # Read job completion first, then take a ledger snapshot. A worker
            # writes its final events before its terminal job status; reversing
            # these reads could close the stream just before a final event.
            job = self.load_job(campaign_id)
            with self.ledger() as L:
                L.db.execute("BEGIN")
                batch = L.events(campaign_id, after_seq=cursor)
                # people may review findings after the end: the stream ends on the last start/resume/finish
                last = L.db.execute("SELECT body FROM event WHERE campaign_id=? AND (body LIKE ? OR body LIKE ? "
                                    "OR body LIKE ?) ORDER BY seq DESC LIMIT 1",
                                    (campaign_id, '%"type":"campaign.finished"%', '%"type":"campaign.started"%',
                                     '%"type":"campaign.resumed"%')).fetchone()
                ended = last is not None and json.loads(last[0])["type"] == "campaign.finished"
                # a loop campaign has no job and no campaign.finished: its stream ends at its conclusion
                ended = ended or chain.concluded(L, campaign_id) is not None
                L.db.execute("COMMIT")
            return batch, ended or (job is not None and job.status in TERMINAL)

        async def stream():
            cursor, heartbeat = after, time.monotonic()
            while not await request.is_disconnected():
                batch, ended = await asyncio.to_thread(poll, cursor)
                for event in batch:
                    body = event.model_dump(mode="json")
                    if isinstance(body["data"].get("review"), dict):
                        explain_review(body["data"]["review"])
                    yield f"id: {event.seq}\nevent: {event.type}\ndata: {json.dumps(body)}\n\n"
                    cursor = event.seq
                if ended:
                    break
                if time.monotonic() - heartbeat >= 15:
                    yield ": keep-alive\n\n"
                    heartbeat = time.monotonic()
                await asyncio.sleep(self.server.poll_interval)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def artifact_response(self, artifact_id: str):
        if not ARTIFACT_ID.fullmatch(artifact_id):
            raise HTTPException(404, "Artifact not found")
        with self.ledger() as L:
            record = L.get("artifact", artifact_id)
            if record is None:
                raise HTTPException(404, "Artifact not found")
            path = L.blob_path(artifact_id)
            if not path.resolve().is_relative_to(L.blobs.resolve()) or not path.is_file():
                raise HTTPException(404, "Artifact bytes not found")
        # anyone can store a file (a person's upload names any type): nothing served here may run on this origin.
        # Plain data and media show inline; anything else (HTML, SVG, scripts) only downloads, in a sandbox.
        media = record.media_type.split(";")[0].strip().lower()
        headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"}
        if media not in INLINE_TYPES and not media.startswith("video/"):
            headers["Content-Disposition"] = "attachment"
        return FileResponse(path, media_type=record.media_type, headers=headers)

    def cached_bundle(self, campaign_id):
        with self.export_mutex, self.ledger() as L:
            c = get_campaign(L, campaign_id)
            if not exportable(L, c):
                raise HTTPException(409, "Campaign has no recorded video")
            # A conservative global revision also invalidates on new evidence,
            # reviews or invalidations that were not accompanied by an event.
            stamp = [3] + [L.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("record", "event", "invalidation")]
            out = self.cache_dir / _key(campaign_id)
            out.mkdir(parents=True, exist_ok=True)
            target, version = out / "bundle.json", out / "source.json"
            if not target.exists() or not version.exists() or json.loads(version.read_text()) != stamp:
                exported = replay.export(_ExportLedger(L, out), campaign_id, out / "export",
                                         include_verification=False)
                if c.inputs:
                    exported = exported.model_copy(update={"video": self.artifacts + c.inputs[0]})
                _atomic_json(target, exported.model_dump(mode="json"))
                _atomic_json(version, stamp)
            return ReplayBundle.model_validate_json(target.read_text(encoding="utf-8"))

    def loop_export(self, cid: str):
        """An open file of the loop campaign's export (translucent.loop_export), made once per ledger revision and
        one at a time per ledger: reads need no token, and an export reads and packs every blob the campaign cites.
        Each revision has its own file, so a newer one never replaces a file being served (Windows refuses that)."""
        from translucent import loop_export
        if not self.loop_export_mutex.acquire(timeout=api_v1.BUSY_WAIT_S):     # open reads must not pile up threads
            raise HTTPException(503, "Another export is being made; try again shortly",
                                headers={"Retry-After": "5"})
        try:
            with self.ledger() as L:
                # insert-only tables: their last rowids name the ledger's revision
                stamp = [L.db.execute(f"SELECT MAX(rowid) FROM {t}").fetchone()[0]
                         for t in ("record", "event", "invalidation")]
                out = self.cache_dir / ("loop-" + _key(cid))
                key = json.dumps([loop_export.EXPORT_VERSION, stamp]).encode()   # a new format is a new file
                target = out / (hashlib.sha256(key).hexdigest()[:24] + ".loop.json")
                if not target.exists():
                    out.mkdir(parents=True, exist_ok=True)
                    temp = out / f"{uuid.uuid4().hex}.tmp.json"
                    try:
                        loop_export.export(L, cid, temp, ledger=self.id)
                        os.replace(temp, target)
                    finally:
                        temp.unlink(missing_ok=True)
                    for old in out.iterdir():
                        if old != target:
                            try:
                                old.unlink()
                            except OSError:   # still being served: removed with the next revision
                                pass
                return target.open("rb")
        finally:
            self.loop_export_mutex.release()

    def prewarm(self):
        """Prepare the export of every campaign with a recorded video, so its first bundle request is fast. Loop
        and other campaigns without a video are never exported here."""
        errors = self.server.app.state.export_errors
        with self.ledger() as L:
            ids = [c.id for c in L.list("campaign") if c.inputs]
        for cid in ids:
            if self.server.warm_stop.is_set():
                break
            try:
                self.cached_bundle(cid)
            except Exception as exc:
                errors[cid if self.id == DEFAULT else f"{self.id}/{cid}"] = f"{type(exc).__name__}: {exc}"
                logging.getLogger(__name__).warning("Could not prepare campaign %s: %s", cid, exc)

    def branch_view(self, L, fork):
        state = next((e.data for e in reversed(L.events(fork.campaign_id)) if e.type == "campaign.finished"), None)
        job = self.load_job(fork.campaign_id)
        return {"fork": fork.model_dump(mode="json"), "branch": L.get("campaign", fork.campaign_id).model_dump(),
                "status": (state or {}).get("status") or (job.status if job else "running"),
                "reason": (state or {}).get("reason"), "job": job.model_dump() if job else None,
                "findings": [{**k.model_dump(mode="json"), "status": L.claim_status(k.id)}
                             for k in L.list("claim", campaign_id=fork.campaign_id)]}

    def once(self, request: Request, body: BaseModel, status: int, act):
        """A person's write with an Idempotency-Key header runs once: the same key and body again returns the first
        response; the same key with a different body is a 409. Keys are per token, method and path (so per ledger)."""
        key = request.headers.get("Idempotency-Key")
        if key is None:
            return act()
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", key):
            raise HTTPException(400, "Idempotency-Key must be 1-128 letters, digits or _.:-")
        principal = hashlib.sha256(request.headers.get("Authorization", "").encode("utf-8")).hexdigest()
        slot = hashlib.sha256(json.dumps([principal, request.method, request.url.path, key]).encode()).hexdigest()
        digest = hashlib.sha256(body.model_dump_json().encode("utf-8")).hexdigest()
        path = self.replies_dir / f"{slot}.json"
        with self.replies_mutex:
            if path.exists():
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved["body"] != digest:
                    raise HTTPException(409, "This Idempotency-Key was already used with a different request")
                return JSONResponse(saved["response"], status_code=saved["status"])
            result = jsonable_encoder(act())
            self.replies_dir.mkdir(parents=True, exist_ok=True)
            _atomic_json(path, {"body": digest, "status": status, "response": result})
            return result


def create_app(ledger_dir=None, state_dir=None, *, campaigner_factory=None, poll_interval=0.25, data_root=None,
               import_root=None, loop_workers=2):
    """One process serving one ledger (`ledger_dir`, the legacy form) or every ledger under `data_root`.

    The legacy /api/... routes serve the ledger with id "default": `ledger_dir` itself, or data_root/ledgers/default
    when there is one. /api/v1/ledgers/{id}/... (translucent.api_v1) serves any of them. `import_root` (or
    TRANSLUCENT_IMPORT_ROOT) is the one server folder dataset imports may read by path. `loop_workers` computational
    loop requests run at once in this process (0: none run here; an MCP session or another server runs them)."""
    data_root = data_root or (None if ledger_dir else os.environ.get("TRANSLUCENT_DATA_ROOT"))
    if data_root:
        if ledger_dir:
            raise ValueError("serve a data root or one ledger, not both")
        workspace = Workspace(data_root, state_root=state_dir or os.environ.get("TRANSLUCENT_STATE_DIR"))
    else:
        ledger_dir = Path(ledger_dir or os.environ.get("TRANSLUCENT_LEDGER_DIR", DEFAULT_LEDGER))
        state_dir = Path(state_dir or os.environ.get("TRANSLUCENT_STATE_DIR", DEFAULT_STATE))
        workspace = Workspace(ledgers={DEFAULT: ledger_dir}, state_root=state_dir)
    warmer = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal warmer
        runner = planner = None
        lock = _StateLock(workspace.state_root / "server.lock")
        try:
            if workspace.legacy:   # the configured folder: created when missing, as the single-ledger server did
                with workspace.open(DEFAULT):
                    pass
            for lid in workspace.ids():
                ctx.host(lid).recover()
            warmer = threading.Thread(target=ctx.prewarm, name="translucent-export-cache", daemon=True)
            warmer.start()
            if loop_workers > 0:
                runner = threading.Thread(target=ctx.run_loop_requests, name="translucent-loop-runner", daemon=True)
                runner.start()
                planner = threading.Thread(target=ctx.run_planner_turns, name="translucent-loop-planner", daemon=True)
                planner.start()
            yield
        finally:
            # Retain exclusive ownership until the workers close their ledgers.
            # A hard process exit leaves a durable running job for recovery.
            ctx.stopping.set()  # a worker waiting on a person or paused ends its wait instead of holding shutdown
            ctx.loop_wake.set()
            if runner is not None:
                await asyncio.to_thread(runner.join)   # a request it is running finishes within its wall limit
            if planner is not None:   # a model call in flight is not waited for; the next start closes its turn
                await asyncio.to_thread(planner.join, PLANNER_JOIN_S)
            for h in list(ctx.hosts.values()):
                if h.worker is not None:
                    await asyncio.to_thread(h.worker.join)
            ctx.warm_stop.set()
            if warmer is not None:
                await asyncio.to_thread(warmer.join)
            lock.close()

    app = FastAPI(title="Translucent", lifespan=lifespan)
    ctx = Server(app, workspace, campaigner_factory or Campaigner, poll_interval,
                 import_root or os.environ.get("TRANSLUCENT_IMPORT_ROOT"), loop_workers)
    app.state.translucent = ctx

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # FastAPI's own 422 echoes the input and cannot render an out-of-range number such as 1e999 (a 500)
        return JSONResponse({"detail": "; ".join(f"{'.'.join(str(k) for k in p['loc'][1:]) or 'request'}: {p['msg']}"
                                                 for p in exc.errors())}, status_code=422)

    @app.exception_handler(UnknownLedger)
    async def unknown_ledger(request, exc):
        return JSONResponse({"detail": "Ledger not found"}, status_code=404)

    app.state.export_ready = threading.Event()
    app.state.export_errors = {}
    origins = [x.strip() for x in os.environ.get("TRANSLUCENT_CORS_ORIGINS", "").split(",") if x.strip()]
    if "*" in origins:
        raise ValueError("TRANSLUCENT_CORS_ORIGINS must list explicit origins")
    app.add_middleware(WriteGate)     # inside CORS, so a refused write still carries the CORS headers
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PUT"],
                       allow_headers=["Authorization", "Last-Event-ID", "Content-Type", "Idempotency-Key"])

    def default() -> LedgerHost:
        return ctx.host(DEFAULT)    # UnknownLedger (404) on a data root without a default ledger

    @contextmanager
    def ledger():
        with default().ledger() as L:
            yield L

    @app.get("/api/campaigns")
    def campaigns():
        h = default()
        with h.ledger() as L:
            return [h.view(c) for c in L.list("campaign")]

    @app.get("/api/campaigns/{campaign_id}")
    def campaign(campaign_id: str):
        h = default()
        with h.ledger() as L:
            return h.view(get_campaign(L, campaign_id))

    @app.get("/api/campaigns/{campaign_id}/events")
    def events(campaign_id: str, request: Request, last_event_id: str | None = Header(None)):
        return default().events_response(campaign_id, request, last_event_id)

    @app.get("/api/campaigns/{campaign_id}/bundle")
    def bundle(campaign_id: str):
        body = default().cached_bundle(campaign_id).model_dump(mode="json")
        for review in body["reviews"]:
            explain_review(review)
        for event in body["events"]:
            if isinstance(event["data"].get("review"), dict):
                explain_review(event["data"]["review"])
        return body

    @app.get("/api/campaigns/{campaign_id}/graph")
    def campaign_graph(campaign_id: str):
        from translucent.graph import campaign_graph as build_graph
        with ledger() as L:
            get_campaign(L, campaign_id)
            return build_graph(L, campaign_id)

    @app.get('/api/campaigns/{campaign_id}/intervention')
    def intervention_campaign(campaign_id: str):
        from translucent.intervention import records as intervention_records
        from translucent.intervention_export import projection
        with ledger() as L:
            get_campaign(L,campaign_id)
            if not intervention_records(L,campaign_id,'protocol'):raise HTTPException(404,'No intervention campaign')
            return projection(L,campaign_id).model_dump(mode='json',exclude={'receipt'})

    @app.get('/api/campaigns/{campaign_id}/intervention/{section}')
    def intervention_section(campaign_id: str,section: str):
        allowed={'trials','hypotheses','policies','decisions','scores','comparisons','updates','next_experiments','procedure','protocol'}
        if section not in allowed:raise HTTPException(404,'No intervention section')
        return intervention_campaign(campaign_id)[section]

    @app.get("/api/findings/{claim_id}/evidence")
    def finding_evidence(claim_id: str):
        from translucent.verify import evidence
        with ledger() as L:
            if L.get("claim", claim_id) is None:
                raise HTTPException(404, "Finding not found")
            from translucent.advice import signoffs
            out = evidence(L, claim_id)
            out["status"] = L.claim_status(claim_id)
            out["signoffs"] = signoffs(L, claim_id)
            return out

    @app.get("/api/compare")
    def compare(a: str, b: str):
        from translucent.diff import compare as diff
        with ledger() as L:
            try:
                return diff(L, a, b)
            except LookupError as e:
                raise HTTPException(404, str(e)) from e
            except ValueError as e:
                raise HTTPException(400, str(e)) from e

    @app.get("/api/artifacts/{artifact_id:path}")
    def artifact(artifact_id: str):
        return default().artifact_response(artifact_id)

    @app.get("/api/budget")
    def balances():
        return budget.balances()

    @app.post("/api/campaigns", status_code=202, dependencies=[Depends(_authorize)])
    def post_campaign(body: CampaignRequest):
        return default().queue_video_campaign(body)

    # ------------------------------------------------------------ people in the loop

    @app.get("/api/session")
    def session():
        # live only when a campaign on the server's profile could run: opted in, with the profile's provider keys
        return {"live": _live_enabled() and not _missing_keys(_profile()), "team": _team_enabled(),
                "model": PLANNER[_profile()]}

    @app.get("/api/experiments")
    def recorded_experiments():
        with ledger() as L:
            return experiments.list_experiments(L)

    @app.put("/api/uploads/{upload_id}/{filename}", status_code=201, dependencies=[Depends(_authorize_person)])
    async def upload(upload_id: str, filename: str, request: Request):
        """One file of a new experiment, streamed to disk; register it with POST /api/experiments."""
        return await ctx.receive_upload(default().uploads_dir, upload_id, filename, request, files=UPLOAD_FILES,
                                        too_many="An experiment has at most a video, a clock file and a voltage file")

    @app.post("/api/experiments", status_code=201, dependencies=[Depends(_authorize_person)])
    def add_experiment(body: ExperimentRequest):
        h = default()
        folder = h.uploads_dir / body.upload_id
        if not folder.is_dir():
            raise HTTPException(404, "Upload the recording and its files first")
        # its own lock: decoding a large recording must not hold up campaigns or people's actions
        if not h.register_mutex.acquire(timeout=1):
            raise HTTPException(409, "Another recording is being added; try again in a minute")
        try:
            with h.ledger() as L:
                try:
                    video = experiments.register(L, experiments.scrubbed(body.manifest), folder)
                except ValueError as e:  # includes pydantic's ValidationError: the person's input, said plainly
                    raise HTTPException(422, _plain(e)) from e  # files stay (until they expire) to retry the manifest
                entry = next(x for x in experiments.list_experiments(L) if x["video_id"] == video.id)
        finally:
            h.register_mutex.release()
        shutil.rmtree(folder, ignore_errors=True)
        return entry

    @app.get("/api/campaigns/{campaign_id}/people")
    def people(campaign_id: str):
        with ledger() as L:
            get_campaign(L, campaign_id)
            notes = [{"seq": e.seq, "t": e.t, "text": e.data.get("text"), "author": e.data.get("author")}
                     for e in L.events(campaign_id) if e.type == "human.note"]
            reviews = [r.model_dump(mode="json") for k in L.list("claim", campaign_id=campaign_id)
                       for r in L.list("review", ref=k.id) if r.reviewer == "human"]
            return {"questions": list(_questions(L, campaign_id).values()), "notes": notes, "reviews": reviews}

    @app.post("/api/campaigns/{campaign_id}/notes", status_code=201, dependencies=[Depends(_authorize_person)])
    def post_note(campaign_id: str, body: NoteRequest, request: Request):
        h = default()

        def act():
            text = scrub(body.text)
            with h.ledger() as L:
                get_campaign(L, campaign_id)
                h.require_active(L, campaign_id)
                ev = L.emit(campaign_id, "human.note", "person:" + body.author, f"{body.author} left a note",
                            detail=text, data={"text": text, "author": body.author})
            return {"event": ev.model_dump(mode="json")}
        return h.once(request, body, 201, act)

    @app.post("/api/campaigns/{campaign_id}/answers", status_code=201, dependencies=[Depends(_authorize_person)])
    def post_answer(campaign_id: str, body: AnswerRequest, request: Request):
        h = default()

        def act():
            text = scrub(body.text)
            with h.people_mutex, h.ledger() as L:
                get_campaign(L, campaign_id)
                q = _questions(L, campaign_id).get(body.question_id)
                if q is None:
                    raise HTTPException(404, "Question not found")
                if q["status"] != "open":
                    raise HTTPException(409, f"The question is no longer open ({q['status']})")
                ev = L.emit(campaign_id, "question.answered", "person:" + body.author,
                            f"{body.author} answered: {text}", detail=text,
                            data={"question_id": body.question_id, "text": text, "author": body.author})
            return {"event": ev.model_dump(mode="json")}
        return h.once(request, body, 201, act)

    @app.post("/api/campaigns/{campaign_id}/reviews", status_code=201, dependencies=[Depends(_authorize_person)])
    def post_review(campaign_id: str, body: ReviewRequest, request: Request):
        h = default()
        agree = body.verdict == "agree"

        def act():
            with h.ledger() as L:
                get_campaign(L, campaign_id)
                claim = L.get("claim", body.claim_id)
                if claim is None or claim.campaign_id != campaign_id:
                    raise HTTPException(404, "Finding not found in this campaign")
                rv = L.add(Review(id=f"rev_{uuid.uuid4().hex[:12]}", claim_id=claim.id, reviewer="human",
                                  verdict="supported" if agree else "refuted", author=body.author,
                                  created_at=time.time(),
                                  reason=scrub(body.reason) or f"{body.author} agrees with this finding."))
                L.emit(campaign_id, "claim.reviewed", "person:" + body.author,
                       f"{body.author} {'agreed with' if agree else 'challenged'} a finding", detail=rv.reason,
                       refs=[claim.id, rv.id], data={"review": rv.model_dump()})
            return {"review": rv.model_dump(mode="json")}
        return h.once(request, body, 201, act)

    @app.post("/api/campaigns/{campaign_id}/control", status_code=202, dependencies=[Depends(_authorize_person)])
    def post_control(campaign_id: str, body: ControlRequest, request: Request):
        h = default()

        def act():
            with h.ledger() as L:
                get_campaign(L, campaign_id)
                h.require_active(L, campaign_id)
                ev = L.emit(campaign_id, "campaign.control", "person:" + body.author,
                            f"{body.author} asked to {body.action} the campaign",
                            data={"action": body.action, "author": body.author})
            return {"event": ev.model_dump(mode="json")}
        return h.once(request, body, 202, act)

    @app.get("/api/campaigns/{campaign_id}/next")
    def campaign_next(campaign_id: str):
        from translucent.advice import next_steps, untraced_numbers
        with ledger() as L:
            get_campaign(L, campaign_id)
            return {"advice": next_steps(L, campaign_id), "answer": untraced_numbers(L, campaign_id)}

    @app.get("/api/campaigns/{campaign_id}/protocol")
    def campaign_protocol(campaign_id: str):
        from translucent.protocol import board
        with ledger() as L:
            get_campaign(L, campaign_id)
            return board(L, campaign_id)

    @app.get("/api/campaigns/{campaign_id}/forks")
    def list_forks(campaign_id: str):
        h = default()
        with h.ledger() as L:
            get_campaign(L, campaign_id)
            return [h.branch_view(L, f) for f in L.list("fork") if f.parent_campaign_id == campaign_id]

    @app.get("/api/findings/{claim_id}/branches")
    def finding_branches(claim_id: str):
        from translucent.rerun import branches
        with ledger() as L:
            try:
                return branches(L, claim_id)
            except LookupError as e:
                raise HTTPException(404, str(e)) from e

    @app.post("/api/campaigns/{campaign_id}/forks", status_code=201, dependencies=[Depends(_authorize_person)])
    def post_fork(campaign_id: str, body: ForkRequest, request: Request):
        return default().once(request, body, 201, lambda: make_fork(campaign_id, body.model_copy(
            update={"reason": scrub(body.reason)}), request))

    def make_fork(campaign_id: str, body: ForkRequest, request: Request):
        """A person's what-if. "rerun": re-run only what the changed assumption reaches, with no model calls; a short,
        strictly local rerun runs now, one that reaches the remote sandbox (or is long) is a queued job with the same
        rules as a live campaign. "agent": queue the agent on the branch (a paid run). Queued work needs the
        administrator token, live opt-in and a budget."""
        from translucent import rerun
        h = default()

        def validate(L):
            get_campaign(L, campaign_id)
            try:
                _, claim, overrides = rerun.check(L, campaign_id, body.claim_id, body.overrides)
            except LookupError as e:
                raise HTTPException(404, str(e)) from e
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            if len(L.list("fork", ref=claim.id)) >= rerun.MAX_FORKS_PER_CLAIM:
                raise HTTPException(429, f"This finding already has {rerun.MAX_FORKS_PER_CLAIM} what-ifs")
            return claim, overrides

        def record(L):
            validate(L)
            return rerun.create_fork(L, campaign_id, author=body.author, reason=body.reason,
                                     overrides=body.overrides, claim_id=body.claim_id)

        with h.ledger() as L:
            claim, overrides = validate(L)
            why = rerun.needs_job(L, claim, overrides) if body.mode == "rerun" else "it runs the agent"
        if why is None:
            with h.people_mutex, h.ledger() as L:
                fork = record(L)
                rerun.rerun(L, fork.id)
                return h.branch_view(L, fork)
        _authorize(request)
        if body.budget is None:
            raise HTTPException(422, f"This what-if needs a budget: {why}")
        _within_caps(body.budget, body.profile or _profile())
        if body.mode == "agent":
            _require_keys(body.profile or _profile())
        with h.mutex:
            h.require_free_slot()
            with h.ledger() as L:
                fork = record(L)
                if body.mode == "agent":
                    rerun.declare(L, fork)
                view_ = h.branch_view(L, fork)
            job = Job(campaign_id=fork.campaign_id, status="queued", profile=body.profile or _profile(),
                      created_at=time.time(), updated_at=time.time(), budget=body.budget,
                      rerun=fork.id if body.mode == "rerun" else None)
            h.launch(job)
            return {**view_, "job": job.model_dump(), "status": "queued"}

    @app.post("/api/campaigns/{campaign_id}/resume", status_code=202, dependencies=[Depends(_authorize)])
    def post_resume(campaign_id: str, body: ResumeRequest):
        h = default()
        with h.mutex:
            h.require_free_slot()
            with h.ledger() as L:
                c = get_campaign(L, campaign_id)
                events_state, last = campaign_state(L, campaign_id)
            if events_state == "new":
                raise HTTPException(409, "The campaign never started; there is nothing to resume")
            if events_state == "ended" and last == "finished":
                raise HTTPException(409, "The campaign already finished")
            prior, now = h.load_job(campaign_id), time.time()
            profile = body.profile or (prior.profile if prior else "dev")
            _within_caps(body.budget, profile)
            _require_keys(profile)
            job = Job(campaign_id=c.id, status="queued", profile=profile,
                      created_at=now, updated_at=now, resume=True, budget=body.budget)
            h.launch(job)
            return {"campaign": c.model_dump(), "job": job.model_dump()}

    @app.post("/api/v1/ledgers/{lid}/video-campaigns", status_code=202, dependencies=[Depends(_authorize)])
    def post_video_campaign(body: CampaignRequest, host: LedgerHost = Depends(api_v1.get_ledger)):
        """POST /api/campaigns on any ledger: the same body, administrator token and live opt-in, and one queued,
        running or paused job per ledger (a second in the same ledger is a 409; other ledgers are not held up)."""
        return host.queue_video_campaign(body)

    app.include_router(api_v1.router)
    return app


app = create_app()
