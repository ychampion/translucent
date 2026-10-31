"""`translucent init` and `translucent import`: a team's ledgers and records from the command line."""
from pathlib import Path

import pytest

from translucent import cli
from translucent.ledger import Ledger

MANIFEST = Path(__file__).parent / "data" / "import_v2" / "manifest.json"


def test_init_creates_a_ledger_once_and_import_reads_a_team_manifest(tmp_path, capsys):
    root = tmp_path / "root"
    assert cli.main(["init", "acme", "--data-root", str(root), "--title", "ACME cells"]) == 0
    with pytest.raises(SystemExit):
        cli.main(["init", "acme", "--data-root", str(root)])
    ledger = root / "ledgers" / "acme"
    assert cli.main(["import", str(MANIFEST), "--ledger", str(ledger)]) == 0
    assert "Imported dataset fastcharge-screen-2026q3: 3 record(s)" in capsys.readouterr().out
    assert [d.id for d in Ledger(ledger).list("dataset")] == ["fastcharge-screen-2026q3"]
    assert cli.main(["import", str(MANIFEST), "--ledger", str(ledger)]) == 0     # same files: the stored dataset


def test_import_refuses_a_missing_ledger_and_reports_a_bad_manifest(tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli.main(["import", str(MANIFEST), "--ledger", str(tmp_path / "typo")])
    assert not (tmp_path / "typo").exists()
    cli.main(["init", "acme", "--data-root", str(tmp_path)])
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 2, "id": "x"}', encoding="utf-8")
    assert cli.main(["import", str(bad), "--ledger", str(tmp_path / "ledgers" / "acme")]) == 1
    assert "Not imported:" in capsys.readouterr().err


def test_serve_takes_a_data_root_or_a_ledger_not_both(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["serve", "--data-root", str(tmp_path), "--ledger", str(tmp_path)])
    with pytest.raises(SystemExit):
        cli.main(["serve", "--data-root", str(tmp_path / "missing")])
