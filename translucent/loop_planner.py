"""The loop planner: Meta's model chooses the next experiment; code and the solver do every calculation (design §2.8).

A planner works in turns. A turn starts when something it has not seen happens (the campaign starts, a result or a
failure arrives, a person rejects or cancels a request, amends the objective or writes a note) and ends at the first
of: a proposal, `wait` or `ask_person`, `conclude`, the step cap, the model budget, a stall or garbled calls. Its
context is rebuilt from the ledger every turn (`planner_context`), so a restart loses nothing and no hidden memory
exists. Every step is a `planner` loop record (turn_started, model_request, tool_call, tool_result, turn_finished)
written by the planner's agent actor; a turn a crash left open is closed by the next one as `interrupted`.

What the planner may do is generated from `loop_service.planner_tools()`: it reads the campaign, defines derived
measurements (computed by ops/1, never typed), proposes and cancels its own requests, and concludes (the outcome is
computed). It cannot approve, reject, revise, submit a result or amend the objective: those tools are never offered,
LoopService refuses them for an agent actor, and loop admission refuses them a third time.

Planner kinds (objective.planner.kind), one turn loop for all of them:
  muse              Muse Wonder through a metered ChatSession (intervention_chat), with an exact replay tape per turn
  muse_no_feedback  the same model; one turn proposes the whole experiment budget and it never sees a result
  domain_baseline   DomainBaselinePlanner: the adapter's deterministic baseline_policy(), no model
  scripted          ScriptedLoopPlanner: scripted replies, for tests and offline demos
  external          an MCP client plans itself; never scheduled here

An investigation from the engineer's own records (objective.uploads, translucent.records) adds three tools: read an
uploaded file (inspect_upload), import it with a manifest the planner writes (import_records, refused by the importer
with file, line and column), list a record's computed segments (list_segments) and run its own analysis of the
uploads in the network-blocked, credential-free sandbox (run_analysis; what it prints is for reading, never a fit
input); the context then separates the instrument facts, the model assumptions, the engineer's constraints and what
is missing. Campaigns without uploads see exactly the tools, prompt and context they always had.

`planner_tick` is the trigger a runner thread or the CLI calls; `dry_run` estimates a model campaign's cost without
contacting a provider. Lessons from memory (S8) reach the context through the `lessons` hook of `planner_context`.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from pydantic import ValidationError

from translucent import chain, datasets, records
from translucent.intervention_chat import ChatSession, Settings, StopCampaign, assistant_message, worst_case_cost
from translucent.intervention_errors import RecordingError
from translucent.ledger import AdmissionError, Ledger
from translucent.locks import ExclusiveFileLock
from translucent.loop import (AMENDED, PENDING, REFERENCE, REGISTRATION, NotAllowed, NotFound, _baseline_record,
                              adapter_for, campaign_ids, current_objective, experiments_used, request_states)
from translucent.loop_compare import objective_kind
from translucent.loop_contracts import ObjectiveData
from translucent.loop_service import CAPABILITIES, Actor, LoopService, planner_tools
from translucent.recordings import NoRecording
from translucent.scrub import scrub, scrub_strings
from translucent.runtime import TEMPLATE

STEP_CAP = 40               # tool calls per turn
FULL_REQUESTS = 6           # the latest requests shown in full; older ones by state only (design §4 risk 10)
NOTES = 10                  # the latest people's notes, questions and answers shown
SERIES_POINTS = 200         # read_series returns at most this many points to the model
STALL_REPLIES = 3           # consecutive replies that ran no tool before the turn stops
AUTOMATIC = frozenset({"muse", "muse_no_feedback", "domain_baseline", "scripted"})
CLOSER = "system:planner"   # closes a turn its process left open

# Loop records that start a turn, and campaign events that do. The planner's own records never do.
TRIGGER_RECORDS = frozenset({"objective", "amendment", "result", "failure", "rejection", "cancellation"})
TRIGGER_EVENTS = frozenset({"human.note", "question.answered"})
# Statuses after which a turn is not re-run until something new happens. An interrupted turn is re-run.
_READER = Actor("system", "planner", "planner")


# ------------------------------------------------------------------ the tools, generated from the capability table

# Planner tool name for each capability a planner uses in a turn. Capabilities of planner_tools() that a turn does not
# use (systems, datasets, dataset, campaigns, import_dataset, open_campaign) are left out: a turn plans one campaign
# and never reads another's records.
TOOL_FOR = {"view": "get_view", "series": "read_series", "request": "get_request",
            "define_measurement": "define_measurement", "propose": "propose_experiment",
            "cancel": "cancel_request", "conclude": "conclude"}
# Turn control: these write nothing but the planner's own receipt (ask_person also posts the question as an event).
CONTROL_TOOLS = ("ask_person", "wait")
_OPERATORS = ["final", "initial", "max", "min", "mean", "value_at", "time_to_threshold", "delta", "slope", "integral",
              "duration_where"]


# An investigation from records: reading the uploads (the campaign's view capability) and importing them
RECORDS_TOOLS = {"inspect_upload": "view", "import_records": "import_dataset", "list_segments": "view",
                 "run_analysis": "view"}
ANALYSIS_CODE_CHARS = 20000
ANALYSIS_STDOUT_CHARS, ANALYSIS_STDERR_CHARS, ANALYSIS_OUTPUT_CHARS = 6000, 3000, 4000
# run_analysis's sandbox: translucent.sandbox.runner.run_untrusted (Modal, network-blocked, no credentials); tests and
# evaluations replace it
ANALYSIS_RUNNER = None


def _analysis_runner():
    if ANALYSIS_RUNNER is not None:
        return ANALYSIS_RUNNER
    from translucent.sandbox.runner import run_untrusted
    return run_untrusted


def from_records(obj: ObjectiveData | None) -> bool:
    return bool(obj is not None and getattr(obj, "uploads", None))


def offered_tools(obj: ObjectiveData | None = None) -> dict[str, str | None]:
    """Planner tool name -> the capability it uses (None for turn control), from planner_tools(); with the records
    tools when the objective starts from uploads."""
    out: dict[str, str | None] = {TOOL_FOR[c]: c for c in planner_tools() if c in TOOL_FOR}
    if from_records(obj):
        out |= RECORDS_TOOLS
    return out | {name: None for name in CONTROL_TOOLS}


def _prediction_schema() -> dict:
    return {"type": "object", "description": "quantity -> {value, lo, hi, unit}: your sealed forecast",
            "additionalProperties": {"type": "object", "required": ["value", "lo", "hi", "unit"],
                                     "properties": {"value": {"type": "number"}, "lo": {"type": "number"},
                                                    "hi": {"type": "number"}, "unit": {"type": "string"}}}}


def allowed_action(obj: ObjectiveData, action: str) -> bool:
    """objective.planner.actions, when the registering person set it, limits the actions the planner may propose."""
    only = obj.planner.get("actions")
    return not isinstance(only, list) or action in only


def tool_schemas(obj: ObjectiveData) -> list[dict]:
    """Chat-completions function schemas for the offered tools, with the system's actions and series named."""
    spec = obj.system.get("spec") or {}
    actions = [a["name"] for a in spec.get("actions", []) if a.get("executor") != "retrospective" and
               allowed_action(obj, a["name"])]
    series = [q["name"] for q in spec.get("quantities", []) if q.get("kind") == "series"]
    params = {
        "get_view": ({}, [], "The campaign state as at the start of the turn, rebuilt from the ledger now."),
        "read_series": ({"ref": {"type": "string", "description": "a request id, a result id or a baseline "
                                                                  "'dataset/record' ref"},
                         "quantity": {"type": "string", "enum": series},
                         "window": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                                    "description": "[start, end] in record seconds"},
                         "max_points": {"type": "integer", "maximum": SERIES_POINTS}},
                        ["ref", "quantity"], None),
        "get_request": ({"request_id": {"type": "string"}}, ["request_id"], None),
        "define_measurement": ({"name": {"type": "string", "description": "short snake_case name"},
                                "spec": {"type": "object", "required": ["op", "channel", "unit"],
                                         "properties": {"op": {"type": "string", "enum": _OPERATORS},
                                                        "channel": {"type": "string", "enum": series},
                                                        "window": {"type": "array", "items": {"type": "number"}},
                                                        "threshold": {"type": "number"},
                                                        "direction": {"type": "string",
                                                                      "enum": ["rising", "falling"]},
                                                        "at_s": {"type": "number"},
                                                        "unit": {"type": "string", "description": "the unit the "
                                                                 "operator yields for the channel"}}}},
                               ["name", "spec"],
                               "Define a derived measurement over a recorded series (ops/1). Translucent applies "
                               "it to every baseline and completed result that has the channel and records the "
                               "values; you never type a value."),
        "propose_experiment": ({"action": {"type": "string", "enum": actions},
                                "params": {"type": "object", "description": "parameter -> number, in the "
                                                                            "system's units, inside its bounds"},
                                "hypothesis": {"type": "string", "description": "the mechanism you think limits "
                                                                                "the target here"},
                                "prediction": _prediction_schema(),
                                "rationale": {"type": "string", "description": "why this experiment, from the "
                                                                               "evidence"},
                                "expected_effect": {"type": "string", "description": "which decision changes "
                                                                                     "with the result"},
                                "evidence_ids": {"type": "array", "items": {"type": "string"},
                                                 "description": "ids from this campaign's state only"}},
                               ["action", "params", "hypothesis", "prediction", "rationale", "expected_effect"],
                               "Request an experiment. A person approves it before it runs. Predict the target "
                               "and every constraint quantity the action produces, with value, lo and hi."),
        "cancel_request": ({"request_id": {"type": "string"}, "reason": {"type": "string"}},
                           ["request_id", "reason"], None),
        "conclude": ({"text": {"type": "string", "description": "what was found, with the ids it rests on"},
                      "unresolved_reasons": {"type": "array", "items": {"type": "string"}}},
                     ["text"], "End the campaign. The outcome is computed from the comparisons; you write the text."),
        "ask_person": ({"question": {"type": "string"}}, ["question"],
                       "Ask the engineer a question; the turn ends and the answer arrives as a note."),
        "inspect_upload": ({"file": {"type": "string", "description": "an uploaded file's name"},
                            "start_line": {"type": "integer", "minimum": 1}, "lines": {"type": "integer",
                                                                                       "maximum": records.MAX_LINES},
                            "header_row": {"type": "integer", "minimum": 0, "description": "lines before the header "
                                           "(as in a manifest); with it, Translucent computes every column's "
                                           "statistics under that header"},
                            "delimiter": {"type": "string"}, "decimal": {"type": "string", "enum": [".", ","]},
                            "skip_rows": {"type": "integer", "minimum": 0}},
                           ["file"], "Read raw lines of an uploaded file; with header_row, also the computed "
                                     "statistics of every column under that header. Nothing is interpreted."),
        "import_records": ({"manifest": {"type": "object", "description": "an import v2 manifest "
                                         "(manifest_version 2): id, title, author, citation, licence, records "
                                         "with specimen, configuration, conditions, time, tables, provenance"},
                            "missing_information": {"type": "array", "items": {
                                "type": "object", "required": ["item", "why_it_matters"],
                                "properties": {"item": {"type": "string"}, "why_it_matters": {"type": "string"}}},
                                "description": "what the files and documentation do not establish"}},
                           ["manifest", "missing_information"],
                           "Import uploaded files with the manifest you write. The importer refuses a wrong one "
                           "with the file, line and column; correct it and import again. Files come only from "
                           "this campaign's uploads."),
        "list_segments": ({"record": {"type": "integer", "minimum": 1}}, ["record"],
                          "Every segment Translucent computed for imported record n (rest, constant current, "
                          "other), with measured current, voltage, temperatures and chamber."),
        "run_analysis": ({"code": {"type": "string", "description": "Python 3.12 with numpy, scipy and pandas; the "
                                   "uploads are at /work/inputs/<name>; print what you need"},
                          "files": {"type": "array", "items": {"type": "string"},
                                    "description": "the uploads to give it (default: all)"},
                          "outputs": {"type": "array", "items": {"type": "string"},
                                      "description": "files it writes to /work/outputs/ to return (text)"},
                          "timeout_s": {"type": "number", "minimum": 5, "maximum": 120}},
                         ["code"], "Run your own analysis of the uploads in a fresh sandbox with no network and no "
                                   "credentials. What it prints is for your reading; fits, checks and designs take "
                                   "their inputs only from imported records and tests."),
        "wait": ({"reason": {"type": "string"}}, [],
                 "End the turn with nothing to do until something new happens."),
    }
    out = []
    for name, capability in offered_tools(obj).items():
        properties, required, text = params[name]
        description = text or CAPABILITIES[capability].summary
        out.append({"type": "function", "function": {
            "name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required}}})
    return out


# ------------------------------------------------------------------ the prompt

PROMPT = """You plan the experiments of an engineering campaign in Translucent. A person registered the objective: a \
target to improve and constraints that must hold. You decide what to measure, which experiment to run next, when to \
revise your explanation and when to stop. Deterministic code and the system's solver do every calculation; people \
approve experiments and run physical tests.

Each turn you receive the campaign state, rebuilt from the ledger: the objective and its constraints with their \
versions, the baseline, every completed result with its constraint checks, prediction error and comparison, pending \
requests, the remaining budget, the system's actions with their bounds and validity range, and people's notes and \
amendments. "turn.new_since_last_turn" says what changed. You remember nothing between turns beyond this state.

How you work
- Start from the evidence. Say which constraint you expect to bind and why, using numbers from the state.
- Every proposal states a hypothesis (the mechanism you think limits the target) and a quantitative prediction, with \
value, lo and hi, for the target and every constraint quantity the action produces. The prediction is sealed with \
the request and later compared with the result.
- When a result arrives, compare it with your prediction first. If it fell outside your interval, say what you now \
believe and why, and let that change the next experiment.
- When the objective has a new version, re-check every earlier result against the current constraints: an earlier \
candidate may no longer pass.
- If the evidence you need is a quantity nobody has measured yet (a fit, a calibration, a value derived from a \
series), get it: run the action that produces it, or define a measurement over a recorded series. Translucent's \
operators compute measurements; you never type a value.
- Prefer the experiment whose result would most change your next decision. Stay inside the parameter bounds; \
outside the stated validity range, say the result would be an extrapolation.
- Propose one experiment; the turn then ends. You are called again when something new happens: a result, a failure, \
a rejection, an amendment or a note. Call wait when nothing should be done until then.
- Conclude when the budget is spent, when no remaining experiment could change the decision, or when no candidate \
can meet the constraints, and name any unresolved reasons. Translucent computes the outcome; you write the text.

What you may not do
- You cannot approve, reject or revise a request, submit or edit a result, amend the objective or its constraints, \
or change the budget. Those belong to people and to Translucent.
- Never state a measured number that is not in the campaign state or a tool result, and never present a prediction \
as a measurement.
- Cite evidence only by ids that appear in this campaign's state. Ids from any other campaign are refused.
- Use tool calls. Text that looks like a tool call does not run."""

NO_FEEDBACK = """

This campaign is the no-feedback arm. Propose all {n} experiments now, in this one turn, each with its hypothesis \
and prediction. You will not see their results and will not be called again."""


# loop-compare/3 objectives (milestone 2). A loop-compare/1 or /2 campaign keeps the prompt it ran with, so its
# recorded turns replay byte for byte.
OBJECTIVE_KINDS = {
    "fastest_feasible": """

The objective
- Find the fastest protocol that meets every constraint of the current objective version. Meeting the constraints \
comes first: a result that breaks one is infeasible, however fast it is.
- Speed is judged against a reference that meets the same constraints: the baseline when it does, otherwise the \
best single-step protocol found by the system's reference search (requests marked as reference-search runs, which \
the system runs and which do not count against your budget). A failing baseline is never a speed reference.
- Say "meets the constraints" for a result that only recovers feasibility, and "faster than the reference" only \
when its comparison says so. No result is proven to be the fastest possible: never call one optimal.""",
    "hard_target": """

The objective
- Meet the hard target (objective.target.limit) while meeting every constraint of the current objective version. \
The target is fixed: only an engineer's amendment changes it. Never relax it, and never report a result that misses \
it as meeting the objective.
- Meeting the constraints comes first: a result that breaks one is infeasible, however fast it is.
- Say "meets the constraints" for a result that only recovers feasibility; never call a result optimal.""",
}


RECORDS_GUIDANCE = """This campaign starts from the engineer's own records: raw files (investigation.uploads) and \
their documentation (investigation.documentation), not a prepared dataset. Nothing is imported until you import it.

Intake
- Read each file with inspect_upload: its raw lines (preamble, header, first rows), then the column statistics \
Translucent computes under the header row you name. Establish from the files and the documentation the units, the \
time base, which column is which quantity, the sign convention of the current, which cell and fixture each record \
comes from, and the chamber temperature.
- Import with import_records. The manifest holds instrument facts only: what the files or the documentation state. \
Name system quantities where they apply (surface_temperature for the cell surface thermocouple, current for the cell \
current, voltage for the terminal voltage, chamber_temperature for the chamber or ambient sensor) and lab:<name> \
for anything else; give units as recorded (Translucent converts them). Give an uncertainty only where the \
documentation states one. Use the system pybamm-fastcharge-lab.
- missing_information lists what the files and documentation do not establish and the work needs (a calibration, \
thermocouple placement, the current's direction, a test's start state, a chamber log). Never fill a gap in the \
manifest by assumption; if a missing fact blocks the work, ask the engineer.
- A refused manifest names the file, line and column: correct it and import again.
- When a question needs a calculation no tool makes, write it in run_analysis (a sandbox with the uploads, numpy, \
scipy and pandas, no network). Its printout informs you; it never becomes a fit input or a reported result.

Record tests
- Translucent divides each imported record's current into segments (rest, constant current, other) with the \
measured current, voltage, temperatures and chamber (investigation.segments lists those of at least 10 min; \
list_segments gives all). A record test (record_test) is one constant-current segment. You state whether it is a \
discharge or a charge (Translucent checks the voltage) and the state of charge at its start: that is your \
assumption, so cite the evidence for it (for example a constant-voltage charge to 4.2 V and a rest before it).
- Record tests and lab tests share one numbering, in the order their results arrive (investigation.tests); \
fit_thermal and check_model name tests by it. Keep some tests out of a fit to check it.

Model and design
- Model version 0 is the parameter set as shipped, calibrated to no fixture. The records come from their own \
fixture; the objective's fixture may differ, so a fit to the records is evidence, not proof, about the next test.
- Designs come only from optimize_protocol: the numerical controller searches; you never type a design. A lab test \
(lab_charge, in the simulated lab, computational; bench_charge, on a real bench, run by a person) is the next \
experiment; when its result arrives, compare it with the forecast and revise the model or the design if the \
evidence warrants it.
- A design is a recommendation only when its model version stands behind it: a fit that met the adequacy rule and \
a check_model on tests it did not fit that met the same rule, with no check failing it. Translucent enforces this: \
a design under any other version is withheld from the comparison with the limits (its result's model_standing says \
why) and is a lead, not a result. When no family the system offers explains the records, say so and conclude with \
that as the unresolved dependency (what measurement or model would settle it), rather than recommend a design.
- Keep three things apart in what you write: instrument facts (the records and their documentation), model \
assumptions (families, start states, what a fit cannot identify) and the engineer's constraints (the objective). \
You can change the second, never the first or the third."""


def system_prompt(obj: ObjectiveData, remaining: int | None = None) -> str:
    prompt = PROMPT + OBJECTIVE_KINDS.get(objective_kind(obj), "")
    guidance = ((obj.system.get("spec") or {}).get("objective_template") or {}).get("planner_guidance")
    if isinstance(guidance, str) and guidance:      # the system's own working rules, frozen with its spec
        prompt += "\n\n" + guidance
    if from_records(obj):
        prompt += "\n\n" + RECORDS_GUIDANCE
    if obj.planner.get("kind") == "muse_no_feedback":
        return prompt + NO_FEEDBACK.format(n=remaining if remaining is not None else obj.budget.get("experiments"))
    return prompt


# ------------------------------------------------------------------ the context

def _stated(v) -> bool:
    """Worth showing: not None, False, empty text or an empty list (0 and 0.0 are stated values)."""
    return v is not None and v is not False and v != "" and v != [] and v != ()


def _shown(v):
    """The context as the model reads it: a threshold crossing not reached in its window becomes words ("not reached
    within [t0, t1] s"), never a number or a bare null."""
    if isinstance(v, dict):
        if v.get("value") is None and isinstance(v.get("not_reached_within"), (list, tuple)):
            t0, t1 = v["not_reached_within"]
            rest = {k: _shown(x) for k, x in v.items() if k not in ("value", "h", "not_reached_within")}
            return rest | {"value": f"not reached within [{t0:g}, {t1:g}] s"}
        return {k: _shown(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_shown(x) for x in v]
    return v


def _comparison(rec) -> dict:
    """A comparison record as the planner reads it: the objective's values for the candidate, its constraint checks
    and the target against the baseline, with the verdict and reasons."""
    d = rec.data
    t = d["target"]
    return {"id": rec.id, "objective_version": d["objective_version"], "basis": d["basis"], "verdict": d["verdict"],
            "values": d["candidate"]["values"],
            "target": {k: t[k] for k in ("quantity", "unit", "baseline", "candidate", "delta", "better")},
            "constraints": [{k: c[k] for k in ("quantity", "op", "limit", "unit", "value", "h", "ok",
                                               "not_reached_within") if k in c} for c in d["constraints"]],
            "reasons": list(d["reasons"])}


def planner_context(L: Ledger, cid: str, *, turn: dict | None = None, full_requests: int = FULL_REQUESTS,
                    lessons: Callable[[Ledger, str, ObjectiveData], list[dict] | None] | None = None,
                    no_results: bool = False) -> dict:
    """What the planner sees, rebuilt from the ledger every turn: the campaign's LoopView (loop_view.view through
    LoopService.view) without its clock fields, plus what a planner needs beside it: each constraint's version, the
    baseline records' configurations, each result's latest comparison (its objective values and constraint checks),
    the best candidate that passes every constraint, notes, the budget and the system's actions with their bounds
    and validity. No wall-clock times, so a replayed turn sends the same bytes. The latest `full_requests` requests
    (and every pending one) are in full; older ones by state only. `lessons(L, cid, objective)` is the hook S8's
    scoped memory fills; `no_results` hides every result (the no-feedback arm)."""
    view = LoopService(L, _READER).view(cid)
    recs = chain.records(L, cid)
    obj = current_objective(L, cid, recs)
    states = request_states(L, cid, recs)
    spec = obj.system.get("spec") or {}
    try:
        adapter = adapter_for(obj)
    except KeyError:
        adapter = None
    validity = getattr(adapter, "validity", None)
    details = getattr(adapter, "result_details", None)      # a system's own summary of what a result found
    bound = (spec.get("objective_template") or {}).get("bound_parameters") or {}

    # the objective, each constraint with the version since which it holds as it stands
    since, target_since = {}, 1
    for rec in recs:
        if rec.type in ("objective", "amendment"):
            version = 1 if rec.type == "objective" else rec.data["version"]
            if rec.type == "amendment" and rec.data.get("target") is not None:
                target_since = version
            for c in rec.data.get("constraints") or []:
                key = (c["quantity"], c["op"])
                if since.get(key, (None, None))[1] != (c["limit"], c["unit"]):
                    since[key] = (version, (c["limit"], c["unit"]))
    objective = dict(view["objective"])
    legacy = objective_kind(obj) == "legacy"
    if legacy:              # loop-compare/1 and /2: the context they were recorded with, so their turns replay
        objective = {k: x for k, x in objective.items() if k not in ("kind", "summary", "statement_version")}
    objective["target"] = dict(objective["target"], since_version=target_since)
    objective["constraints"] = [dict(c, since_version=since[(c["quantity"], c["op"])][0])
                                for c in objective["constraints"]]
    objective["quantities"] = [q.model_dump(mode="json", exclude_none=True) for q in obj.quantities]

    baseline = {"comparisons_use": view["baseline"], "records": []}
    for ref in obj.baseline:
        record = _baseline_record(L, obj, ref)
        if record is not None:
            baseline["records"].append({"ref": ref, "configuration": dict(record.configuration),
                                        "configuration_units": dict(record.configuration_units),
                                        "conditions": dict(record.conditions),
                                        "series": [{"quantity": c.quantity, "unit": c.unit, "n": c.n}
                                                   for c in record.channels]})

    current, older = {}, {}                       # request id -> its latest comparison (current version first)
    for rec in recs:
        if rec.type == "comparison":
            rq = rec.data["candidate"]["request_id"]
            (current if rec.data["objective_version"] == obj.version else older)[rq] = rec

    views = {r["request_id"]: r for r in view["requests"]}
    order = sorted(states.values(), key=lambda s: s.versions[0].seq)
    full = {s.request_id for s in order[-full_requests:]} if full_requests > 0 else set()
    full |= {s.request_id for s in order if s.state in PENDING}
    requests, incumbent = [], None
    for st in order:
        v = views[st.request_id]
        cmp = current.get(st.request_id) or older.get(st.request_id)
        if cmp is not None and not no_results and cmp.data["objective_version"] == obj.version:
            t = cmp.data["target"]
            if t["candidate"] is not None and all(c["ok"] is True for c in cmp.data["constraints"]):
                key = t["candidate"] if obj.target.direction == "minimize" else -t["candidate"]
                if incumbent is None or key < incumbent["_key"]:
                    incumbent = {"_key": key, "request_id": st.request_id, "comparison_id": cmp.id,
                                 "value": t["candidate"], "unit": t["unit"], "verdict": cmp.data["verdict"]}
        hidden = no_results and st.state not in PENDING
        if st.request_id not in full:
            requests.append({"request_id": st.request_id, "state": "not shown" if hidden else st.state,
                             "action": st.action,
                             **({"verdict": cmp.data["verdict"]} if cmp is not None and not no_results else {})})
            continue
        d = st.data
        item = {k: v[k] for k in ("request_id", "version", "state", "action", "executor", "params", "proposed_by",
                                  "approved_by", "rationale", "prediction")}
        item |= {"expected_effect": d.expected_effect, "evidence_ids": list(d.evidence_ids),
                 "objective_version": d.objective_version, "cost": d.cost}
        if st.proposed_by == REGISTRATION:
            item["note"] = "re-run of a baseline configuration on the solver, pre-approved at registration"
        elif st.proposed_by == REFERENCE:
            item["note"] = ("a run of the system's reference search (the fastest single-step protocol meeting the "
                            "constraints, by bisection); not counted against the experiment budget")
        if callable(validity):
            try:
                item["validity"] = validity(d.action, dict(d.params))
            except (KeyError, ValueError, TypeError):
                pass
        if hidden:
            item["state"] = "not shown"
            requests.append(item)
            continue
        if v["result"] is not None:
            r = v["result"]
            item["result"] = {"result_id": r["result_id"], "basis": r["basis"], "measurements": r["measurements"],
                              "categories": r["categories"], "series": [s["quantity"] for s in r["series"]],
                              "prediction_error": r["prediction_error"]}
            if callable(details):
                try:
                    if (found := details(L, st)) is not None:
                        item["result"]["details"] = found
                except (KeyError, ValueError, TypeError, OSError):
                    pass
        if bound.get(st.action):          # set by Translucent from the ledger: the agent sees them, never types them
            item["bound_params"] = {k: item["params"].pop(k) for k in bound[st.action] if k in item["params"]}
        if cmp is not None:
            item["comparison"] = _comparison(cmp)
        if st.failure is not None:
            item["failure"] = {"error": st.failure.data["error"], "retryable": st.failure.data["retryable"]}
        if st.closed_by is not None:
            item["closed"] = {"by": st.closed_by.actor, "reason": st.closed_by.data.get("reason", "")}
        if v["rejections"]:
            item["refused_uploads"] = [x["reasons"] for x in v["rejections"]]
        requests.append(item)
    if incumbent is not None:
        incumbent.pop("_key")

    notes = [{"seq": e.seq, "type": e.type, "actor": e.actor, "text": e.detail or e.title}
             for e in L.events(cid) if e.type in ("human.note", "question.asked", "question.answered")][-NOTES:]
    used = experiments_used(states)
    limit = obj.budget.get("experiments")
    template_bound = (spec.get("objective_template") or {}).get("bound_parameters") or {}
    system = {"id": obj.system["id"], "version": obj.system["version"], "title": spec.get("title"),
              "description": spec.get("description"), "solver": spec.get("solver"),
              "quantities": [{k: q[k] for k in ("name", "unit", "kind", "label", "tolerance", "categories")
                              if _stated(q.get(k)) and not (k == "tolerance" and q[k] == 0)}   # 0: no bound declared
                             for q in spec.get("quantities", [])],
              "actions": [{"name": a["name"], "label": a["label"], "executor": a["executor"],
                           "deterministic": a.get("deterministic"), "produces": a.get("produces"),
                           "parameters": [{k: p[k] for k in ("name", "unit", "lo", "hi", "choices", "integer",
                                                              "label") if _stated(p.get(k))}
                                          for p in a.get("parameters", [])
                                          if p["name"] not in (template_bound.get(a["name"]) or ())],
                           **({"procedure": a["procedure"]} if a.get("procedure") else {})}
                          for a in spec.get("actions", []) if a.get("executor") != "retrospective" and
                          allowed_action(obj, a["name"])]}
    out = {"campaign": {"id": cid, "title": view["campaign"]["title"], "status": view["campaign"]["status"],
                        "planner": obj.planner},
           "turn": turn or {}, "objective": objective, "system": system,
           "baseline": baseline,
           "requests": requests, "next_action": view["next_action"], "incumbent": incumbent,
           "measurements_defined": [{"id": r.id, "name": r.data["name"], **r.data["spec"],
                                     "defined_by": r.data["defined_by"]} for r in recs if r.type == "measurement"],
           "notes": notes,
           "budget": {"experiments_used": used, "experiments_budget": limit,
                      "experiments_remaining": None if limit is None else max(limit - used, 0),
                      "model_usd_spent": round(model_spend(L, cid, recs), 6),
                      "model_usd_budget": float(obj.budget.get("model_usd", 0.0) or 0.0)},
           "conclusion": view["conclusion"], "events_cursor": view["events_cursor"]}
    if not legacy:          # feasibility, speed against the reference and the objective, kept apart
        a = view["assessment"]
        out["assessment"] = {k: a[k] for k in ("headline", "feasibility", "speed", "objective", "uncertainty")}
    if from_records(obj):
        out["investigation"] = records.investigation(L, cid, obj, recs, states)
    if lessons is not None:
        found = lessons(L, cid, obj)
        if found is not None:
            out["lessons"] = found
    return _shown(out)


def model_spend(L: Ledger, cid: str, recs=None) -> float:
    """Model dollars the campaign's planner turns spent, from their model_request receipts."""
    recs = chain.records(L, cid) if recs is None else recs
    return sum(float(r.data["payload"].get("cost_usd") or 0.0) for r in recs
               if r.type == "planner" and r.data["phase"] == "model_request")


# ------------------------------------------------------------------ turns: when one is due, locks, interrupted turns

def turn_lock_path(root: Path | str, cid: str) -> Path:
    return Path(root) / "locks" / f"turn-{cid}.lock"


def _turns(recs) -> list[dict]:
    """[{turn, started: record, finished: record | None}] in order."""
    out: dict[int, dict] = {}
    for r in recs:
        if r.type != "planner":
            continue
        n = r.data["payload"].get("turn")
        if r.data["phase"] == "turn_started":
            out[n] = {"turn": n, "started": r, "finished": None}
        elif r.data["phase"] == "turn_finished" and n in out:
            out[n]["finished"] = r
    return list(out.values())


def turn_due(L: Ledger, cid: str) -> list[dict]:
    """What the planner has not seen and should act on: the triggering records and events after the context of the
    last turn that ran to an end ([] when no turn is due). A turn left open by a dead process makes one due again;
    one still running (its lock held) does not."""
    recs = chain.records(L, cid)
    if not recs or chain.concluded(L, cid):
        return []
    obj = current_objective(L, cid, recs)
    turns = _turns(recs)
    if turns and turns[-1]["finished"] is None:
        probe = ExclusiveFileLock(turn_lock_path(L.root, cid))
        if not probe.try_acquire():
            return []                                   # a turn is running now
        probe.release()
    ended = [t for t in turns if t["finished"] is not None and
             t["finished"].data["payload"].get("status") != "interrupted"]
    if obj.planner.get("kind") == "muse_no_feedback" and ended:
        return []                                       # the batch is fixed: it never sees what follows
    after = ended[-1]["started"].data["payload"]["context_seq"] if ended else 0
    planner_actors = {t["started"].actor for t in turns}
    out = []
    for e in L.events(cid, after_seq=after):
        if e.type == "loop.recorded":
            rec = e.data["record"]
            if rec["type"] in TRIGGER_RECORDS and rec["actor"] not in planner_actors:
                out.append({"seq": e.seq, "kind": rec["type"], "actor": rec["actor"], "title": e.title,
                            **({"request_id": rec["data"]["request_id"]} if "request_id" in rec["data"] else {})})
        elif e.type in TRIGGER_EVENTS and e.actor not in planner_actors:
            out.append({"seq": e.seq, "kind": e.type, "actor": e.actor, "title": e.title})
    if not out and turns and turns[-1]["finished"] is None:
        out.append({"seq": turns[-1]["started"].seq, "kind": "interrupted_turn", "actor": CLOSER,
                    "title": f"Turn {turns[-1]['turn']} stopped before it finished"})
    return out


def close_interrupted(L: Ledger, cid: str) -> list[int]:
    """Close every turn a dead process left open, as `interrupted`. Call with the turn lock held."""
    closed = []
    for t in _turns(chain.records(L, cid)):
        if t["finished"] is None:
            chain.append(L, cid, "planner",
                         {"phase": "turn_finished", "payload": {"turn": t["turn"], "status": "interrupted",
                                                                "reason": "the process running the turn stopped "
                                                                          "before it finished"}},
                         CLOSER, chain.natural_id("pln", cid, t["turn"], "closed"))
            closed.append(t["turn"])
    return closed


# ------------------------------------------------------------------ planners without a model

def _call(name: str, args: dict, n: int) -> dict:
    return {"id": f"call_{n}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, sort_keys=True)}}


def _context_of(messages: list[dict]) -> dict:
    return json.loads(messages[1]["content"]) if len(messages) > 1 else {}


class ScriptedLoopPlanner:
    """A deterministic planner for tests and offline demos: answers each model call from a script, never a model.

    `script` is a list of replies used in order, or one callable used for every call. A reply is a list of
    (tool, arguments) pairs (tool calls), a string (a reply with no tool call), a dict (a raw assistant message), or
    a callable(context, messages) returning one of those. When the list runs out, the planner calls `wait`. The
    context is the one the turn sends the model, so a script sees exactly what Muse Wonder would see."""

    def __init__(self, script: list | Callable, model: str = "scripted"):
        self.script = script if callable(script) else list(script)
        self.model = model
        self.records: list[dict] = []
        self.spent_usd = 0.0
        self.calls = 0                           # tool-call ids, unique within the planner's life
        self.seen: list[list[dict]] = []         # the messages of every call, for tests

    def reply(self, messages: list[dict]) -> dict:
        self.seen.append(json.loads(json.dumps(messages)))
        if callable(self.script):
            item = self.script
        else:
            item = self.script.pop(0) if self.script else [("wait", {"reason": "script finished"})]
        if callable(item):
            item = item(_context_of(messages), messages)
        if item is None:
            item = [("wait", {})]
        if isinstance(item, dict):
            return {"role": "assistant", **item}
        if isinstance(item, str):
            return {"role": "assistant", "content": item}
        calls = []
        for name, args in item:
            self.calls += 1
            calls.append(_call(name, args, self.calls))
        return {"role": "assistant", "content": None, "tool_calls": calls}

    def chat(self, messages: list[dict], tools: list | None = None) -> dict:
        response = self.reply(messages)
        self.records.append({"kind": "scripted", "model": self.model, "response": response, "cost_usd": 0.0})
        return response


class ScriptedChatClient:
    """A provider-shaped fake client (client.chat(**request) -> reply) driven by a ScriptedLoopPlanner, so a real
    ChatSession meters and records an exact tape without a provider. Tapes it makes say model
    "scripted-fake-client"; they are never a paid model's output."""

    MODEL = "scripted-fake-client"

    def __init__(self, planner: ScriptedLoopPlanner):
        self.planner = planner

    def chat(self, **request):
        message = self.planner.reply(request["messages"])
        tokens_in = len(json.dumps(request["messages"])) // 4
        tokens_out = len(json.dumps(message)) // 4
        return SimpleNamespace(
            model=self.MODEL,
            choices=[SimpleNamespace(message=SimpleNamespace(**message, refusal=None),
                                     finish_reason="tool_calls" if message.get("tool_calls") else "stop")],
            usage=SimpleNamespace(prompt_tokens=tokens_in, completion_tokens=tokens_out,
                                  prompt_tokens_details=None, completion_tokens_details=None))


def baseline_history(L: Ledger, cid: str) -> list[dict]:
    """A BaselinePolicy's history from the ledger, in seq order: first the registered constraints
    {"constraints": {quantity: limit}, "objective_version": 1}, then {"action", "params", "result": {quantity: value}}
    for every request but the system's runs (registration re-runs and reference-search runs) and the proposals an
    amendment withdrew before they ran (a request with no result otherwise counts as tried), and
    {"constraints": ..., "objective_version": v} where an amendment changed the constraints."""
    recs = chain.records(L, cid)
    states = request_states(L, cid, recs)
    out = []
    for rec in recs:
        if rec.type == "objective":
            out.append({"constraints": {c["quantity"]: c["limit"] for c in rec.data["constraints"]},
                        "objective_version": 1})
        elif rec.type == "amendment" and rec.data.get("constraints") is not None:
            out.append({"constraints": {c["quantity"]: c["limit"] for c in rec.data["constraints"]},
                        "objective_version": rec.data["version"]})
        elif rec.type == "request" and rec.data["version"] == 1 and rec.actor not in (REGISTRATION, REFERENCE):
            st = states[rec.data["request_id"]]
            if st.closed_by is not None and st.closed_by.actor == AMENDED:
                continue                  # superseded unrun: free to be proposed again under the new version
            entry = {"action": st.action, "params": dict(st.data.params), "state": st.state}
            if st.result is not None:
                entry["result"] = {q: m.value for q, mid in st.result.data["measurements"].items()
                                   if (m := L.get("measurement", mid)) is not None}
            out.append(entry)
    return out


class DomainBaselinePlanner:
    """The adapter's deterministic baseline policy as a planner (no model): each turn proposes what
    `policy.propose(history, remaining)` returns, waits while requests are pending, and concludes when the policy has
    nothing left or the budget is spent. It states no prediction: it is a comparator, not a forecaster.

    It proposes nothing while one of its proposals still awaits a person's approval: a turn started by something
    else (a baseline or reference-search result, a note) must not stack more proposals behind an unapproved one,
    or the budget is spent before any result can teach the policy. A policy's own batch (the Bayesian optimiser's
    initial Sobol points) still arrives in one turn. Under auto-approval nothing waits, so nothing changes there."""

    predicts = False

    def __init__(self, L: Ledger, cid: str, policy=None):
        self.L, self.cid = L, cid
        self.policy = policy if policy is not None else adapter_for(current_objective(L, cid)).baseline_policy()
        if self.policy is None:
            raise ValueError(f"{current_objective(L, cid).system['id']} has no baseline policy")
        self.model = f"baseline-{self.policy.name}"
        self.records: list[dict] = []
        self.spent_usd = 0.0

    def _reply(self, messages: list[dict]) -> dict:
        obj = current_objective(self.L, self.cid)
        states = request_states(self.L, self.cid)
        limit = obj.budget.get("experiments")
        remaining = (limit - experiments_used(states)) if limit is not None else 10 ** 6
        pending = any(s.state in PENDING for s in states.values())
        unapproved = any(s.state == "awaiting_approval" and s.proposed_by not in (REGISTRATION, REFERENCE)
                         for s in states.values())
        first = len(messages) == 2
        proposals = self.policy.propose(baseline_history(self.L, self.cid), remaining) \
            if first and remaining > 0 and not unapproved else []
        if proposals:
            calls = [("propose_experiment", {"action": action, "params": params, "prediction": {},
                                             "hypothesis": f"{self.policy.name} needs no hypothesis",
                                             "rationale": f"Next point of the deterministic {self.policy.name} "
                                                          "baseline policy.",
                                             "expected_effect": "Fixed policy: the result updates its model only."})
                     for action, params in proposals]
        elif pending:
            calls = [("wait", {"reason": "requests are pending"})]
        else:
            calls = [("conclude", {"text": f"The {self.policy.name} baseline policy finished: "
                                           + ("the experiment budget is spent." if remaining <= 0 else
                                              "it proposes no further experiment.")})]
        base = sum(len(r["response"].get("tool_calls") or []) for r in self.records) + 1
        return {"role": "assistant", "content": None,
                "tool_calls": [_call(name, args, base + i) for i, (name, args) in enumerate(calls)]}

    def chat(self, messages: list[dict], tools: list | None = None) -> dict:
        response = self._reply(messages)
        self.records.append({"kind": "deterministic", "policy": self.policy.name, "response": response,
                             "cost_usd": 0.0})
        return response


# ------------------------------------------------------------------ one turn

class _Refused(Exception):
    """A tool call the planner layer refuses before any service call; the model gets the reason."""


def prediction_problems(obj: ObjectiveData, action: str, prediction: Any) -> list[str]:
    """A proposal predicts, with value, lo and hi, the target and every constraint quantity the action produces (an
    operator quantity counts when the action produces its channel); an action producing none of them predicts at
    least one quantity it produces."""
    spec = next((a for a in (obj.system.get("spec") or {}).get("actions", []) if a["name"] == action), None)
    if spec is None:
        return []                                       # the service names the unknown action
    if not isinstance(prediction, dict):
        return ["prediction is an object: quantity -> {value, lo, hi, unit}"]
    produced = set(spec.get("produces", ()))
    defs = {q.name: q for q in obj.quantities}

    def made(q: str) -> bool:
        d = defs.get(q)
        return q in produced or (d is not None and d.source == "operator" and d.operator.channel in produced)

    needed = [q for q in dict.fromkeys([obj.target.quantity] + [c.quantity for c in obj.constraints]) if made(q)]
    out = []
    if not needed and not any(q in produced for q in prediction):
        out.append(f"predict at least one quantity {action} produces: {', '.join(sorted(produced))}")
    for q in needed:
        p = prediction.get(q)
        if not isinstance(p, dict) or any(not isinstance(p.get(k), (int, float)) or isinstance(p.get(k), bool)
                                          for k in ("value", "lo", "hi")):
            out.append(f"predict {q} with value, lo and hi (and its unit)")
        elif not p["lo"] <= p["value"] <= p["hi"]:
            out.append(f"prediction {q}: lo <= value <= hi")
    return out


class _Turn:
    def __init__(self, L: Ledger, cid: str, session, *, step_cap: int, full_requests: int, lessons, triggers):
        self.L, self.cid, self.session = L, cid, session
        self.step_cap, self.full_requests, self.lessons, self.triggers = step_cap, full_requests, lessons, triggers
        recs = chain.records(L, cid)
        self.obj = current_objective(L, cid, recs)
        self.kind = self.obj.planner.get("kind")
        self.number = len(_turns(recs)) + 1
        self.actor = Actor("agent", session.model, "planner")
        self.prior_spend = model_spend(L, cid, recs)
        self.k = 0
        self.steps = 0
        self.cursor = len(session.records)
        self.proposed: list[str] = []
        states = request_states(L, cid, recs)
        limit = self.obj.budget.get("experiments")
        self.remaining = None if limit is None else max(limit - experiments_used(states), 0)

    # receipts
    def receipt(self, phase: str, payload: dict):
        # the model's words and arguments, scrubbed like any free text before the insert-only ledger keeps them
        chain.append(self.L, self.cid, "planner", {"phase": phase, "payload": scrub_strings(payload)}, self.actor.label,
                     chain.natural_id("pln", self.cid, self.number, self.k))
        self.k += 1

    def save_requests(self):
        for i, entry in enumerate(self.session.records[self.cursor:], start=self.cursor + 1):
            payload = {k: v for k, v in entry.items() if k not in ("request", "raw_response")}
            self.receipt("model_request", {"turn": self.number, "tape_index": i, **payload})
            if float(entry.get("cost_usd") or 0.0) > 0:     # the LoopView's spend sums these, as for every campaign
                self.L.emit(self.cid, "cost.updated", self.actor.label, "Spending updated",
                            data={"usd": round(float(entry["cost_usd"]), 6), "role": "planner"})
        self.cursor = len(self.session.records)

    def context(self) -> dict:
        turn = {"number": self.number, "kind": self.kind,
                "new_since_last_turn": [{k: t[k] for k in ("seq", "kind", "actor", "title", "request_id") if k in t}
                                        for t in self.triggers]}
        return planner_context(self.L, self.cid, turn=turn, full_requests=self.full_requests, lessons=self.lessons,
                               no_results=self.kind == "muse_no_feedback")

    # the tools
    def dispatch(self, name: str, args: dict) -> dict:
        tools = offered_tools(self.obj)
        if name not in tools:
            raise _Refused(f"{name} is not a planner tool; the planner's tools are {', '.join(tools)}")
        key = f"{self.cid}/turn-{self.number}/step-{self.steps}"
        svc = LoopService(self.L, self.actor, idempotency_key=key)
        cid = self.cid
        if name == "get_view":
            return self.context()
        if name == "read_series":
            window = args.get("window")
            out = svc.series(cid, args["ref"], args["quantity"], window=tuple(window) if window else None,
                             max_points=min(int(args.get("max_points") or SERIES_POINTS), SERIES_POINTS))
            return out
        if name == "get_request":                       # without wall-clock times, so a replay sends the same bytes
            out = svc.request(cid, args["request_id"])
            return out | {"timeline": [{k: v for k, v in e.items() if k != "t"} for e in out["timeline"]]}
        if name == "define_measurement":
            res = svc.define_measurement(cid, args["name"], args["spec"])
            return {"record_id": res["record"]["id"],
                    "measurements": [{"id": m["id"], "subject": m["subject"], "value": m["value"],
                                      "unit": m["unit"], "h": m["uncertainty"]["value"]}
                                     for m in res["measurements"]]}
        if name == "propose_experiment":
            if not allowed_action(self.obj, args.get("action", "")):
                raise _Refused(f"{args.get('action')} is not among the actions this campaign's planner may propose")
            if self.kind == "muse_no_feedback" and self.remaining is not None and \
                    len(self.proposed) >= self.remaining:
                raise _Refused(f"all {self.remaining} experiments of the budget are proposed")
            if getattr(self.session, "predicts", True):
                text = [k for k in ("hypothesis", "rationale", "expected_effect")
                        if not isinstance(args.get(k), str) or not args[k].strip()]
                problems = [f"state the {k.replace('_', ' ')}" for k in text] + \
                    prediction_problems(self.obj, args.get("action", ""), args.get("prediction"))
                if problems:
                    raise _Refused("; ".join(problems))
            rationale = f"Hypothesis: {args['hypothesis'].strip()}\n\n{args['rationale'].strip()}" \
                if getattr(self.session, "predicts", True) else args["rationale"]
            res = svc.propose(cid, args["action"], args["params"], rationale=rationale,
                              prediction=args.get("prediction") or {}, expected_effect=args["expected_effect"],
                              evidence_ids=args.get("evidence_ids") or ())
            mine = "rq_" + chain.digest([cid, "request", "agent", key])[:20]
            if res["request_id"] == mine:
                self.proposed.append(mine)
            return {"request_id": res["request_id"], "version": res["version"], "state": res["state"],
                    "content_hash": res["content_hash"], "cost": res["cost"],
                    **({"duplicate_of_pending": True} if res["request_id"] != mine else {})}
        if name == "cancel_request":
            res = svc.cancel(cid, args["request_id"], args["reason"])
            return {"request_id": res["request_id"], "state": res["state"]}
        if name == "conclude":
            rec = svc.conclude(cid, args["text"], args.get("unresolved_reasons") or ())
            return {"record_id": rec["id"], "outcome": rec["data"]["outcome"],
                    "comparison_id": rec["data"]["comparison_id"]}
        if name == "inspect_upload":
            path = records.upload_path(self.L.root, cid, self.obj.uploads or [], str(args["file"]))
            return records.inspect(path, start_line=int(args.get("start_line") or 1),
                                   lines=int(args.get("lines") or 30), header_row=args.get("header_row"),
                                   delimiter=args.get("delimiter"), decimal=args.get("decimal") or ".",
                                   skip_rows=int(args.get("skip_rows") or 0))
        if name == "import_records":
            manifest, missing = args["manifest"], args["missing_information"]
            if not isinstance(manifest, dict):
                raise _Refused("send the manifest itself, as an object")
            if not isinstance(missing, list):
                raise _Refused("missing_information is a list (empty when nothing is missing)")
            if manifest.get("system") not in (None, self.obj.system["id"]):
                raise _Refused(f"the records are imported for this campaign's system, {self.obj.system['id']}")
            manifest = {**manifest, "system": self.obj.system["id"]}
            svc._allow("import_dataset")
            files = {n: records.upload_path(self.L.root, cid, self.obj.uploads or [], n)
                     for n in datasets.file_names(manifest)}
            stored = datasets.import_dataset(self.L, manifest, files)
            known = records.campaign_records(self.L, self.obj, cid)
            start = len(known) + 1 if stored.id not in records.imported(self.L, cid) else None
            return {"dataset_id": stored.id,
                    "records": [{"record": (start + i) if start else None, "ref": f"{stored.id}/{r.id}",
                                 "channels": [{"quantity": c.quantity, "unit": c.unit, "n": c.n,
                                               "t_range_s": list(c.t_range)} for c in r.channels],
                                 "ignored_columns": list(r.ignored_columns), "import_notes": list(r.import_notes)}
                                for i, r in enumerate(stored.records)],
                    "missing_information": missing}
        if name == "run_analysis":
            code = args["code"]
            if not isinstance(code, str) or not code.strip() or len(code) > ANALYSIS_CODE_CHARS:
                raise _Refused(f"code is Python text of at most {ANALYSIS_CODE_CHARS} characters")
            uploads = self.obj.uploads or []
            names = args.get("files") or [u["name"] for u in uploads]
            outputs = [str(n) for n in args.get("outputs") or []]
            inputs = {str(n): records.upload_path(self.L.root, cid, uploads, str(n)).read_bytes() for n in names}
            inputs["__outputs__.json"] = json.dumps(outputs).encode()
            run = _analysis_runner()(code, inputs, timeout_s=float(args.get("timeout_s") or 60))

            def text(b: bytes) -> str:
                try:
                    return b.decode("utf-8")[:ANALYSIS_OUTPUT_CHARS]
                except UnicodeDecodeError:
                    return f"<{len(b)} bytes, not text>"
            return {"status": run.status, "exit_code": run.exit_code, "reason": run.reason,
                    "elapsed_s": round(run.elapsed, 1), "stdout": run.stdout[-ANALYSIS_STDOUT_CHARS:],
                    "stderr": run.stderr[-ANALYSIS_STDERR_CHARS:],
                    "outputs": {n: text(b) for n, b in run.outputs.items()},
                    "input_sha256": run.input_sha256}
        if name == "list_segments":
            from translucent.systems import lgm50_lab as lab

            found = records.campaign_records(self.L, self.obj, cid)
            n = int(args["record"])
            if not 1 <= n <= len(found):
                raise _Refused(f"record must be 1 to {len(found)}" if found else "no record is imported yet")
            return {"record": n, "ref": found[n - 1][0], "segments": lab.record_segments(self.L, found[n - 1][1])}
        if name == "ask_person":
            question = scrub(str(args["question"]).strip())     # a model may echo a secret it saw
            if not question:
                raise _Refused("ask a question")
            ev = self.L.emit(cid, "question.asked", self.actor.label, "Planner asks: " + question, detail=question)
            return {"asked": True, "seq": ev.seq}
        return {"waiting": True}                        # wait

    def check_budget(self, messages: list[dict], schemas: list[dict]):
        """The campaign's model budget across turns; the session's own cap is checked again by ChatSession."""
        settings = getattr(self.session, "settings", None)
        if settings is None:
            return
        limit = float(self.obj.budget.get("model_usd", 0.0) or 0.0)
        worst = worst_case_cost(settings, messages, schemas)
        spent = self.prior_spend + self.session.spent_usd
        if spent + worst > limit + 1e-12:
            raise StopCampaign("budget", f"Next request reserves ${worst:.6f}; the campaign's model budget has "
                                         f"${max(limit - spent, 0.0):.6f} left of ${limit:.2f}")

    def run(self) -> dict:
        context = self.context()
        text = chain.canonical(context).decode()
        schemas = tool_schemas(self.obj)
        model = getattr(self.session, "model", None)
        news = ", ".join(dict.fromkeys(t["kind"].replace("_", " ").replace(".", " ") for t in self.triggers))
        self.receipt("turn_started", {"turn": self.number, "kind": self.kind, "model": model,
                                      "title": f"The planner is choosing the next step (turn {self.number}" +
                                               (f", after: {news})" if news else ")"),
                                      "context_seq": context["events_cursor"],
                                      "context_sha256": chain.digest(context), "context_chars": len(text),
                                      "triggers": self.triggers, "step_cap": self.step_cap,
                                      "tools": [s["function"]["name"] for s in schemas]})
        messages = [{"role": "system", "content": system_prompt(self.obj, self.remaining)},
                    {"role": "user", "content": text}]
        status, reason = "incomplete", "the turn ended without an outcome"
        idle: list[str] = []
        try:
            while True:
                if self.steps >= self.step_cap:
                    status, reason = "step_cap", f"Turn cap of {self.step_cap} tool calls reached"
                    break
                self.check_budget(messages, schemas)
                try:
                    response = self.session.chat(messages, schemas)
                finally:
                    self.save_requests()
                calls = response.get("tool_calls") or []
                messages.append(assistant_message(response))
                if not calls:
                    garbled = bool(TEMPLATE.search(response.get("content") or ""))
                    idle.append("garbled" if garbled else "idle")
                    if len(idle) >= STALL_REPLIES:
                        status, reason = ("garbled", f"{STALL_REPLIES} replies held tool calls as plain text") \
                            if all(x == "garbled" for x in idle) else \
                            ("stalled", f"{STALL_REPLIES} model replies contained no executable tool call")
                        break
                    messages.append({"role": "user", "content": (
                        "Your tool call arrived as plain text and did not run. Call the tool again using the "
                        "tool-call format." if garbled else
                        "Continue by calling a tool: propose_experiment, define_measurement, wait, ask_person or "
                        "conclude.")})
                    continue
                ran, ended = False, None
                for call in calls:
                    if self.steps >= self.step_cap:
                        ended = ("step_cap", f"Turn cap of {self.step_cap} tool calls reached")
                        break
                    self.steps += 1
                    function = call.get("function") or {}
                    name = function.get("name", "")
                    raw = function.get("arguments") or "{}"
                    try:
                        args = json.loads(raw) if isinstance(raw, str) else raw
                        if not isinstance(args, dict):
                            raise ValueError("tool arguments must be a JSON object")
                    except ValueError as e:
                        result = {"error": f"garbled arguments: {e}"[:2000]}
                        self.receipt("tool_result", {"turn": self.number, "step": self.steps, "tool": name,
                                                     "result": result, "garbled": True})
                    else:
                        # a well-formed call of a planner tool is progress, even when it is refused
                        ran = ran or name in offered_tools(self.obj)
                        self.receipt("tool_call", {"turn": self.number, "step": self.steps, "tool": name,
                                                   "arguments": args})
                        try:
                            result = self.dispatch(name, args)
                        except _Refused as e:
                            result = {"error": str(e)[:2000]}
                        except (NotAllowed, NotFound, AdmissionError, ValidationError, KeyError, TypeError,
                                ValueError) as e:
                            what = f"missing argument {e}" if isinstance(e, KeyError) else str(e)
                            result = {"error": f"{type(e).__name__}: {what}"[:2000]}
                        stored = result if name not in ("get_view", "read_series") or "error" in result else \
                            {"sha256": chain.digest(result), "chars": len(chain.canonical(result))}
                        self.receipt("tool_result", {"turn": self.number, "step": self.steps, "tool": name,
                                                     "result": stored})
                    messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                     "content": chain.canonical(result).decode()})
                    if "error" in result:
                        continue
                    if name == "conclude":
                        ended = ("concluded", "The planner concluded the campaign")
                        break
                    if name in ("wait", "ask_person"):
                        ended = ("waiting" if name == "wait" else "asked",
                                 str(args.get("reason") or args.get("question") or "")[:500] or
                                 "nothing to do until something new happens")
                        break
                if ended is None and self.proposed:
                    if self.kind != "muse_no_feedback" or self.remaining is None or \
                            len(self.proposed) >= self.remaining:
                        ended = ("proposed", f"Proposed {', '.join(self.proposed)}")
                if ended is not None:
                    status, reason = ended
                    break
                if ran:
                    idle = []
                else:
                    idle.append("garbled")
                    if len(idle) >= STALL_REPLIES:
                        status, reason = ("garbled", f"{STALL_REPLIES} replies ran no valid tool call") \
                            if all(x == "garbled" for x in idle) else \
                            ("stalled", f"{STALL_REPLIES} model replies contained no executable tool call")
                        break
        except StopCampaign as e:
            status, reason = e.status, e.reason
        except NoRecording as e:
            status, reason = "replay_error", str(e)
        except RecordingError as e:
            status, reason = "recording_error", str(e)
        except Exception as e:  # noqa: BLE001 - the turn records why it stopped rather than vanish
            status, reason = "controller_error", f"{type(e).__name__}: {e}"[:1000]
        try:
            self.save_requests()
        except Exception as e:  # noqa: BLE001
            status, reason = "recording_error", f"{reason}; cannot record model requests: {type(e).__name__}: {e}"
        result = {"campaign_id": self.cid, "turn": self.number, "status": status, "reason": reason[:2000],
                  "tool_calls": self.steps, "proposed": list(self.proposed),
                  "spent_usd": round(self.session.spent_usd, 12), "model": model}
        self.receipt("turn_finished", {k: v for k, v in result.items() if k != "campaign_id"} |
                     {"model_usd_total": round(self.prior_spend + self.session.spent_usd, 12)})
        return result


def run_turn(L: Ledger, cid: str, session, *, step_cap: int = STEP_CAP, full_requests: int = FULL_REQUESTS,
             lessons=None, only_if_due: bool = False) -> dict | None:
    """Run one planner turn under the campaign's turn lock. `session` is a ChatSession (Muse Wonder, metered, with a
    tape), a ScriptedLoopPlanner or a DomainBaselinePlanner. A turn a dead process left open is closed first. With
    only_if_due, nothing runs (None) unless turn_due finds something new under the lock. Returns the turn's status
    (also recorded as its turn_finished receipt), or status "busy" when another process holds the lock."""
    if step_cap <= 0:
        raise ValueError("The per-turn tool-call cap must be positive")
    lock = ExclusiveFileLock(turn_lock_path(L.root, cid))
    if not lock.try_acquire():
        return {"campaign_id": cid, "status": "busy", "reason": "another process is running this campaign's turn"}
    try:
        current_objective(L, cid)                       # NotFound for an unknown campaign
        close_interrupted(L, cid)                       # first: with no open turn, turn_due never probes our lock
        triggers = turn_due(L, cid)
        if chain.concluded(L, cid) or (only_if_due and not triggers):
            return None
        return _Turn(L, cid, session, step_cap=step_cap, full_requests=full_requests, lessons=lessons,
                     triggers=triggers).run()
    finally:
        lock.release()


def turn_session(root: Path | str, cid: str, turn: int, settings: Settings, *, replay: bool = False,
                 client=None, store: Path | str | None = None) -> ChatSession:
    """A metered ChatSession for one turn, its tape under <ledger>/planner/<cid>/turn-<n> (or `store`/turn-<n>)."""
    base = Path(store) if store is not None else Path(root) / "planner" / cid
    return ChatSession(settings, base / f"turn-{turn}", replay=replay, client=client)


def next_turn(L: Ledger, cid: str) -> int:
    return len(_turns(chain.records(L, cid))) + 1


# Muse Wonder's reasoning counts against max_tokens: 4000 cut replies off mid-turn (eight proposals in one reply need
# more), so a reply starts with 8000 and one that is cut off is asked again with up to 24000.
MAX_TOKENS, LENGTH_MAX_TOKENS = 8000, 24000


def default_session_for(*, live: bool, max_tokens: int = MAX_TOKENS, step_cap: int = STEP_CAP):
    """The `session_for` a server or the CLI passes to planner_tick: the domain baseline always runs; a model planner
    runs only with `live` (the operator's opt-in to paid calls), metered on the campaign's profile and held to what is
    left of its model budget; a scripted planner has no script outside its own process, so it is not run here."""
    def session_for(L: Ledger, cid: str, obj: ObjectiveData):
        kind = obj.planner.get("kind")
        if kind == "domain_baseline":
            return DomainBaselinePlanner(L, cid)
        if kind in ("muse", "muse_no_feedback") and live:
            left = float(obj.budget.get("model_usd", 0.0)) - model_spend(L, cid)
            if left <= 0:
                return None
            settings = Settings(profile=obj.planner.get("profile") or "meta", budget_usd=left, max_tokens=max_tokens,
                                step_cap=step_cap, length_max_tokens=max(max_tokens, LENGTH_MAX_TOKENS))
            return turn_session(L.root, cid, next_turn(L, cid), settings)
        return None
    return session_for


def planner_tick(root: Path | str, session_for: Callable[[Ledger, str, ObjectiveData], Any], campaign_id=None, *,
                 step_cap: int = STEP_CAP, lessons="system") -> list[dict]:
    """The turn trigger a runner thread or the CLI calls after LoopRunner.tick: for each campaign (or one) whose
    planner is scheduled here and has something new, run one turn. `session_for(L, cid, objective)` gives the
    planner session, or None when this process may not run that planner (a paid planner without live opt-in).
    Lessons default to those scoped to the campaign's system (loop_learning.lessons); pass None for none."""
    if lessons == "system":
        from translucent.loop_learning import lessons
    L = Ledger(root)
    try:
        out = []
        for cid in [campaign_id] if campaign_id else campaign_ids(L):
            obj = current_objective(L, cid)
            if obj.planner.get("kind") not in AUTOMATIC or not turn_due(L, cid):
                continue
            session = session_for(L, cid, obj)
            if session is None:
                continue
            result = run_turn(L, cid, session, step_cap=step_cap, lessons=lessons, only_if_due=True)
            if result is not None:
                out.append(result)
        return out
    finally:
        L.db.close()


# ------------------------------------------------------------------ cost estimate (no provider) and the paid smoke

def fastcharge_objective(*, kind: str = "muse", profile: str | None = "meta", model_usd: float = 1.5,
                         experiments: int = 8, registered_by: str = "integrator", objective_kind: str | None = None,
                         target_min: float | None = None) -> dict:
    """A pybamm-fastcharge objective, 8 computational experiments by default.

    objective_kind None: the milestone campaign's case A (campaign-choice §2) as the frozen evaluation protocol
    registers it (loop-compare/2): minimise the 10-80 % charge time of the LG M50 with the anode potential at or
    above +10 mV and the cell at or below 45 degC.
    objective_kind "fastest_feasible" or "hard_target" (loop-compare/3, milestone 2), from the adapter's objective
    template: "Find the fastest protocol meeting these constraints.", or with `target_min` "Meet a 10-80 % charge in
    at most <target_min> min while meeting these constraints." A hard target changes only by an amendment."""
    data = {"system": {"id": "pybamm-fastcharge"}, "datasets": [], "baseline": [],
            "target": {"quantity": "charge_time_80", "unit": "min", "direction": "minimize"},
            "constraints": [{"quantity": "min_anode_potential", "op": ">=", "limit": 10.0, "unit": "mV",
                             "label": "Plating margin: anode potential at or above +10 mV"},
                            {"quantity": "max_temperature", "op": "<=", "limit": 45.0, "unit": "degC",
                             "label": "Cell at or below 45 degC"}],
            "quantities": [{"name": "charge_time_80", "unit": "min", "source": "result"},
                           {"name": "min_anode_potential", "unit": "mV", "source": "result"},
                           {"name": "max_temperature", "unit": "degC", "source": "result"}],
            "budget": {"model_usd": model_usd, "experiments": experiments, "experiment_usd": 0.0},
            "planner": {"kind": kind, "profile": profile, "client": None}, "registered_by": registered_by}
    if objective_kind is None:
        if target_min is not None:
            raise ValueError("A hard target needs objective_kind='hard_target'")
        return data | {"evaluator": "loop-compare/2",
                       "statement": "Charge the LG M50 from 10 to 80 % faster than the failing 1.5C single-step "
                                    "baseline while the anode potential stays at or above +10 mV vs Li/Li+ and the "
                                    "cell at or below 45 degC."}
    from translucent.systems.lgm50_fastcharge import OBJECTIVE_TEMPLATE as template
    if objective_kind == "fastest_feasible":
        if target_min is not None:
            raise ValueError("A fastest_feasible objective has no hard target; use objective_kind='hard_target'")
        statement = template["statement"]["fastest_feasible"]
    elif objective_kind == "hard_target":
        if target_min is None or not math.isfinite(target_min) or target_min <= 0:
            raise ValueError("A hard target needs a positive target_min in minutes")
        statement = template["statement"]["hard_target"].format(
            target=template["hard_target"]["words"].format(limit=float(target_min), unit="min"))
        data["target"] = dict(data["target"], limit=float(target_min))
    else:
        raise ValueError(f"Unknown objective kind {objective_kind!r}")
    data["constraints"] = [dict(c) for c in template["constraints"]]
    return data | {"evaluator": "loop-compare/3", "kind": objective_kind, "statement": statement}


def dry_run(objective: dict | None = None, *, profile: str = "meta", step_cap: int = STEP_CAP,
            max_tokens: int = 6000, calls_per_turn: int = 3, typical_output_tokens: int = 1500) -> dict:
    """Estimate a model planner campaign's cost without contacting a provider. The first call's input is measured
    on a throwaway ledger (the real prompt, tools and context); later turns add one request with its result each.
    States its assumptions; live spending is capped by the metered session and the objective's model budget."""
    from translucent.providers import TOKEN_PRICES
    from translucent.runtime import PROFILES
    data = dict(objective or fastcharge_objective(profile=profile))
    provider, model = PROFILES[profile]["orchestrator"]
    p_in, _, p_out = TOKEN_PRICES[provider][model]
    with tempfile.TemporaryDirectory(prefix="translucent-dryrun-") as tmp:
        L = Ledger(Path(tmp) / "ledger")
        try:
            data["registered_by"] = data.get("registered_by") or "dry-run"
            cid = LoopService(L, Actor("person", data["registered_by"], "cli")).open_campaign(
                data, "Dry run")["campaign_id"]
            obj = current_objective(L, cid)
            schemas = tool_schemas(obj)
            messages = [{"role": "system", "content": system_prompt(obj)},
                        {"role": "user", "content": chain.canonical(planner_context(L, cid)).decode()}]
            first_chars = len(json.dumps(messages)) + len(json.dumps(schemas))
            settings = Settings(profile=profile, budget_usd=1e9, max_tokens=max_tokens, step_cap=step_cap)
            worst_first = worst_case_cost(settings, messages, schemas)
            spec = obj.system["spec"]
            action = next(a for a in spec["actions"] if a["executor"] == "computational" and a["parameters"])
            produced = len(action["produces"])
        finally:
            L.db.close()
    experiments = int(obj.budget.get("experiments") or 0)
    per_request_chars = 2500 + produced * 170      # a request in full with its result, measured shapes, rounded up
    per_call_chars = 1500                          # each tool result and reply within a turn
    no_feedback = obj.planner.get("kind") == "muse_no_feedback"
    turns = 1 if no_feedback else experiments + 2
    typical_calls = (experiments + 2) if no_feedback else turns * calls_per_turn
    ceiling_calls = step_cap if no_feedback else turns * step_cap

    def input_chars(turn: int, call: int) -> int:
        return first_chars + min(turn, experiments) * per_request_chars + call * per_call_chars

    def cost(calls_in_turn: int, out_tokens: int) -> float:
        total = 0.0
        for t in range(turns):
            for c in range(calls_in_turn):
                total += input_chars(t, c) // 2 * p_in + out_tokens * p_out
        return total
    typical = cost(typical_calls if no_feedback else calls_per_turn, typical_output_tokens)
    ceiling = cost(step_cap, max_tokens)
    return {"dry_run": True, "system": obj.system["id"], "kind": obj.planner.get("kind"), "profile": profile,
            "provider": provider, "model": model, "experiments": experiments, "turns": turns,
            "first_call_input_chars": first_chars, "first_call_reservation_usd": round(worst_first, 6),
            "typical_calls": typical_calls, "typical_estimate_usd": round(typical, 4),
            "ceiling_calls": ceiling_calls, "ceiling_estimate_usd": round(ceiling, 4),
            "model_budget_usd": obj.budget.get("model_usd"),
            "assumptions": {"input_tokens": "characters / 2, the metered client's conservative rule",
                            "calls_per_turn": calls_per_turn, "typical_output_tokens": typical_output_tokens,
                            "ceiling_output_tokens": max_tokens, "context_growth_chars_per_experiment":
                                per_request_chars, "chars_per_call_within_a_turn": per_call_chars,
                            "turns": "one per experiment, plus the start and the conclusion"
                                     if not no_feedback else "one turn proposes the whole budget"},
            "prices_usd_per_million": {"input": p_in * 1e6, "output": p_out * 1e6},
            "note": "No provider client is constructed and no network is used. The ledger is a throwaway in a "
                    "temporary folder. Spending is capped live by the metered session and the objective's "
                    "model budget."}


def smoke(ledger: Path, *, profile: str, budget_usd: float, step_cap: int = 8, max_tokens: int = 4000,
          objective_kind: str | None = "fastest_feasible", target_min: float | None = None) -> dict:
    """The integrator's paid smoke run: open a muse campaign on pybamm-fastcharge (a new campaign's objective by
    default; objective_kind None is case A) with a model budget of `budget_usd` and run one metered planner turn. It
    ends at the first proposal (no solver runs: a person has not approved anything) or at the budget."""
    L = Ledger(ledger)
    try:
        person = Actor("person", "integrator", "cli")
        data = fastcharge_objective(kind="muse", profile=profile, model_usd=budget_usd, objective_kind=objective_kind,
                                    target_min=target_min)
        cid = LoopService(L, person).open_campaign(data, "Planner smoke run")["campaign_id"]
        settings = Settings(profile=profile, budget_usd=budget_usd, max_tokens=max_tokens, step_cap=step_cap)
        session = turn_session(L.root, cid, next_turn(L, cid), settings)
        result = run_turn(L, cid, session, step_cap=step_cap)
        planner = [{"seq": r.seq, "phase": r.data["phase"],
                    **{k: r.data["payload"].get(k) for k in ("status", "tool", "cost_usd") if k in r.data["payload"]}}
                   for r in chain.records(L, cid) if r.type == "planner"]
        return {"campaign_id": cid, "turn": result, "receipts": planner, "tape": str(session.store)}
    finally:
        L.db.close()


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m translucent.loop_planner",
                                 description="Loop planner: cost dry-run, and the integrator's paid smoke turn.")
    sub = ap.add_subparsers(dest="command", required=True)
    d = sub.add_parser("dry-run", help="estimate a model campaign's cost; contacts no provider")
    d.add_argument("--kind", choices=["muse", "muse_no_feedback"], default="muse")
    d.add_argument("--profile", choices=["meta", "dev"], default="meta")
    d.add_argument("--experiments", type=int, default=8)
    d.add_argument("--model-usd", type=float, default=1.5)
    d.add_argument("--step-cap", type=int, default=STEP_CAP)
    d.add_argument("--objective", choices=["fastest_feasible", "hard_target", "case-a"], default="fastest_feasible",
                   help="a new campaign's objective (loop-compare/3), or the evaluation's case A (loop-compare/2)")
    d.add_argument("--target-min", type=float, default=None, help="the hard target, minutes (hard_target only)")
    s = sub.add_parser("smoke", help="PAID: one metered planner turn on a new pybamm-fastcharge campaign")
    s.add_argument("--ledger", type=Path, required=True)
    s.add_argument("--profile", choices=["meta", "dev"], default="dev")
    s.add_argument("--budget-usd", type=float, default=0.05)
    s.add_argument("--step-cap", type=int, default=8)
    s.add_argument("--max-tokens", type=int, default=4000)
    s.add_argument("--live", action="store_true", help="required: this calls a paid model")
    s.add_argument("--objective", choices=["fastest_feasible", "hard_target", "case-a"], default="fastest_feasible")
    s.add_argument("--target-min", type=float, default=None)
    args = ap.parse_args(list(argv) if argv is not None else None)
    objective_kind = None if args.objective == "case-a" else args.objective
    if (args.target_min is not None) != (objective_kind == "hard_target"):
        ap.error("--target-min goes with --objective hard_target, and only with it")
    if args.command == "dry-run":
        out = dry_run(fastcharge_objective(kind=args.kind, profile=args.profile, model_usd=args.model_usd,
                                           experiments=args.experiments, objective_kind=objective_kind,
                                           target_min=args.target_min), profile=args.profile,
                      step_cap=args.step_cap)
    else:
        if not args.live:
            ap.error("smoke calls a paid model: add --live to confirm")
        if not math.isfinite(args.budget_usd) or not 0 < args.budget_usd <= 0.25:
            ap.error("the smoke run's budget is at most $0.25")
        out = smoke(args.ledger, profile=args.profile, budget_usd=args.budget_usd, step_cap=args.step_cap,
                    max_tokens=args.max_tokens, objective_kind=objective_kind, target_min=args.target_min)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
