"""Motion team: use configured Viam planning and collision geometry."""
import math

from .types import Context, Pose, PoseYaw

IMPLEMENTED = {'go_to_origin', 'go_to_pose'}

POSE_FIELDS = ('x', 'y', 'z', 'o_x', 'o_y', 'o_z', 'theta')
# The tool's own +Z axis points straight down in the task frame, so the only free
# orientation left is yaw about it. Viam sends that as an orientation vector, and
# theta about a downward axis is exactly the yaw the planner is given.
TOOL_DOWN = {'o_x': 0.0, 'o_y': 0.0, 'o_z': -1.0}
# Joints of the first waypoint of the recorded coconut pour, reached physically
# during teaching. They are only used by teach_origin, which visits them once so
# the pose there can be measured; go_to_origin then plans to that measured pose.
ORIGIN_JOINTS_DEG = [-4.31, 33.34, -35.34, 3.23, -85.29, 1.89]
DEFAULT_SETTINGS = {
    'position_tolerance_mm': 5.0,
    'orientation_tolerance_deg': 5.0,
    'joint_tolerance_deg': 2.0,
    'speed_deg_s': None,  # None leaves the arm module's own configured speed alone.
    'origin_pose': None,  # Measured by teach_origin; required by go_to_origin.
    'origin_joints_deg': ORIGIN_JOINTS_DEG,
}


def bounded(value, low, high, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not low < value <= high:
        raise ValueError(f'{name} must be a number in ({low}, {high}]')


def settings(config):
    """Validate the team-owned primitive_settings.motion block over the defaults."""
    supplied = config.get('primitive_settings', {}).get('motion', {})
    if not isinstance(supplied, dict) or not set(supplied) <= set(DEFAULT_SETTINGS):
        raise ValueError('primitive_settings.motion accepts only: '
                         + ', '.join(sorted(DEFAULT_SETTINGS)))
    values = {**DEFAULT_SETTINGS, **supplied}
    for key in ('position_tolerance_mm', 'orientation_tolerance_deg', 'joint_tolerance_deg'):
        bounded(values[key], 0, 50, f'primitive_settings.motion.{key}')
    if values['speed_deg_s'] is not None:
        bounded(values['speed_deg_s'], 0, 30, 'primitive_settings.motion.speed_deg_s')
    origin = values['origin_joints_deg']
    if not isinstance(origin, list) or not origin:
        raise ValueError('primitive_settings.motion.origin_joints_deg must be a joint list')
    for index, angle in enumerate(origin):
        if type(angle) not in (int, float) or not math.isfinite(angle) or abs(angle) > 360:
            raise ValueError(f'primitive_settings.motion.origin_joints_deg[{index}] '
                             'must be a joint angle within +/-360 degrees')
    if values['origin_pose'] is not None:
        validate_pose(values['origin_pose'], 'primitive_settings.motion.origin_pose')
    return values


def validate_pose(pose, name):
    """A full Viam pose: millimetres, a nonzero orientation vector, theta in degrees."""
    if not isinstance(pose, dict) or set(pose) != set(POSE_FIELDS):
        raise ValueError(f'{name} must have exactly these fields: ' + ', '.join(POSE_FIELDS))
    for key in POSE_FIELDS:
        value = pose[key]
        if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 10000:
            raise ValueError(f'{name}.{key} must be a finite number')
    if sum(pose[key] ** 2 for key in ('o_x', 'o_y', 'o_z')) < 1e-8:
        raise ValueError(f'{name} orientation vector cannot be zero')


def as_pose(pose: PoseYaw) -> Pose:
    """The full Viam pose that a task-frame x/y/z/yaw means: the tool points down."""
    return {'x': pose['x'], 'y': pose['y'], 'z': pose['z'], 'theta': pose['yaw'], **TOOL_DOWN}


def deviation(reached: Pose, wanted: Pose) -> dict:
    """How far the tool stopped from the request, in mm and degrees."""
    # Orientation vectors are neither Euler angles nor axis-angle: compare through
    # the SDK's quaternion. Its OrientationVector takes theta in radians.
    from viam.spatialmath import OrientationVector

    def quaternion(pose):
        return OrientationVector(o_x=pose['o_x'], o_y=pose['o_y'], o_z=pose['o_z'],
                                 theta=math.radians(pose['theta'])).to_quaternion()

    here, there = quaternion(reached), quaternion(wanted)
    dot = abs(sum(a * b for a, b in ((here.w, there.w), (here.i, there.i),
                                     (here.j, there.j), (here.k, there.k))))
    return {'position_error_mm': math.dist((reached['x'], reached['y'], reached['z']),
                                           (wanted['x'], wanted['y'], wanted['z'])),
            'orientation_error_deg': math.degrees(2 * math.acos(min(1.0, dot)))}


def handles(ctx: Context, name):
    if ctx.robot is None:
        raise ValueError(f'{name} needs a connected robot')
    return ctx.config, settings(ctx.config), ctx.config['limits']['primitive_timeout_s']


async def apply_speed(arm, tuning, timeout):
    """Explicitly opt into the documented UFactory speed extension when configured.

    A configured speed is a deliberate request, so a module that rejects the command
    fails the primitive instead of silently moving at the machine's default speed.
    """
    if tuning['speed_deg_s'] is None:
        return None
    await arm.do_command({'set_speed': tuning['speed_deg_s']}, timeout=timeout)
    return tuning['speed_deg_s']


async def tool_pose(ctx: Context) -> Pose:
    """Read-only: where the tool is now, in the configured frame. Moves nothing."""
    from viam.services.motion import MotionClient

    config, _, timeout = handles(ctx, 'tool_pose')
    motion = MotionClient.from_robot(ctx.robot, config['resources']['motion'])
    held = await motion.get_pose(config['resources']['gripper'], config['frame'], timeout=timeout)
    return {key: getattr(held.pose, key) for key in POSE_FIELDS}


async def plan_to(ctx: Context, wanted: Pose, tuning, timeout, name) -> dict:
    """Plan to a full task-frame pose through Viam, then verify where the tool stopped.

    The motion service owns planning, frame transforms, and collision avoidance
    against the machine's configured geometry. This adds only the task-level part:
    checking that the arm actually arrived.
    """
    from viam.proto.common import Pose as ViamPose, PoseInFrame
    from viam.services.motion import MotionClient

    config = ctx.config
    tool = config['resources']['gripper']
    motion = MotionClient.from_robot(ctx.robot, config['resources']['motion'])
    destination = PoseInFrame(reference_frame=config['frame'], pose=ViamPose(**wanted))
    if not await motion.move(component_name=tool, destination=destination, timeout=timeout):
        raise RuntimeError(f'Viam motion service could not plan or complete {name}')
    reached = await tool_pose(ctx)
    offset = deviation(reached, wanted)
    if (offset['position_error_mm'] > tuning['position_tolerance_mm']
            or offset['orientation_error_deg'] > tuning['orientation_tolerance_deg']):
        raise RuntimeError(
            f'{name}: arm stopped {offset["position_error_mm"]:.1f} mm and '
            f'{offset["orientation_error_deg"]:.1f} deg from the requested pose')
    return {'pose': reached, **offset}


async def go_to_origin(ctx: Context) -> dict:
    """Plan back to the taught origin pose, so every run starts from one known state.

    Collision-planned by the Viam motion service like any other pose. The origin is
    measured once by teach_origin and stored in primitive_settings.motion.origin_pose;
    it keeps the orientation it was taught at, which need not be tool-down.
    """
    from viam.components.arm import Arm

    config, tuning, timeout = handles(ctx, 'go_to_origin')
    origin = tuning['origin_pose']
    if origin is None:
        raise ValueError('No taught origin pose: set primitive_settings.motion.origin_pose, '
                         'or run main.py --teach-origin --execute once to measure it')
    arm = Arm.from_robot(ctx.robot, config['resources']['arm'])
    speed = await apply_speed(arm, tuning, timeout)
    return {**await plan_to(ctx, origin, tuning, timeout, 'go_to_origin'), 'speed_deg_s': speed}


async def go_to_pose(ctx: Context, *, pose: PoseYaw) -> dict:
    """Move the tool to a task-frame x/y/z in mm and yaw in degrees, pointing down.

    Planning, frame transforms, and collision geometry come from the Viam motion
    service and the machine's frame system. Returns the measured arrival pose and
    its deviation from the request; raises if the arm did not arrive within the
    configured tolerances.
    """
    from viam.components.arm import Arm

    config, tuning, timeout = handles(ctx, 'go_to_pose')
    if not config['calibrated']:
        raise ValueError('go_to_pose needs a calibrated workspace before it can move')
    for axis in ('x', 'y', 'z'):
        low, high = config['workspace_mm'][axis]
        if not low <= pose[axis] <= high:
            raise ValueError(f'pose.{axis}={pose[axis]} is outside the calibrated workspace')
    arm = Arm.from_robot(ctx.robot, config['resources']['arm'])
    speed = await apply_speed(arm, tuning, timeout)
    return {**await plan_to(ctx, as_pose(pose), tuning, timeout, 'go_to_pose'),
            'speed_deg_s': speed}


async def teach_origin(ctx: Context) -> dict:
    """One-time calibration: visit the origin joints and measure the pose there.

    This is the only move in this module that the motion service does not plan: a
    joint-space move is the one way to reproduce a taught joint configuration
    exactly, and its pose cannot be computed without it. The path must be clear.
    Store the returned pose as primitive_settings.motion.origin_pose; go_to_origin
    then plans to it with full collision avoidance and never repeats this move.
    """
    from viam.components.arm import Arm
    from viam.proto.component.arm import JointPositions

    config, tuning, timeout = handles(ctx, 'teach_origin')
    origin = tuning['origin_joints_deg']
    arm = Arm.from_robot(ctx.robot, config['resources']['arm'])
    speed = await apply_speed(arm, tuning, timeout)
    started = list((await arm.get_joint_positions(timeout=timeout)).values)
    if len(started) != len(origin):
        raise RuntimeError(f'Arm reports {len(started)} joints; the origin has {len(origin)}')
    await arm.move_to_joint_positions(JointPositions(values=list(origin)), timeout=timeout)
    reached = list((await arm.get_joint_positions(timeout=timeout)).values)
    error = max(abs(a - b) for a, b in zip(reached, origin))
    if error > tuning['joint_tolerance_deg']:
        raise RuntimeError(f'Arm stopped {error:.2f} deg from the origin joints')
    return {'origin_pose': await tool_pose(ctx), 'joints_deg': reached,
            'started_from_deg': started, 'max_joint_error_deg': error, 'speed_deg_s': speed}
