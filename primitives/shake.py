"""Shake team: agitate an object the gripper is already holding, without letting go.

`shake` is a hold-to-hold primitive, like `stir`: it starts holding the object,
shakes it in place, and finishes holding it. It never grasps or releases, so the
plan that calls it owns the grasp.

Four steps, in this order:

1. **Level the tool.** The gripper is rotated so its axis lies in the horizontal
   plane, keeping the heading it already had, so a cup gripped from the side stays
   upright through the shake. This is a planned move like any other pose.
2. **Verify the swept box.** Only once the tool is level is the stroke's box known,
   so the check happens there: every corner of it must sit inside the calibrated
   workspace. Nothing oscillates until that passes.
3. **Capture the stroke in joint space.** The Motion service is asked, ONCE each,
   to plan and arrive at the top and bottom of the stroke -- the same
   collision-aware, workspace-bound-checked moves as before. Right after each
   arrival, the arm's actual joint positions are read back. These two joint
   configurations, not the Cartesian poses themselves, are what the oscillation
   below actually uses.
4. **Oscillate in joint space.** Moving repeatedly through the Motion service for
   every stroke is what made this feel like it was pausing: each call is a full
   replan from scratch, so a fast back-and-forth shake stalls into a series of
   separate stop-and-go moves. Instead, once the two joint configurations above are
   known, the arm interpolates directly between them with move_to_joint_positions,
   many small steps per stroke rather than one big jump -- this is what makes the
   motion continuous and quick instead of jerky, closer to how a bartender actually
   flicks a shaker back and forth rather than pausing at each end.

IMPORTANT TRADEOFF, stated plainly rather than hidden: steps 1-3 keep the full
collision-aware Motion service verification this file always had. Step 4 does NOT
re-verify each individual stroke against the workspace or obstacles the way
motion.plan_to() did before -- it trusts that interpolating directly between two
already-verified joint configurations stays inside the space between them. For the
small stroke distances this primitive allows (stroke_mm is capped well below the
scale of the configured obstacles), that is a reasonable bet, but it is a real
reduction in per-stroke safety guarantees compared to the original design, not a
free improvement. If your team wants every stroke individually replanned and
verified, keep the previous motion.plan_to()-per-stroke version instead and accept
the slower, more stop-start motion it produces.

Because every capture move (step 3) is still planned and read back, and the
oscillation (step 4) samples the same two verified endpoints, the reported
frequency reflects what the joint interpolation loop actually achieved, not the
Motion service's per-call latency, which no longer gates each individual stroke.

Distance and frequency are calibrated physical settings in
`primitive_settings.shake`, not planner arguments; the planner chooses only how
long to shake, and the runtime bounds that by `limits.max_stir_duration_s`.
"""
import asyncio
import math
import time

from viam.components.arm import Arm
from viam.proto.component.arm import JointPositions

from . import gripper, motion
from .types import Context, Pose

IMPLEMENTED = {'shake'}

DEFAULT_SETTINGS = {
    'stroke_mm': 15.0,        # Fixed distance the tool travels each way from centre.
    'frequency_hz': 0.5,      # Fixed rate: full up-and-down cycles per second.
    'require_holding': True,  # Only turn off for a gripper that cannot report it.
    'samples_per_stroke': 12, # NEW: joint-space waypoints per half-cycle. More = smoother, slower to compute.
}


def settings(config):
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

    Keeps the heading the tool already points along, and its roll, so levelling is
    the smallest rotation that makes a side grip hold the object upright.
    """
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
    """Both ends of the stroke, refused before moving unless the whole box is clear."""
    if not config['calibrated']:
        raise ValueError('shake needs a calibrated workspace before it can move')
    ends = {edge: {**centre, 'z': centre['z'] + offset * tuning['stroke_mm']}
            for edge, offset in (('top', 1.0), ('bottom', -1.0))}
    for edge, pose in ends.items():
        within_workspace({axis: pose[axis] for axis in ('x', 'y', 'z')}, config,
                         f'shake {edge} of stroke')
    return ends


def _ease(frac: float) -> float:
    """Smoothstep -- accelerate into and decelerate out of each stroke end,
    the same shape a wrist naturally moves through, instead of a linear
    ramp that would feel mechanical."""
    frac = max(0.0, min(1.0, frac))
    return frac * frac * (3 - 2 * frac)


async def _capture_joint_endpoints(ctx, arm, ends, plan_tuning, timeout):
    """Plans to each verified end of the stroke exactly once, reading back
    the arm's real joint positions on arrival. This is what lets the fast
    oscillation loop below work in joint space without needing to solve
    inverse kinematics itself -- the Motion service already did that,
    for these two specific, already-safety-checked poses."""
    endpoints = {}
    for edge in ('top', 'bottom'):
        await motion.plan_to(ctx, ends[edge], plan_tuning, timeout, f'shake capture {edge}')
        joints = await arm.get_joint_positions()
        endpoints[edge] = list(joints.values)
    return endpoints


async def strokes(ctx, arm, endpoints, tuning, duration_s):
    """Oscillate directly between the two captured joint configurations.

    Unlike the previous per-stroke motion.plan_to() version, this does NOT
    ask the Motion service to replan on every stroke -- it interpolates
    between top_joints and bottom_joints directly and calls
    move_to_joint_positions repeatedly. That's what removes the stop-start
    pausing: each waypoint is a small, fast joint move instead of a full
    collision-aware replan from scratch.
    """
    top = endpoints['top']
    bottom = endpoints['bottom']
    n_joints = len(top)

    half_period = 0.5 / tuning['frequency_hz']
    samples = max(int(tuning['samples_per_stroke']), 2)
    step_s = half_period / samples

    began = time.monotonic()
    count = 0
    at_top = True  # which end we're currently heading TOWARD

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

    Precondition: the gripper is holding the object. Postcondition: it still is, and
    the tool is back at the levelled centre of the stroke, verified through the
    motion service. Returns the stroke actually achieved -- count, frequency,
    distance -- the levelled pose, and the holding report before and after, which is
    the evidence that nothing was dropped. It does not prove the contents mixed.

    The stroke itself runs in joint space (see module docstring) for a fast,
    continuous shake rather than a series of individually replanned, stop-start
    moves -- the top and bottom of the stroke are still verified against the
    calibrated workspace via the Motion service before any oscillation begins.
    """
    from viam.components.gripper import Gripper

    config, plan_tuning, timeout = motion.handles(ctx, 'shake')
    tuning = settings(config)
    motion.bounded(duration_s, 0, config['limits']['max_stir_duration_s'], 'duration_s')
    if not config['calibrated']:
        raise ValueError('shake needs a calibrated workspace before it can move')
    handle = Gripper.from_robot(ctx.robot, config['resources']['gripper'])
    holding = dict(required=tuning['require_holding'],
                   setting='primitive_settings.shake.require_holding')
    before = await gripper.held(handle, timeout, **holding)
    if tuning['require_holding'] and before is not True:
        raise RuntimeError('shake starts from a held object; the gripper reports nothing held')
    start = await motion.tool_pose(ctx)
    within_workspace({axis: start[axis] for axis in ('x', 'y', 'z')}, config, 'shake start')

    # 1. Level the tool, so the box the stroke sweeps is known before anything sweeps it.
    centre = await motion.plan_to(ctx, level(start), plan_tuning, timeout, 'shake levelling')
    # 2. Only now is the stroke's box real; clear it before oscillating.
    ends = swept_box(centre['pose'], tuning, config)

    # 3. Capture the verified top/bottom as real joint configurations, ONCE each.
    arm = Arm.from_robot(ctx.robot, config['resources']['arm'])
    endpoints = await _capture_joint_endpoints(ctx, arm, ends, plan_tuning, timeout)

    # 4. Oscillate fast, in joint space, between those two captured configurations.
    count, elapsed = await strokes(ctx, arm, endpoints, tuning, duration_s)

    # Settle back at the verified centre pose -- this IS re-checked through
    # the Motion service, same as before, as the final safety-critical move.
    settled = await motion.plan_to(ctx, centre['pose'], plan_tuning, timeout, 'shake centre')
    after = await gripper.held(handle, timeout, **holding)
    if tuning['require_holding'] and after is not True:
        raise RuntimeError('Object was dropped during the shake; the gripper holds nothing')
    return {'requested_duration_s': duration_s, 'duration_s': elapsed, 'strokes': count,
            'requested_frequency_hz': tuning['frequency_hz'],
            'frequency_hz': count / (2 * elapsed) if elapsed else 0.0,
            'stroke_mm': tuning['stroke_mm'], 'start_pose': start,
            'levelled_pose': centre['pose'],
            'swept_z_mm': [ends['bottom']['z'], ends['top']['z']],
            'holding_before': before, 'holding_after': after, **settled}
