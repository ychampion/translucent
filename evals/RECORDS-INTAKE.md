# Records intake in a simulated lab

The frozen evaluation has 15 scored runs across H1, H2 and H3. Every lab outcome is simulated. No physical battery was tested, and no engineer-time saving was measured. Both model arms used Muse Wonder with the same underlying table parser, numerical solver and simulated lab, through separate tool interfaces and prompts. Both had a USD 2.50 model cap and two simulated lab tests; their call limits, context management, import checks and enforcement of the recommendation standing rule differed. This compares two implemented workflows and does not isolate the effect of any one component. The numerical arm received correctly prepared inputs.

The primary result is the design the run recommended, assessed under the hidden simulated condition, together with whether its model met the standing rule. A run that recommended nothing is reported as declined. An unsupported recommendation is a recommendation under a model that did not meet that rule. The last design found is a secondary result and does not count as a recommendation.

## Results

| Arm | Scored runs | Recommended | Feasible recommendations | Unsupported recommendations | Model spend, total USD | Campaign wall time, total s |
|---|---:|---:|---:|---:|---:|---:|
| Translucent investigation | 6 | 5 | 5/5 | 0 | 11.882675 | 5698.4 |
| General Meta agent, same underlying tools | 6 | 6 | 6/6 | 1 | 4.6316704 | 9439.6 |
| Numerical controller, prepared inputs | 3 | 0 | 0/0 | 0 | 0 | 985.2 |

The general agent used about 39% of Translucent's model spend. Translucent did not complete more designs. With six runs per model arm, the single unsupported recommendation is descriptive evidence, without a claim of statistical superiority.

| Case | Translucent repeat 1 t80, min | Translucent repeat 2 t80, min | General agent repeat 1 t80, min | General agent repeat 2 t80, min | Reference controller with the hidden condition t80, min |
|---|---:|---:|---:|---:|---:|
| H1 | 35.875 | 35.875 | 35.875 | 40.63 | 35.875 |
| H2 | 36.991 | 36.991 | 36.991 | 36.991 | 36.75 |
| H3 | 40.199 | declined | 40.199 | 40.199 | 40.199 |

All listed recommendations are feasible in the hidden simulated lab. H1 general-agent repeat 2 recommended 1.5/1.5/1.15/0.6C although its model did not fit its own tests: fit RMSE 1.1221 K against the 0.3 K rule. Feasibility in the hidden lab does not make that recommendation supported by its evidence. In that run the agent stated that no model stood behind any design, and the tool interface had prevented it from fitting or checking its follow-up tests. H3 Translucent repeat 2 declined before a design: after USD 2.152711 of recorded model cost, its next model-call reservation would have exceeded the cap. Its frozen import assessment records five missing rows from each of its two files. Those rows were dropped by an import manifest error that the importer now refuses; every retained value matched the reference. The frozen assessment is unchanged. The numerical controller declined in all three cases because its fitted model did not meet the adequacy rule; its last H3 design, which it did not recommend, was infeasible in the hidden lab.

## Cases and method

H1 supplies the published cell02 and cell03 exports and withholds cell04. H2 supplies cell03 and cell04 and withholds cell02. H3 supplies a synthetic re-export of cell02 and cell04 and withholds cell03. Its semicolon delimiter, decimal comma, unit-less headers and ambiguous temperature-channel order test import interpretation. The values come from the published records, converted and reformatted.

Each hidden lab is a family-2 simulation parameterised by a fit to the withheld cell's C/2, 1C and 1.5C discharges from full. This stays inside the available model family. The source-record fits themselves did not meet the adequacy rule (fit RMSE 0.980, 1.066 and 1.302 K), so the hidden labs define test scenarios for the workflow; they are not validated models of the withheld cells. The anode potential is always forecast; no lab result measures it. Each model run has a USD 2.50 model cap and two simulated lab tests. The objective is the fastest 10-80% charge at 25 degrees C with cell temperature at or below 45 degrees C and anode potential at or above +25 mV.

The case definitions, reference imports, arm policies, freeze check and scorer are in [records_intake.py](records_intake.py). Its assessment calls [fastcharge_lab.py](fastcharge_lab.py). The raw Chen 2020 LG M50 records are CC BY 4.0 and are fetched by `scripts/fetch_lgm50_data.py`; they are not committed.

## Frozen plan and provenance

[records-intake-plan.json](records-intake-plan.json) copies the original registered 15-run plan and its primary and secondary metrics, with the original freeze identifier and package versions. [records-intake-summary.json](results/records-intake-summary.json) copies selected per-run numbers from the frozen outputs and records the source file digests. D0 development runs and voided connection failures are excluded from these scored rows.

The original implementation freeze is `5c1f56a3f95bbb6dc5a27e21b82860bc6c81f4a3920e3ea15f86d58d850379f2`. The code in this repository has later fixes to the importer, scorer and product, so it is not the original frozen implementation. These historical scores have not been rescored or changed.

Source file digests, SHA256:

| Source basename | Digest |
|---|---|
| REPORT.md | a15735f13d379e00643c2df003a1d6a62518e87590409674d6256714a4bbdd3f |
| freeze.json | 5b846cb6bbe49178722bc77c3b093f7f1f7a139a675df1341cfad380554d9271 |
| oracle.json | 3d1bb3809d15de61697734de30eee43a8ac0aee2b42aa8528b3f465c087af87f |

## Reproduction scope

The case definitions and evaluation machinery are present. The original frozen source tree and complete scored-run archive are not distributed here. The source digests identify external originals; they cannot reconstruct missing files. Exact historical replay and independent regeneration of every historical score are therefore not available from this tree alone. The saved summary supports inspection of the reported outcomes, without claiming that missing evidence is included.

A new evaluation under current code needs a new output directory and a new freeze. Fetch the records and install the pinned solver first:

```sh
uv sync --group dev --group solver
uv run python scripts/fetch_lgm50_data.py
uv run python -m evals.records_intake prepare --out runs/records-intake-new --cases H1 H2 H3
uv run python -m evals.records_intake freeze --out runs/records-intake-new
uv run python -m evals.records_intake run --out runs/records-intake-new --cases H1 H2 H3 --arms numeric --repeats 1
uv run python -m evals.records_intake oracle --out runs/records-intake-new
uv run python -m evals.records_intake report --out runs/records-intake-new --md runs/records-intake-new/report.md
```

These commands make no model calls and cover only the numerical arm. They do not reproduce the two historical Muse arms. A new model-arm evaluation requires paid calls, explicit live enablement and a separately chosen total spend cap. Model replies are not deterministic. Its results must be reported separately from this historical evaluation.

The learned-model fast-charge evaluation is a separate result: see [FASTCHARGE-LAB-PROTOCOL.md](FASTCHARGE-LAB-PROTOCOL.md) and [FASTCHARGE-LAB.md](FASTCHARGE-LAB.md). Adaptive Muse matched the adaptive numerical controller under its frozen criteria; it did not outperform it. That report is copied from the original generated report, whose SHA256 is `55a46435b1760da6f2f863bf12643afa6be9e4f36c47d5e8ccf5b7136940d8fd`. Its 63-run results source has SHA256 `bc4d28c6cc45428c463ff8f8374cb53ba1bea7c9d7364c6309df0d68457521f0`. The complete M3 scored-run archive is also external, so the same historical-replay limitation applies.
