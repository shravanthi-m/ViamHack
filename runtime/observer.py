"""Varista presentation with optional, fixed supervised Claudia replay."""
import asyncio
import base64
import json
import secrets
import signal
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from primitives.vision import MAX_IMAGE_BYTES, image_info, observe, credentials, validate_observation
from primitives import localization
from .observer_audio import transcribe
from .observer_demo import ClaudiaDemo
from .observer_live import LiveFeed
from .observer_vision import select_objects

WEB = Path(__file__).with_name('observer_web')


def now():
    return datetime.now(timezone.utc).isoformat()


def read_run(directory):
    """Tail only the operator-selected run. Ignore a partially written final line."""
    if directory is None:
        return {'mode': 'unattached', 'status': 'idle', 'events': []}
    root = Path(directory)
    result = {}
    try:
        result = json.loads((root / 'result.json').read_text())
    except (OSError, ValueError):
        pass
    if not isinstance(result, dict):
        result = {}
    events = []
    try:
        with (root / 'events.jsonl').open('rb') as source:
            size = source.seek(0, 2)
            start = max(0, size - 128 * 1024)
            source.seek(start)
            if start:
                source.readline()
            for line in source:
                if not line.endswith(b'\n'):
                    continue
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        # Don't publish arbitrary arguments, paths or machine configuration.
                        events.append({k: row[k] for k in
                                       ('time', 'timestamp', 'step', 'tool', 'status', 'object') if k in row})
                except (ValueError, UnicodeDecodeError):
                    continue
    except OSError:
        pass
    return {'mode': result.get('mode', 'recorded'), 'status': result.get('status', 'waiting'),
            'name': root.name, 'events': events[-40:]}


class Observer:
    def __init__(self, config, *, image=None, run=None, view=None, demo=None, objects=None, observation=None):
        self.config, self.run, self.view = config, run, view
        self.objects = select_objects(config, objects)
        self.demo = demo or ClaudiaDemo()
        self.live = LiveFeed(config, view, self.objects)
        self.lock, self.operation = threading.Lock(), threading.Lock()
        self.frame, self.observation = None, None
        if image:
            self.set_frame(Path(image).read_bytes(), 'Loaded image')
        if observation:
            saved = json.loads(Path(observation).read_text())
            if not self.frame or saved.get('image', {}).get('sha256') != self.frame['sha256']:
                raise ValueError('Saved detections must match the loaded image exactly')
            validate_observation({k: saved[k] for k in ('summary', 'detections')}, self.objects)
            self.observation = {**saved, 'motion_ready': False, 'frame_id': self.frame['id'],
                'localization': {item['object_id']: {'status': 'blocked', 'reason': 'Saved image; fresh observation required for motion'}
                                 for item in saved['detections']}}

    def set_frame(self, data, source, captured_at=None):
        info = image_info(data)
        frame = {'id': secrets.token_hex(8), 'data': data, **info,
                 'source': source, 'captured_at': captured_at, 'loaded_at': now()}
        with self.lock:
            self.frame, self.observation = frame, None
        return frame

    def state(self):
        with self.lock:
            frame = {k: v for k, v in self.frame.items() if k != 'data'} if self.frame else None
            observation = self.observation
        provider = credentials()
        demo = self.demo.state()
        run_directory = self.demo.run_directory()
        owns_run = demo['enabled'] and demo['status'] != 'idle'
        run = read_run(run_directory if owns_run else self.run)
        if owns_run:
            run['mode'] = 'execute'
            if not run_directory:
                run['status'] = 'failed' if demo['status'] == 'failed' else 'waiting'
        live = self.live.state()
        return {'api_version': 2, 'frame': frame, 'observation': observation, 'run': run, 'demo': demo, 'live': live,
                'vision_configured': bool(provider['OPENROUTER_API_KEY']),
                'model': provider['OPENROUTER_VISION_MODEL'],
                'stt_model': provider['OPENROUTER_STT_MODEL'],
                'localization': localization.readiness(self.config),
                'capture_enabled': self.view is not None and not demo['active'] and not live['active'],
                'live_enabled': self.view is not None and not demo['active'], 'view': self.view,
                'objects': self.objects}

    async def capture(self):
        from primitives.camera import capture
        from primitives.types import Context
        from .connection import connect
        from .config import ROOT
        if self.view is None:
            raise ValueError('Start with --view to enable station snapshots.')
        if self.demo.state()['active']:
            raise ValueError('Claudia is using the station. Wait for the routine to finish.')
        if self.live.state()['active']:
            raise ValueError('Stop live view before taking a separate snapshot.')
        robot = await connect()
        try:
            result = await capture(Context(self.config, robot), view=self.view)
            for item in result['images']:
                if item['mime_type'] in ('image/jpeg', 'image/png', 'image/webp'):
                    path = Path(item['path'])
                    frame = self.set_frame((path if path.is_absolute() else ROOT / path).read_bytes(),
                                           self.view + ' camera snapshot', result['captured_at'])
                    return {'frame_id': frame['id']}
            raise ValueError('The camera returned no supported colour image.')
        finally:
            await asyncio.wait_for(robot.close(), 10)

    def scan(self):
        if self.demo.state()['active']:
            raise ValueError('Wait for the current routine to finish before scanning.')
        live = self.live.state()
        if live['active']:
            return self.live.scan()
        with self.lock:
            frame = self.frame
        if frame is None:
            raise ValueError('Load an image or capture a snapshot first.')
        result = {**observe(frame['data'], self.objects),
                  'frame_id': frame['id'], 'observed_at': now()}
        result['localization'] = {}
        for item in result['detections']:
            identity = item['object_id']
            try:
                if identity not in self.config['objects']:
                    raise ValueError('Display-only object; no configured robot manipulation target.')
                if not frame['captured_at'] or self.view is None:
                    raise ValueError('Saved/uploaded image: capture a fresh calibrated camera image for robot localization')
                target = localization.target_from_observation(self.config, identity, result,
                                                              view=self.view, captured_at=frame['captured_at'])
                result['localization'][identity] = {'status': 'target_estimated', 'target': target,
                    'note': 'Requires the fixed orientation/height scene gate; image analysis does not authorize motion'}
            except ValueError as exc:
                result['localization'][identity] = {'status': 'blocked', 'reason': str(exc)}
        with self.lock:
            if self.frame['id'] != frame['id']:
                raise ValueError('The image changed during analysis. Scan the new image.')
            self.observation = result
        return result


def handler(observer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, value, status=200, mime='application/json'):
            data = json.dumps(value, allow_nan=False).encode() if mime == 'application/json' else value
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def valid_host(self):
            return self.headers.get('Host') in (
                f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def stream(self, session):
            live = observer.live.state()
            if not live['active'] or live['session'] != session:
                return self.respond({'error': 'Start live view first.'}, 409)
            self.connection.settimeout(5)
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
            self.end_headers()
            previous = None
            try:
                while observer.live.state()['active'] and observer.live.session == session:
                    frame = observer.live.next_frame(session, previous)
                    if frame is None:
                        continue
                    previous = frame['id']
                    data = frame['data']
                    self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '
                                     + str(len(data)).encode() + b'\r\n\r\n' + data + b'\r\n')
                    self.wfile.flush()
            except (OSError, TimeoutError):
                pass
            self.close_connection = True

        def do_GET(self):
            if not self.valid_host():
                return self.respond({'error': 'Use the local observer address.'}, 403)
            path = urlsplit(self.path).path
            if path in ('/api/live.mjpg', '/api/live/identified'):
                if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                    return self.respond({'error': 'Use the observer page.'}, 403)
                query = parse_qs(urlsplit(self.path).query)
                if path == '/api/live.mjpg':
                    return self.stream(query.get('session', [''])[0])
                data = observer.live.identified(query.get('id', [''])[0])
                if data is None:
                    return self.respond({'error': 'Identified frame expired.'}, 404)
                return self.respond(data, mime='image/jpeg')
            if path == '/api/state':
                return self.respond(observer.state())
            if path == '/api/frame':
                with observer.lock:
                    frame = observer.frame
                if not frame:
                    return self.respond({'error': 'No image loaded.'}, 404)
                return self.respond(frame['data'], mime=frame['mime'])
            assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                      '/style.css': ('style.css', 'text/css; charset=utf-8'),
                      '/cafe-editorial.png': ('cafe-editorial.png', 'image/png'),
                      '/cafe-lifestyle.png': ('cafe-lifestyle.png', 'image/png')}
            if path in assets:
                name, mime = assets[path]
                return self.respond((WEB / name).read_bytes(), mime=mime)
            return self.respond({'error': 'Not found.'}, 404)

        def do_POST(self):
            origin = self.headers.get('Origin')
            if not self.valid_host() or origin != 'http://' + self.headers.get('Host', ''):
                return self.respond({'error': 'Use the observer page for this action.'}, 403)
            if self.headers.get('Content-Type') != 'application/json':
                return self.respond({'error': 'JSON required.'}, 415)
            exclusive = self.path not in ('/api/live/stop', '/api/live/heartbeat')
            if exclusive and not observer.operation.acquire(blocking=False):
                return self.respond({'error': 'A snapshot or scan is still in progress.'}, 409)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= MAX_IMAGE_BYTES * 4 // 3 + 1024:
                    raise ValueError('Request is empty or too large.')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('Expected a JSON object.')
                if self.path == '/api/live/start':
                    if observer.demo.state()['active']:
                        raise ValueError('Wait for the current routine to finish before starting live view.')
                    result = observer.live.start(body.get('identify', True))
                elif self.path == '/api/live/stop':
                    result = observer.live.stop()
                elif self.path == '/api/live/heartbeat':
                    result = observer.live.heartbeat(body.get('session'))
                elif self.path == '/api/image':
                    encoded = body.get('data', '')
                    if not isinstance(encoded, str):
                        raise ValueError('Invalid image data.')
                    frame = observer.set_frame(base64.b64decode(encoded, validate=True), 'Uploaded image')
                    result = {'frame_id': frame['id']}
                elif self.path == '/api/capture':
                    result = asyncio.run(observer.capture())
                elif self.path == '/api/scan':
                    result = observer.scan()
                elif self.path == '/api/request':
                    result = observer.demo.request(body.get('text'), review_only=body.get('review_only', False))
                elif self.path == '/api/transcribe':
                    encoded = body.get('data', '')
                    if not isinstance(encoded, str):
                        raise ValueError('Invalid recording data.')
                    result = transcribe(base64.b64decode(encoded, validate=True), body.get('format'))
                else:
                    return self.respond({'error': 'Not found.'}, 404)
                self.respond(result)
            except ValueError as exc:
                self.respond({'error': str(exc)}, 400)
            except Exception:
                self.respond({'error': 'The observation failed. Check camera connectivity and local configuration.'}, 502)
            finally:
                if exclusive:
                    observer.operation.release()
    return Handler


def serve(args, config):
    from primitives.camera import resolve
    if args.view:
        resolve(config, args.view)
    demo = ClaudiaDemo(pack=args.demo_pack, config_path=args.config,
                       enabled=args.enable_demo_execution)
    observer = Observer(config, image=args.image, run=args.run, view=args.view, demo=demo,
                        objects=args.objects, observation=args.observation)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(observer))
    server.daemon_threads = True
    mode = 'supervised Claudia execution enabled' if demo.enabled else 'preview; no motion'
    print(f'Varista: http://127.0.0.1:{server.server_port} ({mode})', flush=True)
    def stop_server(_signum, _frame):
        raise KeyboardInterrupt
    previous_term = signal.signal(signal.SIGTERM, stop_server)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        observer.live.close()
        demo.close()
        signal.signal(signal.SIGTERM, previous_term)
