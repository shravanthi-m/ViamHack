"""Offline honey orchestration checks; never connect to hardware."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from runtime import honey_tilt as honey, taught_actions as taught
from test_taught_actions import fixture, FakeIO


def honey_fixture():
    config, book = fixture('squeeze')
    config['objects'] = ['honey', 'cup']
    book.update(action='honey', tilt_dwell_s=0, max_translation_mm=10,
                source_object='honey', cup_object='cup', inspection_calibration_sha256='abc')
    book['poses']['cup_tilt'] = copy.deepcopy(book['poses']['cup_action'])
    book['poses']['cup_tilt']['pose']['theta'] = 15
    book['poses']['inspection_pose'] = copy.deepcopy(book['poses']['source_approach'])
    book['poses']['inspection_pose']['joints'] = [0, 0, 0, 0, 0, 0]
    targets = {name: dict(object_id=name, frame=book['frame'],
                         pose=copy.deepcopy(book['poses'][label]['pose']))
               for name, label in [('honey', 'source_grasp'), ('cup', 'cup_action')]}
    return config, book, targets


def profile_for(book, name):
    pose = book['poses']['source_grasp' if name == 'honey' else 'cup_action']['pose']
    return dict(view='wrist', camera_resource='camera', model='test',
                calibration_sha256='abc', z_mm=pose['z'],
                orientation={k: pose[k] for k in ('o_x', 'o_y', 'o_z', 'theta')})


class HoneyTests(unittest.IsolatedAsyncioTestCase):
    async def test_tilt_returns_upright_then_to_detected_source_before_release(self):
        config, book, targets = honey_fixture()
        targets['honey']['pose']['x'] += 5
        targets['cup']['pose']['y'] += 3
        moved = honey.translated_book(book, config, targets)
        io = FakeIO(config)
        log = lambda *a, **k: None
        result = await taught.execute(moved, config, io, honey.tilt_action(io, moved, log), log)
        self.assertEqual(result['action'], 'tilt_only')
        self.assertEqual(result['dispensed_amount'], 'unknown')
        moves = [call[1] for call in io.calls if call[0] == 'move']
        labels = ['source_approach', 'source_grasp', 'source_lift', 'cup_approach',
                  'cup_action', 'cup_tilt', 'cup_action', 'cup_approach',
                  'source_lift', 'source_grasp', 'source_approach']
        self.assertEqual(moves, [moved['poses'][label]['pose'] for label in labels])
        self.assertEqual(io.calls[-3][1], targets['honey']['pose'])
        self.assertEqual(sum(c[0] == 'close' for c in io.calls), 1)
        self.assertFalse(io.held)
        self.assertEqual(book['poses']['source_grasp']['pose']['x'], 220)

    def test_bad_localized_targets_rejected(self):
        config, book, targets = honey_fixture()
        for field, value in [('x', 240), ('z', 110), ('theta', 15)]:
            bad = copy.deepcopy(targets)
            bad['honey']['pose'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                honey.translated_book(book, config, bad)
        bad = copy.deepcopy(targets)
        bad['cup']['frame'] = 'arm'
        with self.assertRaises(ValueError):
            honey.translated_book(book, config, bad)

    def test_inspection_requires_pose_and_joint_match(self):
        _, book, _ = honey_fixture()
        state = copy.deepcopy(book['poses']['inspection_pose'])
        honey.check_inspection(book, state)
        for change in ('pose', 'joints', 'missing', 'nan'):
            bad = copy.deepcopy(state)
            if change == 'pose':
                bad['pose']['x'] += 2
            elif change == 'joints':
                bad['joints'][0] += 1
            elif change == 'nan':
                bad['joints'][0] = float('nan')
            else:
                bad['joints'] = None
            with self.subTest(change=change), self.assertRaises(ValueError):
                honey.check_inspection(book, bad)

    def test_profiles_bind_inspection_calibration_and_gripper_geometry(self):
        config, book, _ = honey_fixture()
        with patch.object(honey.localization, 'target_profile', side_effect=lambda c,n: profile_for(book,n)):
            honey.profiles(book, config)
            bad = copy.deepcopy(book)
            bad['inspection_calibration_sha256'] = 'different'
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                honey.profiles(bad, config)
            bad = copy.deepcopy(book)
            bad['poses']['cup_action']['pose']['z'] += 5
            with self.assertRaisesRegex(ValueError, 'height/orientation'):
                honey.profiles(bad, config)

    async def test_tilt_failure_stops_and_does_not_drop_bottle(self):
        config, book, _ = honey_fixture()
        io = FakeIO(config)
        io.fail_at = 6
        log = lambda *a, **k: None
        with self.assertRaises(RuntimeError):
            await taught.execute(book, config, io, honey.tilt_action(io, book, log), log)
        self.assertEqual(io.calls[-1], ('stop',))
        self.assertTrue(io.held)
        self.assertEqual(io.calls.count(('open',)), 1)

    async def test_detection_uses_one_capture_and_inference_for_two_objects(self):
        config, book, targets = honey_fixture()
        io = FakeIO(config)
        io.state = AsyncMock(return_value=book['poses']['inspection_pose'])
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            image = directory / 'image.jpg'
            image.write_bytes(b'fake')
            capture = dict(view='wrist', captured_at='test', images=[dict(path=str(image), mime_type='image/jpeg')])
            with patch.object(honey, 'profiles', return_value=[profile_for(book,'honey')]), \
                 patch.object(honey.camera, 'capture', new=AsyncMock(return_value=capture)) as cap, \
                 patch.object(honey.vision, 'observe', return_value={}) as observe, \
                 patch.object(honey.localization, 'target_from_observation', side_effect=lambda c,n,o,**kw:targets[n]):
                await honey.detect(io, book, config, directory)
                cap.assert_awaited_once()
                observe.assert_called_once_with(b'fake', ['honey', 'cup'], model='test')

    async def test_offline_and_missing_profiles_never_connect(self):
        config, book, _ = honey_fixture()
        args = SimpleNamespace(config='config', book='book', execute=False)
        with patch.object(honey, 'read_json', side_effect=[config, book]), \
             patch.object(honey, 'profiles'), patch.object(honey, 'connect', new=AsyncMock()) as connect:
            await honey.dispatch(args)
            connect.assert_not_awaited()
        args.execute = True
        with patch.object(honey, 'read_json', side_effect=[config, book]), \
             patch.object(honey, 'profiles', side_effect=ValueError('missing')), \
             patch.object(honey, 'connect', new=AsyncMock()) as connect:
            with self.assertRaises(ValueError):
                await honey.dispatch(args)
            connect.assert_not_awaited()

    def test_generic_runner_cannot_bypass_detection(self):
        with self.assertRaisesRegex(ValueError, 'fresh localization'):
            taught.action_function('honey')


if __name__ == '__main__':
    unittest.main()
