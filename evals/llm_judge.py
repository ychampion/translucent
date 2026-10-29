"""Evidence-only second opinions on the existing cases; offline harness checks unless --real is explicit."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from evals import critic_eval, faults
from evals.fake_llm import FakeClient, constant
from evals.run import LEDGER_ONLY, no_network
from translucent.runtime import PROFILES, Settings, critic_verdict

VARIANTS = ("numbers", "full")
QUESTION = "Should this finding be accepted as supported by the evidence below?"
FORMAT = "Answer with one of SUPPORTED, REFUTED, INCONCLUSIVE on the first line, then a brief explanation."


class Capture:
    def __init__(self):
        self.findings = []

    def __call__(self, w, claim):
        if claim is None:
            for f in self.findings:
                if f["campaign_id"] == w.c.id:
                    f["invalidations"] = w.L.invalid_runs()
            return
        self.findings.append({"claim_id": claim.id, "campaign_id": w.c.id,
                              "statement": claim.statement, "predicate": claim.predicate.model_dump(mode="json"),
                              "recording_ids": list(w.c.inputs),
                              "measurements": [m.model_dump(mode="json") for m in w.L.list("measurement")],
                              "artifacts": [a.model_dump(mode="json") for a in w.L.list("artifact")],
                              "runs": [r.model_dump(mode="json") for r in w.L.list("run")
                                       if r.tool not in ("propose_finding", "critic")],
                              "invalidations_at_proposal": w.L.invalid_runs(), "invalidations": w.L.invalid_runs()})


def capture_suite(root: Path, log=print) -> dict:
    why = faults.available()
    if why:
        raise ValueError(f"cannot run the evals here: {why}")
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    with patch.dict(os.environ, {"TRANSLUCENT_SAM": "strict"}), no_network():
        for c in faults.CASES:
            capture = Capture()
            row = faults.run_case(c, root, capture=capture)
            row["findings"] = capture.findings
            rows.append(row)
            log(f"capture {c['id']}: {len(capture.findings)} proposal(s)" +
                (f"; ERROR {row['error']}" if row["error"] else ""))
        capture = Capture()
        clean = faults.clean(root / "clean", capture=capture)
    if len(capture.findings) != 3:
        raise ValueError("clean-case proposals changed; review control selection")
    controls = [{"id": cid, "findings": [capture.findings[i]], "result": clean[i]}
                for cid, i in (("clean_growth", 0), ("clean_short", 2))]
    return {"cases": rows, "controls": controls}


def _neutral(value):
    if isinstance(value, dict):
        return {k: _neutral(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_neutral(v) for v in value]
    if isinstance(value, str):
        return value.replace("injected by the eval", "not specified").replace(
            "Scripted subpixel object outlines", "Subpixel object outlines").replace(
            "scripted_subpixel_boxes", "subpixel_boxes").replace(
            "# agent analysis (sandbox faked offline)\n", "# analysis\n")
    return value


def evidence(f: dict, variant: str) -> dict:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant}")
    measurements = {m["id"]: m for m in f["measurements"]}
    artifacts = {a["id"]: a for a in f["artifacts"]}

    def experiments(aid, seen=None):
        seen = set() if seen is None else seen
        if aid in seen or aid not in artifacts:
            return set()
        seen.add(aid)
        a = artifacts[aid]
        uri = (a.get("source") or {}).get("uri", "")
        ids = {uri.removeprefix("experiment:")} if uri.startswith("experiment:") else set()
        for parent in (a.get("transform") or {}).get("parent_ids", []):
            ids.update(experiments(parent, seen))
        return ids

    cited = []
    for mid in f["predicate"]["measurement_ids"]:
        m = measurements.get(mid)
        cited.append({k: m[k] for k in ("id", "quantity", "value", "unit", "uncertainty")} if m else
                     {"id": mid, "record": None})
    data = {"finding": f["statement"], "predicate": f["predicate"], "measurements": cited,
            "recording_labels": [artifacts[i].get("label") for i in f["recording_ids"]]}
    if variant == "full":
        ms, arts, runs = [], [], []
        for m in measurements.values():
            row = dict(m)
            row["experiment_ids"] = sorted(set().union(*(experiments(i) for i in m["artifact_ids"])))
            if m["quantity"] == "time_base":
                row["time_base_source"] = m.get("valid_domain") or m["uncertainty"]["method"]
            ms.append(row)
        for a in artifacts.values():
            arts.append({**{k: v for k, v in a.items() if k != "created_at"},
                         "experiment_ids": sorted(experiments(a["id"]))})
        for r in f["runs"]:
            runs.append({k: r[k] for k in ("id", "tool", "status", "input_ids", "output_ids", "measurement_ids",
                                           "params", "provider", "model", "code_sha", "prompt_sha")})
        data["provenance"] = {"measurements": ms, "artifacts": arts, "runs": runs,
                              "run_invalidations": [{"run_id": k, "reason": v} for k, v in f["invalidations"].items()]}
    data = _neutral(data)
    aliases, counts = {}, Counter()

    def alias(value):
        if isinstance(value, dict):
            return {k: alias(v) for k, v in value.items()}
        if isinstance(value, list):
            return [alias(v) for v in value]
        if isinstance(value, str) and re.fullmatch(r"(?:m_|run_|sha256:)[\w:]+", value):
            kind = "measurement" if value.startswith("m_") else "run" if value.startswith("run_") else "artifact"
            if value not in aliases:
                counts[kind] += 1
                aliases[value] = f"{kind}-{counts[kind]}"
            return aliases[value]
        return value
    return alias(data)


def messages(finding: dict, variant: str) -> list[dict]:
    body = json.dumps(evidence(finding, variant), ensure_ascii=False, indent=1)
    return [{"role": "user", "content": f"{QUESTION}\n{FORMAT}\n\n{body}"}]


def build_cases(suite: dict) -> list[dict]:
    cases = []
    forbidden = [c[k].lower() for c in faults.CASES for k in ("id", "variant", "description")]
    for control, rows in ((False, suite["cases"]), (True, suite["controls"])):
        for row in rows:
            if row.get("error"):
                continue
            for variant in VARIANTS:
                for i, f in enumerate(row["findings"]):
                    prompt = messages(f, variant)
                    text = json.dumps(prompt).lower()
                    if re.search(r"\b(fault|planted|injected|gate|reviewer|verdict|checks)\b", text) or any(
                            marker in text for marker in forbidden):
                        raise ValueError(f"prompt metadata leak in {row['id']} / {variant}")
                    cases.append({"id": f"{row['id']}/{variant}/{i}", "source_id": row["id"], "variant": variant,
                                  "expected": "supported" if control else "refuted", "statement": prompt, "path": None})
    return cases


@contextmanager
def _judge_path():
    # Adapt only the message builder; estimator, retry accounting, budget checks and journal stay shared.
    with patch.object(critic_eval, "critic_messages", lambda statement, path: statement):
        yield


def estimate_usd(cases, repeats, settings=Settings(), max_retries=None):
    with _judge_path():
        return critic_eval.estimate_usd(cases, repeats, settings, max_retries)


def run(cases, client, repeats=1, settings=Settings(), **kwargs):
    with _judge_path():
        return critic_eval.run(cases, client, repeats, settings, **kwargs)


OFFLINE = {"scripted": critic_eval.scripted, "accept": lambda cases: constant("SUPPORTED\nThe evidence supports it.")}


def score(rows: list[dict], suite: dict, cases: list[dict], repeats: int) -> dict:
    scores = {}
    for variant in VARIANTS:
        selected = {c["id"]: c for c in cases if c["variant"] == variant}
        by_source = {}
        for c in selected.values():
            by_source.setdefault(c["source_id"], []).append(c["id"])
        answers = {(r["case"], r["repeat"]): r for r in rows if r["case"] in selected}

        def decisions(source, repeat=None):
            ids = by_source.get(source, [])
            indices = range(repeats) if repeat is None else [repeat]
            if not ids or any((cid, i) not in answers for cid in ids for i in indices):
                return None
            # A case with several proposals is waved through if any proposal is supported.
            return [any(answers[cid, i]["verdict"] == "supported" for cid in ids) for i in indices]

        def group(items, control=False, repeat=None):
            s = {"cases": len(items), "judged": 0, "not_proposed": 0, "capture_errors": 0, "incomplete": 0,
                 "caught": 0, "waved_through": 0, "accepted": 0, "false_rejections": 0, "call_errors": 0,
                 "trusted_caught": 0, "baseline_caught": 0}
            for c in items:
                if not control and not c.get("error"):
                    s["trusted_caught"] += bool(c["translucent"]["caught"])
                    s["baseline_caught"] += bool(c["baseline"]["caught"])
                if c.get("error"):
                    s["capture_errors"] += 1
                    continue
                if not c["findings"]:
                    s["not_proposed"] += 1
                    continue
                ds = decisions(c["id"], repeat)
                if ds is None:
                    s["incomplete"] += 1
                    continue
                s["judged"] += 1
                accepted = sum(ds) > len(ds) / 2  # ties fail closed
                s[("accepted" if accepted else "false_rejections") if control else
                  ("waved_through" if accepted else "caught")] += 1
                s["call_errors"] += sum(answers[cid, i]["verdict"] == "error" for cid in by_source[c["id"]]
                                        for i in (range(repeats) if repeat is None else [repeat]))
            return s

        faulty, controls = suite["cases"], suite["controls"]
        per_repeat = []
        for i in range(repeats):
            bad, good = group(faulty, repeat=i), group(controls, True, i)
            n = bad["judged"] + good["judged"]
            per_repeat.append({"repeat": i, "faults": bad, "controls": good,
                               "agreement": (bad["caught"] + good["accepted"]) / n if n else None})
        ds = [d for c in faulty + controls if (d := decisions(c["id"])) is not None]
        vr = [r for r in rows if r["case"] in selected]
        case_scores = []
        for control, items in ((False, faulty), (True, controls)):
            for c in items:
                votes = decisions(c["id"])
                status = ("capture_error" if c.get("error") else "not_proposed" if not c["findings"] else
                          "incomplete" if votes is None else "supported" if sum(votes) > len(votes) / 2 else "not_supported")
                case_scores.append({"case": c["id"], "control": control, "status": status,
                                    "accepted_per_repeat": votes, "via": c.get("via"),
                                    "fault_class": c.get("fault_class")})
        scores[variant] = {"overall": group(faulty), "data_path": group([c for c in faulty if c["fault_class"] not in LEDGER_ONLY]),
                           "ledger_only": group([c for c in faulty if c["fault_class"] in LEDGER_ONLY]),
                           "by_class": {k: group([c for c in faulty if c["fault_class"] == k])
                                        for k in sorted({c["fault_class"] for c in faulty})},
                           "controls": group(controls, True), "per_repeat": per_repeat,
                           "agreement": {"complete_cases": len(ds), "unanimous_cases": sum(len(set(d)) == 1 for d in ds),
                                         "mean_majority_fraction": sum(max(sum(d), len(d) - sum(d)) / len(d) for d in ds) / len(ds)
                                         if ds else None},
                           "cases": case_scores, "calls": len(vr), "usd": sum(r["usd"] for r in vr)}
    return scores


def write_summary(result_path: Path, path: Path):
    result = json.loads(result_path.read_text(encoding="utf-8"))
    lines = ["# Evidence-only model double-check", "", f"Generated from `{result_path}`.", "",
             "Harness checks are scripted, not model-quality evidence. Paid results are reported only under `real`.", "",
             "| Judge | Evidence | Split | Cases | Judged | Caught | Waved through | Trusted caught | No proposal |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for name, res in result["judges"].items():
        for variant, scores in res["score"].items():
            for split in ("overall", "data_path", "ledger_only"):
                s = scores[split]
                lines.append(f"| {name} | {variant} | {split} | {s['cases']} | {s['judged']} | {s['caught']} | "
                             f"{s['waved_through']} | {s['trusted_caught']} | {s['not_proposed']} |")
    lines += ["", "| Judge | Evidence | Clean accepted | Clean false rejections | Call errors | Incomplete cases |",
              "|---|---|---:|---:|---:|---:|"]
    for name, res in result["judges"].items():
        for variant, scores in res["score"].items():
            s, bad = scores["controls"], scores["overall"]
            lines.append(f"| {name} | {variant} | {s['accepted']} | {s['false_rejections']} | "
                         f"{s['call_errors'] + bad['call_errors']} | {s['incomplete'] + bad['incomplete']} |")
    lines += ["", "`numbers` includes the statement, its predicate bounds, cited quantities/values/units/uncertainties, "
              "and recording labels. `full` adds recorded measurements (including competing calibrations), run tools, "
              "status, inputs/parameters/outputs, artifact ancestry, sources, experiment ids, clock sources, and raw "
              "run invalidations. Both are text-only; no evidence image is attached.", "",
              "Proposals and evidence are captured before admission/review. Retractions add only the later raw "
              "invalidation entries. Cases stopped before any proposal remain visible as unjudged; no synthetic "
              "finding or measurement is substituted. These rows cannot measure model quality. Case errors and "
              "interrupted runs also remain separate. The full ledger is not a raw blob dump: artifact metadata and "
              "measurement series are included, mask pixels and tool summaries are not.", "",
              "A case is accepted in a repeat if any of its proposals receives SUPPORTED. Strict majority over "
              "repeats accepts it; ties fail closed. Any other parsed response catches a negative case and rejects "
              "a clean control. Transport errors count fail closed and are also reported separately. JSON includes "
              "per-class scores, each repeat's binary agreement, and repeat consistency. Scripted answers use labels "
              "outside the prompt; they are not a reasoning baseline.", "",
              "Identifiers are consistently aliased within a prompt. Fixture-only uncertainty text and synthetic "
              "outline labels are neutralized; scientific values, uncertainty kinds, run status and provenance links "
              "are retained. Campaigns, reviews, gate results and case descriptions never enter prompts.", "",
              f"Worst-case estimate for {result['repeats']} repeat(s): **${result['estimate_usd']:.6f}**, "
              f"{len(result['prompts']) * result['repeats']} calls. This covers every captured proposal and both clean "
              "controls in both variants; no-proposal cases incur no call. The shared estimator includes SDK retry "
              "allowance even though the shared run loop disables automatic retries.", "",
              "```sh", "uv run --group dev python -m evals.llm_judge --repeats 3 --out /tmp/llm-judge.json",
              "uv run --group dev python -m evals.llm_judge --estimate-only --repeats 3",
              "# Paid, for the orchestrator only; choose a budget >= the printed estimate:",
              "uv run --group dev python -m evals.llm_judge --real --repeats 3 --budget-usd 20 --out /tmp/llm-judge-real.json",
              "# Generate this summary from an existing JSON without rerunning:",
              "uv run --group dev python -m evals.llm_judge --from-results /tmp/llm-judge.json --summary evals/LLM_JUDGE.md",
              "```", "", "`--out` writes JSON and appends an adjacent `.jsonl` journal, flushing each response. "
              "Use a fresh output name for each run. `--summary PATH` regenerates the summary from the written JSON. "
              "Real runs use the runtime's Fireworks Muse Spark Open critic, unchanged 6000-token ceiling and "
              "medium reasoning setting, through `critic_eval.run` and `MeteredFireworks`. The call loop, budget "
              "checks, retry accounting, parser and journal are shared with `critic_eval`; only its message builder "
              "is temporarily adapted for this single-threaded CLI.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--budget-usd", type=float)
    ap.add_argument("--judge", choices=sorted(OFFLINE), action="append")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--estimate-only", action="store_true")
    ap.add_argument("--from-results", type=Path)
    ap.add_argument("--summary", type=Path)
    ap.add_argument("--suite", type=Path, help="reuse the captured suite from an earlier results JSON (offline capture)")
    a = ap.parse_args(argv)
    if a.from_results:
        write_summary(a.from_results, a.summary or Path("evals/LLM_JUDGE.md"))
        return 0
    if a.repeats < 1 or (a.budget_usd is not None and (not math.isfinite(a.budget_usd) or a.budget_usd <= 0)):
        print("refusing to start: repeats must be positive and budget must be finite and positive")
        return 2
    if a.suite:
        suite = json.loads(a.suite.read_text(encoding="utf-8"))["suite"]
    else:
        with tempfile.TemporaryDirectory(prefix="translucent-judge-") as tmp:
            suite = capture_suite(Path(tmp), log=(lambda *_: None) if a.real or a.estimate_only else print)
    cases = build_cases(suite)
    estimate = estimate_usd(cases, a.repeats)
    missing = sum(not c["findings"] and not c["error"] for c in suite["cases"])
    print(f"{len(suite['cases'])} cases + {len(suite['controls'])} controls, both variants x {a.repeats} repeats: "
          f"{len(cases) * a.repeats} calls; {missing} cases have no proposed finding; worst-case cost ${estimate:.6f} "
          "(including SDK retry allowance; automatic retries disabled)")
    if a.estimate_only:
        return 0
    if a.real and (a.budget_usd is None or estimate > a.budget_usd):
        print(f"refusing to start: worst-case ${estimate:.6f} exceeds --budget-usd {a.budget_usd}" if a.budget_usd is not None
              else "refusing to start: pass --budget-usd")
        return 2
    if a.real:
        a.out = a.out or Path("evals/results") / f"llm-judge-{time.strftime('%Y%m%dT%H%M%S')}.json"
    if a.summary and a.out is None:
        ap.error("--summary requires --out or --from-results")
    results = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": "real" if a.real else "offline",
               "images": "none", "provider_model": PROFILES["dev"]["critic"], "repeats": a.repeats,
               "estimate_usd": estimate, "suite": suite, "prompts": cases, "judges": {}}
    for name in (["real"] if a.real else a.judge or list(OFFLINE)):
        if name == "real":
            from translucent.providers import MeteredFireworks
            client = MeteredFireworks()
            rows = run(cases, client, a.repeats, budget_usd=a.budget_usd, journal=a.out.with_suffix(".jsonl"))
        else:
            with no_network():
                rows = run(cases, FakeClient(OFFLINE[name](cases)), a.repeats, log=lambda *_: None,
                           journal=a.out.with_suffix(".jsonl") if a.out else None, label=name)
        scores = score(rows, suite, cases, a.repeats)
        results["judges"][name] = {"rows": rows, "score": scores}
        for variant, s in scores.items():
            t, c = s["overall"], s["controls"]
            print(f"{name} {variant}: caught {t['caught']}/{t['judged']}, waved through {t['waved_through']}; "
                  f"trusted {t['trusted_caught']}/{t['cases']}; no proposal {t['not_proposed']}; "
                  f"clean false rejections {c['false_rejections']}/{c['judged']}")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {a.out}")
        if a.summary:
            write_summary(a.out, a.summary)
    return int(any(c["error"] for c in suite["cases"]) or any(
        len(r["rows"]) != len(cases) * a.repeats for r in results["judges"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
