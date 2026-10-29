"""Run a campaign headless. Example:
  uv run python scripts/run_campaign.py --ledger runs/m4 --movie 4 --profile dev --budget 1.0
  uv run python scripts/run_campaign.py --ledger runs/m4 --experiment lu2023/movie1 --profile dev --budget 1.0
(use the same --ledger for both so the Lu campaign starts with the Movie 4 lessons)
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from translucent import tools
from translucent.ledger import Ledger
from translucent.runtime import Campaigner, Settings, new_campaign

HERO = ("How fast do the lithium filaments grow across the solid electrolyte, does the battery short when they "
        "bridge it, and does it recover when they retract?")
LU_OBJECTIVE = ("When does lithium start plating on the graphite electrode during fast charging, and does the cell's "
                "voltage log agree with the paper's stated onset after 10 h of charging?")
EXPERIMENTS = {
    "lu2023/movie1": dict(path="data/raw/lu2023/movie1.mp4", label="Lu Movie 1",
                          citation="Lu et al., Nat. Commun. 14, 5127 (2023), Supplementary Movie 1",
                          title="Lithium plating on a graphite electrode", objective=LU_OBJECTIVE),
}


def experiment(name: str) -> dict:
    if name in EXPERIMENTS:
        return EXPERIMENTS[name]
    n = int(name.removeprefix("reisecker2023/movie"))
    return dict(path=f"data/raw/reisecker2023/movie{n}.mp4", label=f"Movie {n}",
                citation=f"Reisecker et al., Nat. Commun. 14, 2432 (2023), Supplementary Movie {n}",
                title="Lithium filaments in a solid-state battery", objective=HERO)


def lu_agent(messages, schemas):
    """Offline demonstration policy; the real runtime and gate still do the work."""
    if not schemas:
        return "INCONCLUSIVE\nNo independent visual evidence was supplied.", []
    vid = re.search(r"sha256:[0-9a-f]{64}", messages[0]["content"]).group()
    done = [m["content"] for m in messages if m["role"] == "tool"]
    plan = [("lookup_calibration", {"video_id": vid, "quantity": "spatial_scale"}),
            ("lookup_calibration", {"video_id": vid, "quantity": "time_base"}),
            ("read_cell_voltage", {"video_id": vid})]
    if len(done) < len(plan):
        return "I will read the experiment records.", [plan[len(done)]]
    said = " ".join(done)
    first = {}
    for cycle in (1, 2):
        first[cycle] = re.search(rf"cycle {cycle} charge \([^)]*\): voltage first goes below 0 V[^—]*— "
                                 r"(m_[0-9a-f]{12})", said).group(1)
    plateau = re.search(r"cycle 1 charge \([^)]*\): voltage first comes within[^—]*— (m_[0-9a-f]{12})", said).group(1)
    findings = [(first[1], 6.7, 8.2, "If the voltage reading is within 10 mV, the first charge drops below 0 V between 6.7 and 8.2 hours."),
                (first[1], 7.3, 7.5, "The first drop below 0 V comes between 7.3 and 7.5 hours."),
                (first[2], 0.0, 1.0, "In the second charge the voltage drops below 0 V within the first hour."),
                (first[1], 9.5, 10.5, "If the voltage reading is within 10 mV, the stated 10 h onset is when the voltage first drops below 0 V."),
                (plateau, 9.5, 11.0, "The voltage settles onto its plateau between 9.5 and 11 hours.")]
    index = said.count("Finding id")
    if index < len(findings):
        mid, lo, hi, text = findings[index]
        return "I will check a timing finding.", [("propose_finding", {
            "type": "value_within", "measurement_ids": [mid], "params": {"lo": lo, "hi": hi, "unit": "h"},
            "statement": text})]
    return "The records show the timing limits.", [("finish", {
        "answer": "If plating starts at the first drop below 0 V, the stated 10 h can't be reconciled with this log: "
                  "if the voltage reading is within 10 mV, that first drop comes between 6.7 and 8.2 hours. Tighter "
                  "times, and when the plateau starts, can't be pinned down from this log. These are voltage readings; "
                  "on their own they don't show when plating becomes visible. The second charge is in the log, not in the movie.",
        "not_proven": "How accurate the voltage reading is isn't stated. When plating becomes visible needs outlines from the video."})]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="runs/m4")
    ap.add_argument("--movie", type=int, default=4, help="Reisecker movie number (ignored with --experiment)")
    ap.add_argument("--experiment", help="e.g. lu2023/movie1 or reisecker2023/movie4")
    ap.add_argument("--profile", default="dev")
    ap.add_argument("--budget", type=float, default=1.0)
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--memory", default="on")
    ap.add_argument("--objective")
    ap.add_argument("--offline", action="store_true", help="scripted local demonstration; no paid calls")
    a = ap.parse_args(argv)

    name = a.experiment or f"reisecker2023/movie{a.movie}"
    x = experiment(name)
    L = Ledger(Path(a.ledger))
    v = tools.import_video(L, Path(x["path"]), label=x["label"], experiment=name, licence="CC BY 4.0",
                           citation=x["citation"])
    c = new_campaign(L, a.objective or x["objective"], x["title"], [v.id], memory=a.memory, budget_usd=a.budget)
    print("campaign", c.id)
    camp = Campaigner(L, c, Settings(profile="dev" if a.offline else a.profile, max_steps=a.steps))
    if a.offline:
        from evals.fake_llm import FakeClient
        from evals.ablation import scripted_agent
        fake = FakeClient(lu_agent if name == "lu2023/movie1" else scripted_agent)
        camp.clients = {"fireworks": fake, "meta": fake}
    res = camp.run()
    print(json.dumps(res, indent=1))
    for e in L.events(c.id):
        if e.type in ("step.started", "tool.finished", "claim.reviewed", "memory.learned", "campaign.finished"):
            print(f"{e.seq:3d} {e.actor:18s} {e.title}")
    L.db.close()
    return res


if __name__ == "__main__":
    main()
