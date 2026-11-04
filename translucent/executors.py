"""Executors: the computational executor and the human-result intake, both ending in `ingest_output`, the one place
a returned result becomes evidence (design §2.6).

Computational: `run_computational` runs the adapter's execute() in a child process (`python -P -m
translucent.exec_child`), so this process never imports a solver. The child gets the request as JSON on stdin and
answers with one JSON line plus its sha256 on stdout; it runs in `<ledger>/work/exec/<rq>/<n>/` (never on its
sys.path) with only the environment a Python solver needs (an allowlist: no keys, tokens or connection strings),
under a wall-time limit. Any refusal becomes a `failure` record.

Human: nothing physical is ever executed here. A person runs the approved procedure and returns the result;
`parse_human_result` reads it (hash, executed configuration in its units, table and scalars) and `ingest_output`
checks it like solver output. Refusals become `result_rejected` records (the service writes them).

`ingest_output` refuses with reasons when a produced quantity is missing, a unit is unknown or measures something
else (conversion only through translucent.units), a series is non-finite or its time runs backwards, the executed
configuration differs from the approved one beyond a parameter's tolerance, a specimen is not the requested one or
was consumed by another destructive test, or the adapter's check_result finds a problem. Files are stored first,
outside any transaction; then the Run, its Measurements, their events, tool.finished and the `result` loop record
commit together.
"""
from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from translucent import chain, loop_compare, units
from translucent.scrub import scrub
from translucent.contracts import Measurement, Run, Source, Transform, Uncertainty
from translucent.ledger import Ledger, sha256_file
from translucent.loop import RUNNER, RequestState, consumed_by
from translucent.loop_contracts import LoopRecord, ScalarSpec, TableSpec, TimeBase
from translucent.systems.base import ActionSpec, ExecutionOutput, ResultTable, SystemAdapter

CHILD_START_S = 20.0      # interpreter start and imports, on top of the execution's own wall-time limit
STAGED_INPUTS = "inputs.json"   # what an adapter's stage() writes in the work folder; removed once the child ends
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSW|PWD|CREDENTIAL|AUTH|COOKIE|DSN", re.I)
# what a Python solver (numpy, SciPy, CasADi, PyBaMM) needs: the OS, temp and home folders, locale, thread counts,
# the interpreter's own settings. Anything else (DATABASE_URL, SMTP_PASS, SENTRY_DSN...) stays with the parent.
_CHILD_ENV = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "OS", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "COMMONPROGRAMFILES", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
    "LOCALAPPDATA", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "USER", "USERNAME",
    "LOGNAME", "LANG", "LANGUAGE", "TZ", "TERM", "VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONHOME", "PYTHONUTF8",
    "PYTHONDONTWRITEBYTECODE", "PYTHONHASHSEED", "PYTHONNOUSERSITE", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH",
    "CI",   # PyBaMM asks about telemetry on stdin unless it sees CI or its own opt-out
})
_CHILD_PREFIXES = ("LC_", "OMP_", "MKL_", "OPENBLAS_", "NUMEXPR_", "VECLIB_", "KMP_", "PYBAMM_")


class ResultInvalid(ValueError):
    """A returned result was refused; `reasons` are plain sentences, recorded as they are."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


class ExecutionFailed(RuntimeError):
    def __init__(self, error: str, retryable: bool = False):
        super().__init__(error)
        self.retryable = retryable


class HumanUpload(BaseModel):
    """A person's returned result (HTTP JSON plus uploaded files, or MCP with local paths)."""
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    request_id: str
    content_hash: str                             # the version the person was approved to run
    executed_configuration: dict[str, float | str]
    executed_units: dict[str, str] = {}
    specimen_id: str | None = None
    table: TableSpec | None = None                # the import v2 column grammar
    time: TimeBase | None = None
    scalars: list[ScalarSpec] = []
    notes: str = ""
    author: str


# ------------------------------------------------------------------ the child process

def _loose(body) -> bytes:
    # canonical JSON as exec_child writes it: NaN and inf pass, so they are refused below with a reason
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def result_digest(body) -> str:
    return hashlib.sha256(_loose(body)).hexdigest()


def _import_roots(adapter: SystemAdapter) -> list[str]:
    """sys.path roots the child needs to import translucent and the adapter's module, as this process did."""
    import translucent
    roots = [Path(translucent.__file__).resolve().parents[1]]
    name = type(adapter).__module__
    module = sys.modules.get(name)
    path = getattr(module, "__file__", None)
    if name == "__main__" or path is None:
        raise ExecutionFailed(f"the {adapter.spec.id} adapter is defined where a child process cannot import it")
    roots.append(Path(path).resolve().parents[name.count(".") + (Path(path).name == "__init__.py")])
    return list(dict.fromkeys(str(r) for r in roots))


def child_env(roots: list[str]) -> dict[str, str]:
    """The allowlisted part of this process's environment (never a name that looks like a secret); PYTHONPATH
    reaches `roots` first."""
    env = {k: v for k, v in os.environ.items()
           if (k.upper() in _CHILD_ENV or k.upper().startswith(_CHILD_PREFIXES)) and not _SECRET_NAME.search(k)}
    # inherited entries made absolute here: the child's cwd is the work folder, where "." or "" would point
    extra = [str(Path(p).resolve()) for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p.strip()]
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(roots + extra))
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONSAFEPATH"] = "1"        # -P for the solver's own subprocesses too: no import from the work folder
    return env


def run_child(adapter: SystemAdapter, action: str, params: dict, workdir: Path, wall_s: float) -> tuple[dict, str]:
    """Run execute() in `python -P -m translucent.exec_child`; (the child's output dict, its sha256)."""
    workdir.mkdir(parents=True, exist_ok=True)
    request = {"system": adapter.spec.id, "action": action, "params": params, "workdir": str(workdir.resolve()),
               "max_wall_s": wall_s,
               "adapter": {"module": type(adapter).__module__, "class": type(adapter).__qualname__}}
    env = child_env(_import_roots(adapter))
    (workdir / "request.json").write_text(json.dumps(request, indent=1), encoding="utf-8")
    try:
        # -P: the work folder (cwd) is not put on sys.path, so nothing in it can shadow a module the child imports
        proc = subprocess.run([sys.executable, "-P", "-m", "translucent.exec_child"], input=json.dumps(request),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=workdir,
                              env=env, timeout=wall_s + CHILD_START_S)
    except subprocess.TimeoutExpired:
        raise ExecutionFailed(f"the solver did not finish within its wall-time limit of {wall_s:g} s",
                              retryable=True) from None
    (workdir / "stderr.txt").write_text(proc.stderr, encoding="utf-8")
    line = next((x for x in reversed(proc.stdout.splitlines()) if x.strip()), "")
    try:
        message = json.loads(line)
        body, sha = message["result"], message["sha256"]
    except (ValueError, KeyError, TypeError):
        tail = proc.stderr.strip().splitlines()[-1:] or ["no output"]
        raise ExecutionFailed(f"the executor exited with code {proc.returncode} and no result ({tail[0][:300]})")
    if result_digest(body) != sha:
        raise ExecutionFailed("the executor's result does not match its sha256")
    if "error" in body:
        raise ExecutionFailed(str(body["error"])[:2000], retryable=body.get("kind") == "timeout")
    return body, sha


def decode_output(body: dict, workdir: Path) -> ExecutionOutput:
    """The child's JSON back to an ExecutionOutput; files must lie inside the run's work folder."""
    try:
        o, tb = body["output"], body["output"]["table"]
        table = ResultTable(time_unit=tb.get("time_unit"), t=tuple(tb.get("t", ())),
                            series={q: (u, tuple(v)) for q, (u, v) in tb.get("series", {}).items()},
                            scalars={q: (v, u) for q, (v, u) in tb.get("scalars", {}).items()})
        files, root = {}, workdir.resolve()
        for name, p in o.get("files", {}).items():
            path = (root / p).resolve()            # relative to the work folder (an absolute path stays as is)
            if not path.is_relative_to(root) or not path.is_file():
                raise ResultInvalid([f"output file {name} is not a file in the run's work folder"])
            files[name] = path
        return ExecutionOutput(table=table, files=files, solver={k: str(v) for k, v in o.get("solver", {}).items()},
                               executed=dict(o.get("executed", {})))
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        if isinstance(e, ResultInvalid):
            raise
        raise ResultInvalid([f"the executor's output is malformed ({type(e).__name__}: {e})"]) from None


def executor_digest(adapter: SystemAdapter) -> str:
    """What ran: the adapter's source digest with the solver it names."""
    return chain.digest({"adapter": adapter.code_digest(), "solver": adapter.spec.solver or {}})


def record_failure(L: Ledger, cid: str, req: RequestState, attempt: int, error: str, retryable: bool) -> LoopRecord:
    return chain.append(L, cid, "failure", {"request_id": req.request_id, "attempt": attempt,
                                            "error": scrub(error)[:2000],
                                            "retryable": retryable}, RUNNER,
                        chain.natural_id("fai", req.request_id, attempt))


def run_computational(L: Ledger, cid: str, req: RequestState, adapter: SystemAdapter, *, worker: dict,
                      workdir: Path, timeout_s: float) -> LoopRecord:
    """Execute a request whose `execution(started)` is recorded and whose claim lock this process holds; record
    the result, or a failure. No ledger transaction is open while the child runs."""
    attempt, started = req.attempt, time.time()
    try:
        if (stage := getattr(adapter, "stage", None)) is not None:
            try:                                 # inputs the system derives from the ledger, for the child to read
                stage(L, cid, req, workdir)
            except (ValueError, KeyError, OSError) as e:
                return record_failure(L, cid, req, attempt, f"the request's inputs could not be staged: {e}", False)
        try:
            body, sha = run_child(adapter, req.action, dict(req.data.params), workdir, timeout_s)
        finally:
            (Path(workdir) / STAGED_INPUTS).unlink(missing_ok=True)    # staged inputs never outlive the child
        output = decode_output(body, workdir)
        raw = workdir / "output.json"            # the child's exact answer, kept as evidence; its sha256 is `sha`
        raw.write_bytes(_loose(body))
        output = ExecutionOutput(table=output.table, files={**output.files, "output.json": raw},
                                 solver=output.solver, executed=output.executed)
        return ingest_output(L, cid, req, output, adapter=adapter, basis="computational", submitted_by=RUNNER,
                             attempt=attempt, payload_sha256=sha, started_at=started,
                             run_params={"worker": worker, "timeout_s": timeout_s})
    except ExecutionFailed as e:
        return record_failure(L, cid, req, attempt, str(e), e.retryable)
    except ResultInvalid as e:
        return record_failure(L, cid, req, attempt, "the solver's output was refused: " + "; ".join(e.reasons),
                              False)


# ------------------------------------------------------------------ validation

def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def normalize(adapter: SystemAdapter, action: ActionSpec, table: ResultTable) -> tuple[ResultTable, list[str]]:
    """The table in the system's canonical units with time in seconds, and the reasons it cannot be."""
    quantities, out, seen = {q.name: q for q in adapter.spec.quantities}, [], set()
    t: list[float] = list(table.t)
    if table.series:
        if table.time_unit is None:
            out.append("the series need a time unit")
        elif not all(_finite(x) for x in t):
            out.append("time has values that are not finite numbers")
        else:
            try:
                t = units.convert(t, table.time_unit, "s")
            except ValueError as e:
                out.append(f"time: {e}")
            if (back := next((i for i in range(1, len(t)) if t[i] < t[i - 1]), None)) is not None:
                out.append(f"time goes backwards at sample {back} ({t[back - 1]:g} s, then {t[back]:g} s)")
    series, scalars = {}, {}
    for q, (unit, values) in table.series.items():
        seen.add(q)
        spec = quantities.get(q)
        if spec is None or spec.kind != "series":
            out.append(f"{q} is not a series quantity of {adapter.spec.id}")
        elif len(values) != len(table.t):
            out.append(f"{q} has {len(values)} values for {len(table.t)} times")
        elif not all(_finite(v) for v in values):
            out.append(f"{q} has values that are not finite numbers")
        else:
            try:
                series[q] = (spec.unit, tuple(units.convert(list(values), unit, spec.unit)))
            except ValueError as e:
                out.append(f"{q}: {e}")
    for q, (value, unit) in table.scalars.items():
        seen.add(q)
        spec = quantities.get(q)
        if spec is None or spec.kind == "series":
            out.append(f"{q} is not a scalar quantity of {adapter.spec.id}")
        elif spec.kind == "category":
            if value not in spec.categories:
                out.append(f"{q} is {value!r}; {adapter.spec.id} names {', '.join(spec.categories)}")
            else:
                scalars[q] = (value, "")
        elif not _finite(value):
            out.append(f"{q} is not a finite number")
        else:
            try:
                scalars[q] = (units.convert(value, unit, spec.unit), spec.unit)
            except ValueError as e:
                out.append(f"{q}: {e}")
    out += [f"the result lacks {q}" for q in action.produces if q not in seen]
    return ResultTable(time_unit="s" if table.series else None, t=tuple(t), series=series, scalars=scalars), out


def configuration_problems(action: ActionSpec, requested: dict, executed: dict) -> list[str]:
    """The executed configuration against the approved one, within each parameter's tolerance."""
    out = [f"the executed configuration names unknown parameter {k}" for k in executed
           if k not in {p.name for p in action.parameters}]
    for p in action.parameters:
        if p.name not in executed:
            out.append(f"the executed configuration lacks {p.name}")
            continue
        got, want = executed[p.name], requested[p.name]
        if isinstance(want, str) or isinstance(got, str):
            if got != want:
                out.append(f"{p.name} was {got}; the approved request says {want}")
        elif not _finite(got):
            out.append(f"{p.name} was not a finite number")
        elif abs(got - want) > p.tolerance + 1e-9 * max(1.0, abs(want)):
            out.append(f"{p.name} was {got:g} {p.unit}; the approved request says {want:g} {p.unit} "
                       f"(tolerance {p.tolerance:g})")
    return out


def specimen_problems(L: Ledger, req: RequestState, action: ActionSpec, specimen_id: str | None) -> list[str]:
    want = req.data.specimen or {}
    if not specimen_id:
        return ["the result must name the specimen tested"] if action.destructive or want else []
    out = []
    if "id" in want and specimen_id != want["id"]:
        out.append(f"specimen {specimen_id} is not the requested specimen {want['id']}")
    if (used := consumed_by(L, specimen_id, req.request_id)) is not None:
        out.append(f"specimen {specimen_id} was consumed by destructive test {used.data['request_id']} "
                   f"({used.id})")
    return out


# ------------------------------------------------------------------ human results

def upload_sha256(upload: HumanUpload, files: dict[str, Path]) -> str:
    """Identity of one upload: its JSON and the bytes of every file, so an identical re-upload is recognised."""
    return chain.digest({"upload": upload.model_dump(mode="json"),
                         "files": {n: sha256_file(p) for n, p in sorted(files.items())}})


def _read_table(path: Path, spec: TableSpec, tbase: TimeBase | None = None):
    """A returned table read as an imported one is (translucent.datasets: the same grammar, refusals and limits;
    no row is dropped or changed without a refusal), as (time, columns, import note), or the reasons it cannot be."""
    from translucent import datasets
    try:
        table = datasets._table(path, spec)
    except (OSError, ValueError) as e:
        return [str(e)]
    note = datasets._import_note(spec, table, tbase) if tbase is not None else None
    return table.t, table.columns, note


def parse_human_result(req: RequestState, adapter: SystemAdapter, upload: HumanUpload,
                       files: dict[str, Path]) -> ExecutionOutput | list[str]:
    """A person's upload as an ExecutionOutput in the units they declared (ingest_output converts and checks), or
    the reasons it cannot be one."""
    out = []
    if upload.request_id != req.request_id:
        out.append(f"the result names request {upload.request_id}, not {req.request_id}")
    if upload.content_hash != req.approved_hash:
        named = req.version_named(upload.content_hash)
        approved = req.version_named(req.approved_hash) if req.approved_hash else None
        out.append(f"the result names version {named}; the approved version is {approved}" if named else
                   f"the result names an unknown version ({upload.content_hash[:12]}); the approved version is "
                   f"{approved}")
    action = adapter.spec.action(req.action)
    executed = {}
    for k, v in upload.executed_configuration.items():
        p = next((x for x in action.parameters if x.name == k), None)
        if p is None or isinstance(v, str):
            executed[k] = v
        elif k not in upload.executed_units:
            out.append(f"executed_units must give the unit of {k}")
        else:
            try:
                executed[k] = units.convert(v, upload.executed_units[k], p.unit)
            except ValueError as e:
                out.append(f"executed {k}: {e}")
    if bad := [n for n in files if Path(n).name != n or not Path(files[n]).is_file()]:
        out.append(f"uploaded files are flat names of existing files: {', '.join(bad)}")
    t, series, note = (), {}, None
    if upload.table is not None:
        if upload.time is None:
            out.append("a table needs its time base (what t = 0 means, and the time unit)")
        elif upload.table.file not in files:
            out.append(f"the table file {upload.table.file} was not uploaded")
        else:
            read = _read_table(Path(files[upload.table.file]), upload.table, upload.time)
            if isinstance(read, list):
                out += read
            else:
                try:
                    t = tuple(x - upload.time.offset_s for x in units.convert(read[0], upload.time.unit, "s"))
                except ValueError as e:
                    out.append(f"time: {e}")
                note = read[2]
                for col, ch in upload.table.columns.items():
                    if ch.quantity in series:
                        out.append(f"{ch.quantity} is given by more than one column")
                    series[ch.quantity] = (ch.unit, tuple(read[1][col]))
    scalars = {}
    for s in upload.scalars:
        if s.quantity in scalars or s.quantity in series:
            out.append(f"{s.quantity} is given more than once")
        scalars[s.quantity] = (s.value, s.unit)
    if out:
        return out
    return ExecutionOutput(table=ResultTable(time_unit="s" if series else None, t=t, series=series, scalars=scalars),
                           files={n: Path(p) for n, p in files.items()}, solver={}, executed=executed,
                           import_note=note)


def human_uncertainties(upload: HumanUpload) -> dict[str, Uncertainty]:
    """Declared uncertainties of the returned values, by quantity (channel or scalar)."""
    out = {ch.quantity: ch.uncertainty for ch in (upload.table.columns.values() if upload.table else ())
           if ch.uncertainty is not None}
    return out | {s.quantity: s.uncertainty for s in upload.scalars if s.uncertainty is not None}


# ------------------------------------------------------------------ ingest

def _artifact_kind(name: str) -> tuple[str, str]:
    suffix = Path(name).suffix.lower()
    kind = "table" if suffix in (".csv", ".tsv") else "plot" if suffix in (".png", ".jpg", ".jpeg", ".svg") \
        else "video" if suffix in (".mp4", ".mov", ".avi") else "text"
    return kind, mimetypes.guess_type(name)[0] or "application/octet-stream"


def ingest_output(L: Ledger, cid: str, req: RequestState, output: ExecutionOutput, *, adapter: SystemAdapter,
                  basis: str, submitted_by: str, attempt: int | None, payload_sha256: str,
                  specimen_id: str | None = None, uncertainties: dict[str, Uncertainty] | None = None,
                  run_params: dict | None = None, started_at: float | None = None) -> LoopRecord:
    """Validate an output against the approved request and record it as the request's result. Raises
    ResultInvalid with every reason, or AdmissionError if the request's state no longer allows a result."""
    if L.in_transaction():
        raise RuntimeError("ingest_output copies files; call it outside Ledger.transaction()")
    action = adapter.spec.action(req.action)
    table, problems = normalize(adapter, action, output.table)
    problems += configuration_problems(action, dict(req.data.params), dict(output.executed))
    if basis == "physical":
        problems += specimen_problems(L, req, action, specimen_id)
    if not problems:
        problems += adapter.check_result(req.action, dict(req.data.params), table)
    if problems:
        raise ResultInvalid(problems)

    human = basis == "physical"
    where = f"request:{req.request_id}" if human else f"exec:{req.request_id}/{attempt}"
    files = {}
    for name, path in sorted(output.files.items()):
        kind, media = _artifact_kind(name)
        files[name] = L.put_file(path, kind=kind, media_type=media, label=name,
                                 source=Source(uri=f"{where}/{name}")).id
    quantities = {q.name: q for q in adapter.spec.quantities}
    # a person states an uncertainty in the unit they wrote the value in; it scales with the value's conversion
    sent = {q: u for q, (u, _) in output.table.series.items()} | {q: u for q, (_, u) in output.table.scalars.items()}
    stated = {q: u.model_copy(update={"value": u.value * abs(float(units.factor(sent[q], quantities[q].unit)[0]))})
              if u.value is not None and q in sent and q in quantities else u
              for q, u in (uncertainties or {}).items()}

    def uncertainty(q: str) -> Uncertainty:
        if not human:
            return Uncertainty(value=quantities[q].tolerance, kind="bound",
                               method=f"solver bound declared by {adapter.spec.id} {adapter.spec.version}")
        return stated.get(q) or Uncertainty(value=None, kind="none",
                                            method="not stated by the person who returned the result")

    parents = list(files.values())
    series = {q: L.put_json({"t": list(table.t), "t_unit": "s", "y": list(y), "unit": unit,
                             "uncertainty": uncertainty(q).model_dump(mode="json")}, kind="series",
                            label=f"{quantities[q].label or q} ({unit})",
                            transform=Transform(op="normalize_units", parent_ids=parents,
                                                params={"quantity": q, "unit": unit}) if parents else None).id
              for q, (unit, y) in table.series.items()}

    run_id = chain.natural_id("run", req.request_id, attempt, payload_sha256)
    measurements = [Measurement(id=chain.natural_id("m", run_id, q), run_id=run_id, quantity=q, value=float(v),
                                unit=unit, uncertainty=uncertainty(q), subject=req.request_id,
                                artifact_ids=parents, label=quantities[q].label or q,
                                valid_domain=(adapter.spec.solver or {}).get("validity") if not human else None)
                    for q, (v, unit) in table.scalars.items() if quantities[q].kind != "category"]
    # A measurement is a number; a category (run status, validity range) is recorded on the result itself
    categories = {q: v for q, (v, _) in table.scalars.items() if quantities[q].kind == "category"}
    if human:
        tool, params = "human_result", {"author": submitted_by, "content_hash": req.approved_hash,
                                        "payload_sha256": payload_sha256}
        if output.import_note is not None:      # what reading the returned table did, as an import records it
            params["import_note"] = output.import_note
    else:
        tool, params = f"execute:{adapter.spec.id}.{req.action}", {
            "request_id": req.request_id, "attempt": attempt, "content_hash": req.approved_hash,
            "payload_sha256": payload_sha256, "action": req.action, "params": dict(req.data.params),
            **(run_params or {})}
    run = Run(id=run_id, campaign_id=cid, step_id=req.request_id, tool=tool, idempotency_key=payload_sha256,
              input_ids=[], output_ids=list(series.values()) + parents, measurement_ids=[m.id for m in measurements],
              params=params, code_sha=None if human else executor_digest(adapter),
              provider="human" if human else "local", status="ok", started_at=started_at or time.time(),
              ended_at=time.time())
    data = {"request_id": req.request_id, "content_hash": req.approved_hash, "attempt": attempt, "basis": basis,
            "run_id": run_id, "measurements": {m.quantity: m.id for m in measurements}, "categories": categories,
            "series": series,
            "files": files, "submitted_by": submitted_by,
            "executed": {"configuration": dict(output.executed), "specimen_id": specimen_id,
                         "solver": dict(output.solver), "destructive": action.destructive}}
    actor = submitted_by
    with L.transaction():
        L.add(run)
        for m in measurements:
            L.add(m)
            L.emit(cid, "measurement.recorded", actor, m.label or m.quantity, refs=[m.id],
                   data={"value": m.value, "unit": m.unit, "measurement": m.model_dump(mode="json")})
        # as tools.call writes it, and verify re-checks it: the run's tool as actor, then its outputs and measurements
        L.emit(cid, "tool.finished", run.tool, f"{action.label} finished",
               refs=[run_id] + run.output_ids + [m.id for m in measurements],
               data={"status": "ok", "summary": f"{len(measurements)} values and {len(series)} series recorded",
                     "cost_usd": 0.0, "result": {"request_id": req.request_id}})
        rec = chain.append(L, cid, "result", data, actor, chain.natural_id("res", req.request_id))
        loop_compare.record_comparisons(L, cid)     # the comparison commits with the result
        return rec
