# Bring your own experiment

Provide a video, a short title, a citation (a lab notebook reference is fine),
its licence or usage terms, and the name of the person declaring its records.
Choose an ID of 3–64 lowercase letters, digits, underscores or hyphens, starting
with a letter or digit. Importing and listing experiments run locally without
paid model calls. A manifest can also include optional `notes`.

A video’s frame rate describes playback. Export software may speed it up or
repeat images, so **playback frame rate is never an acquisition clock**. For
timing, provide a CSV with `frame,time_s`: exactly one row per source video frame,
numbered from zero, in order. Times must be finite and nondecreasing, and are
normalized to the first frame. Equal times require identical decoded images.
If compression changes repeated copies, use an export preserving identical
images or lab timestamps that resolve each source frame; never invent times.
Alternatively, declare the acquisition interval and its source. This assumes
identical consecutive images are playback repeats; stationary acquisitions need
timestamps. Ambiguous repeats are refused. If timing cannot be established,
omit the clock; findings needing a recorded clock will be refused.

For distances, declare micrometres per pixel from microscope metadata, or the
length of a scale bar in both pixels and micrometres. Name the source; optional
`uncertainty` is an absolute uncertainty in micrometres per pixel for either form.
Omitted uncertainty is zero declared uncertainty, not an independent accuracy
check. Scale and clock calibrations retain “declared by <author> from <source>”.
These are declarations, not automatic validation of your microscope or notebook.
Scale uncertainty follows length and growth measurements; very broad scale
bounds can prevent these measurements.

An optional voltage CSV has `time_s,voltage_V` columns with finite values and
strictly increasing times. `offset_s` is the voltage-log time corresponding to
the video’s first frame: Translucent subtracts it to align the log. Name the
record supporting this alignment. A logged voltage minimum alone does not
establish a short circuit or lithium plating.

For example, save this as `manifest.json` beside the named files:

```json
{
  "id": "cell-042",
  "title": "Cell 042, operando growth",
  "citation": "Lab notebook 2026-10-28, experiment 042",
  "licence": "Internal lab use",
  "author": "Ana Patel",
  "video": "cell-042.mp4",
  "scale": {
    "bar_px": 100,
    "bar_um": 50,
    "uncertainty": 0.005,
    "source": "Microscope calibration slide, notebook page 42"
  },
  "clock": {
    "timestamps_csv": "frames.csv",
    "source": "Acquisition computer timestamp export"
  },
  "voltage": {
    "csv": "voltage.csv",
    "offset_s": 12,
    "source": "Potentiostat export; video start marked at log time 12 s"
  }
}
```

Import the same records from the command line:

```sh
uv run translucent add-experiment --id cell-042 --video cell-042.mp4 \
  --title "Cell 042, operando growth" \
  --citation "Lab notebook 2026-10-28, experiment 042" \
  --licence "Internal lab use" --author "Ana Patel" \
  --scale-bar-px 100 --scale-bar-um 50 --scale-uncertainty 0.005 \
  --scale-source "Microscope calibration slide, notebook page 42" \
  --timestamps frames.csv --clock-source "Acquisition computer timestamp export" \
  --voltage voltage.csv --voltage-offset-s 12 \
  --voltage-source "Potentiostat export; video start marked at log time 12 s" \
  --ledger runs/my-lab
uv run translucent experiments --ledger runs/my-lab
```

For a declared interval, replace `--timestamps frames.csv` with
`--interval-s 2 --interval-uncertainty-s 0.01` and give its source with
`--clock-source`. In a manifest, use `"interval_s": 2, "uncertainty_s": 0.01`
instead of `timestamps_csv`. Interval uncertainty accumulates per captured image.
The current growth tool conservatively refuses any nonzero time bound; voltage
timing can still be supported with that uncertainty. To import the manifest from Python:

```python
from translucent.experiments import register
from translucent.ledger import Ledger

ledger = Ledger("runs/my-lab")
try:
    video = register(ledger, "manifest.json")
finally:
    ledger.db.close()
```

Copies and hashes live under `data/experiments/cell-042/`; the ledger records the
video as `user/cell-042`. Repeating an identical import returns the existing
video. Records are immutable: choose a new ID for corrections. If the video is
unchanged, also use a separate ledger: each ledger associates identical video
bytes with one experiment. Back up both the experiment folder and ledger.

## Records without a video: manifest v2

A v2 manifest imports a team's own records: time series from a cycler, potentiostat
or logger, tables, single values, and optional images and videos. No video is
needed. One manifest is one *dataset*; it holds up to 500 *records* (one test of
one specimen each). This example imports a BioLogic EC-Lab export and a photo:

```json
{
  "manifest_version": 2,
  "id": "fastcharge-screen-2026q3",
  "title": "Fast-charge screening, cell 07",
  "author": "Ana Ruiz",
  "citation": "Lab notebook 14, pp. 3-9",
  "licence": "internal",
  "records": [{
    "id": "cell07-cc",
    "role": "baseline",
    "specimen": {"id": "cell-0007", "batch": "LGM50-2607", "properties": {"capacity_Ah": 5.0}},
    "configuration": {"c_rate": 0.2, "protocol": "CC"},
    "configuration_units": {"c_rate": "1/h"},
    "conditions": {"ambient": 25}, "conditions_units": {"ambient": "degC"},
    "time": {"unit": "s", "origin": "start of charge", "offset_s": 10.0,
             "source": "cycler step log", "uncertainty_s": 1.0},
    "tables": [{
      "file": "cell07.mpt", "format": "tsv", "header_row": 5, "time_column": "time/s",
      "columns": {
        "Ecell/V": {"quantity": "voltage", "unit": "V",
                    "uncertainty": {"value": 0.001, "kind": "resolution", "method": "cycler spec"}},
        "I/mA": {"quantity": "current", "unit": "mA"},
        "Temperature/°C": {"quantity": "temperature", "unit": "degC"}
      }}],
    "scalars": [{"quantity": "charge_time_80", "value": 1260, "unit": "s", "source": "cycler summary"}],
    "media": [{"file": "teardown.png", "kind": "image", "needs_vision": true, "label": "Anode after test"}],
    "provenance": {"source": "BioLogic EC-Lab export", "instrument": "VMP3", "operator": "Ana",
                   "acquired_at": "2026-07-14"}
  }]
}
```

Import it from Python. The files sit beside the manifest unless you name a folder
or give a mapping of filename to path:

```python
from translucent.datasets import import_dataset
from translucent.ledger import Ledger

ledger = Ledger("runs/my-lab")
try:
    dataset = import_dataset(ledger, "manifest.json")   # or (ledger, manifest_dict, "folder/")
finally:
    ledger.db.close()
```

**Tables.** `format` is `csv`, `tsv` or `json`. `header_row` is the number of
preamble lines before the column names (at most 200). EC-Lab's
`Nb header lines : N` counts the names line too, so use N - 1; a Maccor export
with 13 lines before `Rec,Cycle P,...` uses 13. `skip_rows` skips unit rows after
the names. `delimiter` overrides the comma or tab. `decimal` is `.` or `,`; a
comma decimal needs another delimiter, such as `;` or a tab. Text may be UTF-8 or
Windows-1252 (what EC-Lab and Maccor write, e.g. `°C`). JSON tables are columns
(`{"t": [...], "V": [...]}`) or rows (`[{"t": 0, "V": 3.6}, ...]`), numbers only.
List only the columns you want; the others are ignored and named in the record.
Every listed value must be a finite number, and time must never go back. A time
may repeat (a cycler can log a step change twice at one instant): both rows are
kept, and the count is recorded.

**Time.** `time.unit` is the time column's unit (`s`, `ms`, `min`, `h`). Record
time is the column time in seconds minus `offset_s`, so t = 0 is the `origin` you
describe. All tables of a record share it. An image's `t_s` is its time on the
record clock; a video's `t_s` is the record time of its first frame.

**Units** come from the registry in `translucent/units.py` (`V`, `mV`, `A`, `mA`,
`Ah`, `degC`, `K`, `1/h`, `mV/s`, ...). Every number in `configuration` and
`conditions` needs its unit (`1` for a plain number). Values keep the unit you
declare; any conversion happens when a measurement is computed, and is recorded
with it. When `system` names a registered system, each column must be one of its
quantities in a unit of the same dimension, or a `lab:<name>` quantity for
anything else. Each quantity appears once per record.

**Specimen and configuration.** `specimen.id` names the physical specimen (cell,
coupon, sample); records of one specimen share it. Each record stores a hash of
the system and its configuration with units, so equal configurations can be found.

**Media.** Images: PNG, JPEG, or TIFF (a multi-page TIFF is kept as a stack).
Videos: MP4, MOV, AVI, or a multi-page TIFF time-lapse. Each file is checked by
its bytes and decoded before import; GIF, BMP, ND2, CZI and playlists are refused.
A video's `clock` follows the v1 rules above (`timestamps_csv` with one row per
frame, or `interval_s`); list the timestamps file in the clock only.

**Provenance.** Give the dataset's citation (with a DOI where there is one) and
licence; each record names its source, instrument, operator and date. Every file
is stored by its SHA-256, which the record lists, so the bytes can be checked
against a published checksum. Secret-looking text in free-text fields is removed.

**Repeats and corrections.** Importing the same manifest and files again returns
the stored dataset. A different dataset under an id this ledger already holds is
refused: use a new id. Another ledger may reuse the id.

**Limits.** 64 files per import, 500 MB per file, 2,000,000 rows per table, 500
records per dataset. A refusal names the file, line or row, and column, e.g.
`cell07.mpt, line 11, column 'time/s': time goes back from 15 to 14; times must not decrease`.

A v1 manifest (the video format above) also imports this way. It becomes one
record with the video as media, the voltage CSV as a `voltage` channel aligned by
its offset, and the scale as a value.
