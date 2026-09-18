"""Thin Viam adapter. No IK, trajectory generator, or gripper driver here."""
import asyncio
import math
from dataclasses import asdict

from google.protobuf.json_format import MessageToDict, ParseDict
from viam.components.arm import Arm
from viam.components.camera import Camera
from viam.components.gripper import Gripper
from viam.proto.common import Pose, PoseInFrame, WorldState
from viam.proto.service.motion import Constraints, LinearConstraint
from viam.services.motion import MotionClient


def message(value):
    return MessageToDict(value, preserving_proto_field_name=True,
                         always_print_fields_with_no_presence=True)


class ViamIO:
    def __init__(self, robot, config):
        self.robot, self.config = robot, config
        names = config['resources']
        self.arm = Arm.from_robot(robot, names['arm'])
        self.gripper = Gripper.from_robot(robot, names['gripper'])
        self.camera = Camera.from_robot(robot, names['camera'])
        self.motion = MotionClient.from_robot(robot, names['motion'])
        self.tool = names['gripper']
        self.timeout = config['limits']['command_timeout_s']

    async def pose(self):
        return message(await self.motion.get_pose(
            self.tool, self.config['frame'], timeout=self.timeout))['pose']

    async def holding(self):
        return asdict(await self.gripper.is_holding_something(timeout=self.timeout))

    async def capabilities(self):
        info = {}
        for name, call in (
            ('camera_properties', lambda: self.camera.get_properties(timeout=5)),
            ('gripper_torque', lambda: self.gripper.do_command({'get_gripper_torque': True}, timeout=5)),
        ):
            try:
                value = await asyncio.wait_for(call(), 6)
                info[name] = message(value) if hasattr(value, 'DESCRIPTOR') else value
            except Exception as exc:
                info[name] = {'unavailable': f'{type(exc).__name__}: {exc}'}
        return info

    async def capture(self, folder):
        folder.mkdir(parents=True, exist_ok=False)
        pose = await self.pose()
        joints = message(await self.arm.get_joint_positions(timeout=self.timeout))
        images, metadata = await self.camera.get_images(timeout=self.timeout)
        if not images:
            raise RuntimeError('Camera returned no images')
        streams = []
        for index, image in enumerate(images):
            mime = str(image.mime_type)
            suffix = {'image/jpeg': '.jpg', 'image/png': '.png',
                      'image/vnd.viam.dep': '.dep'}.get(mime, '.bin')
            filename = f'image-{index}{suffix}'
            (folder / filename).write_bytes(image.data)
            streams.append(dict(name=image.name, mime=mime, file=filename,
                                width=image.width, height=image.height))
        try:
            holding = await self.holding()
        except Exception as exc:
            holding = {'unavailable': str(exc)}
        return dict(pose=pose, joints=joints, images=streams,
                    camera_metadata=message(metadata), holding=holding)

    async def configure(self, profile):
        # Explicitly opt into the documented UFactory extension.
        if self.config['gripper_mode'] == 'ufactory_g2':
            async def torque():
                feedback = await self.gripper.do_command(
                    {'get_gripper_torque': True}, timeout=self.timeout)
                values = [value for key, value in feedback.items()
                          if 'torque' in key and type(value) in (int, float)
                          and math.isfinite(value) and 0 <= value <= 100]
                if len(values) != 1:
                    raise RuntimeError('G2 force capability was not confirmed')
                return values[0]
            await torque()
            await self.gripper.do_command(
                {'set_gripper_torque': profile['grip_force_percent']}, timeout=self.timeout)
            if not math.isclose(await torque(), profile['grip_force_percent'], abs_tol=0.5):
                raise RuntimeError('Gripper force readback did not match requested setting')
        await self.arm.do_command({'set_speed': profile['speed_deg_s']}, timeout=self.timeout)

    async def move(self, pose, rotating=False):
        ok = await self.motion.move(
            component_name=self.tool,
            destination=PoseInFrame(reference_frame=self.config['frame'], pose=Pose(**pose)),
            world_state=ParseDict(self.config.get('world_state', {}), WorldState()),
            constraints=Constraints(linear_constraint=[LinearConstraint(
                line_tolerance_mm=5, orientation_tolerance_degs=180 if rotating else 5)]),
            timeout=self.timeout)
        if not ok:
            raise RuntimeError('Viam motion returned false')

    async def open(self):
        await self.gripper.open(timeout=self.timeout)

    async def grab(self):
        if not await self.gripper.grab(timeout=self.timeout):
            raise RuntimeError('Gripper Grab did not report an object')

    async def stop(self):
        # Never auto-open on failure: an object may still be suspended.
        await asyncio.wait_for(self.robot.stop_all(), 5)
