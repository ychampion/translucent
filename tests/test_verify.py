import json
import sqlite3
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from translucent import cli, gate, replay
from translucent.contracts import Campaign, Claim, Measurement, Predicate, Run, Source, Uncertainty
from translucent.ledger import Ledger, SCHEMA
from translucent.verify import verify
from translucent.ledger import sha256_file


@pytest.fixture
def recorded(tmp_path):
    ledger = Ledger(tmp_path / "ledger")
    video = tmp_path / "tiny.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 1, (16, 16))
    assert writer.isOpened()
    writer.write(np.zeros((16, 16, 3), dtype=np.uint8))
    writer.release()
    art = ledger.put_file(video, kind="video", media_type="video/mp4", source=Source(uri="experiment:fixture"))
    ledger.add(Campaign(id="c", objective="Check the voltage", title="Fixture", inputs=[art.id],
                        budget_usd=0, created_at=time.time()))
    ledger.emit("c", "campaign.started", "runtime", "Started")
    ledger.add(Run(id="r", campaign_id="c", step_id="s", tool="read_cell_voltage", idempotency_key="r",
                   input_ids=[art.id], status="ok", measurement_ids=["m"], started_at=0, ended_at=0))
    ledger.add(Measurement(id="m", run_id="r", quantity="voltage", value=2, unit="V", artifact_ids=[art.id],
                           uncertainty=Uncertainty(value=0.01, kind="bound", method="fixture")))
    ledger.emit("c", "measurement.recorded", "read_cell_voltage", "Voltage", refs=["m"],
                data={"measurement": ledger.get("measurement", "m").model_dump(mode="json")})
    ledger.emit("c", "tool.finished", "read_cell_voltage", "Read voltage", refs=["r", "m"],
                data={"status": "ok"})
    ledger.add(Claim(id="k", campaign_id="c", author="fixture", statement="The voltage is near 2 V.",
                     predicate=Predicate(type="value_within", params={"lo": 1.9, "hi": 2.1, "unit": "V"},
                                         measurement_ids=["m"]), created_at=time.time()))
    review = gate.review(ledger, "k")
    assert review.verdict == "supported"
    ledger.emit("c", "claim.reviewed", "gate", "Finding earned", refs=["k", review.id],
                data={"review": review.model_dump(mode="json")})
    ledger.emit("c", "campaign.finished", "runtime", "Done")
    yield ledger, art, review
    ledger.db.close()


def change(ledger, kind, rid, **changes):
    obj = ledger.get(kind, rid).model_dump(mode="json")
    ledger.db.execute("DROP TRIGGER record_no_update")
    ledger.db.execute("UPDATE record SET body=? WHERE id=?", (json.dumps({**obj, **changes}), rid))
    ledger.db.executescript(SCHEMA)


def test_clean_and_source_untouched(recorded, capsys):
    ledger, art, _ = recorded
    before = ledger.db.execute("SELECT * FROM record").fetchall(), ledger.events("c")
    result = verify(ledger.root)
    assert result.ok, result.problems
    assert (result.findings, result.matches, result.files) == (1, 1, 1)
    assert cli.main(["verify", str(ledger.root)]) == 0
    assert "1 findings re-checked, 1 match; 1 files intact" in capsys.readouterr().out
    assert before == (ledger.db.execute("SELECT * FROM record").fetchall(), ledger.events("c"))


def test_changed_blob_byte(recorded):
    ledger, art, _ = recorded
    path = ledger.blob_path(art.id)
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(data)
    result = verify(ledger.root)
    assert not result.ok and any("bytes changed" in p for p in result.problems)
    assert cli.main(["verify", str(ledger.root)]) == 1


def test_edited_verdict(recorded):
    ledger, _, review = recorded
    change(ledger, "review", review.id, verdict="refuted")
    result = verify(ledger.root)
    assert not result.ok and result.matches == 0
    assert any("recorded refuted, re-check gives supported" in p for p in result.problems)


def test_deleted_event(recorded):
    ledger = recorded[0]
    ledger.db.execute("DROP TRIGGER event_no_delete")
    ledger.db.execute("DELETE FROM event WHERE seq=2")
    ledger.db.executescript(SCHEMA)
    result = verify(ledger.root)
    assert not result.ok and any("gap or changed order" in p for p in result.problems)


def test_reordered_events(recorded):
    ledger = recorded[0]
    events = ledger.events("c")
    ledger.db.execute("DROP TRIGGER event_no_update")
    for event, other in zip(events[:2], reversed(events[:2])):
        ledger.db.execute("UPDATE event SET body=? WHERE seq=?", (other.model_dump_json(), event.seq))
    ledger.db.executescript(SCHEMA)
    result = verify(ledger.root)
    assert not result.ok and any("Event identity changed" in p for p in result.problems)


def test_measurement_run_flipped_to_failed(recorded):
    ledger = recorded[0]
    change(ledger, "run", "r", status="tool_failure")
    result = verify(ledger.root)
    assert not result.ok and result.matches == 0
    assert any("failed run" in p for p in result.problems)


def test_historical_reviews_survive_later_invalidation(recorded):
    ledger = recorded[0]
    ledger.invalidate_run("r", "A later calibration issue")
    review = gate.review(ledger, "k")
    assert review.verdict == "unsupported"
    result = verify(ledger.root)
    assert result.ok, result.problems
    assert result.matches == 1


RECORDINGS = sorted((Path(__file__).resolve().parents[1] / "web" / "public" / "fixtures" / "loop").glob("*.loop.json"))


@pytest.mark.parametrize("path", RECORDINGS, ids=[p.name for p in RECORDINGS])
def test_every_shipped_loop_recording_verifies(path):
    """The README and the web app tell a reader to verify these recordings offline; each must pass as shipped."""
    result = verify(path)
    assert result.ok, result.problems


def test_an_invalidation_in_the_same_clock_tick_as_a_review_is_still_after_it(recorded, monkeypatch):
    """The flake this replaces: on a coarse clock the invalidation got the review's own time, and verify re-checked
    the earlier supported review with the later invalidation in place."""
    from translucent import ledger as ledger_mod
    ledger = recorded[0]
    tick = ledger.db.execute("SELECT MAX(inserted_at) FROM record").fetchone()[0]
    monkeypatch.setattr(ledger_mod.time, "time", lambda: tick)
    ledger.invalidate_run("r", "A later calibration issue")
    assert ledger.db.execute("SELECT at FROM invalidation").fetchone()[0] > tick
    assert gate.review(ledger, "k").verdict == "unsupported"       # the new review, in that same tick, is after it
    at, = ledger.db.execute("SELECT at FROM invalidation").fetchone()
    assert ledger.db.execute("SELECT MAX(inserted_at) FROM record").fetchone()[0] > at
    monkeypatch.undo()
    result = verify(ledger.root)
    assert result.ok, result.problems
    assert result.matches == 1


def test_later_invalidation_without_review_is_not_a_current_match(recorded):
    ledger = recorded[0]
    ledger.invalidate_run("r", "The calibration was withdrawn")
    before = ledger.db.execute("SELECT * FROM invalidation").fetchall(), ledger.events("c")
    result = verify(ledger.root)
    assert not result.ok and result.matches == 0
    assert any("evidence since invalidated" in p for p in result.problems)
    assert before == (ledger.db.execute("SELECT * FROM invalidation").fetchall(), ledger.events("c"))


def test_consistent_measurement_and_review_edit_keeps_original_event(recorded):
    ledger, _, saved = recorded
    change(ledger, "measurement", "m", value=3)
    fresh = gate.review(ledger, "k")
    assert fresh.verdict == "refuted"
    updated = saved.model_copy(update={"verdict": fresh.verdict, "checks": fresh.checks, "reason": fresh.reason})
    change(ledger, "review", saved.id, **updated.model_dump(mode="json"))
    event = next(e for e in ledger.events("c") if e.type == "claim.reviewed")
    event = event.model_copy(update={"data": {"review": updated.model_dump(mode="json")}})
    ledger.db.execute("DROP TRIGGER record_no_delete")
    ledger.db.execute("DELETE FROM record WHERE id=?", (fresh.id,))
    ledger.db.execute("DROP TRIGGER event_no_update")
    ledger.db.execute("UPDATE event SET body=? WHERE campaign_id=? AND seq=?", (event.model_dump_json(), "c", event.seq))
    ledger.db.executescript(SCHEMA)
    result = verify(ledger.root)
    assert not result.ok
    assert any("Measurement m differs from its recorded event" in p for p in result.problems)


def test_tool_finished_refs_disagree_with_run_outputs(recorded):
    ledger, art, _ = recorded
    change(ledger, "run", "r", output_ids=[art.id])
    result = verify(ledger.root)
    assert not result.ok
    assert any("Run r differs from its recorded completion" in p for p in result.problems)


def test_voltage_log_is_sampled_on_movie_clock(recorded, tmp_path, monkeypatch):
    times = [10.0, 10.0, 30.0, 60.0, 60.0]
    monkeypatch.setattr(replay.instruments, "frame_times", lambda exp: times)
    monkeypatch.setattr(replay.instruments, "frame_time_uncertainty", lambda exp: 1224.248)
    monkeypatch.setattr(replay.instruments, "voltage", lambda exp: {
        "clock": "log", "t": list(range(81)), "v": [t / 100 for t in range(81)]})
    monkeypatch.setattr(replay, "_video_meta", lambda path: {"width": 16, "height": 16, "fps": 1, "frames": 5})
    bundle = replay.export(recorded[0], "c", tmp_path / "log-bundle", include_verification=False)
    series = bundle.series[0]
    assert series.t == times
    assert series.y == [0.1, 0.1, 0.3, 0.6, 0.6]
    assert series.frame_of_t == list(range(5))
    assert "approximate" in series.name and "1224.25 s" in series.name
    assert bundle.frame_time_s == times


def test_movie4_series_fields_stay_byte_identical(recorded, tmp_path, monkeypatch):
    from translucent.contracts import Series
    from translucent import instruments
    voltage = instruments.voltage("reisecker2023/movie4")
    expected = Series(name="Cell voltage", unit="V", t=voltage["t"], y=voltage["v"],
                      frame_of_t=list(range(len(voltage["t"])))).model_dump_json()
    monkeypatch.setattr(replay, "_experiment", lambda ledger, video: "reisecker2023/movie4")
    bundle = replay.export(recorded[0], "c", tmp_path / "m4-bundle", include_verification=False)
    assert bundle.series[0].model_dump_json() == expected
    assert bundle.frame_time_s == instruments.frame_times("reisecker2023/movie4")


def test_lu_voltage_uses_full_recorded_movie_window(recorded, tmp_path, monkeypatch):
    from bisect import bisect_left
    from translucent import instruments
    times = instruments.frame_times(instruments.LU)
    voltage = instruments.voltage(instruments.LU)
    monkeypatch.setattr(replay, "_experiment", lambda ledger, video: instruments.LU)
    monkeypatch.setattr(replay, "_video_meta", lambda path: {
        "width": 16, "height": 16, "fps": 1, "frames": len(times)})
    bundle = replay.export(recorded[0], "c", tmp_path / "lu-bundle", include_verification=False)
    series = bundle.series[0]
    assert len(series.t) == len(series.frame_of_t) == len(times) == 1261
    assert series.frame_of_t == list(range(1261))
    assert series.t[-1] > 90000
    for frame in (0, 300, 630, 1000, 1260):
        index = bisect_left(voltage["t"], series.t[frame])
        assert abs(series.t[frame] - times[frame]) <= voltage["dt"] / 2
        assert series.y[frame] == voltage["v"][index]


def test_independent_log_without_frame_clock_stays_unsynced(recorded, tmp_path, monkeypatch):
    monkeypatch.setattr(replay.instruments, "frame_times", lambda exp: None)
    monkeypatch.setattr(replay.instruments, "voltage", lambda exp: {
        "clock": "log", "t": list(range(8000)), "v": [0.1] * 8000})
    bundle = replay.export(recorded[0], "c", tmp_path / "no-clock", include_verification=False)
    series = bundle.series[0]
    assert series.frame_of_t is None
    assert len(series.t) <= 2001
    assert (series.t[0], series.t[-1]) == (0, 7999)


def test_bundle_offline_and_tamper(recorded, tmp_path):
    ledger = recorded[0]
    out = tmp_path / "bundle"
    replay.export(ledger, "c", out)
    assert (out / "verification.json").is_file()
    assert verify(out).ok
    bundle = json.loads((out / "bundle.json").read_text())
    bundle["reviews"][0]["verdict"] = "refuted"
    (out / "bundle.json").write_text(json.dumps(bundle))
    result = verify(out / "bundle.json")
    assert not result.ok and any("Bundle review differs" in p for p in result.problems)


def test_legacy_bundle_and_missing_path_fail_clearly(tmp_path):
    assert not verify(tmp_path / "missing").ok
    (tmp_path / "bundle.json").write_text("{}")
    assert "lacks verification data" in verify(tmp_path).problems[0]


def test_closed_input_directory_has_identical_files_and_bytes(recorded):
    ledger = recorded[0]
    root = ledger.root
    ledger.db.close()
    def files():
        return {p.relative_to(root).as_posix(): sha256_file(p) for p in root.rglob("*") if p.is_file()}
    before = files()
    assert verify(root).ok
    assert files() == before
    assert not (root / "work").exists()


def test_physical_event_order_is_checked_even_if_sequence_numbers_sort(recorded):
    ledger = recorded[0]
    row = ledger.db.execute("SELECT campaign_id, seq, body FROM event WHERE seq=2").fetchone()
    ledger.db.execute("DROP TRIGGER event_no_delete")
    ledger.db.execute("DELETE FROM event WHERE seq=2")
    ledger.db.execute("INSERT INTO event VALUES (?,?,?)", row)
    ledger.db.executescript(SCHEMA)
    result = verify(ledger.root)
    assert not result.ok and any("changed order" in p for p in result.problems)


def test_supported_finding_checks_failed_calibration_run_transitively(recorded):
    ledger, art, _ = recorded
    ledger.add(Run(id="cal_run", campaign_id="c", step_id="s", tool="lookup_calibration",
                   idempotency_key="cal", input_ids=[art.id], status="ok", measurement_ids=["cal"],
                   started_at=0, ended_at=0))
    ledger.add(Measurement(id="cal", run_id="cal_run", quantity="spatial_scale", value=200, unit="px/mm",
                           artifact_ids=[art.id], uncertainty=Uncertainty(value=1, kind="bound", method="fixture")))
    ledger.add(Run(id="derived_run", campaign_id="c", step_id="s", tool="measure",
                   idempotency_key="derived", input_ids=[art.id], status="ok", measurement_ids=["m2"],
                   started_at=0, ended_at=0))
    ledger.add(Measurement(id="m2", run_id="derived_run", quantity="voltage", value=2, unit="V",
                           artifact_ids=[art.id], calibration_ids=["cal"],
                           uncertainty=Uncertainty(value=0.01, kind="bound", method="fixture")))
    ledger.add(Claim(id="k2", campaign_id="c", author="fixture", statement="A second voltage finding.",
                     predicate=Predicate(type="value_within", params={"lo": 1.9, "hi": 2.1, "unit": "V"},
                                         measurement_ids=["m2"]), created_at=time.time()))
    assert gate.review(ledger, "k2").verdict == "supported"
    assert verify(ledger.root).ok
    change(ledger, "run", "cal_run", status="tool_failure")
    # Follow calibrations even when the gate's direct measurement check does not change.
    result = verify(ledger.root)
    assert not result.ok and any("failed run cal_run" in p for p in result.problems)


def test_missing_insert_only_protection_is_reported(recorded):
    ledger = recorded[0]
    ledger.db.execute("DROP TRIGGER event_no_update")
    result = verify(ledger.root)
    assert not result.ok and any("insert-only protections" in p for p in result.problems)


def test_bundle_verifies_without_source_ledger(recorded, tmp_path):
    ledger = recorded[0]
    out = tmp_path / "portable"
    replay.export(ledger, "c", out)
    ledger.db.close()
    # Moving the source is reversible and confirms the exported evidence is self-contained.
    ledger.root.rename(ledger.root.with_name("hidden-ledger"))
    before = {p.relative_to(out).as_posix(): sha256_file(p) for p in out.rglob("*") if p.is_file()}
    assert verify(out).ok
    after = {p.relative_to(out).as_posix(): sha256_file(p) for p in out.rglob("*") if p.is_file()}
    assert before == after


@pytest.mark.parametrize("receipt", [None, [], 42, {"version": 1, "campaign_id": "c", "files": None}])
def test_malformed_bundle_file_list_returns_a_plain_problem(recorded, tmp_path, receipt):
    out = tmp_path / "bundle"
    replay.export(recorded[0], "c", out)
    (out / "verification.json").write_text(json.dumps(receipt))
    result = verify(out)
    assert not result.ok
    assert len(result.problems) == 1 and "evidence file list" in result.problems[0]
    assert "\n" not in result.problems[0]
