import copy
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from PIL import Image

from runtime.observer import Observer, handler, read_run
from runtime.observer_vision import image_type, observe, validate_observation, select_objects
from runtime.config import DEFAULT_CONFIG, read_json

buffer = io.BytesIO()
Image.new('RGB', (640, 480), 'white').save(buffer, format='PNG')
IMAGE = buffer.getvalue()
CONFIG = read_json(DEFAULT_CONFIG)
CONFIG['objects'] = ['cup', 'pitcher']


def observation():
    return {'summary': 'One visible cup.', 'detections': [
        {'object_id': 'cup', 'bbox': [0.2, 0.3, 0.5, 0.8], 'visibility': 'clear', 'note': 'Side visible.'}]}


class VisionTests(unittest.TestCase):
    def test_shaker_is_a_distinct_display_label_without_changing_task_objects(self):
        config = copy.deepcopy(CONFIG)
        self.assertEqual(select_objects(config), ['pitcher', 'cup', 'shaker'])
        self.assertNotIn('shaker', config['objects'])
        self.assertEqual(select_objects(config, 'shaker,pitcher'), ['shaker', 'pitcher'])
        value = observation()
        value['detections'].append({'object_id': 'shaker', 'bbox': [.6, .1, .9, .7],
                                    'visibility': 'clear', 'note': 'Capped metal bottle.'})
        result = validate_observation(value, select_objects(config))
        self.assertEqual(result['detections'][1]['object_id'], 'shaker')
        self.assertFalse(result['motion_ready'])

    def test_valid_observation_never_becomes_a_target(self):
        result = validate_observation(observation(), CONFIG['objects'])
        self.assertFalse(result['motion_ready'])
        self.assertNotIn('pose', result)
        self.assertNotIn('frame', result)

    def test_rejects_invalid_boxes_and_identities(self):
        for box in ([0.7, 0.2, 0.3, 0.4], [0, 0, 1.01, 1], [0, 0, float('nan'), 1],
                    [False, 0, 1, 1], [0, 1], 'box'):
            with self.subTest(box=box):
                value = observation()
                value['detections'][0]['bbox'] = box
                with self.assertRaises(ValueError):
                    validate_observation(value, CONFIG['objects'])
        value = observation()
        value['detections'][0]['object_id'] = 'invented'
        with self.assertRaises(ValueError):
            validate_observation(value, CONFIG['objects'])
        value = observation()
        value['detections'].append(copy.deepcopy(value['detections'][0]))
        with self.assertRaises(ValueError):
            validate_observation(value, CONFIG['objects'])

    def test_rejects_pose_injection(self):
        value = observation()
        value['detections'][0]['pose'] = {'x': 2}
        with self.assertRaises(ValueError):
            validate_observation(value, CONFIG['objects'])

    def test_empty_scene_is_valid(self):
        self.assertEqual(validate_observation({'summary': 'No objects.', 'detections': []}, ['cup'])['detections'], [])

    def test_image_validation(self):
        self.assertEqual(image_type(IMAGE), 'image/png')
        for data in (b'', b'<svg></svg>', b'secret'):
            with self.assertRaises(ValueError):
                image_type(data)

    @patch('primitives.vision.credentials', return_value={'OPENROUTER_API_KEY': '', 'OPENROUTER_VISION_MODEL': 'test-model'})
    @patch('primitives.vision.urlopen')
    def test_missing_key_does_not_call_network(self, remote, _credentials):
        with self.assertRaisesRegex(ValueError, 'OPENROUTER_API_KEY'):
            observe(IMAGE, ['cup'])
        remote.assert_not_called()

    @patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-secret'})
    @patch('primitives.vision.urlopen')
    def test_openrouter_contract(self, remote):
        raw = observation()
        raw['detections'][0]['bbox'] = [200, 300, 500, 800]
        payload = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(raw)}}]}
        remote.return_value = io.BytesIO(json.dumps(payload).encode())
        result = observe(IMAGE, CONFIG['objects'])
        request = remote.call_args.args[0]
        body = json.loads(request.data)
        self.assertTrue(body['provider']['require_parameters'])
        self.assertEqual(body['response_format']['type'], 'json_schema')
        self.assertTrue(body['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-secret')
        self.assertFalse(result['motion_ready'])

    @patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-secret'})
    @patch('primitives.vision.urlopen')
    def test_truncated_response_is_rejected(self, remote):
        payload = {'choices': [{'finish_reason': 'length', 'message': {'content': json.dumps(observation())}}]}
        remote.return_value = io.BytesIO(json.dumps(payload).encode())
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            observe(IMAGE, ['cup'])

    @patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-secret'})
    @patch('primitives.vision.urlopen')
    def test_provider_errors_do_not_leak_response(self, remote):
        remote.side_effect = HTTPError('https://openrouter.ai', 401, 'test-secret', {}, None)
        with self.assertRaises(ValueError) as raised:
            observe(IMAGE, ['cup'])
        self.assertIn('401', str(raised.exception))
        self.assertNotIn('test-secret', str(raised.exception))


class ObserverTests(unittest.TestCase):
    def test_replacing_image_clears_detection(self):
        observer = Observer(CONFIG)
        first = observer.set_frame(IMAGE, 'first')
        observer.observation = observation()
        second = observer.set_frame(IMAGE, 'second')
        self.assertNotEqual(first['id'], second['id'])
        self.assertIsNone(observer.state()['observation'])
        self.assertNotIn('data', observer.state()['frame'])

    @patch('runtime.observer.observe')
    def test_stale_scan_cannot_attach_to_new_frame(self, remote):
        observer = Observer(CONFIG)
        observer.set_frame(IMAGE, 'first')
        def change_frame(*_args):
            observer.set_frame(IMAGE, 'second')
            return observation()
        remote.side_effect = change_frame
        with self.assertRaisesRegex(ValueError, 'changed'):
            observer.scan()
        self.assertIsNone(observer.observation)

    def test_run_tail_handles_partial_lines_and_hides_arguments(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            (root / 'result.json').write_text(json.dumps({'mode': 'mock', 'status': 'running'}))
            (root / 'events.jsonl').write_text(json.dumps({'step': 'capture', 'status': 'started', 'args': {'private': 1}}) + '\n{"step":')
            result = read_run(root)
            self.assertEqual(result['mode'], 'mock')
            self.assertEqual(result['events'], [{'step': 'capture', 'status': 'started'}])
            (root / 'result.json').write_text('[]')
            self.assertEqual(read_run(root)['status'], 'waiting')


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.observer = Observer(CONFIG)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.observer))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.observer.live.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, body=None, origin=None):
        request = Request(self.origin + path, data=json.dumps(body).encode() if body is not None else None,
                          headers={'Content-Type': 'application/json', 'Origin': origin or self.origin})
        return urlopen(request)

    def test_serves_only_known_assets(self):
        with self.request('/') as response:
            self.assertIn(b'Your robot barista', response.read())
        for path in ('/.env', '/../../.env', '/api/execute', '/config/local.json'):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.request(path)
            self.assertEqual(error.exception.code, 404)

    def test_cross_origin_scan_is_rejected(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/scan', {}, 'https://unrelated.example')
        self.assertEqual(error.exception.code, 403)

    def test_no_motion_endpoint(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/execute', {'plan': 'anything'})
        self.assertEqual(error.exception.code, 404)

    def test_live_start_is_explicit_and_same_origin(self):
        with patch.object(self.observer.live, 'start', return_value={'status': 'connecting'}) as start:
            with self.assertRaises(HTTPError):
                self.request('/api/live/start')
            with self.assertRaises(HTTPError):
                self.request('/api/live/start', {'identify': True}, 'https://unrelated.example')
            start.assert_not_called()
            with self.request('/api/live/start', {'identify': False}) as response:
                self.assertEqual(json.load(response)['status'], 'connecting')
            start.assert_called_once_with(False)

    def test_live_stop_and_heartbeat_are_not_blocked_by_slow_operations(self):
        live = self.observer.live
        live.session = 'test-session'
        live.stop_event.clear()
        self.observer.operation.acquire()
        try:
            with self.request('/api/live/heartbeat', {'session': live.session}) as response:
                self.assertTrue(json.load(response)['ok'])
            with self.request('/api/live/stop', {}) as response:
                self.assertFalse(json.load(response)['active'])
        finally:
            self.observer.operation.release()

    def test_stream_emits_jpeg_and_never_starts_a_camera_on_get(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/live.mjpg?session=inactive')
        self.assertEqual(error.exception.code, 409)
        self.assertIsNone(self.observer.live.thread)
        live = self.observer.live
        live.session = 'test-session'
        live.stop_event.clear()
        live._publish(IMAGE, '2026-09-19T15:00:00+00:00')
        with self.request('/api/live.mjpg?session=test-session') as response:
            self.assertIn('multipart/x-mixed-replace', response.headers['Content-Type'])
            self.assertEqual(response.readline(), b'--frame\r\n')
            self.assertEqual(response.readline(), b'Content-Type: image/jpeg\r\n')
            size = int(response.readline().split(b':')[1])
            self.assertEqual(response.readline(), b'\r\n')
            self.assertEqual(response.read(size), live.frame['data'])
            live.stop()

    def test_identified_images_are_selected_by_exact_frame_id(self):
        self.observer.live.identified_frames['exact'] = IMAGE
        with self.request('/api/live/identified?id=exact') as response:
            self.assertEqual(response.read(), IMAGE)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/live/identified?id=expired')
        self.assertEqual(error.exception.code, 404)

    def test_claudia_request_previews_fixed_routine(self):
        with self.request('/api/request', {'text': 'Claudia, help pour a drink'}) as response:
            value = json.load(response)
        self.assertEqual(value['status'], 'preview')
        self.assertEqual(value['recipe'][0], 'Pour coconut water')
        self.assertIsNone(self.observer.demo.thread)
        with self.request('/api/request', {'text': 'pour 500ml then shake'}) as response:
            self.assertEqual(json.load(response)['intent'], 'unsupported')

    def test_voice_request_requires_separate_review(self):
        with self.request('/api/request', {'text': 'pour a drink', 'review_only': True}) as response:
            self.assertEqual(json.load(response)['status'], 'review')
        with self.request('/api/request', {'text': 'hello Claudia', 'review_only': True}) as response:
            self.assertEqual(json.load(response)['intent'], 'chat')
        self.assertIsNone(self.observer.demo.thread)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/request', {'text': 'pour a drink', 'review_only': 'false'})
        self.assertEqual(error.exception.code, 400)

    def test_cross_origin_drink_request_is_rejected(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/request', {'text': 'pour a drink'}, 'https://unrelated.example')
        self.assertEqual(error.exception.code, 403)

    def test_upload_and_state_without_key_or_robot(self):
        import base64
        with self.request('/api/image', {'data': base64.b64encode(IMAGE).decode()}) as response:
            self.assertIn('frame_id', json.load(response))
        with self.request('/api/state') as response:
            value = json.load(response)
            self.assertEqual(value['frame']['source'], 'Uploaded image')
            self.assertFalse(value['capture_enabled'])
            self.assertIsNone(value['observation'])
        with self.request('/api/frame') as response:
            self.assertEqual(response.read(), IMAGE)

    def test_busy_operations_are_rejected(self):
        self.observer.operation.acquire()
        try:
            with self.assertRaises(HTTPError) as error:
                self.request('/api/scan', {})
            self.assertEqual(error.exception.code, 409)
        finally:
            self.observer.operation.release()


if __name__ == '__main__':
    unittest.main()
