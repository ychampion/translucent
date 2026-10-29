import json
import shutil

import pytest

from evals import critic_eval as ce
from translucent.runtime import critic_verdict


@pytest.mark.parametrize("text,want", [
    ("SUPPORTED\nThe drop is at the mark.", "supported"),
    ("supported", "supported"),
    ("**SUPPORTED**\nVisible at 90 s.", "supported"),
    ("Verdict: SUPPORTED. The line sits on the drop.", "supported"),
    ("SUPPORTED - the voltage reaches 0 V there.", "supported"),
    ("REFUTED\nThe drop is at 90 s, not 180 s.", "refuted"),
    ("**REFUTED**: wrong time.", "refuted"),
    ("Verdict: refuted - not what the image shows.", "refuted"),
    ("INCONCLUSIVE\nTemperature is not shown.", "inconclusive"),
    # fail closed: none of these may read as agreement
    ("Not supported", "inconclusive"),
    ("Not supported.\nThe mark is on a flat line.", "inconclusive"),
    ("NOT SUPPORTED", "inconclusive"),
    ("UNSUPPORTED", "inconclusive"),
    ("I can't support this.", "inconclusive"),
    ("SUPPORTED? No.", "inconclusive"),
    ("SUPPORTED ? no, the drop is earlier", "inconclusive"),
    ("SUPPORTED - no, the drop is earlier.", "inconclusive"),
    ("SUPPORTED, but only partly: the drop is earlier.", "inconclusive"),
    ("SUPPORTED: the image does not support the timing.", "inconclusive"),
    ("SUPPORTED or REFUTED, hard to say.", "inconclusive"),
    ("SUPPORTED/REFUTED", "inconclusive"),
    ("Supportedly true", "inconclusive"),
    ("Looking at the plot carefully.\nSUPPORTED", "inconclusive"),
    ("The finding is supported by the image.", "inconclusive"),
    ("", "inconclusive"),
    (None, "inconclusive"),
    ("<|channel|>SUPPORTED", "inconclusive"),
])
def test_critic_verdict_fails_closed(text, want):
    assert critic_verdict(text)[0] == want


def test_critic_verdict_reason():
    assert critic_verdict("SUPPORTED\nThe drop is at 90 s.")[1] == "The drop is at 90 s."
    assert critic_verdict("REFUTED: wrong time.")[1] == "wrong time."
    assert critic_verdict("Not supported")[1] == "Not supported"


@pytest.mark.parametrize("text", [
    "Supported (partially): the time is uncertain.",
    "Supported only if the clock is right.",
    "Supported: the image does not show this event.",
    "SUPPORTED - although the voltage does not drop at the mark, so this is wrong.",
    "SUPPORTED\nOn reflection, REFUTED: the change is elsewhere.",
    "SUPPORTED\nThe image doesn't show this event.",
    "SUPPORTED\nOnly if the clock is right.",
    "REFUTED\nFinal answer: SUPPORTED.",
])
def test_critic_rejects_hedges_and_later_verdicts(text):
    assert critic_verdict(text)[0] == "inconclusive"


def test_manifest_loads_seeded_cases():
    cases = ce.load_cases()
    assert 12 <= len(cases) <= 16
    exp = [c["expected"] for c in cases]
    assert exp.count("supported") >= 4 and exp.count("refuted") >= 6 and exp.count("inconclusive") >= 1
    assert all(c["path"].is_file() and c["why"] for c in cases)
    assert {c["id"] for c in cases} == {s[0] for s in ce.SEEDS}


def test_manifest_rejects_bad_cases(tmp_path):
    src = ce.load_cases()[0]
    shutil.copy(src["path"], tmp_path / src["image"])
    good = {k: src[k] for k in ("id", "statement", "image", "expected", "why")}
    for bad, msg in [({**good, "expected": "agrees"}, "expected"), ({**good, "image": "nope.png"}, "not found"),
                     ({k: v for k, v in good.items() if k != "why"}, "missing")]:
        (tmp_path / "manifest.json").write_text(json.dumps({"cases": [bad]}))
        with pytest.raises(ValueError, match=msg):
            ce.load_cases(tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps({"cases": [good, good]}))
    with pytest.raises(ValueError, match="duplicate"):
        ce.load_cases(tmp_path)


def test_score():
    rows = [{"expected": "supported", "verdict": "supported", "usd": 0.01},
            {"expected": "supported", "verdict": "inconclusive", "usd": 0.01},
            {"expected": "refuted", "verdict": "supported", "usd": 0.01},
            {"expected": "refuted", "verdict": "refuted", "usd": 0.01},
            {"expected": "inconclusive", "verdict": "error", "usd": 0.0}]
    s = ce.score(rows)
    assert s["agreement"] == 0.4 and s["false_agree"] == 0.333 and s["wrong_findings"] == 3
    assert s["unclear"] == 0.4 and s["errors"] == 1 and s["missed_correct"] == 0.5
    assert s["usd_total"] == 0.04 and s["usd_per_run"] == 0.008


def test_offline_critics():
    from evals.fake_llm import FakeClient
    cases = ce.load_cases()
    good = ce.score(ce.run(cases, FakeClient(ce.scripted(cases)), 2, log=lambda *_: None))
    assert good["runs"] == 2 * len(cases) and good["agreement"] == 1.0 and good["false_agree"] == 0.0
    yes = ce.score(ce.run(cases, FakeClient(ce.always_agree(cases)), log=lambda *_: None))
    assert yes["false_agree"] == 1.0


def test_real_refuses_over_budget_before_any_call(monkeypatch, capsys):
    import translucent.providers as p

    def boom():
        raise AssertionError("a paid client was built")
    monkeypatch.setattr(p, "MeteredFireworks", boom)
    est = ce.estimate_usd(ce.load_cases(), 3)
    assert est > 0
    assert ce.main(["--real", "--repeats", "3", "--budget-usd", str(est / 2)]) == 2
    assert "refusing to start" in capsys.readouterr().out
    assert ce.main(["--real"]) == 2  # no budget given: refuse too
    for budget in ("nan", "inf", "-1", "0"):
        assert ce.main(["--real", "--budget-usd", budget]) == 2


def test_real_runs_through_metered_client_when_in_budget(monkeypatch, tmp_path):
    import translucent.providers as p
    from evals.fake_llm import FakeClient, constant
    fake = FakeClient(constant("REFUTED\nno"), usd_per_call=0.001)
    monkeypatch.setattr(p, "MeteredFireworks", lambda: fake)
    out = tmp_path / "r.json"
    assert ce.main(["--real", "--budget-usd", "100", "--out", str(out)]) == 0
    s = json.loads(out.read_text())["real"]["score"]
    assert s["runs"] == len(ce.load_cases()) and s["false_agree"] == 0.0 and s["usd_per_run"] == 0.001
    assert fake.calls[0]["model"] == ce.PROFILES["dev"]["critic"][1]
    saved = [json.loads(line) for line in out.with_suffix(".jsonl").read_text().splitlines()]
    assert len(saved) == len(ce.load_cases()) and all(row["critic"] == "real" for row in saved)


def test_estimate_includes_automatic_retry_attempts():
    from openai import DEFAULT_MAX_RETRIES
    cases = ce.load_cases()[:1]
    once = ce.estimate_usd(cases, 1, max_retries=0)
    assert ce.estimate_usd(cases, 2) == pytest.approx(once * 2 * (1 + DEFAULT_MAX_RETRIES))


def test_run_stops_before_next_worst_case_exceeds_own_budget():
    from evals.fake_llm import FakeClient, constant
    cases = ce.load_cases()[:1]
    worst = ce.estimate_usd(cases, 1, max_retries=0)
    fake = FakeClient(constant("SUPPORTED\nVisible."), usd_per_call=worst / 2)
    fake.spent = 5.0  # unrelated committed spend does not consume this run's budget
    logs = []
    rows = ce.run(cases, fake, 3, budget_usd=worst * 1.25, log=logs.append)
    assert len(rows) == len(fake.calls) == 1
    assert rows[0]["usd"] == pytest.approx(worst / 2)
    assert "budget" in logs[-1] and "stopped" in logs[-1]


@pytest.mark.parametrize("budget", [float("nan"), float("inf"), -1.0, 0.0])
def test_invalid_budget_never_starts_a_call(budget):
    from evals.fake_llm import FakeClient, constant
    fake = FakeClient(constant("SUPPORTED\nVisible."))
    with pytest.raises(ValueError, match="budget"):
        ce.run(ce.load_cases()[:1], fake, budget_usd=budget, log=lambda *_: None)
    assert not fake.calls


@pytest.mark.parametrize("repeats", [0, -1])
def test_nonpositive_repeats_never_starts_or_builds_a_paid_client(monkeypatch, repeats):
    import translucent.providers as p
    from evals.fake_llm import FakeClient, constant

    def boom():
        raise AssertionError("a paid client was built")

    monkeypatch.setattr(p, "MeteredFireworks", boom)
    fake = FakeClient(constant("SUPPORTED\nVisible."))
    with pytest.raises(ValueError, match="repeats"):
        ce.run(ce.load_cases()[:1], fake, repeats, log=lambda *_: None)
    assert not fake.calls
    assert ce.main(["--real", "--budget-usd", "100", "--repeats", str(repeats)]) == 2


def test_eval_disables_unreserved_sdk_retries():
    from types import SimpleNamespace
    from evals.fake_llm import FakeClient, constant
    fake = FakeClient(constant("SUPPORTED\nVisible."))
    fake._client = SimpleNamespace(max_retries=2)
    fake._client.with_options = lambda **kw: SimpleNamespace(**kw)
    ce.run(ce.load_cases()[:1], fake, log=lambda *_: None)
    assert fake._client.max_retries == 0


def test_each_result_survives_interruption_before_next_result(tmp_path):
    from evals.fake_llm import FakeClient
    journal = tmp_path / "runs.jsonl"
    calls = []

    def policy(messages, tools):
        calls.append(1)
        if len(calls) > 1:
            assert len(journal.read_text().splitlines()) == 1
            raise KeyboardInterrupt
        return "SUPPORTED\nVisible.", []

    with pytest.raises(KeyboardInterrupt):
        ce.run(ce.load_cases()[:2], FakeClient(policy, usd_per_call=0.001), journal=journal,
               log=lambda *_: None)
    saved = [json.loads(line) for line in journal.read_text().splitlines()]
    assert len(saved) == 1 and saved[0]["case"] == ce.load_cases()[0]["id"]
    assert saved[0]["usd"] == 0.001 and saved[0]["verdict"] == "supported"


def test_failed_calls_keep_cost_and_stop_at_own_budget(tmp_path):
    from evals.fake_llm import FakeClient
    cases = ce.load_cases()[:1]
    worst = ce.estimate_usd(cases, 1, max_retries=0)

    def timeout(messages, tools):
        raise TimeoutError("no response; reservation kept")

    fake = FakeClient(timeout, usd_per_call=worst / 2)
    journal = tmp_path / "failures.jsonl"
    rows = ce.run(cases, fake, 3, budget_usd=worst * 1.25, journal=journal, log=lambda *_: None)
    assert len(rows) == 1 and rows[0]["verdict"] == "error"
    saved = json.loads(journal.read_text())
    assert saved["usd"] == pytest.approx(worst / 2) and "TimeoutError" in saved["reason"]
