"""Home the arm, then move it to a pose offset from that home.

    python main.py                          # read the pose, print both steps, move nothing
    python main.py --teach-origin --execute # one time: measure the origin pose
    python main.py --execute                # home, then jog +20/+20, after confirming
    python main.py --dx 20 --dy 20 --yaw 0 --speed 10 --execute
    python main.py --shake 5                # print the three shake steps, move nothing
    python main.py --shake 5 --execute      # home, lift clear of the table, shake for 5 s

Every run starts at the taught origin, so a commanded pose is always approached
from one known state instead of wherever the arm was left. Both steps go through
the Viam motion service, so both are planned around the machine's configured
collision geometry.

The origin is a pose, and a pose cannot be computed from the taught joints without
moving there, so --teach-origin visits those joints once, measures the pose, and
writes it to config/local.json. That single joint move is not collision-planned;
everything after it is.

Both steps are the primitives in primitives/motion.py; this script only orders
them and reports what they observed. All Viam calls live inside those primitives.
Offsets are measured from the pose the arm reaches after homing.

--shake replaces the jog with a mid-air shake: home, lift straight up to a position
clear of the table, then run the shake primitive there. Mid-air is measured from the
taught origin rather than the middle of the calibrated box, because the origin is a
pose the arm is known to reach, taught at the side-grip orientation shake needs to
level; only its height changes. The box that shake will sweep is checked against the
calibrated workspace before anything moves, so a lift that does not fit is refused in
the dry run. shake is hold-to-hold: it starts and finishes holding the object and
never grasps or releases, so the gripper must already hold whatever is being shaken.
Use --allow-empty to shake an empty gripper, which turns that check off.

The agent demo path (python -m runtime) is unchanged and remains the entry point
for plans.
"""
import argparse
import asyncio
import json
import math
import os
from pathlib import Path

from dotenv import load_dotenv

from primitives import motion, shake
from primitives.types import Context

ROOT = Path(__file__).resolve().parent
LOCAL_CONFIG = ROOT / 'config/local.json'
DEFAULT_ADDRESS = 'armfarm7-main.310sld03v2.viam.cloud'
MAX_STEP_MM = 50.0        # This is a jog, not a transport move: refuse more.
DEFAULT_LIFT_MM = 150.0   # Clear of the table, still well inside the taught reach.
MAX_LIFT_MM = 400.0       # The workspace bounds the lift too; this bounds a typo.


def parser():
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument('--dx', type=float, default=20.0, help='Offset from home in mm (default: 20)')
    cli.add_argument('--dy', type=float, default=20.0, help='Offset from home in mm (default: 20)')
    cli.add_argument('--dz', type=float, default=0.0, help='Offset from home in mm (default: 0)')
    cli.add_argument('--yaw', type=float, default=0.0,
                     help='Yaw in degrees at the target; the tool points down (default: 0)')
    cli.add_argument('--speed', type=float,
                     help='Cap arm speed in deg/s for every move (UFactory set_speed)')
    cli.add_argument('--config', default=str(LOCAL_CONFIG if LOCAL_CONFIG.exists()
                                             else ROOT / 'config/demo.json'))
    cli.add_argument('--teach-origin', action='store_true',
                     help=f'Measure the origin pose and save it to {LOCAL_CONFIG.name}')
    cli.add_argument('--shake', type=float, metavar='SECONDS',
                     help='Instead of the jog: lift to a mid-air position above the origin '
                          'and shake there for this many seconds')
    cli.add_argument('--lift', type=float, default=DEFAULT_LIFT_MM, metavar='MM',
                     help=f'Height of that mid-air position above the origin '
                          f'(default: {DEFAULT_LIFT_MM:g})')
    cli.add_argument('--allow-empty', action='store_true',
                     help='Shake with nothing in the gripper: turns off the holding check')
    cli.add_argument('--execute', action='store_true', help='Actually move the arm')
    cli.add_argument('--address', default=os.environ.get('VIAM_MACHINE_ADDRESS', DEFAULT_ADDRESS))
    return cli


def summary(pose):
    return (f'x={pose["x"]:8.2f} y={pose["y"]:8.2f} z={pose["z"]:8.2f}  '
            f'ov=({pose["o_x"]:.3f}, {pose["o_y"]:.3f}, {pose["o_z"]:.3f}) '
            f'theta={pose["theta"]:.2f}')


async def connect(address):
    from viam.robot.client import RobotClient
    # Import before connecting: RobotClient only creates clients for resource types
    # whose class exists when it builds its manager, and motion.py imports its SDK
    # clients lazily. See the same note in runtime/connection.py.
    from viam.components.arm import Arm  # noqa: F401
    from viam.components.camera import Camera  # noqa: F401
    from viam.components.gripper import Gripper  # noqa: F401
    from viam.services.motion import MotionClient  # noqa: F401

    load_dotenv(ROOT / '.env')
    key, key_id = os.environ.get('VIAM_API_KEY'), os.environ.get('VIAM_API_KEY_ID')
    if not key or not key_id:
        raise SystemExit('Set VIAM_API_KEY and VIAM_API_KEY_ID in .env (see .env.example)')
    options = RobotClient.Options.with_api_key(api_key=key, api_key_id=key_id)
    return await asyncio.wait_for(RobotClient.at_address(address, options), 30)


def task_config(args):
    config = json.loads(Path(args.config).read_text())
    settings = config.setdefault('primitive_settings', {})
    if args.speed is not None:
        settings.setdefault('motion', {})['speed_deg_s'] = args.speed
    if args.allow_empty:
        # Deliberate and per-run: the shake config on disk keeps requiring a held object.
        settings.setdefault('shake', {})['require_holding'] = False
    motion.settings(config)  # Fail on bad tuning before connecting to anything.
    if args.shake is not None:
        shake.settings(config)
    return config


def save_origin(config, pose):
    """Write the measured origin into the local config, which stays out of Git."""
    stored = json.loads(LOCAL_CONFIG.read_text()) if LOCAL_CONFIG.exists() else config
    stored.setdefault('primitive_settings', {}).setdefault('motion', {})['origin_pose'] = pose
    motion.settings(stored)  # Never write a config the primitive would reject.
    LOCAL_CONFIG.write_text(json.dumps(stored, indent=2, allow_nan=False) + '\n')
    return LOCAL_CONFIG


async def confirm(prompt):
    print(f'\n{prompt}')
    print('Confirm the workspace is clear and manual/free-drive mode is OFF.')
    if (await asyncio.to_thread(input, 'Type go to move the arm: ')).strip().lower() != 'go':
        print('Aborted; nothing moved.')
        return False
    return True


async def teach(args, ctx):
    print(f'step 1  teach_origin: joints -> '
          f'{[round(v, 2) for v in motion.settings(ctx.config)["origin_joints_deg"]]}')
    if not args.execute:
        print('\nDry run: nothing moved. Add --execute to measure the origin.')
        return
    if not await confirm('This joint move is NOT collision-planned: the path from here to '
                         'the origin must be clear.'):
        return
    taught = await motion.teach_origin(ctx)
    print(f'origin joints: {[round(v, 2) for v in taught["joints_deg"]]} '
          f'(max joint error {taught["max_joint_error_deg"]:.2f} deg)')
    print(f'origin pose:   {summary(taught["origin_pose"])}')
    print(f'saved to {save_origin(ctx.config, taught["origin_pose"])}')
    print('Later runs plan to this pose through the motion service.')


def midair(config, home, lift_mm):
    """The mid-air pose `lift_mm` above `home`, with the box shake will sweep checked.

    Only the height changes, so the pose keeps the orientation the origin was taught
    at. The stroke's box is confirmed inside the calibrated workspace here, before
    anything moves, rather than after the arm is already up there; shake checks it
    again itself once the tool is levelled.
    """
    if not config['calibrated']:
        raise SystemExit('A mid-air shake needs a calibrated workspace. Run: '
                         'python -m runtime --config config/local.json calibrate --write')
    centre = {**home, 'z': home['z'] + lift_mm}
    tuning = shake.settings(config)
    try:
        shake.within_workspace({axis: centre[axis] for axis in ('x', 'y', 'z')},
                               config, 'mid-air position')
        ends = shake.swept_box(shake.level(centre), tuning, config)
    except ValueError as refused:
        raise SystemExit(f'{refused}\nLower --lift, or re-teach the origin.') from refused
    return centre, tuning, ends


async def home_and_shake(args, ctx):
    config, tuning, timeout = motion.handles(ctx, 'shake')
    origin = tuning['origin_pose']
    if origin is None:
        raise SystemExit('No taught origin pose yet. Run: '
                         'python main.py --teach-origin --execute')
    centre, shaking, ends = midair(config, origin, args.lift)
    print(f'step 1  go_to_origin: {summary(origin)}')
    print(f'step 2  lift to mid-air: {args.lift:+.1f} mm above home, {summary(centre)}')
    print(f'step 3  shake:        {args.shake:.1f} s of {shaking["stroke_mm"]:.1f} mm strokes '
          f'at {shaking["frequency_hz"]:.2f} Hz, sweeping z '
          f'{ends["bottom"]["z"]:.1f} to {ends["top"]["z"]:.1f} mm')
    print('        shake levels the tool first, then holds on throughout: it never '
          'grasps or releases.')
    if not shaking['require_holding']:
        print('        --allow-empty: the holding check is off, so nothing is verified held.')
    if not args.execute:
        print('\nDry run: nothing moved. Add --execute to run all three steps.')
        return
    if not await confirm('All three steps are planned by the Viam motion service.'):
        return
    homed = await motion.go_to_origin(ctx)
    print(f'homed:   {summary(homed["pose"])}')
    print(f'         arrival error {homed["position_error_mm"]:.2f} mm, '
          f'{homed["orientation_error_deg"]:.2f} deg')
    # Lift from where the arm actually stopped, so the box checked is the box swept.
    centre, _, _ = midair(config, homed['pose'], args.lift)
    lifted = await motion.plan_to(ctx, centre, tuning, timeout, 'lift to mid-air')
    print(f'mid-air: {summary(lifted["pose"])}')
    print(f'         arrival error {lifted["position_error_mm"]:.2f} mm, '
          f'{lifted["orientation_error_deg"]:.2f} deg')
    shaken = await shake.shake(ctx, duration_s=args.shake)
    print(f'shaken:  {shaken["strokes"]} strokes in {shaken["duration_s"]:.1f} s, '
          f'{shaken["frequency_hz"]:.2f} Hz achieved against '
          f'{shaken["requested_frequency_hz"]:.2f} Hz asked for')
    print(f'         endpoint z range {shaken["swept_z_mm"][0]:.1f} '
          f'to {shaken["swept_z_mm"][1]:.1f} mm; '
          'intermediate joint strokes do not report tool-pose error')
    print(f'         holding before {shaken["holding_before"]}, '
          f'after {shaken["holding_after"]}')
    print(f'settled: {summary(shaken["pose"])}')
    print('Done.')


async def home_and_jog(args, ctx):
    tuning = motion.settings(ctx.config)
    origin = tuning['origin_pose']
    if origin is None:
        raise SystemExit('No taught origin pose yet. Run: '
                         'python main.py --teach-origin --execute')
    print(f'step 1  go_to_origin: {summary(origin)}')
    print(f'step 2  go_to_pose:   home + ({args.dx:+.1f}, {args.dy:+.1f}, {args.dz:+.1f}) mm, '
          f'yaw {args.yaw:.1f} deg, tool pointing down')
    if not args.execute:
        print('\nDry run: nothing moved. Add --execute to run both steps.')
        return
    if not await confirm('Both steps are planned by the Viam motion service.'):
        return
    homed = await motion.go_to_origin(ctx)
    print(f'homed:   {summary(homed["pose"])}')
    print(f'         arrival error {homed["position_error_mm"]:.2f} mm, '
          f'{homed["orientation_error_deg"]:.2f} deg')
    home = homed['pose']
    target = {'x': home['x'] + args.dx, 'y': home['y'] + args.dy,
              'z': home['z'] + args.dz, 'yaw': args.yaw}
    print(f'target:  x={target["x"]:8.2f} y={target["y"]:8.2f} z={target["z"]:8.2f} '
          f'yaw={target["yaw"]:.2f}')
    reached = await motion.go_to_pose(ctx, pose=target)
    print(f'reached: {summary(reached["pose"])}')
    print(f'         arrival error {reached["position_error_mm"]:.2f} mm, '
          f'{reached["orientation_error_deg"]:.2f} deg')
    print('Done.')


def step(args):
    if args.teach_origin:
        return teach
    return home_and_shake if args.shake is not None else home_and_jog


async def run(args, config):
    robot = await connect(args.address)
    ctx = Context(config, robot)
    try:
        print(f'config: {args.config}')
        print(f'tool now in {config["frame"]}: {summary(await motion.tool_pose(ctx))}')
        try:
            await step(args)(args, ctx)
        except BaseException:
            # Halt motion only; never auto-open a gripper that may be holding something.
            await asyncio.wait_for(robot.stop_all(), 5)
            raise
    finally:
        await asyncio.wait_for(robot.close(), 10)


def main():
    args = parser().parse_args()
    if args.teach_origin and args.shake is not None:
        raise SystemExit('--teach-origin only measures the origin; run --shake on its own')
    if args.allow_empty and args.shake is None:
        raise SystemExit('--allow-empty only applies to --shake')
    for axis in ('dx', 'dy', 'dz'):
        offset = getattr(args, axis)
        if not math.isfinite(offset) or abs(offset) > MAX_STEP_MM:
            raise SystemExit(f'--{axis} must be within +/-{MAX_STEP_MM:g} mm for a jog')
    if not math.isfinite(args.lift) or not 0 <= args.lift <= MAX_LIFT_MM:
        raise SystemExit(f'--lift must be 0 to {MAX_LIFT_MM:g} mm above the origin')
    config = task_config(args)
    if args.shake is not None:
        ceiling = config['limits']['max_stir_duration_s']
        if not math.isfinite(args.shake) or not 0 < args.shake <= ceiling:
            raise SystemExit(f'--shake must be a duration in (0, {ceiling:g}] seconds, '
                             'the configured limits.max_stir_duration_s')
    asyncio.run(run(args, config))


if __name__ == '__main__':
    main()
