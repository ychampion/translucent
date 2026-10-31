"""`python -m translucent.exec_child`: run one system adapter's execute() in a child process.

The parent (translucent.executors.run_child) sends one JSON request on stdin:

  {"system": <id>, "adapter": {"module", "class"}, "action", "params", "workdir", "max_wall_s"}

and reads one line from stdout: {"result": {"output": ...} | {"error", "kind"}, "sha256": <sha256 of the result's
canonical JSON>}. While execute() runs, fd 1 points at stderr, so an adapter's prints and its solver's console
output cannot corrupt that line. The child never opens the ledger.

The adapter module is the one the parent's in-process adapter object came from: trusted, pinned code, never a name
taken from a ledger record or from an agent. A watchdog ends the child when execute() runs past max_wall_s.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
import threading
import traceback
from functools import reduce
from pathlib import Path


def _encode(body) -> bytes:
    # canonical JSON; NaN and inf pass through, so the parent refuses them with a reason instead of a crash here
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def load_adapter(system_id: str, ref: dict | None):
    """The registered adapter for `system_id`, importing the parent's adapter module first (it may register
    itself); if it does not, its class is instantiated and registered."""
    from translucent import systems
    module = importlib.import_module(ref["module"]) if ref else None
    try:
        adapter = systems.get(system_id)
    except KeyError:
        if module is None:
            raise
        adapter = reduce(getattr, ref["class"].split("."), module)()
        systems.register(adapter)
    if adapter.spec.id != system_id:
        raise RuntimeError(f"{ref} provides system {adapter.spec.id}, not {system_id}")
    return adapter


def encode_output(output) -> dict:
    table = output.table
    return {"table": {"time_unit": table.time_unit, "t": list(table.t),
                      "series": {q: [u, list(v)] for q, (u, v) in table.series.items()},
                      "scalars": {q: [v, u] for q, (v, u) in table.scalars.items()}},
            "files": {name: str(Path(p).resolve()) for name, p in output.files.items()},
            "solver": dict(output.solver), "executed": dict(output.executed)}


def main() -> int:
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    guard, sent = threading.Lock(), []

    def send(result: dict) -> bool:
        with guard:
            if sent:
                return False
            sent.append(True)
            out.write(json.dumps({"result": result, "sha256": hashlib.sha256(_encode(result)).hexdigest()}) + "\n")
            out.flush()
            return True

    def overrun(limit: float):
        if send({"error": f"execution ran past its wall-time limit of {limit:g} s", "kind": "timeout"}):
            os._exit(124)

    try:
        req = json.loads(sys.stdin.read())
        adapter = load_adapter(req["system"], req.get("adapter"))
        limit = float(req["max_wall_s"])
        watchdog = threading.Timer(limit, overrun, args=(limit,))
        watchdog.daemon = True
        watchdog.start()
        try:
            result = {"output": encode_output(adapter.execute(req["action"], req["params"], Path(req["workdir"])))}
        finally:
            watchdog.cancel()
    except Exception as e:  # noqa: BLE001 - every failure goes back to the parent, typed, and becomes a record
        traceback.print_exc()
        kind = "not_executable" if type(e).__name__ == "NotExecutable" else "exception"
        result = {"error": f"{type(e).__name__}: {e}", "kind": kind}
    send(result)
    return 0 if "output" in result else 1


if __name__ == "__main__":
    sys.exit(main())
