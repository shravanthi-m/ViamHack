"""One fixed, opt-in Claudia routine, using the existing supervised replay."""
import asyncio
import re
import sys
import threading
from pathlib import Path
from uuid import uuid4

from .config import ROOT, read_json
from .demo_pack import verify

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
                 'prepare the coconut and pitcher demo', 'the signature pour', 'signature pour'):
        return 'signature'
    return 'unsupported'


class ClaudiaDemo:
    def __init__(self, *, pack=None, config_path=None, enabled=False):
        self.pack = Path(pack).resolve() if pack else None
        self.config_path = Path(config_path).resolve() if config_path else None
        self.enabled = enabled
        self.lock = threading.RLock()
        self.thread = self.loop = self.task = None
        self.closing = False
        self.run_root = None
        self.status, self.prompt = 'idle', None
        if enabled:
            if not self.pack or not self.config_path:
                raise ValueError('Demo execution requires --demo-pack and an exact --config.')
            if not sys.stdin.isatty():
                raise ValueError('Start demo execution in an interactive operator terminal.')
            verify(self.pack, self.config_path)

    def state(self):
        with self.lock:
            return {'enabled': self.enabled, 'status': self.status, 'prompt': self.prompt,
                    'recipe': RECIPE, 'active': bool(self.thread and self.thread.is_alive())}

    def run_directory(self):
        with self.lock:
            root = self.run_root
        # This root belongs to exactly one request, never an unrelated latest run.
        paths = list((root / 'demo').glob('episode_*')) if root else []
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
            return {'intent': selected, 'message': 'Ambitious. My repertoire is one signature pour and a little banter. Try “Claudia, help pour a drink,” “what can you do,” or “tell me a joke.”'}
        if review_only:
            return {'intent': selected, 'status': 'review', 'recipe': RECIPE,
                    'message': 'Excellent taste. Coconut water, then the pitcher. Review your request and press send. I haven’t started moving.'}
        with self.lock:
            if self.closing:
                raise ValueError('Varista is shutting down.')
            if not self.enabled:
                return {'intent': selected, 'status': 'preview', 'recipe': RECIPE,
                        'message': 'The signature. Excellent taste. Coconut water first, then the pitcher, returning each one. This is a preview; I haven’t started moving.'}
            if self.thread and self.thread.is_alive():
                raise ValueError('Claudia already has a drink request in progress. Follow the operator checks.')
            if self.status == 'failed':
                raise ValueError('The routine stopped. The operator must inspect and reset the station, then restart the server before another attempt.')
            verify(self.pack, self.config_path)
            self.run_root = ROOT / 'runs' / 'claudia' / uuid4().hex
            self.status, self.prompt = 'waiting_operator', None
            self.thread = threading.Thread(target=self._worker, name='claudia-demo')
            self.thread.start()
            return {'intent': selected, 'status': 'waiting_operator', 'recipe': RECIPE,
                    'message': 'One signature pour. Naturally. Coconut water, then the pitcher. I’m waiting for the operator’s station check before I begin.'}

    async def _ask(self, prompt):
        from .demonstrations import ask
        with self.lock:
            self.status, self.prompt = 'waiting_operator', prompt
        result = await ask(prompt)
        with self.lock:
            self.status, self.prompt = 'running', None
        return result

    async def _perform(self):
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
            except BaseException as exc:
                print(f'Claudia routine stopped: {type(exc).__name__}: {exc}', flush=True)
                with self.lock:
                    self.status = 'failed'
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
