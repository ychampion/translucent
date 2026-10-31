"""Findings re-check with the gate rules they were recorded under (translucent.past_gates)."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import time

import pytest

from translucent import gate, past_gates
from translucent.contracts import Campaign, Claim, Measurement, Predicate, Run, Source, Uncertainty
from translucent.ledger import SCHEMA, Ledger
from translucent.verify import verify

ROOT = Path(__file__).resolve().parents[1]


def _sha(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_frozen_copies_are_the_recorded_bytes_and_the_last_row_is_todays_gate():
    """Changing translucent/gate.py without freezing the old one here would strand every finding it recorded."""
    *past, (_, _, current) = past_gates.VERSIONS
    assert _sha(ROOT / "translucent" / "gate.py") == current, (
        "gate.py changed: copy its previous version to translucent/past_gates/gate_<commit>.py and add a row")
    for commit, _, sha in past:
        assert _sha(ROOT / "translucent" / "past_gates" / f"gate_{commit}.py") == sha
    assert [at for _, at, _ in past_gates.VERSIONS] == sorted(at for _, at, _ in past_gates.VERSIONS)
    assert past_gates.in_force(past_gates.VERSIONS[0][1] - 1) is None
    assert past_gates.in_force(past_gates.VERSIONS[1][1]) == past_gates.VERSIONS[1][0]


def _ledger(tmp_path, recorded_with):
    """A finding the first gate supported and today's gate does not: 10.21 +/- 10 in [0, 20.42] (fixed in aefb44d).
    Its campaign's setup record names the gate code `recorded_with` reviewed it with."""
    L = Ledger(tmp_path / "ledger")
    art = L.put_bytes(b"recording", kind="video", media_type="video/mp4", source=Source(uri="fixture:recording"))
    L.add(Campaign(id="c", objective="Check", title="Fixture", inputs=[art.id], budget_usd=0, created_at=time.time()))
    setup = L.put_json({"code": {"files": {"gate.py": recorded_with}}}, kind="report", label="Setup")
    L.emit("c", "campaign.started", "runtime", "Started", refs=[setup.id], data={"setup_id": setup.id})
    L.add(Run(id="r", campaign_id="c", step_id="s", tool="read_cell_voltage", idempotency_key="r",
              input_ids=[art.id], status="ok", started_at=0, ended_at=0))
    L.add(Measurement(id="m", run_id="r", quantity="voltage", value=10.21, unit="V", artifact_ids=[art.id],
                      uncertainty=Uncertainty(value=10, kind="bound", method="fixture")))
    L.add(Claim(id="k", campaign_id="c", author="fixture", statement="About 10 V.", created_at=time.time(),
                predicate=Predicate(type="value_within", params={"lo": 0, "hi": 20.42, "unit": "V"},
                                    measurement_ids=["m"])))
    return L


def test_a_finding_rechecks_with_the_gate_its_setup_names(tmp_path):
    first, _, first_sha = past_gates.VERSIONS[0]
    L = _ledger(tmp_path, first_sha)
    assert past_gates.module(first).review(L, "k").verdict == "supported"
    L.emit("c", "campaign.finished", "runtime", "Done")
    L.db.close()
    result = verify(L.root)
    assert result.ok, result.problems
    assert result.notes == [f"Re-checked with the gate code the findings were recorded with ({first_sha[:12]})",
                            f"Finding k: supported under the gate it was recorded with ({first}); "
                            "today's gate gives inconclusive"]


def test_naming_todays_gate_does_not_excuse_an_old_verdict(tmp_path):
    first, _, _ = past_gates.VERSIONS[0]
    L = _ledger(tmp_path, past_gates.VERSIONS[-1][2])
    past_gates.module(first).review(L, "k")  # reviewed by other rules than the setup claims
    L.db.close()
    result = verify(L.root)
    assert result.problems == ["Finding k: recorded supported, re-check gives inconclusive; "
                               "verdict, checks or reason differ"]


@pytest.mark.skipif(not (ROOT / "runs" / "fw-chain6" / "ledger.sqlite").exists(), reason="needs runs/fw-chain6")
def test_committed_ledger_verifies_and_says_where_todays_gate_differs():
    result = verify(ROOT / "runs" / "fw-chain6")
    assert result.ok and result.summary() == "12 findings re-checked, 12 match; 16 files intact"
    assert ("Finding claim_336515bdae: supported under the gate it was recorded with (185894a); "
            "today's gate gives inconclusive") in result.notes


@pytest.mark.skipif(not (ROOT / "runs" / "fw-chain6" / "ledger.sqlite").exists(), reason="needs runs/fw-chain6")
def test_a_rewritten_verdict_in_an_old_ledger_is_still_caught(tmp_path):
    copy = tmp_path / "fw-chain6"
    shutil.copytree(ROOT / "runs" / "fw-chain6", copy, ignore=shutil.ignore_patterns("*-wal", "*-shm"))
    db = sqlite3.connect(copy / "ledger.sqlite")
    triggers = re.findall(r"CREATE TRIGGER.*?END;", SCHEMA)
    for name in ("record_no_update", "event_no_update"):
        db.execute(f"DROP TRIGGER {name}")
    for table in ("record", "event"):  # the review and its claim.reviewed event, so only the gate can tell
        db.execute(f"UPDATE {table} SET body=replace(body, '\"verdict\":\"supported\"', '\"verdict\":\"refuted\"') "
                   "WHERE body LIKE '%claim_336515bdae%' AND body LIKE '%\"reviewer\":\"gate\"%'")
    for sql in triggers:
        db.execute(sql)
    db.commit()
    db.close()
    result = verify(copy)
    assert "Finding claim_336515bdae: recorded refuted, re-check gives supported; verdict, checks or reason differ" \
        in result.problems
