"""Reconnect only read-only teaching operations; never retry motion or release."""
import asyncio

from primitives.types import Context
from .connection import connect
from .demonstrations import ViamIO, event


def connection_error(exc):
    from grpclib.const import Status
    from grpclib.exceptions import GRPCError, StreamTerminatedError
    from viam.errors import ViamError, ViamGRPCError
    if isinstance(exc, (ConnectionError, TimeoutError, StreamTerminatedError)):
        return True
    if isinstance(exc, OSError):
        # Disk/permission errors must not be mistaken for network failures.
        import errno
        return exc.errno in (errno.ECONNRESET, errno.ECONNREFUSED, errno.EPIPE,
                             errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ETIMEDOUT)
    if isinstance(exc, (GRPCError, ViamGRPCError)):
        return getattr(exc, 'status', getattr(exc, 'grpc_code', None)) in (
            Status.UNAVAILABLE, Status.DEADLINE_EXCEEDED, Status.CANCELLED)
    return isinstance(exc, (ViamError, RuntimeError)) and any(
        text in str(exc).lower() for text in ('channel closed', 'channel is closed',
            'not connected', 'connection closed', 'connection lost', 'unable to establish a connection'))


class TeachingConnection:
    def __init__(self, config, *, connector=None, delay_s=2, read_timeout_s=15):
        self.config = config
        self.connector = connector or (lambda: connect(managed_reconnect=False))
        self.delay_s = delay_s
        self.read_timeout_s = read_timeout_s
        self.io = None
        self.directory = None
        self.lock = asyncio.Lock()
        self.recoveries = 0
        self.gaps = []
        self.phase = None

    def log(self, step, **data):
        if self.directory is not None:
            event(self.directory, step, **data)
        else:
            print(step, flush=True)

    async def reconnect(self, failed):
        async with self.lock:
            if self.io is not failed:
                return  # Another reader already replaced this connection.
            if failed is not None:
                self.log('connection_lost_hold_arm_still', phase=self.phase)
                self.recoveries += 1
                gap = dict(phase=self.phase, recovery=self.recoveries)
                self.gaps.append(gap)
                try:
                    await asyncio.wait_for(failed.ctx.robot.close(), 2)
                except Exception as exc:
                    self.log('old_connection_close_failed', error=str(exc))
            attempt = 0
            while True:
                attempt += 1
                try:
                    robot = await self.connector()
                    self.io = ViamIO(Context(self.config, robot))
                    self.log('teaching_connection_ready', attempt=attempt, phase=self.phase)
                    return
                except Exception as exc:
                    if not connection_error(exc):
                        raise
                    self.log('reconnecting_keep_arm_still', attempt=attempt, error=str(exc))
                    await asyncio.sleep(self.delay_s)

    async def read(self, method, *args):
        failures = 0
        while True:
            active = self.io
            if active is None:
                await self.reconnect(None)
                continue
            try:
                call_args = args
                if method == 'capture' and failures:
                    call_args = (args[0], f'{args[1]}_retry{failures}')
                return await asyncio.wait_for(getattr(active, method)(*call_args), self.read_timeout_s)
            except Exception as exc:
                if not connection_error(exc):
                    raise
                self.log('teaching_read_retry', operation=method, error=str(exc))
                failures += 1
                await self.reconnect(active)
                await asyncio.sleep(self.delay_s)

    async def state(self):
        return await self.read('state')

    async def capture(self, directory, label):
        return await self.read('capture', directory, label)

    async def stop(self):
        if self.io is None:
            raise RuntimeError('No connection available for StopAll; verify the arm manually')
        await self.io.stop()  # Never reconnect/retry a robot command automatically.

    async def aclose(self):
        if self.io is not None:
            try:
                await asyncio.wait_for(self.io.ctx.robot.close(), 5)
            except Exception as exc:
                self.log('teaching_connection_close_failed', error=str(exc))
