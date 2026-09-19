"""The command-line shim's own logic: which steps run, and what is refused first.

main.py composes primitives rather than talking to Viam, so these tests check the
composition -- the order of the three shake steps and the checks that happen before
any of them move -- against the same fakes the shake primitive is tested with.
"""
import contextlib
import io
import unittest
from unittest.mock import patch

import main
from primitives.types import Context
from runtime.config import DEFAULT_CONFIG, read_json
from tests.test_shake import FakeArm, FakeGripper, FakeMotion

# The taught origin: a side grip, already near level, so shake can level it.
ORIGIN = dict(x=493.28, y=-48.70, z=202.22, o_x=0.990, o_y=-0.130, o_z=-0.047,
              theta=-178.00)


def config(origin=ORIGIN, **overrides):
    values = read_json(DEFAULT_CONFIG)
    values['calibrated'] = True
    values['workspace_mm'] = {'x': [-800, 800], 'y': [-800, 800], 'z': [37, 805]}
    values['primitive_settings'] = {'motion': {'origin_pose': origin},
                                    'shake': {'frequency_hz': 2.0}}
    return {**values, **overrides}


def args(*extra):
    return main.parser().parse_args(['--shake', '0.6', *extra])


class MidairTests(unittest.TestCase):
    def test_lifts_straight_up_and_keeps_the_taught_orientation(self):
        centre, _, ends = main.midair(config(), ORIGIN, 150.0)
        self.assertEqual(centre['z'], ORIGIN['z'] + 150.0)
        for key in ('x', 'y', 'o_x', 'o_y', 'o_z', 'theta'):
            self.assertEqual(centre[key], ORIGIN[key])
        self.assertEqual([ends['bottom']['z'], ends['top']['z']],
                         [centre['z'] - 15.0, centre['z'] + 15.0])

    def test_a_stroke_leaving_the_workspace_is_refused_before_moving(self):
        # The lift itself fits; the top of the stroke above it does not.
        ceiling = config()
        ceiling['workspace_mm']['z'] = [37, ORIGIN['z'] + 160.0]
        with self.assertRaisesRegex(SystemExit, 'top of stroke'):
            main.midair(ceiling, ORIGIN, 150.0)

    def test_an_uncalibrated_workspace_is_refused(self):
        with self.assertRaisesRegex(SystemExit, 'calibrated workspace'):
            main.midair(config(calibrated=False), ORIGIN, 150.0)

    def test_a_tool_pointing_straight_down_cannot_be_levelled(self):
        down = {**ORIGIN, 'o_x': 0.0, 'o_y': 0.0, 'o_z': -1.0}
        with self.assertRaisesRegex(SystemExit, 'no horizontal heading'):
            main.midair(config(), down, 150.0)


class ShakeStepTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service, self.gripper = FakeMotion(), FakeGripper()
        self.service.at = dict(ORIGIN)

    async def run_steps(self, parsed, values=None):
        from viam.components.arm import Arm
        from viam.components.gripper import Gripper
        from viam.services.motion import MotionClient

        # The shim reports every step it takes; capture it rather than printing it.
        with patch.object(MotionClient, 'from_robot', return_value=self.service), \
             patch.object(Gripper, 'from_robot', return_value=self.gripper), \
             patch.object(Arm, 'from_robot', return_value=FakeArm(self.service)), \
             patch.object(main, 'confirm', return_value=True), \
             contextlib.redirect_stdout(io.StringIO()) as reported:
            await main.home_and_shake(parsed, Context(values or config(), robot=object()))
        return reported.getvalue()

    async def test_homes_then_lifts_then_shakes_in_that_order(self):
        parsed = args('--execute')
        await self.run_steps(parsed)
        heights = [round(goal['z'], 2) for goal in self.service.goals()]
        lifted = ORIGIN['z'] + parsed.lift
        self.assertEqual(heights[0], ORIGIN['z'])   # home first
        self.assertEqual(heights[1], lifted)        # then up into the air
        self.assertEqual(heights[2], lifted)        # then shake levels the tool
        self.assertEqual(sorted({height - lifted for height in heights[3:-1]}),
                         [-15.0, 15.0])            # strokes, both directions
        self.assertEqual(heights[-1], lifted)      # settles back at the centre
        self.assertAlmostEqual(self.service.goals()[-1]['o_z'], 0.0, places=9)

    async def test_a_dry_run_moves_nothing(self):
        reported = await self.run_steps(args())
        self.assertIn('Dry run', reported)
        self.assertEqual(self.service.requests, [])
        self.assertEqual(self.gripper.checks, 0)

    async def test_an_empty_gripper_fails_before_the_arm_leaves_home(self):
        self.gripper = FakeGripper(holding=False)
        with self.assertRaisesRegex(RuntimeError, 'reports nothing held'):
            await self.run_steps(args('--execute'))
        # Homed and lifted, but nothing oscillated: shake checks the grip first.
        self.assertEqual(len(self.service.requests), 2)

    async def test_without_a_taught_origin_nothing_runs(self):
        with self.assertRaisesRegex(SystemExit, 'teach-origin'):
            await self.run_steps(args('--execute'), config(origin=None))
        self.assertEqual(self.service.requests, [])


class ArgumentTests(unittest.TestCase):
    def test_allow_empty_turns_off_the_holding_check_for_this_run_only(self):
        from primitives import shake
        parsed = args('--allow-empty', '--config', 'config/demo.json')
        self.assertFalse(shake.settings(main.task_config(parsed))['require_holding'])
        self.assertTrue(shake.settings(main.task_config(args(
            '--config', 'config/demo.json')))['require_holding'])

    def test_the_mode_flags_pick_the_step(self):
        self.assertIs(main.step(args()), main.home_and_shake)
        self.assertIs(main.step(main.parser().parse_args([])), main.home_and_jog)
        self.assertIs(main.step(main.parser().parse_args(['--teach-origin'])), main.teach)


if __name__ == '__main__':
    unittest.main()
