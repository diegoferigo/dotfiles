# Findings format

Every reviewer role in this directory returns its result as a list of findings in this format, so that the orchestrator can arbitrate them and a renderer can turn them into a report or a GitHub review. The format is plain text and does not depend on a harness.

## Fields

One finding is a block of `key: value` lines. `id`, `reviewer`, `severity`, `anchor`, `claim` and `evidence` are required. The other fields are optional, so a reviewer that cannot fill them still returns a usable finding.

| field              | content                                                                                                                                                                                                                                                                                                                      |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`               | Short stable id, for example `S1`. Unique within one review.                                                                                                                                                                                                                                                                 |
| `reviewer`         | The role that raised it, named like its file in this directory (`correctness` for `correctness.md`).                                                                                                                                                                                                                         |
| `severity`         | `CRITICAL`, `HIGH`, `MEDIUM` or `LOW`, `CRITICAL` is data loss, a crash or a security hole, `HIGH` a wrong result on a normal path, `MEDIUM` a wrong result on an edge path or needless state, `LOW` naming and wording. Blocking follows Severity gating in [../reference/review-rubric.md](../reference/review-rubric.md). |
| `confidence`       | Optional, `high`, `medium` or `low`, `medium` when absent. Rank by severity, then by confidence.                                                                                                                                                                                                                             |
| `category`         | Optional, one of `bug`, `design`, `simplification`, `naming`, `docs`, `test`, `security`, `performance`, `question`.                                                                                                                                                                                                         |
| `anchor`           | `path:line` or `path:start-end` of the code the finding is about. Use the nearest changed line when the real target is outside the diff. For a plan or design with no code, a section title or a quoted sentence of it.                                                                                                      |
| `symbol`           | Optional. The real symbol or line when it differs from the anchor.                                                                                                                                                                                                                                                           |
| `claim`            | One or two sentences: what is wrong and what breaks.                                                                                                                                                                                                                                                                         |
| `evidence`         | Quoted code, the exact command and its output, or the source of the pinned dependency. A bare assertion is not evidence.                                                                                                                                                                                                     |
| `suggested_action` | Optional. The concrete change.                                                                                                                                                                                                                                                                                               |
| `verification`     | Optional. `verified` when the evidence was reproduced, `unverified` when it was not, `needs-evidence` when a check is still missing. Absent means `unverified`.                                                                                                                                                              |

Example:

```text
id: C1
reviewer: correctness
severity: HIGH
confidence: high
category: bug
anchor: src/pool.py:88
claim: The lock is released before the counter is updated, so two workers can both pass the limit check.
evidence: src/pool.py:84-90, and `pytest tests/test_pool.py::test_limit -n 4` fails 3 runs out of 10 with `assert 5 <= 4`.
suggested_action: Update the counter inside the `with self._lock:` block.
verification: verified
```

## Rules

- Verify before reporting whenever a check is cheap: read the callers, grep the uses, run the test or the benchmark. A qualitative claim (design, naming, coupling) cites the code. A quantitative claim (speed, memory, failure rate) cites the command and its result.
- Write each finding so it stands alone, because a human or an agent may act on it in isolation: a `path:line` anchor plus a short quoted snippet (line numbers drift), the concrete change, and no references such as "same as above".
- A finding without evidence is not reported. If a check could not be run, say so with `verification: unverified` instead of dropping the evidence field.
- Do not put a verdict in a finding. The verdict (approve, request changes) belongs to the whole review and is emitted by the orchestrator or the renderer.
- Do not put lifecycle state (accepted, rejected, deferred) in a finding. The orchestrator records it in the ledger.
- A role that returns no finding says so in one line. Silence is a valid result.
- Whether a finding blocks follows Severity gating in [../reference/review-rubric.md](../reference/review-rubric.md), so a finding does not state it.
- A reviewer that reports on a diff it did not write does not rewrite or edit anything. It only returns findings.
