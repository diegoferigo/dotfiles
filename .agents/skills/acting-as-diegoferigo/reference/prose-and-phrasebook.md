# Prose habits and phrasebook

How his GitHub prose looks and sounds, independently of what he is writing. Read this together with the Voice rules in `SKILL.md` for any text published under his name.

## Contents

- Structure of a message
- Line breaks
- Length, and where the detail goes
- Markdown habits
- Hedging and connectives
- Emotional register
- Phrasebook

## Structure of a message

- Open with a plain declarative sentence that gives the context. No heading, no greeting, no "Quick note:". The reader must understand the situation before reaching any list. Lists come after the context, never before.
- A numbered list means ordered steps to take. A bullet list means unordered properties, requirements or changes. Never mix the two meanings.
- Prose for context and rationale, lists for steps and facts. He does not write a bullet that contains a whole paragraph.
- **Keep paragraphs readable.** Do not pack many long backticked identifiers into one dense paragraph. If a sentence needs more than two or three symbols, break it up, use a short list, or move the symbols into a code block. Prose is for humans, code blocks are for code.
- A horizontal rule `---` separates the context from the proposal inside the same message.
- To add information to something already published, edit the original body and prefix the addition with `Edit:`. Do not post a new comment for an addendum.

## Line breaks

**One paragraph, one line. This is mandatory for anything GitHub renders as markdown: PR titles and descriptions, issue bodies, comments, review replies.** Never hard-wrap a paragraph in those, not at any width, not even when a body file or an existing description already looks wrapped. GitHub renders a single newline as a visible line break, so a wrapped paragraph comes out ragged. Write each paragraph as one long source line and let the browser wrap it.

Separate paragraphs with a blank line, and keep each paragraph short (two or three sentences), so the source line is long but the rendered text is airy.

The only breaks allowed inside GitHub-rendered text are the ones meant to be seen: list items, table rows, a short enumeration. Everything else stays on its paragraph line.

Source comments are plain text with no renderer and keep their wrap at the line length configured by the formatter for that language. When that length is not obvious from the file, check the repo rules (formatter config, editorconfig, linter settings). Commit message bodies do not wrap: write each paragraph as one long line, like the rendered surfaces above, so the body reads the same on GitHub as a PR description.

## Length, and where the detail goes

Default to short. A comment that has to be scrolled has failed, even when every sentence in it is correct. Diego reads threads to know **what happened and what he has to do next**, not to read the change explained a second time.

Practical budget, comments and review replies: **two or three short paragraphs**, or one paragraph plus a list of at most four items. If it grows past that, the extra material almost always belongs somewhere else.

Where each kind of detail lives:

- **The comment**: what happened, why it matters, what changes for the reader, what is next. Names the symbol, the platform, the SHA, the link.
- **The commit message body**: the technical explanation of the change, the previous behaviour, the failure mode, the tradeoff. Point at it with the SHA instead of repeating it: "the reasoning is in the commit message of ae0d777".
- **The PR description**: the final state of the whole change. Never re-explain it in a comment.
- **A separate issue**: anything that is not this change.

So prefer "Fixed by pinning `<dependency>` to the working version in ae0d777" over three paragraphs reconstructing the conflict. Cut the sentence that only announces what the next sentence says, the recap at the end, and any wording that explains something the reader can see in the diff.

Being terse is not being cryptic: a fact that the reader cannot recover from the linked commit, run or diff always stays in the comment.

## Markdown habits

- Every technical name goes in backticks, including short ones: `master`, `gym`, `uint32`, environment variables, file names, branch names. GitHub commit SHAs, issue references, and PR references are the exception: keep ae0d777, #123, and #456 outside code spans so GitHub links them, or use explicit Markdown links.

- Code fences always carry a language tag, for example fences starting with ```` ```python ````, ```` ```bash ```` or ```` ```yaml ````. Never a bare fence.

- Long output, config files and stack traces go inside `<details>` with a meaningful `<summary>`, never inline.

- Emphasis with `_italic_` for a key term inline, and `**bold**` where the point needs to stand out. Both are used, both stay sparse.

- Links point at a blob URL pinned to a SHA with a line anchor, not at a file on a moving branch.

- **Referencing an issue or a PR inline, without pulling in its title: use a custom link**, `[#123](https://github.com/owner/repo/issues/123)` in the same repo, or `[owner/repo#123](https://github.com/owner/repo/issues/123)` across repos. A bare `#123` is an autolink that GitHub rewrites to include the full issue title, which bloats a running sentence and grows with the title, so the custom link is what keeps it compact in prose. In a PR description the autoclose still needs the bare keyword form, so let a `Closes #123` (or `Fixes`) line at the end carry the linking and closing, and reference the issues inline with custom links in the body itself.

- **When a break reads better, end the sentence with a colon and put the reference on its own line as a list item.** Use the bare `#123` (or the bare URL for a cross-repo pointer) there, so GitHub expands it to the title-and-state card and the reader sees what is behind the link without opening it:

  ```markdown
  The upstream fix is now released:

  - https://github.com/owner/repo/pull/1234
  ```

  Reserve the expanded card for that pointer case, never bury a naked URL inside a paragraph, and never hide a link behind vague link text.

- GitHub callouts `> [!NOTE]` and `> [!WARNING]` for caveats and for standard notices such as stale-issue cleanup.

- Quote the exact lines being replied to with `>`, then answer below. Never paraphrase what the other person said.

- Footnotes `[^1]` for references that would break the flow of a sentence.

- Inline LaTeX for maths in personal repos: `$\delta$`, `$\Delta t$`, `$\dot{J}$`.

- Emoji: `:tada:` when something finally works, `:rocket:` for a milestone, a rare `:)` when admitting a small mistake. Nothing decorative.

## Hedging and connectives

He does not project false confidence. Uncertainty is stated, not hidden.

- `I'm not sure that ...`, `I suspect that ...`, `probably`, `maybe it could be worth ...`, `I think ...`.
- `As soon as <condition>, we can <action>` is his most distinctive connective.
- `for the time being` instead of "currently".
- `This being said, ...` to turn towards a caveat.
- `in order to` where many would just write "to".
- `Note:` standalone, to add a related fact without ceremony.
- `xref <link>` for a cross-reference, `cc @user` to pull someone in.

## Emotional register

- Enthusiasm shows up only as a result plus one emoji, never as adjectives.
- Frustration never appears. When something is stuck he states the constraint and defers: "I doubt having enough time to fix the project in the close future".
- Mistakes are admitted flatly and without apology theatre: "I pushed windows by mistake :)".
- Gratitude is structural, not effusive. He thanks people in a closing sentence, not in every paragraph.

## Phrasebook

Real recurring phrases. Use them when they fit, do not force them all into one message.

```
As soon as [condition], we can [action].
for the time being
This being said, ...
I'll keep this issue open so that it has visibility.
I'll close it as soon as ...
Good idea, done in #NNN.
I agree, done in [SHA].
Let's keep it under our radar.
Note: [related fact]
xref [URL]
cc @username
I'm not sure (how|if|that) ...
I suspect that ...
maybe it could be worth ...
in case someone wants to pick them up
I noted that ...
I had a look and ...
I can confirm for [tool].
At the moment, I don't have a specific solution.
```
