"""
ONE file: the actual shake primitive definition AND the script that
runs it end to end (grip -> lift -> shake -> put down -> STOP, still
holding).

Direct mode sends one controller-profiled joint move per half-stroke, using
the Viam UFactory module's `extra={direct: True, speed_d: ...}` extension.
It avoids restarting Viam's interpolator for every tiny waypoint. Completion
is still awaited; commands are never queued ahead of the arm. The existing
90 deg/s request applies only to stroke commands, not global arm settings.
Acceleration and hardware limits remain the driver's responsibility.

frequency_hz is a pacing target, not a guarantee. Motion and readback time
count toward that budget. Results report achieved frequency and overruns.
The previous waypoint path is available with control_mode="sampled".
See docs/shake-speed.md for driver compatibility and validation status.
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

OVERRIDE_SPEED_DEG_S = 90.0  # Existing script request; not a payload safety certification.


DEFAULT_SETTINGS = {
    'stroke_mm': 10.0,
    'frequency_hz': 20.0,       # Requested ceiling; report what the arm achieves.
    'require_holding': True,
    'samples_per_stroke': 4,    # Used only by the comparison mode, "sampled".
    'control_mode': 'direct',
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
    if values['control_mode'] not in ('direct', 'sampled'):
        raise ValueError('primitive_settings.shake.control_mode must be direct or sampled')
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
        joints = await arm.get_joint_positions(timeout=timeout)
        endpoints[edge] = list(joints.values)
    return endpoints


async def strokes(ctx, arm, endpoints, tuning, duration_s):
    """Run bounded, sequential strokes; never skip ahead when the arm is late.

    Endpoint capture leaves the arm at bottom. Both control modes follow the
    same joint-space line; neither proves Cartesian or whole-arm clearance.
    """
    config, plan_tuning, timeout = motion.handles(ctx, 'shake')
    motion.bounded(duration_s, 0, config['limits']['max_stir_duration_s'], 'duration_s')
    motion.bounded(OVERRIDE_SPEED_DEG_S, 0, 90, 'shake stroke speed_deg_s')
    top = endpoints['top']
    bottom = endpoints['bottom']
    n_joints = len(top)
    if not n_joints or len(bottom) != n_joints or any(
            not math.isfinite(v) for v in [*top, *bottom]):
        raise ValueError('shake endpoints must contain matching finite joint positions')
    half_period = 0.5 / tuning['frequency_hz']
    direct = tuning['control_mode'] == 'direct'
    samples = 1 if direct else max(int(tuning['samples_per_stroke']), 2)
    step_s = half_period / samples
    began = time.monotonic()
    count = 0
    calls = 0
    motion_s = 0.0
    readback_s = 0.0
    sleep_s = 0.0
    overruns = 0
    at_top = True
    while time.monotonic() - began < duration_s:
        stroke_began = time.monotonic()
        stroke_overran = False
        start_joints = bottom if at_top else top
        end_joints = top if at_top else bottom
        for i in range(1, samples + 1):
            if time.monotonic() - began >= duration_s:
                break
            frac = _ease(i / samples)
            target = [
                start_joints[j] + (end_joints[j] - start_joints[j]) * frac
                for j in range(n_joints)
            ]
            called = time.monotonic()
            await arm.move_to_joint_positions(
                JointPositions(values=target),
                extra={'direct': direct, 'speed_d': OVERRIDE_SPEED_DEG_S,
                       'waitAtEnd': True},
                timeout=timeout)
            motion_s += time.monotonic() - called
            calls += 1
            if i == samples:
                read_started = time.monotonic()
                reached = list((await arm.get_joint_positions(timeout=timeout)).values)
                readback_s += time.monotonic() - read_started
                if len(reached) != n_joints or any(not math.isfinite(v) for v in reached):
                    raise RuntimeError('shake endpoint readback has invalid joint positions')
                error = max(abs(a - b) for a, b in zip(reached, end_joints))
                if error > plan_tuning['joint_tolerance_deg']:
                    raise RuntimeError(f'shake endpoint missed by {error:.2f} degrees')
                stroke_overran = time.monotonic() - stroke_began > half_period
            # Count command latency as part of the interval, not an extra delay.
            # Anchor each stroke separately so a late stroke causes no catch-up burst.
            delay = min(stroke_began + i * step_s, began + duration_s) - time.monotonic()
            if delay > 0:
                sleep_started = time.monotonic()
                await asyncio.sleep(delay)
                sleep_s += time.monotonic() - sleep_started
        else:
            count += 1
            overruns += stroke_overran
            at_top = not at_top
            continue
        # Duration expired partway through a sampled stroke. Do not count it.
        break
    elapsed = time.monotonic() - began
    return {'strokes': count, 'duration_s': elapsed,
            'frequency_hz': count / (2 * elapsed) if elapsed else 0.0,
            'control_mode': tuning['control_mode'], 'move_calls': calls,
            'motion_call_time_s': motion_s, 'joint_readback_time_s': readback_s,
            'pacing_sleep_s': sleep_s, 'overrun_strokes': overruns}


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

    endpoints = await _capture_joint_endpoints(ctx, arm, ends, plan_tuning, timeout)
    print(f'Shake mode: {tuning["control_mode"]}; stroke speed request '
          f'{OVERRIDE_SPEED_DEG_S} deg/s; frequency target {tuning["frequency_hz"]} Hz')
    timing = await strokes(ctx, arm, endpoints, tuning, duration_s)
    print(f'Achieved {timing["frequency_hz"]:.2f} Hz; '
          f'{timing["move_calls"]} move calls; {timing["overrun_strokes"]} late strokes')
    settled = await motion.plan_to(ctx, centre['pose'], plan_tuning, timeout, 'shake centre')
    after = await gripper_lib.held(handle, timeout, **holding)
    if tuning['require_holding'] and after is not True:
        raise RuntimeError('Object was dropped during the shake; the gripper holds nothing')
    return {'requested_duration_s': duration_s, **timing,
            'requested_frequency_hz': tuning['frequency_hz'],
            'stroke_mm': tuning['stroke_mm'], 'start_pose': start,
            'levelled_pose': centre['pose'],
            'swept_z_mm': [ends['bottom']['z'], ends['top']['z']],
            'override_speed_deg_s': OVERRIDE_SPEED_DEG_S,
            'holding_before': before, 'holding_after': after, **settled}


SHAKE_DURATION_S = 14.5
LIFT_MM = 50.0


async def connect():
    load_dotenv()
    opts = RobotClient.Options.with_api_key(api_key=os.environ['VIAM_API_KEY'],
                                           api_key_id=os.environ['VIAM_API_KEY_ID'])
    return await RobotClient.at_address(os.environ['VIAM_MACHINE_ADDRESS'], opts)


async def run_sequence(robot, config):
    print('Gripping...')
    handle = Gripper.from_robot(robot, config['resources']['gripper'])
    grabbed = await handle.grab()
    if grabbed is False:
        print('Gripper reported nothing grasped -- aborting.')
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


async def main():
    with open('config/local.json') as f:
        config = json.load(f)
    robot = await connect()
    try:
        await run_sequence(robot, config)
    except (Exception, asyncio.CancelledError):
        try:
            await asyncio.wait_for(robot.stop_all(),
                                   timeout=config['limits']['primitive_timeout_s'])
        except Exception as stop_error:
            print(f'StopAll failed; arm state is unknown: {stop_error}')
        raise
    finally:
        await robot.close()


if __name__ == '__main__':
    asyncio.run(main())
