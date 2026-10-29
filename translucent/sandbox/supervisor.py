"""Root-owned runtime. Never import this module from the agent interpreter."""

import ctypes
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time

CONTROL = Path("/control")
UID = 10001
LOG_LIMIT = 1024 * 1024
OUTPUT_LIMIT = 1024 * 1024


def kill_analysis():
    """Kill by UID so setsid/double-fork cannot escape a process-group kill."""
    for _ in range(50):
        live = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                info = (entry / "status").read_text()
                owner = next(x for x in info.splitlines() if x.startswith("Uid:"))
                state = next(x for x in info.splitlines() if x.startswith("State:"))
                if int(owner.split()[1]) == UID and "Z" not in state:
                    live.append(int(entry.name))
            except (OSError, StopIteration):
                continue
        if not live:
            return
        for pid in live:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(0.02)
    raise RuntimeError("analysis descendants did not stop; refuse output collection")


def collect(names):
    """Freeze workers first; inspect every declared file before copying any bytes."""
    opened = []
    total = 0
    try:
        directory = os.open("/work/outputs", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if set(os.listdir(directory)) != set(names):
                raise ValueError("undeclared or missing output file")
            for name in names:
                # The controller already validates flat names; repeat at this boundary.
                if not name or "/" in name or "\\" in name or name in (".", ".."):
                    raise ValueError("invalid output name")
                fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
                opened.append((name, fd))
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("outputs must be regular files without links")
                total += info.st_size
                if total > OUTPUT_LIMIT:
                    raise ValueError("declared outputs exceed 1 MiB total")
        finally:
            os.close(directory)
        metadata = {}
        for name, fd in opened:
            digest = hashlib.sha256()
            size = 0
            with (CONTROL / "exports" / name).open("xb") as target:
                while chunk := os.read(fd, 1024 * 1024):
                    size += len(chunk)
                    if size > os.fstat(fd).st_size or size > OUTPUT_LIMIT:
                        raise ValueError("output changed during collection")
                    digest.update(chunk)
                    target.write(chunk)
            metadata[name] = {"size": size, "sha256": digest.hexdigest()}
        return metadata
    finally:
        for _, fd in opened:
            os.close(fd)


def main(budget):
    if os.geteuid() != 0:
        raise RuntimeError("supervisor needs root to drop privileges and reap all analysis workers")
    # Orphaned workers reparent here instead of retaining open pipes indefinitely.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0):  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER failed")
    names = json.loads((CONTROL / "manifest.json").read_text())
    deadline = time.monotonic() + budget
    logs = {"stdout": bytearray(), "stderr": bytearray()}
    seen = 0
    result = {"status": "error", "exit_code": None, "outputs": {}, "reason": "",
              "sandbox_environment": dict(os.environ)}
    proc = None
    try:
        proc = subprocess.Popen(
            ["/usr/local/bin/python", "-I", "-u", "/opt/spike/bootstrap.py"],
            cwd="/work", stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            user=UID, group=UID, extra_groups=[], start_new_session=True,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp",
                 "TMPDIR": "/tmp", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
        )
        timed_out = False
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
            selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                if time.monotonic() >= deadline:
                    timed_out = True
                    kill_analysis()
                    break
                if proc.poll() is not None:
                    kill_analysis()
                for key, _ in selector.select(timeout=0.05):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        remaining = max(0, LOG_LIMIT - sum(map(len, logs.values())))
                        retained = chunk[:remaining]
                        logs[key.data].extend(retained)
                        # Persist the bounded prefix outside the guest through
                        # Modal's exec log stream before OOM can kill this helper.
                        view = memoryview(retained)
                        fd = 1 if key.data == "stdout" else 2
                        while view:
                            view = view[os.write(fd, view):]
                        seen += len(chunk)
            # Code can close both pipes and continue computing, so EOF is not completion.
            while proc.poll() is None and not timed_out:
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                time.sleep(0.02)
        kill_analysis()
        result["exit_code"] = proc.wait(timeout=2)
        if timed_out:
            result["status"] = "timeout"
            result["reason"] = "analysis wall-clock deadline exceeded"
        elif result["exit_code"] != 0:
            result["status"] = "error"
            result["reason"] = "analysis exited unsuccessfully"
        else:
            try:
                result["outputs"] = collect(names)
                result["status"] = "ok"
            except (OSError, ValueError) as exc:
                result["status"] = "rejected"
                result["reason"] = str(exc)
    except Exception as exc:
        result["status"] = "error"
        result["reason"] = f"supervisor: {type(exc).__name__}: {exc}"
    finally:
        kill_analysis()
        if proc is not None:
            proc.wait(timeout=2)
            proc.stdout.close()
            proc.stderr.close()
        for name, data in logs.items():
            (CONTROL / f"{name}.bin").write_bytes(data)
        result["logs_truncated"] = seen > LOG_LIMIT
        (CONTROL / "result.json").write_text(json.dumps(result))


if __name__ == "__main__":
    main(float(sys.argv[1]))
