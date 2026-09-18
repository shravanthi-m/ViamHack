import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from primitives.types import Context
from runtime.__main__ import dispatch, parser
from runtime.config import DEFAULT_CONFIG, ROOT, read_json
from runtime.orchestrator import run_plan, validate_plan


def fixture():
    config = read_json(DEFAULT_CONFIG)
    config['calibrated'] = True
    config['workspace_mm'] = {'x': [-100, 100], 'y': [-100, 100], 'z': [0, 300]}
    return read_json(ROOT / 'demos/fixed.json'), config


class PlanTests(unittest.TestCase):
    def test_reject_unknown_tools_arguments_ids_and_references(self):
        plan, config = fixture()
        mutations = (
            lambda p: p['steps'][0].update(tool='eval'),
            lambda p: p['steps'][0]['args'].update(torque=100),
            lambda p: p['steps'][0]['args'].update(object_id='unknown'),
            lambda p: p['steps'][1].update(id='cup'),
            lambda p: p['steps'][2]['args'].update(source={'$ref': 'coconut'}),
            lambda p: p['steps'][4]['args'].update(source={'$ref': 'pour_coffee'}),
            lambda p: p['steps'][8]['args'].update(duration_s=float('nan')),
            lambda p: p['steps'][8]['args'].update(duration_s=100),
        )
        for change in mutations:
            with self.subTest(change=change):
                altered = copy.deepcopy(plan)
                change(altered)
                with self.assertRaises(ValueError):
                    validate_plan(altered, config)

    def test_uncalibrated_mock_allowed_but_execution_rejected(self):
        plan, _ = fixture()
        config = read_json(DEFAULT_CONFIG)
        validate_plan(plan, config)
        with self.assertRaises(ValueError):
            validate_plan(plan, config, execute=True)

    def test_explicit_pose_bounds_checked_before_execution(self):
        _, config = fixture()
        plan = dict(version=1, instruction='Move to supplied pose', steps=[
            dict(id='move', tool='go_to_pose', args={'pose': dict(
                x=101, y=0, z=100, o_x=0, o_y=0, o_z=1, theta=0)})])
        with self.assertRaises(ValueError):
            validate_plan(plan, config, execute=True)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.plan, self.config = fixture()
        self.robot = AsyncMock()
        self.calls = []

        async def localize(ctx, *, object_id):
            self.calls.append(('localize', object_id))
            return dict(object_id=object_id, frame='world', pose=dict(
                x=0, y=0, z=100, o_x=0, o_y=0, o_z=1, theta=0))

        def action(name):
            async def invoke(ctx, **args):
                self.calls.append((name, args))
                return {}
            return invoke

        self.handlers = {name: action(name) for name in
                         ('pour', 'pick_up', 'insert_into', 'stir', 'place_back')}
        self.handlers['localize'] = localize

    async def execute(self):
        return await run_plan(self.plan, Context(self.config, self.robot),
                              execute=True, runs=self.temp.name, handlers=self.handlers)

    def result(self):
        return read_json(next(Path(self.temp.name).glob('*/result.json')))

    async def test_mock_resolves_references_without_calling_hardware_or_handlers(self):
        path = await run_plan(self.plan, Context(read_json(DEFAULT_CONFIG), self.robot),
                              runs=self.temp.name, handlers=self.handlers)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.robot.mock_calls, [])
        result = read_json(path / 'result.json')
        self.assertEqual(result['mode'], 'mock')
        self.assertEqual(len(result['results']), 10)
        events = [json.loads(line) for line in (path / 'events.jsonl').read_text().splitlines()]
        pour = next(e for e in events if e.get('tool') == 'pour')
        self.assertEqual(pour['args']['source']['object_id'], 'coffee')

    async def test_physical_dispatch_passes_localized_targets_in_order(self):
        await self.execute()
        self.assertEqual([c[0] for c in self.calls], [s['tool'] for s in self.plan['steps']])
        self.assertEqual(self.calls[2][1]['source']['object_id'], 'coffee')
        self.assertEqual(self.calls[4][1]['source']['object_id'], 'coconut_water')
        self.assertEqual(self.result()['status'], 'completed')
        self.robot.stop_all.assert_not_awaited()

    async def test_missing_implementation_fails_before_connection(self):
        config_path = Path(self.temp.name) / 'config.json'
        config_path.write_text(json.dumps(self.config))
        args = parser().parse_args(['--config', str(config_path), 'run', '--execute'])
        with patch('runtime.__main__.connect', new_callable=AsyncMock) as connect:
            with self.assertRaisesRegex(ValueError, 'implementations missing'):
                await dispatch(args)
            connect.assert_not_awaited()

    async def test_bad_localization_stops_before_any_motion(self):
        original = self.handlers['localize']
        async def outside(ctx, **args):
            result = await original(ctx, **args)
            result['pose']['x'] = 101
            return result
        self.handlers['localize'] = outside
        with self.assertRaises(ValueError):
            await self.execute()
        self.assertEqual(len(self.calls), 1)
        self.robot.stop_all.assert_awaited_once()
        self.assertEqual(self.result()['status'], 'aborted')

    async def test_failure_never_continues_or_retries(self):
        self.handlers['pour'] = AsyncMock(side_effect=RuntimeError('grasp failed'))
        with self.assertRaisesRegex(RuntimeError, 'grasp failed'):
            await self.execute()
        self.handlers['pour'].assert_awaited_once()
        self.assertEqual(len(self.calls), 2)
        self.robot.stop_all.assert_awaited_once()
        self.assertEqual(self.result()['status'], 'aborted')

    async def test_cancellation_stops_and_preserves_evidence(self):
        self.handlers['pour'] = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.execute()
        self.robot.stop_all.assert_awaited_once()
        self.assertTrue(self.result()['stop_requested'])

    async def test_timeout_stops_and_preserves_original_error_if_stop_fails(self):
        async def blocked(ctx, **args):
            await asyncio.sleep(10)
        self.handlers['pour'] = blocked
        self.config['limits']['primitive_timeout_s'] = 0.01
        self.robot.stop_all.side_effect = RuntimeError('disconnected')
        with self.assertRaises(TimeoutError):
            await self.execute()
        result = self.result()
        self.assertEqual(result['status'], 'aborted')
        self.assertIn('TimeoutError', result['error'])
        self.assertIn('disconnected', result['stop_error'])

    async def test_invalid_handler_result_is_failure(self):
        self.handlers['pour'] = AsyncMock(return_value=False)
        with self.assertRaisesRegex(ValueError, 'JSON object'):
            await self.execute()
        self.robot.stop_all.assert_awaited_once()
