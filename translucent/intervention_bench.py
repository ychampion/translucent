"""Factual intervention benchmark and deterministic performance claim arithmetic."""

from __future__ import annotations

from datetime import datetime, timezone
from copy import deepcopy
import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from translucent.intervention import (
    canonical,
    create,
    example,
    fit_policy,
    get,
    protocol,
    propose_policy,
    record,
    records,
    register_fold,
    commit_decision,
    reveal,
)


class IncompletePairing(ValueError):
    """Two policies do not yet have the same complete held-out trial pairs."""


class ClaimRuleViolation(ValueError):
    """A proposed scientific claim does not satisfy its registered rules."""


def submit_policy(L, cid, policy_id):
    """Submit a screened candidate or record a frozen comparator's registration."""
    policy = get(L, cid, policy_id, "policy")
    if policy.data["kind"] == "baseline" and protocol(L, cid).data.get(
        "require_baseline_screens"
    ):
        screens = [
            s
            for s in records(L, cid, "screen")
            if s.data["policy_id"] == policy_id and s.data["survived"]
        ]
        if not screens:
            folds = [
                f
                for f in records(L, cid, "fold")
                if f.data["scope"] == "baseline_registration"
            ]
            fold = (
                folds[0]
                if folds
                else register_fold(
                    L,
                    cid,
                    "baseline_registration",
                    protocol(L, cid).data["splits"][0],
                    [],
                    [],
                )
            )
            fitted = fit_policy(L, cid, policy_id, fold.id)
            screen_policy(L, cid, policy_id, fold.id, fitted)
    return record(L, cid, "submission", dict(policy_id=policy_id))


def screen_policy(L, cid, policy_id, fold_id, fitted):
    """Record a reproducible training-only screen before held-out submission."""
    from translucent.intervention_fit_verification import screen_data

    policy = get(L, cid, policy_id, "policy")
    fold = get(L, cid, fold_id, "fold")
    data = screen_data(
        L, cid, policy, fold, fitted.data["model"], protocol(L, cid).data
    )
    return record(L, cid, "screen", data)


def wilson(k, n):
    if not n:
        return [0.0, 1.0]
    z = 1.959963984540054
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, center - half), min(1.0, center + half)]


def _subset(t, revealed, subset):
    flags = set(t.data["flags"]) | set(revealed.data["flags"])
    if subset == "all":
        return True
    if subset == "without_inferred":
        return "heater_off_inferred" not in flags
    if subset == "without_censored":
        return "censored" not in flags
    if subset.startswith("condition:"):
        c = t.data["condition"]
        return subset == f"condition:{c['format']}:{c['soc']}"
    raise ValueError("Subset is not registered")


def observations(L, cid, policy_id, split, subset="all", before_seq=None):
    cache = getattr(L, "_intervention_verification_cache", None)
    key = (cid, policy_id, split, subset, before_seq)
    if cache is not None and key in cache.observations:
        return deepcopy(cache.observations[key])
    result = _observations(L, cid, policy_id, split, subset, before_seq)
    if cache is not None:
        cache.observations[key] = deepcopy(result)
    return result


def _campaign_stop(L, cid, before_seq=None):
    """Find a recorded stop before the score's own ledger position."""
    stops = [
        r
        for r in records(L, cid, "planner")
        if r.data.get("phase") in ("stop", "stopped")
        and (before_seq is None or r.seq < before_seq)
    ]
    return stops[-1] if stops and stops[-1].data.get("status") != "finished" else None


def _observations(L, cid, policy_id, split, subset="all", before_seq=None):
    if split not in protocol(L, cid).data["splits"]:
        raise ValueError("Split is not registered")
    get(L, cid, policy_id, "policy")
    cache = getattr(L, "_intervention_verification_cache", None)
    if cache is not None:
        rs = cache.select(cid, ("trial", "decision", "reveal"))
    else:
        rows = L.db.execute(
            "SELECT body FROM record WHERE kind='intervention' AND campaign_id=? AND json_extract(body,'$.type') IN ('trial','decision','reveal') ORDER BY rowid",
            (cid,),
        )
        from translucent.intervention_records import InterventionRecord

        rs = [InterventionRecord.model_validate_json(body) for (body,) in rows]
    if before_seq is not None:
        rs = [r for r in rs if r.seq < before_seq]
    trials = {r.id: r for r in rs if r.type == "trial"}
    revealed = {r.data["trial_id"]: r for r in rs if r.type == "reveal"}
    out = []
    partial = _campaign_stop(L, cid, before_seq) is not None
    seen = set()
    selected = [
        r
        for r in rs
        if r.type == "decision"
        and r.data["scored"]
        and r.data["policy_id"] == policy_id
        and r.data["split"] == split
    ]
    finals = {
        r.data["parent_decision_id"]: r
        for r in selected
        if r.data.get("parent_decision_id")
    }
    for d in (r for r in selected if not r.data.get("parent_decision_id")):
        t = trials.get(d.data["trial_id"])
        r = revealed.get(d.data["trial_id"])
        if t is None or r is None:
            if t is not None and r is None and partial:
                continue
            raise ValueError("Scored action has no recorded outcome")
        if d.seq >= r.seq:
            raise ValueError("Decision must be committed before outcome reveal")
        if r.data.get("simulated"):
            raise ValueError("A simulated outcome cannot be evidence")
        if not _subset(t, r, subset):
            continue
        key = t.data["trial_key"]
        if key in seen:
            raise ValueError("Repeated held-out trial within one policy/split")
        seen.add(key)
        terminal = finals.get(d.id, d)
        watch = d.data.get("observe_s", 0) if d.data["decision"] == "observe" else 0
        if d.data["decision"] == "observe" and terminal.id == d.id:
            if partial:
                continue
            raise ValueError("Paid observation lacks a terminal decision")
        ti = r.data.get("t_ignition")
        if watch and r.data["outcome"] != "rescued" and ti is None:
            raise ValueError(
                f"Ignited trial {key} has a paid watch but no recorded ignition time"
            )
        early = bool(watch and ti is not None and ti <= t.data["t_heater_off"] + watch)
        spent = min(watch, max(0.0, ti - t.data["t_heater_off"])) if early else watch
        y = int(r.data["outcome"] != "rescued")
        action = terminal.data["decision"]
        out.append(
            dict(
                key=key,
                condition=t.data["condition"],
                y=y,
                p=d.data["p_ignite"] if early else terminal.data["p_ignite"],
                action=action,
                unsafe=int(early or (action == "stand_down" and y == 1)),
                missed=int(action in ("escalate", "abstain") and y == 0),
                correct=bool(
                    not early
                    and (
                        (action in ("escalate", "abstain") and y == 1)
                        or (action == "stand_down" and y == 0)
                    )
                ),
                abstain=int(action == "abstain"),
                decision_id=d.id,
                final_decision_id=terminal.id,
                reveal_id=r.id,
                observe_s=watch,
                watch_time_s=spent,
                ignited_during_watch=early,
                committed_seq=d.seq,
                final_committed_seq=terminal.seq,
                revealed_seq=r.seq,
            )
        )
    return out


def decision_cost(row, costs):
    """Recorded-action loss. Abstention is a charged escalation, never charged twice."""
    escalation = (
        costs["abstain"]
        if row["abstain"]
        else costs["unnecessary_escalation"]
        if row["missed"]
        else 0.0
    )
    unsafe_cost = (
        costs.get("watch_ignition", costs["unsafe"])
        if row.get("ignited_during_watch")
        else costs["unsafe"]
    )
    return float(
        row["unsafe"] * unsafe_cost
        + row["watch_time_s"] * costs["watch_per_second"]
        + escalation
    )


def score(L, cid, policy_id, split, subset="all", before_seq=None):
    cache = getattr(L, "_intervention_verification_cache", None)
    key = (cid, policy_id, split, subset, before_seq)
    if cache is not None and key in cache.scores:
        return deepcopy(cache.scores[key])
    result = _score(L, cid, policy_id, split, subset, before_seq)
    stop = _campaign_stop(L, cid, before_seq)
    if stop is not None:
        completed = set(result["decision_ids"])
        pending = [
            r.id
            for r in records(L, cid, "decision")
            if r.data["scored"]
            and r.data["policy_id"] == policy_id
            and r.data["split"] == split
            and not r.data.get("parent_decision_id")
            and r.id not in completed
            and (before_seq is None or r.seq < before_seq)
        ]
        result.update(partial=True, campaign_status=stop.data["status"])
        if pending:
            result["pending_decision_ids"] = pending
    if cache is not None:
        cache.scores[key] = deepcopy(result)
    return result


def _score(L, cid, policy_id, split, subset="all", before_seq=None):
    rows = observations(L, cid, policy_id, split, subset, before_seq)
    config = protocol(L, cid).data
    return summarize_rows(rows, policy_id, split, subset, config)


def summarize_rows(rows, policy_id, split, subset, config):
    """Summarize factual paired rows with the registered loss and intervals."""
    n = len(rows)
    amended = config["primary_metric"] == "mean_decision_cost"
    stable = config.get("numerical_version", 1) >= 2
    from translucent.intervention_numeric import mean, quantile

    def average(values):
        return mean(values) if stable else float(np.asarray(values).mean())

    def interval(values):
        return (
            [quantile(values, p) for p in (0.025, 0.975)]
            if stable
            else np.quantile(values, [0.025, 0.975]).tolist()
        )

    if not n:
        result = dict(
            policy_id=policy_id,
            split=split,
            subset=subset,
            n=0,
            unsafe=0,
            missed=0,
            abstain=0,
            brier=None,
            logloss=None,
            accuracy=None,
            accuracy_interval=[0.0, 1.0],
            status="not_run",
            reason="No recorded predictions",
            decision_ids=[],
            reveal_ids=[],
        )
        if amended:
            result.update(
                mean_cost=None,
                mean_cost_interval=None,
                total_cost=0.0,
                watch_time_s=0.0,
                watch_requested_s=0.0,
                per_trial_costs=[],
            )
        return result
    p = np.array([r["p"] for r in rows])
    y = np.array([r["y"] for r in rows])
    clip = config["probability_clip"]
    pc = np.clip(p, clip, 1 - clip)
    bs = (p - y) ** 2
    ll = -(y * np.log(pc) + (1 - y) * np.log(1 - pc))
    if stable:
        bs = [(r["p"] - r["y"]) ** 2 for r in rows]
        ll = [
            -math.log(min(1 - clip, max(clip, r["p"])))
            if r["y"]
            else -math.log(1 - min(1 - clip, max(clip, r["p"])))
            for r in rows
        ]
    unsafe = sum(r["unsafe"] for r in rows)
    missed = sum(r["missed"] for r in rows)
    abstain = sum(r["abstain"] for r in rows)
    correct = sum(r["correct"] for r in rows)
    watch = (math.fsum if stable else sum)(r["watch_time_s"] for r in rows)
    costs = config["costs"]
    actual_costs = np.array([decision_cost(r, costs) for r in rows])
    rng = (
        np.random.Generator(np.random.PCG64(config["seed"]))
        if stable
        else np.random.default_rng(0)
    )
    groups = {}
    for i, r in enumerate(rows):
        groups.setdefault(tuple(sorted(r["condition"].items())), []).append(i)
    blocks = list(groups.values())
    bs_samples = []
    ll_samples = []
    cost_samples = []
    for _ in range(256):
        ids = [
            i
            for k in rng.integers(0, len(blocks), len(blocks), dtype=np.int64)
            for i in blocks[int(k)]
        ]
        bs_samples.append(average([bs[i] for i in ids]))
        ll_samples.append(average([ll[i] for i in ids]))
        cost_samples.append(average(actual_costs[ids]))
    result = dict(
        policy_id=policy_id,
        split=split,
        subset=subset,
        n=n,
        unsafe=unsafe,
        missed=missed,
        abstain=abstain,
        brier=average(bs),
        logloss=average(ll),
        accuracy=correct / n,
        accuracy_interval=wilson(correct, n),
        unsafe_interval=wilson(unsafe, n),
        missed_interval=wilson(missed, n),
        abstain_interval=wilson(abstain, n),
        brier_interval=interval(bs_samples),
        logloss_interval=interval(ll_samples),
        abstention_cost=abstain * config["abstention_cost"],
        watch_time_s=watch,
        watch_requested_s=sum(r["observe_s"] for r in rows),
        ignited_during_watch=sum(r["ignited_during_watch"] for r in rows),
        total_cost=(math.fsum(actual_costs) if stable else float(actual_costs.sum()))
        if amended
        else unsafe * costs["unsafe"]
        + missed * costs["unnecessary_escalation"]
        + watch * costs["watch_per_second"]
        + abstain * costs["abstain"],
        decision_ids=[r["decision_id"] for r in rows],
        reveal_ids=[r["reveal_id"] for r in rows],
        status="scored",
        interval_method="Wilson 95% for binary proportions; condition-block bootstrap for losses",
    )
    if amended:
        result.update(
            mean_cost=average(actual_costs),
            mean_cost_interval=interval(cost_samples),
            per_trial_costs=[
                dict(
                    trial_key=r["key"],
                    cost=float(c),
                    decision_id=r["decision_id"],
                    reveal_id=r["reveal_id"],
                )
                for r, c in zip(rows, actual_costs)
            ],
        )
    return result


def exact_mcnemar(a_only, b_only):
    n = a_only + b_only
    return (
        min(
            1.0, 2 * sum(math.comb(n, k) for k in range(min(a_only, b_only) + 1)) / 2**n
        )
        if n
        else 1.0
    )


def holm(pvalues, m):
    if len(pvalues) > m:
        raise ValueError("More comparisons than ledger submissions")
    ordered = sorted(pvalues, key=lambda k: (pvalues[k], k))
    adjusted = {}
    running = 0.0
    for rank, k in enumerate(ordered):
        running = max(running, (m - rank) * pvalues[k])
        adjusted[k] = min(1.0, running)
    return adjusted


def _pair(a, b):
    aa = {r["key"]: r for r in a}
    bb = {r["key"]: r for r in b}
    if not aa or aa.keys() != bb.keys():
        raise IncompletePairing(
            "Comparison requires the same complete paired held-out trials"
        )
    ac = sum(aa[k]["unsafe"] == 0 and bb[k]["unsafe"] == 1 for k in aa)
    bc = sum(aa[k]["unsafe"] == 1 and bb[k]["unsafe"] == 0 for k in aa)
    return ac, bc, exact_mcnemar(ac, bc)


def _unsafe_comparison(L, cid, a, b, split, subset="all", before_seq=None):
    submitted = [
        r.data["policy_id"]
        for r in records(L, cid, "submission")
        if before_seq is None or r.seq < before_seq
    ]
    if a not in submitted or b not in submitted:
        raise ValueError("Both policies must be submitted")
    target = observations(L, cid, b, split, subset, before_seq)
    pvalues = {}
    pairs = {}
    for policy_id in submitted:
        try:
            pairs[policy_id] = _pair(
                observations(L, cid, policy_id, split, subset, before_seq), target
            )
        except IncompletePairing:
            pairs[policy_id] = (0, 0, 1.0)
        pvalues[policy_id] = pairs[policy_id][2]
    ac, bc, p = _pair(observations(L, cid, a, split, subset, before_seq), target)
    return dict(
        a=a,
        b=b,
        split=split,
        subset=subset,
        n=len(target),
        a_only_correct=ac,
        b_only_correct=bc,
        p=p,
        p_holm=holm(pvalues, len(submitted))[a],
        candidates_submitted=len(submitted),
        metric="unsafe",
        correction_family="all submitted policies against this fixed baseline, including not-run candidates",
    )


@lru_cache(maxsize=2048)
def _signflip_cached(magnitudes, observed, seed, resamples):
    values = np.asarray(magnitudes, dtype=float)
    n = len(values)
    if not n:
        return (1.0, "exact", 1)
    threshold = max(0.0, observed - 1e-9)
    exceed = 0
    if n <= 20:
        total = 1 << n
        # Bounded batches avoid constructing the complete 2**20 by 20 matrix.
        for first in range(0, total, 8192):
            numbers = np.arange(first, min(total, first + 8192), dtype=np.uint64)
            signs = (
                2
                * ((numbers[:, None] >> np.arange(n, dtype=np.uint64)) & 1).astype(
                    float
                )
                - 1
            )
            exceed += int(
                np.count_nonzero(np.abs(np.sum(signs * values, axis=1)) >= threshold)
            )
        return (exceed / total, "exact", total)
    rng = np.random.default_rng(seed)
    for first in range(0, resamples, 4096):
        size = min(4096, resamples - first)
        signs = rng.integers(0, 2, size=(size, n), dtype=np.int8) * 2 - 1
        exceed += int(
            np.count_nonzero(np.abs(np.sum(signs * values, axis=1)) >= threshold)
        )
    # Include the observed assignment: prevents an unsupported zero Monte Carlo p.
    return ((exceed + 1) / (resamples + 1), "seeded Monte Carlo", resamples)


def paired_signflip(differences, *, seed=0, resamples=100000):
    """Two-sided paired mean-cost randomization; exact through 20 discordant trials.

    The signed sum and mean have the same permutation ordering. Zero differences
    carry no randomization information. Above 20, the registered seeded 100,000
    assignments estimate the same sign-flip test with a plus-one correction.
    """
    if type(seed) is not int or type(resamples) is not int or resamples < 1:
        raise ValueError("Invalid registered randomization parameters")
    differences = [float(v) for v in differences]
    if any(not math.isfinite(v) for v in differences):
        raise ValueError("Costs must be finite")
    nz = [v for v in differences if abs(v) > 1e-10]
    magnitudes = tuple(sorted(round(abs(v), 10) for v in nz))
    observed = round(abs(sum(nz)), 10)
    p, method, draws = _signflip_cached(magnitudes, observed, seed, resamples)
    return dict(
        p=p,
        discordant=len(nz),
        method=method,
        assignments=draws,
        seed=seed,
        statistic="absolute paired sum of cost differences",
        monte_carlo_se=math.sqrt(p * (1 - p) / draws) if method != "exact" else 0.0,
    )


def _cost_pair(a, b, costs, seed, resamples, *, inferential=True):
    aa = {r["key"]: r for r in a}
    bb = {r["key"]: r for r in b}
    if not aa or aa.keys() != bb.keys():
        raise IncompletePairing(
            "Comparison requires the same complete paired held-out trials"
        )
    keys = sorted(aa)
    ca = [decision_cost(aa[k], costs) for k in keys]
    cb = [decision_cost(bb[k], costs) for k in keys]
    delta = [x - y for x, y in zip(ca, cb)]
    test = (
        paired_signflip(delta, seed=seed, resamples=resamples)
        if inferential
        else dict(
            p=None,
            discordant=sum(abs(d) > 1e-10 for d in delta),
            method="descriptive baseline comparison",
            assignments=0,
            seed=seed,
            statistic="paired mean cost difference; no inference",
            monte_carlo_se=None,
        )
    )
    ac, bc, unsafe_p = _pair(a, b)
    ma = sum(aa[k]["missed"] == 0 and bb[k]["missed"] == 1 for k in keys)
    mb = sum(aa[k]["missed"] == 1 and bb[k]["missed"] == 0 for k in keys)
    return dict(
        n=len(keys),
        a_mean_cost=sum(ca) / len(keys),
        b_mean_cost=sum(cb) / len(keys),
        mean_cost_difference=sum(delta) / len(keys),
        per_trial_differences=[
            dict(trial_key=k, difference=d) for k, d in zip(keys, delta)
        ],
        a_only_correct=ac,
        b_only_correct=bc,
        unsafe_mcnemar_p=unsafe_p,
        a_unsafe=sum(r["unsafe"] for r in a),
        b_unsafe=sum(r["unsafe"] for r in b),
        a_missed=sum(r["missed"] for r in a),
        b_missed=sum(r["missed"] for r in b),
        missed_a_only_correct=ma,
        missed_b_only_correct=mb,
        missed_p=exact_mcnemar(ma, mb),
        **test,
    )


_COMPARISON_CACHE = {}


# New policy-comparison claims use registered mean decision cost. The unsafe-only
# exact McNemar statistic remains descriptive and cannot establish this claim.
def comparison(L, cid, a, b, split, subset="all", before_seq=None):
    """Primary policy_beats endpoint: paired mean-cost sign flips with full-family Holm.

    Unsafe McNemar remains descriptive. The secondary two-part endpoint combines
    observed unsafe-count nonincrease with corrected exact missed-rescue McNemar.
    """
    config = protocol(L, cid).data
    if config["primary_metric"] != "mean_decision_cost":
        return _unsafe_comparison(L, cid, a, b, split, subset, before_seq)
    submitted = [
        r.data["policy_id"]
        for r in records(L, cid, "submission")
        if before_seq is None or r.seq < before_seq
    ]
    if a not in submitted or b not in submitted:
        raise ValueError("Both policies must be submitted")
    baselines = [
        i for i in submitted if get(L, cid, i, "policy").data["kind"] == "baseline"
    ]
    if b not in baselines:
        raise ValueError("Primary comparison requires a registered baseline")
    amended = config.get("amendment", 0) >= 3
    candidates = (
        [i for i in submitted if get(L, cid, i, "policy").data["kind"] == "agent"]
        if amended
        else submitted
    )
    m = len(candidates) * len(baselines)
    if amended and a not in candidates:
        pair = _cost_pair(
            observations(L, cid, a, split, subset, before_seq),
            observations(L, cid, b, split, subset, before_seq),
            config["costs"],
            config["seed"],
            config["primary_test"]["resamples"],
            inferential=False,
        )
        pair.update(
            p=None,
            method="descriptive baseline comparison",
            assignments=0,
            monte_carlo_se=None,
        )
        return dict(
            a=a,
            b=b,
            split=split,
            subset=subset,
            **pair,
            p_holm=None,
            missed_p_holm=None,
            secondary_two_part=False,
            candidates_submitted=len(candidates),
            baselines_submitted=len(baselines),
            family_size=m,
            metric="mean_decision_cost",
            inferential=False,
            correction_family="screened agent candidates times registered baseline comparators",
            reason="Frozen baselines are descriptive comparators, not submitted candidate strategies",
            secondary_limit="Observed unsafe-count nonincrease is descriptive, not a noninferiority test",
        )
    boundary = L.db.execute(
        "SELECT COALESCE(MAX(rowid),0) FROM record WHERE kind='intervention' AND campaign_id=? AND json_extract(body,'$.type') IN ('decision','reveal','submission') AND json_extract(body,'$.seq')<?",
        (cid, before_seq or 2**63 - 1),
    ).fetchone()[0]
    cache_key = (str(L.root.resolve()), cid, split, subset, boundary)
    cached = _COMPARISON_CACHE.get(cache_key)
    if cached is None:
        rows = {
            i: observations(L, cid, i, split, subset, before_seq) for i in submitted
        }
        pvalues = {}
        missed_values = {}
        pairs = {}
        permutation = config.get("primary_test", config.get("permutation", {}))
        seed = permutation.get("seed", config["seed"])
        resamples = permutation.get("resamples", 100000)
        for candidate in candidates:
            for baseline in baselines:
                key = (candidate, baseline)
                try:
                    pairs[key] = _cost_pair(
                        rows[candidate],
                        rows[baseline],
                        config["costs"],
                        seed,
                        resamples,
                    )
                    pvalues[key] = pairs[key]["p"]
                    missed_values[key] = pairs[key]["missed_p"]
                except IncompletePairing:
                    pvalues[key] = 1.0
                    missed_values[key] = 1.0
        adjusted = holm(pvalues, m)
        secondary = holm(missed_values, m)
        cached = (pairs, adjusted, secondary)
        if len(_COMPARISON_CACHE) >= 128:
            _COMPARISON_CACHE.clear()
        _COMPARISON_CACHE[cache_key] = cached
    pairs, adjusted, secondary = cached
    key = (a, b)
    if key not in pairs:
        raise IncompletePairing(
            "Comparison requires the same complete paired held-out trials"
        )
    pair = pairs[key]
    return dict(
        a=a,
        b=b,
        split=split,
        subset=subset,
        **pair,
        p_holm=adjusted[key],
        missed_p_holm=secondary[key],
        secondary_two_part=pair["a_unsafe"] <= pair["b_unsafe"]
        and pair["a_missed"] < pair["b_missed"]
        and secondary[key] < config["alpha"],
        candidates_submitted=len(candidates),
        baselines_submitted=len(baselines),
        family_size=m,
        metric="mean_decision_cost",
        **({"inferential": True} if amended else {}),
        correction_family="screened agent candidates times registered baseline comparators, including not-run baseline stubs"
        if amended
        else "all ledger-submitted policies times all submitted baselines, including not-run submissions and self comparisons",
        secondary_limit="Observed unsafe count nonincrease is descriptive, not an equivalence or noninferiority test",
    )


def propose_claim(L, cid, predicate):
    return record(L, cid, "claim", dict(predicate=predicate))


def require_complete_coverage(L, cid, policy_id, split, subset, before_seq):
    """A gate claim covers the registered physical-trial universe, without skips.

    Scores remain available while a campaign runs. A claim cannot substitute a
    favourable completed subset for the full trial set sealed in its split scopes.
    Outcome-dependent exclusions remain included until an actual reveal proves
    their registered exclusion flag.
    """
    folds = [
        f
        for f in records(L, cid, "fold")
        if f.seq < before_seq and f.data["split"] == split
    ]
    scopes = {f.data["scope"] for f in folds}
    if not scopes:
        raise ClaimRuleViolation("No registered fold scopes for this split")
    trials = [
        t
        for t in records(L, cid, "trial")
        if t.seq < before_seq and t.data["scope"] in scopes
    ]
    revealed = {
        r.data["trial_id"]: r for r in records(L, cid, "reveal") if r.seq < before_seq
    }
    grouped = {}
    for trial in trials:
        key = trial.data["trial_key"]
        item = grouped.setdefault(
            key, dict(condition=trial.data["condition"], flags=set())
        )
        item["flags"].update(trial.data["flags"])
        if trial.id in revealed:
            item["flags"].update(revealed[trial.id].data["flags"])
    expected = set()
    for key, item in grouped.items():
        if subset == "all":
            include = True
        elif subset == "without_inferred":
            include = "heater_off_inferred" not in item["flags"]
        elif subset == "without_censored":
            include = "censored" not in item["flags"]
        elif subset.startswith("condition:"):
            c = item["condition"]
            include = subset == f"condition:{c['format']}:{c['soc']}"
        else:
            raise ClaimRuleViolation("Subset is not registered")
        if include:
            expected.add(key)
    actual = {
        r["key"] for r in observations(L, cid, policy_id, split, subset, before_seq)
    }
    if actual != expected:
        raise ClaimRuleViolation(
            f"Complete held-out coverage required: {len(actual)} of {len(expected)} registered physical trials; "
            f"{len(expected - actual)} missing, {len(actual - expected)} outside the registered subset"
        )


CLAIM_FIELDS = {
    "policy_performance": ("type", "policy_id", "split", "n", "unsafe"),
    "policy_beats": ("type", "a", "b", "split"),
    "policy_no_more_unsafe_fewer_missed": ("type", "a", "b", "split"),
}


def validate_predicate(predicate):
    """Validate a model's claim schema before any scientific recomputation."""
    if not isinstance(predicate, dict):
        raise ClaimRuleViolation(
            "Intervention claim needs an object predicate with field 'type'"
        )
    value = dict(predicate)
    kind = value.get("type")
    if not isinstance(kind, str) or kind not in CLAIM_FIELDS:
        raise ClaimRuleViolation(
            "Intervention claim needs a supported 'type' (policy_performance, "
            "policy_beats, policy_no_more_unsafe_fewer_missed)"
        )
    if kind != "policy_performance":
        value.setdefault("a", value.get("policy_id"))
        value.setdefault("b", value.get("baseline_id"))
    fields = CLAIM_FIELDS[kind]
    required = ", ".join(fields)
    for field in fields:
        if field not in value or value[field] is None:
            raise ClaimRuleViolation(
                f"{kind} claim is missing field '{field}' (required: {required})"
            )
        integer = field in ("n", "unsafe")
        if integer and (type(value[field]) is not int or value[field] < 0):
            raise ClaimRuleViolation(
                f"{kind} claim field '{field}' must be a nonnegative integer"
            )
        if not integer and (not isinstance(value[field], str) or not value[field]):
            raise ClaimRuleViolation(
                f"{kind} claim field '{field}' must be a nonempty string"
            )
    if "evidence_ids" in value and (
        not isinstance(value["evidence_ids"], list)
        or any(not isinstance(rid, str) for rid in value["evidence_ids"])
    ):
        raise ClaimRuleViolation(
            f"{kind} claim field 'evidence_ids' must be a list of record ids"
        )
    for field in ("subset", "action"):
        if field in value and not isinstance(value[field], str):
            raise ClaimRuleViolation(f"{kind} claim field '{field}' must be a string")
    return value


def evaluate_claim(L, claim):
    """Recompute performance and pairwise mean-cost superiority from real feedback.

    policy_beats uses the registered paired sign-flip cost test and the complete
    submitted-policy times baseline Holm family. Unsafe McNemar is descriptive.
    """
    cid = claim.campaign_id
    try:
        p = validate_predicate(claim.data.get("predicate"))
        if p.get("simulated"):
            raise ClaimRuleViolation("A simulated outcome cannot support a claim")
        if p.get("action", "recorded_heater_off") != "recorded_heater_off":
            raise ClaimRuleViolation("This action has no recorded outcome")
        for rid in p.get("evidence_ids", []):
            known = {r.id: r for r in records(L, cid)}
            if rid not in known:
                raise ClaimRuleViolation(f"Claim cites unknown evidence {rid!r}")
            e = known[rid]
            if e.type not in ("decision", "reveal") or (
                e.type == "reveal" and e.data.get("simulated")
            ):
                raise ClaimRuleViolation(
                    "Only committed decisions and real revealed outcomes can support a claim; simulated forecasts cannot"
                )
        config = protocol(L, cid).data
        split = p["split"]
        if split not in config["splits"]:
            raise ClaimRuleViolation("Split is not registered")
        submitted = {r.data["policy_id"] for r in records(L, cid, "submission")}
        ids = (
            [p["policy_id"]] if p["type"] == "policy_performance" else [p["a"], p["b"]]
        )
        if any(pid not in submitted for pid in ids):
            raise ClaimRuleViolation(
                "Claim policies must be registered held-out submissions"
            )
        if (
            p["type"] != "policy_performance"
            and get(L, cid, p["b"], "policy").data["kind"] != "baseline"
        ):
            raise ClaimRuleViolation(
                "Primary comparison requires a registered baseline"
            )
        subset = p.get("subset", "all")
        minimum = protocol(L, cid).data["min_trials"]
        if p["type"] == "policy_performance":
            require_complete_coverage(L, cid, p["policy_id"], split, subset, claim.seq)
            s = score(L, cid, p["policy_id"], split, subset, claim.seq)
            if s["n"] < minimum:
                raise ClaimRuleViolation(
                    f"Protocol minimum is {minimum} trials; found {s['n']}"
                )
            supported = (
                type(p["n"]) is int
                and type(p["unsafe"]) is int
                and p["n"] == s["n"]
                and p["unsafe"] == s["unsafe"]
            )
            return dict(
                verdict="supported" if supported else "refuted",
                reason=f"{s['unsafe']} unsafe stand-downs over {s['n']} held-out trials",
                computed=s,
                claim_id=claim.id,
            )
        if p["type"] == "policy_beats":
            for pid in (p["a"], p["b"]):
                require_complete_coverage(L, cid, pid, split, subset, claim.seq)
            c = comparison(L, cid, p["a"], p["b"], split, subset, claim.seq)
            if c.get("inferential") is False:
                raise ClaimRuleViolation(
                    "Frozen baselines are descriptive comparators; only screened agent candidates can claim superiority"
                )
            if c["n"] < minimum:
                raise ClaimRuleViolation(
                    f"Protocol minimum is {minimum} trials; found {c['n']}"
                )
            supported = (
                c["mean_cost_difference"] < 0
                if c["metric"] == "mean_decision_cost"
                else c["a_only_correct"] > c["b_only_correct"]
            ) and c["p_holm"] < protocol(L, cid).data["alpha"]
            reason = (
                f"Mean decision cost {c['a_mean_cost']:.6g} vs {c['b_mean_cost']:.6g}; paired sign-flip p={c['p']:.6g}, Holm p={c['p_holm']:.6g}, family {c['family_size']}"
                if c["metric"] == "mean_decision_cost"
                else f"Unsafe discordant pairs {c['a_only_correct']} vs {c['b_only_correct']}; exact p={c['p']:.6g}, Holm p={c['p_holm']:.6g}, {c['candidates_submitted']} submitted policies"
            )
            return dict(
                verdict="supported" if supported else "inconclusive",
                reason=reason,
                computed=c,
                claim_id=claim.id,
            )
        if p["type"] == "policy_no_more_unsafe_fewer_missed":
            for pid in (p["a"], p["b"]):
                require_complete_coverage(L, cid, pid, split, subset, claim.seq)
            c = comparison(L, cid, p["a"], p["b"], split, subset, claim.seq)
            if c["n"] < minimum:
                raise ClaimRuleViolation(
                    f"Protocol minimum is {minimum} trials; found {c['n']}"
                )
            if c.get("inferential") is False:
                raise ClaimRuleViolation(
                    "Frozen baselines are descriptive comparators; only screened agent candidates have a secondary inferential claim"
                )
            if c["metric"] != "mean_decision_cost":
                raise ClaimRuleViolation(
                    "Secondary two-part endpoint was not registered"
                )
            return dict(
                verdict="supported" if c["secondary_two_part"] else "inconclusive",
                reason=f"Observed unsafe counts {c['a_unsafe']} vs {c['b_unsafe']}; missed rescues {c['a_missed']} vs {c['b_missed']}; exact missed-rescue McNemar p={c['missed_p']:.6g}, Holm p={c['missed_p_holm']:.6g}. Count nonincrease is descriptive, not statistical noninferiority",
                computed=c,
                claim_id=claim.id,
            )
        raise ClaimRuleViolation("Unknown intervention claim kind")
    except (ClaimRuleViolation, IncompletePairing) as exc:
        return dict(
            verdict="unsupported", reason=str(exc), computed=None, claim_id=claim.id
        )


def review_claim(L, claim_id):
    claim = get(L, L.get("intervention", claim_id).campaign_id, claim_id, "claim")
    return record(L, claim.campaign_id, "review", evaluate_claim(L, claim))


def verify_arithmetic(L, cid):
    """Recompute policy provenance, executable fits, decisions and performance."""
    # Recompute a fresh comparison family for this verification, then share it among claims.
    for key in list(_COMPARISON_CACHE):
        if key[1] == cid:
            _COMPARISON_CACHE.pop(key)
    out = []
    rs = records(L, cid)
    byid = {r.id: r for r in rs}
    reveals = {r.data["trial_id"]: r for r in rs if r.type == "reveal"}
    submissions = [r for r in rs if r.type == "submission"]
    p = protocol(L, cid)
    from translucent.intervention_comparison import (
        check_action,
        check_history,
        check_probability,
        probability_tolerance,
    )
    from translucent.intervention_fit_verification import (
        check_refit,
        recompute_fit,
        screen_data,
    )

    try:
        probability_atol = probability_tolerance(p.data)
    except ValueError as exc:
        out.append(f"{p.id}: {exc}")
        probability_atol = 0.0
    amended = p.data.get("amendment", 0) >= 3
    candidates = (
        [r for r in submissions if byid[r.data["policy_id"]].data["kind"] == "agent"]
        if amended
        else submissions
    )
    if len(candidates) > p.data["heldout_cap_K"]:
        out.append(f"{cid}: held-out candidate submission cap exceeded")
    if (
        amended
        and any(split != "prequential" for split in p.data["splits"])
        and reveals
    ):
        first_reveal = min(r.seq for r in reveals.values())
        if any(
            r.type == "policy" and r.data["kind"] == "agent" and r.seq >= first_reveal
            for r in rs
        ):
            out.append(
                f"{cid}: non-prequential candidate spec registered after an outcome reveal"
            )
    if len({s.data["policy_id"] for s in submissions}) != len(submissions):
        out.append(f"{cid}: repeated submission")
    scored = [r for r in rs if r.type == "decision" and r.data["scored"]]
    if scored and any(s.seq >= min(d.seq for d in scored) for s in submissions):
        out.append(f"{cid}: submission after held-out scoring began")
    for r in rs:
        d = r.data
        try:
            if r.type == "submission":
                policy = byid[d["policy_id"]]
                if policy.data["kind"] == "agent" or p.data.get(
                    "require_baseline_screens"
                ):
                    if not any(
                        screen.type == "screen"
                        and screen.seq < r.seq
                        and screen.data["policy_id"] == policy.id
                        and screen.data["survived"] is True
                        for screen in rs
                    ):
                        raise ValueError(
                            "Policy submission has no preceding surviving screen"
                        )
            if (
                r.type == "policy"
                and d["kind"] == "agent"
                and d["spec"]["family"] == "muse"
            ):
                raise ValueError(
                    "The Muse-only family is reserved for a frozen baseline comparator"
                )
            if r.type == "fold":
                train = [byid[i] for i in d["train_ids"]]
                test = [byid[i] for i in d["test_ids"]]
                if r.seq <= p.seq or d["split"] not in p.data["splits"]:
                    raise ValueError("Unregistered split")
                if len(set(d["train_ids"] + d["test_ids"])) != len(train) + len(test):
                    raise ValueError("Repeated or overlapping fold trials")
                if any(
                    t.type != "trial" or t.seq >= r.seq or t.data["scope"] != d["scope"]
                    for t in train + test
                ):
                    raise ValueError("Invalid fold scope or registration order")
                if d["split"] == "loco" and {
                    tuple(sorted(t.data["condition"].items())) for t in train
                } & {tuple(sorted(t.data["condition"].items())) for t in test}:
                    raise ValueError("LOCO condition leakage")
                if d["split"] == "lofo" and {
                    t.data["condition"]["format"] for t in train
                } & {t.data["condition"]["format"] for t in test}:
                    raise ValueError("LOFO format leakage")
                if (
                    d["split"] == "prequential"
                    and train
                    and max(t.data["exp"] for t in train)
                    >= min(t.data["exp"] for t in test)
                ):
                    raise ValueError("Prequential Exp order leakage")
                protected = test if d["split"] == "prequential" else train + test
                if any(
                    t.id in reveals and reveals[t.id].seq < r.seq for t in protected
                ):
                    raise ValueError(
                        "Fold was registered after a training or held-out outcome reveal"
                    )
            if r.type == "fit":
                fold = byid[d["fold_id"]]
                policy = byid[d["policy_id"]]
                if (
                    fold.type != "fold"
                    or policy.type != "policy"
                    or fold.seq >= r.seq
                    or policy.seq >= r.seq
                ):
                    raise ValueError("Fit preceded its policy or fold registration")
                if d["train_ids"] != fold.data["train_ids"]:
                    raise ValueError("Fit training partition differs")
                if d["reveal_ids"] != [reveals[i].id for i in d["train_ids"]] or any(
                    reveals[i].seq >= r.seq for i in d["train_ids"]
                ):
                    raise ValueError("Fit used an outcome before it was revealed")
                if set(fold.data["train_ids"]) & set(fold.data["test_ids"]):
                    raise ValueError("Held-out training leakage")
                if (
                    d.get("bootstrap_generator") != "PCG64"
                    or d.get("bootstrap_seed") != p.data["seed"]
                ):
                    raise ValueError("Fit bootstrap provenance differs from protocol")
                fresh = recompute_fit(L, cid, policy, fold, p.data)
                check_refit(d["model"], fresh, config=p.data, atol=probability_atol)
            if r.type == "decision":
                policy = byid.get(d["policy_id"])
                if (
                    policy is not None
                    and policy.type == "policy"
                    and policy.data["spec"]["family"] == "muse"
                ) or d.get("model_receipt_id"):
                    from translucent.intervention_muse import validate_muse_decision

                    validate_muse_decision(L, cid, d, before_seq=r.seq)
                if d.get("parent_decision_id"):
                    initial = byid[d["parent_decision_id"]]
                    if initial.type != "decision" or initial.seq >= r.seq:
                        raise ValueError("Terminal decision has no preceding parent")
                    for field in (
                        "fit_id",
                        "fold_id",
                        "split",
                        "scored",
                        "trial_id",
                        "policy_id",
                    ):
                        if d.get(field) != initial.data.get(field):
                            raise ValueError(
                                f"Terminal decision changes its parent's {field}"
                            )
                    paid = [
                        observation
                        for observation in rs
                        if observation.type == "observation"
                        and observation.data["decision_id"] == initial.id
                    ]
                    if len(paid) != 1 or not initial.seq < paid[0].seq < r.seq:
                        raise ValueError(
                            "Terminal decision lacks a prior paid observation"
                        )
                    if d.get("observe_s") != initial.data["observe_s"]:
                        raise ValueError(
                            "Terminal decision changes its paid observation window"
                        )
            if r.type == "decision" and d["scored"]:
                f = byid[d["fit_id"]]
                fold = byid[d.get("fold_id") or f.data["fold_id"]]
                if (
                    f.seq >= r.seq
                    or f.data["policy_id"] != d["policy_id"]
                    or d["trial_id"] not in fold.data["test_ids"]
                    or not set(f.data["train_ids"]) <= set(fold.data["train_ids"])
                ):
                    raise ValueError(
                        "Decision has no preceding fit on its training fold"
                    )
                if d["split"] != fold.data["split"]:
                    raise ValueError("Decision split differs from its registered fold")
                if not any(
                    s.data["policy_id"] == d["policy_id"] and s.seq < r.seq
                    for s in submissions
                ):
                    raise ValueError("Decision preceded policy submission")
                if (
                    d["split"] == "prequential"
                    and byid[d["policy_id"]].data["spec"]["family"] != "muse"
                ):
                    updates = [
                        u
                        for u in rs
                        if u.type == "update"
                        and u.data["policy_id"] == d["policy_id"]
                        and u.seq < r.seq
                    ]
                    if updates and d["fit_id"] != updates[-1].data["applied_fit_id"]:
                        raise ValueError(
                            "Prequential decision did not use the accepted world model"
                        )
                    previous = [
                        x
                        for x in scored
                        if x.seq < r.seq
                        and x.data["policy_id"] == d["policy_id"]
                        and x.data["split"] == "prequential"
                        and not x.data.get("parent_decision_id")
                    ]
                    if previous and d["fit_id"] != previous[-1].data["fit_id"]:
                        prior = previous[-1]
                        compatible = [
                            u
                            for u in updates
                            if u.seq > prior.seq
                            and u.data["previous_fit_id"] == prior.data["fit_id"]
                            and u.data["applied_fit_id"] == d["fit_id"]
                            and byid[u.data["candidate_fit_id"]].data["fold_id"]
                            == fold.id
                        ]
                        if not compatible:
                            raise ValueError(
                                "Prequential refit has no compatibility update from its previously retained model"
                            )
                if d.get("parent_decision_id"):
                    initial = byid[d["parent_decision_id"]]
                    paid = [
                        o
                        for o in rs
                        if o.type == "observation"
                        and o.data["decision_id"] == initial.id
                    ]
                    if len(paid) != 1 or not initial.seq < paid[0].seq < r.seq:
                        raise ValueError(
                            "Terminal decision lacks a prior paid observation"
                        )
                    if d.get("observe_s") != initial.data["observe_s"]:
                        raise ValueError(
                            "Terminal decision changes its paid observation window"
                        )
                policy = byid[d["policy_id"]]
                if (
                    d["decision"] == "observe"
                    and "pre_model" in f.data["model"]
                    and d["observe_s"] != f.data["model"]["spec"]["observation_s"]
                ):
                    raise ValueError(
                        "Committed observation differs from its trained world-model window"
                    )
                if policy.data["spec"]["family"] != "muse":
                    from translucent.intervention_models import predict
                    from translucent.intervention_actions import (
                        decide,
                        observation_features,
                    )

                    row = example(
                        L,
                        cid,
                        d["trial_id"],
                        physics="physics_prior" in str(policy.data["spec"]),
                    )
                    if d.get("parent_decision_id"):
                        window = byid[d["parent_decision_id"]].data["observe_s"]
                        extra = observation_features(
                            L, cid, d["trial_id"], d["policy_id"], window
                        )
                        row = {
                            **row,
                            "features": {**row["features"], **extra},
                            "observed": True,
                        }
                    probability, hard = predict(f.data["model"], row)
                    trial_name = byid[d["trial_id"]].data["trial_key"]
                    check_probability(
                        d["p_ignite"],
                        probability,
                        atol=probability_atol,
                        trial=trial_name,
                    )
                    lo, hi = d["interval"]
                    if not 0 <= lo <= d["p_ignite"] <= hi <= 1:
                        raise ValueError("Committed probability interval is invalid")
                    # Only the already-bounded recomputation noise may extend
                    # an endpoint for rule validation. The stored interval and
                    # the probability used to choose the action are unchanged.
                    checked_interval = [min(lo, probability), max(hi, probability)]
                    expected = decide(
                        probability,
                        checked_interval,
                        policy.data["decision_rule"],
                        p.data["costs"],
                        hard_prediction=hard,
                        terminal=bool(d.get("parent_decision_id")),
                        world_model=f.data["model"],
                    )
                    check_action(
                        d["decision"],
                        expected["action"],
                        recorded_p=d["p_ignite"],
                        recomputed_p=probability,
                        trial=trial_name,
                    )
                    if (
                        not d.get("parent_decision_id")
                        and d["decision"] == "observe"
                        and d["observe_s"] != expected["observe_s"]
                    ):
                        raise ValueError(
                            "Committed watch duration differs from its executable decision rule"
                        )
            if r.type == "observation":
                initial = byid[d["decision_id"]]
                trial = byid[d["trial_id"]]
                if (
                    initial.data["decision"] != "observe"
                    or initial.seq >= r.seq
                    or initial.data["observe_s"] != d["observe_s"]
                ):
                    raise ValueError(
                        "Observation was released before its watch commitment"
                    )
                if (d["trial_id"], d["policy_id"]) != (
                    initial.data["trial_id"],
                    initial.data["policy_id"],
                ):
                    raise ValueError(
                        "Observation belongs to a different trial or policy than its watch commitment"
                    )
                if d.get("source_artifact") != trial.data["series_artifact"]:
                    raise ValueError("Observation source differs from its trial trace")
                end = trial.data["t_heater_off"] + d["observe_s"]
                import json

                source = json.loads(
                    L.blob_path(trial.data["series_artifact"]).read_text()
                )
                pairs = [
                    (t, y)
                    for t, y in zip(source["t"], source["temperature_C"])
                    if t <= end
                ]
                expected = dict(
                    t=[t for t, y in pairs], temperature_C=[y for t, y in pairs]
                )
                if d["trace"] != expected:
                    raise ValueError(
                        "Observation differs from its paid recorded window"
                    )
            if r.type == "screen":
                fold = byid[d["fold_id"]]
                policy = byid[d["policy_id"]]
                if fold.seq >= r.seq or policy.seq >= r.seq:
                    raise ValueError("Screen preceded its policy or fold registration")
                if d["train_ids"] != fold.data["train_ids"] or d["reveal_ids"] != [
                    reveals[i].id for i in d["train_ids"]
                ]:
                    raise ValueError("Screen provenance differs from the training fold")
                if any(reveals[i].seq >= r.seq for i in d["train_ids"]):
                    raise ValueError("Screen used unrevealed feedback")
                model = recompute_fit(L, cid, policy, fold, p.data)
                fresh = screen_data(L, cid, policy, fold, model, p.data)
                if canonical(d) != canonical(fresh):
                    raise ValueError(
                        "Recorded screen counts or survival differ from training-only recomputation"
                    )
            if r.type == "update":
                from translucent.intervention_actions import (
                    compatibility,
                    history_predictions,
                )

                policy = byid[d["policy_id"]]
                spec = policy.data["spec"]
                previous = byid[d["previous_fit_id"]]
                candidate = byid[d["candidate_fit_id"]]
                applied = byid[d["applied_fit_id"]]
                if any(
                    f.type != "fit" or f.data["policy_id"] != policy.id
                    for f in (previous, candidate, applied)
                ):
                    raise ValueError(
                        "Compatibility models belong to a different policy"
                    )
                fold = byid[candidate.data["fold_id"]]
                if (
                    fold.type != "fold"
                    or fold.data["split"] != "prequential"
                    or d.get("fold_id", fold.id) != fold.id
                ):
                    raise ValueError(
                        "Compatibility update does not match its candidate prequential fold"
                    )
                if (
                    d["evaluated_trial_ids"] != fold.data["train_ids"]
                    or candidate.data["train_ids"] != fold.data["train_ids"]
                ):
                    raise ValueError(
                        "Compatibility must evaluate all registered training history"
                    )
                history = {
                    t.id
                    for t in rs
                    if t.type == "trial"
                    and t.data["scope"] == fold.data["scope"]
                    and t.id in reveals
                    and reveals[t.id].seq < fold.seq
                }
                if set(fold.data["train_ids"]) != history:
                    raise ValueError(
                        "Compatibility training fold omits revealed scope history"
                    )
                if (
                    d["previous_rule"] != policy.data["decision_rule"]
                    or d["candidate_rule"] != policy.data["decision_rule"]
                ):
                    raise ValueError(
                        "Compatibility decision rule differs from the registered policy"
                    )
                prior_updates = [
                    x
                    for x in rs
                    if x.type == "update"
                    and x.data["policy_id"] == policy.id
                    and x.seq < r.seq
                ]
                prior_decisions = [
                    x
                    for x in scored
                    if x.data["policy_id"] == policy.id
                    and x.data["split"] == "prequential"
                    and not x.data.get("parent_decision_id")
                    and x.seq < r.seq
                ]
                states = [(x.seq, x.data["applied_fit_id"]) for x in prior_updates] + [
                    (x.seq, x.data["fit_id"]) for x in prior_decisions
                ]
                if not states or previous.id != max(states)[1]:
                    raise ValueError(
                        "Compatibility previous model is not the last retained state"
                    )
                if not fold.data["train_ids"]:
                    raise ValueError("Compatibility update has no measured feedback")
                latest = max(
                    (reveals[i] for i in fold.data["train_ids"]), key=lambda x: x.seq
                )
                if (
                    d["after_reveal_id"] != latest.id
                    or d["after_trial"] != latest.data["trial_id"]
                ):
                    raise ValueError(
                        "Compatibility update is not anchored to the latest training reveal"
                    )
                rows = [
                    example(
                        L,
                        cid,
                        i,
                        training=True,
                        post_s=spec.get("observation_s", 0),
                        physics="physics_prior" in str(spec),
                    )
                    for i in d["evaluated_trial_ids"]
                ]
                if any(reveals[i].seq >= r.seq for i in d["evaluated_trial_ids"]):
                    raise ValueError("Compatibility used unrevealed feedback")
                if any(
                    byid[d[k]].seq >= r.seq
                    for k in ("previous_fit_id", "candidate_fit_id")
                ):
                    raise ValueError("Compatibility preceded its models")
                old = history_predictions(
                    byid[d["previous_fit_id"]].data["model"],
                    d["previous_rule"],
                    rows,
                    p.data["costs"],
                )
                new = history_predictions(
                    byid[d["candidate_fit_id"]].data["model"],
                    d["candidate_rule"],
                    rows,
                    p.data["costs"],
                )
                trial_names = [
                    byid[i].data["trial_key"] for i in d["evaluated_trial_ids"]
                ]
                check_history(
                    d["old_predictions"],
                    old,
                    atol=probability_atol,
                    trials=trial_names,
                    side="previous",
                )
                check_history(
                    d["new_predictions"],
                    new,
                    atol=probability_atol,
                    trials=trial_names,
                    side="candidate",
                )
                fresh = compatibility(
                    d["old_predictions"],
                    d["new_predictions"],
                    tolerance=p.data["calibration_tolerance"],
                )
                if any(d[k] != v for k, v in fresh.items()):
                    raise ValueError("Compatibility verdict differs")
                executable_verdict = compatibility(
                    old, new, tolerance=p.data["calibration_tolerance"]
                )
                if any(
                    d[k] != executable_verdict[k] for k in ("accepted", "added_unsafe")
                ):
                    raise ValueError(
                        "Compatibility safety/calibration acceptance differs from executable models"
                    )
                if byid[d["after_reveal_id"]].seq >= r.seq:
                    raise ValueError("Update preceded measured feedback")
                wanted = (
                    d["candidate_fit_id"] if d["accepted"] else d["previous_fit_id"]
                )
                if d["applied_fit_id"] != wanted:
                    raise ValueError("A rejected update was applied")
            if r.type == "score":
                if d.get("post_hoc"):
                    from translucent.intervention_sensitivity import recompute_record

                    fresh = recompute_record(L, cid, d, r.seq)
                else:
                    fresh = score(
                        L, cid, d["policy_id"], d["split"], d["subset"], r.seq
                    )
                if canonical(fresh) != canonical(d):
                    raise ValueError(
                        "Recorded score differs from committed decisions and real outcomes"
                    )
            if r.type == "comparison":
                if d.get("post_hoc"):
                    from translucent.intervention_sensitivity import recompute_record

                    fresh = recompute_record(L, cid, d, r.seq)
                else:
                    fresh = comparison(
                        L, cid, d["a"], d["b"], d["split"], d["subset"], r.seq
                    )
                if canonical(fresh) != canonical(d):
                    raise ValueError(
                        "Recorded comparison differs from exact paired recomputation"
                    )
            if r.type == "review":
                fresh = evaluate_claim(L, byid[d["claim_id"]])
                if canonical(fresh) != canonical(d):
                    raise ValueError("Gate verdict differs on offline recomputation")
        except (ValueError, KeyError, TypeError) as exc:
            out.append(f"{r.id}: {exc}")
    return out


def funnel_counts(L, cid):
    policies = records(L, cid, "policy")
    submitted = {r.data["policy_id"] for r in records(L, cid, "submission")}
    amended = protocol(L, cid).data.get("amendment", 0) >= 3
    candidates = (
        {r.id for r in policies if r.data["kind"] == "agent"}
        if amended
        else {r.id for r in policies}
    )
    baseline_ids = {r.id for r in policies if r.data["kind"] == "baseline"}
    submitted_candidates = submitted & candidates
    screens = [
        r for r in records(L, cid, "screen") if r.data["policy_id"] in candidates
    ]
    screened = {r.data["policy_id"] for r in screens if not r.data["survived"]}
    counts = dict(
        proposed=len(candidates),
        screened_out=len(screened - submitted_candidates),
        submitted=len(submitted_candidates),
        pending=len(candidates - submitted - screened),
        candidate_models_proposed=sum(
            r.data["candidate_models_proposed"] for r in screens
        ),
        candidate_models_screened_out=sum(
            r.data["candidate_models_screened_out"] for r in screens
        ),
        unit="Held-out candidate submission is the agent learning strategy; frozen baselines are comparators"
        if amended
        else "Held-out submission is the learning strategy; candidate-model counts are training-only nested fits",
    )
    if amended:
        counts.update(
            candidates_proposed=len(candidates),
            candidates_screened_out=counts["screened_out"],
            candidates_submitted=len(submitted_candidates),
            baselines_registered=len(submitted & baseline_ids),
            policies_registered_total=len(policies),
        )
    return counts


def _passive_reference(L, cid):
    from translucent.contracts import Source
    from translucent.systems.nist_heat import load_dataset, SYSTEM

    continuous = [t for t in load_dataset() if t["delay_after_vent_s"] is None]
    traces = []
    for t in continuous:
        aid = None
        if t["t"]:
            aid = L.put_json(
                dict(t=t["t"], temperature_C=t["y"]),
                kind="series",
                source=Source(
                    uri="doi:" + SYSTEM["doi"],
                    citation=SYSTEM["citation"],
                    licence=SYSTEM["licence"],
                ),
            ).id
        traces.append(
            dict(
                trial_key=t["trial_key"],
                condition=dict(format=t["format"], soc=t["soc"]),
                series_artifact=aid,
                t_ignition=t["t_ignition"],
                event_sources=t["event_sources"],
                flags=t["flags"],
            )
        )
    return record(
        L,
        cid,
        "reference",
        dict(
            name="passive_never_stop",
            n=len(continuous),
            ignited=sum(t["outcome"] != "rescued" for t in continuous),
            ignition_interval=wilson(
                sum(t["outcome"] != "rescued" for t in continuous), len(continuous)
            ),
            traces=traces,
            source=SYSTEM["citation"],
            limit="Observed continuous heating series; no stand-down/escalation decision point, not a paired policy comparator",
        ),
    )


def run_campaign(
    L,
    sealed_store,
    *,
    splits=("loco", "lofo", "prequential"),
    with_inferred=True,
    bootstrap=64,
    progress=None,
    heater_off_lag_s=10,
    unsafe_ratio=10,
):
    from translucent.intervention_models import (
        frozen_specs,
        scripted_specs,
        split_folds,
        bootstrap_predict,
        muse_replay,
    )
    from translucent.systems.nist_heat import load_dataset, ingest

    c = create(
        L,
        splits=list(splits),
        heater_off_lag_s=heater_off_lag_s,
        unsafe_ratio=unsafe_ratio,
    )
    policies = {}
    from translucent.intervention_physics import register_priors

    register_priors(L, c.id)
    _passive_reference(L, c.id)
    for name, spec in {**frozen_specs(), **scripted_specs()}.items():
        if name.startswith("agent_"):
            record(
                L,
                c.id,
                "planner",
                dict(
                    planner="scripted generic search v1",
                    call={
                        "tool": "propose_policy",
                        "arguments": {"name": name, "spec": spec.model_dump()},
                    },
                    observes="Registered feature library only; no trial outcomes or exploratory feature ranking",
                ),
            )
        rule = (
            {"mode": "always_observe", "observe_s": 30}
            if name == "always_observe_30"
            else None
        )
        p = propose_policy(
            L,
            c.id,
            name,
            spec,
            kind="agent" if name.startswith("agent_") else "baseline",
            decision_rule=rule,
        )
        policies[name] = p
        if p.data["kind"] == "baseline":
            submit_policy(L, c.id, p.id)
    from translucent.intervention_runtime import register_hypotheses

    register_hypotheses(L, c.id, [p.id for p in policies.values()])
    from translucent.intervention_models import PolicySpec

    diagnostic = propose_policy(
        L,
        c.id,
        "generic_constant_candidate",
        PolicySpec(family="constant", probability=0),
    )
    active = {}
    submitted = {r.data["policy_id"] for r in records(L, c.id, "submission")}
    source = [
        dict(
            id=t["trial_key"],
            condition=dict(format=t["format"], soc=t["soc"]),
            exp=t["exp"],
        )
        for t in load_dataset(heater_off_lag_s=heater_off_lag_s)
        if t["t"]
        and t["delay_after_vent_s"] is not None
        and (with_inferred or "heater_off_inferred" not in t["flags"])
    ]
    for split in splits:
        common = None
        for label, train_keys, test_keys in split_folds(source, split):
            scope = split if split == "prequential" else f"{split}:{label}"
            if common is None or split != "prequential":
                trials = ingest(
                    L,
                    c.id,
                    sealed_store,
                    scope=scope,
                    include_inferred=with_inferred,
                    heater_off_lag_s=heater_off_lag_s,
                )
                bykey = {t.data["trial_key"]: t for t in trials}
                if split == "prequential":
                    common = bykey
            else:
                bykey = common
            train = [bykey[x["id"]] for x in train_keys]
            test = [bykey[x["id"]] for x in test_keys]
            fold = register_fold(
                L, c.id, scope, split, [t.id for t in train], [t.id for t in test]
            )
            already = {r.data["trial_id"] for r in records(L, c.id, "reveal")}
            for t in train:
                if t.id in already:
                    continue
                commit_decision(
                    L,
                    c.id,
                    t.id,
                    "training",
                    "abstain",
                    0.5,
                    [0, 1],
                    scored=False,
                    split=split,
                )
                reveal(L, c.id, t.id, sealed_store)
            if progress:
                progress(
                    f"{split} fold {label}: {len(train)} training, {len(test)} held out"
                )
            fitted_policies = {}
            for name, p in policies.items():
                fitted = fit_policy(L, c.id, p.id, fold.id)
                if p.data["kind"] == "agent":
                    screened = screen_policy(L, c.id, p.id, fold.id, fitted)
                    if p.id not in submitted:
                        if not screened.data["survived"]:
                            raise ValueError(
                                "Registered scripted strategy has no screening survivor"
                            )
                        submit_policy(L, c.id, p.id)
                        submitted.add(p.id)
                if split == "prequential" and p.data["spec"]["family"] != "muse":
                    from translucent.intervention_actions import checked_update

                    fitted = checked_update(
                        L, c.id, p.id, fitted, active.get(p.id), fold_id=fold.id
                    )
                    active[p.id] = fitted
                fitted_policies[name] = fitted
            diagnostic_fit = fit_policy(L, c.id, diagnostic.id, fold.id)
            screen_policy(L, c.id, diagnostic.id, fold.id, diagnostic_fit)
            from translucent.intervention_actions import (
                decide,
                observe,
                observation_features,
            )

            for name, p in policies.items():
                fitted = fitted_policies[name]
                spec = p.data["spec"]
                training = [
                    example(
                        L,
                        c.id,
                        i,
                        training=True,
                        post_s=spec.get("observation_s", 0),
                        physics="physics_prior" in str(spec),
                    )
                    for i in fitted.data["train_ids"]
                ]
                for t in test:
                    row = example(L, c.id, t.id, physics="physics_prior" in str(spec))
                    if name == "muse_only":
                        replay = muse_replay(row["features"])
                        if replay["status"] == "not_run":
                            continue
                        prob = replay["p_ignite"]
                        h = int(prob >= 0.5)
                        interval = replay["interval"]
                    else:
                        prob, h, interval = bootstrap_predict(
                            fitted.data["model"], training, row, replicates=bootstrap
                        )
                    chosen = decide(
                        prob,
                        interval,
                        p.data["decision_rule"],
                        protocol(L, c.id).data["costs"],
                        hard_prediction=h,
                        world_model=fitted.data["model"],
                    )
                    initial = commit_decision(
                        L,
                        c.id,
                        t.id,
                        p.id,
                        chosen["action"],
                        prob,
                        interval,
                        observe_s=chosen["observe_s"],
                        split=split,
                        fit_id=fitted.id,
                        fold_id=fold.id,
                        simulated=p.data["simulated"],
                    )
                    if chosen["action"] == "observe":
                        observe(L, c.id, t.id, p.id, chosen["observe_s"])
                        extra = observation_features(
                            L, c.id, t.id, p.id, chosen["observe_s"]
                        )
                        after = {
                            **row,
                            "features": {**row["features"], **extra},
                            "observed": True,
                        }
                        prob, h, interval = bootstrap_predict(
                            fitted.data["model"], training, after, replicates=bootstrap
                        )
                        terminal = decide(
                            prob,
                            interval,
                            p.data["decision_rule"],
                            protocol(L, c.id).data["costs"],
                            hard_prediction=h,
                            terminal=True,
                            world_model=fitted.data["model"],
                        )
                        commit_decision(
                            L,
                            c.id,
                            t.id,
                            p.id,
                            terminal["action"],
                            prob,
                            interval,
                            split=split,
                            fit_id=fitted.id,
                            fold_id=fold.id,
                            observe_s=chosen["observe_s"],
                            parent_decision_id=initial.id,
                        )
            for t in test:
                reveal(L, c.id, t.id, sealed_store)
    scores = []
    comparisons = []
    subsets = ["all", "without_inferred", "without_censored"] + [
        f"condition:{f}:{s}" for f in ("18650", "21700") for s in (25, 50, 75, 100)
    ]
    for split in splits:
        for name, p in policies.items():
            for subset in subsets:
                s = score(L, c.id, p.id, split, subset)
                record(L, c.id, "score", s)
                scores.append(s)
        for a in ("agent_threshold", "agent_logistic"):
            for b in policies:
                if b.startswith("agent_") or b == "muse_only":
                    continue
                comp = comparison(L, c.id, policies[a].id, policies[b].id, split)
                record(L, c.id, "comparison", comp)
                comparisons.append(comp)
    from translucent import gate

    for s in scores:
        if s["subset"] != "all" or not s["n"]:
            continue
        claim = propose_claim(
            L,
            c.id,
            dict(
                type="policy_performance",
                policy_id=s["policy_id"],
                split=s["split"],
                subset="all",
                n=s["n"],
                unsafe=s["unsafe"],
            ),
        )
        gate.review(L, claim.id)
    for comp in comparisons:
        for kind in ("policy_beats", "policy_no_more_unsafe_fewer_missed"):
            claim = propose_claim(
                L,
                c.id,
                dict(
                    type=kind,
                    a=comp["a"],
                    b=comp["b"],
                    split=comp["split"],
                    subset="all",
                ),
            )
            gate.review(L, claim.id)
    from translucent.intervention_runtime import propose_experiments, write_procedure
    from translucent.intervention_sensitivity import write_sensitivities

    propose_experiments(L, c.id, fold.data["train_ids"])
    # The final procedure closes the campaign. Descriptive records must be
    # appended beforehand, without reopening a completed evidence ledger.
    write_sensitivities(L, c.id)
    chosen = policies["agent_threshold"]
    supported = [
        r.data["claim_id"]
        for r in records(L, c.id, "review")
        if r.data["verdict"] == "supported"
        and get(L, c.id, r.data["claim_id"], "claim").data["predicate"].get("policy_id")
        == chosen.id
    ]
    if supported:
        write_procedure(L, c.id, chosen.id, supported)
    return c, policies, scores, comparisons


def _minimum_unanimous_discordances(alpha, family_size):
    # Best possible two-sided p with d nonzero paired differences is 2 / 2**d.
    d = 1
    while 2 ** (1 - d) * family_size >= alpha:
        d += 1
    return d


def _supported_comparisons(campaign, kind):
    """Read scientific support from the gate's recorded verdicts only."""
    claims = {c["id"]: c["predicate"] for c in campaign.get("claims", [])}
    return {
        (
            p.get("a", p.get("policy_id")),
            p.get("b", p.get("baseline_id")),
            p.get("split"),
        )
        for r in campaign.get("reviews", [])
        if r["verdict"] == "supported"
        and (p := claims.get(r["claim_id"], {})).get("type") == kind
        and p.get("subset", "all") == "all"
    }


def _primary_variant(campaign):
    """Identify the registered data/lag/cost setting without re-selecting it."""
    return (
        campaign["subset"] == "with_inferred"
        and campaign["heater_off_lag_s"] == 10
        and campaign["unsafe_ratio"] == 10
    )


def markdown_report(result):
    """Disclose the registered comparison and descriptive robustness checks."""
    campaigns = result["campaigns"]
    primary = next((c for c in campaigns if _primary_variant(c)), None)
    reference = primary or next(iter(campaigns), {})
    config = reference.get("protocol", {})
    costs = config.get("costs", {})
    alpha = config.get("alpha", 0.05)
    permutation = config.get("primary_test", {})
    cost_assumption = (
        "Cost assumption for this table: escalating on a trial that ignites costs 0; "
        "escalation efficacy was not observed."
    )
    families = sorted(
        {c["family_size"] for campaign in campaigns for c in campaign["comparisons"]}
    )
    family_text = "; ".join(
        f"{c['variant']}: {c['funnel']['submitted']} candidate submissions and "
        f"{c['funnel'].get('baselines_registered', 'unreported')} registered baselines"
        for c in campaigns
    )
    primary_pairs = [
        item
        for item in (primary or {}).get("comparisons", [])
        if item["split"] == "loco"
    ]
    primary_support = (
        _supported_comparisons(primary, "policy_beats") if primary else set()
    )
    always_escalate = (primary or {}).get("policy_ids", {}).get("always_escalate")
    escalation_wins = [
        item
        for item in primary_pairs
        if item["b"] == always_escalate
        and (item["a"], item["b"], item["split"]) in primary_support
    ]
    learned_advantage = (
        "The decisive comparator is always_escalate: the learned policies do not establish an advantage over it in the registered primary analysis."
        if primary_pairs and not escalation_wins
        else "Assess learned-policy value against always_escalate using the registered primary gate verdicts below."
    )
    lines = [
        "# NIST intervention benchmark",
        "",
        learned_advantage
        + " The primary differences against hard-rule baselines can arise from the decision-rule asymmetry, an illustrative temperature cut, and failed physics forecasts. Such differences alone do not establish that the agent learned a better intervention model.",
        "",
        "This is a factual case study of ignition after the recorded heater cut. An alternative stop time has no observed outcome. No provider was called by this benchmark. Suppression efficacy was not recorded.",
        "",
        "Amendment 2 changed the costs and primary endpoint: cf46b56 was committed at 2026-10-30 02:12:58 +05:30 and c71925d at 02:22:19 +05:30. Amendment 3 restricted the correction family and candidate cap to screened agent submissions: bbda321 was committed at 2026-10-30 02:50:31 +05:30 (2026-10-29 21:20:31 UTC). The first archived scoring run began at 2026-10-29 21:21:02 UTC, 31 seconds after amendment 3. These commits precede the archived scoring runs and any Muse Wonder campaign; git cannot establish that no earlier held-out result had been inspected. The dataset, exploratory analyses and feature library were already available. This amendment chain is not evidence of an unseen-data preregistration.",
        "",
        f"The registered primary endpoint remains mean decision cost: unsafe stand-down costs {costs.get('unsafe', 100):g}, ignition during a paid watch costs {costs.get('watch_ignition', 100):g}, unnecessary escalation costs {costs.get('unnecessary_escalation', 10):g}, watching costs {costs.get('watch_per_second', 0.05):g} per second, and abstention costs {costs.get('abstain', 10):g} as a deferred escalation. Abstention is counted as escalation for missed rescues and accuracy and is charged once. Cost-ratio sensitivities retain the registered escalation and watch costs.",
        "",
        f"The registered paired test uses the absolute sum of per-trial cost differences: all sign assignments through {permutation.get('exact_max_discordant', 20)} nonzero pairs, otherwise {permutation.get('resamples', 100000):,} seeded assignments with a plus-one correction. Holm correction covers screened agent candidate submissions times registered baseline comparators. A missing Muse comparator contributes p=1. Baseline-versus-baseline differences are descriptive. The registered family is retained for every split and data sensitivity, but only the default-lag, with-inferred LOCO result is primary; all other splits and sensitivities are descriptive. Unsafe-only McNemar and the secondary two-part results are reported descriptively. Observed unsafe nonincrease does not establish statistical noninferiority.",
        "",
        "Ledger registration counts: " + family_text + ".",
        "",
        f"Policies search generic feature families using training-fold CV. Only screened agent strategies count toward the fixed K={config.get('heldout_cap_K', 'unreported')} candidate cap; baseline registrations do not consume K. The ledger freezes registrations before the first held-out decision. LOFO reports the known format-transfer failure, and prequential commits in Exp-number order. Within-condition outcomes share a fitted model, so per-trial sign flips can overstate the evidence when paired losses are dependent. The condition-clustered sensitivity below addresses this dependence descriptively without changing the primary analysis.",
        "",
        "The registered 10 s thermocouple lag is primary. The separately refitted 7 s sensitivity reflects the supply-lamp video marker; edited Power Off timing supports 9–10 s. Both lags are scored with and without inferred decision points. Rescued traces stop at render-safe reheat. Exp3 and Exp17 lack traces and are excluded; Exp67/73 are finite-window censored rescues, with exclusion scores in JSON; Exp33 has suspect SOC. Outcome-correlated data-quality flags are hidden from planner tools and Muse prompts, and retained in the ledger for audit and subset analysis.",
        "",
        "The without_inferred subset selects on outcome: it excludes trials whose heater-off could not be located because ignition followed quickly. It is not a neutral data-quality sensitivity. Decision points inferred using paper-mean vent-relative delays are reconstructed by a different procedure from directly located cuts.",
        "",
        "Paper Table 3 is a retrospective in-sample lookup using these outcomes, with an Exp44 inconsistency. Missing conditions use a default delay of -1 s, which implements always-escalate; that is an implementation choice. The separately refitted delay lookup is the validation comparator. The fixed_temperature baseline's 170 °C cut is illustrative, unsourced. The threshold sweep below does not select a replacement primary cut. temperature_refit reproduces the exploratory pooled threshold.",
        "",
        "World models return probabilities and intervals; separate decision rules map them to actions. The primary agent strategies use the registered cost rule, whereas the frozen probability-producing baselines use their classification rules. Applying different decision rules under asymmetric costs is a substantive comparator choice. The post-hoc cost-rule sensitivity below applies the same registered cost rule to each baseline's own held-out probability, without refitting or adding observation.",
        "",
        "Observation releases only a paid recorded window from the registered options. Ignition while waiting is unsafe regardless of the terminal decision, and watch time then stops at ignition. The optimistic perfect-information continuation bound and training watch-risk estimate used to choose observation are not calibrated guarantees.",
        "",
        f"Prequential updates require a prior reveal, no newly unsafe revealed trial, and no more than {config.get('calibration_tolerance', 0.01):g} deterioration in calibration or Brier score. Rejected fits remain unapplied. Every forecast carries simulated=true; claims use real recorded outcomes and preceding commitments.",
        "",
        "The two-node solver uses same-condition continuous-heating priors and fits hidden core state to pre-off surface measurements. Hidden state and alternative-action forecasts remain simulated. A physics-prior feature competes only inside training folds. Proposed experiments rank mechanistic-model disagreement and include a human-reviewable heating and watch schedule. No proposed experiment was performed.",
        "",
        "Binary intervals are Wilson 95%; losses and mean cost have condition-block bootstrap 95% intervals. Prediction intervals bootstrap training conditions conditional on the selected spec and are not calibrated guarantees.",
    ]
    quality = reference.get("data_quality", {})
    delays = quality.get("inferred_ignition_delays_s", [])
    if delays:
        lines += [
            "",
            f"Across the source interrupted-trial set, all {quality.get('inferred_count', len(delays))} inferred heater-off decision points precede recorded ignition by {min(delays):g}–{max(delays):g} s. Excluding them removes ignition outcomes preferentially and changes the interpretation of watch-policy costs.",
        ]
    for passive in reference.get("references", []):
        if passive.get("name") == "passive_never_stop":
            lo, hi = passive["ignition_interval"]
            trace_n = sum(bool(t.get("series_artifact")) for t in passive["traces"])
            lines += [
                "",
                f"Passive continuous heating has {passive['ignited']}/{passive['n']} recorded ignitions (Wilson 95% {100 * lo:.1f}–{100 * hi:.1f}%; {trace_n} traces), a separate observed series without a paired decision point.",
            ]
    lines += [
        "",
        cost_assumption,
        "",
        "| Data, lag, unsafe:escalation | Split | Policy | n | Mean cost [95%] | Unsafe [rate 95%] | Missed [rate 95%] | Abstain | Watch s | Brier [95%] | Log-loss [95%] | Accuracy [Wilson 95%] |",
        "|---|---|---|---:|---|---|---|---:|---:|---|---|---|",
    ]
    for campaign in campaigns:
        names = {v: k for k, v in campaign["policy_ids"].items()}
        for item in campaign["scores"]:
            if item["subset"] != "all":
                continue
            if not item["n"]:
                lines.append(
                    f"| {campaign['variant']} | {item['split']} | {names[item['policy_id']]} | {item['n']} | not run | — | — | — | — | — | — | — |"
                )
                continue

            def ci(field):
                return f"{item[field]:.3f} [{item[field + '_interval'][0]:.3f}, {item[field + '_interval'][1]:.3f}]"

            def count_ci(field):
                return f"{item[field]} [{item[field + '_interval'][0]:.3f}, {item[field + '_interval'][1]:.3f}]"

            lines.append(
                f"| {campaign['variant']} | {item['split']} | {names[item['policy_id']]} | {item['n']} | {ci('mean_cost')} | {count_ci('unsafe')} | {count_ci('missed')} | {item['abstain']} | {item['watch_time_s']:.1f} | {ci('brier')} | {ci('logloss')} | {ci('accuracy')} |"
            )
    lines += [
        "",
        "Every cost and metric traces to committed decision and reveal ids. Condition scores, exclusions, watch ignitions, intervals and per-trial differences remain in the receipt. Only a registered primary policy_beats claim with a supported gate review receives 'yes' below; every other verdict is descriptive.",
        "",
        cost_assumption,
        "",
        "| Data, lag, ratio | Split | Agent | Baseline | Agent / baseline mean cost | Registered paired p | Registered Holm p | Family | Lower cost supported? | Secondary two-part | Unsafe McNemar p (descriptive) | Clustered p (descriptive) | Clustered Holm p (descriptive) | Condition blocks |",
        "|---|---|---|---|---:|---:|---:|---:|---|---|---:|---:|---:|---:|",
    ]
    for campaign in campaigns:
        names = {v: k for k, v in campaign["policy_ids"].items()}
        supported = _supported_comparisons(campaign, "policy_beats")
        clusters = {
            (c["a"], c["b"], c["split"]): c
            for c in campaign.get("sensitivities", {}).get("clustered_comparisons", [])
        }
        for item in campaign["comparisons"]:
            key = (item["a"], item["b"], item["split"])
            won = (
                _primary_variant(campaign)
                and item["split"] == "loco"
                and key in supported
            )
            cluster = clusters.get(key)
            cluster_fields = (
                f"{cluster['p']:.5g} | {cluster['p_holm']:.5g} | {cluster['n_blocks']}"
                if cluster
                else "not run | not run | —"
            )
            lines.append(
                f"| {campaign['variant']} | {item['split']} | {names[item['a']]} | {names[item['b']]} | {item['a_mean_cost']:.3f} / {item['b_mean_cost']:.3f} | {item['p']:.5g} | {item['p_holm']:.5g} | {item['family_size']} | {'yes' if won else 'descriptive'} | descriptive | {item['unsafe_mcnemar_p']:.5g} | {cluster_fields} |"
            )
    lines += [
        "",
        "Condition-clustered p values flip the sign of the total paired cost difference for each held-out condition block, rather than each trial. Their Holm values use the same candidate-by-baseline family size, including missing comparators at p=1, but are descriptive robustness checks outside the registered primary test.",
        "",
        f"With d discordant units the best possible exact two-sided p is 2^(1-d). At alpha={alpha:g}, at least {_minimum_unanimous_discordances(alpha, 1)} unanimous discordances are needed without correction. "
        + "; ".join(
            f"a {m}-comparison family needs at least {_minimum_unanimous_discordances(alpha, m)} at the first Holm step"
            for m in families
        )
        + ". Mixed signs can require more. Sharing a fitted model within a condition does not create independent inferential units. Monte Carlo p values have finite resolution and the JSON records sampling standard error.",
    ]
    if primary:
        clustered = [
            c
            for c in primary.get("sensitivities", {}).get("clustered_comparisons", [])
            if c["split"] == "loco"
        ]
        if clustered:
            blocks = max(c["n_blocks"] for c in clustered)
            family = max(
                (
                    c["family_size"]
                    for c in primary["comparisons"]
                    if c["split"] == "loco"
                ),
                default=1,
            )
            lines += [
                "",
                f"The primary LOCO sensitivity has {blocks} condition blocks: the smallest possible two-sided clustered p is {2 ** (1 - blocks):.5g}, or {min(1, family * 2 ** (1 - blocks)):.5g} at the first Holm step. "
                + (
                    "No clustered comparison survives correction."
                    if all(c["p_holm"] > alpha for c in clustered)
                    else "Inspect the descriptive clustered values alongside the registered per-trial values."
                ),
            ]
    lines += [
        "",
        "## post-hoc sensitivity (added after the primary results were known)",
        "",
        'These baseline variants apply decide(..., mode="cost") to their own original held-out probabilities with no refit and no watch. They are descriptive, marked post_hoc=true in the ledger, and outside the registered Holm family. Their paired sign-flip p values use the registered test mechanics without a new confirmatory correction family. They do not amend or replace the primary analysis.',
        "",
        cost_assumption,
        "",
        "| Data, lag, ratio | Split | Cost-rule baseline | n | Unsafe | Missed | Mean cost | Interpretation |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for campaign in campaigns:
        names = {v: k for k, v in campaign["policy_ids"].items()}
        for item in campaign.get("sensitivities", {}).get("cost_rule_scores", []):
            lines.append(
                f"| {campaign['variant']} | {item['split']} | {names[item['policy_id']]} | {item['n']} | {item['unsafe']} | {item['missed']} | {item['mean_cost']:.3f} | descriptive |"
            )
    lines += [
        "",
        cost_assumption,
        "",
        "| Data, lag, ratio | Split | Agent | Cost-rule baseline | Agent / baseline mean cost | Paired p | Interpretation |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for campaign in campaigns:
        names = {v: k for k, v in campaign["policy_ids"].items()}
        for item in campaign.get("sensitivities", {}).get("cost_rule_comparisons", []):
            lines.append(
                f"| {campaign['variant']} | {item['split']} | {names[item['a']]} | {names[item['b']]} | {item['a_mean_cost']:.3f} / {item['b_mean_cost']:.3f} | {item['p']:.5g} | descriptive |"
            )
    physics = [
        (campaign, p)
        for campaign in campaigns
        for p in campaign.get("sensitivities", {}).get("physics_probabilities", [])
    ]
    if physics:
        lines += [
            "",
            "| Data, lag, ratio | Split | Physics baseline | Minimum p | Maximum p | Interpretation |",
            "|---|---|---|---:|---:|---|",
        ]
        for campaign, item in physics:
            names = {v: k for k, v in campaign["policy_ids"].items()}
            lines.append(
                f"| {campaign['variant']} | {item['split']} | {names[item['policy_id']]} | {item['min_p']:.5g} | {item['max_p']:.5g} | descriptive |"
            )
        primary_physics = {
            next(k for k, v in c["policy_ids"].items() if v == p["policy_id"]): p
            for c, p in physics
            if _primary_variant(c) and p["split"] == "loco"
        }
        one = primary_physics.get("physics_only")
        two = primary_physics.get("two_node_physics")
        if one and two:
            lines += [
                "",
                f"Both physics baselines fail to discriminate under their primary classification rules: physics_only gives p={one['min_p']:g} everywhere (maximum {one['max_p']:g}), and two_node_physics never exceeds {two['max_p']:g}. Their primary wins are evidence of failed comparators, not of useful learned discrimination.",
            ]
    lines += [
        "",
        "The fixed-temperature sweep is descriptive and post-hoc. It varies the illustrative, unsourced temperature cut across the declared grid on the same held-out trials. No threshold is selected from this sweep and the primary 170 °C rule is unchanged.",
        "",
        cost_assumption,
        "",
        "| Data, lag, ratio | Split | Threshold °C | n | Unsafe | Missed | Mean cost | Interpretation |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for campaign in campaigns:
        for item in campaign.get("sensitivities", {}).get("threshold_sweep", []):
            lines.append(
                f"| {campaign['variant']} | {item['split']} | {item['threshold_c']:g} | {item['n']} | {item['unsafe']} | {item['missed']} | {item['mean_cost']:.3f} | descriptive |"
            )
    lines += [
        "",
        "| Data, lag, ratio | Candidate proposals | Screened out | Candidates submitted / cap | Baselines registered | Inner models / screened out | Updates accepted / rejected |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for campaign in campaigns:
        f = campaign["funnel"]
        u = campaign["updates"]
        lines.append(
            f"| {campaign['variant']} | {f['proposed']} | {f['screened_out']} | {f['submitted']} / {campaign['protocol']['heldout_cap_K']} | {f.get('baselines_registered', 0)} | {f['candidate_models_proposed']} / {f['candidate_models_screened_out']} | {u['accepted']} / {u['rejected']} |"
        )
    if primary:
        names = {v: k for k, v in primary["policy_ids"].items()}
        lines += [
            "",
            "At the registered primary default-lag, with-inferred LOCO evaluation, the recorded gate verdicts are:",
        ]
        for agent in sorted(
            {names[c["a"]] for c in primary["comparisons"] if c["split"] == "loco"}
        ):
            pairs = [
                c
                for c in primary["comparisons"]
                if c["split"] == "loco" and names[c["a"]] == agent
            ]
            supported = _supported_comparisons(primary, "policy_beats")
            wins = [
                names[c["b"]]
                for c in pairs
                if (c["a"], c["b"], c["split"]) in supported
            ]
            fails = [
                names[c["b"]]
                for c in pairs
                if (c["a"], c["b"], c["split"]) not in supported
            ]
            lines.append(
                f"{agent}: supported lower mean cost than {', '.join(wins) if wins else 'no scored baseline'}; superiority not established against {', '.join(fails) if fails else 'none of the scored baselines'}. These verdicts retain the registered hard-rule comparators; the descriptive cost-rule and clustered comparisons limit their scientific interpretation."
            )
        muse_n = next(
            (
                s["n"]
                for s in primary["scores"]
                if s["split"] == "loco"
                and s["subset"] == "all"
                and names[s["policy_id"]] == "muse_only"
            ),
            0,
        )
        lines += [
            "",
            f"Muse-only has {muse_n} recorded held-out predictions. A missing comparator is not evidence of superiority.",
            "",
            learned_advantage
            + " A cost-aware rule can outperform classification rules under the registered asymmetric costs without learning useful physical discrimination. The clustered test and cost-rule-baseline sensitivity must accompany any account of the primary wins.",
        ]
    lines += [
        "",
        "LOFO is a known transfer failure; format, chemistry and cell design cannot be separated here. Inspect unsafe errors and missed rescues alongside cost. Published-data reuse, reconstructed events, finite observation windows, uncertain kinetic priors and indistinguishable boundary pairs limit the claims. These are experimental operating procedures, not validated fire-safety instructions.",
        "",
        "Reproduce offline into a chosen directory: `uv run translucent bench nist --out /path/to/output`. The command prints the paths it wrote. The default remains `evals`; use a separate directory to preserve committed reports. Select one split with `--split loco`, `--split lofo` or `--split prequential`. `--with-inferred` or `--without-inferred`, `--lag-s 7` or `--lag-s 10`, and `--unsafe-ratio 5`, `10` or `20` restrict the registered sensitivity plan. Ledger paths in result JSON exist after rerunning; they are not committed. Verify a generated ledger with `uv run translucent verify PATH`, or verify the committed self-contained web fixture directly.",
        "",
    ]
    return "\n".join(lines)


def _benchmark_variant(job):
    from translucent.ledger import Ledger
    from translucent.verify import verify

    out, stamp, split, include, lag, ratio, bootstrap, verbose = job
    out = Path(out)
    subset = "with_inferred" if include else "without_inferred"
    variant = f"{subset}-lag{lag}-ratio{ratio}"
    root = out / "runs" / f"intervene-{stamp}-{variant}"
    L = Ledger(root)
    store = out / "sealed" / f"intervene-{stamp}-{variant}"

    def progress(message):
        if verbose:
            print(f"[{variant}] {message}", flush=True)

    try:
        c, policies, scores, comparisons = run_campaign(
            L,
            store,
            splits=[split] if split else ("loco", "lofo", "prequential"),
            with_inferred=include,
            bootstrap=bootstrap,
            progress=progress,
            heater_off_lag_s=lag,
            unsafe_ratio=ratio,
        )
        from translucent.intervention_sensitivity import sensitivities
        from translucent.systems.nist_heat import load_dataset

        descriptive = sensitivities(L, c.id)
        inferred = [
            t
            for t in load_dataset(heater_off_lag_s=lag)
            if t["t"]
            and t["delay_after_vent_s"] is not None
            and "heater_off_inferred" in t["flags"]
        ]
        checked = verify(root, c.id)
        if not checked.ok:
            raise ValueError("; ".join(checked.problems))
        result = dict(
            id=c.id,
            subset=subset,
            variant=variant,
            heater_off_lag_s=lag,
            unsafe_ratio=ratio,
            ledger=str(root),
            policy_ids={k: p.id for k, p in policies.items()},
            protocol=protocol(L, c.id).data,
            funnel=funnel_counts(L, c.id),
            updates={
                "accepted": sum(r.data["accepted"] for r in records(L, c.id, "update")),
                "rejected": sum(
                    not r.data["accepted"] for r in records(L, c.id, "update")
                ),
            },
            scores=scores,
            comparisons=comparisons,
            sensitivities=descriptive,
            references=[r.data for r in records(L, c.id, "reference")],
            data_quality={
                "source": "all interrupted decision points before inferred-trial exclusions",
                "inferred_count": len(inferred),
                "inferred_ignition_delays_s": [
                    t["t_ignition"] - t["t_heater_off"]
                    for t in inferred
                    if t["t_ignition"] is not None
                ],
            },
            claims=[
                dict(id=r.id, predicate=r.data["predicate"])
                for r in records(L, c.id, "claim")
            ],
            reviews=[r.data for r in records(L, c.id, "review")],
            verification=checked.as_dict(),
        )
        # A completed campaign is independently recoverable if a later variant is interrupted.
        checkpoint = out / "results" / f"intervene-{stamp}-{variant}.json"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_bytes(canonical(result))
        return result
    finally:
        L.db.close()


def benchmark(
    out=Path("evals"),
    *,
    split=None,
    inferred=None,
    lag_s=None,
    unsafe_ratio=None,
    progress=print,
    bootstrap=64,
):
    from concurrent.futures import ProcessPoolExecutor

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(f"Benchmark output directory: {out.resolve()}")
        if (out / "INTERVENE.md").exists():
            progress(
                f"Replacing existing report: {(out / 'INTERVENE.md').resolve()}; "
                "use --out DIRECTORY to preserve that report"
            )
    result = dict(
        version=2,
        stamp=stamp,
        task="factual ignition after recorded heater-off",
        preregistration_amendment=3,
        campaigns=[],
    )
    # One-factor cost sensitivities retain the primary data and lag. Lag sensitivities
    # include both inferred-trial settings; the registered default is first.
    variants = [
        (True, 10, 10),
        (False, 10, 10),
        (True, 7, 10),
        (False, 7, 10),
        (True, 10, 5),
        (True, 10, 20),
    ]
    if inferred is not None:
        variants = [v for v in variants if v[0] == inferred]
    if lag_s is not None:
        variants = [v for v in variants if v[1] == lag_s]
    if unsafe_ratio is not None:
        variants = [v for v in variants if v[2] == unsafe_ratio]
    if not variants:
        raise ValueError(
            "This combination is outside the registered one-factor sensitivity plan"
        )
    jobs = [(str(out), stamp, split, *v, bootstrap, bool(progress)) for v in variants]
    if len(jobs) == 1:
        result["campaigns"] = [_benchmark_variant(jobs[0])]
    else:
        # Every process owns its SQLite connection and sealed store. Three workers
        # bound CPU and memory while independent registered variants run in parallel.
        with ProcessPoolExecutor(max_workers=min(3, len(jobs))) as pool:
            result["campaigns"] = list(pool.map(_benchmark_variant, jobs))
    path = out / "results" / f"intervene-{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(result))
    report = out / "INTERVENE.md"
    report.write_text(markdown_report(result), encoding="utf-8", newline="\n")
    if progress:
        progress(f"Benchmark written: {path.resolve()}; report: {report.resolve()}")
    return result, path
