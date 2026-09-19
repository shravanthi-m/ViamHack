import asyncio
import unittest
from unittest.mock import patch

from primitives import gripper
from primitives.types import Context
from runtime.config import DEFAULT_CONFIG, read_json


def config(**overrides):
    return {**read_json(DEFAULT_CONFIG), **overrides}


class FakeGripper:
    """A UFactory-style gripper: torque through do_command, holding through feedback."""

    def __init__(self, *, torque=5.0, grabs=True, holding=None, force_command=True,
                 reports_holding=True, readback=None, torque_keys=('gripper_torque',)):
        self.torque_value, self.grabs, self.force_command = torque, grabs, force_command
        self.reports_holding, self.readback, self.torque_keys = reports_holding, readback, torque_keys
        self.holding = grabs if holding is None else holding
        self.commands, self.opened = [], 0

    async def do_command(self, command, timeout=None):
        self.commands.append(command)
        if not self.force_command:
            raise RuntimeError('unimplemented: do_command')
        if 'set_gripper_torque' in command:
            self.torque_value = (command['set_gripper_torque'] if self.readback is None
                                 else self.readback)
            return {'ok': True}
        return {key: self.torque_value for key in self.torque_keys}

    async def grab(self, timeout=None):
        return self.grabs

    async def open(self, timeout=None):
        self.opened += 1
        self.holding = False

    async def is_holding_something(self, timeout=None):
        from viam.proto.component.gripper import IsHoldingSomethingResponse
        if not self.reports_holding:
            raise RuntimeError('unimplemented: IsHoldingSomething')
        return IsHoldingSomethingResponse(is_holding_something=self.holding)


class GripperTestCase(unittest.IsolatedAsyncioTestCase):
    async def call(self, coroutine, handle=None, **overrides):
        from viam.components.gripper import Gripper
        self.handle = handle or FakeGripper()
        with patch.object(Gripper, 'from_robot', return_value=self.handle):
            return await coroutine(Context(config(**overrides), robot=object()))


class CloseGripperTests(GripperTestCase):
    async def close(self, force_percent=20.0, handle=None, **overrides):
        return await self.call(lambda ctx: gripper.close_gripper(ctx, force_percent=force_percent),
                               handle, **overrides)

    async def test_sets_the_force_and_reads_it_back_before_closing(self):
        result = await self.close(20.0)
        self.assertIn({'set_gripper_torque': 20.0}, self.handle.commands)
        # Read, set, read: the setting is confirmed, never assumed.
        self.assertEqual([list(c)[0] for c in self.handle.commands],
                         ['get_gripper_torque', 'set_gripper_torque', 'get_gripper_torque'])
        self.assertEqual(result['force_percent'], 20.0)
        self.assertEqual(result['requested_force_percent'], 20.0)
        self.assertTrue(result['holding'])

    async def test_a_force_that_does_not_take_is_a_failure(self):
        handle = FakeGripper(readback=3.0)  # gripper ignores the setting
        with self.assertRaisesRegex(RuntimeError, 'readback is 3.0%'):
            await self.close(20.0, handle)

    async def test_a_gripper_without_force_control_refuses_rather_than_guessing(self):
        handle = FakeGripper(force_command=False)
        with self.assertRaisesRegex(RuntimeError, 'does not implement the torque extension'):
            await self.close(20.0, handle)

    async def test_an_ambiguous_torque_report_is_refused(self):
        # Silence and two readings are both unusable: neither says what the force is.
        for keys in ((), ('gripper_torque', 'motor_torque')):
            with self.subTest(keys=keys):
                with self.assertRaisesRegex(RuntimeError, r'torque values, not a single'):
                    await self.close(20.0, FakeGripper(torque_keys=keys))

    async def test_a_missing_extension_reads_differently_from_an_ambiguous_one(self):
        with self.assertRaisesRegex(RuntimeError, 'does not implement the torque extension'):
            await self.close(20.0, FakeGripper(force_command=False))

    async def test_closing_on_nothing_is_a_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'without reporting an object'):
            await self.close(20.0, FakeGripper(grabs=False))

    async def test_a_grab_that_reports_nothing_held_is_a_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'reports nothing held'):
            await self.close(20.0, FakeGripper(grabs=True, holding=False))

    async def test_force_is_capped_by_the_configured_maximum(self):
        for bad in (0, -5, 31, 101, float('nan'), '20'):
            with self.subTest(force_percent=bad):
                with self.assertRaises(ValueError):
                    await self.close(bad)
        # The ceiling is the team's, and a plan may use all of it.
        result = await self.close(30.0)
        self.assertEqual(result['force_percent'], 30.0)

    async def test_a_raised_ceiling_is_honoured_but_still_bounded(self):
        raised = {'primitive_settings': {'gripper': {'max_force_percent': 60}}}
        result = await self.close(45.0, **raised)
        self.assertEqual(result['force_percent'], 45.0)
        with self.assertRaises(ValueError):
            await self.close(61.0, **raised)

    async def test_settings_are_validated(self):
        for bad in ({'max_force_percent': 0}, {'max_force_percent': 101},
                    {'force_tolerance_percent': 0}, {'force_tolerance_percent': 11},
                    {'require_holding': 'yes'}, {'unknown': 1}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    await self.close(20.0, primitive_settings={'gripper': bad})


class AtomicGripper(FakeGripper):
    def __init__(self, *, speed=2000, response=None, failure=None, closes=True, **kwargs):
        super().__init__(holding=False, **kwargs)
        self.speed, self.response, self.failure, self.closes = speed, response, failure, closes

    async def do_command(self, command, timeout=None):
        self.commands.append(command)
        if 'get_gripper_speed' in command:
            return {'gripper_speed': self.speed}
        if 'grab_with_torque' in command:
            if self.failure:
                raise self.failure
            self.holding = self.closes
            return {} if self.response is None else self.response
        raise AssertionError('Atomic adapter must not issue torque get/set commands')

    async def grab(self, timeout=None):
        raise AssertionError('Atomic adapter must not fall back to Grab')


class AtomicGripperTests(GripperTestCase):
    async def close(self, handle, force=10, **settings):
        return await self.call(lambda ctx: gripper.close_gripper(ctx, force_percent=force), handle,
                               primitive_settings={'gripper': {'force_control': 'ufactory_atomic', **settings}})

    async def test_atomic_force_and_existing_speed_with_holding_evidence(self):
        handle = AtomicGripper(speed=1700)
        result = await self.close(handle)
        self.assertEqual(handle.commands, [{'get_gripper_speed': True},
                         {'grab_with_torque': {'position': 0, 'speed': 1700, 'torque': 10}}])
        self.assertTrue(result['holding'])
        self.assertFalse(result['force_readback'])
        self.assertEqual(result['commanded_force_percent'], 10)
        self.assertNotIn('force_percent', result)  # No invented readback.

    async def test_invalid_speed_blocks_before_closure(self):
        for speed in (None, True, '2000', 0, 5001, 1.5, float('nan')):
            handle = AtomicGripper(speed=speed)
            with self.subTest(speed=speed), self.assertRaisesRegex(RuntimeError, 'speed'):
                await self.close(handle)
            self.assertEqual(handle.commands, [{'get_gripper_speed': True}])

    async def test_atomic_requires_empty_then_held_feedback(self):
        loaded = AtomicGripper()
        loaded.holding = True
        with self.assertRaisesRegex(RuntimeError, 'empty hand'):
            await self.close(loaded)
        self.assertEqual(loaded.commands, [])
        for handle in (AtomicGripper(reports_holding=False), AtomicGripper(closes=False)):
            with self.assertRaises(RuntimeError):
                await self.close(handle)

    async def test_failure_timeout_and_cancellation_never_retry_or_release(self):
        for failure in (RuntimeError('unsupported'), TimeoutError(), asyncio.CancelledError()):
            handle = AtomicGripper(failure=failure)
            with self.assertRaises(type(failure)):
                await self.close(handle)
            self.assertEqual(len(handle.commands), 2)
            self.assertEqual(handle.opened, 0)

    async def test_unexpected_response_is_not_success_even_when_holding(self):
        with self.assertRaisesRegex(RuntimeError, 'Unexpected'):
            await self.close(AtomicGripper(response={'success': False}))

    async def test_force_and_feedback_settings_fail_before_commands(self):
        for force, settings in [(10.5, {}), (31, {}), (True, {}), (10, {'require_holding': False})]:
            handle = AtomicGripper()
            with self.assertRaises(ValueError):
                await self.close(handle, force, **settings)
            self.assertEqual(handle.commands, [])

    def test_object_profiles_reject_missing_unknown_and_invalid_values(self):
        for profiles in ({}, {'coconut_water': 31}, {'coconut_water': True},
                         {'coconut_water': 10.5}, {'typo': 10}, []):
            cfg = config(primitive_settings={'gripper': {'force_control': 'ufactory_atomic',
                                                         'object_force_percent': profiles}})
            with self.assertRaises(ValueError):
                gripper.force_for_object(cfg, 'coconut_water')


class OpenGripperTests(GripperTestCase):
    async def open(self, handle=None, **overrides):
        return await self.call(gripper.open_gripper, handle, **overrides)

    async def test_opens_and_confirms_nothing_is_held(self):
        handle = FakeGripper(holding=True)
        result = await self.open(handle)
        self.assertEqual(handle.opened, 1)
        self.assertFalse(result['holding'])

    async def test_a_gripper_that_still_holds_after_opening_is_a_failure(self):
        handle = FakeGripper(holding=True)

        async def stuck(timeout=None):
            handle.opened += 1  # opened, but the object never let go

        handle.open = stuck
        with self.assertRaisesRegex(RuntimeError, 'still reports something held'):
            await self.open(handle)

    async def test_a_gripper_without_feedback_must_be_opted_out_explicitly(self):
        handle = FakeGripper(reports_holding=False)
        with self.assertRaisesRegex(RuntimeError, 'cannot report whether it is holding'):
            await self.open(handle)
        result = await self.open(handle,
                                 primitive_settings={'gripper': {'require_holding': False}})
        self.assertIsNone(result['holding'])

    async def test_needs_a_connected_robot(self):
        with self.assertRaisesRegex(ValueError, 'needs a connected robot'):
            await gripper.open_gripper(Context(config(), robot=None))


if __name__ == '__main__':
    unittest.main()
