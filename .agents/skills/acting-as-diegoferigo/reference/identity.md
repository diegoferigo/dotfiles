# Identity: accounts, git identity and branch names

This file holds the workplace-dependent identity details. Pick the register from the repo owner, not from whichever account happens to be active.

|              | work                                       | personal                                                 |
| ------------ | ------------------------------------------ | -------------------------------------------------------- |
| GitHub login | `diegoferigo-rai`                          | `diegoferigo`                                            |
| Repos        | the employer's org repos                   | his own projects and open-source repos he contributes to |
| Git identity | work address (the employer's email domain) | personal address                                         |

## Branch names

Use `diegoferigo/<topic>`, or `diegoferigo/<stack>/<layer>` for stacked work. In his personal repos, an older pattern is a descriptive `snake_case` phrase, optionally prefixed with `fix/`.

## Switching GitHub accounts

Both accounts are configured in `gh`:

```bash
gh auth status                    # see which one is active
gh auth switch -u diegoferigo     # personal
gh auth switch -u diegoferigo-rai # work
```

Prefer a per-command token over switching: it leaves the active account untouched, so nothing has to be restored.

```bash
GH_TOKEN=$(gh auth token -u diegoferigo) gh pr create --draft ...      # personal
GH_TOKEN=$(gh auth token -u diegoferigo-rai) gh pr create --draft ...  # work
```

In an interactive shell `ghp <args>` does the personal one, but it is a bash function from `~/.bashrc.d/functions`, so it does not exist in an agent's non-interactive shell: write the `GH_TOKEN=` form there. Check the permission on the target repo first (`gh repo view --json viewerPermission`): the active account often has only READ on his personal repos.

Search and API calls against a repo the active account cannot see fail with a confusing `422 Validation Failed`, so switch first. Always restore the account that was active before you started.

Some repos have been transferred and redirect (an old `owner/name` now lives at a new `owner/name`); the search API does not follow redirects, so use the current name in `repo:` qualifiers.

### `git` picks the account from the remote, `gh` from the active account

git selects the GitHub account and the commit email per repo, independently of `gh auth switch`. `~/.config/git/config` defaults to the personal account and email. A repo with any remote under a work organization listed in the `includeIf` blocks of `~/.config/git/config` includes `~/.config/git/work.gitconfig`, which switches to the work email and the `diegoferigo-rai` token. Both credential helpers call `gh auth token -u <user>`, so a plain `git push` works in either kind of repo without switching.

- This needs git >= 2.36 (`includeIf hasconfig`). Use `~/.pixi/bin/git`: the system `/usr/bin/git` 2.34 ignores the include, so every repo gets the personal identity and pushes to work repos fail.
- Outside a repo (`git ls-remote <url>`, a fresh `git init` with no remote) the personal identity applies. Add the remote before committing in a work repo.
- `gh` commands still use the active account. Check it with `gh api user -q .login` and switch as above.
- Do not set `user.email` or credential helpers in `~/.gitconfig`: git reads it last, so it would override the per-org selection.

Verify the identity of a repo before committing or pushing:

```bash
git config --show-origin user.email
printf 'protocol=https\nhost=github.com\npath=x\n\n' | git credential fill | grep ^username
```
