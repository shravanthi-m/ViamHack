import math
import unittest
from unittest.mock import patch

from primitives import shake
from primitives.types import Context
from runtime.config import DEFAULT_CONFIG, read_json

# The arm's real resting orientation: gripping from the side, already near level.
START = dict(x=400.0, y=-50.0, z=250.0, o_x=0.882, o_y=-0.471, o_z=-0.130, theta=172.2)


def config(**overrides):
    values = read_json(DEFAULT_CONFIG)
    values['calibrated'] = True
    values['workspace_mm'] = {'x': [-500, 500], 'y': [-500, 500], 'z': [0, 500]}
    return {**values, **overrides}


def tuned(**shake_settings):
    """A fast shake, so a test takes milliseconds rather than seconds."""
    return {'primitive_settings': {'shake': {'frequency_hz': 2.0, **shake_settings}}}


class FakeMotion:
    """Stands in for the motion service: records plans, reports the tool at the goal."""

    def __init__(self, moved=True):
        self.moved, self.requests, self.reads = moved, [], 0
        self.at = dict(START)

    async def move(self, *, component_name, destination, timeout=None):
        self.requests.append(destination)
        if self.moved:
            pose = destination.pose
            self.at = {key: getattr(pose, key) for key in
                       ('x', 'y', 'z', 'o_x', 'o_y', 'o_z', 'theta')}
        return self.moved

    async def get_pose(self, component_name, destination_frame, timeout=None):
        from viam.proto.common import Pose, PoseInFrame
        self.reads += 1
        return PoseInFrame(reference_frame=destination_frame, pose=Pose(**self.at))

    def goals(self):
        return [{key: getattr(r.pose, key) for key in
                 ('x', 'y', 'z', 'o_x', 'o_y', 'o_z', 'theta')} for r in self.requests]


class FakeGripper:
    def __init__(self, *, holding=True, drops=False, supported=True):
        self.holding, self.drops, self.supported, self.checks = holding, drops, supported, 0

    async def is_holding_something(self, timeout=None):
        from viam.proto.component.gripper import IsHoldingSomethingResponse
        self.checks += 1
        if not self.supported:
            raise RuntimeError('unimplemented: IsHoldingSomething')
        held = self.holding and not (self.drops and self.checks > 1)
        return IsHoldingSomethingResponse(is_holding_something=held)


class FakeArm:
    """Reports distinct joints for captured endpoints and records direct strokes."""
    def __init__(self, service):
        self.service, self.moves = service, []

    async def do_command(self, command, timeout=None):
        return {}

    async def get_joint_positions(self, timeout=None):
        from viam.proto.component.arm import JointPositions
        return JointPositions(values=[self.service.at['z'] / 10, 0, 0, 0, 0, 0])

    async def move_to_joint_positions(self, positions, timeout=None):
        self.moves.append(list(positions.values))


class ShakeTests(unittest.IsolatedAsyncioTestCase):
    async def shake(self, duration_s=0.3, *, service=None, gripper=None, **overrides):
        from viam.components.gripper import Gripper
        from viam.components.arm import Arm
        from viam.services.motion import MotionClient
        self.service = service or FakeMotion()
        self.gripper = gripper or FakeGripper()
        self.arm = FakeArm(self.service)
        with patch.object(MotionClient, 'from_robot', return_value=self.service), \
             patch.object(Gripper, 'from_robot', return_value=self.gripper), \
             patch.object(Arm, 'from_robot', return_value=self.arm):
            return await shake.shake(Context(config(**overrides), robot=object()),
                                     duration_s=duration_s)

    async def test_levels_the_tool_before_any_stroke(self):
        result = await self.shake(**tuned())
        levelling = self.service.goals()[0]
        # Tool axis rotated into the horizontal plane, heading and position kept.
        self.assertAlmostEqual(levelling['o_z'], 0.0, places=9)
        self.assertAlmostEqual(math.hypot(levelling['o_x'], levelling['o_y']), 1.0, places=9)
        self.assertAlmostEqual(levelling['o_x'] / levelling['o_y'],
                               START['o_x'] / START['o_y'], places=6)
        self.assertAlmostEqual(levelling['theta'], START['theta'], places=6)
        for axis in ('x', 'y', 'z'):
            self.assertAlmostEqual(levelling[axis], START[axis], places=6)
        self.assertAlmostEqual(result['levelled_pose']['o_z'], 0.0, places=9)

    async def test_plans_endpoints_then_interpolates_recorded_joint_configurations(self):
        result = await self.shake(**tuned())
        goals = self.service.goals()
        self.assertGreaterEqual(len(goals), 3, 'expected levelling plus strokes')
        centre = result['levelled_pose']['z']
        offsets = sorted({round(goal['z'] - centre, 6) for goal in goals[1:-1]})
        self.assertEqual(offsets, [-15.0, 15.0])  # fixed distance, both directions
        # The planner establishes endpoints; the new primitive strokes in joint space.
        for goal in goals:
            self.assertAlmostEqual(goal['x'], START['x'], places=6)
            self.assertAlmostEqual(goal['y'], START['y'], places=6)
            self.assertAlmostEqual(goal['o_z'], 0.0, places=9)
        self.assertEqual(len(goals), 4)  # level, top capture, bottom capture, settle
        self.assertEqual(len(self.arm.moves), result['strokes'] * 12)
        for joints in self.arm.moves:
            self.assertGreaterEqual(joints[0], (centre - 15) / 10)
            self.assertLessEqual(joints[0], (centre + 15) / 10)
            self.assertEqual(joints[1:], [0] * 5)
        self.assertEqual(result['swept_z_mm'], [centre - 15.0, centre + 15.0])
        self.assertAlmostEqual(goals[-1]['z'], centre, places=6)  # settles back at centre

    async def test_planned_endpoints_and_settle_have_pose_readback(self):
        """The new joint strokes do not provide per-stroke tool-pose feedback."""
        await self.shake(**tuned())
        self.assertEqual(self.service.reads, len(self.service.requests) + 1)

    async def test_alternates_up_and_down(self):
        result = await self.shake(**tuned())
        centre = result['levelled_pose']['z']
        heights = [joints[0] * 10 - centre for joints in self.arm.moves[11::12]]
        self.assertEqual(len(heights), result['strokes'])
        self.assertEqual(heights[0], 15)
        for first, second in zip(heights, heights[1:]):
            self.assertEqual(first, -second, 'strokes must alternate direction')

    async def test_a_stroke_leaving_the_workspace_never_oscillates(self):
        narrow = {'x': [-500, 500], 'y': [-500, 500], 'z': [245, 500]}
        with self.assertRaisesRegex(ValueError, 'bottom of stroke'):
            await self.shake(workspace_mm=narrow, **tuned())
        # Levelling is planned first, since the box is only knowable once level.
        self.assertEqual(len(self.service.requests), 1)

    async def test_a_tool_pointing_straight_down_cannot_be_levelled(self):
        service = FakeMotion()
        service.at = {**START, 'o_x': 0.0, 'o_y': 0.0, 'o_z': -1.0}
        with self.assertRaisesRegex(ValueError, 'no horizontal heading'):
            await self.shake(service=service, **tuned())
        self.assertEqual(service.requests, [])

    async def test_refuses_to_shake_nothing(self):
        with self.assertRaisesRegex(RuntimeError, 'reports nothing held'):
            await self.shake(gripper=FakeGripper(holding=False), **tuned())
        self.assertEqual(self.service.requests, [])

    async def test_a_drop_during_the_shake_is_a_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'dropped during the shake'):
            await self.shake(gripper=FakeGripper(drops=True), **tuned())

    async def test_a_gripper_without_feedback_must_be_opted_out_explicitly(self):
        with self.assertRaisesRegex(RuntimeError, 'cannot report whether it is holding'):
            await self.shake(gripper=FakeGripper(supported=False), **tuned())
        result = await self.shake(gripper=FakeGripper(supported=False),
                                  **tuned(require_holding=False))
        self.assertIsNone(result['holding_before'])
        self.assertTrue(result['strokes'])

    async def test_uncalibrated_workspace_never_moves(self):
        with self.assertRaisesRegex(ValueError, 'calibrated workspace'):
            await self.shake(calibrated=False, **tuned())
        self.assertEqual(self.service.requests, [])

    async def test_a_stroke_the_planner_refuses_is_a_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'could not plan or complete'):
            await self.shake(service=FakeMotion(moved=False), **tuned())

    async def test_duration_is_bounded_by_the_configured_maximum(self):
        for bad in (0, -1, 16, float('nan')):
            with self.subTest(duration_s=bad):
                with self.assertRaises(ValueError):
                    await self.shake(bad, **tuned())

    async def test_settings_are_validated(self):
        for bad in ({'stroke_mm': 0}, {'stroke_mm': 51}, {'frequency_hz': 0},
                    {'frequency_hz': 3}, {'require_holding': 'yes'}, {'unknown': 1}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    await self.shake(primitive_settings={'shake': bad})


if __name__ == '__main__':
    unittest.main()
