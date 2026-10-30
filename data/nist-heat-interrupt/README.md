# NIST heating interruption data

Source: Wai Cheong Tam and colleagues, *Determination of Safe Intervention Windows
for 18650 and 21700 Lithium-Ion Batteries from External Heating: Thermocouple Data*,
version 1.0.0, DOI [10.18434/mds2-4027](https://doi.org/10.18434/mds2-4027),
[NIST record](https://data.nist.gov/od/id/mds2-4027).

The workbook, descriptions, disclaimer and record JSON are unchanged NIST files.
Their checksums are in the record JSON. NIST's [open licence](https://www.nist.gov/open/license)
places US government employee work in the US public domain and permits worldwide
reuse with acknowledgement; read `Data Disclaimer.txt` for the full terms.

`trials.csv` transcribes format, SOC and interruption delay from Tables A1/A2.
`outcomes.csv` transcribes the table colours in Tam et al., *Applied Thermal
Engineering* 298 (2026), 131107, DOI
[10.1016/j.applthermaleng.2026.131107](https://doi.org/10.1016/j.applthermaleng.2026.131107).
Blue means rescued, black delayed ignition, red ignition. Continuous trials ignited.
These derived CSVs were prepared 2026-10-30 from the supplied validation inventory;
the paper PDF is not redistributed. Exp44 uses its delayed-ignition table colour
despite the conflicting Table 3 text. Exp61 retains its red label despite cooling.

There are 77 trials, 75 traces and 50 usable interrupted trials. Exp3 and Exp17
have no trace. No vent or heater timestamps were published. Library code reconstructs
them, including the 10 s sensor lag; provenance accompanies each derived time.
Rescued traces are truncated at render-safe reheating. Exp67/Exp73 are censored,
Exp33 has suspect SOC, and five heater-off times are inferred. The benchmark
predicts recorded ignition after recorded heater-off, not an alternative stop time
or the effect of suppression. Public source labels are available to the operator;
the planner tool boundary, seals and offline receipts are an audit of the recorded
evaluation, not cryptographic proof that nobody previously knew this public dataset.
