"""Every executable/library entry activates new claims without rewriting the old checker."""
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
LEGACY_GATE_SHA256 = "4245d5a3599bd946e790de43910a36fb92e244de32b2d95aa2e34ecef1cac8c9"


@pytest.mark.parametrize("entry, command", [
    pytest.param("translucent.cli", "run", id="cli-run"),
    pytest.param("translucent.cli", "bench", id="cli-bench"),
    pytest.param("translucent.cli", "verify", id="cli-verify"),
    pytest.param("translucent.cli", "export", id="cli-export"),
    pytest.param("translucent.server", "", id="server"),
    pytest.param("translucent.mcp_server", "", id="mcp"),
    pytest.param("translucent.runtime", "", id="runtime"),
    pytest.param("translucent.verify", "", id="verify-library"),
])
def test_entrypoint_installs_dispatch_before_loading_intervention_records(tmp_path, entry, command):
    # Each process begins with the original checker, without importing the
    # intervention controller through a helper before the entrypoint under test.
    program = r'''
import contextlib
import importlib
import io
from pathlib import Path
import sys

from translucent import gate
from translucent.contracts import Campaign, Claim, Predicate
from translucent.ledger import Ledger

assert not getattr(gate.review, "_intervention_dispatch", False)
assert "translucent.intervention" not in sys.modules
original = gate.review
ledger = Ledger(Path(sys.argv[2]) / "ledger")
legacy = Campaign(id="legacy", objective="Check an unmeasured value", title="Legacy", inputs=[],
                  memory="off", budget_usd=0, created_at=0)
ledger.add(legacy)
old_claim = Claim(id="old_claim", campaign_id=legacy.id, author="test", statement="A value exists",
                  predicate=Predicate(type="value_within", params={"lo": 0, "hi": 1, "unit": "V"}, measurement_ids=[]), created_at=0)
ledger.add(old_claim)
before = original(ledger, old_claim.id)

entry = importlib.import_module(sys.argv[1])
assert getattr(gate.review, "_intervention_dispatch", False)
assert gate.review is not original
# The CLI installs the shared hook before parsing any command. Parsing each
# actual command's help exits before opening a provider or starting work.
if sys.argv[3]:
    assert callable(entry.main)
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            entry.main([sys.argv[3], "--help"])
        except SystemExit as stopped:
            assert stopped.code == 0
        else:
            raise AssertionError("CLI help did not stop before execution")
from translucent.intervention_gate import install
dispatcher = gate.review
assert install() is dispatcher
assert gate.review is dispatcher
after = gate.review(ledger, old_claim.id)
assert (after.verdict, after.checks, after.reason) == (before.verdict, before.checks, before.reason)

# Only after proving activation do we load the intervention controller and build
# a real factual claim. No provider or dataset file participates in this test.
from translucent.intervention import (create, propose_policy, record, register_fold, fit_policy,
                                      commit_decision, seal)
from translucent.intervention_bench import submit_policy, propose_claim
from translucent.intervention_models import PolicySpec
campaign = create(ledger, min_trials=1, splits=["loco"])
artifact = ledger.put_json({"t": list(range(10)), "temperature_C": list(range(20, 30))}, kind="series")
policy = propose_policy(ledger, campaign.id, "fixed escalation", PolicySpec(family="constant"), kind="baseline")
submit_policy(ledger, campaign.id, policy.id)
trial = record(ledger, campaign.id, "trial", {
    "scope": "test", "trial_key": "Exp1", "exp": 1, "condition": {"format": "18650", "soc": 50},
    "series_artifact": artifact.id, "t_heater_on": 0, "t_vent": 3, "t_heater_off": 5,
    "delay_after_vent_s": 2, "flags": [], "event_sources": {}, "sealed_hash": seal("ignited", "test-only-salt"),
})
fold = register_fold(ledger, campaign.id, "test", "loco", [], [trial.id])
fitted = fit_policy(ledger, campaign.id, policy.id, fold.id)
commit_decision(ledger, campaign.id, trial.id, policy.id, "escalate", 1, [0, 1], fit_id=fitted.id)
record(ledger, campaign.id, "reveal", {"trial_id": trial.id, "outcome": "ignited", "salt": "test-only-salt",
    "t_ignition": 8, "flags": [], "simulated": False})
claim = propose_claim(ledger, campaign.id, {"type": "policy_performance", "policy_id": policy.id,
    "split": "loco", "n": 1, "unsafe": 0})
review = gate.review(ledger, claim.id)
assert review.type == "review" and review.data["verdict"] == "supported"
print("intervention supported; legacy verdict unchanged; dispatcher idempotent")
'''
    checked = subprocess.run(
        [sys.executable, "-c", program, entry, str(tmp_path), command],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert checked.stdout.strip() == "intervention supported; legacy verdict unchanged; dispatcher idempotent"


def test_original_gate_source_remains_the_recorded_checker():
    # A Windows checkout with core.autocrlf has CRLF line endings; the recorded hash is of the LF source.
    source = (ROOT / "translucent/gate.py").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(source).hexdigest() == LEGACY_GATE_SHA256
