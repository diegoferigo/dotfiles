#!/usr/bin/env -S pixi exec --spec python=3.13 -- python
"""Propagate the Copilot session name to the herdr pane title.

The name comes from workspace.yaml, which Copilot keeps up to date with the
auto-title and with /rename. This is separate from herdr-agent-state.sh, which
herdr overwrites on updates.
"""

import json
import os
import pathlib
import re
import socket
import sys
import time


def parse_scalar(value: str) -> str | None:
    """Decode a YAML scalar that is either plain, 'single' or "double" quoted."""

    if value.startswith('"'):
        try:
            return json.loads(value) or None
        except ValueError:
            return value.strip('"') or None

    if value.startswith("'"):
        return value[1:-1].replace("''", "'") or None

    return value or None


def read_name(session_id: str) -> str | None:
    """Return the session name stored in workspace.yaml, if any."""

    path = pathlib.Path.home() / ".copilot" / "session-state" / session_id / "workspace.yaml"

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    match = re.search(r"^name:[ \t]*(.*?)[ \t]*$", text, flags=re.MULTILINE)

    if match is None:
        return None

    name = parse_scalar(match.group(1))

    if name is None:
        return None

    # Control characters such as ESC would reach the terminal title.
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", name).strip() or None


def main() -> None:

    pane_id = os.environ.get("HERDR_PANE_ID")
    socket_path = os.environ.get("HERDR_SOCKET_PATH")

    if os.environ.get("HERDR_ENV") != "1" or not pane_id or not socket_path:
        return

    try:
        data = json.load(sys.stdin)
    except ValueError:
        return

    session_id = data.get("session_id") or data.get("sessionId")

    if not isinstance(session_id, str) or not re.fullmatch(r"[0-9A-Za-z-]+", session_id):
        return

    name = read_name(session_id)
    params = {"pane_id": pane_id, "source": "copilot-title", "agent": "copilot"}
    if name:
        params |= {"title": name, "tokens": {"title": name}}
    else:
        params |= {"clear_title": True, "tokens": {"title": None}}

    request = {
        "id": f"copilot-title:{time.time_ns()}",
        "method": "pane.report_metadata",
        "params": params,
    }

    # A hook must never break the session, so a missing or slow herdr is ignored
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.5)
            client.connect(socket_path)
            client.sendall((json.dumps(request) + "\n").encode("utf-8"))
            client.recv(4096)
    except OSError:
        pass


if __name__ == "__main__":
    main()
