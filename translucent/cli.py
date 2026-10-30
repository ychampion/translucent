"""Translucent console entry point; budget/export/serve never call paid models."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from translucent.intervention_gate import install as _install_intervention_gate

_install_intervention_gate()

HERO = ("How fast do the lithium filaments grow across the solid electrolyte, does the battery short when they "
        "bridge it, and does it recover when they retract?")


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
    args = parser.parse_args(argv)
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
    if args.command == "verify":
        from translucent.verify import verify as recheck
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
    if args.command == "serve":
        import uvicorn
        from translucent.server import create_app
        uvicorn.run(create_app(args.ledger, args.state), host=args.host, port=args.port)
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
        video = tools.import_video(L, Path(f"data/raw/reisecker2023/movie{args.movie}.mp4"),
            label=f"Movie {args.movie}", experiment=f"reisecker2023/movie{args.movie}", licence="CC BY 4.0",
            citation=f"Reisecker et al., Nat. Commun. 14, 2432 (2023), Supplementary Movie {args.movie}")
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
