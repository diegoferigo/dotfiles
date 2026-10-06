# Legacy workarounds (superseded by newer gh-stack)

Recipes here are **only for gh-stack versions older than the one noted** and are kept purely as history, the current commands handle these cases natively, so you should not need this file on an up-to-date install. Before using anything below, run `gh stack --version`; if you are on the fixed version, use the current path in the SKILL and reference files instead. The REST endpoints referenced here are undocumented and were reverse-engineered against old builds, re-verify them against the version in front of you before relying on them.

## Contents

- [Removing a merged PR from a stack (pre-v0.1.0)](#removing-a-merged-pr-from-a-stack-pre-v010)
- [Undocumented Stacks REST endpoints (v0.0.8)](#undocumented-stacks-rest-endpoints-v008)

## Removing a merged PR from a stack (pre-v0.1.0)

> **Current behavior:** in **v0.1.0** `gh stack unstack <stack#>` already handles a merged member, it unstacks the open PRs and leaves the merged one behind. The workaround below is needed **only on pre-v0.1.0**.

**Legacy (pre-v0.1.0):** `unstack` was *refused* while a merged member was present: `HTTP 422 … Pull requests #… cannot be removed from this stack` (`.../stacks/{n}/unstack`): which blocked the whole "unstack + recreate" idea:

- `gh stack unstack --local` still succeeded (drops local tracking only); the remote `unstack` was refused, leaving the stack.
- `gh stack submit` for a *fresh* stack of the still-open branches also failed, because submit first tries to reset the existing stack: `⚠ Failed to delete existing stack: HTTP 422 … cannot be removed … Run gh stack submit again to retry`, retrying loops forever.
- Direct REST `PATCH /stacks/{n}`, `DELETE /stacks/{n}/pulls/{#}` → 404.

**Legacy recovery (pre-v0.1.0):** leave the dead merged-only stack alone and mint a brand-new one via the REST create endpoint, then re-sync local tracking:

```bash
# 1. Get each open PR's base right first (chain them; bottom targets trunk).
gh pr edit <cpp#>  --base <python-branch>      # etc., set every base
# 2. Create a NEW stack directly (bypasses submit's "delete existing" step).
echo '{"pull_requests":[<bottom#>,<#>,<#>,<top#>]}' \
  | gh api --method POST repos/<o>/<r>/stacks --input -     # returns new stack #
# 3. Import it into local tracking so gh stack works again.
gh stack checkout <new#>
```

Verify membership server-side with `gh api repos/{o}/{r}/stacks/{n}` (not `gh stack view`, which reads local tracking). The old stack keeps only its merged PRs (`open:false`) and is harmless. **Caveat:** those REST endpoints are undocumented/young, re-verify against the current gh-stack version, and if a real remove/reorder-without-reset lands, prefer it (see the precedence policy in [commands.md](commands.md#installed-version-precedence)).

## Undocumented Stacks REST endpoints (v0.0.8)

*Observed in gh-stack v0.0.8 (undocumented, re-verify):* `GET /repos/{o}/{r}/stacks` (list) · `GET …/stacks/{n}` (members; `open:false` = all-merged/dead) · `GET …/stacks?pull_request={#}` (stacks a PR belongs to; `[]` = none) · `POST …/stacks` body `{"pull_requests":[…bottom→top…]}` (create) · `POST …/stacks/{n}/add` (append a PR) · `POST …/stacks/{n}/unstack` (refused if any member is merged). A PR's own `stack` field (`gh api …/pulls/{#} --jq .stack`) is `null` when it is in no live stack.
