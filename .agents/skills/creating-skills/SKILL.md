---
name: creating-skills
description: >-
  Author, structure, improve, and validate reusable agent skills (SKILL.md) for
  Copilot CLI, Claude, and the open Agent Skills spec. Use whenever the user
  asks to create, edit, split, review, package, or validate a skill; mentions
  SKILL.md, skill frontmatter, descriptions, reference files, or agentskills.io;
  or wants repeatable instructions captured as a skill.
---

# Creating agent skills

A skill is a folder with a `SKILL.md` file (plus optional reference files and scripts) that an agent loads **only when relevant** to a task. Only each skill's `name` + `description` are pre-loaded; the body is read when the skill triggers. On **Claude**, reference files are then read on demand (lazy); on **Copilot CLI**, invoking a skill makes *all* its files available at once, so keep the whole skill lean, not just SKILL.md. Optimise for discovery and low token cost.

Agent Skills is an **open cross-platform standard** ([agentskills.io](https://agentskills.io/specification)); Copilot CLI and Claude are two implementations, so the canonical field rules below come from that spec.

Docs: Open spec <https://agentskills.io/specification> · Copilot CLI <https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills> · Claude <https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices>

## Contents

- [Location & layout](#location--layout)
- [Frontmatter (YAML)](#frontmatter-yaml)
- [Authoring workflow](#authoring-workflow)
- [Core principles](#core-principles)
- [Command shorthands](#command-shorthands)
- [Security](#security)
- [Validate](#validate)
- [Reference files](#reference-files)

## Location & layout

- **Personal** (all repos): `~/.copilot/skills/<name>/` (or `~/.agents/skills/`).
- **Project** (one repo): `.github/skills/<name>/` (or `.claude/skills/`, `.agents/skills/`). Copilot code review also reads `.github/skills/*/SKILL.md`, from the PR head branch, so a skill there can be tested in the PR that changes it ([changelog](https://github.blog/changelog/2026-07-17-copilot-code-review-customization-and-configurability-improvements/), [GA](https://github.blog/changelog/2026-07-29-copilot-code-review-agent-skills-and-mcp-now-generally-available/)). MCP tool calls made during review are read-only.
- One directory per skill; dir name = `name`, lowercase with hyphens.

```text
<name>/
├── SKILL.md              # required: metadata + overview (loaded when triggered)
├── reference/*.md        # optional: details (on Claude, loaded on demand)
├── scripts/…             # optional: EXECUTED (code never enters context, only output does)
├── assets/…              # optional: templates/images/data to copy or use
└── …                     # any other files
```

Conventional dir names from the spec are `references/`, `scripts/`, `assets/`; this skill uses `reference/`, which is also fine. Bundled files cost zero tokens until accessed on Claude, and Copilot CLI exposes them only after invocation, so scripts are cheaper than regenerated inline code. Say explicitly whether the agent should **run** a script or **read** it as reference.

## Frontmatter (YAML)

```yaml
---
name: processing-pdfs            # required; ≤64; lowercase/numbers/hyphens; must match dir
description: >-                   # required; ≤1024 chars; third person; what it does + when to use it
  Extract text and tables from PDFs and fill forms. Use when the user mentions
  PDFs, forms, or document extraction.
license: MIT                     # optional
compatibility: Requires Python 3.14+ and uv   # optional; ≤500; only if the skill has env requirements
metadata:                        # optional; arbitrary string→string map (author, version…)
  author: example-org
  version: "1.0"
allowed-tools: Bash(git:*) Read  # optional; pre-approves tools, see Security
disable-model-invocation: true   # optional; Copilot CLI: the agent never auto-invokes it, default false
---
```

- `name`: on Claude it additionally must not contain `claude`/`anthropic` (not enforced by Copilot). Prefer gerund form (`processing-pdfs`).
- `name` in Copilot CLI may also contain colons for namespaced skills (for example `my-plugin:search`); other clients may reject them. `user-invocable: false` hides a skill from `/SKILL-NAME` ([CLI reference](https://docs.github.com/en/copilot/reference/cli-command-reference)).
- `description`: the only thing pre-loaded for discovery, so spend effort here.
- `allowed-tools`: space-separated. Copilot accepts coarse `shell`/`bash`; the open spec/Claude also accept fine-grained patterns like `Bash(git:*) Bash(jq:*) Read`. **Experimental**: support varies by agent. `compatibility` and `metadata` are optional spec fields; most skills don't need `compatibility`.

## Authoring workflow

Evaluation-driven: find real gaps first, then write the minimum that closes them.

```
- [ ] 0. Baseline: run the agent on 2–3 representative tasks WITHOUT the skill;
         note the concrete failures. Write the skill to fix exactly those.
- [ ] 1. Scope: one focused task per skill (not a catch-all).
- [ ] 2. Name in gerund form where natural (processing-pdfs); matches the dir.
- [ ] 3. Write the description: what it does + explicit trigger phrases, third person.
- [ ] 4. Draft the body: concise overview + steps; assume the agent is smart.
- [ ] 5. Move deep/situational detail into reference/*.md, linked one level deep.
- [ ] 6. Add scripts/templates for fragile or repeated steps; reference them by path.
- [ ] 7. Validate: /skills reload → /skills info <name>; dry-run a triggering prompt.
- [ ] 8. Iterate: re-run the representative prompts; refine only where it still fails.
```

Optional two-agent loop: one agent (skill NOT loaded) helps you design/refine; a fresh agent (skill loaded) reveals gaps by actually using it.

## Core principles

- **Concise is key.** Every token in a loaded SKILL.md competes with the rest of context. Only add what the agent doesn't already know; cut background it can infer. Keep the body **under ~500 lines**: split before it grows past that.
- **The description drives discovery.** It's how the agent picks this skill among many. State *what it does* **and** *when to use it*, in the **third person**, with concrete trigger terms. Agents tend to **under-trigger**: make the description a little "pushy" (e.g. "use this whenever the user mentions X, Y, or Z, even if they don't say '<skill>'"). See `reference/examples.md` for good vs. bad.
- **Progressive disclosure.** SKILL.md is a table of contents: keep an overview in it and link detailed material in `reference/*.md`. Keep references **one level deep** (linked directly from SKILL.md) and add a short **table of contents to any file over ~100 lines**.
- **Match freedom to fragility.** Use high freedom (prose direction) when many approaches work; medium freedom (pseudocode or parameterised commands) for a preferred pattern; low freedom (exact command, "do not modify") when a sequence is fragile or must be consistent.
- **Explain WHY, don't just command.** Reserve ALL-CAPS MUST/NEVER for genuinely fragile steps; over-using them is a yellow flag: give the reasoning instead, so the agent generalises correctly.
- **Don't over-offer options.** Pick a sensible default and give one escape hatch ("use X; for <case> use Y"), not a menu of five tools.
- **Timeless & consistent.** Avoid time-sensitive phrasing ("before August 2025…"). Put superseded guidance in a collapsed "Legacy/Old patterns" section instead. Use one term per concept throughout; synonym drift hurts the agent's parsing.
- **Write for the agent, not a human.** Be declarative, step-oriented, and unambiguous; give preconditions/postconditions and 1–3 example prompts. Use forward-slash paths even on Windows.
- **For fragile multi-step tasks, add a feedback loop.** A copy-in `- [ ]` checklist the agent ticks off, and/or a validate → fix → repeat gate that only proceeds once a check passes (see `reference/examples.md`).
- **Test with the models you'll use.** What Opus infers, a smaller model may need spelled out; aim for instructions that work across them.

## Command shorthands

A skill invoked as a slash command receives the text after its name as the turn input: `/<skill-name> <verb> [text]`. The verb is that input's first token, which the skill body interprets. Add shorthands only to a skill whose work is a repeatable set of actions; a purely advisory skill needs none.

Shared core, kept identical across skills so the muscle memory transfers:

- `?`: list the shorthands and primary actions this skill exposes. Invoking the skill with no verb runs `?`.
- `status`: report the skill's current workflow state. Add it only to a skill that drives a stateful, multi-step workflow; omit it from a stateless one.

Rules:

- Verb-first, lowercase, one word; free prose follows it, with no rigid syntax. Use a single modifier `!` appended to a verb, meaning "escalate".
- Do not shadow a native harness command. A shorthand earns its place only by adding discipline the harness lacks, and its expansion routes to the host CLI's native feature (plan mode, parallel subagents, reviewer agents) when one exists. Those names differ by harness (Copilot CLI, Claude, and others), so detect what the current one offers and use it rather than reimplementing it.

## Security

`allowed-tools` pre-approves tools so the agent runs them **without asking**. Only pre-approve `shell`/`bash` (or `Bash(...)` patterns) if you fully trust the skill and every script it calls: it removes the confirmation step and lets prompt injections run commands. Prefer the narrowest pattern (`Bash(git:*) Read`) over a blanket `shell`. When in doubt, omit it so the agent must confirm.

Treat installing a third-party skill like installing software: audit `SKILL.md`, every script, and any external URLs it fetches (attacker-controlled content and even images can carry injected instructions).

## Validate

First validate against the agentskills.io spec if your toolchain provides a spec validator; then check the target agent loads and discovers the skill.

```bash
# Run from a repository root that contains skills/<name>/SKILL.md.
gh skill publish --dry-run

# after creating/editing, in a CLI session:
/skills reload
/skills info <name>        # confirm it loaded and shows your description
/skills list               # see it among available skills
```

`gh skill publish --dry-run` validates every discovered skill without publishing anything. It expects a repository layout such as `skills/<name>/SKILL.md`, `skills/<scope>/<name>/SKILL.md` or `plugins/<scope>/skills/<name>/SKILL.md`. Do not run it from inside the skill directory itself: the CLI then sees the directory as `.` and can report a false `name ... does not match directory name "."` error. For an installed standalone skill, copy it into a disposable `skills/<name>/` tree, initialize that tree as a local git repository, run the dry-run from its root, then delete the disposable tree. A missing `license` is a recommendation warning, not a validation failure; no Git remote is required for a dry-run.

Then test discovery both ways: run a prompt that *should* trigger it (confirm the agent selects it) and one that should *not* (confirm it doesn't over-trigger).

To find, install, pin, update, or publish skills others can reuse, use the `gh skill` CLI (`gh skill search|install OWNER/REPO SKILL [--pin vX]|update|publish`); install records provenance (source repo, ref, SHA) in the frontmatter. Use `gh` 2.102.0 or later: it fixes `gh skill search` passing result paths to `gh skill install` without an option separator ([release notes](https://github.com/cli/cli/releases/tag/v2.102.0)).

To ship skills together with MCP servers, package them as an Agent Plugin 1.0 (VS Code, Copilot CLI and the Copilot app): `$schema` in `plugin.json`, skills under `skills/`, MCP configuration in `mcp.json`, and Copilot-only files in `com.github.copilot/` ([changelog](https://github.blog/changelog/2026-08-12-agent-plugins-1-0-in-vs-code-copilot-cli-and-the-copilot-app)).

## Reference files

- **Description patterns, examples & anti-patterns; progressive-disclosure patterns; naming**: [reference/examples.md](reference/examples.md)
- **Copy-paste starting point**: [templates/SKILL.md.template](templates/SKILL.md.template)
