import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from primitives import localization, vision
from primitives.types import Context
from runtime.config import validate_target
from runtime.orchestrator import require_localization_profiles
from tests.test_localization import AFFINE, config
from tests.test_observer import IMAGE, observation


class LocalizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calibration = self.root / 'calibration.json'
        self.calibration.write_text(json.dumps({'H': AFFINE, 'frame': 'world', 'units': 'mm',
                                              'image_size': [640, 480], 'max_error_mm': 1.0}))
        self.evidence = self.root / 'test-evidence.md'
        self.evidence.write_text('Synthetic test fixture, not real physical evidence.')
        self.profile_path = self.root / 'cup.json'
        self.profile = {'schema': 'localization-profile/1', 'status': 'validated', 'object_id': 'cup',
            'view': 'overhead', 'camera_resource': 'overhead-cam', 'frame': 'world', 'model': vision.DEFAULT_MODEL,
            'detector_version': 'bbox-1000-v1',
            'calibration_sha256': hashlib.sha256(self.calibration.read_bytes()).hexdigest(),
            'image_size': [640, 480], 'bbox_anchor': [0.5, 0.5], 'offset_xy_mm': [2, 3],
            'z_mm': 100, 'orientation': {'o_x': 0, 'o_y': 0, 'o_z': -1, 'theta': 0},
            'max_error_mm': 2.0, 'tolerance_mm': 5.0,
            'validated_region_mm': {'x': [-200, 200], 'y': [-200, 200]},
            'scope': 'fixed_orientation_and_height', 'evidence': str(self.evidence)}
        self.write_profile()
        self.config = config({'overhead': str(self.calibration)})
        self.config['primitive_settings']['localization']['target_profiles'] = {'cup': str(self.profile_path)}
        self.provider = patch('primitives.vision.credentials', return_value={
            'OPENROUTER_API_KEY': 'test', 'OPENROUTER_VISION_MODEL': vision.DEFAULT_MODEL})
        self.provider.start()
        self.addCleanup(self.provider.stop)
        self.found = {**observation(), 'model': vision.DEFAULT_MODEL, 'detector_version': 'bbox-1000-v1',
                      'image': {'width': 640, 'height': 480}}

    def write_profile(self):
        self.profile_path.write_text(json.dumps(self.profile))

    def target(self, **kwargs):
        return localization.target_from_observation(self.config, 'cup', self.found,
            view=kwargs.get('view', 'overhead'),
            captured_at=kwargs.get('captured_at', datetime.now(timezone.utc).isoformat()))

    def test_maps_actual_detection_with_measured_geometry(self):
        target = self.target()
        # Centre is (224,264) pixels -> (62,107) mm + measured offset (2,3).
        self.assertAlmostEqual(target['pose']['x'], 64)
        self.assertAlmostEqual(target['pose']['y'], 110)
        self.assertEqual(target['pose']['z'], 100)
        validate_target(target, self.config, execute=True)
        self.assertEqual(set(target), {'object_id', 'frame', 'pose'})

    def test_missing_profile_blocks_before_capture(self):
        self.config['primitive_settings']['localization']['target_profiles'] = {}
        plan = {'steps': [{'tool': 'localize', 'args': {'object_id': 'cup'}}]}
        with self.assertRaisesRegex(ValueError, 'missing measured'):
            require_localization_profiles(plan, self.config)

    def test_candidate_or_wrong_model_cannot_supply_target(self):
        for key, value in [('status', 'candidate'), ('model', 'unvalidated-model'),
                           ('calibration_sha256', 'different'), ('detector_version', 'old')]:
            with self.subTest(key=key):
                original = self.profile[key]
                self.profile[key] = value
                self.write_profile()
                with self.assertRaises(ValueError):
                    self.target()
                self.profile[key] = original

    def test_bad_calibration_cannot_be_hidden_by_small_profile_error(self):
        raw = json.loads(self.calibration.read_text())
        raw['max_error_mm'] = 67.85
        self.calibration.write_text(json.dumps(raw))
        self.profile['calibration_sha256'] = hashlib.sha256(self.calibration.read_bytes()).hexdigest()
        self.write_profile()
        with self.assertRaisesRegex(ValueError, 'error exceeds'):
            self.target()

    def test_requires_known_calibration_resolution(self):
        raw = json.loads(self.calibration.read_text())
        del raw['image_size']
        self.calibration.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ValueError, 'image_size'):
            self.target()

    def test_rejects_resized_or_wrong_view_observation(self):
        with self.assertRaisesRegex(ValueError, 'wrong camera'):
            self.target(view='wrist')
        self.found['image']['width'] = 320
        with self.assertRaisesRegex(ValueError, 'resolution'):
            self.target()

    def test_rejects_stale_future_and_naive_times(self):
        for stamp in (datetime.now(timezone.utc)-timedelta(minutes=5),
                      datetime.now(timezone.utc)+timedelta(minutes=5), datetime.now()):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                self.target(captured_at=stamp.isoformat())

    def test_rejects_absence_and_occlusion(self):
        self.found['detections'][0]['visibility'] = 'partial'
        with self.assertRaisesRegex(ValueError, 'absent, occluded'):
            self.target()
        self.found['detections'] = []
        with self.assertRaises(ValueError):
            self.target()

    def test_rejects_unvalidated_region_and_workspace(self):
        self.profile['validated_region_mm']['x'] = [0, 1]
        self.write_profile()
        with self.assertRaisesRegex(ValueError, 'validated region'):
            self.target()
        self.profile['validated_region_mm']['x'] = [-200, 200]
        self.write_profile()
        self.config['workspace_mm']['x'] = [-1, 1]
        with self.assertRaisesRegex(ValueError, 'workspace'):
            self.target()

    async def test_localize_captures_and_writes_evidence_without_motion(self):
        image = self.root / 'scene.png'
        image.write_bytes(IMAGE)
        capture = {'view': 'overhead', 'captured_at': datetime.now(timezone.utc).isoformat(),
                   'images': [{'mime_type': 'image/png', 'path': str(image)}]}
        with patch('primitives.camera.capture', new_callable=AsyncMock, return_value=capture) as camera:
            with patch('primitives.vision.observe', return_value=self.found) as detector:
                result = await localization.localize(Context(self.config, object()), object_id='cup')
        camera.assert_awaited_once()
        detector.assert_called_once()
        validate_target(result, self.config, execute=True)
        self.assertEqual(json.loads(image.with_suffix('.localization.json').read_text())['target'], result)

    def test_overlapping_identities_are_rejected(self):
        value = observation()
        value['detections'].append({**copy.deepcopy(value['detections'][0]), 'object_id': 'pitcher'})
        with self.assertRaisesRegex(ValueError, 'multiple identities'):
            vision.validate_observation(value, ['cup', 'pitcher'])

    def test_invalid_image_bytes_are_rejected(self):
        with self.assertRaises(ValueError):
            vision.image_info(b'\x89PNG\r\n\x1a\nbroken')
