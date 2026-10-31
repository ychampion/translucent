"""MCP stdio server. Writes require --write; stdout contains JSON-RPC messages only.

Loop campaigns (design §2.9): one tool for each capability of translucent.loop_service.CAPABILITIES that an agent or
a person may call (LOOP_TOOLS), plus wait_for_request; the runner's and the comparator's capabilities have no tool.
Reads are open to every session. Agent tools need --write and act as the client ("agent:<clientInfo name>"). Person
tools (approve, reject, revise, submit a result, amend, note) exist only when the local operator started the server
with --operator "<name>": the operator vouches for that name, as holding the team token does over HTTP, and records
are written under it, never under the client's self-declared name. Files are read only under --import-root.

Several writers may share a ledger: the API server, any number of MCP sessions and the CLI. Loop records are safe
by construction (chain admission in one IMMEDIATE transaction). A legacy video or intervention campaign is written
by one process at a time: an MCP session takes the campaign's lock for each write call (translucent.locks), and the
API worker holds it while it runs the campaign.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

from translucent.verify import evidence, snapshot, verify
from translucent.ledger import Ledger, TABLES
from translucent.locks import campaign_lock
from translucent.intervention_gate import install as _install_intervention_gate

_install_intervention_gate()

VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
PAGE = {"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        "cursor": {"type": "string", "pattern": "^[0-9]+$", "description": "Number of records already read."}}


def _tool(name, description, properties=None, required=(), *, read_only=True):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {}, "required": list(required),
                            "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": False,
                            "idempotentHint": read_only, "openWorldHint": name == "run_tool"}}


TOOLS = [
    _tool("list_campaigns", "List campaigns, 50 at a time. Advance cursor by the number returned.", PAGE),
    _tool("list_findings", "List findings and their latest 50 reviews. Advance cursor by the number returned.",
          {"campaign_id": {"type": "string"}, **PAGE}),
    _tool("get_evidence", "Read evidence in pages. Use next_cursor for records and value_next_cursor for samples.",
          {"claim_id": {"type": "string"}, **PAGE,
           "sample_limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 256},
           "sample_cursor": {"type": "string", "pattern": "^[0-9]+$",
                             "description": "Number of samples already read in each measurement."}}, ["claim_id"]),
    _tool("verify", "Re-check a campaign or the configured ledger offline without changing its files.",
          {"campaign_or_ledger": {"type": "string"}}, ["campaign_or_ledger"]),
    _tool("list_branches", "A finding's alternatives: the findings that replace it or that it replaces, people's "
          "what-if branches made from it with their verdicts, and findings elsewhere testing the same quantity. "
          "Read-only: only a person can make a what-if branch.", {"claim_id": {"type": "string"}}, ["claim_id"]),
]


EXCLUDED = {"propose_finding", "record_lesson", "run_analysis_code", "declare_winner"}
CAMPAIGN = {"campaign_id": {"type": "string", "minLength": 1}}


def _schema(name):
    from translucent import tools
    schema = deepcopy(tools.REGISTRY[name]["schema"]["function"]["parameters"])
    schema["additionalProperties"] = False
    return schema


def _write_tools():
    result = [
        _tool("list_experiments", "List recorded videos with their experiment id and citation."),
        _tool("open_campaign", "Open a campaign on a recorded experiment.", {
            "objective": {"type": "string", "minLength": 1, "maxLength": 10000},
            "experiment": {"type": "string", "minLength": 1},
            "budget_usd": {"type": "number", "minimum": 0}},
            ["objective", "experiment", "budget_usd"], read_only=False),
        _tool("list_tools", "List this campaign's measurement tools and argument schemas.", CAMPAIGN, ["campaign_id"]),
        _tool("run_tool", "Run a measurement tool and record its evidence. Images are local file paths.",
              {**CAMPAIGN, "tool": {"type": "string"}, "arguments": {"type": "object"}},
              ["campaign_id", "tool", "arguments"], read_only=False),
    ]
    for name, description in [("propose_finding", "Ask the gate to check a finding; read each failed check's explanation."),
                              ("record_lesson", "Save a lesson with evidence from this campaign.")]:
        schema = _schema(name)
        result.append(_tool(name, description, {**CAMPAIGN, **schema["properties"]},
                            ["campaign_id", *schema["required"]], read_only=False))
    result.append(_tool("finish_campaign", "End this campaign. Further writes will be refused.",
                        {**CAMPAIGN, "answer": {"type": "string"}, "not_proven": {"type": "string"}},
                        ["campaign_id", "answer", "not_proven"], read_only=False))
    return result


# ------------------------------------------------------------ loop campaigns

_KEY = {"idempotency_key": {"type": "string", "pattern": "^[A-Za-z0-9_.:-]{1,128}$",
                            "description": "Optional. The same key and arguments again, in this session, return "
                                           "the first result; a different call under a used key is refused. A "
                                           "campaign another session opened is not this session's without --adopt."}}
_TEXT = {"type": "string", "minLength": 1, "maxLength": 10000}
_REQUEST = {**CAMPAIGN, "request_id": {"type": "string", "pattern": "^rq_[0-9a-f]{20}$"}}
_HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$",
         "description": "The content_hash of the version you read (get_request, get_campaign_view)."}
_FILES = {"type": "object", "description": "File name -> path, relative to (or under) the server's --import-root."}

# capability -> (tool, what the tool adds to the capability's summary, properties, required). Who may call a tool,
# and whether it writes, comes from CAPABILITIES; a capability missing here has no tool (the parity test fails).
LOOP_TOOLS = {
    "systems": ("list_systems", "", {}, ()),
    "datasets": ("list_datasets", "", {}, ()),
    "dataset": ("get_dataset", "", {"dataset_id": {"type": "string", "minLength": 1}}, ("dataset_id",)),
    "campaigns": ("list_loop_campaigns", "Advance cursor to next_cursor.",
                  {"limit": PAGE["limit"], "cursor": {"type": "integer", "minimum": 0, "default": 0}}, ()),
    "view": ("get_campaign_view", "", CAMPAIGN, ("campaign_id",)),
    "series": ("read_series", "ref is a request or result id, or dataset_id/record_id; t0 and t1 are seconds.",
               {**CAMPAIGN, "ref": {"type": "string", "minLength": 1}, "quantity": {"type": "string", "minLength": 1},
                "t0": {"type": "number"}, "t1": {"type": "number"},
                "max_points": {"type": "integer", "minimum": 2, "maximum": 20000, "default": 2000}},
               ("campaign_id", "ref", "quantity")),
    "request": ("get_request", "", _REQUEST, ("campaign_id", "request_id")),
    "import_dataset": ("import_dataset", "Send the manifest itself. files is a folder, or file name -> path, read "
                       "only under the server's --import-root.",
                       {"manifest": {"type": "object"}, "files": {"description": "A folder, or file name -> path, "
                                                                                 "relative to --import-root."}},
                       ("manifest",)),
    "open_campaign": ("open_loop_campaign", "You plan it (planner kind external); the server's operator, or else "
                      "your client, registers it, without pre-approvals. This session owns it.",
                      {"title": {"type": "string", "minLength": 1, "maxLength": 200}, "objective": {"type": "object"},
                       **_KEY}, ("title", "objective")),
    "define_measurement": ("define_measurement", "",
                           {**CAMPAIGN, "name": {"type": "string", "minLength": 1, "maxLength": 80},
                            "spec": {"type": "object"}, **_KEY}, ("campaign_id", "name", "spec")),
    "propose": ("propose_experiment", "A person approves it (over HTTP, the web app, or an --operator session); "
                "then call wait_for_request.",
                {**CAMPAIGN, "action": {"type": "string", "minLength": 1}, "params": {"type": "object"},
                 "rationale": _TEXT, "prediction": {"type": "object"}, "expected_effect": _TEXT,
                 "evidence_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
                 "specimen": {"type": "object"}, **_KEY},
                ("campaign_id", "action", "params", "rationale", "prediction", "expected_effect")),
    "cancel": ("cancel_request", "", {**_REQUEST, "reason": _TEXT, **_KEY}, ("campaign_id", "request_id", "reason")),
    "conclude": ("conclude_campaign", "",
                 {**CAMPAIGN, "text": _TEXT, "unresolved_reasons": {"type": "array", "items": _TEXT, "maxItems": 50},
                  **_KEY}, ("campaign_id", "text")),
    "approve": ("approve_request", "As the server's operator.",
                {**_REQUEST, "content_hash": _HASH, "note": {"type": "string", "maxLength": 2000}, **_KEY},
                ("campaign_id", "request_id", "content_hash")),
    "reject": ("reject_request", "As the server's operator.",
               {**_REQUEST, "content_hash": _HASH, "reason": _TEXT, **_KEY},
               ("campaign_id", "request_id", "content_hash", "reason")),
    "revise": ("revise_request", "As the server's operator.",
               {**_REQUEST, "params": {"type": "object"}, "reason": _TEXT, "approve": {"type": "boolean"}, **_KEY},
               ("campaign_id", "request_id", "params", "reason")),
    "submit_result": ("submit_result", "As the server's operator; upload is the HumanUpload without author.",
                      {**CAMPAIGN, "upload": {"type": "object"}, "files": _FILES}, ("campaign_id", "upload")),
    "amend": ("amend_objective", "As the server's operator.",
              {**CAMPAIGN, "reason": _TEXT, "target": {"type": "object"},
               "constraints": {"type": "array", "items": {"type": "object"}, "maxItems": 50}, **_KEY},
              ("campaign_id", "reason")),
    "note": ("add_note", "As the server's operator.", {**CAMPAIGN, "text": {**_TEXT, "maxLength": 2000}},
             ("campaign_id", "text")),
}
LOOP_BY_TOOL = {spec[0]: capability for capability, spec in LOOP_TOOLS.items()}
WAIT = _tool("wait_for_request", "Wait up to timeout_s for a request to settle, running it here if it is approved "
             "computational work; returns the request (settled false while it still waits for a person).",
             {**_REQUEST, "timeout_s": {"type": "number", "minimum": 0, "maximum": 60, "default": 30}},
             ["campaign_id", "request_id"], read_only=False)
WAITING = frozenset({"awaiting_approval", "queued", "running", "awaiting_result"})


def _loop_tools(*, write: bool, operator: str | None):
    """The loop tools this session offers: reads always; agent writes with --write; person tools with --operator."""
    from translucent.loop_service import CAPABILITIES
    out = []
    for capability, cap in CAPABILITIES.items():
        if capability not in LOOP_TOOLS or not cap.actors - {"system"}:
            continue
        if cap.write and not (write and ("agent" in cap.actors or operator)):
            continue
        name, more, properties, required = LOOP_TOOLS[capability]
        out.append(_tool(name, f"{cap.summary}. {more}".strip(), properties, required, read_only=not cap.write))
    return out + ([WAIT] if write else [])


def _arguments(schema, value, path="arguments"):
    kind = schema.get("type")
    valid = {"object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "integer": type(value) is int,
             "number": type(value) in (int, float), "boolean": type(value) is bool,
             "null": value is None}.get(kind, True)
    if not valid:
        raise ValueError(f"{path} must be {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']}")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if set(schema.get("required", [])) - value.keys():
            raise ValueError(f"{path} is missing required fields")
        if schema.get("additionalProperties") is False and set(value) - properties.keys():
            raise ValueError(f"{path} has extra fields: {', '.join(sorted(set(value) - properties.keys()))}")
        for key, item in value.items():
            _arguments(properties.get(key, {}), item, f"{path}.{key}")
    elif isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", math.inf):
            raise ValueError(f"{path} has the wrong number of items")
        for item in value:
            _arguments(schema.get("items", {}), item, path + "[]")
    elif isinstance(value, str):
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", math.inf) or \
                ("pattern" in schema and re.fullmatch(schema["pattern"], value) is None):
            raise ValueError(f"{path} does not match the advertised format")
        if path in ("arguments.cursor", "arguments.sample_cursor") and (len(value) > 19 or int(value) >= 2**63):
            raise ValueError(f"{path} is too large")
    elif type(value) in (int, float):
        try:
            valid = math.isfinite(value) and schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError(f"{path} must be a finite number within the advertised bounds")


def _records(ledger, kind, limit, cursor=0, campaign_id=None, ref=None, newest=False):
    query, args = "SELECT body FROM record WHERE kind=?", [kind]
    if campaign_id is not None:
        query, args = query + " AND campaign_id=?", args + [campaign_id]
    if ref is not None:
        query, args = query + " AND ref=?", args + [ref]
    order = "inserted_at DESC, rowid DESC" if newest else "inserted_at, rowid"
    rows = ledger.db.execute(query + f" ORDER BY {order} LIMIT ? OFFSET ?", args + [limit, cursor])
    records = [TABLES[kind].model_validate_json(body) for (body,) in rows]
    return records[::-1] if newest else records


def _evidence_page(chain, arguments):
    limit, cursor = arguments.get("limit", 50), int(arguments.get("cursor", "0"))
    fields = ("measurements", "runs", "artifacts", "reviews", "problems", "gate_checks")
    counts = {key: len(chain[key]) for key in fields}
    for key in fields:
        chain[key] = chain[key][cursor:cursor + limit]
    sample_limit, sample_cursor = arguments.get("sample_limit", 256), int(arguments.get("sample_cursor", "0"))
    for measurement in chain["measurements"]:
        values = measurement["value"]
        if isinstance(values, list):
            measurement["value"] = values[sample_cursor:sample_cursor + sample_limit]
            measurement["value_count"] = len(values)
            measurement["value_cursor"] = str(sample_cursor)
            measurement["value_next_cursor"] = (str(sample_cursor + sample_limit)
                                                 if sample_cursor + sample_limit < len(values) else None)
    return {**chain, "counts": counts, "next_cursor": str(cursor + limit)
            if any(count > cursor + limit for count in counts.values()) else None}


def _finding(ledger, claim):
    count = ledger.db.execute("SELECT COUNT(*) FROM record WHERE kind='review' AND ref=?", (claim.id,)).fetchone()[0]
    return {**claim.model_dump(mode="json"), "status": ledger.claim_status(claim.id),
            "reviews": [r.model_dump(mode="json") for r in _records(ledger, "review", 50, ref=claim.id, newest=True)],
            "review_count": count, "reviews_truncated": count > 50}


def call(root, name, arguments):
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if tool is None:
        raise ValueError("Unknown tool")
    schema = tool["inputSchema"]
    _arguments(schema, arguments)
    root = Path(root).resolve()
    limit, cursor = arguments.get("limit", 50), int(arguments.get("cursor", "0"))
    with snapshot(root) as ledger:
        if name == "list_campaigns":
            return [c.model_dump(mode="json") for c in _records(ledger, "campaign", limit, cursor)]
        if name == "list_findings":
            cid = arguments.get("campaign_id")
            if cid is not None and ledger.get("campaign", cid) is None:
                raise ValueError("Campaign not found")
            return [_finding(ledger, c)
                    for c in _records(ledger, "claim", limit, cursor, campaign_id=cid)]
        if name == "list_branches":
            from translucent.rerun import branches
            try:
                return branches(ledger, arguments["claim_id"])
            except LookupError as e:
                raise ValueError(str(e)) from e
        if name == "get_evidence":
            claim_id = arguments["claim_id"]
            chain = evidence(ledger, claim_id)
            reviews = chain["reviews"]
            chain.update(status=ledger.claim_status(claim_id),
                         gate_checks=[c for r in reviews if r["reviewer"] == "gate" for c in r["checks"]])
            return _evidence_page(chain, arguments)
        target = arguments["campaign_or_ledger"]
        if ledger.get("campaign", target):
            cid = target
        elif target == "ledger" or Path(target).resolve() == root:
            cid = None
        else:
            raise ValueError("Choose a campaign id or the configured ledger; other paths are not exposed")
    return verify(root, campaign_id=cid).as_dict()


def _experiments(ledger):
    return [{"experiment": a.source.uri.removeprefix("experiment:"), "video_id": a.id,
             "label": a.label, "citation": a.source.citation}
            for a in ledger.list("artifact") if a.kind == "video" and a.source and
            a.source.uri.startswith("experiment:")]


def _reference(ledger, campaign, rid, kinds):
    if re.fullmatch(r"[0-9a-f]{64}", rid):
        rid = "sha256:" + rid
    kind = ledger.kind_of(rid)
    if kind not in kinds:
        raise ValueError(f"{rid} is not a recorded {' or '.join(kinds)}")
    record = ledger.get(kind, rid)
    if kind == "measurement":
        record = ledger.get("run", record.run_id)
    if kind == "artifact":
        owned = rid in campaign.inputs or any(rid in r.output_ids
                    for r in ledger.list("run", campaign_id=campaign.id))
    else:
        owned = record is not None and record.campaign_id == campaign.id
    if not owned:
        raise ValueError(f"{rid} belongs to another campaign; run the measurement in this campaign")


@contextmanager
def _writer(root):
    ledger = Ledger(root)
    try:
        yield ledger
    finally:
        ledger.db.close()


class Protocol:
    def __init__(self, root, *, write=False, adopt=False, state_dir=None, operator=None, import_root=None):
        # state_dir is accepted for compatibility: MCP writers no longer lock the API's state directory or the
        # ledger (several writers share a ledger; see the module docstring)
        self.root, self.initialized, self.ready = Path(root).resolve(), False, False
        self.write, self.adopt = write, adopt
        self.client_info, self.owned, self.step = {}, set(), 0
        self.closed = False
        if adopt and not write:
            raise ValueError("--adopt requires --write")
        if operator is not None:
            operator = operator.strip()
            if not write or not operator or len(operator) > 80 or re.search(r"[\x00-\x1f\x7f]", operator):
                raise ValueError("--operator needs --write and a person's name (1-80 characters)")
        self.operator = operator
        self.import_root = Path(import_root).resolve() if import_root is not None else None
        if self.import_root is not None and not self.import_root.is_dir():
            raise ValueError("The --import-root folder does not exist")
        if write and not (self.root / "ledger.sqlite").is_file():
            raise ValueError("The configured ledger does not exist")
        self.tools = TOOLS + (_write_tools() if write else []) + _loop_tools(write=write, operator=operator)

    def close(self):
        self.closed = True

    def _campaign(self, ledger, cid, *, writing=False):
        campaign = ledger.get("campaign", cid)
        if campaign is None:
            raise ValueError("Campaign not found")
        if writing:
            if cid not in self.owned and not self.adopt:
                raise ValueError("This session did not open that campaign. Restart with --adopt to write to it.")
            if any(e.type == "campaign.finished" for e in ledger.events(cid)):
                raise ValueError("Campaign is finished; further writes are refused")
        return campaign

    def _call(self, name, arguments):
        if name in {t["name"] for t in TOOLS}:
            return call(self.root, name, arguments)
        if name in LOOP_BY_TOOL or name == WAIT["name"]:
            return self._loop_call(name, arguments)
        from translucent import runtime, tools
        from translucent.server import explain_check
        tool = next(t for t in self.tools if t["name"] == name)
        _arguments(tool["inputSchema"], arguments)
        if name in {"list_experiments", "list_tools"}:
            with snapshot(self.root) as ledger:
                if name == "list_experiments":
                    return _experiments(ledger)
                self._campaign(ledger, arguments["campaign_id"])
                from translucent.intervention import records as intervention_records
                if intervention_records(ledger,arguments['campaign_id'],'protocol'):
                    from translucent.intervention_runtime import tool_schemas
                    return [{k:v for k,v in t.items() if k!='read_only'} for t in tool_schemas()]
                return [{"name": n, "description": s["schema"]["function"]["description"], "inputSchema": _schema(n)}
                        for n, s in tools.REGISTRY.items() if n not in EXCLUDED]
        with _writer(self.root) as ledger:
            actor = "agent:" + self.client_info["name"]
            if name == "open_campaign":
                from translucent.loop_surface import budget_problem
                from translucent.server import _profile
                if problem := budget_problem(arguments["budget_usd"], _profile(), "budget_usd"):
                    raise ValueError(problem)     # the same caps as POST /api/campaigns
                if arguments['experiment'].split(':')[0]=='nist':
                    from translucent.intervention_runtime import open_intervention
                    parts=arguments['experiment'].split(':')
                    split=parts[1] if len(parts)>1 else 'loco'
                    if split not in ('loco','lofo','prequential'):raise ValueError('Choose nist:loco, nist:lofo or nist:prequential')
                    campaign,fold,_=open_intervention(ledger,self.root.parent/(self.root.name+'-sealed'),split=split)
                    ledger.emit(campaign.id,'human.note',actor,'MCP intervention session opened',
                                data={'via':'mcp','clientInfo':self.client_info,'fold_id':fold.id})
                    self.owned.add(campaign.id)
                    return {**campaign.model_dump(mode='json'),'fold_id':fold.id}
                videos = [v for v in _experiments(ledger) if v["experiment"] == arguments["experiment"]]
                if not videos:
                    raise ValueError("Experiment not found; choose an experiment from list_experiments")
                campaign = runtime.new_campaign(ledger, arguments["objective"], videos[0]["label"] or arguments["experiment"],
                                                [v["video_id"] for v in videos], budget_usd=arguments["budget_usd"])
                # the code that will judge its findings, so verify re-checks them with the same gate rules
                setup = ledger.put_json(runtime.setup_snapshot(ledger, campaign, {"profile": "mcp", "models": {}}),
                                        kind="report", label="Setup")
                ledger.emit(campaign.id, "campaign.started", actor, campaign.title, detail=campaign.objective,
                            refs=[setup.id], data={"via": "mcp", "clientInfo": self.client_info, "setup_id": setup.id})
                self.owned.add(campaign.id)
                return campaign.model_dump(mode="json")
            campaign = self._campaign(ledger, arguments["campaign_id"], writing=True)
            lock = campaign_lock(self.root, campaign.id)
            if not lock.try_acquire():
                raise ValueError("Another process is writing to this campaign (the API server runs it, or another "
                                 "MCP session is mid-call); try again when it stops")
            try:
                return self._legacy_write(ledger, campaign, name, arguments, actor)
            finally:
                lock.release()

    # ------------------------------------------------------------ loop campaigns

    def _loop_call(self, name, arguments):
        from translucent import chain
        from translucent.ledger import AdmissionError
        from translucent.loop_service import CAPABILITIES, Actor, LoopService, ResultRefused
        tool = next(t for t in self.tools if t["name"] == name)
        _arguments(tool["inputSchema"], arguments)
        agent = Actor("agent", self.client_info.get("name") or "reader", "mcp")
        if name == WAIT["name"]:
            return self._wait(agent, arguments)
        capability = LOOP_BY_TOOL[name]
        cap = CAPABILITIES[capability]
        # a person's tool acts as the operator who started the server; never as the client's self-declared name
        actor = Actor("person", self.operator, "mcp") if "agent" not in cap.actors else agent
        cid = arguments.get("campaign_id")
        # a read-only session reads a snapshot, as its other tools do; a writing session reads the live ledger
        with (_writer if self.write else snapshot)(self.root) as ledger:
            service = LoopService(ledger, actor, idempotency_key=arguments.get("idempotency_key"),
                                  import_root=self.import_root)
            if cap.write and cid is not None:
                self._loop_campaign(ledger, cid)
            try:
                return self._loop_dispatch(service, capability, arguments)
            except ResultRefused as e:
                raise ValueError(f"The result was refused and the refusal recorded ({getattr(e.record, 'id', '')}): "
                                 f"{e}") from e
            except AdmissionError as e:
                if cap.write and cid is not None and (done := chain.concluded(ledger, cid)) is not None:
                    raise ValueError(f"Campaign concluded at seq {done.seq}; further writes are refused") from e
                raise

    def _loop_campaign(self, ledger, cid):
        """Writes to a loop campaign need it to be one, opened by this session or adopted (as for other campaigns)."""
        from translucent.loop import current_objective
        current_objective(ledger, cid)
        if cid not in self.owned and not self.adopt:
            raise ValueError("This session did not open that campaign. Restart with --adopt to write to it.")

    def _loop_dispatch(self, service, capability, a):
        from translucent.loop_surface import campaigns, objective_budget_problems, replay, view
        cid = a.get("campaign_id")
        if capability == "systems":
            return service.systems()
        if capability == "datasets":
            return service.datasets()
        if capability == "dataset":
            return service.dataset(a["dataset_id"])
        if capability == "campaigns":
            return campaigns(service, self.root.name, cursor=a.get("cursor", 0), limit=a.get("limit", 50))
        if capability == "view":
            return view(service, cid, self.root.name)
        if capability == "series":
            window = None
            if "t0" in a or "t1" in a:
                window = (a.get("t0", -math.inf), a.get("t1", math.inf))
            return service.series(cid, a["ref"], a["quantity"], window=window, max_points=a.get("max_points", 2000))
        if capability == "request":
            return service.request(cid, a["request_id"])
        if capability == "import_dataset":
            files = a.get("files", ".")
            if not isinstance(files, str) and not (isinstance(files, dict) and
                                                   all(isinstance(p, str) for p in files.values())):
                raise ValueError("files is a folder, or file name -> path, under the import root")
            return service.import_dataset(a["manifest"], files)
        if capability == "open_campaign":
            from translucent.server import _profile
            objective = dict(a["objective"])
            objective["planner"] = {"kind": "external", "profile": None, "client": dict(self.client_info)}
            objective["registered_by"] = self.operator or self.client_info["name"]
            if problems := objective_budget_problems(objective, _profile()):
                raise ValueError("; ".join(problems))
            opened = service.open_campaign(objective, a["title"])
            # a replayed key returns the campaign it opened; only the session that created it owns it (the key is
            # no secret: another session could replay it to take over the campaign)
            if opened["created"]:
                self.owned.add(opened["campaign_id"])
            elif opened["campaign_id"] not in self.owned and not self.adopt:
                raise ValueError("Another session opened this campaign with this idempotency key. Restart with "
                                 "--adopt to write to it, or open a new one with a new key.")
            return {"campaign_id": opened["campaign_id"], "view": view(service, opened["campaign_id"], self.root.name)}
        if capability == "define_measurement":
            return service.define_measurement(cid, a["name"], a["spec"])
        if capability == "propose":
            return replay(service, "propose", cid, action=a["action"], params=a["params"]) or                 service.propose(cid, a["action"], a["params"], rationale=a["rationale"],
                                prediction=a["prediction"], expected_effect=a["expected_effect"],
                                evidence_ids=tuple(a.get("evidence_ids", ())), specimen=a.get("specimen"))
        if capability == "cancel":
            return service.cancel(cid, a["request_id"], a["reason"])
        if capability == "conclude":
            return service.conclude(cid, a["text"], a.get("unresolved_reasons", ()))
        if capability == "approve":
            return service.approve(cid, a["request_id"], a["content_hash"], a.get("note", ""))
        if capability == "reject":
            return service.reject(cid, a["request_id"], a["content_hash"], a["reason"])
        if capability == "revise":
            return replay(service, "revise", cid, request_id=a["request_id"], params=a["params"]) or \
                service.revise(cid, a["request_id"], a["params"], a["reason"], approve=a.get("approve", False))
        if capability == "submit_result":
            upload = {**a["upload"], "author": self.operator}
            files = a.get("files", {})
            if not all(isinstance(p, str) for p in files.values()):
                raise ValueError("files maps each file name to a path under the import root")
            located = {n: service._confined(p) for n, p in files.items()}   # the import root's one rule
            return service.submit_result(cid, upload, located)
        if capability == "amend":
            return replay(service, "amend", cid, reason=a["reason"], target=a.get("target"),
                          constraints=a.get("constraints")) or \
                service.amend(cid, a["reason"], target=a.get("target"), constraints=a.get("constraints"))
        if capability == "note":
            from translucent import chain
            if done := chain.concluded(service.L, cid):
                raise ValueError(f"Campaign concluded at seq {done.seq}; further writes are refused")
            return service.note(cid, a["text"])
        raise ValueError(f"{capability} has no MCP tool")

    def _wait(self, agent, a):
        """Poll the request until it settles or timeout_s passes, running approved computational work in this
        process (LoopRunner.tick, one request at a time) so a request completes with or without an API server."""
        from translucent.loop_runner import LoopRunner
        from translucent.loop_service import LoopService
        cid, rq = a["campaign_id"], a["request_id"]
        deadline = time.monotonic() + a.get("timeout_s", 30)
        runner = LoopRunner(self.root, process="mcp", workers=1)
        while True:
            with _writer(self.root) as ledger:
                state = LoopService(ledger, agent).request(cid, rq)
            if state["state"] not in WAITING or time.monotonic() >= deadline:
                break
            if not (state["state"] in ("queued", "running") and runner.tick(cid, limit=1)):
                time.sleep(0.2)      # a person's decision, or another process running it
        out = {**state, "settled": state["state"] not in WAITING}
        if runner.problems:
            out["runner_problems"] = [p.splitlines()[0] for p in runner.problems]
        return out

    def _legacy_write(self, ledger, campaign, name, arguments, actor):
        """A write to a video or intervention campaign, holding its campaign lock."""
        from translucent import tools
        from translucent.server import explain_check
        from translucent.intervention import records as intervention_records
        if intervention_records(ledger,campaign.id,'protocol'):
            if name!='run_tool':raise ValueError('Use list_tools and run_tool for this intervention campaign; finish through supported write_procedure')
            from translucent.intervention_runtime import InterventionTools
            folds=intervention_records(ledger,campaign.id,'fold')
            service=InterventionTools(ledger,campaign.id,folds[-1].id,self.root.parent/(self.root.name+'-sealed'))
            return service.call(arguments['tool'],arguments['arguments'])
        if name == "finish_campaign":
            data = {"status": "finished", "answer": arguments["answer"], "not_proven": arguments["not_proven"]}
            ledger.emit(campaign.id, "campaign.finished", actor, "Campaign finished", detail=data["answer"], data=data)
            return {"campaign_id": campaign.id, **data}
        if name == "run_tool":
            name, args = arguments["tool"], arguments["arguments"]
            if name not in tools.REGISTRY or name in EXCLUDED:
                raise ValueError("Tool is not available through run_tool; choose from list_tools")
        else:
            args = {k: v for k, v in arguments.items() if k != "campaign_id"}
        _arguments(_schema(name), args)
        for key in ("video_id", "boxes_id", "scale_id", "time_base_id"):
            if key in args:
                _reference(ledger, campaign, args[key], ("artifact",) if key in {"video_id", "boxes_id"}
                           else ("measurement",))
        for mid in args.get("measurement_ids", []):
            _reference(ledger, campaign, mid, ("measurement",))
        for rid in args.get("evidence_ids", []):
            _reference(ledger, campaign, rid, ("measurement", "run", "claim"))
        if args.get("supersedes"):
            _reference(ledger, campaign, args["supersedes"], ("claim",))
        self.step += 1
        result = tools.call(tools.Context(ledger, campaign.id, step_id=f"mcp{self.step}"), name, args)
        run = ledger.get("run", result.run_id)
        value = {"run_id": result.run_id, "status": result.status, "summary": result.summary,
                 "outputs": list(dict.fromkeys(result.outputs + (run.measurement_ids or []))) if run else result.outputs,
                 "images": [str(Path(p).resolve()) for p in result.images], "data": result.data}
        if name == "propose_finding" and result.status == "ok":
            review = [r for r in ledger.list("review", ref=result.data["claim_id"]) if r.reviewer == "gate"][-1]
            value.update(claim_id=review.claim_id, verdict=review.verdict, reason=review.reason,
                         failed_checks=[{**c.model_dump(), "explanation": explain_check(c.model_dump())}
                                        for c in review.checks if not c.passed])
        return value

    def handle(self, message):
        if self.closed:
            return self.error(message.get("id") if isinstance(message, dict) else None, -32600, "Server is closed")
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or \
                not isinstance(message.get("method"), str):
            return self.error(None, -32600, "Invalid request")
        rid, method = message.get("id"), message["method"]
        if "id" not in message:
            if method == "notifications/initialized" and self.initialized:
                self.ready = True
            return None
        if isinstance(rid, bool) or not isinstance(rid, (str, int)):
            return self.error(None, -32600, "Request id must be text or an integer")
        params = message.get("params", {})
        if not isinstance(params, dict):
            return self.error(rid, -32602, "Parameters must be an object")
        if method == "initialize":
            if self.initialized:
                return self.error(rid, -32600, "Server is already initialized")
            if not isinstance(params.get("protocolVersion"), str) or not isinstance(params.get("capabilities"), dict) or \
                    not isinstance(params.get("clientInfo"), dict):
                return self.error(rid, -32602, "Initialization needs protocolVersion, capabilities and clientInfo")
            if self.write and any(not isinstance(params["clientInfo"].get(k), str) or
                                  not params["clientInfo"][k].strip() for k in ("name", "version")):
                return self.error(rid, -32602, "Write access needs a clientInfo name and version")
            self.client_info = {k: params["clientInfo"][k] for k in ("name", "version") if k in params["clientInfo"]}
            self.initialized = True
            version = params["protocolVersion"] if params["protocolVersion"] in VERSIONS else VERSIONS[-1]
            result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "translucent", "version": "0.1.0"},
                      "instructions": ("Open a campaign, run recorded measurement tools, and propose findings to the gate. "
                                       "Only the gate decides verdicts. SAM follows TRANSLUCENT_SAM (strict replay by default). "
                                       "Loop campaigns: open_loop_campaign, propose_experiment, then wait_for_request; "
                                       + (f"approvals are recorded as {self.operator}, the operator who started this server."
                                          if self.operator else
                                          "a person approves each experiment over HTTP or in the web app.")
                                       if self.write else "Read recorded evidence only. No tool runs experiments or calls providers.")}
        elif method == "ping":
            result = {}
        elif not self.ready:
            return self.error(rid, -32600, "Initialize the server before calling tools")
        elif method == "tools/list":
            if params.get("cursor"):
                return self.error(rid, -32602, "There are no further tools")
            result = {"tools": self.tools}
        elif method == "tools/call":
            if not isinstance(params.get("name"), str) or params["name"] not in {t["name"] for t in self.tools}:
                return self.error(rid, -32602, "Unknown tool")
            try:
                value = self._call(params["name"], params.get("arguments", {}))
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                          "isError": bool(isinstance(value, dict) and (value.get("ok") is False or
                                          value.get("status") in {"tool_failure", "invalid_result"}))}
            except (OSError, ValueError, LookupError, TypeError, RecursionError, sqlite3.Error) as exc:
                result = {"content": [{"type": "text", "text": " ".join(str(exc).splitlines())}], "isError": True}
        else:
            return self.error(rid, -32601, "Method not found")
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    @staticmethod
    def error(rid, code, message):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def add_arguments(parser):
    """The server's flags, shared by `python -m translucent.mcp_server` and `translucent mcp`."""
    parser.add_argument("--ledger", type=Path,
                        default=Path(os.environ.get("TRANSLUCENT_LEDGER_DIR", "state/ledger")))
    parser.add_argument("--write", action="store_true", help="Enable recorded tools and gated findings")
    parser.add_argument("--adopt", action="store_true", help="Allow writes to existing unfinished campaigns")
    parser.add_argument("--state", type=Path, help="Ignored: kept for older configurations (MCP writers no longer "
                                                    "lock the API's state; several writers share a ledger)")
    parser.add_argument("--operator", metavar="NAME",
                        help="Offer the person tools (approve, reject, revise, submit results, amend, note), recorded "
                             "as this person: you, the local operator, vouch for the name. Needs --write")
    parser.add_argument("--import-root", type=Path, help="The one folder import_dataset and submit_result may read")
    return parser


def main(argv=None):
    parser = argparse.ArgumentParser(description="Translucent MCP server (stdio; read-only unless --write)")
    add_arguments(parser)
    return run(parser.parse_args(argv), parser)


def run(args, parser):
    if not (args.ledger / "ledger.sqlite").is_file():
        parser.error("The configured ledger does not exist")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        protocol = Protocol(args.ledger, write=args.write, adopt=args.adopt, state_dir=args.state,
                            operator=args.operator, import_root=args.import_root)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    try:
        for line in sys.stdin:
            try:
                message = json.loads(line)
                response = protocol.handle(message)
            except (ValueError, UnicodeError, RecursionError):
                response = protocol.error(None, -32700, "Invalid JSON")
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        protocol.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
