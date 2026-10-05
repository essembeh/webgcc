import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from webgcc.settings import Settings
from webgcc.webapp import create_app

HELLO = '#include <stdio.h>\nint main(int argc, char **argv) { printf("%d %s\\n", argc, argv[argc - 1]); return 3; }\n'
DOUBLE = (
    '#include <stdio.h>\nint main(void) { int n; printf("n? "); scanf("%d", &n); printf("double=%d\\n", 2 * n); }\n'
)
BUSY_LOOP = "int main(void) { for (;;) {} }\n"


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(run_timeout=2.0)
    with TestClient(create_app(settings)) as out:
        yield out


def build(client: TestClient, source: str) -> str:
    response = client.post("/api/compile", json={"source": source})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"], payload["output"]
    build_id: str = payload["build_id"]
    return build_id


def read_until(ws: WebSocketTestSession, *, text: str | None = None) -> tuple[str, dict[str, Any] | None]:
    """Collect output until `text` is seen, or until the final control message."""
    output = ""
    while True:
        message = ws.receive()
        if message.get("bytes") is not None:
            output += message["bytes"].decode()
            if text is not None and text in output:
                return output, None
        elif message.get("text") is not None:
            control: dict[str, Any] = json.loads(message["text"])
            if control["type"] != "started":
                return output, control
        else:
            raise AssertionError(f"unexpected message {message}")


def start(ws: WebSocketTestSession, build_id: str, args: str = "") -> None:
    ws.send_json({"type": "start", "build_id": build_id, "args": args})
    assert ws.receive_json() == {"type": "started", "command": f"./main {args}".strip()}


def test_index(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "CodeMirror" in response.text


@pytest.mark.parametrize("asset", ["codemirror.min.js", "xterm.min.js", "xterm-addon-fit.min.js"])
def test_vendored_assets(client: TestClient, asset: str) -> None:
    assert f"static/vendor/{asset}" in client.get("/").text
    assert client.get(f"/static/vendor/{asset}").status_code == 200


def test_compile_error(client: TestClient) -> None:
    payload = client.post("/api/compile", json={"source": "int main(void) { return x; }\n"}).json()
    assert not payload["success"]
    assert payload["build_id"] is None
    assert "main.c:1:25: error:" in payload["output"]


def test_run_with_args(client: TestClient) -> None:
    build_id = build(client, HELLO)
    with client.websocket_connect("/api/run") as ws:
        ws.send_json({"type": "start", "build_id": build_id, "args": 'a "deux mots"'})
        assert ws.receive_json()["command"] == "./main a 'deux mots'"
        output, control = read_until(ws)
    assert output == "3 deux mots\r\n"
    assert control is not None
    assert control["type"] == "exit"
    assert control["returncode"] == 3


def test_run_interactive_stdin(client: TestClient) -> None:
    build_id = build(client, DOUBLE)
    with client.websocket_connect("/api/run") as ws:
        start(ws, build_id)
        # The prompt is flushed before scanf blocks, like in a real terminal.
        read_until(ws, text="n? ")
        ws.send_json({"type": "input", "data": "21\r"})
        output, control = read_until(ws)
    assert "double=42" in output
    assert control is not None
    assert control["returncode"] == 0


def test_run_ctrl_c(client: TestClient) -> None:
    build_id = build(client, BUSY_LOOP)
    with client.websocket_connect("/api/run") as ws:
        start(ws, build_id)
        ws.send_json({"type": "input", "data": "\x03"})
        _, control = read_until(ws)
    assert control is not None
    assert control["signal"] == "SIGINT"
    assert not control["killed"]


def test_run_stop(client: TestClient) -> None:
    build_id = build(client, BUSY_LOOP)
    with client.websocket_connect("/api/run") as ws:
        start(ws, build_id)
        ws.send_json({"type": "kill"})
        _, control = read_until(ws)
    assert control is not None
    assert control["signal"] == "SIGKILL"
    assert control["killed"]


def test_run_segfault(client: TestClient) -> None:
    build_id = build(client, "int main(void) { int *p = 0; return *p; }\n")
    with client.websocket_connect("/api/run") as ws:
        start(ws, build_id)
        _, control = read_until(ws)
    assert control is not None
    assert control["signal"] == "SIGSEGV"


def test_run_timeout(client: TestClient) -> None:
    build_id = build(client, "#include <unistd.h>\nint main(void) { sleep(10); }\n")
    with client.websocket_connect("/api/run") as ws:
        start(ws, build_id)
        _, control = read_until(ws)
    assert control is not None
    assert control["timed_out"]
    assert control["returncode"] is None


@pytest.mark.parametrize(
    ("message", "detail"),
    [
        ({"type": "start", "build_id": "nope"}, "Unknown build, compile again"),
        ({"type": "oops"}, "Invalid start message"),
    ],
)
def test_run_refused(client: TestClient, message: dict[str, str], detail: str) -> None:
    with client.websocket_connect("/api/run") as ws:
        ws.send_json(message)
        assert ws.receive_json() == {"type": "error", "detail": detail}


def test_run_invalid_args(client: TestClient) -> None:
    build_id = build(client, HELLO)
    with client.websocket_connect("/api/run") as ws:
        ws.send_json({"type": "start", "build_id": build_id, "args": '"unclosed'})
        assert ws.receive_json()["detail"].startswith("Invalid arguments")


UNUSED = "int main(void) { int x; return 0; }\n"


@pytest.mark.parametrize(
    ("cflags", "command", "warned"),
    [
        (None, "gcc -Wall -Wextra main.c -o main -lm", True),
        ("", "gcc main.c -o main -lm", False),
        ("-Wall -Werror", "gcc -Wall -Werror main.c -o main -lm", True),
    ],
)
def test_compile_cflags(client: TestClient, cflags: str | None, command: str, warned: bool) -> None:
    payload = client.post("/api/compile", json={"source": UNUSED, "cflags": cflags}).json()
    assert payload["command"] == command
    assert ("unused variable" in payload["output"]) == warned


def test_compile_werror_fails(client: TestClient) -> None:
    payload = client.post("/api/compile", json={"source": UNUSED, "cflags": "-Wall -Werror"}).json()
    assert not payload["success"]


def test_compile_invalid_cflags(client: TestClient) -> None:
    response = client.post("/api/compile", json={"source": UNUSED, "cflags": '"-Wall'})
    assert response.status_code == 422


def test_config(client: TestClient) -> None:
    assert client.get("/api/config").json() == {
        "gcc": "gcc",
        "default_cflags": "-Wall -Wextra",
        "ldflags": "-lm",
        "run_timeout": 2.0,
    }
