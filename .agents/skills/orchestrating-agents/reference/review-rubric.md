# Review rubric

## Contents

- How to review
- Optional independent reviewer
- Contrarian review: challenge, do not just check
- Reviewer voice: blunt, ranked, evidence-anchored
- Other review lenses
- Cross-validation and direct checks
- Severity gating
- Multi-round convergence
- Contribution-quality signals
- Verdicts
- Nit threshold
- Iteration cap
- Agent, model and reasoning-effort routing
- Concurrency & cost budget

## How to review

Review **by evidence, against the acceptance criteria** - not by re-reading the whole change. Use the subagent's structured return: check that the reported build/test/lint actually cover the criteria and are green. For non-code deliverables without a suite, define deterministic structural checks yourself (for example link/anchor/frontmatter/format validation or a small script) and use that output as the gate. If evidence is missing but no implementation defect is established, emit `needs-evidence`: run the check directly or ask the same worker for the missing output without requesting code changes. Cheaper models hallucinate; trust checks, not prose. Concrete evidence means `file:line`, the exact command run, and its output; a bare claim ("looks correct", "tests pass") is unverified until it carries that proof. Resolve a finding by evidence, never by counting how many agents asserted it. Review directly when the evidence and artifact fit one bounded inspection. When the artifact needs substantial separate context, is critical, or is outside the orchestrator's expertise, route it to one separate reviewer subagent. Nested review is allowed only when the root brief explicitly authorized nested delegation.

For an explicit vulnerability or security-review request, invoke the dedicated `security-review` agent first. Generic reviewers may supplement it but never replace it.

Reviewer findings are also evidence to arbitrate, not instructions to forward. Reconcile each finding with acceptance criteria and ledger decisions; reject findings that would undo an explicit choice, such as a user-approved exception.

## Optional independent reviewer

For uncertain, larger, critical, or ambiguous artifacts, spawn a **read-only** reviewer subagent separate from the worker. It judges the diff or artifact against the acceptance criteria and returns prioritized findings. Use it when independent review justifies the extra agent, especially when the first agent reports uncertainty or the change has broad impact. The orchestrator still arbitrates and emits the final verdict.

Use a **rubber-duck** agent for a different job: plan, decomposition, or risky design critique before or alongside implementation. Rubber-ducking challenges reasoning and can catch logic or design flaws before there is a diff to review. When the feature is available for the current Claude/GPT session, Copilot CLI selects a contrasting model automatically, which supplies useful model diversity. Use a reviewer for evidence-based artifact review after work returns; use a rubber-duck when the plan or reasoning itself needs scrutiny.

## Contrarian review: challenge, do not just check

A contrarian argues against the change's shape rather than rechecking acceptance criteria. Use one for:

- A judgment-heavy or high-blast-radius change, a new abstraction, public API, or dependency, or another hard-to-reverse decision;
- a performance claim that needs its measurement attacked;
- plausible technical debt or divergence from a simpler/native path;
- a design before implementation, while changing it is easy.

Give it the task's bounded named risks. Ask it to state the strongest case for the change, argue for rejection or doing nothing, name a concrete simpler alternative and measure any performance claim. Its output does not decide the outcome; arbitrate and record it.

## Reviewer voice: blunt, ranked, evidence-anchored

Lead with blockers, rank by severity then confidence, and make each finding self-contained: stable `file:line`, quoted context, evidence and concrete fix. Do not add unnecessary praise or hedge defects. Directness does not replace proof; an unsupported claim is `needs-evidence`.

## Other review lenses

Apply only the lens the risk needs: future-reader/maintainability, project-specific domain invariants, scope/YAGNI, or adversarial correctness. Explicit security reviews still go to `security-review` first.

Run narrow enumerable checks directly. Delegate a lens only when it requires substantial separate context; use a smart-tier reviewer when it needs judgment.

## Cross-validation and direct checks

Use one smart-tier reviewer when independent judgment is warranted. Run cheap, deterministic probes such as ASCII/style, link, schema or config consistency checks directly when they take only a few tool calls. Delegate at most two probes only when each needs substantial separate context or long-running commands.

## Severity gating

Rank findings by severity, then confidence. Only **CRITICAL/HIGH/MEDIUM** block integration; **LOW** items are improvements, not blockers, and never gate a commit on their own. Tell the smart reviewer to skip cosmetic nits (a cheap probe owns those) and report the blocking findings first.

## Multi-round convergence

Run review as reviewer\<->orchestrator rounds until no blocking findings remain, not a single pass. Convergence means zero unresolved CRITICAL/HIGH/MEDIUM findings. Each LOW finding ends `actioned`, `refuted` or `accepted-deferred`; LOW never consumes another round by itself. Record deferred LOW items as backlog todos and the final verdict in the ledger. Respect the iteration cap below.

## Contribution-quality signals

Judge an agent's output (dev or review) by two quick checks before reading the diff in full:

- **Change scope** (`git diff --stat`): did it stay in the assigned files.
- **Rework ratio**: how many correction rounds before lint+tests go green. A contribution that returns already-green with evidence attached is high quality. Do **not** turn this into a cost/time-versus-value data exercise. The cost-and-scope tradeoff is a planning-time decision. In an interactive session, ask when a real scope choice exists. In unattended mode, follow the supplied objective and budget, choose a conservative cap, and record the decision rather than blocking for input that cannot arrive.

## Verdicts

Emit exactly one per return, and log it in the ledger:

- **approve** - all criteria met with green evidence. Commit the task (commits are integrated serially): the orchestrator commits in shared-tree mode; an explicitly authorized worker may commit in its isolated worktree. Verify the commit before integration (see [git-modes.md](git-modes.md)), then release the subagent and mark `done`.
- **accept-with-nits** - criteria met; only trivial issues remain. You apply the nits, re-run the fast check because a "trivial" edit can still break the build, then commit. (See threshold below.)
- **needs-evidence** - no defect is established, but required proof is missing or does not cover the acceptance criteria. Run the check directly or ask the same worker for evidence; do not request code changes.
- **request-changes** - criteria not fully met, but the approach is sound. Send a concrete fix list to the **same** live subagent; increment the round counter. (Requires a resumable spawn; if only one-shot, respawn with full context.)
- **reject-and-respec** - wrong approach or the task was mis-scoped. Rewrite the brief and respawn (possibly at a higher tier).

## Nit threshold

An issue is a **nit** (eligible for accept-with-nits) only if **all** hold:

- No behavior change (naming, formatting, a stray import, a comment, a tiny guard).
- Small: a few lines, localized, no logic redesign.
- Faster for you to fix than to round-trip.

If it changes behavior, touches logic, or spans multiple sites -> `request-changes`. Keep nit-fixing rare and tiny: your edits still consume your context budget.

## Iteration cap

Default **max 2-3 review rounds** per task. On exceeding it:

- Escalate the subagent's model tier and retry once, **or**
- Re-scope the task (`reject-and-respec`), **or**
- Take the task over yourself only as a last resort. Record the escalation/decision in the ledger.

## Agent, model and reasoning-effort routing

Choose the agent type first, using the mapping in [task-brief.md](task-brief.md#spawning--tiering-notes). Then choose model tier and reasoning effort separately from fragility, ambiguity and reversal cost. Prefer harness-configured model defaults unless the user or active instructions explicitly require an override.

| task shape                                                        | model tier      | reasoning effort  |
| ----------------------------------------------------------------- | --------------- | ----------------- |
| exact mechanical edit or deterministic check                      | fast/cheap      | lowest supported  |
| bounded implementation with clear tests                           | fast/cheap      | default           |
| ambiguous root cause, API/design judgment, hard-to-reverse change | smart           | high              |
| security, concurrency or critical cross-system reasoning          | dedicated/smart | highest practical |

Raise effort first when the model has the needed capability but reasoned too shallowly. Raise the model tier when broader context or stronger judgment is needed. Raise both after a wrong assumption or repeated acceptance-criterion failure. Do not escalate a deterministic failure with a clear local correction.

## Concurrency & cost budget

- Two limits bound parallelism: the harness **hard ceiling** (Copilot CLI: `subagents.maxConcurrency` in `/settings`), which you cannot exceed, and your **soft cap** at or below it. Keep at least one slot free for review, a revision or an urgent dependency unless the user explicitly prioritizes all capacity.
- Drive the soft cap primarily from credit, latency and rate-limit budgets. Use `/limits --max-ai-credits` and `/usage` when the harness exposes them.
- Watch local load, memory and GPU only for tasks whose resource profile runs local builds, tests or inference; those signals do not measure remote agent capacity.
- Default the soft cap to a conservative few (**\<=3-4**) and adjust from what you observe; monitoring it is the orchestrator's job, since the ceiling reflects the platform's limit and not the machine's real-time load.
- Cap total review rounds across the feature; if the budget is blown, pause and stop and plan again rather than repeating failed rounds.
- Default to at most one independent reviewer and two substantial probes per task; extra agents require a named risk that cannot fit the existing review.
- Prefer serial execution for tasks sharing files even if deps allow parallelism.
