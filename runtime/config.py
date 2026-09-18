"""Task policy only. Hardware configuration stays on the Viam machine."""
import json
import math
from pathlib import Path

from .calibration import settings as calibration_settings

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / 'config/demo.json'
POSE_FIELDS = ('x', 'y', 'z', 'o_x', 'o_y', 'o_z', 'theta')
YAW_POSE_FIELDS = ('x', 'y', 'z', 'yaw')


def read_json(path):
    return json.loads(Path(path).read_text())


def number(value, low, high, name):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not low <= value <= high):
        raise ValueError(f'{name} must be a finite number in [{low}, {high}]')


def fields(value, required, name):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError(f'{name} must have exactly these fields: {", ".join(required)}')


def validate_config(config, *, execute=False):
    if not isinstance(config, dict):
        raise ValueError('Config must be an object')
    for key in ('resources', 'frame', 'objects', 'calibrated', 'workspace_mm', 'limits'):
        if key not in config:
            raise ValueError(f'Missing config field: {key}')
    fields(config['resources'], ('arm', 'gripper', 'camera', 'motion'), 'resources')
    for name in (*config['resources'].values(), config['frame']):
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Resource names and frame must be nonempty strings')
    objects = config['objects']
    if (not isinstance(objects, list) or not objects
            or any(not isinstance(x, str) or not x for x in objects)
            or len(set(objects)) != len(objects)):
        raise ValueError('objects must be unique nonempty strings')
    limits = config['limits']
    fields(limits, ('max_steps', 'primitive_timeout_s', 'run_timeout_s', 'max_stir_duration_s'), 'limits')
    if type(limits['max_steps']) is not int:
        raise ValueError('max_steps must be an integer')
    number(limits['max_steps'], 1, 100, 'max_steps')
    number(limits['primitive_timeout_s'], 0.01, 600, 'primitive_timeout_s')
    number(limits['run_timeout_s'], 0.01, 3600, 'run_timeout_s')
    number(limits['max_stir_duration_s'], 0.01, 60, 'max_stir_duration_s')
    if type(config['calibrated']) is not bool:
        raise ValueError('calibrated must be boolean')
    if execute and not config['calibrated']:
        raise ValueError('Set measured workspace bounds and calibrated=true in config/local.json')
    fields(config['workspace_mm'], ('x', 'y', 'z'), 'workspace_mm')
    for axis, pair in config['workspace_mm'].items():
        if pair is None and not config['calibrated'] and not execute:
            continue
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError(f'workspace_mm.{axis} needs [minimum, maximum]')
        for value in pair:
            number(value, -10000, 10000, f'workspace_mm.{axis}')
        if pair[0] >= pair[1]:
            raise ValueError(f'workspace_mm.{axis} minimum must be below maximum')
    calibration_settings(config)  # Optional; present once bounds are derived from the machine.


def validate_pose(pose, config, *, execute=False):
    fields(pose, POSE_FIELDS, 'pose')
    for key in POSE_FIELDS:
        number(pose[key], -10000, 10000, f'pose.{key}')
    if sum(pose[key] ** 2 for key in ('o_x', 'o_y', 'o_z')) < 1e-8:
        raise ValueError('Pose orientation vector cannot be zero')
    if execute:
        for axis in ('x', 'y', 'z'):
            number(pose[axis], *config['workspace_mm'][axis], f'pose.{axis}')


def validate_yaw_pose(pose, config, *, execute=False):
    """Planner-supplied go_to_pose argument: position in mm, yaw in degrees."""
    fields(pose, YAW_POSE_FIELDS, 'pose')
    for axis in ('x', 'y', 'z'):
        number(pose[axis], -10000, 10000, f'pose.{axis}')
    number(pose['yaw'], -180, 180, 'pose.yaw')
    if execute:
        for axis in ('x', 'y', 'z'):
            number(pose[axis], *config['workspace_mm'][axis], f'pose.{axis}')


def validate_target(target, config, *, execute=False):
    fields(target, ('object_id', 'frame', 'pose'), 'target')
    if target['object_id'] not in config['objects'] or target['frame'] != config['frame']:
        raise ValueError('Target must name a configured object in the configured frame')
    validate_pose(target['pose'], config, execute=execute)
