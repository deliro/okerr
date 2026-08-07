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

## Documentation

The site is built with MkDocs and published to GitHub Pages by CI — never by
hand. The API reference is generated from the source docstrings by
mkdocstrings, and `docs/index.md` includes a marked region of `README.md`, so
there is no second copy of anything to keep in sync.

Versions are managed by [mike](https://github.com/jimporter/mike) and each one
lives in its own directory on the `gh-pages` branch:

| Version   | Source                | Deployed when                     |
| --------- | --------------------- | --------------------------------- |
| `latest`  | the newest release    | a GitHub Release is published     |
| `<x.y.z>` | that release, pinned  | a GitHub Release is published     |
| `dev`     | the `main` branch     | every push to `main`              |

`latest` is what the site opens by default; `dev` documents unreleased code.
Deep links in the README point at `/latest/...` on purpose — they must keep
working for people running the released version.

```sh
just docs           # live preview of your working tree
just docs-build     # strict build, same as CI
just docs-versions  # preview the published multi-version site
```

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
