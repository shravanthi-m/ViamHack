"""Fixed-task admission and failure behavior; no hardware connections."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from runtime import fixed_tasks
from tests.test_putback import config, RecordingIO, ORIGIN
from tests.test_teach_replay import POSE


class FixedTaskTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.robot = AsyncMock()
        self.io = RecordingIO()
        self.connect = self.enterContext(patch('runtime.connection.connect', AsyncMock(return_value=self.robot)))
        self.enterContext(patch('runtime.__main__.check_resources'))
        self.enterContext(patch('runtime.fixed_tasks.ViamIO', return_value=self.io))
        self.validation = self.enterContext(patch('runtime.fixed_tasks.validate', return_value=(config(), {})))
        self.enterContext(patch('runtime.fixed_tasks.poses', return_value={
            'hover': {**POSE, 'z': POSE['z'] + 60}, 'place': POSE, 'origin': ORIGIN}))

    async def run_task(self, task, answers):
        return await fixed_tasks.execute(task, Path('pack'), 'config',
                                        ask_fn=AsyncMock(side_effect=answers), runs=self.temp.name)

    async def test_unknown_reset_identity_never_connects(self):
        with self.assertRaisesRegex(ValueError, 'Unknown held object'):
            await self.run_task('reset', ['unknown'])
        self.connect.assert_not_awaited()

    async def test_declined_gate_never_connects(self):
        for task, answers in [('reset', ['empty', 'no']), ('shake', ['no'])]:
            with self.subTest(task=task), self.assertRaises(RuntimeError):
                await self.run_task(task, answers)
        self.connect.assert_not_awaited()

    async def test_pack_drift_during_gate_never_connects(self):
        self.validation.side_effect = [(config(), {}), ValueError('Pack changed')]
        with self.assertRaisesRegex(ValueError, 'Pack changed'):
            await self.run_task('reset', ['empty', 'yes'])
        self.connect.assert_not_awaited()

    async def test_empty_reset_only_goes_home_and_never_opens(self):
        await self.run_task('reset', ['empty', 'yes', 'yes'])
        self.assertEqual(self.io.poses, [ORIGIN])
        self.assertNotIn('open', self.io.calls)
        self.robot.close.assert_awaited_once()

    async def test_held_state_mismatch_stops_without_motion(self):
        self.io.held = True
        with self.assertRaisesRegex(RuntimeError, 'disagrees'):
            await self.run_task('reset', ['empty', 'yes'])
        self.assertEqual(self.io.poses, [])
        self.assertNotIn('open', self.io.calls)
        self.assertIn('stop', self.io.calls)
        self.robot.close.assert_awaited_once()

    async def test_loaded_reset_requires_support_before_release(self):
        self.io.held = True
        with self.assertRaises(RuntimeError):
            await self.run_task('reset', ['pitcher', 'yes', 'no'])
        self.assertEqual(self.io.poses[-1], POSE)
        self.assertNotIn('open', self.io.calls)
        self.assertNotIn(ORIGIN, self.io.poses)
        self.assertIn('stop', self.io.calls)

    async def test_loaded_reset_returns_then_homes(self):
        self.io.held = True
        await self.run_task('reset', ['coconut_water', 'yes', 'yes', 'yes'])
        self.assertEqual(self.io.poses, [{**POSE, 'z': POSE['z'] + 60}, POSE,
                                       {**POSE, 'z': POSE['z'] + 60}, ORIGIN])
        self.assertEqual(self.io.calls.count('open'), 1)

    async def test_shake_empty_hand_never_calls_primitive(self):
        with patch('shake_full.shake', new_callable=AsyncMock) as shake:
            with self.assertRaisesRegex(RuntimeError, 'verified held'):
                await self.run_task('shake', ['yes'])
            shake.assert_not_awaited()
        self.assertIn('stop', self.io.calls)

    async def test_shake_uses_existing_primitive_and_never_opens(self):
        self.io.held = True
        with patch('shake_full.shake', AsyncMock(return_value={'holding_after': True})) as shake:
            await self.run_task('shake', ['yes', 'yes'])
            self.assertEqual(shake.call_args.kwargs, {'duration_s': 3})
        self.assertNotIn('open', self.io.calls)
        self.assertEqual(self.io.poses, [])

    async def test_cancellation_stops_and_closes_without_release(self):
        self.io.held = True
        with patch('shake_full.shake', AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await self.run_task('shake', ['yes'])
        self.assertIn('stop', self.io.calls)
        self.assertNotIn('open', self.io.calls)
        self.robot.close.assert_awaited_once()

    async def test_lost_object_after_shake_stops_without_release(self):
        self.io.held = True
        async def drop(*args, **kwargs):
            self.io.held = False
            return {'holding_after': False}
        with patch('shake_full.shake', side_effect=drop):
            with self.assertRaisesRegex(RuntimeError, 'no longer held'):
                await self.run_task('shake', ['yes'])
        self.assertIn('stop', self.io.calls)
        self.assertNotIn('open', self.io.calls)


class ShakeAdmissionTests(unittest.TestCase):
    def test_pickup_needs_correct_reviewed_book(self):
        from tests.test_taught_actions import fixture
        cfg, book = fixture()
        cfg['taught_shake_book'] = 'runs/test.json'
        with patch('runtime.fixed_tasks.read_json', return_value=book):
            self.assertEqual(fixed_tasks.shaker_book(cfg), book)
            book['station_verified'] = False
            with self.assertRaisesRegex(ValueError, 'station'):
                fixed_tasks.shaker_book(cfg)
        cfg.pop('taught_shake_book')
        with self.assertRaisesRegex(ValueError, 'taught_shake_book'):
            fixed_tasks.shaker_book(cfg)

    def test_explicit_root_settings_required(self):
        import shake_full
        cfg = config()
        with patch('runtime.fixed_tasks.verify', return_value={}), \
             patch('runtime.fixed_tasks.read_json', return_value=cfg):
            cfg.setdefault('primitive_settings', {}).pop('shake', None)
            with self.assertRaisesRegex(ValueError, 'explicitly configure'):
                fixed_tasks.validate('shake', 'pack', 'config')
            cfg['primitive_settings']['shake'] = dict(shake_full.DEFAULT_SETTINGS)
            fixed_tasks.validate('shake', 'pack', 'config')
            cfg['primitive_settings']['shake']['require_holding'] = False
            with self.assertRaisesRegex(ValueError, 'require_holding'):
                fixed_tasks.validate('shake', 'pack', 'config')


class PickupUITests(unittest.IsolatedAsyncioTestCase):
    async def test_full_task_reuses_taught_flow_and_root_action(self):
        from tests.test_taught_actions import fixture, FakeIO
        from runtime.config import read_json
        import shake_full
        cfg, book = fixture()
        io, robot = FakeIO(cfg), AsyncMock()
        with tempfile.TemporaryDirectory() as directory, \
             patch('runtime.fixed_tasks.validate', return_value=(cfg, {})), \
             patch('runtime.fixed_tasks.shaker_book', return_value=book), \
             patch('runtime.connection.connect', AsyncMock(return_value=robot)), \
             patch('runtime.__main__.check_resources'), \
             patch('runtime.taught_actions.ActionIO', return_value=io), \
             patch('runtime.taught_actions.execute', new_callable=AsyncMock) as execute:
            result = await fixed_tasks.execute('shaker', 'pack', 'config',
                       ask_fn=AsyncMock(side_effect=['yes', 'yes']), runs=directory)
            execute.assert_awaited_once()
            self.assertIs(execute.call_args.args[3], shake_full.shake)
            self.assertEqual(read_json(result / 'book.json'), book)
            self.assertEqual(read_json(result / 'result.json')['status'], 'completed')
            robot.close.assert_awaited_once()

    async def test_declined_pickup_gate_never_connects(self):
        from tests.test_taught_actions import fixture
        cfg, book = fixture()
        with patch('runtime.fixed_tasks.validate', return_value=(cfg, {})), \
             patch('runtime.fixed_tasks.shaker_book', return_value=book), \
             patch('runtime.connection.connect', new_callable=AsyncMock) as connect:
            with self.assertRaises(RuntimeError):
                await fixed_tasks.execute('shaker', 'pack', 'config',
                    ask_fn=AsyncMock(return_value='no'), runs='unused')
            connect.assert_not_awaited()
