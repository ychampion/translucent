"""S4b loop-compare/1 and /2: the comparator's truth table, comparisons recorded with every result and after an amendment,
same basis only, the computed conclusion (computational gains are labelled computational), and "not reached"."""
import pytest
from support.analytic_system import simulate
from support.loop_systems import PREDICTION, objective, register

from translucent import chain
from translucent.contracts import Uncertainty
from translucent.ledger import AdmissionError, Ledger
from translucent.loop import baseline_request_id, computed_outcome
from translucent.loop_compare import compare, record_comparisons, select_best
from translucent.loop_contracts import (DatasetRecord, LoopRecord, ObjectiveData, Provenance, ScalarSpec, Specimen,
                                        StoredChannel, StoredRecord)
from translucent.loop_runner import LoopRunner
from translucent.loop_service import Actor, LoopService

ANA = Actor("person", "Ana", "http")
MUSE = Actor("agent", "muse-test", "planner")


# ------------------------------------------------------------------ 1. the truth table (pure)

def obj(**kw) -> ObjectiveData:
    return ObjectiveData.model_validate(objective(**kw))


def v(x, h=0.0, unit="s"):
    return {"value": x, "unit": unit, "h": h}


def nr(t1, unit="s"):
    return {"value": None, "unit": unit, "h": 0.0, "not_reached_within": [0.0, t1]}


def run(o, baseline, candidate, basis="computational", why=None):
    out = compare(o, basis, None if baseline is None else {"refs": ["b/1"], "values": baseline},
                  {"request_id": "rq_x", "result_id": "res_x", "values": candidate}, baseline_reason=why)
    chain.payload("comparison", out)                   # a valid payload: finite numbers, known verdict
    return out


BASE = {"charge_time_80": v(1000.0, 5.0), "max_temperature": v(40.0, 0.2, "degC")}


def cand(t, temp=40.0, h=5.0, th=0.2):
    return {"charge_time_80": v(t, h), "max_temperature": v(temp, th, "degC")}


@pytest.mark.parametrize("candidate, kw, verdict, better", [
    (cand(800.0), {}, "candidate_better", True),                                  # better
    (cand(1050.0), {}, "not_better", False),                                      # not better
    (cand(800.0, temp=46.0), {}, "violates_constraints", True),                   # violation
    (cand(990.0, h=50.0), {}, "inconclusive", None),                              # target within uncertainty
    (cand(800.0, temp=44.9, th=0.5), {}, "inconclusive", True),                   # constraint straddles the limit
    (cand(1050.0), {"margin": -100.0}, "candidate_better", True),                 # negative margin: slowdown allowed
    (cand(800.0), {"margin": 300.0}, "not_better", False),                        # improvement below the margin
    (cand(900.0), {"limit": 850.0}, "not_better", False),                         # better, but misses the limit
    (cand(800.0), {"limit": 850.0}, "candidate_better", True),                    # better and inside the limit
    (cand(850.0, h=10.0), {"limit": 850.0}, "inconclusive", None),                # the limit straddled
])
def test_truth_table(candidate, kw, verdict, better):
    o = obj(target={"quantity": "charge_time_80", "unit": "s", "direction": "minimize"} | kw)
    out = run(o, BASE, candidate)
    assert (out["verdict"], out["target"]["better"]) == (verdict, better)
    assert out["evaluator"] == "loop-compare/2" and out["objective_version"] == 1
    assert (out["reasons"] == []) == (verdict == "candidate_better")


FAILING = {"charge_time_80": v(1000.0, 5.0), "max_temperature": v(51.0, 0.2, "degC")}     # breaks the 45 degC limit


@pytest.mark.parametrize("evaluator, candidate, verdict, decided_by", [
    ("loop-compare/2", cand(1300.0), "candidate_better", "feasibility"),   # slower, but within the limit it broke
    ("loop-compare/1", cand(1300.0), "not_better", None),                  # /1 judged the target alone
    ("loop-compare/2", cand(800.0), "candidate_better", "feasibility"),    # faster and within the limit
    ("loop-compare/2", cand(1300.0, temp=46.0), "violates_constraints", None),   # both break it: no winner
    ("loop-compare/2", cand(1300.0, temp=44.9, th=0.5), "inconclusive", None),   # the candidate straddles it
])
def test_a_candidate_that_meets_the_limits_a_failing_baseline_breaks_is_better(evaluator, candidate, verdict,
                                                                             decided_by):
    out = run(obj(evaluator=evaluator), FAILING, candidate)
    assert out["verdict"] == verdict and out["evaluator"] == evaluator
    assert out["target"].get("decided_by") == decided_by
    if evaluator == "loop-compare/2":
        assert [c["ok"] for c in out["baseline"]["constraints"]] == [False]      # the baseline's own check is kept
        assert out["target"]["delta"] is not None                                 # and so is the time it costs
    else:
        assert "constraints" not in out["baseline"]                              # /1's payload is unchanged


@pytest.mark.parametrize("candidate, verdict", [
    (cand(1300.0), "not_better"),                   # meets the constraints, misses the target's own 1200 s limit
    (cand(1200.0, h=10.0), "inconclusive"),         # straddles it
    (cand(1100.0), "candidate_better"),             # meets both
])
def test_feasibility_never_overrides_the_target_limit(candidate, verdict):
    o = obj(target={"quantity": "charge_time_80", "unit": "s", "direction": "minimize", "limit": 1200.0})
    out = run(o, FAILING, candidate)
    assert out["verdict"] == verdict
    assert out["target"].get("decided_by") == ("feasibility" if verdict == "candidate_better" else None)


def test_reasons_say_why_in_plain_words():
    o = obj(target={"quantity": "charge_time_80", "unit": "s", "direction": "minimize", "limit": 850.0})
    assert "misses the absolute limit of 850 s" in run(o, BASE, cand(900.0))["reasons"][0]
    out = run(obj(), BASE, cand(800.0, temp=46.0))
    assert out["reasons"] == ["Cell stays at or below 45 degC: max_temperature is 46 ± 0.2 degC, outside <= 45 degC"]
    assert out["constraints"][0] == {"quantity": "max_temperature", "op": "<=", "limit": 45.0, "unit": "degC",
                                     "value": 46.0, "h": 0.2, "ok": False}


def test_the_numbers_follow_the_documented_rule():
    out = run(obj(), BASE, cand(800.0, h=12.0))
    assert out["target"]["delta"] == 200.0 and out["target"]["h"] == 13.0          # hypot(5, 12)
    # better needs d - H > margin, strictly; not better needs d + H <= margin
    verdicts = [run(obj(), BASE, cand(c, h=12.0))["verdict"] for c in (986.0, 987.0, 1012.0, 1013.0)]
    assert verdicts == ["candidate_better", "inconclusive", "inconclusive", "not_better"]


@pytest.mark.parametrize("baseline, candidate, why, reason", [
    (None, cand(800.0), "simulated candidate vs measured baseline", "simulated candidate vs measured baseline"),
    (BASE, {"charge_time_80": v(800.0)}, None, "the candidate lacks max_temperature"),
    ({"max_temperature": v(40.0, 0.2, "degC")}, cand(800.0), None, "the baseline lacks charge_time_80"),
    (BASE, {"charge_time_80": {"missing": "no series"}, "max_temperature": v(40.0, 0.2, "degC")}, None,
     "the candidate lacks charge_time_80 (no series)"),
    (BASE, cand(800.0) | {"max_temperature": v(40.0, 0.2, "V")}, None, "the candidate lacks max_temperature ("),
])
def test_not_comparable_on_basis_mismatch_or_a_missing_quantity(baseline, candidate, why, reason):
    out = run(obj(), baseline, candidate, why=why)
    assert out["verdict"] == "not_comparable" and any(r.startswith(reason) for r in out["reasons"])
    assert out["target"]["better"] is None and out["target"]["delta"] is None


def test_maximize_mirrors_and_units_convert():
    o = obj(target={"quantity": "charge_time_80", "unit": "min", "direction": "maximize", "margin": 1.0},
            constraints=[{"quantity": "max_temperature", "op": "<=", "limit": 318.15, "unit": "K", "label": "T"}])
    out = run(o, BASE, cand(1200.0, temp=44.0))                 # 20 min against 16.67 min, 317.15 K
    assert out["verdict"] == "candidate_better" and out["target"]["delta"] == pytest.approx(200.0 / 60)
    assert out["constraints"][0]["value"] == pytest.approx(317.15) and out["constraints"][0]["unit"] == "K"
    assert run(o, BASE, cand(1030.0))["verdict"] == "not_better"     # half a minute longer, margin 1 min


def test_a_crossing_not_reached_is_a_lower_bound():
    # time to reach 40 degC: longer is better, and at least half an hour is required
    o = obj(target={"quantity": "time_to_40C", "unit": "s", "direction": "maximize"},
            constraints=[{"quantity": "time_to_40C", "op": ">=", "limit": 1800.0, "unit": "s",
                          "label": "Stays under 40 degC for half an hour"}])
    base = {"time_to_40C": v(1200.0, 5.0)}
    out = run(o, base, {"time_to_40C": nr(3600.0)})
    assert out["verdict"] == "candidate_better" and out["target"]["candidate"] is None
    assert out["constraints"][0]["ok"] is True and out["constraints"][0]["not_reached_within"] == [0.0, 3600.0]
    short = run(o, base, {"time_to_40C": nr(600.0)})
    assert short["verdict"] == "inconclusive"                    # not reached by 600 s says nothing about 1800 s
    assert run(o, base, {"time_to_40C": v(900.0)})["verdict"] == "violates_constraints"
    m = obj(target={"quantity": "charge_time_80", "unit": "s", "direction": "minimize"}, constraints=[])
    assert run(m, {"charge_time_80": v(1200.0, 5.0)}, {"charge_time_80": nr(3600.0)})["verdict"] == "not_better"
    assert run(m, {"charge_time_80": nr(3600.0)}, {"charge_time_80": v(1200.0)})["verdict"] == "candidate_better"


def _comparison(seq, basis, verdict, candidate, direction="minimize"):
    data = {"basis": basis, "verdict": verdict, "target": {"candidate": candidate, "direction": direction}}
    return LoopRecord(id=f"cmp_{seq}", campaign_id="c", type="comparison", seq=seq, actor="system:comparator",
                      data=data, created_at=0.0, previous_hash="", digest="")


def test_select_best_prefers_measured_then_the_best_value_then_the_earliest():
    sim_fast = _comparison(1, "computational", "candidate_better", 500.0)
    bench_slow = _comparison(2, "physical", "candidate_better", 900.0)
    bench_fast = _comparison(3, "physical", "candidate_better", 800.0)
    bench_tie = _comparison(4, "physical", "candidate_better", 800.0)
    assert select_best([sim_fast, bench_slow, bench_fast, bench_tie]) == ("improved", bench_fast)
    assert select_best([sim_fast, _comparison(5, "computational", "candidate_better", 400.0)])[0] == \
        "improved_in_simulation"
    assert select_best([_comparison(6, "computational", "inconclusive", 1.0),
                        _comparison(7, "computational", "not_better", 2.0)]) == \
        ("not_improved", _comparison(7, "computational", "not_better", 2.0))
    assert select_best([_comparison(8, "computational", "inconclusive", 1.0),
                        _comparison(9, "computational", "not_comparable", None)]) == ("unresolved", None)


# ------------------------------------------------------------------ 2. on a ledger

@pytest.fixture(autouse=True)
def adapter(monkeypatch):
    return register(monkeypatch)


@pytest.fixture
def L(tmp_path):
    led = Ledger(tmp_path / "ledger")
    yield led
    led.db.close()


def bench(L, current_mA=1000.0, charge_s=14400.0, peak=26.5):
    """A measured baseline record: a 1 A bench charge with its temperature log and its two scalars."""
    t, y = [0.0, 600.0, 1200.0], [25.0, 26.0, peak]
    s = L.put_json({"t": t, "t_unit": "s", "y": y, "unit": "degC"}, kind="series")
    record = StoredRecord(id="baseline-1", role="baseline", specimen=Specimen(id="cell-1"),
                          configuration={"current_A": current_mA, "ambient_C": 25.0},
                          configuration_units={"current_A": "mA", "ambient_C": "degC"}, configuration_hash="0" * 64,
                          conditions={}, conditions_units={}, time=None,
                          channels=[StoredChannel(column="T", quantity="temperature", unit="degC",
                                                  uncertainty=Uncertainty(value=0.1, kind="std", method="spec"),
                                                  series_id=s.id, n=3, t_range=(0.0, 1200.0))],
                          scalars=[ScalarSpec(quantity="charge_time_80", value=charge_s, unit="s", source="log"),
                                   ScalarSpec(quantity="max_temperature", value=peak, unit="degC", source="log",
                                              uncertainty=Uncertainty(value=0.1, kind="std", method="spec"))],
                          media=[], provenance=Provenance(source="test"), files={})
    L.add(DatasetRecord(id="bench", manifest_version=2, manifest_id=s.id, title="t", author="Ana", citation="c",
                        licence="internal", system="loop-test-cell", records=[record], created_at=0.0))


def open_loop(L, **kw):
    bench(L)
    kw = {"datasets": ["bench"], "baseline": ["bench/baseline-1"]} | kw
    return LoopService(L, ANA).open_campaign(objective(**kw), "Faster charge")["campaign_id"]


def simulate_at(L, cid, current, action="simulate_charge", tick=True):
    req = LoopService(L, MUSE).propose(cid, action, {"current_A": current, "ambient_C": 25.0},
                                       rationale="Try this current.", prediction=PREDICTION, expected_effect="x")
    LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])
    if tick:
        LoopRunner(L.root, process="cli", workers=1).advance(cid, req["request_id"])
    return req["request_id"]


def comparisons(L, cid):
    return [r for r in chain.records(L, cid) if r.type == "comparison"]


def test_every_result_is_compared_and_the_conclusion_is_improved_in_simulation(L):
    cid = open_loop(L, baseline_simulation=True)
    LoopRunner(L.root, process="cli", workers=1).tick(cid)          # the 1 A baseline on the solver
    assert comparisons(L, cid) == []                                # the baseline is not a candidate
    fast, slow = simulate_at(L, cid, 2.0), simulate_at(L, cid, 0.8)
    by_request = {c.data["candidate"]["request_id"]: c for c in comparisons(L, cid)}
    assert set(by_request) == {fast, slow} and len(comparisons(L, cid)) == 2
    best = by_request[fast]
    assert best.actor == "system:comparator" and best.data["basis"] == "computational"
    assert best.data["verdict"] == "candidate_better" and best.data["baseline"]["refs"] == ["bench/baseline-1"]
    assert best.data["target"]["baseline"] == simulate(1.0, 25.0)[2] and best.data["target"]["candidate"] == 7200.0
    assert by_request[slow].data["verdict"] == "not_better"
    assert computed_outcome(L, cid) == ("improved_in_simulation", best.id)
    # "improved" with only computational evidence is refused, through any writer
    with pytest.raises(AdmissionError, match="outcome is computed from the comparisons: improved_in_simulation"):
        chain.append(L, cid, "conclusion", {"outcome": "improved", "comparison_id": best.id, "text": "It worked",
                                            "unresolved_reasons": [], "author": "agent:muse-test"},
                     "agent:muse-test", "con_forged")
    with pytest.raises(AdmissionError, match=f"names {by_request[slow].id} as the result"):
        LoopService(L, MUSE).conclude(cid, f"The result is {by_request[slow].id}.")
    done = LoopService(L, MUSE).conclude(cid, f"In simulation, 2 A charges faster ({best.id}).")
    assert done["data"]["outcome"] == "improved_in_simulation" and done["data"]["comparison_id"] == best.id
    assert record_comparisons(L, cid) == []                          # nothing is compared after the conclusion


def test_a_comparison_is_the_comparators_never_a_writers(L):
    cid = open_loop(L, baseline_simulation=True)
    LoopRunner(L.root, process="cli", workers=1).tick(cid)
    simulate_at(L, cid, 2.0)
    (real,) = comparisons(L, cid)
    forged = dict(real.data, verdict="not_better")
    with pytest.raises(AdmissionError, match="recorded not_better, recomputed candidate_better"):
        chain.append(L, cid, "comparison", forged, "system:comparator", "cmp_forged")
    with pytest.raises(AdmissionError, match="comparisons are computed by the comparator"):
        chain.append(L, cid, "comparison", real.data, "agent:muse-test", "cmp_agent")
    assert LoopService(L, Actor("system", "comparator")).record_comparisons(cid) == []     # nothing owed


def test_a_computational_candidate_against_a_measured_baseline_is_not_comparable(L):
    cid = open_loop(L)                                              # no baseline_simulation
    simulate_at(L, cid, 2.0)
    (c,) = comparisons(L, cid)
    assert c.data["verdict"] == "not_comparable" and c.data["reasons"] == ["simulated candidate vs measured baseline"]
    assert LoopService(L, MUSE).conclude(cid, "Nothing comparable.", ["no simulated baseline"])["data"][
        "outcome"] == "unresolved"


def test_a_late_computational_baseline_compares_the_waiting_candidates_again(L):
    cid = open_loop(L, baseline_simulation=True)
    rq = simulate_at(L, cid, 2.0)                                   # runs before the baseline re-run
    (first,) = comparisons(L, cid)
    assert first.data["verdict"] == "not_comparable"
    assert first.data["reasons"] == ["the computational baseline has not finished on the solver yet"]
    LoopRunner(L.root, process="cli", workers=1).tick(cid)          # now the baseline
    first, second = comparisons(L, cid)
    assert second.data["candidate"]["request_id"] == rq and second.data["verdict"] == "candidate_better"
    assert computed_outcome(L, cid)[1] == second.id


def test_a_bench_result_against_the_bench_baseline_is_improved(L, tmp_path):
    cid = open_loop(L, baseline_simulation=True)
    LoopRunner(L.root, process="cli", workers=1).tick(cid)
    simulate_at(L, cid, 2.0)
    req = LoopService(L, MUSE).propose(cid, "cell_test", {"current_A": 2.0, "ambient_C": 25.0}, rationale="Confirm.",
                                       prediction=PREDICTION, expected_effect="x", specimen={"id": "cell-07"})
    LoopService(L, ANA).approve(cid, req["request_id"], req["content_hash"])
    t, y, charge = simulate(2.0, 25.0)
    (tmp_path / "run.csv").write_text("time_s,T\n" + "".join(f"{a!r},{b!r}\n" for a, b in zip(t, y)))
    LoopService(L, ANA).submit_result(cid, {
        "request_id": req["request_id"], "content_hash": req["content_hash"],
        "executed_configuration": {"current_A": 2.0, "ambient_C": 25.0},
        "executed_units": {"current_A": "A", "ambient_C": "degC"}, "specimen_id": "cell-07",
        "table": {"file": "run.csv", "time_column": "time_s", "columns": {"T": {"quantity": "temperature",
                                                                               "unit": "degC"}}},
        "time": {"unit": "s", "origin": "charger on", "source": "logger"},
        "scalars": [{"quantity": "max_temperature", "value": max(y), "unit": "degC", "source": "logger",
                     "uncertainty": {"value": 0.5, "kind": "std", "method": "thermocouple"}},
                    {"quantity": "charge_time_80", "value": charge, "unit": "s", "source": "charger"}],
        "author": "Ana"}, {"run.csv": tmp_path / "run.csv"})
    bench_cmp = comparisons(L, cid)[-1]
    assert bench_cmp.data["basis"] == "physical" and bench_cmp.data["verdict"] == "candidate_better"
    assert bench_cmp.data["target"]["baseline"] == 14400.0                           # the measured record
    assert bench_cmp.data["constraints"][0]["h"] == 1.0                               # 2 sigma of 0.5
    assert computed_outcome(L, cid) == ("improved", bench_cmp.id)


def test_an_amendment_compares_every_result_again_under_the_new_version(L):
    cid = open_loop(L, baseline_simulation=True)
    LoopRunner(L.root, process="cli", workers=1).tick(cid)
    rq = simulate_at(L, cid, 2.0)
    peak = max(simulate(2.0, 25.0)[1])
    out = LoopService(L, ANA).amend(cid, "The cell must stay cooler.", constraints=[
        {"quantity": "max_temperature", "op": "<=", "limit": 25.1, "unit": "degC", "label": "Below 25.1 degC"}])
    (again,) = [c for c in comparisons(L, cid) if c.data["objective_version"] == 2]
    assert [c["id"] for c in out["comparisons"]] == [again.id] and again.data["candidate"]["request_id"] == rq
    assert again.data["verdict"] == "violates_constraints" and again.data["constraints"][0]["value"] == peak
    assert computed_outcome(L, cid) == ("not_improved", again.id)
    assert LoopService(L, MUSE).conclude(cid, "Too hot under the new limit.")["data"]["outcome"] == "not_improved"


def test_a_threshold_never_reached_is_an_answer(L):
    """time_to_threshold on a series that never crosses: define_measurement records "not reached within the window"
    instead of refusing, and an objective quantity defined that way is compared as a lower bound."""
    time_to_40 = {"op": "time_to_threshold", "channel": "temperature", "threshold": 40.0, "direction": "rising",
                  "unit": "s"}
    cid = open_loop(L, baseline_simulation=True,
                    quantities=objective()["quantities"] + [{"name": "time_to_40C", "unit": "s", "source": "operator",
                                                             "operator": time_to_40}],
                    constraints=[{"quantity": "time_to_40C", "op": ">=", "limit": 1800.0, "unit": "s",
                                  "label": "Stays under 40 degC for half an hour"}])
    LoopRunner(L.root, process="cli", workers=1).tick(cid)
    rq = simulate_at(L, cid, 2.0)
    (c,) = comparisons(L, cid)
    check = c.data["constraints"][0]
    assert check["ok"] is True and check["value"] is None
    assert check["not_reached_within"] == [0.0, simulate(2.0, 25.0)[2]]
    assert c.data["verdict"] == "candidate_better"
    out = LoopService(L, MUSE).define_measurement(cid, "warm", time_to_40)
    assert out["measurements"] == []
    assert {m["subject"] for m in out["not_reached"]} == {"bench/baseline-1", rq,
                                                          baseline_request_id(cid, "bench/baseline-1")}
    assert out["not_reached"][0] == {"subject": "bench/baseline-1", "not_reached_within": [0.0, 1200.0]}
    runs = [r for r in L.list("run", campaign_id=cid) if r.tool == "operator:time_to_threshold"]
    assert len(runs) == 3 and all(r.measurement_ids == [] and r.params["not_reached_within"] for r in runs)
    # a threshold the series starts past is still refused: "already above" is not "not reached"
    with pytest.raises(AdmissionError, match="already at or above"):
        LoopService(L, MUSE).define_measurement(cid, "warm20", time_to_40 | {"threshold": 20.0})
