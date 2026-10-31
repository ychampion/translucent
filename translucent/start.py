"""`translucent start fastcharge`: a working fast-charge campaign from nothing, in one command.

Creates the ledger under a data root (if it is not there), imports the Chen 2020 LG M50 records with their failing
1.5C four-step baseline (checked byte for byte against the published files), and opens the campaign: minimise the
10-80 % charge time with the anode potential at or above +10 mV and the cell at or below 45 degC, 8 computational
experiments, the baseline re-run on the solver at registration. Run again, it finds the same ledger, dataset and
campaign and changes nothing.

Planners: `bo` (the adapter's Bayesian-optimisation baseline, no model), `external` (an agent of your own over MCP or
HTTP proposes), `muse` (Muse Wonder on the meta profile; paid, so the server must run with TRANSLUCENT_LIVE=1 and an
administrator token). Every request waits for a person's approval in the web app unless --auto-approve is given.
"""
from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path

from translucent import datasets
from translucent.ledger import Ledger
from translucent.loop_planner import fastcharge_objective
from translucent.loop_service import Actor, LoopService
from translucent.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
RECORDS = ROOT / "data" / "raw" / "chen2020-lgm50"
MANIFEST = ROOT / "data" / "chen2020-lgm50" / "manifest.json"
FETCH = ROOT / "scripts" / "fetch_lgm50_data.py"
PLANNERS = {"bo": "domain_baseline", "external": "external", "muse": "muse"}
STATEMENT = ("Charge the LG M50 from 10 to 80 % faster than the failing 1.5C four-step protocol while the anode "
             "potential stays at or above +10 mV vs Li/Li+ and the cell at or below 45 degC.")


class StartError(Exception):
    pass


def check_records(records: Path = RECORDS) -> None:
    """The three published files, byte for byte (sizes and sha256 pinned by the fetch script)."""
    for name, (size, sha) in runpy.run_path(str(FETCH))["FILES"].items():
        path = records / name
        if not path.is_file():
            raise StartError(f"{name} is not in {records}; fetch the records with "
                             "`uv run python scripts/fetch_lgm50_data.py` (1.8 MB, Zenodo, CC BY 4.0)")
        if path.stat().st_size != size or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise StartError(f"{path} is not the published {name}: its sha256 differs from the pinned one")


def objective(planner: str, person: str, auto_approve: bool, dataset_id: str, baseline_ref: str) -> dict:
    data = fastcharge_objective(kind=PLANNERS[planner], profile="meta" if planner == "muse" else None,
                                model_usd=1.5 if planner == "muse" else 0.0, registered_by=person)
    data |= {"statement": STATEMENT, "datasets": [dataset_id], "baseline": [baseline_ref],
             "baseline_simulation": True}
    if auto_approve:
        data["auto_approve"] = [{"executor": "computational", "max_usd": 0.0, "max_wall_s": 300.0,
                                 "max_count": data["budget"]["experiments"]}]
    return data


def start_fastcharge(data_root: Path, *, ledger_id: str = "lab", planner: str = "bo", person: str,
                     auto_approve: bool = False, records: Path = RECORDS) -> dict:
    """Ledger, dataset and campaign, each created only if it is not there. Returns what was found or made."""
    check_records(records)
    ws = Workspace(data_root)
    created = not ws.exists(ledger_id)
    if created:
        ws.create(ledger_id, title="Cell lab")
    L = Ledger(ws.path(ledger_id))
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        dataset = datasets.import_dataset(L, manifest, records)         # the same import again is a no-op
        ref = next(r.id for r in dataset.records if r.role == "baseline")
        opened = LoopService(L, Actor("person", person, "cli"), idempotency_key=f"start-fastcharge-{planner}") \
            .open_campaign(objective(planner, person, auto_approve, dataset.id, f"{dataset.id}/{ref}"),
                           "Faster LG M50 charge")
        return {"ledger": ledger_id, "ledger_created": created, "path": str(ws.path(ledger_id)),
                "dataset": dataset.id, "baseline": f"{dataset.id}/{ref}", "campaign_id": opened["campaign_id"],
                "planner": planner}
    finally:
        L.db.close()
