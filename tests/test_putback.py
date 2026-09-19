import copy
import tempfile
import unittest
from pathlib import Path

from runtime.config import read_json
from runtime.demonstrations import write
from runtime.putback import poses, put_back
from runtime.teaching import teach_skill
from tests.test_teach_replay import POSE, FakeIO, config as base_config, yes

ORIGIN = dict(x=0, y=0, z=200, o_x=1, o_y=0, o_z=0, theta=180)


def config():
    stored = base_config()
    stored['primitive_settings']['motion'] = {'speed_deg_s': 10.0, 'origin_pose': copy.deepcopy(ORIGIN)}
    return stored


class RecordingIO(FakeIO):
    """FakeIO that remembers which poses were commanded, and can fail to release."""

    def __init__(self, *args, release=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.poses = []
        self.release = release

    async def pose(self, pose):
        self.poses.append(copy.deepcopy(pose))
        await super().pose(pose)

    async def open(self):
        self.calls.append('open')
        if self.release:
            self.held = False


class PutBack(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = config()
        await teach_skill('coconut_water', FakeIO(), self.config, root=self.root, ask_fn=yes)

    def holding(self, **kwargs):
        io = RecordingIO(**kwargs)
        io.held = True
        return io

    async def run_put_back(self, io, **kwargs):
        return await put_back(io, self.config, 'coconut_water', root=self.root,
                              runs=self.root / 'runs', **kwargs)

    async def test_hovers_descends_releases_retraces_then_homes(self):
        io = self.holding()
        path = await self.run_put_back(io)
        hover = dict(POSE, z=POSE['z'] + 60)
        self.assertEqual(io.poses, [hover, POSE, hover, ORIGIN])
        self.assertEqual(io.calls[0], 'prepare')
        self.assertEqual(io.calls.count('open'), 1)
        self.assertNotIn('stop', io.calls)
        self.assertFalse(io.held)
        result = read_json(path / 'result.json')
        self.assertEqual(result['status'], 'completed')
        self.assertIn('episode_', result['episode'])

    async def test_an_empty_hand_never_moves(self):
        io = RecordingIO()                       # held stays False
        with self.assertRaisesRegex(RuntimeError, 'nothing to put back'):
            await self.run_put_back(io)
        self.assertEqual(io.poses, [])
        self.assertEqual(io.calls[-1], 'stop')

    async def test_an_unverified_release_stops_before_homing(self):
        io = self.holding(release=False)
        with self.assertRaisesRegex(RuntimeError, 'Release not verified'):
            await self.run_put_back(io)
        self.assertNotIn(ORIGIN, io.poses)        # Never carry it home.
        self.assertEqual(io.calls[-1], 'stop')

    async def test_a_motion_failure_stops_all_and_is_recorded(self):
        io = self.holding(fail='pose')
        with self.assertRaisesRegex(RuntimeError, 'motion failure'):
            await self.run_put_back(io)
        self.assertEqual(io.calls[-1], 'stop')
        result = read_json(next((self.root / 'runs').glob('put_back/episode_*/result.json')))
        self.assertEqual(result['status'], 'failed')
        self.assertIn('motion failure', result['error'])

    async def test_the_object_is_never_released_as_recovery(self):
        io = self.holding(fail='pose')
        with self.assertRaises(RuntimeError):
            await self.run_put_back(io)
        self.assertNotIn('open', io.calls)
        self.assertTrue(io.held)


class Targets(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = config()
        await teach_skill('coconut_water', FakeIO(), self.config, root=self.root, ask_fn=yes)

    def test_hover_sits_directly_above_the_taught_place(self):
        targets = poses(self.config, 'coconut_water', root=self.root, approach_mm=45)
        self.assertEqual(targets['place'], POSE)
        self.assertEqual(targets['hover']['z'], POSE['z'] + 45)
        for key in ('x', 'y', 'o_x', 'o_y', 'o_z', 'theta'):
            self.assertEqual(targets['hover'][key], POSE[key])
        self.assertEqual(targets['origin'], ORIGIN)

    def test_a_hover_outside_the_workspace_is_refused_before_connecting(self):
        stored = config()
        stored['workspace_mm']['z'] = [0, POSE['z'] + 20]   # No room to hover above the place.
        with self.assertRaises(ValueError):
            poses(stored, 'coconut_water', root=self.root, approach_mm=60)

    def test_an_absent_origin_is_refused(self):
        stored = config()
        stored['primitive_settings']['motion']['origin_pose'] = None
        with self.assertRaisesRegex(ValueError, 'No taught origin pose'):
            poses(stored, 'coconut_water', root=self.root)

    def test_a_failed_episode_is_refused(self):
        path = next((self.root / 'coconut_water').glob('episode_*'))
        meta = read_json(path / 'metadata.json')
        meta['success'] = False
        write(path / 'metadata.json', meta)
        with self.assertRaises(ValueError):
            poses(self.config, 'coconut_water', root=self.root)

    def test_an_unknown_object_is_refused(self):
        with self.assertRaises(ValueError):
            poses(self.config, 'not_a_station_object', root=self.root)


if __name__ == '__main__':
    unittest.main()
