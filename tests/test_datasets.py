"""Import v2: a team's own records into a ledger. Everything here is offline."""
import json
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from support.analytic_system import AnalyticSystem

from translucent import datasets, experiments, systems
from translucent.ledger import Ledger, sha256_file
from translucent.loop_contracts import OperatorSpec

DATA = Path(__file__).parent / "data" / "import_v2"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ledger(tmp_path):
    L = Ledger(tmp_path / "ledger")
    yield L
    L.db.close()


@pytest.fixture
def src(tmp_path):
    """A copy of the fixture folder, safe to edit."""
    out = tmp_path / "src"
    shutil.copytree(DATA, out)
    return out


def manifest(name="manifest.json"):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def series(L, ch):
    return json.loads(L.blob_path(ch.series_id).read_bytes())


def artifacts(L):
    return {a.id for a in L.list("artifact")}


# ---------------------------------------------------------------- what imports

def test_eclab_export_imports_with_units_and_offsets_in_the_transform(ledger):
    d = datasets.import_dataset(ledger, DATA / "manifest.json")
    assert ledger.get("dataset", d.id) == d and d.manifest_version == 2 and d.upgraded_from is None
    rec = d.records[0]
    assert rec.specimen.id == "cell-0007" and rec.role == "baseline"
    assert [(c.column, c.quantity, c.unit, c.n) for c in rec.channels] == [
        ("Ecell/V", "voltage", "V", 21), ("I/mA", "current", "mA", 21), ("Temperature/°C", "temperature", "degC", 21)]
    voltage, current, temp = (series(ledger, c) for c in rec.channels)
    # record time = column time - offset_s (10 s); values keep their source unit
    assert voltage["t"][:3] == [-10.0, -5.0, 0.0] and voltage["t_unit"] == "s"
    assert voltage["y"][:3] == [3.6, 3.605, 3.61] and current["y"][0] == 1000.0 and current["unit"] == "mA"
    assert temp["y"][-1] == 27.0 and rec.channels[0].t_range == (-10.0, 90.0)
    assert rec.channels[0].uncertainty.kind == "resolution" and rec.channels[1].uncertainty.kind == "none"
    art = ledger.get("artifact", rec.channels[0].series_id)
    p = art.transform.params
    assert art.kind == "series" and art.transform.op == "dataset_parse/1"
    assert art.transform.parent_ids == [rec.files["eclab-cell07.mpt"]]
    assert (p["unit"], p["time_unit"], p["time_to_s"], p["offset_s"], p["header_row"]) == ("V", "s", ["1", "0"], 10.0, 5)
    assert (p["delimiter"], p["encoding"], p["rows"], p["repeated_times"]) == ("\t", "cp1252", 21, 0)
    assert "mode" in rec.ignored_columns and "time/s" not in rec.ignored_columns
    raw = ledger.get("artifact", rec.files["eclab-cell07.mpt"])
    assert raw.kind == "table" and raw.source.uri == f"dataset:{d.id}/eclab-cell07.mpt"
    assert raw.id == "sha256:" + sha256_file(DATA / "eclab-cell07.mpt")
    assert rec.scalars[0].quantity == "charge_time_80" and rec.conditions_units == {"ambient": "degC"}


def test_time_units_unit_rows_millivolts_and_json_columns(ledger):
    d = datasets.import_dataset(ledger, DATA / "manifest.json")
    mv, js = d.records[1], d.records[2]
    v = series(ledger, mv.channels[0])
    # minutes to seconds, then minus the 30 s offset; the unit row after the header is skipped
    assert v["t"] == [-30.0, 0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0] and v["y"][0] == 3500.0 and v["unit"] == "mV"
    p = ledger.get("artifact", mv.channels[0].series_id).transform.params
    assert (p["time_unit"], p["time_to_s"], p["skip_rows"]) == ("min", ["60", "0"], 1)
    t = series(ledger, js.channels[0])
    assert t["t"] == [0.0, 10.0, 20.0, 30.0, 40.0] and t["y"][1] == 300.15 and js.ignored_columns == ["probe"]


def test_images_are_media_and_no_movie_is_needed(ledger):
    d = datasets.import_dataset(ledger, DATA / "manifest.json")
    (img,) = d.records[0].media
    assert img["kind"] == "image" and img["media_type"] == "image/png" and img["shape"] == [16, 24]
    assert img["needs_vision"] and img["label"] == "Anode after test"
    assert ledger.get("artifact", img["artifact_id"]).kind == "frame"
    assert not any(a.kind == "video" for a in ledger.list("artifact"))


def test_chen2020_maccor_export_imports_as_published(ledger):
    """Real records: the Maccor preamble, CRLF, an unsigned current and two rows logged at one test time."""
    d = datasets.import_dataset(ledger, DATA / "chen2020-manifest.json")
    (rec,) = d.records
    assert d.licence == "CC BY 4.0" and "10.5281/zenodo.4032561" in d.citation
    assert rec.specimen.id == "LGM50_cell02" and rec.provenance.instrument == "Maccor #3"
    assert rec.files["LGM50_cell02_excerpt.csv"] == "sha256:" + sha256_file(DATA / "LGM50_cell02_excerpt.csv")
    assert [c.quantity for c in rec.channels] == ["voltage", "current", "temperature", "lab:chamber_temperature"]
    assert all(c.n == 300 for c in rec.channels) and rec.channels[0].t_range == (71140.32, 79225.12998)
    assert ledger.get("artifact", rec.channels[0].series_id).transform.params["repeated_times"] == 1
    assert {"Rec", "Md", "DPT Time", "Step Time [s]"} <= set(rec.ignored_columns)
    v = series(ledger, rec.channels[0])
    i = v["t"].index(73649.64)  # records 2685 and 2686 share this time (a suspended test): both rows kept, in order
    assert v["t"][i + 1] == 73649.64 and v["y"][i:i + 2] == [2.8821, 2.8975]
    out = datasets.measure(ledger, d.id, rec.id, OperatorSpec(op="max", channel="temperature", unit="K"))
    assert out["value"] == max(series(ledger, rec.channels[2])["y"]) + 273.15


# ---------------------------------------------------------------- refusals name the file, row and column

def _edit(src, change, name="manifest.json"):
    m = json.loads((src / name).read_text(encoding="utf-8"))
    change(m)
    return m


def _rewrite(path, old, new, count=1, encoding="cp1252"):
    text = path.read_bytes().decode(encoding)
    assert old in text
    path.write_bytes(text.replace(old, new, count).encode(encoding))


@pytest.mark.parametrize("case, words", [
    ("unknown unit", r"eclab-cell07\.mpt, column 'I/mA': unknown unit 'mAmp'"),
    ("non-monotonic time", r"eclab-cell07\.mpt, line 11, column 'time/s': time goes back from 15 to 14"),
    ("NaN", r"eclab-cell07\.mpt, line 9, column 'Ecell/V': 'NaN' is not a finite number"),
    ("empty value", r"cell08-mv\.csv, line 5, column 'I': the value is empty"),
    ("not a number", r"cell08-mv\.csv, line 4, column 'V': '3,52' is not a number"),
    ("missing column", r"eclab-cell07\.mpt, header on line 6: no column 'Ewe/V'"),
    ("path traversal", r"record cell07-cc table '\.\./secrets\.csv': use a filename without folders"),
    ("windows path", r"record cell07-cc media 'C:\\\\data\\\\x\.png': use a filename without folders"),
    ("JSON NaN", r"cell09-thermal\.json, row 3, column 'T_K': NaN is not a finite number"),
    ("JSON string", r"cell09-thermal\.json, row 2, column 't_s': \"10\" is not a number"),
    ("time column listed", r"column 'elapsed': it is the time column"),
    ("duplicate quantity", r"column 'I': record cell08-cc already has voltage from column 'V'"),
    ("no time base", r"record cell09-thermal: give its time base"),
    ("blank line", r"cell08-mv\.csv, line 5: a blank line inside the data"),
    ("short row", r"cell08-mv\.csv, line 6: 2 fields, but column 'I' is field 3"),
    ("configuration unit", r"configuration 'c_rate': give its unit in configuration_units"),
    ("not an image", r"teardown-cell07\.png: not a PNG, JPEG or TIFF image"),
    ("decimal comma", r"a comma cannot be both the decimal mark and the delimiter"),
])
def test_refusals_are_plain_and_store_nothing(ledger, src, case, words):
    m = manifest()
    r07, r08, r09 = m["records"]
    mpt, mv, js = src / "eclab-cell07.mpt", src / "cell08-mv.csv", src / "cell09-thermal.json"
    if case == "unknown unit":
        r07["tables"][0]["columns"]["I/mA"]["unit"] = "mAmp"
    elif case == "non-monotonic time":
        _rewrite(mpt, "2.000000000000000E+01", "1.400000000000000E+01")  # line 11: 20 s -> 14 s, after 15 s
    elif case == "NaN":
        _rewrite(mpt, "3.6100000E+00", "NaN")
    elif case == "empty value":
        _rewrite(mv, "1,3540,2.5", "1,3540,", encoding="utf-8")
    elif case == "not a number":
        _rewrite(mv, "0.5,3520", '0.5,"3,52"', encoding="utf-8")
    elif case == "missing column":
        r07["tables"][0]["columns"]["Ewe/V"] = r07["tables"][0]["columns"].pop("Ecell/V")
    elif case == "path traversal":
        r07["tables"][0]["file"] = "../secrets.csv"
    elif case == "windows path":
        r07["media"][0]["file"] = "C:\\data\\x.png"
    elif case == "JSON NaN":
        js.write_text('{"t_s": [0, 10, 20], "T_K": [298.15, 299.0, NaN]}')
    elif case == "JSON string":
        js.write_text('[{"t_s": 0, "T_K": 298.15}, {"t_s": "10", "T_K": 299.0}]')
    elif case == "time column listed":
        r08["tables"][0]["columns"]["elapsed"] = {"quantity": "lab:t", "unit": "min"}
    elif case == "duplicate quantity":
        r08["tables"][0]["columns"]["I"]["quantity"] = "voltage"
    elif case == "no time base":
        del r09["time"]
    elif case == "blank line":
        _rewrite(mv, "1,3540", "\n1,3540", encoding="utf-8")
    elif case == "short row":
        _rewrite(mv, "1.5,3560,2.5", "1.5,3560", encoding="utf-8")
    elif case == "configuration unit":
        del r08["configuration_units"]
    elif case == "not an image":
        (src / "teardown-cell07.png").write_bytes(b"GIF89a" + bytes(40))
    elif case == "decimal comma":
        r08["tables"][0]["decimal"] = ","
    with pytest.raises(ValueError, match=words):
        datasets.import_dataset(ledger, m, src)
    assert artifacts(ledger) == set() and ledger.list("dataset") == []


def test_cross_dimension_unit_for_a_bound_system_is_refused(ledger, src, monkeypatch):
    monkeypatch.setattr(systems, "_ADAPTERS", dict(systems._ADAPTERS))
    systems.register(AnalyticSystem())
    m = manifest()
    m["system"] = "analytic-cell"
    m["records"] = [m["records"][2]]          # temperature in K: the system's degC dimension, so it imports
    assert datasets.import_dataset(ledger, m, src).system == "analytic-cell"
    m["id"] = "thermal-in-volts"
    m["records"][0]["tables"][0]["columns"]["T_K"]["unit"] = "V"
    with pytest.raises(ValueError, match=r"cell09-thermal\.json, column 'T_K': unit V does not measure temperature; "
                                         r"system analytic-cell records it in degC"):
        datasets.import_dataset(ledger, m, src)
    m["records"][0]["tables"][0]["columns"]["T_K"] = {"quantity": "pressure", "unit": "Pa"}
    with pytest.raises(ValueError, match=r"'pressure' is not a quantity of system analytic-cell"):
        datasets.import_dataset(ledger, m, src)
    m["records"][0]["tables"][0]["columns"]["T_K"] = {"quantity": "lab:pressure", "unit": "Pa"}
    assert datasets.import_dataset(ledger, m, src).records[0].channels[0].quantity == "lab:pressure"
    m["id"], m["system"] = "unknown-system", "no-such-system"
    with pytest.raises(ValueError, match=r"system 'no-such-system' is not registered here"):
        datasets.import_dataset(ledger, m, src)


def test_more_than_64_files_is_refused(ledger, src):
    m = manifest()
    r = m["records"][0]
    r["media"] = [{"file": f"img{i:02d}.png", "kind": "image"} for i in range(64)]
    with pytest.raises(ValueError, match=r"the manifest names 67 files; one import takes at most 64"):
        datasets.import_dataset(ledger, m, src)
    with pytest.raises(ValueError, match=r"65 files were given; one import takes at most 64"):
        datasets.import_dataset(ledger, manifest(), {f"f{i}": src / "x" for i in range(65)})


def test_a_table_many_records_name_is_parsed_once_and_an_import_holds_bounded_points(ledger, tmp_path, monkeypatch):
    """500 records may name one file: it is parsed once for all of them (with every column any of them reads), and
    the rows parsed and series points held before storing are capped, so the records cannot multiply the work."""
    (tmp_path / "t.csv").write_text("t,a,b,note\n" + "".join(f"{i},{i * 0.5},{-i},n\n" for i in range(50)))
    rec = lambda i, cols, offset: {
        "id": f"r{i}", "specimen": {"id": f"s{i}"}, "provenance": {"source": "x"},
        "time": {"unit": "s", "origin": "start", "source": "x", "offset_s": offset},
        "tables": [{"file": "t.csv", "time_column": "t",
                    "columns": {c: {"quantity": f"lab:{c}", "unit": "V"} for c in cols}}]}
    m = {"manifest_version": 2, "id": "shared", "title": "t", "author": "a", "citation": "c", "licence": "l",
         "records": [rec(i, ["a"] if i % 2 else ["a", "b"], 0.0) for i in range(500)]}
    parses, real = [], datasets._table
    monkeypatch.setattr(datasets, "_table", lambda path, spec: parses.append(sorted(spec.columns)) or real(path, spec))
    d = datasets.import_dataset(ledger, m, tmp_path)
    assert parses == [["a", "b"]] and len(d.records) == 500
    assert d.records[1].ignored_columns == ["b", "note"] and d.records[0].ignored_columns == ["note"]
    assert {c.series_id for r in d.records for c in r.channels if c.column == "a"} == {d.records[0].channels[0].series_id}
    # different time bases make different series: their points count against the import's cap
    monkeypatch.setattr(datasets, "MAX_IMPORT_POINTS", 50 * 20)
    m2 = {**m, "id": "offsets", "records": [rec(i, ["a"], float(i)) for i in range(21)]}
    with pytest.raises(ValueError, match="series hold more than 1,000 points in all; split the records"):
        datasets.import_dataset(ledger, m2, tmp_path)
    monkeypatch.setattr(datasets, "MAX_IMPORT_POINTS", 40)
    with pytest.raises(ValueError, match="tables hold more than 40 rows in all"):
        datasets.import_dataset(ledger, {**m, "id": "rows"}, tmp_path)
    assert ledger.get("dataset", "offsets") is None and ledger.get("dataset", "rows") is None


def test_a_json_table_is_size_checked_before_it_is_parsed(ledger, tmp_path, monkeypatch):
    """json.loads of [{},{},...] takes tens of times the file's size before the 2M-row check could run."""
    (tmp_path / "t.json").write_text("[" + "{}," * 1000)       # not even JSON: parsing first would say so
    m = {"manifest_version": 2, "id": "json-table", "title": "t", "author": "a", "citation": "c", "licence": "l",
         "records": [{"id": "r", "specimen": {"id": "s"}, "provenance": {"source": "x"},
                      "time": {"unit": "s", "origin": "start", "source": "x"},
                      "tables": [{"file": "t.json", "format": "json", "time_column": "t",
                                  "columns": {"y": {"quantity": "lab:y", "unit": "V"}}}]}]}
    with pytest.raises(ValueError, match="t.json: line 1"):
        datasets.import_dataset(ledger, m, tmp_path)
    monkeypatch.setattr(datasets, "MAX_JSON_TABLE_BYTES", 2048)
    with pytest.raises(ValueError, match="t.json: a JSON table can be up to 0 MB; send a larger one as CSV"):
        datasets.import_dataset(ledger, m, tmp_path)


def test_missing_and_unnamed_files_are_refused(ledger, src):
    files = {p.name: p for p in src.iterdir() if p.is_file()}
    with pytest.raises(ValueError, match=r"the manifest does not name these files: \.gitattributes, LGM50"):
        datasets.import_dataset(ledger, manifest(), files)
    (src / "cell08-mv.csv").unlink()
    with pytest.raises(ValueError, match=r"cell08-mv\.csv: file not found"):
        datasets.import_dataset(ledger, manifest(), src)
    with pytest.raises(ValueError, match=r"say where the files are"):
        datasets.import_dataset(ledger, manifest())


def test_free_text_is_scrubbed_of_secrets(ledger, src):
    m = manifest()
    key = "sk-proj-" + "a1B2c3D4e5F6g7H8i9J0" * 3
    m["notes"] = f"uploaded with api_key={key}"
    m["records"][0]["provenance"]["notes"] = f"token {key}"
    d = datasets.import_dataset(ledger, m, src)
    stored = ledger.blob_path(d.manifest_id).read_text()
    assert key not in stored and key not in d.records[0].provenance.notes and "[secret removed]" in stored


# ---------------------------------------------------------------- idempotency and conflicts

def test_same_import_is_idempotent_and_a_different_body_conflicts_only_in_this_ledger(ledger, src, tmp_path):
    first = datasets.import_dataset(ledger, DATA / "manifest.json")
    before = artifacts(ledger)
    again = datasets.import_dataset(ledger, manifest(), src)
    assert again == first and artifacts(ledger) == before and len(ledger.list("dataset")) == 1
    _rewrite(src / "cell08-mv.csv", "3500", "3501", encoding="utf-8")
    with pytest.raises(datasets.DatasetConflict, match="a different dataset already has this id in this ledger"):
        datasets.import_dataset(ledger, manifest(), src)
    assert artifacts(ledger) == before, "a refused import stores nothing"
    other = Ledger(tmp_path / "other")
    try:
        d = datasets.import_dataset(other, manifest(), src)
        assert d.id == first.id and d.records[1].channels[0].series_id != first.records[1].channels[0].series_id
    finally:
        other.db.close()


WORKER = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    from translucent import datasets
    from translucent.ledger import Ledger
    L = Ledger(sys.argv[1])
    while not Path(sys.argv[3]).exists():
        time.sleep(0.005)
    try:
        print(datasets.import_dataset(L, sys.argv[2]).model_dump_json())
    finally:
        L.db.close()
""")


def test_concurrent_identical_imports_make_one_record(tmp_path):
    root, go = tmp_path / "shared", tmp_path / "go"
    Ledger(root).db.close()
    workers = [subprocess.Popen([sys.executable, "-c", WORKER, str(root), str(DATA / "manifest.json"), str(go)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT)
               for _ in range(3)]
    time.sleep(1.5)        # let every worker import and open the ledger, then start them together
    go.touch()
    outs = [w.communicate(timeout=180) for w in workers]
    assert all(w.returncode == 0 for w in workers), [e[-800:] for _, e in outs]
    bodies = {json.dumps({k: v for k, v in json.loads(o).items() if k != "created_at"}) for o, _ in outs}
    assert len(bodies) == 1
    L = Ledger(root)
    try:
        assert len(L.list("dataset")) == 1
    finally:
        L.db.close()


# ---------------------------------------------------------------- v1 manifests and media formats

def _video(path, frames=8, fourcc="mp4v", size=(64, 48)):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), 10, size)
    assert writer.isOpened()
    for i in range(frames):
        frame = np.zeros((size[1], size[0], 3), np.uint8)
        frame[10:30, 4:8 + 6 * i] = 255
        writer.write(frame)
    writer.release()


def test_v1_manifest_through_v2_is_one_record_with_video_media_and_a_voltage_channel(ledger, tmp_path):
    v1 = tmp_path / "v1"
    shutil.copytree(DATA / "v1", v1)
    _video(v1 / "cell-e2e.mp4")
    d = datasets.import_dataset(ledger, v1 / "manifest.json")
    assert d.upgraded_from == "v1" and d.manifest_version == 1 and d.id == "cell-e2e"
    (rec,) = d.records
    (video,) = rec.media
    assert video["kind"] == "video" and video["media_type"] == "video/mp4" and video["shape"] == [8, 48, 64]
    assert video["t_s"] == 0.0 and video["clock"]["timestamps_csv"] == "frames.csv"
    assert video["timestamps_id"] == rec.files["frames.csv"] and ledger.get("artifact", video["artifact_id"]).kind == "video"
    (ch,) = rec.channels
    assert (ch.quantity, ch.unit, ch.column) == ("voltage", "V", "voltage_V")
    s = series(ledger, ch)
    assert s["t"][0] == -2.0 and s["t"][-1] == 16.0, "voltage-log time minus the declared offset (12 s)"
    assert rec.time.offset_s == 12 and rec.scalars[0].value == 0.5 and rec.scalars[0].unit == "um"
    # the same manifest also validates as a v1 Manifest object
    assert datasets.import_dataset(ledger, experiments.Manifest.model_validate(json.loads(
        (v1 / "manifest.json").read_text())), v1) == d


def _tiff(path, pages):
    ok, buf = cv2.imencodemulti(".tiff", pages) if len(pages) > 1 else cv2.imencode(".tiff", pages[0])
    assert ok
    path.write_bytes(buf.tobytes())


def _media_manifest(media, files=()):
    return {"manifest_version": 2, "id": "media-check", "title": "Media", "author": "A", "citation": "c",
            "licence": "internal", "records": [{"id": "r1", "specimen": {"id": "s1"}, "media": media,
                                                 "provenance": {"source": "bench"}}]}


def test_common_image_and_video_formats_are_accepted(ledger, tmp_path):
    pages = [np.full((12, 20), 40 * i, np.uint8) for i in range(3)]
    _tiff(tmp_path / "stack.tif", pages)
    _tiff(tmp_path / "one.tiff", pages[:1])
    ok, jpg = cv2.imencode(".jpg", np.zeros((10, 10, 3), np.uint8))
    (tmp_path / "photo.jpg").write_bytes(jpg.tobytes())
    _video(tmp_path / "clip.avi", fourcc="MJPG")
    _video(tmp_path / "clip.mp4")
    media = [{"file": "stack.tif", "kind": "image"}, {"file": "one.tiff", "kind": "image", "t_s": 5.0},
             {"file": "photo.jpg", "kind": "image"},
             {"file": "clip.avi", "kind": "video", "clock": {"interval_s": 2.0, "source": "camera log"}},
             {"file": "clip.mp4", "kind": "video"},
             {"file": "stack.tif", "kind": "video", "clock": {"interval_s": 60, "source": "microscope"}}]
    with pytest.raises(ValueError, match="stack.tif: used both as image and as video"):
        datasets.import_dataset(ledger, _media_manifest(media), tmp_path)
    shutil.copyfile(tmp_path / "stack.tif", tmp_path / "timelapse.tif")
    media[-1]["file"] = "timelapse.tif"
    d = datasets.import_dataset(ledger, _media_manifest(media), tmp_path)
    got = {m["file"]: (m["media_type"], m["shape"]) for m in d.records[0].media}
    assert got == {"stack.tif": ("image/tiff", [3, 12, 20]), "one.tiff": ("image/tiff", [12, 20]),
                   "photo.jpg": ("image/jpeg", [10, 10]), "clip.avi": ("video/x-msvideo", [8, 48, 64]),
                   "clip.mp4": ("video/mp4", [8, 48, 64]), "timelapse.tif": ("image/tiff", [3, 12, 20])}
    assert d.records[0].media[3]["clock"] == {"interval_s": 2.0, "uncertainty_s": 0.0, "source": "camera log"}
    assert d.records[0].channels == [] and d.records[0].time is None


@pytest.mark.parametrize("media, payload, words", [
    ({"file": "x.gif", "kind": "image"}, b"GIF89a" + bytes(30), "not a PNG, JPEG or TIFF image"),
    ({"file": "x.mp4", "kind": "video"}, b"#EXTM3U\nhttp://example.com/a.ts\n", "not an MP4, MOV or AVI video"),
    ({"file": "x.png", "kind": "image"}, b"\x89PNG\r\n\x1a\n" + bytes(30), "could not be decoded"),
    ({"file": "x.png", "kind": "image", "clock": {"interval_s": 1, "source": "s"}}, b"", "a clock belongs to a video"),
    ({"file": "x.mp4", "kind": "video", "clock": {"interval_s": 1}}, b"", "x.mp4: clock:"),
])
def test_unsupported_media_is_refused_by_its_bytes(ledger, tmp_path, media, payload, words):
    (tmp_path / media["file"]).write_bytes(payload)
    with pytest.raises(ValueError, match=words):
        datasets.import_dataset(ledger, _media_manifest([media]), tmp_path)
    assert artifacts(ledger) == set()


def test_video_timestamps_keep_the_v1_rule_of_one_row_per_frame(ledger, tmp_path):
    _video(tmp_path / "clip.mp4")
    (tmp_path / "t.csv").write_text("frame,time_s\n" + "".join(f"{i},{i}\n" for i in range(7)))
    media = [{"file": "clip.mp4", "kind": "video", "clock": {"timestamps_csv": "t.csv", "source": "log"}}]
    with pytest.raises(ValueError, match=r"clip\.mp4 clock: t\.csv: expected one timestamp per source frame"):
        datasets.import_dataset(ledger, _media_manifest(media), tmp_path)


def test_decimal_comma_with_semicolons_and_a_long_preamble(ledger, tmp_path):
    lines = [f"preamble line {i}; with; separators" for i in range(40)] + ["t;U;T", "0;3,5;25,0", "1,5;3,75;25,5"]
    (tmp_path / "eu.csv").write_text("\n".join(lines), encoding="utf-8")
    m = _media_manifest([])
    m["records"][0].update(time={"origin": "start", "source": "log"}, tables=[{
        "file": "eu.csv", "header_row": 40, "delimiter": ";", "decimal": ",", "time_column": "t",
        "columns": {"U": {"quantity": "voltage", "unit": "V"}, "T": {"quantity": "temperature", "unit": "degC"}}}])
    d = datasets.import_dataset(ledger, m, tmp_path)
    s = series(ledger, d.records[0].channels[0])
    assert s["t"] == [0.0, 1.5] and s["y"] == [3.5, 3.75]
    m["id"] = "media-check-2"
    m["records"][0]["tables"][0]["format"] = "tsv"
    m["records"][0]["tables"][0]["delimiter"] = None
    with pytest.raises(ValueError, match=r"eu\.csv, header on line 41: no column 't'.*check format and delimiter"):
        datasets.import_dataset(ledger, m, tmp_path)


def _fuzz_table(rng, tmp_path, corrupt):
    """A random instrument-style export, the manifest for it, and what a corruption (if any) must be refused for."""
    delim = rng.choice([",", "\t", ";"])
    decimal = "," if delim != "," and rng.random() < 0.4 else "."
    eol = rng.choice(["\r\n", "\n"])
    encoding = rng.choice(["utf-8-sig", "utf-8", "cp1252"])
    trailing = delim if rng.random() < 0.3 else ""
    pre = [rng.choice(["Instrument: X", 'Note "quoted, with commas"; and; semicolons', "", "Nb header lines : 9",
                       "Température d'essai\t25 °C"]) for _ in range(rng.randint(0, 30))]
    names = ["time", "U/V", "I/mA", "note"]
    n = rng.randint(2, 60)
    t, u, i = [], [], []
    for k in range(n):
        t.append(round((t[-1] if t else 0) + rng.choice([0, 0.5, 1, 2.25]) * (k > 0), 6))
        u.append(round(rng.uniform(2.5, 4.2), rng.randint(0, 6)))
        i.append(rng.choice([0.0, -1500.0, 1e-3, 1234.5e3]))
    fmt = lambda v: repr(float(v)).replace(".", decimal) if decimal == "," else repr(float(v))
    rows = [[fmt(a), fmt(b), fmt(c), "x"] for a, b, c in zip(t, u, i)]
    want = None
    if corrupt:
        k, col = rng.randrange(n), rng.choice([0, 1, 2])
        bad = rng.choice(["NaN", "", "abc", "inf", "decrease"])
        if bad == "decrease":
            k, col = (rng.randrange(1, n), 0) if n > 1 else (0, 0)
            rows[k][0] = fmt(t[k - 1] - 1) if k else rows[k][0]
            if not k:
                bad = None
        else:
            rows[k][col] = bad
        if bad is not None:
            want = (len(pre) + 2 + k, names[col])
    text = eol.join(pre + [delim.join(names) + trailing] + [delim.join(r) + trailing for r in rows]) + eol
    (tmp_path / "f.txt").write_bytes(text.encode(encoding))
    m = _media_manifest([])
    m["records"][0].update(time={"origin": "start", "source": "log", "offset_s": 0.5}, tables=[{
        "file": "f.txt", "format": "tsv" if delim == "\t" else "csv", "delimiter": None if delim == "\t" else delim,
        "header_row": len(pre), "decimal": decimal, "time_column": "time",
        "columns": {"U/V": {"quantity": "voltage", "unit": "V"}, "I/mA": {"quantity": "current", "unit": "mA"}}}])
    return m, (t, u, i), want


@pytest.mark.parametrize("seed", range(40))
def test_fuzzed_exports_parse_exactly_and_refusals_name_the_line(tmp_path, seed):
    import random
    rng = random.Random(seed)
    L = Ledger(tmp_path / "ledger")
    try:
        m, (t, u, i), _ = _fuzz_table(rng, tmp_path, corrupt=False)
        rec = datasets.import_dataset(L, m, tmp_path).records[0]
        su, si = series(L, rec.channels[0]), series(L, rec.channels[1])
        assert su["t"] == [x - 0.5 for x in t] and su["y"] == u and si["y"] == i
        assert rec.ignored_columns == ["note"]
        m, _, want = _fuzz_table(rng, tmp_path, corrupt=True)
        m["id"] = "media-check-2"
        if want is None:
            return
        line, col = want
        with pytest.raises(ValueError, match=rf"f\.txt, line {line}, column '{col}': "):
            datasets.import_dataset(L, m, tmp_path)
    finally:
        L.db.close()


def test_the_documented_example_is_a_valid_manifest():
    doc = (ROOT / "docs" / "BRING-YOUR-OWN.md").read_text(encoding="utf-8")
    example = doc.split("## Records without a video")[1].split("```json")[1].split("```")[0]
    m = datasets.ManifestV2.model_validate(json.loads(example))
    assert set(datasets._check(m, None)) == {"cell07.mpt", "teardown.png"}


def test_configuration_hash_names_the_system_and_the_units():
    h = datasets.configuration_hash
    assert h(None, {"c": 3.0, "p": "CC"}, {"c": "1/h"}) == h(None, {"p": "CC", "c": 3.0}, {"c": "1/h"})
    assert h(None, {"c": 3.0}, {"c": "1/h"}) != h(None, {"c": 3.0}, {"c": "1/min"})
    assert h(None, {"c": 3.0}, {"c": "1/h"}) != h("analytic-cell", {"c": 3.0}, {"c": "1/h"})


def test_v1_add_experiment_still_registers(tmp_path, monkeypatch):
    """The v1 flow is unchanged: a v1 manifest still registers through experiments.register."""
    monkeypatch.setattr(experiments, "ROOT", tmp_path / "experiments")
    v1 = tmp_path / "v1"
    shutil.copytree(DATA / "v1", v1)
    _video(v1 / "cell-e2e.mp4")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is needed to remux the video")
    L = Ledger(tmp_path / "ledger")
    try:
        video = experiments.register(L, v1 / "manifest.json")
        assert video.source.uri == "experiment:user/cell-e2e"
        assert experiments.voltage("user/cell-e2e")["t"][0] == -2.0
    finally:
        L.db.close()
