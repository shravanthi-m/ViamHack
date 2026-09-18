"""Connect to the armfarm7 machine and poll each configured resource."""

import asyncio
import os

from dotenv import load_dotenv
from viam.components.arm import Arm
from viam.components.camera import Camera
from viam.components.gripper import Gripper
from viam.robot.client import RobotClient

# Credentials live in .env (see .env.example); real environment variables win.
load_dotenv()

MACHINE_ADDRESS = os.environ.get(
    "VIAM_MACHINE_ADDRESS", "armfarm7-main.310sld03v2.viam.cloud"
)

# Resources configured on the machine that behave like grippers (the arm's
# gripper plus the static fixtures the sample code reports the same way).
GRIPPER_LIKE = ["gripper", "table", "wall-front", "wall-side", "ceiling"]


async def connect() -> RobotClient:
    api_key = os.environ.get("VIAM_API_KEY")
    api_key_id = os.environ.get("VIAM_API_KEY_ID")
    if not api_key or not api_key_id:
        raise SystemExit(
            "Set VIAM_API_KEY and VIAM_API_KEY_ID (see .env.example) before running."
        )

    opts = RobotClient.Options.with_api_key(api_key=api_key, api_key_id=api_key_id)
    return await RobotClient.at_address(MACHINE_ADDRESS, opts)


async def main():
    async with await connect() as machine:
        print("Resources:")
        for name in machine.resource_names:
            print(f"  {name}")

        arm = Arm.from_robot(machine, "arm")
        print(f"\narm get_end_position: {await arm.get_end_position()}")

        cam = Camera.from_robot(machine, "cam")
        images, metadata = await cam.get_images()
        print(f"cam get_images: {len(images)} image(s), metadata={metadata}")

        for name in GRIPPER_LIKE:
            resource = Gripper.from_robot(machine, name)
            print(f"{name} is_moving: {await resource.is_moving()}")


if __name__ == "__main__":
    asyncio.run(main())
