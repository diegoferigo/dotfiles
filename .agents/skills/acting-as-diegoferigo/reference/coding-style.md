# Coding style: shared principles

Language specific rules live in [python.md](python.md) and [cpp.md](cpp.md). This file holds what applies to any language he writes.

## Contents

- Minimize complexity: simple things work
- Earn every element
- Least astonishment: predictable behavior
- Commenting philosophy
- Error handling: fail fast with actionable messages
- Reading order of a file (write for the reviewer)
- Scope of changes
- Tests

## Minimize complexity: simple things work

The default is the simplest thing that solves the actual problem. Simple code works: it is easy to read, to test, and to change, and complexity is a cost every future reader pays, so spend it only where the problem genuinely demands it.

- **Do not overengineer.** No speculative abstraction, no generality for a case that does not exist yet, no configuration knob nobody asked for. Solve the problem in front of you, not the one you imagine you might have later.
- **Do not reinvent the wheel.** Before writing your own, reach for the standard library, an existing helper already in the repo, or a well-established dependency the repo already uses. Search for what exists first; a hand-rolled version of something the language or a dependency already provides is a bug surface and a maintenance cost.
- **Prefer boring over clever.** When two solutions both work, take the one with fewer moving parts and fewer concepts a reader must hold at once. Cleverness that saves three lines but needs a paragraph to explain is not worth it.
- An abstraction has to earn its place: introduce it to remove a real duplication or to make something testable, never for its own sake (this is the same rule as Scope of changes, seen from the design side).

## Earn every element

A reviewer pays for every field, flag, parameter, check, helper, type and feature you add, so the author has to justify each one, not the reviewer to justify its removal. Before committing, walk the new symbols of the diff and apply these principles. Most over-built code fails one of them.

- **Need.** Keep an element only if something in this change uses it today and a named failure follows from deleting it. A capability nobody asked for (an option, a switch, a field that is always the same value) does not exist yet. An abstraction or file whose consumer is not in the same change is hard to review: land it with its first use.
- **Guarantees already given.** Do not re-implement or re-check what the language, the standard library, a dependency or the surrounding framework already guarantees: no defensive checks for states the caller contract rules out, no second copy of state something else already tracks, no reimplementation of a helper a dependency ships. Before writing one, find out what the layer below provides, and verify it in the version in use instead of assuming.
- **One concept, one definition.** A second type, parameter source or config path for a concept that already exists is duplication, even when the shapes differ slightly. Reuse or extend the existing one. When sharing is not possible yet, say why in one line. A value has one source of truth, and a default exists only when it is the right value for every user, otherwise the parameter is required.
- **Derive, do not store.** Keep the least state that determines behavior. A variable that can be computed from others is a query. A boolean describes the present, not history, is named for what is true (positive form, no double negatives), and a stored flag is a last resort because every flag doubles the cases to test and to read.
- **Own what you operate on.** A class owns the data it works on. A non-owning view, a hand-off between two objects or a split of responsibility needs a reason a reader can state in one sentence, such as a real boundary or a test seam. Otherwise merge them.
- **Boring constructs.** Use the idiom a competent reader of the language already knows. An unfamiliar language feature, a trick that saves lines or a branch kept "just in case" is a cost, not an asset.
- **Names are the documentation.** Use the vocabulary of the domain and of the surrounding system, in full words, and do not coin a term when one exists. The name says what the thing is and what the function does (a function that mutates is not `get`). No one-letter names, and no two names that differ by a prefix or a plural. Rename when the explanation needs a sentence.
- **Comments and docs stay true.** Say why, not what. A comment that needs context to be understood, or that no longer matches the next line, is deleted or rewritten. Logic that is not obvious from the code gets its explanation where a reader meets it first, usually the header.
- **Size is not a defense.** "It is only a few lines" never justifies keeping an element, in code or in a reply. Every line has a reading and maintenance cost. Defend it with the need, or remove it.
- **Prefer deletion.** When a change can be done by removing code, do that. After writing a feature, reread the diff once as its reviewer and delete everything you cannot defend with a current need.

## Least astonishment: predictable behavior

Behavior should match what a reader of the call site, or a user of the tool, already expects. A silent result that differs from that expectation is the worst outcome, worse than a loud failure.

- Prefer a clear, explained failure over a silent wrong result. When the inputs do not allow the expected outcome, stop and say why, do not quietly produce a different one.
- Do not mutate state the caller did not ask you to touch (a lock file, a config, a global) as a side effect of an unrelated command. A surprising side effect is a bug even when it happens to work.
- Keep a new flag or mode orthogonal to the existing ones: it changes only the dimension it names and leaves every other behavior identical to the run without it, so whoever knows the old behavior is not surprised by the new one.
- Match the surrounding conventions (naming, argument order, return shape, defaults) so a symbol behaves like its neighbours, and let a default be the least surprising value rather than the most powerful one.
- When two designs both work, pick the one a user is less likely to be surprised by, even if it does less. Predictability beats power that astonishes.

## Commenting philosophy

The single most characteristic trait: **comments explain why, never what.**

- No comment at all on self-evident code. `// increment the counter` is noise.
- Where a non-obvious constraint exists, write one dense block comment above the function stating the constraint, the failure mode it prevents, and the fallback behavior. Reference the external API that forces the constraint by name.
- Inline comments are reserved for the one line that would otherwise look like a mistake (an intentionally empty entry, an off-by-one that mirrors another API).
- Never comment a removal. When you delete logic, do not leave a comment noting that it was removed, or that something is "no longer" done, or why the old code is gone. A comment describes the code that is present, so a note about absent code is noise to a future reader who never saw the original and has no context for it. The reason a thing was removed belongs in the commit message and the PR description, never in a source comment. The same holds for a comment justifying the new absence of a setting or a flag: if the current code needs no such comment to be understood on its own, it does not need one.
- Section banners structure long files, see [Reading order of a file](#reading-order-of-a-file).

**Signpost comments** are the one sanctioned "what" comment, and his favourite habit in a longer routine. Prefix each non-obvious logical block with one or two plain sentences naming the step it performs, then leave a blank line before the next block. They are a narrative for the human skimming the function, not a description of the mechanics. This applies to every language (C++, Python, CMake, and so on).

- Only where they earn their place. Skip them on short or self-explanatory code: a block whose intent is obvious from the names and calls needs none. Do not narrate every line, and never restate what the code plainly says.
- One or two lines, sitting directly above the block, with a blank line separating one block from the next.
- Describe the step, not the syntax: "Parse the input and build the intermediate description.", not "Call load() and assign the result.".
- Orthogonal to "why, never what": the dense block comment above still explains a non-obvious constraint or failure mode; the signpost only guides the reader through the steps of the routine.

```python
# Parse the input resource (a path or a URDF/SDF string) and build the
# intermediate model description.
intermediate_description = rod.Sdf.load(model_input)

# Lump links together if not all joints are considered.
# Note: this assigns a zero position to all joints not considered.
if considered_joints is not None:
    intermediate_description = intermediate_description.reduce(...)
```

**Formatting of a comment**, in any language:

- Start with an uppercase letter and end with a full stop, including a single-line comment.
- One sentence, one full stop. Do not join two sentences with a semicolon or a comma splice: write "A does x. B does y.", not "A does x; B does y.".
- Every sentence starts with an uppercase letter, including one that begins with a quoted token. Reword so the quoted value moves off the sentence start (write "Mode 'bridge' isolates them.", not "'bridge' isolates them.").
- Keep a sentence on one line when the line limit allows it. Wrap only because the line is too long, never for stylistic effect.
- When a sentence must wrap, balance the lines LaTeX-style, choosing breakpoints that fall at clause boundaries so both wrapped lines look intentional rather than mechanically filled. A line never ends on a dangling function word such as an article, preposition, or conjunction, and no continuation line is left carrying only one or two orphan words. Breaking before a conjunction that follows a comma fixes such a widow, but it is not a blanket rule. Do it only when the trailing clause holds a word or two, and leave a fuller clause of four or five words in place.

Example of the block-comment style he writes:

```cpp
// Resolve a compiler command to an absolute path, mirroring how execvp() searches PATH. CppADCG
// invokes the compiler with execv() (NOT execvp()), so it does not search PATH; a bare command name
// such as the conda "x86_64-conda-linux-gnu-c++" set in $CXX otherwise fails at runtime with
// "No such file or directory". A command that already contains a '/' is returned unchanged.
// Otherwise every PATH entry is probed for an executable regular file, treating an empty entry as
// the current directory (":" semantics), exactly like execvp. If nothing matches, the command is
// returned unchanged so the caller still surfaces a meaningful error.
```

## Error handling: fail fast with actionable messages

A recurring correction he makes, so treat it as a rule:

- Validate at construction time, or as early as the information is available, not at the point of eventual use.
- Never let a misconfiguration surface later as an opaque crash. If a downstream library will fail obscurely, check the precondition yourself and throw first.
- The message names the offending value **and** the environment needed to debug it (the variable it came from, the `PATH`, the resolved path when it differs from the request), plus what to do about it.
- No defensive `try/except` (Python) or `try/catch` (C++) or silent fallbacks around things that should not fail. Swallowing an error is acceptable only when the miss is expected and benign, and then it deserves a comment saying so.

## Reading order of a file

Agents can now write a lot of code quickly. The bottleneck is reviewing it, so write code in the way that makes the human review as easy as possible. For a file, that means a logical narrative: a reading order that explains the code (not its history, not the order in which it was written). A reviewer who has never seen the file should be able to scroll from the first line to the last and follow it: what the file defines, how things are created, what they offer, how the work is done, and which helpers support it.

Two things work together, and one without the other is not enough:

1. **Order.** The sections follow that narrative, so each one builds on what the reader has already seen.
2. **Box comments.** The `====` and `----` boxes divide the file into those sections and guide the reader. They are signposts, not decoration: add one only where it helps navigation, and make the title say what the section is about.

The same boxes help agents: they work as a table of contents, and one `grep` for a title or for `// ===` finds a section without reading the whole file.

This applies to every language and to any long file, configuration included (YAML, TOML, CMake, launch files, shell scripts), above roughly 170 lines; short files need no banners. Use the file's comment prefix (`//`, `#`).

- Order: types and constants, lifecycle, public API, core logic, private helpers. Adapt the chapters to the file, and keep natural pairs together (constructor next to destructor, `open` next to `close`, getter next to setter).
- Banner shape: three lines, with the bars ending exactly at the last letter of the title, so the box is visually aligned. Width is the comment prefix plus the title.

```cpp
// ===============
// Lifecycle hooks
// ===============
```

```python
# ===============
# Lifecycle hooks
# ===============
```

- Two levels, like heading underlines in reStructuredText: `=` boxes for the chapters of the file, `-` boxes for subsections inside a chapter, for example to group the methods of a long class or to split a long function into steps. Same shape and same alignment rule, in every language. Do not add a third level.

```cpp
// ==================
// Command write path
// ==================
```

- Keep it terse. Titles are one to three words, no sentences. Comments say why, in one line when possible. A file or class doc gets two to four short lines plus a pointer to the README or doc that holds the detail (for example a safety policy), instead of a multi-paragraph block in the source.
- A header that defines several classes or resources opens with a short overview comment right before the `namespace`, saying in a line or two what each one is for. A header with a single class needs no overview: the class doc, right before the class, does that job.

```cpp
// ArmService: asks the robot which on-board service owns it, and releases it.
// NoArmService: stand-in for robots without a service. ArmSwitcherService: the real one.
namespace acme::robot::hardware::interfaces::vendor {
```

- Reordering is a pure move: do it in its own commit, with no logic change, so the diff can be checked with `git diff --color-moved`.
- In a config file the chapters follow the logical grouping of the keys (for example robot, limits, gains, safety), not alphabetical order, unless the tool requires an order.
- In an existing file follow the style it already has. Do not reorder or add banners to files you are not otherwise changing.

## Scope of changes

- Minimal and surgical. Fix the root cause, not the symptom.
- No unrelated refactors, no drive-by renames, no speculative abstraction.
- Before deleting a line that looks pointless, `git blame` it and find out why it is there. A strange line is often a load-bearing workaround for an external API or bug, the kind that earns an `UPSTREAM-GAP` comment, so understand the fence before you remove it.
- Extract a helper when it makes something testable or removes a real duplication, not for its own sake.
- When a fix is generally useful and not tied to the branch that exposed it, land it as a standalone PR against the trunk rather than burying it in a stack.
- Centralise a mapping (name to index, string to enum) in one helper instead of spreading raw lookups around. He rejects PRs that scatter them.

## Tests

- Add a small unit test when it is cheap and meaningful. Skip it when the only way to test would be a heavy fixture (a full JIT compile, hardware, a simulator run) to prove a small pure function.
- Test the extracted helper directly rather than driving the whole subsystem.
- Cover three things: the success path, the failure path, and the interesting edge case (the empty entry, the non-executable file, the singular configuration).
- Use RAII helpers, fixtures or context managers to set and restore process state instead of leaking it between tests.
