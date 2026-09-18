"""Home the arm, then move it to a pose offset from that home.

    python main.py                          # read the pose, print both steps, move nothing
    python main.py --teach-origin --execute # one time: measure the origin pose
    python main.py --execute                # home, then jog +20/+20, after confirming
    python main.py --dx 20 --dy 20 --yaw 0 --speed 10 --execute

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

from primitives import motion
from primitives.types import Context

ROOT = Path(__file__).resolve().parent
LOCAL_CONFIG = ROOT / 'config/local.json'
DEFAULT_ADDRESS = 'armfarm7-main.310sld03v2.viam.cloud'
MAX_STEP_MM = 50.0        # This is a jog, not a transport move: refuse more.


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
    if args.speed is not None:
        config.setdefault('primitive_settings', {}).setdefault('motion', {})['speed_deg_s'] = args.speed
    motion.settings(config)  # Fail on bad tuning before connecting to anything.
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


async def run(args, config):
    robot = await connect(args.address)
    ctx = Context(config, robot)
    try:
        print(f'config: {args.config}')
        print(f'tool now in {config["frame"]}: {summary(await motion.tool_pose(ctx))}')
        try:
            await (teach if args.teach_origin else home_and_jog)(args, ctx)
        except BaseException:
            # Halt motion only; never auto-open a gripper that may be holding something.
            await asyncio.wait_for(robot.stop_all(), 5)
            raise
    finally:
        await asyncio.wait_for(robot.close(), 10)


def main():
    args = parser().parse_args()
    for axis in ('dx', 'dy', 'dz'):
        step = getattr(args, axis)
        if not math.isfinite(step) or abs(step) > MAX_STEP_MM:
            raise SystemExit(f'--{axis} must be within +/-{MAX_STEP_MM:g} mm for a jog')
    asyncio.run(run(args, task_config(args)))


if __name__ == '__main__':
    main()
