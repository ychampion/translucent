import json
import re
from types import SimpleNamespace

import pytest

from evals import critic_eval, faults, llm_judge as lj
from evals.fake_llm import FakeClient, constant
from evals.run import no_network


@pytest.fixture(scope="module")
def captured(tmp_path_factory):
    with no_network():
        return lj.capture_suite(tmp_path_factory.mktemp("judge"), log=lambda *_: None)


def test_every_prompt_has_no_answer_or_case_leaks(captured):
    cases = lj.build_cases(captured)
    assert {c["variant"] for c in cases} == {"numbers", "full"}
    for c in cases:
        text = json.dumps(c["statement"]).lower()
        assert not re.search(r"\b(fault|planted|injected|gate|reviewer|verdict|checks)\b", text)
        for source in faults.CASES:
            for field in ("id", "variant", "description"):
                assert source[field].lower() not in text
        assert "Should this finding be accepted as supported by the evidence below?" in c["statement"][0]["content"]
        assert "image_url" not in text
    full = next(c for c in cases if c["source_id"] == "scale_si_bar" and c["variant"] == "full")
    text = json.dumps(full["statement"])
    for field in ("calibration_ids", "input_ids", "measurement_ids", "experiment_ids", "time_base_source"):
        assert field in text
    assert "253.1" in text and "161.2" in text


@pytest.mark.parametrize("raw,want", [
    ("SUPPORTED", "supported"), ("**REFUTED**: Different value.", "refuted"),
    (None, "inconclusive"), ("", "inconclusive"), ("Not supported", "inconclusive"),
    ("SUPPORTED? No.", "inconclusive"), ("SUPPORTED or REFUTED", "inconclusive"),
    ("SUPPORTED\nOnly if the clock is right.", "inconclusive"),
    ("Some thoughts\nSUPPORTED", "inconclusive"), ("SUPPORTED\nREFUTED", "inconclusive"),
])
def test_parser_fails_closed(raw, want):
    assert lj.critic_verdict(raw)[0] == want


def test_capture_preserves_original_results_and_controls(captured):
    rows = captured["cases"]
    assert len(rows) == 38 and not any(c["error"] for c in rows)
    assert sum(c["translucent"]["caught"] for c in rows) == 38
    assert sum(c["baseline"]["caught"] for c in rows) == 1
    assert len(captured["controls"]) == 2
    assert "6.200 ± 0.018" in captured["controls"][0]["result"]["translucent"]
    for c in captured["controls"]:
        assert len(c["findings"]) == 1
    for c in rows:
        if c["id"].startswith("retract_"):
            assert c["findings"][0]["invalidations"]
            assert not c["findings"][0]["invalidations_at_proposal"]
    assert next(c for c in rows if c["id"] == "claim_cites_nonexistent")["findings"]
    assert not next(c for c in rows if c["id"] == "scale_via_sandbox")["findings"]


def test_proposal_metadata_is_not_evidence_and_numbers_match(captured):
    f = captured["controls"][0]["findings"][0]
    changed = {**f, "claim_id": "planted", "campaign_id": "fault", "review": {"reason": "injected"}}
    for variant in lj.VARIANTS:
        assert lj.messages(changed, variant) == lj.messages(f, variant)
    numbers, full = (lj.evidence(f, v) for v in lj.VARIANTS)
    assert numbers == {k: full[k] for k in numbers}


def test_scripted_and_always_accept_complete_all_available_cases(captured, tmp_path):
    cases = lj.build_cases(captured)
    for name in ("scripted", "accept"):
        journal = tmp_path / f"{name}.jsonl"
        with no_network():
            rows = lj.run(cases, FakeClient(lj.OFFLINE[name](cases)), 3, journal=journal, label=name,
                          log=lambda *_: None)
        assert len(rows) == 3 * len(cases)
        assert len(journal.read_text().splitlines()) == len(rows)
        score = lj.score(rows, captured, cases, 3)
        for variant in ("numbers", "full"):
            s = score[variant]
            assert s["overall"]["cases"] == 38
            assert s["data_path"]["cases"] == 24 and s["ledger_only"]["cases"] == 14
            assert s["controls"]["false_rejections"] == 0
            assert s["controls"]["accepted"] == 2
            assert s["overall"]["not_proposed"] > 0
            assert s["overall"]["incomplete"] == 0
            assert s["overall"]["waved_through"] == (0 if name == "scripted" else s["overall"]["judged"])
            assert s["overall"]["caught"] == (s["overall"]["judged"] if name == "scripted" else 0)
            assert len(s["per_repeat"]) == 3
            assert len(s["cases"]) == 40  # 38 faults + 2 controls


def tiny():
    suite = {"cases": [{"id": "example", "fault_class": "time base", "via": "tool", "error": None,
                         "translucent": {"caught": True}, "baseline": {"caught": False}, "findings": [{}]}],
             "controls": []}
    cases = [{"id": "example/numbers/0", "source_id": "example", "variant": "numbers", "expected": "refuted",
              "statement": [{"role": "user", "content": "Review the supplied measurements."}], "path": None}]
    return suite, cases


def test_majority_and_incomplete_runs():
    suite, cases = tiny()
    rows = [{"case": cases[0]["id"], "repeat": i, "verdict": v, "usd": 0} for i, v in
            enumerate(("supported", "refuted", "inconclusive"))]
    s = lj.score(rows, suite, cases, 3)["numbers"]
    assert s["overall"]["caught"] == 1 and s["overall"]["waved_through"] == 0
    assert s["per_repeat"][0]["agreement"] == 0 and s["per_repeat"][1]["agreement"] == 1
    assert s["agreement"]["unanimous_cases"] == 0
    s = lj.score(rows[:1], suite, cases, 3)["numbers"]["overall"]
    assert s["incomplete"] == 1 and s["caught"] == s["waved_through"] == 0
    rows[1]["verdict"] = "error"
    s = lj.score(rows, suite, cases, 3)["numbers"]["overall"]
    assert s["caught"] == 1 and s["call_errors"] == 1
    s = lj.score(rows[:2], suite, cases, 2)["numbers"]["overall"]
    assert s["caught"] == 1  # one acceptance and one non-acceptance: no strict majority


def test_several_findings_do_not_multiply_case_denominator():
    suite, cases = tiny()
    cases.append({**cases[0], "id": "example/numbers/1"})
    rows = [{"case": c["id"], "repeat": r, "verdict": "supported" if i == r else "refuted", "usd": 0}
            for i, c in enumerate(cases) for r in range(3)]
    s = lj.score(rows, suite, cases, 3)["numbers"]
    assert s["overall"]["judged"] == s["overall"]["waved_through"] == 1
    assert s["cases"][0]["accepted_per_repeat"] == [True, True, False]


def test_budget_refusal_and_shared_retry_estimate(monkeypatch, capsys):
    import translucent.providers as providers
    from openai import DEFAULT_MAX_RETRIES
    suite, cases = tiny()
    def capture(*a, log=print):
        log("capture progress")
        return suite
    monkeypatch.setattr(lj, "capture_suite", capture)
    monkeypatch.setattr(lj, "build_cases", lambda *a: cases)
    monkeypatch.setattr(providers, "MeteredFireworks", lambda: pytest.fail("paid client constructed"))
    estimate = lj.estimate_usd(cases, 3)
    assert estimate == pytest.approx(lj.estimate_usd(cases, 1, max_retries=0) * 3 * (1 + DEFAULT_MAX_RETRIES))
    assert lj.main(["--real", "--repeats", "3", "--budget-usd", str(estimate / 2)]) == 2
    output = capsys.readouterr().out
    assert "worst-case" in output.splitlines()[0] and "capture progress" not in output
    assert lj.main(["--real"]) == 2
    for budget in ("nan", "inf", "-1", "0"):
        assert lj.main(["--real", "--budget-usd", budget]) == 2


def test_budget_stop_errors_and_journal_use_shared_loop(tmp_path):
    _, cases = tiny()
    worst = lj.estimate_usd(cases, 1, max_retries=0)
    def timeout(messages, tools):
        raise TimeoutError("no response")
    fake = FakeClient(timeout, usd_per_call=worst / 2)
    fake.spent = 4
    fake._client = SimpleNamespace(with_options=lambda **kw: SimpleNamespace(**kw))
    original = critic_eval.critic_messages
    journal = tmp_path / "timeout.jsonl"
    rows = lj.run(cases, fake, 3, budget_usd=worst * 1.25, journal=journal, log=lambda *_: None)
    assert len(rows) == 1 and rows[0]["verdict"] == "error"
    assert rows[0]["usd"] == pytest.approx(worst / 2)
    assert fake._client.max_retries == 0 and critic_eval.critic_messages is original
    assert json.loads(journal.read_text())["verdict"] == "error"


def test_authorized_path_uses_metered_client_with_fake_transport(monkeypatch, tmp_path):
    import translucent.providers as providers
    suite, cases = tiny()
    monkeypatch.setattr(lj, "capture_suite", lambda *a, **kw: suite)
    monkeypatch.setattr(lj, "build_cases", lambda *a: cases)
    seen = []
    def policy(messages, tools):
        seen.append(messages)
        return "REFUTED", []
    fake = FakeClient(policy, usd_per_call=0.001)
    monkeypatch.setattr(providers, "MeteredFireworks", lambda: fake)
    out = tmp_path / "metered.json"
    with no_network():
        assert lj.main(["--real", "--budget-usd", "1", "--out", str(out)]) == 0
    assert seen == [cases[0]["statement"]]
    assert fake.calls[0]["model"] == lj.PROFILES["dev"]["critic"][1]
    assert json.loads(out.with_suffix(".jsonl").read_text())["usd"] == 0.001


def test_cli_writes_json_journal_and_generated_summary(monkeypatch, captured, tmp_path):
    monkeypatch.setattr(lj, "capture_suite", lambda *a, **kw: captured)
    out, report = tmp_path / "results.json", tmp_path / "summary.md"
    with no_network():
        assert lj.main(["--out", str(out), "--summary", str(report), "--repeats", "2"]) == 0
    res = json.loads(out.read_text())
    assert set(res["judges"]) == {"scripted", "accept"}
    assert len(out.with_suffix(".jsonl").read_text().splitlines()) == sum(len(v["rows"]) for v in res["judges"].values())
    assert "Harness checks" in report.read_text()
    assert res["images"] == "none"
