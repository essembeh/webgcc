from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WEBGCC_")

    # Arbitrary C code is executed: never bind on a public interface without isolation.
    host: str = "127.0.0.1"
    port: int = 8000
    gcc: str = "gcc"
    # English diagnostics, parsed by the web page to link errors to editor lines.
    gcc_locale: str = "C.UTF-8"
    cflags: list[str] = Field(default_factory=lambda: ["-Wall", "-Wextra"])
    ldflags: list[str] = Field(default_factory=lambda: ["-lm"])
    clang_format: str = "clang-format"
    # Same brace style as the default source, so that formatting it changes nothing.
    format_style: str = "{BasedOnStyle: LLVM, IndentWidth: 4, ColumnLimit: 100}"
    compile_timeout: float = 15.0
    # Wall-clock limit of an interactive run; the CPU limit catches busy infinite loops sooner.
    run_timeout: float = 300.0
    cpu_limit: int = 30
    # Output throttling, so that an infinite printf loop scrolls instead of flooding the browser.
    max_output_rate: int = 512_000
    max_source_bytes: int = 200_000
    max_output_bytes: int = 64_000
    max_memory_mb: int = 512
    max_file_mb: int = 10
    max_builds: int = 50
    workdir: Path | None = None


settings = Settings()
