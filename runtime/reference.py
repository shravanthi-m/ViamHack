"""Scene-paired reference traces: what the arm did, what it saw, what stayed unverified.

An advisory artifact, never an executable plan. `trajectory_compact.json` is what
replay runs; this pairs those waypoints with the camera frames captured beside them,
adds the phase structure and the taught grasp, and states plainly what the episode
does not establish. An agent reads it to decide what to do; it does not hand it to
the executor, which accepts validated plans and taught tracks only.

Frame and waypoint clocks do not share an origin, so pairing is done on absolute UTC
and every pairing carries how stale it is and how far the arm had moved by then.
"""
import json
from datetime import datetime
from pathlib import Path

from .compaction import step_deg
from .config import number, read_json
from .demonstrations import PHASES, now, write

TRACE = 'reference_trace.json'
FRAME_LOG = 'frames.jsonl'
# The recorded phases, in the words teaching prompts for. Intent only: no phase
# postcondition here was verified by a sensor.
PHASE_INTENT = {
    'lift': 'Raise the grasped object clear of the surface it started on',
    'transport': 'Carry it to the pour position over the fixed cup',
    'pour': 'Tilt and hold until the intended amount has left the source',
    'return_upright': 'Rotate the source back to upright before travelling',
    'transport_back': 'Carry it back towards the taught place spot',
    'place': 'Set it down at the original taught place and release',
    'retract': 'Withdraw the empty gripper to a clear pose',
}


def seconds(stamp):
    return datetime.fromisoformat(stamp).timestamp()


def position_error_mm(a, b):
    return sum((a[axis] - b[axis]) ** 2 for axis in ('x', 'y', 'z')) ** 0.5


def scenes(directory):
    """Captured frames with their absolute capture interval and the state at capture."""
    path = Path(directory) / FRAME_LOG
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        state = record.get('observation', {}).get('state') or {}
        if not state.get('timestamp'):
            continue                            # No state means nothing to pair against.
        out.append(dict(phase=record.get('phase'), attempt=record.get('attempt'),
                        captured_at=state['timestamp'], at=seconds(state['timestamp']),
                        state=state, frames=record['observation'].get('frames', {})))
    return sorted(out, key=lambda item: item['at'])


def views(frames):
    return {view: [image['path'] for image in payload.get('images', [])]
            for view, payload in frames.items()}


def pair_scene(row, candidates, max_age_s):
    """Nearest scene from the same phase and attempt, with how well it describes the row.

    `offset_s` is signed: negative means the picture predates the waypoint. The pose and
    joint errors say how much the arm had moved in between, which is the honest measure
    of whether the image shows the waypoint or merely its neighbourhood.
    """
    at = seconds(row['timestamp'])
    usable = [scene for scene in candidates
              if scene['phase'] == row['phase'] and scene['attempt'] == row.get('attempt')]
    if not usable:
        return None
    scene = min(usable, key=lambda item: abs(item['at'] - at))
    offset = scene['at'] - at
    if abs(offset) > max_age_s:
        return None
    state = scene['state']
    pairing = dict(captured_at=scene['captured_at'], offset_s=round(offset, 3), views=views(scene['frames']))
    if state.get('pose'):
        pairing['pose_error_mm'] = round(position_error_mm(state['pose'], row['pose']), 2)
    if state.get('joints') and row.get('joints'):
        pairing['joint_error_deg'] = round(step_deg(state, row), 3)
    return pairing


def phase_summary(phase, rows, paired):
    start, end = rows[0], rows[-1]
    summary = dict(phase=phase, intent=PHASE_INTENT[phase], waypoints=len(rows),
                   duration_s=round(end['t'] - start['t'], 3),
                   start_pose=start['pose'], end_pose=end['pose'],
                   scenes=sum(1 for index in paired if index is not None))
    if rows[0].get('joints'):
        summary['joint_travel_deg'] = round(sum(step_deg(a, b) for a, b in zip(rows, rows[1:])), 3)
        summary['taught_motion'] = summary['joint_travel_deg'] > 1.0
    return summary


def unverified(meta, track):
    """What this episode does not establish, in the agent's own words at read time."""
    notes = ['Object localization is unimplemented: any offset must be measured and confirmed '
             'by the operator, and this trace contains no detector output.',
             'Replay returns the object to the original taught place; the return spot is not '
             'adapted to the scene.',
             'Recorded joint moves do not run through collision planning; pose moves are planned '
             'by the Viam motion service against the machine geometry.',
             'Phase intents are the recording prompts, not sensor-verified postconditions.']
    evidence = meta.get('gripper_force_evidence', {})
    if evidence.get('source') == 'operator_entered':
        notes.append(f"Grip force {meta.get('gripper_force_percent')}% is an operator-entered "
                     'setting percentage, not a measured contact force, and is marked '
                     'hardware_verified false. Replay pauses for manual closure.')
    if meta.get('validation_status') == 'candidate':
        notes.append('The episode is a candidate: one operator-reported success, no repeat trials.')
    if track.get('format') == 'pose-track/1':
        notes.append('Poses are replayed through the planner, which may choose a different arm '
                     'configuration than the one taught.')
    return notes


def reference_trace(directory, meta, track, *, max_pair_age_s=1.0):
    """Build the advisory trace for one episode and its captured scenes."""
    number(max_pair_age_s, 0.01, 60, 'max_pair_age_s')
    rows = track['waypoints']
    captured = scenes(directory)
    paired = [pair_scene(row, captured, max_pair_age_s) for row in rows]

    waypoints, phases = [], []
    for phase in PHASES:
        indices = [i for i, row in enumerate(rows) if row['phase'] == phase]
        if not indices:
            raise ValueError(f'Track is missing the {phase} phase')
        phases.append(phase_summary(phase, [rows[i] for i in indices], [paired[i] for i in indices]))
    for row, scene in zip(rows, paired):
        entry = dict(t=round(row['t'], 3), phase=row['phase'], pose=row['pose'],
                     source_index=row.get('source_index'), scene=scene)
        if row.get('joints'):
            entry['joints'] = row['joints']
        waypoints.append(entry)

    anchors = {label: dict(captured_at=payload.get('timestamp'),
                           views=views(payload.get('frames', {})))
               for label, payload in (meta.get('observations') or {}).items()}
    return dict(
        schema='reference-trace/1', advisory=True, at=now(),
        derived_from=dict(episode=Path(directory).name, track=track.get('format'),
                          waypoints=len(rows), frames=len(captured)),
        object=meta.get('object'), station=meta.get('station'), frame=track.get('frame'),
        units=dict(pose='mm and degrees', joints=track.get('joint_units', 'degrees')),
        outcome=dict(grasp_success=meta.get('grasp_success'), success=meta.get('success'),
                     success_source=meta.get('success_source'),
                     validation_status=meta.get('validation_status')),
        grasp=dict(type=meta.get('grasp_type'), pregrasp_pose=meta.get('pregrasp_pose'),
                   grasp_pose=meta.get('grasp_pose'), place_pose=meta.get('place_pose'),
                   gripper_force_percent=meta.get('gripper_force_percent'),
                   force_evidence=meta.get('gripper_force_evidence')),
        scene_pairing=dict(joined_on='absolute UTC capture time, per phase and attempt',
                           max_age_s=max_pair_age_s,
                           paired=sum(1 for scene in paired if scene),
                           unpaired=sum(1 for scene in paired if scene is None),
                           note='Frame and waypoint clocks do not share an origin; never join on t.'),
        unverified=unverified(meta, track), anchors=anchors, phases=phases, waypoints=waypoints)


def write_reference(directory, track_name, *, max_pair_age_s=1.0):
    directory = Path(directory)
    meta = read_json(directory / 'metadata.json')
    trace = reference_trace(directory, meta, read_json(directory / track_name),
                            max_pair_age_s=max_pair_age_s)
    write(directory / TRACE, trace)
    return trace


def describe(name, trace):
    pairing = trace['scene_pairing']
    lines = [f"{name}: reference trace over {len(trace['waypoints'])} waypoints, "
             f"{pairing['paired']} scene-paired, {pairing['unpaired']} without a frame within "
             f"{pairing['max_age_s']}s"]
    quiet = [phase['phase'] for phase in trace['phases'] if phase.get('taught_motion') is False]
    if quiet:
        lines.append('  phases that teach no motion: ' + ', '.join(quiet))
    lines.append(f"  {len(trace['unverified'])} stated limits, advisory only: an agent reads this, "
                 'the executor does not')
    return '\n'.join(lines)
