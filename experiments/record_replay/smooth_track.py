"""Rebuild the approach phase of a joint track: smooth it, drop the dead pauses.

Offline only; nothing connects and nothing moves. The tail of the track (by
default everything from the moment the wrist first turns, i.e. the pour and the
return) is copied through verbatim, so only the travel up to the pour is
reshaped.

    python -m experiments.record_replay.smooth_track experiments/record_replay/tracks/coconut_pour.json --name coconut_pour_smooth

The rebuilt approach keeps the recorded route but removes idle samples and hand
jitter, then re-times it with an ease-in-out profile so it starts and ends at
zero speed. --report prints the before/after motion profile.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

FORMAT = 'joint-track/1'


def parser():
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument('track', type=Path)
    cli.add_argument('--name', help='Output track name (default: <input>_smooth)')
    cli.add_argument('--out', default=str(Path(__file__).with_name('tracks')))
    cli.add_argument('--replace', action='store_true')
    cli.add_argument('--pre-pour-pause', type=float, default=0.5,
                     help='Settle time between the end of the approach and the preserved tail')
    cli.add_argument('--approach-s', type=float,
                     help='Approach duration (default: derived from --max-speed-deg-s)')
    cli.add_argument('--max-speed-deg-s', type=float, default=12.0,
                     help='Peak joint speed of the rebuilt approach')
    cli.add_argument('--rate', type=float, default=10.0, help='Output samples per second')
    cli.add_argument('--idle-deg', type=float, default=0.15,
                     help='Samples within this of the last kept pose are idle and are dropped')
    cli.add_argument('--smooth-passes', type=int, default=8,
                     help='Binomial smoothing passes over the approach; 0 keeps the raw route')
    cli.add_argument('--preserve-joint', type=int, default=6,
                     help='1-based joint whose first motion marks the start of the preserved tail')
    cli.add_argument('--preserve-tol', type=float, default=0.05)
    cli.add_argument('--preserve-from', type=float,
                     help='Preserve from this timestamp instead of auto-detecting')
    cli.add_argument('--report', action='store_true', help='Print the motion profile of both tracks')
    return cli


def load(path):
    track = json.loads(Path(path).read_text())
    if track.get('format') != FORMAT:
        raise SystemExit(f'{path} is not a {FORMAT} file')
    return track


def write(path, value):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def gap(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


def split_at(waypoints, args):
    """First index of the tail that is copied through untouched."""
    if args.preserve_from is not None:
        found = next((i for i, wp in enumerate(waypoints) if wp['t'] >= args.preserve_from), None)
        if found in (None, 0):
            raise SystemExit('--preserve-from is outside the track')
        return found
    column = args.preserve_joint - 1
    if not 0 <= column < len(waypoints[0]['joints']):
        raise SystemExit(f'--preserve-joint {args.preserve_joint} is out of range')
    start = waypoints[0]['joints'][column]
    found = next((i for i, wp in enumerate(waypoints)
                  if abs(wp['joints'][column] - start) > args.preserve_tol), None)
    if found is None:
        raise SystemExit(f'Joint {args.preserve_joint} never moves; pass --preserve-from instead')
    return found


def dedwell(poses, idle):
    kept = [poses[0]]
    for pose in poses[1:]:
        if gap(pose, kept[-1]) > idle:
            kept.append(pose)
    if gap(poses[-1], kept[-1]) > 1e-9:
        kept.append(poses[-1])   # pin the seam pose exactly
    return kept


def smooth(poses, passes):
    points = [list(pose) for pose in poses]
    for _ in range(passes):
        if len(points) < 3:
            break
        moved = [points[0]]
        for index in range(1, len(points) - 1):
            moved.append([(a + 2 * b + c) / 4 for a, b, c
                          in zip(points[index - 1], points[index], points[index + 1])])
        moved.append(points[-1])
        points = moved
    return points


def lengths(poses):
    marks = [0.0]
    for a, b in zip(poses, poses[1:]):
        marks.append(marks[-1] + gap(a, b))
    return marks


def at(poses, marks, target):
    if target <= 0:
        return list(poses[0])
    if target >= marks[-1]:
        return list(poses[-1])
    index = next(i for i in range(1, len(marks)) if marks[i] >= target)
    span = marks[index] - marks[index - 1]
    ratio = 0.0 if span <= 0 else (target - marks[index - 1]) / span
    return [a + (b - a) * ratio for a, b in zip(poses[index - 1], poses[index])]


def ease(fraction):
    # Smoothstep: zero speed at both ends, peak speed 1.5x the average.
    return fraction * fraction * (3 - 2 * fraction)


def rebuild(track, args):
    waypoints = track['waypoints']
    split = split_at(waypoints, args)
    raw = [wp['joints'] for wp in waypoints[:split]]
    trimmed = dedwell(raw, args.idle_deg)
    shaped = smooth(trimmed, args.smooth_passes)
    marks = lengths(shaped)
    total = marks[-1]
    if total <= 0:
        raise SystemExit('The approach does not move; nothing to smooth')
    duration = args.approach_s or (1.5 * total / args.max_speed_deg_s)
    steps = max(2, round(duration * args.rate))
    approach = []
    for index in range(steps + 1):
        fraction = index / steps
        approach.append(dict(t=round(fraction * duration, 4),
                             joints=[round(value, 4)
                                     for value in at(shaped, marks, ease(fraction) * total)]))
    approach[-1]['joints'] = [round(value, 4) for value in shaped[-1]]  # exact seam

    offset = duration + args.pre_pour_pause - waypoints[split]['t']
    tail = [dict(t=round(wp['t'] + offset, 4), joints=list(wp['joints']))
            for wp in waypoints[split:]]

    # How far the smoothed route strays from the recorded one at equal progress.
    plain = lengths(trimmed)
    drift = max(gap(at(shaped, marks, marks[-1] * (mark / plain[-1])), pose)
                for mark, pose in zip(plain, trimmed)) if plain[-1] > 0 else 0.0
    return approach + tail, dict(
        split_index=split, split_t=waypoints[split]['t'], raw_approach_samples=split,
        moving_samples=len(trimmed), approach_waypoints=len(approach),
        approach_s=round(duration, 3), pre_pour_pause_s=args.pre_pour_pause,
        approach_travel_deg=round(total, 2),
        peak_speed_deg_s=round(1.5 * total / duration, 2),
        route_drift_deg=round(drift, 3), preserved_waypoints=len(tail),
        preserved_from_s=waypoints[split]['t'], preserved_duration_s=round(
            waypoints[-1]['t'] - waypoints[split]['t'], 3))


def stats(waypoints):
    width = len(waypoints[0]['joints'])
    columns = [[wp['joints'][i] for wp in waypoints] for i in range(width)]
    steps = [gap(a['joints'], b['joints']) for a, b in zip(waypoints, waypoints[1:])] or [0.0]
    return dict(joint_min=[round(min(c), 3) for c in columns],
                joint_max=[round(max(c), 3) for c in columns],
                joint_travel_deg=[round(sum(abs(b - a) for a, b in zip(c, c[1:])), 2) for c in columns],
                max_step_deg=round(max(steps), 3))


def profile(waypoints, label):
    print(f'--- {label} ---')
    previous = None
    for wp in waypoints:
        if previous:
            step = wp['t'] - previous['t']
            speed = gap(wp['joints'], previous['joints']) / step if step else 0.0
        else:
            speed = 0.0
        print(f'{wp["t"]:6.2f} ' + ' '.join(f'{v:7.2f}' for v in wp['joints'])
              + f' |{speed:6.1f} ' + '#' * min(30, int(speed / 2)))
        previous = wp


def main():
    args = parser().parse_args()
    track = load(args.track)
    name = args.name or f'{track["name"]}_smooth'
    output = Path(args.out) / f'{name}.json'
    if output.exists() and not args.replace:
        raise SystemExit(f'{output} exists; pass --replace to overwrite deliberately')
    waypoints, notes = rebuild(track, args)
    result = dict(format=FORMAT, name=name, arm=track['arm'], units=track['units'],
                  joint_count=track['joint_count'], waypoint_count=len(waypoints),
                  duration_s=waypoints[-1]['t'],
                  imported_at=datetime.now(timezone.utc).isoformat(),
                  source=dict(file=str(args.track), track=track['name'],
                              sha256=track.get('source', {}).get('sha256')),
                  derived=dict(tool='smooth_track.py', parent=track['name'],
                               strategy='rebuild approach with an ease-in-out profile, drop idle '
                                        'samples and hand jitter, shorten the pre-pour pause; '
                                        'copy the wrist reset, pour and return verbatim',
                               smooth_passes=args.smooth_passes, idle_deg=args.idle_deg,
                               rate_hz=args.rate, **notes),
                  stats=stats(waypoints), waypoints=waypoints)
    output.parent.mkdir(parents=True, exist_ok=True)
    write(output, result)
    print(f'{track["name"]}: {track["waypoint_count"]} waypoints, {track["duration_s"]:.1f}s')
    print(f'{name}: {len(waypoints)} waypoints, {result["duration_s"]:.1f}s -> {output}')
    print(json.dumps(notes, indent=2))
    if args.report:
        profile(track['waypoints'], f'{track["name"]} (original)')
        profile(waypoints, f'{name} (rebuilt)')


if __name__ == '__main__':
    main()
