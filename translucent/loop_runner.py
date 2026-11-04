"""The loop runner: claims queued computational requests, runs them, and recovers runs abandoned by a crash.

Claim protocol (design §2.6), per request:
  1. take `<ledger>/locks/req-<sha16(request_id)>.lock` without waiting; if another process (or thread) holds it,
     the request is being run and is left alone;
  2. in one BEGIN IMMEDIATE, re-derive the state. Queued: record execution(started, n + 1). Running, with the lock
     free: its runner died, so record execution(abandoned, n); the fold puts a deterministic action with n < 3 back
     in the queue and it starts again as attempt n + 1 in the same transaction; anything else gets a failure;
  3. run the child (translucent.executors.run_computational) with no ledger transaction open;
  4. store the outputs, then commit the result (or a failure) in one transaction; release the lock.

All state is in the ledger and the kernel releases a dead process's lock, so a restart needs no repair step: the
next tick sees what was left running and recovers it. The API runs a runner thread; MCP calls `tick(cid, limit=1)`
inline; both can run at once against one ledger, and the lock plus admission keep every request to one execution.
"""
from __future__ import annotations

import os
import socket
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from translucent import chain, loop_reference
from translucent.executors import executor_digest, record_failure, run_computational
from translucent.ledger import Ledger
from translucent.locks import ExclusiveFileLock
from translucent.loop import (RUNNER, RequestState, adapter_for, campaign_ids, claim_path, current_objective,
                              registered_executor_digest, request_states)
from translucent.loop_contracts import ObjectiveData
from translucent.systems.base import SystemAdapter


def wall_limit(obj: ObjectiveData, adapter: SystemAdapter, action: str) -> float:
    """The action's max_wall_s, lowered by the objective's budget.max_wall_s when the person set one."""
    limit = float(adapter.spec.action(action).max_wall_s)
    cap = obj.budget.get("max_wall_s")
    return min(limit, float(cap)) if isinstance(cap, (int, float)) and not isinstance(cap, bool) else limit


class LoopRunner:
    def __init__(self, ledger_root: Path | str, *, process: str, workers: int = 2):
        self.root = Path(ledger_root)
        self.process = process                   # "api", "mcp" or "cli": recorded on each execution
        self.workers = max(1, workers)
        self.problems: list[str] = []            # requests this runner could not advance, and why

    @property
    def worker(self) -> dict:
        return {"host": socket.gethostname(), "pid": os.getpid(), "process": self.process}

    def tick(self, campaign_id: str | None = None, limit: int | None = None) -> int:
        """Advance queued and abandoned computational requests (one campaign, or all); at most `limit` of them.
        Returns how many were advanced. Planner turns are scheduled by the planner (S7), not here. A reference
        search step that is due (loop_reference) is queued first, so a restart resumes a search."""
        L = Ledger(self.root)
        try:
            cids = [cid for cid in ([campaign_id] if campaign_id else campaign_ids(L)) if not chain.concluded(L, cid)]
            for cid in cids:
                try:
                    loop_reference.queue(L, cid)
                except Exception as e:  # noqa: BLE001 - one campaign's search must not stop the others' runs
                    self.problems.append(f"{cid}: reference search not queued: {type(e).__name__}: {e}")
            todo = [(cid, rq) for cid in cids
                    for rq, st in request_states(L, cid).items()
                    if st.executor == "computational" and st.state in ("queued", "running")]
        finally:
            L.db.close()
        if limit is None and self.workers > 1 and len(todo) > 1:
            with ThreadPoolExecutor(self.workers) as pool:
                return sum(pool.map(lambda item: self.advance(*item), todo))
        done = 0
        for cid, rq in todo:
            if limit is not None and done >= limit:
                break
            done += self.advance(cid, rq)
        return done

    def advance(self, cid: str, request_id: str) -> bool:
        """Claim one request and take it as far as this process can; False when it was not ours to advance."""
        lock = ExclusiveFileLock(claim_path(self.root, request_id))
        if not lock.try_acquire():
            return False
        L = None
        try:
            L = Ledger(self.root)
            obj = current_objective(L, cid)
            try:
                adapter = adapter_for(obj)
            except KeyError as e:
                self.problems.append(f"{cid}/{request_id}: {e.args[0]} is not registered in this process")
                return False
            if executor_digest(adapter) != registered_executor_digest(obj):
                self.problems.append(f"{cid}/{request_id}: the installed {obj.system['id']} code or solver differs "
                                     "from what the campaign registered; not run")
                return False
            claimed, st = self._claim(L, cid, request_id, adapter)
            if st is not None:
                run_computational(L, cid, st, adapter, worker=self.worker,
                                  workdir=self.root / "work" / "exec" / request_id / str(st.attempt),
                                  timeout_s=wall_limit(obj, adapter, st.action))
            return claimed
        except Exception as e:  # noqa: BLE001 - the request stays running; the next tick records it abandoned
            self.problems.append(f"{cid}/{request_id}: {type(e).__name__}: {e}\n{traceback.format_exc(limit=5)}")
            return False
        finally:
            if L is not None:
                L.db.close()
            lock.release()

    def _execution(self, st: RequestState, attempt: int, phase: str, adapter: SystemAdapter) -> dict:
        return {"request_id": st.request_id, "content_hash": st.approved_hash, "attempt": attempt, "phase": phase,
                "worker": self.worker, "executor_digest": executor_digest(adapter)}

    def _claim(self, L: Ledger, cid: str, rq: str, adapter: SystemAdapter) -> tuple[bool, RequestState | None]:
        """(advanced, the state to run): one transaction that recovers an abandoned attempt and/or starts the next."""
        with L.transaction():
            st = request_states(L, cid).get(rq)
            if st is None or st.executor != "computational":
                return False, None
            if st.state == "running":                 # we hold the lock, so its holder is gone
                n = st.attempt
                chain.append(L, cid, "execution", self._execution(st, n, "abandoned", adapter), RUNNER,
                             chain.natural_id("aba", rq, n))
                st = request_states(L, cid)[rq]
                if st.state != "queued":
                    deterministic = adapter.spec.action(st.action).deterministic
                    record_failure(L, cid, st, n, f"the runner stopped during attempt {n}; " + (
                        f"{st.action} is not deterministic, so it is not run again" if not deterministic else
                        f"that was the last of {n} attempts"), retryable=False)
                    return True, None
            if st.state != "queued":
                return False, None
            n = st.attempt + 1
            chain.append(L, cid, "execution", self._execution(st, n, "started", adapter), RUNNER,
                         chain.natural_id("exe", rq, n))
            return True, request_states(L, cid)[rq]
