"""
ONE file: the actual shake primitive definition AND the script that
runs it end to end (grip -> lift -> shake -> put down -> STOP, still
holding).

Speed pushed to 90 deg/s (half of xArm6's 180 deg/s hardware ceiling --
real safety margin still there, not the absolute max). frequency_hz
LEFT at 20 rather than pushed higher, since going faster on frequency
is what causes stutter, not smoothness -- each move has real network
round-trip time that a shorter requested interval can't shrink.
samples_per_stroke REDUCED 6 -> 4: fewer, more spaced-out waypoints
means fewer round-trips fighting the timing budget, letting the arm's
own onboard acceleration/deceleration profiling do more of the actual
smoothing between points, instead of our code trying to force many
tiny steps through a connection that can't keep up with them.
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
from viam.services.motion import MotionClient

from primitives import gripper as gripper_lib
from primitives import motion
from primitives.types import Context, Pose

OVERRIDE_SPEED_DEG_S = 90.0  # half of xArm6 hardware max (180) -- real speed lever


DEFAULT_SETTINGS = {
    'stroke_mm': 10.0,
    'frequency_hz': 20.0,       # left unchanged -- see module docstring
    'require_holding': True,
    'samples_per_stroke': 4,    # reduced from 6 -- see module docstring
}


def shake_settings(config):
    supplied = config.get('primitive_settings', {}).get('shake', {})
    if not isinstance(supplied, dict) or not set(supplied) <= set(DEFAULT_SETTINGS):
        raise ValueError('primitive_settings.shake accepts only: '
                         + ', '.join(sorted(DEFAULT_SETTINGS)))
    values = {**DEFAULT_SETTINGS, **supplied}
    motion.bounded(values['stroke_mm'], 0, 50, 'primitive_settings.shake.stroke_mm')
    motion.bounded(values['frequency_hz'], 0, 40, 'primitive_settings.shake.frequency_hz')
    motion.bounded(values['samples_per_stroke'], 2, 60, 'primitive_settings.shake.samples_per_stroke')
    if type(values['require_holding']) is not bool:
        raise ValueError('primitive_settings.shake.require_holding must be boolean')
    return values


def level(pose):
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


def swept_box(centre, tuning, config):
    if not config['calibrated']:
        raise ValueError('shake needs a calibrated workspace before it can move')
    ends = {edge: {**centre, 'z': centre['z'] + offset * tuning['stroke_mm']}
            for edge, offset in (('top', 1.0), ('bottom', -1.0))}
    for edge, pose in ends.items():
        within_workspace({axis: pose[axis] for axis in ('x', 'y', 'z')}, config,
                         f'shake {edge} of stroke')
    return ends


def _ease(frac):
    frac = max(0.0, min(1.0, frac))
    return frac * frac * (3 - 2 * frac)


async def _capture_joint_endpoints(ctx, arm, ends, plan_tuning, timeout):
    endpoints = {}
    for edge in ('top', 'bottom'):
        await motion.plan_to(ctx, ends[edge], plan_tuning, timeout, f'shake capture {edge}')
        joints = await arm.get_joint_positions()
        endpoints[edge] = list(joints.values)
    return endpoints


async def strokes(ctx, arm, endpoints, tuning, duration_s):
    top = endpoints['top']
    bottom = endpoints['bottom']
    n_joints = len(top)
    half_period = 0.5 / tuning['frequency_hz']
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


async def shake(ctx, *, duration_s):
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

    print(f'Bypassing motion.py speed cap -- setting {OVERRIDE_SPEED_DEG_S} deg/s directly...')
    await arm.do_command({'set_speed': OVERRIDE_SPEED_DEG_S}, timeout=timeout)

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
            'override_speed_deg_s': OVERRIDE_SPEED_DEG_S,
            'holding_before': before, 'holding_after': after, **settled}


load_dotenv()
API_KEY = os.environ['VIAM_API_KEY']
API_KEY_ID = os.environ['VIAM_API_KEY_ID']
MACHINE_ADDRESS = os.environ['VIAM_MACHINE_ADDRESS']
SHAKE_DURATION_S = 14.5
LIFT_MM = 50.0


async def connect():
    opts = RobotClient.Options.with_api_key(api_key=API_KEY, api_key_id=API_KEY_ID)
    return await RobotClient.at_address(MACHINE_ADDRESS, opts)


async def main():
    with open('config/local.json') as f:
        config = json.load(f)

    robot = await connect()

    print('Gripping...')
    handle = Gripper.from_robot(robot, config['resources']['gripper'])
    grabbed = await handle.grab()
    if grabbed is False:
        print('Gripper reported nothing grasped -- aborting.')
        await robot.close()
        return
    print('Grip confirmed.')

    ctx = Context(config=config, robot=robot)
    config, plan_tuning, timeout = motion.handles(ctx, 'shake')

    origin_pose = await motion.tool_pose(ctx)
    lift_pose = {**origin_pose, 'z': origin_pose['z'] + LIFT_MM}
    print(f'Lifting {LIFT_MM}mm...')
    await motion.plan_to(ctx, lift_pose, plan_tuning, timeout, 'lift before shake')

    print(f'Shaking for {SHAKE_DURATION_S}s...')
    result = await shake(ctx, duration_s=SHAKE_DURATION_S)
    print('Shake result:', result)

    print('Putting down...')
    await motion.plan_to(ctx, result['start_pose'], plan_tuning, timeout, 'put down after shake')

    print('Done -- still holding.')

    await robot.close()


if __name__ == '__main__':
    asyncio.run(main())
