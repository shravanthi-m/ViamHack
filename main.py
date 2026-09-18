import asyncio
import os
import sys

from dotenv import load_dotenv
from viam.robot.client import RobotClient

from config import DRINKS
from pour import pour_cycle

# Credentials live in .env (see .env.example); real environment variables win.
load_dotenv()

MACHINE_ADDRESS = os.environ.get(
    "VIAM_MACHINE_ADDRESS", "armfarm7-main.310sld03v2.viam.cloud"
)


async def connect() -> RobotClient:
    api_key = os.environ.get("VIAM_API_KEY")
    api_key_id = os.environ.get("VIAM_API_KEY_ID")
    if not api_key or not api_key_id:
        raise SystemExit(
            "Set VIAM_API_KEY and VIAM_API_KEY_ID (see .env.example) before running."
        )

    options = RobotClient.Options.with_api_key(api_key=api_key, api_key_id=api_key_id)
    return await RobotClient.at_address(MACHINE_ADDRESS, options)

async def main(drink: str):
    robot = await connect()
    arm = ...  # Arm.from_robot(robot, "arm")
    await pour_cycle(arm, DRINKS[drink])
    await robot.close()

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
