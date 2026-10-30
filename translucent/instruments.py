"""The experiment-records interface: what the lab recorded alongside each video (scale, clock, cell voltage).

For the replayed experiments these records come from the published paper and its supplementary files, extracted
and checked in data/groundtruth/ (see data/groundtruth/README.md). A live instrument would implement the same
four functions. Returning None means "not on record"; callers must not substitute a guess.
"""
from __future__ import annotations

import csv
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
GT = ROOT / "data" / "groundtruth"


@lru_cache
def _reisecker() -> dict:
    return {m["movie"]: m for m in json.loads((GT / "reisecker2023.json").read_text(encoding="utf-8"))["movies"]}


@lru_cache
def _movie4_rows() -> list[dict]:
    with open(GT / "reisecker2023_movie4_timebase.csv", newline="") as fh:
        return list(csv.DictReader(fh))


# sha256 of each published video file: the records below describe these exact bytes and no others
RECORDED_FILES = {
    "reisecker2023/movie1": "b69fdd24bcbdcfbbee0c8502a572f50506ad0b2540b8433e5d76f66011b3daef",
    "reisecker2023/movie2": "5d5b3c534174adfffd07f039e368cfd1c01f90c5a21fed3cc52ddbeb2cf7a1ec",
    "reisecker2023/movie3": "f913e042b831e4ed277baeef5a20f7ac0a2a8a3332ac76b843870b58e02efbce",
    "reisecker2023/movie4": "69a069441837fe778c22f20923e9376669e67ef3f6f05e989a59743d2867cb0d",
    "reisecker2023/movie5": "2fc1b29589cc731facf8efb8bf415071ed9a0cbc6f2f5e9682c70c6ddaa4c653",
    "lu2023/movie1": "708efdce5f66643a0a2edcdaaec9cd80d44a3aaf3dbe5df3918cb3e79e50c7c4",
}


def check_recording(experiment: str, sha256: str) -> None:
    """Refuse to attach an experiment's records to a file that is not that experiment's video."""
    want = RECORDED_FILES.get(experiment)
    if experiment.startswith("user/"):
        from translucent import experiments
        manifest, record, _ = experiments.load(experiment)
        want = record["sha256"][manifest.video]
    if want and want != sha256:
        raise ValueError(f"this file is not the recorded video of {experiment}; its lab records do not apply")


LU = "lu2023/movie1"


@lru_cache
def _lu() -> dict:
    return {q["id"]: q for q in json.loads((GT / "lu2023.json").read_text(encoding="utf-8"))["quantities"]}


@lru_cache
def _lu_rows() -> list[dict]:
    with open(GT / "lu2023" / "lu2023_fig2_voltage.csv", newline="") as fh:
        return list(csv.DictReader(fh))


@lru_cache
def _lu_registration() -> list[float]:
    with open(GT / "lu2023" / "lu2023_fig2_registration.csv", newline="") as fh:
        return [float(r["movie_px_per_um"]) for r in csv.DictReader(fh)]


@lru_cache
def _lu_clock() -> tuple[list[float], dict]:
    folder = GT / "lu2023"
    meta = json.loads((folder / "lu2023_movie1_timebase.json").read_text(encoding="utf-8"))
    check_recording(LU, meta["video_sha256"])
    with (folder / "lu2023_movie1_timebase.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    times = [float(r["t_s"]) for r in rows]
    if len(times) != meta["frames"] or len(times) != 1261 or np.any(np.diff(times) < 0):
        raise ValueError("Lu inset clock does not cover the recorded movie in order")
    return times, meta


def _lu_segments(rows: list[dict]) -> list[dict]:
    """Protocol segments in log order: cycle, mode, current density, first/last sample time (s)."""
    segs = []
    for r in rows:
        key = (r["cycle"], r["segment"])
        if not segs or segs[-1]["key"] != key:
            segs.append({"key": key, "cycle": int(r["cycle"]), "mode": r["mode"],
                         "current_mA_cm2": abs(float(r["current_density_mA_cm2_signed_charge_positive"])),
                         "t_start_s": float(r["t_s"]), "t_end_s": float(r["t_s"]), "n": 0})
        segs[-1]["t_end_s"] = float(r["t_s"])
        segs[-1]["n"] += 1
    for s in segs:
        del s["key"]
    return segs


PLATEAU_BAND_V = 0.005
VOLTAGE_ERROR_V = 0.010   # conditional sensitivity assumption; the source does not state voltage accuracy


def plating_markers(t, v, segments) -> list[dict]:
    """Voltage markers of lithium plating in each charge segment, as time into that charge (h). Which marker is
    'onset' is a definition choice; each marker states its own definition."""
    t, v = np.asarray(t, float), np.asarray(v, float)
    out = []
    for s in segments:
        if s["mode"] != "charge":
            continue
        sel = (t >= s["t_start_s"]) & (t <= s["t_end_s"])
        ts, vs = t[sel], v[sel]
        name = f"cycle {s['cycle']} charge ({s['current_mA_cm2']:g} mA/cm2)"
        i_min = int(np.argmin(vs))
        terminal = float(np.median(vs[ts >= ts[-1] - 3600]))
        inside = np.abs(vs - terminal) <= PLATEAU_BAND_V
        starts = np.flatnonzero(inside & ~np.r_[False, inside[:-1]])
        ends = np.flatnonzero(inside & ~np.r_[inside[1:], False])
        plateau = [a for a, b in zip(starts, ends) if ts[b] - ts[a] >= 1800]
        found = {"first_below_0V": np.flatnonzero(vs < 0),
                 "plateau_reached": plateau,
                 "voltage_minimum": np.array([i_min])}
        what = {"first_below_0V": "voltage first goes below 0 V vs Li/Li+",
                "plateau_reached": f"voltage first comes within {PLATEAU_BAND_V * 1000:g} mV of the final-hour "
                                   f"median ({terminal:g} V) and stays in that band for at least 30 min "
                                   "(an operational plateau definition)",
                "voltage_minimum": "voltage reaches its lowest value in this charge"}
        for kind, idx in found.items():
            if len(idx):
                i = int(idx[0])
                dt = float(np.median(np.diff(ts)))
                if kind == "first_below_0V":
                    early, late = np.flatnonzero(vs < VOLTAGE_ERROR_V), np.flatnonzero(vs < -VOLTAGE_ERROR_V)
                    if not len(late):
                        continue   # a crossing is not established under the stated voltage-error assumption
                    lo, hi = ts[early[0]], ts[late[0]]
                    why = "first crossings of +10 and -10 mV, including one sample interval"
                elif kind == "voltage_minimum":
                    possible = ts[vs <= vs[i_min] + 2 * VOLTAGE_ERROR_V]
                    lo, hi = possible[0], possible[-1]
                    why = "all samples within 20 mV of the recorded minimum, including one sample interval"
                else:
                    # A 5 mV plateau is narrower than the assumed error in either it or its terminal median.
                    lo, hi = ts[0], ts[-1]
                    why = ("whole charge window: the 5 mV operational plateau is not resolved under this "
                           "voltage-error assumption; its logged-sample time is not a physical onset time")
                lo, hi = max(float(ts[0]), float(lo) - dt), min(float(ts[-1]), float(hi) + dt)
                u = max(float(ts[i]) - lo, hi - float(ts[i])) / 3600
                out.append({"kind": kind, "subject": name, "cycle": s["cycle"],
                            "t_s": float(ts[i]), "t_in_charge_h": float((ts[i] - s["t_start_s"]) / 3600),
                            "voltage_V": float(vs[i]), "definition": what[kind],
                            "time_window_s": [lo, hi], "uncertainty_h": u,
                            "uncertainty_method": "conditional bound assuming voltage error within +/-10 mV "
                                                  "(accuracy not stated); " + why})
    return out


def _movie(experiment: str) -> int | None:
    if experiment.startswith("reisecker2023/movie"):
        return int(experiment.removeprefix("reisecker2023/movie"))
    return None


def layout(experiment: str) -> str | None:
    """Where the sample sits in the frame, from the experiment's records (composite movies carry plots and captions)."""
    if (n := _movie(experiment)) is not None:
        return _reisecker().get(n, {}).get("geometry")
    if experiment == LU:
        return json.loads((GT / "lu2023.json").read_text(encoding="utf-8"))["movie"].get("geometry")
    return None


def spatial_scale(experiment: str) -> dict | None:
    if experiment.startswith("user/"):
        from translucent import experiments
        return experiments.spatial_scale(experiment)
    if experiment == LU:  # registration of movie frames to the Fig. 2 strips (50 um bar); not the stated 400 um FOV
        reg = _lu_registration()
        lo, hi = min(reg), max(reg)
        value = float(np.median(reg))
        bound = max(value - lo, hi - value) + value / 94
        return {"px_per_mm": round(1000 * value, 1), "uncertainty": round(1000 * bound, 1),
                "method": f"movie frames registered to the paper's Fig. 2 image strips, which carry a 50 um scale "
                          f"bar ({len(reg)} registrations); bound = maximum departure from the median plus "
                          "one pixel of the 94-pixel raster bar (includes ~4% electrode expansion during charge). "
                          "The stated 400 um field of view would give 3200 px/mm and can't be reconciled under "
                          "the assumption that the registered Fig. 2 scale bar applies",
                "source": "Lu et al. 2023, Fig. 2 scale bar, registered to Supplementary Movie 1"}
    n = _movie(experiment)
    if n is None:
        return None
    v = _reisecker()[n].get("calibration_px_per_mm")
    if v is None:
        return None
    if isinstance(v, list):  # a range from two readings
        lo, hi = v
        value, u = (lo + hi) / 2, (hi - lo) / 2
    else:  # Fig. 3 scale bars disagree by up to ~5% between panels
        value, u = v, round(0.025 * v, 1)
    return {"px_per_mm": round(value, 1), "uncertainty": u,
            "method": "1 mm scale bar in the paper's Fig. 3a, registered to this movie; ± half the spread "
                      "between scale-bar readings",
            "source": "Reisecker et al. 2023, Fig. 3a scale bar"}


def frame_times(experiment: str) -> list[float] | None:
    """Recorded time estimates (s) per playback frame; check frame_time_uncertainty for coarse bounds."""
    if experiment.startswith("user/"):
        from translucent import experiments
        return experiments.frame_times(experiment)
    if _movie(experiment) == 4:
        return [float(r["t_s_rel_41p32h"]) for r in _movie4_rows()]
    if experiment == LU:
        return _lu_clock()[0]
    return None


def time_base_source(experiment: str) -> str | None:
    if experiment.startswith("user/"):
        from translucent import experiments
        return experiments.time_base_source(experiment)
    if _movie(experiment) == 4:
        return "on-screen voltage-vs-time panel recorded with the movie (41.32-41.46 h), marker position per frame"
    if experiment == LU:
        meta = _lu_clock()[1]
        return (f"{meta['source']}; {meta['method']}; conservative bound +/-{meta['uncertainty_s']} s. "
                "Stated in Methods: one image per minute (60 s acquisition interval), not a verified "
                "60 s per exported movie frame. " + meta["limitations"])
    return None


def frame_time_uncertainty(experiment: str) -> float | None:
    if experiment.startswith("user/"):
        from translucent import experiments
        return experiments.frame_time_uncertainty(experiment)
    return _lu_clock()[1]["uncertainty_s"] if experiment == LU else None


def voltage(experiment: str) -> dict | None:
    """Cell voltage per playback frame with its clock; None if no electrical record accompanies the video."""
    if experiment.startswith("user/"):
        from translucent import experiments
        return experiments.voltage(experiment)
    if experiment == LU:
        rows = _lu_rows()
        segs = _lu_segments(rows)
        movie_times, clock = _lu_clock()
        movie_end = min(movie_times[-1] + clock["uncertainty_s"],
                        max(s["t_end_s"] for s in segs if s["cycle"] == 1 and s["mode"] == "discharge"))
        return {"t": [float(r["t_s"]) for r in rows], "v": [float(r["voltage_V_vs_Li"]) for r in rows], "dt": 2.0,
                "short_threshold_v": None, "segments": segs, "clock": "log",
                "source": {"path": str(GT / "lu2023/lu2023_fig2_voltage.csv"),
                           "uri": "data/groundtruth/lu2023/lu2023_fig2_voltage.csv",
                           "label": "Full Lu cell voltage log", "citation": "Lu et al. 2023, Fig. 2 Source Data",
                           "licence": "CC BY 4.0"},
                "movie_time_range_s": [max(0.0, movie_times[0] - clock["uncertainty_s"]), movie_end],
                "notes": "The stated 10 h onset can't be reconciled under the assumptions that onset = first "
                         "0 V crossing and voltage error is within +/-10 mV: its time window still lies before "
                         "9.5 h. The logged 5 mV plateau is an operational definition, with no precise physical "
                         "onset established at this voltage accuracy. Cycle 2 is in the log, not in this movie.",
                "method": "cell voltage vs Li/Li+ from the paper's Source Data (Fig. 2a), potentiostat clock, "
                          "one sample per 2 s; the movie's voltage inset shows the same curve"}
    if _movie(experiment) != 4:
        return None
    rows = _movie4_rows()
    t = [float(r["t_s_rel_41p32h"]) for r in rows]
    distinct = sorted(set(t))
    dt = sorted(b - a for a, b in zip(distinct, distinct[1:]))[len(distinct) // 2]
    # shorted, the panel reads |V| <= 0.27 V; open, >= 1.48 V. 0.5 V splits them well outside the 0.05 V reading
    # resolution (0.05 V itself sits in the post-short noise: 0.057, 0.048, 0.051 V, and read the second short 25 s late)
    return {"t": t, "v": [float(r["voltage_V"]) for r in rows], "dt": dt, "short_threshold_v": 0.5,
            "method": "voltage read from the recorded voltage-vs-time panel at the moving marker, per frame"}


def record(experiment: str) -> dict:
    """The experiment's metadata for a bundle: title, citation, licence, and the scale, clock and voltage records with
    where each came from (or why it is not on record). Shaped as contracts.ExperimentRecord."""
    def fact(value, source=None, method=None, declared_by=None, missing="not on record"):
        if value is None:
            return {"status": "unavailable", "reason": missing}
        return {"status": "available", "value": value, "source": source, "method": method, "declared_by": declared_by}

    out = {"id": experiment, "layout": None, "notes": None, "files": {}}
    author = None
    if experiment.startswith("user/"):
        from translucent import experiments
        manifest, rec, _ = experiments.load(experiment)
        author = manifest.author
        out.update(title=manifest.title, citation=manifest.citation, licence=manifest.licence, author=author,
                   notes=manifest.notes, files=dict(rec["sha256"]))
    elif experiment in RECORDED_FILES:
        meta = json.loads((GT / ("lu2023.json" if experiment == LU else "reisecker2023.json")).read_text(
            encoding="utf-8"))
        movie = meta["movie"] if experiment == LU else _reisecker()[_movie(experiment)]
        out.update(title=movie.get("caption_verbatim"), citation=meta["source"].get("citation"),
                   licence=meta["source"].get("licence"), files={"video": RECORDED_FILES[experiment]},
                   layout=layout(experiment))
    else:
        out.update(title=None)
    scale = spatial_scale(experiment)
    out["scale"] = fact(scale and f"{scale['px_per_mm']:g} ± {scale['uncertainty']:g} px/mm",
                        scale and scale.get("source"), scale and scale.get("method"), author if scale else None)
    clock = time_base_source(experiment)
    bound = frame_time_uncertainty(experiment)
    times = frame_times(experiment)
    out["clock"] = fact(clock and (f"{len(times)} frame times" if times else "recorded") +
                        (f", bound ± {bound:g} s" if bound else ""), clock, None, author if clock else None,
                        missing="no recorded clock: playback frame timing is not real time")
    v = voltage(experiment)
    src = (v or {}).get("source") or {}
    out["voltage"] = fact(v and f"{len(v['t'])} readings", src.get("citation") or src.get("label"), v and v.get("method"),
                          author if v else None, missing="no electrical record accompanies this video")
    if v and v.get("notes") and not out["notes"]:
        out["notes"] = v["notes"]
    return out
