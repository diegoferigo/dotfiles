# Python style

## Contents

- Layout of a module
- Section banners
- Minimise indentation, not lines of code
- Typing
- Data modelling
- Function design
- Docstrings
- Errors, validation and logging
- Naming, including maths-heavy code
- Tests
- Packaging and tooling

## Layout of a module

- `src/` layout for packages, one module per entity in the public `api/` package,
  internal algorithms in their own subpackage, maths in `math/`, parsers in
  `parsers/`, small helpers in `utils/`.
- Declare `__all__` in every `__init__.py`. It is the explicit public surface of
  the package, and Ruff needs it: a name imported in `__init__.py` and listed in
  `__all__` is treated as an intentional re-export, so no `F401` "imported but
  unused" and no `import x as x` trick. Keep `__all__` sorted alphabetically.
- `__init__.py` imports the submodules, runs the package configuration, and lists
  the public names in `__all__`. With `__all__` in place there is no need to `del`
  imported helpers to keep the namespace clean. Add `# isort:skip` on the rare
  import that must keep its position.
- Absolute imports inside the package. Relative imports only for sibling modules
  at the top of a subpackage.
- Prefer importing the module and qualifying at the call site
  (`import pathlib` then `pathlib.Path`) over `from module import name`. Keeping
  the namespace makes it obvious where every class or function comes from, which
  matters in long files with many imports. A short canonical alias
  (for example `np` for `numpy`) is the accepted shorthand for this same
  qualified style.
- Import aliases are canonical and identical in every file, for example
  `import numpy as np`, and the project's own typing module gets a short alias
  too (`import myproject.typing as mytyping`).
- Import a heavy or optional dependency inside the function that needs it.

## Section banners

See [Reading order of a file](coding-style.md#reading-order-of-a-file) for how to
order the sections. Long modules are split with a boxed comment. Chapters use
`=`, subsections use `-` (for example groups of methods inside a long class, or
the steps of a long function), and the bars match the title length:

```python
# =======================
# Index-related functions
# =======================


# ----------------
# Helper functions
# ----------------
```

## Minimise indentation, not lines of code

Depth is the thing to avoid, not length. A flat function that is a few lines
longer is better than a compact nested one.

- Prefer comprehensions and generator expressions over accumulating in a loop.
- Deep nesting is a last resort. Two levels inside a function is already a smell.
- Break early. Guard clauses at the top, `continue` and `break` inside loops, so
  the main body stays at a single level of indentation.
- Personal style: leave one blank line after the function signature (or after the
  docstring, when there is one) before the body starts.

```python
# Preferred: guard clauses, then flat body.
def process(model: Model, *, name: str) -> Result:

    if name not in model.names:
        raise ValueError(f"Unknown name '{name}'.")

    if model.is_empty:
        return Result.empty()

    return Result(...)
```

## Typing

- Python 3.10+, annotations on everything public.
- `X | None`, never `Optional[X]`.
- `from __future__ import annotations` only in files that actually need forward
  references. Do not add it by reflex to every file.
- A project that has its own vocabulary of types gets a `typing.py` module,
  imported with a short alias. Canonical types and permissive `...Like` input
  types are separate names.
- `Self` and `override` are imported with a fallback, so the package still works
  on Python 3.10 (`Self` lands in 3.11, `override` in 3.12):

  ```python
  try:
      from typing import Self, override
  except ImportError:
      from typing_extensions import Self, override
  ```

- Decorators are typed properly with `ParamSpec` and `TypeVar`, not `Callable`.
- No `@overload`. If a signature needs two shapes, use a union or two functions.

## Data modelling

- Dataclasses are the default container. Standard `@dataclasses.dataclass` for
  plain objects, the framework variant when the framework needs it.
- `dataclasses.field(default_factory=...)` for any mutable or array default.
- `kw_only=True` on base-class fields that must not be positional.
- Encapsulation idiom: a private `_field` plus a `@property` with a docstring
  that returns the usable value.
- The preferred constructor is `@staticmethod def build(...)` returning the
  concrete type. Use `@classmethod def build(cls, ...)` only when the class is
  meant to be subclassed, and let the checker infer `cls`: do not annotate it.
  Two-stage construction gets a public `build_from_<source>` that parses the
  input and delegates to `build`.
- Named shortcuts wrap the constructor instead of duplicating it, for example
  `zero()` calling `build()` with zeros.
- Interfaces are `abc.ABC` with `@abc.abstractmethod`.
- Configuration enums are `enum.Enum` decorated with `@enum.unique`. Use
  `enum.IntEnum` or `enum.IntFlag` only when integer semantics are actually
  needed (C interop, bitmasks, on-the-wire or on-disk values), never as the
  default.

## Function design

- Keyword-only arguments after `*` for anything beyond the primary object:

  ```python
  def name_to_index(model: Model, *, link_name: str) -> int:
  ```

- `match` for dispatch on an enum, with an explicit failing default:

  ```python
  match representation:
      case VelRepr.Inertial:
          return W_array
      case VelRepr.Body:
          return ...
      case _:
          raise ValueError(f"Unhandled representation: {representation!r}")
  ```

- Pure functions in the algorithmic core. All state comes in as arguments, all
  results come out as return values, no hidden mutation.
- Return an annotated `tuple[...]` for a small fixed set of results. Do not
  invent a `NamedTuple` for a two-value return.
- Inner closures for the steps of a long algorithm, with a type alias declared
  just above so the closure signature stays readable.

## Docstrings

- Google style, one-line summary first, then `Args:`, `Returns:`, and `Note:` or
  `Raises:` when relevant. Class docstrings use `Attributes:`.
- An argument that needs explaining drops to an indented continuation line.
- Prefer human readability over heavy rst. When the syntax buys nothing, write
  plain prose: say "the vector v" rather than wrapping every symbol in a role.
  Reserve rst directives (`:math:`, cross-references, `.. code-block::`) for the
  rare repo that renders docstrings as web docs (Sphinx/autodoc), practically
  none of his. There, follow that repo's existing convention instead.
- Maths still goes in the docstring as `:math:` inside an r-string when a rendered
  equation genuinely helps; for a lone symbol or a simple expression, inline text
  reads better.
- Every `@property` has a docstring, even a one-liner.
- Private functions and inner closures usually have none.
- Module-level docstrings, when present, are a short human-readable summary of what
  the module is for, in plain prose, not a wall of rst directives or a generated
  API listing. Skip the module docstring entirely rather than pad it with
  boilerplate. The exception is again a repo whose docs site renders these; there,
  match its convention.

## Errors, validation and logging

- Build the message first, then use it, so the same text can be logged or raised:

  ```python
  msg = "Inertia tensor does not meet the triangular inequality"
  if not validate:
      logging.warning(msg)
  else:
      raise ValueError(msg)
  ```

- `raise ValueError(f"Link '{link_name}' not found in the model.")`: the offending
  value is quoted in the message.
- `raise RuntimeError(msg) from exc` when wrapping a lower-level failure.
- `assert` is for internal shape and invariant checks only, never for validating
  user input.
- A project with real logging needs gets a thin `logging` module over the stdlib,
  exposing `debug`, `info`, `warning`, `error` plus a `configure()` and a level
  enum. Do not sprinkle `print`.
- No defensive `try/except` around things that should not fail.

## Naming, including maths-heavy code

- Private helpers and backing fields take a leading `_`. Constants are
  `SCREAMING_SNAKE_CASE`.
- In maths and physics code the notation of the field wins over spelled-out
  English. Unicode identifiers are fine when they match the equations
  (`δ`, `ṡ`, `τ`), and frame-annotated names follow a documented convention:

  ```
  W_H_B    homogeneous transform from base B to world W
  W_p_B    position of B expressed in W
  W_v_WB   twist of B relative to W, expressed in W
  ```

- Whenever such a convention is used, state it in the module or class docstring.
  The reader must never have to guess what a prefix means.

## Tests

- pytest. Expensive fixtures (models, simulators, compiled artifacts) are
  `scope="session"`, cheap or stateful ones are `scope="function"`.
- Sweeps are parametrised fixtures with explicit ids:

  ```python
  @pytest.fixture(
      scope="function",
      params=[
          pytest.param(VelRepr.Inertial, id="inertial"),
          pytest.param(VelRepr.Body, id="body"),
      ],
  )
  ```

- Random state comes from a seeded key managed in `conftest.py`, split per test,
  so runs are reproducible but not identical.

## Packaging and tooling

- `pyproject.toml` only, `hatchling` plus `hatch-vcs`, `dynamic = ["version"]`
  from git tags. Never a hardcoded version string.
- Optional dependencies as extras, with an `all` extra that pulls the others.
- `project.urls` lists at least `Changelog`, `Source` and `Tracker`.
- Line length, import order and lint rules come from the repo config (`ruff`,
  for example 120 columns).
