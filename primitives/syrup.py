"""Syrup team: squeeze and drizzle each fully encapsulate grasp, action, return, release."""
import asyncio

from .types import Context, Target
from .manipulation_common import (
    require_frame, move_arm_to_pose, grasp_or_raise, release,
    circular_xy_waypoints, get_gripper,
)

IMPLEMENTED = set()


def _syrup_settings(ctx: Context, object_id: str, primitive: str) -> dict:
    """
    Calibrated dispense parameters live in config, not the planner --
    same philosophy as pouring.py's _pour_settings. Expected shape:

        config['primitive_settings']['syrup']['caramel']['squeeze'] = {
            'pulses': 3, 'pulse_s': 0.3, 'rest_s': 0.2,
            'hover_height_mm': 60.0,
        }
        config['primitive_settings']['syrup']['caramel']['drizzle'] = {
            'radius_mm': 20.0, 'loops': 2, 'points_per_loop': 16,
            'pulses_per_point': 1, 'pulse_s': 0.15, 'rest_s': 0.1,
            'hover_height_mm': 60.0,
        }

    Keyed by source['object_id'] so different syrups (caramel,
    chocolate, ketchup...) can carry different calibrated settings.
    """
    try:
        return ctx.config['primitive_settings']['syrup'][object_id][primitive]
    except KeyError as e:
        raise ValueError(
            f"Missing primitive_settings.syrup.{object_id}.{primitive} in config."
        ) from e


async def _pick_up(ctx: Context, source: Target) -> dict:
    require_frame(ctx, source)
    pose = source['pose']
    approach = {**pose, 'z': pose['z'] + 50.0}
    await move_arm_to_pose(ctx, approach)
    await move_arm_to_pose(ctx, pose)
    await grasp_or_raise(ctx)
    await move_arm_to_pose(ctx, approach)
    return approach


async def _put_back(ctx: Context, source: Target, approach: dict) -> None:
    pose = source['pose']
    await move_arm_to_pose(ctx, approach)
    await move_arm_to_pose(ctx, pose)
    await release(ctx)


async def _squeeze_pulses(ctx: Context, pulses: int, pulse_s: float, rest_s: float) -> None:
    """
    HARDWARE CAVEAT: Viam's generic Gripper API only exposes
    grab()/open()/stop()/is_moving() -- there's no standard "close by
    X%". Toggling grab() on an already-held bottle risks dropping it
    entirely, depending on your gripper's behavior. Tries do_command
    first in case your gripper model exposes finer control; falls back
    to grab() otherwise. Test on an EMPTY bottle at low pulse counts
    before trusting this with real syrup over a live cup.
    """
    gripper = get_gripper(ctx)
    for _ in range(pulses):
        try:
            await gripper.do_command({'squeeze': True})  # ADAPT: real command name for your gripper module
        except Exception:
            await gripper.grab()
        await asyncio.sleep(pulse_s)
        await asyncio.sleep(rest_s)


async def squeeze(ctx: Context, *, source: Target, target: Target) -> dict:
    """Start/end empty-handed; squeeze source over target, dispensing a stationary shot."""
    settings = _syrup_settings(ctx, source['object_id'], 'squeeze')
    require_frame(ctx, target)

    approach = await _pick_up(ctx, source)

    hover_pose = {**target['pose'], 'z': target['pose']['z'] + settings.get('hover_height_mm', 60.0)}
    await move_arm_to_pose(ctx, hover_pose)

    await _squeeze_pulses(ctx, settings['pulses'], settings['pulse_s'], settings['rest_s'])

    await _put_back(ctx, source, approach)

    return {'squeezed': source['object_id'], 'into': target['object_id'], 'pulses': settings['pulses']}


async def drizzle(ctx: Context, *, source: Target, target: Target) -> dict:
    """Start/end empty-handed; trace circular pass(es) over target while squeezing -- the caramel-drizzle spiral."""
    settings = _syrup_settings(ctx, source['object_id'], 'drizzle')
    require_frame(ctx, target)

    approach = await _pick_up(ctx, source)

    drizzle_pose = {**target['pose'], 'z': target['pose']['z'] + settings.get('hover_height_mm', 60.0)}

    for _ in range(settings['loops']):
        waypoints = circular_xy_waypoints(drizzle_pose, settings['radius_mm'], settings['points_per_loop'])
        for wp in waypoints:
            await move_arm_to_pose(ctx, wp)
            await _squeeze_pulses(ctx, settings['pulses_per_point'], settings['pulse_s'], settings['rest_s'])

    await _put_back(ctx, source, approach)

    return {
        'drizzled': source['object_id'],
        'over': target['object_id'],
        'loops': settings['loops'],
        'radius_mm': settings['radius_mm'],
    }


IMPLEMENTED.update({'squeeze', 'drizzle'})
