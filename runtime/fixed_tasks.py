"""Supervised fixed task compositions. No generated poses or primitive changes."""
import asyncio
from pathlib import Path

from primitives import motion
from primitives.types import Context
from .config import read_json, validate_config, validate_pose
from .demo_pack import verify
from .demonstrations import ViamIO, confirm, event, new_directory, now, stop_on_failure, write
from .orchestrator import require_implementations, validate_plan
from primitives.registry import implementations
from .putback import poses

RECIPES = {
    'signature': ['Pour coconut water', 'Return the carton', 'Pour from the pitcher', 'Return the pitcher'],
    'reset': ['Operator identifies empty hand, coconut carton, or pitcher',
              'Return identified object to its taught place if held', 'Return home with empty hand'],
    'shake': ['Start with a securely held, sealed object', 'Shake for 3 seconds',
              'Finish still holding at the levelled centre'],
}


def validate(task, pack, config_path):
    if task not in RECIPES:
        raise ValueError('Choose signature, reset, or shake')
    manifest = verify(pack, config_path)
    config = read_json(config_path)
    validate_config(config, execute=True)
    if task == 'reset':
        origin = motion.settings(config)['origin_pose']
        if origin is None:
            raise ValueError('Reset needs the existing taught origin pose')
        validate_pose(origin, config, execute=True)
        for name in ('coconut_water', 'pitcher'):
            poses(config, name, root=Path(pack) / 'demonstrations')
    if task == 'shake':
        from primitives import shake
        if not shake.settings(config)['require_holding']:
            raise ValueError('The fixed shake requires require_holding=true')
        plan = dict(version=1, instruction='Shake an already held object for three seconds.',
                    steps=[dict(id='shake', tool='shake', args={'duration_s': 3})])
        validate_plan(plan, config, execute=True)
        require_implementations(plan, implementations())
    return config, manifest


async def execute(task, pack, config_path, *, ask_fn, runs):
    """One admitted attempt; uncertainty aborts before motion, faults never retry."""
    from .connection import connect
    from .__main__ import check_resources

    if task not in ('reset', 'shake'):
        raise ValueError('Signature uses the existing supervised replay executor')
    config, _ = validate(task, pack, config_path)
    held = None
    targets = None
    if task == 'reset':
        held = (await ask_fn('Observed held object: type empty, coconut_water, or pitcher; q if unknown:')).strip()
        if held not in ('empty', 'coconut_water', 'pitcher'):
            raise ValueError('Unknown held object; establish the station state before reset')
        if held != 'empty':
            targets = poses(config, held, root=Path(pack) / 'demonstrations')
        await confirm('Reset intended: arm stationary, free-drive OFF, held state observed; '
                      'if loaded, same taught grip/orientation, original return spot clear, '
                      '60 mm vertical approach and all carried-object/home paths clear?', ask_fn)
    else:
        await confirm('Shake intended: sealed object already securely side-gripped and lifted clear; '
                      'arm stationary, free-drive OFF, levelling rotation and full shake swept volume '
                      'clear? This shakes for 3 seconds and finishes STILL HOLDING; no pickup or release.', ask_fn)
    # Recheck frozen dependencies after the operator wait, before connecting.
    validate(task, pack, config_path)
    robot = await connect()
    io = ViamIO(Context(config, robot))
    directory = None
    outcome = dict(mode='execute', status='running', task=task, started_at=now(), recovery_attempt_budget=0)
    try:
        directory = new_directory(runs, task)
        write(directory / 'result.json', outcome)
        check_resources(robot, config)
        async with asyncio.timeout(config['limits']['run_timeout_s']):
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
                from primitives import shake
                if holding is not True:
                    raise RuntimeError('Shake requires a verified held object; no motion')
                event(directory, 'shake_started', duration_s=3)
                result = await shake.shake(Context(config, robot), duration_s=3)
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
