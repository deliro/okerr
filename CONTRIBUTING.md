# Contributing to corrode

## Setup

Requirements: [uv](https://docs.astral.sh/uv/) and [just](https://github.com/casey/just).

```sh
git clone https://github.com/deliro/corrode
cd corrode
just install
```

## Development loop

```sh
just            # lint + typecheck + tests (what CI runs)
just test       # tests only (includes doctests and README blocks)
just test-cov   # tests with coverage report
just fmt        # auto-format
just typecheck  # mypy (3.13 and 3.11 modes), basedpyright, ty, pyrefly
```

All four type checkers must pass with zero errors. Every code block in
`README.md` is executed and type-checked by `tests/test_readme_blocks.py` —
if you change the README, its examples must actually run.

## Commit messages

Releases are automated with [release-please](https://github.com/googleapis/release-please),
so commits must follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat: ...` — new public API (minor bump)
- `fix: ...` — bug fix (patch bump)
- `docs: ...`, `chore: ...`, `test: ...` — no release

## Pull requests

- New public API needs: tests, doctest examples in the docstring, a README
  section, and typesafety coverage in `tests/type_checking/typesafety.py`.
- Patch coverage target is 100% (enforced by codecov).
- CI must be fully green: tests on 3.11–3.14, ruff check + format, all four
  type checkers.
