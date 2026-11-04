"""An import never drops or changes experimental evidence without saying so.

The records are exact lines of the file a planner imported in a recorded records campaign (case H3, run 2: the
published LG M50 cell02 test re-exported with a German logger's settings), and the manifest is the one it sent.
That manifest set skip_rows 5 as if it counted the preamble, so the first five rows of data were dropped without
a word; and the evaluation's comparison matched the two rows logged at 73649.64 s to one value, which reported a
15 mV difference where the import had none."""
import json
from pathlib import Path

import pytest

from evals import records_intake as ri
from translucent import datasets, records
from translucent.ledger import Ledger
from translucent.systems import lgm50_lab as lab

DATA = Path(__file__).parent / "data" / "import_v2"
FILE = "cell02_export_excerpt.csv"


@pytest.fixture
def ledger(tmp_path):
    L = Ledger(tmp_path / "ledger")
    yield L
    L.db.close()


def sent(**table) -> dict:
    """The manifest the planner sent, for this campaign's system, with `table` settings changed."""
    m = json.loads((DATA / "h3-skip-rows-manifest.json").read_text(encoding="utf-8"))
    m["system"] = lab.SYSTEM_ID
    m["records"][0]["tables"][0].update(table)
    return m


def values(L, rec, quantity):
    ch = next(c for c in rec.channels if c.quantity == quantity)
    return json.loads(L.blob_path(ch.series_id).read_bytes())


def test_skip_rows_never_drops_rows_of_data(ledger):
    with pytest.raises(ValueError) as e:
        datasets.import_dataset(ledger, sent(), {FILE: DATA / FILE})
    msg = str(e.value)
    # line 7 is the first row of data (t = 0 s); the message says what skip_rows is for and what header_row counts
    assert f"{FILE}, line 7:" in msg and "row of data (time '0')" in msg and "header_row (5 here)" in msg
    assert not ledger.list("dataset")


def test_the_corrected_import_keeps_every_row_as_written(ledger):
    d = datasets.import_dataset(ledger, sent(skip_rows=0), {FILE: DATA / FILE})
    (rec,) = d.records
    v = values(ledger, rec, "voltage")
    # all eleven rows, the five the first manifest dropped among them, at the times and values the file has
    assert v["t"][:6] == [0.0, 10.00002, 19.99998, 30.0, 40.00002, 49.99998] and len(v["t"]) == 11
    assert v["y"][:2] == [3.1313, 3.1315] and v["unit"] == "V"
    # the two rows logged at 73649.64 s (a suspended step, mode SBr then R) are both kept, in file order
    i = v["t"].index(73649.64)
    assert v["t"][i + 1] == 73649.64 and v["y"][i:i + 2] == [2.8821, 2.8975]
    assert values(ledger, rec, "current")["y"][0] == 0.0 and values(ledger, rec, "current")["unit"] == "mA"
    assert values(ledger, rec, "surface_temperature")["y"][0] == 300.1323     # kelvin, as written
    assert rec.ignored_columns == ["mode"]


def test_the_record_says_what_the_file_does_not(ledger):
    d = datasets.import_dataset(ledger, sent(skip_rows=0), {FILE: DATA / FILE})
    (note,) = d.records[0].import_notes
    assert note["file"] == FILE and note["header_line"] == 6 and note["first_data_line"] == 7
    assert note["rows_read"] == 11 and note["lines_skipped"] == []
    assert note["repeated_times"] == {"count": 1, "lines": [16]}
    # the header names only the time unit; current in mA and temperatures in K were the importer's reading
    assert note["units_not_in_file"] == [{"column": c, "unit": u} for c, u in
                                         (("I", "mA"), ("Q", "Ah"), ("T1", "K"), ("T2", "K"), ("U", "V"))]
    assert note["changes"][0] == "decimal commas read as decimal points"
    assert ledger.get("dataset", d.id).records[0].import_notes == [note]


def test_a_units_line_is_skipped_kept_and_counts_as_stating_units(ledger, tmp_path):
    lines = (DATA / FILE).read_text(encoding="utf-8").splitlines()
    path = tmp_path / FILE
    path.write_text("\n".join([*lines[:6], "s;;;mA;V;Ah;K;K", *lines[6:]]) + "\n", encoding="utf-8")
    d = datasets.import_dataset(ledger, sent(skip_rows=1), {FILE: path})
    (note,) = d.records[0].import_notes
    assert note["lines_skipped"] == [{"line": 7, "text": "s;;;mA;V;Ah;K;K"}] and note["rows_read"] == 11
    assert note["units_not_in_file"] == [] and note["first_data_line"] == 8


def test_the_file_preview_shows_the_lines_skip_rows_passes_over():
    out = records.inspect(DATA / FILE, header_row=5, delimiter=";", decimal=",", skip_rows=5)
    assert [s["line"] for s in out["table"]["lines_skipped"]] == [7, 8, 9, 10, 11]
    assert out["table"]["lines_skipped"][0]["text"] == "0;1;R;0;3,1313;0;299,9461;300,1323"
    assert out["table"]["data_rows"] == 6


def test_a_dataset_stored_before_import_notes_is_the_same_dataset(ledger):
    """Re-importing the same files where the stored record predates import notes is not a conflict (the ledger is
    insert-only, so the old record is compared as the conflict check compares it)."""
    d = datasets.import_dataset(ledger, sent(skip_rows=0), {FILE: DATA / FILE})
    old = d.model_copy(update={"records": [d.records[0].model_copy(update={"import_notes": []})]})
    assert datasets._body(old) == datasets._body(d)
    other = d.model_copy(update={"title": "another dataset"})
    assert datasets._body(other) != datasets._body(d)


def test_rows_at_one_time_are_compared_in_file_order():
    """The evaluation's import check: identical imports differ by nothing, a dropped row is not compared."""
    t = [73630.32, 73649.64, 73649.64, 73649.71998]
    v = [2.8818, 2.8821, 2.8975, 2.8975]
    assert ri.paired_diffs(t, v, t, v) == [0.0] * 4
    assert ri.paired_diffs(t[1:], v[1:], t, v) == [0.0] * 3
    assert max(ri.paired_diffs(t, v, t, [2.8818, 2.8975, 2.8821, 2.8975])) == pytest.approx(0.0154)


def test_an_export_written_before_import_notes_may_lack_only_empty_ones():
    """Exports written before import notes existed have no `import_notes` key. Verify compares such a record without
    it only where the note recomputed from the receipt is empty; a note that was made cannot be left out."""
    from translucent.loop_export import without_later_empty_fields
    older = {"record": 1, "ref": "lab/cell02"}
    noted = {"record": 2, "ref": "lab/cell04", "import_notes": [{"rows_read": 6307}]}
    recomputed = {"investigation": {"instrument_facts": [dict(older, import_notes=[]), noted]}}
    stored = {"investigation": {"instrument_facts": [older, {k: v for k, v in noted.items() if k != "import_notes"}]}}
    assert without_later_empty_fields(recomputed, stored)["investigation"]["instrument_facts"] == [older, noted]
    assert without_later_empty_fields(recomputed, recomputed) == recomputed
