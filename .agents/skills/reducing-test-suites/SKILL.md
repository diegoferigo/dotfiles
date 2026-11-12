---
name: reducing-test-suites
description: >-
  Reduces an existing test suite to its high-signal core by measuring per-test
  coverage, inventorying assertions, folding redundant tests and shrinking the
  lines a reader must read, with an explicit loss budget agreed with the owner.
  Focused on Python and pytest, with principles that carry over to C++ and other
  languages. Use whenever a test suite feels too big, tests keep growing, or the
  user asks to cut, slim, simplify, deduplicate, consolidate or prune tests, or
  to review whether tests are high-signal, even if this skill is not named.
---

# Reducing test suites

This skill shrinks a suite that already exists. For non-test code, use the `simplifier-reviewer` agent if available.

## Process

1. **Fix the goal and the baseline.** The owner picks the goal: lines a reader must read (the usual one), runtime or flakiness. The number of tests is a poor goal: folding can lower the count and raise the lines. Lines a reader must read: non-blank, non-comment lines of test files, fixtures, `conftest` files and test data files, counted with one stated rule kept for the whole job. Record the baseline twice (lines, tests, line and branch coverage) to detect nondeterminism; note failing and skipped tests, markers, and what CI actually runs. Propose a coverage loss budget with the baseline, then **stop until the owner confirms it**. Do not invent one.
2. **Measure, do not eyeball.** Collect coverage with one context per test, branches on, subprocesses and entry points included. Derive tests with no unique lines or branches, groups with identical coverage, and a minimal covering set. These are candidates for review, not cuts. Mechanics and limits: [reference/measuring.md](reference/measuring.md).
3. **Coverage is not behavior.** A test redundant for coverage may hold the only assertion on an output. Inventory the assertions of each candidate and fold them into a kept test or a table. Delete outright only tests that restate the code or check a cosmetic detail.
4. **Write a verdict per test:** KEEP, FOLD (into which test) or DROP (why). Never touch a test without a written reason: a verdict without a reason cannot be vetoed. Before a DROP, check history and references (blame, log, bug or issue references, regression-test names). KEEP by default: regression tests tied to a bug, concurrency, stale or changed files, formats written to disk, byte-for-byte equality between entry points, hand-written contract tests (a contract derived from the code is tautological). Handle candidates one at a time and re-derive after each fold: two tests can cover each other, and dropping both loses coverage. Fault check: break the code (mutation testing or manual fault injection) and confirm a kept test still fails. It is required for every FOLD and for a DROP justified as "restates the code" or "cosmetic"; optional otherwise.
5. **Get sign-off before the cut.** Hand the owner the verdict list, ranked by importance, with the expected coverage kept versus the baseline. Spend the budget from the bottom: wording variants of errors, defensive or unreachable branches, cosmetic output. Cut only after the owner signs off.
6. **Then attack the lines.** Shared builders and fixtures for setup, table-driven tests, one whole-structure assertion instead of many substring checks, data literals built by helpers, no comments that restate the code. Do not golf: a failing test's message must still name the cause. The owner judges readability.
7. **Verify premises before applying.** Analyses go stale between rounds: re-check that the test still exists, that the helper is not already there, that two entry points really share a mechanism. Analysis is read-only and separate from the cut.
8. **Guard each round.** Re-measure coverage with the same recipe as the baseline, and re-run the assertion inventory on the folded tests. If coverage falls below the budget or an assertion is lost, revert the round, tell the owner, and go back to the verdict step for the affected verdicts only. Do not loop on the same cut. One commit per group of changes. Do not amend after review, so reviewers can see what changed since they last looked.
9. **Stop at the core.** Core: the behaviors on the KEEP list plus those the owner names as essential. If the owner has no written list, ask the owner to name them. Stop when only core remains. If the goal is not met, report how far it is instead of cutting core tests. Final report: lines and coverage before and after.

**Light path:** for a small manual cleanup of a few obvious tests, skip the measurement and the guard: inventory the assertions, write the verdicts, keep the fault check for FOLDs and for "restates the code" or "cosmetic" DROPs, attack the lines. Show the verdict list to the owner before the cut, as in the sign-off step.

## Keeping it small

Tests grow back. Follow the Tests section of coding-style.md (acting-as-diegoferigo skill, `~/.agents/skills/acting-as-diegoferigo/reference/coding-style.md`) and repeat a measured pass periodically.
