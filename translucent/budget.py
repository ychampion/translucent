"""Hard spending caps per paid provider. Every paid model call in this repo goes through translucent.providers.

Caps are set in CAPS_USD. Neither provider exposes a billing API we use, so:
1. before a call, reserve its worst-case cost (output at the max_tokens ceiling, inputs over-estimated);
   refuse if committed + worst case would pass the cap;
2. after the call, settle the reservation with the cost computed from the response's `usage`;
3. a call that fails or returns no usage keeps its full reservation (never under-count); the one exception is
   a 4xx rejection (bad request, auth, not found), which is refused before any generation and settles at $0.

Ledgers: budget/<provider>_ledger.jsonl (append-only). `python -m translucent.budget` prints balances.
"""
import json
import os
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUDGET_DIR = ROOT / "budget"
CAPS_USD = {"meta": 145.00, "fireworks": 25.00}
SAFETY = 1.25  # headroom on every pre-call estimate


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    def __init__(self, provider):
        self.provider = provider
        self.cap = CAPS_USD[provider]
        self.path = BUDGET_DIR / f"{provider}_ledger.jsonl"
        self.lock = self.path.with_suffix(".lock")

    def _locked(self):
        ledger = self

        class _Lock:
            def __enter__(self):
                BUDGET_DIR.mkdir(parents=True, exist_ok=True)
                deadline = time.time() + 30
                while True:
                    try:
                        self.fd = os.open(ledger.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                        return self
                    except FileExistsError:
                        if time.time() > deadline:
                            raise BudgetExceeded(f"{ledger.lock} busy for 30 s; remove it only if no call is running")
                        time.sleep(0.1)

            def __exit__(self, *exc):
                os.close(self.fd)
                os.remove(ledger.lock)
        return _Lock()

    def _records(self):
        if not self.path.exists():
            return []
        return [json.loads(ln) for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def balance(self):
        """Committed spend: settled costs plus every reservation not yet settled."""
        reserved, settled = {}, {}
        for r in self._records():
            (reserved if r["kind"] == "reserve" else settled)[r["id"]] = r["usd"]
        committed = sum(settled.values()) + sum(v for k, v in reserved.items() if k not in settled)
        return {"provider": self.provider, "cap_usd": self.cap, "committed_usd": round(committed, 6),
                "remaining_usd": round(self.cap - committed, 6), "calls": len(reserved),
                "unsettled": [k for k in reserved if k not in settled]}

    def _append(self, rec):
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def reserve(self, usd, what):
        # stop switch: budget/<provider>.paused blocks every new call until the file is removed
        if (BUDGET_DIR / f"{self.provider}.paused").exists():
            raise BudgetExceeded(f"{self.provider} calls are paused ({BUDGET_DIR / f'{self.provider}.paused'})")
        with self._locked():
            b = self.balance()
            if b["committed_usd"] + usd > self.cap:
                raise BudgetExceeded(f"{self.provider} {what}: worst case ${usd:.4f} would pass the "
                                     f"${self.cap:.2f} cap (committed ${b['committed_usd']:.4f})")
            rid = uuid.uuid4().hex
            self._append({"kind": "reserve", "id": rid, "usd": usd, "what": what, "t": time.time()})
            return rid

    def settle(self, rid, usd, detail):
        with self._locked():
            self._append({"kind": "settle", "id": rid, "usd": usd, "detail": detail, "t": time.time()})


def balances():
    return {p: Ledger(p).balance() for p in CAPS_USD}


if __name__ == "__main__":
    print(json.dumps(balances(), indent=1))
