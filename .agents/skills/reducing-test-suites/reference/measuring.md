# Measuring per-test coverage

These are Python mechanics and may drift with tool releases; the principles in SKILL.md do not depend on them.

## Python and pytest

Use `pytest-cov` with dynamic contexts, scoped to the source under test, run serially:

```bash
pytest -p no:xdist --cov=<source-package> --cov-branch --cov-context=test --cov-report=
```

- Contexts are `<nodeid>|setup`, `<nodeid>|run` and `<nodeid>|teardown` ([pytest-cov contexts](https://pytest-cov.readthedocs.io/en/latest/contexts.html)). Strip the phase and union the three sets per node id.
- Read the data with `CoverageData` ([API](https://coverage.readthedocs.io/en/latest/api_coveragedata.html)): `measured_contexts()`, then `set_query_contexts(...)` and `lines(file)` / `arcs(file)`. `set_query_contexts` takes regular expressions matched with `re.search`, so escape and anchor node ids (`^` + `re.escape(ctx)` + `$`), or read all contexts once and filter in Python. `arcs()` holds data only with branch measurement on; both return `None` for an unmeasured file.
- Code that runs outside any test (module-level imports at collection) lands in the empty context `""`. Do not read it as a test.
- Subprocesses and console entry points: use a recent coverage.py with `patch = subprocess` under `[run]` (check its docs for the setting and version: [config](https://coverage.readthedocs.io/en/latest/config.html#config-run-patch), [subprocess](https://coverage.readthedocs.io/en/latest/subprocess.html)). Set `parallel = true` and run `coverage combine` before reading the data, as the subprocess page states. The older route is `COVERAGE_PROCESS_START` plus a call to `coverage.process_startup()` at interpreter start, usually from a `.pth` file. pytest-cov no longer does subprocess measurement itself ([changelog](https://pytest-cov.readthedocs.io/en/latest/changelog.html)).
- Probe before trusting it: run one known subprocess-based test and confirm its context lists the child's lines. If it does not, child lines are not attributable to a test.
- Contexts may be split or lost under xdist or parallel runs; measure serially.
- Baseline: `coverage report` over the `--cov` scope (source only, line and branch totals), plus the line count of the test files by the rule in SKILL.md.

## Deriving redundancy

From the map test -> covered set:

- **No unique coverage:** tests whose set is a subset of the union of all other tests.
- **Identical groups:** tests with exactly the same set; keep one, inventory the assertions of the others.
- **Minimal covering set:** greedy set cover over the union; tests outside it are candidates, not verdicts.

Every candidate still needs an assertion inventory before a verdict.

## Limits

- Whole-application tests: a test whose set covers most of a module blurs the signal. Judge it by its assertions.
- Lazy imports run inside the first test that triggers them and look like unique coverage there.
- Parametrized cases are separate contexts. Cases redundant one by one may still define a table together; judge them as a table.
- Some tests cannot be measured by coverage at all (shell scripts, docs checks, other languages). Judge them by assertions alone.

## Other languages

A sketch only. For C++ (gtest or similar with gcov or llvm-cov), look for a coverage run per test filter or per test binary, and a way to merge or compare the profiles. Per-test runs there cost more, test-filter names change as tests are folded.
