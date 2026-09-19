"""Gripper team: close on an object at a checked force, and open to release it.

`close_gripper` takes the force as an argument because different objects need
different grips -- a paper cup and a steel spoon are not the same squeeze. Viam's
Gripper API itself has no force parameter: `Grab` closes at whatever the module is
configured for. Force here is the documented UFactory torque extension, a percent
of the gripper's rated torque, set through `do_command`.

The default adapter sets torque and verifies readback before Grab. Stations with
the UFactory atomic extension can explicitly select `ufactory_atomic`: send force
and closure together, preserving the current speed, then require holding feedback.
This adapter reports commanded controller percent, not measured force or readback.
It never falls back to an ordinary Grab after a failed extension command.

`open_gripper` releases whatever is held. It is a deliberate command, unlike the
failure paths elsewhere in this scaffold, which stop motion but never auto-open a
gripper that may be suspending an object.

`primitive_settings.gripper.max_force_percent` is the team-owned ceiling on what a
plan may ask for. Raising it is a hardware decision, not a way to make a grasp hold.
"""
import math

from .types import Context

IMPLEMENTED = {'close_gripper', 'open_gripper'}

TORQUE_READ = {'get_gripper_torque': True}
DEFAULT_SETTINGS = {
    'max_force_percent': 30.0,       # Conservative ceiling, as in the earlier station config.
    'force_tolerance_percent': 0.5,  # How far readback may sit from the request.
    'require_holding': True,         # Only turn off for a gripper that cannot report it.
    'force_control': 'torque_readback',
    'object_force_percent': {},      # Explicit replay settings, keyed by configured object ID.
}


def settings(config):
    """Validate the team-owned primitive_settings.gripper block over the defaults."""
    supplied = config.get('primitive_settings', {}).get('gripper', {})
    if not isinstance(supplied, dict) or not set(supplied) <= set(DEFAULT_SETTINGS):
        raise ValueError('primitive_settings.gripper accepts only: '
                         + ', '.join(sorted(DEFAULT_SETTINGS)))
    values = {**DEFAULT_SETTINGS, **supplied}
    for key, ceiling in (('max_force_percent', 100), ('force_tolerance_percent', 10)):
        value = values[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= ceiling:
            raise ValueError(f'primitive_settings.gripper.{key} must be a number in (0, {ceiling}]')
    if type(values['require_holding']) is not bool:
        raise ValueError('primitive_settings.gripper.require_holding must be boolean')
    if values['force_control'] not in ('torque_readback', 'ufactory_atomic'):
        raise ValueError('gripper.force_control must be torque_readback or ufactory_atomic')
    if values['force_control'] == 'ufactory_atomic' and not values['require_holding']:
        raise ValueError('ufactory_atomic requires holding feedback')
    profiles = values['object_force_percent']
    if not isinstance(profiles, dict):
        raise ValueError('gripper.object_force_percent must map configured object IDs to percentages')
    for object_id, force in profiles.items():
        if object_id not in config.get('objects', []):
            raise ValueError(f'Unknown gripper object: {object_id}')
        validate_force(force, values)
    return values


def validate_force(force, tuning):
    if (type(force) not in (int, float) or not math.isfinite(force)
            or not 0 < force <= tuning['max_force_percent']):
        raise ValueError(f'force_percent must be a number in (0, '
                         f'{tuning["max_force_percent"]}], the configured maximum')
    # The module converts torque to uint16. Refuse silent truncation.
    if tuning['force_control'] == 'ufactory_atomic' and force != int(force):
        raise ValueError('ufactory_atomic force_percent must be a whole controller percent')


def force_for_object(config, object_id):
    """Require an explicit per-object setting; never guess from another object."""
    tuning = settings(config)
    if object_id not in tuning['object_force_percent']:
        raise ValueError(f'{object_id}: missing gripper.object_force_percent setting')
    return tuning['object_force_percent'][object_id]


def max_force(config):
    """The ceiling the planner is shown, so it cannot ask for more than the team allows."""
    return settings(config)['max_force_percent']


async def held(handle, timeout, *, required, setting):
    """Whether the gripper reports an object. None when it cannot say and need not."""
    try:
        response = await handle.is_holding_something(timeout=timeout)
    except Exception as exc:
        if required:
            raise RuntimeError(
                'Gripper cannot report whether it is holding anything, so this primitive '
                f'cannot confirm its postcondition. Set {setting} to false only if you '
                'accept working without that check.') from exc
        return None
    return bool(response.is_holding_something)


async def torque(handle, timeout):
    """Read the gripper's force setting back, as a percent of its rated torque.

    Two different problems, reported differently: a module that does not implement
    the extension at all, and one that answers ambiguously. The extension reports
    torque under a module-specific key, so accept exactly one plausible number and
    refuse anything else rather than guess which reading is the force.
    """
    try:
        feedback = await handle.do_command(TORQUE_READ, timeout=timeout)
    except Exception as exc:
        raise RuntimeError(
            'This gripper does not implement the torque extension, so it cannot honour a '
            'requested force. Use a gripper that reports its torque, or grasp through a '
            'primitive that does not name one.') from exc
    values = [value for key, value in (feedback or {}).items()
              if 'torque' in key.lower() and type(value) in (int, float)
              and math.isfinite(value) and 0 <= value <= 100]
    if len(values) != 1:
        raise RuntimeError(f'Gripper reported {len(values)} torque values, not a single '
                           'one, so its force cannot be set or confirmed on this machine')
    return float(values[0])


async def apply_force(handle, force_percent, tuning, timeout):
    """Set the grip force and prove it took, or fail before anything closes."""
    before = await torque(handle, timeout)
    await handle.do_command({'set_gripper_torque': force_percent}, timeout=timeout)
    after = await torque(handle, timeout)
    if not math.isclose(after, force_percent, abs_tol=tuning['force_tolerance_percent']):
        raise RuntimeError(f'Gripper force readback is {after}%, not the requested '
                           f'{force_percent}%; the setting did not take')
    return {'force_percent': after, 'force_before_percent': before}


async def close_gripper(ctx: Context, *, force_percent: float) -> dict:
    """Close the gripper at a named force and finish holding the object, or raise.

    The force is a percent of the gripper's rated torque, bounded by
    primitive_settings.gripper.max_force_percent. The selected adapter establishes
    readback or commands atomic force-limited closure. Postcondition: the gripper
    reports an object held. Evidence distinguishes commanded force from readback;
    neither identifies the object or proves a secure grasp throughout a pour.
    """
    from viam.components.gripper import Gripper

    if ctx.robot is None:
        raise ValueError('close_gripper needs a connected robot')
    config = ctx.config
    tuning = settings(config)
    validate_force(force_percent, tuning)
    timeout = config['limits']['primitive_timeout_s']
    handle = Gripper.from_robot(ctx.robot, config['resources']['gripper'])
    if tuning['force_control'] == 'ufactory_atomic':
        return await atomic_close(handle, force_percent, timeout)
    applied = await apply_force(handle, force_percent, tuning, timeout)
    if not await handle.grab(timeout=timeout):
        raise RuntimeError('Gripper closed without reporting an object grasped')
    holding = await held(handle, timeout, required=tuning['require_holding'],
                         setting='primitive_settings.gripper.require_holding')
    if tuning['require_holding'] and holding is not True:
        raise RuntimeError('Gripper closed but reports nothing held')
    return {'requested_force_percent': force_percent, 'holding': holding, **applied}


async def atomic_close(handle, force_percent, timeout):
    """Station-specific UFactory adapter; an empty response is only an RPC ack."""
    setting = 'ufactory_atomic'
    if await held(handle, timeout, required=True, setting=setting) is not False:
        raise RuntimeError('Atomic closure requires a verified empty hand')
    feedback = await handle.do_command({'get_gripper_speed': True}, timeout=timeout)
    speed = feedback.get('gripper_speed') if isinstance(feedback, dict) else None
    if (type(speed) not in (int, float) or not math.isfinite(speed)
            or not 1 <= speed <= 5000 or speed != int(speed)):
        raise RuntimeError('No supported current gripper speed; refusing closure')
    command = {'grab_with_torque': {'position': 0, 'speed': speed, 'torque': force_percent}}
    response = await handle.do_command(command, timeout=timeout)
    if response != {}:
        raise RuntimeError('Unexpected atomic gripper response; inspect before any retry')
    holding = await held(handle, timeout, required=True, setting=setting)
    if holding is not True:
        raise RuntimeError('Atomic gripper closed but reports nothing held')
    return dict(requested_force_percent=force_percent, commanded_force_percent=force_percent,
                force_control='ufactory_atomic', force_readback=False, holding=holding,
                command=command)


async def open_gripper(ctx: Context) -> dict:
    """Open the gripper and finish holding nothing, or raise.

    Deliberately releases whatever is held, so the caller owns what happens to it.
    Postcondition: the gripper reports nothing held. Returns that report as evidence
    the object was actually let go rather than still pinched.
    """
    from viam.components.gripper import Gripper

    if ctx.robot is None:
        raise ValueError('open_gripper needs a connected robot')
    config = ctx.config
    tuning = settings(config)
    timeout = config['limits']['primitive_timeout_s']
    handle = Gripper.from_robot(ctx.robot, config['resources']['gripper'])
    await handle.open(timeout=timeout)
    holding = await held(handle, timeout, required=tuning['require_holding'],
                         setting='primitive_settings.gripper.require_holding')
    if tuning['require_holding'] and holding is not False:
        raise RuntimeError('Gripper opened but still reports something held')
    return {'holding': holding}
