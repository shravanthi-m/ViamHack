"""Import a taught arm track into a canonical format, inspect it, and replay it.

Standalone companion to teach_path.py: no project imports, its own connection.

    python -m experiments.record_replay.replay_path import ~/Downloads/coconut_pour_clean_full.json --name coconut_pour
    python -m experiments.record_replay.replay_path show experiments/record_replay/tracks/coconut_pour.json
    python -m experiments.record_replay.replay_path run experiments/record_replay/tracks/coconut_pour.json              # validate only
    python -m experiments.record_replay.replay_path run experiments/record_replay/tracks/coconut_pour.json --execute    # moves the arm

Replay is opt-in: without --execute nothing connects and nothing moves. With it,
the arm must already be near the first waypoint (or pass --goto-start), torque
must be back ON (manual / free-drive mode OFF), and you confirm at a prompt.
"""
import argparse
import asyncio
import hashlib
import json
import math
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_ADDRESS = 'armfarm7-main.310sld03v2.viam.cloud'
FORMAT = 'joint-track/1'
SAMPLE_KEYS = ('waypoints', 'samples', 'points', 'trajectory', 'path', 'track', 'data')
JOINT_KEYS = ('joints', 'joint_positions', 'positions', 'values', 'angles', 'position')
TIME_KEYS = ('t', 'time', 'time_s', 'timestamp', 'seconds')


def parser():
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = cli.add_subparsers(dest='command', required=True)

    load = sub.add_parser('import', help='Normalize a recording into experiments/record_replay/tracks/<name>.json')
    load.add_argument('source', type=Path)
    load.add_argument('--name', help='Track name (default: source filename stem)')
    load.add_argument('--out', default=str(Path(__file__).with_name('tracks')))
    load.add_argument('--arm', default='arm')
    load.add_argument('--units', choices=['degrees', 'radians'], default='degrees')
    load.add_argument('--replace', action='store_true')

    show = sub.add_parser('show', help='Print a track summary; no connection')
    show.add_argument('track', type=Path)
    show.add_argument('--tolerance-deg', type=float, default=2.0)

    run = sub.add_parser('run', help='Replay a track; --execute is required to move')
    run.add_argument('track', type=Path)
    run.add_argument('--arm', help='Arm component name (default: the track\'s)')
    run.add_argument('--execute', action='store_true', help='Actually move the physical robot')
    run.add_argument('--tolerance-deg', type=float, default=2.0,
                     help='Drop waypoints closer than this to the last kept one (0 keeps all)')
    run.add_argument('--speed-scale', type=float, default=1.0,
                     help='Playback speed relative to the recording (0.1..1)')
    run.add_argument('--speed-deg-s', type=float,
                     help='Ask the arm module to cap joint speed (UFactory set_speed)')
    run.add_argument('--no-pace', action='store_true',
                     help='Move as fast as the arm allows instead of honoring recorded dwell')
    run.add_argument('--goto-start', action='store_true',
                     help='Allow an initial move to the first waypoint from where the arm is now')
    run.add_argument('--start-tolerance-deg', type=float, default=10.0)
    run.add_argument('--max-step-deg', type=float, default=45.0,
                     help='Reject a track that jumps more than this between waypoints')
    run.add_argument('--joint-limit-deg', type=float, default=360.0)
    run.add_argument('--timeout', type=float, default=30.0, help='Per-move timeout in seconds')
    run.add_argument('--address',
                     default=os.environ.get('VIAM_MACHINE_ADDRESS', DEFAULT_ADDRESS))
    return cli


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def rows(raw):
    """Find the list of samples in whatever shape the recording arrived in."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in SAMPLE_KEYS:
            if isinstance(raw.get(key), list) and raw[key]:
                return raw[key]
        lists = [value for value in raw.values() if isinstance(value, list) and value]
        if len(lists) == 1:
            return lists[0]
    raise SystemExit('Could not find a list of samples; expected a key like samples or waypoints')


def joints(row, index):
    """Pull the joint vector out of one sample."""
    if isinstance(row, list):
        values = row
    elif isinstance(row, dict):
        found = next((row[key] for key in JOINT_KEYS if key in row), None)
        if isinstance(found, dict):  # e.g. {"joints": {"values": [...]}}
            found = next((found[key] for key in JOINT_KEYS if key in found), None)
        if found is None:
            raise SystemExit(f'Sample {index} has no joint values; keys were {sorted(row)}')
        values = found
    else:
        raise SystemExit(f'Sample {index} is a {type(row).__name__}, not a list or object')
    if not isinstance(values, list) or not values:
        raise SystemExit(f'Sample {index} has an empty joint vector')
    numbers = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SystemExit(f'Sample {index} has a non-numeric joint value: {value!r}')
        numbers.append(float(value))
    return numbers


def stamp(row, index, previous):
    if isinstance(row, dict):
        for key in TIME_KEYS:
            value = row.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                return float(value)
    return previous + 0.1  # No timing in the source: assume the 10 Hz teach default.


def normalize(raw, args):
    samples = rows(raw)
    scale = 180.0 / math.pi if args.units == 'radians' else 1.0
    waypoints, clock = [], -0.1
    for index, row in enumerate(samples):
        clock = stamp(row, index, clock)
        waypoints.append(dict(t=round(clock, 4), joints=[round(v * scale, 4) for v in joints(row, index)]))
    width = len(waypoints[0]['joints'])
    odd = [i for i, wp in enumerate(waypoints) if len(wp['joints']) != width]
    if odd:
        raise SystemExit(f'Joint count changes at sample {odd[0]}: expected {width}')
    base = waypoints[0]['t']
    for wp in waypoints:
        wp['t'] = round(wp['t'] - base, 4)
    if any(b['t'] < a['t'] for a, b in zip(waypoints, waypoints[1:])):
        raise SystemExit('Timestamps go backwards; clean the recording before importing')
    return waypoints


def stats(waypoints):
    width = len(waypoints[0]['joints'])
    columns = [[wp['joints'][i] for wp in waypoints] for i in range(width)]
    steps = [max(abs(a - b) for a, b in zip(x['joints'], y['joints']))
             for x, y in zip(waypoints, waypoints[1:])] or [0.0]
    return dict(joint_min=[round(min(c), 3) for c in columns],
                joint_max=[round(max(c), 3) for c in columns],
                joint_travel_deg=[round(sum(abs(b - a) for a, b in zip(c, c[1:])), 2) for c in columns],
                max_step_deg=round(max(steps), 3))


def simplify(waypoints, tolerance):
    if tolerance <= 0 or len(waypoints) < 3:
        return list(waypoints)
    kept = [waypoints[0]]
    for wp in waypoints[1:-1]:
        if max(abs(a - b) for a, b in zip(wp['joints'], kept[-1]['joints'])) >= tolerance:
            kept.append(wp)
    kept.append(waypoints[-1])
    return kept


def load_track(path):
    track = read(path)
    if not isinstance(track, dict) or track.get('format') != FORMAT:
        raise SystemExit(f'{path} is not a {FORMAT} file; run the import subcommand on it first')
    if not track.get('waypoints'):
        raise SystemExit('Track has no waypoints')
    return track


def check(track, args):
    width = track['joint_count']
    previous = None
    for index, wp in enumerate(track['waypoints']):
        values = wp['joints']
        if len(values) != width:
            raise SystemExit(f'Waypoint {index} has {len(values)} joints; track declares {width}')
        for value in values:
            if not math.isfinite(value) or abs(value) > args.joint_limit_deg:
                raise SystemExit(f'Waypoint {index} value {value} exceeds +/-{args.joint_limit_deg} deg')
        if previous is not None:
            step = max(abs(a - b) for a, b in zip(values, previous))
            if step > args.max_step_deg:
                raise SystemExit(f'Waypoint {index} jumps {step:.1f} deg; over --max-step-deg')
        previous = values


def do_import(args):
    name = args.name or args.source.stem
    output = Path(args.out) / f'{name}.json'
    if output.exists() and not args.replace:
        raise SystemExit(f'{output} exists; pass --replace to overwrite deliberately')
    body = args.source.read_bytes()
    waypoints = normalize(json.loads(body), args)
    summary = stats(waypoints)
    span = [abs(value) for wp in waypoints for value in wp['joints']]
    if args.units == 'degrees' and max(span) <= 3.2:
        print('Warning: every value is within +/-3.2; if this track is in radians, '
              're-import with --units radians')
    track = dict(format=FORMAT, name=name, arm=args.arm, units='degrees',
                 joint_count=len(waypoints[0]['joints']), waypoint_count=len(waypoints),
                 duration_s=waypoints[-1]['t'], imported_at=datetime.now(timezone.utc).isoformat(),
                 source=dict(file=str(args.source), sha256=hashlib.sha256(body).hexdigest(),
                             units=args.units, sample_count=len(waypoints)),
                 stats=summary, waypoints=waypoints)
    output.parent.mkdir(parents=True, exist_ok=True)
    write(output, track)
    print(f'Imported {len(waypoints)} waypoints, {track["duration_s"]:.1f}s, '
          f'{track["joint_count"]} joints -> {output}')
    describe(track, 2.0)


def describe(track, tolerance):
    kept = simplify(track['waypoints'], tolerance)
    print(json.dumps(dict(name=track['name'], arm=track['arm'], units=track['units'],
                          joint_count=track['joint_count'], duration_s=track['duration_s'],
                          waypoints=track['waypoint_count'],
                          after_tolerance={f'{tolerance}deg': len(kept)},
                          first=track['waypoints'][0]['joints'],
                          last=track['waypoints'][-1]['joints'],
                          **track['stats']), indent=2))


async def connect(address):
    from viam.robot.client import RobotClient
    api_key = os.environ.get('VIAM_API_KEY')
    api_key_id = os.environ.get('VIAM_API_KEY_ID')
    if not api_key or not api_key_id:
        raise SystemExit('Set VIAM_API_KEY and VIAM_API_KEY_ID (see .env.example) before running.')
    options = RobotClient.Options.with_api_key(api_key=api_key, api_key_id=api_key_id)
    return await asyncio.wait_for(RobotClient.at_address(address, options), 30)


async def play(arm, kept, args):
    from viam.proto.component.arm import JointPositions
    began = time.monotonic()
    for index, wp in enumerate(kept):
        await asyncio.wait_for(
            arm.move_to_joint_positions(JointPositions(values=wp['joints'])), args.timeout)
        if not args.no_pace:
            target = began + wp['t'] / args.speed_scale
            await asyncio.sleep(max(0.0, target - time.monotonic()))
        if index % 10 == 0 or index == len(kept) - 1:
            print(f'  {index + 1}/{len(kept)}  t={wp["t"]:.1f}s  elapsed={time.monotonic() - began:.1f}s')
    return time.monotonic() - began


async def execute(track, kept, args):
    from viam.components.arm import Arm
    from viam.proto.component.arm import JointPositions
    robot = await connect(args.address)
    interrupted = False
    try:
        arm = Arm.from_robot(robot, args.arm or track['arm'])
        now = list((await asyncio.wait_for(arm.get_joint_positions(), 10)).values)
        if len(now) != track['joint_count']:
            raise SystemExit(f'Arm reports {len(now)} joints; track has {track["joint_count"]}')
        gap = max(abs(a - b) for a, b in zip(now, kept[0]['joints']))
        print(f'Arm is at {[round(v, 2) for v in now]}')
        print(f'Track starts at {[round(v, 2) for v in kept[0]["joints"]]} ({gap:.1f} deg away)')
        if gap > args.start_tolerance_deg and not args.goto_start:
            raise SystemExit('Arm is not at the start of the track. Hand-move it there, or pass '
                             '--goto-start to let the arm travel there first.')
        print('Confirm the workspace is clear, the cup is placed, and manual/free-drive mode is OFF.')
        if (await asyncio.to_thread(input, 'Type run to move the arm: ')).strip().lower() != 'run':
            print('Aborted; nothing moved.')
            return
        if args.speed_deg_s:
            try:
                await asyncio.wait_for(arm.do_command({'set_speed': args.speed_deg_s}), 10)
            except Exception as exc:
                print(f'Warning: set_speed not accepted by this arm module ({exc}); '
                      'using the module default speed')
        if gap > args.start_tolerance_deg:
            print('Moving to the first waypoint...')
            await asyncio.wait_for(
                arm.move_to_joint_positions(JointPositions(values=kept[0]['joints'])), args.timeout)
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(play(arm, kept, args))
        loop.add_signal_handler(signal.SIGINT, task.cancel)
        try:
            print(f'Replaying {len(kept)} waypoints (Ctrl-C stops the arm)...')
            elapsed = await task
            print(f'Done in {elapsed:.1f}s (recorded {track["duration_s"]:.1f}s).')
        except asyncio.CancelledError:
            interrupted = True
            print('\nInterrupted.')
        except Exception:
            interrupted = True
            raise
        finally:
            loop.remove_signal_handler(signal.SIGINT)
            if interrupted:
                # Halt motion only; never auto-open a gripper that may be holding something.
                print('Stopping the arm...')
                await asyncio.wait_for(robot.stop_all(), 10)
    finally:
        await robot.close()


def do_run(args):
    track = load_track(args.track)
    if not 0.1 <= args.speed_scale <= 1.0:
        raise SystemExit('--speed-scale must be between 0.1 and 1.0')
    check(track, args)
    kept = simplify(track['waypoints'], args.tolerance_deg)
    print(json.dumps(dict(track=str(args.track), name=track['name'],
                          arm=args.arm or track['arm'], recorded_waypoints=track['waypoint_count'],
                          replay_waypoints=len(kept), recorded_duration_s=track['duration_s'],
                          speed_scale=args.speed_scale, paced=not args.no_pace,
                          max_step_deg=track['stats']['max_step_deg']), indent=2))
    if not args.execute:
        print('Offline validation only. No connection or motion. Add --execute to run.')
        return
    if not args.address:
        raise SystemExit('Set VIAM_MACHINE_ADDRESS or pass --address')
    asyncio.run(execute(track, kept, args))


def main():
    args = parser().parse_args()
    if args.command == 'import':
        do_import(args)
    elif args.command == 'show':
        describe(load_track(args.track), args.tolerance_deg)
    else:
        do_run(args)


if __name__ == '__main__':
    try:
        main()
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f'Not valid JSON: {exc}') from exc
