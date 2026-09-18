"""Shared connection only; importing this module never connects or loads the SDK."""
import asyncio
import os
from pathlib import Path


async def connect():
    from dotenv import load_dotenv
    from viam.robot.client import RobotClient

    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    address = os.environ.get('VIAM_MACHINE_ADDRESS')
    key, key_id = os.environ.get('VIAM_API_KEY'), os.environ.get('VIAM_API_KEY_ID')
    if not all((address, key, key_id)):
        raise ValueError('Set VIAM_MACHINE_ADDRESS, VIAM_API_KEY and VIAM_API_KEY_ID in .env')
    options = RobotClient.Options.with_api_key(api_key=key, api_key_id=key_id)
    return await asyncio.wait_for(RobotClient.at_address(address, options), 30)
