"""Offline sequence tests: no robot, network, or credentials."""
import asyncio
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from runtime import taught_actions as ta
from runtime.config import DEFAULT_CONFIG, read_json
from primitives.types import Context


def fixture(action='shake'):
    config = read_json(DEFAULT_CONFIG)
    config['primitive_settings']['shake'] = dict(stroke_mm=10, frequency_hz=0.5,
        require_holding=True, samples_per_stroke=4, control_mode='sampled')
    book = ta.new_book(action, config)
    book.update(station_verified=True, force_percent=10, travel_speed_deg_s=10,
                placement_speed_deg_s=3, settle_s=0)
    for label, xyz in {'source_approach': (200, 0, 200), 'source_grasp': (220, 0, 100),
                       'source_lift': (220, 0, 180), 'cup_approach': (300, 0, 200),
                       'cup_action': (300, 0, 180)}.items():
        if label not in ta.LABELS[action]:
            continue
        pose = dict(zip(('x', 'y', 'z'), xyz), o_x=1, o_y=0, o_z=0, theta=0)
        book['poses'][label] = dict(source='motion.get_pose', frame='world',
                                  component='gripper', pose=pose)
    return config, book


class FakeIO:
    def __init__(self, config):
        self.ctx = Context(config, None)
        self.held = False
        self.calls = []
        self.fail_at = None
        self.fail_grasp = False
        self.fail_release = False

    async def holding(self):
        return self.held

    async def move_at(self, pose, speed):
        self.calls.append(('move', dict(pose), speed))
        if len([c for c in self.calls if c[0] == 'move']) == self.fail_at:
            raise RuntimeError('planning failed')

    async def open(self):
        self.calls.append(('open',))
        if not self.fail_release:
            self.held = False

    async def close(self, force):
        self.calls.append(('close', force))
        self.held = not self.fail_grasp

    async def stop(self):
        self.calls.append(('stop',))


class TaughtActionTests(unittest.IsolatedAsyncioTestCase):
    def test_atomic_gripper_fractional_force_fails_before_motion(self):
        config, book = fixture()
        config['primitive_settings']['gripper'] = {'force_control': 'ufactory_atomic'}
        book['force_percent'] = 10.5
        with self.assertRaisesRegex(ValueError, 'whole controller percent'):
            ta.validate(book, config)

    async def test_shake_returns_to_supported_grasp_before_release(self):
        config, book = fixture()
        io = FakeIO(config)
        async def shake(ctx, duration_s):
            self.assertTrue(io.held)
            self.assertEqual(io.calls[-1][1], book['poses']['source_lift']['pose'])
            io.calls.append(('shake',))
            return {'start_pose': book['poses']['source_lift']['pose']}
        await ta.execute(book, config, io, shake, lambda *a, **k: None)
        self.assertEqual([c[0] for c in io.calls],
                         ['open', 'move', 'move', 'close', 'move', 'shake', 'move', 'move', 'open', 'move'])
        place = io.calls[-3]
        self.assertEqual(place[1], book['poses']['source_grasp']['pose'])
        self.assertEqual(place[2], 3)
        self.assertFalse(io.held)

    async def test_squeeze_transport_reverses_before_placement(self):
        config, book = fixture('squeeze')
        io = FakeIO(config)
        action = AsyncMock(return_value={'pulses': 1})
        await ta.execute(book, config, io, action, lambda *a, **k: None)
        moves = [c[1] for c in io.calls if c[0] == 'move']
        labels = ['source_approach', 'source_grasp', 'source_lift', 'cup_approach',
                  'cup_action', 'cup_approach', 'source_lift', 'source_grasp', 'source_approach']
        self.assertEqual(moves, [book['poses'][label]['pose'] for label in labels])
        action.assert_awaited_once_with(io.ctx)

    async def test_faults_stop_without_releasing_held_object(self):
        for failure in ('action', 'place', 'cancel'):
            config, book = fixture()
            io = FakeIO(config)
            io.fail_at = 5 if failure == 'place' else None
            action = AsyncMock(return_value={})
            if failure == 'action':
                action.side_effect = RuntimeError('shake failed')
            if failure == 'cancel':
                action.side_effect = asyncio.CancelledError()
            with self.subTest(failure=failure), self.assertRaises((RuntimeError, asyncio.CancelledError)):
                await ta.execute(book, config, io, action, lambda *a, **k: None)
            self.assertEqual(io.calls[-1], ('stop',))
            self.assertEqual(io.calls.count(('open',)), 1)  # Empty-hand initial opening only.
            self.assertTrue(io.held)

    async def test_bad_grasp_never_lifts(self):
        config, book = fixture()
        io = FakeIO(config)
        io.fail_grasp = True
        action = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, 'Grasp not confirmed'):
            await ta.execute(book, config, io, action, lambda *a, **k: None)
        action.assert_not_awaited()
        self.assertEqual(len([c for c in io.calls if c[0] == 'move']), 2)

    async def test_loaded_start_never_opens(self):
        config, book = fixture()
        io = FakeIO(config)
        io.held = True
        with self.assertRaisesRegex(RuntimeError, 'empty gripper'):
            await ta.execute(book, config, io, AsyncMock(), lambda *a, **k: None)
        self.assertEqual(io.calls, [('stop',)])

    def test_wrong_frame_missing_pose_and_workspace_fail_before_motion(self):
        config, book = fixture()
        bad = copy.deepcopy(book)
        bad['frame'] = 'arm'
        with self.assertRaisesRegex(ValueError, 'frame/resources'):
            ta.validate(bad, config)
        bad = copy.deepcopy(book)
        del bad['poses']['source_grasp']
        with self.assertRaisesRegex(ValueError, 'Teach source_grasp'):
            ta.validate(bad, config)
        bad = copy.deepcopy(book)
        bad['poses']['source_lift']['pose']['z'] = 1000
        with self.assertRaises(ValueError):
            ta.validate(bad, config)

    def test_inherits_no_unreviewed_shake_defaults(self):
        config, book = fixture()
        del config['primitive_settings']['shake']
        with self.assertRaisesRegex(ValueError, 'explicit reviewed'):
            ta.validate(book, config)

    def test_adapter_is_root_shake_and_squeeze_fails_closed(self):
        import shake_full
        self.assertIs(ta.action_function('shake'), shake_full.shake)
        with self.assertRaisesRegex(ValueError, 'not integrated'):
            ta.action_function('squeeze')

    async def test_missing_squeeze_adapter_never_connects(self):
        config, book = fixture('squeeze')
        args = SimpleNamespace(config='unused', book='unused', command='run', execute=True)
        with patch.object(ta, 'read_json', side_effect=[config, book]), \
             patch.object(ta, 'connect', new_callable=AsyncMock) as connect:
            with self.assertRaisesRegex(ValueError, 'not integrated'):
                await ta.dispatch(args)
            connect.assert_not_awaited()

    async def test_offline_run_does_not_connect(self):
        config, book = fixture()
        args = SimpleNamespace(config='unused', book='unused', command='run', execute=False)
        with patch.object(ta, 'read_json', side_effect=[config, book]), \
             patch.object(ta, 'connect', new_callable=AsyncMock) as connect:
            await ta.dispatch(args)
            connect.assert_not_awaited()

    async def test_stop_failure_is_logged_and_original_fault_preserved(self):
        config, book = fixture()
        io = FakeIO(config)
        io.fail_at = 1
        io.stop = AsyncMock(side_effect=RuntimeError('stop unavailable'))
        events = []
        with self.assertRaisesRegex(RuntimeError, 'planning failed'):
            await ta.execute(book, config, io, AsyncMock(),
                             lambda step, **data: events.append((step, data)))
        self.assertEqual(events[-2][0], 'stop_all_failed')
        self.assertIn('stop unavailable', events[-2][1]['error'])
        self.assertEqual(events[-1][0], 'failed')


if __name__ == '__main__':
    unittest.main()
