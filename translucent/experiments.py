"""Immutable, locally supplied experiment records. Playback rate is never an acquisition clock."""
from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from translucent.ledger import Ledger, sha256_file

# a deployment keeps registered experiments on a volume next to its ledger
ROOT = Path(os.environ.get("TRANSLUCENT_EXPERIMENTS_DIR") or Path(__file__).resolve().parents[1] / "data" / "experiments")
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]


def _filename(value: str) -> str:
    if value in {".", "..", "manifest.json", "record.json", "record.sha256"} or "/" in value or "\\" in value or ":" in value:
        raise ValueError("use a filename without folders; supply its location in files")
    if any(ord(c) < 32 for c in value):
        raise ValueError("filenames cannot contain control characters")
    return value


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Scale(_Record):
    um_per_px: Positive | None = None
    bar_px: Positive | None = None
    bar_um: Positive | None = None
    uncertainty: Nonnegative = 0.0
    source: Text

    @model_validator(mode="after")
    def calibration(self):
        if self.um_per_px is not None:
            if self.bar_px is not None or self.bar_um is not None:
                raise ValueError("supply um_per_px or both bar_px and bar_um, not both forms")
        elif self.bar_px is None or self.bar_um is None:
            raise ValueError("supply um_per_px or both bar_px and bar_um")
        if self.uncertainty >= self.value:
            raise ValueError("scale uncertainty must be smaller than the scale in um per pixel")
        if not math.isfinite(self.value) or not math.isfinite(1000 / (self.value - self.uncertainty)):
            raise ValueError("scale and uncertainty must give a finite calibration in pixels per millimetre")
        return self

    @property
    def value(self):
        return self.um_per_px if self.um_per_px is not None else self.bar_um / self.bar_px


class Clock(_Record):
    timestamps_csv: Text | None = None
    interval_s: Positive | None = None
    uncertainty_s: Nonnegative = 0.0
    source: Text

    @field_validator("timestamps_csv")
    @classmethod
    def filename(cls, value):
        return _filename(value) if value is not None else value

    @model_validator(mode="after")
    def clock(self):
        if (self.timestamps_csv is None) == (self.interval_s is None):
            raise ValueError("supply timestamps_csv or interval_s, exactly one")
        if self.timestamps_csv is not None and self.uncertainty_s:
            raise ValueError("uncertainty_s belongs to an interval_s clock")
        if self.interval_s is not None and self.uncertainty_s >= self.interval_s:
            raise ValueError("interval uncertainty must be smaller than the acquisition interval")
        return self


class Voltage(_Record):
    csv: Text
    offset_s: float = Field(allow_inf_nan=False)
    source: Text

    @field_validator("csv")
    @classmethod
    def filename(cls, value):
        return _filename(value)


class Manifest(_Record):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    title: Text
    citation: Text
    licence: Text
    author: Text
    video: Text
    scale: Scale | None = None
    clock: Clock | None = None
    voltage: Voltage | None = None
    notes: str | None = None

    @field_validator("video")
    @classmethod
    def filename(cls, value):
        return _filename(value)

    @model_validator(mode="after")
    def filenames(self):
        names = self.files()
        if len(names) != len(set(names)):
            raise ValueError("video, timestamp and voltage files must have different filenames")
        return self

    def files(self):
        return ([self.video] + ([self.clock.timestamps_csv] if self.clock and self.clock.timestamps_csv else [])
                + ([self.voltage.csv] if self.voltage else []))


def _rows(path: Path, columns: tuple[str, ...]) -> list[dict]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh, strict=True)
            if reader.fieldnames != list(columns):
                raise ValueError(f"{path.name}: CSV header must be {','.join(columns)}")
            rows = list(reader)
    except (csv.Error, UnicodeError) as e:
        raise ValueError(f"{path.name}: cannot read the CSV; use UTF-8 comma-separated columns") from e
    if len(rows) < 2:
        raise ValueError(f"{path.name}: provide at least two rows of measurements")
    for i, row in enumerate(rows, 2):
        if None in row or any(v is None or not v.strip() for v in row.values()):
            raise ValueError(f"{path.name}: row {i} has missing or extra columns")
        try:
            if not all(math.isfinite(float(v)) for v in row.values()):
                raise ValueError()
        except ValueError as e:
            raise ValueError(f"{path.name}: row {i} must contain finite numbers") from e
    return rows


def _timestamps(path: Path, frames: int, captures: list[int]) -> list[float]:
    rows = _rows(path, ("frame", "time_s"))
    if len(rows) != frames:
        raise ValueError(f"{path.name}: expected one timestamp per source frame ({frames} rows), got {len(rows)}")
    if any(r["frame"].strip() != str(i) for i, r in enumerate(rows)):
        raise ValueError(f"{path.name}: frame numbers must run from 0 to {frames - 1} in order, without gaps")
    times = [float(r["time_s"]) for r in rows]
    if any(b < a for a, b in zip(times, times[1:])) or times[-1] == times[0]:
        raise ValueError(f"{path.name}: times must increase, with equal times only for repeated images")
    if any(times[i] == times[i - 1] and captures[i] != captures[i - 1] for i in range(1, frames)):
        raise ValueError(f"{path.name}: different images share a timestamp; provide a resolved time for each "
                         "captured image (equal times are only for identical repeated frames)")
    relative = [t - times[0] for t in times]
    if not all(math.isfinite(t) for t in relative):
        raise ValueError(f"{path.name}: timestamp range is too large to represent in seconds")
    return relative


def _volts(path: Path) -> tuple[list[float], list[float]]:
    rows = _rows(path, ("time_s", "voltage_V"))
    t = [float(r["time_s"]) for r in rows]
    if any(b <= a for a, b in zip(t, t[1:])):
        raise ValueError(f"{path.name}: voltage times must increase without duplicates")
    if not math.isfinite(t[-1] - t[0]):
        raise ValueError(f"{path.name}: voltage time range is too large to represent in seconds")
    return t, [float(r["voltage_V"]) for r in rows]


def _captures(path: Path, *, interval=False) -> list[int]:
    """Collapse identical decoded images; a heuristic alone cannot establish an acquisition cadence."""
    from translucent import tools
    cap, previous, captures = cv2.VideoCapture(str(path)), None, []
    try:
        if not cap.isOpened():
            raise ValueError("the video could not be opened; use a video readable by OpenCV and FFmpeg")
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            captures.append(0 if previous is None else captures[-1] + int(not np.array_equal(frame, previous)))
            previous = frame
        expected = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()
    if len(captures) < 2 or len(captures) != expected:
        raise ValueError("the video must contain at least two readable frames, with no truncated frames")
    if interval and len(captures) >= 4:
        repeats, reliable = tools._repeat_factor(path)
        if reliable and repeats > 1.2:
            detected = np.r_[0, np.cumsum(tools._new_image_flags(path))].astype(int).tolist()
            if detected != captures:
                raise ValueError("playback repeats cannot be distinguished from changing images; provide a "
                                 "timestamps CSV with one recorded time per source frame")
    if interval and captures[-1] == 0:
        raise ValueError("all video frames are identical; provide timestamps to distinguish acquisitions from repeats")
    return captures


def load(experiment: str) -> tuple[Manifest, dict, Path]:
    ident = experiment.removeprefix("user/")
    # Validate before constructing a path, including for direct tools.import_video callers.
    if not experiment.startswith("user/") or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", ident):
        raise ValueError("invalid user experiment id")
    folder = ROOT / ident
    try:
        if sha256_file(folder / "record.json") != (folder / "record.sha256").read_text(encoding="ascii").strip():
            raise ValueError("capture records have changed since registration; their lab records no longer apply")
        manifest = Manifest.model_validate_json((folder / "manifest.json").read_text(encoding="utf-8"))
        record = json.loads((folder / "record.json").read_text(encoding="utf-8"))
        if manifest.id != ident:
            raise ValueError("the stored manifest has a different experiment id")
        captures = record["capture_indices"]
        if (not isinstance(captures, list) or len(captures) != record["frames"] or len(captures) < 2
                or any(type(i) is not int for i in captures) or captures[0] != 0
                or any(b - a not in (0, 1) for a, b in zip(captures, captures[1:]))):
            raise ValueError("stored capture records do not cover the video's frames in order")
        for name in ["manifest.json"] + [n for n in manifest.files() if n != manifest.video]:
            if sha256_file(folder / name) != record["sha256"][name]:
                raise ValueError(f"{name} has changed since registration; its lab records no longer apply")
        return manifest, record, folder
    except (OSError, KeyError, json.JSONDecodeError) as e:
        raise ValueError(f"{experiment}: experiment records are missing or unreadable; register the experiment first") from e


def declaration(manifest: Manifest, source: str) -> str:
    return f"declared by {manifest.author} from {source}"


def spatial_scale(experiment: str) -> dict | None:
    manifest, _, _ = load(experiment)
    s = manifest.scale
    if s is None:
        return None
    value, u = s.value, s.uncertainty
    source = declaration(manifest, s.source)
    return {"px_per_mm": 1000 / value, "uncertainty": 1000 / (value - u) - 1000 / value,
            "method": f"{source}; {value:g} um per pixel, bound +/-{u:g} um per pixel; "
                      "zero uncertainty means conditional on the declared scale, not verified accuracy",
            "source": source}


def frame_times(experiment: str) -> list[float] | None:
    manifest, record, folder = load(experiment)
    if manifest.clock is None:
        return None
    if manifest.clock.timestamps_csv:
        return _timestamps(folder / manifest.clock.timestamps_csv, record["frames"], record["capture_indices"])
    return [i * manifest.clock.interval_s for i in record["capture_indices"]]


def time_base_source(experiment: str) -> str | None:
    manifest, _, _ = load(experiment)
    c = manifest.clock
    if c is None:
        return None
    method = ("per-source-frame timestamps, relative to the first frame" if c.timestamps_csv else
              f"{c.interval_s:g} s per captured image, interval bound +/-{c.uncertainty_s:g} s; "
              "repeated playback images share a capture time; zero uncertainty is conditional on this declaration")
    return f"{declaration(manifest, c.source)}; {method}"


def frame_time_uncertainty(experiment: str) -> float | None:
    manifest, record, _ = load(experiment)
    c = manifest.clock
    return record["capture_indices"][-1] * c.uncertainty_s if c and c.interval_s is not None else None


def voltage(experiment: str) -> dict | None:
    manifest, _, folder = load(experiment)
    if manifest.voltage is None:
        return None
    s = manifest.voltage
    t, v = _volts(folder / s.csv)
    times = frame_times(experiment)
    source = declaration(manifest, s.source)
    return {"t": [x - s.offset_s for x in t], "v": v, "dt": float(np.median(np.diff(t))),
            "short_threshold_v": None, "segments": [], "clock": "log",
            "source": {"path": str(folder / s.csv), "uri": f"data/experiments/{manifest.id}/{s.csv}",
                       "label": f"{manifest.title}: voltage log", "citation": manifest.citation,
                       "licence": manifest.licence},
            "movie_time_range_s": [times[0], times[-1]] if times else None,
            "time_base_source": time_base_source(experiment),
            "time_uncertainty_s": frame_time_uncertainty(experiment) or 0,
            "method": f"{source}; voltage log time minus {s.offset_s:g} s gives time from the first video frame; "
                      "offset is declared, not independently verified",
            "notes": manifest.notes or ""}


def register(ledger: Ledger, manifest_path_or_dict, files=None):
    """Register a JSON manifest and local files (name -> path, or a directory); return its imported video Artifact."""
    from translucent import tools
    base = Path.cwd()
    if isinstance(manifest_path_or_dict, Manifest):
        manifest = manifest_path_or_dict
    elif isinstance(manifest_path_or_dict, dict):
        manifest = Manifest.model_validate(manifest_path_or_dict)
    else:
        path = Path(manifest_path_or_dict)
        base = path.resolve().parent
        try:
            manifest = Manifest.model_validate_json(path.read_text(encoding="utf-8"))
        except UnicodeError as e:
            raise ValueError("the manifest must be a UTF-8 JSON file") from e
    if files is None or isinstance(files, (str, Path)):
        base = Path(files) if files is not None else base
        paths = {name: base / name for name in manifest.files()}
    else:
        missing = [n for n in manifest.files() if n not in files]
        if missing:
            raise ValueError("missing file: " + ", ".join(missing))
        paths = {n: Path(files[n]) for n in manifest.files()}
    ROOT.mkdir(parents=True, exist_ok=True)
    folder, created = ROOT / manifest.id, False
    with tempfile.TemporaryDirectory(prefix=".incoming-", dir=ROOT) as tmp:
        stage = Path(tmp)
        for name, path in paths.items():
            if not path.is_file():
                raise ValueError(f"{name}: file not found; provide the recording and its CSV files")
            shutil.copyfile(path, stage / name)
        (stage / "manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
        hashes = {n: sha256_file(stage / n) for n in ["manifest.json"] + manifest.files()}
        if any(sha256_file(paths[n]) != hashes[n] for n in manifest.files()):
            raise ValueError("an input file changed while it was being copied; retry with a saved copy")
        if folder.exists():
            _, old, _ = load(f"user/{manifest.id}")
            if hashes[manifest.video] != old["sha256"].get(manifest.video):
                raise ValueError(f"{manifest.id}: a different video is already registered under this id; use a new id")
            if hashes != old["sha256"]:
                raise ValueError(f"{manifest.id}: the manifest or lab records differ from the registered copy; use a new id")
            if any(sha256_file(folder / n) != digest for n, digest in hashes.items()):
                raise ValueError(f"{manifest.id}: stored files have changed since registration")
        else:
            # decoders guess the format from content: only a real MP4/MOV may reach them (not a playlist posing as one)
            with (stage / manifest.video).open("rb") as f:
                head = f.read(12)
            if head[4:8] not in (b"ftyp", b"moov", b"wide", b"free", b"mdat"):
                raise ValueError(f"{manifest.video} is not an MP4 or MOV video; export the recording as MP4 and try again")
            captures = _captures(stage / manifest.video, interval=bool(manifest.clock and manifest.clock.interval_s))
            if manifest.clock and manifest.clock.timestamps_csv:
                _timestamps(stage / manifest.clock.timestamps_csv, len(captures), captures)
            if manifest.clock and manifest.clock.interval_s is not None:
                if not math.isfinite(captures[-1] * manifest.clock.interval_s):
                    raise ValueError("acquisition interval gives an elapsed time too large to represent in seconds")
            if manifest.voltage:
                t, _ = _volts(stage / manifest.voltage.csv)
                if not all(math.isfinite(x - manifest.voltage.offset_s) for x in t):
                    raise ValueError("voltage offset gives times too large to represent in seconds")
            record = {"sha256": hashes, "frames": len(captures), "capture_indices": captures}
            (stage / "record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            (stage / "record.sha256").write_text(sha256_file(stage / "record.json") + "\n", encoding="ascii")
            stage.rename(folder)
            created = True
        try:
            return tools.import_video(ledger, folder / manifest.video, label=manifest.title,
                                      citation=manifest.citation, licence=manifest.licence,
                                      experiment=f"user/{manifest.id}")
        except Exception as e:
            if created:
                shutil.rmtree(folder)
            if isinstance(e, subprocess.CalledProcessError):
                raise ValueError("the video could not be prepared for playback; export it as an MP4 and try again") from e
            raise


def list_experiments(ledger: Ledger) -> list[dict]:
    return [{"experiment": a.source.uri.removeprefix("experiment:"), "video_id": a.id, "label": a.label,
             "citation": a.source.citation, "licence": a.source.licence}
            for a in ledger.list("artifact") if a.kind == "video" and a.source
            and a.source.uri.startswith("experiment:")]
