"""Live presentation contracts; fake camera and detector only, never a robot."""
import asyncio
import io
import threading
import time
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch, AsyncMock
from PIL import Image

from runtime.observer_live import LiveFeed, camera_source, jpeg
from runtime.config import read_json, DEFAULT_CONFIG

CONFIG = read_json(DEFAULT_CONFIG)
VIEW = 'wrist'
BUFFER = io.BytesIO()
Image.new('RGB', (1200, 900), 'green').save(BUFFER, 'PNG')
IMAGE = BUFFER.getvalue()
RESULT = {'summary': 'A cup.', 'detections': [
    {'object_id': 'cup', 'bbox': [.2, .3, .5, .8], 'visibility': 'clear', 'note': 'Cup'}]}


def wait_until(predicate, timeout=3):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for live worker')


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.closed = threading.Event()
        self.connections = 0
        @asynccontextmanager
        async def source(config, view):
            self.connections += 1
            async def read():
                await asyncio.sleep(.001)
                return IMAGE, '2026-09-19T15:00:00+00:00'
            try:
                yield read
            finally:
                self.closed.set()
        self.feed = LiveFeed(CONFIG, VIEW, ['cup'], source=source, detector=lambda *_: RESULT)
        self.key = patch('runtime.observer_live.credentials', return_value={'OPENROUTER_API_KEY': 'test'})
        self.key.start()

    def tearDown(self):
        self.feed.close()
        if self.feed.analysis_thread:
            self.feed.analysis_thread.join(3)
        self.key.stop()

    def test_constructing_and_reading_state_never_connect(self):
        self.assertFalse(self.feed.state()['active'])
        self.assertEqual(self.connections, 0)
        self.assertIsNone(self.feed.thread)

    def test_video_uses_one_connection_and_stop_closes_it(self):
        session = self.feed.start(False)['session']
        first = self.feed.next_frame(session)
        second = self.feed.next_frame(session, first['id'])
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(self.connections, 1)
        self.assertEqual(first['data'][:3], b'\xff\xd8\xff')
        self.assertIsNone(self.feed.state()['observation'])
        self.feed.close()
        self.assertTrue(self.closed.is_set())
        self.assertIsNone(self.feed.next_frame(session))

    def test_slow_identification_never_freezes_camera_and_retains_exact_image(self):
        entered, finish = threading.Event(), threading.Event()
        sampled = []
        def detector(data, objects):
            sampled.append(data)
            entered.set()
            finish.wait(3)
            return RESULT
        self.feed.detector = detector
        session = self.feed.start()['session']
        self.assertTrue(entered.wait(2))
        initial = self.feed.state()['frames']
        wait_until(lambda: self.feed.state()['frames'] > initial + 1)
        self.assertEqual(len(sampled), 1)
        finish.set()
        wait_until(lambda: self.feed.state()['observation'])
        result = self.feed.state()['observation']
        self.assertEqual(self.feed.identified(result['frame_id']), sampled[0])
        self.assertNotEqual(result['frame_id'], self.feed.frame['id'])
        self.assertFalse(result['motion_ready'])
        self.assertNotIn('pose', result)
        self.assertNotIn('data', result)
        self.feed.heartbeat(session)

    def test_stopped_inflight_analysis_cannot_publish_or_spawn_overlap(self):
        entered, finish = threading.Event(), threading.Event()
        def detector(*_):
            entered.set()
            finish.wait(3)
            return RESULT
        self.feed.detector = detector
        self.feed.start()
        self.assertTrue(entered.wait(2))
        self.feed.close()
        with self.assertRaisesRegex(ValueError, 'finishing'):
            self.feed.start()
        finish.set()
        self.feed.analysis_thread.join(2)
        self.assertIsNone(self.feed.state()['observation'])

    def test_absent_view_key_and_bad_options_block_before_connecting(self):
        self.feed.view = None
        with self.assertRaises(ValueError):
            self.feed.start()
        self.feed.view = VIEW
        with self.assertRaises(ValueError):
            self.feed.start('yes')
        with patch('runtime.observer_live.credentials', return_value={'OPENROUTER_API_KEY': ''}):
            with self.assertRaisesRegex(ValueError, 'OPENROUTER'):
                self.feed.start(True)
            self.feed.start(False)
        with self.assertRaises(ValueError):
            self.feed.heartbeat('wrong-session')

    def test_abandoned_viewer_expires_and_closes_camera(self):
        with patch('runtime.observer_live.LEASE_SECONDS', .1):
            self.feed.start(False)
            wait_until(lambda: self.closed.is_set())
        self.assertFalse(self.feed.state()['active'])

    def test_identification_failure_keeps_video_running(self):
        def detector(*_):
            raise RuntimeError('credential-like-sensitive-error')
        self.feed.detector = detector
        self.feed.start()
        wait_until(lambda: self.feed.state()['analysis_error'])
        self.assertTrue(self.feed.state()['active'])
        self.assertNotIn('sensitive', self.feed.state()['analysis_error'])
        self.assertIsNone(self.feed.state()['observation'])

    def test_camera_failure_ends_stream_and_hides_internal_details(self):
        @asynccontextmanager
        async def source(*_):
            raise RuntimeError('private machine address')
            yield
        self.feed.source = source
        self.feed.start(False)
        wait_until(lambda: self.feed.state()['status'] == 'failed')
        self.assertFalse(self.feed.state()['active'])
        self.assertNotIn('private', self.feed.state()['error'])

    def test_identified_frame_history_is_bounded(self):
        with patch('runtime.observer_live.IDENTIFY_INTERVAL', .01):
            self.feed.start()
            wait_until(lambda: self.feed.state()['frames'] >= 5)
            self.assertLessEqual(len(self.feed.identified_frames), 2)

    def test_jpeg_is_bounded_and_rejects_non_images(self):
        with Image.open(io.BytesIO(jpeg(IMAGE))) as image:
            self.assertEqual(image.size, (960, 720))
        with self.assertRaises(ValueError):
            jpeg(b'not an image')

    def test_camera_adapter_only_reads_configured_camera_and_closes(self):
        from types import SimpleNamespace
        robot = SimpleNamespace(close=AsyncMock())
        camera = SimpleNamespace(get_images=AsyncMock(return_value=(
            [SimpleNamespace(data=IMAGE, mime_type='image/png')], SimpleNamespace(captured_at=None))))
        async def check():
            async with camera_source(CONFIG, VIEW) as read:
                data, stamp = await read()
                self.assertEqual(data, IMAGE)
                self.assertIsInstance(stamp, str)
        with patch('runtime.observer_live.connect', new_callable=AsyncMock, return_value=robot) as connect, \
             patch('viam.components.camera.Camera.from_robot', return_value=camera) as get_camera:
            asyncio.run(check())
        connect.assert_awaited_once_with(managed_reconnect=False)
        get_camera.assert_called_once_with(robot, CONFIG['resources']['camera'])
        camera.get_images.assert_awaited_once_with(timeout=8)
        robot.close.assert_awaited_once()
