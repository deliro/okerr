# corrode

A Rust-like `Result` type for Python 3.11+, fully type annotated.

<div align="center" markdown="1">

> *Explicit is better than implicit.*
> *Errors should never pass silently.*
>
> — The Zen of Python

[![CI](https://github.com/deliro/corrode/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/deliro/corrode/actions/workflows/ci.yml?query=branch%3Amain)
[![codecov](https://codecov.io/gh/deliro/corrode/branch/main/graph/badge.svg)](https://codecov.io/gh/deliro/corrode)
[![PyPI](https://img.shields.io/pypi/v/corrode)](https://pypi.org/project/corrode/)
[![Downloads](https://img.shields.io/pypi/dm/corrode)](https://pypistats.org/packages/corrode)
[![Python versions](https://img.shields.io/pypi/pyversions/corrode)](https://pypi.org/project/corrode/)

</div>

📚 **[Documentation](https://deliro.github.io/corrode/latest/)** — versioned
API reference generated from the source docstrings; every example runs as a
doctest in CI.

## Table of Contents

- [Installation](#installation)
- [Why](#why)
- [Exhaustive error handling](#exhaustive-error-handling)
- [Tour](#tour)
- [Strict by design](#strict-by-design)
- [Iterator utilities](#iterator-utilities)
- [Async iterator utilities](#async-iterator-utilities)
  - [Racing alternatives](#racing-alternatives)
  - [Fan-out over different types](#fan-out-over-different-types)
  - [Streaming sources](#streaming-sources)
  - [Exceptions and `ExceptionGroup`](#exceptions-and-exceptiongroup)
- [Adopting corrode in an existing codebase](#adopting-corrode-in-an-existing-codebase)
- [Typing](#typing)
- [But... why?](#but-why)
- [License](#license)

<!-- --8<-- [start:body] -->

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


# The caller has no idea this can fail
user = get_user(1)
assert user.name == "Alice"
```

The same function with `Result[T, E]` — a union of `Ok[T] | Err[E]`:

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
    if user_id <= 0:
        return Err(NotFound(user_id=user_id))
    if user_id == 13:
        return Err(Forbidden(reason="banned"))
    return Ok(User(id=user_id, name="Alice"))


# Failures are ordinary values — compare them, pass them around, store them
assert get_user(1) == Ok(User(id=1, name="Alice"))
assert get_user(-1) == Err(NotFound(user_id=-1))
```

## Exhaustive error handling

The other half of the payoff: `match` both variants, and let `assert_never`
prove every error case is handled. Add a variant to `GetUserError` and the
type checker flags every site that doesn't handle it:

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
                # a new, unhandled variant makes this a type error
                assert_never(e)
```

## Tour

One scenario — a tiny shop — threaded through the core combinators. Every
method has a doctested example in the
[API reference](https://deliro.github.io/corrode/latest/api/result/); the
`_async` variants mirror their sync counterparts:

```python
from dataclasses import dataclass
from corrode import Ok, Err, Result, from_optional


@dataclass
class Product:
    name: str
    price: int


CATALOG = {"tea": Product(name="tea", price=300)}
STOCK = {"tea": 2}


def find_product(name: str) -> Result[Product, str]:
    # from_optional bridges the idiomatic `T | None` into Result
    return from_optional(CATALOG.get(name), f"unknown product: {name}")


def reserve(product: Product, qty: int) -> Result[Product, str]:
    if STOCK.get(product.name, 0) < qty:
        return Err(f"out of stock: {product.name}")
    return Ok(product)


def parse_qty(raw: str) -> Result[int, str]:
    if raw.isdigit() and int(raw) > 0:
        return Ok(int(raw))
    return Err(f"bad quantity: {raw!r}")


tea = Product(name="tea", price=300)

# map transforms the success value; an Err passes through untouched
assert find_product("tea").map(lambda p: p.price) == Ok(300)
assert find_product("mate").map(lambda p: p.price) == Err("unknown product: mate")

# and_then chains a step that can itself fail...
assert find_product("tea").and_then(lambda p: reserve(p, 1)) == Ok(tea)
assert find_product("tea").and_then(lambda p: reserve(p, 5)) == Err("out of stock: tea")
# ...and short-circuits: on Err, reserve is never called
assert find_product("mate").and_then(lambda p: reserve(p, 1)) == Err("unknown product: mate")

# zip combines independent results into a tuple; the first Err wins
assert find_product("tea").zip(parse_qty("2")) == Ok((tea, 2))
assert find_product("tea").zip(parse_qty("no")) == Err("bad quantity: 'no'")
assert find_product("mate").zip(parse_qty("no")) == Err("unknown product: mate")

# or_else recovers from failure; an Ok passes through untouched
assert find_product("mate").or_else(lambda _: find_product("tea")) == Ok(tea)
assert find_product("tea").or_else(lambda _: find_product("mate")) == Ok(tea)


# A lookup can both fail and legitimately find nothing. transpose moves the
# "nothing" out of the Result, so "this order has no coupon" stays a separate
# case from "the coupon service is down" instead of hiding inside Ok(None)
def find_coupon(order: str) -> Result[int | None, str]:
    if order == "broken":
        return Err("coupon service unavailable")
    return Ok(500 if order == "tea" else None)


assert find_coupon("tea").transpose() == Ok(500)
assert find_coupon("mate").transpose() is None
assert find_coupon("broken").transpose() == Err("coupon service unavailable")

# Results are ordinary values, so they can be stored. Reading one back
# yields nesting, which flatten collapses — keeping "no such order",
# "the order failed", and "the order succeeded" distinct
history: dict[str, Result[Product, str]] = {
    "tea": reserve(tea, 2),
    "mate": Err("out of stock: mate"),
}


def last_order(name: str) -> Result[Result[Product, str], str]:
    return from_optional(history.get(name), "never ordered")


assert last_order("tea").flatten() == Ok(tea)
assert last_order("mate").flatten() == Err("out of stock: mate")
assert last_order("chai").flatten() == Err("never ordered")

# unwrap_or leaves Result-land with a fallback
assert find_product("tea").map(lambda p: p.price).unwrap_or(0) == 300
assert find_product("mate").map(lambda p: p.price).unwrap_or(0) == 0
```

Also available — see the [API reference](https://deliro.github.io/corrode/latest/api/result/):

- transforms: `map_err`, `map_or`, `map_or_else`, `transpose`
- predicates: `is_ok`, `is_err`, `is_ok_and`, `is_err_and`
- side effects: `inspect`, `inspect_err`
- extraction: `ok`, `err`, `ok_value`, `err_value`, `unwrap`, `expect`,
  `unwrap_or_else`, `unwrap_or_raise`
- `@as_result` / `@as_async_result` to wrap exception-raising code at the
  boundary — shown in
  [Adopting corrode](#adopting-corrode-in-an-existing-codebase)
- `_async` variants of every combinator that takes a callback
- `do()` notation — deprecated; see [But... why?](#but-why)

## Strict by design

`Ok` and `Err` refuse to behave like ordinary containers wherever that would
hide a bug.

The most common one: `if result:` looks natural and is always wrong — any
container would be truthy, so a failure would pass the check. `corrode`
makes it loud at runtime, and because `__bool__` is typed as `NoReturn`, a
type checker reports the body of `if result:` as unreachable before you
ever run it:

```python
from corrode import Result, Ok, Err

result: Result[int, str] = Err("hidden failure")

try:
    bool(result)  # this is what `if result:` does under the hood
except TypeError as e:
    print(e)  # Ok and Err have no truth value; use is_ok()/is_err(), ...
```

The same principle elsewhere:

- **No iteration.** `list(Err(...))` or `for x in result` raises with an
  explanatory message instead of silently producing nothing.
- **Immutable.** The contained value cannot be reassigned or deleted after
  construction. Instances are safe to share and to use as dict keys or set
  members (hashable when the contained value is hashable).

## Iterator utilities

`corrode.iterator` works with iterables of `Result` values
([API reference](https://deliro.github.io/corrode/latest/api/iterator/)):

| Function        | Semantics                                                          |
| --------------- | ------------------------------------------------------------------ |
| `collect`       | all values, or the **first** error (short-circuits)                |
| `collect_all`   | all values, or **all** errors (never short-circuits)               |
| `first_ok`      | the **first** value, or **all** errors (short-circuits on success) |
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

`first_ok` is the mirror image of `collect`: it takes the first success and
keeps every failure. Typical use is a fallback chain — several ways to obtain
the same thing, tried in order of preference:

```python
from dataclasses import dataclass
from corrode import Ok, Err, Result
from corrode.iterator import first_ok


@dataclass(frozen=True)
class Payment:
    order: str
    cents: int


# Two payload versions are in flight during a producer rollout
def parse_v2(raw: dict[str, object]) -> Result[Payment, str]:
    match raw:
        case {"order_id": str(order), "amount_cents": int(cents)}:
            return Ok(Payment(order=order, cents=cents))
    return Err("v2: expected order_id and integer amount_cents")


def parse_v1(raw: dict[str, object]) -> Result[Payment, str]:
    match raw:
        case {"order": str(order), "amount": str(amount)}:
            return Ok(Payment(order=order, cents=round(float(amount) * 100)))
    return Err("v1: expected order and decimal amount")


def parse_webhook(raw: dict[str, object]) -> Result[Payment, list[str]]:
    # A generator, so the fallback is lazy: parse_v1 is never called for a
    # payload that is already v2. With one parser per schema, nothing forces
    # the caller to know which version it is looking at.
    return first_ok(parse(raw) for parse in (parse_v2, parse_v1))


assert parse_webhook({"order_id": "A-1", "amount_cents": 990}) == Ok(Payment("A-1", 990))
assert parse_webhook({"order": "A-1", "amount": "9.90"}) == Ok(Payment("A-1", 990))

# Nothing matched. The rejection can be logged with every reason at once —
# with a plain `or` chain only the last failure survives, which is exactly
# the one that says the least about a payload that was never v1 to begin with
assert parse_webhook({"orderId": "A-1"}) == Err(
    ["v2: expected order_id and integer amount_cents", "v1: expected order and decimal amount"]
)
```

## Async iterator utilities

`corrode.async_iterator` runs coroutines or tasks concurrently
([API reference](https://deliro.github.io/corrode/latest/api/async-iterator/)):

| Function                                   | Semantics                                                      |
| ------------------------------------------ | -------------------------------------------------------------- |
| `collect`                                  | input order; first `Err` cancels the rest                      |
| `collect_all`                              | input order; all values or **all** errors, runs everything     |
| `first_ok`                                 | race: first `Ok` to complete wins and cancels the rest, or all errors in input order |
| `map_collect` / `map_partition`            | `collect` / `partition` with the mapping inline                |
| `partition`                                | input order; `(oks, errs)`, runs everything                    |
| `filter_ok_unordered` / `filter_err_unordered` | yield in **completion** order, skip the other side         |
| `filter_ok` / `filter_err`                 | yield in **input** order (explicit `concurrency` required)     |
| `try_reduce`                               | sequential fold, short-circuit on `Err`                        |
| `zip`                                      | `Result.zip` for 2–5 awaitables: all values as a tuple, or the first `Err` cancels the rest |

Every concurrent function takes `concurrency` to bound how many tasks run at once —
`None` means unlimited, except for `filter_ok` / `filter_err`, which require an explicit
bound; `try_reduce` is sequential and takes none. Each also takes either a plain iterable or an
async iterable of awaitables (e.g. an async generator over a paginated API) —
async sources are consumed lazily and closed on exit. All of them clean up
after themselves: cancelling the
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

### Racing alternatives

When the same thing can be fetched from several places, `first_ok` sends the
requests at once and returns the first success, cancelling the rest. The
losers are cancelled, not left running in the background, so the cost of the
extra attempts is bounded by the winner's latency.

Two details make it different from `asyncio.wait(..., FIRST_COMPLETED)`:
a returned `Err` does **not** win the race — a mirror that answers "404" in a
millisecond must not beat the mirror that has the file — and if every
alternative fails, the caller gets *all* the errors, which is the difference
between a log line saying "download failed" and one that says which mirror
said what.

```python
import asyncio
from dataclasses import dataclass
from corrode import Ok, Err, Result
from corrode.async_iterator import first_ok


@dataclass(frozen=True)
class Mirror:
    host: str
    latency: float  # stands in for the network round-trip
    artifacts: frozenset[str]


MIRRORS = (
    Mirror("eu.cdn.example", 0.05, frozenset({"app-1.2.3.tar"})),
    Mirror("us.cdn.example", 0.02, frozenset({"app-1.2.3.tar"})),
    # Fast to answer, but this region has not replicated the release yet
    Mirror("ap.cdn.example", 0.01, frozenset()),
)


async def download(mirror: Mirror, artifact: str) -> Result[bytes, str]:
    await asyncio.sleep(mirror.latency)
    if artifact not in mirror.artifacts:
        return Err(f"{mirror.host}: 404 {artifact}")
    return Ok(f"{artifact} via {mirror.host}".encode())


async def main() -> None:
    # The 404 from ap arrives first and is recorded, not returned; us wins
    # the race and eu is cancelled mid-flight
    release = await first_ok([download(m, "app-1.2.3.tar") for m in MIRRORS])
    assert release == Ok(b"app-1.2.3.tar via us.cdn.example")

    # Nowhere to be found: one report, every mirror, in input order
    missing = await first_ok([download(m, "app-9.9.9.tar") for m in MIRRORS])
    assert missing == Err(
        [
            "eu.cdn.example: 404 app-9.9.9.tar",
            "us.cdn.example: 404 app-9.9.9.tar",
            "ap.cdn.example: 404 app-9.9.9.tar",
        ]
    )


asyncio.run(main())
```

### Fan-out over different types

`collect` needs a homogeneous list. Assembling one page out of several
unrelated calls is the heterogeneous case: `zip` takes 2–5 awaitables of
different types and returns a typed tuple, or the first `Err` — with the
remaining calls cancelled, so a checkout that already failed on the profile
does not keep charging the shipping API.

Compared to `asyncio.gather`, the values stay typed instead of collapsing
into `list[Any]`, and an expected failure ("card declined") stays a value
instead of becoming an exception that has to be told apart from a bug.

```python
import asyncio
from dataclasses import dataclass
from corrode import Ok, Err, Result, async_iterator


@dataclass(frozen=True)
class Profile:
    name: str
    tier: str


@dataclass(frozen=True)
class Quote:
    carrier: str
    cents: int


completed: list[str] = []


async def load_profile(user_id: int) -> Result[Profile, str]:
    return Ok(Profile(name="Alice", tier="gold"))


async def load_balance(user_id: int) -> Result[int, str]:
    return Err("billing: card declined") if user_id == 13 else Ok(2500)


async def shipping_quote(user_id: int) -> Result[Quote, str]:
    await asyncio.sleep(0.05)  # the slowest upstream
    completed.append("shipping")
    return Ok(Quote(carrier="dhl", cents=490))


async def checkout(user_id: int) -> Result[tuple[Profile, int, Quote], str]:
    # zip shadows the builtin on purpose, mirroring Result.zip — reach for it
    # through the module to keep the builtin available
    return await async_iterator.zip(
        load_profile(user_id),
        load_balance(user_id),
        shipping_quote(user_id),
    )


async def main() -> None:
    page = await checkout(user_id=1)
    assert page == Ok((Profile("Alice", "gold"), 2500, Quote("dhl", 490)))

    # The declined card ends the checkout, and the shipping call is cancelled
    # rather than left to finish into a response nobody will read
    assert await checkout(user_id=13) == Err("billing: card declined")
    assert completed == ["shipping"]  # only the successful checkout got there


asyncio.run(main())
```

### Streaming sources

Every function here also accepts an async iterable, so the work does not have
to be listed up front. The usual source is a paginated API or a database
cursor: materialising it first (`[x async for x in pages()]`) means crawling
the whole thing into memory before the first item is processed, and the
concurrency limit then applies to nothing.

Passing the async generator directly keeps the two interleaved — pages are
pulled only as workers free up, so at most `concurrency` requests are in
flight and memory stays bounded no matter how many pages there are.

```python
import asyncio
from collections.abc import AsyncIterator
from corrode import Ok, Result
from corrode.async_iterator import map_collect

PAGES: dict[str | None, tuple[list[int], str | None]] = {
    None: ([1, 2, 3], "cursor-2"),
    "cursor-2": ([4, 5, 6], "cursor-3"),
    "cursor-3": ([7, 8], None),
}


async def list_orders(cursor: str | None) -> tuple[list[int], str | None]:
    await asyncio.sleep(0.01)  # one round-trip per page
    return PAGES[cursor]


async def order_ids() -> AsyncIterator[int]:
    cursor: str | None = None
    while True:
        page, cursor = await list_orders(cursor)
        for order_id in page:
            yield order_id
        if cursor is None:
            return


async def order_total(order_id: int) -> Result[int, str]:
    await asyncio.sleep(0.01)
    return Ok(order_id * 100)


async def main() -> None:
    # Listing and fetching overlap: the totals for page 1 are already being
    # fetched while page 2 is still being listed. Four in flight at a time,
    # and the generator is closed on exit even if a fetch fails
    totals = await map_collect(order_ids(), order_total, concurrency=4)
    assert totals == Ok([100, 200, 300, 400, 500, 600, 700, 800])


asyncio.run(main())
```

### Exceptions and `ExceptionGroup`

A raised exception (as opposed to a returned `Err`) means a bug or an
infrastructure failure — it propagates and cancels everything else. The
contract is uniform: **exceptions from the concurrent utilities always arrive
wrapped in an `ExceptionGroup`, even when only one task failed.** One failure
is a group of one.

This is deliberate. How many tasks fail "at the same time" is a race — if a
lone exception propagated bare, `except ConnectionError` would work in
testing and miss the day two requests fail in the same event-loop tick. What
you catch must not depend on timing, so there is exactly one thing to write:
`except*` — the same rule `asyncio.TaskGroup` follows. Tasks that raise
*while being cancelled* are collected into the group too — nothing is
silently discarded.

One carve-out: sequential `try_reduce` runs one coroutine at a time, so only
one can fail, and it propagates bare.

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

`corrode` is designed for gradual adoption — no big-bang rewrite:

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


# Step 1: wrap — the body is untouched, callers get Result.
# Raised KeyError / ValueError become Err(exc); other exceptions propagate.
@as_result(KeyError, ValueError)
def parse_port_wrapped(key: str) -> int:
    return int(os.environ[key])


assert parse_port_wrapped("PORT") == Ok(8080)  # Result[int, KeyError | ValueError]


# Step 2: explicit Err — the decorator is gone, the error types move into
# the signature; callers don't change
def parse_port_explicit(key: str) -> Result[int, KeyError | ValueError]:
    if key not in os.environ:
        return Err(KeyError(key))
    try:
        return Ok(int(os.environ[key]))
    except ValueError as exc:
        return Err(exc)


assert parse_port_explicit("PORT") == Ok(8080)
assert parse_port_explicit("MISSING").is_err()


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

`@as_result` accepts only `Exception` subclasses — `KeyboardInterrupt`,
`SystemExit`, and `asyncio.CancelledError` always propagate, so Ctrl-C and
task cancellation keep working no matter what you wrap.

`try`/`except` and `Result` mix freely in the same function — catch what you
caught before and wrap it in `Err`.

## Typing

`corrode` is fully typed and ships a `py.typed` marker ([PEP 561](https://peps.python.org/pep-0561/)) —
type information works out of the box, no stubs needed. Every release is
verified against four type checkers in strict mode: mypy, basedpyright, ty,
and pyrefly. All README examples are executed *and* type-checked in CI; all
docstring examples run as doctests.

## But... why?

Answers to the questions every "Result vs exceptions in Python" debate
arrives at.

### Doesn't this give me two error channels instead of one?

Yes — and that is the architecture of Rust and Go, not a Python compromise.
Rust has `Result` *and* panics; Go has `error` values *and* `panic`. The
channels carry different things: an `Err` is an expected failure, part of the
contract and visible in the signature; a raised exception is a bug or an
infrastructure fault the caller cannot meaningfully handle at the call site.

The honest difference: Python's ecosystem raises exceptions for *expected*
failures too (`KeyError`, a unique-constraint violation). Contain that leak
at the boundary — wrap third-party calls with `@as_result` in your adapter
layer — and the interior of your domain keeps the contract: every `Err` is
in the signature, and any exception that escapes is by definition a bug that
should crash loudly.

The alternative isn't one channel — it's one *invisible* channel. A
signature that returns `User` and says nothing about the five ways it can
fail has the same two channels; it just hides both.

### Where is the `?` operator?

There is no honest way to build it. Rust's `?` is not just syntax: the
compiler checks every propagated error against the declared return type and
converts it via `From`. Every Python emulation keeps the syntax and loses the
check:

- **Decorator + control-flow exception** (`resulty`, `meiga`, and the
  `@pipeline` that `returns` shipped and later removed): an unwrap-like
  method typed `-> T` that secretly raises, caught by the decorator. The
  error type is erased at the raise site, so nothing verifies the propagated
  errors against the function's declared `E` — the signature can lie.
- **Generator do-notation** (corrode's own `do()`, now deprecated): the same
  hole — the annotation it requires is not checked by type checkers.
- **AST rewriting at import time** (how pytest rewrites asserts): produces
  real early returns, but requires installing an import hook before user code
  loads, confuses coverage and debuggers, and the typing hole remains.

A fix would require type-checker plugins, and neither pyright nor ty nor
pyrefly has a plugin API. `corrode` does not ship features whose static
types can lie — so propagation is spelled with `match`, combinators, or the
iterator utilities.

### Isn't `match` on every fallible call verbose?

Yes — a constant, local, predictable cost. Go has paid it (`if err != nil`)
at industrial scale for over a decade, and when offered built-in propagation
(the `try` proposal, 2019) the community rejected it, preferring explicit
control flow. In practice the tax is smaller than it looks: `map`,
`and_then`, and `or_else` chain the linear cases, the iterator utilities
cover collections, and `match` remains only where control flow genuinely
branches — where the verbosity *is* the information.

### When should I not use corrode?

In thin glue over exception-raising libraries. If a module has no domain
logic of its own — it just calls an ORM or HTTP client and forwards the
outcome — wrapping every call gives you `Result` *and* `try/except` in the
same function: the worst of both worlds. Use `Result` where failures are
part of your domain and you can enumerate them; leave plain exceptions at
edges that are already all-exception territory.

<!-- --8<-- [end:body] -->

## Acknowledgements

`corrode` started as a fork of [rustedpy/result](https://github.com/rustedpy/result),
which laid the groundwork for Rust-style result types in Python.

## License

MIT License
