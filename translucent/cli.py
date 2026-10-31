"""Translucent console entry point; budget/export/serve never call paid models."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from translucent.intervention_gate import install as _install_intervention_gate

_install_intervention_gate()

HERO = ("How fast do the lithium filaments grow across the solid electrolyte, does the battery short when they "
        "bridge it, and does it recover when they retract?")


def _existing_ledger(path, parser, hint="run, add-experiment and intervene create a new one"):
    """Commands that read a ledger never create one: a mistyped --ledger would otherwise open a new, empty ledger."""
    if not (Path(path) / "ledger.sqlite").is_file():
        parser.error(f"no ledger at {path}; check --ledger ({hint})")


def _new_ledger_note(path):
    """Commands that write records may create a ledger; they say so, so a mistyped path is visible."""
    if not (Path(path) / "ledger.sqlite").is_file():
        print(f"Creating a new ledger at {path}", file=sys.stderr)


def _team_server(data_root) -> None:
    """A data-root server for one team: the web app on this machine (bun run dev, port 3000) may call it, and people
    approve with the team key. Without TRANSLUCENT_TEAM_TOKEN the key is kept in the data root's team-key file, made
    once: whoever can read that folder can already write the ledgers in it."""
    if not os.environ.get("TRANSLUCENT_CORS_ORIGINS"):
        os.environ["TRANSLUCENT_CORS_ORIGINS"] = "http://localhost:3000,http://127.0.0.1:3000"
    if os.environ.get("TRANSLUCENT_TEAM_TOKEN"):
        print("Team key: the value of TRANSLUCENT_TEAM_TOKEN")
        return
    import secrets
    path = Path(data_root) / "team-key"
    try:                                   # made once, readable by its owner only
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(secrets.token_urlsafe(18) + "\n")
    os.environ["TRANSLUCENT_TEAM_TOKEN"] = path.read_text(encoding="utf-8").strip()
    print(f"Team key (the web app asks for it before an approval): {os.environ['TRANSLUCENT_TEAM_TOKEN']}  "
          f"[kept in {path}]")


def _add_experiment(args, parser):
    from pydantic import ValidationError
    from translucent.experiments import register
    from translucent.ledger import Ledger

    manifest = {k: getattr(args, k) for k in ("id", "title", "citation", "licence", "author")}
    manifest["video"] = args.video.name
    files = {args.video.name: args.video}
    has_scale = any(x is not None for x in (args.scale_um_per_px, args.scale_bar_px, args.scale_bar_um))
    if has_scale:
        if (args.scale_bar_px is None) != (args.scale_bar_um is None):
            parser.error("a scale bar needs both --scale-bar-px and --scale-bar-um")
        if args.scale_um_per_px is not None and args.scale_bar_um is not None:
            parser.error("provide either --scale-um-per-px or a scale bar")
        if not args.scale_source or not args.scale_source.strip():
            parser.error("a scale needs --scale-source: name the calibration image or microscope record")
        scale = {"source": args.scale_source}
        if args.scale_um_per_px is not None:
            scale["um_per_px"] = args.scale_um_per_px
        else:
            scale.update(bar_px=args.scale_bar_px, bar_um=args.scale_bar_um)
        if args.scale_uncertainty is not None:
            scale["uncertainty"] = args.scale_uncertainty
        manifest["scale"] = scale
    elif args.scale_source is not None or args.scale_uncertainty is not None:
        parser.error("provide a scale value with its source and uncertainty")
    if args.timestamps is not None or args.interval_s is not None:
        if not args.clock_source or not args.clock_source.strip():
            parser.error("--interval-s/--timestamps need --clock-source: name the lab record supporting acquisition timing")
        clock = {"source": args.clock_source}
        if args.timestamps is not None:
            if args.interval_uncertainty_s is not None:
                parser.error("--interval-uncertainty-s requires --interval-s")
            clock["timestamps_csv"] = args.timestamps.name
        else:
            clock["interval_s"] = args.interval_s
            if args.interval_uncertainty_s is not None:
                clock["uncertainty_s"] = args.interval_uncertainty_s
        manifest["clock"] = clock
    elif args.clock_source is not None or args.interval_uncertainty_s is not None:
        parser.error("provide --timestamps or --interval-s with the clock source")
    if args.voltage is not None:
        if args.voltage_offset_s is None:
            parser.error("--voltage needs --voltage-offset-s: the log time at the video's first frame")
        if not args.voltage_source or not args.voltage_source.strip():
            parser.error("--voltage needs --voltage-source: name the voltage log and the record of its alignment")
        manifest["voltage"] = {"csv": args.voltage.name, "offset_s": args.voltage_offset_s,
                               "source": args.voltage_source}
    elif args.voltage_offset_s is not None or args.voltage_source is not None:
        parser.error("provide --voltage with its offset and source")
    for path in (args.timestamps, args.voltage):
        if path is not None:
            if path.name in files and files[path.name].resolve() != path.resolve():
                parser.error(f"two input files are named {path.name}; give them different filenames")
            files[path.name] = path
    ledger = None
    try:
        _new_ledger_note(args.ledger)
        ledger = Ledger(args.ledger)
        video = register(ledger, manifest, files)
    except ValidationError as e:
        problems = [f"{'.'.join(str(k).replace('_', ' ') for k in p['loc']) or 'manifest'}: "
                    f"{p['msg'].removeprefix('Value error, ')}" for p in e.errors(include_url=False)]
        parser.error("; ".join(problems))
    except (ValueError, OSError) as e:
        parser.error(str(e))
    finally:
        if ledger is not None:
            ledger.db.close()
    print(f"Registered user/{args.id}: {args.title} ({video.id})")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="translucent")
    commands = parser.add_subparsers(dest="command", required=True)
    intervene = commands.add_parser("intervene", help="Run or replay a metered intervention planner")
    intervene.add_argument("system", choices=["nist"])
    intervene.add_argument("--profile", choices=["meta", "dev"], default="meta")
    intervene.add_argument("--budget", type=float, required=True)
    intervene.add_argument("--split", choices=["loco", "prequential", "lofo"], default="loco")
    intervene.add_argument("--ledger", type=Path)
    intervene.add_argument("--recordings", type=Path, help="Private request and response recording directory")
    intervene.add_argument("--replay", type=Path, help="Replay recorded requests exactly, without provider keys")
    intervene.add_argument("--muse-only", action="store_true", help="Run the no-tool model comparator")
    intervene.add_argument("--dry-run", action="store_true", help="Print call and cost estimates without a provider")
    intervene.add_argument("--steps", type=int, default=120, help="Tool calls per fold (default: 120)")
    bench = commands.add_parser('bench',help='Reproduce offline intervention benchmarks')
    bench.add_argument('system',choices=['nist'])
    bench.add_argument('--split',choices=['loco','lofo','prequential'])
    bench.add_argument('--lag-s','--lag',dest='lag_s',type=int,choices=[7,10],help='Select one registered heater-off lag sensitivity')
    bench.add_argument('--unsafe-ratio',type=int,choices=[5,10,20],help='Select one registered unsafe:escalation cost ratio')
    sensitivity=bench.add_mutually_exclusive_group()
    sensitivity.add_argument('--with-inferred',dest='inferred',action='store_true')
    sensitivity.add_argument('--without-inferred',dest='inferred',action='store_false')
    bench.set_defaults(inferred=None)
    bench.add_argument('--out',type=Path,default=Path('evals'))
    run = commands.add_parser("run", help="Run a campaign on a recorded movie (uses metered providers)")
    run.add_argument("--ledger", default="state/ledger")
    run.add_argument("--movie", type=int, default=4)
    run.add_argument("--profile", choices=("dev", "meta"), default="dev")
    run.add_argument("--budget", type=float, default=1.0)
    run.add_argument("--steps", type=int, default=30)
    run.add_argument("--memory", choices=("on", "off"), default="on")
    run.add_argument("--objective", default=HERO)
    run.add_argument("--ask-wait", type=float, default=0, help="seconds ask_person waits for a person (0: nobody)")
    resume = commands.add_parser("resume", help="Continue an interrupted or stopped campaign (uses metered providers)")
    resume.add_argument("campaign_id")
    resume.add_argument("--ledger", default="state/ledger")
    resume.add_argument("--budget", type=float, default=1.0)
    resume.add_argument("--profile", choices=("dev", "meta"), default="dev")
    export = commands.add_parser("export", help="Export an existing campaign without provider calls")
    export.add_argument("campaign_id")
    export.add_argument("out", type=Path)
    export.add_argument("--ledger", default="state/ledger")
    serve = commands.add_parser("serve", help="Serve the API; live campaigns are disabled by default")
    serve.add_argument("--ledger", default=None)
    serve.add_argument("--state", default=None)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--create-ledger", action="store_true", help="Create an empty ledger if --ledger has none")
    serve.add_argument("--data-root", default=None,
                       help="Serve every ledger under this folder (see `translucent init`) instead of one --ledger")
    serve.add_argument("--import-root", default=None,
                       help="The one server folder dataset imports may read by path (default: none)")
    init = commands.add_parser("init", help="Create a ledger under a data root, for serve --data-root (offline)")
    init.add_argument("ledger_id")
    init.add_argument("--data-root", required=True)
    init.add_argument("--title", default=None)
    imp = commands.add_parser("import", help="Import a team's records from a v2 (or v1) manifest (offline)")
    imp.add_argument("manifest", type=Path)
    imp.add_argument("--ledger", type=Path, required=True)
    imp.add_argument("--files", type=Path, default=None, help="Folder holding the files (default: the manifest's)")
    commands.add_parser("budget", help="Print read-only provider balances")
    verify = commands.add_parser("verify", help="Re-check recorded findings and files offline")
    verify.add_argument("path", type=Path)
    verify.add_argument("--campaign", default=None)
    add = commands.add_parser("add-experiment", help="Import your recording and declared lab records (offline)")
    for name in ("id", "title", "citation", "licence", "author"):
        add.add_argument("--" + name, required=True)
    add.add_argument("--video", type=Path, required=True)
    scale = add.add_mutually_exclusive_group()
    scale.add_argument("--scale-um-per-px", type=float)
    scale.add_argument("--scale-bar-px", type=float)
    add.add_argument("--scale-bar-um", type=float)
    add.add_argument("--scale-source")
    add.add_argument("--scale-uncertainty", type=float, help="Absolute uncertainty in micrometres per pixel")
    clock = add.add_mutually_exclusive_group()
    clock.add_argument("--timestamps", type=Path)
    clock.add_argument("--interval-s", type=float)
    add.add_argument("--interval-uncertainty-s", type=float)
    add.add_argument("--clock-source")
    add.add_argument("--voltage", type=Path)
    add.add_argument("--voltage-offset-s", type=float, help="Voltage-log time at the video's first frame")
    add.add_argument("--voltage-source")
    add.add_argument("--ledger", type=Path, default=Path("state/ledger"))
    experiments = commands.add_parser("experiments", help="List recordings imported into the ledger (offline)")
    experiments.add_argument("--ledger", type=Path, default=Path("state/ledger"))
    learning = commands.add_parser("learning-export",
                                   help="Write loop campaigns as learning episodes, JSONL (offline, read-only)")
    learning.add_argument("out", type=Path)
    learning.add_argument("--ledger", type=Path, required=True)
    learning.add_argument("--campaign", action="append", dest="campaigns", default=None,
                          help="A loop campaign to export (repeatable; default: every loop campaign of the ledger)")
    learning.add_argument("--scope", default=None, help="ledger:<id> (default: ledger:<the ledger folder's name>)")
    learning.add_argument("--include-series", action="store_true", help="Include raw series (omitted by default)")
    learning.add_argument("--heldout", choices=("campaign", "time"), default="campaign")
    learning.add_argument("--heldout-fraction", type=float, default=0.2)
    learning.add_argument("--seed", type=int, default=0)
    start = commands.add_parser("start", help="Set up a working campaign from nothing: ledger, records, objective")
    start.add_argument("example", choices=["fastcharge"])
    start.add_argument("--data-root", type=Path, required=True)
    start.add_argument("--ledger-id", default="lab")
    start.add_argument("--person", required=True, help="Who registers the campaign and approves its requests")
    start.add_argument("--planner", choices=["bo", "external", "muse"], default="bo",
                       help="bo: Bayesian optimisation, no model; external: your agent over MCP or HTTP; "
                            "muse: Muse Wonder (paid, needs --live)")
    start.add_argument("--auto-approve", action="store_true",
                       help="Approve the campaign's computational requests without a person (default: each waits)")
    start.add_argument("--serve", action="store_true", help="Then serve the data root on --host/--port")
    start.add_argument("--live", action="store_true",
                       help="With --serve: let the server's planner make paid model calls (needs "
                            "TRANSLUCENT_ADMIN_TOKEN)")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8000)
    from translucent import mcp_server
    mcp = mcp_server.add_arguments(commands.add_parser(
        "mcp", help="Serve a ledger to an agent over MCP on stdin/stdout (read-only unless --write)"))
    args = parser.parse_args(argv)
    if args.command == "mcp":
        return mcp_server.run(args, mcp)
    if args.command == "intervene":
        import math
        import time
        from translucent.intervention_chat import Settings
        from translucent.intervention_session import dry_run, run_intervention

        if not math.isfinite(args.budget) or args.budget <= 0 or args.steps < 1:
            intervene.error("budget must be positive and finite; steps must be positive")
        if args.recordings is not None and args.replay is not None:
            intervene.error("use either --recordings or --replay")
        settings = Settings(profile=args.profile, budget_usd=args.budget, step_cap=args.steps)
        if args.dry_run:
            print(json.dumps(dry_run(settings, split=args.split, muse_only=args.muse_only), indent=2))
            return 0
        if args.ledger is not None:
            _new_ledger_note(args.ledger)
        ledger_path = args.ledger or Path("state") / (
            "intervene-" + time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + f"-{time.time_ns() % 1000000000:09d}"
        )
        try:
            result = run_intervention(
                ledger_path, settings, split=args.split, muse_only=args.muse_only,
                recordings=args.recordings, replay=args.replay,
            )
        except (ValueError, OSError) as exc:
            intervene.error(str(exc))
        print(json.dumps(result, indent=2))
        return 0 if result.get("status") in ("finished", "complete", "completed") else 2
    if args.command=='bench':
        from translucent.intervention_bench import benchmark
        benchmark(args.out,split=args.split,inferred=args.inferred,lag_s=args.lag_s,unsafe_ratio=args.unsafe_ratio)
        return 0
    if args.command == "add-experiment":
        return _add_experiment(args, add)
    if args.command == "experiments":
        from translucent.experiments import list_experiments
        from translucent.ledger import Ledger
        _existing_ledger(args.ledger, experiments)
        ledger = None
        try:
            ledger = Ledger(args.ledger)
            print(json.dumps(list_experiments(ledger), indent=2))
            return 0
        except (ValueError, OSError) as e:
            experiments.error(str(e))
        finally:
            if ledger is not None:
                ledger.db.close()
    if args.command == "learning-export":
        from translucent.ledger import Ledger
        from translucent.loop_learning import export as learning_export
        _existing_ledger(args.ledger, learning)
        L = Ledger(args.ledger)
        try:
            done = learning_export(L, scope=args.scope or f"ledger:{L.root.name}", campaign_ids=args.campaigns,
                                   out=args.out, include_series=args.include_series, heldout=args.heldout,
                                   heldout_fraction=args.heldout_fraction, seed=args.seed)
        except ValueError as e:
            learning.error(str(e))
        finally:
            L.db.close()
        h = done["header"]
        print(f"{done['episodes']} episodes from {len(h['campaign_ids'])} campaign(s), held out by "
              f"{h['split']['rule']} (seed {h['split']['seed']}) -> {done['path']}")
        for x in h["excluded"]:
            print(f"Excluded {x['campaign_id']}: {x['reason']}", file=sys.stderr)
        return 0
    if args.command == "verify":
        from translucent.verify import verify as recheck
        if not args.path.exists():
            verify.error(f"no ledger, export or receipt at {args.path}")
        result = recheck(args.path, args.campaign)
        print(result.summary())
        for problem in result.problems:
            print(problem)
        for note in result.notes:
            print(note)
        return 0 if result.ok else 1
    if args.command == "budget":
        from translucent.budget import balances
        print(json.dumps(balances(), indent=2))
        return 0
    if args.command == "init":
        from translucent.workspace import LedgerExists, init_ledger
        try:
            path = init_ledger(args.data_root, args.ledger_id, title=args.title)
        except (ValueError, LedgerExists) as e:
            parser.error(str(e))
        print(f"Created ledger {args.ledger_id} at {path}")
        return 0
    if args.command == "import":
        from translucent import datasets
        from translucent.ledger import Ledger
        _existing_ledger(args.ledger, imp, "create one with `translucent init` or `serve --create-ledger`")
        try:
            record = datasets.import_dataset(Ledger(args.ledger), args.manifest, args.files)
        except ValueError as e:
            print(f"Not imported: {e}", file=sys.stderr)
            return 1
        print(f"Imported dataset {record.id}: {len(record.records)} record(s), "
              + ", ".join(f"{r.id} ({len(r.channels)} channels)" for r in record.records))
        return 0
    if args.command == "start":
        from translucent.start import StartError, start_fastcharge
        if args.planner == "muse" and args.serve and not args.live:
            parser.error("the muse planner makes paid model calls: add --live, or choose --planner bo or external")
        if args.live and not os.environ.get("TRANSLUCENT_ADMIN_TOKEN"):
            parser.error("--live needs TRANSLUCENT_ADMIN_TOKEN in the environment")
        try:
            out = start_fastcharge(args.data_root, ledger_id=args.ledger_id, planner=args.planner,
                                   person=args.person, auto_approve=args.auto_approve)
        except (StartError, ValueError) as e:
            print(f"Not started: {e}", file=sys.stderr)
            return 1
        print(f"Ledger {out['ledger']} at {out['path']}" + (" (created)" if out["ledger_created"] else ""))
        print(f"Dataset {out['dataset']}; failing baseline {out['baseline']}")
        print(f"Campaign {out['campaign_id']}, planner {out['planner']}")
        if args.planner == "external":
            print("Connect your agent over MCP (--adopt lets it write to this campaign):\n"
                  f"  uv run --directory {Path(__file__).resolve().parents[1]} python -m translucent.mcp_server "
                  f"--ledger {Path(out['path']).resolve()} --write --adopt")
        if not args.serve:
            print(f"Serve it: translucent serve --data-root {args.data_root}")
            return 0
        import uvicorn
        from translucent.server import create_app
        if args.live:
            os.environ["TRANSLUCENT_LIVE"] = "1"
        _team_server(args.data_root)
        print(f"Web app: cd web, then NEXT_PUBLIC_API_BASE=http://{args.host}:{args.port} bun run dev, "
              "and open http://localhost:3000")
        uvicorn.run(create_app(data_root=args.data_root), host=args.host, port=args.port)
        return 0
    if args.command == "serve":
        import uvicorn
        from translucent.ledger import Ledger
        from translucent.server import DEFAULT_LEDGER, create_app
        if args.data_root:
            if args.ledger or args.create_ledger:
                parser.error("serve takes --data-root or --ledger, not both")
            if not Path(args.data_root).is_dir():
                parser.error(f"--data-root {args.data_root} is not a folder; create ledgers with `translucent init`")
            _team_server(args.data_root)
            uvicorn.run(create_app(state_dir=args.state, data_root=args.data_root, import_root=args.import_root),
                        host=args.host, port=args.port)
            return 0
        ledger = Path(args.ledger or os.environ.get("TRANSLUCENT_LEDGER_DIR", DEFAULT_LEDGER))
        if args.create_ledger and not (ledger / "ledger.sqlite").is_file():
            Ledger(ledger).db.close()
            print(f"Created an empty ledger at {ledger}", file=sys.stderr)
        _existing_ledger(ledger, serve, "--create-ledger starts the server on a new, empty one")
        uvicorn.run(create_app(ledger, args.state, import_root=args.import_root), host=args.host, port=args.port)
        return 0
    from translucent import analysis, tools  # register analysis for CLI campaigns too
    from translucent.ledger import Ledger
    if args.command == "run":
        import math
        if not math.isfinite(args.budget) or args.budget <= 0 or args.steps <= 0 or args.movie <= 0:
            parser.error("budget, steps and movie must be positive; budget must be finite")
        if not math.isfinite(args.ask_wait) or args.ask_wait < 0:
            parser.error("ask-wait must be a finite number of seconds, 0 or more")
    if args.command == "resume":
        import math
        if not math.isfinite(args.budget) or args.budget <= 0:
            parser.error("budget must be positive and finite")
    if args.command in ("export", "resume"):
        _existing_ledger(args.ledger, export if args.command == "export" else resume)
    else:
        _new_ledger_note(args.ledger)
    L = Ledger(Path(args.ledger))
    try:
        if args.command == "export":
            from translucent.replay import export as export_bundle
            if L.get("campaign", args.campaign_id) is None:
                parser.error("campaign not found")
            bundle = export_bundle(L, args.campaign_id, args.out)
            print(f"{len(bundle.events)} events, {len(bundle.tracks)} tracks, "
                  f"{len(bundle.claims)} findings -> {args.out}")
            return 0
        from translucent.runtime import Campaigner, Settings, campaign_state, new_campaign
        if args.command == "resume":
            campaign = L.get("campaign", args.campaign_id)
            if campaign is None:
                parser.error("campaign not found")
            if campaign_state(L, campaign.id) == ("ended", "finished"):
                parser.error("campaign already finished")
            result = Campaigner(L, campaign.model_copy(update={"budget_usd": args.budget}),
                                Settings(profile=args.profile), resume=True).run()
            print(json.dumps(result, indent=1))
            return 0
        try:
            video = tools.import_video(L, Path(f"data/raw/reisecker2023/movie{args.movie}.mp4"),
                label=f"Movie {args.movie}", experiment=f"reisecker2023/movie{args.movie}", licence="CC BY 4.0",
                citation=f"Reisecker et al., Nat. Commun. 14, 2432 (2023), Supplementary Movie {args.movie}")
        except FileNotFoundError:
            parser.error(f"data/raw/reisecker2023/movie{args.movie}.mp4 is missing (movies are not in git); fetch it "
                         f"with: uv run python scripts/fetch_data.py reisecker2023/movie{args.movie}")
        campaign = new_campaign(L, args.objective, "Lithium filaments in a solid-state battery", [video.id],
                                memory=args.memory, budget_usd=args.budget)
        print("campaign", campaign.id)
        result = Campaigner(L, campaign, Settings(profile=args.profile, max_steps=args.steps,
                                                  ask_wait_s=args.ask_wait)).run()
        print(json.dumps(result, indent=1))
        for event in L.events(campaign.id):
            if event.type in {"step.started", "tool.finished", "claim.reviewed", "memory.learned", "campaign.finished"}:
                print(f"{event.seq:3d} {event.actor:18s} {event.title}")
        return 0
    finally:
        L.db.close()


if __name__ == "__main__":
    raise SystemExit(main())
