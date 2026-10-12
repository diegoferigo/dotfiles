# Pixi: running commands and managing dependencies

Applies to every repo managed by `pixi`.

## Contents

- Everything goes through the task runner
- Non-task commands
- Dependencies
- Other task runners

## Everything goes through the task runner

In a repo managed by `pixi`, **every command goes through `pixi run <task>`**.
Calling `colcon`, `cmake`, `ctest`, `pre-commit` or a compiler directly picks up
the system toolchain instead of the environment, and silently drops the flags the
tasks inject (mixins, toolchain files, standard version). The resulting errors
look like real code bugs but are not.

When the build cache is stale, run the repo's clean task and rebuild. Do not
hand-delete parts of the build tree, and do not work around a stale cache by
adding flags.

## Non-task commands

Arbitrary (non-task) commands go through `pixi run` too, so they run inside the
sourced environment: `pixi run ros2 topic list`, `pixi run python ...`. A bare
`ros2` or `python` from the shell picks up the wrong environment or none.

## Dependencies

Manage dependencies through `pixi.toml`, never the live environment: add or
remove a package in the correct `[feature.*]` and target section, then
regenerate the lockfile with `pixi lock`. Never `pip install`, `uv add` or
`conda install` into the environment. When upstream docs give a `uv`, `uvx` or
`pip` command, read it as a request to add that package to `pixi.toml` and run
the tool with `pixi run`, not to install it out of band.

The lockfile change lands in its own commit, separate from the `pixi.toml` edit.
For the commit shape see the lockfile rule in
[github-writing.md](github-writing.md).

## Other task runners

The same principle transfers to other repos: use the documented entry point
(`pixi run`, `make`, `tox`, `npm run`) rather than reconstructing the command.
