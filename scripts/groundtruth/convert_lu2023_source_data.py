"""Convert Lu et al. 2023 (Nat. Commun. 14, 5127) Source Data, sheet "Fig. 2", into tidy CSV and derive
voltage events (plating onset = first V < 0 during charge; voltage-dip minimum) from it.

The sheet holds the optical-cell voltage profile shown in Fig. 2 (and as the inset of Supplementary Movie 1)
as 8 (time, voltage) column pairs, one per protocol segment. Time is continuous across segments, in seconds
(2 s sampling); voltage is V vs Li/Li+ (graphite working electrode vs Li foil).

Usage:
  uv run --with openpyxl python -I convert_lu2023_source_data.py <MOESM5_ESM.xlsx> <out_dir>
"""
import csv
import json
import sys
from pathlib import Path

import openpyxl

# (time column, voltage column) -> segment metadata. Header text (row 3) is checked against `header`.
SEGMENTS = [
    (0, 1, "2 mA cm-2 charge", 2.0, "charge", 1),
    (3, 4, "2 mA cm-2 relax", 0.0, "relax_after_charge", 1),
    (6, 7, "2 mA cm-2_discharge", -2.0, "discharge", 1),
    (9, 10, "2 mA cm-2_discharge_relax", 0.0, "relax_after_discharge", 1),
    (12, 13, "4 mA cm-2 charge", 4.0, "charge", 2),
    (15, 16, "4 mA cm-2 charge_relax", 0.0, "relax_after_charge", 2),
    (18, 19, "4 mA cm-2 discharge", -4.0, "discharge", 2),
    (21, 22, "Relax", 0.0, "relax_after_discharge", 2),
]


def main(xlsx, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ws = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)["Fig. 2"]
    rows = list(ws.iter_rows(values_only=True))
    hdr = rows[2]
    tidy, events = [], {}
    for tc, vc, header, j, mode, cycle in SEGMENTS:
        # Header sits in the voltage column for segment 1 and in the time column otherwise.
        assert header in (hdr[tc], hdr[vc]), (header, hdr[tc], hdr[vc])
        pts = [(r[tc], r[vc]) for r in rows[5:]
               if tc < len(r) and isinstance(r[tc], (int, float)) and isinstance(r[vc], (int, float))]
        t0 = pts[0][0]
        for k, (t, v) in enumerate(pts):
            tidy.append({"cycle": cycle, "segment": header, "mode": mode,
                         "current_density_mA_cm2_signed_charge_positive": j,
                         "sample_index": k, "t_s": t, "t_h": round(t / 3600, 6),
                         "t_in_segment_s": round(t - t0, 3), "voltage_V_vs_Li": v})
        seg = {"t_start_s": pts[0][0], "t_end_s": pts[-1][0],
               "duration_h": round((pts[-1][0] - pts[0][0]) / 3600, 4), "n_samples": len(pts),
               "v_start_V": pts[0][1], "v_end_V": pts[-1][1],
               "v_min_V": min(p[1] for p in pts), "v_max_V": max(p[1] for p in pts)}
        if mode == "charge":
            neg = next((p for p in pts if p[1] < 0), None)
            vmin = min(pts, key=lambda p: p[1])
            seg["first_negative_voltage"] = None if neg is None else {
                "t_s": neg[0], "t_in_charge_h": round((neg[0] - t0) / 3600, 4), "voltage_V": neg[1]}
            seg["voltage_minimum"] = {"t_s": vmin[0], "t_in_charge_h": round((vmin[0] - t0) / 3600, 4),
                                      "voltage_V": vmin[1]}
        events[f"cycle{cycle}_{mode}"] = seg
    with open(out / "lu2023_fig2_voltage.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(tidy[0].keys()))
        w.writeheader(); w.writerows(tidy)
    (out / "lu2023_fig2_segments_derived.json").write_text(json.dumps(events, indent=1))
    print(f"{len(tidy)} rows"); print(json.dumps(events, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
