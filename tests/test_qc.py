"""translucent.qc: two filaments merged into one outline are caught; single filaments are not."""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from meta_sam_parser._mask_codec import _encode_raster

from translucent import qc, tools

ROOT = Path(__file__).resolve().parents[1]
SAM = ROOT / "data" / "recordings" / "sam"


def canvas(h=160, w=360):
    return np.zeros((h, w), np.uint8)


def filament(m, y_root, length, width, angle_deg=0.0, curve=0.0, x_root=None):
    """Draw a filament growing left from the right edge (root) at row y_root."""
    x_root = m.shape[1] - 1 if x_root is None else x_root
    s = np.linspace(0, length, max(2, int(length)))
    a = np.deg2rad(angle_deg)
    x = x_root - s * np.cos(a)
    y = y_root + s * np.sin(a) + curve * np.sin(np.pi * s / max(length, 1)) * s / max(length, 1)
    pts = np.stack([x, y], 1).round().astype(np.int32)
    cv2.polylines(m, [pts], False, 1, thickness=width)
    return m


def crop(m):
    ys, xs = np.nonzero(m)
    return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def merged(gap, ratio=2.0, width=10, tilt=0.0, spread=0.0, length=240):
    """Two filaments from separate roots on the electrode: common tilt, diverging by 2*spread degrees."""
    m = canvas(260)
    filament(m, 110, length / ratio, width, tilt - spread)
    filament(m, 110 + width + gap, length, width, tilt + spread)
    return crop(m)


@pytest.mark.parametrize("gap", [3, 6, 15, 40, 90])
@pytest.mark.parametrize("ratio", [1.0, 1.5, 2.0, 4.0])
def test_merged_parallel_caught(gap, ratio):
    m = canvas(220)
    filament(m, 30, 240 / ratio, 10)
    filament(m, 40 + gap, 240, 10)
    assert qc.two_bodies(crop(m), 0, "right")


@pytest.mark.parametrize("tilt", [-12, -5, 5, 12])
@pytest.mark.parametrize("spread", [0, 3])
@pytest.mark.parametrize("gap", [4, 20])
def test_merged_tilted_caught(tilt, spread, gap):
    assert qc.two_bodies(merged(gap, 2.0, tilt=tilt, spread=spread), 0, "right")


@pytest.mark.parametrize("grows_from,axis,k", [("left", 0, 2), ("top", 1, 1), ("bottom", 1, 3)])
def test_merged_any_growth_direction(grows_from, axis, k):
    """Rotate the right-growing merged mask so its root is on another side."""
    assert qc.two_bodies(np.rot90(merged(8), k), axis, grows_from)


def test_merged_wrong_axis_is_not_judged_merged():
    """Two side-by-side filaments seen along the wrong axis are one body per cross-section near the root."""
    assert not qc.two_bodies(merged(8, 1.0), 1, "bottom")


@pytest.mark.parametrize("width", [3, 8, 20, 45, 80])
@pytest.mark.parametrize("curve", [0.0, 25.0])
def test_single_filament_not_caught(width, curve):
    m = filament(canvas(220), 60, 260, width, 0, curve)
    assert not qc.two_bodies(crop(m), 0, "right")


@pytest.mark.parametrize("angle", [-20, 8, 30])
def test_single_tilted_not_caught(angle):
    assert not qc.two_bodies(crop(filament(canvas(260), 120, 240, 12, angle)), 0, "right")


@pytest.mark.parametrize("seed", range(8))
def test_single_noisy_not_caught(seed):
    """Ragged edges and 1 px holes are mask noise, not a second filament."""
    rng = np.random.default_rng(seed)
    m = filament(canvas(220), 80, 250, int(rng.integers(6, 50)), float(rng.uniform(-10, 10)), float(rng.uniform(0, 20)))
    edge = cv2.dilate(m, np.ones((3, 3), np.uint8)) - cv2.erode(m, np.ones((3, 3), np.uint8))
    flip = (rng.random(m.shape) < 0.3) & (edge > 0)
    m = np.where(flip, 1 - m, m).astype(np.uint8)
    holes = (rng.random(m.shape) < 0.01) & (cv2.erode(m, np.ones((5, 5), np.uint8)) > 0)
    m[holes] = 0
    assert not qc.two_bodies(crop(m), 0, "right")


def test_side_branch_not_caught():
    """A dendrite branching off its trunk away from the root is one filament."""
    m = filament(canvas(220), 100, 260, 10)
    filament(m, 100, 120, 8, 25, x_root=m.shape[1] - 1 - 120)
    assert not qc.two_bodies(crop(m), 0, "right")


def test_short_neighbour_is_a_blind_spot():
    """Documented limit: a neighbour under a fifth of the filament's length is not told apart."""
    m = canvas(220)
    filament(m, 30, 30, 10)
    filament(m, 60, 240, 10)
    assert not qc.two_bodies(crop(m), 0, "right")


def test_too_wide_box_only():
    um = 1000 / 161.2
    assert qc.too_wide((1300, 300, 1500, 382, 0.9), 0, um)           # two filaments 70 px apart, one box
    assert not qc.too_wide((1300, 300, 1500, 312, 0.9), 0, um)       # one 12 px filament
    assert tools._flag((1300, 300, 1500, 382, 0.9), 1920, 1080, 0) is None       # no scale given: rule off
    assert "no mask to show it is one body" in tools._flag((1300, 300, 1500, 382, 0.9), 1920, 1080, 0, um)


def sam_line(f, objs):
    """SAM output line with box + mask records; objs: id -> (x1, y1, mask)."""
    parts = []
    for o, (x1, y1, m) in objs.items():
        h, w = m.shape
        payload = _encode_raster(bytes(m.astype(np.uint8).ravel()), w, h)
        parts.append(f"{o}<|box;x1={x1};y1={y1};x2={x1 + w - 1};y2={y1 + h - 1};w=1920;h=1080;c=0.9|>"
                     f"<|mask;x=0;y=0;c=0.9;data={h},{w},{payload}|>")
    return f"<{f}f>" + ",".join(parts)


def test_merged_track_end_to_end():
    single = crop(filament(canvas(60, 200), 20, 150, 10))
    two = merged(5, 1.5, length=150)
    lines = qc.frame_lines({"video": "\n".join(sam_line(f, {"1": (1300, 300, two), "2": (1300, 500, single)})
                                                for f in range(0, 40, 5))})
    assert set(lines) == set(range(0, 40, 5))
    assert qc.masks_in(lines[0])["1"].shape == two.shape
    why, seen = qc.merged_track(lines, "1", list(range(40)), 0, "right")
    assert why and "two separate bodies" in why and seen == 8
    assert qc.merged_track(lines, "2", list(range(40)), 0, "right") == (None, 8)
    assert qc.merged_track({}, "1", list(range(40)), 0, "right") == (None, 0)   # no masks: box-only rules apply


def _credible_real_outlines():
    """Every real recorded outline that passes the existing _flag rules (so it could become evidence)."""
    out = []
    for p in sorted(SAM.glob("*.json")):
        d = json.loads(p.read_text())
        boxes = tools._boxes(d["text"]) if d["kind"] == "video" else tools._boxes(d["text"], frame=0)
        for f, objs in boxes.items():
            for o, b in objs.items():
                if not tools._flag(b, 1920, 1080, 0):
                    out.append((p.name, d["kind"], f, o, b))
    return out


# Movie 1's only SAM video track (one dendrite); live campaigns also record Movie 4 tracks
M1_TRACK_STREAM = "stream:718b38692"
M4_STREAM = "stream:6e44c94da"  # Movie 4's encoded-packet hash (recordings.stream_id), prefix


def test_real_movie1_track_not_merged():
    """Regression on a real recorded outline: the Movie 1 SAM video track (one dendrite) is not merged."""
    p = next(p for p in sorted(SAM.glob("*.json")) if (d := json.loads(p.read_text()))["kind"] == "video"
             and d.get("source", "").startswith(M1_TRACK_STREAM))
    text = json.loads(p.read_text())["text"]
    lines = qc.frame_lines({"video": text})
    boxes = tools._boxes(text)
    for g in ("right", "left"):
        why, seen = qc.merged_track(lines, "1", sorted(boxes), 0, g)
        assert why is None and seen == len(lines)


def test_real_credible_image_outlines_not_merged():
    """Every real Movie 4 image outline that passes the other checks holds one body (any growth side)."""
    # Movie 4's filaments lie within y ~230-560 px (data/groundtruth/reisecker2023.json "geometry"); live campaigns
    # also outlined grid stripes outside that band, whose blobs are rightly flagged and are not filaments
    m4 = {p.name for p in SAM.glob("*.json") if json.loads(p.read_text()).get("source", "").startswith(M4_STREAM)}
    picked = [(n, f, o) for n, k, f, o, b in _credible_real_outlines()
              if k == "image" and n in m4 and 230 <= (b[1] + b[3]) / 2 <= 560]
    assert picked
    for name in dict.fromkeys(n for n, _, _ in picked):
        line = json.loads((SAM / name).read_text())["text"].splitlines()[0]
        masks = qc.masks_in(line)
        for _, _, o in (p for p in picked if p[0] == name):
            m = masks[o]
            assert not any(qc.two_bodies(m, ax, g) for g, ax in (("right", 0), ("left", 0))), (name, o)


def test_one_brief_merge_cannot_hide_between_sampled_frames():
    single = crop(filament(canvas(60, 200), 20, 150, 10))
    two = merged(5, 1.5, length=150)
    lines = qc.frame_lines({"video": "\n".join(sam_line(f, {"1": (1300, 300, two if f == 3 else single)})
                                                for f in range(10))})
    why, seen = qc.merged_track(lines, "1", list(range(10)), 0, "right")
    assert why and seen == 10


def test_mask_joined_only_at_electrode_is_still_merged():
    m = merged(4, 2.0, length=160)
    m[:, -2:] = 1
    assert qc.two_bodies(m, 0, "right")


def test_mask_coverage_is_for_the_exact_frame_and_object():
    single = crop(filament(canvas(60, 200), 20, 150, 10))
    line = sam_line(0, {"11": (1300, 300, single)})
    assert qc.has_mask(line, "11")
    assert not qc.has_mask(line, "1")
    assert not qc.has_mask("", "11")


def test_wide_single_with_mask_is_measured_and_merged_masks_are_not(tmp_path, monkeypatch):
    from evals import faults
    if faults.available():
        pytest.skip(faults.available())
    monkeypatch.setenv("TRANSLUCENT_SAM", "strict")
    make = faults.Maker(tmp_path)
    try:
        for args, is_merged in (({"single_width": 82}, False), ({"gap": 2, "ratio": 1.5}, True),
                                ({"ratio": 3.0, "angle": 0.15}, True)):
            w = make()
            tr = faults._qc_outline_track(w, **args)
            mg = faults.measure(w, tr.data["boxes_id"], faults.scale(w), faults.clock(w))
            assert mg.status == "ok", mg.summary
            measurements = [m for m in w.L.list("measurement") if m.quantity in
                            ("filament_length", "elapsed_time", "growth_velocity")]
            if is_merged:
                assert not mg.data["ids"] and not measurements, mg.summary
                assert "two separate bodies" in mg.summary
            else:
                assert faults.velocity(mg) and len(measurements) == 3, mg.summary
                claim = faults.naive_claim(w, faults.velocity(mg))
                assert claim.data["verdict"] == "supported", claim.summary
    finally:
        for w in make.worlds:
            w.close()
