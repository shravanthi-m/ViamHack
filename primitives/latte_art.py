"""
Latte art team: pour-drawing version.

Picks up a milk/cream/foam source, tilts it to start a pour (same
theta-ramp technique as pouring.py), traces `letter`'s shape WHILE
still tilted (the pour stays running as the arm moves through the
path), then untilts to stop, and returns the source.

This is different from an etching approach (dragging a solid tool
through already-poured foam): here nothing has been poured into the
cup yet for this letter -- the pour itself IS the drawing motion.
Getting a continuous, legible line this way is a genuinely harder
physical problem than etching, because pour rate, movement speed, and
height above the cup all have to stay in sync -- move too fast and the
line breaks up or goes too thin, too slow and it pools instead of
tracing a clean shape.
"""
import asyncio

from .types import Context, Target
from .manipulation_common import require_frame, move_arm_to_pose, grasp_or_raise, release, ramp_theta

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
        'tilt_deg': 40.0,          # how far to tilt to start the pour
        'ramp_up_s': 1.0,          # how long to ramp into full tilt
        'ramp_down_s': 1.0,        # how long to ramp back down to stop
        'pour_height_mm': 40.0,    # height above the cup's surface while pouring
        'cup_radius_mm': 30.0,     # scales the (u, v) path to a real size
        'dwell_s_per_point': 0.3,  # how long to linger at each path point while pouring
    }

    All of these are placeholders needing real calibration -- pour
    height and dwell time especially will need physical testing to
    get a line that reads as a "V" rather than a blob or a broken dribble.
    """
    try:
        return ctx.config['primitive_settings']['latte_art'][letter]
    except KeyError as e:
        raise ValueError(f"Missing primitive_settings.latte_art.{letter} in config.") from e


async def latte_art(ctx: Context, *, source: Target, target: Target, letter: str = 'V') -> dict:
    """Start/end empty-handed; pick up source, tilt to pour, trace `letter` while pouring, stop, return source."""
    letter = letter.upper()
    path = LETTER_PATHS.get(letter)
    if path is None:
        raise ValueError(f"No path defined for letter '{letter}'. Add it to LETTER_PATHS.")

    settings = _letter_settings(ctx, letter)
    require_frame(ctx, source)
    require_frame(ctx, target)

    # 1. Pick up the milk/cream/foam source
    source_pose = source['pose']
    approach = {**source_pose, 'z': source_pose['z'] + 50.0}
    await move_arm_to_pose(ctx, approach)
    await move_arm_to_pose(ctx, source_pose)
    await grasp_or_raise(ctx)
    await move_arm_to_pose(ctx, approach)

    target_pose = target['pose']
    cup_radius = settings.get('cup_radius_mm', 30.0)
    pour_height = settings.get('pour_height_mm', 40.0)
    baseline_theta = target_pose['theta']  # "not tilted" reference
    peak_theta = baseline_theta + settings['tilt_deg']

    def draw_pose(u: float, v: float, theta: float) -> dict:
        return {
            **target_pose,
            'x': target_pose['x'] + u * cup_radius,
            'y': target_pose['y'] + v * cup_radius,
            'z': target_pose['z'] + pour_height,
            'theta': theta,
        }

    # 2. Move to the V's first point, NOT tilted yet
    first_u, first_v = path[0]
    await move_arm_to_pose(ctx, draw_pose(first_u, first_v, baseline_theta))

    # 3. Tilt up to start the pour, staying at that first point
    await ramp_theta(ctx, draw_pose(first_u, first_v, baseline_theta), baseline_theta, peak_theta, settings['ramp_up_s'])

    # 4. Trace the rest of the path WHILE STILL TILTED -- the pour
    # keeps running as the arm moves from point to point. This is the
    # actual "drawing" step.
    for (u, v) in path[1:]:
        await move_arm_to_pose(ctx, draw_pose(u, v, peak_theta))
        await asyncio.sleep(settings.get('dwell_s_per_point', 0.3))

    # 5. Tilt back down at the last point to stop the pour
    last_u, last_v = path[-1]
    await ramp_theta(ctx, draw_pose(last_u, last_v, peak_theta), peak_theta, baseline_theta, settings['ramp_down_s'])

    # 6. Lift clear, then return the source and release
    lift_pose = {**draw_pose(last_u, last_v, baseline_theta), 'z': target_pose['z'] + pour_height + 20.0}
    await move_arm_to_pose(ctx, lift_pose)
    await move_arm_to_pose(ctx, approach)
    await move_arm_to_pose(ctx, source_pose)
    await release(ctx)

    return {'poured_letter': letter, 'on': target['object_id']}


IMPLEMENTED.update({'latte_art'})
