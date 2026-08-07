"""Iterator utilities for Result."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import TypeVar

from .result import Err, Ok, Result

T = TypeVar("T")
U = TypeVar("U")
E = TypeVar("E")
F = TypeVar("F")


def collect(iterable: Iterable[Result[T, E]]) -> Result[list[T], E]:
    """
    Collect an iterable of ``Result`` values into ``Ok[list]``.

    Returns the first ``Err`` encountered, short-circuiting the iteration.

    Examples:
        >>> collect([Ok(1), Ok(2), Ok(3)])
        Ok([1, 2, 3])
        >>> collect([Ok(1), Err("bad"), Ok(3)])
        Err('bad')

    """
    items: list[T] = []
    for result in iterable:
        match result:
            case Ok(value):
                items.append(value)
            case Err():
                return result
    return Ok(items)


def collect_all(iterable: Iterable[Result[T, E]]) -> Result[list[T], list[E]]:
    """
    Collect an iterable of ``Result`` values, accumulating **all** errors.

    Returns ``Ok`` of all success values only if every result is ``Ok``;
    otherwise returns ``Err`` of every error encountered. Unlike ``collect``,
    never short-circuits — the whole iterable is consumed, so the caller gets
    a complete error report (the validation use case). Unlike ``partition``,
    the outcome is a ``Result``: "all succeeded" and "something failed" are
    distinct variants instead of an empty-list check.

    Examples:
        >>> collect_all([Ok(1), Ok(2), Ok(3)])
        Ok([1, 2, 3])
        >>> collect_all([Ok(1), Err("a"), Ok(3), Err("b")])
        Err(['a', 'b'])

    """
    oks, errs = partition(iterable)
    if errs:
        return Err(errs)
    return Ok(oks)


def map_collect(
    iterable: Iterable[T],
    f: Callable[[T], Result[U, E]],
) -> Result[list[U], E]:
    """
    Apply *f* to each element and collect into ``Ok[list]``.

    Returns the first ``Err`` produced by *f*, short-circuiting the iteration.

    Examples:
        >>> def parse(s: str) -> Result[int, str]:
        ...     return Ok(int(s)) if s.isdigit() else Err(f"not a number: {s!r}")
        >>> map_collect(["1", "2", "3"], parse)
        Ok([1, 2, 3])
        >>> map_collect(["1", "x", "3"], parse)
        Err("not a number: 'x'")

    """
    items: list[U] = []
    for element in iterable:
        match f(element):
            case Ok(value):
                items.append(value)
            case Err() as err:
                return err
    return Ok(items)


def partition(
    iterable: Iterable[Result[T, E]],
) -> tuple[list[T], list[E]]:
    """
    Split an iterable of ``Result`` into ``(oks, errs)``.

    Consumes all elements without short-circuiting.

    Examples:
        >>> partition([Ok(1), Err("a"), Ok(2), Err("b")])
        ([1, 2], ['a', 'b'])

    """
    oks: list[T] = []
    errs: list[E] = []
    for result in iterable:
        match result:
            case Ok(value):
                oks.append(value)
            case Err(e):
                errs.append(e)
    return oks, errs


def map_partition(
    iterable: Iterable[T],
    f: Callable[[T], Result[U, E]],
) -> tuple[list[U], list[E]]:
    """
    Apply *f* to each element and split the results into ``(oks, errs)``.

    Consumes all elements without short-circuiting.

    Examples:
        >>> def parse(s: str) -> Result[int, str]:
        ...     return Ok(int(s)) if s.isdigit() else Err(f"not a number: {s!r}")
        >>> map_partition(["1", "x", "3"], parse)
        ([1, 3], ["not a number: 'x'"])

    """
    return partition(f(element) for element in iterable)


def filter_ok(iterable: Iterable[Result[T, E]]) -> Iterator[T]:
    """
    Yield the value from each ``Ok``, skipping ``Err`` values.

    Examples:
        >>> list(filter_ok([Ok(1), Err("x"), Ok(2)]))
        [1, 2]

    """
    for result in iterable:
        match result:
            case Ok(value):
                yield value


def filter_err(iterable: Iterable[Result[T, E]]) -> Iterator[E]:
    """
    Yield the error from each ``Err``, skipping ``Ok`` values.

    Examples:
        >>> list(filter_err([Ok(1), Err("x"), Ok(2), Err("y")]))
        ['x', 'y']

    """
    for result in iterable:
        match result:
            case Err(e):
                yield e


def try_reduce(
    iterable: Iterable[T],
    initial: U,
    f: Callable[[U, T], Result[U, E]],
) -> Result[U, E]:
    """
    Fold *iterable* with *f*, short-circuiting on ``Err``.

    Examples:
        >>> def safe_add(acc: int, x: int) -> Result[int, str]:
        ...     return Err(f"negative value: {x}") if x < 0 else Ok(acc + x)
        >>> try_reduce([1, 2, 3], 0, safe_add)
        Ok(6)
        >>> try_reduce([1, -1, 3], 0, safe_add)
        Err('negative value: -1')

    """
    acc: U = initial
    for element in iterable:
        match f(acc, element):
            case Ok(value):
                acc = value
            case Err() as err:
                return err
    return Ok(acc)
