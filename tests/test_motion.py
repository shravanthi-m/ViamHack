from tests.config import UNCALIBRATED_CONFIG
import unittest
from unittest.mock import patch

from primitives import motion
from primitives.types import Context
from runtime.config import read_json


def config(**overrides):
    values = read_json(UNCALIBRATED_CONFIG)
    values['calibrated'] = True
    values['workspace_mm'] = {'x': [-500, 500], 'y': [-500, 500], 'z': [0, 500]}
    return {**values, **overrides}


def viam_pose(x, y, z, yaw, *, o_x=0.0, o_y=0.0, o_z=-1.0):
    from viam.proto.common import Pose
    return Pose(x=x, y=y, z=z, o_x=o_x, o_y=o_y, o_z=o_z, theta=yaw)


class FakeMotion:
    """Stands in for the Viam motion service: records the plan request, reports arrival."""

    def __init__(self, reached, *, moved=True):
        self.reached, self.moved, self.requests = reached, moved, []

    async def move(self, *, component_name, destination, timeout=None):
        self.requests.append((component_name, destination, timeout))
        return self.moved

    async def get_pose(self, component_name, destination_frame, timeout=None):
        from viam.proto.common import PoseInFrame
        return PoseInFrame(reference_frame=destination_frame, pose=self.reached)


class FakeArm:
    """Stands in for the arm: reports joints, records joint moves and do_commands."""

    def __init__(self, joints=(0.0,) * 6, *, arrives_at=None, speed_supported=True):
        self.joints = list(joints)
        self.arrives_at = arrives_at
        self.speed_supported, self.commands, self.moves = speed_supported, [], []

    async def get_joint_positions(self, timeout=None):
        from viam.proto.component.arm import JointPositions
        return JointPositions(values=self.joints)

    async def move_to_joint_positions(self, positions, timeout=None):
        self.moves.append(list(positions.values))
        self.joints = list(self.arrives_at if self.arrives_at is not None else positions.values)

    async def do_command(self, command, timeout=None):
        if not self.speed_supported:
            raise RuntimeError('unknown command: set_speed')
        self.commands.append(command)
        return {'ok': True}


class MotionTestCase(unittest.IsolatedAsyncioTestCase):
    def drive(self, service=None, arm=None):
        """Point both SDK handles at fakes for the duration of one primitive call."""
        from viam.components.arm import Arm
        from viam.services.motion import MotionClient
        self.service, self.arm = service, arm or FakeArm()
        return (patch.object(MotionClient, 'from_robot', return_value=service),
                patch.object(Arm, 'from_robot', return_value=self.arm))

    async def call(self, coroutine, service=None, arm=None, **overrides):
        motion_patch, arm_patch = self.drive(service, arm)
        with motion_patch, arm_patch:
            return await coroutine(Context(config(**overrides), robot=object()))


class GoToPoseTests(MotionTestCase):
    async def go(self, pose, service, arm=None, **overrides):
        return await self.call(lambda ctx: motion.go_to_pose(ctx, pose=pose),
                               service, arm, **overrides)

    async def test_plans_through_viam_with_the_tool_pointing_down(self):
        pose = dict(x=120.0, y=-45.0, z=210.0, yaw=90.0)
        result = await self.go(pose, FakeMotion(viam_pose(120, -45, 210, 90)))
        (component, destination, timeout), = self.service.requests
        self.assertEqual(component, 'gripper')
        self.assertEqual(destination.reference_frame, 'world')
        self.assertEqual((destination.pose.x, destination.pose.y, destination.pose.z),
                         (120.0, -45.0, 210.0))
        self.assertEqual((destination.pose.o_x, destination.pose.o_y, destination.pose.o_z),
                         (0.0, 0.0, -1.0))
        self.assertEqual(destination.pose.theta, 90.0)
        self.assertEqual(timeout, 60)
        self.assertAlmostEqual(result['position_error_mm'], 0)
        self.assertAlmostEqual(result['orientation_error_deg'], 0, places=4)
        self.assertEqual(result['pose']['z'], 210.0)

    async def test_measures_arrival_within_tolerance(self):
        pose = dict(x=0.0, y=0.0, z=100.0, yaw=0.0)
        result = await self.go(pose, FakeMotion(viam_pose(3, 0, 100, 2.0)))
        self.assertAlmostEqual(result['position_error_mm'], 3.0, places=6)
        self.assertAlmostEqual(result['orientation_error_deg'], 2.0, places=3)

    async def test_refuses_a_move_the_service_could_not_complete(self):
        pose = dict(x=0.0, y=0.0, z=100.0, yaw=0.0)
        with self.assertRaisesRegex(RuntimeError, 'could not plan'):
            await self.go(pose, FakeMotion(viam_pose(0, 0, 100, 0), moved=False))

    async def test_stopping_short_of_the_request_is_a_failure(self):
        pose = dict(x=0.0, y=0.0, z=100.0, yaw=0.0)
        for reached in (viam_pose(0, 0, 90, 0), viam_pose(0, 0, 100, 30)):
            with self.subTest(reached=reached):
                with self.assertRaisesRegex(RuntimeError, 'from the requested pose'):
                    await self.go(pose, FakeMotion(reached))

    async def test_endpoint_outside_the_approved_workspace_never_moves(self):
        service = FakeMotion(viam_pose(0, 0, 600, 0))
        with self.assertRaisesRegex(ValueError, 'outside the calibrated workspace'):
            await self.go(dict(x=0.0, y=0.0, z=600.0, yaw=0.0), service)
        self.assertEqual(service.requests, [])

    async def test_uncalibrated_config_never_moves(self):
        service = FakeMotion(viam_pose(0, 0, 100, 0))
        with self.assertRaisesRegex(ValueError, 'calibrated workspace'):
            await self.go(dict(x=0.0, y=0.0, z=100.0, yaw=0.0), service, calibrated=False)
        self.assertEqual(service.requests, [])

    async def test_team_tolerances_are_validated_and_applied(self):
        pose = dict(x=0.0, y=0.0, z=100.0, yaw=0.0)
        reached = viam_pose(0, 0, 103, 0)
        await self.go(pose, FakeMotion(reached))  # default 5 mm tolerance accepts 3 mm
        tight = {'primitive_settings': {'motion': {'position_tolerance_mm': 1}}}
        with self.assertRaisesRegex(RuntimeError, 'from the requested pose'):
            await self.go(pose, FakeMotion(reached), **tight)
        for bad in ({'position_tolerance_mm': 0}, {'position_tolerance_mm': 51},
                    {'position_tolerance_mm': 'tight'}, {'speed_deg_s': 31},
                    {'origin_joints_deg': []}, {'origin_joints_deg': [0, 'x']},
                    {'unknown': 10}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    await self.go(pose, FakeMotion(reached),
                                  primitive_settings={'motion': bad})


ORIGIN = dict(x=300.0, y=-100.0, z=250.0, o_x=0.882, o_y=-0.471, o_z=-0.001, theta=172.247)


def taught(**motion_settings):
    return {'primitive_settings': {'motion': {'origin_pose': ORIGIN, **motion_settings}}}


class GoToOriginTests(MotionTestCase):
    async def home(self, service, arm=None, **overrides):
        return await self.call(motion.go_to_origin, service, arm, **overrides)

    def viam_origin(self, **changes):
        return viam_pose(**{**{'x': ORIGIN['x'], 'y': ORIGIN['y'], 'z': ORIGIN['z'],
                               'yaw': ORIGIN['theta'], 'o_x': ORIGIN['o_x'],
                               'o_y': ORIGIN['o_y'], 'o_z': ORIGIN['o_z']}, **changes})

    async def test_plans_to_the_taught_pose_keeping_its_orientation(self):
        result = await self.home(FakeMotion(self.viam_origin()), **taught())
        (component, destination, _), = self.service.requests
        self.assertEqual(component, 'gripper')
        self.assertEqual(destination.reference_frame, 'world')
        # The taught orientation is horizontal and must survive untouched.
        self.assertAlmostEqual(destination.pose.o_x, ORIGIN['o_x'], places=6)
        self.assertAlmostEqual(destination.pose.o_z, ORIGIN['o_z'], places=6)
        self.assertAlmostEqual(destination.pose.theta, ORIGIN['theta'], places=4)
        self.assertAlmostEqual(result['position_error_mm'], 0, places=6)

    async def test_without_a_taught_origin_it_refuses_instead_of_guessing(self):
        service = FakeMotion(self.viam_origin())
        with self.assertRaisesRegex(ValueError, 'No taught origin pose'):
            await self.home(service)
        self.assertEqual(service.requests, [])

    async def test_stopping_short_of_the_origin_is_a_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'go_to_origin: arm stopped'):
            await self.home(FakeMotion(self.viam_origin(x=ORIGIN['x'] + 40)), **taught())

    async def test_homing_does_not_need_a_calibrated_workspace(self):
        service = FakeMotion(self.viam_origin())
        await self.home(service, calibrated=False,
                        workspace_mm={'x': None, 'y': None, 'z': None}, **taught())
        self.assertEqual(len(service.requests), 1)

    async def test_configured_speed_is_applied_before_moving(self):
        arm = FakeArm()
        result = await self.home(FakeMotion(self.viam_origin()), arm, **taught(speed_deg_s=10))
        self.assertEqual(arm.commands, [{'set_speed': 10}])
        self.assertEqual(result['speed_deg_s'], 10)

    async def test_an_arm_that_cannot_slow_down_fails_instead_of_moving_fast(self):
        service = FakeMotion(self.viam_origin())
        with self.assertRaises(RuntimeError):
            await self.home(service, FakeArm(speed_supported=False), **taught(speed_deg_s=5))
        self.assertEqual(service.requests, [])

    async def test_a_malformed_taught_origin_is_rejected(self):
        service = FakeMotion(self.viam_origin())
        for bad in ({**ORIGIN, 'o_x': 0.0, 'o_y': 0.0, 'o_z': 0.0},  # zero orientation
                    {key: value for key, value in ORIGIN.items() if key != 'theta'},
                    {**ORIGIN, 'x': float('inf')}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    await self.home(service, **{'primitive_settings':
                                                {'motion': {'origin_pose': bad}}})


class TeachOriginTests(MotionTestCase):
    async def teach(self, service, arm, **overrides):
        return await self.call(motion.teach_origin, service, arm, **overrides)

    async def test_visits_the_origin_joints_and_measures_the_pose_there(self):
        arm = FakeArm(joints=[10.0] * 6)
        measured = viam_pose(300, -100, 250, 172.247, o_x=0.882, o_y=-0.471, o_z=-0.001)
        result = await self.teach(FakeMotion(measured), arm)
        self.assertEqual(arm.moves, [motion.ORIGIN_JOINTS_DEG])
        self.assertEqual(result['started_from_deg'], [10.0] * 6)
        self.assertAlmostEqual(result['max_joint_error_deg'], 0)
        self.assertAlmostEqual(result['origin_pose']['x'], 300.0, places=4)
        self.assertAlmostEqual(result['origin_pose']['o_x'], 0.882, places=6)

    async def test_stopping_short_of_the_origin_joints_is_a_failure(self):
        short = [angle + 5 for angle in motion.ORIGIN_JOINTS_DEG]
        with self.assertRaisesRegex(RuntimeError, 'from the origin joints'):
            await self.teach(FakeMotion(None), FakeArm(joints=[0.0] * 6, arrives_at=short))

    async def test_joint_count_mismatch_never_moves(self):
        arm = FakeArm(joints=[0.0] * 7)
        with self.assertRaisesRegex(RuntimeError, 'reports 7 joints'):
            await self.teach(FakeMotion(None), arm)
        self.assertEqual(arm.moves, [])


if __name__ == '__main__':
    unittest.main()
