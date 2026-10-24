# Rendering findings

Reviewers return findings in the format of [../reviewers/findings.md](../reviewers/findings.md). The orchestrator arbitrates them by evidence (see [review-rubric.md](review-rubric.md)) and then renders the surviving ones for one of two targets. The reviewers never write for a target, so the same role prompts serve both.

## Target A: local report before a push

The review is of the user's own change, so the output feeds the arbitration loop and not a person.

- Order by severity, then confidence. Lead with the blocking findings.
- End with exactly one verdict from Verdicts in [review-rubric.md](review-rubric.md). The verdict comes from the arbitrated findings, not from any single reviewer.

## Target B: review of someone else's pull request

The review is published to GitHub in the voice of the user. Before publishing, load the skill that owns that voice (`acting-as-diegoferigo` for Diego, see `reference/reviewing.md`) and follow it. It decides the tone, the labels, the anchoring and the submission call. This file only maps findings to it.

1. **Run the reviewers read-only** on the pull request diff (`gh pr diff <n>`, or a checkout when a check needs to run code). They do not edit, comment or push.
2. **Drop what the toolchain owns**: formatting, import order and anything a linter, type checker or CI job already enforces. Drop findings about code the pull request did not change.
3. **Re-verify every kept finding** against the code before it is published. A finding that is `unverified` or `needs-evidence` becomes a question to the author, not an assertion.
4. **Cap the list.** A few high-conviction comments beat a long one. Keep the structural findings and fold or drop the cosmetic ones.
5. **Map to the label ladder of the voice skill.** The `Question` row wins over the severity rows, so an `unverified` `CRITICAL` finding is a `**Question:**` until it is verified.

| finding                                                | label             |
| ------------------------------------------------------ | ----------------- |
| `CRITICAL` (data loss, real bug, security hole)        | `**Critical:**`   |
| `HIGH` or `MEDIUM`, a change the author should make    | no prefix         |
| `LOW`, concrete non-blocking improvement               | `**Suggestion:**` |
| `LOW`, trivial and optional, naming or wording         | `**Nit:**`        |
| `category: question`, or `verification` not `verified` | `**Question:**`   |
| context only                                           | `**FYI:**`        |

A repository with its own severity labels wins over this table.

6. **Write each finding as one inline comment** on its `anchor`, compact, as a question or proposal. Put the evidence in the comment only as far as the author needs it to act.
7. **Choose the verdict for the review body** with Severity gating in [review-rubric.md](review-rubric.md), separately from the findings. A verdict from only `LOW` findings is stated as non-blocking. The body holds the verdict and one or two sentences, never a finding.
8. **Do not submit** until the user asks. Show the review first.

Findings that the user rejects when shown are dropped, not argued.
