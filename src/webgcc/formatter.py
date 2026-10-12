import asyncio
import shutil

from .builder import SOURCE_NAME, WebGccError
from .settings import Settings


class FormatterUnavailableError(WebGccError):
    pass


class FormatError(WebGccError):
    pass


def formatter_available(settings: Settings) -> bool:
    return shutil.which(settings.clang_format) is not None


async def format_source(source: str, settings: Settings) -> str:
    """Return `source` formatted by clang-format with the configured style."""
    cmd = [settings.clang_format, f"--style={settings.format_style}", f"--assume-filename={SOURCE_NAME}"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise FormatterUnavailableError(f"{settings.clang_format} not found") from error
    try:
        async with asyncio.timeout(settings.compile_timeout):
            stdout, stderr = await proc.communicate(source.encode("utf-8"))
    except TimeoutError as error:
        proc.kill()
        await proc.wait()
        raise FormatError("clang-format timed out") from error
    if proc.returncode != 0:
        raise FormatError(stderr.decode("utf-8", errors="replace").strip() or f"exit code {proc.returncode}")
    return stdout.decode("utf-8", errors="replace")
