"""One fixed, opt-in Claudia routine, using the existing supervised replay."""
import asyncio
import re
import sys
import threading
from pathlib import Path
from uuid import uuid4

from .config import ROOT, read_json
from .demo_pack import verify
from .fixed_tasks import RECIPES

RECIPE = ['Pour coconut water', 'Return the carton', 'Pour from the pitcher', 'Return the pitcher']
CHAT = {
    'greeting': (('', 'hello', 'hi', 'hey', 'hello claudia'),
                 'Well, hello. I’m Claudia. One signature pour, plenty of personality. What can I do for you?'),
    'wellbeing': (('how are you', 'how are you doing'),
                  'Perfectly calibrated for small talk. My pouring still needs the operator’s checks. How glamorous.'),
    'thanks': (('thanks', 'thank you', 'thank you claudia'),
               'You’re welcome. Excellent manners. I’ll put in a good word with the appliances.'),
    'identity': (('who are you', 'what is your name', 'what s your name'),
                 'I’m Claudia, Varista’s robot bartender. Human-taught moves. Self-appointed star of the counter.'),
    'menu': (('what can you do', 'what can you make', 'what is on the menu', 'what s on the menu', 'help'),
             'The signature: coconut water, then a pour from the pitcher. A very exclusive menu. Ask me to pour a drink, or ask what I see.'),
    'joke': (('tell me a joke', 'say something sassy', 'be sassy'),
             'I have one drink on the menu and somehow you’re still deciding. Take your time, darling.'),
    'goodbye': (('bye', 'goodbye', 'see you later'),
                'Until next time. Try not to miss the charming machinery too much.'),
}


async def inspect_station(config):
    """Read stopped/held/home state without preparing hardware or commanding motion."""
    from .connection import connect
    from primitives import motion, gripper
    from primitives.types import Context
    from viam.components.arm import Arm
    from viam.components.gripper import Gripper
    robot = await connect(managed_reconnect=False)
    try:
        ctx = Context(config, robot)
        tuning = motion.settings(config)
        arm = Arm.from_robot(robot, config['resources']['arm'])
        hand = Gripper.from_robot(robot, config['resources']['gripper'])
        moving, holding, pose = await asyncio.gather(
            arm.is_moving(timeout=8),
            gripper.held(hand, 8, required=True, setting='stopped-task inspection'),
            motion.tool_pose(ctx))
        if type(moving) is not bool or type(holding) is not bool:
            raise ValueError('The station did not report a known moving/held state.')
        delta = motion.deviation(pose, tuning['origin_pose'])
        return {'moving': moving, 'holding': holding, **delta,
                'at_home': delta['position_error_mm'] <= tuning['position_tolerance_mm']
                and delta['orientation_error_deg'] <= tuning['orientation_tolerance_deg']}
    finally:
        await asyncio.wait_for(robot.close(), 5)


def failure_message(exc):
    """Publish actionable known failure categories without leaking raw SDK details."""
    message = str(exc)
    if 'Emergency Stop Button Pushed In' in message:
        return ('The robot reported its emergency-stop button is pressed. An operator must inspect the station '
                'and restore hardware readiness before Reset. The website cannot release the emergency stop.')
    if 'Demo dependency changed:' in message or 'Runtime file list changed' in message:
        return 'The execution package no longer matches the code. Refresh the package and restart the website before another attempt.'
    if 'self-collision constraint' in message:
        if 'arm:wrist_link' in message and 'cam_origin' in message:
            return ('Motion planner blocked a predicted collision between the wrist and camera geometry. '
                    'The pour may be partial. Inspect the held object and recover with Reset; '
                    'the taught path or collision geometry needs review before another pour.')
        return ('Motion planner blocked a predicted self-collision. Inspect the station and recover with Reset; '
                'the blocked movement cannot be continued safely.')
    if 'all IK solutions failed constraints' in message:
        return 'No motion plan satisfied the station constraints. Inspect the station and recover before retrying.'
    if message == 'Operator aborted':
        return 'Operator aborted the routine.'
    if message == 'Operator did not confirm the phase gate':
        return 'Operator declined the check.'
    if isinstance(exc, asyncio.CancelledError):
        return 'Routine cancelled.'
    return 'The routine failed. Inspect the station before a fresh attempt.'


def intent(text):
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 500:
        raise ValueError('Enter a request of 1–500 characters.')
    words = re.sub(r'[^a-z0-9 ]', ' ', text.lower())
    words = ' '.join(words.split())
    words = re.sub(r'^(?:hey |hi )?claudia\b *', '', words)
    words = re.sub(r'^please ', '', words)
    words = re.sub(r' please$', '', words)
    for name, (phrases, _) in CHAT.items():
        if words in phrases:
            return name
    if words in ('what do you see', 'what can you see', 'analyze scene', 'scan scene'):
        return 'scene'
    if words in ('help pour a drink', 'pour a drink', 'make me a drink', 'make a drink',
                 'can you help pour a drink', 'can you pour a drink',
                 'prepare the coconut and pitcher demo', 'the signature pour', 'signature pour',
                 'pour signature drink'):
        return 'signature'
    if words in ('reset', 'reset station'):
        return 'reset'
    if words in ('shake', 'shake held object'):
        return 'shake'
    return 'unsupported'


class ClaudiaDemo:
    def __init__(self, *, pack=None, config_path=None, enabled=False, browser_gates=False):
        self.pack = Path(pack).resolve() if pack else None
        self.config_path = Path(config_path).resolve() if config_path else None
        self.enabled = enabled
        self.browser_gates = browser_gates
        self.gate_id = self.gate_future = None
        self.gate_answered = False
        self.failure_id = None
        self.failure_reason = None
        self.reset_required = False
        self.lock = threading.RLock()
        self.thread = self.loop = self.task = None
        self.closing = False
        self.run_root = None
        self.status, self.prompt = 'idle', None
        self.selected = 'signature'
        if enabled:
            if not self.pack or not self.config_path:
                raise ValueError('Demo execution requires --demo-pack and an exact --config.')
            if not browser_gates and not sys.stdin.isatty():
                raise ValueError('Start demo execution in an interactive operator terminal.')
            verify(self.pack, self.config_path)

    def state(self):
        with self.lock:
            return {'enabled': self.enabled, 'status': self.status, 'prompt': self.prompt,
                    'gate_id': self.gate_id if not self.gate_answered else None,
                    'browser_gates': self.browser_gates,
                    'failure_id': self.failure_id, 'failure_reason': self.failure_reason,
                    'reset_required': self.reset_required,
                    'task': self.selected, 'recipe': RECIPES[self.selected],
                    'active': bool(self.thread and self.thread.is_alive())}

    def run_directory(self):
        with self.lock:
            root = self.run_root
        # This root belongs to exactly one request, never an unrelated latest run.
        paths = list(root.glob('*/episode_*')) if root else []
        return paths[0] if len(paths) == 1 else None

    def request(self, text, *, review_only=False):
        if not isinstance(review_only, bool):
            raise ValueError('review_only must be a boolean.')
        selected = intent(text)
        if selected in CHAT:
            return {'intent': 'chat', 'message': CHAT[selected][1]}
        if selected == 'scene':
            return {'intent': selected}
        if selected == 'unsupported':
            return {'intent': selected, 'message': 'Choose signature pour, reset, or shake held object. Shaker pickup and automatic object identification for reset are unavailable.'}
        if review_only:
            return {'intent': selected, 'status': 'review', 'recipe': RECIPES[selected],
                    'message': 'Review this fixed task and press send. No motion has started.'}
        with self.lock:
            if self.closing:
                raise ValueError('Varista is shutting down.')
            if not self.enabled:
                return {'intent': selected, 'status': 'preview', 'recipe': RECIPES[selected],
                        'message': 'Preview: ' + ' → '.join(RECIPES[selected]) + '. No robot motion.'}
            if self.thread and self.thread.is_alive():
                raise ValueError('Claudia already has a drink request in progress. Follow the operator checks.')
            if self.status == 'failed':
                raise ValueError('The routine stopped. Inspect the station, then use Clear stopped task before a fresh attempt.')
            if self.reset_required and selected != 'reset':
                raise ValueError('Run Reset first to return the identified object and reach home with an empty hand.')
            verify(self.pack, self.config_path)
            if selected != 'signature':
                from .fixed_tasks import validate
                validate(selected, self.pack, self.config_path)
            self.selected = selected
            self.run_root = ROOT / 'runs' / 'claudia' / uuid4().hex
            self.status, self.prompt = 'waiting_operator', None
            self.thread = threading.Thread(target=self._worker, name='claudia-demo')
            self.thread.start()
            return {'intent': selected, 'status': 'waiting_operator', 'recipe': RECIPES[selected],
                    'message': 'Waiting for the operator’s checks: ' + ' → '.join(RECIPES[selected]) + '.'}

    def answer(self, gate_id, value):
        if not isinstance(value, str) or not 0 < len(value.strip()) <= 500:
            raise ValueError('Enter the requested observation or confirmation, or q to abort.')
        with self.lock:
            if (not self.enabled or not self.browser_gates or self.closing
                    or not gate_id or gate_id != self.gate_id or self.gate_answered
                    or self.gate_future is None or self.gate_future.done()):
                raise ValueError('This operator check is no longer pending. Read the current check.')
            future = self.gate_future
            self.gate_answered = True
            def deliver():
                if not future.done():
                    future.set_result(value.strip())
            future.get_loop().call_soon_threadsafe(deliver)
        return {'ok': True}

    async def clear_failure(self, failure_id, held, inspected):
        if inspected is not True or held not in ('empty', 'coconut_water', 'pitcher'):
            raise ValueError('Inspect the station and identify an empty hand, coconut carton, or pitcher. Unknown objects require manual recovery.')
        def check_pending():
            if (not self.enabled or self.closing or self.status != 'failed'
                    or not failure_id or failure_id != self.failure_id
                    or (self.thread and self.thread.is_alive())):
                raise ValueError('This stopped task is no longer available to clear. Refresh its state.')
        with self.lock:
            check_pending()
        verify(self.pack, self.config_path)
        config = read_json(self.config_path)
        try:
            observed = await inspect_station(config)
        except Exception as exc:
            raise ValueError('Could not verify the stopped arm and gripper. Check the station connection and retry the inspection.') from exc
        if observed['moving'] is not False:
            raise ValueError('The arm is still moving. Stop it and inspect the station before clearing this task.')
        if observed['holding'] is not (held != 'empty'):
            raise ValueError('Gripper feedback disagrees with the selected held state. Inspect the gripper before continuing.')
        verify(self.pack, self.config_path)
        from .demonstrations import now, write
        with self.lock:
            check_pending()
            reset_required = held != 'empty' or not observed['at_home']
            evidence = {'checked_at': now(), 'failure_id': failure_id,
                        'operator_inspected': True, 'observed_held_object': held,
                        'station': observed, 'reset_required': reset_required,
                        'action': 'clear_failure_only', 'motion_started': False}
            if self.run_root:
                self.run_root.mkdir(parents=True, exist_ok=True)
                write(self.run_root / ('recovery_' + failure_id + '.json'), evidence)
            self.reset_required = reset_required
            self.failure_id = self.failure_reason = None
            self.status, self.prompt = 'idle', None
        return {'ok': True, 'reset_required': reset_required,
                'message': 'Stopped task cleared. Run Reset before Pour.' if reset_required
                           else 'Stopped task cleared. Choose a fresh task; no motion has started.'}

    async def _ask(self, prompt):
        from .demonstrations import ask
        with self.lock:
            self.status, self.prompt = 'waiting_operator', prompt
            if self.browser_gates:
                self.gate_future = asyncio.get_running_loop().create_future()
                self.gate_id = uuid4().hex
                self.gate_answered = False
        try:
            result = await self.gate_future if self.browser_gates else await ask(prompt)
            if result.lower() in ('q', 'quit', 'abort'):
                raise RuntimeError('Operator aborted')
        finally:
            with self.lock:
                self.gate_id = self.gate_future = None
                self.prompt = None
        with self.lock:
            self.status, self.prompt = 'running', None
        return result

    async def _perform(self):
        if self.selected != 'signature':
            from .fixed_tasks import execute
            await execute(self.selected, self.pack, self.config_path, ask_fn=self._ask, runs=self.run_root)
            return
        from .__main__ import check_resources
        from .connection import connect
        from .demonstrations import ViamIO, confirm
        from .replay import run_demo
        from primitives.types import Context
        manifest = verify(self.pack, self.config_path)
        config = read_json(self.config_path)
        options = {'root': self.pack / 'demonstrations', 'compact': manifest['compact']}
        await run_demo(None, config, **options)  # Offline validation before connection.
        await confirm('Claudia will pour coconut water, return it, then pour and return the pitcher. '
                      'Physical run intended, arm at saved home, hand empty, fixed cup in place, '
                      'free-drive OFF and full swept paths clear?', self._ask)
        # Reject drift during the operator wait as well as at request time.
        verify(self.pack, self.config_path)
        robot = await connect()
        try:
            check_resources(robot, config)
            await run_demo(ViamIO(Context(config, robot)), config, runs=self.run_root,
                           execute=True, ask_fn=self._ask, **options)
        finally:
            await asyncio.wait_for(robot.close(), 10)

    def _worker(self):
        async def run():
            with self.lock:
                self.loop, self.task = asyncio.get_running_loop(), asyncio.current_task()
            try:
                if self.closing:
                    raise asyncio.CancelledError()
                await self._perform()
                with self.lock:
                    self.status = 'completed'
                    if self.selected == 'reset':
                        self.reset_required = False
            except BaseException as exc:
                print(f'Claudia routine stopped: {type(exc).__name__}: {exc}', flush=True)
                with self.lock:
                    self.status = 'failed'
                    self.failure_id = uuid4().hex
                    self.failure_reason = failure_message(exc)
            finally:
                with self.lock:
                    self.prompt = None
                    self.loop = self.task = None
        asyncio.run(run())

    def close(self):
        with self.lock:
            self.closing = True
            if self.loop and self.task:
                self.loop.call_soon_threadsafe(self.task.cancel)
            thread = self.thread
        if thread:
            thread.join()
