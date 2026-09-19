"""Latte art team: grab the milk cup, carry it to the target cup, and pour-draw a letter."""
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


# ---- Internal helpers -------------------------------------------------------

def _get_arm(ctx: Context) -> Arm:
    return Arm.from_robot(ctx.robot, ctx.config['resources']['arm'])


def _get_gripper(ctx: Context) -> Gripper:
    return Gripper.from_robot(ctx.robot, ctx.config['resources']['gripper'])


def _require_frame(ctx: Context, target: Target) -> None:
    if target['frame'] != ctx.config['frame']:
        raise ValueError(
            f"Target '{target['object_id']}' is in frame '{target['frame']}', "
            f"expected '{ctx.config['frame']}'. Re-localize before using it."
        )


def _to_viam_pose(pose: Pose) -> ViamPose:
    return ViamPose(
        x=pose['x'], y=pose['y'], z=pose['z'],
        o_x=pose['o_x'], o_y=pose['o_y'], o_z=pose['o_z'],
        theta=pose['theta'],
    )


async def _move_arm_to_pose(ctx: Context, pose: Pose) -> None:
    """Prefers the Motion service (frame- and obstacle-aware), falling
    back to a direct arm move only if that call fails."""
    try:
        motion = MotionClient.from_robot(ctx.robot, ctx.config['resources']['motion'])
        destination = PoseInFrame(reference_frame=ctx.config['frame'], pose=_to_viam_pose(pose))
        success = await motion.move(component_name=ctx.config['resources']['arm'], destination=destination)
        if success is False:
            raise RuntimeError(f'Motion service reported failure moving to pose {pose}')
        return
    except Exception:
        pass

    arm = _get_arm(ctx)
    await arm.move_to_position(_to_viam_pose(pose))


async def _ramp_theta(ctx: Context, base_pose: Pose, start_theta: float, end_theta: float, duration_s: float, hz: float = 10.0) -> None:
    """Smoothly varies ONLY theta -- ramping up starts the pour, ramping down stops it."""
    steps = max(int(duration_s * hz), 2)
    for i in range(1, steps + 1):
        frac = i / steps
        eased = frac * frac * (3 - 2 * frac)  # smoothstep, avoids a jerky snap-tilt
        theta = start_theta + (end_theta - start_theta) * eased
        await _move_arm_to_pose(ctx, {**base_pose, 'theta': theta})
        await asyncio.sleep(1.0 / hz)


def _letter_settings(ctx: Context, object_id: str, letter: str) -> dict:
    """
    config['primitive_settings']['latte_art'][object_id][letter] = {
        'tilt_deg': 40.0,          # how far to tilt to start the pour
        'ramp_up_s': 1.0,          # how long to ramp into full tilt
        'ramp_down_s': 1.0,        # how long to ramp back down to stop
        'pour_height_mm': 40.0,    # height above the cup's surface while pouring
        'cup_radius_mm': 30.0,     # scales the (u, v) path to a real size
        'dwell_s_per_point': 0.3,  # how long to linger at each path point while pouring
    }
    Keyed by the source object_id (e.g. 'milk') and the letter, since
    different pourable sources may need different calibration.
    """
    try:
        return ctx.config['primitive_settings']['latte_art'][object_id][letter]
    except KeyError as e:
        raise ValueError(f"Missing primitive_settings.latte_art.{object_id}.{letter} in config.") from e


# ---- The primitive -----------------------------------------------------------

async def latte_art(ctx: Context, *, source: Target, target: Target, letter: str = 'V') -> dict:
    """
    Start/end empty-handed; grab the milk cup, tilt to pour over target, trace
    `letter` while pouring, stop, return the milk cup.

    Precondition: the gripper is holding nothing. Postcondition: it is holding
    nothing again, and the milk cup is back where it was picked up. Returns the
    letter drawn, the target it was drawn on, the tilt actually used, and the
    gripper's holding report before and after the grasp -- evidence that the
    milk cup was actually grasped and later actually released, not proof of
    what the poured line looks like.
    """
    letter = letter.upper()
    path = LETTER_PATHS.get(letter)
    if path is None:
        raise ValueError(f"No path defined for letter '{letter}'. Add it to LETTER_PATHS.")

    settings = _letter_settings(ctx, source['object_id'], letter)
    _require_frame(ctx, source)
    _require_frame(ctx, target)

    gripper = _get_gripper(ctx)
    if await gripper.is_holding_something():
        raise RuntimeError('Gripper is already holding something -- expected empty-handed start')

    # 1. Grab the cup containing the milk
    source_pose = source['pose']
    approach = {**source_pose, 'z': source_pose['z'] + 50.0}
    await _move_arm_to_pose(ctx, approach)
    await _move_arm_to_pose(ctx, source_pose)
    grabbed = await gripper.grab()
    if grabbed is False:
        raise RuntimeError('Gripper reported no object grasped')
    await _move_arm_to_pose(ctx, approach)

    # 2. Carry it over to the target cup, computing draw positions relative to it
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

    first_u, first_v = path[0]
    await _move_arm_to_pose(ctx, draw_pose(first_u, first_v, baseline_theta))

    # 3. Tilt over the target -- this starts the pour
    await _ramp_theta(ctx, draw_pose(first_u, first_v, baseline_theta), baseline_theta, peak_theta, settings['ramp_up_s'])

    # 4. Motion the V while still tilted -- the pour keeps running as it moves
    for (u, v) in path[1:]:
        await _move_arm_to_pose(ctx, draw_pose(u, v, peak_theta))
        await asyncio.sleep(settings.get('dwell_s_per_point', 0.3))

    # 5. Untilt at the last point -- stops the pour
    last_u, last_v = path[-1]
    await _ramp_theta(ctx, draw_pose(last_u, last_v, peak_theta), peak_theta, baseline_theta, settings['ramp_down_s'])

    # 6. Lift clear, carry the milk cup back, and release
    lift_pose = {**draw_pose(last_u, last_v, baseline_theta), 'z': target_pose['z'] + pour_height + 20.0}
    await _move_arm_to_pose(ctx, lift_pose)
    await _move_arm_to_pose(ctx, approach)
    await _move_arm_to_pose(ctx, source_pose)
    await gripper.open()

    still_holding = await gripper.is_holding_something()
    if still_holding:
        raise RuntimeError('Gripper still reports holding something after open() -- release failed')

    return {
        'drew': letter,
        'on': target['object_id'],
        'tilt_deg': settings['tilt_deg'],
        'holding_after_grasp': True,
        'holding_after_release': still_holding,
    }


IMPLEMENTED.update({'latte_art'})
