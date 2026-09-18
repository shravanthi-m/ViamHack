"""Gripper team: close on an object at a checked force, and open to release it.

`close_gripper` takes the force as an argument because different objects need
different grips -- a paper cup and a steel spoon are not the same squeeze. Viam's
Gripper API itself has no force parameter: `Grab` closes at whatever the module is
configured for. Force here is the documented UFactory torque extension, a percent
of the gripper's rated torque, set through `do_command`.

That extension is not universal, so this module never assumes it. Every close that
names a force first reads the torque back from the gripper to prove the command was
understood, sets it, and reads it back again to prove it took. A gripper that cannot
report its torque fails the primitive instead of quietly closing at the machine's
default, because a force silently ignored is how a cup gets crushed.

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
    return values


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
    primitive_settings.gripper.max_force_percent. It is set and read back before the
    gripper closes, so a machine that cannot honour it fails instead of closing at
    its own default. Postcondition: the gripper reports an object held. Returns the
    force actually read back and the holding report, which is the evidence a later
    step or gate can check. It does not prove which object was grasped.
    """
    from viam.components.gripper import Gripper

    if ctx.robot is None:
        raise ValueError('close_gripper needs a connected robot')
    config = ctx.config
    tuning = settings(config)
    if (type(force_percent) not in (int, float) or not math.isfinite(force_percent)
            or not 0 < force_percent <= tuning['max_force_percent']):
        raise ValueError(f'force_percent must be a number in (0, '
                         f'{tuning["max_force_percent"]}], the configured maximum')
    timeout = config['limits']['primitive_timeout_s']
    handle = Gripper.from_robot(ctx.robot, config['resources']['gripper'])
    applied = await apply_force(handle, force_percent, tuning, timeout)
    if not await handle.grab(timeout=timeout):
        raise RuntimeError('Gripper closed without reporting an object grasped')
    holding = await held(handle, timeout, required=tuning['require_holding'],
                         setting='primitive_settings.gripper.require_holding')
    if tuning['require_holding'] and holding is not True:
        raise RuntimeError('Gripper closed but reports nothing held')
    return {'requested_force_percent': force_percent, 'holding': holding, **applied}


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
