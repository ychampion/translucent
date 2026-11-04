"""Validated JSON import from fresh, credential-free analysis sandboxes.

Agent code reads /work/inputs/inputs.json (ledger id -> flat filename), with
measurement JSON and artifact bytes in that directory. It must write exactly
/work/outputs/<name>.json for each declaration: a finite JSON number, or
{"value": number, "uncertainty": {"value": number|null, "kind": ..., "method": ...}}.
No guest-created identifiers, metadata, units, or pickle are admitted.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import secrets
import uuid

from translucent.contracts import Measurement, Run, Uncertainty
from translucent.tools import ToolResult, tool

CODE_LIMIT = 1024 * 1024
ARTIFACT_LIMIT = 16 * 1024 * 1024
INPUT_LIMIT = 32 * 1024 * 1024
OUTPUT_LIMIT = 64 * 1024
TOTAL_OUTPUT_LIMIT = 1024 * 1024
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
SCHEMA_NAME = "^[A-Za-z_][A-Za-z0-9_]{0,63}$"  # the same rule in JSON Schema regex; Meta's API refuses \Z
ARTIFACT_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


class _RunHashLedger:
    """tools.call does not forward code_sha, so this adapter adds it at the run's single insertion.

    All operations delegate to the original ledger; the adapter removes itself before the matching add,
    including on failed runs. No ledger method or global registry is patched.
    """
    def __init__(self, ctx, run_id, digest):
        self.ctx, self.run_id, self.digest, self.original = ctx, run_id, digest, ctx.ledger

    def __getattr__(self, name):
        return getattr(self.original, name)

    def add(self, record):
        if isinstance(record, Run) and record.id == self.run_id:
            self.ctx.ledger = self.original
            record = record.model_copy(update={"code_sha": self.digest})
        return self.original.add(record)


def run_untrusted(code, inputs, timeout_s=120):
    """Lazy SDK import keeps offline callers and fake runners network-free."""
    from translucent.sandbox.runner import run_untrusted as run
    return run(code, inputs, timeout_s=timeout_s)


def _number(value):
    if type(value) not in (int, float):
        raise ValueError("output value must be a finite JSON number")
    try:
        valid = math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("output value must be a finite JSON number")
    return value


def _nudged(data: bytes, rng: random.Random) -> bytes:
    """A measurement input file with every number moved by 0.2-0.5% (independent sign per number)."""
    m = json.loads(data)
    move = lambda v: v * (1 + rng.choice((-1, 1)) * rng.uniform(0.002, 0.005))
    m["value"] = [move(v) for v in m["value"]] if isinstance(m["value"], list) else move(m["value"])
    if m["uncertainty"].get("value") is not None:
        m["uncertainty"]["value"] = abs(move(m["uncertainty"]["value"]))
    return json.dumps(m, allow_nan=False).encode("utf-8")


def _same(a: float | None, b: float | None) -> bool:
    """Did not move with inputs moved by 0.2-0.5%? Computed numbers move on that order; a typed-in number plus a
    token term of the inputs moves by far less than one part in a million."""
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=0.0)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _result(data):
    if type(data) is not bytes or len(data) > OUTPUT_LIMIT:
        raise ValueError("output must be bytes at most 64 KiB")
    obj = json.loads(data.decode("utf-8"), object_pairs_hook=_unique,
                     parse_constant=lambda x: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    uncertainty = Uncertainty(value=None, kind="none", method="No uncertainty returned by analysis")
    if isinstance(obj, dict):
        if "value" not in obj or set(obj) - {"value", "uncertainty"}:
            raise ValueError("output contains undeclared fields")
        value = _number(obj["value"])
        if "uncertainty" in obj:
            u = obj["uncertainty"]
            if type(u) is not dict or set(u) != {"value", "kind", "method"}:
                raise ValueError("malformed uncertainty")
            if type(u["method"]) is not str or not 1 <= len(u["method"]) <= 2000:
                raise ValueError("uncertainty method must be bounded text")
            if u["kind"] == "none":
                if u["value"] is not None:
                    raise ValueError("none uncertainty must have null value")
            elif _number(u["value"]) < 0:
                raise ValueError("negative uncertainty")
            uncertainty = Uncertainty.model_validate(u, strict=True)
    else:
        value = _number(obj)
    return value, uncertainty


@tool("run_analysis_code",
      "Run Python in a fresh network-blocked Modal sandbox. Read /work/inputs/inputs.json for ledger-id to "
      "filename mapping; measurement files contain value, unit, uncertainty. Write exactly one <name>.json "
      "in /work/outputs per declaration: finite number or {value, uncertainty?}. No other output files. "
      "Uncertainty is {value, kind: std|ci95|bound|resolution|none, method}; none uses null value. Read every "
      "number from the input files, never type one into the code: the code is run again on the inputs moved "
      "slightly, and each output and uncertainty must move with them. Don't round outputs.",
      {"required": ["code", "inputs", "outputs"], "additionalProperties": False, "properties": {
          "code": {"type": "string", "maxLength": CODE_LIMIT},
          "inputs": {"type": "array", "maxItems": 64, "items": {"type": "string"}},
          "outputs": {"type": "array", "minItems": 1, "maxItems": 32, "items": {
              "type": "object", "required": ["name", "unit", "label"], "additionalProperties": False,
              "properties": {"name": {"type": "string", "pattern": SCHEMA_NAME},
                             "unit": {"type": "string", "maxLength": 80},
                             "label": {"type": "string", "maxLength": 90}}}}}},
      "Running sandboxed analysis")
def run_analysis_code(ctx, run_id, rec, code, inputs, outputs):
    rec["provider"] = "modal"
    if type(code) is not str or len(code.encode("utf-8")) > CODE_LIMIT:
        raise ValueError("code must be UTF-8 text at most 1 MiB")
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    rec["code_sha"] = digest
    ctx.ledger = _RunHashLedger(ctx, run_id, digest)
    if type(inputs) is not list or len(inputs) > 64 or any(type(i) is not str for i in inputs):
        raise ValueError("inputs must be at most 64 ledger ids")
    if len(inputs) != len(set(inputs)):
        raise ValueError("duplicate input id")
    if type(outputs) is not list or not 1 <= len(outputs) <= 32:
        raise ValueError("declare 1 to 32 outputs")
    for out in outputs:
        if type(out) is not dict or set(out) != {"name", "unit", "label"}:
            raise ValueError("output declaration needs only name, unit, label")
        if type(out["name"]) is not str or not NAME.fullmatch(out["name"]):
            raise ValueError("output name must be a safe identifier")
        for key, limit in (("unit", 80), ("label", 90)):
            if type(out[key]) is not str or not 1 <= len(out[key]) <= limit:
                raise ValueError(f"output {key} must be bounded nonempty text")
    names = [o["name"] + ".json" for o in outputs]
    if len(set(names)) != len(names):
        raise ValueError("duplicate output declaration")
    files, index, calibrations, artifacts = {}, {}, [], []
    total = 0
    for n, rid in enumerate(inputs):
        kind = ctx.ledger.kind_of(rid)
        if kind == "measurement":
            m = ctx.ledger.get(kind, rid)
            if ctx.ledger.stale_reason(rid):
                raise ValueError("input measurement is stale")
            data = json.dumps({"value": m.value, "unit": m.unit, "uncertainty": m.uncertainty.model_dump()},
                              allow_nan=False).encode("utf-8")
            filename = f"input_{n}.json"
            calibrations.extend(m.calibration_ids)
            if m.quantity in ("spatial_scale", "time_base"):  # a scale or clock read directly calibrates the output
                calibrations.append(rid)
            artifacts.extend(m.artifact_ids)
        elif kind == "artifact":
            if not ARTIFACT_ID.fullmatch(rid):
                raise ValueError("invalid content-addressed artifact id")
            a = ctx.ledger.get(kind, rid)
            if not 0 <= a.bytes <= ARTIFACT_LIMIT:
                raise ValueError("artifact exceeds 16 MiB")
            with ctx.ledger.blob_path(rid).open("rb") as handle:
                data = handle.read(ARTIFACT_LIMIT + 1)
            if len(data) != a.bytes or "sha256:" + hashlib.sha256(data).hexdigest() != rid:
                raise ValueError("artifact bytes fail size/hash verification")
            filename = f"input_{n}.bin"
            artifacts.append(rid)
        else:
            raise ValueError("input must be a recorded measurement or artifact")
        rec["inputs"].append(rid)
        total += len(data)
        if total > INPUT_LIMIT:
            raise ValueError("inputs exceed 32 MiB")
        files[filename], index[rid] = data, filename
    # the code itself is evidence: stored with the run, so a receipt can show what computed each number
    rec["inputs"].append(ctx.ledger.put_bytes(code.encode("utf-8"), suffix=".py", kind="code",
                                              media_type="text/x-python", label="Analysis code").id)
    files["inputs.json"] = json.dumps(index).encode("utf-8")
    files["__outputs__.json"] = json.dumps(names).encode("utf-8")
    result = run_untrusted(code, files, timeout_s=120)
    rec["request_id"] = result.sandbox_id
    if result.status != "ok":
        raise RuntimeError(f"sandbox {result.status}: {str(result.reason)[:500]}")
    if not result.terminated:
        raise RuntimeError("sandbox termination unconfirmed")
    if type(result.outputs) is not dict or set(result.outputs) != set(names):
        raise ValueError("undeclared or missing output")
    if any(type(data) is not bytes for data in result.outputs.values()):
        raise ValueError("output must be immutable bytes")
    if sum(map(len, result.outputs.values())) > TOTAL_OUTPUT_LIMIT:
        raise ValueError("outputs exceed 1 MiB total")
    # Parse everything before queuing any evidence: a bad last output cannot
    # admit a good-looking prefix from the same untrusted execution.
    parsed = [_result(result.outputs[name]) for name in names]
    # A number typed into the code is not computed from the record. Run the same code again on the recorded
    # measurements nudged by a fraction of a percent: every output, and every stated uncertainty, must move.
    measured = [f for rid, f in index.items() if ctx.ledger.kind_of(rid) == "measurement"]
    if not measured:
        raise ValueError("analysis must read at least one recorded measurement, so its outputs can be checked "
                         "against the record")
    # One nudge can leave honest code's output where it was: a ratio of two inputs does not move when both happen to
    # move by the same factor (about 1 call in 2,400). So an output that stays put is nudged once more, independently;
    # only one that stays put both times is refused. A typed-in number never moves, so it is still refused.
    rng = random.Random(secrets.randbits(64))  # unpredictable, so code can't anticipate the moved inputs
    fixed = [spec["name"] for spec in outputs]
    for attempt in range(2):
        nudged = {**files, **{f: _nudged(files[f], rng) for f in measured}}
        check = run_untrusted(code, nudged, timeout_s=120)
        rec["check_request_id" if attempt == 0 else "recheck_request_id"] = check.sandbox_id
        if check.status != "ok" or not check.terminated or type(check.outputs) is not dict \
                or set(check.outputs) != set(names):
            raise RuntimeError("the code did not run when its recorded inputs changed slightly "
                               f"({check.status}: {str(check.reason)[:300]}); it must compute from /work/inputs")
        fixed = [spec["name"] for spec, (v, u), name in zip(outputs, parsed, names) if spec["name"] in fixed
                 for v2, u2 in [_result(check.outputs[name])]
                 if _same(v, v2) or (u.kind != "none" and _same(u.value, u2.value))]
        if not fixed:
            break
    if fixed:
        raise ValueError(f"{', '.join(fixed)} did not change when the recorded inputs changed slightly, so it is "
                         "not computed from them. Read every number from /work/inputs instead of typing it into "
                         "the code, and compute each uncertainty from the data.")
    ids = {}
    for spec, (value, uncertainty) in zip(outputs, parsed):
        mid = "m_" + uuid.uuid4().hex[:12]
        rec["measurements"].append(Measurement(
            id=mid, run_id=run_id, quantity="derived:" + spec["name"], value=value, unit=spec["unit"],
            label=spec["label"], uncertainty=uncertainty,
            calibration_ids=list(dict.fromkeys(calibrations)), artifact_ids=list(dict.fromkeys(artifacts))))
        ids[spec["name"]] = mid
    shown = "; ".join(f"{spec['name']} = {value:.6g}"
                      + (f" ± {u.value:.3g}" if u.value is not None else "") + f" {spec['unit']} (cite {ids[spec['name']]})"
                      for spec, (value, u) in zip(outputs, parsed))
    return ToolResult(run_id, "ok", f"Recorded {len(ids)} derived measurements from sandboxed analysis: {shown}.",
                      data={"measurements": ids, "code_sha": digest, "sandbox_id": result.sandbox_id})
