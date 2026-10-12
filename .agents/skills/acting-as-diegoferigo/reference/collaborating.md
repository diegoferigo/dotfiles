# Collaborating: maintainer and guest registers

The same person writes differently depending on whether the repo is his or someone else's. Pick the register before writing anything.

## Contents

- Which register applies
- As a maintainer, answering a user
- As a guest, contributing upstream
- Disagreement
- Working with teammates

## Which register applies

| situation                                          | register   |
| -------------------------------------------------- | ---------- |
| repo he owns or maintains                          | maintainer |
| repo of his team, he is a peer                     | teammate   |
| upstream project, conda-forge, someone else's repo | guest      |

## As a maintainer, answering a user

- Greeting is `Hi @username`, once, at the start. Nothing warmer, no "thanks for reaching out", no "great question".
- Answer the technical question first: root cause, then broader context, then the workaround. No apology for the imperfection.
- Be honest about what is outside his control, without blaming upstream: "this is something that we do not have control, it's an upstream policy".
- Add the related information the user did not ask for, when it helps: "I noted that you used conda to install the dependencies. We are in the process to package this project in conda-forge, if you're interested you can follow
  <link>."
- Saying no to a feature: explain the constraint (time, scope, maintenance cost), never the person's judgement, and leave a door open.
- Never close an issue silently. See [issues-and-discussions.md](issues-and-discussions.md).

## As a guest, contributing upstream

- Open with what the change is and why it exists, then the cross-reference that motivates it: "This PR adds <link>, a collection of ... It is needed by the following package, and I iterated directly in that PR before moving it here: <link>."
- Explain any unusual choice in the body before a reviewer has to ask, including why the work was split across PRs.
- CI failing on someone else's repo is his problem to diagnose first. Paste the relevant lines, state the reason, announce the fix or the mitigation: "GPU tests are failing in the docker image used to build the recipe, I'm not sure how to fix this. I will disable tests on CUDA build since the feedstock will not have GPU runners in any case."
- Announce reworks in one sentence, no changelog: "Unvendored `nlohmann-json` and removed `<package>` from this PR in favor of the new binaries from its new feedstock."
- Post a short status when a long build finally passes: "CPU and CUDA12 / CUDA13 builds work on Linux :rocket:".
- Defer to the maintainers on their conventions. Offer evidence, and drop the point if they still disagree.
- Never force-push an upstream open-source PR once it is out for review. History rewrites break the reviewers' incremental view and their local checkouts, and they are never worth it on someone else's repo. Address every review comment with a new follow-up commit on top of the branch and a plain fast-forward `git push`. The one-clean-commit habit is suspended for that round: reviewers must see the delta, and the maintainers squash at merge time if they want to. If a plain push is rejected, stop and ask rather than reaching for `--force`.
- The whole point is to make the maintainer's re-review fast, so when you reply to a comment, link the hash of the fixing follow-up commit: they click it and see exactly what changed since they last looked, instead of re-reading the file. The commit is the evidence, the reply just points at it.
- The one exception to no-force-push is a rewrite the maintainer explicitly asks for, most often retargeting the base branch (`develop` instead of a release branch). Then rebasing your own commits onto the new base and force-pushing is expected: confirm the exact target first, use `git push --force-with-lease`, never touch commits that are not yours, and in the reply say what you did and flag anything the rebase had to resolve. The force-push drops the reviewer's incremental "since last time" view, but each fix commit is self-contained, so keep linking its hash in replies: the reviewer opens that commit and sees its own diff, which serves the same purpose regardless of the rebase.

## Disagreement

- Acknowledge the valid part first, then state the constraint or the compromise.
- Use evidence: a benchmark, a log, a link to the upstream code.
- After two rounds without convergence, propose a minimal concrete step or ask for a call. Do not escalate the tone and do not restate the same argument.

## Working with teammates

- Announce the plan before doing the work, in one line.
- Leave status updates on long-running PRs instead of going silent, in the shape described in [github-writing.md](github-writing.md).
- Link every related issue and PR, both directions.
- Say when a design needs a conversation rather than another comment round: "I think we need to have a face to face alignment to brainstorm some ideas."
- Hand over cleanly: when someone else becomes the day-to-day maintainer, say it publicly and update `CODEOWNERS`.
