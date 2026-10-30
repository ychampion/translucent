"""Scoped memory (only lessons that concern the campaign, capped, supported first, rejected never), setup facts offered
as citable ids, and the experiment record in the bundle."""
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from translucent import instruments, memory, replay, tools
from translucent.contracts import Campaign, Claim, Lesson, Measurement, Predicate, Review, Run, Source, Uncertainty
from translucent.ledger import Ledger
from translucent.verify import verify


def movie(L, tmp_path, name, experiment, shade):
    path = tmp_path / f"{name}.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 1, (16, 16))
    for _ in range(2):
        writer.write(np.full((16, 16, 3), shade, dtype=np.uint8))
    writer.release()
    return L.put_file(path, kind="video", media_type="video/mp4", label=name,
                      source=Source(uri=f"experiment:{experiment}", citation=name)).id


@pytest.fixture
def world(tmp_path):
    L = Ledger(tmp_path / "ledger")
    a = movie(L, tmp_path, "m4", "reisecker2023/movie4", 10)
    a2 = movie(L, tmp_path, "m2", "reisecker2023/movie2", 90)
    b = movie(L, tmp_path, "lu", "lu2023/movie1", 170)
    now = time.time()
    for cid, vid in (("c_a", a), ("c_b", b), ("c_a_again", a)):
        L.add(Campaign(id=cid, objective="x", title=cid, inputs=[vid], budget_usd=0, created_at=now))
    n = iter(range(100))

    def measured(cid, vid, quantity="spatial_scale", value=161.2):
        i = next(n)
        L.add(Run(id=f"r{i}", campaign_id=cid, step_id="s1", tool="lookup_calibration", idempotency_key=f"k{i}",
                  input_ids=[vid], measurement_ids=[f"m{i}"], status="ok", started_at=now, ended_at=now))
        return L.add(Measurement(id=f"m{i}", run_id=f"r{i}", quantity=quantity, value=value, unit="px/mm",
                                 artifact_ids=[vid], uncertainty=Uncertainty(value=4, kind="bound", method="bar"))).id

    def lesson(lid, scope, evidence, learned_in="c_a", text=None, contradicts=None, at=None):
        return L.add(Lesson(id=lid, text=text or f"lesson {lid}", scope=scope, evidence_ids=evidence,
                            learned_in=learned_in, created_at=at or time.time(), contradicts=contradicts))
    yield SimpleNamespace(L=L, a=a, a2=a2, b=b, measured=measured, lesson=lesson)
    L.db.close()



def campaign(w, cid, vid):
    return w.L.add(Campaign(id=cid, objective="x", title=cid, inputs=[vid], budget_usd=0, created_at=time.time()))


def picked(w, c):
    return {d["lesson"].id: (d["reason"], d["confidence"]) for d in memory.select(w.L, c)}


def test_loads_only_lessons_that_concern_the_campaign(world):
    w = world
    on_a = w.measured("c_a", w.a)
    on_b = w.measured("c_b", w.b)
    w.lesson("rec", "operando battery microscopy filament bridging", [on_a])      # free text, evidence on movie 4
    w.lesson("src", "source:reisecker2023", [on_a])
    w.lesson("method", "method:timing", [on_b], learned_in="c_b")
    w.lesson("lu", "operando battery microscopy plating", [on_b], learned_in="c_b")
    w.lesson("lu_src", "source:lu2023", [on_b], learned_in="c_b")
    same = picked(w, campaign(w, "new_a", w.a))
    assert same == {"rec": ("same recording", "tentative"), "src": ("same recording", "tentative"),
                    "method": ("method", "tentative")}
    sibling = picked(w, campaign(w, "new_a2", w.a2))                             # another movie of the same paper
    assert sibling == {"rec": ("same source", "tentative"), "src": ("same source", "tentative"),
                       "method": ("method", "tentative")}
    assert set(picked(w, campaign(w, "new_b", w.b))) == {"method", "lu", "lu_src"}


def test_capped_supported_first_and_rejected_never(world):
    w = world
    on_a = w.measured("c_a", w.a)
    again = w.measured("c_a_again", w.a)
    for i in range(7):
        w.lesson(f"t{i}", "method:timing", [on_a], at=1000 + i)
    w.lesson("both", "method:timing", [on_a, again], at=1)                       # evidence from two campaigns
    old = w.lesson("old", "method:scale", [on_a], at=2)
    claim = w.L.add(Claim(id="k", campaign_id="c_a_again", author="worker", statement="Scale is 161.2.",
                          created_at=time.time(), predicate=Predicate(type="value_within", measurement_ids=[again],
                                                                       params={"lo": 150, "hi": 170, "unit": "px/mm"})))
    w.L.add(Review(id="rv", claim_id=claim.id, reviewer="gate", verdict="supported", checks=[], reason="",
                   created_at=time.time()))
    w.lesson("against", "method:scale", [claim.id], learned_in="c_a_again", contradicts=old.id, at=3)
    c = campaign(w, "new", w.a)
    got = memory.select(w.L, c)
    assert len(got) == memory.CAP
    assert got[0]["lesson"].id == "both" and got[0]["confidence"] == "supported"
    assert "old" not in {d["lesson"].id for d in got}
    assert memory.confidence(w.L, old) == "rejected"
    assert memory.confidence(w.L, w.L.get("lesson", "against")) == "tentative"


def test_setup_facts_are_offered_as_citable_ids(world):
    w = world
    scale = w.measured("c_a", w.a)
    w.lesson("setup", "setup:" + w.a.removeprefix("sha256:"), [scale], text="Ana confirmed the scale bar.")
    text = memory.prompt(w.L, memory.select(w.L, campaign(w, "new", w.a)))
    assert "untrusted priors" in text and "Lessons from earlier campaigns" in text
    assert f"Confirmed setup you may cite directly: {scale}." in text
    assert memory.select(w.L, campaign(w, "other", w.b)) == []


def test_experiment_record_round_trips_export_and_verify(world, tmp_path):
    w = world
    w.L.emit("c_a", "campaign.started", "runtime", "x")
    bundle = replay.export(w.L, "c_a", tmp_path / "out", evidence_only=True)
    e = bundle.experiment
    assert e.id == "reisecker2023/movie4" and e.citation.startswith("Reisecker")
    assert e.scale.status == "available" and e.clock.status == "available" and e.voltage.status == "available"
    assert e.files == {"video": instruments.RECORDED_FILES["reisecker2023/movie4"]}
    assert verify(tmp_path / "out").ok, verify(tmp_path / "out").problems
    w.L.emit("c_b", "campaign.started", "runtime", "x")
    lu = replay.export(w.L, "c_b", tmp_path / "lu", evidence_only=True).experiment
    assert lu.id == "lu2023/movie1" and lu.layout


def test_experiment_without_records_says_why(monkeypatch):
    for name in ("spatial_scale", "frame_times", "voltage", "time_base_source", "frame_time_uncertainty"):
        monkeypatch.setattr(instruments, name, lambda exp: None)
    r = instruments.record("somewhere/else")
    assert r["scale"] == {"status": "unavailable", "reason": "not on record"}
    assert "playback frame timing is not real time" in r["clock"]["reason"]
