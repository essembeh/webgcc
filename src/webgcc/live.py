import asyncio
import contextlib
import time

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from .builder import Builder, InvalidArgumentsError, UnknownBuildError
from .process import PtyProcess, signal_name
from .schema import (
    ErrorMessage,
    ExitMessage,
    InputMessage,
    KillMessage,
    ResizeMessage,
    StartedMessage,
    StartMessage,
    client_message,
)


class Session:
    """Bridges a websocket and a program running in a pseudo-terminal."""

    def __init__(self, ws: WebSocket, process: PtyProcess, max_output_rate: int) -> None:
        self.ws = ws
        self.process = process
        self.max_output_rate = max_output_rate
        self.killed = False

    async def forward_output(self) -> None:
        while data := await self.process.read():
            try:
                await self.ws.send_bytes(data)
            except (WebSocketDisconnect, RuntimeError):
                self.process.kill()
                return
            # Throttling: the program blocks on the full pty buffer meanwhile.
            await asyncio.sleep(len(data) / self.max_output_rate)

    async def forward_input(self) -> None:
        try:
            while True:
                try:
                    message = client_message.validate_python(await self.ws.receive_json())
                except ValidationError:
                    continue
                match message:
                    case InputMessage(data=data):
                        self.process.write(data.encode("utf-8"))
                    case ResizeMessage(cols=cols, rows=rows):
                        self.process.resize(cols, rows)
                    case KillMessage():
                        self.killed = True
                        self.process.kill()
        except (WebSocketDisconnect, RuntimeError):
            self.process.kill()


async def _refuse(ws: WebSocket, detail: str) -> None:
    await ws.send_json(ErrorMessage(detail=detail).model_dump())
    await ws.close()


async def run_session(ws: WebSocket, builder: Builder) -> None:
    await ws.accept()
    try:
        start = StartMessage.model_validate(await ws.receive_json())
        command, process = await builder.spawn(start.build_id, start.args, cols=start.cols, rows=start.rows)
    except ValidationError:
        await _refuse(ws, "Invalid start message")
        return
    except UnknownBuildError:
        await _refuse(ws, "Unknown build, compile again")
        return
    except InvalidArgumentsError as error:
        await _refuse(ws, f"Invalid arguments: {error}")
        return
    except WebSocketDisconnect:
        return

    started_at = time.monotonic()
    session = Session(ws, process, builder.settings.max_output_rate)
    timed_out = False
    try:
        await ws.send_json(StartedMessage(command=command).model_dump())
        output = asyncio.create_task(session.forward_output())
        inputs = asyncio.create_task(session.forward_input())
        try:
            async with asyncio.timeout(builder.settings.run_timeout):
                returncode = await process.wait()
        except TimeoutError:
            timed_out = True
            process.kill()
            returncode = await process.wait()
        duration = time.monotonic() - started_at
        # Let the remaining output reach the client; a leftover child holding the pty is killed after 1s.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(output, 1.0)
        inputs.cancel()
        exit_message = ExitMessage(
            returncode=None if timed_out else returncode,
            signal=None if timed_out else signal_name(returncode),
            timed_out=timed_out,
            killed=session.killed,
            duration=duration,
        )
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await ws.send_json(exit_message.model_dump())
            await ws.close()
    finally:
        process.close()
