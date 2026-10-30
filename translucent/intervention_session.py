"""Private replay identity and command orchestration for paid intervention runs.

The controller owns the manifest; planner tools cannot read it. It contains the
entropy needed to reproduce record IDs and per-trial salts. Copy it together with
the chat recordings for strict offline replay, never into a public replay bundle.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from dataclasses import asdict
from pathlib import Path
import secrets
import time

from translucent.intervention import canonical


class ReplayIdentity:
    """A ledger-local entropy stream; ordinary campaigns keep their random IDs."""

    def __init__(self, seed, epoch):
        self.seed = bytes.fromhex(seed)
        if len(self.seed) != 32 or not math.isfinite(epoch):
            raise ValueError("Invalid intervention replay identity")
        self.epoch = epoch
        self.counters = {"id": 0, "salt": 0, "time": 0}

    def _next(self, purpose):
        self.counters[purpose] += 1
        value = f"{purpose}:{self.counters[purpose]}".encode()
        return hmac.new(self.seed, value, hashlib.sha256).hexdigest()

    def identifier(self):
        return self._next("id")[:32]

    def salt(self):
        return self._next("salt")

    def timestamp(self):
        self.counters["time"] += 1
        return self.epoch + self.counters["time"] / 1000


def session_manifest(
    store,
    settings,
    *,
    split="loco",
    muse_only=False,
    replay=False,
    selected=None,
    min_trials=20,
):
    """Create a private run manifest or validate the requested replay settings."""
    store = Path(store)
    requested = dict(
        **asdict(settings),
        split=split,
        muse_only=muse_only,
        selected=sorted(selected) if selected is not None else None,
        min_trials=min_trials,
    )
    path = store / "manifest.json"
    if replay:
        manifest = json.loads(path.read_text())
        if manifest.get("version") != 1 or manifest.get("settings") != requested:
            raise ValueError("Replay settings differ from the recorded campaign")
        if (
            manifest.get("digest")
            != hashlib.sha256(
                canonical({k: v for k, v in manifest.items() if k != "digest"})
            ).hexdigest()
        ):
            raise ValueError("Replay manifest digest differs")
    else:
        store.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(store, 0o700)
        manifest = dict(
            version=1,
            settings=requested,
            completion_receipt="run.json",
            seed=secrets.token_hex(32),
            epoch=time.time(),
            private="Controller replay entropy; never expose through planner tools or export",
        )
        manifest["digest"] = hashlib.sha256(canonical(manifest)).hexdigest()
        from translucent.intervention_chat import atomic_recording

        atomic_recording(path, canonical(manifest))
    return manifest


def decision_bytes(ledger, campaign_id):
    """The exact committed record bodies, including IDs, sequences and hashes."""
    return [
        body
        for (body,) in ledger.db.execute(
            "SELECT body FROM record WHERE kind='intervention' AND campaign_id=? "
            "AND json_extract(body,'$.type')='decision' ORDER BY rowid",
            (campaign_id,),
        )
    ]


def run_intervention(
    ledger_path,
    settings,
    *,
    split="loco",
    muse_only=False,
    recordings=None,
    replay=None,
    client=None,
    selected=None,
    min_trials=20,
):
    """Run one fresh campaign; replay checks the exact committed decision bytes."""
    from translucent.intervention_chat import ChatSession, atomic_recording
    from translucent.intervention_errors import RecordingError
    from translucent.intervention_runtime import open_intervention
    from translucent.ledger import Ledger
    from translucent.recordings import NoRecording

    ledger_path = Path(ledger_path)
    if (ledger_path / "ledger.sqlite").exists():
        raise ValueError(
            "Use a fresh ledger directory for an intervention run or replay"
        )
    store = Path(replay or recordings or ledger_path / "recordings")
    manifest = session_manifest(
        store,
        settings,
        split=split,
        muse_only=muse_only,
        replay=replay is not None,
        selected=selected,
        min_trials=min_trials,
    )
    ledger = Ledger(ledger_path)
    try:
        ledger.intervention_identity = ReplayIdentity(
            manifest["seed"], manifest["epoch"]
        )
        campaign, fold, sealed = open_intervention(
            ledger,
            ledger_path / "sealed",
            split=split,
            selected=selected,
            min_trials=min_trials,
            budget_usd=settings.budget_usd,
        )
        session = ChatSession(settings, store, replay=replay is not None, client=client)
        if muse_only:
            from translucent.intervention_muse import run_muse_only

            result = run_muse_only(ledger, campaign, fold, sealed, session)
        else:
            from translucent.intervention_live import run_live

            result = run_live(
                ledger, campaign, fold, sealed, session, step_cap=settings.step_cap
            )
        if replay is not None and result["status"] == "replay_error":
            raise NoRecording(result["reason"])
        session.assert_consumed()
        decisions = decision_bytes(ledger, campaign.id)
        proof = store / "decisions.json"
        completion = store / "run.json"
        run_receipt = dict(
            version=1,
            status=result["status"],
            reason=result["reason"],
            tool_calls=result.get("tool_calls", 0),
            spent_usd=session.spent_usd,
            recording_count=len(session.records),
        )
        if replay is not None:
            if json.loads(proof.read_text()) != decisions:
                raise ValueError("Replay committed decisions differ from the recording")
            if completion.exists():
                if json.loads(completion.read_text()) != run_receipt:
                    raise NoRecording(
                        "Replay run status, reason, counts or spend differ from the recording"
                    )
            elif manifest.get("completion_receipt"):
                raise NoRecording(
                    "Replay is missing its required run.json completion receipt"
                )
            # Older manifests have no completion receipt. They retain their
            # exact-request and decision checks, and a missing tape still fails
            # above rather than becoming a successful replay_error result.
            result["decisions_identical"] = True
        else:
            try:
                atomic_recording(proof, canonical(decisions))
                atomic_recording(completion, canonical(run_receipt))
            except RecordingError as error:
                result.update(status="recording_error", reason=str(error))
        result.update(
            campaign_id=campaign.id,
            ledger=str(ledger_path),
            recordings=str(store),
            committed_decisions=len(decisions),
            replayed=replay is not None,
        )
        return result
    finally:
        ledger.db.close()


def dry_run(settings, *, split="loco", muse_only=False):
    """Estimate uncached cost without constructing a provider or a ledger.

    A planner chooses how many calls to make and how much history to request.
    The estimate therefore states its token assumptions rather than pretending
    to predict its eventual bill. Live spending is independently hard capped.
    """
    from translucent.providers import TOKEN_PRICES
    from translucent.runtime import PROFILES
    from translucent.systems.nist_heat import load_dataset

    trials = [
        t for t in load_dataset() if t["t"] and t["delay_after_vent_s"] is not None
    ]
    folds = {
        "loco": len({(t["format"], t["soc"]) for t in trials}),
        "lofo": len({t["format"] for t in trials}),
        "prequential": len(trials),
    }[split]
    provider, model = PROFILES[settings.profile]["orchestrator"]
    input_price, _, output_price = TOKEN_PRICES[provider][model]
    calls = len(trials) if muse_only else folds * settings.step_cap
    maximum = calls * 2 if muse_only else calls
    input_tokens = 4000 if muse_only else 8000
    per_call = input_tokens * input_price + settings.max_tokens * output_price
    return dict(
        dry_run=True,
        profile=settings.profile,
        provider=provider,
        model=model,
        split=split,
        mode="muse_only" if muse_only else "planner",
        folds=folds,
        held_out_trials=len(trials),
        planned_calls=calls,
        calls_with_all_watch_windows=maximum if muse_only else None,
        call_basis="One initial call per held-out trial, plus one if observing"
        if muse_only
        else "One model request per tool call at the per-fold tool cap; "
        "actual count is planner-dependent",
        input_tokens_per_call_assumed=input_tokens,
        output_tokens_per_call_ceiling=settings.max_tokens,
        uncached_estimate_usd=round(calls * per_call, 6),
        all_watch_estimate_usd=round(maximum * per_call, 6) if muse_only else None,
        retry_attempts_per_request=settings.retries + 1,
        prices_usd_per_million=dict(input=input_price * 1e6, output=output_price * 1e6),
        budget_usd=settings.budget_usd,
        note="Estimate excludes retries and repair/stall replies; token history is uncertain. "
        "Each attempt reserves a conservative ceiling before any provider call. "
        "No network, provider client, recording store or ledger is opened by dry-run.",
    )
