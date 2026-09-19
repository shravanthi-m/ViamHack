"""Derived compact replay tracks: a retimed subsequence of the taught waypoints.

Raw episodes stay immutable; compaction writes a sidecar. It never invents a joint
vector or a pose. Every output waypoint is a recorded waypoint, so replay's check of
the reached tool pose against the taught pose keeps its meaning and no fabricated
configuration is ever commanded. Compaction only drops waypoints that the arm's own
interpolation between the kept neighbours already covers to within a tolerance, and
shortens the time the arm stood still.

It cannot repair a pair of adjacent taught waypoints already further apart than the
replay step limit: no subsequence can subdivide them, and the motion between them
was never recorded. Those are reported and block the write. Only re-recording that
phase can supply the missing samples.
"""
import copy
from pathlib import Path

from .config import number, read_json
from .demonstrations import PHASES, joint_vector, now, write

TRACK = 'trajectory.json'
COMPACT_TRACK = 'trajectory_compact.json'
# Dwell inside these phases does physical work: liquid keeps flowing while the arm
# holds a tilt. Their recorded timing is preserved instead of capped.
HOLD_PHASES = ('pour',)


def step_deg(a, b):
    """Largest single-joint change between two waypoints: the replay limit's measure."""
    return max(abs(x - y) for x, y in zip(a['joints'], b['joints']))


def deviation_deg(chord_a, chord_b, row):
    """Worst per-joint gap between a waypoint and the straight joint-space chord.

    This is what the arm's own interpolation would miss if the waypoint were dropped.
    """
    a, b, p = chord_a['joints'], chord_b['joints'], row['joints']
    span = [y - x for x, y in zip(a, b)]
    scale = sum(v * v for v in span)
    u = 0.0 if scale == 0 else sum((q - x) * v for q, x, v in zip(p, a, span)) / scale
    u = min(1.0, max(0.0, u))
    return max(abs(q - (x + u * v)) for q, x, v in zip(p, a, span))


def phase_blocks(rows):
    """Contiguous phase blocks as (phase, offset, rows), in the taught order."""
    blocks, sequence = [], []
    for index, row in enumerate(rows):
        if not blocks or row['phase'] != blocks[-1][0]:
            blocks.append((row['phase'], index, []))
            sequence.append(row['phase'])
        blocks[-1][2].append(row)
    if sequence != list(PHASES):
        raise ValueError('Track must contain every phase exactly once and in order')
    return blocks


def simplify(rows, tol_deg, budget_deg):
    """Indices to keep: the endpoints, plus every waypoint the chords cannot represent.

    Douglas-Peucker in joint space. A span is also split when its chord would exceed
    the step budget, so dropping waypoints never manufactures a jump larger than
    replay accepts. Adjacent pairs cannot be split further and pass through as taught.
    """
    keep, pending = {0, len(rows) - 1}, [(0, len(rows) - 1)]
    while pending:
        i, j = pending.pop()
        if j <= i + 1:
            continue
        worst, split = -1.0, i + 1
        for k in range(i + 1, j):
            value = deviation_deg(rows[i], rows[j], rows[k])
            if value > worst:
                worst, split = value, k
        if worst > tol_deg or step_deg(rows[i], rows[j]) > budget_deg:
            keep.add(split)
            pending += [(i, split), (split, j)]
    return sorted(keep)


def retime(rows, kept, *, still_deg, max_dwell_s, hold):
    """Gap between kept waypoints: all the time the arm moved, capped time it did not.

    Idle time is measured on the original samples inside the span, so collapsing a
    hesitation never shortens the motion around it. Hold phases keep taught timing.
    """
    gaps, removed = [], 0.0
    for start, end in zip(kept, kept[1:]):
        total = rows[end]['t'] - rows[start]['t']
        if hold:
            gaps.append(total)
            continue
        idle = sum(rows[k + 1]['t'] - rows[k]['t'] for k in range(start, end)
                   if step_deg(rows[k], rows[k + 1]) < still_deg)
        kept_idle = min(idle, max_dwell_s)
        removed += idle - kept_idle
        gaps.append(max(total - idle + kept_idle, 0.0))
    return gaps, removed


def compact_track(track, *, tol_deg=1.0, still_deg=0.1, budget_deg=12.0, max_dwell_s=0.25,
                  replay_limit_deg=20.0, hold_phases=HOLD_PHASES, emit='joint-track/1'):
    """Return (compact track, report). The track is a subsequence of the input.

    `emit='pose-track/1'` keeps the same waypoints but declares them for the pose path,
    where replay drives each taught tool pose through the motion service instead of
    commanding the taught joint vector. The planner then supplies continuous motion
    between waypoints and checks collisions, at the cost of possibly reaching a taught
    pose in a different arm configuration. The joint step limit does not apply there.
    """
    if emit not in ('joint-track/1', 'pose-track/1'):
        raise ValueError('Emit format must be joint-track/1 or pose-track/1')
    for value, low, high, label in ((tol_deg, 0.01, 10, 'tol_deg'), (still_deg, 0.0, 5, 'still_deg'),
                                    (budget_deg, 0.1, 20, 'budget_deg'), (max_dwell_s, 0.0, 10, 'max_dwell_s'),
                                    (replay_limit_deg, 0.01, 20, 'replay_limit_deg')):
        number(value, low, high, label)
    if track.get('format') != 'joint-track/1':
        raise ValueError('Only joint-track/1 episodes carry the joint waypoints compaction works on')
    rows = track.get('waypoints')
    if not isinstance(rows, list) or not rows or track.get('waypoint_count') != len(rows):
        raise ValueError('Missing or inconsistent trajectory waypoints')
    for row in rows:
        joint_vector(row['joints'], track['joint_count'])

    out, phases, clock, idle_removed = [], [], 0.0, 0.0
    for phase, offset, block in phase_blocks(rows):
        kept = simplify(block, tol_deg, budget_deg)
        gaps, removed = retime(block, kept, still_deg=still_deg, max_dwell_s=max_dwell_s,
                               hold=phase in hold_phases)
        idle_removed += removed
        for position, index in enumerate(kept):
            row = copy.deepcopy(block[index])
            row['source_t'] = row['t']                  # Keep the taught clock inspectable.
            row['source_index'] = offset + index
            if out:
                # Replay re-bases its clock at every phase, so the operator's wait between
                # phases never paces anything; carrying it would only inflate duration_s.
                clock += gaps[position - 1] if position else min(row['source_t'] - out[-1]['source_t'],
                                                                 max_dwell_s)
            row['t'] = clock
            out.append(row)
        phases.append(dict(phase=phase, waypoints_in=len(block), waypoints_out=len(kept)))

    carried = [dict(phase=a['phase'], source_index=a['source_index'], t=round(a['source_t'], 3),
                    step_deg=round(step_deg(a, b), 3))
               for a, b in zip(out, out[1:])
               if b['source_index'] == a['source_index'] + 1 and step_deg(a, b) > replay_limit_deg]
    created = [step_deg(a, b) for a, b in zip(out, out[1:]) if b['source_index'] > a['source_index'] + 1]

    result = dict(track)
    result['format'] = emit
    result['waypoints'] = out
    result['waypoint_count'] = len(out)
    result['duration_s'] = out[-1]['t']
    columns = list(zip(*(row['joints'] for row in out)))
    result['stats'] = dict(joint_min=[min(c) for c in columns], joint_max=[max(c) for c in columns],
                           joint_travel_deg=[sum(abs(b - a) for a, b in zip(c, c[1:])) for c in columns],
                           max_step_deg=max((step_deg(a, b) for a, b in zip(out, out[1:])), default=0))
    result['compaction'] = dict(schema='compact-track/1', derived_from=TRACK, at=now(),
                                subsequence_of_taught_waypoints=True, phases=phases,
                                waypoints=dict(before=len(rows), after=len(out)),
                                duration_s=dict(before=track['duration_s'], after=result['duration_s']),
                                idle_seconds_removed=round(idle_removed, 3),
                                emitted_as=emit, carried_steps_over_limit=carried,
                                parameters=dict(tol_deg=tol_deg, still_deg=still_deg, budget_deg=budget_deg,
                                                max_dwell_s=max_dwell_s, replay_limit_deg=replay_limit_deg,
                                                hold_phases=list(hold_phases)))
    report = dict(phases=phases, carried=carried, emit=emit, idle_seconds_removed=idle_removed,
                  worst_created_step_deg=max(created, default=0.0),
                  before=dict(waypoints=len(rows), duration_s=track['duration_s'],
                              max_step_deg=max((step_deg(a, b) for a, b in zip(rows, rows[1:])), default=0)),
                  after=dict(waypoints=len(out), duration_s=result['duration_s'],
                             max_step_deg=result['stats']['max_step_deg']))
    return result, report


def verify(original, compact, *, replay_limit_deg=20.0):
    """Fail loudly unless the output is a faithful, replayable subsequence."""
    rows, out = original['waypoints'], compact['waypoints']
    cursor = -1
    for row in out:
        cursor = next((i for i in range(cursor + 1, len(rows))
                       if rows[i]['joints'] == row['joints'] and rows[i]['pose'] == row['pose']
                       and rows[i]['phase'] == row['phase']), None)
        if cursor is None:
            raise ValueError('Compact track is not a subsequence of the taught waypoints')
    for a, b in zip(out, out[1:]):
        if b['t'] < a['t']:
            raise ValueError('Compact timestamps go backwards')
    if out[-1]['t'] != compact['duration_s'] or compact['waypoint_count'] != len(out):
        raise ValueError('Inconsistent compact duration or waypoint count')
    phase_blocks(out)
    taught_release = next(row for row in reversed(rows) if row['phase'] == 'place')
    if next(row for row in reversed(out) if row['phase'] == 'place')['pose'] != taught_release['pose']:
        raise ValueError('Compaction dropped the taught release waypoint')
    if compact.get('format') == 'joint-track/1':
        over = max((step_deg(a, b) for a, b in zip(out, out[1:])), default=0)
        if over > replay_limit_deg:
            raise ValueError(f'Compact track still exceeds the replay step limit: {over:.2f} deg')


def latest_episode(root, skill):
    """Newest complete, operator-successful episode: the same one replay would select.

    Deliberately does not run replay's validation; a track that fails the step limit
    is exactly the input compaction is asked to inspect.
    """
    if not skill.isidentifier():
        raise ValueError('Skill name must be an identifier')
    for path in sorted((Path(root) / skill).glob('episode_*'), reverse=True):
        meta = read_json(path / 'metadata.json')
        if meta.get('status') == 'complete' and meta.get('success') is True:
            return path
    raise ValueError(f'No successful complete episode for {skill}; teach it first')


def compact_episode(directory, **options):
    """Write <episode>/trajectory_compact.json beside the immutable taught track.

    A track whose taught waypoints already jump further than replay allows is reported
    as blocked and not written: the remedy is re-recording, not post-processing.
    """
    directory = Path(directory)
    track = read_json(directory / TRACK)
    compact, report = compact_track(track, **options)
    report['blocked'] = None
    if report['carried'] and report['emit'] == 'pose-track/1':
        worst = max(item['step_deg'] for item in report['carried'])
        report['warning'] = (
            f"{len(report['carried'])} taught waypoint pair(s) jump up to {worst:.2f} deg of joint "
            'travel with nothing sampled in between. The pose path does not check that, so the '
            'planner chooses the route between them: inspect that span before executing.')
    elif report['carried']:
        worst = max(item['step_deg'] for item in report['carried'])
        phases = sorted({item['phase'] for item in report['carried']})
        report['blocked'] = (
            f"{len(report['carried'])} taught waypoint pair(s) already jump up to {worst:.2f} deg, over the "
            f"{options.get('replay_limit_deg', 20.0):g} deg replay limit, in: {', '.join(phases)}. "
            'No subsequence can subdivide an adjacent pair; the arm motion between them was never sampled. '
            'Re-record that demonstration, moving slower where the samples thinned out.')
        return report
    verify(track, compact, replay_limit_deg=options.get('replay_limit_deg', 20.0))
    write(directory / COMPACT_TRACK, compact)
    report['path'] = directory / COMPACT_TRACK
    report['bytes'] = dict(before=(directory / TRACK).stat().st_size,
                           after=(directory / COMPACT_TRACK).stat().st_size)
    return report


def describe(name, report):
    before, after = report['before'], report['after']
    drop = 100 * (1 - after['waypoints'] / before['waypoints'])
    lines = [f"{name}: {before['waypoints']} -> {after['waypoints']} waypoints ({drop:.0f}% fewer) "
             f"as {report['emit']}, "
             f"{before['duration_s']:.1f}s -> {after['duration_s']:.1f}s "
             f"({report['idle_seconds_removed']:.1f}s of standing still removed)",
             '  ' + '  '.join(f"{p['phase']} {p['waypoints_in']}->{p['waypoints_out']}"
                              for p in report['phases']),
             f"  worst step {after['max_step_deg']:.2f} deg (taught {before['max_step_deg']:.2f}; "
             f"largest introduced by dropping {report['worst_created_step_deg']:.2f})"]
    if report.get('bytes'):
        lines.append(f"  {report['bytes']['before']/1024:.1f} KiB -> {report['bytes']['after']/1024:.1f} KiB"
                     f"  wrote {report['path']}")
    if report.get('warning'):
        lines.append('  WARNING: ' + report['warning'])
    if report.get('blocked'):
        lines.append('  NOT WRITTEN: ' + report['blocked'])
    return '\n'.join(lines)
