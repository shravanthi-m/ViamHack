"""Teach full gripper poses and compose pickup/action/placement; offline by default."""
import argparse
import asyncio
import json
from pathlib import Path

from primitives import gripper, motion
from primitives.types import Context
from .config import number, read_json, validate_config, validate_pose
from .connection import connect
from .demonstrations import ViamIO, confirm, event, new_directory, write

LABELS = {
    'shake': ('source_approach', 'source_grasp', 'source_lift'),
    'squeeze': ('source_approach', 'source_grasp', 'source_lift', 'cup_approach', 'cup_action'),
    'honey': ('source_approach', 'source_grasp', 'source_lift', 'cup_approach',
              'cup_action', 'cup_tilt', 'inspection_pose'),
}


def new_book(action, config):
    book = dict(version=1, action=action, frame=config['frame'],
                resources={k: config['resources'][k] for k in ('arm', 'gripper', 'motion')},
                poses={}, force_percent=None, travel_speed_deg_s=None,
                placement_speed_deg_s=None, duration_s=5.0,
                settle_s=1.0, station_verified=False)
    if action == 'honey':
        book.update(tilt_dwell_s=None, max_translation_mm=None,
                    source_object='honey', cup_object='cup',
                    inspection_calibration_sha256=None)
    return book


def identity(book, config):
    if book.get('version') != 1 or book.get('action') not in LABELS:
        raise ValueError('Unsupported taught-action file')
    if (book.get('frame') != config['frame'] or
            book.get('resources') != {k: config['resources'][k]
                                      for k in ('arm', 'gripper', 'motion')}):
        raise ValueError('Taught frame/resources differ from this station; teach again')


def validate(book, config):
    validate_config(config, execute=True)
    identity(book, config)
    if book.get('station_verified') is not True:
        raise ValueError('Confirm measured station config and set station_verified=true')
    for name in LABELS[book['action']]:
        record = book['poses'].get(name)
        if not isinstance(record, dict) or record.get('source') != 'motion.get_pose':
            raise ValueError(f'Teach {name} with this recorder first')
        if record.get('frame') != book['frame'] or record.get('component') != book['resources']['gripper']:
            raise ValueError(f'{name}: frame/component mismatch')
        validate_pose(record.get('pose'), config, execute=True)
    number(book['force_percent'], 0.01, gripper.max_force(config), 'force_percent')
    gripper.validate_force(book['force_percent'], gripper.settings(config))
    number(book['travel_speed_deg_s'], 0.01, 30, 'travel_speed_deg_s')
    number(book['placement_speed_deg_s'], 0.01, book['travel_speed_deg_s'], 'placement_speed_deg_s')
    number(book['settle_s'], 0, 5, 'settle_s')
    p = {k: book['poses'][k]['pose'] for k in LABELS[book['action']]}
    if p['source_lift']['z'] <= p['source_grasp']['z']:
        raise ValueError('source_lift must clear the supported source_grasp height')
    if p['source_approach'] == p['source_grasp']:
        raise ValueError('source_approach and source_grasp must be distinct')
    if not gripper.settings(config)['require_holding']:
        raise ValueError('Taught actions require gripper holding checks')
    if book['action'] == 'honey':
        number(book.get('tilt_dwell_s'), 0, 5, 'tilt_dwell_s')
        number(book.get('max_translation_mm'), 0, 50, 'max_translation_mm')
        if motion.deviation(p['cup_tilt'], p['cup_action'])['orientation_error_deg'] < 1:
            raise ValueError('Teach a distinct cup_tilt orientation; do not guess a theta rotation')
    if book['action'] == 'shake':
        import shake_full
        supplied = config.get('primitive_settings', {}).get('shake')
        if not isinstance(supplied, dict) or set(supplied) != set(shake_full.DEFAULT_SETTINGS):
            raise ValueError('Set all explicit reviewed primitive_settings.shake fields; do not inherit speed defaults')
        tuning = shake_full.shake_settings(config)
        if not tuning['require_holding']:
            raise ValueError('Shake holding checks must remain enabled')
        number(book['duration_s'], 0.01, config['limits']['max_stir_duration_s'], 'duration_s')
        shake_full.swept_box(shake_full.level(p['source_lift']), tuning, config)
    return p


def sequence(action):
    start = ['source_approach', 'source_grasp', 'GRIP', 'source_lift']
    middle = (['SHAKE'] if action == 'shake' else
              ['cup_approach', 'cup_action', *(['cup_tilt', 'DWELL', 'cup_action']
                if action == 'honey' else ['SQUEEZE']), 'cup_approach'])
    return start + middle + ['source_lift', 'PLACE_AT_SOURCE_GRASP', 'SETTLE', 'RELEASE', 'source_approach']


def action_function(action):
    if action == 'honey':
        raise ValueError('Use python -m runtime.honey_tilt; fresh localization is required')
    if action == 'shake':
        # ROOT implementation, not primitives/shake_full.py. Its main/run_sequence
        # own extra grip/lift steps, so call only the held-object action.
        from shake_full import shake
        return shake
    from . import squeeze_action
    if not squeeze_action.READY:
        raise ValueError('Squeeze poses can be taught, but action-only squeeze is not integrated yet')
    return squeeze_action.squeeze


async def execute(book, config, io, action, log):
    poses = validate(book, config)  # Entire path validated before first command.
    async def move(label, speed):
        log('move', label=label, speed_deg_s=speed)
        await io.move_at(poses[label], speed)

    try:
        async with asyncio.timeout(config['limits']['run_timeout_s']):
            if await io.holding() is not False:
                raise RuntimeError('Start with an empty gripper; no automatic release')
            log('open_empty_gripper')
            await io.open()
            await move('source_approach', book['travel_speed_deg_s'])
            await move('source_grasp', book['placement_speed_deg_s'])
            log('grip', force_percent=book['force_percent'])
            await io.close(book['force_percent'])
            if await io.holding() is not True:
                raise RuntimeError('Grasp not confirmed; no lift')
            await move('source_lift', book['placement_speed_deg_s'])
            if book['action'] in ('squeeze', 'honey'):
                await move('cup_approach', book['travel_speed_deg_s'])
                await move('cup_action', book['placement_speed_deg_s'])
            log('action', action=book['action'])
            async with asyncio.timeout(config['limits']['primitive_timeout_s']):
                result = await action(io.ctx, **({'duration_s': book['duration_s']}
                                               if book['action'] == 'shake' else {}))
            if not isinstance(result, dict) or result.get('success') is False:
                raise RuntimeError('Action must return success evidence as a dict or raise')
            log('action_complete', result=result)
            if await io.holding() is not True:
                raise RuntimeError('Object no longer held after action')
            if book['action'] in ('squeeze', 'honey'):
                await move('cup_approach', book['placement_speed_deg_s'])
            await move('source_lift', book['travel_speed_deg_s'])
            # Return to supported height, NOT the action's elevated start_pose.
            await move('source_grasp', book['placement_speed_deg_s'])
            await asyncio.sleep(book['settle_s'])
            log('release_at_source')
            await io.open()
            if await io.holding() is not False:
                raise RuntimeError('Release not confirmed; no retreat')
            await move('source_approach', book['placement_speed_deg_s'])
            log('complete')
            return result
    except (Exception, asyncio.CancelledError):
        try:
            await io.stop()
            log('stop_all_requested')
        except BaseException as exc:
            print(f'StopAll failed; verify robot manually: {type(exc).__name__}', flush=True)
        raise  # Never auto-release or restart after an uncertain failure.


class ActionIO(ViamIO):
    async def move_at(self, pose, speed):
        from viam.components.arm import Arm
        tuning = {**motion.settings(self.ctx.config), 'speed_deg_s': speed}
        async with asyncio.timeout(self.timeout):
            await motion.apply_speed(Arm.from_robot(self.ctx.robot, self.ctx.config['resources']['arm']),
                                     tuning, self.timeout)
            return await motion.plan_to(self.ctx, pose, tuning, self.timeout, 'taught action pose')


async def dispatch(args):
    config = read_json(args.config)
    validate_config(config)
    path = Path(args.book)
    if args.command == 'init':
        if path.exists():
            raise ValueError('Book already exists; refusing to overwrite recorded poses')
        path.parent.mkdir(parents=True, exist_ok=True)
        write(path, new_book(args.action, config))
        print(f'Created {path}. Settings are unset until reviewed.')
        return
    book = read_json(path)
    identity(book, config)
    if args.command == 'teach':
        if args.name not in LABELS[book['action']]:
            raise ValueError('Choose a label from: ' + ', '.join(LABELS[book['action']]))
        if args.name in book['poses']:
            raise ValueError('Pose already recorded; use a new book to preserve the earlier teaching')
        print('Connecting for read-only pose capture...', flush=True)
        robot = await connect(managed_reconnect=False)
        try:
            io = ViamIO(Context(config, robot))
            state = await asyncio.wait_for(io.state(), 15)
            record = dict(source='motion.get_pose', frame=config['frame'],
                          component=config['resources']['gripper'], **state)
            book['poses'][args.name] = record
            write(path, book)
            print(json.dumps({args.name: record}, indent=2))
            print(f'Saved to {path}; no motion commanded.')
        finally:
            await asyncio.wait_for(robot.close(), 5)
        return
    validate(book, config)
    print(' -> '.join(sequence(book['action'])))
    if book['action'] == 'shake':
        import shake_full
        print('Action: root shake_full.shake; settings:', shake_full.shake_settings(config))
        print('Stroke speed requested by teammate script:', shake_full.OVERRIDE_SPEED_DEG_S, 'deg/s')
    if args.command == 'validate' or not args.execute:
        print('Pose validation passed offline. No connection or motion.')
        if book['action'] == 'squeeze':
            print('Squeeze execution still requires the teammate action-only adapter.')
        return
    action = action_function(book['action'])  # Missing adapter fails BEFORE pickup.
    await confirm('Free-drive OFF, source fixed at taught pose, held-object path clear, '
                  'gripper empty, placement supported, action speed reviewed?')
    directory = new_directory(Path('runs') / 'taught_actions', book['action'])
    write(directory / 'book.json', book)
    print('Connecting for physical execution...', flush=True)
    robot = await connect(managed_reconnect=False)
    try:
        await execute(book, config, ActionIO(Context(config, robot)), action,
                      lambda step, **data: event(directory, step, **data))
    finally:
        await asyncio.wait_for(robot.close(), 5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--book', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('action', choices=LABELS)
    teach = commands.add_parser('teach')
    teach.add_argument('name')
    commands.add_parser('validate')
    run = commands.add_parser('run')
    run.add_argument('--execute', action='store_true')
    asyncio.run(dispatch(parser.parse_args()))


if __name__ == '__main__':
    main()
