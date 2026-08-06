"""Async iterator utilities for Result."""

from __future__ import annotations

import asyncio
import itertools
import operator
from collections.abc import AsyncIterator, Callable, Coroutine, Iterable, Iterator
from typing import TypeVar

from .result import Err, Ok, Result

T = TypeVar("T")
U = TypeVar("U")
E = TypeVar("E")

_R = TypeVar("_R")
_S = TypeVar("_S")
_CoroOrTask = Coroutine[object, object, _R] | asyncio.Task[_R]


_GROUP_MSG = "one or more awaitables raised exceptions"


async def _drain(
    pending: set[asyncio.Task[_R]],
    it: Iterator[_CoroOrTask[_S]],
) -> list[BaseException]:
    """
    Cancel *pending*, close unconsumed items, collect teardown exceptions.

    Tasks that raise something other than ``CancelledError`` while being
    cancelled would otherwise be silently discarded; return those exceptions
    so the caller can attach them to the propagating ``ExceptionGroup``.
    """
    for t in pending:
        t.cancel()
    results = await asyncio.gather(*pending, return_exceptions=True)
    for item in it:
        if isinstance(item, asyncio.Task):
            item.cancel()
        else:
            item.close()
    return [
        r
        for r in results
        if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError)
    ]


async def _cancel_all(
    pending: set[asyncio.Task[_R]],
    it: Iterator[_CoroOrTask[_S]],
) -> None:
    await _drain(pending, it)


def _wrap_indexed(idx: int, item: _CoroOrTask[_R]) -> asyncio.Task[tuple[int, _R]]:
    async def _inner() -> tuple[int, _R]:
        return idx, await item

    return asyncio.ensure_future(_inner())


def _make_pending_indexed(
    it: Iterator[_CoroOrTask[_R]],
    concurrency: int | None,
) -> tuple[set[asyncio.Task[tuple[int, _R]]], int]:
    pending: set[asyncio.Task[tuple[int, _R]]] = set()
    idx = 0
    for item in it if concurrency is None else itertools.islice(it, concurrency):
        pending.add(_wrap_indexed(idx, item))
        idx += 1
    return pending, idx


def _split_done(done: set[asyncio.Task[_R]]) -> tuple[list[_R], list[BaseException]]:
    """Split a finished batch into successful values and raised exceptions."""
    values: list[_R] = []
    excs: list[BaseException] = []
    for task in done:
        try:
            values.append(task.result())
        except BaseException as exc:  # noqa: BLE001 — re-raised by the caller as a group
            excs.append(exc)
    return values, excs


async def collect(
    iterable: Iterable[_CoroOrTask[Result[T, E]]],
    *,
    concurrency: int | None = None,
) -> Result[list[T], E]:
    """
    Await an iterable of coroutines or tasks concurrently, collecting results into ``Ok[list]``.

    Results are returned in input order.
    Returns the first ``Err`` encountered, cancelling remaining tasks.

    *concurrency* limits how many run at the same time.
    ``None`` means unlimited — all are scheduled at once.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Example:
        >>> import asyncio
        >>> async def fetch(i: int) -> Result[int, str]:
        ...     return Ok(i) if i > 0 else Err("bad")
        >>> asyncio.run(collect([fetch(1), fetch(2), fetch(3)]))
        Ok([1, 2, 3])
        >>> asyncio.run(collect([fetch(1), fetch(0)], concurrency=4))
        Err('bad')

        Raised exceptions always arrive as an ``ExceptionGroup`` — one failure
        is a group of one, several failures are collected together:

        >>> async def broken(source: str) -> Result[int, str]:
        ...     raise ConnectionError(source)
        >>> async def demo(sources: list[str]) -> list[str]:
        ...     errors: list[str] = []
        ...     try:
        ...         await collect([broken(s) for s in sources])
        ...     except* ConnectionError as group:
        ...         errors = sorted(str(e) for e in group.exceptions)
        ...     return errors
        >>> asyncio.run(demo(["eu"]))
        ['eu']
        >>> asyncio.run(demo(["eu", "us"]))
        ['eu', 'us']

    """
    it = iter(iterable)
    pending, next_idx = _make_pending_indexed(it, concurrency)
    indexed: dict[int, T] = {}

    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            batch, excs = _split_done(done)
            if excs:
                excs.extend(await _drain(pending, it))
                pending = set()
                raise BaseExceptionGroup(_GROUP_MSG, excs)
            batch.sort(key=operator.itemgetter(0))
            for idx, result in batch:
                match result:
                    case Ok(value):
                        indexed[idx] = value
                        next_item = next(it, None)
                        if next_item is not None:
                            pending.add(_wrap_indexed(next_idx, next_item))
                            next_idx += 1
                    case Err() as err:
                        return err
    finally:
        await _cancel_all(pending, it)

    return Ok([indexed[i] for i in range(len(indexed))])


async def map_collect(
    iterable: Iterable[T],
    f: Callable[[T], _CoroOrTask[Result[U, E]]],
    *,
    concurrency: int | None = None,
) -> Result[list[U], E]:
    """
    Apply *f* to each element concurrently and collect into ``Ok[list]``.

    Results are returned in input order.
    Returns the first ``Err`` produced by *f*, cancelling remaining tasks.

    *concurrency* limits how many calls to *f* run at the same time.
    ``None`` means unlimited — all are scheduled at once.

    **Exceptions**: if *f* raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Example:
        >>> import asyncio
        >>> async def double(x: int) -> Result[int, str]:
        ...     return Ok(x * 2)
        >>> asyncio.run(map_collect([1, 2, 3], double, concurrency=2))
        Ok([2, 4, 6])

    """
    return await collect(
        (f(element) for element in iterable),
        concurrency=concurrency,
    )


async def partition(
    iterable: Iterable[_CoroOrTask[Result[T, E]]],
    *,
    concurrency: int | None = None,
) -> tuple[list[T], list[E]]:
    """
    Await an iterable of coroutines or tasks concurrently, splitting results into ``(oks, errs)``.

    Results are collected in input order within each list.
    Unlike ``collect``, never short-circuits — all awaitables run to completion.

    *concurrency* limits how many run at the same time.
    ``None`` means unlimited — all are scheduled at once.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Example:
        >>> import asyncio
        >>> async def fetch(i: int) -> Result[int, str]:
        ...     return Ok(i) if i > 0 else Err(f"bad: {i}")
        >>> asyncio.run(partition([fetch(1), fetch(-1), fetch(2)]))
        ([1, 2], ['bad: -1'])

    """
    it = iter(iterable)
    pending, next_idx = _make_pending_indexed(it, concurrency)
    indexed: dict[int, Result[T, E]] = {}

    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            batch, excs = _split_done(done)
            if excs:
                excs.extend(await _drain(pending, it))
                pending = set()
                raise BaseExceptionGroup(_GROUP_MSG, excs)
            for idx, result in batch:
                indexed[idx] = result
                next_item = next(it, None)
                if next_item is not None:
                    pending.add(_wrap_indexed(next_idx, next_item))
                    next_idx += 1
    finally:
        await _cancel_all(pending, it)

    oks: list[T] = []
    errs: list[E] = []
    for result in (indexed[i] for i in range(len(indexed))):
        match result:
            case Ok(value):
                oks.append(value)
            case Err(e):
                errs.append(e)
    return oks, errs


async def map_partition(
    iterable: Iterable[T],
    f: Callable[[T], _CoroOrTask[Result[U, E]]],
    *,
    concurrency: int | None = None,
) -> tuple[list[U], list[E]]:
    """
    Apply *f* to each element concurrently and split the results into ``(oks, errs)``.

    Results are collected in input order within each list.
    Never short-circuits — all calls run to completion.

    *concurrency* limits how many calls to *f* run at the same time.
    ``None`` means unlimited — all are scheduled at once.

    **Exceptions**: if *f* raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Example:
        >>> import asyncio
        >>> async def check(i: int) -> Result[int, str]:
        ...     return Ok(i) if i > 0 else Err(f"bad: {i}")
        >>> asyncio.run(map_partition([1, -1, 2], check, concurrency=2))
        ([1, 2], ['bad: -1'])

    """
    return await partition(
        (f(element) for element in iterable),
        concurrency=concurrency,
    )


async def collect_all(
    iterable: Iterable[_CoroOrTask[Result[T, E]]],
    *,
    concurrency: int | None = None,
) -> Result[list[T], list[E]]:
    """
    Await coroutines or tasks concurrently, accumulating **all** errors.

    Returns ``Ok`` of all success values (in input order) only if every result
    is ``Ok``; otherwise returns ``Err`` of every error (in input order).
    Unlike ``collect``, never short-circuits — all awaitables run to
    completion, so the caller gets a complete error report.

    *concurrency* limits how many run at the same time.
    ``None`` means unlimited — all are scheduled at once.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Example:
        >>> import asyncio
        >>> async def fetch(i: int) -> Result[int, str]:
        ...     return Ok(i) if i > 0 else Err(f"bad: {i}")
        >>> asyncio.run(collect_all([fetch(1), fetch(2)]))
        Ok([1, 2])
        >>> asyncio.run(collect_all([fetch(1), fetch(-1), fetch(-2)]))
        Err(['bad: -1', 'bad: -2'])

    """
    oks, errs = await partition(iterable, concurrency=concurrency)
    if errs:
        return Err(errs)
    return Ok(oks)


async def filter_ok_unordered(
    iterable: Iterable[_CoroOrTask[Result[T, E]]],
    *,
    concurrency: int | None = None,
) -> AsyncIterator[T]:
    """
    Await coroutines or tasks concurrently, yielding ``Ok`` values as they complete.

    ``Err`` values are silently skipped.
    Values are yielded in completion order, not input order.

    *concurrency* limits how many run at the same time.
    ``None`` means unlimited — all are scheduled at once.

    If the consumer stops iterating early (``break``, exception, ``aclose()``),
    all in-flight tasks are cancelled and unconsumed coroutines are closed.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Example::

        async for user in filter_ok_unordered([fetch(1), fetch(2), fetch(3)]):
            print(user)
    """
    it = iter(iterable)
    pending: set[asyncio.Task[Result[T, E]]] = {
        asyncio.ensure_future(item)
        for item in (it if concurrency is None else itertools.islice(it, concurrency))
    }

    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            results, excs = _split_done(done)
            if excs:
                excs.extend(await _drain(pending, it))
                pending = set()
                raise BaseExceptionGroup(_GROUP_MSG, excs)
            for result in results:
                match result:
                    case Ok(value):
                        yield value
                    case Err():
                        pass
                next_item = next(it, None)
                if next_item is not None:
                    pending.add(asyncio.ensure_future(next_item))
    finally:
        await _cancel_all(pending, it)


async def filter_err_unordered(
    iterable: Iterable[_CoroOrTask[Result[T, E]]],
    *,
    concurrency: int | None = None,
) -> AsyncIterator[E]:
    """
    Await coroutines or tasks concurrently, yielding ``Err`` values as they complete.

    ``Ok`` values are silently skipped.
    Values are yielded in completion order, not input order.

    *concurrency* limits how many run at the same time.
    ``None`` means unlimited — all are scheduled at once.

    If the consumer stops iterating early (``break``, exception, ``aclose()``),
    all in-flight tasks are cancelled and unconsumed coroutines are closed.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Example::

        async for err in filter_err_unordered([fetch(1), fetch(2), fetch(3)]):
            print(err)
    """
    it = iter(iterable)
    pending: set[asyncio.Task[Result[T, E]]] = {
        asyncio.ensure_future(item)
        for item in (it if concurrency is None else itertools.islice(it, concurrency))
    }

    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            results, excs = _split_done(done)
            if excs:
                excs.extend(await _drain(pending, it))
                pending = set()
                raise BaseExceptionGroup(_GROUP_MSG, excs)
            for result in results:
                match result:
                    case Ok():
                        pass
                    case Err(e):
                        yield e
                next_item = next(it, None)
                if next_item is not None:
                    pending.add(asyncio.ensure_future(next_item))
    finally:
        await _cancel_all(pending, it)


async def filter_ok(
    iterable: Iterable[_CoroOrTask[Result[T, E]]],
    *,
    concurrency: int,
) -> AsyncIterator[T]:
    """
    Await coroutines or tasks concurrently, yielding ``Ok`` values in input order.

    ``Err`` values are silently skipped.
    Values are yielded in input order — later-completing tasks are buffered until
    all earlier ones have been yielded.

    *concurrency* controls the size of the sliding window of in-flight tasks.
    Unlike ``filter_ok_unordered``, ``None`` is not accepted because the
    reorder buffer would be unbounded.

    If the consumer stops iterating early (``break``, exception, ``aclose()``),
    all in-flight tasks are cancelled and unconsumed coroutines are closed.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Example::

        async for user in filter_ok([fetch(1), fetch(2), fetch(3)], concurrency=4):
            print(user)
    """
    it = iter(iterable)
    pending, next_idx = _make_pending_indexed(it, concurrency)
    buf: dict[int, Result[T, E]] = {}
    next_yield = 0

    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            batch, excs = _split_done(done)
            if excs:
                excs.extend(await _drain(pending, it))
                pending = set()
                raise BaseExceptionGroup(_GROUP_MSG, excs)
            for idx, result in batch:
                buf[idx] = result
                next_item = next(it, None)
                if next_item is not None:
                    pending.add(_wrap_indexed(next_idx, next_item))
                    next_idx += 1

            while next_yield in buf:
                match buf.pop(next_yield):
                    case Ok(value):
                        yield value
                    case Err():
                        pass
                next_yield += 1
    finally:
        await _cancel_all(pending, it)


async def filter_err(
    iterable: Iterable[_CoroOrTask[Result[T, E]]],
    *,
    concurrency: int,
) -> AsyncIterator[E]:
    """
    Await coroutines or tasks concurrently, yielding ``Err`` values in input order.

    ``Ok`` values are silently skipped.
    Values are yielded in input order — later-completing tasks are buffered until
    all earlier ones have been yielded.

    *concurrency* controls the size of the sliding window of in-flight tasks.
    Unlike ``filter_err_unordered``, ``None`` is not accepted because the
    reorder buffer would be unbounded.

    If the consumer stops iterating early (``break``, exception, ``aclose()``),
    all in-flight tasks are cancelled and unconsumed coroutines are closed.

    **Exceptions**: if any coroutine raises, all remaining tasks are cancelled and
    every exception — including any raised while those tasks were being cancelled —
    propagates as a single ``ExceptionGroup``, even when only one task failed.
    One failure is a group of one: the exception type you catch never depends
    on timing. Handle with ``except*``.

    Example::

        async for err in filter_err([fetch(1), fetch(2), fetch(3)], concurrency=4):
            print(err)
    """
    it = iter(iterable)
    pending, next_idx = _make_pending_indexed(it, concurrency)
    buf: dict[int, Result[T, E]] = {}
    next_yield = 0

    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            batch, excs = _split_done(done)
            if excs:
                excs.extend(await _drain(pending, it))
                pending = set()
                raise BaseExceptionGroup(_GROUP_MSG, excs)
            for idx, result in batch:
                buf[idx] = result
                next_item = next(it, None)
                if next_item is not None:
                    pending.add(_wrap_indexed(next_idx, next_item))
                    next_idx += 1

            while next_yield in buf:
                match buf.pop(next_yield):
                    case Ok():
                        pass
                    case Err(e):
                        yield e
                next_yield += 1
    finally:
        await _cancel_all(pending, it)


async def try_reduce(
    iterable: Iterable[_CoroOrTask[T]],
    initial: U,
    f: Callable[[U, T], Result[U, E]],
) -> Result[U, E]:
    """
    Await each coroutine or task sequentially, folding with *f* and short-circuiting on ``Err``.

    Unlike the async ``collect`` / ``partition`` family, tasks run one at a time —
    each awaited value is passed to *f* before the next is awaited, because the
    accumulator depends on the previous step.

    On short-circuit — and on any exception — remaining tasks are cancelled and
    unconsumed coroutines are closed.

    **Exceptions**: unlike the concurrent functions, execution is sequential —
    only one coroutine can fail — so exceptions propagate bare, without an
    ``ExceptionGroup``.

    Example:
        >>> import asyncio
        >>> async def fetch(i: int) -> int:
        ...     return i
        >>> def safe_add(acc: int, x: int) -> Result[int, str]:
        ...     return Err(f"negative: {x}") if x < 0 else Ok(acc + x)
        >>> asyncio.run(try_reduce([fetch(1), fetch(2)], 0, safe_add))
        Ok(3)
        >>> asyncio.run(try_reduce([fetch(1), fetch(-1), fetch(3)], 0, safe_add))
        Err('negative: -1')

    """
    it = iter(iterable)
    acc: U = initial
    try:
        for item in it:
            value: T = await item
            match f(acc, value):
                case Ok(new_acc):
                    acc = new_acc
                case Err() as err:
                    return err
    finally:
        for remaining in it:
            if isinstance(remaining, asyncio.Task):
                remaining.cancel()
            else:
                remaining.close()
    return Ok(acc)
