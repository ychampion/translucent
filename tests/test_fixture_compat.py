"""Recorded evidence keeps re-checking the same way.

Every exported fixture (web/public/fixtures/*/ with a verification.json) and every checked-in ledger (runs/*/) is
verified, and its summary line and problem list must equal the ones recorded in tests/data/verify-golden.json. A
change to the gate, the ledger schema, the contracts or verify that alters how old evidence re-checks fails here.
"""
import json
from pathlib import Path

import pytest

from translucent.verify import verify

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = json.loads((ROOT / "tests" / "data" / "verify-golden.json").read_text(encoding="utf-8"))


def _targets():
    found = sorted(p.parent for p in (ROOT / "web" / "public" / "fixtures").glob("*/verification.json"))
    found += sorted(p.parent for p in (ROOT / "runs").glob("*/ledger.sqlite"))
    return [p.relative_to(ROOT).as_posix() for p in found]


def test_every_fixture_has_a_recorded_result():
    assert sorted(_targets()) == sorted(GOLDEN)


@pytest.mark.parametrize("target", sorted(GOLDEN))
def test_fixture_rechecks_unchanged(target):
    path = ROOT / target
    if not path.exists():
        pytest.skip(f"{target} is not in this checkout")
    result = verify(path)
    assert result.summary() == GOLDEN[target]["summary"]
    assert result.problems == GOLDEN[target]["problems"]
