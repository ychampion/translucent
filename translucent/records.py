"""An investigation from an engineer's own records: the uploaded files, what Translucent computes from them before
anything is assumed, and the records a campaign imported.

A campaign whose objective lists `uploads` starts from raw files (instrument exports, logs) and the engineer's
documentation, not from a prepared dataset. The planner reads the files through `inspect`, writes the import v2
manifest itself (translucent.datasets refuses a wrong one with the file, line and column), and turns segments of the
imported records into record tests (pybamm-fastcharge-lab `record_test`). Every number here is computed from the
files: the column statistics, and the segments a record's current divides into. Choosing which segment is what, and
what state the cell was in, stays with the planner and is recorded as its assumption.

    uploads      <ledger root>/uploads/<campaign>/<name>: the files as the engineer gave them, never changed
    inspect      raw lines of a file, and per-column statistics under a header row the caller names
    segments     constant-current, rest and other stretches of a record's current, with their measured extremes
    imported     the datasets this campaign's planner imported (from its chained receipts), in order
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import statistics
from pathlib import Path

from translucent.experiments import _filename

UPLOADS = "uploads"
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_UPLOADS = 32
MAX_DOC_CHARS = 20_000           # documentation text the planner sees, verbatim
LINE_CHARS = 300                 # a raw line is cut here when shown
MAX_LINES = 60
DISTINCT_SHOWN = 8
REST_A = 0.02                    # |current| below this is a rest
CC_REL, CC_ABS = 0.02, 0.02      # a constant-current stretch stays within 2 % (or 20 mA) of its opening median
MIN_SEGMENT_S = 60.0
SEGMENT_MINUTES = 10.0           # segments at least this long are listed in the investigation; the rest on request


# ------------------------------------------------------------------ uploads

def upload_dir(root: Path | str, cid: str) -> Path:
    return Path(root) / UPLOADS / cid


def describe(files: dict[str, bytes]) -> list[dict]:
    """[{name, sha256, bytes}] in name order: what an objective records of its uploads. Refuses a folder in a name,
    an empty set, too many files or one too large."""
    if not files or len(files) > MAX_UPLOADS:
        raise ValueError(f"give 1 to {MAX_UPLOADS} files")
    out = []
    for name in sorted(files):
        _filename(name)
        if len(files[name]) > MAX_UPLOAD_BYTES:
            raise ValueError(f"{name}: larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB")
        out.append({"name": name, "sha256": "sha256:" + hashlib.sha256(files[name]).hexdigest(),
                    "bytes": len(files[name])})
    return out


def stage_uploads(root: Path | str, cid: str, files: dict[str, bytes], expected: list[dict] | None = None) -> list[dict]:
    """Store the engineer's files for a campaign under <root>/uploads/<cid>/ and return describe(files). With
    `expected` (the objective's uploads) the files must be exactly those. A name already staged with other bytes is
    refused; the same bytes again are kept."""
    out = describe(files)
    if expected is not None and out != expected:
        raise ValueError("the files are not the ones the objective recorded")
    folder = upload_dir(root, cid)
    folder.mkdir(parents=True, exist_ok=True)
    for entry in out:
        path = folder / entry["name"]
        if path.exists() and "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"{entry['name']}: another file of that name is already staged for this campaign")
        path.write_bytes(files[entry["name"]])
    return out


def upload_path(root: Path | str, cid: str, uploads: list[dict], name: str) -> Path:
    """A staged file of the campaign, checked against the digest the objective recorded."""
    entry = next((u for u in uploads if u["name"] == name), None)
    if entry is None:
        raise ValueError(f"no uploaded file {name!r}; the uploads are {', '.join(u['name'] for u in uploads)}")
    path = upload_dir(root, cid) / name
    if "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
        raise ValueError(f"{name} changed after it was uploaded")
    return path


# ------------------------------------------------------------------ inspect

def _text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):          # a UTF-16 export (some cycler software writes one)
        try:
            return raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            raise ValueError(f"{path.name}: not text (a UTF-16 mark, but not UTF-16)") from None
    if b"\x00" in raw[:65536]:          # latin-1 decodes any bytes; a NUL never occurs in a text export
        raise ValueError(f"{path.name}: not text (it holds NUL bytes: a binary file)")
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc), enc.removesuffix("-sig")
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path.name}: not text")


def _num(text: str, decimal: str) -> float | None:
    s = text.strip()
    if decimal == ",":
        s = s.replace(".", "").replace(",", ".") if s.count(",") == 1 else s
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def inspect(path: Path, *, start_line: int = 1, lines: int = 30, header_row: int | None = None,
            delimiter: str | None = None, decimal: str = ".", skip_rows: int = 0) -> dict:
    """Raw lines [start_line, start_line + lines) of a text file (1-based, each cut at LINE_CHARS), the total line
    count, the delimiter Translucent counts most often, and, when `header_row` (lines before the header, as in a v2
    manifest) is given, every column under that header with: values read, numeric share, min, max, first, last,
    whether it never decreases, and up to eight distinct values when there are few. Nothing is interpreted."""
    text, encoding = _text(path)
    rows = text.splitlines()
    start = max(1, int(start_line))
    n = max(1, min(int(lines), MAX_LINES))
    shown = [{"line": i, "text": rows[i - 1][:LINE_CHARS] + ("…" if len(rows[i - 1]) > LINE_CHARS else "")}
             for i in range(start, min(start + n, len(rows) + 1))]
    sample = rows[: min(len(rows), 400)]
    counts = {d: sum(r.count(d) for r in sample) for d in (",", ";", "\t", "|")}
    out = {"file": path.name, "bytes": path.stat().st_size, "encoding": encoding, "lines_total": len(rows),
           "lines": shown, "delimiter_counts": {("tab" if d == "\t" else d): c for d, c in counts.items()}}
    if header_row is None:
        return out
    delim = delimiter or max(counts, key=counts.get)
    if decimal not in (".", ","):
        raise ValueError('decimal is "." or ","')
    body = rows[int(header_row):]
    if not body:
        raise ValueError(f"{path.name}: nothing after line {header_row}")
    reader = csv.reader(io.StringIO("\n".join(body)), delimiter=delim)
    header = [h.strip() for h in next(reader)]
    skipped = []
    for _ in range(int(skip_rows)):
        row = next(reader, None)
        if row is not None:
            skipped.append({"line": int(header_row) + reader.line_num, "text": delim.join(row)[:LINE_CHARS]})
    cols: list[list[str]] = [[] for _ in header]
    data_rows = 0
    for row in reader:
        if not any(f.strip() for f in row):
            continue
        data_rows += 1
        for i in range(len(header)):
            cols[i].append(row[i].strip() if i < len(row) else "")
    columns = []
    for name, values in zip(header, cols):
        nums = [v for v in (_num(x, decimal) for x in values) if v is not None]
        col = {"column": name, "values": len(values), "numeric_share": round(len(nums) / len(values), 4)
               if values else 0.0}
        if nums:
            col |= {"min": min(nums), "max": max(nums), "first": nums[0], "last": nums[-1],
                    "never_decreases": all(b >= a for a, b in zip(nums, nums[1:]))}
        distinct = list(dict.fromkeys(values))
        if len(distinct) <= DISTINCT_SHOWN:
            col["distinct"] = distinct
        else:
            col["distinct_count"] = len(distinct)
        columns.append(col)
    out["table"] = {"header_line": int(header_row) + 1, "delimiter": "tab" if delim == "\t" else delim,
                    "decimal": decimal, "lines_skipped": skipped, "data_rows": data_rows, "columns": columns}
    return out


# ------------------------------------------------------------------ segments of a record

def _round(x: float | None, d: int = 4) -> float | None:
    return None if x is None else round(float(x), d)


def segments(t: list[float], current: list[float], *, voltage: list[float] | None = None,
             temperature: list[float] | None = None, chamber: list[float] | None = None,
             capacity_ah: float | None = None) -> list[dict]:
    """The stretches a record's current divides into, in time order, numbered from 1:
      rest              |I| < REST_A
      constant current  |I - I0| <= max(CC_REL |I0|, CC_ABS), I0 the median of the stretch's first five samples
      other             anything else (a constant-voltage taper, a pulse, a ramp)
    Stretches shorter than MIN_SEGMENT_S are kept and marked short. For each: its window on the record clock, the
    median current (as recorded: a magnitude-only export gives a positive number for charge and discharge alike),
    the largest relative departure from it, voltage at start and end and its trend, the surface temperature at
    start, its maximum and rise, and the chamber's median. Directions are not decided here."""
    n = len(t)
    if n == 0 or len(current) != n:
        raise ValueError("t and current must be the same, non-zero length")

    def kind_of(i: int, ref: float | None) -> str:
        if abs(current[i]) < REST_A:
            return "rest"
        if ref is not None and abs(current[i] - ref) <= max(CC_REL * abs(ref), CC_ABS):
            return "constant current"
        return "other"

    spans: list[list] = []          # [kind, start, end (inclusive), ref]
    i = 0
    while i < n:
        if abs(current[i]) < REST_A:
            j = i
            while j + 1 < n and abs(current[j + 1]) < REST_A:
                j += 1
            spans.append(["rest", i, j, None])
            i = j + 1
            continue
        head = [current[x] for x in range(i, min(n, i + 5)) if abs(current[x]) >= REST_A]
        ref = statistics.median(head)
        if all(abs(c - ref) <= max(CC_REL * abs(ref), CC_ABS) for c in head):
            j = i
            while j + 1 < n and kind_of(j + 1, ref) == "constant current":
                j += 1
            spans.append(["constant current", i, j, ref])
            i = j + 1
            continue
        j = i
        while j + 1 < n and kind_of(j + 1, None) != "rest":
            nxt = [current[x] for x in range(j + 1, min(n, j + 6)) if abs(current[x]) >= REST_A]
            if len(nxt) >= 5 and all(abs(c - statistics.median(nxt)) <= max(CC_REL * abs(statistics.median(nxt)),
                                                                             CC_ABS) for c in nxt):
                break
            j += 1
        if spans and spans[-1][0] == "other" and spans[-1][2] == i - 1:
            spans[-1][2] = j
        else:
            spans.append(["other", i, j, None])
        i = j + 1

    out = []
    for k, (kind, a, b, _) in enumerate(spans, start=1):
        sl = range(a, b + 1)
        cur = [current[x] for x in sl]
        med = statistics.median(cur)
        seg = {"segment": k, "kind": kind, "t_start_s": _round(t[a], 3), "t_end_s": _round(t[b], 3),
               "duration_min": _round((t[b] - t[a]) / 60.0, 3), "samples": len(cur),
               "current_A": _round(med, 5),
               "current_departure": _round(max(abs(c - med) for c in cur) / abs(med), 4) if abs(med) >= REST_A
               else None}
        if (t[b] - t[a]) < MIN_SEGMENT_S:
            seg["short"] = True
        if capacity_ah and abs(med) >= REST_A:
            seg["rate_per_nominal_capacity"] = _round(abs(med) / capacity_ah, 4)
        if voltage is not None:
            v0, v1 = voltage[a], voltage[b]
            seg |= {"voltage_start_V": _round(v0), "voltage_end_V": _round(v1),
                    "voltage_trend": "falling" if v1 < v0 - 0.005 else "rising" if v1 > v0 + 0.005 else "flat"}
        if temperature is not None:
            temps = [temperature[x] for x in sl]
            seg |= {"temperature_start_degC": _round(temps[0], 3), "temperature_max_degC": _round(max(temps), 3),
                    "temperature_end_degC": _round(temps[-1], 3), "temperature_rise_K": _round(max(temps) - temps[0], 3)}
        if chamber is not None:
            seg["chamber_median_degC"] = _round(statistics.median(chamber[x] for x in sl), 3)
        if k > 1:
            prev = out[-1]
            seg["after"] = {"segment": prev["segment"], "kind": prev["kind"],
                            **({"voltage_end_V": prev["voltage_end_V"]} if "voltage_end_V" in prev else {}),
                            **({"current_end_A": _round(current[spans[k - 2][2]], 5)})}
        out.append(seg)
    return out


def window(t: list[float], start_s: float, end_s: float) -> range:
    """Indices with start_s <= t <= end_s."""
    lo = next((i for i, x in enumerate(t) if x >= start_s), len(t))
    hi = max((i for i, x in enumerate(t) if x <= end_s), default=-1)
    return range(lo, hi + 1)


# ------------------------------------------------------------------ what a campaign imported

def imported(L, cid: str, recs=None) -> list[str]:
    """Dataset ids this campaign's planner (or a person through the campaign) imported, in order: from the chained
    planner receipts of import_records calls that returned a dataset."""
    from translucent import chain

    out: list[str] = []
    for r in recs if recs is not None else chain.records(L, cid):
        if r.type != "planner" or r.data.get("phase") != "tool_result":
            continue
        p = r.data.get("payload") or {}
        did = (p.get("result") or {}).get("dataset_id") if p.get("tool") == "import_records" else None
        if isinstance(did, str) and did not in out:
            out.append(did)
    return out


def campaign_records(L, obj, cid: str, recs=None) -> list[tuple[str, object]]:
    """[("dataset/record", StoredRecord)] in import order: the records a record test may name (record n is the
    n-th). Datasets the objective names come first, then those the campaign imported."""
    out = []
    for did in [*obj.datasets, *(d for d in imported(L, cid, recs) if d not in obj.datasets)]:
        ds = L.get("dataset", did)
        if ds is None:
            continue
        out += [(f"{did}/{r.id}", r) for r in ds.records]
    return out


# ------------------------------------------------------------------ the investigation, as the planner and the view see it

def _segment_row(s: dict) -> str:
    """One computed segment as a short line, for the context."""
    bits = [f"{s['segment']}", s["kind"], f"{s['t_start_s']:g}-{s['t_end_s']:g} s", f"{s['duration_min']:g} min"]
    if s["kind"] != "rest":
        bits.append(f"I {s['current_A']:g} A" + (f" ({s['rate_per_nominal_capacity']:g}/h of nominal)"
                                                  if "rate_per_nominal_capacity" in s else ""))
    if "voltage_start_V" in s:
        bits.append(f"V {s['voltage_start_V']:g}->{s['voltage_end_V']:g} {s['voltage_trend']}")
    if "temperature_start_degC" in s:
        bits.append(f"T {s['temperature_start_degC']:g}->{s['temperature_max_degC']:g} max "
                    f"(+{s['temperature_rise_K']:g} K)")
    if "chamber_median_degC" in s:
        bits.append(f"chamber {s['chamber_median_degC']:g}")
    if s.get("after"):
        a = s["after"]
        bits.append(f"after {a['segment']} {a['kind']}" + (f" ending {a['voltage_end_V']:g} V"
                                                          if "voltage_end_V" in a else ""))
    return " | ".join(bits)


def _missing(recs) -> list[dict]:
    out = []
    for r in recs:
        if r.type != "planner" or r.data.get("phase") != "tool_call":
            continue
        p = r.data.get("payload") or {}
        if p.get("tool") == "import_records":
            for m in (p.get("arguments") or {}).get("missing_information") or []:
                if isinstance(m, dict) and m not in out:
                    out.append(m)
    return out


def investigation(L, cid: str, obj, recs=None, states=None) -> dict:
    """The records part of the context: the uploads and documentation, the instrument facts of each imported record
    (its manifest as stored), its computed segments, the tests in their shared numbering, the missing information
    the planner declared, and the model assumptions so far, each kept apart."""
    import json

    from translucent import chain
    from translucent.loop import request_states
    from translucent.systems import lgm50_lab as lab

    recs = recs if recs is not None else chain.records(L, cid)
    states = states if states is not None else request_states(L, cid, recs)
    found = []
    segments: dict[str, list[str]] = {}
    for n, (ref, rec) in enumerate(campaign_records(L, obj, cid, recs), start=1):
        found.append({"record": n, "ref": ref, "specimen": rec.specimen.model_dump(mode="json"),
                      "configuration": dict(rec.configuration), "configuration_units": dict(rec.configuration_units),
                      "conditions": dict(rec.conditions), "conditions_units": dict(rec.conditions_units),
                      "time": rec.time.model_dump(mode="json") if rec.time else None,
                      "channels": [{"quantity": c.quantity, "column": c.column, "unit": c.unit,
                                    "uncertainty": c.uncertainty.model_dump(mode="json"), "n": c.n,
                                    "t_range_s": list(c.t_range)} for c in rec.channels],
                      "ignored_columns": list(rec.ignored_columns), "import_notes": list(rec.import_notes)})
        try:
            segs = lab.record_segments(L, rec)
        except ValueError as e:
            segments[str(n)] = [f"no segments: {e}"]
            continue
        shown = [_segment_row(s) for s in segs if s["duration_min"] >= SEGMENT_MINUTES]
        segments[str(n)] = shown + [f"{len(segs) - len(shown)} shorter segments: list_segments({n})"]
    tests, assumptions = [], []
    for k, st in enumerate(lab.lab_tests(states), start=1):
        p = dict(st.data.params)
        if st.action == lab.RECORD_ACTION:
            win = json.loads(p["window"])
            tests.append({"test": k, "kind": "record", "request_id": st.request_id, "record": int(p["record"]),
                          "record_ref": p["record_ref"], "segment": win["segment"],
                          "mode": "discharge" if p["mode"] == 1 else "charge", "start_soc": p["start_soc"]})
            assumptions.append(f"test {k}: record {int(p['record'])} segment {win['segment']} is a "
                               f"{'discharge' if p['mode'] == 1 else 'charge'} from SOC {p['start_soc']:g} "
                               f"(stated by {st.proposed_by})")
        else:
            tests.append({"test": k, "kind": "lab" if st.action == "lab_charge" else "bench",
                          "request_id": st.request_id})
    prior = lab.nominal_model(obj)
    return {"uploads": list(obj.uploads or []),
            "documentation": (obj.documentation or "")[:MAX_DOC_CHARS],
            "instrument_facts": found, "segments": segments, "tests": tests,
            "missing_information": _missing(recs),
            "model_assumptions": {"prior": {"version": 0, **prior["estimate"], "source": prior["source"]},
                                  "families": {str(k): v for k, v in lab.FAMILIES.items()},
                                  "record_test_states": assumptions},
            "engineer_constraints": "objective.constraints (only an engineer's amendment changes them)"}
