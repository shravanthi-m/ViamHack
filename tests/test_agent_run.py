import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from runtime.__main__ import dispatch, parser
from runtime.config import DEFAULT_CONFIG, ROOT, read_json
from runtime.orchestrator import describe_plan, missing_implementations

RUNNABLE = {
    'version': 1,
    'instruction': 'Home, photograph the scene, shake briefly, home again.',
    'steps': [
        {'id': 'home', 'tool': 'go_to_origin', 'args': {}},
        {'id': 'look', 'tool': 'capture', 'args': {'view': 'overhead'}},
        {'id': 'shake_it', 'tool': 'shake', 'args': {'duration_s': 3}},
        {'id': 'home_again', 'tool': 'go_to_origin', 'args': {}},
    ],
}


def config():
    stored = read_json(DEFAULT_CONFIG)
    stored.update(calibrated=True, workspace_mm={'x': [-500, 500], 'y': [-500, 500], 'z': [0, 500]})
    stored.setdefault('primitive_settings', {}).setdefault('camera', {})['views'] = {
        'wrist': 'cam', 'overhead': 'overhead-cam'}
    return stored


class PlanReporting(unittest.TestCase):
    def test_missing_implementations_names_every_unbuilt_tool(self):
        missing = missing_implementations(read_json(ROOT / 'demos/fixed.json'))
        self.assertIn('localize', missing)
        self.assertIn('pour', missing)
        self.assertEqual(missing, sorted(missing))

    def test_a_fully_implemented_plan_reports_nothing_missing(self):
        self.assertEqual(missing_implementations(RUNNABLE), [])

    def test_describe_plan_lists_every_step_and_resolves_references(self):
        described = describe_plan(read_json(ROOT / 'demos/fixed.json'))
        self.assertIn('10 steps:', described)
        for step in read_json(ROOT / 'demos/fixed.json')['steps']:
            self.assertIn(step['id'], described)
        self.assertIn('pour(source=coffee, target=cup)', described)


class AgentRun(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / 'config.json'
        self.config_path.write_text(json.dumps(config()))

    def args(self, plan, *flags):
        path = self.root / 'plan.json'
        path.write_text(json.dumps(plan))
        return parser().parse_args(['--config', str(self.config_path), 'agent-run', str(path),
                                    '--runs', str(self.root / 'runs'), *flags])

    async def test_an_unimplemented_plan_fails_before_the_machine(self):
        with patch('runtime.__main__.connect', new_callable=AsyncMock) as connect:
            with self.assertRaisesRegex(ValueError, 'implementations missing'):
                await dispatch(self.args(read_json(ROOT / 'demos/fixed.json')))
            connect.assert_not_awaited()
        self.assertFalse((self.root / 'runs').exists())

    async def test_an_invalid_plan_fails_before_the_machine(self):
        broken = json.loads(json.dumps(RUNNABLE))
        broken['steps'][1]['args']['view'] = 'nose'
        with patch('runtime.__main__.connect', new_callable=AsyncMock) as connect:
            with self.assertRaises(ValueError):
                await dispatch(self.args(broken))
            connect.assert_not_awaited()

    async def test_refusing_the_gate_aborts_before_connecting(self):
        # confirm() binds ask as a default argument, so the gate itself is what to patch.
        refuse = AsyncMock(side_effect=RuntimeError('Operator did not confirm the phase gate'))
        with patch('runtime.demonstrations.confirm', refuse):
            with patch('runtime.__main__.connect', new_callable=AsyncMock) as connect:
                with self.assertRaisesRegex(RuntimeError, 'did not confirm'):
                    await dispatch(self.args(RUNNABLE))
                connect.assert_not_awaited()
        refuse.assert_awaited_once()

    async def test_confirming_the_gate_connects_and_executes_once(self):
        with patch('runtime.demonstrations.confirm', new_callable=AsyncMock):
            with patch('runtime.__main__.connect', new_callable=AsyncMock) as connect:
                with patch('runtime.__main__.check_resources'):
                    with patch('runtime.__main__.run_plan', new_callable=AsyncMock) as run:
                        run.return_value = self.root / 'runs' / 'fake'
                        await dispatch(self.args(RUNNABLE))
        connect.assert_awaited_once()
        # One run, on the machine: no mock stage precedes it.
        self.assertEqual([call.kwargs.get('execute', False) for call in run.await_args_list], [True])

    async def test_nothing_runs_before_the_gate(self):
        order = []
        gate = AsyncMock(side_effect=lambda *a, **k: order.append('gate'))
        async def plan_run(*a, **k):
            order.append('execute' if k.get('execute') else 'mock')
            return self.root / 'runs' / 'fake'
        with patch('runtime.demonstrations.confirm', gate):
            with patch('runtime.__main__.connect', new_callable=AsyncMock):
                with patch('runtime.__main__.check_resources'):
                    with patch('runtime.__main__.run_plan', plan_run):
                        await dispatch(self.args(RUNNABLE))
        self.assertEqual(order, ['gate', 'execute'])


class ValidateExecutable(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'config.json'
        self.path.write_text(json.dumps(config()))

    def args(self, plan, *flags):
        return parser().parse_args(['--config', str(self.path), 'validate', str(plan), *flags])

    async def test_validate_alone_accepts_an_unimplemented_plan(self):
        await dispatch(self.args(ROOT / 'demos/fixed.json'))

    async def test_executable_flag_rejects_an_unimplemented_plan(self):
        with self.assertRaisesRegex(ValueError, 'not implemented'):
            await dispatch(self.args(ROOT / 'demos/fixed.json', '--executable'))

    async def test_executable_flag_accepts_a_runnable_plan(self):
        plan = Path(self.temp.name) / 'plan.json'
        plan.write_text(json.dumps(RUNNABLE))
        await dispatch(self.args(plan, '--executable'))


if __name__ == '__main__':
    unittest.main()
