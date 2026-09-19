"""
Standalone test for shake(), run from the REPO ROOT (not from inside
primitives/), so the package's own relative imports (gripper, motion)
resolve correctly.

shake() is a hold-to-hold primitive -- it never grasps or releases.
So before running this, the gripper must ALREADY be holding something,
either by jogging/gripping manually via the Viam app's CONTROL tab, or
by this script grabbing something first (see GRAB_BEFORE_SHAKE below).

Run from the repo root, inside your activated .venv:
    python test_shake_only.py
"""
import asyncio
import json
import os

from dotenv import load_dotenv
from viam.robot.client import RobotClient
from viam.components.gripper import Gripper

from primitives.types import Context
from primitives.shake import shake

load_dotenv()

API_KEY = os.environ['rqnbsgerh8isz68uqtwzt82syp4jr9tn']
API_KEY_ID = os.environ['6d8d00be-f621-4fc5-9c56-8772a39f4f07']
MACHINE_ADDRESS = os.environ['armfarm7-main.310sld03v2.viam.cloud']

# If True, this script grabs whatever is at the gripper's current
# position before shaking. If False, YOU must have already gripped
# something manually (Viam app CONTROL tab) before running this.
GRAB_BEFORE_SHAKE = False


async def connect():
    opts = RobotClient.Options.with_api_key(api_key=API_KEY, api_key_id=API_KEY_ID)
    return await RobotClient.at_address(MACHINE_ADDRESS, opts)


async def main():
    with open('config/local.json') as f:
        config = json.load(f)

    robot = await connect()
    ctx = Context(config=config, robot=robot)

    if GRAB_BEFORE_SHAKE:
        gripper = Gripper.from_robot(robot, config['resources']['gripper'])
        grabbed = await gripper.grab()
        if grabbed is False:
            print('Gripper reported nothing grasped -- aborting before shake.')
            await robot.close()
            return

    result = await shake(ctx, duration_s=5.0)
    print(result)

    await robot.close()


if __name__ == '__main__':
    asyncio.run(main())
