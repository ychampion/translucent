"""Trusted intervention controller. Planner tools never expose the sealed store."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import time
import uuid

from translucent.contracts import Campaign, Event
from translucent.intervention_records import InterventionRecord
from translucent.intervention_errors import ToolRejected
from translucent.ledger import AdmissionError


class RuleRejected(AdmissionError, ToolRejected):
    """Reject an explicit planner precondition without hiding integrity failures."""


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def identifier(ledger, prefix, length=None):
    """Use private run entropy when replaying, preserving legacy IDs otherwise."""
    identity = getattr(ledger, "intervention_identity", None)
    value = identity.identifier() if identity is not None else uuid.uuid4().hex
    return prefix + (value[:length] if length is not None else value)


def timestamp(ledger):
    """Return the recorded run clock only for opt-in intervention sessions."""
    identity = getattr(ledger, "intervention_identity", None)
    return identity.timestamp() if identity is not None else time.time()


SEALED_OUTCOME_FIELDS = (
    "outcome",
    "t_ignition",
    "flags",
    "truncated_at",
    "event_sources",
)


def seal(payload, salt, *, version=1):
    """Commit a legacy outcome or every outcome-dependent annotation.

    The version belongs to the public trial record. Missing versions retain
    the original outcome-only commitment so existing receipts remain readable.
    """
    if type(version) is not int or version not in (1, 2):
        raise ValueError(f"Unsupported outcome seal version {version!r}")
    if not isinstance(salt, str):
        raise ValueError("Outcome seal salt must be a string")
    if version == 1:
        outcome = payload["outcome"] if isinstance(payload, dict) else payload
        if not isinstance(outcome, str):
            raise ValueError("Outcome seal label must be a string")
        data = (outcome + salt).encode()
    else:
        data = canonical({field: payload[field] for field in SEALED_OUTCOME_FIELDS})
        data += salt.encode()
    return hashlib.sha256(data).hexdigest()


def matches_seal(trial, payload):
    """Check a revealed payload against the version registered at ingestion."""
    try:
        return (
            payload["outcome"] in ("rescued", "delayed_ignition", "ignited")
            and seal(payload, payload["salt"], version=trial.get("seal_version", 1))
            == trial["sealed_hash"]
        )
    except (KeyError, TypeError, ValueError):
        return False


def records(ledger, cid, kind=None):
    cache = getattr(ledger, "_intervention_verification_cache", None)
    if cache is not None:
        return cache.records(cid, kind)
    if kind is None:
        return ledger.list("intervention", cid)
    rows = ledger.db.execute(
        "SELECT body FROM record WHERE kind='intervention' AND campaign_id=? AND json_extract(body,'$.type')=? ORDER BY rowid",
        (cid, kind),
    )
    return [InterventionRecord.model_validate_json(body) for (body,) in rows]


def _latest(ledger, cid):
    row = ledger.db.execute(
        "SELECT body FROM record WHERE kind='intervention' AND campaign_id=? ORDER BY rowid DESC LIMIT 1",
        (cid,),
    ).fetchone()
    return InterventionRecord.model_validate_json(row[0]) if row else None


def get(ledger, cid, rid, kind=None):
    cache = getattr(ledger, "_intervention_verification_cache", None)
    r = cache.by_id.get(rid) if cache is not None else ledger.get("intervention", rid)
    if r is None or r.campaign_id != cid or (kind is not None and r.type != kind):
        raise ValueError("No such intervention record in this campaign")
    return r


def protocol(ledger, cid):
    found = records(ledger, cid, "protocol")
    if len(found) != 1:
        raise ValueError("Exactly one registered intervention protocol required")
    return found[0]


def _digest(r):
    return hashlib.sha256(canonical(r.model_dump(exclude={"digest"}))).hexdigest()


def record(ledger, cid, kind, data):
    """Record and sequence event atomically, using the existing ledger schema and triggers."""
    ledger.db.execute("BEGIN IMMEDIATE")
    try:
        previous = _latest(ledger, cid)
        seq = ledger.db.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM event WHERE campaign_id=?", (cid,)
        ).fetchone()[0]
        rec = InterventionRecord(
            id=identifier(ledger, "int_"),
            campaign_id=cid,
            type=kind,
            seq=seq,
            data=data,
            previous_hash=previous.digest if previous else "",
            digest="",
        )
        rec = rec.model_copy(update={"digest": _digest(rec)})
        ledger.add(rec)
        event = Event(
            seq=seq,
            campaign_id=cid,
            t=timestamp(ledger),
            type="intervention.recorded",
            actor="intervention",
            title=f"Intervention {kind} recorded",
            refs=[rec.id],
            data={"record": rec.model_dump(mode="json")},
        )
        ledger.db.execute(
            "INSERT INTO event VALUES (?,?,?)", (cid, seq, event.model_dump_json())
        )
        ledger.db.execute("COMMIT")
        return rec
    except BaseException:
        ledger.db.execute("ROLLBACK")
        raise


def create(
    ledger,
    *,
    title="NIST intervention benchmark",
    splits=None,
    heldout_cap=16,
    min_trials=20,
    heater_off_lag_s=10,
    unsafe_ratio=10,
    budget_usd=0,
):
    from translucent.intervention_actions import DEFAULT_COSTS

    if type(heldout_cap) is not int or not 1 <= heldout_cap <= 100:
        raise ValueError("heldout_cap must be an integer in [1,100]")
    if type(min_trials) is not int or min_trials < 1:
        raise ValueError("min_trials must be positive")
    if not math.isfinite(budget_usd) or budget_usd < 0:
        raise ValueError("budget_usd must be finite and nonnegative")
    if heater_off_lag_s not in (7, 10) or unsafe_ratio not in (5, 10, 20):
        raise ValueError(
            "Only pre-registered lag and cost sensitivity values are allowed"
        )
    costs = {
        **DEFAULT_COSTS,
        "unsafe": float(10 * unsafe_ratio),
        "watch_ignition": float(10 * unsafe_ratio),
    }
    # Only new intervention ledgers acquire this index; legacy receipts are untouched.
    ledger.db.execute(
        "CREATE INDEX IF NOT EXISTS intervention_type ON record(campaign_id,json_extract(body,'$.type')) WHERE kind='intervention'"
    )
    c = Campaign(
        id=identifier(ledger, "nist_", 16),
        objective="The heater has just been cut. Is stopping heat enough, or must we escalate?",
        title=title,
        inputs=[],
        memory="off",
        budget_usd=budget_usd,
        created_at=timestamp(ledger),
    )
    ledger.add(c)
    from translucent.contracts import Metric, Predicate, Protocol

    registered = Protocol(
        id=identifier(ledger, "protocol_"),
        campaign_id=c.id,
        author="benchmark",
        created_at=timestamp(ledger),
        metrics=[
            Metric(
                id="mean_decision_cost",
                name="Mean cost after recorded heater-off",
                quantities=["derived:mean_decision_cost"],
                unit="cost",
                predicate=Predicate(
                    type="value_within",
                    params={"lo": 0, "hi": 0, "unit": "cost"},
                    measurement_ids=[],
                ),
                min_methods=1,
                needs_verification=False,
            )
        ],
    )
    ledger.add(registered)
    record(
        ledger,
        c.id,
        "protocol",
        dict(
            primary_metric="mean_decision_cost",
            secondary_metrics=[
                "unsafe",
                "missed",
                "brier",
                "logloss",
                "accuracy",
                "abstain",
            ],
            splits=splits or ["loco", "lofo", "prequential"],
            heldout_cap_K=heldout_cap,
            min_trials=min_trials,
            abstention_cost=costs["abstain"],
            alpha=0.05,
            probability_clip=0.001,
            bootstrap_replicates=64,
            probability_atol=1e-9,
            numerical_version=2,
            verification_version=3,
            require_baseline_screens=True,
            bootstrap_generator="PCG64",
            seed=0,
            observe_options=[30, 60],
            costs=costs,
            calibration_tolerance=0.01,
            amendment=3,
            heater_off_lag_s=heater_off_lag_s,
            unsafe_ratio=unsafe_ratio,
            amendment_registration={
                "version": 3,
                "decision_date": "2026-10-30",
                "before_scoring_commit": "2fed2df",
                "reason": "Frozen baselines are comparators, not candidate learning strategies",
            },
            heldout_cap_unit="screened agent candidate strategies; baseline comparators do not consume K",
            primary_test={
                "name": "paired two-sided sign-flip permutation",
                "exact_max_discordant": 20,
                "resamples": 100000,
                "seed": 0,
                "correction_family": "submitted screened agent candidates times registered baseline comparators",
            },
            preregistration_reasons=[
                "Observation must be available under laboratory action costs",
                "Unsafe-only McNemar has inadequate resolution with few discordances",
                "Video markers support a 7 s heater-off lag sensitivity alongside default 10 s",
                "Frozen baseline comparators are not candidate learning strategies",
            ],
            sensitivity_plan={
                "heater_off_lag_s": [10, 7],
                "unsafe_escalation_ratios": [5, 10, 20],
                "cost_sensitivities_at": "10 s lag, with inferred trials",
            },
            calibration_metric="five-bin expected calibration error, with an additional Brier guard",
            calibration_bins=[0, 0.2, 0.4, 0.6, 0.8, 1],
            funnel={
                "screen": "inner LOCO on revealed training only",
                "max_inner_unsafe_fraction": 0.5,
                "min_train_trials": 10,
                "proposed": 0,
                "screened_out": 0,
                "submitted": 0,
                "baselines_registered": 0,
                "candidate_unit": "agent learning strategy",
            },
            target="recorded ignition after recorded heater-off; no suppression counterfactual",
            censoring="finite observation window; censored trials flagged and sensitivity reported",
        ),
    )
    ledger.emit(c.id, "campaign.started", "intervention", title, refs=[registered.id])
    return c


def admit(ledger, rec):
    ledger._require([rec.campaign_id], ("campaign",), "intervention campaign")
    previous = _latest(ledger, rec.campaign_id)
    if rec.digest != _digest(rec) or rec.previous_hash != (
        previous.digest if previous else ""
    ):
        raise AdmissionError("Intervention hash chain differs")
    if ledger.db.execute(
        "SELECT 1 FROM event WHERE campaign_id=? AND json_extract(body,'$.type')='campaign.finished' LIMIT 1",
        (rec.campaign_id,),
    ).fetchone():
        raise AdmissionError("Campaign is finished")
    if rec.type == "protocol":
        if previous or ledger.events(rec.campaign_id):
            raise AdmissionError(
                "Intervention protocol is fixed before the campaign starts"
            )
    else:
        protocol(ledger, rec.campaign_id)
    if (
        rec.type == "policy"
        and rec.data["kind"] == "agent"
        and rec.data["spec"]["family"] == "muse"
    ):
        raise RuleRejected(
            "The Muse-only family is reserved for a frozen baseline comparator"
        )
    if (
        rec.type == "policy"
        and rec.data["kind"] == "agent"
        and protocol(ledger, rec.campaign_id).data.get("amendment", 0) >= 3
    ):
        if any(
            split != "prequential"
            for split in protocol(ledger, rec.campaign_id).data["splits"]
        ) and records(ledger, rec.campaign_id, "reveal"):
            raise RuleRejected(
                "Register non-prequential candidate specs before the first outcome reveal"
            )
    if rec.type == "trial":
        version = rec.data.get("seal_version", 1)
        if type(version) is not int or version not in (1, 2):
            raise AdmissionError("Unsupported outcome seal version")
        if any(
            r.data.get("kind") == "visual_corroboration"
            for r in records(ledger, rec.campaign_id, "reference")
        ):
            raise AdmissionError(
                "Visual label audit is closed; new sealed trials require a new campaign"
            )
        ledger._require([rec.data["series_artifact"]], ("artifact",), "trial trace")
    if rec.type == "reference" and rec.data.get("kind") == "visual_corroboration":
        trials = records(ledger, rec.campaign_id, "trial")
        revealed = {
            r.data["trial_id"] for r in records(ledger, rec.campaign_id, "reveal")
        }
        if not trials or any(t.id not in revealed for t in trials):
            raise AdmissionError(
                "Bulk visual labels require all campaign outcomes to be revealed"
            )
    if rec.type == "fold":
        d = rec.data
        train = [get(ledger, rec.campaign_id, i, "trial") for i in d["train_ids"]]
        test = [get(ledger, rec.campaign_id, i, "trial") for i in d["test_ids"]]
        if (
            set(d["train_ids"]) & set(d["test_ids"])
            or len(set(d["train_ids"])) != len(train)
            or len(set(d["test_ids"])) != len(test)
        ):
            raise AdmissionError("Training and held-out trials must be distinct")
        if any(t.data["scope"] != d["scope"] for t in train + test):
            raise AdmissionError("Fold scope mismatch")
        if d["split"] != "prequential" and any(
            r.data["scope"] == d["scope"]
            for r in records(ledger, rec.campaign_id, "fold")
        ):
            raise AdmissionError("Fold already registered")
        sealed_ids = set(
            d["test_ids"]
            if d["split"] == "prequential"
            else d["train_ids"] + d["test_ids"]
        )
        if any(
            r.data["trial_id"] in sealed_ids
            for r in records(ledger, rec.campaign_id, "reveal")
        ):
            raise AdmissionError("Register the fold before revealing its outcomes")
        if d["split"] == "loco" and {
            tuple(sorted(t.data["condition"].items())) for t in train
        } & {tuple(sorted(t.data["condition"].items())) for t in test}:
            raise AdmissionError("LOCO condition leakage")
        if d["split"] == "lofo" and {t.data["condition"]["format"] for t in train} & {
            t.data["condition"]["format"] for t in test
        }:
            raise AdmissionError("LOFO format leakage")
        if (
            d["split"] == "prequential"
            and train
            and max(t.data["exp"] for t in train) >= min(t.data["exp"] for t in test)
        ):
            raise AdmissionError(
                "Prequential training must precede test trials in Exp order"
            )
    if rec.type == "fit":
        d = rec.data
        fold = get(ledger, rec.campaign_id, d["fold_id"], "fold")
        get(ledger, rec.campaign_id, d["policy_id"], "policy")
        if d["train_ids"] != fold.data["train_ids"]:
            raise AdmissionError(
                "Fit must use exactly the registered training partition"
            )
        reveals = {
            r.data["trial_id"]: r.id for r in records(ledger, rec.campaign_id, "reveal")
        }
        if any(i not in reveals for i in d["train_ids"]):
            raise AdmissionError("Training outcomes must be revealed before fitting")
        if d["reveal_ids"] != [reveals[i] for i in d["train_ids"]]:
            raise AdmissionError("Fit reveal provenance mismatch")
    if rec.type == "submission":
        p = get(ledger, rec.campaign_id, rec.data["policy_id"], "policy")
        if any(d.data["scored"] for d in records(ledger, rec.campaign_id, "decision")):
            raise RuleRejected(
                "Held-out submissions close at the first scored decision; screen all candidates first"
            )
        submitted = records(ledger, rec.campaign_id, "submission")
        if any(s.data["policy_id"] == p.id for s in submitted):
            raise RuleRejected("Policy already submitted")
        config = protocol(ledger, rec.campaign_id).data
        amended = config.get("amendment", 0) >= 3
        candidates = (
            [
                r
                for r in submitted
                if get(ledger, rec.campaign_id, r.data["policy_id"], "policy").data[
                    "kind"
                ]
                == "agent"
            ]
            if amended
            else submitted
        )
        if (not amended or p.data["kind"] == "agent") and len(candidates) >= config[
            "heldout_cap_K"
        ]:
            raise RuleRejected("Held-out candidate submission cap reached")
        if (
            amended
            and p.data["kind"] == "agent"
            and any(split != "prequential" for split in config["splits"])
        ):
            reveals = records(ledger, rec.campaign_id, "reveal")
            if reveals and p.seq >= min(r.seq for r in reveals):
                raise RuleRejected(
                    "Register non-prequential candidate specs before the first outcome reveal"
                )
        needs_screen = p.data["kind"] == "agent" or config.get(
            "require_baseline_screens"
        )
        if needs_screen and not any(
            s.data["policy_id"] == p.id and s.data["survived"]
            for s in records(ledger, rec.campaign_id, "screen")
        ):
            raise RuleRejected(
                "A candidate must survive training-only screening before submission"
            )
    if rec.type == "decision":
        t = get(ledger, rec.campaign_id, rec.data["trial_id"], "trial")
        if any(
            r.data["trial_id"] == t.id
            for r in records(ledger, rec.campaign_id, "reveal")
        ):
            raise RuleRejected("Decision must be committed before outcome reveal")
        d = rec.data
        if d["decision"] not in ("stand_down", "escalate", "abstain", "observe"):
            raise RuleRejected("Unknown action")
        p, interval = d["p_ignite"], d["interval"]
        if (
            type(p) not in (int, float)
            or not math.isfinite(p)
            or not 0 <= p <= 1
            or len(interval) != 2
            or not all(type(v) in (int, float) and math.isfinite(v) for v in interval)
            or not 0 <= interval[0] <= p <= interval[1] <= 1
        ):
            raise RuleRejected("Invalid probability or interval")
        prior = [
            r
            for r in records(ledger, rec.campaign_id, "decision")
            if r.data["trial_id"] == t.id and r.data["policy_id"] == d["policy_id"]
        ]
        parent = d.get("parent_decision_id")
        if parent:
            initial = get(ledger, rec.campaign_id, parent, "decision")
            if (
                len(prior) != 1
                or prior[0].id != parent
                or initial.data["decision"] != "observe"
                or d["decision"] not in ("stand_down", "escalate")
            ):
                raise RuleRejected(
                    "An observation needs exactly one terminal stand-down/escalate decision"
                )
            if d.get("observe_s") != initial.data["observe_s"] or any(
                d.get(k) != initial.data.get(k)
                for k in (
                    "fit_id",
                    "fold_id",
                    "split",
                    "scored",
                    "trial_id",
                    "policy_id",
                )
            ):
                raise RuleRejected(
                    "Terminal decision must retain its paid window, fit and fold"
                )
            if not any(
                o.data["decision_id"] == parent
                for o in records(ledger, rec.campaign_id, "observation")
            ):
                raise RuleRejected(
                    "Read the paid observation before making its terminal decision"
                )
        elif prior:
            raise RuleRejected("Duplicate trial/policy decision")
        if (
            d["decision"] == "observe"
            and d.get("observe_s")
            not in protocol(ledger, rec.campaign_id).data["observe_options"]
        ):
            raise RuleRejected("Watch duration is not registered")
        if d["scored"]:
            get(ledger, rec.campaign_id, d["policy_id"], "policy")
            if not any(
                s.data["policy_id"] == d["policy_id"]
                for s in records(ledger, rec.campaign_id, "submission")
            ):
                raise RuleRejected("Policy must be submitted for held-out scoring")
            fitted = get(ledger, rec.campaign_id, d["fit_id"], "fit")
            fold = get(
                ledger,
                rec.campaign_id,
                d.get("fold_id") or fitted.data["fold_id"],
                "fold",
            )
            if (
                fitted.data["policy_id"] != d["policy_id"]
                or t.id not in fold.data["test_ids"]
                or fold.data["split"] != d["split"]
                or not set(fitted.data["train_ids"]) <= set(fold.data["train_ids"])
            ):
                raise RuleRejected(
                    "Decision is not in this fitted policy held-out fold"
                )
        policy = next(
            (
                p
                for p in records(ledger, rec.campaign_id, "policy")
                if p.id == d["policy_id"]
            ),
            None,
        )
        muse = policy is not None and policy.data["spec"]["family"] == "muse"
        if muse and not d.get("model_receipt_id"):
            raise AdmissionError("Muse decision requires a model receipt")
        if muse or d.get("model_receipt_id"):
            from translucent.intervention_muse import validate_muse_decision

            validate_muse_decision(ledger, rec.campaign_id, d, before_seq=rec.seq)
    if rec.type == "observation":
        d = rec.data
        initial = get(ledger, rec.campaign_id, d["decision_id"], "decision")
        if (
            initial.data["decision"] != "observe"
            or d["observe_s"] != initial.data["observe_s"]
        ):
            raise RuleRejected("Observation requires a preceding paid watch commitment")
        if (d["trial_id"], d["policy_id"]) != (
            initial.data["trial_id"],
            initial.data["policy_id"],
        ):
            raise RuleRejected("Observation belongs to another trial or policy")
        t = get(ledger, rec.campaign_id, initial.data["trial_id"], "trial")
        if any(x > t.data["t_heater_off"] + d["observe_s"] for x in d["trace"]["t"]):
            raise RuleRejected("Observation exceeds paid window")
        if any(
            o.data["decision_id"] == initial.id
            for o in records(ledger, rec.campaign_id, "observation")
        ):
            raise RuleRejected("Observation already released")
        if any(
            r.data["trial_id"] == t.id
            for r in records(ledger, rec.campaign_id, "reveal")
        ):
            raise RuleRejected("Outcome already revealed")
    if rec.type == "reveal":
        t = get(ledger, rec.campaign_id, rec.data["trial_id"], "trial")
        decisions = [
            r
            for r in records(ledger, rec.campaign_id, "decision")
            if r.data["trial_id"] == t.id
        ]
        if not decisions:
            raise RuleRejected("Must commit a decision before revealing an outcome")
        if any(
            d.data["decision"] == "observe"
            and not any(f.data.get("parent_decision_id") == d.id for f in decisions)
            for d in decisions
        ):
            raise RuleRejected(
                "Commit the terminal watch decision before revealing the outcome"
            )
        if any(
            r.data["trial_id"] == t.id
            for r in records(ledger, rec.campaign_id, "reveal")
        ):
            raise RuleRejected("Outcome already revealed")
        if not matches_seal(t.data, rec.data):
            raise AdmissionError("Reveal does not match seal")
        if (
            rec.data["outcome"] != "rescued"
            and rec.data.get("t_ignition") is None
            and any(d.data["decision"] == "observe" for d in decisions)
        ):
            raise AdmissionError(
                "Ignited trial lacks an ignition time; watch safety is unknown"
            )


def commit_decision(
    ledger,
    cid,
    trial_id,
    policy_id,
    decision,
    p_ignite,
    interval,
    *,
    scored=True,
    split="loco",
    fit_id=None,
    simulated=True,
    observe_s=0,
    parent_decision_id=None,
    fold_id=None,
    model_receipt_id=None,
):
    return record(
        ledger,
        cid,
        "decision",
        dict(
            trial_id=trial_id,
            policy_id=policy_id,
            decision=decision,
            p_ignite=p_ignite,
            interval=interval,
            scored=scored,
            split=split,
            fit_id=fit_id,
            simulated=simulated,
            observe_s=observe_s,
            parent_decision_id=parent_decision_id,
            fold_id=fold_id,
            action_outcome="ignition after recorded heater-off; escalation efficacy unobserved",
            **({"model_receipt_id": model_receipt_id} if model_receipt_id else {}),
        ),
    )


def reveal(ledger, cid, trial_id, sealed_store):
    get(ledger, cid, trial_id, "trial")
    path = Path(sealed_store) / (
        hashlib.sha256(trial_id.encode()).hexdigest() + ".json"
    )
    payload = json.loads(path.read_text())
    return record(ledger, cid, "reveal", dict(trial_id=trial_id, **payload))


def problems(ledger, cid=None):
    """Inert on old campaigns. Check temporal order and salted commitments independently of admission."""
    out = []
    cids = sorted(
        {
            r.campaign_id
            for r in ledger.list("intervention")
            if cid is None or r.campaign_id == cid
        }
    )
    for c in cids:
        rows = records(ledger, c)
        previous = ""
        events = {e.seq: e for e in ledger.events(c)}
        protocols = [r for r in rows if r.type == "protocol"]
        if len(protocols) != 1 or rows[0].type != "protocol":
            out.append(f"{c}: protocol must precede intervention work")
        for r in rows:
            if r.previous_hash != previous or r.digest != _digest(r):
                out.append(f"{r.id}: intervention hash chain changed")
            previous = r.digest
            e = events.get(r.seq)
            if (
                e is None
                or e.type != "intervention.recorded"
                or e.refs != [r.id]
                or e.data.get("record") != r.model_dump(mode="json")
            ):
                out.append(
                    f"{r.id}: intervention record differs from its sequenced event"
                )
        trials = {r.id: r for r in rows if r.type == "trial"}
        reveals = {}
        decisions = [r for r in rows if r.type == "decision"]
        for r in (r for r in rows if r.type == "reveal"):
            tid = r.data["trial_id"]
            t = trials.get(tid)
            if tid in reveals:
                out.append(f"{tid}: duplicate reveal")
            reveals[tid] = r
            if t is None or not matches_seal(t.data, r.data):
                out.append(f"{tid}: revealed outcome does not match seal")
            if t is not None:
                from translucent import systems

                nist = systems.get("nist-heat-interrupt")
                outcome_evidence_problems = nist.outcome_evidence_problems

                out.extend(outcome_evidence_problems(ledger, t, r, rows))
            ds = [d for d in decisions if d.data["trial_id"] == tid]
            if not ds or any(d.seq >= r.seq for d in ds):
                out.append(f"{tid}: decision must be committed before reveal")
        for d in decisions:
            if d.data["trial_id"] not in trials:
                out.append(f"{d.id}: trial missing")
            if d.data["scored"] and d.data["trial_id"] not in reveals:
                out.append(f"{d.id}: scored action has no recorded outcome")
            if d.data["decision"] == "observe" and not any(
                f.data.get("parent_decision_id") == d.id for f in decisions
            ):
                out.append(f"{d.id}: watch action lacks a terminal decision")
        for ref in (
            r
            for r in rows
            if r.type == "reference" and r.data.get("kind") == "visual_corroboration"
        ):
            if any(
                t.seq >= ref.seq or t.id not in reveals or reveals[t.id].seq >= ref.seq
                for t in trials.values()
            ):
                out.append(
                    f"{ref.id}: bulk visual labels were exposed before all campaign outcomes were revealed"
                )
            try:
                import csv
                import io
                from translucent import systems

                nist = systems.get("nist-heat-interrupt")
                corroboration_rows = nist.corroboration_rows

                files = {f["path"]: f["artifact_id"] for f in ref.data["files"]}
                source = ledger.blob_path(files["out/task1_labels.csv"]).read_text()
                expected = corroboration_rows(
                    csv.DictReader(io.StringIO(source)),
                    {t.data["exp"] for t in trials.values()},
                )
                for tid, r in reveals.items():
                    t = trials.get(tid)
                    if t is None:
                        continue
                    if r.data.get("corroboration") != expected.get(t.data["exp"]):
                        out.append(
                            f"{r.id}: corroboration differs from recorded visual reading"
                        )
                    if expected[t.data["exp"]]["vector"] != r.data["outcome"]:
                        out.append(
                            f"{r.id}: outcome differs from corroborating vector label"
                        )
                    for source in r.data.get("corroboration_sources", []):
                        aid = files[source["path"].removeprefix("visual/")]
                        if aid != "sha256:" + source["sha256"]:
                            out.append(f"{r.id}: visual source hash differs")
            except (KeyError, ValueError, OSError) as exc:
                out.append(f"{ref.id}: visual source audit failed: {exc}")
        from translucent.intervention_bench import verify_arithmetic

        out.extend(verify_arithmetic(ledger, c))
    return out


def pre_off_trace(ledger, cid, trial_id):
    cache = getattr(ledger, "_intervention_verification_cache", None)
    key = (cid, trial_id)
    if cache is not None and key in cache.traces:
        return cache.traces[key]
    t = get(ledger, cid, trial_id, "trial")
    series = (
        cache.blob_json(t.data["series_artifact"])
        if cache is not None
        else json.loads(ledger.blob_path(t.data["series_artifact"]).read_text())
    )
    pairs = [
        (x, y)
        for x, y in zip(series["t"], series["temperature_C"])
        if x <= t.data["t_heater_off"]
    ]
    trace = dict(t=[x for x, y in pairs], temperature_C=[y for x, y in pairs])
    if cache is not None:
        cache.traces[key] = trace
    return trace


def example(ledger, cid, trial_id, *, training=False, post_s=0, physics=False):
    from translucent.intervention_models import compute_features

    cache = getattr(ledger, "_intervention_verification_cache", None)
    key = (cid, trial_id, training, post_s, physics)
    if cache is not None and key in cache.examples:
        return cache.examples[key]
    trial = get(ledger, cid, trial_id, "trial")
    d = trial.data
    trace = pre_off_trace(ledger, cid, trial_id)
    row = dict(
        id=trial.id,
        condition=d["condition"],
        trace=trace,
        features=compute_features(
            trace["t"],
            trace["temperature_C"],
            d["t_heater_off"],
            {**d["condition"], **{k: d[k] for k in ("delay_after_vent_s", "t_vent")}},
        ),
    )
    if physics:
        from translucent.intervention_physics import cached_forecast

        row["features"]["physics_prior"] = cached_forecast(trace, d["condition"])[
            "actions"
        ]["stop_now"]["p_ignite"]
    if training:
        revealed = next(
            (
                r
                for r in records(ledger, cid, "reveal")
                if r.data["trial_id"] == trial_id
            ),
            None,
        )
        if revealed is None:
            raise ValueError("Training outcome has not been revealed")
        row["y"] = int(revealed.data["outcome"] != "rescued")
        row["reveal_id"] = revealed.id
        if post_s:
            from translucent.intervention_actions import post_features

            full = (
                cache.blob_json(d["series_artifact"])
                if cache is not None
                else json.loads(ledger.blob_path(d["series_artifact"]).read_text())
            )
            ti = revealed.data["t_ignition"]
            if row["y"] and ti is None:
                raise ValueError(
                    "Ignited trial lacks an ignition time; watch safety is unknown"
                )
            row["post_features_by_window"] = {}
            row["watch_early_by_window"] = {}
            row["watch_time_by_window"] = {}
            for w in (30, 60):
                if w > post_s:
                    continue
                row["post_features_by_window"][str(w)] = post_features(
                    full, d["t_heater_off"], w
                )
                row["watch_early_by_window"][str(w)] = (
                    ti is not None and ti <= d["t_heater_off"] + w
                )
                row["watch_time_by_window"][str(w)] = (
                    min(w, max(0.0, ti - d["t_heater_off"]))
                    if row["watch_early_by_window"][str(w)]
                    else w
                )
            row["post_features"] = row["post_features_by_window"][str(post_s)]
            row["watch_early"] = row["watch_early_by_window"][str(post_s)]
    if cache is not None:
        cache.examples[key] = row
    return row


def propose_policy(ledger, cid, name, spec, *, kind="agent", decision_rule=None):
    from translucent.intervention_models import PolicySpec

    spec = PolicySpec.model_validate(spec)
    if kind not in ("agent", "baseline"):
        raise ValueError("Unknown policy kind")
    if kind == "agent" and spec.family == "muse":
        raise ValueError(
            "The Muse-only family is reserved for a frozen baseline comparator"
        )
    from translucent.intervention_actions import DecisionRule

    rule = DecisionRule.model_validate(
        decision_rule or {"mode": "cost" if kind == "agent" else "classification"}
    )
    return record(
        ledger,
        cid,
        "policy",
        dict(
            name=name,
            kind=kind,
            family=spec.family,
            features=spec.features,
            spec=spec.model_dump(),
            world_model=spec.model_dump(),
            decision_rule=rule.model_dump(),
            simulated=True,
            forecast_origin="physics"
            if spec.family in ("thermal", "two_node")
            else "model",
        ),
    )


def register_fold(ledger, cid, scope, split, train_ids, test_ids):
    if split not in protocol(ledger, cid).data["splits"]:
        raise ValueError("Split not registered")
    return record(
        ledger,
        cid,
        "fold",
        dict(scope=scope, split=split, train_ids=train_ids, test_ids=test_ids),
    )


def fit_policy(ledger, cid, policy_id, fold_id):
    from translucent.intervention_models import fit

    p = get(ledger, cid, policy_id, "policy")
    fold = get(ledger, cid, fold_id, "fold")
    spec = p.data["spec"]
    needs_physics = "physics_prior" in str(spec)
    train = [
        example(
            ledger,
            cid,
            i,
            training=True,
            post_s=spec.get("observation_s", 0),
            physics=needs_physics,
        )
        for i in fold.data["train_ids"]
    ]
    config = protocol(ledger, cid).data
    model = fit(p.data["spec"], train, costs=config["costs"], seed=config["seed"])
    return record(
        ledger,
        cid,
        "fit",
        dict(
            policy_id=policy_id,
            fold_id=fold_id,
            train_ids=fold.data["train_ids"],
            reveal_ids=[r["reveal_id"] for r in train],
            model=model,
            bootstrap_generator="PCG64",
            bootstrap_seed=config["seed"],
            uncertainty="95% condition-block bootstrap of training data conditional on selected spec",
        ),
    )


# Activating this campaign type registers its gate extension. Existing checker
# source bytes and legacy claim handling remain unchanged for old receipts.
from translucent.intervention_gate import install as _install_gate_extension  # noqa: E402

_install_gate_extension()
