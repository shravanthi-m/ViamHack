"""Episode storage and an explicit adapter to existing Viam helpers (no experiments)."""
import asyncio
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from primitives import camera, gripper, motion
from primitives.types import Context
from .config import ROOT, number, read_json, validate_config, validate_pose

PHASES = ('lift', 'transport', 'pour', 'return_upright', 'transport_back', 'place', 'retract')
DEFAULT_ROOT = ROOT / 'demonstrations'


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def new_directory(root, name):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('Skill name must contain only letters, digits, underscores or hyphens')
    path = Path(root) / name / ('episode_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
                              + '_' + uuid4().hex[:8])
    path.mkdir(parents=True, exist_ok=False)
    return path


def event(directory, step, **data):
    print(step, flush=True)
    with (Path(directory) / 'events.jsonl').open('a') as out:
        out.write(json.dumps(dict(timestamp=now(), step=step, **data), allow_nan=False) + '\n')


async def ask(prompt):
    """Cancellable terminal input; no executor thread left blocking shutdown."""
    print(prompt, end=' ', flush=True)
    loop = asyncio.get_running_loop()
    ready = loop.create_future()
    def readable():
        if not ready.done():
            ready.set_result(sys.stdin.readline())
    loop.add_reader(sys.stdin, readable)
    try:
        line = await ready
        if not line:
            raise RuntimeError('Terminal closed')
        value = line.strip()
        if value.lower() in ('q', 'quit', 'abort'):
            raise RuntimeError('Operator aborted')
        return value
    finally:
        loop.remove_reader(sys.stdin)


async def confirm(prompt, ask_fn=ask):
    if (await ask_fn(prompt + ' Type yes:')).lower() != 'yes':
        raise RuntimeError('Operator did not confirm the phase gate')


def station(config):
    """Allowlisted provenance only: never persist credentials or entire local config."""
    calibrations = {}
    for view, filename in config.get('primitive_settings', {}).get('localization', {}).get('homographies', {}).items():
        path = Path(filename)
        body = (path if path.is_absolute() else ROOT / path).read_bytes()
        calibrations[view] = {'sha256': hashlib.sha256(body).hexdigest(), 'data': json.loads(body)}
    return dict(frame=config['frame'], resources=config['resources'], views=camera.views(config),
                workspace_mm=config['workspace_mm'], calibration=calibrations)


def require_station(config, skill):
    validate_config(config, execute=True)
    if skill not in config['objects']:
        raise ValueError(f'{skill!r} must be declared in the task config objects list')
    if not {'wrist', 'overhead'} <= camera.views(config).keys():
        raise ValueError('Teaching/replay needs configured wrist and overhead camera views')


def joint_vector(values, count=None):
    if not isinstance(values, list) or not values or (count is not None and len(values) != count):
        raise ValueError('Missing or inconsistent joint vector')
    for value in values:
        number(value, -360, 360, 'joint angle (degrees)')


class ViamIO:
    """Keep full taught orientation; go_to_pose's yaw-only API would discard it."""
    def __init__(self, ctx):
        self.ctx = ctx
        self.timeout = ctx.config['limits']['primitive_timeout_s']

    async def state(self):
        from viam.components.arm import Arm
        started = now()
        async with asyncio.timeout(self.timeout):
            pose = await motion.tool_pose(self.ctx)
            try:
                joints = list((await Arm.from_robot(self.ctx.robot, self.ctx.config['resources']['arm'])
                               .get_joint_positions(timeout=self.timeout)).values)
            except NotImplementedError:
                joints = None
        validate_pose(pose, self.ctx.config, execute=True)
        if joints is not None:
            joint_vector(joints)
        return dict(timestamp=started, completed_at=now(), pose=pose, joints=joints)

    async def capture(self, directory, label):
        config = copy.deepcopy(self.ctx.config)
        config.setdefault('primitive_settings', {}).setdefault('camera', {})['image_dir'] = str(Path(directory).resolve())
        result = {}
        for view in ('overhead', 'wrist'):
            async with asyncio.timeout(self.timeout):
                frame = await camera.capture(Context(config, self.ctx.robot), view=view)
            for index, item in enumerate(frame['images']):
                original = Path(item['path'])
                original = original if original.is_absolute() else ROOT / original
                dest = Path(directory) / f'{view}_{label}_{index}{original.suffix}'
                original.rename(dest)
                item['path'] = dest.name
                item['sha256'] = hashlib.sha256(dest.read_bytes()).hexdigest()
            if not any(item['mime_type'] in ('image/jpeg', 'image/png', 'image/vnd.viam.rgba')
                       and 'depth' not in (item['name'] or '').lower() for item in frame['images']):
                raise RuntimeError(f'{view} returned no identifiable RGB frame')
            result[view] = frame
        return dict(timestamp=now(), frames=result, state=await self.state())

    async def pose(self, pose):
        validate_pose(pose, self.ctx.config, execute=True)
        async with asyncio.timeout(self.timeout):
            return await motion.plan_to(self.ctx, pose, motion.settings(self.ctx.config), self.timeout, 'taught pose')

    async def joints(self, values):
        from viam.components.arm import Arm
        from viam.proto.component.arm import JointPositions
        async with asyncio.timeout(self.timeout):
            arm = Arm.from_robot(self.ctx.robot, self.ctx.config['resources']['arm'])
            await arm.move_to_joint_positions(JointPositions(values=values), timeout=self.timeout)
            reached = list((await arm.get_joint_positions(timeout=self.timeout)).values)
        joint_vector(reached, len(values))
        if max(abs(a - b) for a, b in zip(reached, values)) > motion.settings(self.ctx.config)['joint_tolerance_deg']:
            raise RuntimeError('Joint replay stopped short')

    async def holding(self):
        from viam.components.gripper import Gripper
        async with asyncio.timeout(self.timeout):
            return await gripper.held(Gripper.from_robot(self.ctx.robot, self.ctx.config['resources']['gripper']),
                                      self.timeout, required=True, setting='teach/replay')

    async def close(self, force):
        async with asyncio.timeout(self.timeout):
            return await gripper.close_gripper(self.ctx, force_percent=force)

    async def open(self):
        async with asyncio.timeout(self.timeout):
            return await gripper.open_gripper(self.ctx)

    async def prepare(self):
        from viam.components.arm import Arm
        async with asyncio.timeout(self.timeout):
            await motion.apply_speed(Arm.from_robot(self.ctx.robot, self.ctx.config['resources']['arm']),
                                     motion.settings(self.ctx.config), self.timeout)

    async def stop(self):
        await asyncio.wait_for(self.ctx.robot.stop_all(), 5)


async def stop_on_failure(io, directory, exc):
    event(directory, 'aborted', error=f'{type(exc).__name__}: {exc}')
    try:
        await io.stop()
        event(directory, 'stop_all_requested')
    except BaseException as stop_exc:
        event(directory, 'stop_all_failed', error=str(stop_exc))
