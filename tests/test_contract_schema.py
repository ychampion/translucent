import json
from pathlib import Path

from translucent.contracts import CONTRACT_VERSION, ReplayBundle

ROOT = Path(__file__).resolve().parents[1]


def test_published_schema_matches_the_contracts():
    published = (ROOT / "docs" / "replay-bundle.schema.json").read_text(encoding="utf-8")
    assert json.loads(published) == ReplayBundle.model_json_schema()


def test_contracts_doc_names_the_current_version():
    assert f"Contract version: {CONTRACT_VERSION}" in (ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
