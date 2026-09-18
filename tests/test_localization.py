import json
import tempfile
import unittest
from pathlib import Path

from primitives import localization
from runtime.config import DEFAULT_CONFIG, read_json, validate_yaw_pose

ARTIFACT = localization.ROOT / 'config/overhead_homography.json'
# A camera looking straight down: half a millimetre per pixel, origin at pixel (100, 50).
AFFINE = [[0.5, 0.0, -50.0], [0.0, 0.5, -25.0], [0.0, 0.0, 1.0]]


def config(homographies=None, hover_z_mm=None, **overrides):
    """The demo config plus a station that declares an overhead camera."""
    values = read_json(DEFAULT_CONFIG)
    values['primitive_settings'] = {'camera': {'views': {'wrist': 'cam',
                                                         'overhead': 'overhead-cam'}}}
    if homographies is not None or hover_z_mm is not None:
        values['primitive_settings']['localization'] = {
            'homographies': {} if homographies is None else homographies,
            'hover_z_mm': hover_z_mm}
    values['workspace_mm'] = {'x': [-805.0, 609.2], 'y': [-805.0, 369.2], 'z': [37.0, 805.0]}
    values['calibrated'] = True
    return {**values, **overrides}


class SettingsTestCase(unittest.TestCase):
    def test_absent_block_leaves_no_calibrated_view(self):
        self.assertEqual(localization.settings(config())['homographies'], {})
        self.assertIsNone(localization.settings(config())['hover_z_mm'])

    def test_rejects_unknown_key(self):
        values = config()
        values['primitive_settings']['localization'] = {'homography': 'x.json'}
        with self.assertRaisesRegex(ValueError, 'accepts only'):
            localization.settings(values)

    def test_rejects_view_the_station_does_not_declare(self):
        with self.assertRaisesRegex(ValueError, 'does not declare'):
            localization.settings(config({'side': 'config/side.json'}))

    def test_rejects_unusable_hover_height(self):
        with self.assertRaisesRegex(ValueError, 'hover_z_mm'):
            localization.settings(config(hover_z_mm='low'))


class CalibrationFileTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def written(self, payload, **overrides):
        path = Path(self.directory.name) / 'overhead.json'
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        return config({'overhead': str(path)}, **overrides)

    def test_loads_the_station_artifact(self):
        measured = localization.calibration(config({'overhead': str(ARTIFACT)}), 'overhead')
        stored = json.loads(ARTIFACT.read_text())
        self.assertEqual([list(row) for row in measured['H']], stored['H'])
        self.assertEqual(measured['accuracy_mm'],
                         {'mean_error_mm': stored['mean_error_mm'],
                          'max_error_mm': stored['max_error_mm']})

    def test_config_points_at_a_calibration_that_loads(self):
        """The shipped config and the shipped artifact have to agree with each other."""
        local = localization.ROOT / 'config/local.json'
        if not local.exists():
            self.skipTest('config/local.json is local calibration and Git ignores it')
        values = read_json(local)
        for view in localization.settings(values)['homographies']:
            self.assertEqual(len(localization.calibration(values, view)['H']), 3)

    def test_reports_an_uncalibrated_view(self):
        with self.assertRaisesRegex(ValueError, 'No hand-eye calibration'):
            localization.calibration(config({'overhead': str(ARTIFACT)}), 'wrist')

    def test_rejects_a_calibration_measured_in_another_frame(self):
        values = self.written({'H': AFFINE, 'frame': 'camera'})
        with self.assertRaisesRegex(ValueError, 'calibrated against frame'):
            localization.calibration(values, 'overhead')

    def test_rejects_other_units(self):
        values = self.written({'H': AFFINE, 'units': 'm'})
        with self.assertRaisesRegex(ValueError, 'millimetres'):
            localization.calibration(values, 'overhead')

    def test_rejects_a_singular_matrix(self):
        values = self.written({'H': [[1, 2, 3], [2, 4, 6], [0, 0, 1]]})
        with self.assertRaisesRegex(ValueError, 'singular'):
            localization.calibration(values, 'overhead')

    def test_rejects_a_matrix_of_the_wrong_shape(self):
        values = self.written({'H': [[1, 0, 0], [0, 1, 0]]})
        with self.assertRaisesRegex(ValueError, '3x3'):
            localization.calibration(values, 'overhead')

    def test_rejects_a_file_that_is_not_json(self):
        values = self.written('{"H": [[1, 0, 0]]\ndef pixel_to_robot(px, py, H):\n')
        with self.assertRaisesRegex(ValueError, 'not valid JSON'):
            localization.calibration(values, 'overhead')

    def test_reports_a_missing_file(self):
        values = config({'overhead': 'config/not-measured-yet.json'})
        with self.assertRaisesRegex(ValueError, 'not found'):
            localization.calibration(values, 'overhead')


class ProjectionTestCase(unittest.TestCase):
    def setUp(self):
        self.station = json.loads(ARTIFACT.read_text())['H']

    def test_affine_calibration_converts_pixels_to_millimetres(self):
        self.assertEqual(localization.project(AFFINE, 100, 50), (0.0, 0.0))
        self.assertEqual(localization.project(AFFINE, 120, 50), (10.0, 0.0))

    def test_station_calibration_satisfies_its_own_equations(self):
        """Check the definition, not a second copy of the arithmetic.

        A projective mapping is defined by H @ (px, py, 1) being parallel to
        (x, y, 1); the residuals below are zero exactly when that holds.
        """
        H = self.station
        for px, py in ((0, 0), (320, 240), (639, 479), (12.5, 7.25)):
            x, y = localization.project(H, px, py)
            scale = H[2][0] * px + H[2][1] * py + H[2][2]
            self.assertAlmostEqual(H[0][0] * px + H[0][1] * py + H[0][2] - x * scale, 0)
            self.assertAlmostEqual(H[1][0] * px + H[1][1] * py + H[1][2] - y * scale, 0)

    def test_station_calibration_lands_in_the_workspace(self):
        values = config({'overhead': str(ARTIFACT)})
        for px, py in ((0, 0), (320, 240), (639, 479)):
            self.assertTrue(localization.pixel_to_frame(values, 'overhead', px, py)
                            ['in_workspace'])

    def test_rejects_a_pixel_on_the_horizon(self):
        # Third row (1, 0, 0): the scale vanishes at px = 0, where the plane is edge-on.
        horizon = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]]
        with self.assertRaisesRegex(ValueError, 'horizon'):
            localization.project(horizon, 0, 25)

    def test_rejects_a_pixel_that_is_not_a_number(self):
        with self.assertRaisesRegex(ValueError, 'px must be'):
            localization.project(AFFINE, None, 10)

    def test_scale_is_the_calibrated_millimetres_per_pixel(self):
        scale = localization.scale_mm_per_px(AFFINE, 100, 50)
        self.assertAlmostEqual(scale['x_axis_mm_per_px'], 0.5)
        self.assertAlmostEqual(scale['y_axis_mm_per_px'], 0.5)

    def test_perspective_makes_the_scale_vary_across_the_image(self):
        near = localization.scale_mm_per_px(self.station, 0, 0)
        far = localization.scale_mm_per_px(self.station, 639, 479)
        self.assertNotAlmostEqual(near['x_axis_mm_per_px'], far['x_axis_mm_per_px'])


class PixelToFrameTestCase(unittest.TestCase):
    def setUp(self):
        self.config = config({'overhead': str(ARTIFACT)})

    def test_reports_the_frame_the_task_runs_in(self):
        found = localization.pixel_to_frame(self.config, 'overhead', 320, 240)
        self.assertEqual(found['frame'], self.config['frame'])
        self.assertEqual(found['pixel'], [320, 240])
        self.assertEqual(found['view'], 'overhead')

    def test_carries_the_calibration_error_with_the_position(self):
        found = localization.pixel_to_frame(self.config, 'overhead', 320, 240)
        self.assertEqual(found['accuracy_mm']['mean_error_mm'], 27.01)
        self.assertEqual(found['accuracy_mm']['max_error_mm'], 67.85)

    def test_result_is_json(self):
        found = localization.pixel_to_frame(self.config, 'overhead', 320, 240)
        self.assertEqual(json.loads(json.dumps(found, allow_nan=False))['view'], 'overhead')

    def test_flags_a_pixel_that_maps_outside_the_workspace(self):
        narrow = config({'overhead': str(ARTIFACT)})
        narrow['workspace_mm'] = {'x': [0.0, 1.0], 'y': [0.0, 1.0], 'z': [37.0, 805.0]}
        self.assertFalse(localization.pixel_to_frame(narrow, 'overhead', 320, 240)
                         ['in_workspace'])


class HoverPoseTestCase(unittest.TestCase):
    def test_refuses_to_invent_a_height(self):
        with self.assertRaisesRegex(ValueError, 'No hover height'):
            localization.hover_pose(config(), 235.0, 40.0)

    def test_uses_the_configured_height(self):
        pose = localization.hover_pose(config(hover_z_mm=150.0), 235.0, 40.0)
        self.assertEqual(pose, {'x': 235.0, 'y': 40.0, 'z': 150.0, 'yaw': 0.0})

    def test_an_explicit_height_wins(self):
        pose = localization.hover_pose(config(hover_z_mm=150.0), 235.0, 40.0, z=200.0, yaw=30.0)
        self.assertEqual(pose['z'], 200.0)
        self.assertEqual(pose['yaw'], 30.0)

    def test_refuses_a_point_outside_the_workspace(self):
        with self.assertRaisesRegex(ValueError, 'outside the calibrated workspace'):
            localization.hover_pose(config(hover_z_mm=150.0), 5000.0, 40.0)

    def test_refuses_a_height_outside_the_workspace(self):
        with self.assertRaisesRegex(ValueError, r'z=1000.0 mm is outside'):
            localization.hover_pose(config(hover_z_mm=1000.0), 235.0, 40.0)

    def test_refuses_a_yaw_that_is_not_an_angle(self):
        with self.assertRaisesRegex(ValueError, 'yaw'):
            localization.hover_pose(config(hover_z_mm=150.0), 235.0, 40.0, yaw=400.0)

    def test_a_detected_pixel_becomes_an_argument_go_to_pose_accepts(self):
        """The whole point: a pixel a detector found is a move the executor will run."""
        values = config({'overhead': str(ARTIFACT)})
        values['primitive_settings']['localization']['hover_z_mm'] = 150.0
        found = localization.pixel_to_frame(values, 'overhead', 320, 240)
        pose = localization.hover_pose(values, found['x'], found['y'])
        validate_yaw_pose(pose, values, execute=True)


if __name__ == '__main__':
    unittest.main()
