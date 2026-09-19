import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from primitives.types import Context
from runtime.config import DEFAULT_CONFIG, read_json
from runtime.demonstrations import PHASES, ViamIO, now, station, write
from runtime.teaching import teach_skill
from runtime.replay import (adapted_plan, bounded_offset, estimate_object_offset,
                            load_episode, replay_skill, run_demo, validate_episode)
from tests.test_camera import FakeCamera, FakeImage
from tests.test_motion import FakeArm, FakeMotion, viam_pose


def config():
    c = read_json(DEFAULT_CONFIG)
    c.update(calibrated=True, objects=['coconut_water', 'pitcher'],
             workspace_mm={a: [-500, 500] for a in 'xyz'},
             primitive_settings={'camera': {'views': {'wrist': 'cam', 'overhead': 'overhead-cam'}}})
    return c


POSE = dict(x=10, y=20, z=100, o_x=1, o_y=0, o_z=0, theta=90)


class FakeIO:
    def __init__(self, fail=None, joints=True):
        self.calls = []
        self.fail = fail
        self.has_joints = joints
        self.held = False
        self.forces = []
        self.current = copy.deepcopy(POSE)

    async def state(self):
        return dict(timestamp=now(), completed_at=now(), pose=copy.deepcopy(self.current),
                    joints=[0.0]*6 if self.has_joints else None)

    async def capture(self, directory, label):
        self.calls.append('capture_' + label)
        await asyncio.sleep(0)
        return dict(timestamp=now(), state=await self.state(),
                    frames={v: {'images': [{'path': f'{v}_{label}.jpg'}]} for v in ('wrist', 'overhead')})

    async def pose(self, pose):
        self.calls.append('pose')
        if self.fail == 'pose':
            raise RuntimeError('motion failure')
        self.current = copy.deepcopy(pose)

    async def joints(self, values):
        self.calls.append('joints')
        self.current = copy.deepcopy(POSE)

    async def holding(self):
        return self.held

    async def close(self, force):
        self.calls.append('close')
        self.forces.append(force)
        self.held = True

    async def open(self):
        self.calls.append('open')
        self.held = False

    async def prepare(self):
        self.calls.append('prepare')

    async def stop(self):
        self.calls.append('stop')


async def yes(prompt):
    await asyncio.sleep(0)
    if 'Enter the force percent' in prompt:
        return '12'
    return 'yes'


class EpisodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = config()

    async def teach(self, name='coconut_water', io=None, ask_fn=yes):
        return await teach_skill(name, io or FakeIO(), self.config, root=self.root, ask_fn=ask_fn)

    async def episode(self):
        await self.teach()
        return load_episode(self.root, 'coconut_water', self.config)

    def scene(self):
        return dict(object='coconut_water', frame='world', reliable=True, source='test_operator',
                    timestamp=now(), observation={'timestamp': now()}, offset=dict(dx=2, dy=3, dz=1))

    async def replay(self, io, episode=None, scene=None, **kwargs):
        async def answer(prompt):
            if 'Close the gripper manually' in prompt:
                io.held = True
            return await yes(prompt)
        return await replay_skill('coconut_water', scene or self.scene(), io=io, config=self.config,
                                  episode=episode or await self.episode(), directory=self.root,
                                  force_percent=10, pause_s=0, ask_fn=answer, **kwargs)

    async def test_teaching_read_only_and_roundtrip(self):
        io = FakeIO()
        path = await self.teach(io=io)
        _, meta, track = load_episode(self.root, 'coconut_water', self.config)
        self.assertTrue(meta['grasp_success'])
        self.assertEqual(meta['gripper_force_percent'], 12.0)
        self.assertTrue(meta['gripper_force_evidence']['holding'])
        self.assertEqual(meta['gripper_force_evidence']['source'], 'operator_entered')
        self.assertFalse(meta['gripper_force_evidence']['hardware_verified'])
        self.assertEqual(meta['validation_status'], 'candidate')
        self.assertEqual(meta['place_pose'], POSE)
        self.assertEqual(list(dict.fromkeys(wp['phase'] for wp in track['waypoints'])), list(PHASES))
        self.assertEqual(track['waypoints'][0]['t'], 0)
        self.assertIn('max_step_deg', track['stats'])
        self.assertTrue((path / 'samples.jsonl').exists())
        self.assertTrue(all(c.startswith('capture_') for c in io.calls))

    async def test_failure_preserves_partial_and_not_selected(self):
        io = FakeIO()
        async def abort(prompt):
            if 'Close the gripper' in prompt:
                return 'no'
            return 'yes'
        with self.assertRaises(RuntimeError):
            await self.teach(io=io, ask_fn=abort)
        meta = read_json(next(self.root.glob('*/episode_*/metadata.json')))
        self.assertFalse(meta['success'])
        self.assertEqual(meta['status'], 'paused')
        self.assertIn('grasp_pose', meta)
        self.assertEqual(io.calls[-1], 'stop')
        with self.assertRaisesRegex(ValueError, 'No successful'):
            load_episode(self.root, 'coconut_water', self.config)

    async def test_adaptation_changes_only_pickup_and_preserves_orientation_and_joints(self):
        _, meta, track = await self.episode()
        frozen = copy.deepcopy(track)
        plan = adapted_plan(meta, track, dict(dx=2, dy=3, dz=4), self.config)
        self.assertEqual(plan['grasp']['x'], 12)
        for old, new in zip(track['waypoints'], plan['waypoints']):
            self.assertEqual(new['joints'], old['joints'])
            for k in ('o_x', 'o_y', 'o_z', 'theta'):
                self.assertEqual(new['pose'][k], old['pose'][k])
            if old['phase'] != 'lift':
                self.assertEqual(new['pose'], old['pose'])
                self.assertEqual(new['replay_mode'], 'joints')
        self.assertEqual(track, frozen)
        with self.assertRaises(ValueError):
            adapted_plan(meta, track, dict(dx=100, dy=0, dz=0), self.config)
        meta['grasp_pose']['x'] = 499
        with self.assertRaises(ValueError):
            adapted_plan(meta, track, dict(dx=2, dy=0, dz=0), self.config)

    async def test_replay_success_and_stop_without_release_after_failure(self):
        io = FakeIO()
        await self.replay(io)
        self.assertIn('joints', io.calls)
        self.assertEqual(io.calls.count('open'), 2)
        self.assertNotIn('stop', io.calls)
        bad = FakeIO(fail='pose')
        with self.assertRaisesRegex(RuntimeError, 'motion failure'):
            await self.replay(bad)
        self.assertEqual(bad.calls[-1], 'stop')
        self.assertEqual(bad.calls.count('open'), 1)  # Never release as recovery.

    async def test_cancellation_stops_while_holding(self):
        io = FakeIO()
        async def cancel(values):
            raise asyncio.CancelledError()
        io.joints = cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.replay(io)
        self.assertTrue(io.held)
        self.assertEqual(io.calls[-1], 'stop')
        self.assertEqual(io.calls.count('open'), 1)

    async def test_stale_unreliable_wrong_identity_never_moves(self):
        ep = await self.episode()
        for override in ({'timestamp': '2000-01-01T00:00:00+00:00'}, {'reliable': False},
                         {'object': 'pitcher'}, {'frame': 'camera'}, {'offset': dict(dx=float('nan'),dy=0,dz=0)}):
            io = FakeIO()
            with self.assertRaises(ValueError):
                await self.replay(io, ep, {**self.scene(), **override})
            self.assertEqual(io.calls, ['stop'])

    async def test_bad_trajectory_refused_before_motion(self):
        ep = await self.episode()
        for corrupt in ('phase', 'joint', 'station', 'time'):
            path, meta, track = copy.deepcopy(ep)
            if corrupt == 'phase':
                track['waypoints'][0]['phase'] = 'pour'
            elif corrupt == 'joint':
                track['waypoints'][-1]['joints'] = [300]*6
            elif corrupt == 'station':
                meta['station']['frame'] = 'wrong'
            else:
                track['waypoints'][-1]['t'] = -1
            io = FakeIO()
            with self.assertRaises(ValueError):
                await self.replay(io, (path, meta, track))
            self.assertEqual(io.calls, ['stop'])

    async def test_missing_joint_api_supports_pose_capture_and_replay(self):
        await self.teach(io=FakeIO(joints=False))
        ep = load_episode(self.root, 'coconut_water', self.config)
        self.assertEqual(ep[2]['format'], 'pose-track/1')
        io = FakeIO(joints=False)
        await self.replay(io, ep)
        self.assertNotIn('joints', io.calls)

    async def test_demo_offline_and_second_object_is_observed_after_first(self):
        await self.teach()
        await self.teach('pitcher')
        await run_demo(None, self.config, root=self.root)
        io = FakeIO()
        async def answer(prompt):
            if 'Close the gripper manually' in prompt:
                io.held = True
            if 'force percent' in prompt:
                return '10'
            if 'Measured dx' in prompt:
                return '0,0,0'
            return 'yes'
        path = await run_demo(io, self.config, root=self.root, runs=self.root/'runs',
                              execute=True, ask_fn=answer, pause_s=0)
        self.assertEqual(read_json(path/'result.json')['status'], 'completed')
        self.assertEqual(io.forces, [])  # Operator-entered force never invokes torque control.
        self.assertLess(io.calls.index('capture_coconut_water_after_place'), io.calls.index('capture_pitcher_current'))

    async def test_automatic_demo_uses_object_settings_for_operator_entered_episodes(self):
        await self.teach()
        await self.teach('pitcher')
        self.config['primitive_settings']['gripper'] = {
            'force_control': 'ufactory_atomic',
            'object_force_percent': {'coconut_water': 10, 'pitcher': 20}}
        io = FakeIO()
        prompts = []
        async def answer(prompt):
            prompts.append(prompt)
            if 'Measured dx' in prompt:
                return '0,0,0'
            return 'yes'
        path = await run_demo(io, self.config, root=self.root, runs=self.root/'runs',
                              execute=True, ask_fn=answer, pause_s=0)
        self.assertEqual(io.forces, [10, 20])
        self.assertFalse(any('manually' in p or 'enter the team-approved' in p for p in prompts))
        self.assertEqual(sum('Grasp succeeded' in p for p in prompts), 2)
        self.assertEqual(read_json(path/'result.json')['status'], 'completed')
        self.assertIn('automatic_grasp_completed', (path/'events.jsonl').read_text())

    async def test_missing_second_object_force_blocks_entire_demo_before_io(self):
        await self.teach()
        await self.teach('pitcher')
        self.config['primitive_settings']['gripper'] = {
            'force_control': 'ufactory_atomic', 'object_force_percent': {'coconut_water': 10}}
        io = FakeIO()
        for execute in (False, True):
            with self.assertRaisesRegex(ValueError, 'pitcher: missing'):
                await run_demo(io, self.config, root=self.root, execute=execute)
        self.assertEqual(io.calls, [])

    async def test_atomic_closure_failure_stops_before_lift_without_release(self):
        ep = await self.episode()
        self.config['primitive_settings']['gripper'] = {
            'force_control': 'ufactory_atomic', 'object_force_percent': {'coconut_water': 10}}
        io = FakeIO()
        io.close = AsyncMock(side_effect=TimeoutError('closure uncertain'))
        with self.assertRaises(TimeoutError):
            await self.replay(io, ep)
        io.close.assert_awaited_once_with(10)
        self.assertEqual(io.calls.count('open'), 1)  # Initial opening only.
        self.assertEqual(io.calls.count('pose'), 2)  # Approach/grasp; no lift.
        self.assertEqual(io.calls[-1], 'stop')

    async def test_live_transition_refuses_ik_joint_jump(self):
        ep = await self.episode()
        io = FakeIO()
        original = io.state
        async def far():
            state = await original()
            state['joints'] = [80]*6
            return state
        io.state = far
        with self.assertRaisesRegex(RuntimeError, 'Live joint transition'):
            await self.replay(io, ep)
        self.assertNotIn('joints', io.calls)
        self.assertTrue(io.held)
        self.assertEqual(io.calls[-1], 'stop')


class BoundsTests(unittest.TestCase):
    def test_stub_never_invents_offset(self):
        with self.assertRaises(NotImplementedError):
            estimate_object_offset({}, {})

    def test_bounds_are_radial_finite_and_unit_specific(self):
        for offset in (dict(dx=15,dy=15,dz=0), dict(dx=0,dy=0,dz=11),
                       dict(dx=True,dy=0,dz=0), dict(dx=0,dy=0,dz=float('inf'))):
            with self.assertRaises(ValueError):
                bounded_offset(offset)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_camera_wrapper_preserves_depth_and_full_pose(self):
        with tempfile.TemporaryDirectory() as directory:
            c = config()
            io = ViamIO(Context(c, object()))
            camera = FakeCamera([FakeImage(name='color'), FakeImage(b'raw-depth', name='depth', mime_type='image/vnd.viam.dep')])
            with patch('viam.components.camera.Camera.from_robot', return_value=camera), \
                 patch('viam.components.arm.Arm.from_robot', return_value=FakeArm()), \
                 patch('viam.services.motion.MotionClient.from_robot', return_value=FakeMotion(viam_pose(10,20,100,90,o_x=1,o_y=0,o_z=0))):
                observation = await io.capture(Path(directory), 'before')
            self.assertEqual(observation['state']['pose'], POSE)
            for frame in observation['frames'].values():
                self.assertEqual((Path(directory)/frame['images'][1]['path']).read_bytes(), b'raw-depth')
                self.assertEqual(frame['images'][1]['mime_type'], 'image/vnd.viam.dep')
                self.assertIn('sha256', frame['images'][0])

    async def test_full_orientation_moves_via_existing_planner(self):
        io = ViamIO(Context(config(), object()))
        fake = FakeMotion(viam_pose(10,20,100,90,o_x=1,o_y=0,o_z=0))
        with patch('viam.services.motion.MotionClient.from_robot', return_value=fake):
            await io.pose(POSE)
        self.assertEqual(fake.requests[0][1].pose.o_x, 1)
        self.assertEqual(fake.requests[0][1].pose.theta, 90)


if __name__ == '__main__':
    unittest.main()
