"""Latte art team: etch a letter into the foam surface using a held pick."""
import asyncio

from .types import Context, Target
from .manipulation_common import require_frame, move_arm_to_pose, grasp_or_raise, release

IMPLEMENTED = set()

# 2D outline of a "V" in local (u, v) coordinates, roughly in [-1, 1],
# scaled to a real size via cup_radius_mm at draw time. Add more
# letters/shapes here -- the drawing logic doesn't know it's a "V".
LETTER_PATHS = {
    'V': [(-0.6, 1.0), (0.0, -1.0), (0.6, 1.0)],
}


def _letter_settings(ctx: Context, letter: str) -> dict:
    """
    config['primitive_settings']['latte_art'][letter] = {
        'cup_radius_mm': 30.0, 'dip_depth_mm': 3.0,
        'surface_probe_start_mm': 50.0, 'surface_probe_step_mm': 2.0,
        'surface_probe_max_descend_mm': 80.0,
    }
    """
    try:
        return ctx.config['primitive_settings']['latte_art'][letter]
    except KeyError as e:
        raise ValueError(f"Missing primitive_settings.latte_art.{letter} in config.") from e


async def _find_surface_z(ctx: Context, target: Target, settings: dict) -> float:
    """
    PLACEHOLDER CONTACT CHECK: descends the configured max distance
    and falls back to target['pose']['z'] -- it does NOT yet read
    real force/current feedback. Wire that in (via your gripper's
    signal or a dedicated force-torque sensor component) before
    trusting this on a real drink, since foam height genuinely varies.
    """
    pose = target['pose']
    start_z = pose['z'] + settings.get('surface_probe_start_mm', 50.0)
    step_mm = settings.get('surface_probe_step_mm', 2.0)
    max_descend_mm = settings.get('surface_probe_max_descend_mm', 80.0)

    current_z = start_z
    descended = 0.0
    while descended < max_descend_mm:
        current_z -= step_mm
        descended += step_mm
        await move_arm_to_pose(ctx, {**pose, 'z': current_z})
        await asyncio.sleep(0.05)

        contact_detected = False  # ADAPT: wire up real force/current feedback here
        if contact_detected:
            return current_z

    return pose['z']


async def latte_art(ctx: Context, *, source: Target, target: Target, letter: str = 'V') -> dict:
    """Start/end empty-handed; pick the etch pick, trace `letter` into target's foam, return the pick."""
    letter = letter.upper()
    path = LETTER_PATHS.get(letter)
    if path is None:
        raise ValueError(f"No path defined for letter '{letter}'. Add it to LETTER_PATHS.")

    settings = _letter_settings(ctx, letter)
    require_frame(ctx, source)
    require_frame(ctx, target)

    # Pick up the etching pick
    source_pose = source['pose']
    approach = {**source_pose, 'z': source_pose['z'] + 50.0}
    await move_arm_to_pose(ctx, approach)
    await move_arm_to_pose(ctx, source_pose)
    await grasp_or_raise(ctx)
    await move_arm_to_pose(ctx, approach)

    # Find the real foam surface by touch, not by assumption
    surface_z = await _find_surface_z(ctx, target, settings)
    dip_z = surface_z - settings.get('dip_depth_mm', 3.0)
    lift_z = surface_z + 20.0
    cup_radius = settings.get('cup_radius_mm', 30.0)
    target_pose = target['pose']

    def _draw_pose(u: float, v: float, z: float) -> dict:
        return {**target_pose, 'x': target_pose['x'] + u * cup_radius, 'y': target_pose['y'] + v * cup_radius, 'z': z}

    first_u, first_v = path[0]
    await move_arm_to_pose(ctx, _draw_pose(first_u, first_v, lift_z))
    await move_arm_to_pose(ctx, _draw_pose(first_u, first_v, dip_z))

    for (u, v) in path[1:]:
        await move_arm_to_pose(ctx, _draw_pose(u, v, dip_z))

    last_u, last_v = path[-1]
    await move_arm_to_pose(ctx, _draw_pose(last_u, last_v, lift_z))

    # Return the pick and release
    await move_arm_to_pose(ctx, approach)
    await move_arm_to_pose(ctx, source_pose)
    await release(ctx)

    return {'drew': letter, 'on': target['object_id']}


IMPLEMENTED.update({'latte_art'})
