"""Supervised fixed task compositions. No generated poses or primitive changes."""
import asyncio
from pathlib import Path

from primitives import motion
from primitives.types import Context
from .config import ROOT, read_json, validate_config, validate_pose
from .demo_pack import verify
from .demonstrations import ViamIO, confirm, event, new_directory, now, stop_on_failure, write
from .putback import poses

RECIPES = {
    'signature': ['Pour coconut water', 'Return the carton', 'Pour from the pitcher', 'Return the pitcher'],
    'reset': ['Operator identifies empty hand, coconut carton, or pitcher',
              'Return identified object to its taught place if held', 'Return home with empty hand'],
    'shake': ['Start with a securely held, sealed object', 'Shake for 3 seconds',
              'Finish still holding at the levelled centre'],
    'shaker': ['Start empty with shaker at taught location', 'Approach and grip',
               'Lift and run shake_full', 'Return to supported source pose',
               'Release and retreat'],
}


def shaker_book(config):
    from .taught_actions import validate as validate_book
    path = config.get('taught_shake_book')
    if not isinstance(path, str) or not path.strip():
        raise ValueError('Configure taught_shake_book with the reviewed shake pose file')
    book = read_json(ROOT / path)
    if book.get('action') != 'shake':
        raise ValueError('Pick up and shake requires a shake pose book')
    validate_book(book, config)
    return book


def validate(task, pack, config_path):
    if task not in RECIPES:
        raise ValueError('Choose signature, reset, shake, or shaker')
    manifest = verify(pack, config_path)
    config = read_json(config_path)
    validate_config(config, execute=True)
    if task == 'shaker':
        shaker_book(config)
        path = str((ROOT / config['taught_shake_book']).resolve())
        if path not in manifest['external_sha256']:
            raise ValueError('Prepare a new pack that pins the taught shake book')
    if task == 'reset':
        origin = motion.settings(config)['origin_pose']
        if origin is None:
            raise ValueError('Reset needs the existing taught origin pose')
        validate_pose(origin, config, execute=True)
        for name in ('coconut_water', 'pitcher'):
            poses(config, name, root=Path(pack) / 'demonstrations')
    if task == 'shake':
        import shake_full
        supplied = config.get('primitive_settings', {}).get('shake')
        if not isinstance(supplied, dict) or set(supplied) != set(shake_full.DEFAULT_SETTINGS):
            raise ValueError('Review and explicitly configure every shake_full setting before UI execution')
        if not shake_full.shake_settings(config)['require_holding']:
            raise ValueError('The fixed shake requires require_holding=true')
        if config['limits']['max_stir_duration_s'] < 3:
            raise ValueError('Fixed shake duration exceeds the configured limit')
    return config, manifest


async def execute(task, pack, config_path, *, ask_fn, runs):
    """One admitted attempt; uncertainty aborts before motion, faults never retry."""
    from .connection import connect
    from .__main__ import check_resources

    if task not in ('reset', 'shake', 'shaker'):
        raise ValueError('Signature uses the existing supervised replay executor')
    config, _ = validate(task, pack, config_path)
    held = None
    targets = None
    if task == 'shaker':
        book = shaker_book(config)
        import shake_full
        print('Pickup shake settings:', shake_full.shake_settings(config), flush=True)
        print('Grip:', book['force_percent'], 'travel:', book['travel_speed_deg_s'],
              'placement:', book['placement_speed_deg_s'], 'duration:', book['duration_s'],
              'stroke speed:', shake_full.OVERRIDE_SPEED_DEG_S, flush=True)
        await confirm('Full pickup/shake/return intended: fault-free stationary arm, empty hand, '
                      'shaker at taught location, free-drive OFF, no other controller, '
                      'current-to-approach and held-object paths reviewed and clear, '
                      'source placement supported, operator beside stop control?', ask_fn)
    elif task == 'reset':
        held = (await ask_fn('Observed held object: type empty, coconut_water, or pitcher; q if unknown:')).strip()
        if held not in ('empty', 'coconut_water', 'pitcher'):
            raise ValueError('Unknown held object; establish the station state before reset')
        if held != 'empty':
            targets = poses(config, held, root=Path(pack) / 'demonstrations')
        await confirm('Reset intended: arm stationary, free-drive OFF, held state observed; '
                      'if loaded, same taught grip/orientation, original return spot clear, '
                      '60 mm vertical approach and all carried-object/home paths clear?', ask_fn)
    else:
        import shake_full
        print('UI action: root shake_full.shake; settings:', shake_full.shake_settings(config), flush=True)
        print('Stroke speed request:', shake_full.OVERRIDE_SPEED_DEG_S, 'deg/s', flush=True)
        await confirm('Shake intended: sealed object already securely side-gripped and lifted clear; '
                      'arm stationary, free-drive OFF, levelling rotation and full shake swept volume '
                      'clear? This shakes for 3 seconds and finishes STILL HOLDING; no pickup or release.', ask_fn)
    # Recheck frozen dependencies after the operator wait, before connecting.
    validate(task, pack, config_path)
    if task == 'shaker':
        book = shaker_book(config)
    robot = await connect(managed_reconnect=False)
    from .taught_actions import ActionIO
    io = (ActionIO if task == 'shaker' else ViamIO)(Context(config, robot))
    directory = None
    outcome = dict(mode='execute', status='running', task=task, started_at=now(), recovery_attempt_budget=0)
    try:
        directory = new_directory(runs, task)
        write(directory / 'result.json', outcome)
        check_resources(robot, config)
        async with asyncio.timeout(config['limits']['run_timeout_s']):
            if task == 'shaker':
                from .taught_actions import execute as run_taught, action_function
                write(directory / 'book.json', book)
                await run_taught(book, config, io, action_function('shake'),
                                lambda step, **data: event(directory, step, **data))
                await confirm('Shaker supported and stable, hand empty after retreat, '
                              'no collision/slip/spill?', ask_fn)
                outcome.update(status='completed', success_source='operator_report_and_gripper')
                return directory
            await io.prepare()
            holding = await io.holding()
            if task == 'reset':
                if holding is not (held != 'empty'):
                    raise RuntimeError('Gripper feedback disagrees with observed held state; no reset motion')
                event(directory, 'reset_admitted', held=held)
                if targets:
                    await io.pose(targets['hover'])
                    await io.pose(targets['place'])
                    await confirm('Identified object physically supported at its taught spot; safe to release?', ask_fn)
                    await io.open()
                    if await io.holding() is not False:
                        raise RuntimeError('Release not verified; do not retract or go home')
                    await io.pose(targets['hover'])
                await io.pose(motion.settings(config)['origin_pose'])
                if await io.holding() is not False:
                    raise RuntimeError('Empty hand not verified at home')
                await confirm('Arm at home, hand empty, and returned object upright/stable if applicable?', ask_fn)
            else:
                import shake_full
                if holding is not True:
                    raise RuntimeError('Shake requires a verified held object; no motion')
                event(directory, 'shake_started', duration_s=3)
                async with asyncio.timeout(config['limits']['primitive_timeout_s']):
                    result = await shake_full.shake(Context(config, robot), duration_s=3)
                if await io.holding() is not True:
                    raise RuntimeError('Object no longer held after shake; inspect before recovery')
                event(directory, 'shake_finished', result=result)
                await confirm('Object still securely held, no spill/contact, arm stationary?', ask_fn)
        outcome.update(status='completed', success_source='operator_report_and_gripper')
    except BaseException as exc:
        outcome.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        if directory:
            await stop_on_failure(io, directory, exc)
        else:
            await asyncio.wait_for(robot.stop_all(), 5)
        raise
    finally:
        try:
            if directory:
                outcome['finished_at'] = now()
                write(directory / 'result.json', outcome)
        finally:
            await asyncio.wait_for(robot.close(), 10)
    return directory
