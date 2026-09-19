"""Fixed UI dispatch and physical admission, tested without robot connections."""
import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, patch

from runtime.observer_demo import ClaudiaDemo, intent


class IntentTests(unittest.TestCase):
    def test_claudia_drink_and_scene_requests(self):
        for text in ('Claudia, help pour a drink', 'Hey Claudia, pour a drink please!',
                     'Claudia can you help pour a drink?', 'The signature pour', 'Pour signature drink'):
            self.assertEqual(intent(text), 'signature')
        self.assertEqual(intent('Claudia, what do you see?'), 'scene')
        self.assertEqual(intent('Reset station'), 'reset')
        self.assertEqual(intent('Shake held object'), 'shake')
        self.assertEqual(intent('Pick up and shake'), 'shaker')

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
        for task in ('reset', 'shake', 'shaker'):
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
            self.assertEqual(demo.state()['error'], 'RuntimeError: stopped')
            with self.assertRaisesRegex(ValueError, 'reset'):
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


if __name__ == '__main__':
    unittest.main()
