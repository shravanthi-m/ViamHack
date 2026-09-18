"""
Stage 1 sanity check: test ONLY the raw Viam gripper command on the
cup, with no arm movement, no shaking, nothing else. Confirms the
grip itself works before trusting shake()'s full flow to it.

Run this with the arm ALREADY positioned right at the cup (jog it there
manually first, or via the Viam app's CONTROL tab) -- this script does
not move the arm at all, it only closes the gripper.
"""
import asyncio

from viam.robot.client import RobotClient
from viam.components.gripper import Gripper

API_KEY = '<KEY>'
API_KEY_ID = '<KEY_ID>'
MACHINE_ADDRESS = 'armfarm1-main.XXXX.viam.cloud'
GRIPPER_NAME = 'gripper'


async def connect():
    opts = RobotClient.Options.with_api_key(api_key=API_KEY, api_key_id=API_KEY_ID)
    return await RobotClient.at_address(MACHINE_ADDRESS, opts)


async def main():
    robot = await connect()
    gripper = Gripper.from_robot(robot, GRIPPER_NAME)

    print('Closing gripper...')
    grabbed = await gripper.grab()
    print(f'grab() returned: {grabbed}')

    if grabbed is False:
        print('Gripper reported NO object grasped -- check positioning/alignment.')
    else:
        input('Grip looks OK? Press Enter to release...')

    await gripper.open()
    print('Released.')

    await robot.close()


if __name__ == '__main__':
    asyncio.run(main())
