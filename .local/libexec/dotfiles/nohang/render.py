#!/usr/bin/env python3
"""Print an upstream nohang config with the overrides applied.

Usage: render.py UPSTREAM_CONF OVERRIDES_CONF

nohang rejects a config with a missing key, so the upstream file stays the
base. In the overrides file a `key = value` line replaces the upstream key
(unknown keys are an error) and an `@...` line is appended. Blank lines and
`#` comments are ignored.
"""

import pathlib
import re
import sys

KEY_VALUE = re.compile(r"^(?P<key>[a-z_]+)\s*=")


def render(upstream: str, overrides: str) -> str:
    replacements: dict[str, str] = {}
    appended: list[str] = []
    for line in overrides.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = KEY_VALUE.match(line)
        if match:
            replacements[match["key"]] = line
        else:
            appended.append(line)

    output: list[str] = []
    applied: set[str] = set()
    for line in upstream.splitlines():
        match = KEY_VALUE.match(line)
        if match and match["key"] in replacements:
            output.append(replacements[match["key"]])
            applied.add(match["key"])
        else:
            output.append(line)

    unknown = sorted(replacements.keys() - applied)
    if unknown:
        sys.exit(f"unknown nohang keys in overrides: {', '.join(unknown)}")

    return "\n".join([*output, *appended]) + "\n"


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    upstream, overrides = (pathlib.Path(arg) for arg in sys.argv[1:])
    sys.stdout.write(render(upstream.read_text(), overrides.read_text()))


if __name__ == "__main__":
    main()
