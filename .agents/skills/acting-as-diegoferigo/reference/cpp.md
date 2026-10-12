# C++ style


## Contents

- Headers and includes
- Naming
- API design
- Errors and logging
- Comments and documentation
- Tests
- CMake

## Headers and includes

- C++20.
- `#pragma once` at the top of every header, not include guards.
- Explicit includes for everything used, never rely on transitive includes. This
  is something reviewers flag on his PRs and he fixes immediately.
- Forward declare in headers when it avoids an include.
- Public headers stay thin. Implementation details go to the source file or
  behind pImpl. The pImpl member is always named `pImpl`, not `impl_` and not
  `m_pImpl`.
- `std::unique_ptr` for exclusive ownership, `std::shared_ptr` only when
  ownership is genuinely shared. Pointer aliases live next to the class they
  belong to, in the same header and inside the same namespace block:

  ```cpp
  using InputSignalPtr = std::shared_ptr<const Signal>;
  ```

## Naming

Follow the repo `.clang-tidy`, always: his own naming habits changed between
projects precisely because he follows the local convention. A representative
scheme:

| kind | convention |
|---|---|
| types, enums, structs, aliases | `CamelCase` |
| member functions | `camelBack` |
| variables, parameters, members | `lower_case` |
| private and protected members | trailing `_` |
| constants | `k` prefix |
| macros and enum constants | `UPPER_CASE` |
| namespaces | `lower_case` |

Habits that carry across repos:

- Namespaces are `lower_snake_case`, nested with the C++17 syntax, and closed
  with a comment:

  ```cpp
  namespace my_project::core {
  ...
  } // namespace my_project::core
  ```

- An `enum class` starts with an `Invalid` value so a default-constructed enum is
  never silently meaningful.
- Older codebases use the `m_` member prefix, newer ones use a trailing `_`. Match
  the file you are in.
- `constexpr` (or `inline constexpr` for header constants) over `#define` macros
  or runtime `const` for compile-time constants.

## API design

- `const` by default. `const auto&` in range-for. Early returns instead of nested
  conditionals.
- `[[nodiscard]]` on any function that returns a value or a status the caller
  must not ignore.
- Rule of zero by default: rely on RAII members and declare no special member
  functions. When a class does manage a resource, spell out the rule of five
  explicitly with `= default` / `= delete`, and mark move operations `noexcept`.
- `noexcept` on move constructors, move assignment and any function that is
  guaranteed not to throw. It is not decoration: containers rely on it, for
  example `std::vector` reallocation.
- Internal helpers live in a `detail` namespace **specifically so they can be unit
  tested**, and the public entry point becomes a thin call into them.
- Prefer composition. Use an abstract base class when there is a real polymorphic
  contract, and keep it small.
- Return a value or throw. For a result that may legitimately be absent, return
  `std::optional<T>` rather than a sentinel value or an out-parameter plus `bool`.
  Legacy code that returns `bool` for success gets extended in the same style
  rather than mixed with exceptions.

## Errors and logging

- Fail fast, as described in [coding-style.md](coding-style.md).
- Exceptions carry a full diagnostic message. Build it with `std::format`,
  falling back to `std::ostringstream` only where `std::format` is not available
  in the toolchain.
- When a subsystem has many distinct failure modes, give it a typed exception per
  entity rather than a single generic error:

  ```cpp
  class JointNotFound : public std::runtime_error { ... };
  ```

- `assert(false)` marks a branch that must be unreachable, with a comment saying
  why it exists (usually to silence a compiler warning after an exhaustive
  switch). Switch to `std::unreachable()` once the project moves to C++23.
- Projects with their own logging expose short stream macros with a library
  prefix, used as `bfError << "message";`. Use the project's macros, do not add
  `std::cout`.
- A singleton is a static local inside the accessor function, never a global.

## Comments and documentation

- New headers: `///` doc comments, no `/** */`, no `@brief`. First sentence is
  the brief. Existing files keep their style.
- Document a declaration only when the name and signature do not say enough
  (units, empty optional, ownership, lifetime, thread safety, errors). Otherwise
  list declarations compactly, with no blank lines, under a box comment. A
  documented declaration gets a blank line before it.
- Parameters go in the same prose, not in `@param` rows, unless most are
  non-obvious. Do not redocument an override. Trailing member notes use `///<`.
- Boxes, the overview before a `namespace` (multi-entity headers) and
  implementation notes stay plain `//`. A class doc is 1-3 lines of `///`.
- Internal helpers get a comment only when the reason for their existence is not
  obvious.
- The general rule from [coding-style.md](coding-style.md) applies: explain why,
  never what.
- Long `.cpp` and `.hpp` files use boxed `// ====` section banners in reading
  order, see [Reading order of a file](coding-style.md#reading-order-of-a-file).

## Tests

The shared testing rules in [coding-style.md](coding-style.md) apply as they are
(success path, failure path, edge case; RAII helpers to set and restore process
state). C++ specific:

- Test the helper extracted into a `detail` namespace directly, rather than
  driving the whole subsystem.

## CMake

- Target based. Properties go on the target, never as directory-wide flags.
- One option per feature, named after the project, defaulting to the
  least-surprising value.
- Install and export rules are part of the target definition, not an afterthought.
- Formatting is clang-format's job (for example 120 columns, pointer
  left-aligned). Do not hand-format around it, and re-run it after editing.
