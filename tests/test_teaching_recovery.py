import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from primitives.types import Context
from runtime.config import read_json
from runtime.demonstrations import write
from runtime.teaching import teach_skill, record_phase
from runtime.teaching_connection import TeachingConnection
from tests.test_teach_replay import FakeIO, config, yes


class TeachingRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_readers_share_one_reconnection_and_retry(self):
        cfg = config()
        old, new = (SimpleNamespace(close=AsyncMock()) for _ in range(2))
        broken = SimpleNamespace(ctx=Context(cfg, old), state=AsyncMock(side_effect=TimeoutError('lost')))
        healthy = SimpleNamespace(ctx=Context(cfg, new), state=AsyncMock(return_value={'sample': 'fresh'}))
        connector = AsyncMock(side_effect=[old, new])
        io = TeachingConnection(cfg, connector=connector, delay_s=0)
        with patch('runtime.teaching_connection.ViamIO', side_effect=lambda ctx: broken if ctx.robot is old else healthy):
            results = await asyncio.gather(io.state(), io.state())
        self.assertEqual(results, [{'sample': 'fresh'}]*2)
        self.assertEqual(connector.await_count, 2)
        self.assertEqual(io.recoveries, 1)
        old.close.assert_awaited_once()
        await io.aclose()
        new.close.assert_awaited_once()

    async def test_validation_failure_is_not_retried(self):
        io = TeachingConnection(config(), connector=AsyncMock(), delay_s=0)
        io.io = SimpleNamespace(state=AsyncMock(side_effect=ValueError('outside workspace')))
        with self.assertRaisesRegex(ValueError, 'outside workspace'):
            await io.state()
        io.connector.assert_not_awaited()

    async def test_operator_abort_cancels_pending_reads(self):
        io = FakeIO()
        async def blocked():
            await asyncio.Event().wait()
        io.state = blocked
        async def abort(prompt):
            if 'when transport' in prompt:
                raise RuntimeError('Operator aborted')
            return ''
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(RuntimeError, 'Operator aborted'):
                await asyncio.wait_for(record_phase(io, Path(root), 'transport', [], 0, 5, abort), 1)


class ResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupted_and_legacy_episodes_resume_without_reteaching_lift(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory() as root:
                async def interrupt(prompt):
                    if 'when transport is complete' in prompt:
                        raise RuntimeError('process interrupted')
                    return await yes(prompt)
                with self.assertRaises(RuntimeError):
                    await teach_skill('coconut_water', FakeIO(), config(), root=root, ask_fn=interrupt)
                directory = next((Path(root)/'coconut_water').glob('episode_*'))
                meta = read_json(directory/'metadata.json')
                self.assertEqual(meta['completed_phases'], ['lift'])
                if legacy:
                    meta.pop('completed_phases')
                    meta['status'] = 'failed'
                    write(directory/'metadata.json', meta)
                io = FakeIO()
                result = await teach_skill('coconut_water', io, config(), root=root, resume='latest', ask_fn=yes)
                self.assertEqual(result, directory)
                self.assertNotIn('capture_before', io.calls)
                self.assertNotIn('capture_grasp', io.calls)
                events = [json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]
                self.assertEqual(sum(e['step'] == 'record_lift' for e in events), 1)
                self.assertTrue(list((directory/'interruptions').glob('*/metadata.json')))
                self.assertEqual(read_json(directory/'metadata.json')['status'], 'complete')
                self.assertEqual(len(list((Path(root)/'coconut_water').glob('episode_*'))), 1)

    async def test_gap_keeps_raw_data_but_repeats_only_affected_phase(self):
        io = FakeIO()
        io.phase = None
        io.recoveries = 0
        state = io.state
        async def recover_once():
            if io.phase == 'transport' and not io.recoveries:
                io.recoveries += 1
            return await state()
        io.state = recover_once
        with tempfile.TemporaryDirectory() as root:
            directory = await teach_skill('coconut_water', io, config(), root=root, ask_fn=yes)
            raw = [json.loads(line) for line in (directory/'samples.jsonl').read_text().splitlines()]
            selected = read_json(directory/'trajectory.json')['waypoints']
            self.assertEqual(len({r['attempt'] for r in raw if r['phase'] == 'transport'}), 2)
            self.assertEqual(len({r['attempt'] for r in selected if r['phase'] == 'transport'}), 1)
            self.assertEqual(len({r['attempt'] for r in selected if r['phase'] == 'lift'}), 1)
