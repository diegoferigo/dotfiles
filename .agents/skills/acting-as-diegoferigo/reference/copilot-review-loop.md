# Copilot review workflow profile

## Contents

- Runtime contract
- Scope and consent
- PR state and gathering
- Work available while waiting
- Requesting a Copilot review
- Thread disposition and auto-resolution
- Reading the overview
- Reading the review session logs
- Canonical per-PR iteration
- Stack scheduling and invalidation
- Holistic stack review
- Layer and commit ownership
- Runtime overrides and handoff

## Runtime contract

This file is the GitHub/Copilot domain profile for the stateful workflow runtime in the `orchestrating-agents` skill. Load that skill and follow its `reference/workflow-runtime.md` for scheduling, active waiting, durable agents, resource capacity, execution-mode selection, model/effort routing, worktree ownership and crash recovery. Follow its `reference/git-modes.md` for generic git isolation and stack ownership. For stack scope, load the separate `gh-stack` skill at workflow startup, including when no topology change is currently planned.

The same profile runs a single PR and a stack. A single PR may remain entirely on the main agent, but it still uses the runtime's gather-classify-act-reconcile loop. A stack adds dependencies and invalidation, not a second pipeline.

The profile is:

| runtime field         | Copilot review definition                                                                                                                                                                                   |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| scope                 | the requested PR or every PR in the requested stack                                                                                                                                                         |
| unit                  | one PR and its remote head                                                                                                                                                                                  |
| unit state            | PR identity, head, assessment, activity watermarks, dirty flag, round and state                                                                                                                             |
| gather                | reviews, three comment surfaces, thread state, requested reviewers, CI, remote head, PR base/merge state and server stack membership                                                                        |
| classify              | `open`, `waiting`, `settled` or `blocked`                                                                                                                                                                   |
| ready work            | actionable findings, CI failures, investigation and justified content fixes                                                                                                                                 |
| dependencies          | stack order; lower rewrites invalidate rewritten uppers                                                                                                                                                     |
| serialized operations | restack, push, reply, resolve and review request                                                                                                                                                            |
| completion            | single PR: settled on its current head; stack: every PR settled plus a blocking-clean holistic review on the final merge-base/top-head range                                                                |
| reopen                | new activity, failed required CI, changed head/base/membership or a changed holistic merge-base/top-head pair                                                                                               |
| stop                  | escalated non-convergence, exhausted round/cost/rate budget, unknown assessment, stuck CI or unauthorized topology                                                                                          |
| consent               | autonomous fixes, push, reply, resolve and re-review through convergence; owned/team stack restacks may force-push with explicit leases and retarget the all-open remainder after a base merge; never merge |

## Scope and consent

Run this workflow only when Diego asks to address a Copilot review. A request for the full agentic loop through convergence grants standing consent, for that loop only, to make review fixes, push, reply, resolve threads, request fresh Copilot reviews and continue without asking again each round. On a Diego-owned or team stack, it also authorizes the `--force-with-lease` pushes required by the orchestrator's restack. If a base merges during an owned/team stack loop, it also authorizes the canonical `gh-stack` trunk retargeting of the all-open remainder after server membership and PR bases are journaled. It never grants consent to merge, any other topology redesign, branch deletion or force-push on a guest/upstream PR.

The stack rewrite workflow is only for a stack owned by Diego or his team. On a guest/upstream PR, keep the branch append-only after review starts: use follow-up commits, never amend, force-push or restack it.

`address` means one local iteration unless Diego explicitly asks to publish it or loop until Copilot suggests acceptance. Without publish or loop consent, gather, fix and verify, then stop before push, reply, resolution or review request. `address!` uses the same workflow with the full build, test and lint gate. Three finding-bearing Copilot rounds per PR is an escalation threshold, not a stop, when Diego requested convergence: raise model/reasoning or respec the fix and allow one escalated round. Stop after that only for non-convergence or an exhausted budget. A cascade-only reopen and a holistic-review fix do not increment the PR's Copilot round; holistic review uses its own runtime review counter. If the assessment is missing or cannot be recognized, finish the current round, quote the overview verbatim and stop.

## PR state and gathering

Extend the runtime unit table with:

| field                            | meaning                                                                                |
| -------------------------------- | -------------------------------------------------------------------------------------- |
| PR and branch                    | stable identity and stack position                                                     |
| head SHA                         | current remote tip and observed version                                                |
| assessment and assessment commit | latest Copilot judgment and reviewed SHA                                               |
| last review ID                   | review-submission watermark                                                            |
| last inline comment ID           | inline-comment watermark                                                               |
| last conversation comment ID     | top-level-comment watermark                                                            |
| reply barrier watermark          | review being addressed plus later activity included before the last successful request |
| dirty                            | local content or ancestry moved                                                        |
| round                            | rounds in which this PR's own findings were addressed                                  |
| state                            | `open`, `waiting`, `settled` or `blocked`                                              |

If the harness cannot load the runtime skill, preserve this compact table in a session SQL table or a file under the session artifact directory. Never keep it only in conversation or `/tmp`, and never commit it to the repository. This is a degraded single-agent fallback, not permission to improvise fleet or topology operations without the canonical runtime.

At the start of every reconciliation, gather **every PR in scope**, including settled ones:

```bash
gh api repos/<owner>/<repo>/pulls/<n>/reviews
gh api repos/<owner>/<repo>/pulls/<n>/comments
gh api repos/<owner>/<repo>/issues/<n>/comments
gh api repos/<owner>/<repo>/pulls/<n>/requested_reviewers
gh pr checks <n>
gh pr view <n> --json state,baseRefName,headRefName,headRefOid
# For stack scope, use gh-stack's server-membership inspection command.
# Use the reviewThreads GraphQL query in github-writing.md for isResolved.
```

Compare IDs against the stored watermarks. A PR is settled only when:

1. the latest recognizable Copilot assessment suggests acceptance;
2. its `commit_id` equals the current remote head;
3. no review or comment newer than the watermarks is unaddressed;
4. CI required by this workflow is complete.

Anything else is open, waiting or blocked. A human/self comment or a review requested manually in the web UI can reopen a settled PR even when its head did not move. A changed base, parent merge, server membership or remote restack also reopens the affected chain and blocks stale prepared mutations. Update watermarks only after classifying and handling new activity.

## Work available while waiting

Apply the runtime's work-conserving waiting policy. Poll all gather sources for every PR, including settled ones, with moderate backoff and within GitHub rate limits.

Start at roughly 30 seconds after expected activity, back off toward 2-5 minutes while nothing changes, and reset after an event. Stagger large-stack queries, persist the next poll time and add randomized jitter. When present, wait at least `x-poll-interval`; obey `retry-after`; and when `x-ratelimit-remaining` reaches zero, wait until `x-ratelimit-reset`. Stop nonessential polling before the remaining budget threatens required writes or reconciliation.

Use free capacity, in this order, for:

1. new human or self-authored requests;
2. new Copilot findings;
3. failed required CI;
4. ambiguous open findings;
5. code/test tracing, reproduction, layer ownership, repository prior art, versioned-API research and verification planning.

Suitable bounded helpers are `explore` for repository tracing, `research` for sourced web/GitHub work, `rubber-duck` for a risky layer decision, `task` for a long read-only command, and `security-review` first for an explicit exploitable vulnerability. Helpers are read-only evidence producers. They never commit, push, reply, resolve, request reviews, switch branches or run `gh stack`.

Do not create speculative code changes to fill capacity. When no safe work remains, sleep with backoff and gather again.

## Requesting a Copilot review

Previous-review replies are a hard precondition. For the first request, seed the barrier with every comment and thread belonging to the review being addressed, regardless of when it was created, plus later activity observed before the request. For subsequent requests, add comments or threads created or updated after the last successful request watermark. Do not pull unrelated older review history into scope. Before every request, verify that each barrier item has a published response:

- `Addressed` has the head pushed in the current round and one-line change;
- `Won't fix` or `Incorrect` has its reason and matching resolution;
- a question has an answer but remains open unless Diego asked to close it.

Journal the intended thread, marker, current-round SHA and body before posting; record the returned reply ID afterwards, then re-read the thread surface. A missing or failed current reply blocks the request. A reply correctly posted in an earlier round remains satisfied after a later restack; do not edit or duplicate it only because its historical SHA changed. Never ask Copilot web for a new review first and reply to the previous review afterwards.

The default request is the REST `requested_reviewers` call below, with the GraphQL `requestReviews` mutation as its fallback. `gh pr edit <n> --add-reviewer "@copilot"` is the native equivalent (`gh` 2.102.0); it is documented in `gh pr edit --help` but untested here, so verify with the `requested_reviewers` read below.

Copilot review effort is `Lite` or `Balanced`. Balanced is the default since 2026-09-28. The changelog of 2026-10-02 says the REST and GraphQL APIs accept a per-request effort, but the public REST reference, the `RequestReviewsInput` GraphQL schema and `gh pr edit --help` show no effort field, so its name is `unverified`. Do not invent one. Without it Copilot uses the effort previously used on the PR, then the requestor, repository and organization settings (see [review effort level](https://docs.github.com/en/copilot/concepts/agents/code-review#review-effort-level)). Choose it in the PR Reviewers menu or in settings, only when Diego asks. The watermark and barrier logic is unchanged: effort never skips the reply precondition, and the effort used is shown in the overview comment.

The reviewer is a bot, not a GitHub `User`. Request it through REST:

```bash
gh api repos/<owner>/<repo>/pulls/<n>/requested_reviewers -X POST \
  -f 'reviewers[]=copilot-pull-request-reviewer[bot]'
```

The login is `copilot-pull-request-reviewer[bot]`, not `copilot-swe-agent[bot]`. Confirm it from an existing review if needed:

```bash
gh api repos/<owner>/<repo>/pulls/<n>/reviews \
  --jq '.[].user.login'
```

A request can return `201` while silently dropping the bot. Verify registration:

```bash
gh api repos/<owner>/<repo>/pulls/<n>/requested_reviewers \
  --jq '.users[].login'
```

The REST response can omit bot review requests even when user requests work normally. If the bot is absent, do not repeat the same REST call. Use the GraphQL `requestReviews` mutation with `botIds`, taking the bot node id from an existing Copilot review:

```bash
pr_id="$(gh pr view <n> --repo <owner>/<repo> --json id --jq .id)"
bot_id="$(
  gh api repos/<owner>/<repo>/pulls/<n>/reviews \
    --jq '[.[] | select(.user.login | startswith("copilot"))][-1].user.node_id'
)"
gh api graphql \
  -f query='mutation($prId: ID!, $botId: ID!) {
    requestReviews(input: {
      pullRequestId: $prId,
      botIds: [$botId],
      union: true
    }) {
      pullRequest {
        reviewRequests(first: 20) {
          nodes {
            requestedReviewer {
              __typename
              ... on Bot { login }
            }
          }
        }
      }
    }
  }' \
  -f prId="$pr_id" \
  -f botId="$bot_id"
```

Verify the returned `reviewRequests` contains a `Bot` named `copilot-pull-request-reviewer`. If no previous Copilot review exists from which to recover the bot node id, stop and use the web request button only when Diego asks. Journal the successful request as an external mutation through the runtime. Request at most once for each new remote head and never cycle DELETE/POST.

Before either request path, assert that every handled thread from the previous round has a published marker reply and disposition. A pushed head with unanswered old threads is not ready for another review.

Match the resulting review by commit, not submission time:

```bash
gh api repos/<owner>/<repo>/pulls/<n>/reviews \
  --jq '.[]|select(.user.login|startswith("copilot"))|"\(.id) \(.submitted_at) \(.commit_id)"'
```

Do not accept a newer review of an older SHA as the requested result.

Copilot reads custom instructions, `AGENTS.md`, skills and `REVIEW.md` (review-only guidance) from the PR head branch, so a change to review guidance can be tested in the PR that makes it ([changelog](https://github.blog/changelog/2026-07-17-copilot-code-review-customization-and-configurability-improvements)).

## Thread disposition and auto-resolution

Every handled inline thread gets exactly one leading marker:

- `**Addressed.** Done in <sha>: <one-line change>.`
- `**Won't fix.** <one-line reason>.`
- `**Incorrect.** <one-line reason the finding does not apply>.`

On an append-only branch, cite the pushed follow-up commit. On a stack, reply only after restack and push, then cite the SHA of the `Address review:` commit as it is after the restack, pushed in the current round.

- For an addressed Copilot comment, reply after the fix is pushed, request the re-review, then let Copilot auto-resolve it. After the review lands, query `isResolved`; if the fixed thread remains open, resolve it manually with `ADDRESSED`.
- For `Won't fix` or `Incorrect`, reply and resolve manually with `WONT_FIX` or `INVALID`.
- Leave a question thread open unless Diego explicitly asks to close it.
- Human-reviewer and self-comment threads do not auto-resolve. After consent, reply and resolve them manually, except for question threads.

The marker and native resolution reason must agree. The canonical GraphQL commands and enum meanings live in [github-writing.md](github-writing.md#resolving-review-threads-and-the-resolution-reason).

## Reading the overview

Use the overview groups as the working set:

- **Open**: triage these (verify, then classify as in the loop step); `new` means the latest commit introduced the finding.
- **Resolved since last review**: verify the linked thread closed.
- **Previously missed**: triage these too, even when the full finding exists only in the overview.

`Findings: None` does not mean nothing is left when Open or Previously missed has entries. Each round, fetch the full body of the latest review and the latest `<!-- ccr-overview-v2 -->` comment, and parse the collapsed and nested `<details>` blocks. Per finding, extract the severity (the `<img>` alt text, for example `Low severity`), the title, the `path:line` and the text. Never ignore a finding because it is low: verify it, then fix it or reply with the evidence.

Body-only findings have no inline thread. Treat each as a work item of the PR unit, keyed by review ID and the review-submission watermark so it is processed once. Fix it and cite the commit in the next reply, or, when declined, post one top-level PR comment per finding with the leading `**Won't fix.**` or `**Incorrect.**` marker and the reason. Never try to resolve a thread that does not exist. A round with body-only findings counts as finding-bearing, and the assessment stays the exit signal.

Use severity and title to prioritize and reference findings. Read low-confidence findings in the review body and judge them on the code. Record review effort for diagnostics, but do not use it for model routing.

Keep sign-offs distinct:

- The **approval assessment** is advisory and is the loop exit signal.
- A formal Copilot `APPROVE` counts only where an admin enabled it and for the configured paths. A new commit dismisses it like a human approval.

Neither form authorizes a merge.

## Reading the review session logs

The Copilot reviewer can store comments that never reach the published review. Read the session log when the summary claims issues but lists `Findings: None`, counts more unresolved issues than open threads, or states a finding too vaguely to act on.

This is an internal, undocumented API that the Copilot CLI uses: it can change or refuse the call at any time, so treat a failure as "no log" and fall back to the published review. The agents API lists the review sessions of a PR by its REST id, not its number. The latest session log is a stream of SSE `data:` chunks carrying the reviewer's tool calls, and each `store_comment` call holds one finding:

```bash
pr_id=$(gh api repos/OWNER/REPO/pulls/NUMBER --jq .id)
gh_user=OWNER_ACCOUNT  # the account that owns the PR, never the default one
headers=(-H "Authorization: Bearer $(gh auth token -u "$gh_user")" -H "Copilot-Integration-Id: copilot-4-cli")
api=https://api.githubcopilot.com/agents/sessions
session=$(curl -s "${headers[@]}" "$api/resource/pull/$pr_id?page_size=100" \
  | jq -r '.sessions | sort_by(.created_at) | last | .id')
curl -s "${headers[@]}" "$api/$session/logs" \
  | sed -n 's/^data: //p' \
  | jq -r 'select(type == "object") | .choices[]?.delta.tool_calls[]?.function
      | select(.name == "store_comment") | .arguments | fromjson
      | "\(.file_location):\(.start_line)-\(.end_line) fixed=\(.fixed)\n\(.comment_content)\n"'
```

- The list returns 20 sessions unless `page_size` is raised. Check that the latest session `state` is `completed` and that its `created_at` matches the review request, not the review submission. An `in_progress` session has a partial log.
- A completed session with no `store_comment` call means the summary text has no finding behind it, so it is not actionable.
- A stored comment also carries `comment_type` and `fix_skip_reason`. Treat an unpublished comment like any other finding: verify it on the code, fix it or record why not, and mention it in the commit body since there is no thread to resolve.
- Read the other tool calls (`rg`, `view`, `glob`) and `delta.content` to see what the reviewer inspected when a verdict is unclear.
- `gh agent-task` does not list review sessions. The same endpoint with `?resource_type=pull&resource_id=` returns an empty list.

## Canonical per-PR iteration

Every PR uses this domain primitive inside the runtime loop:

1. **Gather and classify.** Read all surfaces, compare watermarks, inspect CI and set the state.
2. **Triage, address and verify.** The Copilot reviewer lacks the big picture and is often wrong, so verify each finding 1:1 against the code, docs, tests or a quick run (use a subagent for claims that do not add up or are expensive to check). Classify it: valid (fix), partly valid (fix what is true, say what is not) or wrong/out of scope (reply with the evidence, no change). Never apply a finding because it was made. The same verification applies to human comments, respectfully and with evidence. Run targeted build, tests and lint; `address!` runs the full gate.
3. **Publish and reply when consent covers it.** Push final branch state, post every previous-review reply with reachable SHAs, apply disposition rules and verify the replies landed. Otherwise stop with the verified local diff and draft replies.
4. **Request and wait.** Only after the reply barrier passes, request one fresh review when the head moved. If only a `Won't fix` or `Incorrect` disposition happened and the head did not move, do not churn a same-SHA request. Mark the PR blocked if the unchanged assessment still wants changes.
5. **Assess.** Gather again, verify auto-resolution and update watermarks. Settle or increment the round only if this PR's own findings were addressed.

Stop when the escalated round still does not converge, the assessment is unrecognizable, the same concern repeats, a required check is stuck, the budget is exhausted, or the safe next action needs an unauthorized topology decision.

## Stack scheduling and invalidation

A stack schedules the same per-PR primitive:

1. Gather every PR. Address independent content findings in parallel when the runtime's fleet criteria are met.
2. Treat worker returns as a stream. Judge and persist each committed fix immediately; never wait for every subagent to finish before advancing a dependency-ready layer.
3. Select the lowest returned layer whose lower dependencies are settled and whose branch includes their current heads. Release only worktrees that the publication uses, then have the orchestrator push only that verified branch with the explicit branch-scoped lease procedure from `gh-stack`.
4. For each published layer, immediately pass its reply barrier and request a fresh review when needed. Continue gathering and processing other workers while its review and CI run.
5. Queue an upper result, without publishing or requesting review, while any lower layer can still rewrite it. A lower rewrite marks every affected upper dirty and reopens it because its assessment SHA is stale.
6. Gather all PRs again. Skip work and re-requests for settled, unchanged PRs, but keep gathering them so new activity can reopen them.
7. When every PR is provisionally settled, run the holistic review below. Its findings re-enter the same layer-assignment and per-PR loop.

Only gathering, investigation and independent content edits are parallel. Restack, push, reply, resolution and review requests are serialized.

Streaming does not weaken ordering: lower layers may advance while upper workers run, but an upper review request waits until every lower layer that could rewrite it is settled. If the only available stack command would rewrite a branch still owned by an active worker, keep useful work moving elsewhere and queue that publication instead of waiting idle or disturbing the worktree.

Streaming is not permission for partial topology surgery. Before publishing a layer, re-gather server membership and PR bases, verify the layer still has the recorded parent, verify its branch contains the settled parent head, and compare local and remote tips against the journaled explicit lease. Push only that ref with the branch-scoped Git command defined by `gh-stack`. Do not use `gh stack push`, `sync`, `submit`, `link`, `unstack`, rebase, reorder or restack to make a layer streamable. If its ancestry is stale, or publication would move an active worker's branch, change a PR base or alter grouping, queue it until the normal serialized stack operation is safe.

Under this profile, the orchestrator executes topology mutations, rebases and pushes. Workers edit, verify and optionally commit only. Exact commands, lock handling, sync and recovery come from `gh-stack`. Push only branches whose content or ancestry changed, including every upper branch rewritten by restack. Apply [identity.md](identity.md) before push.

## Holistic stack review

Per-layer Copilot reviews cannot see the full feature. Before declaring a stack converged, fetch and verify every gathered remote head, compute the immutable merge-base of the gathered remote trunk and final `top-headRefOid`, and run one deep review of `<merge-base-sha>...<top-headRefOid>` as required by `gh-stack`. Never use an unverified local top branch as the endpoint.

Persist the merge-base SHA, top remote head SHA, reviewer result, holistic round and unresolved blocking findings. The record is valid only for that exact pair and while the final gather confirms the same remote stack. Assign findings to owning layers, apply fixes through the normal loop, restack and reconverge per PR, then rerun the holistic review on the new pair. Stack completion requires no unresolved CRITICAL, HIGH or MEDIUM finding in this record. Stop and report after three holistic correction cycles unless Diego explicitly raises that budget; the third cycle uses a smart reviewer and highest practical reasoning.

## Layer and commit ownership

Assign every finding before editing:

1. Put the fix in the earliest layer whose responsibility and public contract require it.
2. If an upper comment exposes a lower defect, fix the lower layer, mark every rewritten upper dirty, and reply on the original thread after final push.
3. Keep the fix in the commented layer when moving it lower would add an abstraction, dependency or behavior that layer does not need.
4. Keep cross-layer findings on the main agent or serialize explicit dependent tasks. Two workers never decide ownership independently.

Commit policy: never squash, amend, fold or fixup after review has started, on any branch.

- On an owned/team stack, add a new `Address review: <what was hardened>` commit (bulleted body, one bullet per reviewer concern) on the layer that owns the change. Then restack the layers above with a plain rebase: their existing commits keep their content and only the parents change. Push with `--force-with-lease` only as needed for that restack. The reply cites the SHA of the new `Address review:` commit as it is after the restack. Do not use `git absorb` or autosquash for review fixes.
- On a non-stacked PR, and on every guest/upstream PR, add the same follow-up commit and never amend or force-push.
- A worker may create only the assigned `Address review:` commit. The orchestrator verifies its paths, identity, trailers, commit range and checks.

## Runtime overrides and handoff

Default to the main agent. The review-specific fleet threshold is at least two open PRs with substantial independent fixes and unambiguous layer ownership. Overlapping interfaces, a design-changing lower fix, uncertain topology, costly bootstrap or a guest PR keep execution on the main agent.

Record the selected execution mode and reason in the loop state. It may change between rounds as evidence changes, but never during a restack/push phase. A fleet-to-main transition must first use the runtime teardown: stop writers, judge and integrate or reject their work, then remove only clean run-owned worktrees and record a new baseline. Copilot severity and review effort do not determine model tier. Each brief adds the exact PR, layer, findings, surrounding contract, commit policy and the prohibition on topology changes, push, replies, resolution and review requests. Copy the applicable Diego persona constraints because subagents do not inherit this skill.

For a stack fleet:

Record topology, every pre-round local and remote head, and occupied worktrees before delegation. Follow the runtime's generic worktree handoff and the exact restack procedure from `gh-stack`. If a worktree needs a bootstrap step before build, test or lint, budget that cost before choosing fleet fan-out. Push the moved chain bottom-up with journaled explicit leases only on Diego-owned or team branches, and only as needed for the restack. Reply only with the post-restack SHAs of the `Address review:` commits pushed in the current round, then request eligible reviews.

An unexpected branch move, dirty unrelated worktree, conflict, topology mismatch or third remote SHA is a stop condition. Never commandeer an existing worktree or improvise `link`/`unstack` as repair.
