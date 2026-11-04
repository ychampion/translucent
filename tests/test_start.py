"""`translucent start fastcharge`: from an empty folder to a concluded, verified campaign that the server runs alone.

The first test needs only the LG M50 records (fetched with scripts/fetch_lgm50_data.py; they are git-ignored). The
second runs the solver up to 17 times (the baseline, up to 8 reference-search runs and 8 experiments): opt in with
TRANSLUCENT_SOLVER_TESTS=1. No model is called.
"""
import os
import shutil
import time

import pytest
from fastapi.testclient import TestClient
from support.keys import add_key

from translucent import runtime, server
from translucent.cli import main as cli
from translucent.ledger import Ledger
from translucent.loop import request_states
from translucent.start import RECORDS, StartError, start_fastcharge
from translucent.verify import verify

pytestmark = pytest.mark.skipif(not RECORDS.is_dir(), reason="fetch the LG M50 records first")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("start calls no model")
    monkeypatch.setattr(runtime, "_client", denied)
    for name in ("TRANSLUCENT_LIVE", "TRANSLUCENT_ADMIN_TOKEN", "TRANSLUCENT_PROFILE"):
        monkeypatch.delenv(name, raising=False)


def count(root):
    L = Ledger(root / "ledgers" / "lab")
    try:
        return L.db.execute("SELECT COUNT(*) FROM record").fetchone()[0]
    finally:
        L.db.close()


def test_start_creates_once_and_a_second_start_changes_nothing(tmp_path, capsys):
    root = tmp_path / "data"
    assert cli(["start", "fastcharge", "--data-root", str(root), "--person", "Ana"]) == 0
    first = capsys.readouterr().out
    assert "(created)" in first and "failing baseline chen2020-lgm50/failing-baseline-1p5c" in first
    n = count(root)
    assert cli(["start", "fastcharge", "--data-root", str(root), "--person", "Ana"]) == 0
    again = capsys.readouterr().out
    assert "(created)" not in again and again.splitlines()[2] == first.splitlines()[2]      # the same campaign
    assert count(root) == n
    cid = first.split("Campaign ")[1].split(",")[0]
    L = Ledger(root / "ledgers" / "lab")
    try:
        # the baseline re-run, pre-approved at registration; the reference search waits for its result (it runs only
        # if the baseline breaks the constraints, as this 1.5C one does)
        assert [s.proposed_by for s in request_states(L, cid).values()] == ["policy:registration"]
        from translucent import chain, loop_reference
        from translucent.loop import current_objective
        recs = chain.records(L, cid)
        s = loop_reference.state(L, cid, current_objective(L, cid, recs), request_states(L, cid, recs), recs)
        assert s["status"] == "waiting_for_baseline"
    finally:
        L.db.close()
    assert verify(root / "ledgers" / "lab").ok


def test_altered_records_are_refused_before_anything_is_written(tmp_path):
    records = tmp_path / "records"
    shutil.copytree(RECORDS, records)
    with open(records / "LGM50_cell03.csv", "ab") as fh:
        fh.write(b"\n")
    with pytest.raises(StartError, match="is not the published LGM50_cell03.csv"):
        start_fastcharge(tmp_path / "data", person="Ana", records=records)
    assert not (tmp_path / "data").exists()
    (records / "LGM50_cell03.csv").unlink()
    with pytest.raises(StartError, match="fetch the records"):
        start_fastcharge(tmp_path / "data", person="Ana", records=records)


def test_the_paid_planner_needs_live_and_the_administrator_token(tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli(["start", "fastcharge", "--data-root", str(tmp_path), "--person", "Ana", "--planner", "muse", "--serve"])
    assert "paid model calls" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli(["start", "fastcharge", "--data-root", str(tmp_path), "--person", "Ana", "--planner", "muse", "--serve",
             "--live"])
    assert "TRANSLUCENT_ADMIN_TOKEN" in capsys.readouterr().err
    assert not (tmp_path / "ledgers").exists()


@pytest.mark.skipif(os.environ.get("TRANSLUCENT_SOLVER_TESTS") != "1", reason="runs the solver up to 17 times")
def test_the_server_runs_a_started_bo_campaign_to_a_verified_conclusion(tmp_path):
    root = tmp_path / "data"
    out = start_fastcharge(root, person="Ana", planner="bo", auto_approve=True)
    cid = out["campaign_id"]
    viewer = add_key(root, "Vic", "viewer", "viewer-0123456789abcdefghij")      # reads are private
    with TestClient(server.create_app(data_root=root, poll_interval=0.01, loop_workers=1), headers=viewer) as c:
        deadline, view = time.time() + 540, None
        while time.time() < deadline:
            view = c.get(f"/api/v1/ledgers/lab/campaigns/{cid}/view").json()
            if view.get("conclusion"):
                break
            time.sleep(0.5)
        assert view and view.get("conclusion"), view and [r["state"] for r in view["requests"]]
    # loop-compare/3: the failing baseline is no speed reference; the reference search's answer is
    assert view["objective"]["kind"] == "fastest_feasible"
    assert view["conclusion"]["outcome"] in ("faster_than_reference_in_simulation", "feasible_only_in_simulation"), \
        view["conclusion"]
    findings = view["conclusion"]["findings"]
    assert findings["feasibility"] == {"baseline": "fails", "best": "meets", "recovered": True,
                                       "best_request_id": findings["feasibility"]["best_request_id"]}
    assert findings["speed"]["reference"]["source"] == "reference_search"
    done = [r for r in view["requests"] if r["state"] == "completed" and r["role"] == "experiment"]
    assert 1 <= len(done) <= 8
    references = [r for r in view["requests"] if r["role"] == "reference"]
    assert 1 <= len(references) <= 8 and view["spend"]["experiments_used"] == len(done)
    assert verify(root / "ledgers" / "lab").ok


def test_without_the_solver_nothing_is_written(tmp_path, monkeypatch):
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "pybamm" else real(name, *a))
    with pytest.raises(StartError, match="uv sync --group dev --group solver"):
        start_fastcharge(tmp_path / "data", person="Ana")
    assert not (tmp_path / "data").exists()


def test_a_team_server_has_a_key_and_admits_the_local_web_app(tmp_path, monkeypatch, capsys):
    """The first start makes one engineer key, stored hashed, shown once and kept in the owner-only team-key file;
    it never becomes a process-wide token. A restart keeps it. An operator's own TRANSLUCENT_TEAM_TOKEN or CORS list
    is left alone."""
    from translucent.auth import CredentialStore
    from translucent.cli import _team_server
    for name in ("TRANSLUCENT_TEAM_TOKEN", "TRANSLUCENT_CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    _team_server(tmp_path, "Ana")
    key = (tmp_path / "team-key").read_text(encoding="utf-8").strip()
    assert len(key) >= 20 and "TRANSLUCENT_TEAM_TOKEN" not in os.environ and key in capsys.readouterr().out
    store = CredentialStore(tmp_path / "credentials.json")
    assert (store.find(key).name, store.find(key).role) == ("Ana", "engineer")
    assert key not in (tmp_path / "credentials.json").read_text(encoding="utf-8")
    assert "http://localhost:3000" in os.environ["TRANSLUCENT_CORS_ORIGINS"].split(",")
    _team_server(tmp_path, "Ana")                             # a restart keeps the key the web app has
    assert (tmp_path / "team-key").read_text(encoding="utf-8").strip() == key and store.find(key).name == "Ana"
    assert key not in capsys.readouterr().out and len(store.list()) == 1
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("TRANSLUCENT_TEAM_TOKEN", "chosen-team-key-0123456789-32chars-minimum")
    monkeypatch.setenv("TRANSLUCENT_CORS_ORIGINS", "https://lab.example")
    _team_server(other, "Ana")
    assert os.environ["TRANSLUCENT_TEAM_TOKEN"] == "chosen-team-key-0123456789-32chars-minimum"
    assert os.environ["TRANSLUCENT_CORS_ORIGINS"] == "https://lab.example"
    assert not (other / "team-key").exists() and not (other / "credentials.json").exists()
    assert "chosen-team-key-0123456789-32chars-minimum" not in capsys.readouterr().out
