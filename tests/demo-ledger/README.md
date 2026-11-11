# Demo ledger

A small, fictional ledger of the task-graph stack. Use it to change the behavior of `ledger_graph.py` or `ledger_board.py` and to iterate on it in a new session, without touching the real ledger.

It has 3 epics, every status (`done`, `in_rev`, `in_progress`, `pending`, `blocked` on a human, `deferred`), priorities P0 to P3, dependencies, `overlap = "hunks"`, a task without an epic and an inbox question.

Always work on a copy, because `set` and `answer` write the file:

```bash
cp -a tests/demo-ledger /tmp/demo-ledger
S=.agents/skills/orchestrating-agents/scripts
pixi run -q python $S/ledger_graph.py /tmp/demo-ledger/tasks.toml check
pixi run -q python $S/ledger_graph.py /tmp/demo-ledger/tasks.toml tree
pixi run -q python $S/ledger_graph.py /tmp/demo-ledger/tasks.toml next --rw 2 --ro 2 --budget 4 --caps-note demo --peek
pixi exec --spec textual python $S/ledger_board.py /tmp/demo-ledger/tasks.toml
```

The worktrees in the file do not exist: `check` accepts them and `cleanup` and `resume` report them as missing.
