# Precedence

This skill fills gaps, it does not override a repo. Resolve conflicts in this
order, highest first:

1. **Explicit instruction from Diego** in the current conversation.
2. **Machine-checked rules**: formatters, linters, type checkers, pre-commit,
   compiler flags, CI. Not negotiable.
3. **Documented repo conventions**: `CONTRIBUTING.md`, the repo
   `.github/copilot-instructions.md`, PR and issue templates, `CODEOWNERS`.
4. **Local conventions of the surrounding code**: the file, then the module, then
   the package. Match what is already there even if he writes it differently
   elsewhere.
5. **Global personal instructions** (`~/.copilot/copilot-instructions.md`): the
   always-on personal defaults (language, voice, honesty, ask-vs-guess, consent).
   They lose to anything repo-specific above.
6. **This skill** (the GitHub persona), which extends those global defaults.

Consequences:

- A repo PR template always beats his personal PR layout. With no template, use
  the personal layout.
- Repo commit conventions (Conventional Commits, ticket prefixes, required
  trailers) beat his plain imperative subject.
- Tone in reviews and replies is almost always gap territory, so the personal
  voice applies there.
- Do not edit project config to make the project match his style, and do not
  reformat a file to a personal preference.
- If the project rule is only implicit (a habit visible in the code, not written
  down), follow the project and tell Diego you deviated from his usual style.

This order applies to every rule in this skill and in its reference files, so
none of them repeats the caveat.
