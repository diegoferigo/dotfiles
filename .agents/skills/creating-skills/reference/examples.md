# Examples, patterns & anti-patterns

## Contents

- [Writing descriptions](#writing-descriptions)
- [Naming conventions](#naming-conventions)
- [Progressive-disclosure patterns](#progressive-disclosure-patterns)
- [Degrees of freedom (examples)](#degrees-of-freedom-examples)
- [Workflows & feedback loops](#workflows--feedback-loops)
- [Common anti-patterns](#common-anti-patterns)

## Writing descriptions

The description must say **what** the skill does and **when** to use it, in the **third person**, with concrete trigger terms. It is injected into the system prompt for skill selection, so first/second person hurts discovery.

Good:

```yaml
description: Extract text and tables from PDFs, fill forms, merge documents. Use when working with PDF files or when the user mentions PDFs, forms, or document extraction.
description: Analyze Excel spreadsheets, create pivot tables, generate charts. Use when analyzing spreadsheets, tabular data, or .xlsx files.
description: Generate commit messages from git diffs. Use when the user asks for help writing commit messages or reviewing staged changes.
```

Bad:

```yaml
description: Helps with documents            # vague, no triggers, no scope
description: I can help you process Excel     # first person; breaks discovery
description: Does stuff with files            # meaningless
```

Tips: include specific nouns/file types users say; scope explicitly when it matters ("Only for monorepos", "TypeScript projects"); avoid overlap with other skills' descriptions.

**Fight under-triggering: be a little "pushy."** Agents often skip a skill that would help. Name the triggers broadly and tell the agent to use it even when the user doesn't request it by name:

```yaml
description: Build internal metrics dashboards from company data. Use whenever the user mentions dashboards, data visualization, internal metrics, or wants to display company data, even if they don't explicitly say "dashboard".
```

Note: trivial one-step asks ("read this PDF") may not trigger a skill even with a perfect description, because the agent just does them. Skills fire most reliably on complex/multi-step/specialised tasks: write descriptions accordingly.

## Naming conventions

- Lowercase, numbers, hyphens only; ≤64 chars; no leading/trailing hyphen, no consecutive `--`; matches the directory. Avoid `claude`/`anthropic` in the name **on Claude** (not part of the open spec / not enforced by Copilot).
- Prefer **gerund form**: `processing-pdfs`, `analyzing-spreadsheets`, `testing-code`. Noun phrases are acceptable (`pdf-processing`), and a literal tool keyword is fine when it aids discovery (`gh-stack`).
- Avoid vague/generic names: `helper`, `utils`, `tools`, `data`, `files`.

## Progressive-disclosure patterns

**Pattern 1: overview + references.** SKILL.md has a quick start; advanced topics link out. These paths are illustrative examples inside the fenced block:

```markdown
## Advanced
**Forms**: see [reference/forms.md](reference/forms.md)
**API**: see [reference/api.md](reference/api.md)
```

**Pattern 2: domain split.** One reference file per domain so only relevant context loads (e.g. `reference/finance.md`, `reference/sales.md`); SKILL.md navigates between them and can suggest `grep` to find specifics.

**Pattern 3: conditional detail.** Show the basic path inline; link niche paths (e.g. tracked-changes, low-level format) that are read only when needed.

**Rules.** Keep references **one level deep** from SKILL.md (nested links get partially read). Add a **table of contents** to any file over ~100 lines so a partial read still reveals its scope. Put executable scripts in `scripts/` and reference them by path (they're run, not loaded into context).

## Degrees of freedom (examples)

High freedom (prose, many valid approaches):

```markdown
## Code review
1. Analyze structure. 2. Check bugs/edge cases. 3. Suggest readability fixes.
4. Verify project conventions.
```

Low freedom (exact command, fragile, must be consistent):

```markdown
## Migration
Run exactly: `python scripts/migrate.py --verify --backup`
Do not modify the command or add flags.
```

## Workflows & feedback loops

For fragile multi-step tasks, give the agent structure it can follow and self-check.

**Copy-in checklist**: the agent copies this into its reply and ticks items off, so it can't silently skip a step:

```markdown
Copy this checklist and update it as you go:
- [ ] Ran the test suite and it passes
- [ ] Regenerated the lockfile
- [ ] Verified the diff touches only the intended files
```

**Validate → fix → repeat**: only proceed once a check passes:

```markdown
1. Make the change.
2. Run `scripts/validate.py` (or check against the style guide).
3. If it reports errors, fix them and go to 2. Do not continue until it passes.
```

**Plan → validate → execute** for risky batch/destructive work: have the agent write a plan (e.g. `changes.json`), validate it with a script, then execute, so errors surface before anything is changed.

## Common anti-patterns

- **Verbose body** that re-explains what the model already knows (file formats, how libraries work). Delete it.
- **Catch-all skill** covering many unrelated tasks: split into focused skills.
- **Vague/first-person description**: fails to trigger.
- **Deeply nested references**: flatten to one level from SKILL.md.
- **Blindly pre-approving `shell`/`bash`** in `allowed-tools`: a security risk; omit unless fully trusted.
- **No validation**: always `/skills reload` + `/skills info <name>` and dry-run a triggering prompt before relying on it.
- **Writing for imagined problems**: anticipating gaps that never occur. Baseline the task without the skill first, then write only what fixes the real failures.
- **Time-sensitive content**: "before/after August 2025" rots immediately; put superseded guidance in a collapsed "Legacy/Old patterns" section.
- **ALL-CAPS MUST/NEVER everywhere**: brittle; explain the reasoning so the agent generalises. Reserve hard commands for genuinely fragile steps.
- **Too many options with no default**: the agent can't choose. Give one default and a single escape hatch.
- **Windows backslash paths**: fail on Unix/macOS; always use forward slashes.
- **Inconsistent terminology**: mixing synonyms ("field"/"box"/"element") for one concept hurts parsing. Pick one term and keep it.
