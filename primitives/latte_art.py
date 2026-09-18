"""
Latte art team: pour-drawing version. Self-contained -- everything
this needs lives in this one file, built only from types.py and
direct Viam SDK calls (no shared helper module).

Picks up a milk/cream/foam source, tilts it to start a pour, traces
`letter`'s shape WHILE still tilted (the pour keeps running as the arm
moves through the path), then untilts to stop, and returns the source.
"""
import asyncio

from viam.components.arm import Arm
from viam.components.gripper import Gripper
from viam.proto.common import Pose as ViamPose, PoseInFrame
from viam.services.motion import MotionClient

from .types import Context, Target, Pose

IMPLEMENTED = set()

# 2D outline of a "V" in local (u, v) coordinates, roughly in [-1, 1],
# scaled to a real size via cup_radius_mm at draw time. Add more
# letters/shapes here -- the drawing logic doesn't know it's a "V".
LETTER_PATHS = {
    'V': [(-0.6, 1.0), (0.0, -1.0), (0.6, 1.0)],
}


# ---- Internal helpers (real Viam SDK calls only, nothing imported from elsewhere) ----

def _get_arm(ctx: Context) -> Arm:
    return Arm.from_robot(ctx.robot, ctx.config['resources']['arm'])


def _get_gripper(ctx: Context) -> Gripper:
    return Gripper.from_robot(ctx.robot, ctx.config['resources']['gripper'])


def _require_frame(ctx: Context, target: Target) -> None:
    """Targets are only valid in the configured task frame -- fail loudly
    rather than silently acting on a mismatched frame."""
    if target['frame'] != ctx.config['frame']:
        raise ValueError(
            f"Target '{target['object_id']}' is in frame '{target['frame']}', "
            f"expected '{ctx.config['frame']}'. Re-localize before using it."
        )


def _to_viam_pose(pose: Pose) -> ViamPose:
    """Target poses are already Viam-native units (mm, orientation vector,
    theta in degrees) -- this is a direct field mapping, no conversion."""
    return ViamPose(
        x=pose['x'], y=pose['y'], z=pose['z'],
        o_x=pose['o_x'], o_y=pose['o_y'], o_z=pose['o_z'],
        theta=pose['theta'],
    )


async def _move_arm_to_pose(ctx: Context, pose: Pose) -> None:
    """
    Moves to `pose`, already expressed in ctx.config['frame'].

    Prefers the Motion service (frame- and obstacle-aware), falling
    back to a direct arm move only if that call fails (e.g. Motion
    service not configured on this machine).
    """
    try:
        motion = MotionClient.from_robot(ctx.robot, 'builtin')
        destination = PoseInFrame(reference_frame=ctx.config['frame'], pose=_to_viam_pose(pose))
        success = await motion.move(component_name=ctx.config['resources']['arm'], destination=destination)
        if success is False:
            raise RuntimeError(f'Motion service reported failure moving to pose {pose}')
        return
    except Exception:
        pass  # fall back to a direct arm move below

    arm = _get_arm(ctx)
    await arm.move_to_position(_to_viam_pose(pose))


async def _grasp_or_raise(ctx: Context) -> None:
    """Wraps gripper.grab(); converts a failed-grasp boolean into a raised
    exception -- never let a bare False propagate as a return value."""
    gripper = _get_gripper(ctx)
    grabbed = await gripper.grab()
    if grabbed is False:
        raise RuntimeError('Gripper reported no object grasped')


async def _release(ctx: Context) -> None:
    await _get_gripper(ctx).open()


async def _ramp_theta(ctx: Context, base_pose: Pose, start_theta: float, end_theta: float, duration_s: float, hz: float = 10.0) -> None:
    """Smoothly varies ONLY theta (rotation about the tool's pointing
    axis) from start_theta to end_theta, holding x, y, z fixed. This
    is the tilt: ramping up starts the pour, ramping down stops it."""
    steps = max(int(duration_s * hz), 2)
    for i in range(1, steps + 1):
        frac = i / steps
        eased = frac * frac * (3 - 2 * frac)  # smoothstep, avoids a jerky snap-tilt
        theta = start_theta + (end_theta - start_theta) * eased
        await _move_arm_to_pose(ctx, {**base_pose, 'theta': theta})
        await asyncio.sleep(1.0 / hz)


# ---- Config ----

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


# ---- The primitive itself ----

async def latte_art(ctx: Context, *, source: Target, target: Target, letter: str = 'V') -> dict:
    """Start/end empty-handed; pick up source, tilt to pour, trace `letter` while pouring, stop, return source."""
    letter = letter.upper()
    path = LETTER_PATHS.get(letter)
    if path is None:
        raise ValueError(f"No path defined for letter '{letter}'. Add it to LETTER_PATHS.")

    settings = _letter_settings(ctx, letter)
    _require_frame(ctx, source)
    _require_frame(ctx, target)

    # 1. Pick up the milk/cream/foam source
    source_pose = source['pose']
    approach = {**source_pose, 'z': source_pose['z'] + 50.0}
    await _move_arm_to_pose(ctx, approach)
    await _move_arm_to_pose(ctx, source_pose)
    await _grasp_or_raise(ctx)
    await _move_arm_to_pose(ctx, approach)

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
    await _move_arm_to_pose(ctx, draw_pose(first_u, first_v, baseline_theta))

    # 3. Tilt up to start the pour, staying at that first point
    await _ramp_theta(ctx, draw_pose(first_u, first_v, baseline_theta), baseline_theta, peak_theta, settings['ramp_up_s'])

    # 4. Trace the rest of the path WHILE STILL TILTED -- the pour
    # keeps running as the arm moves from point to point.
    for (u, v) in path[1:]:
        await _move_arm_to_pose(ctx, draw_pose(u, v, peak_theta))
        await asyncio.sleep(settings.get('dwell_s_per_point', 0.3))

    # 5. Tilt back down at the last point to stop the pour
    last_u, last_v = path[-1]
    await _ramp_theta(ctx, draw_pose(last_u, last_v, peak_theta), peak_theta, baseline_theta, settings['ramp_down_s'])

    # 6. Lift clear, then return the source and release
    lift_pose = {**draw_pose(last_u, last_v, baseline_theta), 'z': target_pose['z'] + pour_height + 20.0}
    await _move_arm_to_pose(ctx, lift_pose)
    await _move_arm_to_pose(ctx, approach)
    await _move_arm_to_pose(ctx, source_pose)
    await _release(ctx)

    return {'poured_letter': letter, 'on': target['object_id']}


IMPLEMENTED.update({'latte_art'})
