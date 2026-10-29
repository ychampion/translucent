"""User recordings use the same tools and gate as published experiments; everything here is offline."""
import copy
import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from meta_sam_parser._mask_codec import _encode_raster
from pydantic import ValidationError

from translucent import cli, experiments, instruments, tools
from translucent.contracts import Transform
from translucent.experiments import Manifest
from translucent.ledger import Ledger, sha256_file
from translucent.runtime import new_campaign


def _video(path, *, change=0):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (320, 128))
    assert writer.isOpened()
    for i in range(12):
        frame = np.zeros((128, 320, 3), np.uint8)
        frame[60:68, 20:60 + 8 * i + change] = 255
        writer.write(frame)
        writer.write(frame)
    writer.release()
    assert path.stat().st_size > 0


@pytest.fixture
def recording(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSLUCENT_SAM", "strict")
    monkeypatch.setattr(experiments, "ROOT", tmp_path / "experiments")
    source = tmp_path / "source"
    source.mkdir()
    _video(source / "movie.mp4")
    (source / "times.csv").write_text("frame,time_s\n" + "".join(f"{i},{100 + 2 * (i // 2)}\n" for i in range(24)))
    (source / "voltage.csv").write_text("time_s,voltage_V\n" +
                                      "".join(f"{5 + 2 * i},{0.1 + abs(i - 7) * 0.2}\n" for i in range(12)))
    manifest = {"id": "ana-cell-01", "title": "Cell 01 optical recording", "citation": "Lab notebook 42, run 7",
                "licence": "CC BY 4.0", "author": "Ana Silva", "video": "movie.mp4",
                "scale": {"um_per_px": 0.5, "uncertainty": 0.01, "source": "Microscope calibration 2026-10-27"},
                "clock": {"timestamps_csv": "times.csv", "source": "Camera acquisition log, run 7"},
                "voltage": {"csv": "voltage.csv", "offset_s": 5, "source": "Potentiostat export, run 7"},
                "notes": "Two playback frames per captured image."}
    ledger = Ledger(tmp_path / "ledger")
    yield SimpleNamespace(source=source, manifest=manifest, ledger=ledger, tmp=tmp_path)
    ledger.db.close()


def _register(r, manifest=None):
    m = manifest or r.manifest
    return experiments.register(r.ledger, m, {p.name: p for p in r.source.iterdir()})


def _context(r, video):
    campaign = new_campaign(r.ledger, "Measure this recording", "User experiment", [video.id], budget_usd=0)
    return tools.Context(r.ledger, campaign.id)


def _call(ctx, name, **args):
    result = tools.call(ctx, name, args)
    assert result.status == "ok", result.summary
    return result


def _finding(ctx, mid, lo=12.5, hi=15.5):
    return _call(ctx, "propose_finding", type="value_within", measurement_ids=[mid],
                 params={"lo": lo, "hi": hi, "unit": "s"},
                 statement="The lowest logged voltage sample occurs 12.5 to 15.5 seconds after the first image.")


def test_register_list_copy_hash_and_idempotency(recording):
    r = recording
    manifest_path = r.source / "manifest.json"
    manifest_path.write_text(json.dumps(r.manifest))
    first = experiments.register(r.ledger, manifest_path, r.source)
    before = len(r.ledger.list("artifact"))
    again = _register(r)
    assert again == first and len(r.ledger.list("artifact")) == before
    assert first.source.uri == "experiment:user/ana-cell-01"
    assert first.shape == [24, 128, 320]
    assert experiments.list_experiments(r.ledger) == [{"experiment": "user/ana-cell-01", "video_id": first.id,
            "label": r.manifest["title"], "citation": r.manifest["citation"], "licence": "CC BY 4.0"}]
    stored = experiments.ROOT / r.manifest["id"]
    assert Manifest.model_validate_json((stored / "manifest.json").read_text()) == Manifest(**r.manifest)
    assert experiments.register(r.ledger, stored / "manifest.json", r.source) == first
    hashes = (stored / "record.json").read_text()
    for name in ("movie.mp4", "times.csv", "voltage.csv"):
        assert (stored / name).read_bytes() == (r.source / name).read_bytes()
        assert sha256_file(stored / name) in hashes
    instruments.check_recording("user/ana-cell-01", sha256_file(r.source / "movie.mp4"))
    with pytest.raises(ValueError, match="recorded video|different video"):
        instruments.check_recording("user/ana-cell-01", "0" * 64)


def test_existing_id_refuses_changed_video_and_records(recording):
    r = recording
    first = _register(r)
    original = (r.source / "movie.mp4").read_bytes()
    _video(r.source / "movie.mp4", change=8)
    with pytest.raises(ValueError, match="video|existing|already"):
        _register(r)
    (r.source / "movie.mp4").write_bytes(original)
    changed = copy.deepcopy(r.manifest)
    changed["scale"]["um_per_px"] = 0.8
    with pytest.raises(ValueError, match="record|manifest|existing|already"):
        _register(r, changed)
    (r.source / "voltage.csv").write_text((r.source / "voltage.csv").read_text().replace("0.1", "0.2"))
    with pytest.raises(ValueError, match="record|content|existing|already|file"):
        _register(r)
    assert experiments.list_experiments(r.ledger)[0]["video_id"] == first.id
    assert instruments.spatial_scale("user/ana-cell-01")["px_per_mm"] == pytest.approx(2000)


def test_same_video_cannot_silently_borrow_another_experiments_records(recording):
    r = recording
    first = _register(r)
    changed = copy.deepcopy(r.manifest)
    changed["id"] = "ana-cell-02"
    changed["scale"]["um_per_px"] = 0.8
    with pytest.raises(ValueError, match="record|experiment|video"):
        _register(r, changed)
    assert experiments.list_experiments(r.ledger)[0]["video_id"] == first.id
    assert tools._experiment(r.ledger, first.id) == "user/ana-cell-01"


@pytest.mark.parametrize("clock", ["timestamps", "interval"])
def test_declared_records_flow_through_tools_and_gate(recording, clock):
    r = recording
    if clock == "interval":
        r.manifest["clock"] = {"interval_s": 2, "uncertainty_s": 0.01, "source": "Acquisition settings, run 7"}
        r.manifest["scale"] = {"bar_px": 100, "bar_um": 50, "uncertainty": 0.01,
                               "source": "Scale bar on calibration image 7"}
    video = _register(r)
    ctx = _context(r, video)
    exp = "user/ana-cell-01"
    times = instruments.frame_times(exp)
    assert times == [2.0 * (i // 2) for i in range(24)]
    assert times[-1] == 22 and tools._video_meta(r.ledger.blob_path(video.id))["fps"] == 30
    assert tools._capture_frames(r.ledger.blob_path(video.id), times) == list(range(0, 24, 2))
    repeat, reliable = tools._repeat_factor(r.ledger.blob_path(video.id))
    assert reliable and repeat == pytest.approx(2)
    if clock == "interval":
        assert instruments.frame_time_uncertainty(exp) == pytest.approx(0.11)
    scale = _call(ctx, "lookup_calibration", video_id=video.id, quantity="spatial_scale")
    timing = _call(ctx, "lookup_calibration", video_id=video.id, quantity="time_base")
    for result, kind in ((scale, "scale"), (timing, "clock")):
        cal = r.ledger.get("measurement", result.data["measurement_id"])
        text = f"{cal.valid_domain} {cal.uncertainty.method}"
        assert "declared by Ana Silva" in text and r.manifest[kind]["source"] in text
        assert video.id in cal.artifact_ids
    inspected = _call(ctx, "inspect_video", video_id=video.id, frame=8)
    assert inspected.images and all(p.is_file() for p in inspected.images)
    assert inspected.data["repeats"] == pytest.approx(2)
    voltage = _call(ctx, "read_cell_voltage", video_id=video.id)
    assert voltage.data["events"]
    event = r.ledger.get("measurement", voltage.data["events"][0])
    assert event.quantity == "elapsed_time" and event.value == pytest.approx(14)
    assert "Ana Silva" in event.uncertainty.method
    assert r.manifest["voltage"]["source"] in event.uncertainty.method
    assert r.ledger.blob_path(voltage.data["source_id"]).read_bytes() == (r.source / "voltage.csv").read_bytes()
    finding = _finding(ctx, event.id)
    assert finding.data["verdict"] == "supported", finding.summary
    checks = r.ledger.list("review", ref=finding.data["claim_id"])[0].checks
    assert any("declared by Ana Silva" in c.detail and r.manifest["clock"]["source"] in c.detail for c in checks)
    assert all(run.cost_usd == 0 for run in r.ledger.list("run"))


def test_no_clock_refuses_timing_despite_video_fps_and_voltage_log(recording):
    r = recording
    del r.manifest["clock"]
    video = _register(r)
    ctx = _context(r, video)
    assert instruments.frame_times("user/ana-cell-01") is None
    assert instruments.time_base_source("user/ana-cell-01") is None
    clock = _call(ctx, "lookup_calibration", video_id=video.id, quantity="time_base")
    assert "No recorded clock" in clock.summary and not clock.data.get("measurement_id")
    inspect = _call(ctx, "inspect_video", video_id=video.id, frame=0)
    assert "No recorded clock" in inspect.summary
    voltage = _call(ctx, "read_cell_voltage", video_id=video.id)
    finding = _finding(ctx, voltage.data["events"][0])
    assert finding.data["verdict"] == "unsupported"
    assert "no recorded time_base" in finding.summary


def test_voltage_log_with_matching_sample_count_is_not_a_frame_clock(recording):
    r = recording
    (r.source / "voltage.csv").write_text("time_s,voltage_V\n" +
        "".join(f"{5 + i * 22 / 23},{0.1 + abs(i - 14) * 0.2}\n" for i in range(24)))
    video = _register(r)
    ctx = _context(r, video)
    voltage = _call(ctx, "read_cell_voltage", video_id=video.id)
    log_clock = r.ledger.get("measurement", voltage.data["time_base_id"])
    assert len(log_clock.value) == video.shape[0] == 24
    scale = _call(ctx, "lookup_calibration", video_id=video.id, quantity="spatial_scale").data["measurement_id"]
    outlines = r.ledger.put_json({}, kind="table", label="Empty test outlines")
    measured = tools.call(ctx, "measure_growth", {"boxes_id": outlines.id, "video_id": video.id,
        "grows_from": "left", "scale_id": scale, "time_base_id": log_clock.id})
    assert measured.status == "tool_failure"
    assert "not a clock for this video's frames" in measured.summary


def test_no_optional_records_stays_uncalibrated(recording):
    r = recording
    for name in ("scale", "clock", "voltage"):
        del r.manifest[name]
    video = _register(r)
    ctx = _context(r, video)
    assert instruments.spatial_scale("user/ana-cell-01") is None
    assert instruments.voltage("user/ana-cell-01") is None
    assert "No scale on record" in _call(ctx, "lookup_calibration", video_id=video.id, quantity="spatial_scale").summary
    assert "No voltage log" in _call(ctx, "read_cell_voltage", video_id=video.id).summary


@pytest.mark.parametrize("clock", ["timestamps", "interval", None])
def test_known_bar_growth_uses_capture_times_and_refuses_playback_timing(recording, clock):
    r = recording
    if clock == "interval":
        r.manifest["clock"] = {"interval_s": 2, "source": "Camera acquisition settings"}
    elif clock is None:
        del r.manifest["clock"]
    video = _register(r)
    ctx = _context(r, video)
    boxes, lines = {}, {}
    for frame in range(24):
        image = tools._frame(r.ledger.blob_path(video.id), frame)
        ys, xs = np.where(image[:, :, 0] > 128)
        x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
        mask = (image[y1:y2 + 1, x1:x2 + 1, 0] > 128).astype(np.uint8)
        height, width = mask.shape
        encoded = _encode_raster(bytes(mask.ravel()), width, height)
        boxes[str(frame)] = {"1": [x1, y1, x2, y2, 1.0]}
        lines[str(frame)] = (f"<{frame}f>1<|box;x1={x1};y1={y1};x2={x2};y2={y2};w=320;h=128;c=1|>"
                             f"<|mask;x=0;y=0;c=1;data={height},{width},{encoded}|>")
    masks = r.ledger.put_json(lines, kind="masks", transform=Transform(op="threshold_bright_bar", parent_ids=[video.id]))
    outlines = r.ledger.put_json(boxes, kind="table", transform=Transform(op="mask_bounds", parent_ids=[masks.id]))
    scale = _call(ctx, "lookup_calibration", video_id=video.id, quantity="spatial_scale").data["measurement_id"]
    timing = _call(ctx, "lookup_calibration", video_id=video.id, quantity="time_base").data.get("measurement_id")
    args = {"boxes_id": outlines.id, "video_id": video.id, "grows_from": "left", "scale_id": scale}
    if timing:
        args["time_base_id"] = timing
    measured = _call(ctx, "measure_growth", **args)
    assert "1" in measured.data["ids"], measured.summary
    ids = measured.data["ids"]["1"]
    velocity = r.ledger.get("measurement", ids["velocity"])
    if clock:
        assert velocity.value == pytest.approx(2.0)
        assert len(r.ledger.get("measurement", ids["length"]).value) == 12
        assert velocity.time_range == (0, 22)
    else:
        assert velocity.value > 50
    finding = _call(ctx, "propose_finding", type="value_within", measurement_ids=[velocity.id],
                    params={"lo": velocity.value - 0.1, "hi": velocity.value + 0.1, "unit": "um/s"},
                    statement="The bar grows at the measured speed.")
    assert finding.data["verdict"] == ("supported" if clock else "unsupported"), finding.summary
    if clock:
        precise = _call(ctx, "propose_finding", type="value_within", measurement_ids=[velocity.id],
                        params={"lo": 1.999, "hi": 2.001, "unit": "um/s"},
                        statement="The bar grows between 1.999 and 2.001 micrometres per second.")
        assert precise.data["verdict"] == "inconclusive", precise.summary
    else:
        assert "no recorded time_base" in finding.summary


@pytest.mark.parametrize("field,record", [
    ("clock", {"interval_s": 2}), ("clock", {"interval_s": 2, "source": "  "}),
    ("clock", {"timestamps_csv": "times.csv", "source": ""}),
    ("scale", {"um_per_px": 0.5}), ("scale", {"bar_px": 100, "bar_um": 50, "source": "\t"}),
    ("voltage", {"csv": "voltage.csv", "offset_s": 5, "source": ""}),
])
def test_declarations_require_their_source(recording, field, record):
    manifest = copy.deepcopy(recording.manifest)
    manifest[field] = record
    with pytest.raises(ValidationError):
        Manifest(**manifest)


@pytest.mark.parametrize("field,record", [
    ("clock", {"interval_s": 0, "source": "lab"}),
    ("clock", {"interval_s": float("nan"), "source": "lab"}),
    ("clock", {"interval_s": 2, "uncertainty_s": -1, "source": "lab"}),
    ("clock", {"interval_s": 2, "timestamps_csv": "times.csv", "source": "lab"}),
    ("clock", {"source": "lab"}),
    ("scale", {"um_per_px": -1, "source": "lab"}),
    ("scale", {"um_per_px": 1e-310, "source": "lab"}),
    ("scale", {"bar_px": 1e-300, "bar_um": 1e300, "source": "lab"}),
    ("scale", {"bar_px": 0, "bar_um": 50, "source": "lab"}),
    ("scale", {"um_per_px": 0.5, "bar_px": 100, "bar_um": 50, "source": "lab"}),
    ("scale", {"bar_px": 100, "source": "lab"}),
    ("voltage", {"csv": "voltage.csv", "offset_s": float("inf"), "source": "lab"}),
])
def test_invalid_or_ambiguous_declarations_are_rejected(recording, field, record):
    manifest = copy.deepcopy(recording.manifest)
    manifest[field] = record
    with pytest.raises(ValidationError):
        Manifest(**manifest)


@pytest.mark.parametrize("id", ["ab", "../escape", "Uppercase", "a" * 65, "bad.id", "-start"])
def test_invalid_ids_are_rejected(recording, id):
    manifest = {**recording.manifest, "id": id}
    with pytest.raises(ValidationError):
        Manifest(**manifest)


@pytest.mark.parametrize("name", ["../movie.mp4", "/tmp/movie.mp4", "sub/movie.mp4", "sub\\movie.mp4"])
def test_manifest_only_accepts_plain_filenames(recording, name):
    manifest = {**recording.manifest, "video": name}
    with pytest.raises(ValidationError):
        Manifest(**manifest)


@pytest.mark.parametrize("kind,contents", [
    ("times", "image,seconds\n0,0\n"),
    ("times", "frame,time_s\n0,0\n1,2\n"),
    ("times", "frame,time_s\n" + "".join(f"{i},{-i}\n" for i in range(24))),
    ("times", "frame,time_s\n" + "".join(f"{i},nan\n" for i in range(24))),
    ("times", "frame,time_s\n" + "".join(f"0,{i}\n" for i in range(24))),
    ("voltage", "time_s,volts\n0,1\n2,0\n"),
    ("voltage", "time_s,voltage_V\n0,1\n2,nan\n"),
    ("voltage", "time_s,voltage_V\n2,1\n0,0\n"),
    ("voltage", "time_s,voltage_V\n0,1\n0,0\n"),
    ("voltage", "time_s,voltage_V\n"),
    ("voltage", "time_s,voltage_V\n0,1,extra\n2,0\n"),
])
def test_bad_csv_is_refused_before_import(recording, kind, contents):
    r = recording
    (r.source / f"{kind}.csv").write_text(contents)
    with pytest.raises(ValueError, match="CSV|csv|time|frame|voltage|row|column"):
        _register(r)
    assert experiments.list_experiments(r.ledger) == []
    assert not (experiments.ROOT / r.manifest["id"]).exists()


def test_missing_video_and_corrupted_stored_records_fail_closed(recording):
    r = recording
    path = r.source / "movie.mp4"
    content = path.read_bytes()
    path.unlink()
    with pytest.raises((ValueError, OSError)):
        _register(r)
    path.write_bytes(content)
    _register(r)
    stored_csv = experiments.ROOT / r.manifest["id"] / "times.csv"
    stored_csv.write_text(stored_csv.read_text().replace("100", "101"))
    with pytest.raises(ValueError, match="changed|hash|integrity|modified"):
        instruments.frame_times("user/ana-cell-01")


def test_different_images_cannot_share_a_timestamp(recording):
    r = recording
    path = r.source / "times.csv"
    path.write_text(path.read_text().replace("2,102\n", "2,100\n"))
    with pytest.raises(ValueError, match="timestamp|time|repeat|image"):
        _register(r)
    assert experiments.list_experiments(r.ledger) == []


def test_changed_capture_mapping_is_not_a_new_clock(recording):
    r = recording
    r.manifest["clock"] = {"interval_s": 2, "source": "Camera acquisition settings"}
    _register(r)
    path = experiments.ROOT / r.manifest["id"] / "record.json"
    record = json.loads(path.read_text())
    record["capture_indices"] = list(range(24))
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="changed|hash|integrity|modified"):
        instruments.frame_times("user/ana-cell-01")


def test_finite_interval_cannot_overflow_the_recorded_time_range(recording):
    r = recording
    r.manifest["clock"] = {"interval_s": 1e308, "source": "Mistyped acquisition settings"}
    with pytest.raises(ValueError, match="finite|range|interval|time"):
        _register(r)
    assert experiments.list_experiments(r.ledger) == []


def test_two_frame_recording_can_be_inspected(recording):
    r = recording
    path = r.source / "movie.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (320, 128))
    assert writer.isOpened()
    for width in (40, 48):
        frame = np.zeros((128, 320, 3), np.uint8)
        frame[60:68, 20:20 + width] = 255
        writer.write(frame)
    writer.release()
    del r.manifest["clock"], r.manifest["voltage"]
    video = _register(r)
    ctx = _context(r, video)
    inspected = _call(ctx, "inspect_video", video_id=video.id, frame=1)
    assert inspected.data["frames"] == 2 and inspected.images[0].is_file()
    assert "No recorded clock" in inspected.summary
    assert tools._repeat_factor(r.ledger.blob_path(video.id))[1] is False


def test_regular_small_changes_are_not_proof_of_playback_repeats(tmp_path):
    path = tmp_path / "distinct-images.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"FFV1"), 30, (64, 64))
    assert writer.isOpened()
    for i in range(24):
        writer.write(np.full((64, 64, 3), 11 * (i // 2) + i % 2, np.uint8))
    writer.release()
    assert experiments._captures(path) == list(range(24))
    repeats, reliable = tools._repeat_factor(path)
    assert reliable and repeats > 1.2
    with pytest.raises(ValueError, match="timestamps"):
        experiments._captures(path, interval=True)


def _cli_args(r):
    return ["add-experiment", "--id", "cli-cell-01", "--video", str(r.source / "movie.mp4"),
            "--title", "CLI recording", "--citation", "Notebook 42", "--licence", "CC BY 4.0",
            "--author", "Ana Silva", "--ledger", str(r.tmp / "cli-ledger")]


def test_cli_register_and_list(recording, capsys):
    r = recording
    args = _cli_args(r) + ["--scale-bar-px", "100", "--scale-bar-um", "50", "--scale-source", "Calibration image",
                           "--interval-s", "2", "--interval-uncertainty-s", "0.01", "--clock-source", "Camera settings",
                           "--voltage", str(r.source / "voltage.csv"), "--voltage-offset-s", "5",
                           "--voltage-source", "Potentiostat log"]
    assert cli.main(args) == 0
    assert "Registered user/cli-cell-01" in capsys.readouterr().out
    assert cli.main(["experiments", "--ledger", str(r.tmp / "cli-ledger")]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1 and rows[0]["experiment"] == "user/cli-cell-01"


@pytest.mark.parametrize("extra,reason", [(["--interval-s", "2"], "source"),
    (["--scale-um-per-px", "0.5"], "source"), (["--scale-bar-px", "100"], "scale-bar-um"),
    (["--interval-uncertainty-s", "0.01"], "interval"),
])
def test_cli_missing_fields_are_plain_errors(recording, capsys, extra, reason):
    with pytest.raises(SystemExit) as exc:
        cli.main(_cli_args(recording) + extra)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert reason in err.lower() and "Traceback" not in err and "pydantic.dev" not in err


def test_cli_malformed_csv_is_a_plain_error(recording, capsys):
    r = recording
    (r.source / "times.csv").write_text("frame,time_s\n0,not-a-time\n")
    with pytest.raises(SystemExit) as exc:
        cli.main(_cli_args(r) + ["--timestamps", str(r.source / "times.csv"), "--clock-source", "Camera log"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "time" in err.lower() and "Traceback" not in err
