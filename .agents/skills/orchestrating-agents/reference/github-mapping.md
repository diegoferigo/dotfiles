# Keys and GitHub mapping

How each key of `tasks.toml` maps to GitHub Issues and Projects, so the ledger can be mirrored 1:1 later. Nothing here syncs with GitHub. It only fixes the vocabulary. The file format is in [task-graph.md](task-graph.md).

## Contents

- [Keys and GitHub mapping](#keys-and-github-mapping)
- [Iteration versus wave](#iteration-versus-wave)
- [Unverified](#unverified)

## Keys and GitHub mapping

The keys use the GitHub names so an export needs no renaming. Each item is `key`: GitHub field, then the notes.

- `id`: issue number, or a Projects draft issue. Keep the ledger id too. A table of id to issue number does not exist yet.
- `title`: issue title.
- `deps`: `blocked by` (`gh issue edit --add-blocked-by`, `gh issue view --json blockedBy,blocking`). `blocking` is the inverse.
- `parent`: sub-issue parent (`gh issue edit --parent`). An epic is an issue with sub-issues. Limits: 100 sub-issues, 8 levels.
- `type`: issue type (`gh issue create --type`). Defaults are Bug, Feature, Task. An epic can be an organization-level custom type.
- `status`: Projects `Status` single select. `pending` and `deferred` are Todo, the running statuses are In progress, `done` is Done.
- `priority`: issue field or Projects custom field (single select). Issue fields are in public preview, use `gh api`.
- `milestone`: milestone.
- `write_set`, `read_only`: Projects custom fields (text, checkbox), with `gh project field-create` and `item-edit`.
- `footprint`, `exclusive`, `overlap`: Projects custom fields (number, text): the weight of the task and the resources it takes alone.
- `blocker`, `answer`: a comment on the issue, or a Projects text field. The convention is `blocker = "human: <question>"`.
- `worktree`, `branch`: Projects custom fields (text), or the linked pull request branch. On the epic for a group that shares one worktree, on the leaf otherwise.
- Agent, tier and effort, reviewer, round, verdict and commit: Projects custom fields, linked pull request and closing keywords. They live in the registry, not in `tasks.toml`.
- Assignee, labels and due date: the same built-in fields. Not in the file today.
- Sub-issue progress: the built-in `Sub-issue progress`. Derived from the children, do not store.

Sources: [sub-issues](https://docs.github.com/en/rest/issues/sub-issues), [issue dependencies](https://docs.github.com/en/rest/issues/issue-dependencies), [issue fields](https://github.blog/changelog/2026-05-21-issue-fields-are-now-in-public-preview-for-all-organizations/).

## Iteration versus wave

An iteration is a Projects field for a time-box: repeating blocks of days or weeks, with breaks, each with a name and a date range. A project can group, filter (`@current`, `@previous`, `@next`, `iteration:>"Iteration 4"`) and sort by it, and the roadmap layout draws iterations on a timeline ([About iteration fields](https://docs.github.com/en/issues/planning-and-tracking-with-projects/understanding-fields/about-iteration-fields)).

It is a planning tool chosen by a human, not a dependency order. It has no relation to `deps`. For the ledger:

- Do not map the wave to an iteration. The wave is computed from the graph and changes when a task is added.
- Use an iteration, if at all, for a human time-box of a long feature (for example one week of a multi-week epic). There is no `iteration` key yet.
- A task with an iteration is still blocked by its `deps`: the iteration says when it is wanted, `deps` says when it can start.

## Unverified

Not read at the source: the GraphQL mutation names for sub-issues, dependencies and issue fields, the REST paths to add or remove a dependency, a limit on the number of dependencies, whether issue types and sub-issues are formally GA, the JSON keys of `gh project item-list`, and whether a Projects filter exists for blocked items. Check them before writing a sync.
