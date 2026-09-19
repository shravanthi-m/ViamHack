"""Agent/operator entry points. Run from the repository root with python -m runtime."""
import argparse
import asyncio
import copy
import json
import signal
from datetime import datetime, timezone

from primitives import camera, motion
from primitives.registry import catalog, implementations
from primitives.types import Context
from . import calibration
from pathlib import Path

from .config import DEFAULT_CONFIG, ROOT, read_json, validate_config
from .compaction import COMPACT_TRACK, HOLD_PHASES, TRACK, compact_episode, describe, latest_episode
from .reference import describe as reference_describe, write_reference
from .connection import connect
from .orchestrator import (describe_plan, missing_implementations, require_implementations,
                           require_localization_profiles, run_plan, validate_plan)

LOCAL_CONFIG = ROOT / 'config/local.json'


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--config', default=str(DEFAULT_CONFIG), help='Full task config (no implicit merge)')
    sub = cli.add_subparsers(dest='command', required=True)
    observer = sub.add_parser('observer', help='Varista UI with optional supervised Claudia routine')
    observer.add_argument('--port', type=int, default=8765)
    observer.add_argument('--image', help='Optional saved station image to preview')
    observer.add_argument('--observation', help='Optional saved detections matching --image exactly')
    observer.add_argument('--objects', help='Comma-separated detection IDs (default: coconut_water,pitcher,cup)')
    observer.add_argument('--run', help='Exact run directory whose events to display')
    observer.add_argument('--view', default='overhead',
                          help='Camera view streamed automatically when the website opens (default: overhead)')
    observer.add_argument('--demo-pack', help='Exact frozen coconut/pitcher demo pack')
    observer.add_argument('--enable-demo-execution', action='store_true',
                          help='Allow UI task requests to execute with operator checks on the webpage')
    observer.add_argument('--require-reset', action='store_true',
                          help='Preserve a recovery block at startup: only supervised Reset may run until it completes')
    sub.add_parser('localization-status', help='Show measured localization prerequisites offline')
    vp = sub.add_parser('vision-demo-profile', help='Fit/check measured image offsets offline; no robot connection')
    vp.add_argument('object', choices=('coconut_water', 'pitcher'))
    vp.add_argument('measurements', help='JSON with texture patches and measured image placements')
    vp.add_argument('--out', required=True, help='New measured profile JSON')
    vp.add_argument('--demonstrations', default=str(ROOT / 'demonstrations'))
    vc = sub.add_parser('vision-demo-check', help='Check a saved image against a measured replay profile, offline')
    vc.add_argument('object', choices=('coconut_water', 'pitcher'))
    vc.add_argument('image')
    vc.add_argument('--demonstrations', default=str(ROOT / 'demonstrations'))
    perceive = sub.add_parser('perceive', help='Analyze a saved image through OpenRouter; never connects to robot')
    perceive.add_argument('image')
    perceive.add_argument('--objects', help='Comma-separated detection IDs (default: coconut_water,pitcher,cup)')
    perceive.add_argument('--out', required=True, help='Save image observations, normally under runs/')
    pack = sub.add_parser('prepare-demo', help='Freeze a supervised demo pack offline; no connection')
    pack.add_argument('--out', required=True, help='New directory, normally under runs/')
    pack.add_argument('--demonstrations', default=str(ROOT / 'demonstrations'))
    pack.add_argument('--compact', action='store_true')
    prepared = sub.add_parser('prepared-demo', help='Verify a frozen pack offline; --execute moves')
    prepared.add_argument('pack')
    prepared.add_argument('--execute', action='store_true')
    prepared.add_argument('--runs', default=str(ROOT / 'runs/teach_replay'))
    fixed = sub.add_parser('fixed-task', help='Offline check or supervised signature/reset/held-object shake')
    fixed.add_argument('task', choices=('signature', 'reset', 'shake'))
    fixed.add_argument('--pack', required=True, help='Exact frozen demo pack')
    fixed.add_argument('--execute', action='store_true')
    fixed.add_argument('--runs', default=str(ROOT / 'runs/fixed_tasks'))
    sub.add_parser('tools', help='Print the primitive catalog and implementation readiness')
    brief = sub.add_parser('brief', help='Prepare a language-planning prompt for your coding/LLM agent')
    brief.add_argument('instruction')
    brief.add_argument('--out', help='Also write the prompt as one JSON file an agent can read '
                                     'without scrolling a terminal')
    check = sub.add_parser('validate', help='Validate a plan offline')
    check.add_argument('plan')
    check.add_argument('--executable', action='store_true',
                       help='Also fail when the plan needs a primitive no team implementation '
                            'provides, which validation alone does not check')
    run = sub.add_parser('run', help='Mock by default; --execute calls team implementations')
    run.add_argument('plan', nargs='?', default=str(ROOT / 'demos/fixed.json'))
    run.add_argument('--execute', action='store_true')
    run.add_argument('--runs', default=str(ROOT / 'runs'))
    sub.add_parser('inspect', help='Read Viam resources and frame configuration; no movement')
    teach = sub.add_parser('teach', help='Record a hand-guided demonstration; never commands motion')
    teach.add_argument('skill')
    teach.add_argument('--demonstrations', default=str(ROOT / 'demonstrations'))
    teach.add_argument('--hz', type=float, default=5)
    teach.add_argument('--camera-hz', type=float, default=2,
                       help='Camera captures per second during each trajectory phase (0.1..5; default 2)')
    teach.add_argument('--grasp-type', default='side')
    teach.add_argument('--notes', default='')
    teach.add_argument('--resume', nargs='?', const='latest', metavar='EPISODE',
                       help='Resume latest interrupted episode, or a specific episode directory name')
    replay = sub.add_parser('replay-demo', help='Validate taught coconut/pitcher demo; --execute moves')
    replay.add_argument('--demonstrations', default=str(ROOT / 'demonstrations'))
    replay.add_argument('--execute', action='store_true')
    replay.add_argument('--runs', default=str(ROOT / 'runs/teach_replay'))
    replay.add_argument('--pause-s', type=float, default=0.2)
    replay.add_argument('--max-xy-mm', type=float, default=20)
    replay.add_argument('--max-z-mm', type=float, default=10)
    replay.add_argument('--max-step-deg', type=float, default=20)
    replay.add_argument('--compact', action='store_true',
                        help='Replay the derived trajectory_compact.json tracks instead of the '
                             'full taught ones (run the compact command first)')
    agent = sub.add_parser('agent-run', help='Execute an agent-authored plan on the machine '
                                             'behind one gate; no mock stage')
    agent.add_argument('plan')
    agent.add_argument('--runs', default=str(ROOT / 'runs'))
    back = sub.add_parser('put-back', help='Unattended: set the held object down at its taught '
                                          'place pose and return to the origin; no operator gate')
    back.add_argument('skill')
    back.add_argument('--demonstrations', default=str(ROOT / 'demonstrations'))
    back.add_argument('--runs', default=str(ROOT / 'runs/put_back'))
    back.add_argument('--approach-mm', type=float, default=60,
                      help='Height of the hover pose above the taught place, descended and '
                           'retraced vertically because the held object is not in the '
                           'collision model (default 60)')
    back.add_argument('--dry-run', action='store_true',
                      help='Print the poses and move nothing')
    small = sub.add_parser('compact', help='Derive smaller replay tracks from taught episodes; '
                                           'no connection, no movement, raw episodes untouched')
    small.add_argument('skills', nargs='*', default=['coconut_water', 'pitcher'],
                       help='Objects to compact (default: the demo pair)')
    small.add_argument('--demonstrations', default=str(ROOT / 'demonstrations'))
    small.add_argument('--tol-deg', type=float, default=1.0,
                       help='Worst per-joint gap a dropped waypoint may leave against the '
                            'straight chord the arm interpolates instead (default 1)')
    small.add_argument('--still-deg', type=float, default=0.1,
                       help='Per-joint change below which the arm counts as standing still (default 0.1)')
    small.add_argument('--budget-deg', type=float, default=12,
                       help='Largest joint step dropping waypoints may introduce; keep it under '
                            'the replay limit (default 12)')
    small.add_argument('--max-dwell-s', type=float, default=0.25,
                       help='Longest pause kept where the arm merely stood still (default 0.25)')
    small.add_argument('--emit', choices=['joint-track/1', 'pose-track/1'], default='joint-track/1',
                       help='joint-track/1 commands the taught joint vectors (no collision '
                            'planning, 20 degree step limit); pose-track/1 drives the taught tool '
                            'poses through the motion service, which plans continuous collision-'
                            'checked motion between them and may use another arm configuration')
    small.add_argument('--pair-age-s', type=float, default=1.0,
                       help='Furthest a captured scene may sit from a waypoint and still be '
                            'paired with it in the reference trace (default 1)')
    small.add_argument('--keep-dwell', default=','.join(HOLD_PHASES),
                       help='Phases whose taught timing is preserved because the dwell does '
                            f'physical work (default {",".join(HOLD_PHASES)}; empty string for none)')
    bounds = sub.add_parser('calibrate', help='Derive the workspace from configured '
                                              'obstacle geometry; no movement')
    bounds.add_argument('--write', action='store_true',
                        help=f'Save the derived bounds to {LOCAL_CONFIG.name} and mark it calibrated')
    bounds.add_argument('--margin-mm', type=float,
                        help='Clearance kept from every configured obstacle, on top of the '
                             'tool extent (default: calibration.margin_mm)')
    bounds.add_argument('--reach-mm', type=float,
                        help='Cap on tool distance from the arm base, for the faces no '
                             'obstacle bounds (default: calibration.reach_mm)')
    bounds.add_argument('--anchor', help='x,y,z in mm that the workspace must contain '
                                         '(default: the taught origin, else the tool now)')
    return cli


def anchor_point(text):
    values = [float(part) for part in text.split(',')]
    if len(values) != 3:
        raise ValueError('--anchor takes x,y,z in mm')
    return tuple(values)


def save_workspace(config, result):
    """Derived bounds are local calibration: they belong in the config Git ignores."""
    stored = read_json(LOCAL_CONFIG) if LOCAL_CONFIG.exists() else copy.deepcopy(config)
    stored['workspace_mm'] = result['workspace_mm']
    stored['calibrated'] = True
    stored['calibration'] = {
        'margin_mm': result['margin_mm'],
        'reach_mm': result['reach_mm'],
        'derived': {'source': 'viam_frame_system',
                    'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    **{key: result[key] for key in
                       ('bounds_from', 'anchor_mm', 'arm_base_mm', 'tool_mm',
                        'obstacles', 'skipped')}},
    }
    validate_config(stored, execute=True)  # Never write a config the executor would refuse.
    LOCAL_CONFIG.write_text(json.dumps(stored, indent=2, allow_nan=False) + '\n')
    return LOCAL_CONFIG


async def calibrate(args, config, robot):
    """Read the machine's configured geometry and turn it into task workspace bounds."""
    if args.anchor:
        anchor = anchor_point(args.anchor)
    elif motion.settings(config)['origin_pose'] is not None:
        anchor = tuple(motion.settings(config)['origin_pose'][axis] for axis in calibration.AXES)
    else:
        tool = await motion.tool_pose(Context(config, robot))
        anchor = tuple(tool[axis] for axis in calibration.AXES)
    result = calibration.workspace(await calibration.read_frames(robot), config, anchor,
                                   margin_mm=args.margin_mm, reach_mm=args.reach_mm)
    print(calibration.describe(result))
    if not args.write:
        print('\nNothing written. Review the bounds above, then re-run with --write.')
        return
    print(f'\nsaved to {save_workspace(config, result)}')
    print('Review it before any --execute run: these bounds are only as right as the '
          'machine geometry they came from.')


def check_resources(robot, config):
    expected = {'arm': ('component', 'arm'), 'gripper': ('component', 'gripper'),
                'camera': ('component', 'camera'), 'motion': ('service', 'motion')}
    available = {(r.type, r.subtype, r.name) for r in robot.resource_names}
    for role, name in config['resources'].items():
        if (*expected[role], name) not in available:
            raise ValueError(f'Configured {role} resource {name!r} not found; run inspect')
    for view, name in camera.views(config).items():
        if ('component', 'camera', name) not in available:
            raise ValueError(f'Camera {name!r} for the {view} view not found; run inspect')


async def agent_run(args, config):
    """Validate an agent-authored plan, then execute it on the machine behind one gate.

    There is no mock stage: fabricated observations only ever checked executor
    plumbing, and every check that can fail offline still runs first. Validation is
    the execute-grade one and implementations are required before connecting, so a
    plan that cannot run is rejected here rather than at the arm. The gate is the only
    thing between an agent-written plan and motion: it prints the whole plan, and
    refusal aborts before anything connects. Use `validate --executable` for a dry
    check that never reaches the machine.
    """
    from .demonstrations import confirm
    plan = read_json(args.plan)
    validate_plan(plan, config, execute=True)
    require_implementations(plan, implementations())
    require_localization_profiles(plan, config)
    print(f'Valid plan, every tool implemented: {len(plan["steps"])} steps.\n')
    print(describe_plan(plan))
    print('\nNothing below is a mock. The next confirmation runs these steps on the machine.')
    await confirm('Workspace and swept path clear, free-drive OFF, hand empty, '
                  'and every step above intended? Run this on the machine?')
    robot = await connect()
    try:
        check_resources(robot, config)
        print(await run_plan(plan, Context(config, robot), execute=True, runs=args.runs))
    finally:
        await asyncio.wait_for(robot.close(), 10)


async def connected(args, config, plan=None):
    robot = await connect()
    try:
        if args.command == 'inspect':
            from google.protobuf.json_format import MessageToDict
            frames = await asyncio.wait_for(robot.get_frame_system_config(), 15)
            print(json.dumps({
                'resources': [dict(type=r.type, subtype=r.subtype, name=r.name) for r in robot.resource_names],
                'frames': [MessageToDict(f, preserving_proto_field_name=True) for f in frames],
            }, indent=2))
        elif args.command == 'calibrate':
            check_resources(robot, config)
            await calibrate(args, config, robot)
        else:
            check_resources(robot, config)
            print(await run_plan(plan, Context(config, robot), execute=True, runs=args.runs))
    finally:
        await asyncio.wait_for(robot.close(), 10)


async def dispatch(args):
    config = read_json(args.config)
    validate_config(config)
    if args.command == 'fixed-task':
        from .fixed_tasks import RECIPES, validate
        validate(args.task, args.pack, args.config)
        print(' → '.join(RECIPES[args.task]))
        if not args.execute:
            print('Offline check passed. No connection or motion; physical entry conditions still required.')
            return
        from .observer_demo import ClaudiaDemo
        from uuid import uuid4
        demo = ClaudiaDemo(pack=args.pack, config_path=args.config, enabled=True)
        demo.selected = args.task
        demo.run_root = Path(args.runs) / uuid4().hex
        await demo._perform()
        print(demo.run_directory())
        return
    if args.command in ('vision-demo-profile', 'vision-demo-check'):
        from primitives import replay_vision
        from .replay import load_episode
        episode = load_episode(args.demonstrations, args.object, config, track_name=COMPACT_TRACK)
        if args.command == 'vision-demo-profile':
            result = replay_vision.build_profile(config, args.object, episode, args.measurements, args.out)
        else:
            # Offline diagnostic only: file age does not authorize a physical scene.
            offset, evidence = replay_vision.offset(config, args.object, episode, args.image,
                                                    datetime.now(timezone.utc).isoformat())
            result = dict(offset=offset, evidence=evidence, offline_only=True, motion_authorized=False)
        print(json.dumps(result, indent=2))
        return
    if args.command == 'localization-status':
        from primitives.localization import readiness
        print(json.dumps(readiness(config), indent=2))
        return
    if args.command == 'perceive':
        from primitives.vision import observe, select_objects
        output = Path(args.out)
        if output.exists():
            raise ValueError('Use a new output path to preserve earlier perception evidence')
        result = await asyncio.to_thread(observe, Path(args.image).read_bytes(), select_objects(config, args.objects))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
        print(json.dumps(result, indent=2))
        return
    if args.command == 'prepare-demo':
        from .demo_pack import prepare
        manifest = prepare(args.config, args.demonstrations, args.out, compact=args.compact)
        print(json.dumps(manifest['episodes'], indent=2))
        print(f'Prepared candidate at {args.out}. No connection or motion. '
              'Physical rehearsal and all operator gates remain required.')
        return
    if args.command == 'prepared-demo':
        from .demo_pack import verify
        manifest = verify(args.pack, args.config)
        # Reuse the existing physical path and its gates without introducing a
        # second executor. No new settings or silent compact/full-track fallback.
        replay_args = argparse.Namespace(
            command='replay-demo', config=args.config,
            demonstrations=str(Path(args.pack).resolve() / 'demonstrations'),
            execute=args.execute, runs=args.runs, compact=manifest['compact'],
            pause_s=0.2, max_xy_mm=20, max_z_mm=10, max_step_deg=20)
        await dispatch(replay_args)
        return
    if args.command in ('teach', 'replay-demo'):
        from .demonstrations import ViamIO, require_station
        from .teaching import teach_skill
        from .replay import run_demo
        if args.command == 'teach':
            require_station(config, args.skill)
            from .teaching_connection import TeachingConnection
            io = TeachingConnection(config)
            try:
                print(await teach_skill(args.skill, io, config, root=args.demonstrations,
                                        hz=args.hz, camera_hz=args.camera_hz, resume=args.resume,
                                        grasp_type=args.grasp_type, notes=args.notes))
            finally:
                await io.aclose()
            return
        else:
            # Validate every episode offline before even connecting.
            await run_demo(None, config, root=args.demonstrations, pause_s=args.pause_s,
                           max_xy_mm=args.max_xy_mm, max_z_mm=args.max_z_mm,
                           max_step_deg=args.max_step_deg, compact=args.compact)
            if not args.execute:
                return
        robot = await connect()
        try:
            check_resources(robot, config)
            io = ViamIO(Context(config, robot))
            print(await run_demo(io, config, root=args.demonstrations, runs=args.runs,
                                 execute=True, pause_s=args.pause_s, max_xy_mm=args.max_xy_mm,
                                 max_z_mm=args.max_z_mm, max_step_deg=args.max_step_deg,
                                 compact=args.compact))
        finally:
            await asyncio.wait_for(robot.close(), 10)
    elif args.command == 'compact':
        blocked = False
        for skill in args.skills:
            path = latest_episode(args.demonstrations, skill)
            report = compact_episode(
                path, tol_deg=args.tol_deg, still_deg=args.still_deg, budget_deg=args.budget_deg,
                max_dwell_s=args.max_dwell_s, emit=args.emit,
                hold_phases=tuple(p for p in args.keep_dwell.split(',') if p))
            print(describe(skill, report))
            # The reference trace is advisory, so it is written even when replay is blocked;
            # it then describes the taught track it could not compact.
            trace = write_reference(path, COMPACT_TRACK if report['blocked'] is None else TRACK,
                                    max_pair_age_s=args.pair_age_s)
            print(reference_describe(skill, trace))
            blocked = blocked or bool(report['blocked'])
        print('\nDerived tracks only; taught episodes are unchanged. Replay them with '
              'replay-demo --compact, and inspect the adapted plan before any --execute run.')
        if blocked:
            raise ValueError('Some episodes need re-recording before they can be replayed')
    elif args.command == 'tools':
        print(json.dumps(catalog(config), indent=2))
    elif args.command == 'brief':
        guide = (ROOT / 'agents/orchestrator.md').read_text()
        planning = {'instruction': args.instruction, 'config': config, 'tools': catalog(config),
                    'example_plan': read_json(ROOT / 'demos/fixed.json')}
        if args.out:
            Path(args.out).write_text(json.dumps({'guide': guide, **planning}, indent=2) + '\n')
            print(f'wrote {args.out}')
            print('Compose a plan, then: python -m runtime --config CONFIG agent-run PLAN')
            return
        print(guide)
        print('\nPlanning input:\n' + json.dumps(planning, indent=2))
    elif args.command == 'agent-run':
        await agent_run(args, config)
    elif args.command == 'put-back':
        from .demonstrations import ViamIO
        from .putback import describe as describe_put_back, poses, put_back
        targets = poses(config, args.skill, root=args.demonstrations, approach_mm=args.approach_mm)
        print(describe_put_back(args.skill, targets, args.approach_mm))
        if args.dry_run:
            print('\nDry run: nothing moved.')
            return
        robot = await connect()
        try:
            check_resources(robot, config)
            print(await put_back(ViamIO(Context(config, robot)), config, args.skill,
                                 root=args.demonstrations, runs=args.runs,
                                 approach_mm=args.approach_mm))
        finally:
            await asyncio.wait_for(robot.close(), 10)
    elif args.command in ('inspect', 'calibrate'):
        await connected(args, config)
    else:
        plan = read_json(args.plan)
        execute = getattr(args, 'execute', False)
        validate_plan(plan, config, execute=execute or getattr(args, 'executable', False))
        if args.command == 'validate':
            print(f'Valid plan: {len(plan["steps"])} steps. No connection or motion.')
            missing = missing_implementations(plan)
            if missing:
                # Validation alone says nothing about this, so say it here rather than
                # letting an agent discover it at the machine.
                print('Not executable: no team implementation for ' + ', '.join(missing))
                if args.executable:
                    raise ValueError('Plan needs primitives that are not implemented')
            else:
                if args.executable:
                    require_localization_profiles(plan, config)
                print('Every tool has a team implementation.')
        elif execute:
            require_implementations(plan, implementations())  # Fail before connecting.
            require_localization_profiles(plan, config)
            await connected(args, config, plan)
        else:
            print(await run_plan(plan, Context(config), runs=args.runs))
            print('Mock completed with fabricated observations. No connection or motion.')


def main():
    args = parser().parse_args()

    async def run():
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
        await dispatch(args)

    try:
        if args.command == 'observer':
            from .observer import serve
            config = read_json(args.config)
            validate_config(config)
            serve(args, config)
            return
        asyncio.run(run())
    except (ValueError, KeyError, FileNotFoundError, RuntimeError, TimeoutError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == '__main__':
    main()
