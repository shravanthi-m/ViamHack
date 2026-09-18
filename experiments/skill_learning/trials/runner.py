"""Run one bounded episode, preserving evidence even on cancellation."""
import asyncio
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

POSE_KEYS = ('x', 'y', 'z', 'o_x', 'o_y', 'o_z', 'theta')
WAYPOINTS = ('pregrasp', 'grasp', 'lift', 'pour_ready', 'pour_tilt')


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:12]


def number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be a finite number in [{low}, {high}]')


def validate(config, profile, task):
    from google.protobuf.json_format import ParseDict
    from viam.proto.common import WorldState
    ParseDict(config.get('world_state', {}), WorldState())
    if task not in ('pick', 'pour'):
        raise ValueError('Task must be pick or pour')
    if config['gripper_mode'] not in ('native', 'ufactory_g2'):
        raise ValueError('Unknown gripper_mode')
    for key in ('arm', 'gripper', 'camera', 'motion'):
        if not config['resources'].get(key):
            raise ValueError(f'Missing resource {key}')
    if not config.get('frame'):
        raise ValueError('Missing reference frame')
    if not config.get('calibrated'):
        raise ValueError('Teach waypoints, set workspace bounds, then set calibrated=true')
    limits = config['limits']
    number(limits['command_timeout_s'], 1, 120, 'command_timeout_s')
    number(limits['episode_timeout_s'], 1, 600, 'episode_timeout_s')
    number(limits['max_speed_deg_s'], 0.1, 30, 'max_speed_deg_s')
    number(limits['start_tolerance_mm'], 0.1, 20, 'start_tolerance_mm')
    number(limits['start_orientation_tolerance_deg'], 0.1, 10, 'start_orientation_tolerance_deg')
    number(limits['max_grip_force_percent'], 1, 100, 'max_grip_force_percent')
    number(profile['speed_deg_s'], 0.1, limits['max_speed_deg_s'], 'speed_deg_s')
    number(profile['hold_s'], 0, 10, 'hold_s')
    number(profile['pour_dwell_s'], 0, 10, 'pour_dwell_s')
    number(profile['object']['mass_g'], 0, 10000, 'mass_g')
    if not profile['object']['id'] or profile['object']['compliance'] not in ('rigid', 'semi_rigid', 'soft'):
        raise ValueError('Object needs id and compliance: rigid, semi_rigid, or soft')
    force = profile.get('grip_force_percent')
    if config['gripper_mode'] == 'native' and force is not None:
        raise ValueError('Native Grab cannot tune force; use null or verify a G2 gripper')
    if config['gripper_mode'] == 'ufactory_g2':
        number(force, 1, limits['max_grip_force_percent'], 'grip_force_percent')
    bounds = config['workspace_mm']
    for axis in ('x', 'y', 'z'):
        pair = bounds[axis]
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError(f'Set workspace_mm.{axis} to [minimum, maximum]')
        for value in pair:
            number(value, -10000, 10000, f'workspace {axis}')
        if pair[0] >= pair[1]:
            raise ValueError('Workspace minimum must be smaller than maximum')
    for name in WAYPOINTS if task == 'pour' else WAYPOINTS[:3]:
        pose = config['waypoints'][name]
        if not isinstance(pose, dict) or set(pose) != set(POSE_KEYS):
            raise ValueError(f'Teach waypoint {name} with all seven Viam pose fields')
        for key, value in pose.items():
            number(value, -10000, 10000, f'{name}.{key}')
        if sum(pose[key] ** 2 for key in ('o_x', 'o_y', 'o_z')) < 1e-8:
            raise ValueError(f'{name} has a zero orientation vector')
        for axis in ('x', 'y', 'z'):
            if not bounds[axis][0] <= pose[axis] <= bounds[axis][1]:
                raise ValueError(f'{name} is outside workspace on {axis}')
    if config['waypoints']['lift']['z'] <= config['waypoints']['grasp']['z']:
        raise ValueError('Lift must be above grasp in the configured world frame')


def steps(task):
    result = [('open', None), ('move', 'grasp'), ('grab', None),
              ('move', 'lift'), ('hold', None), ('verify', None)]
    if task == 'pour':
        result += [('move', 'pour_ready'), ('verify', None),
                   ('pour', None), ('verify', None), ('move', 'lift')]
    return result + [('move', 'grasp'), ('open', None), ('move', 'pregrasp')]


def orientation_error(a, b):
    # Viam orientation vectors are NOT Euler angles or axis-angle. Use its SDK.
    from viam.spatialmath import OrientationVector
    def quaternion(p):
        return OrientationVector(o_x=p['o_x'], o_y=p['o_y'], o_z=p['o_z'],
                                 theta=math.radians(p['theta'])).to_quaternion()
    qa, qb = quaternion(a), quaternion(b)
    dot = abs(qa.w * qb.w + qa.i * qb.i + qa.j * qb.j + qa.k * qb.k)
    return math.degrees(2 * math.acos(min(1, max(0, dot))))


async def episode(io, config, profile, task, root):
    validate(config, profile, task)
    run = Path(root) / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8])
    run.mkdir(parents=True, exist_ok=False)
    write(run / 'config.json', config)
    write(run / 'profile.json', profile)
    source_root = Path(__file__).resolve().parents[3]
    sources = {name: (source_root / name).read_text() for name in
               ('experiments/skill_learning/trial.py',
                'experiments/skill_learning/trials/runner.py',
                'experiments/skill_learning/trials/viam_io.py',
                'runtime/connection.py', 'requirements.txt')}
    write(run / 'source.json', sources)
    result = dict(status='running', task=task, profile_id=fingerprint(profile),
                  config_id=fingerprint(config), code_id=fingerprint(sources), outcome=None)
    write(run / 'result.json', result)
    print(f'Trial: {run}', flush=True)

    def event(**data):
        with (run / 'events.jsonl').open('a') as file:
            file.write(json.dumps(dict(time=datetime.now(timezone.utc).isoformat(), **data)) + '\n')

    async def snapshot(name):
        observation = await io.capture(run / name)
        write(run / name / 'state.json', observation)
        return observation

    try:
        async with asyncio.timeout(config['limits']['episode_timeout_s']):
            state = await snapshot('00-before')
            current, start = state['pose'], config['waypoints']['pregrasp']
            distance = math.sqrt(sum((current[k] - start[k]) ** 2 for k in ('x', 'y', 'z')))
            if distance > config['limits']['start_tolerance_mm'] or orientation_error(current, start) > config['limits']['start_orientation_tolerance_deg']:
                raise RuntimeError('Position arm at taught pregrasp before starting a trial')
            if state['holding'].get('is_holding_something') is not False:
                raise RuntimeError('Start with an empty gripper and working holding feedback')
            streams = state['images']
            depth = [s for s in streams if 'depth' in s['name'].lower() or s['mime'] == 'image/vnd.viam.dep']
            color = [s for s in streams if s not in depth and s['mime'].startswith('image/')]
            if not depth or not color:
                raise RuntimeError('Camera GetImages must return both RGB and depth streams')
            await io.configure(profile)
            for index, (action, target) in enumerate(steps(task), 1):
                event(stage=index, action=action, target=target, status='started')
                if action == 'move':
                    await io.move(config['waypoints'][target])
                elif action == 'pour':
                    # Never block on camera/network observations while tilted:
                    # logging latency would silently change the poured amount.
                    await io.move(config['waypoints']['pour_tilt'], rotating=True)
                    event(stage=index, status='tilted')
                    await asyncio.sleep(profile['pour_dwell_s'])
                    await io.move(config['waypoints']['pour_ready'], rotating=True)
                    event(stage=index, status='upright')
                elif action == 'open':
                    await io.open()
                elif action == 'grab':
                    await io.grab()
                elif action == 'verify':
                    if not (await io.holding())['is_holding_something']:
                        raise RuntimeError('Object not held; trial stopped')
                else:
                    await asyncio.sleep(profile['hold_s'])
                await snapshot(f'{index:02d}-{target or action}')
                event(stage=index, status='completed')
            result['status'] = 'completed'  # This is execution status, NOT task success.
    except BaseException as exc:
        result.update(status='aborted', error=f'{type(exc).__name__}: {exc}')
        try:
            await io.stop()
            result['stop_requested'] = True
        except BaseException as stop_exc:
            result['stop_error'] = str(stop_exc)
        event(status='aborted', error=result['error'])
        raise
    finally:
        write(run / 'result.json', result)
    return run


def label(run, outcome, notes='', poured_g=None):
    run = Path(run)
    result = read(run / 'result.json')
    if outcome not in ('success', 'miss', 'slip', 'crush', 'spill', 'underpour', 'abort'):
        raise ValueError('Unknown outcome')
    if outcome == 'success' and result['status'] != 'completed':
        raise ValueError('An incomplete episode cannot be marked successful')
    if poured_g is not None:
        number(poured_g, 0, 10000, 'poured_g')
    result.update(outcome=outcome, notes=notes, poured_g=poured_g)
    write(run / 'result.json', result)


def report(root):
    groups = {}
    for path in sorted(Path(root).glob('*/result.json')):
        result = read(path)
        profile = read(path.parent / 'profile.json')
        key = (result['task'], result['config_id'], result['profile_id'], result.get('code_id', 'unknown'))
        group = groups.setdefault(key, dict(task=key[0], config_id=key[1], profile_id=key[2],
                                           code_id=key[3], profile=profile, trials=0, labeled=0, successes=0, outcomes={}, evidence=[]))
        group['trials'] += 1
        outcome = result['outcome']
        if outcome is not None:
            group['labeled'] += 1
            group['successes'] += outcome == 'success'
            group['outcomes'][outcome] = group['outcomes'].get(outcome, 0) + 1
        group['evidence'].append(str(path.parent))
    return list(groups.values())
