"""Read-only camera presentation with bounded, independently sampled identification.

Frames remain in memory. No motion primitive or localization is called here.
"""
import asyncio
import io
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from primitives.camera import resolve, kind_of, captured_at
from primitives.vision import image_info, observe, credentials
from .connection import connect

FPS = 5
IDENTIFY_INTERVAL = 3
LEASE_SECONDS = 20


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def jpeg(data):
    from PIL import Image
    image_info(data)
    with Image.open(io.BytesIO(data)) as source:
        source.thumbnail((960, 720))
        output = io.BytesIO()
        source.convert('RGB').save(output, 'JPEG', quality=80)
        return output.getvalue()


@asynccontextmanager
async def camera_source(config, view):
    from viam.components.camera import Camera
    resource = resolve(config, view)
    robot = await connect(managed_reconnect=False)
    try:
        camera = Camera.from_robot(robot, resource)
        async def read():
            images, metadata = await camera.get_images(timeout=8)
            for image in images:
                if kind_of(image) in ('image/jpeg', 'image/png', 'image/webp'):
                    return image.data, captured_at(metadata).isoformat()
            raise ValueError('The camera returned no supported colour image.')
        yield read
    finally:
        await asyncio.wait_for(robot.close(), 5)


class LiveFeed:
    def __init__(self, config, view, objects, *, source=camera_source, detector=observe):
        self.config, self.view, self.objects = config, view, objects
        self.source, self.detector = source, detector
        self.condition = threading.Condition(threading.RLock())
        self.stop_event = threading.Event()
        self.stop_event.set()
        self.thread = self.analysis_thread = None
        self.session = None
        self.status, self.error = 'stopped', None
        self.frame = self.observation = None
        self.identified_frames = {}
        self.analysis_error = None
        self.identify = self.analyzing = False
        self.frames = 0
        self.fps = 0
        self.lease = 0

    def start(self, identify=True):
        if not isinstance(identify, bool):
            raise ValueError('identify must be a boolean.')
        if self.view is None:
            raise ValueError('Start the observer with --view to enable live video.')
        resolve(self.config, self.view)
        if identify and not credentials()['OPENROUTER_API_KEY']:
            raise ValueError('Identification needs OPENROUTER_API_KEY; turn identification off for video only.')
        with self.condition:
            if any(worker and worker.is_alive() for worker in (self.thread, self.analysis_thread)):
                raise ValueError('Live view is already running or finishing its last request. Wait before restarting.')
            self.session = secrets.token_hex(12)
            self.stop_event = threading.Event()
            self.status, self.error = 'connecting', None
            self.frame = self.observation = None
            self.identified_frames.clear()
            self.analysis_error = None
            self.identify, self.analyzing = identify, False
            self.frames, self.fps = 0, 0
            self.lease = time.monotonic()
            self.thread = threading.Thread(target=self._run, name='counter-camera', daemon=True)
            self.analysis_thread = threading.Thread(target=self._identify, name='counter-identification', daemon=True) if identify else None
            self.thread.start()
            if self.analysis_thread:
                self.analysis_thread.start()
            return self.state()

    def heartbeat(self, session):
        with self.condition:
            if session != self.session or self.stop_event.is_set():
                raise ValueError('This live session has ended. Start live view again.')
            self.lease = time.monotonic()
        return {'ok': True}

    def stop(self):
        with self.condition:
            self.stop_event.set()
            if self.status != 'failed':
                self.status = 'stopped'
            self.analyzing = False
            self.condition.notify_all()
        return self.state()

    def close(self):
        self.stop()
        if self.thread:
            self.thread.join(timeout=7)
        # An HTTP analysis already in flight may finish, but cannot publish or retry.

    def state(self):
        with self.condition:
            frame = self.frame
            result = dict(self.observation) if self.observation else None
            if result:
                result['age_s'] = round(max(0, time.monotonic() - result.pop('_received')), 1)
            return {'session': self.session, 'status': self.status,
                    'active': not self.stop_event.is_set(), 'error': self.error,
                    'identify': self.identify, 'analyzing': self.analyzing,
                    'analysis_error': self.analysis_error, 'observation': result,
                    'frames': self.frames, 'fps': round(self.fps, 1),
                    'frame_age_s': round(time.monotonic() - frame['received'], 1) if frame else None,
                    'captured_at': frame['captured_at'] if frame else None}

    def identified(self, frame_id):
        with self.condition:
            return self.identified_frames.get(frame_id)

    def next_frame(self, session, previous=None):
        with self.condition:
            self.condition.wait_for(lambda: self.stop_event.is_set() or session != self.session
                                    or (self.frame and self.frame['id'] != previous), timeout=2)
            if self.stop_event.is_set() or session != self.session:
                return None
            return self.frame if self.frame and self.frame['id'] != previous else None

    def _publish(self, data, captured):
        data = jpeg(data)
        received = time.monotonic()
        with self.condition:
            if self.stop_event.is_set():
                return
            if self.frame:
                rate = 1 / max(received - self.frame['received'], .001)
                self.fps = rate if not self.fps else .8 * self.fps + .2 * rate
            self.frame = {'id': secrets.token_hex(8), 'data': data,
                          'captured_at': captured, 'received': received}
            self.frames += 1
            self.status = 'streaming'
            self.condition.notify_all()

    def _run(self):
        try:
            asyncio.run(self._session())
        except Exception:
            # SDK exceptions can contain machine addresses; keep the UI error generic.
            with self.condition:
                self.status = 'failed'
                self.error = 'Camera connection failed. Check the station connection, then restart live view.'
        finally:
            self.stop()

    async def _session(self):
        async def capture():
            async with self.source(self.config, self.view) as read:
                while not self.stop_event.is_set():
                    started = time.monotonic()
                    data, captured = await asyncio.wait_for(read(), 10)
                    self._publish(data, captured)
                    await asyncio.sleep(max(0, 1 / FPS - (time.monotonic() - started)))
        task = asyncio.create_task(capture())
        try:
            while not task.done():
                if self.stop_event.is_set() or time.monotonic() - self.lease > LEASE_SECONDS:
                    self.stop()
                    task.cancel()
                    break
                await asyncio.sleep(.2)
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def _identify(self):
        previous, errors = None, 0
        while not self.stop_event.is_set():
            with self.condition:
                self.condition.wait_for(lambda: self.stop_event.is_set()
                                        or (self.frame and self.frame['id'] != previous), timeout=1)
                if self.stop_event.is_set():
                    return
                frame = self.frame
                if not frame or frame['id'] == previous:
                    continue
                if time.monotonic() - frame['received'] > 3:
                    previous = frame['id']
                    continue
                self.analyzing = True
            started = time.monotonic()
            try:
                result = self.detector(frame['data'], self.objects)
                with self.condition:
                    if self.stop_event.is_set():
                        return
                    self.observation = {**result, 'motion_ready': False, 'frame_id': frame['id'],
                                        'captured_at': frame['captured_at'], 'observed_at': timestamp(),
                                        '_received': frame['received'],
                                        'latency_s': round(time.monotonic() - started, 1)}
                    self.identified_frames[frame['id']] = frame['data']
                    while len(self.identified_frames) > 2:
                        del self.identified_frames[next(iter(self.identified_frames))]
                    self.analysis_error = None
                errors = 0
            except Exception:
                errors += 1
                with self.condition:
                    self.analysis_error = 'Identification unavailable; video is still live. Check OpenRouter connectivity, credits and model.'
            finally:
                with self.condition:
                    self.analyzing = False
            previous = frame['id']
            if self.stop_event.wait(min(30, 5 * errors) if errors else IDENTIFY_INTERVAL):
                return
