"""Fresh, credential-free Modal isolation for agent-written analysis.

Inputs are flat filenames, available at /work/inputs. Reserve __outputs__.json
for a UTF-8 JSON list of flat output filenames to collect from /work/outputs.
No output is imported unless the controller declared it before execution.
Returned output bytes are untrusted: never unpickle or use them as claim evidence
without a separate trusted validator.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Literal

import modal

APP_NAME = "translucent-sandbox"
BASE_IMAGE = "python:3.12.6-slim-bookworm@sha256:c0d63ec61d3a1321f8dc2d46ab6bd38465e005237c0a463712020e5d338eae25"
OUTPUT_MANIFEST = "__outputs__.json"
LOG_LIMIT = 1024 * 1024
OUTPUT_LIMIT = 1024 * 1024
INPUT_LIMIT = 256 * 1024 * 1024
HERE = Path(__file__).resolve().parent
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")


@dataclass
class RunResult:
    status: Literal["ok", "timeout", "error", "rejected"] = "error"
    exit_code: int | None = None
    outputs: dict[str, bytes] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    elapsed: float = 0.0
    input_sha256: dict[str, str] = field(default_factory=dict)
    output_sha256: dict[str, str] = field(default_factory=dict)
    reason: str = ""
    logs_truncated: bool = False
    sandbox_id: str | None = None
    terminated: bool = False
    cold_start_s: float | None = None
    sandbox_environment: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def logs(self) -> dict[str, str]:
        return {"stdout": self.stdout, "stderr": self.stderr}


def _audit(event: str, sandbox_id: str):
    """Optional local receipt, so the test harness can recover after interruption."""
    directory = os.environ.get("TRANSLUCENT_EVIDENCE_DIR")
    if directory:
        target = Path(directory).resolve()
        if not target.is_relative_to(HERE):
            raise ValueError("evidence directory must stay under translucent/sandbox")
        target.mkdir(parents=True, exist_ok=True)
        with (target / "lifecycle.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": event, "sandbox_id": sandbox_id, "time": time.time()}) + "\n")


def _image():
    packages = [x for x in (HERE / "requirements-image.txt").read_text().splitlines() if x]
    # The SDK's implicit image cache is required; there are no named images,
    # deployed Functions, local-directory mounts, or separately managed Volumes.
    return (
        modal.Image.from_registry(BASE_IMAGE)
        .pip_install(*packages, extra_options="--only-binary=:all: --no-deps")
        .run_commands(
            "groupadd --gid 10001 analysis && useradd --uid 10001 --gid 10001 --no-create-home analysis",
            "mkdir -p /control/exports /opt/spike /work/inputs /work/outputs",
            "chmod 700 /control /control/exports && chmod 755 /work /opt/spike",
            "chown 10001:10001 /work/inputs /work/outputs",
        )
        .add_local_file(HERE / "bootstrap.py", "/opt/spike/bootstrap.py", copy=True)
        .add_local_file(HERE / "supervisor.py", "/opt/spike/supervisor.py", copy=True)
        .dockerfile_commands("USER root", "RUN chown 0:0 /opt/spike/*.py && chmod 644 /opt/spike/*.py", "WORKDIR /work")
    )


def _validate(code, inputs, timeout_s):
    if not isinstance(code, str) or len(code.encode("utf-8")) > LOG_LIMIT:
        raise ValueError("code must be UTF-8 text at most 1 MiB")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        raise ValueError("timeout_s must be a number in [5, 120]")
    if not math.isfinite(timeout_s) or not 5 <= timeout_s <= 120:
        raise ValueError("timeout_s must be in [5, 120]")
    if not isinstance(inputs, dict) or len(inputs) > 128:
        raise ValueError("inputs must be a dict with at most 128 files")
    size = 0
    for name, value in inputs.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("input names must be flat safe filenames")
        if not isinstance(value, bytes):
            raise ValueError("input values must be immutable bytes")
        size += len(value)
    if size > INPUT_LIMIT:
        raise ValueError("inputs exceed 256 MiB total")
    raw = inputs.get(OUTPUT_MANIFEST, b"[]")
    if len(raw) > 65536:
        raise ValueError("output declaration exceeds 64 KiB")
    try:
        names = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid output declaration JSON") from exc
    if not isinstance(names, list) or len(names) > 128:
        raise ValueError("declare at most 128 output filenames")
    for name in names:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("output names must be flat safe filenames")
        if name.lower().endswith((".pkl", ".pickle")):
            raise ValueError("pickle outputs are forbidden")
    if len(set(names)) != len(names):
        raise ValueError("duplicate declared output")
    return names


async def _write(sb, path: str, data: bytes):
    await sb.filesystem.write_bytes.aio(data, path)


async def _read(sb, path: str, limit: int) -> bytes:
    # Only read root-owned control/export files after the analysis UID is dead.
    # The stat check is authoritative there: the agent cannot race a later read.
    info = await sb.filesystem.stat.aio(path)
    if not 0 <= info.size <= limit:
        raise ValueError(f"collection exceeds byte limit for {path}")
    data = await sb.filesystem.read_bytes.aio(path)
    if len(data) != info.size or len(data) > limit:
        raise ValueError(f"collection integrity failure for {path}")
    return data


async def _run(code, inputs, names, timeout_s, result):
    sb = None
    deadline = None
    log_tasks = []
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    stream_truncated = False

    async def receive(stream, name):
        nonlocal stream_truncated
        async for chunk in stream:
            remaining = max(0, LOG_LIMIT - sum(map(len, captured.values())))
            captured[name].extend(chunk[:remaining])
            stream_truncated |= len(chunk) > remaining

    try:
        app = await modal.App.lookup.aio(APP_NAME, create_if_missing=True)
        creation_start = time.monotonic()
        sb = await modal.Sandbox.create.aio(
            "sleep", "infinity", app=app, image=_image(),
            block_network=True, cpu=(0.5, 2), memory=(512, 2048),
            timeout=math.ceil(timeout_s), workdir="/work",
            secrets=[], volumes={}, network_file_systems={},
            encrypted_ports=[], h2_ports=[], unencrypted_ports=[],
            env={},
        )
        result.sandbox_id = sb.object_id
        _audit("created", sb.object_id)
        # Creation may build the image. The deadline begins once the Sandbox
        # handle exists; the independent Modal lifetime also bounds orphan runs.
        deadline = time.monotonic() + timeout_s
        async with asyncio.timeout_at(deadline):
            probe = await sb.exec.aio("python", "-I", "-c", "import os; assert os.geteuid() == 0")
            if await probe.wait.aio() != 0:
                raise RuntimeError("root supervisor is unavailable")
            result.cold_start_s = time.monotonic() - creation_start
            semaphore = asyncio.Semaphore(4)

            async def upload(path, data):
                async with semaphore:
                    await _write(sb, path, data)

            await asyncio.gather(
                upload("/control/manifest.json", json.dumps(names).encode()),
                upload("/work/code.py", code.encode("utf-8")),
                *(upload("/work/inputs/" + name, data) for name, data in inputs.items()),
            )
            # Inputs are independent copies and intentionally writable for the
            # mutation gate. The parent directory and supervisor remain root-owned.
            setup = await sb.exec.aio("chown", "-R", "10001:10001", "/work/inputs")
            if await setup.wait.aio() != 0:
                raise RuntimeError("could not prepare input copies")
            # Filesystem operations incur network round trips. Reserve time for
            # bounded timeout-log collection before the server lifetime expires.
            budget = deadline - time.monotonic() - min(6, timeout_s / 2)
            if budget <= 0:
                raise TimeoutError("startup exhausted sandbox lifetime")
            proc = await sb.exec.aio("python", "-I", "/opt/spike/supervisor.py", str(budget), text=False)
            log_tasks = [asyncio.create_task(receive(proc.stdout, "stdout")),
                         asyncio.create_task(receive(proc.stderr, "stderr"))]
            helper_exit = await proc.wait.aio()
            result.exit_code = helper_exit
            stream_results = await asyncio.gather(*log_tasks, return_exceptions=True)
            if helper_exit != 0:
                raise RuntimeError("supervisor failed (including possible sandbox OOM)")
            if any(isinstance(item, BaseException) for item in stream_results):
                raise RuntimeError("analysis log stream failed")
            metadata = json.loads(await _read(sb, "/control/result.json", 65536))
            result.logs_truncated = metadata["logs_truncated"]
            result.status = metadata["status"]
            result.reason = metadata["reason"]
            result.exit_code = metadata["exit_code"]
            result.sandbox_environment = metadata["sandbox_environment"]
            if result.status not in ("ok", "timeout", "error", "rejected"):
                raise ValueError("invalid supervisor status")
            if result.status == "ok":
                if set(metadata["outputs"]) != set(names):
                    raise ValueError("output declaration mismatch")
                total = sum(info["size"] for info in metadata["outputs"].values())
                if total > OUTPUT_LIMIT or any(x["size"] < 0 for x in metadata["outputs"].values()):
                    raise ValueError("outputs exceed 1 MiB total")
                for name in names:
                    info = metadata["outputs"][name]
                    data = await _read(sb, "/control/exports/" + name, info["size"])
                    digest = hashlib.sha256(data).hexdigest()
                    if len(data) != info["size"] or digest != info["sha256"]:
                        raise ValueError("output transfer integrity mismatch")
                    result.outputs[name] = data
                    result.output_sha256[name] = digest
    except TimeoutError:
        result.status = "timeout"
        result.reason = "controller wall-clock deadline exceeded"
    except ValueError as exc:
        result.status = "rejected"
        result.reason = str(exc)
    except Exception as exc:
        # OOM/transport/startup failures never count as a successful denial probe.
        is_timeout = type(exc).__name__ == "SandboxTimeoutError"
        result.status = "timeout" if is_timeout else "error"
        result.reason = f"{type(exc).__name__}: {exc}"
    finally:
        for task in log_tasks:
            if not task.done():
                task.cancel()
        if log_tasks:
            try:
                async with asyncio.timeout(2):
                    await asyncio.gather(*log_tasks, return_exceptions=True)
            except TimeoutError:
                pass
        # Preserve already-streamed logs even when the guest or helper dies.
        # Ignoring malformed UTF-8 prevents replacement decoding from expanding
        # the public string logs beyond the same 1 MiB combined byte bound.
        result.stdout = captured["stdout"].decode("utf-8", errors="ignore")
        result.stderr = captured["stderr"].decode("utf-8", errors="ignore")
        result.logs_truncated |= stream_truncated
        if result.status != "ok":
            result.outputs.clear()
            result.output_sha256.clear()
        if sb is not None:
            try:
                async with asyncio.timeout(25):
                    await sb.terminate.aio()
                    try:
                        await sb.wait.aio(raise_on_termination=False)
                    except modal.exception.SandboxTimeoutError:
                        # SDK 1.3.2 raises for a server lifetime expiry even with
                        # raise_on_termination=False; poll still proves it stopped.
                        pass
                    result.terminated = await sb.poll.aio() is not None
                    if result.exit_code is None:
                        result.exit_code = sb.returncode
                    if not result.terminated:
                        raise RuntimeError("sandbox termination not confirmed")
                    _audit("terminated", sb.object_id)
            except Exception as exc:
                result.status = "error"
                result.outputs.clear()
                result.output_sha256.clear()
                result.reason += f"; termination unconfirmed: {type(exc).__name__}: {exc}"


def run_untrusted(code: str, inputs: dict[str, bytes], timeout_s=120) -> RunResult:
    """Run synchronously, with at most 120 s sandbox lifetime plus 25 s cleanup.

    Runtime setup and output collection consume the sandbox lifetime; startup can
    include an image build before the handle is returned. Async callers should
    offload this synchronous API to a thread. Never pass controller credentials
    in code or declared inputs.
    """
    started = time.monotonic()
    result = RunResult()
    try:
        names = _validate(code, inputs, timeout_s)
        # Snapshot the mapping and hash bytes before handing independent copies
        # to Modal; no sandbox hash is authoritative for controller inputs.
        copied = dict(inputs)
        result.input_sha256 = {name: hashlib.sha256(data).hexdigest() for name, data in copied.items()}
        asyncio.run(_run(code, copied, names, timeout_s, result))
    except ValueError as exc:
        result.status = "rejected"
        result.reason = str(exc)
    finally:
        result.elapsed = time.monotonic() - started
    return result
