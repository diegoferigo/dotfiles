# GitHub writing: PR titles, descriptions, commit messages

## Contents

- Opening a pull request
- Keeping the description current
- PR titles
- PR description: find the template first
- PR description: what, why, tested
- Commit messages
- Resolving review threads and the resolution reason
- Issue and top-level comments
- Progress comments on a long-running PR
- Media in a PR: verify the image before posting

## Opening a pull request

Always open in draft, and **always assign Diego**, in the same command so it cannot be forgotten:

```bash
gh pr create --draft --assignee @me --title '...' --body-file <file>
```

`@me` is the account that is currently active, so check it first (see the account table in [identity.md](identity.md)). GitHub **silently drops** an assignee that is not a collaborator on the repo, and `gh pr create` still succeeds, so confirm the assignment landed with `gh pr view <n> --json assignees`. If it did not, or the PR was created without it, fix it with `gh pr edit <n> --add-assignee @me` and say so.

Mark it ready for review only when Diego explicitly asks. If a PR was already created as ready by mistake, say so instead of quietly converting it.

**If a PR that was opened as draft is now ready for review, assume Diego did it, not a mistake.** He reviews and flips his own PRs to ready, so do not convert it back to draft to "restore" the state you left: that fights his action. The one real gotcha is that some `gh pr edit` calls (for example `--base`) silently un-draft a PR as a side effect, so when you only meant to edit a field, check `gh pr view <n> --json isDraft` afterwards and undo just that accidental flip with `gh pr ready <n> --undo`. When in doubt whether the change was his or a side effect, leave it ready and ask instead of undoing.

Write the body to a file and pass `--body-file`, so quoting does not mangle the markdown. Do not hard-wrap the paragraphs in that file: one paragraph is one long line (see the Line breaks rule in [prose-and-phrasebook.md](prose-and-phrasebook.md)). A body file wrapped at a fixed column renders ragged on GitHub.

He does not add labels, milestones or reviewers unless the repo automation needs them.

## Keeping the description current

The description describes the PR **in its final state**, so it goes stale as soon as the branch grows. Every time something is added to or changed in an open PR, re-read the description and ask whether it still describes the branch. This is part of the change, not an optional follow-up.

Update it when:

- a new commit adds a behaviour, a file or a dependency the description does not mention;
- the approach changed, so the "why" paragraph now explains a solution that is no longer the one implemented;
- the "tested" section lists commands that no longer cover what the branch does;
- the scope shrank, for example a fix was extracted into its own PR, and the description still claims it.

Rewrite the affected sentences in place. Do not append a changelog of what happened during review: that history belongs in the thread and in the commits.

## PR titles

One sentence saying WHAT changes, imperative mood, no trailing period, no branch name. Backticks around symbols. A reader should understand the change from the title alone.

Pass the title in single quotes, never double quotes: the shell runs backticks as a command substitution and drops the symbol. Read the title back after creating the PR.

```
Resolve the runtime compiler command against `PATH`
Update the ROS 2 nodes for cross-distro API compatibility
Refactor the contact-model class
Remove absolute time from the model state and introduce a `time_step` field
Fix running autodiff through the rotation helper
```

Add an issue or ticket identifier only if the repo requires one. Diego does not use Jira, so never invent a ticket prefix.

The PR title and the commit subject use the same style, so a single-commit PR has the same text in both places. Where the ecosystem has its own convention, follow it exactly: a conda-forge recipe PR uses this title shape:

```text
Add `package-name`
```

## PR description: find the template first

The structure of the body always comes from the repo, never from this skill.

1. Look for a template: `.github/pull_request_template.md`, `.github/PULL_REQUEST_TEMPLATE/*.md`, or the same paths at the repo root or under `docs/`. If one exists, fill its sections in order and delete the HTML comments.
2. If there is no template, look at how the last few merged PRs in that repo are written and follow that.
3. If neither gives a clear answer, ask Diego which structure he wants, or leave the body empty and let him use the description generator in the GitHub web UI.

Never paste a structure from another repo into a repo that does not use it.

## PR description: what, why, tested

Whatever the section names are, a good description from him answers three questions, in this order. Map them onto the template's sections.

**What changes.** Say what changes and how the behaviour differs, before and after. Name a class or function only when it is the clearest way to point at the change, not as a rule. A short bullet list by default (see the length section below), a sentence of prose only when the change is a single thing. When you spot an adjacent gap and deliberately leave it, say so in one line and where it goes (its own PR, an issue), rather than silently widening the scope or silently dropping it.

**Why it changes.** The rationale and the concrete symptom, in plain prose. Say which alternatives were explored and dropped, and say explicitly when a bug is more general than the setup that exposed it, only when that is not obvious. Whether to link the issue or paste the actual failure is a register choice: the open-source shape does it, the work shape does not (see below).

**How it was tested.** This section is a record of validation that already happened, not a plan for later. Run and confirm the checks first, then list only what was really run: the exact command and the observed outcome. Never list a check that was not executed. The point of grouping them is so a reviewer, or anyone later, can re-run the same steps, so write the commands in a form that reproduces the result. For a change with no runtime behaviour, such as tooling, CI or config, say it is covered by CI instead of restating the CI checks.

Format a posted command so the renderer never soft-wraps it: when it is too long to sit on one line, break it with a trailing backslash and put one argument per indented continuation line, so the fenced block stays readable and copy-pastes verbatim. A pytest run over several files takes the multi-line shape

```
pixi run pytest -q \
  tests/isaaclab/test_policy_replay.py \
  tests/isaaclab/test_run_isaaclab.py \
  tests/isaaclab/core/workflows/test_wandb_checkpoints.py
```

rather than one long line the browser folds mid-path. The same rule holds for any command in a fenced block, including the media-provenance command below.

### Length: match the register

Default to the shortest description that answers the three questions. Length is keyed on the repo owner, not on how much was changed.

- **Work repos: readable, not heavy.** Go as long as the change genuinely needs, no longer. Under "what", a one-sentence summary and a short bullet list of the changes. Under "why", at most two short paragraphs of plain prose stating the problem this removes. No links, no SHA, no pasted error block, no restating the code: the reviewers are smart and read the diff and the commit messages for the rest. This is the default shape for any work repo. The description exists to give the reviewer intent and confidence, not to document the change: anything the diff, the commit or CI already shows is noise that makes the signal harder to find, and in a squash-merge repo that noise lands on `main` too. Apply it per section: "what" keeps only behaviour a user or reviewer would notice, and "tested" keeps only evidence CI cannot provide, i.e. the manual or real-world checks and where they ran.
- **Open-source and personal repos: a bit more room.** A short paragraph of context, the reasoning behind the design, a link to the issue, inline LaTeX for the math and a runnable snippet when the PR adds an API. More detail than a work PR, but still no walls of text: add a sentence because a contributor who does not share the internal context needs it, not to be thorough for its own sake.

A work PR description is short enough to read top to bottom in a minute, not an inventory of the diff: say what changes for the user and why, in plain words, and leave flags, file lists, workarounds and step-by-step details to the diff. Do not list every file or script touched, and do not put a backticked name in every line. In "Tested", one bullet for the main check with its outcome, plus one for what was not tested. Prefer a short bullet list for "What" over paragraphs of prose: one line per bullet, no sub-bullets, no paragraph inside a bullet. A list is for scanning, so a dense list of long bullets is as unreadable as a wall of text. Keep prose for "Why".

When in doubt, cut. A reviewer can open the diff, the commit body and the linked run for anything the description leaves out.

The skeleton below shows the proportions of the work shape. It is a shape to reproduce, not text to copy: fill each section with the real change.

> ### What change is being made
>
> One sentence on what changes, then a short bullet list, one line each, of how the behaviour differs. Name the behaviour it deliberately preserves.
>
> ### Why this change is being made
>
> One or two short paragraphs of plain prose: the problem this removes and the constraint that shapes the solution. No links, no SHA.
>
> ### Tested
>
> - The command or manual check run, and its outcome.
> - One bullet per independent check.

The open-source shape answers the same three questions, with a short context paragraph in front and, where they clarify the change, inline LaTeX and a runnable snippet for a new API. It runs a little longer than the work shape because an outside contributor does not share the internal context, but it still stops at the point where the diff, a linked commit or the referenced issue carries the rest. Reference an issue inline with a custom link, `[#123](https://github.com/owner/repo/issues/123)`, so GitHub does not rewrite it to the full issue title, and let a `Closes #123` / `Fixes #123` line at the end do the linking and closing. Never paste the issue content into the description. A bare `#123` or a bare URL on its own line expands to the title-and-state card, so keep that only for a standalone pointer where the card helps, never inside flowing prose (see [prose-and-phrasebook.md](prose-and-phrasebook.md)).

For a PR belonging to a stack, do not add any stack note to the description: no "part of a stack" line, no pointer to the parent PR, no landing order. The tooling (`gh stack` / GitHub) already renders the stack and its order, so a hand-written mention only duplicates it and goes stale. Write the description as if the PR stood on its own.

## Commit messages

Common to both registers:

- Imperative subject, sentence case (first word capitalised, the rest lower unless a proper noun), no type/scope prefix, no trailing period. Roughly 45 to 80 characters.
- No Conventional Commits. Never write `feat:`, `fix:` or `chore:` unless the repo enforces them.
- Backticks around symbols in the subject are fine and common.
- One logical change per commit. Lockfiles and other generated artifacts get their own commit: adding a dependency is `Add <x> dependency` touching only `pixi.toml`, followed by a separate `Update pixi.lock`.
- A body is added only when the change needs a justification, and it says why, not what the diff already shows.
- Never mention the agent in the subject.
- Never add an AI-agent `Co-authored-by` trailer (Copilot, Claude, or any other assistant) on any repo, work or personal. This applies even when the tooling suggests one by default: strip it. It also applies to any commit you delegate to a sub-agent: the instruction you hand it must forbid the trailer too. `Co-authored-by` for real human co-authors is still fine.

**Work**: add a body structured as *previous behavior* -> *problem it caused* -> *what this commit does*. Do not hard-wrap the body: one paragraph is one long line, like the PR description (see the Line breaks rule in [prose-and-phrasebook.md](prose-and-phrasebook.md)). The skeleton below shows the structure; fill it with the real change.

```
Short imperative subject naming the fix

Previously, <the old behaviour> did <what it did>, so <the concrete problem that followed>. This surfaced as <the observed symptom> with no clear hint about the cause.

<What this commit changes>, and <the guard or safety it adds>. Add unit tests for the success path and the relevant failure cases.
```

Note that symbols are written plainly in the body, without backticks, since it is plain text.

A review-driven follow-up commit uses the subject `Address review: <what was hardened>` and a bulleted body, one bullet per reviewer concern.

**Never squash, amend, fold or fixup commits after review of a PR has started**, including on Diego-owned and team stacks. The rule is absolute. Each review round adds one follow-up commit per affected layer, on the layer that owns the change, so the diff of each round stays visible, the evolution of the PR is traceable and each reviewer reply can cite a reachable SHA. On a stack, the layers above are restacked with a plain rebase (see [copilot-review-loop.md](copilot-review-loop.md)). Guest/upstream PRs remain append-only after review starts.

**Personal**: usually subject only, since the PR carries the explanation. The subjects are short imperative sentences, for example `Update <component>` or `Restore <behaviour> in <area>`.

A body is added only when the reason is not obvious from the subject, and it is one or two sentences. `Co-authored-by` for real human co-authors is normal here.

## Resolving review threads and the resolution reason

This closes an inline review thread, and it comes up in both roles. As the PR author, once a reviewer comment is handled, push the fix and reply inside that thread with the canonical shape:

> **Addressed.** Done in 0ba959e2a2: guarded the empty input before indexing it.

Use `**Won't fix.** <reason>.` for a valid concern deliberately declined, and `**Incorrect.** <reason>.` for a false positive. The marker comes first; an addressed reply then cites the reachable SHA in plain text so GitHub links it. As a reviewer on someone else's PR, he can also mark a thread he opened as resolved once the author has fixed it, when clearing it helps the author track what is left. Reply in the thread the comment belongs to (`gh api .../pulls/<n>/comments/<comment_id>/replies`), never as a new top-level comment, so the conversation stays anchored to its line. The etiquette in [reviewing.md](reviewing.md) still holds: leave a thread open when it asked a question, and never resolve just to make a concern disappear.

Then mark the thread resolved. Resolving is consent-gated, so only do it once Diego has asked (in this session, through a standing instruction, or by asking for the complete Copilot review loop), and only after the reply and, on his own PR, the pushed fix are already in place. Do not resolve a question thread unless Diego explicitly asks. Resolution needs the GraphQL `resolveReviewThread` mutation on the thread id, since the REST comment endpoints cannot resolve.

A Copilot code review comment carries a resolution reason, the `Addressed` / `Won't fix` / `Incorrect` dropdown next to its `Resolve conversation` button, and the mutation takes it as the `resolutionReason` argument of enum `PullRequestReviewThreadResolutionReason`. Pick the one that is true, do not default to `ADDRESSED`:

- `ADDRESSED`: the comment was right and the fix is pushed. This is the common case.
- `WONT_FIX`: the comment is valid but the change is deliberately not made, for a reason stated in the reply (out of scope, a tradeoff taken on purpose).
- `INVALID`: the comment is wrong, the `Incorrect` option, for a false positive. Say why in the reply before resolving.

```bash
# thread ids for the PR, with the first comment so you resolve the right one
gh api graphql -f query='{ repository(owner:"OWNER", name:"REPO") {
  pullRequest(number: N) { reviewThreads(first: 50) {
    nodes { id isResolved comments(first: 1) { nodes { path body } } } } } }'

# resolve one thread with the reason that fits (ADDRESSED | WONT_FIX | INVALID)
gh api graphql -f query='mutation($id: ID!) {
  resolveReviewThread(input: { threadId: $id, resolutionReason: ADDRESSED }) {
    thread { isResolved } } }' -f id='PRRT_...'
```

The reason is what feeds the green `Addressed` pill (or its `Won't fix` / `Incorrect` siblings) the UI shows on a Copilot comment, so a bare `resolveReviewThread` with no `resolutionReason` resolves the thread but leaves the marker unset. To set the reason on a thread already resolved without one, `unresolveReviewThread` first, then resolve again with the argument. `unresolveReviewThread` is also the plain undo.

Copilot can auto-resolve an addressed comment during re-review. The policy for when to rely on that behavior and when to resolve manually is in [copilot-review-loop.md](copilot-review-loop.md#thread-disposition-and-auto-resolution).

## Issue and top-level comments

Same voice as reviews: get to the technical point in the first sentence, quote the exact error or symbol, propose a concrete next step. When reporting that something is fixed, cite the commit SHA.

## Progress comments on a long-running PR

Some PRs are a loop: push, wait for CI, read the failure, push again. Each iteration gets its own short top-level comment, so the thread reads as a log of what happened. Never edit the description instead: the description says what the PR does in its final state, the comments say how it got there.

**This shape assumes an append-only branch**, where every iteration is a new commit and nothing is ever force pushed. That is where it works best (a channel rebuild, a long CI-driven PR, a shared branch other people track), because a SHA cited in a comment stays reachable forever and the thread becomes the history of the branch.

On a branch that gets rebased (for example a restack after review), use it with care:

- do not cite a SHA that the next force push will rewrite, refer to the change by name instead, or wait and cite it after the branch settles;
- say plainly what happened to the history, for example "rebased on `main`, the fix is in the new follow-up commit", so a reviewer knows their local checkout is stale;
- the useful content is the same either way (what CI found, what changed, the state of the new run), only the SHA-anchored part is unreliable.

The shape of one comment, three or four sentences in total:

1. What CI or a local run revealed, with the concrete numbers or the failing platform. This comes first, it is the reason the rest exists.
2. What was changed in response, one clause per fix, no explanation of how the fix works: that goes in the commit message body.
3. The commit SHA in backticks, and the state of the new run.

Rules that keep them readable:

- **One paragraph, plus at most one short bullet list** when there are several independent findings. No headings, no tables, no checklists, no emoji parade.
- **Do not restate the PR description**, and do not re-derive the root cause. A reader who scrolls the thread should see only the delta since the previous comment, and can open the commit for the reasoning.
- **Numbers, not adjectives**: `patch application 99/99`, `9/9 on linux-64`, not "everything looks good now".
- **Always end with the CI state**, and link the run when it is useful: "A fresh CI run is starting", "the replacement run has passed patch checks on all five platforms and is now in the full build phase: <link>".
- **Never claim a run passed before it did.** "A fresh CI run is starting" is the honest sentence while it is queued.
- A comment answering a reviewer request opens with `**Addressed.** Done in <sha>:` and then the same shape.
- A comment correcting or extending the immediately previous one opens with `Follow-up:`.

Two illustrations of that shape, with the specifics stood in for by placeholders:

> The patch checks passed everywhere except one platform, where source downloads timed out. The actual builds exposed two issues from the update:
>
> - a mixed-dependency conflict on one OS
> - a missing propagated include path on another
>
> I pinned the offending dependency and fixed the include path. Pushed as `<sha>`, a fresh CI run is starting.

> Follow-up: the first refreshed run found three more platform-specific patches that are now upstream. Removed those too. The replacement run has passed patch checks on all platforms and is now in the full build phase: <link to the run>.

When Diego asks to commit and wait for CI, the deliverable is the commit plus this comment, and the comment is posted only if he asked for it: the consent rule in `SKILL.md` still applies.

## Media in a PR: verify the image before posting

When a screenshot, render, or short recording genuinely helps a reviewer, attach it with the upload-to-`user-attachments` flow (see the `github-pr-media` skill), but open and look at every file first: never post an image sight unseen. A stale, blank, half-rendered, or wrong-scene frame is worse than no image, so confirm the frame actually shows what the caption claims, then upload and embed it. A demonstration render need not use the exact production asset: a representative or synthetic scene is fine when it shows the capability, as long as the caption says what it is. Keep the caption factual and in the house voice, one or two sentences naming what the image shows and any setting that matters (for example a fast vs quality render preset), and put the media in the PR description under its own short heading, not buried in a comment, when it is part of what the PR delivers.

Always record, next to the media, the exact command that generated it, so the frame is reproducible and its provenance is auditable: paste the command in a fenced block under the same heading (the script or task invocation, with the device or resolution that matters), and prefer a command that runs from the repo over a one-off scratch script. A render whose command is not written down is a frame nobody can regenerate, which is exactly what this rule prevents.

When the frame comes from a scratch script that is not worth committing, do not strand the command pointing at a file no reviewer has: paste the whole script inside a collapsed `<details><summary>` block under the same heading, so the frame stays reproducible without adding throwaway code to the tree. Keep the real command outside the block and the script folded inside it.
