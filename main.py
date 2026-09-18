import asyncio, sys
from viam.robot.client import RobotClient
from config import DRINKS
from pour import pour_cycle

async def connect():
    # reuse your existing connect logic from helpers.py
    pass

async def main(drink: str):
    robot = await connect()
    arm = ...  # Arm.from_robot(robot, "arm")
    await pour_cycle(arm, DRINKS[drink])
    await robot.close()

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))