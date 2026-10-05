from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter


class CompileRequest(BaseModel):
    source: str
    # Shell-like gcc options placed before main.c; None means the server default.
    cflags: str | None = None


class CompileResponse(BaseModel):
    build_id: str | None
    success: bool
    command: str
    returncode: int | None
    output: str
    timed_out: bool
    duration: float


class ConfigResponse(BaseModel):
    gcc: str
    default_cflags: str
    ldflags: str
    run_timeout: float


TerminalSize = Annotated[int, Field(ge=1, le=1000)]


# Websocket protocol of /api/run: client messages are JSON, program output is sent as binary frames.
class StartMessage(BaseModel):
    type: Literal["start"]
    build_id: str
    args: str = ""
    cols: TerminalSize = 80
    rows: TerminalSize = 24


class InputMessage(BaseModel):
    type: Literal["input"]
    data: str


class ResizeMessage(BaseModel):
    type: Literal["resize"]
    cols: TerminalSize
    rows: TerminalSize


class KillMessage(BaseModel):
    type: Literal["kill"]


ClientMessage = Annotated[InputMessage | ResizeMessage | KillMessage, Field(discriminator="type")]
client_message = TypeAdapter[ClientMessage](ClientMessage)


class StartedMessage(BaseModel):
    type: Literal["started"] = "started"
    command: str


class ExitMessage(BaseModel):
    type: Literal["exit"] = "exit"
    returncode: int | None
    signal: str | None
    timed_out: bool
    killed: bool
    duration: float


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    detail: str
