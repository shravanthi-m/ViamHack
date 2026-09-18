"""
Smooth shake test. Same assumptions as test_shake_raw.py -- gripper
already holding the object, no Target/localize/config -- but fixes
the jerky stop-start motion by:

  1. Sampling a continuous sine wave for z instead of 4 discrete
     stop-points (up/mid/down/mid) per cycle. Many small waypoints
     read as smooth motion; a few big ones read as separate stops.
  2. NOT printing position after every single move. Each print is an
     extra network round-trip on top of the move itself -- that
     doubled latency is most of why it felt like it was pausing.
  3. A realistic shake frequency. 20 cycles/second is far faster than
     a networked move command can keep up with; start around 1-2 Hz
     and only increase once this actually looks smooth.
"""
import asyncio
import math
import os
from pathlib import Path

from dotenv import load_dotenv
from viam.components.arm import Arm
from viam.proto.common import Pose
from viam.robot.client import RobotClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.environ.get("VIAM_API_KEY")
API_KEY_ID = os.environ.get("VIAM_API_KEY_ID")
MACHINE_ADDRESS = os.environ.get("VIAM_MACHINE_ADDRESS")
ARM_NAME = 'arm'

AMPLITUDE_MM = 15.0    # how far up/down each stroke travels
SHAKE_HZ = 30        # actual shake cycles per second -- keep modest at first
SAMPLE_RATE_HZ = 20.0  # how many waypoints per second we send -- this is what controls smoothness, not SHAKE_HZ
DURATION_S = 6.0


async def connect():
    if not API_KEY or not API_KEY_ID or not MACHINE_ADDRESS:
        raise SystemExit(
            "Set VIAM_API_KEY, VIAM_API_KEY_ID, and VIAM_MACHINE_ADDRESS in .env."
        )

    opts = RobotClient.Options.with_api_key(api_key=API_KEY, api_key_id=API_KEY_ID)
    return await RobotClient.at_address(MACHINE_ADDRESS, opts)


async def main():
    robot = await connect()
    arm = Arm.from_robot(robot, ARM_NAME)

    start = await arm.get_end_position()
    print(f'Starting position: x={start.x:.1f} y={start.y:.1f} z={start.z:.1f} theta={start.theta:.1f}')
    print('Shaking...')

    dt = 1.0 / SAMPLE_RATE_HZ
    steps = int(DURATION_S / dt)

    for i in range(steps):
        t = i * dt
        z_offset = AMPLITUDE_MM * math.sin(2 * math.pi * SHAKE_HZ * t)
        pose = Pose(
            x=start.x, y=start.y, z=start.z + z_offset,
            o_x=start.o_x, o_y=start.o_y, o_z=start.o_z, theta=start.theta,
        )
        await arm.move_to_position(pose)
        # deliberately no per-step print/query here -- that round-trip is what caused the stutter

    print('Returning to start position...')
    await arm.move_to_position(start)

    final = await arm.get_end_position()
    print(f'Final position: x={final.x:.1f} y={final.y:.1f} z={final.z:.1f}')

    await robot.close()


if __name__ == '__main__':
    asyncio.run(main())
