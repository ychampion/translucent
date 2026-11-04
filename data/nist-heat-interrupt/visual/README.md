# Visual corroboration and timing evidence

These are factual readings and recorded model outputs from the published sources,
copied unchanged on 2026-10-30. No new model
calls were made to produce this copy. `MANIFEST.json` gives the SHA-256 and byte
count of every supplied CSV and JSON file. Publisher images, video frames, video
files, and the paper PDF are excluded.

The source paper is Tam et al., *Applied Thermal Engineering* 298 (2026), 131107,
DOI [10.1016/j.applthermaleng.2026.131107](https://doi.org/10.1016/j.applthermaleng.2026.131107),
Tables A1/A2 and supplementary videos S3/S4. The thermocouple dataset is NIST
DOI [10.18434/mds2-4027](https://doi.org/10.18434/mds2-4027), under the NIST open
licence. These derived factual readings and recorded model text do not grant a
licence to redistribute the publisher's images or videos.

- `out/task1_labels.csv` is the Muse Spark image reading of table colours, SOC,
  and heater-off delays, joined to the vector-layer reading and the independent
  binary trace ignition check. `task1_summary.json` records request digests,
  usage, agreement counts, and shape inconsistencies. `task1_vs_inventory.json`
  compares the readings with the supplied inventory. `vector_labels.csv` keeps
  the vector-layer reading separately.
- `out/task2_timing.csv` and `task2_timing.json` summarize the video timing read
  by Muse Spark and the flame-onset segmentation produced by SAM 3.1. The latter
  also contains segment timing, uncertainty, and marker details. `S3_ocr.csv`,
  `S4_ocr.csv`, and `chunks/` hold the intermediate text transcriptions;
  `S3_pixels.csv`, `S4_pixels.csv`, `S3_sam.csv`, and `S4_sam.csv` hold the
  extracted marker and segmentation measurements. `job_start_balance.json` is
  the supplied call-budget bookkeeping.
- `recordings/` contains the supplied 143 model-call JSON recordings, including
  the one preserved under `superseded/`. They retain request/media hashes,
  prompts, returned model text (including SAM's generated mask encoding), usage,
  and timestamps. They contain no input image bytes or credentials. The table
  and clock text calls used Muse Spark; the segmentation calls used SAM 3.1.

The image and vector categories agree in all 77 trials. The trace check is
binary: it can corroborate ignition versus no observed ignition, and cannot
distinguish the paper's red and black ignition categories. Exp 61's red label
coexists with cooling before ignition; this shape discrepancy remains visible
in the supplied CSV. Missing traces for Exp 3 and 17 count as unavailable, never
as three-way corroboration, even though the supplied CSV's aggregate agreement
column marks them true. Rescued means no ignition in the finite, render-safe
truncated observation; Exp 67 and 73 remain censored.

The videos are edited montages. Supply-lamp power-off puts the thermocouple
lag near 6–7 s; the editorial "Power Off" marker puts it near 9–10 s. The
benchmark pre-registers 10 s and reports a 7 s sensitivity. These readings do not
establish a measured vent detector or an internal temperature.

During ingest, row corroboration and these source hashes remain in the private
sealed payload. They enter the ledger with the outcome reveal only after a
decision is committed. The planner's pre-off tools expose neither the label
reading nor its corroboration status. Repository files remain operator inputs;
this is a tool-access boundary, not cryptographic secrecy against the operator.
Once every ingested trial has been revealed, the campaign's final audit imports
the CSV/JSON readings and call recordings as content-addressed artifacts for the
self-contained replay receipt. This closes ingest for that campaign; another
sealed evaluation requires a new campaign.
