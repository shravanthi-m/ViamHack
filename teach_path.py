"""Record a hand-guided arm path: type start, move the arm by hand, type stop.

Standalone teaching tool. It shares nothing with the main program: no project
imports, no station.json, its own connection. It only reads joint positions and
never commands the arm, so put the arm into manual / free-drive mode yourself
(UFactory Studio or the Viam control tab) before typing start, and take it back
out before running anything that moves.

    python teach_path.py --name coconut_pour
"""
import argparse
import asyncio
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from viam.components.arm import Arm
from viam.robot.client import RobotClient

load_dotenv()

DEFAULT_ADDRESS = 'armfarm7-main.310sld03v2.viam.cloud'
START = ('start', 'go', 'r')
STOP = ('stop', 's')
QUIT = ('q', 'quit', 'exit')


def parser():
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument('--arm', default='arm', help='Arm component name')
    cli.add_argument('--out', default='paths', help='Directory for recorded paths')
    cli.add_argument('--name', default='coconut_pour', help='Path name, used in the filename')
    cli.add_argument('--hz', type=float, default=10.0, help='Samples per second (1..50)')
    cli.add_argument('--address', default=os.environ.get('VIAM_MACHINE_ADDRESS', DEFAULT_ADDRESS))
    return cli


async def connect(address):
    api_key = os.environ.get('VIAM_API_KEY')
    api_key_id = os.environ.get('VIAM_API_KEY_ID')
    if not api_key or not api_key_id:
        raise SystemExit('Set VIAM_API_KEY and VIAM_API_KEY_ID (see .env.example) before running.')
    options = RobotClient.Options.with_api_key(api_key=api_key, api_key_id=api_key_id)
    return await asyncio.wait_for(RobotClient.at_address(address, options), 30)


class Console:
    """One stdin reader for the whole session, so Ctrl-C and typed lines never fight."""

    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        self.recording = False
        threading.Thread(target=self.pump, daemon=True).start()
        self.loop.add_signal_handler(signal.SIGINT, self.interrupt)

    def pump(self):
        while True:
            line = sys.stdin.readline()
            self.loop.call_soon_threadsafe(
                self.queue.put_nowait, line.strip().lower() if line else None)
            if not line:
                return

    def interrupt(self):
        # Ctrl-C ends a recording without discarding it; when idle it quits.
        print()
        self.queue.put_nowait('stop' if self.recording else 'q')

    async def ask(self, prompt):
        print(prompt, end='', flush=True)
        return await self.queue.get()

    def close(self):
        self.loop.remove_signal_handler(signal.SIGINT)


def write(path, value):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


async def sample(arm, hz, samples, errors):
    period = 1.0 / hz
    started = time.monotonic()
    while True:
        tick = time.monotonic()
        try:
            joints = await asyncio.wait_for(arm.get_joint_positions(), 5)
            samples.append(dict(t=round(tick - started, 4), joints=list(joints.values)))
            errors.clear()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            errors.append(f'{type(exc).__name__}: {exc}')
            if len(errors) >= 5:
                raise RuntimeError(f'Lost the arm while recording: {errors[-1]}')
        await asyncio.sleep(max(0.0, period - (time.monotonic() - tick)))


def extent(samples):
    width = min(len(item['joints']) for item in samples)
    columns = [[item['joints'][index] for item in samples] for index in range(width)]
    return dict(joint_min=[round(min(column), 3) for column in columns],
                joint_max=[round(max(column), 3) for column in columns])


async def record(console, arm, args):
    samples, errors, failure = [], [], None
    task = asyncio.create_task(sample(arm, args.hz, samples, errors))
    began = datetime.now(timezone.utc)
    console.recording = True
    print('Recording. Move the arm by hand, then type stop (Ctrl-C also stops).')
    try:
        while not task.done():
            answer = await console.ask('recording> ')
            if answer is None or answer in STOP:
                break
            print(f'{len(samples)} samples so far; type stop to finish.')
    finally:
        console.recording = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except RuntimeError as exc:
            failure = str(exc)
            print(f'Sampler stopped early: {failure}')
    if not samples:
        print('No samples captured; nothing saved.')
        return
    duration = samples[-1]['t']
    output = Path(args.out) / f"{args.name}-{began.strftime('%Y%m%dT%H%M%S')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    write(output, dict(name=args.name, recorded_at=began.isoformat(), arm=args.arm,
                       machine=args.address, requested_hz=args.hz, duration_s=duration,
                       sample_count=len(samples), units='degrees',
                       effective_hz=round((len(samples) - 1) / duration, 2) if duration else None,
                       read_errors=errors, sampler_error=failure,
                       **extent(samples), samples=samples))
    print(f'Saved {len(samples)} samples over {duration:.1f}s to {output}')


async def session(args):
    robot = await connect(args.address)
    console = Console()
    try:
        arm = Arm.from_robot(robot, args.arm)
        joints = await asyncio.wait_for(arm.get_joint_positions(), 10)
        print(f"Connected to arm '{args.arm}'; joints now: "
              f"{[round(value, 2) for value in joints.values]}")
        print('Put the arm in manual mode, then type start (q to quit).')
        while True:
            answer = await console.ask('teach> ')
            if answer is None or answer in QUIT:
                return
            if answer in START:
                await record(console, arm, args)
                print('Type start to record another path, or q to quit.')
            elif answer:
                print('Commands: start, q')
    finally:
        console.close()
        await robot.close()


def main():
    args = parser().parse_args()
    if not 1 <= args.hz <= 50:
        raise SystemExit('--hz must be between 1 and 50')
    asyncio.run(session(args))


if __name__ == '__main__':
    main()
