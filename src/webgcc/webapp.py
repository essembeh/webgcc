import shlex
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, WebSocket, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import HTTPConnection

from . import __version__
from .builder import Builder, InvalidArgumentsError
from .live import run_session
from .schema import CompileRequest, CompileResponse, ConfigResponse
from .settings import Settings
from .settings import settings as default_settings

STATIC = Path(__file__).parent / "static"


def get_builder(connection: HTTPConnection) -> Builder:
    builder: Builder = connection.app.state.builder
    return builder


BuilderDep = Annotated[Builder, Depends(get_builder)]


def create_app(settings: Settings = default_settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.builder = Builder(settings)
        try:
            yield
        finally:
            app.state.builder.close()

    app = FastAPI(title="webgcc", version=__version__, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/config")
    async def config(builder: BuilderDep) -> ConfigResponse:
        return ConfigResponse(
            gcc=settings.gcc,
            default_cflags=shlex.join(settings.cflags),
            ldflags=shlex.join(settings.ldflags),
            run_timeout=settings.run_timeout,
        )

    @app.post("/api/compile")
    async def compile_source(payload: CompileRequest, builder: BuilderDep) -> CompileResponse:
        if len(payload.source.encode("utf-8")) > settings.max_source_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Source too large")
        try:
            return await builder.compile(payload.source, payload.cflags)
        except InvalidArgumentsError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Invalid gcc options: {error}") from error

    @app.websocket("/api/run")
    async def run(ws: WebSocket, builder: BuilderDep) -> None:
        await run_session(ws, builder)

    return app


app = create_app()
