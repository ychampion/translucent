"""Lu et al. 2023 graphite plating as the second campaign: records, an offline scripted campaign, memory carry-over."""
import csv
import importlib.util
import json
import re
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.ablation import scripted_agent as movie4_agent
from evals.fake_llm import FakeClient
from translucent import instruments, tools
from translucent.ledger import Ledger
from translucent.runtime import Campaigner, Settings, new_campaign

ROOT = Path(__file__).resolve().parents[1]
GT = ROOT / "data" / "groundtruth"
LU = "lu2023/movie1"
LU_VIDEO = ROOT / "data" / "raw" / "lu2023" / "movie1.mp4"
M4_VIDEO = ROOT / "data" / "raw" / "reisecker2023" / "movie4.mp4"
SEGS = json.loads((GT / "lu2023" / "lu2023_fig2_segments_derived.json").read_text())
CITE = "Lu et al., Nat. Commun. 14, 5127 (2023), Supplementary Movie 1"


@pytest.fixture(scope="module")
def tmp_path_factory():
    # Windows' restricted token cannot reopen pytest's mode-0700 directories.
    root = GT / "lu2023" / f".test-a-{uuid.uuid4().hex}"
    root.mkdir()
    def make(name, numbered=True):
        path = root / f"{name}-{uuid.uuid4().hex}"
        path.mkdir()
        return path
    yield SimpleNamespace(mktemp=make, _retention_policy="all")
    assert root.resolve().is_relative_to(ROOT)
    shutil.rmtree(root)


def _import_lu(L):
    return tools.import_video(L, LU_VIDEO, label="Lu Movie 1", experiment=LU, licence="CC BY 4.0", citation=CITE)


# ------------------------------------------------------------------ records

def test_lu_scale_from_registration_not_stated_fov():
    cal = instruments.spatial_scale(LU)
    q = {x["id"]: x for x in json.loads((GT / "lu2023.json").read_text())["quantities"]}["movie_calibration"]
    lo, hi = q["value"]["range"]
    assert cal["px_per_mm"] == pytest.approx(1000 * q["value"]["px_per_um_from_fig2_scale_bar"], abs=5)
    value = cal["px_per_mm"] / 1000
    assert cal["uncertainty"] == pytest.approx(1000 * (max(value - lo, hi - value) + value / 94), abs=1)
    assert abs(cal["px_per_mm"] - 3200) > cal["uncertainty"]  # the stated 400 um field of view is not used
    assert "Fig. 2" in cal["source"] and "400 um" in cal["method"]


def test_lu_time_base_from_recorded_inset_not_stated_cadence():
    with (GT / "lu2023/lu2023_movie1_timebase.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    meta = json.loads((GT / "lu2023/lu2023_movie1_timebase.json").read_text())
    times = instruments.frame_times(LU)
    assert len(times) == len(rows) == 1261 and times == sorted(times)
    assert times == [float(r["t_s"]) for r in rows]
    bound = instruments.frame_time_uncertainty(LU)
    assert 1200 < bound < 1300
    for row, t in zip(rows, times):
        assert float(row["lower_s"]) <= t <= float(row["upper_s"])
        assert max(t - float(row["lower_s"]), float(row["upper_s"]) - t) <= bound + 0.002
    assert times[0] - bound < 0 and times[-1] / 3600 > 25
    assert meta["video_sha256"] == instruments.RECORDED_FILES[LU]
    assert meta["stated_acquisition_interval_s"] == 60
    assert times[630] / 3600 == pytest.approx(13, abs=0.4)
    src = instruments.time_base_source(LU)
    assert "one image per minute" in src and "inset" in src and "Coarse" in src


def test_lu_voltage_log_and_markers_match_ground_truth():
    s = instruments.voltage(LU)
    assert len(s["t"]) == len(s["v"]) == 84433 and s["dt"] == 2.0 and s["clock"] == "log"
    names = ["cycle1_charge", "cycle1_relax_after_charge", "cycle1_discharge", "cycle1_relax_after_discharge",
             "cycle2_charge", "cycle2_relax_after_charge", "cycle2_discharge", "cycle2_relax_after_discharge"]
    assert len(s["segments"]) == len(names)
    for seg, n in zip(s["segments"], names):
        assert seg["t_start_s"] == pytest.approx(SEGS[n]["t_start_s"]) and seg["t_end_s"] == pytest.approx(SEGS[n]["t_end_s"])
        assert seg["n"] == SEGS[n]["n_samples"]
    mk = {(m["cycle"], m["kind"]): m for m in instruments.plating_markers(s["t"], s["v"], s["segments"])}
    for cyc in (1, 2):
        gt = SEGS[f"cycle{cyc}_charge"]
        assert mk[cyc, "first_below_0V"]["t_s"] == gt["first_negative_voltage"]["t_s"]
        assert mk[cyc, "first_below_0V"]["t_in_charge_h"] == pytest.approx(gt["first_negative_voltage"]["t_in_charge_h"], abs=1e-3)
        assert mk[cyc, "voltage_minimum"]["t_s"] == gt["voltage_minimum"]["t_s"]
        assert mk[cyc, "voltage_minimum"]["voltage_V"] == pytest.approx(gt["voltage_minimum"]["voltage_V"])
    assert mk[1, "first_below_0V"]["t_in_charge_h"] < 9.5 < mk[1, "plateau_reached"]["t_in_charge_h"] < 11
    assert mk[2, "plateau_reached"]["t_s"] > mk[2, "voltage_minimum"]["t_s"]
    assert mk[2, "first_below_0V"]["t_in_charge_h"] < 0.25


def test_lu_refuses_a_file_that_is_not_the_recorded_movie(tmp_path):
    with pytest.raises(ValueError, match="not the recorded video"):
        instruments.check_recording(LU, "0" * 64)
    L = Ledger(tmp_path / "L")
    try:
        with pytest.raises(ValueError, match="not the recorded video"):
            tools.import_video(L, M4_VIDEO, label="x", experiment=LU, licence="CC BY 4.0", citation=CITE)
    finally:
        L.db.close()


# ------------------------------------------------------------------ an offline scripted campaign

def _marker(said, cycle, words):
    return re.search(rf"cycle {cycle} charge \([^)]*\): {words}[^—]*— (m_[0-9a-f]{{12}})", said).group(1)


def lu_agent(messages, tools_):
    """Scripted orchestrator for Lu (state read back from the conversation); critic/worker answer SUPPORTED."""
    if not tools_:
        return "SUPPORTED\nConsistent.", []
    vid = re.search(r"(sha256:[0-9a-f]{64})", messages[0]["content"]).group(1)
    done = [m["content"] for m in messages if m["role"] == "tool"]
    plan = [("lookup_calibration", {"video_id": vid, "quantity": "spatial_scale"}),
            ("lookup_calibration", {"video_id": vid, "quantity": "time_base"}),
            ("read_cell_voltage", {"video_id": vid}),
            ("segment_images", {"video_id": vid, "boxes": [[0, 0, 160, 938]], "max_images": 4})]
    if len(done) < len(plan):
        name, args = plan[len(done)]
        return f"Next I will run {name}.", [(name, args)]
    said = " ".join(done)
    neg1, neg2 = _marker(said, 1, "voltage first goes below 0 V"), _marker(said, 2, "voltage first goes below 0 V")
    plat1 = _marker(said, 1, "voltage first comes within")
    findings = [
        ("value_within", neg1, 7.3, 7.5, "In the first charge the voltage first goes below 0 V after about 7.4 hours."),
        ("value_within", neg2, 0.2, 0.25, "At double the current the voltage goes below 0 V within 15 minutes."),
        ("value_within", neg1, 9.5, 10.5, "Plating starts, taken as the first drop below 0 V, at the stated 10 hours."),
        ("value_within", plat1, 9.5, 11.0, "The voltage reaches its plateau at about 10 hours of charging."),
        ("value_within", neg1, 6.7, 8.2, "With voltage error within 10 mV, the first crossing is between 6.7 and 8.2 h."),
    ]
    k = said.count("Finding id")
    if k < len(findings):
        t, mid, lo, hi, text = findings[k]
        return "Checking a finding.", [("propose_finding", {"type": t, "measurement_ids": [mid],
                                                             "params": {"lo": lo, "hi": hi, "unit": "h"},
                                                             "statement": text})]
    return "Done.", [("finish", {"answer": "See findings.", "not_proven": "Visual plating onset (needs SAM)."})]


def _campaign(L, video, agent, objective="When does lithium plate?"):
    c = new_campaign(L, objective, "Lu", [video.id], budget_usd=1.0)
    camp = Campaigner(L, c, Settings(profile="dev", max_steps=20))
    fake = FakeClient(agent)
    camp.clients = {"fireworks": fake, "meta": fake}
    return c, camp, camp.run()


def _verdicts(L, c):
    return {cl.statement: L.claim_status(cl.id) for cl in L.list("claim", campaign_id=c.id)}


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    """Movie 4 campaign (records a lesson), then a Lu campaign in the same ledger, as run_campaign.py does."""
    mp = pytest.MonkeyPatch()
    mp.setenv("TRANSLUCENT_SAM", "strict")
    L = Ledger(tmp_path_factory.mktemp("lu") / "L")
    m4 = tools.import_video(L, M4_VIDEO, label="Movie 4", experiment="reisecker2023/movie4", licence="CC BY 4.0",
                            citation="Reisecker et al. 2023, Supplementary Movie 4")
    c4, _, r4 = _campaign(L, m4, movie4_agent)
    lu = _import_lu(L)
    c, camp, res = _campaign(L, lu, lu_agent)
    yield L, c4, c, res
    L.db.close()
    mp.undo()


def test_lu_campaign_earns_timing_and_rejects_stated_onset_as_first_crossing(shared):
    L, _, c, res = shared
    assert res["status"] == "finished"
    v = _verdicts(L, c)
    assert v["In the first charge the voltage first goes below 0 V after about 7.4 hours."] == "inconclusive"
    assert v["At double the current the voltage goes below 0 V within 15 minutes."] == "unsupported"
    assert v["Plating starts, taken as the first drop below 0 V, at the stated 10 hours."] == "refuted"
    assert v["The voltage reaches its plateau at about 10 hours of charging."] == "inconclusive"
    assert v["With voltage error within 10 mV, the first crossing is between 6.7 and 8.2 h."] == "supported"
    runs = {r.tool: r for r in L.list("run", campaign_id=c.id)}
    assert runs["read_cell_voltage"].status == "ok" and runs["lookup_calibration"].status == "ok"
    volts = [m for m in L.list("measurement") if m.quantity == "cell_voltage" and m.run_id == runs["read_cell_voltage"].id]
    assert 2000 < len(volts[0].value) < 3000  # stored one per minute; markers come from the full 2 s series
    source = next(a for a in L.list("artifact") if a.label == "Full Lu cell voltage log")
    assert L.blob_path(source.id).read_bytes() == (GT / "lu2023/lu2023_fig2_voltage.csv").read_bytes()
    assert source.id in volts[0].artifact_ids


def test_voltage_log_clock_is_not_a_frame_clock(shared):
    L, _, c, _ = shared
    ms = L.list("measurement")
    log_clock = next(m for m in ms if m.quantity == "time_base" and m.label == "Voltage log clock")
    scale = next(m for m in ms if m.quantity == "spatial_scale" and "Lu" in (L.get("artifact", m.artifact_ids[0]).label or ""))
    boxes = L.put_json({"0": {"1": [0, 120, 40, 140, 0.9]}}, kind="table", label="Object outlines")
    r = tools.call(tools.Context(L, c.id), "measure_growth", {
        "boxes_id": boxes.id, "video_id": scale.artifact_ids[0], "grows_from": "left", "scale_id": scale.id,
        "time_base_id": log_clock.id})
    assert r.status == "tool_failure" and "not a clock for this video's frames" in r.summary


def test_lu_sam_without_recordings_fails_closed(shared):
    L, _, c, _ = shared
    seg = [r for r in L.list("run", campaign_id=c.id) if r.tool == "segment_images"]
    assert seg and all(r.status == "tool_failure" for r in seg) and not any(r.measurement_ids for r in seg)


def test_lu_campaign_loads_movie4_lesson(shared):
    L, c4, c, _ = shared
    lessons = [x for x in L.list("lesson") if x.learned_in == c4.id]
    assert lessons
    loaded = {r for e in L.events(c.id) if e.type == "memory.loaded" for r in e.refs}
    assert {x.id for x in lessons} <= loaded


def test_run_campaign_script_accepts_lu(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("run_campaign", ROOT / "scripts" / "run_campaign.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.experiment("reisecker2023/movie4")["path"].endswith("reisecker2023/movie4.mp4")
    monkeypatch.setenv("TRANSLUCENT_SAM", "strict")
    result = mod.main(["--ledger", str(tmp_path / "L"), "--experiment", LU, "--steps", "15", "--offline"])
    L = Ledger(tmp_path / "L")
    c = L.list("campaign")[0]
    assert result["status"] == "finished" and "10 h" in c.objective
    assert "inconclusive" in _verdicts(L, c).values() and "refuted" in _verdicts(L, c).values()
    assert "If plating starts at the first drop below 0 V, the stated 10 h can't be reconciled" in result["answer"]
    assert all(r.cost_usd == 0 and r.provider == "local" for r in L.list("run"))
    L.db.close()


def test_offline_runner_keeps_movie_option_and_shares_lessons(tmp_path):
    spec = importlib.util.spec_from_file_location("run_campaign", ROOT / "scripts/run_campaign.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = str(tmp_path / "shared")
    assert mod.main(["--ledger", root, "--movie", "4", "--offline"])["status"] == "finished"
    assert mod.main(["--ledger", root, "--experiment", LU, "--offline"])["status"] == "finished"
    L = Ledger(Path(root))
    campaigns = L.list("campaign")
    first = next(c for c in campaigns if "solid-state" in c.title)
    second = next(c for c in campaigns if "graphite" in c.title)
    lessons = {x.id for x in L.list("lesson") if x.learned_in == first.id}
    loaded = {r for e in L.events(second.id) if e.type == "memory.loaded" for r in e.refs}
    assert lessons and lessons <= loaded
    assert {"inconclusive", "refuted"} <= set(_verdicts(L, second).values())
    L.db.close()
