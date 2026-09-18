"""Cup team: fully encapsulate grasp, vertical shake, return, and release."""
import asyncio

from .types import Context, Target
from .manipulation_common import require_frame, move_arm_to_pose, grasp_or_raise, release

IMPLEMENTED = set()

APPROACH_HEIGHT_MM = 50.0


def _shake_settings(ctx: Context, object_id: str) -> dict:
    """
    config['primitive_settings']['shake'][object_id] = {
        'amplitude_mm': 15.0,  # how far up/down each stroke travels
        'hz': 2.0,             # how many up-down cycles per second
    }

    Keep amplitude modest to start -- this is lifting and jostling a
    cup that already has liquid in it. Too large an amplitude (or too
    fast an hz) risks sloshing contents over the rim, same spill risk
    we discussed for the lid earlier. If this cup doesn't have a lid
    on it, treat that as a hard prerequisite before shaking rather than
    something to just tune around.
    """
    try:
        return ctx.config['primitive_settings']['shake'][object_id]
    except KeyError as e:
        raise ValueError(f"Missing primitive_settings.shake.{object_id} in config.") from e


async def shake(ctx: Context, *, target: Target, duration_s: float) -> dict:
    """Start/end empty-handed; pick up the cup, shake it up and down in place, put it back, release."""
    require_frame(ctx, target)

    max_duration = ctx.config.get('limits', {}).get('max_shake_duration_s')
    if max_duration is not None and duration_s > max_duration:
        raise ValueError(f'duration_s={duration_s} exceeds configured max_shake_duration_s={max_duration}')

    settings = _shake_settings(ctx, target['object_id'])
    amplitude = settings['amplitude_mm']
    hz = settings['hz']

    pose = target['pose']
    approach = {**pose, 'z': pose['z'] + APPROACH_HEIGHT_MM}

    # 1. Pick up the cup
    await move_arm_to_pose(ctx, approach)
    await move_arm_to_pose(ctx, pose)
    await grasp_or_raise(ctx)
    await move_arm_to_pose(ctx, approach)

    # 2. Shake: oscillate z around the lifted height. 4 sub-steps per
    # cycle (up, mid, down, mid) for a smoother stroke than a raw
    # square wave between two extremes.
    base_z = approach['z']
    step_s = 1.0 / (hz * 4)
    elapsed = 0.0
    while elapsed < duration_s:
        for dz in (amplitude, 0.0, -amplitude, 0.0):
            await move_arm_to_pose(ctx, {**approach, 'z': base_z + dz})
            await asyncio.sleep(step_s)
            elapsed += step_s
            if elapsed >= duration_s:
                break

    # 3. Settle back to the neutral lifted height before returning
    await move_arm_to_pose(ctx, approach)

    # 4. Put the cup back and release
    await move_arm_to_pose(ctx, pose)
    await release(ctx)

    return {'shaken': target['object_id'], 'duration_s': duration_s, 'amplitude_mm': amplitude, 'hz': hz}


IMPLEMENTED.update({'shake'})
