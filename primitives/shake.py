"""Shake team: agitate an object the gripper is already holding, without letting go.

`shake` is a hold-to-hold primitive, like `stir`: it starts holding the object,
shakes it in place, and finishes holding it. It never grasps or releases, so the
plan that calls it owns the grasp.

Three steps, in this order:

1. **Level the tool.** The gripper is rotated so its axis lies in the horizontal
   plane, keeping the heading it already had, so a cup gripped from the side stays
   upright through the shake. This is a planned move like any other pose.
2. **Verify the swept box.** Only once the tool is level is the stroke's box known,
   so the check happens there: every corner of it must sit inside the calibrated
   workspace. Nothing oscillates until that passes.
3. **Stroke up and down.** A fixed distance at a fixed frequency, between two poses
   the Viam motion service plans, so every stroke is collision-checked against the
   machine's configured geometry and its arrival verified. A stroke that falls short
   fails the primitive rather than becoming a quieter shake nobody notices.

Because every stroke is planned and then read back, the achieved frequency is
bounded by planning and network latency, not by this module. The result reports what
was actually achieved next to what was asked for, rather than assuming the request
was met.

Distance and frequency are calibrated physical settings in
`primitive_settings.shake`, not planner arguments; the planner chooses only how
long to shake, and the runtime bounds that by `limits.max_stir_duration_s`.
"""
import asyncio
import math
import time

from . import gripper, motion
from .types import Context, Pose

IMPLEMENTED = {'shake'}

DEFAULT_SETTINGS = {
    'stroke_mm': 15.0,        # Fixed distance the tool travels each way from centre.
    'frequency_hz': 0.5,      # Fixed rate: full up-and-down cycles per second.
    'require_holding': True,  # Only turn off for a gripper that cannot report it.
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


async def strokes(ctx, ends, tuning, plan_tuning, duration_s, timeout):
    """Alternate between the two ends of the stroke for the requested duration.

    Every stroke is planned and verified, so a blocked or stalled arm fails here
    instead of shaking through a shorter travel than asked for. Pacing targets the
    half period and never sleeps negative, so a stroke slower than the requested
    frequency simply slows the shake instead of queueing up.
    """
    half_period = 0.5 / tuning['frequency_hz']
    order = ('top', 'bottom')
    began = time.monotonic()
    count, worst = 0, {'position_error_mm': 0.0, 'orientation_error_deg': 0.0}
    while time.monotonic() - began < duration_s:
        edge = order[count % len(order)]
        arrival = await motion.plan_to(ctx, ends[edge], plan_tuning, timeout,
                                       f'shake stroke to {edge}')
        worst = {key: max(value, arrival[key]) for key, value in worst.items()}
        count += 1
        await asyncio.sleep(max(0.0, began + count * half_period - time.monotonic()))
    return count, time.monotonic() - began, worst


async def shake(ctx: Context, *, duration_s: float) -> dict:
    """Level the held object, then shake it up and down in place; finish still holding.

    Precondition: the gripper is holding the object. Postcondition: it still is, and
    the tool is back at the levelled centre of the stroke, verified through the
    motion service. Returns the stroke actually achieved -- count, frequency,
    distance -- the levelled pose, and the holding report before and after, which is
    the evidence that nothing was dropped. It does not prove the contents mixed.
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
    # 3. Stroke between the two cleared ends, then settle back at the centre.
    count, elapsed, worst = await strokes(ctx, ends, tuning, plan_tuning, duration_s, timeout)
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
            'worst_stroke_error_mm': worst['position_error_mm'],
            'worst_stroke_orientation_error_deg': worst['orientation_error_deg'],
            'holding_before': before, 'holding_after': after, **settled}
