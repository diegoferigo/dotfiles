<!--
UPSTREAM MIRROR, github/gh-stack : skills/gh-stack/references/stack-design.md
Pinned at commit d4ab7ab47e5b3e3708a27c8c42abcdf4bc321419 (skill v0.2.0, synced 2026-10-06).
Everything between upstream:begin/upstream:end is a faithful copy; to sync, replace
that block wholesale from upstream. Put local additions only under "Local notes".
-->

> **This file = upstream copy + local notes.** Everything between the `upstream:begin`/`upstream:end` markers is verbatim from upstream; local additions live under [Local notes](#local-notes) at the end.

<!-- upstream:begin -->

# Designing a stack

How to decide what goes in each layer. Read this before running `gh stack init`.

## Contents

- [Plan the layers before writing code](#plan-the-layers-before-writing-code)
- [Branch naming](#branch-naming)
- [Staging changes deliberately](#staging-changes-deliberately)
- [When to add a layer](#when-to-add-a-layer)
- [One stack, one story](#one-stack-one-story)

## Plan the layers before writing code

A stack is a dependency chain. If code in one layer depends on code in another, the dependency must live in the same branch or a lower one. That constraint is much cheaper to satisfy by planning than by restructuring later, because there is no non-interactive in-place reorder — fixing the order means`unstack` and `init` again.

Decide the layers first, then write code into them:

```
(main) <- todo-app/models <- todo-app/api <- todo-app/frontend <- todo-app/integration
```

- `todo-app/models` — shared types and schema
- `todo-app/api` — routes that use the models
- `todo-app/frontend` — components that call the routes
- `todo-app/integration` — tests exercising the whole feature

This is illustrative. Infer the stack topic and layer names from the actual task; do not reuse `todo-app` or these layer names literally.

The failure mode to avoid is writing everything on one branch and trying to split it afterwards. If a task is large enough to warrant a stack, create the stack at the start.

## Branch naming

Prefer a shared topic prefix plus the layer's concern: `<topic>/<concern>` — for example, `billing/schema`, `billing/api`, `billing/ui`. This keeps related branches recognizable without using generic names that could belong to any stack. **User and repository branch naming conventions take precedence; follow them instead.**

Names are used exactly as given — nothing is prepended or transformed, and slashes are kept, so `gh stack add refactor/foo` creates a branch literally named `refactor/foo`.

If you pass `-m` without a branch name, the name is generated from the commit message in date-and-slug form (for example `03-24-add_api_routes`). Prefer naming the branch yourself.

## Staging changes deliberately

Use `git add` and `git commit` directly rather than the `add -Am` shortcut. The point is control over which changes land in which branch. With several modified files in the working tree, stage the subset that belongs to the current layer, commit it, then create the next branch and stage the rest there:

```bash
git add internal/models/user.go internal/models/session.go
git commit -m "Add user and session models"

gh stack add api-routes
git add internal/api/routes.go internal/api/handlers.go
git commit -m "Add user API routes"
```

Multiple commits per branch are fine. What matters is that every commit in a branch serves the same concern, and that a change belonging to a different concern goes in a different branch.

Note that `gh stack add <branch>` without `-Am` does not touch the working tree, so uncommitted changes carry over to the new branch. Commit or stash first if you want the new layer to start clean.

## When to add a layer

Add a branch when you start a **different concern that depends on what you have built so far**. Signals:

- Moving from backend to frontend, or from core logic to tests or documentation
- The next changes have a different reviewer audience
- The current branch's diff is already large enough to review on its own

A layer that cannot be described in one sentence is usually two layers.

## One stack, one story

A stack should read as a coherent progression: a reviewer walks the PRs bottom to top and sees the feature being built.

**Use a single stack** when every branch serves the same feature or project, even if the layers span different concerns.

**Start a separate stack** for unrelated work — a different feature, an unrelated bug fix, an independent refactor. Do not mix efforts into one stack just because you happened to work on both. Use `gh stack init` for the new effort, or `gh stack checkout <target>` to move between existing stacks.

A trivial incidental fix can ride along in the current stack. Once it grows into its own project, it deserves its own stack.

<!-- upstream:end -->

______________________________________________________________________

## Local notes

Prefer branch names scoped under the actor's GitHub username when the repository has no stronger convention: `<username>/<stack-name>/<layer>` (for example, `alice/auth-refactor/db-schema`). This namespaces stacks created by multiple people and keeps examples using `<scope>/<layer>` consistent.

Once the layers are decided, the mechanical way to carve an existing big branch into these layers (soft-reset method + per-layer verification gate) is in [splitting.md](splitting.md).

### Author every commit split-ready, but default to small groups

Write each commit as a self-contained, single-concern unit: one coherent change, its tests, and the docs it touches, staged deliberately so no hunk of a different concern rides along. A commit shaped this way is already a splittable seam, so if a reviewer later asks for finer granularity a layer can be carved into smaller layers with the soft-reset method in [splitting.md](splitting.md), with no untangling of mixed hunks. This keeps future re-layering cheap without paying for it upfront.

Splittable is the safety net, not the target: default to small groups from the start. A small PR is the better trade-off, because the intrinsic latency of a review round (a human getting to the PR, reading it, a back-and-forth, CI) is often large and roughly fixed per PR regardless of size, so a lean diff clears that fixed cost fast and merges sooner, while a big diff pays the same latency but compounds it with a slower, error-prone read. Group only what a reviewer must see together to judge the change correctly, and split the moment a layer stops reading as one sentence. When the choice is between one large layer and two small ones that each stand on their own, prefer the two.
