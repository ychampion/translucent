"""MCP stdio server. Writes require --write; stdout contains JSON-RPC messages only."""
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

from translucent.verify import evidence, snapshot, verify
from translucent.ledger import Ledger, TABLES
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
    def __init__(self, root, *, write=False, adopt=False, state_dir=None):
        self.root, self.initialized, self.ready = Path(root).resolve(), False, False
        self.write, self.adopt = write, adopt
        self.client_info, self.owned, self.step = {}, set(), 0
        self.locks, self.closed = [], False
        self.tools = TOOLS + (_write_tools() if write else [])
        if adopt and not write:
            raise ValueError("--adopt requires --write")
        if write:
            from translucent.server import DEFAULT_STATE, _StateLock
            if not (self.root / "ledger.sqlite").is_file():
                raise ValueError("The configured ledger does not exist")
            state = Path(state_dir or os.environ.get("TRANSLUCENT_STATE_DIR", DEFAULT_STATE)).resolve()
            state.mkdir(parents=True, exist_ok=True)
            try:
                self.locks.append(_StateLock(state / "server.lock"))
                self.locks.append(_StateLock(self.root / "mcp.lock"))
            except (OSError, RuntimeError) as exc:
                self.close()
                raise ValueError("Cannot start MCP writes: another server owns this ledger or API state directory. "
                                 "Stop that writer first; use the API server's --state directory.") from exc

    def close(self):
        while self.locks:
            self.locks.pop().close()
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
                ledger.emit(campaign.id, "campaign.started", actor, campaign.title, detail=campaign.objective,
                            data={"via": "mcp", "clientInfo": self.client_info})
                self.owned.add(campaign.id)
                return campaign.model_dump(mode="json")
            campaign = self._campaign(ledger, arguments["campaign_id"], writing=True)
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
                                       "Only the gate decides verdicts. SAM follows TRANSLUCENT_SAM (strict replay by default)."
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
            except (OSError, ValueError, KeyError, TypeError, RecursionError, sqlite3.Error) as exc:
                result = {"content": [{"type": "text", "text": " ".join(str(exc).splitlines())}], "isError": True}
        else:
            return self.error(rid, -32601, "Method not found")
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    @staticmethod
    def error(rid, code, message):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Translucent MCP server (stdio; read-only unless --write)")
    parser.add_argument("--ledger", type=Path,
                        default=Path(os.environ.get("TRANSLUCENT_LEDGER_DIR", "state/ledger")))
    parser.add_argument("--write", action="store_true", help="Enable recorded tools and gated findings")
    parser.add_argument("--adopt", action="store_true", help="Allow writes to existing unfinished campaigns")
    parser.add_argument("--state", type=Path, help="API state directory to lock (same as translucent serve --state)")
    args = parser.parse_args(argv)
    if not (args.ledger / "ledger.sqlite").is_file():
        parser.error("The configured ledger does not exist")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        protocol = Protocol(args.ledger, write=args.write, adopt=args.adopt, state_dir=args.state)
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
