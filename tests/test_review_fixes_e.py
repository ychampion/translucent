"""Measurement-path regressions from the 2026-10-28 independent review; no network or paid calls."""
import json
from types import SimpleNamespace

import numpy as np
import pytest
from meta_sam_parser._mask_codec import _encode_raster

from translucent import instruments, qc, tools
from translucent.contracts import Source, Transform
from translucent.ledger import Ledger
from translucent.runtime import new_campaign

LU = instruments.LU
M4 = "reisecker2023/movie4"


@pytest.fixture
def world(tmp_path, monkeypatch):
    worlds, meta = [], {}
    monkeypatch.setenv("TRANSLUCENT_SAM", "strict")
    monkeypatch.setattr(tools, "_video_meta", lambda path: meta[str(path)])
    monkeypatch.setattr(tools, "_repeat_factor", lambda path: (1.0, False))
    monkeypatch.setattr(tools, "_frame", lambda path, frame: np.zeros((938, 1280, 3), np.uint8))
    def make(exp=M4):
        L = Ledger(tmp_path / str(len(worlds)))
        times = instruments.frame_times(exp)
        W, H = (1280, 938) if exp == LU else (1920, 1080)
        video = L.put_bytes(exp.encode(), kind="video", media_type="video/mp4", shape=[len(times), H, W],
                            source=Source(uri=f"experiment:{exp}"))
        meta[str(L.blob_path(video.id))] = {"width": W, "height": H, "frames": len(times), "fps": 30}
        campaign = new_campaign(L, "Offline measurement regression", "review E", [video.id], budget_usd=0)
        ctx = tools.Context(L, campaign.id)
        w = SimpleNamespace(L=L, ctx=ctx, vid=video.id, times=times)
        w.call = lambda name, **args: tools.call(ctx, name, args)
        worlds.append(w)
        return w
    yield make
    for w in worlds:
        w.L.db.close()


def track(w, frames, *, merged=False, faint=(), missing=(), axis=0):
    lines, boxes = {}, {}
    for i, f in enumerate(frames):
        length = 120 + 5 * i
        m = np.ones((10, length), np.uint8)
        if merged:
            m = np.zeros((24, length), np.uint8)
            m[:10, :length // 2] = 1
            m[14:] = 1
        if axis:
            m = m.T
        h, width = m.shape
        c = 0.3 if f in faint else 0.9
        boxes[str(f)] = {"1": [100, 200, 100 + width - 1, 200 + h - 1, c]}
        payload = _encode_raster(bytes(m.ravel()), width, h)
        line = (f"<{f}f>1<|box;x1=100;y1=200;x2={99 + width};y2={199 + h};w=1920;h=1080;c={c}|>"
                f"<|mask;x=0;y=0;c={c};data={h},{width},{payload}|>")
        if f not in missing:
            lines[str(f)] = line
    raw = w.L.put_json(lines, kind="masks", transform=Transform(op="test_outlines", parent_ids=[w.vid]))
    return w.L.put_json(boxes, kind="table", transform=Transform(op="extract_boxes", parent_ids=[raw.id])).id


def measure(w, boxes, side="left"):
    scale = w.call("lookup_calibration", video_id=w.vid, quantity="spatial_scale").data["measurement_id"]
    clock = w.call("lookup_calibration", video_id=w.vid, quantity="time_base").data["measurement_id"]
    return w.call("measure_growth", boxes_id=boxes, video_id=w.vid, scale_id=scale, time_base_id=clock,
                  grows_from=side)


def assert_not_measured(w, result, reason):
    assert not result.data.get("ids"), result.summary
    assert reason in result.summary.lower(), result.summary
    assert not [m for m in w.L.list("measurement") if m.quantity == "growth_velocity"]


def test_h1_lu_coarse_plateaus_are_not_captured_image_repeats(world):
    w = world(LU)
    frames = list(range(300, 421, 4))
    assert len(set(w.times[f] for f in frames)) < len(frames)
    result = measure(w, track(w, frames))
    assert_not_measured(w, result, "clock too coarse")
    assert "1224" in result.summary


@pytest.mark.parametrize("side,axis", [("right", 0), ("bottom", 1)])
def test_h2_opposite_root_cannot_bypass_merged_check(world, side, axis):
    w = world()
    result = measure(w, track(w, [0, 10, 20, 30, 40], merged=True, axis=axis), side)
    assert_not_measured(w, result, "two separate bodies")


def test_h3_faint_frames_cannot_turn_off_merged_check(world):
    w = world()
    result = measure(w, track(w, list(range(0, 100, 10)), merged=True, faint=(0, 10, 20)))
    assert_not_measured(w, result, "two separate bodies")


@pytest.mark.parametrize("missing", [(20,), (0, 10, 20, 30, 40)])
def test_h3_unchecked_outlines_are_not_measurements(world, missing):
    w = world()
    result = measure(w, track(w, [0, 10, 20, 30, 40], missing=missing))
    assert_not_measured(w, result, "merge check")


def test_h3_unreadable_mask_is_a_plain_refusal(world, monkeypatch):
    w = world()
    boxes = track(w, [0, 10, 20, 30, 40])
    def unreadable(*args, **kwargs):
        raise ValueError("broken outline bytes")
    monkeypatch.setattr(qc, "merged_track", unreadable)
    result = measure(w, boxes)
    assert_not_measured(w, result, "merge check")


def test_clean_fit_is_unchanged_with_explicit_single_body_masks(world):
    from evals.faults import filament
    w = world()
    curve, boxes, raw, seen = filament(), {}, {}, set()
    for f, t in enumerate(w.times):
        if t in seen:
            continue
        seen.add(t)
        b = [float(f"{x:.1f}") for x in curve(t)]
        boxes[str(f)] = {"1": b}
        width = int(np.ceil(b[2] - b[0])) + 1
        m = np.ones((13, width), np.uint8)
        payload = _encode_raster(bytes(m.ravel()), width, 13)
        raw[str(f)] = (f"<{f}f>1<|box;x1={int(np.floor(b[0]))};y1=300;x2=1500;y2=312;w=1920;h=1080;c=0.9|>"
                       f"<|mask;x=0;y=0;c=0.9;data=13,{width},{payload}|>")
    masks = w.L.put_json(raw, kind="masks", transform=Transform(op="test_outlines", parent_ids=[w.vid]))
    bx = w.L.put_json(boxes, kind="table", transform=Transform(op="test_subpixel_boxes", parent_ids=[masks.id],
                                                               params={"raster_rounding_px": 1}))
    result = measure(w, bx.id, "right")
    assert result.status == "ok" and result.data.get("ids"), result.summary
    m = w.L.get("measurement", result.data["ids"]["1"]["velocity"])
    assert f"{m.value:.3f}" == "6.200" and f"{m.uncertainty.value:.3f}" == "0.018"


@pytest.fixture
def lu_voltage(world):
    w = world(LU)
    result = w.call("read_cell_voltage", video_id=w.vid)
    assert result.status == "ok", result.summary
    markers = {m.label: m for m in w.L.list("measurement") if m.id in result.data["events"]}
    return w, result, markers


def finding(w, m, lo, hi):
    result = w.call("propose_finding", type="value_within", measurement_ids=[m.id],
                    params={"lo": lo, "hi": hi, "unit": "h"}, statement="Check the recorded electrical marker.")
    assert result.status == "ok", result.summary
    return result.data["verdict"]


def test_m1_first_crossing_carries_voltage_sensitivity_and_still_refutes_10h(lu_voltage):
    w, result, markers = lu_voltage
    m = next(m for name, m in markers.items() if name.startswith("cycle 1") and name.endswith("below 0 V"))
    assert m.value == pytest.approx(7.435)
    assert m.uncertainty.kind == "bound" and m.uncertainty.value > 0.6
    assert "10 mV" in m.uncertainty.method and "not stated" in m.uncertainty.method
    assert finding(w, m, 7.43, 7.44) == "inconclusive"
    assert finding(w, m, 9.5, 10.5) == "refuted"
    assert "10 mV" in result.summary


@pytest.mark.parametrize("suffix", ["plateau reached", "lowest voltage"])
def test_m1_other_voltage_markers_do_not_claim_sample_only_bounds(lu_voltage, suffix):
    w, _, markers = lu_voltage
    m = next(m for name, m in markers.items() if name.startswith("cycle 1") and name.endswith(suffix))
    assert m.uncertainty.value > 2 / 3600
    assert finding(w, m, m.value - 0.001, m.value + 0.001) != "supported"


def test_m5_cycle2_has_no_movie_ancestry_even_through_its_clock(lu_voltage):
    w, result, markers = lu_voltage
    for name, m in markers.items():
        if name.startswith("cycle 2"):
            assert w.vid not in w.L.recordings(m.id)
            assert w.vid not in m.artifact_ids and result.data["source_id"] in m.artifact_ids
            assert all(w.vid not in w.L.recordings(cid) for cid in m.calibration_ids)
            assert "outside" in m.valid_domain.lower()
            assert finding(w, m, m.value - 0.01, m.value + 0.01) != "supported"


def test_l1_box_guard_works_in_lu_field_of_view():
    why = tools._flag((100, 200, 300, 282, 0.9), 1280, 938, 0, 1000 / 3923.5)
    assert why and "no mask" in why


def test_l1_high_resolution_dark_core_is_not_a_second_body():
    m = np.ones((40, 400), np.uint8)
    m[19:21] = 0
    assert not qc.two_bodies(m, 0, "left", um_per_px=1000 / 3923.5)


def test_l6_inspection_states_coarse_clock_bound(world):
    w = world(LU)
    result = w.call("inspect_video", video_id=w.vid, frame=300)
    assert result.status == "ok", result.summary
    assert "coarse" in result.summary.lower() and "1224" in result.summary
    assert "recorded clock for every frame" not in result.summary


def test_l7_log_source_is_provided_by_instrument(world, monkeypatch, tmp_path):
    w = world()
    path = tmp_path / "other_voltage.csv"
    path.write_text("t_s,voltage_V\n0,1\n2,0\n4,-1\n")
    series = {"t": [0, 2, 4], "v": [1, 0, -1], "dt": 2.0, "clock": "log", "segments": [],
              "method": "Other experiment log", "source": {"path": str(path), "uri": "fixture:other-log",
              "label": "Other full voltage log", "citation": "Other experiment", "licence": "CC0"}}
    monkeypatch.setattr(instruments, "voltage", lambda exp: series)
    result = w.call("read_cell_voltage", video_id=w.vid)
    assert result.status == "ok", result.summary
    source = w.L.get("artifact", result.data["source_id"])
    assert w.L.blob_path(source.id).read_bytes() == path.read_bytes()
    assert source.source.uri == "fixture:other-log" and source.source.citation == "Other experiment"
    assert "10 h onset" not in result.summary


def test_movie4_shorts_overlap_the_papers_windows(world):
    """Both shorts land in the paper's timing windows and noise near 0 V after the second short is not a recovery."""
    gt = json.loads((instruments.ROOT / "data" / "groundtruth" / "reisecker2023.json").read_text(encoding="utf-8"))
    paper = next(q["value"] for q in gt["quantities"] if isinstance(q.get("value"), dict)
                 and "second_short_h" in q["value"])
    truth = [[(h - 41.32) * 3600 for h in paper[k]] for k in ("first_short_V_to_0_h", "second_short_h")]
    w = world(M4)
    result = w.call("read_cell_voltage", video_id=w.vid)
    events = [w.L.get("measurement", e) for e in result.data["events"]]
    assert [m.quantity for m in events] == ["voltage_collapse_time", "voltage_recovery_time", "voltage_collapse_time"]
    for m, (lo, hi) in zip([events[0], events[2]], truth):
        assert m.value - m.uncertainty.value <= hi and m.value + m.uncertainty.value >= lo, (m.value, lo, hi)


def test_bare_content_address_reaches_the_same_video(world):
    """A model that drops "sha256:" from a video id (seen on Spark Open, 2026-10-28) still reaches that video."""
    w = world(LU)
    result = w.call("read_cell_voltage", video_id=w.vid.removeprefix("sha256:"))
    assert result.status == "ok", result.summary
    assert w.vid in w.L.get("run", result.run_id).input_ids


def test_campaign_stops_when_the_model_stops_calling_tools(world):
    from evals.fake_llm import FakeClient
    from translucent.runtime import Campaigner, Settings
    w = world(LU)
    campaign = new_campaign(w.L, "Stuck model", "stall", [w.vid], budget_usd=1.0)
    camp = Campaigner(w.L, campaign, Settings(profile="dev", max_steps=30))
    seen = []
    def runaway(messages, tools_):
        seen.append(max(len(m.get("content") or "") for m in messages if m["role"] == "assistant") if len(messages) > 2 else 0)
        return "word " * 6000, []
    fake = FakeClient(runaway, usd_per_call=0.01)
    camp.clients = {"fireworks": fake, "meta": fake}
    assert camp.run()["status"] == "stalled" and len(fake.calls) == 3
    assert max(seen) <= 1500  # the runaway text is not fed back whole


def test_critic_sees_the_voltage_log_for_a_lu_timing_finding(lu_voltage):
    """Markers are hours into a charge; the critic must get the log with the mark, not the latest movie frame."""
    from translucent.runtime import Campaigner, Settings
    w, result, markers = lu_voltage
    m = markers["cycle 1 charge (2 mA/cm2): below 0 V"]
    claim = w.call("propose_finding", type="value_within", measurement_ids=[m.id],
                   params={"lo": 6.0, "hi": 9.0, "unit": "h"}, statement="The first charge drops below 0 V by 9 h.")
    camp = Campaigner(w.L, w.L.get("campaign", w.ctx.campaign_id), Settings(profile="dev"))
    image = camp._evidence_image(claim.data["claim_id"])
    assert image is not None and image.name.endswith("_voltage.png")


def test_spark_open_orchestrator_is_not_offered_the_code_tool(world):
    from evals.fake_llm import FakeClient
    from translucent.runtime import Campaigner, Settings
    w = world(LU)
    offered = []
    def finish(messages, tools_):
        offered.append({t["function"]["name"] for t in tools_})
        return "", [("finish", {"answer": "done", "not_proven": ""})]
    for profile in ("dev", "meta"):
        camp = Campaigner(w.L, new_campaign(w.L, "Tools offered", profile, [w.vid], budget_usd=1.0), Settings(profile=profile))
        fake = FakeClient(finish)
        camp.clients = {"fireworks": fake, "meta": fake}
        camp.run()
    assert "run_analysis_code" not in offered[0] and "run_analysis_code" in offered[1]


def test_all_faint_outlines_say_why_instead_of_a_merge_check(world):
    """Every outline low-confidence: the agent is told that, not '0 of 0 checked' (it hunted for a naming bug)."""
    w = world()
    frames = list(range(0, 60, 10))
    res = measure(w, track(w, frames, faint=frames))
    assert_not_measured(w, res, "none of its 6 outlines is credible (6 low confidence; best confidence 0.30)")
    assert "merge check" not in res.summary


def test_an_unknown_outline_number_is_named(world):
    w = world()
    res = w.call("measure_growth", boxes_id=track(w, [0, 10, 20]), video_id=w.vid, objects=["#8"],
                 scale_id=w.call("lookup_calibration", video_id=w.vid, quantity="spatial_scale").data["measurement_id"],
                 time_base_id=w.call("lookup_calibration", video_id=w.vid, quantity="time_base").data["measurement_id"],
                 grows_from="left")
    assert "#8: no outline has this number" in res.summary


def test_unit_mismatch_result_tells_the_planner_the_fix(lu_voltage):
    # a live Meta campaign (2026-10-29) stated a finding in seconds over two times recorded in hours
    w, result, markers = lu_voltage
    m = next(m for name, m in markers.items() if name.startswith("cycle 1") and name.endswith("below 0 V"))
    res = w.call("propose_finding", type="value_within", measurement_ids=[m.id],
                 params={"lo": 0, "hi": 30000, "unit": "s"}, statement="Check the marker in seconds.")
    assert res.data["verdict"] == "unsupported" and "Give the finding's unit as the measurements' unit" in res.summary
    rv = next(r for r in w.L.list("review") if r.claim_id == res.data["claim_id"])
    assert "Give the finding's unit" not in rv.reason  # the gate's recorded wording is unchanged
