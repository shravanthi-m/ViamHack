from tests.config import UNCALIBRATED_CONFIG
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from primitives import camera, mock
from primitives.registry import catalog, implementations
from primitives.types import Context
from runtime.config import read_json
from runtime.orchestrator import validate_plan


def config(views=None, **overrides):
    values = read_json(UNCALIBRATED_CONFIG)
    if views is not None:
        values['primitive_settings'] = {'camera': {'views': views}}
    return {**values, **overrides}


def station(**overrides):
    return config({'wrist': 'cam', 'overhead': 'overhead-cam'}, **overrides)


class FakeImage:
    def __init__(self, data=b'\xff\xd8jpeg', name='', mime_type='image/jpeg+lazy',
                 width=640, height=480):
        self.data, self.name, self.mime_type = data, name, mime_type
        self.width, self.height = width, height


class FakeMetadata:
    """Stands in for the camera's ResponseMetadata timestamp."""

    class Stamp:
        def __init__(self, seconds):
            self.seconds, self.nanos = seconds, 0

        def ToDatetime(self, tzinfo=None):
            from datetime import datetime
            return datetime.fromtimestamp(self.seconds, tz=tzinfo)

    def __init__(self, seconds=1_700_000_000):
        self.captured_at = self.Stamp(seconds)


class FakeCamera:
    """Stands in for a Viam camera: records the request, returns what it was given."""

    def __init__(self, images=None, metadata=None):
        self.images = [FakeImage()] if images is None else images
        self.metadata = metadata or FakeMetadata()
        self.requests = []

    async def get_images(self, timeout=None, **kwargs):
        self.requests.append(timeout)
        return self.images, self.metadata


class CameraTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def drive(self, fake):
        """Give the primitive a camera without a machine, and a directory to write into."""
        return patch('viam.components.camera.Camera.from_robot', return_value=fake)

    async def shoot(self, view='wrist', fake=None, values=None):
        fake = fake or FakeCamera()
        values = values or station()
        values.setdefault('primitive_settings', {}).setdefault('camera', {})
        values['primitive_settings']['camera']['image_dir'] = self.temp.name
        with self.drive(fake):
            return await camera.capture(Context(values, robot=object()), view=view), fake

    def files(self):
        return sorted(path.name for path in Path(self.temp.name).iterdir())


class CaptureTests(CameraTestCase):
    async def test_saves_the_image_and_reports_where_it_went(self):
        result, fake = await self.shoot()
        self.assertEqual(result['view'], 'wrist')
        self.assertEqual(result['camera'], 'cam')
        self.assertEqual(result['captured_at'], '2023-11-14T22:13:20+00:00')
        self.assertEqual(len(result['images']), 1)
        saved = result['images'][0]
        self.assertEqual(saved['mime_type'], 'image/jpeg')  # The lazy marker is not a type.
        self.assertEqual((saved['bytes'], saved['width'], saved['height']), (6, 640, 480))
        self.assertEqual(Path(saved['path']).read_bytes(), b'\xff\xd8jpeg')
        self.assertTrue(self.files()[0].endswith('-wrist-0.jpg'))
        self.assertEqual(fake.requests, [60])  # The configured primitive timeout.

    async def test_each_view_reads_its_own_camera(self):
        values = station()
        values['primitive_settings']['camera']['image_dir'] = self.temp.name
        for view, resource in (('wrist', 'cam'), ('overhead', 'overhead-cam')):
            with self.subTest(view=view):
                with self.drive(FakeCamera()) as handle:
                    result = await camera.capture(Context(values, robot=object()), view=view)
                self.assertEqual(handle.call_args.args[1], resource)
                self.assertEqual(result['camera'], resource)

    async def test_a_view_the_station_never_declared_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'Unknown camera view'):
            await self.shoot('ceiling')
        with self.assertRaisesRegex(ValueError, 'Unknown camera view'):
            await self.shoot('overhead', values=config())  # Only the wrist view configured.
        self.assertEqual(self.files(), [])

    async def test_every_returned_imager_is_written(self):
        pair = FakeCamera([FakeImage(b'colour', name='color'),
                           FakeImage(b'depth', name='depth', mime_type='image/vnd.viam.dep')])
        result, _ = await self.shoot(fake=pair)
        self.assertEqual([image['name'] for image in result['images']], ['color', 'depth'])
        self.assertEqual([Path(image['path']).suffix for image in result['images']],
                         ['.jpg', '.dep'])
        self.assertEqual(len(self.files()), 2)

    async def test_an_empty_response_is_a_failure_not_an_empty_result(self):
        with self.assertRaisesRegex(RuntimeError, 'no image'):
            await self.shoot(fake=FakeCamera([]))
        with self.assertRaisesRegex(RuntimeError, 'empty image'):
            await self.shoot(fake=FakeCamera([FakeImage(b'')]))
        self.assertEqual(self.files(), [])

    async def test_capture_needs_a_connected_robot(self):
        with self.assertRaisesRegex(ValueError, 'connected robot'):
            await camera.capture(Context(station()), view='wrist')

    async def test_captures_never_overwrite_each_other(self):
        await self.shoot()
        await self.shoot()
        self.assertEqual(len(self.files()), 2)

    async def test_an_unknown_mime_type_is_still_saved(self):
        result, _ = await self.shoot(fake=FakeCamera([FakeImage(b'raw', mime_type='image/tiff')]))
        self.assertEqual(Path(result['images'][0]['path']).suffix, '.bin')
        self.assertEqual(result['images'][0]['mime_type'], 'image/tiff')


class SettingsTests(unittest.TestCase):
    def test_the_configured_camera_is_the_wrist_view_by_default(self):
        self.assertEqual(camera.views(config()), {'wrist': 'cam'})

    def test_bad_camera_settings_are_refused(self):
        for block in ({'views': {'wrist': ''}}, {'views': {'over head': 'overhead-cam'}},
                      {'views': {}}, {'views': 'overhead-cam'}, {'image_dir': ''},
                      {'lens': 'wide'}):
            with self.subTest(block=block):
                values = config()
                values['primitive_settings'] = {'camera': block}
                with self.assertRaises(ValueError):
                    camera.views(values)

    def test_images_land_under_the_repository_unless_told_otherwise(self):
        self.assertEqual(camera.image_dir(config()), camera.ROOT / 'runs/images')
        values = config()
        values['primitive_settings'] = {'camera': {'image_dir': '/tmp/shots'}}
        self.assertEqual(camera.image_dir(values), Path('/tmp/shots'))


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    def test_the_planner_sees_the_views_the_station_declares(self):
        entry = next(tool for tool in catalog(station()) if tool['name'] == 'capture')
        self.assertTrue(entry['implemented'])
        self.assertEqual(entry['parameters']['properties']['view']['enum'],
                         ['overhead', 'wrist'])
        self.assertIn('capture', implementations())

    def test_plans_may_only_name_a_declared_view(self):
        def plan(view):
            return dict(version=1, instruction='Photograph the station',
                        steps=[dict(id='look', tool='capture', args={'view': view})])
        validate_plan(plan('overhead'), station())
        with self.assertRaisesRegex(ValueError, 'Unknown camera view'):
            validate_plan(plan('ceiling'), station())
        with self.assertRaisesRegex(ValueError, 'Unknown camera view'):
            validate_plan(plan('overhead'), config())  # Not declared in this config.

    async def test_a_mock_run_photographs_nothing(self):
        result = await mock.invoke('capture', Context(station()), {'view': 'overhead'})
        self.assertEqual(result, {'view': 'overhead', 'camera': 'overhead-cam',
                                  'captured_at': None, 'images': [], 'mock': True})


class DepthTests(CameraTestCase):
    async def test_depth_dimensions_come_from_the_viam_depth_header(self):
        header = b'DEPTHMAP' + (2).to_bytes(8, 'big') + (3).to_bytes(8, 'big')
        depth = FakeImage(header + bytes(12), name='depth',
                          mime_type='image/vnd.viam.dep', width=None, height=None)
        result, _ = await self.shoot(fake=FakeCamera([depth]))
        self.assertEqual((result['images'][0]['width'], result['images'][0]['height']), (2, 3))

    async def test_a_size_the_sdk_could_not_read_stays_unknown(self):
        blind = FakeImage(b'not-an-image', mime_type='image/tiff', width=None, height=None)
        result, _ = await self.shoot(fake=FakeCamera([blind]))
        self.assertIsNone(result['images'][0]['width'])
