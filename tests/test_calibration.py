from tests.config import UNCALIBRATED_CONFIG
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from runtime import calibration
from runtime.__main__ import dispatch, parser
from runtime.config import read_json, validate_config

ANCHOR = (493.3, -48.7, 202.2)


def box(x, y, z, label, *, at=(0.0, 0.0, 0.0)):
    return {'type': 'box', 'x': x, 'y': y, 'z': z, 'r': 0.0, 'l': 0.0, 'Label': label,
            'translation': {'X': at[0], 'Y': at[1], 'Z': at[2]},
            'orientation': {'type': 'quaternion', 'value': {'W': 1.0, 'X': 0.0, 'Y': 0.0, 'Z': 0.0}}}


def link(name, parent, geometry, at=(0.0, 0.0, 0.0)):
    return {'id': name, 'parent': parent, 'geometry': geometry,
            'translation': {'X': at[0], 'Y': at[1], 'Z': at[2]},
            'orientation': {'type': 'quaternion', 'value': {'W': 1.0, 'X': 0.0, 'Y': 0.0, 'Z': 0.0}}}


def frame(name, parent, pose, kinematics=None, physical=None):
    described = {'reference_frame': name,
                 'pose_in_observer_frame': {'reference_frame': parent, 'pose': pose}}
    if physical is not None:
        described['physical_object'] = physical
    return {'frame': described, 'kinematics': kinematics or {}}


def obstacle(name, pose, geometry):
    return frame(name, 'world', pose, {'name': name, 'links': [link(name, 'world', geometry)]})


def frames():
    """The station as the machine publishes it: a table, a ceiling and two walls."""
    return [
        obstacle('table', {'z': -123.0, 'o_z': 1.0}, box(3000.0, 3000.0, 200.0, 'table')),
        obstacle('ceiling', {'z': 1050.0, 'o_z': 1.0}, box(3000.0, 3000.0, 100.0, 'ceiling')),
        obstacle('wall-side', {'y': 500.0, 'z': 300.0, 'o_z': 1.0},
                 box(3000.0, 100.0, 1600.0, 'wall-side')),
        obstacle('wall-front', {'x': 740.0, 'z': 300.0, 'o_z': 1.0},
                 box(100.0, 3000.0, 1600.0, 'wall-front')),
        frame('arm', 'world', {'o_z': 1.0}, {'name': 'xArm6', 'joints': [
            {'id': 'waist', 'parent': 'base', 'type': 'revolute', 'min': -359.0, 'max': 359.0}],
            'links': [
                {'id': 'base', 'parent': 'world', 'translation': {'X': 0.0, 'Y': 0.0, 'Z': 0.0},
                 'orientation': {'type': ''}},
                {'id': 'base_top', 'parent': 'waist',
                 'translation': {'X': 0.0, 'Y': 0.0, 'Z': 267.0},
                 'orientation': {'type': 'ov_degrees',
                                 'value': {'x': 0.0, 'y': 0.0, 'z': -1.0, 'th': 0.0}},
                 'geometry': {'type': '', 'x': 0.0, 'y': 0.0, 'z': 0.0, 'r': 50.0, 'l': 320.0,
                              'translation': {'X': 0.0, 'Y': 0.0, 'Z': 160.0}}}]}),
        frame('gripper', 'arm', {'z': 105.0, 'o_z': 1.0, 'theta': 180.0}, {'name': 'gripper', 'links': [
            link('case-gripper', 'world', box(75.0, 110.0, 110.0, 'case', at=(0.0, 0.0, -55.0))),
            link('claws', 'case-gripper', box(45.0, 120.0, 112.0, 'claws', at=(0.0, 0.0, -6.0)))]}),
        frame('cam', 'arm', {'x': -83.0, 'y': 14.0, 'z': 18.0, 'o_z': 1.0},
              physical={'center': {'x': 32.2, 'o_z': 1.0}, 'label': 'box',
                        'box': {'dims_mm': {'x': 90.0, 'y': 25.0, 'z': 25.0}}}),
    ]


def config(**overrides):
    return {**read_json(UNCALIBRATED_CONFIG), **overrides}


def derive(**kwargs):
    values = {'margin_mm': 10.0, 'reach_mm': 700.0, **kwargs}
    return calibration.workspace(kwargs.pop('frames', None) or frames(), config(), ANCHOR,
                                 margin_mm=values['margin_mm'], reach_mm=values['reach_mm'])


class WorkspaceTests(unittest.TestCase):
    def test_every_bound_comes_from_configured_geometry(self):
        result = derive()
        # wall-front at x=690, less the tool radius and the margin; table top at z=-23 plus
        # the claw drop and the margin; the walls and table are the only things in range.
        self.assertEqual(result['workspace_mm'],
                         {'x': [-700.0, 609.2], 'y': [-700.0, 369.2], 'z': [37.0, 700.0]})
        self.assertEqual(result['bounds_from'], {'x': ['reach', 'wall-front'],
                                                 'y': ['reach', 'wall-side'],
                                                 'z': ['table', 'reach']})

    def test_tool_extent_is_read_from_the_configured_gripper(self):
        result = derive()
        self.assertEqual(result['tool_mm']['above_mm'], 110.0)
        self.assertEqual(result['tool_mm']['below_mm'], 50.0)
        self.assertAlmostEqual(result['tool_mm']['radius_mm'], math.hypot(37.5, 60.0), places=3)
        # A tool that hangs further down keeps the commanded point further off the table.
        taller = frames()
        taller[5]['kinematics']['links'][1]['geometry']['z'] = 212.0
        self.assertEqual(derive(frames=taller)['workspace_mm']['z'][0], 87.0)

    def test_ceiling_binds_once_it_is_within_reach(self):
        result = derive(reach_mm=1200.0)
        self.assertEqual(result['workspace_mm']['z'], [37.0, 880.0])
        self.assertEqual(result['bounds_from']['z'], ['table', 'ceiling'])

    def test_reach_cap_closes_the_faces_no_obstacle_bounds(self):
        result = derive(reach_mm=600.0)
        self.assertEqual(result['workspace_mm'], {'x': [-600.0, 600.0], 'y': [-600.0, 369.2],
                                                  'z': [37.0, 600.0]})
        self.assertEqual(result['bounds_from']['x'], ['reach', 'reach'])

    def test_unset_reach_falls_back_to_the_arms_own_link_offsets(self):
        result = calibration.workspace(frames(), config(), (100.0, 0.0, 200.0))
        self.assertFalse(result['reach_declared'])
        self.assertAlmostEqual(result['reach_mm'], 267.0 + 105.0, places=6)  # Links, then tool.
        self.assertEqual(result['workspace_mm']['x'], [-372.0, 372.0])

    def test_obstacle_pose_places_its_geometry(self):
        turned = frames()
        # The same wall, stood across the other axis: it has to bind y instead of x.
        turned[3]['frame']['pose_in_observer_frame']['pose'] = {
            'x': 0.0, 'y': -740.0, 'z': 300.0, 'o_z': 1.0, 'theta': 90.0}
        result = derive(frames=turned)
        self.assertEqual(result['bounds_from']['y'], ['wall-front', 'wall-side'])
        self.assertEqual(result['workspace_mm']['y'], [-609.2, 369.2])
        self.assertEqual(result['bounds_from']['x'], ['reach', 'reach'])

    def test_moving_and_foreign_frames_are_never_obstacles(self):
        result = derive()
        self.assertEqual([item['name'] for item in result['obstacles']],
                         ['table', 'ceiling', 'wall-side', 'wall-front'])
        self.assertEqual({item['frame'] for item in result['skipped']}, {'arm', 'gripper', 'cam'})

    def test_geometry_in_an_unreadable_orientation_is_refused(self):
        bad = frames()
        bad[0]['kinematics']['links'][0]['orientation'] = {'type': 'euler_angles',
                                                           'value': {'roll': 0.0}}
        with self.assertRaisesRegex(ValueError, 'euler_angles'):
            derive(frames=bad)

    def test_clearance_that_swallows_the_anchor_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'clearance around'):
            derive(margin_mm=400.0)

    def test_anchor_outside_the_reach_cap_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'outside the reach cap'):
            derive(reach_mm=100.0)

    def test_rounding_never_widens_a_bound(self):
        self.assertEqual(calibration.inward(36.94, 609.29), [37.0, 609.2])


class SettingsTests(unittest.TestCase):
    def test_config_without_a_calibration_block_is_still_valid(self):
        validate_config(read_json(UNCALIBRATED_CONFIG))
        self.assertEqual(calibration.settings({})['margin_mm'], 10.0)

    def test_bad_calibration_blocks_are_rejected_by_every_command(self):
        for block in ({'margin_mm': -1}, {'reach_mm': 0}, {'margin_mm': 'wide'},
                      {'workspace': 'guessed'}, []):
            with self.subTest(block=block):
                with self.assertRaises(ValueError):
                    validate_config(config(calibration=block))


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name) / 'local.json'
        self.robot = AsyncMock()
        self.robot.resource_names = [
            type('Name', (), {'type': kind, 'subtype': subtype, 'name': name})()
            for kind, subtype, name in (('component', 'arm', 'arm'),
                                        ('component', 'gripper', 'gripper'),
                                        ('component', 'camera', 'cam'),
                                        ('service', 'motion', 'builtin'))]

    async def calibrate(self, *flags, settings=None):
        source = Path(self.temp.name) / 'source.json'
        source.write_text(json.dumps(config(**(settings or {}))))
        args = parser().parse_args(['--config', str(source), 'calibrate', *flags])
        with patch('runtime.__main__.connect', new_callable=AsyncMock, return_value=self.robot), \
             patch('runtime.__main__.LOCAL_CONFIG', self.local), \
             patch.object(calibration, 'read_frames', new_callable=AsyncMock,
                          return_value=frames()):
            await dispatch(args)

    async def test_dry_run_writes_nothing(self):
        await self.calibrate('--anchor', '493.3,-48.7,202.2', '--reach-mm', '700')
        self.assertFalse(self.local.exists())

    async def test_write_saves_bounds_and_the_evidence_behind_them(self):
        await self.calibrate('--anchor', '493.3,-48.7,202.2', '--reach-mm', '700', '--write')
        stored = read_json(self.local)
        self.assertEqual(stored['workspace_mm'],
                         {'x': [-700.0, 609.2], 'y': [-700.0, 369.2], 'z': [37.0, 700.0]})
        self.assertTrue(stored['calibrated'])
        self.assertEqual(stored['calibration']['reach_mm'], 700.0)
        self.assertEqual(stored['calibration']['derived']['source'], 'viam_frame_system')
        self.assertEqual(stored['calibration']['derived']['bounds_from']['z'], ['table', 'reach'])
        validate_config(stored, execute=True)  # What was written is runnable as written.

    async def test_the_taught_origin_anchors_the_workspace_by_default(self):
        taught = {'primitive_settings': {'motion': {'origin_pose': {
            'x': 493.3, 'y': -48.7, 'z': 202.2, 'o_x': 0.0, 'o_y': 0.0, 'o_z': -1.0,
            'theta': 180.0}}}}
        await self.calibrate('--reach-mm', '700', '--write', settings=taught)
        self.assertEqual(read_json(self.local)['calibration']['derived']['anchor_mm'],
                         [493.3, -48.7, 202.2])
        self.robot.get_pose.assert_not_awaited()

    async def test_writing_keeps_the_rest_of_the_local_config(self):
        kept = config(calibrated=False)
        kept['primitive_settings'] = {'motion': {'speed_deg_s': 10.0}}
        self.local.write_text(json.dumps(kept))
        await self.calibrate('--anchor', '493.3,-48.7,202.2', '--reach-mm', '700', '--write')
        stored = read_json(self.local)
        self.assertEqual(stored['primitive_settings']['motion']['speed_deg_s'], 10.0)
        self.assertTrue(stored['calibrated'])
