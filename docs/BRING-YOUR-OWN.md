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
