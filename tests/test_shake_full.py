"""Offline contracts for the standalone direct-control shake script."""
import asyncio
import json
import math
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, mock_open, patch

import shake_full
from primitives.types import Context
from runtime.config import DEFAULT_CONFIG, read_json
from viam.proto.component.arm import JointPositions


ENDPOINTS = {'bottom': [0.0] * 6, 'top': [8.0, -4.0, 2.0, 0.0, 0.0, 0.0]}


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Arm:
    def __init__(self, clock, latency=0.125, readback_latency=0.0, error=0.0):
        self.clock = clock
        self.latency = latency
        self.readback_latency = readback_latency
        self.error = error
        self.moves = []
        self.at = ENDPOINTS['bottom']

    async def move_to_joint_positions(self, positions, *, extra, timeout):
        self.moves.append((list(positions.values), extra, timeout))
        self.clock.now += self.latency
        self.at = list(positions.values)

    async def get_joint_positions(self, *, timeout):
        self.clock.now += self.readback_latency
        return JointPositions(values=[v + self.error for v in self.at])


class DirectShakeTests(unittest.IsolatedAsyncioTestCase):
    async def run_strokes(self, *, duration=1.0, latency=0.125,
                          readback_latency=0.0, error=0.0, mode='direct',
                          endpoints=None, frequency=2.0):
        self.clock = Clock()
        self.arm = Arm(self.clock, latency, readback_latency, error)
        config = read_json(DEFAULT_CONFIG)
        self.timeout = config['limits']['primitive_timeout_s']
        tuning = shake_full.shake_settings({'primitive_settings': {'shake': {
            'control_mode': mode, 'frequency_hz': frequency}}})
        with patch.object(shake_full, 'time', self.clock), \
             patch.object(shake_full, 'asyncio', SimpleNamespace(sleep=self.clock.sleep)):
            return await shake_full.strokes(Context(config, robot=object()), self.arm,
                                            ENDPOINTS if endpoints is None else endpoints,
                                            tuning, duration)

    async def test_one_direct_move_per_half_stroke_and_alternating_endpoints(self):
        result = await self.run_strokes()
        self.assertEqual(result['strokes'], 4)
        self.assertEqual(result['move_calls'], 4)
        self.assertEqual([m[0] for m in self.arm.moves],
                         [ENDPOINTS['top'], ENDPOINTS['bottom']] * 2)
        for _, extra, timeout in self.arm.moves:
            self.assertEqual(extra, {'direct': True, 'speed_d': 90.0, 'waitAtEnd': True})
            self.assertEqual(timeout, self.timeout)

    async def test_motion_and_readback_time_count_toward_period(self):
        result = await self.run_strokes(readback_latency=0.0625)
        self.assertEqual(result['frequency_hz'], 2.0)
        self.assertEqual(result['motion_call_time_s'], 0.5)
        self.assertEqual(result['joint_readback_time_s'], 0.25)
        self.assertEqual(result['pacing_sleep_s'], 0.25)
        self.assertEqual(result['overrun_strokes'], 0)

    async def test_slow_arm_gets_no_extra_sleep_or_catch_up_commands(self):
        result = await self.run_strokes(latency=0.5)
        self.assertEqual(result['strokes'], 2)
        self.assertEqual(result['frequency_hz'], 1.0)
        self.assertEqual(result['overrun_strokes'], 2)
        self.assertEqual(self.clock.sleeps, [])

    async def test_waits_for_inflight_stroke_then_starts_no_more(self):
        result = await self.run_strokes(duration=0.25, latency=0.5)
        self.assertEqual(result['move_calls'], 1)
        self.assertEqual(result['duration_s'], 0.5)

    async def test_sampled_comparison_keeps_waypoints(self):
        result = await self.run_strokes(mode='sampled', latency=0.03125)
        self.assertEqual(result['move_calls'], 16)
        self.assertEqual(result['strokes'], 4)
        self.assertTrue(all(not m[1]['direct'] for m in self.arm.moves))
        for points, _, _ in self.arm.moves:
            for point, lo, hi in zip(points, ENDPOINTS['bottom'], ENDPOINTS['top']):
                self.assertTrue(min(lo, hi) <= point <= max(lo, hi))

    async def test_partial_sampled_stroke_is_not_counted(self):
        result = await self.run_strokes(mode='sampled', duration=0.125, latency=0.0625)
        self.assertEqual(result['move_calls'], 2)
        self.assertEqual(result['strokes'], 0)

    async def test_endpoint_miss_fails_before_reversing(self):
        with self.assertRaisesRegex(RuntimeError, 'endpoint missed'):
            await self.run_strokes(error=10.0)
        self.assertEqual(len(self.arm.moves), 1)

    async def test_nonfinite_readback_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'invalid joint positions'):
            await self.run_strokes(error=math.nan)
        self.assertEqual(len(self.arm.moves), 1)

    async def test_invalid_endpoint_vectors_never_move(self):
        for ends in ({'top': [], 'bottom': []},
                     {'top': [0], 'bottom': [0, 1]},
                     {'top': [math.nan], 'bottom': [0]}):
            with self.subTest(ends=ends), self.assertRaises(ValueError):
                await self.run_strokes(endpoints=ends)
            self.assertEqual(self.arm.moves, [])

    async def test_duration_bounds_checked_before_motion(self):
        for duration in (0, -1, 16, math.nan):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                await self.run_strokes(duration=duration)
            self.assertEqual(self.arm.moves, [])

    def test_mode_validation(self):
        with self.assertRaisesRegex(ValueError, 'control_mode'):
            shake_full.shake_settings({'primitive_settings': {'shake': {'control_mode': 'servo'}}})

    async def test_move_failure_propagates_without_retry(self):
        with patch.object(Arm, 'move_to_joint_positions', new_callable=AsyncMock,
                          side_effect=RuntimeError('driver fault')) as move:
            with self.assertRaisesRegex(RuntimeError, 'driver fault'):
                await self.run_strokes()
            self.assertEqual(move.await_count, 1)

    async def test_script_stops_and_closes_on_failure_or_cancellation(self):
        for failure in (RuntimeError('motion failed'), asyncio.CancelledError()):
            robot = SimpleNamespace(stop_all=AsyncMock(), close=AsyncMock())
            with patch('builtins.open', mock_open(read_data=json.dumps(read_json(DEFAULT_CONFIG)))), \
                 patch.object(shake_full, 'connect', AsyncMock(return_value=robot)), \
                 patch.object(shake_full, 'run_sequence', AsyncMock(side_effect=failure)):
                with self.assertRaises(type(failure)):
                    await shake_full.main()
            robot.stop_all.assert_awaited_once_with()
            robot.close.assert_awaited_once_with()

    async def test_stop_failure_preserves_original_failure_and_closes(self):
        robot = SimpleNamespace(stop_all=AsyncMock(side_effect=RuntimeError('stop failed')),
                                close=AsyncMock())
        with patch('builtins.open', mock_open(read_data=json.dumps(read_json(DEFAULT_CONFIG)))), \
             patch.object(shake_full, 'connect', AsyncMock(return_value=robot)), \
             patch.object(shake_full, 'run_sequence', AsyncMock(side_effect=ValueError('original'))):
            with self.assertRaisesRegex(ValueError, 'original'):
                await shake_full.main()
        robot.close.assert_awaited_once_with()


if __name__ == '__main__':
    unittest.main()
