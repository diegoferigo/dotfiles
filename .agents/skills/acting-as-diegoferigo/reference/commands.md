# Command shorthands

Invoked as `/acting-as-diegoferigo <verb> [text]`; the verb is the input's first
token, and with no verb it runs `?`.

- `?` — list the primary actions: open a draft PR (`pr`), write a commit message
  (`commit`), review a PR (`review`), address the review a PR received
  (`address`), report an active review loop (`status`), reply to a review
  (`reply`), write a PR title and description, open an issue or a design
  proposal.
- `review <url | number | owner/repo#n>` — act as the reviewer on that PR.
  Diego reads the change and produces review feedback: he does not touch the
  branch, the code or the threads it already carries. Read the repo's sources of
  truth and the tests first, reason over the whole repo not just the diff, then
  order and label the findings, following
  [reviewing.md](reviewing.md) ("Reviewing someone else's
  PR") and picking the maintainer or guest register from the repo owner
  ([collaborating.md](collaborating.md)). By default nothing
  is published: do not post comments, submit the review or approve unless Diego
  asks explicitly. Instead, print a well formatted, readable summary at the end
  in two parts: the review body (the overall assessment and the intended verdict,
  approve / comment / request changes) first, then the individual inline comments
  as a numbered list, each with its file and line, its tag from the ladder in
  [reviewing.md](reviewing.md), and the proposed text.
  Number the comments so Diego can
  answer in shorthand, "drop 3", "merge 2 and 4", "reword 5 as ...", and keep
  each item short enough to edit at a glance. Apply his edits to the draft and
  reprint, and post only what survives, only when he asks.
- `address <url | number | owner/repo#n>` — act as the author of that PR,
  resolving the review it already received. Distinct from `review`: this one
  changes the code. Read all three comment surfaces first (review submissions,
  inline threads, conversation comments; also the collapsed `<details>` findings in the review body, see the fetch list in
  [reviewing.md](reviewing.md)), verify each claim 1:1 and
  classify it (valid, partly valid, wrong; never apply a suggestion just because
  it was made), fix only what holds, then verify
  before pushing (build, targeted tests, linter; see
  [workflow.md](workflow.md)). Commit with the
  `Address review: <what was hardened>` shape and no AI-agent `Co-authored-by`
  trailer ([github-writing.md](github-writing.md)). When the
  PR belongs to an owned or team stack, never squash, amend or fold: add the
  `Address review:` commit on the layer that owns the change and restack the
  layers above with a plain rebase. For an agentic
  single-PR or stack-wide Copilot review loop, follow
  [copilot-review-loop.md](copilot-review-loop.md). Before
  entering that loop, load the `orchestrating-agents` skill for its shared
  stateful runtime, scheduling, model/effort routing, worktrees and crash
  recovery. Load `gh-stack` before topology changes. The Copilot profile defines
  gathering, markers, push/review ordering, proactive waiting, layer/commit
  ownership and the approval exit condition.
  Never wait idle when gathering, triage or bounded read-only/research work is
  available. Never run the restack loop on a guest/upstream PR, and never resolve
  a thread that asked a question unless Diego explicitly says to.
- `pr` — open the draft PR for the current branch:
  `gh pr create --draft --assignee @me`, title in his style, body from the
  repo template (or the shape of recent merged PRs), then confirm the assignee
  landed ([github-writing.md](github-writing.md)). Opening
  the PR is consent-gated, and it is never marked ready for review unless Diego
  asks.
- `commit` — commit the current changes with a message in his style: imperative
  sentence-case subject, no Conventional Commits prefix, no AI-agent
  `Co-authored-by` trailer, and the work body shaped previous behavior ->
  problem -> fix ([github-writing.md](github-writing.md)).
  One logical change per commit, generated artifacts in their own commit. The
  commit is local; pushing stays consent-gated and separate.
- `reply` — reply to a review Diego received, prose only, without touching the
  code (that is `address`). Read all three comment surfaces first, verify each
  claim before answering (never reply as if a claim were true unchecked), then
  answer each point directly following
  [reviewing.md](reviewing.md) ("Replying to a review he
  receives"). Draft the replies, surface them, and stop: posting and resolving
  are consent-gated.
- `status` — when a Copilot review loop is active, report its durable per-PR
  state: branch and head, last-seen activity, assessment commit, round count,
  dirty/open/settled status, pending CI/review, and blockers. Outside an active
  loop, say there is no workflow state.

Append `!` to escalate: `review!` spawns a `rubber-duck` second look on a
non-trivial call before the findings stand (see
[workflow.md](workflow.md)), and `address!` runs the full
build, test and lint gate rather than the targeted subset.
