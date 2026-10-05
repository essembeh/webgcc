import os
import shlex
import shutil
import tempfile
import uuid
from collections import OrderedDict
from pathlib import Path

from .process import Limits, PtyProcess, execute
from .schema import CompileResponse
from .settings import Settings

SOURCE_NAME = "main.c"
BINARY_NAME = "main"


class WebGccError(Exception):
    pass


class UnknownBuildError(WebGccError):
    pass


class InvalidArgumentsError(WebGccError):
    pass


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


class Builder:
    """Compiles sources in isolated temporary folders and keeps the last `max_builds` binaries."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = Path(tempfile.mkdtemp(prefix="webgcc-", dir=settings.workdir))
        self.builds: OrderedDict[str, Path] = OrderedDict()

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def compile_command(self, cflags: list[str] | None = None) -> list[str]:
        s = self.settings
        return [s.gcc, *(s.cflags if cflags is None else cflags), SOURCE_NAME, "-o", BINARY_NAME, *s.ldflags]

    def _register(self, build_id: str, folder: Path) -> None:
        self.builds[build_id] = folder
        while len(self.builds) > self.settings.max_builds:
            _, evicted = self.builds.popitem(last=False)
            shutil.rmtree(evicted, ignore_errors=True)

    async def compile(self, source: str, cflags: str | None = None) -> CompileResponse:
        try:
            flags = None if cflags is None else shlex.split(cflags)
        except ValueError as error:
            raise InvalidArgumentsError(str(error)) from error
        build_id = uuid.uuid4().hex
        folder = self.root / build_id
        folder.mkdir()
        (folder / SOURCE_NAME).write_text(source, encoding="utf-8")
        cmd = self.compile_command(flags)
        result = await execute(
            cmd,
            cwd=folder,
            time_limit=self.settings.compile_timeout,
            env={**os.environ, "LC_ALL": self.settings.gcc_locale},
            max_output=self.settings.max_output_bytes,
        )
        success = result.returncode == 0 and (folder / BINARY_NAME).is_file()
        if success:
            self._register(build_id, folder)
        else:
            shutil.rmtree(folder, ignore_errors=True)
        return CompileResponse(
            build_id=build_id if success else None,
            success=success,
            command=shlex.join(cmd),
            returncode=result.returncode,
            output=_decode(result.output),
            timed_out=result.timed_out,
            duration=result.duration,
        )

    async def spawn(self, build_id: str, args: str, *, cols: int, rows: int) -> tuple[str, PtyProcess]:
        """Start a compiled program in a pseudo-terminal, returns the displayed command and the process."""
        folder = self.builds.get(build_id)
        if folder is None:
            raise UnknownBuildError(build_id)
        try:
            argv = shlex.split(args)
        except ValueError as error:
            raise InvalidArgumentsError(str(error)) from error
        s = self.settings
        limits = Limits(
            cpu_seconds=s.cpu_limit,
            memory_bytes=s.max_memory_mb * 1024 * 1024,
            file_bytes=s.max_file_mb * 1024 * 1024,
        )
        cmd = [f"./{BINARY_NAME}", *argv]
        process = await PtyProcess.spawn(cmd, cwd=folder, limits=limits, cols=cols, rows=rows)
        return shlex.join(cmd), process
