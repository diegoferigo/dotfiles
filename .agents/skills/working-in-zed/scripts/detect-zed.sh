#!/usr/bin/env bash
# Print `zed` when this session runs under the Zed IDE (an ancestor process is zed-editor), else `no-zed`.
#
# The environment is not used: TERM_PROGRAM and similar variables are inherited from the shell that launched Zed,
# so they can name another terminal (for example herdr) while the agent is still hosted by Zed.
set -euo pipefail

pid=$$
while [[ -n $pid && $pid -gt 1 ]]; do
    comm=$(ps -o comm= -p "$pid" 2>/dev/null || true)
    if [[ $comm == zed* ]]; then
        echo zed
        exit 0
    fi
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ' || true)
done
echo no-zed
