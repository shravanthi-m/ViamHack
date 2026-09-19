"""Unattended recovery: set a held object down at its taught place pose, then home.

There is no operator gate here, so every precondition a sensor can answer is checked
before the arm moves and again after it acts, and any unexpected state stops all
motion rather than continuing. With nobody watching, carrying on from a state the
script did not expect is the one thing it must not do.

The taught place pose keeps its full orientation, so every move goes through the pose
planner. `go_to_pose` is not usable here: it forces the tool downward, which would
twist a held object on the way down.

The held object is not in the machine's collision model. The descent is therefore a
short vertical one from a hover pose directly above the taught place, and the
departure retraces it, so the volume the object sweeps near the table is the one it
already occupied when it was taught. The long leg to the hover pose and the leg home
are planned by the motion service against the configured geometry.
"""
import asyncio
import copy

from primitives import motion
from .compaction import latest_episode
from .config import number, read_json, validate_pose
from .demonstrations import (DEFAULT_ROOT, event, new_directory, now, require_station,
                             stop_on_failure, write)


def poses(config, skill, *, root=DEFAULT_ROOT, approach_mm=60.0):
    """The taught place pose, a hover pose above it, and the taught origin.

    Read and validated before anything connects, so an unreachable target fails here
    rather than part way through a placement.
    """
    require_station(config, skill)
    number(approach_mm, 1, 300, 'approach_mm')
    path = latest_episode(root, skill)
    meta = read_json(path / 'metadata.json')
    if meta.get('object') != skill or meta.get('status') != 'complete' or meta.get('success') is not True:
        raise ValueError(f'Episode for {skill} is incomplete, failed, or names another object')
    place = meta['place_pose']
    hover = copy.deepcopy(place)
    hover['z'] += approach_mm
    origin = motion.settings(config)['origin_pose']
    if origin is None:
        raise ValueError('No taught origin pose: set primitive_settings.motion.origin_pose, '
                         'or run main.py --teach-origin --execute once to measure it')
    for label, pose in (('place', place), ('hover', hover), ('origin', origin)):
        validate_pose(pose, config, execute=True)
    return dict(episode=path, place=place, hover=hover, origin=origin)


def describe(skill, targets, approach_mm):
    return (f'{skill}: hover x={targets["hover"]["x"]:.1f} y={targets["hover"]["y"]:.1f} '
            f'z={targets["hover"]["z"]:.1f} ({approach_mm:g} mm above the taught place), '
            f'down to place z={targets["place"]["z"]:.1f}, release, back up, then origin '
            f'x={targets["origin"]["x"]:.1f} y={targets["origin"]["y"]:.1f} '
            f'z={targets["origin"]["z"]:.1f}.\nTaught place read from {targets["episode"].name}.')


async def put_back(io, config, skill, *, root=DEFAULT_ROOT, runs='runs/put_back', approach_mm=60.0):
    """Place the held object at its taught place pose and return to the taught origin.

    Preconditions checked live: the gripper reports holding something. Postconditions
    checked live: the gripper reports empty after opening, and every pose move verifies
    its own arrival through the planner. One attempt, no recovery, StopAll on failure.
    """
    targets = poses(config, skill, root=root, approach_mm=approach_mm)
    directory = new_directory(runs, 'put_back')
    outcome = dict(status='running', object=skill, started_at=now(), recovery_attempt_budget=0,
                   episode=str(targets['episode']), approach_mm=approach_mm)
    write(directory / 'result.json', outcome)
    try:
        async with asyncio.timeout(config['limits']['run_timeout_s']):
            await io.prepare()
            start = await io.state()
            event(directory, 'start_state', pose=start['pose'], joints=start.get('joints'))
            if await io.holding() is not True:
                # Nothing to put back. Moving to the place pose and opening anyway would
                # drive the gripper at a spot that may already hold the object.
                raise RuntimeError('Gripper does not report holding anything; nothing to put back')
            event(directory, 'approach', pose=targets['hover'])
            await io.pose(targets['hover'])
            event(directory, 'descend', pose=targets['place'])
            await io.pose(targets['place'])
            await io.open()
            if await io.holding() is not False:
                raise RuntimeError('Release not verified; the object may still be held')
            event(directory, 'released', pose=targets['place'])
            event(directory, 'retract', pose=targets['hover'])
            await io.pose(targets['hover'])
            event(directory, 'homing', pose=targets['origin'])
            await io.pose(targets['origin'])
            final = await io.state()
            if await io.holding() is not False:
                raise RuntimeError('Hand not verified empty at the origin')
            event(directory, 'home_state', pose=final['pose'], joints=final.get('joints'))
        outcome.update(status='completed', success_source='gripper_report_and_pose_arrival')
    except BaseException as exc:
        await stop_on_failure(io, directory, exc)
        outcome.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        outcome['finished_at'] = now()
        write(directory / 'result.json', outcome)
    return directory
