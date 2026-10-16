---
name: guide-me-through
description: >-
  Helps the user explore and understand unfamiliar code: a feature, module or
  area of a repo, a bug location, a PR or a stacked PR under review (context
  before the PR first, then how the PR fits in). Asks scoping questions first,
  then explains with path:line evidence, one-shot or as an interactive guided
  tour in chat or in the Zed IDE. Invoked only explicitly, as
  /guide-me-through <target>, for example "/guide-me-through PR 123" or
  "/guide-me-through the xyz feature in this repo".
disable-model-invocation: true
---

# Guide me through

Explain code the user does not know, at the depth they need. Exploration drifts too wide (a file listing) or too deep (a full audit), so fix the scope before reading anything. This skill is read-only: it does not change code.

Input: `/guide-me-through <target> [delivery]`. The target is a repo area, a feature, a symptom, a PR number or URL, or a stack.

Delivery can be given in the call as free prose, and then the delivery question is skipped: `direct` ("direct", "one shot", "just tell me"), `guide in chat` ("guide", "in chat", "step by step") or `guide in IDE` ("in zed", "in the IDE"). Examples: `/guide-me-through PR 123 in chat`, `/guide-me-through the auth flow in zed`, `/guide-me-through direct`. The same applies to `map`, `trace` and `deeper` ("trace the login flow"), which skip the breadth or depth question.

Shorthand: `/guide-me-through ?` lists this usage (target forms, delivery words, tour replies) and does nothing else. Unlike the shared convention, empty input does not run `?`: it infers the target, because the common call is "this branch, now".

With no target, infer it instead of asking:

- On the default branch (`main`/`master`; get it with `git symbolic-ref refs/remotes/origin/HEAD`, or `gh repo view --json defaultBranchRef -q .defaultBranchRef.name` when that fails): a top-level map of the whole repo.
- On another branch with an open PR (`gh pr view --json number,baseRefName`): that PR, with stack detection (see [reference/reviewing-prs.md](reference/reviewing-prs.md)).
- On another branch without a PR: the branch diff against the default branch, treated like a PR.

State the inferred target in the scope sentence below so the user can correct it. Ask only if none of these applies (for example a detached HEAD).

## Step 1: Scope (before exploring)

The purpose of this step is to stop the exploration from going too wide or too deep, so do not skip it. Keep it light: infer what the request already tells you (a PR target means review-style understanding, a symptom means debug), and ask only what is still open, in one structured question form (`ask_user` on Copilot CLI) with defaults and at most 3 questions. Put the reason inside each question.

1. **Purpose**: learn / review / debug / change. It sets the reading strategy.
2. **Breadth or depth**: `map` (breadth, the default for learn and PRs), `trace` (one scenario end to end, the default for debug and change). The user can ask for `deeper` later (invariants, history, callers and callees).
3. **Delivery**: `direct` (one answer, the default), `guide in chat` (step by step, the user steers) or `guide in IDE` (Zed, see [reference/guided-session.md](reference/guided-session.md)).

Do not ask about familiarity or budget. Assume the user knows the language but not the domain and a quick budget, state that, and adjust when they say otherwise.

For a PR target, the shape applies to the pre-PR context (default: a map of the touched area). Do not ask about review findings: this skill stops at understanding (see Not for).

State the chosen scope in one sentence, then start. If the scope turns out wrong, say so and ask to adjust instead of silently widening.

## Step 2: Explore

Read before claiming. Spend effort in proportion to the scope:

- **quick** (default, also for `map`): the entry point and the code on the path, plus one test that covers it; for a map, entry points, module boundaries and 3 to 5 pointers, without blame or history. `path:line` for the key steps.
- **thorough** (`deeper`, or the user asks): README, `AGENTS.md` / `CONTRIBUTING.md` / `ARCHITECTURE`, build and test layout, then code. `git log` (`-S` finds when something appeared) and `git blame` on key files for intent. `path:line` for every claim.

Tests are the cheapest spec. Without tests, use call sites, examples and docstrings, and say the result is less certain.

| Shape  | Method                                                                                     | Output                                       |
| ------ | ------------------------------------------------------------------------------------------ | -------------------------------------------- |
| map    | Entry points, module boundaries, data flow in one paragraph each                           | Module map plus 3 to 5 "start here" pointers |
| trace  | State a hypothesis for one scenario, follow it from entry to effect, confirm or correct it | Ordered steps, each with `path:line`         |
| deeper | Trace plus invariants, error paths, callers and callees, history                           | Evidence, then open questions                |

Rules of thumb:

- Follow one path fully before widening. Depth on one path beats a shallow list of files.
- Never infer behavior from a file name. Open it.
- Every claim carries `path:line` (or a commit SHA). Mark what you did not verify as `unverified`.
- Follow a single trace yourself. Delegate to read-only `explore` subagents only for independent broad areas (for example mapping several modules in parallel), with the scope and the output shape in the prompt.
- Evidence is plain `path:line`. Only the IDE delivery uses clickable links, through `working-in-zed`.
- Read-only on the user's code. Do not write files unless the user asks.

## PR and stacked PR targets

Context first, then the change. Details, stack detection and large PRs: [reference/reviewing-prs.md](reference/reviewing-prs.md).

1. **Pre-PR context**: what the touched area does today on the base branch, who calls it, which tests cover it, why it exists (blame, linked issues).
2. **The PR in that context**: what it changes, which part is the core, what it leaves alone.
3. For a stack, do steps 1 and 2 on the whole stack as one change (layers do not matter when zooming out), then walk the layers from the bottom up. Finish with a recap of how the feature was split across layers and why, to guide a practical review.

## Step 3: Deliver

- `direct`: the answer in the shape above, then "next" pointers. Keep it short.
- `guide in chat` or `guide in IDE`: follow [reference/guided-session.md](reference/guided-session.md).

End with 2 or 3 offers to go further (deeper on a step, another path) rather than continuing unasked. Questions you noticed go under "open questions".

## Not for

- Review findings and comments: when the user wants them, stop after the understanding and suggest `opening-hunk` (to open the diff in Hunk) and `hunk-review` (to add inline comments in a live Hunk session), or a review as the user. This skill hands over the context it gathered.
- Writing or changing the code.
