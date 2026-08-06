# corrode

A Rust-like `Result` type for Python 3.11+, fully type annotated.

<div align="center">

> *Explicit is better than implicit.*
> *Errors should never pass silently.*
>
> — The Zen of Python

[![CI](https://github.com/deliro/corrode/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/deliro/corrode/actions/workflows/ci.yml?query=branch%3Amain)
[![codecov](https://codecov.io/gh/deliro/corrode/branch/main/graph/badge.svg)](https://codecov.io/gh/deliro/corrode)
[![PyPI](https://img.shields.io/pypi/v/corrode)](https://pypi.org/project/corrode/)
[![Python versions](https://img.shields.io/pypi/pyversions/corrode)](https://pypi.org/project/corrode/)

</div>

📚 **[Documentation](https://deliro.github.io/corrode/)** — the full API
reference is generated from the source docstrings, so it always matches the
code, and every example in it is executed as a doctest in CI.

## Table of Contents

- [Installation](#installation)
- [Why](#why)
- [Exhaustive error handling](#exhaustive-error-handling)
- [Guarantees](#guarantees)
- [Tour](#tour)
- [Iterator utilities](#iterator-utilities)
- [Async iterator utilities](#async-iterator-utilities)
  - [Exceptions and `ExceptionGroup`](#exceptions-and-exceptiongroup)
- [Adopting corrode in an existing codebase](#adopting-corrode-in-an-existing-codebase)
- [Typing](#typing)
- [Stability](#stability)
- [License](#license)

## Installation

```sh
uv add corrode
```

or with pip / poetry:

```sh
pip install corrode
poetry add corrode
```

## Why

Exceptions are implicit. Nothing in a function signature tells you it can
raise, what it raises, or whether the caller remembered to handle it.
Bugs hide until production, and `except Exception` becomes the norm:

```python
from dataclasses import dataclass

@dataclass
class User:
    id: int
    name: str

# Can this raise? What exceptions? The signature doesn't tell you.
def get_user(user_id: int) -> User:
    if user_id <= 0:
        raise ValueError(f"Invalid user ID: {user_id}")
    if user_id == 13:
        raise PermissionError("Access denied")
    return User(id=user_id, name="Alice")

# The caller has no idea this can fail — until it does in production
user = get_user(1)
assert user.name == "Alice"
```

`Result[T, E]` is a union of `Ok[T] | Err[E]`:

```python
from dataclasses import dataclass
from corrode import Result, Ok, Err

@dataclass
class User:
    id: int
    name: str

@dataclass
class NotFound:
    user_id: int

@dataclass
class Forbidden:
    reason: str

type GetUserError = NotFound | Forbidden

# Errors are now part of the return type — callers see exactly what can go wrong
def get_user(user_id: int) -> Result[User, GetUserError]:
    # Instead of raise, return Err — the type checker tracks it
    if user_id <= 0:
        return Err(NotFound(user_id=user_id))
    if user_id == 13:
        return Err(Forbidden(reason="banned"))
    return Ok(User(id=user_id, name="Alice"))

# Can't ignore errors — Result forces you to handle both variants
assert get_user(1) == Ok(User(id=1, name="Alice"))
assert get_user(-1) == Err(NotFound(user_id=-1))
```

## Exhaustive error handling

```python
from dataclasses import dataclass
from typing import assert_never
from corrode import Ok, Err, Result


@dataclass
class User:
    id: int
    name: str

@dataclass
class NotFound:
    user_id: int

@dataclass
class Forbidden:
    reason: str

type GetUserError = NotFound | Forbidden


def get_user(user_id: int) -> Result[User, GetUserError]:
    if user_id <= 0:
        return Err(NotFound(user_id=user_id))
    if user_id == 13:
        return Err(Forbidden(reason="banned"))
    return Ok(User(id=user_id, name="Alice"))


match get_user(42):
    case Ok(user):
        print(f"Welcome, {user.name}")
    case Err(e):
        # Nested match on the error — each variant handled explicitly
        match e:
            case NotFound(user_id=uid):
                print(f"User {uid} does not exist")
            case Forbidden(reason=reason):
                print(f"Access denied: {reason}")
            case _:
                # If you add a new variant to GetUserError, mypy reports
                # an error here until you handle it — compile-time safety
                assert_never(e)
```

## Guarantees

`Ok` and `Err` are strict containers:

- **Immutable.** The contained value cannot be reassigned or deleted after
  construction (`AttributeError`). Instances are safe to share and to use as
  dict keys or set members (hashable when the contained value is hashable).
- **No truth value.** `if result:` is the classic silent bug — any `Result`
  would be truthy, so a failure passes the check. `corrode` makes it loud:

```python
from corrode import Result, Ok, Err

result: Result[int, str] = Err("hidden failure")

try:
    if result:  # bug: this is NOT "is it Ok?"
        pass
except TypeError as e:
    print(e)  # Ok and Err have no truth value; use is_ok()/is_err(), ...
```

Use `is_ok()` / `is_err()`, pattern matching, or `is_ok_and()` instead.

- **Not general-purpose iterables.** Iterating an `Err` outside `do()`
  notation (e.g. `list(Err(...))`) raises `DoError` with an explanatory
  message instead of silently misbehaving.
- **`BaseException` is never swallowed.** `@as_result` / `@as_async_result`
  accept only `Exception` subclasses — `KeyboardInterrupt`, `SystemExit` and
  `asyncio.CancelledError` always propagate, so task cancellation keeps
  working.
- **Async utilities never leak tasks, never lose exceptions.** Raised
  exceptions always arrive as a single `ExceptionGroup` — even a lone one —
  so what you catch never depends on timing. See
  [Exceptions and `ExceptionGroup`](#exceptions-and-exceptiongroup).

## Tour

A taste of the combinators. Every sync method has a doctested example in the
[API reference](https://deliro.github.io/corrode/api/result/); the `_async`
variants mirror their sync counterparts:

```python
from dataclasses import dataclass
from corrode import Ok, Err, Result, from_optional


@dataclass
class User:
    id: int
    name: str


def find_user(user_id: int) -> Result[User, str]:
    users = {1: User(id=1, name="Alice")}
    # from_optional bridges the idiomatic `T | None` into Result
    return from_optional(users.get(user_id), f"user {user_id} not found")


# map transforms the success value; Err passes through untouched
assert find_user(1).map(lambda u: u.name) == Ok("Alice")
assert find_user(2).map(lambda u: u.name) == Err("user 2 not found")


# and_then chains fallible steps; the first Err short-circuits
def check_admin(user: User) -> Result[User, str]:
    return Ok(user) if user.id == 1 else Err("not an admin")


assert find_user(1).and_then(check_admin) == Ok(User(id=1, name="Alice"))

# or_else recovers from failures
assert find_user(2).or_else(lambda _: find_user(1)).map(lambda u: u.id) == Ok(1)

# zip combines independent results into a tuple (first Err wins)
assert Ok(1).zip(Ok("a"), Ok(3.0)) == Ok((1, "a", 3.0))

# flatten removes one level of nesting: Result[Result[T, E], E] -> Result[T, E]
assert Ok(Ok(1)).flatten() == Ok(1)

# unwrap_or extracts with a fallback when you leave Result-land
assert find_user(2).map(lambda u: u.name).unwrap_or("guest") == "guest"
```

Wrap exception-raising code at the boundary with `@as_result` /
`@as_async_result`:

```python
import os
from corrode import as_result, Ok

os.environ["PORT"] = "8080"


# Raised KeyError / ValueError become Err(exc); other exceptions propagate
@as_result(KeyError, ValueError)
def parse_port(key: str) -> int:
    return int(os.environ[key])


assert parse_port("PORT") == Ok(8080)  # Result[int, KeyError | ValueError]
```

Also available — see the [API reference](https://deliro.github.io/corrode/api/result/):

- transforms: `map_err`, `map_or`, `map_or_else`
- predicates: `is_ok`, `is_err`, `is_ok_and`, `is_err_and`
- side effects: `inspect`, `inspect_err`
- extraction: `ok`, `err`, `ok_value`, `err_value`, `unwrap`, `expect`,
  `unwrap_or_else`, `unwrap_or_raise`
- `_async` variants of every combinator that takes a callback
- `do()` notation — **deprecated**: the annotation it requires is not checked
  by type checkers, which defeats the purpose; calling it emits a
  `DeprecationWarning`

## Iterator utilities

`corrode.iterator` works with iterables of `Result` values
([API reference](https://deliro.github.io/corrode/api/iterator/)):

| Function        | Semantics                                                          |
| --------------- | ------------------------------------------------------------------ |
| `collect`       | all values, or the **first** error (short-circuits)                |
| `collect_all`   | all values, or **all** errors (never short-circuits)               |
| `map_collect`   | `collect` with the mapping inline                                  |
| `partition`     | split into `(oks, errs)`, keep both sides                          |
| `map_partition` | `partition` with the mapping inline                                |
| `filter_ok`     | lazily yield values, skip errors                                   |
| `filter_err`    | lazily yield errors, skip values                                   |
| `try_reduce`    | fold with a fallible function, short-circuit on the first error    |

```python
from corrode import Ok, Err, Result
from corrode.iterator import map_collect, collect_all, partition


def parse(s: str) -> Result[int, str]:
    return Ok(int(s)) if s.isdigit() else Err(f"not a number: {s!r}")


# Fail fast: the first Err wins
assert map_collect(["1", "2", "3"], parse) == Ok([1, 2, 3])
assert map_collect(["1", "x", "3"], parse) == Err("not a number: 'x'")

# Accumulate: Ok only when everything succeeded, otherwise every error.
# The validation use case — the caller gets a complete error report.
assert collect_all([parse("1"), parse("x"), parse("y")]) == Err(
    ["not a number: 'x'", "not a number: 'y'"]
)

# Keep both sides
assert partition([parse("1"), parse("x"), parse("2")]) == ([1, 2], ["not a number: 'x'"])
```

## Async iterator utilities

`corrode.async_iterator` runs coroutines or tasks concurrently
([API reference](https://deliro.github.io/corrode/api/async-iterator/)):

| Function                                   | Semantics                                                      |
| ------------------------------------------ | -------------------------------------------------------------- |
| `collect`                                  | input order; first `Err` cancels the rest                      |
| `collect_all`                              | input order; all values or **all** errors, runs everything     |
| `map_collect` / `map_partition`            | `collect` / `partition` with the mapping inline                |
| `partition`                                | input order; `(oks, errs)`, runs everything                    |
| `filter_ok_unordered` / `filter_err_unordered` | yield in **completion** order, skip the other side         |
| `filter_ok` / `filter_err`                 | yield in **input** order (explicit `concurrency` required)     |
| `try_reduce`                               | sequential fold, short-circuit on `Err`                        |

Every function accepts `concurrency` to bound how many tasks run at once
(`None` = unlimited). All of them clean up after themselves: cancelling the
caller, breaking out of an `async for`, or an exception in any task cancels
all in-flight tasks and closes unconsumed coroutines — nothing keeps running
in the background.

```python
import asyncio
from dataclasses import dataclass
from corrode import Ok, Err, Result
from corrode.async_iterator import map_collect, collect_all


@dataclass
class User:
    id: int


async def fetch_user(user_id: int) -> Result[User, str]:
    if user_id <= 0:
        return Err(f"bad id: {user_id}")
    return Ok(User(id=user_id))


async def main() -> None:
    # Concurrent and bounded; results in input order; first Err cancels the rest
    result = await map_collect([1, 2, 3, 4, 5], fetch_user, concurrency=3)
    assert result == Ok([User(id=i) for i in range(1, 6)])

    # Error accumulation: every failure is reported, nothing is cancelled
    report = await collect_all([fetch_user(1), fetch_user(-1), fetch_user(-2)])
    assert report == Err(["bad id: -1", "bad id: -2"])


asyncio.run(main())
```

### Exceptions and `ExceptionGroup`

A raised exception (as opposed to a returned `Err`) means a bug or an
infrastructure failure — it propagates and cancels everything else. The
contract is uniform: **exceptions from the concurrent utilities always arrive
wrapped in an `ExceptionGroup`, even when only one task failed.** One failure
is a group of one.

This is deliberate. Whether one or several tasks fail "at the same time" is a
race — if a lone exception propagated bare, `except ConnectionError` would
work in testing and silently miss in production the day two requests fail in
the same event-loop tick. The exception type you catch must never depend on
timing, so there is exactly one thing to write: `except*` (the same rule
`asyncio.TaskGroup` follows). Tasks that raise *while being cancelled* are
collected into the group too — nothing is silently discarded.

The only exception is sequential `try_reduce`: it runs one coroutine at a
time, so only one can fail, and it propagates bare.

```python
import asyncio
from corrode import Ok, Result
from corrode.async_iterator import collect


async def good() -> Result[int, str]:
    return Ok(1)


async def fetch(source: str) -> Result[int, str]:
    raise ConnectionError(f"{source} unreachable")


async def main() -> None:
    # A single failure — still an ExceptionGroup, still except*
    single: list[str] = []
    try:
        await collect([good(), fetch("eu")])
    except* ConnectionError as group:
        single = [str(e) for e in group.exceptions]
    assert single == ["eu unreachable"]

    # Several simultaneous failures — same handling, nothing is lost
    several: list[str] = []
    try:
        await collect([fetch("eu"), fetch("us")])
    except* ConnectionError as group:
        several = sorted(str(e) for e in group.exceptions)
    assert several == ["eu unreachable", "us unreachable"]


asyncio.run(main())
```

## Adopting corrode in an existing codebase

You don't have to rewrite everything at once — `corrode` is designed for
gradual adoption:

1. **Wrap** existing functions with `@as_result(ExcType, ...)` — the body is
   unchanged, callers start receiving `Result` with the exception inside `Err`.
2. **Return `Err(exc)` explicitly** — replace `raise` with `return Err(exc)`
   and drop the decorator; the error types move into the signature, callers
   don't change.
3. **Replace exceptions with domain types** — frozen dataclasses carrying
   exactly the data the caller needs, no more parsing exception messages.

```python
import os
from dataclasses import dataclass
from corrode import as_result, Ok, Err, Result

os.environ["PORT"] = "8080"


# Step 1: wrap — the body is untouched, callers get Result
@as_result(KeyError, ValueError)
def parse_port_wrapped(key: str) -> int:
    return int(os.environ[key])


assert parse_port_wrapped("PORT") == Ok(8080)


# Step 3: domain error types instead of exceptions
@dataclass
class MissingKey:
    key: str

@dataclass
class InvalidValue:
    key: str
    raw: str


def parse_port(key: str) -> Result[int, MissingKey | InvalidValue]:
    raw = os.environ.get(key)
    if raw is None:
        return Err(MissingKey(key=key))
    try:
        return Ok(int(raw))
    except ValueError:
        return Err(InvalidValue(key=key, raw=raw))


assert parse_port("PORT") == Ok(8080)
assert parse_port("MISSING") == Err(MissingKey(key="MISSING"))
```

`try`/`except` and `Result` mix freely in the same function — catch what you
caught before and wrap it in `Err`.

## Typing

`corrode` is fully typed and ships a `py.typed` marker ([PEP 561](https://peps.python.org/pep-0561/)) —
type information works out of the box, no stubs needed. Every release is
verified against **four** type checkers in strict mode: mypy, basedpyright,
ty, and pyrefly. All README examples are executed *and* type-checked in CI;
all docstring examples run as doctests.

## Stability

`corrode` is pre-1.0: breaking changes may occur in minor releases and are
always listed in the [changelog](https://github.com/deliro/corrode/blob/main/CHANGELOG.md).
Versioning follows [SemVer](https://semver.org); after 1.0 the public API —
everything exported from `corrode`, `corrode.iterator`, and
`corrode.async_iterator` — will only break with a major release.

## Acknowledgements

`corrode` is inspired by and originally forked from [rustedpy/result](https://github.com/rustedpy/result).
We are grateful for that library's existence — it laid the foundation for bringing Rust-style result types to Python and made this project possible.

## License

MIT License
