"""`translucent start fastcharge`: from an empty folder to a concluded, verified campaign that the server runs alone.

The first test needs only the LG M50 records (fetched with scripts/fetch_lgm50_data.py; they are git-ignored). The
second runs the solver about nine times (about 2 minutes): opt in with TRANSLUCENT_SOLVER_TESTS=1. No model is
called.
"""
import os
import shutil
import time

import pytest
from fastapi.testclient import TestClient

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
        assert [s.proposed_by for s in request_states(L, cid).values()] == ["policy:registration"]
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


@pytest.mark.skipif(os.environ.get("TRANSLUCENT_SOLVER_TESTS") != "1", reason="runs the solver about nine times")
def test_the_server_runs_a_started_bo_campaign_to_a_verified_conclusion(tmp_path):
    root = tmp_path / "data"
    out = start_fastcharge(root, person="Ana", planner="bo", auto_approve=True)
    cid = out["campaign_id"]
    with TestClient(server.create_app(data_root=root, poll_interval=0.01, loop_workers=1)) as c:
        deadline, view = time.time() + 540, None
        while time.time() < deadline:
            view = c.get(f"/api/v1/ledgers/lab/campaigns/{cid}/view").json()
            if view.get("conclusion"):
                break
            time.sleep(0.5)
        assert view and view.get("conclusion"), view and [r["state"] for r in view["requests"]]
    assert view["conclusion"]["outcome"] == "improved_in_simulation", view["conclusion"]
    done = [r for r in view["requests"] if r["state"] == "completed" and r["proposed_by"] != "policy:registration"]
    assert 1 <= len(done) <= 8
    assert verify(root / "ledgers" / "lab").ok


def test_without_the_solver_nothing_is_written(tmp_path, monkeypatch):
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "pybamm" else real(name, *a))
    with pytest.raises(StartError, match="uv sync --group dev --group solver"):
        start_fastcharge(tmp_path / "data", person="Ana")
    assert not (tmp_path / "data").exists()


def test_a_team_server_has_a_key_and_admits_the_local_web_app(tmp_path, monkeypatch, capsys):
    from translucent.cli import _team_server
    for name in ("TRANSLUCENT_TEAM_TOKEN", "TRANSLUCENT_CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    _team_server(tmp_path)
    key = (tmp_path / "team-key").read_text(encoding="utf-8").strip()
    assert len(key) >= 20 and os.environ["TRANSLUCENT_TEAM_TOKEN"] == key and key in capsys.readouterr().out
    assert "http://localhost:3000" in os.environ["TRANSLUCENT_CORS_ORIGINS"].split(",")
    monkeypatch.delenv("TRANSLUCENT_TEAM_TOKEN")
    _team_server(tmp_path)                                    # a restart keeps the key the web app has
    assert os.environ["TRANSLUCENT_TEAM_TOKEN"] == key
    monkeypatch.setenv("TRANSLUCENT_TEAM_TOKEN", "chosen")
    monkeypatch.setenv("TRANSLUCENT_CORS_ORIGINS", "https://lab.example")
    _team_server(tmp_path)
    assert os.environ["TRANSLUCENT_TEAM_TOKEN"] == "chosen" and os.environ["TRANSLUCENT_CORS_ORIGINS"] == "https://lab.example"
    assert "chosen" not in capsys.readouterr().out
