# Reviewing: replying to feedback, and reviewing others

## Contents

- Replying to a review he receives
- Reviewing someone else's PR
- Mechanics with `gh`

## Replying to a review he receives

Read **every surface** before replying, because they are three separate GitHub APIs and missing one loses maintainer asks:

1. **Review submissions** (approve / changes-requested / commented bodies): `gh api repos/<owner>/<repo>/pulls/<n>/reviews`.
2. **Inline review threads** (comments anchored to a file and line): `gh api repos/<owner>/<repo>/pulls/<n>/comments`.
3. **Conversation comments** (top-level PR comments, i.e. issue comments): `gh api repos/<owner>/<repo>/issues/<n>/comments`.

`gh pr view --json reviews` and the `pulls/<n>/comments` endpoint do **not** include the conversation comments, and maintainers routinely put imperative asks there ("target develop instead of the release branch", "cut down the tests"). Always fetch all three, for every PR, before deciding what needs doing.

**Diego's own comments on his own PR count as asks to act on, not author chatter.** He often reviews his own branch and leaves the work items as self-comments (inline on a line, or top-level), sometimes the only place a task is written down. Do not filter out a comment because its author is the PR author or the currently active account: read his self-comments on every surface and treat each actionable one as an instruction to fix, the same as a maintainer's, then reply and cite the fixing SHA like any other thread. The consent rule still holds for pushing and resolving, but a self-comment left as a to-do is exactly the signal to do the work.

**Verify before acting or replying.** Treat every review comment as a claim, not as the truth. This holds for the Copilot agentic reviewer (it lacks the big picture and often errs) and for human reviewers alike. Check each claim 1:1 against the code, docs, tests or a quick run; use a subagent for claims that do not add up or are expensive to check. Then classify:

- **Valid**: fix it.
- **Partly valid**: fix what is true and say plainly what is not.
- **Wrong or out of scope**: no change, reply with the evidence.

Never apply a suggestion just because it was made, and never reply as if a claim were true without having checked it. When a claim does not hold, say so plainly with the evidence (command, output, file and line). For a human reviewer, do it respectfully and keep the tone rules below; the verification still applies.

The pattern, in order:

1. **Acknowledge in one short clause when the reviewer is right.** "Good catch", "Good point", "Good question". Nothing longer, no praise inflation.
2. **Answer the technical question directly, and explain why.** Not "it's safe" but the mechanism that makes it safe, at the level of the language or the API. Example: "This line cannot throw: passing the error_code argument selects the noexcept overload of std::filesystem::is_regular_file, and ::access is a plain C call that only returns a status."
3. **Explain a deliberate choice when the reviewer questions an omission**, with the reason it was deliberate. Example: the error code is not logged because the loop probes every PATH entry and misses are benign, so logging would be noise.
4. **When the comment exposes a real weakness, say so plainly**, even if the literal objection was wrong. This is the most characteristic move: "That said, you made me realize the previous behavior was not great: ... I pushed <sha> to improve this."
5. **Cite the fixing commit SHA** and mention the tests that were added. This is how you make the maintainer's re-review fast: the SHA links to the exact follow-up diff, so keep it outside code spans so GitHub renders it as a link: "Fixed in 0ba959e2a2." "Covered by the new unit tests." Every "done" or "agreed" reply should carry the hash that backs it, never a bare "fixed" with nothing to click. Keep issue and PR references such as #123 outside code spans for the same reason, or use an explicit Markdown link.
6. **Push back with reasoning when the reviewer is wrong.** Never change the code just to close a thread. State the constraint that makes the suggestion unworkable and offer the alternative that was taken instead.
7. **Cross-reference companion threads** when the same concern spans two files: "see the companion thread".
8. **Leave the thread open** when the reviewer asked a question, so the reviewer can confirm and close it. Resolve it only when Diego explicitly says to.

Shape of a real reply:

> Good catch. The compat shim is a no-op on the Humble/Kilted docker build (`ament_target_dependencies()` still exists there) and that CMake predates 3.29 semicolon-list support, so we can't merge two entries. We now detect a caller-supplied `-DCMAKE_PROJECT_INCLUDE` and honor it (skipping our shim, with a note) instead of clobbering it. Fixed in 418bb1167b.

Never: "Thanks for the feedback! I've updated the code accordingly." That is content-free and does not sound like him.

Keep replies lean: use backticks only on the symbol, file or SHA that matters, not on every noun, and explain only what the reviewer needs to re-review, not the whole diff the linked commit already carries.

## Reviewing someone else's PR

Read the repo's sources of truth first (`CONTRIBUTING.md`, `AGENTS.md` or `copilot-instructions.md`, PR and issue templates, `CODEOWNERS`, and any task-specific docs the change touches) instead of guessing its conventions. Then review substance before tone.

### Read the tests first

Tests reveal the intended behavior and its coverage, so read them before the implementation. Ask whether the test would fail without the change, whether it covers the edge case the change actually touches, and whether it tests behavior rather than a mock. A green suite that would not catch the regression is itself the finding, not a reason to relax.

When the change adds behavior with no test, ask for one, but only for the high-signal cases: the path the change actually introduces, the regression it prevents, the edge case that would break silently. Do not ask for tests to raise coverage for its own sake, for a trivial getter, or for a case the language or the type system already guarantees. Name the specific case worth covering, not "please add tests".

### Zoom out beyond the diff

Reasoning over the whole repository, not just the changed files, is the main edge an agent has over a human reviewer anchored on the diff, so use it. Before judging individual lines, check whether the change belongs in this repo and fits the surrounding design. If you find a major design flaw, say so and stop, since the rest of the diff may not survive the redesign.

Then trace the blast radius into code the PR never touches:

- call sites of a changed function, interface, or format elsewhere in the repo
- invariants and implicit contracts that unmodified consumers still rely on
- backward compatibility of a changed signature, schema, or serialization
- docs or comments left stale by the new behavior

Name the highest-risk consumers so the scope of the change is explicit. When a shared surface changes, weigh the cost on every downstream owner, not just the one the author had in mind.

### Review a stacked PR against the whole stack

When the PR is one entry of a stack (`gh stack`, Graphite, or a manual chain of dependent branches), review it against the bigger picture of the stack, not just its own diff. The single PR is a slice of a larger change split for reviewability, so a slice that looks fine alone can still be wrong in the whole:

- read the stack in order first, so you judge each PR against the state its parent leaves, not against `main`;
- check that the split itself makes sense: each PR should stand as one coherent step, and a change does not belong lower or higher in the stack;
- watch for a decision made in one PR that a later PR contradicts or reworks, and for churn where an early PR adds something a later one deletes;
- confirm each PR builds and its tests pass on its own parent, since that is how it lands, not only at the tip of the stack;
- keep the finding on the PR it belongs to: if the real problem is in a parent, say so there, and if it only emerges from the combination, name the other PR so the author sees the coupling.

Do not add a stack note to the review or the description (the tooling renders the stack already, see [github-writing.md](github-writing.md)); this is about reasoning over the stack, not annotating it.

Also catch the mechanical slips a human skims past: an off-by-one, an inverted condition, a copy-paste error, a mismatched argument order.

Some of the highest-signal problems pass every static check yet are still wrong:

- a new conditional bolted onto an unrelated flow, instead of its own helper, state, or policy
- repeated conditionals on the same shape, which signal a missing dispatcher or model rather than one more branch
- a refactor that relocates complexity instead of removing it: count the concepts a reader must hold, and if that count is unchanged it is not cleaner
- feature-specific logic leaking into a shared or general-purpose module instead of staying in the layer that owns it
- a gratuitous `any`, a cast, or a silent fallback that hides an unclear invariant
- a surprising behavior change: a silent wrong result where a loud failure was expected, an unrelated side effect (mutating a lock file, a config, a global), or a new flag that changes behavior beyond the dimension it names (see "Least astonishment" in [coding-style.md](coding-style.md))
- an overengineered solution: a speculative abstraction, a generic layer for a case that does not exist, or a hand-rolled reimplementation of something the standard library, an existing repo helper, or a current dependency already provides (see "Minimize complexity" in [coding-style.md](coding-style.md))

When you flag one of these, propose the move, not just the problem: name the restructuring (a dispatcher, an extracted helper, relocating the logic to its owning layer), because "this is complex" leaves the author guessing.

### What the toolchain already owns

Do not spend comments on anything a formatter, linter, type checker, pre-commit hook, or CI job already owns: formatting, import order, naming the linter enforces, a type the checker would reject. Raise a type or layering point only when the change passes the static check yet is still wrong. This is the same rule as "do not nitpick formatting or naming that the linters own" below, applied before you write a single line comment.

### Put every finding inline, never in the review body

Each concrete finding goes as an **inline comment anchored to the code line it is about**, never in the review submission body. The body carries only the verdict and a one or two sentence summary (approve, request changes, the overall shape of the change), plus at most a pointer to how many inline comments follow. A specific ask buried in the body is easy to miss and cannot be resolved as its own thread, so if you catch yourself writing "line 189 says ..." or "in `foo.py` the ..." in the body, move it to an inline comment instead.

Anchor to the exact line. When the line you want is outside the diff hunk (an unchanged docstring, a caller the PR never touched), anchor the inline comment to the nearest related changed line and name the real line or symbol in the text, so GitHub still renders it as a thread on the code. Use the multi-line inline range (`start_line` + `line`) when the finding spans several lines.

Submit the body and all inline comments as a single review (the `pulls/<n>/reviews` call with a `comments` array), not as a scatter of standalone comments, so the author gets one coherent review to work through.

### Order and label the findings

Order findings by leverage: correctness and design first, then structural regressions and missed simplifications, then cosmetics. A few high-conviction comments beat a long list. If you have one structural problem and ten nits, the structural problem is the review, so lead with it and do not bury it under the cosmetics.

Label each comment with its weight in bold so the author knows what is required:

- no prefix for a change he should make;
- `**Critical:**` for a blocker (data loss, a real bug, a security hole);
- `**Suggestion:**` or `**Consider:**` for a concrete non-blocking improvement;
- `**Question:**` when the answer determines whether a change is needed;
- `**Nit:**` for a trivial optional;
- `**FYI:**` for context only.

A repo with its own severity labels wins over this ladder.

#### Comment structure: title vs inline

Format each inline comment depending on whether it is non-trivial or trivial:

- **Non-trivial findings** get a concise title in bold combining the prefix and a short headline (`**<Prefix>: <Title>**`), followed by a blank line, and then the comment body (1–2 compact sentences):

  ```markdown
  **Critical: Guard against singular mass matrix configurations**

  Did you check this when the free-floating mass matrix $M$ is near-singular? Inverting it directly will produce `NaN`s in complex scenes; we should either use `pinv` with damping or return a loud error before propagating invalid state.
  ```

  ```markdown
  **Suggestion: Centralize default sensor rates in base class**

  Do you think it's worth defining these sane defaults directly in the base class? This avoids scattering magic constants across robot-specific models, and downstream configs can still override them if needed.
  ```

  ```markdown
  **Question: Benchmark evidence for index-based indexing**

  Do you have any benchmark showing a measurable gain by switching from joint names to indices? If the gain is negligible, I'd prefer keeping names for readability and error-checking on invalid joint lookups.
  ```

- **Trivial / optional findings (`**Nit:**`)** stay inline on the first line with no headline and no blank line, directly followed by the note or a GitHub `suggestion` block:

  ```markdown
  **Nit:** rename `idx` to `joint_idx` for consistency with the rest of the module.
  ```

  ````markdown
  **Nit:** missing trailing comma to keep the multi-line list diff-friendly.
  ```suggestion
      "joint_state_broadcaster",
  ````

  ```

  ```

### Tone

**Approval messages are short and warm.** Approve when the remaining concerns are non-blocking, and say so.

```
Looks good, thanks!
Looks great!
Thanks for your contribution! It is a small and clean fix.
I left a few comments, approving regardless.
```

**Inline comments are questions or proposals, not orders.**

**Keep each comment compact and to the point.** One or two sentences: the ask and the one reason that backs it. Do not restate the diff, do not re-derive the whole justification the reader can see, do not hedge in three clauses. If a comment needs a paragraph, the finding is really a design point for the body or its own thread. Match the length of the examples below, not longer.

```
Do you think it's worth to write some sane defaults directly in the scene base class? This would probably be less surprising to users. Then, downstream consumers could override the values if needed.

Did you check this change when the free-floating mass matrix $M$ corresponds to almost singular configurations? I fear that this change might introduce problems on more complex simulations.

I'd go with `pinv` instead of `inv`. The result should be the same as `lstsq`, if it's faster, much better :)
```

Traits to reproduce:

- **Ask for evidence before accepting a tradeoff.** "Do you have any benchmark supporting the need to switch from names to indices?" And state the principle behind the objection: maintainability and robustness beat micro-optimisation, centralised logic beats scattered magic indices.
- **Name the concrete risk**, not a generic worry: what breaks, in which configuration, and why the current design prevents it.
- **Quantify the cost when you can.** "This adds ~50ms per item in the list" lands harder than "this could be slow", and turns a vague worry into something the author can measure.
- **Self-correct in public.** When he was wrong he appends an `Edit:` note or strikes through the wrong part with `~...~` instead of deleting the comment.
- **Use GitHub `suggestion` blocks** for one-liners, prefixed with `Nit` when trivial.
- **Do not nitpick formatting or naming that the linters own.**
- Praise what deserves it, briefly and specifically: "The improvement of $\\dot{J}$ was much needed."
- Concerns that are worth raising but not worth blocking are stated, then the PR is approved anyway.

## Mechanics with `gh`

```bash
gh pr view <n> --json title,body,reviews
gh api repos/<owner>/<repo>/pulls/<n>/reviews   --jq '.[]|select(.body!="")|{user:.user.login,state,body}'
gh api repos/<owner>/<repo>/pulls/<n>/comments  --jq '.[]|{id,path,line,user:.user.login,body}'
gh api repos/<owner>/<repo>/issues/<n>/comments --jq '.[]|{id,user:.user.login,created_at,body}'

# submit a review with the verdict in the body and every finding as an inline
# comment, all in one call (comments[].line is a line in the PR head / RIGHT side)
gh api repos/<owner>/<repo>/pulls/<n>/reviews -f event=APPROVE -F body=@body.md \
  -f 'comments[][path]=src/foo.py'   -F 'comments[][line]=210' -f 'comments[][body]=**Nit:** ...' \
  -f 'comments[][path]=src/foo.py'   -F 'comments[][start_line]=220' -F 'comments[][line]=228' -f 'comments[][body]=**Suggestion: <Title>**<blank line>...'
# for many/complex comments, POST a JSON payload instead:
#   {"event":"COMMENT","body":"...","comments":[{"path":"...","line":210,"body":"..."}]}

# reply inside an existing inline thread
gh api repos/<owner>/<repo>/pulls/<n>/comments/<comment_id>/replies -f body='...'

# edit a comment already posted, instead of stacking a new one
gh api -X PATCH repos/<owner>/<repo>/issues/comments/<comment_id> -f body='...'  # conversation comment
gh api -X PATCH repos/<owner>/<repo>/pulls/comments/<comment_id>  -f body='...'  # inline review comment
```

Resolving is externally visible and follows the consent rule in `SKILL.md`. Leave question threads open unless Diego explicitly asks to close them. For the canonical `**Addressed.** Done in <sha>:` reply shape, resolution reasons and GraphQL commands, see [github-writing.md](github-writing.md). For Copilot's auto-resolution and agentic review loops, see [copilot-review-loop.md](copilot-review-loop.md).
