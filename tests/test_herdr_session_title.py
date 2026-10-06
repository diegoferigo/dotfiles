from __future__ import annotations

import json
import pathlib
import socket
import subprocess
import sys
import threading

from conftest import REPO_ROOT

HOOK = REPO_ROOT / ".copilot/hooks/herdr-session-title.py"
SESSION_ID = "abc-123"


def test_the_pane_title_has_no_control_characters(tmp_path: pathlib.Path) -> None:
    request = _run_hook(tmp_path, 'name: "fix \\u001b]0;pwned\\u0007 the bug\\n"')

    assert request["params"]["title"] == "fix ]0;pwned the bug"


def test_a_name_made_only_of_control_characters_clears_the_title(
    tmp_path: pathlib.Path,
) -> None:
    request = _run_hook(tmp_path, 'name: "\\u001b\\u0007"')

    assert request["params"]["clear_title"] is True
    assert "title" not in request["params"]


def _run_hook(tmp_path: pathlib.Path, workspace_line: str) -> dict:
    home = tmp_path / "home"
    workspace = home / ".copilot/session-state" / SESSION_ID / "workspace.yaml"
    workspace.parent.mkdir(parents=True)
    workspace.write_text(workspace_line + "\n", encoding="utf-8")

    socket_path = str(tmp_path / "herdr.sock")
    received: list[bytes] = []
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    server.listen(1)

    def serve() -> None:
        connection, _ = server.accept()
        with connection:
            received.append(connection.recv(65536))
            connection.sendall(b"{}\n")

    thread = threading.Thread(target=serve)
    thread.start()

    try:
        subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"session_id": SESSION_ID}),
            env={
                "HOME": str(home),
                "HERDR_ENV": "1",
                "HERDR_PANE_ID": "pane-1",
                "HERDR_SOCKET_PATH": socket_path,
            },
            text=True,
            check=True,
            timeout=30,
        )
    finally:
        thread.join(timeout=10)
        server.close()

    return json.loads(received[0])
