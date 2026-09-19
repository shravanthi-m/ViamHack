"""Candidate honey pickup/tilt/return composition. Offline unless --execute."""
import argparse
import asyncio
import copy
import math
from pathlib import Path

from primitives import camera, localization, motion, vision
from primitives.types import Context
from . import taught_actions as taught
from .config import read_json, validate_target
from .connection import connect
from .demonstrations import confirm, event, new_directory, write


def profiles(book, config):
    """Require measured gripper targets, not generic object centroids."""
    taught.validate(book, config)
    if book['action'] != 'honey':
        raise ValueError('Use a book initialized with init honey')
    names = [book['source_object'], book['cup_object']]
    if names[0] == names[1]:
        raise ValueError('Source bottle and receiving cup must be distinct objects')
    result = [localization.target_profile(config, name) for name in names]
    if any(result[0][k] != result[1][k] for k in
           ('view', 'camera_resource', 'model', 'calibration_sha256')):
        raise ValueError('Honey and cup require profiles for the same calibrated view/model')
    if book.get('inspection_calibration_sha256') != result[0]['calibration_sha256']:
        raise ValueError('Validate calibration at the taught inspection posture and bind its SHA256')
    joints = book['poses']['inspection_pose'].get('joints')
    if (not isinstance(joints, list) or not joints or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in joints)):
        raise ValueError('Inspection posture needs recorded joint positions')
    for profile, label in zip(result, ('source_grasp', 'cup_action')):
        taught_pose = book['poses'][label]['pose']
        expected = {**taught_pose, 'z': profile['z_mm'], **profile['orientation']}
        delta = motion.deviation(expected, taught_pose)
        if delta['position_error_mm'] > 0.1 or delta['orientation_error_deg'] > 0.1:
            raise ValueError(f'{label}: profile must describe the taught gripper target height/orientation')
    return result


def check_inspection(book, state):
    """A wrist-camera planar map is valid only at its measured arm posture."""
    reference = book['poses']['inspection_pose']
    delta = motion.deviation(state['pose'], reference['pose'])
    joints, expected = state.get('joints'), reference.get('joints')
    if (delta['position_error_mm'] > 1 or delta['orientation_error_deg'] > 0.5 or
            not isinstance(joints, list) or not isinstance(expected, list) or
            not joints or len(joints) != len(expected) or
            any(type(a) not in (int, float) or not math.isfinite(a) or abs(a-b) > 0.5
                for a, b in zip(joints, expected))):
        raise ValueError('Manually restore the calibrated inspection posture before detection')


def translated_book(book, config, targets):
    """Translate measured landmarks in XY only, within the reviewed local envelope."""
    moved = copy.deepcopy(book)
    for name, anchor, labels in (
        (book['source_object'], 'source_grasp', ('source_approach', 'source_grasp', 'source_lift')),
        (book['cup_object'], 'cup_action', ('cup_approach', 'cup_action', 'cup_tilt')),
    ):
        target = targets[name]
        validate_target(target, config, execute=True)
        if target['object_id'] != name:
            raise ValueError('Localization returned the wrong object')
        old, new = book['poses'][anchor]['pose'], target['pose']
        alignment = motion.deviation({**new, 'x': old['x'], 'y': old['y']}, old)
        if alignment['position_error_mm'] > 0.1 or alignment['orientation_error_deg'] > 0.1:
            raise ValueError('Detection must preserve the taught height and orientation')
        dx, dy = new['x'] - old['x'], new['y'] - old['y']
        if math.hypot(dx, dy) > book['max_translation_mm']:
            raise ValueError('Detected displacement exceeds the reviewed translation envelope')
        for label in labels:
            moved['poses'][label]['pose']['x'] += dx
            moved['poses'][label]['pose']['y'] += dy
    taught.validate(moved, config)
    return moved


async def detect(io, book, config, directory):
    measured = profiles(book, config)
    check_inspection(book, await io.state())
    captured = await camera.capture(io.ctx, view=measured[0]['view'])
    images = [i for i in captured['images'] if i['mime_type'] in
              ('image/jpeg', 'image/png', 'image/webp')]
    if len(images) != 1:
        raise ValueError('Need one unambiguous RGB image')
    path = localization.ROOT / images[0]['path']
    names = [book['source_object'], book['cup_object']]
    # One frame and one model request, not continuous inference during motion.
    observation = await asyncio.to_thread(vision.observe, path.read_bytes(), names,
                                          model=measured[0]['model'])
    write(directory / 'observation.json', dict(capture=captured, observation=observation))
    check_inspection(book, await io.state())
    targets = {name: localization.target_from_observation(config, name, observation,
               view=captured['view'], captured_at=captured['captured_at']) for name in names}
    write(directory / 'targets.json', targets)
    return translated_book(book, config, targets)


def tilt_action(io, book, log):
    async def tilt(ctx):
        for label in ('cup_tilt', 'cup_action'):
            if await io.holding() is not True:
                raise RuntimeError('Honey bottle not held; abort tilt')
            log('tilt_move', label=label)
            await io.move_at(book['poses'][label]['pose'], book['placement_speed_deg_s'])
            if label == 'cup_tilt':
                await asyncio.sleep(book['tilt_dwell_s'])
        return {'success': True, 'action': 'tilt_only', 'dispensed_amount': 'unknown'}
    return tilt


async def dispatch(args):
    config, book = read_json(args.config), read_json(args.book)
    profiles(book, config)  # All prerequisites checked before connecting.
    print('DETECT honey + cup -> ' + ' -> '.join(taught.sequence('honey')), flush=True)
    if not args.execute:
        print('Offline validation passed; no connection, inference, or movement.')
        return
    await confirm('One trial, no retries: empty gripper, free-drive OFF, no other controller; '
                  'calibration measured at inspection posture; bottle height/orientation unchanged; '
                  'cup stable, cap/nozzle ready, translated held-object paths clear, source supported?')
    directory = new_directory(Path('runs') / 'honey_tilt', 'trial')
    write(directory / 'taught_book.json', book)
    robot = await connect(managed_reconnect=False)
    try:
        io = taught.ActionIO(Context(config, robot))
        async with asyncio.timeout(config['limits']['primitive_timeout_s']):
            if await io.holding() is not False:
                raise RuntimeError('Detection requires an empty gripper')
            moved = await detect(io, book, config, directory)
        write(directory / 'localized_book.json', moved)
        log = lambda step, **data: event(directory, step, **data)
        await taught.execute(moved, config, io, tilt_action(io, moved, log), log)
    finally:
        await asyncio.wait_for(robot.close(), 5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--book', required=True)
    parser.add_argument('--execute', action='store_true')
    asyncio.run(dispatch(parser.parse_args()))


if __name__ == '__main__':
    main()
