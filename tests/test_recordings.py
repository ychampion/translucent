"""Recorded SAM replay: exact matches replay, misses fail in strict mode, nearest mode says what it substituted."""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from translucent import recordings, tools
from translucent.budget import ROOT
from translucent.ledger import Ledger
from translucent.runtime import new_campaign

TEXT = "<5f>1<|box;x1=10;y1=20;x2=30;y2=40;w=100;h=100;c=0.9|>\n"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(recordings, "STORE", tmp_path / "rec")
    return tmp_path / "rec"


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"not really a video")
    return p


def test_strict_replays_exact_and_refuses_miss(store, video):
    sha = recordings._sha_file(video)
    recordings.save("video", sha, "<5f>1<|point;x=20;y=30;w=100;h=100|>", {"a": "1"}, TEXT, {"video_frames_processed": 50})
    sam = recordings.RecordedSAM("strict")
    fid = sam.upload(video)
    text, final = sam.sam_video(file_id=fid, prompt="<5f>1<|point;x=20;y=30;w=100;h=100|>", frames=10,
                                metadata={"a": "1"})
    assert text == TEXT and final.substituted is None and final.usage.model_dump()["replayed"]
    with pytest.raises(recordings.NoRecording):
        sam.sam_video(file_id=fid, prompt="<6f>1<|point;x=20;y=30;w=100;h=100|>", frames=10, metadata={"a": "1"})
    with pytest.raises(recordings.NoRecording):  # same prompt, different settings
        sam.sam_video(file_id=fid, prompt="<5f>1<|point;x=20;y=30;w=100;h=100|>", frames=10, metadata={})


def test_nearest_substitutes_closest_frame_and_says_so(store, video):
    sha = recordings._sha_file(video)
    recordings.save("video", sha, "<5f>1<|point;x=1;y=1;w=100;h=100|>", None, "five", {})
    recordings.save("video", sha, "<40f>1<|point;x=1;y=1;w=100;h=100|>", None, "forty", {})
    recordings.save("video", "other", "<9f>1<|point;x=1;y=1;w=100;h=100|>", None, "other video", {})
    sam = recordings.RecordedSAM("nearest")
    text, final = sam.sam_video(file_id=sam.upload(video), prompt="<12f>1<|point;x=9;y=9;w=100;h=100|>", frames=50)
    assert text == "five" and final.substituted.startswith("<5f>")
    with pytest.raises(recordings.NoRecording):
        sam.sam_video(file_id="rec:unknown", prompt="<1f>x", frames=1)


def test_recording_wrapper_stores_what_live_returned(store, video):
    class Live:
        ledger = SimpleNamespace(balance=lambda: {"committed_usd": 0.0})

        def upload(self, path):
            return "file-123"

        def sam_image(self, *, image_url, prompt, metadata=None):
            return TEXT, SimpleNamespace(usage=SimpleNamespace(model_dump=lambda: {"images": 1}))

    url = "data:image/jpeg;base64,aGVsbG8="
    recordings.RecordingSAM(Live()).sam_image(image_url=url, prompt="<0f>p", metadata={"m": 1})
    text, final = recordings.RecordedSAM("strict").sam_image(image_url=url, prompt="<0f>p", metadata={"m": 1})
    assert text == TEXT and final.usage.model_dump() == {"images": 1, "replayed": True}


def test_image_replay_falls_back_to_source_frame(store):
    """Another FFmpeg build decodes the same frame to different bytes: the recorded source frame still matches."""
    recordings.save("image", recordings._sha_image("data:image/jpeg;base64,aGVsbG8="), "<0f>p", {"m": 1}, TEXT, {},
                    source="sha256:abc#7")
    sam = recordings.RecordedSAM("strict")
    other_bytes = "data:image/jpeg;base64,d29ybGQ="
    text, final = sam.sam_image(image_url=other_bytes, prompt="<0f>p", metadata={"m": 1}, source="sha256:abc#7")
    assert text == TEXT and final.substituted is None
    for prompt, meta, source in [("<0f>p", {"m": 1}, "sha256:abc#8"), ("<0f>q", {"m": 1}, "sha256:abc#7"),
                                 ("<0f>p", {}, "sha256:abc#7"), ("<0f>p", {"m": 1}, None)]:
        with pytest.raises(recordings.NoRecording):
            sam.sam_image(image_url=other_bytes, prompt=prompt, metadata=meta, source=source)


DEV_M4 = ROOT / "runs" / "dev-m4"
M4 = "sha256:e3c71186c4a2b61acb07cd9474ae947a05cb7c48354a4764fa8120588e1a167f"


@pytest.mark.skipif(not (DEV_M4 / "ledger.sqlite").exists() or not recordings.STORE.exists(),
                    reason="needs the seeded recordings (scripts/seed_recordings.py)")
def test_tool_replays_seeded_movie4_segmentation(tmp_path, monkeypatch):
    """The seeded per-image recordings are keyed by the exact JPEG segment_images sends: a strict replay works."""
    monkeypatch.setenv("TRANSLUCENT_SAM", "strict")
    src = Ledger(DEV_M4)
    L = Ledger(tmp_path / "l")
    shutil.copy(src.blob_path(M4), tmp_path / "m4.mp4")
    art = L.put_file(tmp_path / "m4.mp4", **{k: getattr(src.get("artifact", M4), k)
                                              for k in ("kind", "media_type", "label", "shape", "axes", "source")})
    assert art.id == M4
    c = new_campaign(L, "test", "test", [M4])
    ctx = tools.Context(L, c.id)
    points = [[585, 248], [585, 318], [585, 372], [585, 465], [585, 535]]
    res = tools.call(ctx, "segment_images", {"video_id": M4, "points": points})
    assert res.status == "ok", res.summary
    assert "NOTE" not in res.summary and isinstance(ctx.meta, recordings.RecordedSAM)
    run = L.get("run", res.run_id)
    assert run.cost_usd == 0 and run.provider == "meta"
    seeded = [r for q in recordings.STORE.glob("*.json") if (r := json.loads(q.read_text(encoding="utf-8")))["kind"] == "image"]
    m4_stream = recordings.stream_id(src.blob_path(M4))  # the same on any FFmpeg build, raw file or remux
    # live campaigns also record other movies' images (Lu graphite); every one is keyed by its video's stream
    assert any(r.get("source", "").startswith(m4_stream + "#") for r in seeded)
    assert all(r.get("source", "").startswith("stream:") and "#" in r["source"] for r in seeded)
    miss = tools.call(ctx, "segment_images", {"video_id": M4, "points": [[1, 1]]})
    assert miss.status == "tool_failure" and "NoRecording" in miss.summary


@pytest.mark.skipif(not (DEV_M4 / "ledger.sqlite").exists() or not recordings.STORE.exists(),
                    reason="needs the seeded recordings (scripts/seed_recordings.py)")
def test_substituted_replay_is_never_evidence(tmp_path, monkeypatch):
    """Nearest-mode replay of another prompt is shown to the agent but recorded invalid; growth can't be measured."""
    monkeypatch.setenv("TRANSLUCENT_SAM", "nearest")
    src = Ledger(DEV_M4)
    L = Ledger(tmp_path / "l")
    shutil.copy(src.blob_path(M4), tmp_path / "m4.mp4")
    L.put_file(tmp_path / "m4.mp4", **{k: getattr(src.get("artifact", M4), k)
                                       for k in ("kind", "media_type", "label", "shape", "axes", "source")})
    ctx = tools.Context(L, new_campaign(L, "test", "test", [M4]).id)
    seg = tools.call(ctx, "segment_images", {"video_id": M4, "points": [[580, 250], [580, 320]]})
    assert seg.status == "invalid_result" and "cannot be used as evidence" in seg.summary
    scale = tools.call(ctx, "lookup_calibration", {"video_id": M4, "quantity": "spatial_scale"})
    grow = tools.call(ctx, "measure_growth", {"boxes_id": seg.data["boxes_id"], "video_id": M4, "grows_from": "right",
                                              "scale_id": scale.data["measurement_id"]})
    # either refused for its source, or every object dropped as the wrong shape: never measured
    assert (grow.status == "invalid_result" and "did not succeed" in grow.summary) or "not measured" in grow.summary
    assert not [m for m in L.list("measurement") if m.quantity == "filament_length"]


@pytest.mark.skipif(not (DEV_M4 / "ledger.sqlite").exists(), reason="needs runs/dev-m4")
def test_inspect_region_is_clamped(tmp_path):
    """Agents pass regions like [-1, 200, 600, 600]; the tool clamps them instead of crashing in OpenCV."""
    src = Ledger(DEV_M4)
    L = Ledger(tmp_path / "l")
    shutil.copy(src.blob_path(M4), tmp_path / "m4.mp4")
    L.put_file(tmp_path / "m4.mp4", **{k: getattr(src.get("artifact", M4), k)
                                       for k in ("kind", "media_type", "label", "shape", "axes", "source")})
    ctx = tools.Context(L, new_campaign(L, "test", "test", [M4]).id)
    ok = tools.call(ctx, "inspect_video", {"video_id": M4, "frame": 120, "region": [-1, 200, 600, 600]})
    assert ok.status == "ok" and "x 0-600" in ok.summary and ok.images
    # a 600 px wide zoom is drawn larger, and the model is told its coordinates stay in source pixels
    assert "drawn at 1.71x" in ok.summary and "Grid labels are source-video pixels" in ok.summary
    # Movie 4 is a composite (microscope left, the paper's voltage plot right): the records say where the sample is
    assert "Frame layout (experiment records): Left part of the frame (x 0-906 px) is the microscope view" in ok.summary
    bad = tools.call(ctx, "inspect_video", {"video_id": M4, "frame": 120, "region": [5000, 0, 6000, 10]})
    assert bad.status == "tool_failure" and "outside" in bad.summary


def test_a_missing_ffmpeg_is_named_plainly(monkeypatch, tmp_path):
    monkeypatch.setattr(recordings.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="ffmpeg is not installed .* setup problem"):
        recordings.stream_id(tmp_path / "never-hashed.mp4")
