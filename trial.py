"""Minimal Viam trial CLI; physical execution always requires --execute."""
import argparse
import asyncio
import json
import signal
from pathlib import Path

from trials.runner import WAYPOINTS, episode, label, read, report, steps, validate, write


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--config', default='station.json')
    cli.add_argument('--runs', default='runs')
    sub = cli.add_subparsers(dest='command', required=True)
    sub.add_parser('inspect', help='Read resources, current pose, and camera samples; no movement')
    teach = sub.add_parser('teach', help='Save current gripper pose; no movement')
    teach.add_argument('waypoint', choices=WAYPOINTS)
    teach.add_argument('--replace', action='store_true')
    run = sub.add_parser('run')
    run.add_argument('--profile', default='profiles/cup.json')
    run.add_argument('--task', choices=['pick', 'pour'], default='pick')
    run.add_argument('--execute', action='store_true', help='Actually move the physical robot')
    run.add_argument('--repeat', type=int, default=1)
    score = sub.add_parser('label', help='Attach observed outcome to a recorded trial')
    score.add_argument('run', type=Path)
    score.add_argument('outcome', choices=['success', 'miss', 'slip', 'crush', 'spill', 'underpour', 'abort'])
    score.add_argument('--notes', default='')
    score.add_argument('--poured-g', type=float)
    sub.add_parser('report', help='Compare exact profiles and station configurations')
    sub.add_parser('stop', help='Request Viam StopAll')
    return cli


async def connected(args, config):
    from main import connect
    from trials.viam_io import ViamIO
    robot = await asyncio.wait_for(connect(), 30)
    try:
        if args.command == 'stop':
            await asyncio.wait_for(robot.stop_all(), 5)
            return
        io = ViamIO(robot, config)
        if args.command == 'inspect':
            from datetime import datetime, timezone
            output = Path(args.runs) / ('inspect-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
            info = {'resources': [dict(type=r.type, subtype=r.subtype, name=r.name) for r in robot.resource_names]}
            output.mkdir(parents=True)
            try:
                info['observation'] = await asyncio.wait_for(io.capture(output / 'observation'), 30)
            except Exception as exc:
                info['observation_error'] = str(exc)
            info['capabilities'] = await io.capabilities()
            write(output / 'inventory.json', info)
            print(json.dumps(info, indent=2))
            print(f'Saved {output}')
        elif args.command == 'teach':
            if config['waypoints'][args.waypoint] is not None and not args.replace:
                raise ValueError('Waypoint exists; use --replace to update deliberately')
            config['waypoints'][args.waypoint] = await io.pose()
            config['calibrated'] = False
            write(args.config, config)
            print(f'Saved {args.waypoint}; review calibration before executing')
        else:
            profile = read(args.profile)
            for index in range(args.repeat):
                if index:
                    answer = input('Reset object/fill to the same mass, clear workspace, and return to pregrasp. Type ready (or q): ')
                    if answer.strip() != 'ready':
                        break
                run = await episode(io, config, profile, args.task, args.runs)
                print(f'Execution completed; label observed outcome: python trial.py label {run} OUTCOME')
    finally:
        await robot.close()


def main():
    args = parser().parse_args()
    if args.command == 'label':
        label(args.run, args.outcome, args.notes, args.poured_g)
        return
    if args.command == 'report':
        print(json.dumps(report(args.runs), indent=2))
        return
    config = read(args.config)
    if args.command == 'run':
        if not 1 <= args.repeat <= 20:
            raise ValueError('--repeat must be 1..20; each repetition needs a physical reset')
        validate(config, read(args.profile), args.task)
        print(json.dumps({'task': args.task, 'steps': steps(args.task)}, indent=2))
        if not args.execute:
            print('Offline validation only. No connection or motion. Add --execute to run.')
            return
    async def run():
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
        await connected(args, config)
    asyncio.run(run())


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, FileNotFoundError) as exc:
        raise SystemExit(str(exc)) from exc
