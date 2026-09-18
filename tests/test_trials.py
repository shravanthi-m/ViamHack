import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from trials.runner import episode, label, orientation_error, read, report, validate
from trials.viam_io import ViamIO


def fixture():
    config = read('station.example.json')
    config['calibrated'] = True
    config['workspace_mm'] = {'x': [-100, 100], 'y': [-100, 100], 'z': [0, 300]}
    for name in config['waypoints']:
        config['waypoints'][name] = dict(x=0, y=0, z=100, o_x=0, o_y=0, o_z=1, theta=0)
    config['waypoints']['lift']['z'] = 130
    config['waypoints']['pour_tilt']['theta'] = 45
    profile = read('profiles/cup.json')
    profile.update(hold_s=0, pour_dwell_s=0)
    return config, profile


class FakeIO:
    def __init__(self, config, fail_move=None, lost=False):
        self.config = config
        self.calls = []
        self.fail_move = fail_move
        self.lost = lost
        self.moves = 0

    async def capture(self, folder):
        folder.mkdir()
        return {'pose': self.config['waypoints']['pregrasp'],
                'holding': {'is_holding_something': False},
                'images': [{'name': 'color', 'mime': 'image/jpeg'},
                           {'name': 'depth', 'mime': 'image/vnd.viam.dep'}]}

    async def configure(self, profile): self.calls.append('configure')
    async def open(self): self.calls.append('open')
    async def grab(self): self.calls.append('grab')
    async def stop(self): self.calls.append('stop')
    async def holding(self): return {'is_holding_something': not self.lost}

    async def move(self, pose, rotating=False):
        self.calls.append('rotate' if rotating else 'move')
        self.moves += 1
        if self.moves == self.fail_move:
            raise RuntimeError('injected motion failure')


class ValidationTests(unittest.TestCase):
    def test_uncalibrated_and_missing_poses(self):
        c, p = fixture()
        c['calibrated'] = False
        with self.assertRaises(ValueError): validate(c, p, 'pick')
        c['calibrated'] = True
        c['waypoints']['grasp'] = None
        with self.assertRaises(ValueError): validate(c, p, 'pick')

    def test_bounds_force_and_nonfinite(self):
        c, p = fixture()
        for key, value in [('speed_deg_s', float('nan')), ('speed_deg_s', 11), ('grip_force_percent', 20)]:
            changed = dict(p, **{key: value})
            with self.assertRaises(ValueError): validate(c, changed, 'pick')
        c['waypoints']['grasp']['x'] = 1000
        with self.assertRaises(ValueError): validate(c, p, 'pick')

    def test_pick_does_not_need_pour_poses(self):
        c, p = fixture()
        c['waypoints']['pour_ready'] = None
        validate(c, p, 'pick')
        with self.assertRaises(ValueError): validate(c, p, 'pour')

    def test_orientation_uses_viam_convention(self):
        c, _ = fixture()
        pose = c['waypoints']['pregrasp']
        self.assertAlmostEqual(orientation_error(pose, pose), 0)
        self.assertAlmostEqual(orientation_error(pose, dict(pose, theta=90)), 90)
        self.assertAlmostEqual(orientation_error(pose, dict(pose, o_z=-1)), 180)


class EpisodeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c, self.p = fixture()

    async def test_completed_not_automatically_successful_and_report_groups_context(self):
        run = await episode(FakeIO(self.c), self.c, self.p, 'pour', self.temp.name)
        self.assertIsNone(read(run / 'result.json')['outcome'])
        label(run, 'success', poured_g=20)
        other = copy.deepcopy(self.p)
        other['object']['mass_g'] = 200
        await episode(FakeIO(self.c), self.c, other, 'pick', self.temp.name)
        groups = report(self.temp.name)
        self.assertEqual(len(groups), 2)
        self.assertEqual(sum(g['successes'] for g in groups), 1)
        self.assertEqual(sum(g['labeled'] for g in groups), 1)

    async def test_failure_stops_without_release_or_continuation(self):
        io = FakeIO(self.c, fail_move=2)
        with self.assertRaises(RuntimeError):
            await episode(io, self.c, self.p, 'pour', self.temp.name)
        self.assertEqual(io.calls[-1], 'stop')
        self.assertEqual(io.calls.count('open'), 1)
        run = next(Path(self.temp.name).iterdir())
        self.assertEqual(read(run / 'result.json')['status'], 'aborted')
        with self.assertRaises(ValueError): label(run, 'success')

    async def test_slip_prevents_transport(self):
        io = FakeIO(self.c, lost=True)
        with self.assertRaises(RuntimeError):
            await episode(io, self.c, self.p, 'pour', self.temp.name)
        self.assertEqual(io.moves, 2)
        self.assertEqual(io.calls[-1], 'stop')

    async def test_wrong_start_never_moves(self):
        io = FakeIO(self.c)
        original = io.capture
        async def wrong(folder):
            state = await original(folder)
            state['pose'] = dict(state['pose'], x=50)
            return state
        io.capture = wrong
        with self.assertRaises(RuntimeError):
            await episode(io, self.c, self.p, 'pick', self.temp.name)
        self.assertEqual(io.calls, ['stop'])

    async def test_timeout_stops_and_logs(self):
        io = FakeIO(self.c)
        async def blocked(): await asyncio.sleep(5)
        io.grab = blocked
        self.c['limits']['episode_timeout_s'] = 1
        with self.assertRaises(TimeoutError):
            await episode(io, self.c, self.p, 'pick', self.temp.name)
        self.assertEqual(io.calls[-1], 'stop')

    async def test_cancellation_stops_and_logs(self):
        io = FakeIO(self.c)
        async def cancel(): raise asyncio.CancelledError()
        io.grab = cancel
        with self.assertRaises(asyncio.CancelledError):
            await episode(io, self.c, self.p, 'pick', self.temp.name)
        self.assertEqual(io.calls[-1], 'stop')

    async def test_pour_returns_upright_before_next_camera_call(self):
        io = FakeIO(self.c)
        trace = []
        capture, move = io.capture, io.move
        async def record_capture(folder):
            trace.append('camera')
            return await capture(folder)
        async def record_move(pose, rotating=False):
            trace.append('rotate' if rotating else 'move')
            return await move(pose, rotating)
        io.capture, io.move = record_capture, record_move
        await episode(io, self.c, self.p, 'pour', self.temp.name)
        first = trace.index('rotate')
        self.assertEqual(trace[first:first + 3], ['rotate', 'rotate', 'camera'])

    async def test_viam_false_motion_is_failure(self):
        io = object.__new__(ViamIO)
        io.config, io.tool, io.timeout = self.c, 'gripper', 5
        io.motion = AsyncMock()
        io.motion.move.return_value = False
        with self.assertRaises(RuntimeError):
            await io.move(self.c['waypoints']['grasp'])

    async def test_unrecognized_force_command_prevents_any_setting(self):
        io = object.__new__(ViamIO)
        io.config = dict(self.c, gripper_mode='ufactory_g2')
        io.timeout, io.gripper, io.arm = 5, AsyncMock(), AsyncMock()
        io.gripper.do_command.return_value = {}
        with self.assertRaises(RuntimeError): await io.configure(dict(self.p, grip_force_percent=20))
        self.assertEqual(io.gripper.do_command.await_count, 1)
        io.arm.do_command.assert_not_awaited()

    async def test_force_setting_must_be_read_back(self):
        io = object.__new__(ViamIO)
        io.config = dict(self.c, gripper_mode='ufactory_g2')
        io.timeout, io.gripper, io.arm = 5, AsyncMock(), AsyncMock()
        io.gripper.do_command.side_effect = [{'gripper_torque': 50}, {}, {'gripper_torque': 50}]
        with self.assertRaises(RuntimeError): await io.configure(dict(self.p, grip_force_percent=20))
        io.arm.do_command.assert_not_awaited()


if __name__ == '__main__': unittest.main()
