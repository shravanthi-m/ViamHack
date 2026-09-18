"""
Standalone test for latte_art(), bypassing the runtime entirely.

localize isn't implemented yet, so latte_art can't run through a real
plan (source/target need $refs from localize). This calls latte_art()
directly instead, with hand-built Targets standing in for what
localize would eventually return.

Run from the repo root, inside your activated .venv:
    python test_latte_art_only.py
"""
import asyncio
import json

from viam.robot.client import RobotClient

from primitives.types import Context
from primitives.latte_art import latte_art

API_KEY = 'mkqtlf6zzzq5dn3kbb0fcywoz4dgejvt'
API_KEY_ID = '6026240d-6390-44f2-b7fc-8298407fcc9a'
MACHINE_ADDRESS = 'armfarm7-main.310sld03v2.viam.cloud'  # e.g. armfarm7-main.XXXX.viam.cloud


async def connect():
    opts = RobotClient.Options.with_api_key(api_key=API_KEY, api_key_id=API_KEY_ID)
    return await RobotClient.at_address(MACHINE_ADDRESS, opts)


async def main():
    # Load the real config -- same file the runtime itself would use,
    # so this test isn't running against different settings than a
    # real plan eventually would.
    with open('config/local.json') as f:
        config = json.load(f)

    robot = await connect()
    ctx = Context(config=config, robot=robot)

    # Stand-ins for what localize(ctx, object_id='milk') / ('cup')
    # would return once perception's real implementation exists.
    # MEASURE these for real -- jog the arm to each spot and read its
    # actual end position, don't trust these numbers.
    milk_target = {
        'object_id': 'milk',
        'frame': config['frame'],
        'pose': {
            'x': 150.0, 'y': -100.0, 'z': 40.0,
            'o_x': 0.0, 'o_y': 0.0, 'o_z': -1.0, 'theta': 0.0,
        },
    }
    cup_target = {
        'object_id': 'cup',
        'frame': config['frame'],
        'pose': {
            'x': 300.0, 'y': 0.0, 'z': 50.0,
            'o_x': 0.0, 'o_y': 0.0, 'o_z': -1.0, 'theta': 0.0,
        },
    }

    result = await latte_art(ctx, source=milk_target, target=cup_target, letter='V')
    print(result)

    await robot.close()


if __name__ == '__main__':
    asyncio.run(main())
