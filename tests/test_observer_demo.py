"""Fixed UI dispatch and physical admission, tested without robot connections."""
import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, patch

from runtime.observer_demo import ClaudiaDemo, intent, failure_message


class IntentTests(unittest.TestCase):
    def test_collision_failure_is_actionable_without_exposing_sdk_details(self):
        message = failure_message(RuntimeError('private-host: all IK solutions failed constraints. '
                                               'self-collision constraint: arm:wrist_link and cam_origin'))
        self.assertIn('wrist and camera', message)
        self.assertIn('pour may be partial', message)
        self.assertIn('Reset', message)
        self.assertNotIn('private-host', message)
        self.assertNotIn('private-host', failure_message(RuntimeError('private-host network error')))
        emergency = failure_message(RuntimeError('private-host: xArm: Emergency Stop Button Pushed In;'))
        self.assertIn('emergency-stop button is pressed', emergency)
        self.assertNotIn('private-host', emergency)

    def test_claudia_drink_and_scene_requests(self):
        for text in ('Claudia, help pour a drink', 'Hey Claudia, pour a drink please!',
                     'Claudia can you help pour a drink?', 'The signature pour', 'Pour signature drink'):
            self.assertEqual(intent(text), 'signature')
        self.assertEqual(intent('Claudia, what do you see?'), 'scene')
        self.assertEqual(intent('Reset station'), 'reset')
        self.assertEqual(intent('Shake held object'), 'shake')

    def test_unsupported_modifications_never_select_script(self):
        for text in ('Do not pour a drink', 'pour a drink then shake it',
                     'Claudia, pour 500ml', 'run /tmp/arbitrary.py', 'make coffee'):
            self.assertEqual(intent(text), 'unsupported')
        for text in (None, {}, '', 'a' * 501):
            with self.assertRaises(ValueError):
                intent(text)

    @patch('runtime.observer_demo.verify')
    def test_preview_never_verifies_or_starts_execution(self, verify):
        demo = ClaudiaDemo()
        self.assertEqual(demo.request('Claudia, help pour a drink')['status'], 'preview')
        self.assertIsNone(demo.thread)
        verify.assert_not_called()

    def test_all_fixed_tasks_preview_without_starting_a_worker(self):
        demo = ClaudiaDemo()
        for task in ('reset', 'shake'):
            value = demo.request(task)
            self.assertEqual(value['status'], 'preview')
            self.assertEqual(value['intent'], task)
            self.assertIsNone(demo.thread)


class ExecutionTests(unittest.TestCase):
    def enabled(self):
        with patch('runtime.observer_demo.sys.stdin.isatty', return_value=True), patch('runtime.observer_demo.verify'):
            return ClaudiaDemo(pack='unused-pack', config_path='config/demo.json', enabled=True)

    def test_execution_needs_pack_and_operator_terminal(self):
        with self.assertRaises(ValueError):
            ClaudiaDemo(enabled=True)
        with patch('runtime.observer_demo.sys.stdin.isatty', return_value=False):
            with self.assertRaisesRegex(ValueError, 'interactive'):
                ClaudiaDemo(pack='unused', config_path='config/demo.json', enabled=True)

    def test_voice_review_and_banter_never_start_execution(self):
        demo = self.enabled()
        with patch('runtime.observer_demo.verify') as verify:
            result = demo.request('Claudia, pour a drink', review_only=True)
            self.assertEqual(result['status'], 'review')
            for text in ('Hello Claudia', 'Claudia, how are you?', 'Thank you',
                         'Who are you?', 'What can you do?', 'Tell me a joke', 'Bye'):
                self.assertEqual(demo.request(text, review_only=True)['intent'], 'chat')
                self.assertEqual(demo.request(text)['intent'], 'chat')
            self.assertIsNone(demo.thread)
            self.assertIsNone(demo.run_root)
            verify.assert_not_called()
        with self.assertRaises(ValueError):
            demo.request('pour a drink', review_only='false')

    @patch('runtime.observer_demo.verify', side_effect=ValueError('Pack changed'))
    def test_drift_blocks_before_worker_starts(self, verify):
        demo = self.enabled()
        with self.assertRaisesRegex(ValueError, 'Pack changed'):
            demo.request('pour a drink')
        self.assertIsNone(demo.thread)

    def test_duplicate_requests_block_and_shutdown_cancels(self):
        demo = self.enabled()
        entered, cancelled = threading.Event(), threading.Event()
        async def perform():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        with patch.object(demo, '_perform', perform), patch('runtime.observer_demo.verify'):
            demo.request('pour a drink')
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(ValueError, 'in progress'):
                demo.request('pour a drink')
            demo.close()
        self.assertTrue(cancelled.is_set())
        self.assertEqual(demo.state()['status'], 'failed')

    def test_failure_requires_station_reset(self):
        demo = self.enabled()
        with patch.object(demo, '_perform', AsyncMock(side_effect=RuntimeError('stopped'))), patch('runtime.observer_demo.verify'):
            demo.request('pour a drink')
            demo.thread.join(2)
            self.assertEqual(demo.state()['status'], 'failed')
            with self.assertRaisesRegex(ValueError, 'Clear stopped task'):
                demo.request('pour a drink')

    def test_refused_initial_gate_never_connects(self):
        demo = self.enabled()
        with patch('runtime.observer_demo.verify', return_value={'compact': True}), \
             patch('runtime.replay.run_demo', new_callable=AsyncMock) as replay, \
             patch('runtime.demonstrations.confirm', new_callable=AsyncMock, side_effect=RuntimeError('declined')), \
             patch('runtime.connection.connect', new_callable=AsyncMock) as connect:
            with self.assertRaisesRegex(RuntimeError, 'declined'):
                asyncio.run(demo._perform())
        connect.assert_not_awaited()
        self.assertEqual(replay.await_count, 1)
        self.assertIsNone(replay.call_args.args[0])
        self.assertNotIn('execute', replay.call_args.kwargs)

    def test_admitted_request_reuses_replay_with_all_operator_gates(self):
        demo = self.enabled()
        robot = AsyncMock()
        with patch('runtime.observer_demo.verify', return_value={'compact': True}), \
             patch('runtime.replay.run_demo', new_callable=AsyncMock) as replay, \
             patch('runtime.demonstrations.confirm', new_callable=AsyncMock) as confirm, \
             patch('runtime.__main__.check_resources'), \
             patch('runtime.demonstrations.ViamIO') as io, \
             patch('runtime.connection.connect', new_callable=AsyncMock, return_value=robot):
            asyncio.run(demo._perform())
        self.assertEqual(replay.await_count, 2)
        self.assertTrue(replay.call_args.kwargs['execute'])
        self.assertEqual(replay.call_args.kwargs['ask_fn'], demo._ask)
        self.assertEqual(replay.call_args.kwargs['root'], demo.pack / 'demonstrations')
        self.assertTrue(replay.call_args.kwargs['compact'])
        self.assertIs(replay.call_args.args[0], io.return_value)
        confirm.assert_awaited_once()
        robot.close.assert_awaited_once()


class BrowserGateTests(unittest.IsolatedAsyncioTestCase):
    def demo(self):
        with patch('runtime.observer_demo.verify'), patch('runtime.observer_demo.sys.stdin.isatty', return_value=False):
            return ClaudiaDemo(pack='unused', config_path='config/demo.json', enabled=True, browser_gates=True)

    async def test_browser_check_waits_for_matching_answer_and_rejects_replay(self):
        demo = self.demo()
        task = asyncio.create_task(demo._ask('Observed held object?'))
        await asyncio.sleep(0)
        gate = demo.state()['gate_id']
        self.assertFalse(task.done())
        self.assertEqual(demo.state()['prompt'], 'Observed held object?')
        with self.assertRaises(ValueError):
            demo.answer('old-gate', 'empty')
        self.assertFalse(task.done())
        demo.answer(gate, 'empty')
        with self.assertRaises(ValueError):
            demo.answer(gate, 'yes')
        self.assertEqual(await task, 'empty')
        self.assertIsNone(demo.state()['gate_id'])
        second = asyncio.create_task(demo._ask('Paths clear? Type yes:'))
        await asyncio.sleep(0)
        with self.assertRaises(ValueError):
            demo.answer(gate, 'yes')
        demo.answer(demo.state()['gate_id'], 'yes')
        self.assertEqual(await second, 'yes')

    async def test_abort_and_cancellation_clear_pending_check(self):
        demo = self.demo()
        task = asyncio.create_task(demo._ask('Paths clear?'))
        await asyncio.sleep(0)
        demo.answer(demo.state()['gate_id'], 'q')
        with self.assertRaisesRegex(RuntimeError, 'aborted'):
            await task
        self.assertIsNone(demo.state()['gate_id'])
        task = asyncio.create_task(demo._ask('Next check'))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(demo.state()['gate_id'])

    async def test_declined_browser_gate_never_connects(self):
        demo = self.demo()
        with patch('runtime.observer_demo.verify', return_value={'compact': True}), \
             patch('runtime.replay.run_demo', new_callable=AsyncMock), \
             patch('runtime.connection.connect', new_callable=AsyncMock) as connect:
            task = asyncio.create_task(demo._perform())
            await asyncio.sleep(0)
            demo.answer(demo.state()['gate_id'], 'no')
            with self.assertRaisesRegex(RuntimeError, 'did not confirm'):
                await task
            connect.assert_not_awaited()

    async def test_preview_cannot_accept_operator_answers(self):
        demo = ClaudiaDemo(browser_gates=True)
        with self.assertRaises(ValueError):
            demo.answer('any', 'yes')


class BrowserRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.verify = self.enterContext(patch('runtime.observer_demo.verify'))
        self.inspect = self.enterContext(patch('runtime.observer_demo.inspect_station', AsyncMock(
            return_value={'moving': False, 'holding': False, 'at_home': True})))
        self.demo = ClaudiaDemo(pack='unused', config_path='config/demo.json', enabled=True, browser_gates=True)
        self.demo.status, self.demo.failure_id = 'failed', 'stopped-1'

    async def test_home_empty_inspection_clears_without_starting_a_worker(self):
        result = await self.demo.clear_failure('stopped-1', 'empty', True)
        self.assertFalse(result['reset_required'])
        self.assertEqual(self.demo.status, 'idle')
        self.assertIsNone(self.demo.thread)
        self.assertIsNone(self.demo.failure_id)
        with self.assertRaises(ValueError):
            await self.demo.clear_failure('stopped-1', 'empty', True)
        self.assertEqual(self.inspect.await_count, 1)

    async def test_loaded_or_away_from_home_requires_reset_before_pour(self):
        for held, feedback in [('pitcher', {'moving': False, 'holding': True, 'at_home': True}),
                               ('empty', {'moving': False, 'holding': False, 'at_home': False})]:
            self.demo.status, self.demo.failure_id = 'failed', 'stopped-1'
            self.inspect.return_value = feedback
            result = await self.demo.clear_failure('stopped-1', held, True)
            self.assertTrue(result['reset_required'])
            with self.assertRaisesRegex(ValueError, 'Run Reset first'):
                self.demo.request('pour a drink')
        with patch('runtime.fixed_tasks.validate'), patch.object(self.demo, '_perform', AsyncMock()):
            self.demo.request('reset')
            self.demo.thread.join(2)
        self.assertEqual(self.demo.status, 'completed')
        self.assertFalse(self.demo.reset_required)

    async def test_moving_and_mismatched_gripper_keep_failure_block(self):
        for feedback in ({'moving': True, 'holding': False, 'at_home': True},
                         {'moving': False, 'holding': True, 'at_home': True}):
            self.inspect.return_value = feedback
            with self.assertRaises(ValueError):
                await self.demo.clear_failure('stopped-1', 'empty', True)
            self.assertEqual(self.demo.status, 'failed')

    async def test_missing_inspection_unknown_object_and_stale_id_never_connect(self):
        for token, held, inspected in [('stopped-1', 'empty', False), ('stopped-1', 'unknown', True),
                                       ('stopped-1', 'shaker', True), ('old', 'empty', True)]:
            with self.assertRaises(ValueError):
                await self.demo.clear_failure(token, held, inspected)
        self.inspect.assert_not_awaited()

    async def test_disconnection_and_changed_failure_do_not_clear(self):
        self.inspect.side_effect = RuntimeError('private address')
        with self.assertRaisesRegex(ValueError, 'Could not verify') as error:
            await self.demo.clear_failure('stopped-1', 'empty', True)
        self.assertNotIn('private', str(error.exception))
        self.assertEqual(self.demo.status, 'failed')
        async def changed(_):
            self.demo.failure_id = 'stopped-2'
            return {'moving': False, 'holding': False, 'at_home': True}
        self.inspect.side_effect = changed
        with self.assertRaisesRegex(ValueError, 'no longer'):
            await self.demo.clear_failure('stopped-1', 'empty', True)
        self.assertEqual(self.demo.status, 'failed')

    async def test_changed_package_blocks_before_inspection(self):
        self.verify.side_effect = ValueError('Package changed')
        with self.assertRaisesRegex(ValueError, 'Package changed'):
            await self.demo.clear_failure('stopped-1', 'empty', True)
        self.inspect.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
