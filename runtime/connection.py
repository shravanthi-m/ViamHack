"""Shared connection only; importing this module never connects or loads the SDK."""
import asyncio
import os
from pathlib import Path


async def connect(*, managed_reconnect=True):
    from dotenv import load_dotenv
    from viam.robot.client import RobotClient
    # RobotClient builds its resource manager while connecting, and only creates
    # clients for resource types whose class is already imported. Primitives import
    # their SDK clients lazily, so anything imported after this call is missing from
    # the manager and from_robot raises ResourceNotFoundError. Import every resource
    # type the task config can name, before connecting.
    from viam.components.arm import Arm  # noqa: F401
    from viam.components.camera import Camera  # noqa: F401
    from viam.components.gripper import Gripper  # noqa: F401
    from viam.services.motion import MotionClient  # noqa: F401

    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    address = os.environ.get('VIAM_MACHINE_ADDRESS')
    key, key_id = os.environ.get('VIAM_API_KEY'), os.environ.get('VIAM_API_KEY_ID')
    if not all((address, key, key_id)):
        raise ValueError('Set VIAM_MACHINE_ADDRESS, VIAM_API_KEY and VIAM_API_KEY_ID in .env')
    options = RobotClient.Options.with_api_key(api_key=key, api_key_id=key_id)
    # Optional diagnostic bypass for Rust/WebRTC proxy channel errors.
    options.dial_options.disable_webrtc = os.environ.get('VIAM_DISABLE_WEBRTC') == '1'
    if not managed_reconnect:
        # Teaching owns retries. SDK 0.80's background reconnect loop calls
        # sys.exit() on exhaustion, which would terminate the recording process.
        options.check_connection_interval = 0
        options.attempt_reconnect_interval = 0
    return await asyncio.wait_for(RobotClient.at_address(address, options), 30)
