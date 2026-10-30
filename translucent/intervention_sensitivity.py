"""Post-hoc descriptive checks, kept outside the registered primary analysis.

These records rescore already committed forecasts against revealed outcomes.
They are not new prospective decisions, amendments, submissions, or gate claims.
"""

from __future__ import annotations

from copy import deepcopy

from translucent.intervention import get, protocol, records

COST_RULE_BASELINES = (
    "fixed_temperature",
    "temperature_refit",
    "delay_lookup_refit",
    "physics_only",
    "two_node_physics",
    "paper_table3",
)
POST_HOC_LABEL = "post-hoc sensitivity (added after the primary results were known)"
ESCALATION_ASSUMPTION = (
    "Escalating on a trial that ignites costs 0; escalation efficacy was not observed."
)


def _label(sensitivity):
    return dict(
        post_hoc=True,
        descriptive=True,
        sensitivity=sensitivity,
        analysis_version=1,
        analysis_timing=POST_HOC_LABEL,
        outside_registered_holm_family=True,
        escalation_assumption=ESCALATION_ASSUMPTION,
    )


def _rescore(row, action, p):
    """Change only the retrospective action; never create a new commitment."""
    return {
        **row,
        "p": p,
        "action": action,
        "unsafe": int(action == "stand_down" and row["y"] == 1),
        "missed": int(action == "escalate" and row["y"] == 0),
        "abstain": 0,
        "correct": (action == "escalate") == bool(row["y"]),
        "observe_s": 0,
        "watch_time_s": 0,
        "ignited_during_watch": False,
    }


def cost_rule_rows(rows, costs):
    """Apply the same registered Bayes loss rule, without observation or refit."""
    from translucent.intervention_actions import decide

    return [
        _rescore(
            row,
            decide(row["p"], [row["p"], row["p"]], {"mode": "cost"}, costs)["action"],
            row["p"],
        )
        for row in rows
    ]


def _empty_pair():
    return dict(
        n=0,
        p=1.0,
        method="incomplete pairing",
        assignments=0,
        reason="No complete paired recorded predictions",
        per_trial_differences=[],
    )


def sensitivities(L, cid, before_seq=None):
    """Recompute all descriptive records from original decisions and sources.

    A verification snapshot caches this family once while checking every stored
    record independently. Only earlier records may supply evidence.
    """
    relevant = [
        r
        for r in records(L, cid)
        if r.type in ("decision", "reveal", "submission", "policy")
        and (before_seq is None or r.seq < before_seq)
    ]
    boundary = max((r.seq for r in relevant), default=0)
    cache = getattr(L, "_intervention_verification_cache", None)
    key = ("post_hoc_sensitivities", cid, boundary)
    if cache is not None and key in cache.memo:
        return deepcopy(cache.memo[key])

    from translucent.intervention_bench import (
        IncompletePairing,
        _cost_pair,
        holm,
        observations,
        summarize_rows,
    )
    from translucent.intervention_comparison import clustered_signflip

    config = protocol(L, cid).data
    costs = config["costs"]
    permutation = config["primary_test"]
    seed = permutation["seed"]
    resamples = permutation["resamples"]
    policies = {r.id: r for r in relevant if r.type == "policy"}
    byname = {r.data["name"]: r.id for r in policies.values()}
    submitted = [r.data["policy_id"] for r in relevant if r.type == "submission"]
    agents = [i for i in submitted if policies[i].data["kind"] == "agent"]
    baselines = [i for i in submitted if policies[i].data["kind"] == "baseline"]
    result = dict(
        cost_rule_scores=[],
        cost_rule_comparisons=[],
        clustered_comparisons=[],
        threshold_sweep=[],
        physics_probabilities=[],
    )
    for split in config["splits"]:
        rows = {
            i: observations(L, cid, i, split, before_seq=before_seq) for i in submitted
        }
        for name in COST_RULE_BASELINES:
            baseline = byname.get(name)
            if baseline not in rows:
                continue
            original = rows[baseline]
            # These comparators have no paid observation. Never substitute a
            # terminal forecast or an unpaid post-off feature into this check.
            if any(r["observe_s"] for r in original):
                raise ValueError("Cost-rule sensitivity requires pre-off forecasts")
            changed = cost_rule_rows(original, costs)
            summary = summarize_rows(changed, baseline, split, "all", config)
            probabilities = [r["p"] for r in original]
            summary.update(
                **_label("cost_rule"),
                baseline_name=name,
                variant_id=name + "__cost_rule",
                decision_rule={"mode": "cost", "observe_s": 0},
                source="original committed pre-off probabilities; no refit",
                min_p=min(probabilities) if probabilities else None,
                max_p=max(probabilities) if probabilities else None,
            )
            result["cost_rule_scores"].append(summary)
            if name in ("physics_only", "two_node_physics"):
                result["physics_probabilities"].append(
                    dict(
                        policy_id=baseline,
                        split=split,
                        min_p=summary["min_p"],
                        max_p=summary["max_p"],
                    )
                )
            for agent in agents:
                try:
                    paired = _cost_pair(rows[agent], changed, costs, seed, resamples)
                except IncompletePairing:
                    paired = _empty_pair()
                result["cost_rule_comparisons"].append(
                    dict(
                        a=agent,
                        b=baseline,
                        split=split,
                        subset="all",
                        **paired,
                        p_holm=None,
                        **_label("cost_rule"),
                        metric="mean_decision_cost",
                        baseline_name=name,
                        variant_id=name + "__cost_rule",
                        correction_family="outside registered Holm family; unadjusted descriptive p",
                    )
                )

        clustered = {}
        for agent in agents:
            for baseline in baselines:
                try:
                    paired = _cost_pair(
                        rows[agent], rows[baseline], costs, seed, resamples
                    )
                    conditions = {
                        r["key"]: (r["condition"]["format"], r["condition"]["soc"])
                        for r in rows[agent]
                    }
                    differences = paired["per_trial_differences"]
                    test = clustered_signflip(
                        [r["difference"] for r in differences],
                        [conditions[r["trial_key"]] for r in differences],
                        seed=seed,
                        resamples=resamples,
                    )
                    paired.update(test)
                except IncompletePairing:
                    paired = {
                        **_empty_pair(),
                        "n_blocks": 0,
                        "n_discordant_blocks": 0,
                        "per_block_differences": [],
                    }
                clustered[(agent, baseline)] = paired
        adjusted = holm(
            {pair: item["p"] for pair, item in clustered.items()},
            len(agents) * len(baselines),
        )
        for (agent, baseline), paired in clustered.items():
            result["clustered_comparisons"].append(
                {
                    **paired,
                    **_label("condition_clustered"),
                    "a": agent,
                    "b": baseline,
                    "split": split,
                    "subset": "all",
                    "p_holm": adjusted[(agent, baseline)],
                    "family_size": len(agents) * len(baselines),
                    "correction_family": "descriptive clustered counterpart of registered agent-by-baseline family; does not alter primary claims",
                    "metric": "mean_decision_cost",
                }
            )

        fixed = byname.get("fixed_temperature")
        if fixed in rows:
            from translucent.intervention import example

            levels = {
                r["key"]: example(
                    L,
                    cid,
                    get(L, cid, r["decision_id"], "decision").data["trial_id"],
                )["features"]["level"]
                for r in rows[fixed]
            }
            for threshold in range(140, 201, 5):
                changed = [
                    _rescore(
                        r,
                        "escalate" if levels[r["key"]] > threshold else "stand_down",
                        r["p"],
                    )
                    for r in rows[fixed]
                ]
                summary = summarize_rows(changed, fixed, split, "all", config)
                summary.update(
                    **_label("fixed_threshold_sweep"),
                    threshold_c=threshold,
                    source="pre-off level; strict level > threshold rule; no refit or threshold selection",
                    threshold_provenance="illustrative, unsourced",
                    variant_id=f"fixed_temperature__{threshold}C",
                    forecast_metrics="original fixed-temperature probabilities, unchanged by retrospective threshold sweep",
                )
                result["threshold_sweep"].append(summary)

    if cache is not None:
        cache.memo[key] = deepcopy(result)
    return result


def write_sensitivities(L, cid):
    """Append auditable descriptive scores without a submission or gate claim."""
    from translucent.intervention import record

    result = sensitivities(L, cid)
    for group in ("cost_rule_scores", "threshold_sweep"):
        for item in result[group]:
            record(L, cid, "score", item)
    for group in ("cost_rule_comparisons", "clustered_comparisons"):
        for item in result[group]:
            record(L, cid, "comparison", item)
    record(
        L,
        cid,
        "reference",
        dict(
            kind="post_hoc_analysis_disclosure",
            **_label("disclosure"),
            fixed_temperature="170 C: illustrative, unsourced",
            threshold_sweep="140 to 200 C in 5 C steps; no replacement primary threshold selected",
            decision_rule_asymmetry="Registered agents use the cost rule; frozen baselines use classification rules",
            basis="Scientific-validity audit and owner DELTA-7 after primary results were known",
        ),
    )
    return result


def recompute_record(L, cid, saved, before_seq):
    """Select the exact descriptive record from a fresh deterministic family."""
    result = sensitivities(L, cid, before_seq)
    kind = saved.get("sensitivity")
    if kind == "cost_rule":
        group = "cost_rule_comparisons" if "a" in saved else "cost_rule_scores"
    elif kind == "condition_clustered":
        group = "clustered_comparisons"
    elif kind == "fixed_threshold_sweep":
        group = "threshold_sweep"
    else:
        raise ValueError("Unknown post-hoc sensitivity record")
    keys = (
        ("a", "b", "split", "subset")
        if "a" in saved
        else ("policy_id", "split", "subset", "variant_id")
    )
    matches = [
        item for item in result[group] if all(item[k] == saved.get(k) for k in keys)
    ]
    if len(matches) != 1:
        raise ValueError("Post-hoc sensitivity has no unique source computation")
    return matches[0]
