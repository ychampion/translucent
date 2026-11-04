"""Import v2: a team's own records (time series, tables, scalars, images and videos) as one ledger `dataset`.

A manifest (loop_contracts.ManifestV2) lists records. Each names its specimen, configuration, conditions, a time
base, and any number of tables (CSV, TSV or JSON columns; instrument exports with a preamble), scalars and media
files. `import_dataset`:
  1. scrubs secret-looking free text and validates the manifest; a v1 experiment manifest is upgraded first;
  2. copies every file into a staging folder and refuses one that changes while it is copied;
  3. parses every table: declared delimiter, preamble, unit rows and decimal mark; every value a finite number;
     time never going back; units known to translucent.units, and of the system's dimension when one is named.
     Each channel becomes a series {t (s, on the record clock), y (declared unit)}: the source unit is kept, and
     the conversion to a system's unit happens at operator time (translucent.operators);
  4. checks images (PNG, JPEG, TIFF including multi-page stacks) and videos (MP4, MOV, AVI, TIFF stacks) by their
     bytes and decodes them, keeping the v1 clock rules for a video with a clock;
  5. computes every artifact id before writing, so a conflicting dataset is refused before anything is stored;
  6. stores the files and series (put_file copies bytes, so outside any transaction), then adds the DatasetRecord
     inside one Ledger.transaction(), checking the id again there. Imports into one ledger store one at a time
     (an OS file lock under <ledger>/locks/): put_file stages every writer's copy of a blob at the same path.
The same manifest and files give the same record. A different dataset under an id this ledger already holds is
refused with DatasetConflict (HTTP 409); another ledger can use the id. Refusals are plain ValueErrors naming the
file, line or row, and column. Nothing here needs a video: a dataset without media is complete.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from translucent import experiments, operators, units
from translucent.chain import digest
from translucent.contracts import Source, Transform, Uncertainty
from translucent.ledger import AdmissionError, Ledger, sha256_file
from translucent.locks import ExclusiveFileLock
from translucent.loop_contracts import (ChannelSpec, DatasetRecord, ManifestV2, MediaSpec, OperatorSpec, Provenance,
                                        RecordSpec, ScalarSpec, Specimen, StoredChannel, StoredRecord, TableSpec,
                                        TimeBase)
from translucent.scrub import scrub

PARSE_OP = "dataset_parse/1"
MAX_FILES = 64
MAX_FILE_BYTES = 500 * 2**20          # the upload limit (server.UPLOAD_LIMIT)
MAX_POINTS = 2_000_000                # rows per table, so points per channel
MAX_IMPORT_POINTS = 16 * MAX_POINTS   # rows parsed, and series points held until stored, by one import
MAX_JSON_TABLE_BYTES = 64 * 2**20     # parsed JSON takes many times its size; larger tables go as CSV
MIN_ROWS = 2
SKIPPED_LINE_CHARS = 200              # a line skip_rows passed over, as kept in the record
LISTED_LINES = 20                     # lines named in an import note; the count is always whole
LOCK_WAIT_S = 120.0                   # how long an import waits for another import into the ledger

# free-text fields scrubbed of secret-looking strings; ids, filenames, column names and units are left alone
TEXT_KEYS = frozenset({"title", "author", "citation", "licence", "notes", "origin", "source", "label", "method",
                       "instrument", "operator", "acquired_at"})
TABLE_TYPES = {"csv": "text/csv", "tsv": "text/tab-separated-values", "json": "application/json"}
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_NOT_FINITE = frozenset({"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"})


class DatasetConflict(ValueError):
    """A different dataset already has this id in this ledger (HTTP 409)."""


# ---------------------------------------------------------------- manifests

def _scrubbed(value, key: str | None = None):
    if isinstance(value, dict):
        return {k: _scrubbed(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrubbed(v, key) for v in value]
    return scrub(value) if isinstance(value, str) and key in TEXT_KEYS else value


def upgrade_v1(manifest: experiments.Manifest) -> ManifestV2:
    """A v1 experiment manifest as one v2 record: the video is media (keeping its clock, its first frame at record
    time 0), the voltage CSV a table aligned by its declared offset, and the scale a scalar. v1 had no specimen
    identity, so the experiment id stands for it."""
    m = manifest
    tables, time_base, scalars = [], None, []
    if m.voltage:
        time_base = TimeBase(unit="s", origin="first video frame", offset_s=m.voltage.offset_s,
                             source=m.voltage.source)
        tables = [TableSpec(file=m.voltage.csv, format="csv", time_column="time_s",
                            columns={"voltage_V": ChannelSpec(quantity="voltage", unit="V")})]
    if m.scale:
        scalars = [ScalarSpec(quantity="lab:um_per_px", value=m.scale.value, unit="um",
                              source=experiments.declaration(m, m.scale.source),
                              uncertainty=Uncertainty(value=m.scale.uncertainty, kind="bound",
                                                      method="declared scale bound, micrometres per pixel"))]
    media = [MediaSpec(file=m.video, kind="video", t_s=0.0, label=m.title,
                       clock=m.clock.model_dump(exclude_none=True) if m.clock else None)]
    record = RecordSpec(id=m.id, specimen=Specimen(id=m.id), time=time_base, tables=tables, scalars=scalars,
                        media=media, provenance=Provenance(source=m.citation,
                                                           notes=f"declared by {m.author} in a v1 experiment manifest"))
    return ManifestV2(manifest_version=2, id=m.id, title=m.title, author=m.author, citation=m.citation,
                      licence=m.licence, records=[record], notes=m.notes)


def _read_manifest(manifest) -> tuple[ManifestV2, int, Path | None]:
    """(v2 manifest, manifest_version as written, folder the manifest file is in)."""
    base = None
    if isinstance(manifest, ManifestV2):
        return manifest, 2, None
    if isinstance(manifest, experiments.Manifest):
        return upgrade_v1(manifest), 1, None
    if not isinstance(manifest, dict):
        path = Path(manifest)
        base = path.resolve().parent
        try:
            manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        except UnicodeError as e:
            raise ValueError(f"{path.name}: the manifest must be a UTF-8 JSON file") from e
        except json.JSONDecodeError as e:
            raise ValueError(f"{path.name}: line {e.lineno}, column {e.colno}: not valid JSON ({e.msg})") from e
        except OSError as e:
            raise ValueError(f"{path.name}: cannot read the manifest ({e.strerror})") from e
    if not isinstance(manifest, dict):
        raise ValueError("the manifest must be a JSON object")
    if manifest.get("manifest_version", 1) == 1 and "video" in manifest:
        v1 = {k: v for k, v in manifest.items() if k != "manifest_version"}
        return upgrade_v1(experiments.Manifest.model_validate(experiments.scrubbed(v1))), 1, base
    if manifest.get("manifest_version") != 2:
        raise ValueError('the manifest needs "manifest_version": 2 (or is a v1 manifest with a "video")')
    return ManifestV2.model_validate(_scrubbed(manifest)), 2, base


def _adapter(system: str | None):
    if system is None:
        return None
    from translucent import systems
    try:
        return systems.get(system)
    except KeyError:
        known = ", ".join(s.id for s in systems.available())
        raise ValueError(f"system {system!r} is not registered here (registered: {known}); "
                         "leave system out to import records without one") from None


def _unit(text: str, where: str) -> units.Unit:
    try:
        return units.parse(text)
    except ValueError as e:
        raise ValueError(f"{where}: {e}") from None


def _quantity_unit(adapter, quantity: str, unit: str, where: str):
    """The column or scalar unit must measure what the system's quantity measures."""
    _unit(unit, where)
    if adapter is None or quantity.startswith("lab:"):
        return
    known = {q.name: q for q in adapter.spec.quantities}
    if quantity not in known:
        raise ValueError(f"{where}: {quantity!r} is not a quantity of system {adapter.spec.id} "
                         f"({', '.join(sorted(known))}); name anything else lab:<name>")
    if not units.same_dimension(unit, known[quantity].unit):
        raise ValueError(f"{where}: unit {unit} does not measure {quantity}; system {adapter.spec.id} "
                         f"records it in {known[quantity].unit}")


def _check_values(values: dict, value_units: dict, where: str, params: dict | None = None):
    """Every number has a unit; every unit names a value; a system parameter keeps its dimension."""
    for k, v in values.items():
        if isinstance(v, float) and k not in value_units:
            raise ValueError(f"{where} {k!r}: give its unit in {where}_units (1 for a plain number)")
    for k, u in value_units.items():
        if k not in values:
            raise ValueError(f"{where}_units {k!r}: there is no {where} value with this name")
        _unit(u, f"{where} {k!r}")
        if params and k in params and not units.same_dimension(u, params[k]):
            raise ValueError(f"{where} {k!r}: unit {u} does not measure what the system's parameter {k} "
                             f"measures ({params[k]})")


def _filename(name: str, where: str) -> str:
    try:
        return experiments._filename(name)
    except ValueError as e:
        raise ValueError(f"{where} {name!r}: {e}") from None


def _check(m: ManifestV2, adapter) -> dict[str, str]:
    """Checks that need no file contents. Returns every file the manifest names -> its role."""
    roles: dict[str, str] = {}

    def use(name, role, where):
        _filename(name, where)
        if roles.setdefault(name, role) != role:
            raise ValueError(f"{name}: used both as {roles[name]} and as {role}; give each role its own file")

    params = {p.name: p.unit for a in adapter.spec.actions for p in a.parameters} if adapter else None
    seen = set()
    for r in m.records:
        where = f"record {r.id}"
        if r.id in seen:
            raise ValueError(f"{where}: two records have this id")
        seen.add(r.id)
        _check_values(r.configuration, r.configuration_units, "configuration", params)
        _check_values(r.conditions, r.conditions_units, "conditions")
        if r.tables and r.time is None:
            raise ValueError(f"{where}: give its time base (time) for its tables: the time unit, what t = 0 means "
                             "and the record that says so")
        if r.time and _unit(r.time.unit, f"{where} time unit").dimension != units.parse("s").dimension:
            raise ValueError(f"{where}: time unit {r.time.unit} is not a unit of time")
        quantities: dict[str, str] = {}
        for tb in r.tables:
            use(tb.file, "table", f"{where} table")
            if not tb.columns:
                raise ValueError(f"{tb.file}: list at least one column to import")
            if tb.time_column in tb.columns:
                raise ValueError(f"{tb.file}, column {tb.time_column!r}: it is the time column; do not list it in "
                                 "columns as well")
            if tb.format == "json":
                if tb.header_row or tb.skip_rows or tb.delimiter is not None or tb.decimal != ".":
                    raise ValueError(f"{tb.file}: header_row, skip_rows, delimiter and decimal apply to CSV and TSV "
                                     "files, not JSON")
            elif tb.delimiter is not None and (len(tb.delimiter) != 1 or tb.delimiter in '"\r\n'):
                raise ValueError(f"{tb.file}: the delimiter must be one character (not a quote or line break)")
            elif tb.decimal == "," and (tb.delimiter or ("\t" if tb.format == "tsv" else ",")) == ",":
                raise ValueError(f"{tb.file}: a comma cannot be both the decimal mark and the delimiter; "
                                 'use format "tsv" or give a delimiter such as ";"')
            for col, ch in tb.columns.items():
                cw = f"{tb.file}, column {col!r}"
                _quantity_unit(adapter, ch.quantity, ch.unit, cw)
                if ch.quantity in quantities:
                    raise ValueError(f"{cw}: record {r.id} already has {ch.quantity} from column "
                                     f"{quantities[ch.quantity]!r}; name one of them lab:<name>")
                quantities[ch.quantity] = col
        for s in r.scalars:
            _quantity_unit(adapter, s.quantity, s.unit, f"{where} scalar {s.quantity}")
        for md in r.media:
            use(md.file, md.kind, f"{where} media")
            if md.kind == "image" and md.clock is not None:
                raise ValueError(f"{md.file}: a clock belongs to a video; give an image its time as t_s")
            if md.clock is not None:
                try:
                    clock = experiments.Clock.model_validate(md.clock)
                except ValueError as e:
                    raise ValueError(f"{md.file}: clock: {e}") from None
                if clock.timestamps_csv:
                    use(clock.timestamps_csv, "video timestamps", f"{md.file} clock")
    if len(roles) > MAX_FILES:
        raise ValueError(f"the manifest names {len(roles)} files; one import takes at most {MAX_FILES}. "
                         "Split the records into several datasets")
    return roles


# ---------------------------------------------------------------- files

def _sources(names, files_root, base: Path | None) -> dict[str, Path]:
    if isinstance(files_root, dict):
        if len(files_root) > MAX_FILES:
            raise ValueError(f"{len(files_root)} files were given; one import takes at most {MAX_FILES}")
        if missing := [n for n in names if n not in files_root]:
            raise ValueError("missing file: " + ", ".join(missing))
        if extra := sorted(set(files_root) - set(names)):
            raise ValueError("the manifest does not name these files: " + ", ".join(extra))
        return {n: Path(files_root[n]) for n in names}
    root = Path(files_root) if files_root is not None else base
    if root is None:
        raise ValueError("say where the files are: a folder, or a mapping of filename to path")
    return {n: root / n for n in names}


def _stage(paths: dict[str, Path], stage: Path) -> dict[str, Path]:
    out = {}
    for name, path in paths.items():
        if not path.is_file():
            raise ValueError(f"{name}: file not found")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"{name}: files can be up to {MAX_FILE_BYTES // 2**20} MB")
        shutil.copyfile(path, stage / name)
        if sha256_file(stage / name) != sha256_file(path):
            raise ValueError(f"{name}: the file changed while it was being copied; retry with a saved copy")
        out[name] = stage / name
    return out


# ---------------------------------------------------------------- tables

@dataclass
class _Table:
    t: list[float]
    columns: dict[str, list[float]]
    rows: int
    encoding: str
    header_line: int | None = None
    names: list[str] = field(default_factory=list)      # every column the file has, in its order
    skipped: list[dict] = field(default_factory=list)   # {"line", "text"}: the lines skip_rows passed over, as read
    repeated: list[int] = field(default_factory=list)   # lines (rows of a JSON table) whose time equals the one before
    first_line: int | None = None                       # the line of the first data row

    def ignored(self, spec: TableSpec) -> list[str]:
        """The file's columns that `spec` does not read."""
        wanted = {spec.time_column, *spec.columns}
        return [h for h in self.names if h not in wanted]


def _number(text: str, decimal: str, where: str) -> float:
    s = text.strip()
    if not s:
        raise ValueError(f"{where}: the value is empty")
    if decimal == ",":
        if "." in s:
            raise ValueError(f"{where}: {s!r} has a '.' but the decimal mark is ','")
        s = s.replace(",", ".")
    if s.lower() in _NOT_FINITE:
        raise ValueError(f"{where}: {text.strip()!r} is not a finite number")
    if not _NUMBER.fullmatch(s):
        raise ValueError(f"{where}: {text.strip()!r} is not a number")
    v = float(s)
    if not math.isfinite(v):
        raise ValueError(f"{where}: {text.strip()!r} is too large to represent")
    return v


def _check_time(prev: float | None, v: float, where: str):
    if prev is not None and v < prev:
        raise ValueError(f"{where}: time goes back from {prev:g} to {v:g}; times must not decrease")


def _delimited(path: Path, spec: TableSpec, encoding: str) -> _Table:
    name, delim = spec.file, spec.delimiter or ("\t" if spec.format == "tsv" else ",")
    header_line = spec.header_row + 1
    with path.open(newline="", encoding=encoding) as fh:
        for i in range(spec.header_row):
            if not fh.readline():
                raise ValueError(f"{name}: the file ends on line {i + 1}, before its header on line {header_line}")
        reader = csv.reader(fh, delimiter=delim, strict=True)
        try:
            header = [h.strip() for h in next(reader)]
        except StopIteration:
            raise ValueError(f"{name}: the file ends before its header on line {header_line}") from None
        except csv.Error as e:
            raise ValueError(f"{name}, line {header_line}: cannot read the header ({e})") from None
        wanted = [spec.time_column, *spec.columns]
        if missing := [c for c in wanted if c not in header]:
            shown = ", ".join(repr(h) for h in header[:12] if h) + (", ..." if len(header) > 12 else "")
            hint = ""
            if len(header) == 1 and any(d in header[0] for d in "\t,;" if d != delim):
                hint = (f"; the header has no {'tab' if delim == chr(9) else repr(delim)} between names, so check "
                        "format and delimiter")
            raise ValueError(f"{name}, header on line {header_line}: no column {missing[0]!r}; "
                             f"the header has {shown or 'nothing'}{hint}")
        if twice := [c for c in wanted if header.count(c) > 1]:
            raise ValueError(f"{name}, header on line {header_line}: column {twice[0]!r} appears twice")
        index = {c: header.index(c) for c in wanted}
        width = max(index.values()) + 1
        t: list[float] = []
        cols: dict[str, list[float]] = {c: [] for c in spec.columns}
        blank, skipped, repeated, first = None, [], [], None
        try:
            for _ in range(spec.skip_rows):
                row = next(reader, None)
                if row is None:
                    break
                line = spec.header_row + reader.line_num
                if _data_row(row, index, width, spec.decimal):
                    raise ValueError(
                        f"{name}, line {line}: skip_rows {spec.skip_rows} would drop this line, but it is a row of "
                        f"data (time {row[index[spec.time_column]].strip()!r}); skip_rows passes over lines between "
                        f"the header and the data, such as a line of units, and never data. The lines before the "
                        f"header are header_row ({spec.header_row} here); give skip_rows only for lines after the "
                        "header that are not data")
                skipped.append({"line": line, "text": delim.join(row)[:SKIPPED_LINE_CHARS],
                                "cells": {c: row[i].strip() for c, i in index.items() if i < len(row)}})
            for row in reader:
                line = spec.header_row + reader.line_num
                if not any(f.strip() for f in row):
                    blank = blank or line
                    continue
                if blank:
                    raise ValueError(f"{name}, line {blank}: a blank line inside the data")
                if len(row) < width:
                    raise ValueError(f"{name}, line {line}: {len(row)} fields, but column "
                                     f"{max(index, key=index.get)!r} is field {width} of the header")
                if len(row) > len(header) and any(f.strip() for f in row[len(header):]):
                    raise ValueError(f"{name}, line {line}: {len(row)} fields, more than the header's {len(header)}")
                if len(t) >= MAX_POINTS:
                    raise ValueError(f"{name}, line {line}: more than {MAX_POINTS:,} rows; split the file")
                v = _number(row[index[spec.time_column]], spec.decimal,
                            f"{name}, line {line}, column {spec.time_column!r}")
                _check_time(t[-1] if t else None, v, f"{name}, line {line}, column {spec.time_column!r}")
                if t and v == t[-1]:
                    repeated.append(line)
                first = first or line
                t.append(v)
                for c, values in cols.items():
                    values.append(_number(row[index[c]], spec.decimal, f"{name}, line {line}, column {c!r}"))
        except csv.Error as e:
            raise ValueError(f"{name}, line {spec.header_row + reader.line_num}: cannot read the "
                             f"{spec.format.upper()} ({e})") from None
    return _Table(t, cols, len(t), encoding.removesuffix("-sig"), header_line, [h for h in header if h],
                  skipped, repeated, first)


_UNIT_IN_NAME = re.compile(r"\[([^\]]+)\]|\(([^)]+)\)|/\s*([^/\s]+)\s*$")


def _same_unit(text: str, unit: str) -> bool:
    try:
        return units.factor(text.strip(), unit) == (1, 0)
    except ValueError:
        return False


def _unit_stated(column: str, unit: str, cells: list[str]) -> bool:
    """Whether the file itself names `unit` for a column: in its header name ("Voltage [V]", "Ewe/V", "I (mA)") or
    in a line skip_rows passed over (a units line under the header)."""
    named = [g for m in _UNIT_IN_NAME.finditer(column) for g in m.groups() if g]
    return any(_same_unit(x, unit) for x in [*named, *cells])


def _import_note(tb: TableSpec, table: _Table, tbase: TimeBase) -> dict:
    """What the import did to one table and what the file leaves open, as facts a reader can check against the
    file: the lines it passed over, the rows that repeat a time, the units only the manifest states, and every
    change between the text and the stored values."""
    def cells(c):
        return [s["cells"][c] for s in table.skipped if c in s["cells"]]

    unstated = [{"column": c, "unit": u} for c, u in [(tb.time_column, tbase.unit),
                                                       *((c, ch.unit) for c, ch in tb.columns.items())]
                if u != "1" and not _unit_stated(c, u, cells(c))]     # a count or a code has no unit to state
    changes = []
    if tb.decimal == ",":
        changes.append("decimal commas read as decimal points")
    if tbase.unit != "s":
        changes.append(f"time converted from {tbase.unit} to s")
    if tbase.offset_s:
        changes.append(f"{tbase.offset_s:g} s subtracted from the time column (the record clock)")
    changes.append("values stored as written, in the units the manifest gives; analysis converts them to the "
                   "system's units")
    return {"file": tb.file, "header_line": table.header_line, "first_data_line": table.first_line,
            "rows_read": table.rows,
            "lines_skipped": [{"line": s["line"], "text": s["text"]} for s in table.skipped],
            "repeated_times": {"count": len(table.repeated), "lines": table.repeated[:LISTED_LINES]},
            "units_not_in_file": unstated, "changes": changes}


def _data_row(row: list[str], index: dict[str, int], width: int, decimal: str) -> bool:
    """Whether a line reads as a row of data: every column the table takes (time first) is a number."""
    if len(row) < width:
        return False
    try:
        for i in index.values():
            _number(row[i], decimal, "")
    except ValueError:
        return False
    return True


class _NonFinite:
    def __init__(self, text):
        self.text = text


def _json_number(v, where: str) -> float:
    if isinstance(v, _NonFinite):
        raise ValueError(f"{where}: {v.text} is not a finite number")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{where}: {json.dumps(v)[:40]} is not a number")
    v = float(v)
    if not math.isfinite(v):
        raise ValueError(f"{where}: the value is too large to represent")
    return v


def _json_table(path: Path, spec: TableSpec) -> _Table:
    """Columns as {"name": [values...], ...}, or rows as [{"name": value, ...}, ...]."""
    name = spec.file
    if path.stat().st_size > MAX_JSON_TABLE_BYTES:
        raise ValueError(f"{name}: a JSON table can be up to {MAX_JSON_TABLE_BYTES // 2**20} MB; send a larger one "
                         "as CSV")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=_NonFinite)
    except UnicodeError as e:
        raise ValueError(f"{name}: a JSON table must be UTF-8") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"{name}: line {e.lineno}, column {e.colno}: not valid JSON ({e.msg})") from None
    wanted = [spec.time_column, *spec.columns]
    if isinstance(data, dict) and all(isinstance(v, list) for v in data.values()):
        if missing := [c for c in wanted if c not in data]:
            raise ValueError(f"{name}: no column {missing[0]!r}; it has {', '.join(map(repr, list(data)[:12]))}")
        lengths = {c: len(data[c]) for c in wanted}
        if len(set(lengths.values())) > 1:
            shown = ", ".join(f"{c!r}: {n}" for c, n in lengths.items())
            raise ValueError(f"{name}: columns have different lengths ({shown})")
        rows = range(lengths[spec.time_column])
        value = lambda i, c: data[c][i]
        names = list(data)
    elif isinstance(data, list) and all(isinstance(r, dict) for r in data):
        for i, r in enumerate(data):
            if missing := [c for c in wanted if c not in r]:
                raise ValueError(f"{name}, row {i + 1}: no column {missing[0]!r}")
        rows = range(len(data))
        value = lambda i, c: data[i][c]
        names = list(dict.fromkeys(k for r in data for k in r))
    else:
        raise ValueError(f'{name}: give columns as {{"name": [values, ...]}} or rows as [{{"name": value}}, ...]')
    if len(rows) > MAX_POINTS:
        raise ValueError(f"{name}: more than {MAX_POINTS:,} rows; split the file")
    t: list[float] = []
    cols: dict[str, list[float]] = {c: [] for c in spec.columns}
    repeated = []
    for i in rows:
        v = _json_number(value(i, spec.time_column), f"{name}, row {i + 1}, column {spec.time_column!r}")
        _check_time(t[-1] if t else None, v, f"{name}, row {i + 1}, column {spec.time_column!r}")
        if t and v == t[-1]:
            repeated.append(i + 1)
        t.append(v)
        for c, values in cols.items():
            values.append(_json_number(value(i, c), f"{name}, row {i + 1}, column {c!r}"))
    return _Table(t, cols, len(t), "utf-8", None, names, [], repeated, 1)


def _table(path: Path, spec: TableSpec) -> _Table:
    if spec.format == "json":
        out = _json_table(path, spec)
    else:
        # UTF-8 first; instrument software on Windows (EC-Lab, Maccor) writes cp1252, e.g. "°C"
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                out = _delimited(path, spec, encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError(f"{spec.file}: the text is neither UTF-8 nor Windows-1252; save it as UTF-8")
    if out.rows < MIN_ROWS:
        raise ValueError(f"{spec.file}: {out.rows} rows of data; provide at least {MIN_ROWS}")
    return out


def _read_key(tb: TableSpec) -> str:
    return tb.model_copy(update={"columns": {}}).model_dump_json()


def _reads(m: ManifestV2) -> dict[str, TableSpec]:
    """One read per file and parse settings, of every column any record takes from it: a file many records name is
    parsed once (a parse of a 2M-row file costs seconds, and its rows in memory)."""
    out: dict[str, TableSpec] = {}
    for r in m.records:
        for tb in r.tables:
            key = _read_key(tb)
            out[key] = tb if key not in out else out[key].model_copy(
                update={"columns": {**out[key].columns, **tb.columns}})
    return out


def _record_time(table: _Table, spec: TableSpec, tb: TimeBase) -> list[float]:
    """Column time converted to seconds, minus the declared offset: the record clock."""
    t = units.convert(table.t, tb.unit, "s") if tb.unit != "s" else table.t
    t = [x - tb.offset_s for x in t] if tb.offset_s else list(t)
    if not all(math.isfinite(x) for x in t):
        raise ValueError(f"{spec.file}, column {spec.time_column!r}: the offset gives times too large to represent")
    return t


# ---------------------------------------------------------------- media

def _video_type(head: bytes) -> str | None:
    if head[4:8] == b"ftyp":
        return "video/quicktime" if head[8:12] == b"qt  " else "video/mp4"
    if head[4:8] in (b"moov", b"wide", b"free", b"mdat"):
        return "video/quicktime"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "video/x-msvideo"
    return None


def _image_type(head: bytes) -> str | None:
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        return "image/tiff"
    return None


def _pages(path: Path, media_type: str) -> list:
    import cv2
    import numpy as np
    buf = np.frombuffer(path.read_bytes(), np.uint8)
    if media_type == "image/tiff":
        ok, pages = cv2.imdecodemulti(buf, cv2.IMREAD_UNCHANGED)
        return list(pages) if ok else []
    img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
    return [img] if img is not None else []


def _media(path: Path, md: MediaSpec, staged: dict[str, Path]) -> dict:
    """Media type, shape and frame count, with the v1 clock rules checked for a video with a clock."""
    with path.open("rb") as f:
        head = f.read(16)
    image_type = _image_type(head)
    if md.kind == "image":
        if image_type is None:
            raise ValueError(f"{md.file}: not a PNG, JPEG or TIFF image (checked by its first bytes)")
        pages = _pages(path, image_type)
        if not pages:
            raise ValueError(f"{md.file}: the image could not be decoded; export it again as PNG or TIFF")
        h, w = pages[0].shape[:2]
        if len(pages) == 1:
            return {"media_type": image_type, "shape": [h, w], "axes": ["y", "x"], "frames": 1}
        return {"media_type": image_type, "shape": [len(pages), h, w], "axes": ["frame", "y", "x"],
                "frames": len(pages)}
    clock = experiments.Clock.model_validate(md.clock) if md.clock is not None else None
    interval = bool(clock and clock.interval_s)
    if image_type == "image/tiff":
        import numpy as np
        pages = _pages(path, image_type)
        if len(pages) < 2:
            raise ValueError(f"{md.file}: a TIFF video needs at least two pages; import one page as an image")
        captures = [0]
        for a, b in zip(pages, pages[1:]):
            captures.append(captures[-1] + int(not np.array_equal(a, b)))
        if interval and captures[-1] == 0:
            raise ValueError(f"{md.file}: all pages are identical; provide timestamps to tell acquisitions apart")
        media_type, (h, w) = image_type, pages[0].shape[:2]
    else:
        media_type = _video_type(head)
        if media_type is None:
            raise ValueError(f"{md.file}: not an MP4, MOV or AVI video, or a multi-page TIFF stack (checked by its "
                             "first bytes)")
        try:
            captures = experiments._captures(path, interval=interval)
        except ValueError as e:
            raise ValueError(f"{md.file}: {e}") from None
        import cv2
        cap = cv2.VideoCapture(str(path))
        h, w = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        cap.release()
    frames = len(captures)
    if clock and clock.timestamps_csv:
        try:
            experiments._timestamps(staged[clock.timestamps_csv], frames, captures)
        except ValueError as e:
            raise ValueError(f"{md.file} clock: {e}") from None
    if interval and not math.isfinite(captures[-1] * clock.interval_s):
        raise ValueError(f"{md.file}: the acquisition interval gives an elapsed time too large to represent")
    return {"media_type": media_type, "shape": [frames, h, w], "axes": ["frame", "y", "x"], "frames": frames,
            "clock": clock.model_dump(exclude_none=True) if clock else None}


# ---------------------------------------------------------------- the import

def configuration_hash(system: str | None, configuration: dict, configuration_units: dict) -> str:
    """sha256 of the canonical {"system", "configuration"} with each number paired with its declared unit."""
    normalized = {k: {"value": v, "unit": configuration_units[k].strip()} if isinstance(v, float) else {"value": v}
                  for k, v in sorted(configuration.items())}
    return digest({"system": system, "configuration": normalized})


def _json_bytes(obj) -> bytes:
    # the exact bytes Ledger.put_json stores, so an artifact id is known before anything is written
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _artifact_id(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass
class _Write:
    """One artifact to store: a staged file (path) or JSON bytes (data), with the id it will get."""
    id: str
    kind: str
    media_type: str
    label: str
    source: Source | None = None
    path: Path | None = None
    data: bytes | None = None
    parent: str | None = None
    params: dict | None = None
    shape: list[int] | None = None
    axes: list[str] | None = None


def _store(L: Ledger, w: _Write) -> None:
    if L.get("artifact", w.id):
        return
    kw = dict(kind=w.kind, media_type=w.media_type, label=w.label, source=w.source, shape=w.shape, axes=w.axes,
              transform=Transform(op=PARSE_OP, parent_ids=[w.parent], params=w.params) if w.parent else None)
    try:
        art = L.put_bytes(w.data, suffix=".json", **kw) if w.data is not None else L.put_file(w.path, **kw)
    except AdmissionError:
        if L.get("artifact", w.id):  # another process stored the same bytes first
            return
        raise
    if art.id != w.id:
        raise RuntimeError(f"{w.label}: stored as {art.id}, expected {w.id}")


@contextmanager
def _storing(L: Ledger):
    lock = ExclusiveFileLock(L.root / "locks" / "dataset-import.lock")
    deadline = time.monotonic() + LOCK_WAIT_S
    while not lock.try_acquire():
        if time.monotonic() >= deadline:
            raise RuntimeError("another dataset import into this ledger is still storing its files; try again")
        time.sleep(random.uniform(0.02, 0.2))
    try:
        yield
    finally:
        lock.release()


def _body(rec: DatasetRecord) -> dict:
    # import notes follow from the files and the manifest, both in the body; a record stored before notes existed
    # has none, and is the same dataset
    return rec.model_dump(mode="json", exclude={"created_at": True, "records": {"__all__": {"import_notes"}}})


def _conflict(L: Ledger, rec: DatasetRecord) -> DatasetRecord | None:
    """The stored record when it is this one; DatasetConflict when the id holds a different dataset."""
    old = L.get("dataset", rec.id)
    if old is None:
        return None
    if _body(old) != _body(rec):
        raise DatasetConflict(f"{rec.id}: a different dataset already has this id in this ledger; use a new id")
    return old


def file_names(manifest) -> list[str]:
    """Every file a manifest names, after the checks that need no file contents."""
    m, _, _ = _read_manifest(manifest)
    return list(_check(m, _adapter(m.system)))


def import_dataset(ledger: Ledger, manifest, files_root=None) -> DatasetRecord:
    """Import a v2 manifest (a path, a dict or a ManifestV2; a v1 experiment manifest is upgraded) and its files
    (`files_root`: a folder, or filename -> path; default the manifest's folder). Returns the DatasetRecord."""
    m, version, base = _read_manifest(manifest)
    adapter = _adapter(m.system)
    roles = _check(m, adapter)
    paths = _sources(list(roles), files_root, base)
    source = lambda name: Source(uri=f"dataset:{m.id}/{name}", citation=m.citation, licence=m.licence)
    with tempfile.TemporaryDirectory(prefix=".incoming-", dir=ledger.root) as tmp:
        staged = _stage(paths, Path(tmp))
        writes: dict[str, _Write] = {}      # file artifacts first: series cite them as transform parents
        series: dict[str, _Write] = {}      # by id: records reading the same values at the same times store them once
        records, reads, tables, held = [], _reads(m), {}, [0, 0]   # held: rows parsed, series points

        def table_of(tb: TableSpec) -> _Table:
            key = _read_key(tb)
            if key not in tables:
                tables[key] = _table(staged[tb.file], reads[key])
                held[0] += tables[key].rows
                if held[0] > MAX_IMPORT_POINTS:
                    raise ValueError(f"the tables hold more than {MAX_IMPORT_POINTS:,} rows in all; split the records "
                                     "into several datasets")
            return tables[key]

        for r in m.records:
            files, channels, media, ignored, notes = {}, [], [], [], []
            for tb in r.tables:
                table = table_of(tb)
                notes.append(_import_note(tb, table, r.time))
                fid = files[tb.file] = sha256_file_id(staged[tb.file])
                writes.setdefault(tb.file, _Write(fid, "table", TABLE_TYPES[tb.format], f"{m.title}: {tb.file}",
                                                  source(tb.file), path=staged[tb.file]))
                t = _record_time(table, tb, r.time)
                repeated = sum(1 for a, b in zip(t, t[1:]) if a == b)
                ignored += [c for c in table.ignored(tb) if c not in ignored]
                for col, ch in tb.columns.items():
                    data = _json_bytes({"t": t, "t_unit": "s", "y": table.columns[col], "unit": ch.unit})
                    params = {"dataset": m.id, "record": r.id, "file": tb.file, "format": tb.format, "column": col,
                              "quantity": ch.quantity, "unit": ch.unit, "time_column": tb.time_column,
                              "time_unit": r.time.unit, "time_to_s": [str(x) for x in units.factor(r.time.unit, "s")],
                              "offset_s": r.time.offset_s, "header_row": tb.header_row, "skip_rows": tb.skip_rows,
                              "delimiter": None if tb.format == "json" else
                              tb.delimiter or ("\t" if tb.format == "tsv" else ","),
                              "decimal": tb.decimal, "encoding": table.encoding, "rows": table.rows,
                              "repeated_times": repeated}
                    w = _Write(_artifact_id(data), "series", "application/json", f"{r.id}: {ch.quantity} ({ch.unit})",
                               data=data, parent=fid, params=params)
                    if w.id not in series:    # the first record's params win, as storing the same bytes again did
                        series[w.id] = w
                        held[1] += len(t)
                        if held[1] > MAX_IMPORT_POINTS:
                            raise ValueError(f"the records' series hold more than {MAX_IMPORT_POINTS:,} points in all; "
                                             "split the records into several datasets")
                    channels.append(StoredChannel(
                        column=col, quantity=ch.quantity, unit=ch.unit, series_id=w.id, n=len(t),
                        t_range=(t[0], t[-1]),
                        uncertainty=ch.uncertainty or Uncertainty(value=None, kind="none",
                                                                  method="not declared in the manifest")))
            for md in r.media:
                info = _media(staged[md.file], md, staged)
                fid = files[md.file] = sha256_file_id(staged[md.file])
                writes.setdefault(md.file, _Write(fid, "frame" if md.kind == "image" else "video",
                                                  info["media_type"], md.label or f"{m.title}: {md.file}",
                                                  source(md.file), path=staged[md.file], shape=info["shape"],
                                                  axes=info["axes"]))
                entry = {"artifact_id": fid, "file": md.file, "kind": md.kind, "media_type": info["media_type"],
                         "shape": info["shape"], "t_s": md.t_s, "needs_vision": md.needs_vision, "label": md.label}
                if md.kind == "video":
                    entry["clock"] = info["clock"]
                    if info["clock"] and info["clock"].get("timestamps_csv"):
                        ts = info["clock"]["timestamps_csv"]
                        entry["timestamps_id"] = files[ts] = sha256_file_id(staged[ts])
                        writes.setdefault(ts, _Write(entry["timestamps_id"], "table", "text/csv",
                                                     f"{m.title}: {ts}", source(ts), path=staged[ts]))
                media.append(entry)
            records.append(StoredRecord(
                id=r.id, role=r.role, specimen=r.specimen, configuration=r.configuration,
                configuration_units=r.configuration_units,
                configuration_hash=configuration_hash(m.system, r.configuration, r.configuration_units),
                conditions=r.conditions, conditions_units=r.conditions_units, time=r.time, channels=channels,
                scalars=r.scalars, media=media, provenance=r.provenance, files=files, ignored_columns=ignored,
                import_notes=notes))
        manifest_bytes = _json_bytes(m.model_dump(mode="json"))
        manifest_write = _Write(_artifact_id(manifest_bytes), "text", "application/json", f"{m.title}: manifest",
                                source("manifest.json"), data=manifest_bytes)
        rec = DatasetRecord(id=m.id, manifest_version=version, manifest_id=manifest_write.id, title=m.title,
                            author=m.author, citation=m.citation, licence=m.licence, system=m.system,
                            records=records, upgraded_from="v1" if version == 1 else None, created_at=time.time())
        if existing := _conflict(ledger, rec):
            return existing
        with _storing(ledger):
            for w in [*writes.values(), *series.values(), manifest_write]:
                _store(ledger, w)
            with ledger.transaction():
                if existing := _conflict(ledger, rec):
                    return existing
                ledger.add(rec)
    return rec


def sha256_file_id(path: Path) -> str:
    return "sha256:" + sha256_file(path)


# ---------------------------------------------------------------- reading back

def series(ledger: Ledger, dataset_id: str, record_id: str, quantity: str) -> tuple[StoredChannel, dict]:
    """A record's channel for `quantity` and its series {"t", "t_unit", "y", "unit"}."""
    ds = ledger.get("dataset", dataset_id)
    if ds is None:
        raise ValueError(f"no dataset {dataset_id} in this ledger")
    rec = next((r for r in ds.records if r.id == record_id), None)
    if rec is None:
        raise ValueError(f"dataset {dataset_id} has no record {record_id}")
    ch = next((c for c in rec.channels if c.quantity == quantity), None)
    if ch is None:
        raise ValueError(f"{dataset_id}/{record_id} has no {quantity} series "
                         f"(it has {', '.join(c.quantity for c in rec.channels) or 'none'})")
    return ch, json.loads(ledger.blob_path(ch.series_id).read_bytes())


def measure(ledger: Ledger, dataset_id: str, record_id: str, spec: OperatorSpec, *,
            input_unit: str | None = None) -> dict:
    """An ops/1 operator applied to a record's channel; the result names the series it read."""
    ch, s = series(ledger, dataset_id, record_id, spec.channel)
    return {**operators.evaluate(spec, s["t"], s["y"], s["unit"], input_unit=input_unit), "series_id": ch.series_id}
