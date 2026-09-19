"""
ONE file: the actual shake primitive definition AND the script that
runs it end to end (grip -> lift -> shake -> put down -> release).

Run from the REPO ROOT:
    python shake_full.py

This still depends on your project's real primitives/gripper.py and
primitives/motion.py (for gripper.held, motion.plan_to, motion.tool_pose,
motion.handles, motion.bounded) -- those aren't reimplemented here,
just imported, since they already exist in your repo and I haven't
seen their internals to safely rewrite them.

Requires:
  - .env filled in with a REAL (regenerated, non-leaked) API key,
    key ID, and machine address.
  - config/local.json present, with "calibrated": true and a real
    workspace_mm.
  - The gripper positioned at the object, gripping it FROM THE SIDE
    (level() below requires this and raises otherwise).
"""
import asyncio
import json
import math
import os
import time

from dotenv import load_dotenv
from viam.robot.client import RobotClient
from viam.components.arm import Arm
from viam.components.gripper import Gripper
from viam.proto.component.arm import JointPositions

from primitives import gripper as gripper_lib
from primitives import motion
from primitives.types import Context, Pose


# =============================================================================
# THE PRIMITIVE ITSELF (same as primitives/shake.py, joint-space version)
# =============================================================================

DEFAULT_SETTINGS = {
    'stroke_mm': 15.0,         # Fixed distance the tool travels each way from centre.
    'frequency_hz': 0.5,       # Fixed rate: full up-and-down cycles per second.
    'require_holding': True,   # Only turn off for a gripper that cannot report it.
    'samples_per_stroke': 12,  # Joint-space waypoints per half-cycle. More = smoother, slower to compute.
}


def shake_settings(config):
    """Validate the team-owned primitive_settings.shake block over the defaults."""
    supplied = config.get('primitive_settings', {}).get('shake', {})
    if not isinstance(supplied, dict) or not set(supplied) <= set(DEFAULT_SETTINGS):
        raise ValueError('primitive_settings.shake accepts only: '
                         + ', '.join(sorted(DEFAULT_SETTINGS)))
    values = {**DEFAULT_SETTINGS, **supplied}
    motion.bounded(values['stroke_mm'], 0, 50, 'primitive_settings.shake.stroke_mm')
    motion.bounded(values['frequency_hz'], 0, 2, 'primitive_settings.shake.frequency_hz')
    motion.bounded(values['samples_per_stroke'], 2, 60, 'primitive_settings.shake.samples_per_stroke')
    if type(values['require_holding']) is not bool:
        raise ValueError('primitive_settings.shake.require_holding must be boolean')
    return values


def level(pose: Pose) -> Pose:
    """The same position with the tool axis rotated into the horizontal plane.
    Keeps the heading it already had -- a side grip stays upright through the shake."""
    heading = math.hypot(pose['o_x'], pose['o_y'])
    if heading < 1e-6:
        raise ValueError('The tool points straight up or down, so it has no horizontal '
                         'heading to keep. Grip the object from the side before shaking.')
    return {**pose, 'o_x': pose['o_x'] / heading, 'o_y': pose['o_y'] / heading, 'o_z': 0.0}


def within_workspace(point, config, what):
    for axis, value in point.items():
        low, high = config['workspace_mm'][axis]
        if not low <= value <= high:
            raise ValueError(f'{what}: {axis}={value:.1f} mm is outside the calibrated '
                             f'workspace [{low}, {high}]')


def swept_box(centre: Pose, tuning, config):
    """Both ends of the stroke -- UP AND DOWN, only z changes -- refused
    before moving unless the whole box is clear."""
    if not config['calibrated']:
        raise ValueError('shake needs a calibrated workspace before it can move')
    ends = {edge: {**centre, 'z': centre['z'] + offset * tuning['stroke_mm']}
            for edge, offset in (('top', 1.0), ('bottom', -1.0))}
    for edge, pose in ends.items():
        within_workspace({axis: pose[axis] for axis in ('x', 'y', 'z')}, config,
                         f'shake {edge} of stroke')
    return ends


def _ease(frac: float) -> float:
    """Smoothstep -- accelerate into and decelerate out of each stroke end."""
    frac = max(0.0, min(1.0, frac))
    return frac * frac * (3 - 2 * frac)


async def _capture_joint_endpoints(ctx, arm, ends, plan_tuning, timeout):
    """Plans to each verified end of the stroke once, reading back the
    arm's real joint positions on arrival."""
    endpoints = {}
    for edge in ('top', 'bottom'):
        await motion.plan_to(ctx, ends[edge], plan_tuning, timeout, f'shake capture {edge}')
        joints = await arm.get_joint_positions()
        endpoints[edge] = list(joints.values)
    return endpoints


async def strokes(ctx, arm, endpoints, tuning, duration_s):
    """Oscillate directly between the two captured joint configurations --
    this is the FAST, done-many-times-per-second part."""
    top = endpoints['top']
    bottom = endpoints['bottom']
    n_joints = len(top)

    half_period = 0.5 / tuning['frequency_hz']  # <-- THIS is the speed control
    samples = max(int(tuning['samples_per_stroke']), 2)
    step_s = half_period / samples

    began = time.monotonic()
    count = 0
    at_top = True

    while time.monotonic() - began < duration_s:
        start_joints = bottom if at_top else top
        end_joints = top if at_top else bottom
        for i in range(1, samples + 1):
            frac = _ease(i / samples)
            target = [
                start_joints[j] + (end_joints[j] - start_joints[j]) * frac
                for j in range(n_joints)
            ]
            await arm.move_to_joint_positions(JointPositions(values=target))
            await asyncio.sleep(step_s)
        count += 1
        at_top = not at_top

    return count, time.monotonic() - began


async def shake(ctx: Context, *, duration_s: float) -> dict:
    """Level the held object, then shake it up and down in place; finish still holding.

    Precondition: the gripper is holding the object, gripped from the side.
    Postcondition: it still is, and the tool is back at the levelled centre.
    """
    config, plan_tuning, timeout = motion.handles(ctx, 'shake')
    tuning = shake_settings(config)
    motion.bounded(duration_s, 0, config['limits']['max_stir_duration_s'], 'duration_s')
    if not config['calibrated']:
        raise ValueError('shake needs a calibrated workspace before it can move')
    handle = Gripper.from_robot(ctx.robot, config['resources']['gripper'])
    holding = dict(required=tuning['require_holding'],
                   setting='primitive_settings.shake.require_holding')
    before = await gripper_lib.held(handle, timeout, **holding)
    if tuning['require_holding'] and before is not True:
        raise RuntimeError('shake starts from a held object; the gripper reports nothing held')
    start = await motion.tool_pose(ctx)
    within_workspace({axis: start[axis] for axis in ('x', 'y', 'z')}, config, 'shake start')

    centre = await motion.plan_to(ctx, level(start), plan_tuning, timeout, 'shake levelling')
    ends = swept_box(centre['pose'], tuning, config)

    arm = Arm.from_robot(ctx.robot, config['resources']['arm'])
    endpoints = await _capture_joint_endpoints(ctx, arm, ends, plan_tuning, timeout)

    count, elapsed = await strokes(ctx, arm, endpoints, tuning, duration_s)

    settled = await motion.plan_to(ctx, centre['pose'], plan_tuning, timeout, 'shake centre')
    after = await gripper_lib.held(handle, timeout, **holding)
    if tuning['require_holding'] and after is not True:
        raise RuntimeError('Object was dropped during the shake; the gripper holds nothing')
    return {'requested_duration_s': duration_s, 'duration_s': elapsed, 'strokes': count,
            'requested_frequency_hz': tuning['frequency_hz'],
            'frequency_hz': count / (2 * elapsed) if elapsed else 0.0,
            'stroke_mm': tuning['stroke_mm'], 'start_pose': start,
            'levelled_pose': centre['pose'],
            'swept_z_mm': [ends['bottom']['z'], ends['top']['z']],
            'holding_before': before, 'holding_after': after, **settled}


# =============================================================================
# THE ORCHESTRATION (run this script -> this is what actually happens)
# =============================================================================

load_dotenv()

API_KEY = os.environ['VIAM_API_KEY']
API_KEY_ID = os.environ['VIAM_API_KEY_ID']
MACHINE_ADDRESS = os.environ['VIAM_MACHINE_ADDRESS']

SHAKE_DURATION_S = 5.0
LIFT_MM = 50.0


async def connect():
    opts = RobotClient.Options.with_api_key(api_key=API_KEY, api_key_id=API_KEY_ID)
    return await RobotClient.at_address(MACHINE_ADDRESS, opts)


async def main():
    with open('config/local.json') as f:
        config = json.load(f)

    robot = await connect()
    ctx = Context(config=config, robot=robot)
    handle = Gripper.from_robot(robot, config['resources']['gripper'])

    config, plan_tuning, timeout = motion.handles(ctx, 'shake')

    print('Gripping...')
    grabbed = await handle.grab()
    if grabbed is False:
        print('Gripper reported nothing grasped -- aborting.')
        await robot.close()
        return
    print('Grip confirmed.')

    origin_pose = await motion.tool_pose(ctx)
    lift_pose = {**origin_pose, 'z': origin_pose['z'] + LIFT_MM}
    print(f'Lifting {LIFT_MM}mm...')
    await motion.plan_to(ctx, lift_pose, plan_tuning, timeout, 'lift before shake')

    print(f'Shaking for {SHAKE_DURATION_S}s...')
    result = await shake(ctx, duration_s=SHAKE_DURATION_S)
    print('Shake result:', result)

    print('Putting down...')
    await motion.plan_to(ctx, result['start_pose'], plan_tuning, timeout, 'put down after shake')

    print('Releasing...')
    await handle.open()

    await robot.close()
    print('Done.')


if __name__ == '__main__':
    asyncio.run(main())
