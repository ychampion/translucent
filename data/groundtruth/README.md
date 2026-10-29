# Lab records for the replayed experiments (Reisecker 2023, Lu 2023)

Machine-readable outputs: `data/groundtruth/reisecker2023.json`, `data/groundtruth/lu2023.json`, CSVs beside them.
Scripts: `scripts/groundtruth/`. Every number is tagged stated-in-paper, digitised, or derived in the JSON.

## Key facts
- Fig. 3e = single crystal (SC), Fig. 3f = hot-pressed (HP); each panel has DC and pulsed series (not "DC vs pulsed").
- 19 points extracted exactly from vector paths in the publisher PDF (±0.01 mA/cm², ±0.1 µm/s); overlay
  `data/groundtruth/reisecker2023_fig3ef_overlay.png`. SI critical-current-density tables cross-check HP DC x-values (Δ ≤ 0.002).
- Stated slopes: SC 8.4 (DC) / 10.5 (pulsed); HP 13.7 / 1.1 µm/s per mA/cm². Refitting the plotted points:
  SC 9.0 / 10.1 (≈10% agreement); HP 17.4 / 2.9 — the HP stated slopes do not reproduce from the plotted data.
- Growth-rate definition: electrode gap ÷ time to reach the opposing electrode. ~10 µm/s at 1 mA/cm².
  Conditions: 21 ± 1 °C, 1.8 kPa, 1:1 µs pulses, effective CCD = CCD/2.
- Movie ↔ data point (by image registration against paper figures; 26–444 matched features for true pairs, 0 otherwise):
  movie 4 = SC DC sample with the highest CCD 4.95 mA/cm², candidate point (4.83, 45.3 µm/s);
  movie 5 = HP pulsed 6.6 mA/cm² (effective 3.3), candidate point (3.22, 18.3 µm/s); movies 1–3 unmapped.
- Movies 1–4 are the Fig. 3a panels rotated 90°, each with a 1 mm scale bar → 184.9, 492.1, 177–187, 161.2 px/mm.
  For movie 4 the SI "0.1 cm" scale bars disagree by 1.6×.
- Only movie 4 has an on-screen clock: 6.9 s per captured image (~17.8× playback). Its voltage events imply
  90–180 or 17–19 µm/s depending on the start event — **not** the paper's 45 µm/s for that sample.
- Lu: Source Data → tidy CSV (84,433 rows). The stated 400 µm field of view conflicts with the 50 µm scale bar
  (≈326 µm per frame). Plating: paper says "after 10 h"; data shows voltage < 0 at 7.4 h, < −20 mV at 8.9 h,
  minimum at 11.4 h. Colour → SoC: grey < 0.05, blue 0.23, red 0.52, gold 0.95.

## Solid
Fig. 3 points; SI CCD tables; growth-rate definition; conditions; captions (verbatim in JSON); movie 4 time base;
Lu voltage data; Lu colour → SoC mapping.

## Ambiguous
Movie ↔ point mapping for movies 1–3 (4 and 5 are inferred, not stated); current density and waveform for
movies 1–3; which sample dimension is the electrode gap; conflicting scale bars (5% within Fig. 3, 1.6× vs SI);
image interval 2–7 s for movies 1–3 and 5 (±56% on time); the start event for "time to reach the opposing
electrode"; Lu frame timing (1,048 distinct frames at 1 min ≈ 17.5 h vs an inset spanning ≈ 25 h).

## Can compare
Movie 4 contact time vs its voltage collapse; movies 4 and 5 rates vs candidate points; movies 1–3 morphology
and rates against the SC distribution as a set; px calibration for movies 1–4; Lu colour-front order and timing.

## Cannot compare
Tip velocity over time (no ground truth); the stated HP slopes; absolute rates for movies 1–3 and 5 without an
assumed frame interval; Lu at 4 mA/cm² (not in the movie).

## Assumptions
Fig. 3 tick values read by eye from outlined glyphs (scripts check tick counts); movie identities from image
registration, not from the paper; movie 4 is DC only per Supp. Fig. 9; sample extents read by eye (±20 px);
the Lu optical-cell electrolyte is not stated.
