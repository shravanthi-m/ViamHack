"""
Bare-minimum shake test. Assumes the gripper is ALREADY holding the
object (you grip it manually / beforehand, outside this script). No
Target, no localize, no grip, no config lookups -- just: read wherever
the arm currently is, and move it up and down from there.

This is deliberately the simplest possible version, to isolate WHY
nothing moved before. Prints the pose before and after every single
move, so if the arm silently doesn't move, you'll see the printed
position stay identical instead of just guessing.
"""
import asyncio

from viam.robot.client import RobotClient
from viam.components.arm import Arm
from viam.proto.common import Pose

API_KEY = 'mkqtlf6zzzq5dn3kbb0fcywoz4dgejvt'
API_KEY_ID = '6026240d-6390-44f2-b7fc-8298407fcc9a'
MACHINE_ADDRESS = 'armfarm7-main.XXXX.viam.cloud'
ARM_NAME = 'arm'

AMPLITUDE_MM = 15.0   # how far up/down each stroke travels
HZ = 1.0              # cycles per second -- start SLOW so you can watch it
DURATION_S = 6.0


async def connect():
    opts = RobotClient.Options.with_api_key(api_key=API_KEY, api_key_id=API_KEY_ID)
    return await RobotClient.at_address(MACHINE_ADDRESS, opts)


async def move_and_report(arm: Arm, pose: Pose, label: str):
    print(f'  -> commanding move: {label} (target z={pose.z:.1f})')
    await arm.move_to_position(pose)
    current = await arm.get_end_position()
    print(f'  -> actual position after move: x={current.x:.1f} y={current.y:.1f} z={current.z:.1f}')


async def main():
    robot = await connect()
    arm = Arm.from_robot(robot, ARM_NAME)

    start = await arm.get_end_position()
    print(f'Starting position: x={start.x:.1f} y={start.y:.1f} z={start.z:.1f} theta={start.theta:.1f}')

    step_s = 1.0 / (HZ * 4)
    elapsed = 0.0
    while elapsed < DURATION_S:
        for dz, label in ((AMPLITUDE_MM, 'up'), (0.0, 'mid'), (-AMPLITUDE_MM, 'down'), (0.0, 'mid')):
            pose = Pose(
                x=start.x, y=start.y, z=start.z + dz,
                o_x=start.o_x, o_y=start.o_y, o_z=start.o_z, theta=start.theta,
            )
            await move_and_report(arm, pose, label)
            await asyncio.sleep(step_s)
            elapsed += step_s
            if elapsed >= DURATION_S:
                break

    print('Returning to start position...')
    await move_and_report(arm, start, 'start')

    await robot.close()


if __name__ == '__main__':
    asyncio.run(main())
