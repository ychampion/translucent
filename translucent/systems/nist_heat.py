"""NIST mds2-4027: stdlib XLSX ingestion and trace-based event reconstruction.

Times are estimates, not published event markers. The reconstruction uses the
complete trace as an external event annotator; planner features stop at heater-off.
"""

from __future__ import annotations

import csv
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

from translucent.contracts import Source, Transform
from translucent.intervention import canonical, record, seal

DATA = Path(__file__).resolve().parents[2] / "data" / "nist-heat-interrupt"
SYSTEM = dict(
    id="nist-heat-interrupt",
    title="NIST heating interruption, 77 single-cell trials",
    citation="Tam et al., Applied Thermal Engineering 298 (2026) 131107, Tables A1/A2",
    doi="10.18434/mds2-4027",
    licence="NIST open licence; US public domain",
    sensor="One surface type-K thermocouple, 1 Hz, mid-height",
    decision_point="Recorded heater-off; trace slope break minus 10 s thermocouple lag",
)
VENT_MEANS = {
    ("18650", 100): 285,
    ("18650", 75): 274,
    ("18650", 50): 333,
    ("18650", 25): 377.5,
    ("21700", 100): 549,
    ("21700", 75): 616,
    ("21700", 50): 682.7,
    ("21700", 25): 789.7,
}


def read_workbook(path):
    """Read numeric cells and shared strings without Excel or an optional engine."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = [
                "".join(x.itertext())
                for x in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(
                    "m:si", ns
                )
            ]
        rows = []
        for row in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).findall(
            "m:sheetData/m:row", ns
        ):
            values = {}
            for cell in row.findall("m:c", ns):
                col = 0
                for ch in cell.attrib["r"].rstrip("0123456789"):
                    col = 26 * col + ord(ch) - 64
                v = cell.find("m:v", ns)
                value = v.text if v is not None else ""
                if cell.attrib.get("t") == "s":
                    value = shared[int(value)]
                elif cell.attrib.get("t") == "inlineStr":
                    value = "".join(cell.find("m:is", ns).itertext())
                values[col - 1] = value
            rows.append(values)
    result = {
        int(v.replace("Exp", "").strip()): ([], [])
        for k, v in rows[0].items()
        if k and v.startswith("Exp")
    }
    columns = {
        k: int(v.replace("Exp", "").strip())
        for k, v in rows[0].items()
        if k and v.startswith("Exp")
    }
    for row_number, row in enumerate(rows[2:], start=3):
        for col, e in columns.items():
            value = row.get(col, "")
            if value in ("", "na"):
                # The published sheet explicitly marks unavailable traces 'na'.
                continue
            try:
                t = float(row[0])
                y = float(value)
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(
                    f"Invalid numeric trace cell for Exp{e} at worksheet row {row_number}"
                ) from exc
            if not math.isfinite(t) or not math.isfinite(y):
                raise ValueError(
                    f"Non-finite trace cell for Exp{e} at worksheet row {row_number}"
                )
            result[e][0].append(t)
            result[e][1].append(y)
    return result


def _slope(y):
    from translucent.intervention_numeric import linear_fit

    return linear_fit(range(len(y)), y)[1]


def ignition_time(t, y):
    """Return the first recorded ignition/thermocouple-loss jump, if present."""
    t = np.asarray(t)
    y = np.asarray(y)
    jumps = np.where(
        ((y[2:] - y[:-2] > 20) | ((y[2:] - y[:-2] < -10) & (y[:-2] > 120)))
        & (t[:-2] > 150)
    )[0]
    return int(t[jumps[0]]) if len(jumps) else None


def reconstruct(t, y, fmt, soc, delay, outcome, *, heater_off_lag_s=10):
    """Reconstruct annotation times at a registered thermocouple lag.

    The lag sensitivity moves visible slope breaks only. Inferred heater-off
    times have no measured slope break and retain the paper mean-plus-delay.
    """
    if (
        not isinstance(heater_off_lag_s, (int, float))
        or not math.isfinite(heater_off_lag_s)
        or not 0 <= heater_off_lag_s <= 60
    ):
        raise ValueError("heater_off_lag_s must be a finite number in [0,60]")
    sources = {
        "t_heater_on": "trace t=0 assumed power-on; checked against videos S3/S4"
    }
    if not y:
        return dict(
            t_heater_on=None,
            t_vent=None,
            t_heater_off=None,
            t_ignition=None,
            truncated_at=None,
            flags=["no_trace"],
            event_sources={"t_heater_on": "thermocouple malfunction; no trace"},
        )
    t = np.asarray(t)
    y = np.asarray(y)
    if not np.all(np.diff(t) == 1):
        raise ValueError("NIST trace must be sampled at 1 Hz without gaps")
    ignition = ignition_time(t, y)
    sources["t_ignition"] = (
        "trace: first >20 C rise in 2 s, or >10 C drop above 120 C (thermocouple loss)"
    )
    flags = []
    if delay is None:
        return dict(
            t_heater_on=0,
            t_vent=None,
            t_heater_off=None,
            t_ignition=ignition,
            truncated_at=None,
            flags=["thermocouple_loss"] if max(y) < 300 else [],
            event_sources=sources,
        )
    mean = VENT_MEANS[(fmt, soc)]
    end = (ignition or len(y)) - 3
    best = (-9, None)
    for i in range(int(mean - 150 + delay), min(int(mean + 150 + delay), end - 20)):
        if i - 20 < 0:
            continue
        change = _slope(y[i - 20 : i]) - _slope(y[i + 1 : i + 21])
        if change > best[0]:
            best = (change, i)
    if best[1] is None:
        raise ValueError("No heater-off reconstruction window")
    raw = int(t[best[1]])
    gap = ignition - raw if ignition is not None else 9999
    if 0.08 <= best[0] < 2 and gap >= 8:
        off = raw - heater_off_lag_s
        sources["t_heater_off"] = (
            f"trace maximum 20 s slope break minus {heater_off_lag_s:g} s lag; calibrated on videos S3/S4"
        )
    else:
        off = int(round(mean + delay))
        flags.append("heater_off_inferred")
        sources["t_heater_off"] = (
            "INFERRED: paper Table 2 mean vent time + Table A1/A2 delay; no visible slope break"
        )
    sources["t_vent"] = (
        "INFERRED: reconstructed heater-off minus published Table A1/A2 delay"
    )
    cut = None
    if outcome == "rescued":
        if ignition is not None:
            start = int(np.searchsorted(t, off + 30))
            stop = int(np.searchsorted(t, ignition - 5))
            cut = int(t[start + np.argmin(y[start:stop])])
            flags.append("render_safe_reheat")
            sources["truncated_at"] = (
                "trace V-shaped minimum between heater-off+30 s and render-safe ignition-5 s"
            )
        ignition = None
        sources["t_ignition"] = (
            "paper blue label: no ignition before end of observation or render-safe reheating"
        )
    elif max(y) < 300:
        flags.append("thermocouple_loss")
    return dict(
        t_heater_on=0,
        t_vent=off - delay,
        t_heater_off=off,
        t_ignition=ignition,
        truncated_at=cut,
        flags=flags,
        event_sources=sources,
        heater_off_raw_break=raw,
        heater_off_slope_break=best[0],
        heater_off_lag_s=heater_off_lag_s,
    )


@lru_cache(maxsize=8)
def load_dataset(directory=DATA, heater_off_lag_s=10):
    directory = Path(directory)
    traces = read_workbook(
        directory / "Li-ion Battery Safe Intervention Time Temperature Data.xlsx"
    )
    labels = {
        int(r["exp"]): r["outcome"]
        for r in csv.DictReader((directory / "outcomes.csv").open())
    }
    result = []
    for m in csv.DictReader((directory / "trials.csv").open()):
        exp = int(m["exp"])
        fmt = m["format"]
        soc = int(m["soc"])
        delay = int(m["delay_after_vent_s"]) if m["delay_after_vent_s"] else None
        t, y = traces[exp]
        outcome = labels[exp]
        events = reconstruct(
            t, y, fmt, soc, delay, outcome, heater_off_lag_s=heater_off_lag_s
        )
        if exp in (67, 73):
            events["flags"].append("censored")
        if exp == 33:
            events["flags"].append("suspect_soc")
        cut = events["truncated_at"]
        if cut is not None:
            n = t.index(float(cut)) + 1
            t, y = t[:n], y[:n]
        result.append(
            dict(
                exp=exp,
                trial_key=f"Exp{exp}",
                format=fmt,
                soc=soc,
                delay_after_vent_s=delay,
                chemistry="NCA/graphite" if fmt == "18650" else "NMC/graphite",
                capacity_mAh=3500 if fmt == "18650" else 5000,
                t=t,
                y=y,
                outcome=outcome,
                **events,
            )
        )
    return result


@lru_cache(maxsize=4)
def visual_corroboration(directory=DATA):
    """Corroboration of paper categories against a binary recorded trace check.

    A detector cannot distinguish red from black table categories. Agreement
    therefore requires identical image/vector categories and matching binary
    ignition status within the recorded, render-safe-truncated observation.
    Missing traces never count as three-way corroboration.
    """
    path = Path(directory) / "visual" / "out" / "task1_labels.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"Required corroborating label reading is missing: {path}"
        )
    real_experiments = {
        int(row["exp"])
        for row in csv.DictReader((Path(directory) / "trials.csv").open())
    }
    return corroboration_rows(csv.DictReader(path.open()), real_experiments)


def corroboration_rows(rows, real_experiments=None):
    """Pure row interpretation, also used by the offline receipt checker."""
    categories = {
        "blue": "rescued",
        "black": "delayed_ignition",
        "red": "ignited",
        "none": "continuous",
    }
    result = {}
    for row in rows:
        if real_experiments is not None and int(row["exp"]) not in real_experiments:
            continue  # Phantom Exp19/20 rows.
        image = categories[row["image_class"]]
        vector = categories[row["vector_class"]]
        trace = (
            row["trace_label"] if row["trace_label"] != "no trace" else "unavailable"
        )
        binary = "ignited" if image in ("ignited", "delayed_ignition") else image
        result[int(row["exp"])] = dict(
            image=image,
            vector=vector,
            trace=trace,
            agree=image == vector and trace == binary,
        )
    return result


@lru_cache(maxsize=4)
def visual_provenance(directory=DATA):
    """Hashes bind revealed row evidence to the supplied, committed readings."""
    directory = Path(directory)
    return [
        dict(
            path="visual/out/" + name,
            sha256=hashlib.sha256(
                (directory / "visual" / "out" / name).read_bytes()
            ).hexdigest(),
        )
        for name in (
            "task1_labels.csv",
            "task1_summary.json",
            "vector_labels.csv",
            "task2_timing.csv",
            "task2_timing.json",
        )
    ]


def register_visual_evidence(ledger, cid):
    """Close the label-reading audit trail after every ingested trial is revealed.

    Bulk readings contain labels of many trials. They are therefore imported at
    the campaign's end, outside the planner tools, never during sealed ingest.
    """
    from translucent.intervention import records

    trials = records(ledger, cid, "trial")
    revealed = {r.data["trial_id"] for r in records(ledger, cid, "reveal")}
    if not trials or any(t.id not in revealed for t in trials):
        raise ValueError(
            "Visual label artifacts require every campaign trial to be revealed"
        )
    existing = [
        r
        for r in records(ledger, cid, "reference")
        if r.data.get("kind") == "visual_corroboration"
    ]
    if existing:
        return existing[0]
    source = Source(
        uri="doi:10.1016/j.applthermaleng.2026.131107",
        citation=SYSTEM["citation"] + "; supplied image readings and video timing",
        licence="Derived factual readings and model text; publisher images and videos excluded",
    )
    root = DATA / "visual"
    files = []
    # the published order on every OS: Windows compares path parts case-insensitively, POSIX by byte, so a plain
    # sorted() listed "out/S3_ocr.csv" before "out/chunks/..." on Linux and changed the receipt's bytes
    for path in sorted(root.rglob("*"), key=lambda p: [part.lower() for part in p.relative_to(root).parts]):
        if not path.is_file() or path.suffix not in (".csv", ".json"):
            continue
        artifact = ledger.put_file(
            path,
            kind="table" if path.suffix == ".csv" else "text",
            media_type="text/csv" if path.suffix == ".csv" else "application/json",
            source=source,
        )
        files.append(
            dict(path=path.relative_to(root).as_posix(), artifact_id=artifact.id)
        )
    return record(
        ledger,
        cid,
        "reference",
        dict(
            kind="visual_corroboration",
            files=files,
            source=source.model_dump(mode="json"),
            registered_after_reveals=True,
            models={"table": "Muse Spark", "video_timing": ["Muse Spark", "SAM 3.1"]},
            interpretation="Image/vector categories agree; trace corroboration is binary ignition, missing traces unavailable; no counterfactual evidence",
        ),
    )


def outcome_evidence_problems(ledger, trial, revealed, records):
    """Reconstruct recorded NIST annotations without consulting the sealed store.

    Source workbooks and reconstructed experiments are cached for one immutable
    verification snapshot. Fold scopes share the same physical source traces.
    """
    cache = getattr(ledger, "_intervention_verification_cache", None)
    memo = cache.memo if cache is not None else {}
    d, outcome = trial.data, revealed.data
    problems = []
    try:
        observations_key = ("nist_observations", trial.campaign_id)
        if observations_key not in memo:
            by_trial = {}
            for row in records:
                if row.type == "observation":
                    by_trial.setdefault(row.data["trial_id"], []).append(row)
            memo[observations_key] = by_trial
        for observation in memo[observations_key].get(trial.id, ()):
            trace = observation.data["trace"]
            detected = ignition_time(trace["t"], trace["temperature_C"])
            declared = outcome.get("t_ignition")
            if detected is not None and (declared is None or detected < declared):
                problems.append(
                    f"{revealed.id}: paid observation contains ignition jump at "
                    f"{detected} s before revealed t_ignition {declared!r}"
                )
            if outcome["outcome"] != "rescued" and declared is None:
                problems.append(
                    f"{revealed.id}: ignited trial lacks an ignition time; "
                    "watch safety is unknown"
                )

        artifact = ledger.get("artifact", d["series_artifact"])
        transform = artifact.transform if artifact is not None else None
        if transform is None or transform.op != "nist_trace_and_render_safe_truncation":
            return problems
        if len(transform.parent_ids) != 1:
            raise ValueError("NIST trace must identify exactly one source workbook")
        source_id = transform.parent_ids[0]
        workbook_key = ("nist_workbook", source_id)
        if workbook_key not in memo:
            source_bytes = ledger.blob_path(source_id).read_bytes()
            if "sha256:" + hashlib.sha256(source_bytes).hexdigest() != source_id:
                raise ValueError(
                    "NIST source workbook differs from its content address"
                )
            memo[workbook_key] = read_workbook(ledger.blob_path(source_id))
        exp = d["exp"]
        lag = d.get("heater_off_lag_s", 10)
        expected_key = (
            "nist_events",
            source_id,
            exp,
            d["condition"]["format"],
            d["condition"]["soc"],
            d["delay_after_vent_s"],
            outcome["outcome"],
            lag,
        )
        if expected_key not in memo:
            t, y = memo[workbook_key][exp]
            events = reconstruct(
                t,
                y,
                d["condition"]["format"],
                d["condition"]["soc"],
                d["delay_after_vent_s"],
                outcome["outcome"],
                heater_off_lag_s=lag,
            )
            if exp in (67, 73):
                events["flags"].append("censored")
            if exp == 33:
                events["flags"].append("suspect_soc")
            cut = events["truncated_at"]
            n = t.index(float(cut)) + 1 if cut is not None else len(t)
            memo[expected_key] = (events, dict(t=t[:n], temperature_C=y[:n]))
        events, expected_series = memo[expected_key]
        for field in ("t_ignition", "flags", "truncated_at", "event_sources"):
            if outcome.get(field) != events[field]:
                problems.append(
                    f"{revealed.id}: revealed {field} differs from source-trace reconstruction"
                )
        for field in ("t_heater_on", "t_vent", "t_heater_off"):
            if d.get(field) != events[field]:
                problems.append(
                    f"{trial.id}: {field} differs from source-trace reconstruction"
                )
        safe_flags = [
            flag
            for flag in events["flags"]
            if flag in ("heater_off_inferred", "suspect_soc")
        ]
        if d.get("flags") != safe_flags:
            problems.append(
                f"{trial.id}: public trial flags differ from source-trace reconstruction"
            )
        public_sources = {
            field: source
            for field, source in events["event_sources"].items()
            if field in ("t_heater_on", "t_vent", "t_heater_off")
        }
        if d.get("event_sources") != public_sources:
            problems.append(
                f"{trial.id}: public event sources differ from source-trace reconstruction"
            )
        series = (
            cache.blob_json(d["series_artifact"])
            if cache is not None
            else json.loads(ledger.blob_path(d["series_artifact"]).read_text())
        )
        if series != expected_series:
            problems.append(
                f"{trial.id}: recorded trace differs from source workbook and render-safe truncation"
            )
        if transform.params != {
            "exp": exp,
            "truncated_at": events["truncated_at"],
            "heater_off_lag_s": lag,
        }:
            problems.append(f"{trial.id}: trace reconstruction transform differs")
    except (KeyError, TypeError, ValueError, OSError, zipfile.BadZipFile) as exc:
        problems.append(f"{revealed.id}: outcome source reconstruction failed: {exc}")
    return problems


def ingest(
    ledger,
    cid,
    sealed_store,
    *,
    scope="all",
    include_inferred=True,
    selected=None,
    heater_off_lag_s=None,
):
    from translucent.intervention import protocol, records

    registered_lag = protocol(ledger, cid).data.get("heater_off_lag_s", 10)
    if heater_off_lag_s is None:
        heater_off_lag_s = registered_lag
    if heater_off_lag_s != registered_lag:
        raise ValueError("Heater-off lag differs from registered protocol")
    if any(
        r.data.get("kind") == "visual_corroboration"
        for r in records(ledger, cid, "reference")
    ):
        raise ValueError(
            "Campaign label audit is closed; new sealed trials require a new campaign"
        )
    store = Path(sealed_store)
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(store, 0o700)
    source = Source(
        uri="doi:10.18434/mds2-4027",
        citation=SYSTEM["citation"],
        licence=SYSTEM["licence"],
    )
    workbook = ledger.put_file(
        DATA / "Li-ion Battery Safe Intervention Time Temperature Data.xlsx",
        kind="table",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        source=source,
    )
    corroboration = visual_corroboration()
    result = []
    for t in load_dataset(heater_off_lag_s=heater_off_lag_s):
        if (
            not t["t"]
            or t["delay_after_vent_s"] is None
            or (selected is not None and t["exp"] not in selected)
        ):
            continue
        if not include_inferred and "heater_off_inferred" in t["flags"]:
            continue
        artifact = ledger.put_json(
            dict(t=t["t"], temperature_C=t["y"]),
            kind="series",
            source=source,
            transform=Transform(
                op="nist_trace_and_render_safe_truncation",
                parent_ids=[workbook.id],
                params={
                    "exp": t["exp"],
                    "truncated_at": t["truncated_at"],
                    "heater_off_lag_s": heater_off_lag_s,
                },
            ),
        )
        safe_flags = [
            f for f in t["flags"] if f in ("heater_off_inferred", "suspect_soc")
        ]
        identity = getattr(ledger, "intervention_identity", None)
        salt = identity.salt() if identity is not None else secrets.token_hex(32)
        payload = dict(
            outcome=t["outcome"],
            salt=salt,
            t_ignition=t["t_ignition"],
            truncated_at=t["truncated_at"],
            flags=t["flags"],
            event_sources=t["event_sources"],
            simulated=False,
            outcome_source=SYSTEM["citation"],
            corroboration=corroboration.get(t["exp"]),
            corroboration_sources=visual_provenance(),
            corroboration_source="visual/out/task1_labels.csv; image/vector category equality and binary trace agreement; no-trace is unavailable",
        )
        rec = record(
            ledger,
            cid,
            "trial",
            dict(
                scope=scope,
                trial_key=t["trial_key"],
                exp=t["exp"],
                condition={"format": t["format"], "soc": t["soc"]},
                chemistry=t["chemistry"],
                capacity_mAh=t["capacity_mAh"],
                delay_after_vent_s=t["delay_after_vent_s"],
                series_artifact=artifact.id,
                t_heater_on=t["t_heater_on"],
                t_vent=t["t_vent"],
                t_heater_off=t["t_heater_off"],
                heater_off_lag_s=heater_off_lag_s,
                event_sources={
                    k: v
                    for k, v in t["event_sources"].items()
                    if k in ("t_heater_on", "t_vent", "t_heater_off")
                },
                flags=safe_flags,
                seal_version=2,
                sealed_hash=seal(payload, salt, version=2),
            ),
        )
        path = store / (hashlib.sha256(rec.id.encode()).hexdigest() + ".json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(canonical(payload))
        result.append(rec)
    return result


class AlwaysEscalate:
    """The registered frozen comparator that the agent strategies did not beat."""

    name = "always-escalate"

    def propose(self, history, remaining):
        return [("escalate", {})] * max(remaining, 0)


class NistHeatAdapter:
    """NIST behind the system adapter boundary.

    Every method delegates to the module functions above, so the intervention
    engine's outputs are unchanged. Results are revealed from the sealed store,
    never executed.
    """

    metadata = SYSTEM

    def __init__(self):
        from translucent.intervention_actions import DEFAULT_COSTS
        from translucent.systems.base import (
            ActionSpec,
            Parameter,
            Quantity,
            SystemSpec,
        )

        self.costs = DEFAULT_COSTS

        def action(name, label, parameters=()):
            return ActionSpec(
                name=name,
                label=label,
                executor="retrospective",
                parameters=parameters,
                produces=("temperature", "outcome", "t_ignition"),
                deterministic=True,
                destructive=False,
            )

        self.spec = SystemSpec(
            id=SYSTEM["id"],
            version="1",
            title=SYSTEM["title"],
            description=SYSTEM["decision_point"],
            quantities=(
                Quantity("temperature", "degC", kind="series", label=SYSTEM["sensor"]),
                Quantity(
                    "outcome",
                    "",
                    kind="category",
                    categories=("rescued", "delayed_ignition", "ignited"),
                ),
                Quantity("t_ignition", "s", label="Estimated ignition time"),
            ),
            actions=(
                action("stand_down", "Stand down: stopping heat is enough"),
                action("escalate", "Escalate: suppress or evacuate"),
                action("abstain", "Abstain: defer to a person"),
                action(
                    "observe",
                    "Watch the trace, then decide",
                    (Parameter("observe_s", "s", choices=(30, 60), integer=True),),
                ),
            ),
            citation=SYSTEM["citation"],
            licence=SYSTEM["licence"],
        )

    def code_digest(self):
        from translucent.systems.base import file_digest

        return file_digest(Path(__file__))

    def check_params(self, action, params):
        from translucent.systems.base import check_params

        return check_params(self.spec, action, params)

    def configuration(self, action, params):
        return {"action": action, **{k: params[k] for k in sorted(params)}}

    def estimate_cost(self, action, params):
        """Protocol cost units of the action itself; outcome costs come from scoring."""
        from translucent.systems.base import Cost

        unit = {
            "abstain": self.costs["abstain"],
            "observe": self.costs["watch_per_second"] * params.get("observe_s", 0),
        }.get(action, 0.0)
        return Cost(other=(("decision_cost", float(unit)),))

    def execute(self, action, params, workdir):
        from translucent.systems.base import NotExecutable

        raise NotExecutable("NIST outcomes are revealed from the sealed store")

    def check_result(self, action, params, table):
        outcome = table.scalars.get("outcome")
        categories = self.spec.quantities[1].categories
        if outcome is not None and outcome[0] not in categories:
            return [f"outcome must be one of {list(categories)}"]
        return []

    def procedure(self, action, params):
        return None

    def baseline_policy(self):
        return AlwaysEscalate()

    def evidence_problems(self, ledger, campaign_id):
        # Per-trial checks run inside intervention verify via outcome_evidence_problems.
        return []

    def load_trials(self, heater_off_lag_s=10):
        return load_dataset(heater_off_lag_s=heater_off_lag_s)

    def ingest(self, ledger, cid, sealed_store, **kw):
        return ingest(ledger, cid, sealed_store, **kw)

    def outcome_evidence_problems(self, ledger, trial, revealed, records):
        return outcome_evidence_problems(ledger, trial, revealed, records)

    def corroboration_rows(self, rows, real_experiments=None):
        return corroboration_rows(rows, real_experiments)

    def register_closing_evidence(self, ledger, cid):
        return register_visual_evidence(ledger, cid)

    def condition_grid(self):
        return VENT_MEANS

    def continuous_traces(self):
        return [t for t in load_dataset() if t["delay_after_vent_s"] is None]
