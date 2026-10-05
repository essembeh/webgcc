import asyncio
import contextlib
import errno
import fcntl
import os
import resource
import signal
import struct
import termios
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Self

READ_CHUNK = 16 * 1024


@dataclass(frozen=True)
class Limits:
    cpu_seconds: int
    memory_bytes: int
    file_bytes: int


@dataclass(frozen=True)
class ProcessResult:
    returncode: int | None
    output: bytes
    timed_out: bool
    duration: float


def signal_name(returncode: int | None) -> str | None:
    if returncode is None or returncode >= 0:
        return None
    try:
        return signal.Signals(-returncode).name
    except ValueError:
        return f"signal {-returncode}"


def _kill_group(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


async def execute(
    cmd: list[str],
    *,
    cwd: Path,
    time_limit: float,
    max_output: int,
    env: dict[str, str] | None = None,
) -> ProcessResult:
    """Run `cmd` without stdin, stdout+stderr merged and truncated to `max_output` bytes."""
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    timed_out = False
    output = b""
    try:
        async with asyncio.timeout(time_limit):
            output, _ = await proc.communicate()
    except TimeoutError:
        timed_out = True
        _kill_group(proc.pid)
        await proc.wait()
    finally:
        _kill_group(proc.pid)
    return ProcessResult(
        returncode=None if timed_out else proc.returncode,
        output=output[:max_output],
        timed_out=timed_out,
        duration=time.monotonic() - start,
    )


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _child_setup(limits: Limits) -> Callable[[], None]:
    def apply() -> None:
        # Runs after setsid(): the pty becomes the controlling terminal, so Ctrl+C sends SIGINT.
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
        resource.setrlimit(resource.RLIMIT_FSIZE, (limits.file_bytes, limits.file_bytes))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply


async def _wait_readable(fd: int) -> None:
    loop = asyncio.get_running_loop()
    ready: asyncio.Future[None] = loop.create_future()

    def notify() -> None:
        if not ready.done():
            ready.set_result(None)

    loop.add_reader(fd, notify)
    try:
        await ready
    finally:
        loop.remove_reader(fd)


class PtyProcess:
    """A process attached to a pseudo-terminal, like a program launched from a real shell."""

    def __init__(self, proc: asyncio.subprocess.Process, master: int) -> None:
        self.proc = proc
        self.master = master

    @classmethod
    async def spawn(cls, cmd: list[str], *, cwd: Path, limits: Limits, cols: int, rows: int) -> Self:
        master, slave = os.openpty()
        try:
            _set_winsize(master, cols, rows)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
                preexec_fn=_child_setup(limits),
            )
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        os.set_blocking(master, False)
        return cls(proc, master)

    async def read(self) -> bytes:
        """Next output chunk, `b""` once every writer of the terminal is gone."""
        while True:
            try:
                return os.read(self.master, READ_CHUNK)
            except BlockingIOError:
                await _wait_readable(self.master)
            except OSError as error:
                # Linux reports the closed slave side as EIO.
                if error.errno == errno.EIO:
                    return b""
                raise

    def write(self, data: bytes) -> None:
        with contextlib.suppress(BlockingIOError, OSError):
            os.write(self.master, data)

    def resize(self, cols: int, rows: int) -> None:
        with contextlib.suppress(OSError):
            _set_winsize(self.master, cols, rows)

    def kill(self) -> None:
        _kill_group(self.proc.pid)

    async def wait(self) -> int:
        return await self.proc.wait()

    def close(self) -> None:
        self.kill()
        with contextlib.suppress(OSError):
            os.close(self.master)
