import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from translucent import analysis, tools
from translucent.contracts import Campaign, Measurement, Run, Uncertainty
from translucent.ledger import Ledger


@pytest.fixture
def ctx(tmp_path):
    ledger = Ledger(tmp_path / "ledger")
    ledger.add(Campaign(id="analysis_test", objective="Derive a number", title="Analysis", inputs=[],
                        budget_usd=0, created_at=0))
    ledger.add(Run(id="src", campaign_id="analysis_test", step_id="s", tool="fixture", idempotency_key="src", input_ids=[],
                   status="ok", started_at=0, ended_at=0))
    ledger.add(Measurement(id="len", run_id="src", quantity="filament_length", value=[1.0, 2.0, 3.0], unit="um",
                           uncertainty=Uncertainty(value=0.1, kind="std", method="fixture")))
    context = tools.Context(ledger, "analysis_test")
    yield context
    ledger.db.close()


@pytest.fixture
def fake(monkeypatch):
    calls = []
    state = SimpleNamespace(status="ok", outputs={"speed.json": b"2.5"}, reason="",
                            sandbox_id="sb-fake", terminated=True)
    state.depends = True  # the second (nudged-input) run moves its outputs, like code that reads its inputs

    def moved(raw):
        try:
            obj = json.loads(raw)
        except ValueError:
            return raw
        bump = lambda v: v * 1.001 if type(v) in (int, float) else v
        if isinstance(obj, dict):
            obj = {**obj, "value": bump(obj.get("value"))}
            if isinstance(obj.get("uncertainty"), dict):
                obj["uncertainty"] = {**obj["uncertainty"], "value": bump(obj["uncertainty"].get("value"))}
            return json.dumps(obj).encode()
        return json.dumps(bump(obj)).encode()

    def run(code, inputs, timeout_s):
        calls.append((code, inputs, timeout_s))
        if len(calls) % 2 == 0 and state.depends:
            return SimpleNamespace(**{**vars(state), "outputs": {k: moved(v) for k, v in state.outputs.items()}})
        return state
    monkeypatch.setattr(analysis, "run_untrusted", run)
    state.calls = calls
    return state


def invoke(ctx, **changes):
    args = {"code": "# analysis", "inputs": ["len"],
            "outputs": [{"name": "speed", "unit": "um/s", "label": "Growth speed"}]}
    args.update(changes)
    return tools.call(ctx, "run_analysis_code", args)


def assert_failure(ctx, result, before=1):  # the fixture records one input measurement
    assert result.status == "tool_failure", result.summary
    run = ctx.ledger.get("run", result.run_id)
    assert run.status == "tool_failure" and run.provider == "modal"
    assert len(ctx.ledger.list("measurement")) == before
    # the only thing a failed run keeps is the record of why it failed
    assert result.outputs == run.output_ids
    assert all(ctx.ledger.get("artifact", o).label == "Error trace" for o in run.output_ids)
    assert not any(e.type == "measurement.recorded" for e in ctx.ledger.events(ctx.campaign_id))
    assert isinstance(ctx.ledger, Ledger)  # adapter also removes itself on failure


def test_success_records_hash_provider_events_and_idempotency(ctx, fake):
    original = ctx.ledger
    result = invoke(ctx)
    assert result.status == "ok"
    run = ctx.ledger.get("run", result.run_id)
    m = ctx.ledger.get("measurement", result.outputs[0])
    assert run.code_sha == hashlib.sha256(b"# analysis").hexdigest()
    code = ctx.ledger.get("artifact", "sha256:" + run.code_sha)  # the code is kept, under the same hash
    assert code.kind == "code" and code.id in run.input_ids
    assert ctx.ledger.blob_path(code.id).read_bytes() == b"# analysis"
    assert run.provider == "modal" and run.provider_request_id == "sb-fake"
    assert m.quantity == "derived:speed" and m.value == 2.5 and m.unit == "um/s"
    assert m.label == "Growth speed" and m.uncertainty.kind == "none"
    assert m.run_id == run.id and ctx.ledger is original
    assert [e.type for e in original.events(ctx.campaign_id)] == [
        "tool.started", "measurement.recorded", "tool.finished"]
    assert invoke(ctx).run_id == run.id and len(fake.calls) == 2  # the run and its nudged-input check


def test_undeclared_output_rejected(ctx, fake):
    fake.outputs["forged.json"] = b"4"
    assert_failure(ctx, invoke(ctx))


def test_missing_output_rejected(ctx, fake):
    fake.outputs = {}
    assert_failure(ctx, invoke(ctx))


def test_oversize_output_rejected(ctx, fake):
    fake.outputs["speed.json"] = b" " * (analysis.OUTPUT_LIMIT + 1)
    assert_failure(ctx, invoke(ctx))


def test_aggregate_oversize_output_rejected(ctx, fake):
    specs = [{"name": f"n{i}", "unit": "1", "label": "Number"} for i in range(17)]
    fake.outputs = {f"n{i}.json": b"1" + b" " * (analysis.OUTPUT_LIMIT - 1) for i in range(17)}
    assert_failure(ctx, invoke(ctx, outputs=specs))


@pytest.mark.parametrize("status", ["timeout", "error", "rejected"])
def test_sandbox_failure_never_evidence(ctx, fake, status):
    fake.status, fake.reason = status, "defensive refusal"
    result = invoke(ctx)
    assert_failure(ctx, result)
    assert ctx.ledger.get("run", result.run_id).code_sha == hashlib.sha256(b"# analysis").hexdigest()


def test_raised_timeout_is_tool_failure(ctx, monkeypatch):
    def timeout(*args, **kwargs):
        raise TimeoutError("deadline")
    monkeypatch.setattr(analysis, "run_untrusted", timeout)
    assert_failure(ctx, invoke(ctx))


def test_unconfirmed_cleanup_is_not_evidence(ctx, fake):
    fake.terminated = False
    assert_failure(ctx, invoke(ctx))


@pytest.mark.parametrize("raw", [b"garbage", b"true", b'"2"', b"[1,2]", b"null", b"NaN", b"1e999",
    b' {"value":1,"value":2}', b'{"value":3,"unit":"forged"}', b'{"value":3,"run_id":"forged"}',
    b'{"uncertainty":{}}', b'{"value":false}', b'\x80\x04pickle', b'"\xff"',
    b'{"value":1,"uncertainty":{"kind":"std","value":-1,"method":"bad"}}',
    b'{"value":1,"uncertainty":{"kind":"std","value":true,"method":"bad"}}',
    b'{"value":1,"uncertainty":{"kind":"none","value":2,"method":"bad"}}',
    b'{"value":1,"uncertainty":{"kind":"madeup","value":1,"method":"bad"}}'])
def test_malformed_output_never_evidence(ctx, fake, raw):
    fake.outputs["speed.json"] = raw
    assert_failure(ctx, invoke(ctx))


def test_all_outputs_validated_before_evidence(ctx, fake):
    fake.outputs["bad.json"] = b"not json"
    specs = [{"name": name, "unit": "1", "label": name} for name in ("speed", "bad")]
    assert_failure(ctx, invoke(ctx, outputs=specs))


def test_uncertainty_is_preserved(ctx, fake):
    fake.outputs["speed.json"] = b'{"value":2.5,"uncertainty":{"kind":"ci95","value":0.3,"method":"fit"}}'
    result = invoke(ctx)
    assert result.status == "ok"
    m = ctx.ledger.get("measurement", result.outputs[0])
    assert m.uncertainty == Uncertainty(value=0.3, kind="ci95", method="fit")


def test_input_files_and_provenance_inheritance(ctx, fake):
    ledger = ctx.ledger
    artifact = ledger.put_bytes(b"data", kind="table", media_type="application/octet-stream")
    ledger.add(Run(id="source", campaign_id=ctx.campaign_id, step_id="s", tool="fixture", idempotency_key="source",
                   input_ids=[artifact.id], status="ok", started_at=0, ended_at=0))
    for mid, cal in [("scale", []), ("clock", []), ("a", ["scale", "clock"]), ("b", ["scale"])]:
        ledger.add(Measurement(id=mid, run_id="source", quantity="fixture", value=2, unit="um/s",
                               uncertainty=Uncertainty(value=0.1, kind="std", method="fixture"),
                               calibration_ids=cal, artifact_ids=[artifact.id]))
    result = invoke(ctx, inputs=["a", "b", artifact.id])
    assert result.status == "ok"
    m = ledger.get("measurement", result.outputs[0])
    assert m.calibration_ids == ["scale", "clock"] and m.artifact_ids == [artifact.id]
    code = "sha256:" + hashlib.sha256(b"# analysis").hexdigest()  # the code is kept as the run's last input
    assert ledger.get("run", result.run_id).input_ids == ["a", "b", artifact.id, code]
    code, files, timeout = fake.calls[0]
    index = json.loads(files["inputs.json"])
    assert json.loads(files[index["a"]]) == {"value": 2, "unit": "um/s", "uncertainty": {
        "value": 0.1, "kind": "std", "method": "fixture"}}
    assert files[index[artifact.id]] == b"data"
    assert json.loads(files["__outputs__.json"]) == ["speed.json"] and timeout == 120
    ledger.invalidate_run("source", "invalidated calibration")
    assert ledger.stale_reason(m.id)


def test_artifact_size_cap_before_runner(ctx, fake, monkeypatch):
    a = ctx.ledger.put_bytes(b"large", kind="text", media_type="text/plain")
    monkeypatch.setattr(analysis, "ARTIFACT_LIMIT", 4)
    assert_failure(ctx, invoke(ctx, inputs=[a.id]))
    assert fake.calls == []


@pytest.mark.parametrize("change", [{"inputs": ["missing"]}, {"inputs": ["analysis_test"]},
    {"inputs": "bad"}, {"inputs": ["x", "x"]}, {"outputs": []},
    {"outputs": [{"name": "../escape", "unit": "1", "label": "bad"}]},
    {"outputs": [{"name": "x.pkl", "unit": "1", "label": "bad"}]}])
def test_invalid_arguments_refused_before_runner(ctx, fake, change):
    assert_failure(ctx, invoke(ctx, **change))
    assert not fake.calls


@pytest.mark.modal
@pytest.mark.skipif(os.environ.get("TRANSLUCENT_MODAL_TESTS") != "1",
                    reason="set TRANSLUCENT_MODAL_TESTS=1 for real Modal integration")
def test_real_modal_analysis(ctx):
    code = '''import json
from pathlib import Path
import numpy as np
root = Path("/work/inputs")
index = json.loads((root / "inputs.json").read_text())
v = json.loads((root / next(iter(index.values()))).read_text())["value"]
Path("/work/outputs/speed.json").write_text(json.dumps({"value": float(np.sqrt(v))}))
'''
    ledger = ctx.ledger
    ledger.add(Run(id="seed", campaign_id=ctx.campaign_id, step_id="s", tool="fixture", idempotency_key="seed",
                   input_ids=[], status="ok", started_at=0, ended_at=0))
    ledger.add(Measurement(id="input", run_id="seed", quantity="fixture", value=6.25, unit="1",
                           uncertainty=Uncertainty(value=None, kind="none", method="fixture")))
    result = invoke(ctx, code=code, inputs=["input"])
    assert result.status == "ok", result.summary
    run = ledger.get("run", result.run_id)
    assert ledger.get("measurement", result.outputs[0]).value == 2.5
    assert run.code_sha == hashlib.sha256(code.encode()).hexdigest() and run.provider == "modal"
    print(f"Modal run={run.id} sandbox={run.provider_request_id} code_sha={run.code_sha}")


def test_typed_in_numbers_are_refused(ctx, fake):
    # a live Meta campaign (2026-10-29) copied a length series into its code and declared inputs it never read
    fake.depends = False
    result = invoke(ctx)
    assert_failure(ctx, result)
    assert "speed did not change when the recorded inputs changed" in result.summary
    _, real, _ = fake.calls[0]
    _, nudged, _ = fake.calls[1]
    assert nudged["inputs.json"] == real["inputs.json"] and nudged["input_0.json"] != real["input_0.json"]
    a, b = json.loads(real["input_0.json"]), json.loads(nudged["input_0.json"])
    assert all(0.002 <= abs(y / x - 1) <= 0.005 for x, y in zip(a["value"] + [a["uncertainty"]["value"]],
                                                               b["value"] + [b["uncertainty"]["value"]]))


def test_typed_in_uncertainty_is_refused(ctx, fake, monkeypatch):
    fake.outputs["speed.json"] = b'{"value":2.5,"uncertainty":{"kind":"std","value":8.0,"method":"guess"}}'
    real = fake.outputs["speed.json"]
    nudged = b'{"value":2.51,"uncertainty":{"kind":"std","value":8.0,"method":"guess"}}'
    run = analysis.run_untrusted
    monkeypatch.setattr(analysis, "run_untrusted", lambda code, files, timeout_s: (
        run(code, files, timeout_s) if len(fake.calls) == 0 else
        SimpleNamespace(**{**vars(fake), "outputs": {"speed.json": nudged}})))
    result = invoke(ctx)
    assert_failure(ctx, result)
    assert "compute each uncertainty from the data" in result.summary and real != nudged


def test_analysis_needs_a_recorded_measurement(ctx, fake):
    artifact = ctx.ledger.put_bytes(b"data", kind="table", media_type="application/octet-stream")
    result = invoke(ctx, inputs=[artifact.id])
    assert_failure(ctx, result)
    assert "at least one recorded measurement" in result.summary


def test_code_that_breaks_on_nudged_inputs_is_refused(ctx, fake, monkeypatch):
    run = analysis.run_untrusted
    monkeypatch.setattr(analysis, "run_untrusted", lambda code, files, timeout_s: (
        run(code, files, timeout_s) if len(fake.calls) == 0 else
        SimpleNamespace(status="error", reason="AssertionError", outputs={}, sandbox_id="sb2", terminated=True)))
    result = invoke(ctx)
    assert_failure(ctx, result)
    assert "did not run when its recorded inputs changed slightly" in result.summary


def test_token_dependence_on_inputs_is_refused(ctx, fake, monkeypatch):
    # a typed-in 49.1 plus a negligible term of the inputs moves by far less than the inputs did
    run = analysis.run_untrusted
    monkeypatch.setattr(analysis, "run_untrusted", lambda code, files, timeout_s: (
        run(code, files, timeout_s) if len(fake.calls) == 0 else
        SimpleNamespace(**{**vars(fake), "outputs": {"speed.json": b"2.5000000001"}})))
    assert "speed did not change" in invoke(ctx).summary


def test_summary_names_each_measurement_id(ctx, fake):
    # a live campaign cited "early_growth_speed_single" (the output name) because the summary had no ids
    fake.outputs["speed.json"] = b'{"value":2.5,"uncertainty":{"kind":"ci95","value":0.3,"method":"fit"}}'
    result = invoke(ctx)
    assert f"speed = 2.5 ± 0.3 um/s (cite {result.outputs[0]})" in result.summary
