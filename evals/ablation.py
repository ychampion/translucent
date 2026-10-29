"""Memory ablation: does a lesson learned in campaign 1 change campaign 2?

Campaign 1 runs once (memory on) and may record lessons. Its ledger is then copied for every repeat of each arm, and
campaign 2 runs with memory="on" or "off" (Campaigner loads every lesson in its ledger when memory is on, so each
run gets its own copy to keep arms from contaminating each other).

Modes
- fake (default): a scripted offline agent (evals.fake_llm) drives the real Campaigner + tools; no network. It only
  proves the plumbing and the metrics. Its step counts differ between arms *by construction*.
- real (`--real`): the profile's real models via Campaigner's metered clients. Costs money; not run by evals.run.

    uv run python -m evals.ablation            # fake, 2 repeats
    uv run python -m evals.ablation --real --profile dev --n 3    # paid; result in evals/MEMORY.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import tempfile
import time
from collections import Counter
from pathlib import Path

from evals.fake_llm import FakeClient
from translucent.ledger import Ledger
from translucent.runtime import Campaigner, Settings, new_campaign

RESULTS = Path(__file__).resolve().parent / "results"
OBJECTIVE_1 = ("When does the Movie 4 cell short and recover, and does the cell's own electrical record agree with "
               "what the video shows?")
OBJECTIVE_2 = "When does the cell short? State it with an uncertainty the lab's records support."
LESSON = ("For these operando movies, read the cell's voltage log first: it gives short and recovery times on the "
          "recorded clock; playback frame timing is not real time.")


# ------------------------------------------------------------------ scripted offline agent

def scripted_agent(messages, tools):
    """Orchestrator policy for fake mode (state is read back from the conversation). Critic/worker get SUPPORTED."""
    if not tools:
        return "SUPPORTED\nThe image is consistent with the finding.", []
    system = messages[0]["content"]
    vid = re.search(r"(sha256:[0-9a-f]{64})", system).group(1)
    remembered = "Lessons from earlier campaigns" in system
    done = [m["content"] for m in messages if m["role"] == "tool"]
    said = " ".join(done)
    plan = ([] if remembered else [("lookup_calibration", {"video_id": vid, "quantity": "spatial_scale"}),
                                   ("lookup_calibration", {"video_id": vid, "quantity": "time_base"})])
    plan.append(("read_cell_voltage", {"video_id": vid}))
    if len(done) < len(plan):
        name, args = plan[len(done)]
        return f"Next I will run {name}.", [(name, args)]
    collapse = re.search(r"short starts at [^—]*— (m_[0-9a-f]{12})", said)
    if collapse and "Finding id" not in said:
        return "I will check the short time against the recorded clock.", [("propose_finding", {
            "type": "value_within", "measurement_ids": [collapse.group(1)], "params": {"lo": 80, "hi": 100, "unit": "s"},
            "statement": "The cell shorts between 80 and 100 seconds into the recording."})]
    if not remembered and "Lesson saved" not in said:
        evidence = re.findall(r"(run_[0-9a-f]{12}|m_[0-9a-f]{12})", said)[:2]
        return "I will remember what worked.", [("record_lesson", {"text": LESSON, "scope": "source:reisecker2023",
                                                                   "evidence_ids": evidence})]
    return "The objective is answered.", [("finish", {"answer": "See the findings.", "not_proven": "Growth speed."})]


# ------------------------------------------------------------------ runs

def _campaigner(L, c, mode, profile, max_steps):
    camp = Campaigner(L, c, Settings(profile=profile, max_steps=max_steps))
    if mode == "fake":
        fake = FakeClient(scripted_agent, usd_per_call=0.001)
        camp.clients = {"fireworks": fake, "meta": fake}
    return camp


def metrics(L: Ledger, camp: Campaigner, result: dict, prior_lessons: set[str]) -> dict:
    cid = camp.c.id
    ev = L.events(cid)
    runs = L.list("run", campaign_id=cid)
    status = Counter(L.claim_status(c.id) for c in L.list("claim", campaign_id=cid))
    loaded = {r for e in ev if e.type == "memory.loaded" for r in e.refs}
    return {"campaign_id": cid, "memory": camp.c.memory, "status": result.get("status"),
            "steps": sum(1 for e in ev if e.type == "cost.updated" and e.data.get("role") == "orchestrator"),
            "tool_runs": len(runs), "tool_failures": sum(r.status != "ok" for r in runs),
            "first_tools": [r.tool for r in runs[:3]], "cost_usd": round(camp.spent, 6),
            "earned": status["supported"], "refused": status["unsupported"],
            "not_proven": status["inconclusive"] + status["refuted"], "stale": status["stale"],
            "lessons_loaded": len(loaded), "used_campaign1_lesson": bool(loaded & prior_lessons),
            "lessons_learned": len([x for x in L.list("lesson") if x.learned_in == cid])}


def run_ablation(root: Path | None = None, n: int = 2, mode: str = "fake", profile: str = "dev",
                 campaign2_movie: str = "4", max_steps: int = 12, budget_usd: float = 1.0, log=print) -> dict:
    from evals.faults import add_movie
    root = Path(root or tempfile.mkdtemp(prefix="translucent-ablation-"))
    seed = root / "campaign1"
    L = Ledger(seed)
    vids = {m: add_movie(L, m) for m in sorted({"4", campaign2_movie})}
    c1 = new_campaign(L, OBJECTIVE_1, "Campaign 1", [vids["4"]], memory="on", budget_usd=budget_usd)
    camp = _campaigner(L, c1, mode, profile, max_steps)
    first = metrics(L, camp, camp.run(), set())
    lessons = {x.id for x in L.list("lesson")}
    L.db.close()
    log(f"campaign 1: {first['steps']} steps, {len(lessons)} lessons")
    arms = {"on": [], "off": []}
    for i in range(n):
        for arm in ("on", "off"):
            d = root / f"c2_{arm}_{i}"
            shutil.copytree(seed, d)
            L2 = Ledger(d)
            c2 = new_campaign(L2, OBJECTIVE_2, f"Campaign 2 ({arm})", [vids[campaign2_movie]], memory=arm,
                              budget_usd=budget_usd)
            camp2 = _campaigner(L2, c2, mode, profile, max_steps)
            arms[arm].append(metrics(L2, camp2, camp2.run(), lessons))
            L2.db.close()
            log(f"campaign 2 memory={arm} #{i}: {arms[arm][-1]['steps']} steps, "
                f"lesson used={arms[arm][-1]['used_campaign1_lesson']}")
    summary = {arm: {k: round(statistics.mean(r[k] for r in rs), 4) for k in
                     ("steps", "tool_runs", "tool_failures", "cost_usd", "earned", "refused", "used_campaign1_lesson")}
               for arm, rs in arms.items()}
    return {"mode": mode, "profile": profile, "n": n, "campaign2_movie": campaign2_movie, "campaign1": first,
            "campaign1_lessons": sorted(lessons), "arms": arms, "summary": summary, "root": str(root)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--real", action="store_true", help="use the profile's real (paid) models")
    ap.add_argument("--profile", default="dev")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--movie", default="4", help="campaign 2 experiment: 4 (same) or 1 (second experiment)")
    ap.add_argument("--max-steps", type=int, default=12)
    a = ap.parse_args(argv)
    os.environ.setdefault("TRANSLUCENT_SAM", "strict")
    res = run_ablation(n=a.n, mode="real" if a.real else "fake", profile=a.profile, campaign2_movie=a.movie,
                       max_steps=a.max_steps)
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"ablation-{res['mode']}-{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    if res["mode"] == "fake":
        shutil.rmtree(res["root"], ignore_errors=True)  # real-mode ledgers are kept for inspection
    print(json.dumps(res["summary"], indent=1), f"\nwritten {out}")


if __name__ == "__main__":
    main()
